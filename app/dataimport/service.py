"""Data migration from other pharmacy systems (E-Stock or any Excel/CSV export).

Steps (spec 03): read → map columns → normalize → detect duplicates / missing /
conflicting data → dry-run report → user review → apply once, atomically →
reconcile counts and quantities → final report. Nothing is written to items or
stock before apply(); a failure during apply rolls back everything; applying the
same file twice is refused (DB unique index + status check).

Rules honoured:
- opening balances are 'opening' movements and are refused for items that
  already have stock history (no mixing with purchases/sales history);
- missing batch/expiry stays missing (stock is posted untracked), never guessed;
- name similarity alone never links two items: only code or barcode does; a
  similar name goes to 'review' for a human decision.
"""
from __future__ import annotations

import hashlib
import io
import json
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from ..catalog import matching, service as catalog
from ..catalog.normalize import (dedupe_key, normalize_barcode, normalize_form, normalize_text,
                                 parse_name)
from ..core import audit
from ..core.errors import Conflict, NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id
from ..inventory import service as inventory
from ..purchasing.importers import files
from ..purchasing.model import parse_decimal, parse_expiry, to_piasters

ITEM_HEADERS: dict[str, list[str]] = {
    "code": ["كود", "الكود", "كود الصنف", "رقم الصنف", "code", "item code", "item no", "id", "sku"],
    "name_en": ["الاسم الانجليزي", "اسم انجليزي", "english name", "name en", "item name",
                "name", "الصنف", "اسم الصنف", "description", "trade name", "الاسم"],
    "name_ar": ["الاسم العربي", "اسم عربي", "arabic name", "name ar", "الاسم بالعربي"],
    "barcode": ["باركود", "الباركود", "barcode", "ean", "gtin", "بار كود"],
    "public_price": ["سعر الجمهور", "سعر البيع", "السعر", "سعر", "price", "public price",
                     "sell price", "selling price"],
    "cost": ["التكلفه", "تكلفه", "سعر الشراء", "متوسط التكلفه", "cost", "purchase price",
             "avg cost", "buy price"],
    "qty": ["الكميه", "الرصيد", "الكميه الحاليه", "رصيد", "qty", "quantity", "stock", "balance",
            "on hand"],
    "batch_no": ["التشغيله", "رقم التشغيله", "batch", "lot"],
    "expiry": ["الصلاحيه", "تاريخ الصلاحيه", "expiry", "exp", "exp date"],
    "manufacturer": ["الشركه", "الشركه المنتجه", "المنتج", "manufacturer", "company", "producer"],
    "active_ingredient": ["الماده الفعاله", "المادة الفعالة", "active ingredient", "generic",
                          "generic name", "scientific name", "الاسم العلمي"],
    "dosage_form": ["الشكل", "الشكل الصيدلي", "form", "dosage form"],
    "strength": ["التركيز", "strength", "concentration"],
    "pack_size": ["عدد الوحدات", "عدد الشرائط", "وحدات العبوه", "pack size", "units per pack",
                  "عدد الاجزاء", "التجزئه"],
    "min_stock": ["حد الطلب", "الحد الادني", "min", "min stock", "reorder level"],
    "is_imported": ["محلي/مستورد", "مستورد", "imported", "local/imported", "النوع"],
    "is_drug": ["دواء/غير دواء", "دواء", "is drug", "drug"],
    "location": ["الموقع", "المكان", "الرف", "location", "shelf"],
}
_SYN = {normalize_text(s): f for f, ss in ITEM_HEADERS.items() for s in ss}
FIELDS = set(ITEM_HEADERS)


def _detect(rows: list[list]) -> tuple[int, dict[int, str]] | None:
    best = None
    for i, row in enumerate(rows[:30]):
        cmap: dict[int, str] = {}
        for j, v in enumerate(row):
            t = normalize_text(str(v)) if v is not None else ""
            f = _SYN.get(t)
            if f and f not in cmap.values():
                cmap[j] = f
        names = {"name_en", "name_ar", "code", "barcode"} & set(cmap.values())
        if names and len(cmap) >= 2 and (best is None or len(cmap) > len(best[1])):
            best = (i, cmap)
    return best


