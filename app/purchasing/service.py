"""Purchase invoice workflow: import -> review/edit -> approve (posts stock) -> cancel.

Invariants:
- Nothing touches stock, cost or item prices before approve().
- An approved invoice is immutable; corrections are a cancel (reversal) + new invoice.
- Every edit is audited with before/after; the original extraction is kept
  untouched in purchase_invoices.raw_extraction and the source file is kept.
- Concurrent edits are detected with an optimistic version number.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .. import config as config_mod
from ..catalog import matching, service as catalog
from ..catalog.normalize import normalize_text
from ..core import accounts, audit, notify
from ..core.errors import Conflict, DomainError, NotFound, ValidationFailed
from ..core.numbers import whole_number
from ..core.permissions import Context
from ..db import Database, new_id
from ..inventory import service as inventory
from . import validation
from .importers import ai_vision, files
from .model import ExtractedInvoice, parse_expiry, pct_to_bp, to_piasters

EDITABLE_LINE_FIELDS = {"item_id", "qty", "bonus_qty", "public_price", "discount_bp",
                        "unit_net_price", "tax_amount", "line_total", "batch_no", "expiry_date",
                        "raw_name"}
EDITABLE_HEADER_FIELDS = {"supplier_id", "invoice_no", "invoice_date", "declared_total",
                          "extra_discount", "extra_tax", "notes"}


_KEY_MAP = {"discount_pct": "discount_bp"}


def _db_keys(d: dict) -> dict:
    """Extraction field names -> DB column names, so a later user edit of the
    column clears the matching confidence flag."""
    return {_KEY_MAP.get(k, k): v for k, v in d.items()}


class Purchasing:
    def __init__(self, db: Database, settings: config_mod.Settings | None = None,
                 ai_extract=None):
        self.db = db
        self.s = settings or config_mod.load()
        # injectable for tests; default = configured provider (see extractors.py)
        if ai_extract is None:
            from .extractors import from_settings
            provider = from_settings(self.s)
            ai_extract = provider.extract
        self._ai_extract = ai_extract

    # ------------------------------------------------------------ files
    def _store_file(self, ctx: Context, data: bytes, filename: str, mime: str) -> tuple[str, str]:
        sha = hashlib.sha256(data).hexdigest()
        existing = self.db.one("SELECT id FROM source_files WHERE org_id=? AND sha256=?",
                               (ctx.org_id, sha))
        if existing:
            return existing["id"], sha
        ext = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg",
               "image/webp": ".webp", "text/csv": ".csv"}.get(mime, ".xlsx")
        folder = self.s.upload_dir / ctx.org_id            # path never built from user input
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{sha}{ext}"
        if not path.exists():
            tmp = path.with_suffix(path.suffix + ".part")
            tmp.write_bytes(data)
            tmp.replace(path)
        fid = new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO source_files (id, org_id, sha256, filename, mime_type,"
                            " size_bytes, storage_path, uploaded_by) VALUES (?,?,?,?,?,?,?,?)",
                            (fid, ctx.org_id, sha, Path(filename or "upload").name[:200], mime,
                             len(data), str(path), ctx.user_id))
        return fid, sha

    def get_file(self, ctx: Context, file_id: str) -> tuple[bytes, str, str]:
        ctx.require("purchases.read")
        r = self.db.one("SELECT * FROM source_files WHERE id=? AND org_id=?", (file_id, ctx.org_id))
        if not r:
            raise NotFound("الملف غير موجود")
        path = Path(r["storage_path"])
        if not path.exists():   # restored on another machine / data folder moved
            path = self.s.upload_dir / r["org_id"] / path.name
        if not path.exists():
            raise NotFound("ملف الفاتورة الأصلي غير موجود على هذا الجهاز")
        return path.read_bytes(), r["mime_type"], r["filename"]

    # ------------------------------------------------------------ import
    def import_file(self, ctx: Context, data: bytes, filename: str,
                    supplier_id: str | None = None) -> str:
        ctx.require("purchases.create")
        accounts.require_feature(self.db, ctx, "purchasing")
        if not data:
            raise ValidationFailed("الملف فارغ")
        if len(data) > self.s.max_upload_bytes:
            raise ValidationFailed("حجم الملف أكبر من المسموح")
        kind, mime = files.sniff(data, filename)
        if supplier_id:
            catalog.get_supplier(self.db, ctx, supplier_id)

        sha = hashlib.sha256(data).hexdigest()
        dup = self.db.one(
            "SELECT pi.id FROM purchase_invoices pi JOIN source_files f ON f.id=pi.source_file_id"
            " WHERE pi.org_id=? AND f.sha256=? AND pi.status<>'cancelled'", (ctx.org_id, sha))
        if dup:
            raise Conflict("هذا الملف سُجّل من قبل كفاتورة", code="duplicate_file",
                           details={"invoice_id": dup["id"]})

        extracted = files.extract_structured(kind, data)
        if extracted is None:   # image or scanned PDF
            accounts.check_usage(self.db, ctx, "ai_extraction")
            extracted = self._ai_extract(data, mime)      # a failed read is not counted
            accounts.consume_usage(self.db, ctx, "ai_extraction")
        file_id, _ = self._store_file(ctx, data, filename, mime)
        return self._create_from_extraction(ctx, extracted, kind, file_id, supplier_id)

    def create_manual(self, ctx: Context, *, supplier_id: str | None = None) -> str:
        ctx.require("purchases.create")
        return self._create_from_extraction(ctx, ExtractedInvoice(method="manual"), "manual",
                                            None, supplier_id)

    def _create_from_extraction(self, ctx: Context, ex: ExtractedInvoice, kind: str,
                                file_id: str | None, supplier_id: str | None) -> str:
        if not ctx.branch_id:
            raise ValidationFailed("لا يوجد فرع محدد للمستخدم")
        supplier_id = supplier_id or catalog.find_supplier_by_name(self.db, ctx.org_id,
                                                                    ex.supplier_name)
        if not supplier_id:      # no "supplier:" label — try the letterhead text
            for t in ex.header_texts:
                supplier_id = catalog.find_supplier_by_name(self.db, ctx.org_id, t)
                if supplier_id:
                    break
        if supplier_id and ex.invoice_no:
            dup = self.db.one("SELECT id FROM purchase_invoices WHERE org_id=? AND supplier_id=?"
                              " AND invoice_no=? AND status<>'cancelled'",
                              (ctx.org_id, supplier_id, ex.invoice_no))
            if dup:
                raise Conflict("توجد فاتورة بالرقم نفسه من المورد نفسه مسجلة من قبل",
                               code="duplicate_invoice", details={"invoice_id": dup["id"]})
        inv_id = new_id()
        with self.db.transaction():
            self.db.execute(
                "INSERT INTO purchase_invoices (id, org_id, branch_id, supplier_id, supplier_name_raw,"
                " invoice_no, invoice_date, source_type, source_file_id, extraction_method,"
                " extraction_model, declared_total, extra_discount, extra_tax, raw_extraction,"
                " created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (inv_id, ctx.org_id, ctx.branch_id, supplier_id, ex.supplier_name, ex.invoice_no,
                 ex.invoice_date, kind, file_id, ex.method, ex.model, to_piasters(ex.declared_total),
                 to_piasters(ex.extra_discount) or 0, to_piasters(ex.extra_tax) or 0,
                 json.dumps(ex.to_json(), ensure_ascii=False), ctx.user_id))
            for n, l in enumerate(ex.lines, start=1):
                row = {
                    "id": new_id(), "invoice_id": inv_id, "org_id": ctx.org_id, "line_no": n,
                    "raw_name": l.raw_name, "raw_code": l.raw_code, "raw_barcode": l.raw_barcode,
                    "qty": l.qty, "bonus_qty": l.bonus_qty or 0,
                    "public_price": to_piasters(l.public_price),
                    "discount_bp": pct_to_bp(l.discount_pct),
                    "unit_net_price": to_piasters(l.unit_net_price),
                    "tax_amount": to_piasters(l.tax_amount), "line_total": to_piasters(l.line_total),
                    "batch_no": l.batch_no, "expiry_date": l.expiry_date,
                    "expiry_precision": l.expiry_precision,
                    "field_sources": _db_keys(l.sources), "field_confidence": _db_keys(l.confidence),
                }
                self._apply_match(ctx, row, supplier_id)
                validation.derive_line(row)
                self._insert_line(row)
            self._refresh_totals(ctx, inv_id)
            audit.record(self.db, ctx, "purchase.import", "purchase_invoice", inv_id,
                         after={"method": ex.method, "lines": len(ex.lines), "file_id": file_id,
                                "warnings": ex.warnings})
        return inv_id

    def _apply_match(self, ctx: Context, row: dict, supplier_id: str | None) -> None:
        r = matching.match_line(self.db, ctx.org_id, raw_name=row["raw_name"],
                                raw_code=row.get("raw_code"), raw_barcode=row.get("raw_barcode"),
                                supplier_id=supplier_id)
        row["item_id"] = r.item_id if r.status in ("auto", "suggested") else None
        row["match_method"], row["match_score"] = r.method, r.score
        row["match_status"] = r.status

    def _insert_line(self, row: dict) -> None:
        row = dict(row)
        row["field_sources"] = json.dumps(row["field_sources"])
        row["field_confidence"] = json.dumps(row["field_confidence"])
        cols = ", ".join(row)
        self.db.execute(f"INSERT INTO purchase_invoice_lines ({cols}) VALUES"
                        f" ({', '.join('?' * len(row))})", tuple(row.values()))

    def _refresh_totals(self, ctx: Context, inv_id: str) -> None:
        inv = dict(self.db.one("SELECT * FROM purchase_invoices WHERE id=?", (inv_id,)))
        lines = [dict(r) for r in self.db.all(
            "SELECT * FROM purchase_invoice_lines WHERE invoice_id=? ORDER BY line_no", (inv_id,))]
        comp = validation.computed_total(inv, lines)
        # lines being typed may still lack qty/total: sort without comparing None to int
        h = hashlib.sha256(json.dumps(sorted(
            ((normalize_text(l["raw_name"]), l["qty"], l["line_total"]) for l in lines),
            key=lambda t: (t[0], t[1] is None, t[1] or 0, t[2] is None, t[2] or 0)),
            default=str).encode()).hexdigest() if lines else None
        self.db.execute("UPDATE purchase_invoices SET computed_total=?, content_hash=? WHERE id=?",
                        (comp, h, inv_id))

    # ------------------------------------------------------------ read
    def _load(self, ctx: Context, inv_id: str) -> tuple[dict, list[dict]]:
        r = self.db.one("SELECT * FROM purchase_invoices WHERE id=? AND org_id=?",
                        (inv_id, ctx.org_id))
        if not r:
            raise NotFound("الفاتورة غير موجودة")
        lines = [dict(x) for x in self.db.all(
            "SELECT * FROM purchase_invoice_lines WHERE invoice_id=? AND org_id=? ORDER BY line_no",
            (inv_id, ctx.org_id))]
        return dict(r), lines

    def issues(self, ctx: Context, inv: dict, lines: list[dict]) -> list[validation.Issue]:
        item_ids = {l["item_id"] for l in lines if l["item_id"]}
        items = {}
        for iid in item_ids:
            it = self.db.one("SELECT id, public_price, is_drug FROM items WHERE id=? AND org_id=?",
                             (iid, ctx.org_id))
            if it:
                items[iid] = dict(it)
        dup_no = bool(inv["supplier_id"] and inv["invoice_no"] and self.db.one(
            "SELECT 1 FROM purchase_invoices WHERE org_id=? AND supplier_id=? AND invoice_no=?"
            " AND status<>'cancelled' AND id<>?",
            (ctx.org_id, inv["supplier_id"], inv["invoice_no"], inv["id"])))
        dup_content = bool(inv["content_hash"] and self.db.one(
            "SELECT 1 FROM purchase_invoices WHERE org_id=? AND content_hash=? AND status<>'cancelled'"
            " AND id<>? AND coalesce(supplier_id,'')=coalesce(?, '')",
            (ctx.org_id, inv["content_hash"], inv["id"], inv["supplier_id"])))
        return validation.validate(inv, lines, items=items, today=date.today(),
                                   short_expiry_days=self.s.short_expiry_days,
                                   total_tolerance=self.s.total_tolerance_piasters,
                                   duplicate_no=dup_no, duplicate_content=dup_content)

    def get(self, ctx: Context, inv_id: str, *, with_candidates: bool = True) -> dict:
        ctx.require("purchases.read")
        inv, lines = self._load(ctx, inv_id)
        for l in lines:
            l["field_sources"] = json.loads(l["field_sources"])
            l["field_confidence"] = json.loads(l["field_confidence"])
            if l["item_id"]:
                it = self.db.one("SELECT name_en, name_ar, strength, dosage_form, pack_size,"
                                 " public_price FROM items WHERE id=?", (l["item_id"],))
                l["item"] = dict(it) if it else None
            if with_candidates and l["match_status"] in ("suggested", "unmatched"):
                cands = matching.fuzzy_candidates(self.db, ctx.org_id, l["raw_name"], 5)
                l["candidates"] = [c.__dict__ for c in cands]
        raw_lines = [dict(x) for x in self.db.all(
            "SELECT * FROM purchase_invoice_lines WHERE invoice_id=? ORDER BY line_no", (inv_id,))]
        issues = self.issues(ctx, inv, raw_lines)
        inv["acknowledged_issues"] = json.loads(inv["acknowledged_issues"])
        inv.pop("raw_extraction", None)
        if inv["supplier_id"]:
            s = self.db.one("SELECT name FROM suppliers WHERE id=?", (inv["supplier_id"],))
            inv["supplier_name"] = s["name"] if s else None
        return {"invoice": inv, "lines": lines, "issues": [i.as_dict() for i in issues],
                "can_approve": inv["status"] == "draft" and not validation.blocking(issues)}

    def list(self, ctx: Context, status: str | None = None, limit: int = 50, date_from: str | None = None,
             date_to: str | None = None, supplier_id: str | None = None) -> list[dict]:
        ctx.require("purchases.read")
        q = ("SELECT pi.id, pi.invoice_no, pi.invoice_date, pi.status, pi.source_type,"
             " pi.declared_total, pi.computed_total, pi.created_at, s.name supplier_name,"
             " (SELECT count(*) FROM purchase_invoice_lines l WHERE l.invoice_id=pi.id) lines"
             " FROM purchase_invoices pi LEFT JOIN suppliers s ON s.id=pi.supplier_id"
             " WHERE pi.org_id=?")
        params: list = [ctx.org_id]
        if status:
            q += " AND pi.status=?"
            params.append(status)
        if supplier_id:
            q += " AND pi.supplier_id=?"
            params.append(supplier_id)
        if date_from and date_to and status != "draft":      # open drafts always show, whatever their date
            try:
                d1, d2 = date.fromisoformat(date_from), date.fromisoformat(date_to)
            except ValueError:
                raise ValidationFailed("التاريخ غير صحيح")
            q += " AND coalesce(pi.invoice_date, substr(pi.created_at,1,10)) BETWEEN ? AND ?"
            params += [d1.isoformat(), d2.isoformat()]
            limit = 5000
        q += " ORDER BY coalesce(pi.invoice_date, substr(pi.created_at,1,10)) DESC, pi.created_at DESC LIMIT ?"
        params.append(min(limit, 5000))
        return [dict(r) for r in self.db.all(q, tuple(params))]

    # ------------------------------------------------------------ edit
    def _draft_for_update(self, ctx: Context, inv_id: str, version: int | None) -> dict:
        inv = self.db.one("SELECT * FROM purchase_invoices WHERE id=? AND org_id=?",
                          (inv_id, ctx.org_id))
        if not inv:
            raise NotFound("الفاتورة غير موجودة")
        if inv["status"] != "draft":
            raise Conflict("الفاتورة معتمدة أو ملغاة ولا يمكن تعديلها", code="not_draft")
        if version is not None and inv["version"] != version:
            raise Conflict("عُدّلت الفاتورة من مستخدم آخر، حدّث الصفحة", code="stale_version",
                           details={"current_version": inv["version"]})
        return dict(inv)

    def _bump(self, inv_id: str) -> None:
        self.db.execute("UPDATE purchase_invoices SET version=version+1 WHERE id=?", (inv_id,))

    def update_header(self, ctx: Context, inv_id: str, changes: dict, version: int | None) -> dict:
        ctx.require("purchases.edit")
        bad = set(changes) - EDITABLE_HEADER_FIELDS
        if bad:
            raise ValidationFailed(f"حقول غير قابلة للتعديل: {', '.join(sorted(bad))}")
        with self.db.transaction():
            inv = self._draft_for_update(ctx, inv_id, version)
            clean = {}
            for k, v in changes.items():
                if k == "supplier_id" and v:
                    catalog.get_supplier(self.db, ctx, v)
                if k in ("declared_total", "extra_discount", "extra_tax") and v is not None:
                    try:
                        v = whole_number(v)          # piasters: 100.5 or true is refused, never cut
                    except (TypeError, ValueError):
                        raise ValidationFailed(f"قيمة غير صحيحة: {k}")
                    if v < 0:
                        raise ValidationFailed("القيمة لا تكون بالسالب")
                if k in ("extra_discount", "extra_tax") and v is None:
                    v = 0
                if k == "invoice_date" and v:
                    date.fromisoformat(v)
                clean[k] = v
            if clean:
                sets = ", ".join(f"{k}=?" for k in clean)
                try:
                    self.db.execute(f"UPDATE purchase_invoices SET {sets} WHERE id=?",
                                    (*clean.values(), inv_id))
                except sqlite3.IntegrityError as e:
                    raise Conflict("توجد فاتورة بالرقم نفسه من المورد نفسه مسجلة من قبل",
                                   code="duplicate_invoice") from e
                self._refresh_totals(ctx, inv_id)
                self._bump(inv_id)
                audit.record(self.db, ctx, "purchase.header_edit", "purchase_invoice", inv_id,
                             before={k: inv[k] for k in clean}, after=clean)
        return self.get(ctx, inv_id)

    def update_line(self, ctx: Context, inv_id: str, line_id: str, changes: dict,
                    version: int | None) -> dict:
        ctx.require("purchases.edit")
        bad = set(changes) - EDITABLE_LINE_FIELDS - {"confirm_fields"}
        if bad:
            raise ValidationFailed(f"حقول غير قابلة للتعديل: {', '.join(sorted(bad))}")
        with self.db.transaction():
            self._draft_for_update(ctx, inv_id, version)
            line = self.db.one("SELECT * FROM purchase_invoice_lines WHERE id=? AND invoice_id=?"
                               " AND org_id=?", (line_id, inv_id, ctx.org_id))
            if not line:
                raise NotFound("السطر غير موجود")
            line = dict(line)
            before = {k: line.get(k) for k in changes if k != "confirm_fields"}
            src, conf = json.loads(line["field_sources"]), json.loads(line["field_confidence"])
            upd: dict = {}
            for k, v in changes.items():
                if k == "confirm_fields":
                    # user confirms values as shown (e.g. a low-confidence AI read)
                    for f in v or []:
                        if f in EDITABLE_LINE_FIELDS or f in conf:
                            src[f], conf[f] = "user", 1.0
                    continue
                if k == "item_id":
                    if v:
                        if not self.db.one("SELECT 1 FROM items WHERE id=? AND org_id=?"
                                           " AND status='active'", (v, ctx.org_id)):
                            raise NotFound("الصنف غير موجود")
                        upd.update(item_id=v, match_status="confirmed", match_method=(
                            line["match_method"] if v == line["item_id"] and line["match_method"]
                            else "manual"))
                    else:
                        upd.update(item_id=None, match_status="unmatched", match_method=None)
                elif k in ("qty", "bonus_qty", "public_price", "discount_bp", "unit_net_price",
                           "tax_amount", "line_total"):
                    if v is not None:
                        try:
                            v = whole_number(v)      # 2.5 boxes or 12.5 piasters is refused, never cut
                        except (TypeError, ValueError):
                            raise ValidationFailed(f"قيمة غير صحيحة: {k}")
                        if v < 0 or (k == "discount_bp" and v > 10000):
                            raise ValidationFailed(f"قيمة غير صحيحة: {k}")
                    if k == "bonus_qty" and v is None:
                        v = 0
                    upd[k] = v
                elif k == "expiry_date":
                    if v:
                        iso, prec = parse_expiry(v)
                        if not iso:
                            raise ValidationFailed("تاريخ صلاحية غير مفهوم (مثال: 05/2027)")
                        upd.update(expiry_date=iso, expiry_precision=prec)
                    else:
                        upd.update(expiry_date=None, expiry_precision=None)
                elif k == "batch_no":
                    upd[k] = (str(v).strip().upper() or None) if v else None
                elif k == "raw_name":
                    if not str(v or "").strip():
                        raise ValidationFailed("اسم الصنف مطلوب")
                    upd[k] = str(v).strip()
                if k != "item_id":
                    src[k], conf[k] = "user", 1.0
                    if k == "expiry_date":
                        src["expiry_date"] = "user"
            # a user edit of a price/qty invalidates previously *inferred* values
            for dep in ("unit_net_price", "discount_bp", "line_total"):
                if dep not in changes and src.get(dep) == "inferred" and \
                        any(k in changes for k in ("public_price", "discount_bp", "unit_net_price",
                                                   "qty", "tax_amount")):
                    upd[dep] = None
                    src.pop(dep, None)
                    conf.pop(dep, None)
            merged = {**line, **upd, "field_sources": src, "field_confidence": conf}
            validation.derive_line(merged)
            for k in ("unit_net_price", "discount_bp", "line_total"):
                upd[k] = merged[k]
            upd["field_sources"] = json.dumps(merged["field_sources"])
            upd["field_confidence"] = json.dumps(merged["field_confidence"])
            sets = ", ".join(f"{k}=?" for k in upd)
            self.db.execute(f"UPDATE purchase_invoice_lines SET {sets} WHERE id=?",
                            (*upd.values(), line_id))
            self._refresh_totals(ctx, inv_id)
            self._bump(inv_id)
            audit.record(self.db, ctx, "purchase.line_edit", "purchase_invoice", inv_id,
                         before={"line_id": line_id, **before},
                         after={"line_id": line_id, **{k: changes[k] for k in changes}})
        return self.get(ctx, inv_id)

    def add_line(self, ctx: Context, inv_id: str, data: dict, version: int | None) -> dict:
        ctx.require("purchases.edit")
        with self.db.transaction():
            inv = self._draft_for_update(ctx, inv_id, version)
            n = self.db.one("SELECT coalesce(max(line_no),0)+1 n FROM purchase_invoice_lines"
                            " WHERE invoice_id=?", (inv_id,))["n"]
            name = str(data.get("raw_name") or "").strip()
            if not name and not data.get("item_id"):
                raise ValidationFailed("اسم الصنف مطلوب")
            if not name:
                it = catalog.item_row(self.db, ctx, data["item_id"])
                if not it:
                    raise NotFound("الصنف غير موجود")
                name = it["name_en"] or it["name_ar"]
            row = {"id": new_id(), "invoice_id": inv_id, "org_id": ctx.org_id, "line_no": n,
                   "raw_name": name, "raw_code": None, "raw_barcode": data.get("raw_barcode"),
                   "qty": None, "bonus_qty": 0, "public_price": None, "discount_bp": None,
                   "unit_net_price": None, "tax_amount": None, "line_total": None,
                   "batch_no": None, "expiry_date": None, "expiry_precision": None,
                   "field_sources": {"raw_name": "user"}, "field_confidence": {"raw_name": 1.0}}
            self._apply_match(ctx, row, inv["supplier_id"])
            self._insert_line(row)
            self._bump(inv_id)
            audit.record(self.db, ctx, "purchase.line_add", "purchase_invoice", inv_id,
                         after={"line_id": row["id"], "raw_name": name})
        rest = {k: v for k, v in data.items() if k in EDITABLE_LINE_FIELDS and k != "raw_name"}
        cur = self.db.one("SELECT version FROM purchase_invoices WHERE id=?", (inv_id,))["version"]
        if rest:
            return self.update_line(ctx, inv_id, row["id"], rest, cur)
        return self.get(ctx, inv_id)

    def delete_line(self, ctx: Context, inv_id: str, line_id: str, version: int | None) -> dict:
        ctx.require("purchases.edit")
        with self.db.transaction():
            self._draft_for_update(ctx, inv_id, version)
            line = self.db.one("SELECT * FROM purchase_invoice_lines WHERE id=? AND invoice_id=?"
                               " AND org_id=?", (line_id, inv_id, ctx.org_id))
            if not line:
                raise NotFound("السطر غير موجود")
            self.db.execute("DELETE FROM purchase_invoice_lines WHERE id=?", (line_id,))
            self._refresh_totals(ctx, inv_id)
            self._bump(inv_id)
            audit.record(self.db, ctx, "purchase.line_delete", "purchase_invoice", inv_id,
                         before=dict(line))
        return self.get(ctx, inv_id)

    def create_item_from_line(self, ctx: Context, inv_id: str, line_id: str, item: dict,
                              version: int | None, force: bool = False) -> dict:
        """New product the pharmacy never stocked: create it and link the line."""
        ctx.require("items.write")
        inv, lines = self._load(ctx, inv_id)
        line = next((l for l in lines if l["id"] == line_id), None)
        if not line:
            raise NotFound("السطر غير موجود")
        data = {"name_en": line["raw_name"], "public_price": line["public_price"], **item}
        barcodes = [line["raw_barcode"]] if line["raw_barcode"] else []
        item_id = catalog.create_item(self.db, ctx, data, barcodes=barcodes, force=force,
                                      source="purchase_invoice")
        return self.update_line(ctx, inv_id, line_id, {"item_id": item_id}, version)

    def acknowledge(self, ctx: Context, inv_id: str, code: str, reason: str,
                    line_id: str | None, version: int | None) -> dict:
        """Accept a known discrepancy (e.g. supplier rounding) with a written reason."""
        ctx.require("purchases.approve")
        if code not in validation.ACKNOWLEDGEABLE:
            raise ValidationFailed("هذا التنبيه لا يمكن تجاوزه")
        if len((reason or "").strip()) < 5:
            raise ValidationFailed("اكتب سبب واضح لقبول الفرق")
        with self.db.transaction():
            inv = self._draft_for_update(ctx, inv_id, version)
            acks = json.loads(inv["acknowledged_issues"])
            acks.append({"code": code, "line_id": line_id, "reason": reason.strip(),
                         "by": ctx.user_id, "at": accounts.iso(accounts.utcnow())})
            self.db.execute("UPDATE purchase_invoices SET acknowledged_issues=? WHERE id=?",
                            (json.dumps(acks, ensure_ascii=False), inv_id))
            self._bump(inv_id)
            audit.record(self.db, ctx, "purchase.issue_ack", "purchase_invoice", inv_id,
                         after=acks[-1])
        return self.get(ctx, inv_id)

    # ------------------------------------------------------------ approve
    def approve(self, ctx: Context, inv_id: str, version: int | None) -> dict:
        ctx.require("purchases.approve")
        with self.db.transaction():
            inv = self._draft_for_update(ctx, inv_id, version)
            lines = [dict(r) for r in self.db.all(
                "SELECT * FROM purchase_invoice_lines WHERE invoice_id=? ORDER BY line_no",
                (inv_id,))]
            blocking = validation.blocking(self.issues(ctx, inv, lines))
            if blocking:
                raise Conflict("لا يمكن اعتماد الفاتورة قبل حل المشكلات", code="has_blocking_issues",
                               details={"issues": [i.as_dict() for i in blocking]})
            amounts = [validation.effective_amount(l) for l in lines]
            cost_ups: list[str] = []
            price_changes: list[str] = []
            gross = sum(amounts)
            net_total = gross - inv["extra_discount"] + inv["extra_tax"]
            if net_total < 0:     # never a negative supplier balance or unit cost, whatever validation said
                raise Conflict("إجمالي الفاتورة بالسالب (الخصم الإضافي أكبر من إجمالي الأصناف)؛ لا يمكن اعتمادها",
                               code="negative_total", details={"lines_amount": gross, "total": net_total})
            moved = 0
            for l, amount in zip(lines, amounts):
                item = self.db.one("SELECT * FROM items WHERE id=? AND org_id=?",
                                   (l["item_id"], ctx.org_id))
                # allocate invoice-level discount/tax proportionally to line value
                share = Decimal(amount) * Decimal(net_total) / Decimal(gross) if gross else Decimal(0)
                packs = l["qty"] + (l["bonus_qty"] or 0)        # bonus is free stock
                base_qty = packs * item["pack_size"]
                unit_cost = (share / 100 / base_qty).quantize(Decimal("0.000001"), ROUND_HALF_UP)
                batch_id = inventory.get_or_create_batch(self.db, ctx, item["id"], l["batch_no"],
                                                         l["expiry_date"], l["expiry_precision"])
                if batch_id and l["public_price"] is not None:
                    self.db.execute("UPDATE batches SET public_price=coalesce(public_price, ?)"
                                    " WHERE id=?", (l["public_price"], batch_id))
                last = self.db.one("SELECT unit_cost FROM stock_movements WHERE org_id=? AND item_id=? AND"
                                   " movement_type='purchase' AND unit_cost IS NOT NULL ORDER BY rowid DESC LIMIT 1",
                                   (ctx.org_id, item["id"]))
                if last and Decimal(last["unit_cost"]) > 0 and unit_cost > Decimal(last["unit_cost"]) * Decimal("1.005"):
                    up_pct = (unit_cost / Decimal(last["unit_cost"]) - 1) * 100
                    pack_old = Decimal(last["unit_cost"]) * item["pack_size"]
                    pack_new = unit_cost * item["pack_size"]
                    cost_ups.append(f"{item['name_en'] or item['name_ar']}: {pack_old:.2f} ← {pack_new:.2f}"
                                    f" (+{up_pct:.1f}%)")
                inventory.post_movement(self.db, ctx, item_id=item["id"], qty_delta=base_qty,
                                        movement_type="purchase", batch_id=batch_id,
                                        unit_cost=unit_cost, ref_type="purchase_invoice",
                                        ref_id=inv_id, ref_line_id=l["id"],
                                        branch_id=inv["branch_id"])
                moved += base_qty
                if l["public_price"] is not None and l["public_price"] != item["public_price"]:
                    self.db.execute("UPDATE items SET public_price=?, updated_at=? WHERE id=?",
                                    (l["public_price"], accounts.iso(accounts.utcnow()), item["id"]))
                    catalog.record_price(self.db, ctx, item["id"], l["public_price"],
                                         item["public_price"], "purchase_invoice", inv_id)
                    price_changes.append(f"{item['name_en'] or item['name_ar']}: "
                                         f"{(item['public_price'] or 0) / 100:,.2f} ← {l['public_price'] / 100:,.2f}")
                # learn from human confirmations only
                if l["match_status"] == "confirmed" and inv["supplier_id"]:
                    catalog.add_alias(self.db, ctx, item["id"], l["raw_name"],
                                      supplier_id=inv["supplier_id"], source="learned")
                    if l["raw_code"]:
                        catalog.set_supplier_code(self.db, ctx, inv["supplier_id"], l["raw_code"],
                                                  item["id"])
                if l["raw_barcode"] and l["match_status"] == "confirmed":
                    try:
                        catalog.add_barcode(self.db, ctx, item["id"], l["raw_barcode"])
                    except DomainError:
                        pass   # barcode belongs to another item (or no items.write) -> leave for manual review;
                        # these errors are raised before anything is written, so the invoice stays whole
            now = accounts.iso(accounts.utcnow())
            self.db.execute("UPDATE purchase_invoices SET status='approved', approved_by=?,"
                            " approved_at=?, computed_total=?, version=version+1 WHERE id=?",
                            (ctx.user_id, now, net_total, inv_id))
            audit.record(self.db, ctx, "purchase.approve", "purchase_invoice", inv_id,
                         after={"lines": len(lines), "base_units": moved, "total": net_total})
            if inv["supplier_id"]:      # the supplier's account: we now owe this invoice
                self.db.execute("INSERT INTO supplier_ledger (id, org_id, supplier_id, amount, kind, ref_id, note, created_by)"
                                " VALUES (?,?,?,?,'purchase',?,?,?)", (new_id(), ctx.org_id, inv["supplier_id"], net_total,
                                                                       inv_id, f"فاتورة {inv['invoice_no'] or ''}".strip(), ctx.user_id))
            ids = [l["item_id"] for l in lines if l["item_id"]]
            if ids:                     # arrived: no longer short
                self.db.execute(f"UPDATE items SET shortage_at=NULL, shortage_by=NULL WHERE org_id=? AND id IN"
                                f" ({','.join('?' * len(ids))})", (ctx.org_id, *ids))
            label = inv["invoice_no"] or ""
            if cost_ups:
                notify.emit(self.db, ctx, "cost_increase", "warning",
                            f"سعر شراء زاد في {len(cost_ups)} صنف — فاتورة {label}".strip(),
                            "، ".join(cost_ups[:4]) + (" …" if len(cost_ups) > 4 else ""), "purchase_invoice", inv_id)
            if price_changes:
                notify.emit(self.db, ctx, "price_change", "info",
                            f"تغيّر سعر الجمهور من فاتورة شراء ({len(price_changes)} صنف)",
                            "، ".join(price_changes[:4]) + (" …" if len(price_changes) > 4 else ""),
                            "purchase_invoice", inv_id)
        matching.invalidate(ctx.org_id)
        # a recalled batch may arrive after the circular was registered
        try:
            from ..recalls.service import Recalls
            Recalls(self.db).match_new_batches(ctx)
        except Exception:   # alerting must never undo an approved invoice
            import logging
            logging.getLogger("pharm.recalls").exception("recall matching failed")
        return self.get(ctx, inv_id, with_candidates=False)

    def cancel(self, ctx: Context, inv_id: str, reason: str, version: int | None) -> dict:
        if len((reason or "").strip()) < 5:
            raise ValidationFailed("اكتب سبب الإلغاء")
        with self.db.transaction():
            inv = self.db.one("SELECT * FROM purchase_invoices WHERE id=? AND org_id=?",
                              (inv_id, ctx.org_id))
            if not inv:
                raise NotFound("الفاتورة غير موجودة")
            if version is not None and inv["version"] != version:
                raise Conflict("عُدّلت الفاتورة، حدّث الصفحة", code="stale_version")
            if inv["status"] == "cancelled":
                raise Conflict("الفاتورة ملغاة بالفعل")
            if inv["status"] == "approved":
                ctx.require("purchases.cancel")
                moves = self.db.all("SELECT * FROM stock_movements WHERE ref_type='purchase_invoice'"
                                    " AND ref_id=? AND movement_type='purchase'", (inv_id,))
                for m in moves:
                    inventory.post_movement(self.db, ctx, item_id=m["item_id"],
                                            qty_delta=-m["qty_delta"],
                                            movement_type="purchase_reversal",
                                            batch_id=m["batch_id"], unit_cost=Decimal(m["unit_cost"]),
                                            ref_type="purchase_invoice", ref_id=inv_id,
                                            ref_line_id=m["ref_line_id"], branch_id=m["branch_id"],
                                            note=reason)
            else:
                ctx.require("purchases.edit")
            self.db.execute("UPDATE purchase_invoices SET status='cancelled', cancelled_by=?,"
                            " cancelled_at=?, cancel_reason=?, version=version+1 WHERE id=?",
                            (ctx.user_id, accounts.iso(accounts.utcnow()), reason.strip(), inv_id))
            if inv["status"] == "approved" and inv["supplier_id"]:
                posted = self.db.one("SELECT coalesce(sum(amount),0) a FROM supplier_ledger WHERE ref_id=? AND kind='purchase'",
                                     (inv_id,))["a"]
                if posted:
                    self.db.execute("INSERT INTO supplier_ledger (id, org_id, supplier_id, amount, kind, ref_id, note,"
                                    " created_by) VALUES (?,?,?,?,'adjustment',?,?,?)",
                                    (new_id(), ctx.org_id, inv["supplier_id"], -posted, inv_id,
                                     f"إلغاء فاتورة {inv['invoice_no'] or ''}".strip(), ctx.user_id))
            audit.record(self.db, ctx, "purchase.cancel", "purchase_invoice", inv_id,
                         before={"status": inv["status"]}, after={"reason": reason})
        return self.get(ctx, inv_id, with_candidates=False)