def _bool(v) -> int | None:
    t = normalize_text(str(v)) if v not in (None, "") else ""
    if t in ("1", "نعم", "yes", "true", "مستورد", "imported", "دواء", "drug"):
        return 1
    if t in ("0", "لا", "no", "false", "محلي", "local", "غير دواء", "non drug", "nondrug"):
        return 0
    return None


def _read(data: bytes, filename: str) -> list[list]:
    kind, _ = files.sniff(data, filename)
    if kind == "excel":
        return files.read_excel(data)
    if kind == "csv":
        return files.read_csv(data)
    raise ValidationFailed("استيراد البيانات يقبل ملفات Excel (.xlsx) أو CSV فقط")


class DataImport:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------ analyze
    def analyze(self, ctx: Context, data: bytes, filename: str, *, kind: str = "items",
                mapping: dict[int, str] | None = None, source_system: str = "generic",
                options: dict | None = None) -> str:
        ctx.require("items.import")
        if kind not in ("items", "opening_stock"):
            raise ValidationFailed("نوع استيراد غير معروف")
        if kind == "opening_stock":
            ctx.require("inventory.adjust")
        options = {"qty_unit": "pack", **(options or {})}
        if options["qty_unit"] not in ("pack", "base"):
            raise ValidationFailed("وحدة الكمية لازم تكون pack أو base")
        rows = _read(data, filename)
        if mapping:
            bad = set(mapping.values()) - FIELDS
            if bad:
                raise ValidationFailed(f"حقول غير معروفة: {', '.join(sorted(bad))}")
            header_row, cmap = int(options.get("header_row", 0)), {int(k): v for k, v in mapping.items()}
        else:
            found = _detect(rows)
            if not found:
                raise ValidationFailed("لم أتعرف على أعمدة الملف؛ حدد الأعمدة يدويًا",
                                       code="mapping_required",
                                       details={"preview": [[str(c) if c is not None else "" for c in r]
                                                            for r in rows[:5]]})
            header_row, cmap = found
        sha = hashlib.sha256(data).hexdigest()
        if self.db.one("SELECT 1 FROM import_runs WHERE org_id=? AND kind=? AND file_sha256=?"
                       " AND status='applied'", (ctx.org_id, kind, sha)):
            raise Conflict("الملف ده اتستورد قبل كده", code="already_applied")

        run_id = new_id()
        planned = []
        seen_code: dict[str, int] = {}
        seen_bc: dict[str, int] = {}
        seen_key: dict[str, int] = {}
        for idx, r in enumerate(rows[header_row + 1:], start=header_row + 2):
            raw = {cmap[j]: (r[j] if j < len(r) else None) for j in cmap}
            if all(v in (None, "") for v in raw.values()):
                continue
            d, issues = self._normalize(raw, options)
            if kind == "items":
                action, target, more = self._plan_item(ctx, d)
            else:
                action, target, more = self._plan_stock(ctx, d)
            issues += more
            # duplicates inside the file itself
            for key, store, code in ((d.get("code"), seen_code, "duplicate_code_in_file"),
                                     (d.get("barcode"), seen_bc, "duplicate_barcode_in_file"),
                                     (d.get("_dedupe") if kind == "items" else None, seen_key,
                                      "duplicate_item_in_file")):
                if key:
                    if key in store:
                        issues.append({"code": code, "other_row": store[key]})
                        if action in ("create", "update", "link"):
                            action = "review"
                    else:
                        store[key] = idx
            if any(i["code"].startswith("missing_") or i["code"].startswith("invalid_")
                   for i in issues) and action != "skip":
                action = "error" if not d.get("_name") and not d.get("code") else (
                    "review" if action != "error" else action)
            planned.append((idx, raw, d, action, target, issues))

        with self.db.transaction():
            self.db.execute("INSERT INTO import_runs (id, org_id, branch_id, kind, source_system,"
                            " filename, file_sha256, mapping, options, created_by)"
                            " VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (run_id, ctx.org_id, ctx.branch_id, kind, source_system,
                             filename[:200], sha, json.dumps(cmap), json.dumps(options), ctx.user_id))
            for idx, raw, d, action, target, issues in planned:
                self.db.execute("INSERT INTO import_rows (id, run_id, row_no, raw, data, action,"
                                " target_id, issues) VALUES (?,?,?,?,?,?,?,?)",
                                (new_id(), run_id, idx, json.dumps(raw, default=str, ensure_ascii=False),
                                 json.dumps(d, default=str, ensure_ascii=False), action, target,
                                 json.dumps(issues, ensure_ascii=False)))
            report = self._report(run_id)
            self.db.execute("UPDATE import_runs SET report=? WHERE id=?",
                            (json.dumps(report, ensure_ascii=False), run_id))
            audit.record(self.db, ctx, "import.analyze", "import_run", run_id,
                         after={"kind": kind, "file": filename, "counts": report["counts"]})
        return run_id

    def _normalize(self, raw: dict, options: dict) -> tuple[dict, list[dict]]:
        d: dict[str, Any] = {}
        issues: list[dict] = []

        def txt(k):
            v = raw.get(k)
            if v is None:
                return None
            if isinstance(v, float) and v == int(v):
                v = int(v)
            s = str(v).strip()
            return s or None

        for k in ("code", "name_en", "name_ar", "manufacturer", "active_ingredient", "strength",
                  "batch_no", "location"):
            d[k] = txt(k)
        bc = raw.get("barcode")
        if isinstance(bc, float):
            bc = str(int(bc))
        d["barcode"] = normalize_barcode(bc) if bc not in (None, "") else None
        if bc not in (None, "") and (not d["barcode"] or len(d["barcode"]) < 6):
            issues.append({"code": "invalid_barcode", "value": str(bc)})
            d["barcode"] = None
        d["dosage_form"] = normalize_form(txt("dosage_form")) if txt("dosage_form") else None
        for k in ("public_price", "cost"):
            v = raw.get(k)
            if v not in (None, ""):
                dec = parse_decimal(v)
                if dec is None or dec < 0:
                    issues.append({"code": f"invalid_{k}", "value": str(v)})
                else:
                    d[k] = str(dec)
        for k in ("pack_size", "min_stock"):
            v = raw.get(k)
            if v not in (None, ""):
                dec = parse_decimal(v)
                if dec is None or dec < (1 if k == "pack_size" else 0) or dec != dec.to_integral_value():
                    issues.append({"code": f"invalid_{k}", "value": str(v)})
                else:
                    d[k] = int(dec)
        v = raw.get("qty")
        if v not in (None, ""):
            dec = parse_decimal(v)
            if dec is None or dec < 0:
                issues.append({"code": "invalid_qty", "value": str(v)})
            else:
                d["qty"] = str(dec)
        if raw.get("expiry") not in (None, ""):
            iso, prec = parse_expiry(raw.get("expiry"))
            if iso:
                d["expiry_date"], d["expiry_precision"] = iso, prec
            else:
                issues.append({"code": "invalid_expiry", "value": str(raw.get("expiry"))})
        for k in ("is_imported", "is_drug"):
            if raw.get(k) not in (None, ""):
                d[k] = _bool(raw.get(k))
                if d[k] is None:
                    issues.append({"code": f"unknown_{k}", "value": str(raw.get(k))})
        name = d.get("name_en") or d.get("name_ar")
        d["_name"] = name
        if name:
            d["_dedupe"] = dedupe_key(name, d.get("strength"), d.get("dosage_form"),
                                      d.get("pack_size") or 1)
        return d, issues

    def _existing_by_ids(self, ctx: Context, d: dict) -> tuple[str | None, str | None, list[dict]]:
        issues = []
        by_code = by_bc = None
        if d.get("code"):
            r = self.db.one("SELECT id FROM items WHERE org_id=? AND code=?", (ctx.org_id, d["code"]))
            by_code = r["id"] if r else None
        if d.get("barcode"):
            r = self.db.one("SELECT item_id FROM item_barcodes WHERE org_id=? AND barcode=?",
                            (ctx.org_id, d["barcode"]))
            by_bc = r["item_id"] if r else None
        if by_code and by_bc and by_code != by_bc:
            issues.append({"code": "code_barcode_conflict", "item_by_code": by_code,
                           "item_by_barcode": by_bc})
        return by_code, by_bc, issues

    def _plan_item(self, ctx: Context, d: dict) -> tuple[str, str | None, list[dict]]:
        issues = []
        if not d.get("_name"):
            return "error", None, [{"code": "missing_name"}]
        by_code, by_bc, conflict = self._existing_by_ids(ctx, d)
        if conflict:
            return "review", None, conflict
        if by_code or by_bc:
            return "update", by_code or by_bc, issues
        dup = self.db.one("SELECT id FROM items WHERE org_id=? AND dedupe_key=?",
                          (ctx.org_id, d["_dedupe"]))
        if dup:
            return "review", dup["id"], [{"code": "same_item_exists", "item_id": dup["id"]}]
        cands = [c for c in matching.fuzzy_candidates(self.db, ctx.org_id, d["_name"], 3)
                 if c.score >= 0.9 and not c.reasons]
        if cands:
            return "review", cands[0].item_id, [{"code": "similar_item_exists",
                                                 "item_id": cands[0].item_id,
                                                 "label": cands[0].label, "score": cands[0].score}]
        if d.get("public_price") is None:
            issues.append({"code": "price_missing"})          # informative only
        return "create", None, issues

    def _plan_stock(self, ctx: Context, d: dict) -> tuple[str, str | None, list[dict]]:
        by_code, by_bc, conflict = self._existing_by_ids(ctx, d)
        if conflict:
            return "review", None, conflict
        item_id = by_code or by_bc
        if not item_id and d.get("_name"):
            r = self.db.one("SELECT id FROM items WHERE org_id=? AND name_norm=?",
                            (ctx.org_id, parse_name(d["_name"]).norm))
            item_id = r["id"] if r else None
            if item_id:
                return "review", item_id, [{"code": "matched_by_name_only"}]
        if not item_id:
            return "error", None, [{"code": "missing_item", "hint": "استورد الأصناف أولًا"}]
        if d.get("qty") is None:
            return "error", item_id, [{"code": "missing_qty"}]
        if Decimal(d["qty"]) == 0:
            return "skip", item_id, [{"code": "zero_qty"}]
        has_history = self.db.one("SELECT 1 FROM stock_movements WHERE org_id=? AND branch_id=?"
                                  " AND item_id=? LIMIT 1", (ctx.org_id, ctx.branch_id, item_id))
        if has_history:
            return "review", item_id, [{"code": "item_has_history"}]
        issues = []
        if not d.get("expiry_date") and not d.get("batch_no"):
            issues.append({"code": "no_batch_or_expiry"})  # will be posted untracked
        if d.get("cost") is None:
            issues.append({"code": "cost_missing"})
        return "create", item_id, issues

    def _report(self, run_id: str) -> dict:
        counts = {r["action"]: r["n"] for r in self.db.all(
            "SELECT action, count(*) n FROM import_rows WHERE run_id=? GROUP BY action", (run_id,))}
        issue_counts: dict[str, int] = {}
        qty_total = Decimal(0)
        value_total = Decimal(0)
        for r in self.db.all("SELECT issues, data, action FROM import_rows WHERE run_id=?", (run_id,)):
            for i in json.loads(r["issues"]):
                issue_counts[i["code"]] = issue_counts.get(i["code"], 0) + 1
            d = json.loads(r["data"])
            if r["action"] in ("create", "update") and d.get("qty"):
                qty_total += Decimal(d["qty"])
                if d.get("cost"):
                    value_total += Decimal(d["qty"]) * Decimal(d["cost"])
        return {"counts": counts, "issues": issue_counts, "rows": sum(counts.values()),
                "source_qty_total": str(qty_total),
                "source_value_total": str(value_total.quantize(Decimal("0.01")))}

    # ------------------------------------------------------------ review
    def get(self, ctx: Context, run_id: str, action: str | None = None, limit: int = 500) -> dict:
        ctx.require("items.import")
        run = self.db.one("SELECT * FROM import_runs WHERE id=? AND org_id=?", (run_id, ctx.org_id))
        if not run:
            raise NotFound("عملية الاستيراد غير موجودة")
        q = "SELECT * FROM import_rows WHERE run_id=?"
        params: list = [run_id]
        if action:
            q += " AND action=?"
            params.append(action)
        q += " ORDER BY row_no LIMIT ?"
        params.append(limit)
        rows = []
        for r in self.db.all(q, tuple(params)):
            d = dict(r)
            for k in ("raw", "data", "issues"):
                d[k] = json.loads(d[k])
            rows.append(d)
        out = dict(run)
        for k in ("mapping", "options", "report"):
            out[k] = json.loads(out[k])
        out["rows"] = rows
        return out

    def decide(self, ctx: Context, run_id: str, row_id: str, decision: str) -> None:
        ctx.require("items.import")
        run = self.db.one("SELECT * FROM import_runs WHERE id=? AND org_id=?", (run_id, ctx.org_id))
        if not run:
            raise NotFound("عملية الاستيراد غير موجودة")
        if run["status"] != "analyzed":
            raise Conflict("عملية الاستيراد مطبقة أو ملغاة")
        row = self.db.one("SELECT * FROM import_rows WHERE id=? AND run_id=?", (row_id, run_id))
        if not row:
            raise NotFound("السطر غير موجود")
        if row["action"] == "error" and decision != "skip":
            raise ValidationFailed("السطر فيه خطأ؛ صحح الملف أو تجاهل السطر")
        if decision.startswith("link:"):
            item_id = decision[5:]
            if not self.db.one("SELECT 1 FROM items WHERE id=? AND org_id=?", (item_id, ctx.org_id)):
                raise NotFound("الصنف غير موجود")
        elif decision not in ("create", "skip"):
            raise ValidationFailed("قرار غير صحيح")
        if run["kind"] == "opening_stock" and decision == "create" and row["action"] == "review":
            issues = {i["code"] for i in json.loads(row["issues"])}
            if "item_has_history" in issues:
                raise ValidationFailed("الصنف له حركات سابقة؛ استخدم الجرد/التسوية بدل الرصيد الافتتاحي")
        with self.db.transaction():
            self.db.execute("UPDATE import_rows SET decision=? WHERE id=?", (decision, row_id))
            audit.record(self.db, ctx, "import.decide", "import_run", run_id,
                         after={"row_no": row["row_no"], "decision": decision})

    # ------------------------------------------------------------ apply
    def apply(self, ctx: Context, run_id: str) -> dict:
        ctx.require("items.import")
        with self.db.transaction():
            run = self.db.one("SELECT * FROM import_runs WHERE id=? AND org_id=?",
                              (run_id, ctx.org_id))
            if not run:
                raise NotFound("عملية الاستيراد غير موجودة")
            if run["status"] != "analyzed":
                raise Conflict("عملية الاستيراد مطبقة أو ملغاة", code="already_applied")
            if run["kind"] == "opening_stock":
                ctx.require("inventory.adjust")
            rows = [dict(r) for r in self.db.all(
                "SELECT * FROM import_rows WHERE run_id=? ORDER BY row_no", (run_id,))]
            pending = [r["row_no"] for r in rows if r["action"] == "review" and not r["decision"]]
            if pending:
                raise Conflict("فيه أسطر محتاجة قرار قبل التطبيق", code="pending_review",
                               details={"rows": pending[:50], "count": len(pending)})
            options = json.loads(run["options"])
            done = {"created": 0, "updated": 0, "linked": 0, "skipped": 0, "errors": 0,
                    "stock_rows": 0, "posted_qty_packs": Decimal(0), "posted_base_units": 0}
            for r in rows:
                d = json.loads(r["data"])
                action = r["action"]
                target = r["target_id"]
                if r["decision"] == "skip" or action in ("skip",):
                    done["skipped"] += 1
                    continue
                if action == "error":
                    done["errors"] += 1
                    continue
                if r["decision"] and r["decision"].startswith("link:"):
                    action, target = "link", r["decision"][5:]
                elif r["decision"] == "create":
                    action = "create"
                if run["kind"] == "items":
                    self._apply_item(ctx, d, action, target, run_id, done)
                else:
                    self._apply_stock(ctx, d, target, run_id, options, done)
            now = _now()
            done["posted_qty_packs"] = str(done["posted_qty_packs"])
            report = json.loads(run["report"])
            report["applied"] = done
            if run["kind"] == "opening_stock":
                report["reconciliation"] = self._reconcile(ctx, run_id, report)
                if not report["reconciliation"]["ok"]:
                    raise Conflict("الأرصدة بعد الاستيراد لا تطابق الملف؛ تم إلغاء العملية بالكامل",
                                   code="reconciliation_failed", details=report["reconciliation"])
            self.db.execute("UPDATE import_runs SET status='applied', applied_by=?, applied_at=?,"
                            " report=? WHERE id=?",
                            (ctx.user_id, now, json.dumps(report, ensure_ascii=False, default=str),
                             run_id))
            audit.record(self.db, ctx, "import.apply", "import_run", run_id, after=done)
        matching.invalidate(ctx.org_id)
        return report

    def _apply_item(self, ctx: Context, d: dict, action: str, target: str | None, run_id: str,
                    done: dict) -> None:
        fields = {k: d.get(k) for k in ("code", "name_en", "name_ar", "manufacturer",
                                        "active_ingredient", "strength", "dosage_form",
                                        "pack_size", "min_stock", "location", "is_imported",
                                        "is_drug") if d.get(k) is not None}
        if d.get("public_price") is not None:
            fields["public_price"] = to_piasters(Decimal(d["public_price"]))
        if action == "create":
            item_id = catalog.create_item(self.db, ctx, fields, force=True, source="import")
            done["created"] += 1
        elif action in ("update", "link"):
            item_id = target
            # never overwrite existing values with blanks; code only set if empty
            current = catalog.get_item(self.db, ctx, item_id)
            changes = {k: v for k, v in fields.items()
                       if v is not None and (k != "code" or not current.get("code"))}
            if action == "link":
                changes.pop("name_en", None)
                changes.pop("name_ar", None)
                if d.get("_name"):
                    catalog.add_alias(self.db, ctx, item_id, d["_name"], source="import")
            if changes:
                catalog.update_item(self.db, ctx, item_id, changes)
            done["updated" if action == "update" else "linked"] += 1
        else:
            return
        if d.get("barcode"):
            try:
                catalog.add_barcode(self.db, ctx, item_id, d["barcode"])
            except Conflict:
                raise Conflict(f"الباركود {d['barcode']} مسجل لصنف آخر", code="barcode_taken")

    def _apply_stock(self, ctx: Context, d: dict, item_id: str, run_id: str, options: dict,
                     done: dict) -> None:
        item = catalog.get_item(self.db, ctx, item_id)
        qty = Decimal(d["qty"])
        pack = item["pack_size"]
        base = qty * pack if options.get("qty_unit") == "pack" else qty
        if base != base.to_integral_value():
            raise ValidationFailed(f"الكمية {qty} لا تساوي عدد صحيح من الوحدات للصنف {item['name_en'] or item['name_ar']}",
                                   code="fractional_qty")
        base_units = int(base)
        unit_cost = None
        if d.get("cost") is not None:
            per_pack = Decimal(d["cost"])
            unit_cost = (per_pack / pack if options.get("qty_unit") == "pack" else per_pack)
            unit_cost = unit_cost.quantize(Decimal("0.000001"), ROUND_HALF_UP)
        batch_id = inventory.get_or_create_batch(self.db, ctx, item_id, d.get("batch_no"),
                                                 d.get("expiry_date"), d.get("expiry_precision"))
        inventory.post_movement(self.db, ctx, item_id=item_id, qty_delta=base_units,
                                movement_type="opening", batch_id=batch_id, unit_cost=unit_cost,
                                ref_type="import_run", ref_id=run_id, note="رصيد افتتاحي")
        done["stock_rows"] += 1
        done["posted_qty_packs"] += qty
        done["posted_base_units"] += base_units

    def _reconcile(self, ctx: Context, run_id: str, report: dict) -> dict:
        posted = self.db.one("SELECT coalesce(sum(qty_delta),0) q, count(*) n FROM stock_movements"
                             " WHERE ref_type='import_run' AND ref_id=?", (run_id,))
        expected_rows = report["applied"]["stock_rows"]
        expected_units = report["applied"]["posted_base_units"]
        ok = posted["n"] == expected_rows and posted["q"] == expected_units and \
            not inventory.verify_balances(self.db, ctx.org_id)
        return {"ok": ok, "ledger_rows": posted["n"], "ledger_units": posted["q"],
                "expected_rows": expected_rows, "expected_units": expected_units}

    def discard(self, ctx: Context, run_id: str) -> None:
        ctx.require("items.import")
        with self.db.transaction():
            n = self.db.execute("UPDATE import_runs SET status='discarded' WHERE id=? AND org_id=?"
                                " AND status='analyzed'", (run_id, ctx.org_id)).rowcount
            if not n:
                raise Conflict("لا يمكن إلغاء هذه العملية")
            audit.record(self.db, ctx, "import.discard", "import_run", run_id)


# ---------------------------------------------------------------- export
def export_catalog(db: Database, ctx: Context) -> bytes:
    """Data portability: full item list with barcodes and current stock as .xlsx."""
    ctx.require("items.read")
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "items"
    ws.append(["id", "code", "name_en", "name_ar", "active_ingredient", "strength", "dosage_form",
               "manufacturer", "pack_size", "public_price_egp", "barcodes", "stock_base_units",
               "status"])
    for it in db.all("SELECT * FROM items WHERE org_id=? ORDER BY coalesce(name_en,name_ar)",
                     (ctx.org_id,)):
        bcs = ",".join(r["barcode"] for r in db.all(
            "SELECT barcode FROM item_barcodes WHERE org_id=? AND item_id=?", (ctx.org_id, it["id"])))
        q = db.one("SELECT coalesce(sum(qty),0) q FROM stock_balances WHERE org_id=? AND item_id=?",
                   (ctx.org_id, it["id"]))["q"]
        ws.append([it["id"], it["code"], it["name_en"], it["name_ar"], it["active_ingredient"],
                   it["strength"], it["dosage_form"], it["manufacturer"], it["pack_size"],
                   (it["public_price"] or 0) / 100 if it["public_price"] is not None else None,
                   bcs, q, it["status"]])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _now() -> str:
    from ..core.accounts import iso, utcnow
    return iso(utcnow())
