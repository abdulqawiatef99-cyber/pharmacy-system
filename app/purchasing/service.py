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
from fractions import Fraction
from datetime import date
from decimal import Decimal
from pathlib import Path, PurePosixPath

from .. import config as config_mod
from ..catalog import gs1, matching, service as catalog
from ..catalog.normalize import normalize_barcode, normalize_text
from ..core import accounts, audit, notify
from ..core.errors import Conflict, DomainError, NotFound, ValidationFailed
from ..core.numbers import MAX_SAFE, whole_number
from ..core.permissions import Context
from ..db import Database, new_id
from ..inventory import service as inventory
from . import entry_settings, sanity, validation
from .importers import ai_vision, files
from .model import ExtractedInvoice, parse_expiry, pct_to_bp, to_piasters

EDITABLE_LINE_FIELDS = {"item_id", "qty", "bonus_qty", "public_price", "discount_bp",
                        "unit_net_price", "tax_amount", "tax_included", "line_total", "batch_no", "expiry_date",
                        "raw_name", "distributor_margin"}
EDITABLE_HEADER_FIELDS = {"supplier_id", "invoice_no", "invoice_date", "declared_total",
                          "extra_discount", "extra_tax", "notes", "pay_source", "pay_amount", "pay_ref"}
PAY_CREDIT = "credit"          # on the supplier's account (آجل)


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
        """The original of a purchase invoice. Only that: every stored file of the pharmacy used to be served
        here, so `purchases.read` opened another cashier's transfer proof (#324 X14)."""
        ctx.require("purchases.read")
        r = self.db.one("SELECT f.* FROM source_files f WHERE f.id=? AND f.org_id=? AND EXISTS (SELECT 1 FROM"
                        " purchase_invoices pi WHERE pi.org_id=f.org_id AND pi.source_file_id=f.id)",
                        (file_id, ctx.org_id))
        if not r:
            raise NotFound("الملف غير موجود")
        # the registered path is a record of where the file was written, not where to read it: after a restore
        # into another folder (or on another PC) the old absolute path may still exist, and reading it made a
        # restore look complete with the restored copy missing (#324 X26). Same layout as the backup uses.
        stored = PurePosixPath(str(r["storage_path"]).replace("\\", "/"))
        sub = ("transfers",) if stored.parent.name == "transfers" else ()
        path = self.s.upload_dir.joinpath(r["org_id"], *sub, stored.name)
        if stored.name in ("", ".", "..") or not path.is_file():
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
        from ..sales.service import local_today       # update 1 (owner): the supplier invoice date starts as today
        return self._create_from_extraction(ctx, ExtractedInvoice(method="manual", invoice_date=local_today().isoformat()),
                                            "manual", None, supplier_id)

    def _create_from_extraction(self, ctx: Context, ex: ExtractedInvoice, kind: str,
                                file_id: str | None, supplier_id: str | None) -> str:
        if not ctx.branch_id:
            raise ValidationFailed("لا يوجد فرع محدد للمستخدم")
        if supplier_id:      # a supplier id typed by the caller: this pharmacy's own, or nothing is created (#324 S5/X11)
            self._own_supplier(ctx, supplier_id)
        # a supplier name read with low confidence is never linked by itself: the invoice's liability would go
        # to whoever it guessed; the person picks the supplier (supplier_missing blocks approval, #324 S24)
        guessed = (ex.header_confidence or {}).get("supplier_name")
        unsure = guessed is not None and guessed < validation.LOW_CONFIDENCE
        supplier_id = supplier_id or (None if unsure else catalog.find_supplier_by_name(self.db, ctx.org_id,
                                                                                        ex.supplier_name))
        if not supplier_id and not unsure:      # no "supplier:" label — try the letterhead text
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
            if file_id:
                # asked again under the write lock: two uploads of one file both passed the check in
                # import_file before either draft existed, and both could be approved (#324 S18)
                dup = self.db.one(
                    "SELECT pi.id FROM purchase_invoices pi JOIN source_files f ON f.id=pi.source_file_id"
                    " WHERE pi.org_id=? AND pi.status<>'cancelled' AND f.sha256=(SELECT sha256 FROM source_files"
                    " WHERE id=? AND org_id=?)", (ctx.org_id, file_id, ctx.org_id))
                if dup:
                    raise Conflict("هذا الملف سُجّل من قبل كفاتورة", code="duplicate_file",
                                   details={"invoice_id": dup["id"]})
            # the program's own serial (0041, owner: shown next to the supplier's invoice number), per branch
            self.db.execute("INSERT INTO branch_counters (org_id, branch_id, kind, next_no) VALUES (?,?,'purchase',1)"
                            " ON CONFLICT(org_id, branch_id, kind) DO NOTHING", (ctx.org_id, ctx.branch_id))
            serial = self.db.one("SELECT next_no FROM branch_counters WHERE org_id=? AND branch_id=? AND kind='purchase'",
                                 (ctx.org_id, ctx.branch_id))["next_no"]
            self.db.execute("UPDATE branch_counters SET next_no=next_no+1 WHERE org_id=? AND branch_id=? AND kind='purchase'",
                            (ctx.org_id, ctx.branch_id))
            self.db.execute(
                "INSERT INTO purchase_invoices (id, org_id, branch_id, supplier_id, supplier_name_raw,"
                " invoice_no, invoice_date, source_type, source_file_id, extraction_method,"
                " extraction_model, declared_total, extra_discount, extra_tax, raw_extraction,"
                " created_by, serial_no) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (inv_id, ctx.org_id, ctx.branch_id, supplier_id, ex.supplier_name, ex.invoice_no,
                 ex.invoice_date, kind, file_id, ex.method, ex.model, to_piasters(ex.declared_total),
                 to_piasters(ex.extra_discount) or 0, to_piasters(ex.extra_tax) or 0,
                 json.dumps(ex.to_json(), ensure_ascii=False), ctx.user_id, serial))
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

    def _own_supplier(self, ctx: Context, supplier_id: str) -> None:
        if not self.db.one("SELECT 1 FROM suppliers WHERE id=? AND org_id=?", (supplier_id, ctx.org_id)):
            raise NotFound("المورد غير موجود")

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
        inv = dict(self.db.one("SELECT * FROM purchase_invoices WHERE id=? AND org_id=?", (inv_id, ctx.org_id)))
        lines = [dict(r) for r in self.db.all(
            "SELECT * FROM purchase_invoice_lines WHERE invoice_id=? AND org_id=? ORDER BY line_no",
            (inv_id, ctx.org_id))]
        comp = validation.computed_total(inv, lines)
        if comp is not None and abs(comp) > MAX_SAFE:
            raise ValidationFailed("إجمالي الفاتورة أكبر من الحد المسموح")
        # lines being typed may still lack qty/total: sort without comparing None to int
        h = hashlib.sha256(json.dumps(sorted(
            # the distributor's margin only when there is one: invoices without it keep the hash they had (B3)
            ((normalize_text(l["raw_name"]), l["qty"], l["line_total"], *([l["distributor_margin"]]
              if l.get("distributor_margin") else [])) for l in lines),
            key=lambda t: (t[0], t[1] is None, t[1] or 0, t[2] is None, t[2] or 0, t[3:])),     # margin last (Codex P2 #440)
            default=str).encode()).hexdigest() if lines else None
        self.db.execute("UPDATE purchase_invoices SET computed_total=?, content_hash=? WHERE id=?",
                        (comp, h, inv_id))

    # ------------------------------------------------------------ read
    def receiving_context(self, ctx: Context, inv_id: str) -> Context:
        """Use the branch that receives the invoice's stock for contextual reads."""
        from dataclasses import replace
        ctx.require("purchases.read")
        r = self.db.one("SELECT branch_id FROM purchase_invoices WHERE id=? AND org_id=?", (inv_id, ctx.org_id))
        if not r:
            raise NotFound("الفاتورة غير موجودة")
        return replace(ctx, branch_id=r["branch_id"])

    def scan_item(self, ctx: Context, inv_id: str, raw: str) -> dict:
        ctx.require("purchases.edit")
        ctx.require("items.read")
        receiving = self.receiving_context(ctx, inv_id)
        raw = raw.strip()[:100]
        code = gs1.parse(raw)
        barcode = code["gtin"] if code else normalize_barcode(raw) if raw.isdecimal() else None
        if not barcode:
            return {"items": [], "hint": None}
        keys = gs1.gtin_keys(barcode)
        rows = self.db.all(
            f"SELECT DISTINCT i.* FROM item_barcodes b JOIN items i ON i.id=b.item_id AND i.org_id=b.org_id"
            f" WHERE b.org_id=? AND b.barcode IN ({','.join('?' * len(keys))}) AND i.status='active'"
            " AND (i.hidden=0 OR ?)",
            (ctx.org_id, *keys, ctx.can("items.hidden")))
        items = [dict(row) for row in rows]
        if ctx.can("inventory.read"):
            for item in items:
                item["stock_qty"] = self.db.one(
                    "SELECT coalesce(sum(qty),0) q FROM stock_balances WHERE org_id=? AND branch_id=? AND item_id=?",
                    (ctx.org_id, receiving.branch_id, item["id"]))["q"]
        return {"items": items, "hint": {"kind": "gs1", **code} if code else None}

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
        from ..sales.service import local_today
        item_ids = {l["item_id"] for l in lines if l["item_id"]}
        items = {}
        for iid in item_ids:
            it = self.db.one("SELECT id, public_price, is_drug, pack_size FROM items WHERE id=? AND org_id=?",
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
        out = validation.validate(inv, lines, items=items, today=local_today(),
                                  short_expiry_days=self.s.short_expiry_days,
                                  total_tolerance=self.s.total_tolerance_piasters,
                                  duplicate_no=dup_no, duplicate_content=dup_content)
        out += self._slow_before_expiry(ctx, inv, lines)
        if inv["status"] == "draft":
            out += sanity.unusual_issues(self.db, inv, lines, items, entry_settings.get(self.db, ctx.org_id))
        return out

    NEAR_DAYS = 90          # an item is "near expiry" from 3 months before its date (U2-22)

    def _slow_before_expiry(self, ctx: Context, inv: dict, lines: list[dict]) -> list[validation.Issue]:
        """U2-22: from the last 90 days' sales, how much of this line will sell before it gets near its expiry.
        More than that needs a written reason to approve. Items never sold here are left out (a new item would
        always be flagged)."""
        from ..procurement.service import Procurement
        from ..sales.service import local_today
        today = local_today()
        acks = {(a.get("code"), a.get("line_id") or "", json.dumps(a.get("details") or {}, sort_keys=True))
                for a in json.loads(inv.get("acknowledged_issues") or "[]")}
        p, out, by_item = Procurement(self.db, today=today), [], {}
        if inv.get("branch_id") and inv["branch_id"] != ctx.branch_id:      # the branch that receives the goods
            from dataclasses import replace as _replace                       # (Codex P2 on #406)
            ctx = _replace(ctx, branch_id=inv["branch_id"])
        for l in lines:
            if l.get("item_id") and l.get("expiry_date") and l.get("qty") and l["expiry_date"] >= today.isoformat():
                by_item.setdefault(l["item_id"], []).append(l)       # expired lines: refused by "expired" already
        for item_id, group in by_item.items():
            it = self.db.one("SELECT pack_size, is_service FROM items WHERE id=? AND org_id=?", (item_id, ctx.org_id))
            if not it or it["is_service"]:
                continue
            daily = p._usage(ctx, item_id, 90)["daily"]
            if daily <= 0:
                continue
            ahead = max(p._sellable(ctx, item_id)[0], 0)       # the shelf sells first, then the lines, earliest expiry first
            for l in sorted(group, key=lambda x: (x["expiry_date"], x.get("line_no") or 0)):
                days = (date.fromisoformat(l["expiry_date"]) - today).days
                qty = (l["qty"] + (l["bonus_qty"] or 0)) * it["pack_size"]
                rate = Fraction(daily).limit_denominator(1_000_000)          # 13/90, not 0.14444…
                cap = rate * max(0, days - self.NEAR_DAYS)
                will = max(0, int(cap + Fraction(1, 2)) - ahead)             # half up
                ahead += qty
                if will >= qty:
                    continue
                # the match keeps only what the person accepted (this expiry, this quantity): the sales of the day
                # and the date moving on do not undo the acceptance (independent review)
                details = {"item_id": item_id, "expiry": l["expiry_date"], "qty": qty}   # a re-matched item re-asks
                i = validation.Issue("error", "slow_before_expiry",
                                     f"هتبيع حوالي {min(will, qty)} من {qty} وحدة قبل ما يقرب ينتهي (البيع حوالي"
                                     f" {daily * 30:.0f} في الشهر، والصلاحية بعد {days // 30} شهر): اكتب السبب",
                                     l["id"], "expiry_date", details)
                i.acknowledged = ("slow_before_expiry", l["id"], json.dumps(details, sort_keys=True)) in acks
                out.append(i)
        return out

    def _pay_now(self, ctx: Context, inv: dict, net_total: int) -> dict | None:
        """What approval pays now, refused before any write: more than the invoice is impossible (an advance goes
        through the payments screen), a transfer / wallet needs its reference, the person needs the right to pay
        suppliers and to use that money place, and the drawer needs an open shift with the cash in it."""
        plan = self.pay_plan(ctx, inv)
        if plan["source"] == PAY_CREDIT:
            return None
        amount = net_total if plan["amount"] is None else plan["amount"]
        if amount > net_total:
            raise ValidationFailed(f"المدفوع ({amount / 100:,.2f} ج) أكبر من إجمالي الفاتورة ({net_total / 100:,.2f} ج):"
                                   " مستحيل، والدفعة المقدمة من شاشة دفع الموردين", code="pay_above_total")
        if amount <= 0:
            return None
        if not inv["supplier_id"]:
            raise ValidationFailed("حدد المورد قبل الدفع")
        from ..procurement import suppliers
        due = suppliers.balance(self.db, ctx.org_id, inv["supplier_id"]) + net_total
        if amount > max(due, 0):     # the supplier already holds money of ours: that credit is used first (review P2)
            raise ValidationFailed(f"المورد عنده رصيد لينا؛ المستحق بعد الفاتورة دي {max(due, 0) / 100:,.2f} ج بس."
                                   " اكتب المدفوع دلوقتي بحد أقصى ده أو اختار آجل", code="pay_above_due",
                                   details={"due": max(due, 0), "amount": amount})
        ctx.require("suppliers.pay")
        from ..finance import money
        src = money.check_source(self.db, ctx, plan["source"])
        if src == money.DRAWER:
            money.drawer_check(self.db, ctx, amount)       # an open shift, and never below zero
        elif src != money.OUTSIDE and not plan["ref"] and \
                money._account(self.db, ctx, src)["kind"] in ("bank", "wallet"):    # the kind only, no balance (Codex P2)
            raise ValidationFailed("اكتب رقم التحويل أو العملية في «مرجع الدفع»", code="pay_ref_required")
        return {**plan, "amount": amount}

    def pay_plan(self, ctx: Context, inv: dict) -> dict:
        """«طريقة الدفع» as approval will apply it: the chosen source, or the default by supplier type (a warehouse
        is paid cash from the drawer, anyone else on the account); amount None = the whole invoice."""
        src = inv.get("pay_source")
        default = src is None and inv.get("status", "draft") == "draft"
        if src is None and not default:
            src = PAY_CREDIT          # approved before the payment method existed: it went on the account
        if default:
            t = self.db.one("SELECT supplier_type FROM suppliers WHERE id=? AND org_id=?",
                            (inv.get("supplier_id"), ctx.org_id)) if inv.get("supplier_id") else None
            src = "drawer" if t and t["supplier_type"] == "warehouse" else PAY_CREDIT
        return {"source": src, "amount": None if src == PAY_CREDIT else inv.get("pay_amount"),
                "ref": inv.get("pay_ref"), "default": default}

    def get(self, ctx: Context, inv_id: str, *, with_candidates: bool = True) -> dict:
        ctx.require("purchases.read")
        inv, lines = self._load(ctx, inv_id)
        issues = self.issues(ctx, inv, lines)
        items = {}
        for l in lines:
            l["field_sources"] = json.loads(l["field_sources"])
            l["field_confidence"] = json.loads(l["field_confidence"])
            if l["item_id"]:
                iid = l["item_id"]
                if iid not in items:
                    it = self.db.one(
                        "SELECT name_en, name_ar, strength, dosage_form, pack_size, code,"
                        " pack_unit_name, base_unit_name, public_price,"
                        " (SELECT coalesce(sum(qty),0) FROM stock_balances"
                        " WHERE org_id=i.org_id AND branch_id=? AND item_id=i.id) stock_qty"
                        " FROM items i WHERE id=? AND org_id=?", (inv["branch_id"], iid, ctx.org_id))
                    items[iid] = dict(it) if it else None
                item = items[iid]
                l["item"] = {k: v for k, v in item.items() if k != "stock_qty"} if item else None
                l["stock_qty"] = item["stock_qty"] if item and ctx.can("inventory.read") else None
            if with_candidates and l["match_status"] in ("suggested", "unmatched"):
                cands = matching.fuzzy_candidates(self.db, ctx.org_id, l["raw_name"], 5)
                l["candidates"] = [c.__dict__ for c in cands]
        inv["acknowledged_issues"] = json.loads(inv["acknowledged_issues"])
        inv.pop("raw_extraction", None)
        inv["pay"] = self.pay_plan(ctx, inv)
        if inv["supplier_id"]:
            s = self.db.one("SELECT name, supplier_type FROM suppliers WHERE id=? AND org_id=?",
                            (inv["supplier_id"], ctx.org_id))
            inv["supplier_name"] = s["name"] if s else None
            inv["supplier_type"] = s["supplier_type"] if s else None
        return {"invoice": inv, "lines": lines, "issues": [i.as_dict() for i in issues],
                "entry_order": entry_settings.get(self.db, ctx.org_id)["field_order"],
                "can_approve": inv["status"] == "draft" and not validation.blocking(issues)}

    def list(self, ctx: Context, status: str | None = None, limit: int = 50, date_from: str | None = None,
             date_to: str | None = None, supplier_id: str | None = None) -> list[dict]:
        ctx.require("purchases.read")
        q = ("SELECT pi.id, pi.serial_no, pi.invoice_no, pi.invoice_date, pi.status, pi.source_type,"
             " pi.declared_total, pi.computed_total, pi.created_at, pi.approved_at, s.name supplier_name,"
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
                if k == "pay_amount" and v is not None:
                    try:
                        v = whole_number(v)
                    except (TypeError, ValueError):
                        raise ValidationFailed("المبلغ المدفوع غير صحيح")
                    if v < 0:
                        raise ValidationFailed("المبلغ المدفوع لا يكون بالسالب")
                if k == "pay_source":
                    v = (str(v).strip() or None) if v is not None else None
                    if v not in (None, PAY_CREDIT, "drawer", "treasury", "outside"):
                        from ..finance import money
                        try:      # an active account of this pharmacy, stored by its id; who may pay is checked at approval
                            v = money.resolve(self.db, ctx, v)
                            money._account(self.db, ctx, v)
                        except NotFound:
                            raise ValidationFailed("اختر طريقة الدفع من القائمة", code="unknown_source")
                if k == "pay_ref":
                    v = (str(v or "").strip()[:60]) or None
                if k == "invoice_date" and v:
                    try:
                        d = date.fromisoformat(v)
                    except (TypeError, ValueError):
                        raise ValidationFailed("تاريخ فاتورة المورد غير صحيح")
                    from ..sales.service import local_today
                    if d > local_today():          # U2-0: a supplier invoice is never dated after today
                        raise ValidationFailed("تاريخ فاتورة المورد بعد النهارده: مستحيل، راجع التاريخ",
                                               code="invoice_date_future")
                    v = d.isoformat()
                clean[k] = v
            if clean:
                sets = ", ".join(f"{k}=?" for k in clean)
                try:
                    self.db.execute(f"UPDATE purchase_invoices SET {sets} WHERE id=?",
                                    (*clean.values(), inv_id))
                except sqlite3.IntegrityError as e:
                    raise Conflict("توجد فاتورة بالرقم نفسه من المورد نفسه مسجلة من قبل",
                                   code="duplicate_invoice") from e
                after = dict(clean)
                if "supplier_id" in clean and (clean["supplier_id"] or None) != (inv["supplier_id"] or None):
                    rematched = self._rematch(ctx, inv_id, clean["supplier_id"] or None)
                    if rematched:
                        after["rematched"] = rematched
                self._refresh_totals(ctx, inv_id)
                self._bump(inv_id)
                audit.record(self.db, ctx, "purchase.header_edit", "purchase_invoice", inv_id,
                             before={k: inv[k] for k in clean}, after=after)
        return self.get(ctx, inv_id)

    def _rematch(self, ctx: Context, inv_id: str, supplier_id: str | None) -> list[dict]:
        """The supplier was corrected: a line matched through the old supplier's own codes or names is matched
        again as if the invoice had been read under the right one (#324 S19: SKU9 is the 100 mg item at one
        supplier and the 200 mg item at another). A line a person confirmed keeps its item."""
        changed = []
        for l in self.db.all("SELECT id, raw_name, raw_code, raw_barcode, item_id, match_method, match_status"
                             " FROM purchase_invoice_lines WHERE invoice_id=? AND org_id=? AND match_status<>'confirmed'"
                             " ORDER BY line_no", (inv_id, ctx.org_id)):
            row = {"raw_name": l["raw_name"], "raw_code": l["raw_code"], "raw_barcode": l["raw_barcode"]}
            self._apply_match(ctx, row, supplier_id)
            if (row["item_id"], row["match_method"], row["match_status"]) == \
                    (l["item_id"], l["match_method"], l["match_status"]):
                continue
            self.db.execute("UPDATE purchase_invoice_lines SET item_id=?, match_method=?, match_score=?, match_status=?"
                            " WHERE id=? AND org_id=?", (row["item_id"], row["match_method"], row["match_score"],
                                                         row["match_status"], l["id"], ctx.org_id))
            changed.append({"line_id": l["id"], "from": l["item_id"], "to": row["item_id"]})
        return changed

    def update_line(self, ctx: Context, inv_id: str, line_id: str, changes: dict,
                    version: int | None) -> dict:
        ctx.require("purchases.edit")
        bad = set(changes) - EDITABLE_LINE_FIELDS - {"confirm_fields"}
        if bad:
            raise ValidationFailed(f"حقول غير قابلة للتعديل: {', '.join(sorted(bad))}")
        with self.db.transaction():
            inv = self._draft_for_update(ctx, inv_id, version)
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
                elif k in validation.NUMERIC_FIELDS:
                    if v is not None:
                        _, minimum, maximum = validation.NUMERIC_FIELDS[k]
                        try:
                            v = whole_number(v)      # 2.5 boxes or 12.5 piasters is refused, never cut
                        except (TypeError, ValueError):
                            raise ValidationFailed(validation.numeric_message(k))
                        if not minimum <= v <= maximum:
                            raise ValidationFailed(validation.numeric_message(k))
                    if k in ("bonus_qty", "distributor_margin") and v is None:
                        v = 0
                    upd[k] = v
                elif k == "tax_included":
                    if v is not None and (type(v) is not int or v not in (0, 1)):
                        raise ValidationFailed("اختار إذا كانت ضريبة السطر مضافة أو مشمولة")
                    upd[k] = v
                elif k == "expiry_date":
                    if v:
                        iso, prec = parse_expiry(v, compact=True)
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
                if dep not in changes and src.get(dep) in ("inferred", "allocated") and \
                        any(k in changes for k in ("public_price", "discount_bp", "unit_net_price",
                                                   "qty", "tax_amount", "tax_included", "distributor_margin")):
                    upd[dep] = None
                    src.pop(dep, None)
                    conf.pop(dep, None)
            merged = {**line, **upd, "field_sources": src, "field_confidence": conf}
            validation.derive_line(merged)
            from ..sales.service import local_today
            for code, _, related in validation.impossible_line(merged, local_today(), tax_only=inv["extra_tax"] > 0):
                if related.intersection(changes):
                    raise ValidationFailed(validation.MESSAGES[code], code=code)
            amount = validation.effective_amount(merged)
            if amount is not None and abs(amount) > MAX_SAFE:
                raise ValidationFailed("إجمالي السطر أكبر من الحد المسموح")
            if merged["item_id"] and merged["qty"]:
                item = catalog.item_row(self.db, ctx, merged["item_id"])
                if (merged["qty"] + merged["bonus_qty"]) * item["pack_size"] > MAX_SAFE:
                    raise ValidationFailed("الكمية مع البونص أكبر من الحد المسموح للصنف")
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

    def add_line(self, ctx: Context, inv_id: str, data: dict, version: int | None, *, picked: bool = False) -> dict:
        ctx.require("purchases.edit")
        # one transaction for the line and its values: a refused value (2.5 packs) used to leave the blank
        # line and a new version behind an answer that said "refused" (#324 S34)
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
                   "unit_net_price": None, "tax_amount": None, "line_total": None, "distributor_margin": 0,
                   "batch_no": None, "expiry_date": None, "expiry_precision": None,
                   "field_sources": {"raw_name": "user"}, "field_confidence": {"raw_name": 1.0}}
            if picked and data.get("item_id") and not data.get("raw_name"):
                # The picker already chose the item. Other callers still go through supplier alias matching.
                row.update(item_id=None, match_method=None, match_score=None, match_status="unmatched")
            else:
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

    def merge_lines(self, ctx: Context, inv_id: str, line_id: str, other_id: str, version: int | None) -> dict:
        """U2-3: two lines of the same item, batch, expiry and prices become one (quantities, bonus, totals and
        tax added). Anything that differs is refused: those are two real lots, not a duplicate."""
        ctx.require("purchases.edit")
        with self.db.transaction():
            self._draft_for_update(ctx, inv_id, version)
            a, b = (self.db.one("SELECT * FROM purchase_invoice_lines WHERE id=? AND invoice_id=? AND org_id=?",
                                (x, inv_id, ctx.org_id)) for x in (line_id, other_id))
            if not a or not b or line_id == other_id:
                raise NotFound("السطر غير موجود")
            keys = ("item_id", "public_price", "discount_bp", "unit_net_price", "expiry_date", "expiry_precision",
                    "distributor_margin")
            # a value still waiting for a person (low-confidence reading, unconfirmed match) is never merged away
            # (Codex P1 on #406): it is confirmed first
            if a["qty"] is None or b["qty"] is None:
                raise ValidationFailed("اكتب كمية السطرين الأول وبعدين ادمج", code="not_mergeable")
            for ln in (a, b):
                if ln["tax_included"] is not None and validation.line_total_mismatch(dict(ln)):
                    raise ValidationFailed("صحّح ضريبة السطر وإجماليه قبل الدمج", code="line_total_mismatch")
                src, conf = json.loads(ln["field_sources"] or "{}"), json.loads(ln["field_confidence"] or "{}")
                if ln["match_status"] == "suggested" or any(c < validation.LOW_CONFIDENCE and src.get(f) != "user"
                                                            for f, c in conf.items()):
                    raise ValidationFailed("في السطرين قيمة لسه محتاجة مراجعة؛ أكّدها الأول وبعدين ادمج",
                                           code="not_mergeable")
            if not a["item_id"] or a["tax_included"] != b["tax_included"] or any(a[k] != b[k] for k in keys) or \
                    (a["batch_no"] or "").strip().upper() != (b["batch_no"] or "").strip().upper():
                raise ValidationFailed("السطرين مش متطابقين (الصنف والتشغيلة والصلاحية والسعر وهامش الموزع): مينفعش يتدمجوا",
                                       code="not_mergeable")
            # in payable amounts (validation.effective_amount): a total printed without tax on one line and with
            # tax on the other must not change what the invoice pays (Codex P1 on #406)
            # without the distributor's margin, which is per pack and follows the quantity (B3)
            pay_a, pay_b = validation.line_amount(dict(a)), validation.line_amount(dict(b))
            tax = None if a["tax_amount"] is None and b["tax_amount"] is None else (a["tax_amount"] or 0) + (b["tax_amount"] or 0)
            merged = {**dict(a), "qty": a["qty"] + b["qty"], "tax_amount": tax,
                      "line_total": None if a["line_total"] is None and b["line_total"] is None
                      else (pay_a + pay_b if pay_a is not None and pay_b is not None else None)}
            if (pay_a is None) != (pay_b is None) or (merged["line_total"] is None and (a["line_total"], b["line_total"]) != (None, None)) \
                    or (pay_a is not None and validation.line_amount(merged) != pay_a + pay_b):
                raise ValidationFailed("السطرين مش هيتدمجوا من غير ما إجمالي الفاتورة يتغير؛ عدّل الكمية يدويًا", code="not_mergeable")
            sources = json.loads(a["field_sources"])
            if a["tax_included"] is not None and merged["line_total"] is not None:
                sources["line_total"] = "allocated"
            self.db.execute("UPDATE purchase_invoice_lines SET qty=?, bonus_qty=?, line_total=?, tax_amount=?, field_sources=?"
                            " WHERE id=? AND org_id=?",
                            (merged["qty"], (a["bonus_qty"] or 0) + (b["bonus_qty"] or 0), merged["line_total"], tax,
                             json.dumps(sources), line_id, ctx.org_id))
            self.db.execute("DELETE FROM purchase_invoice_lines WHERE id=? AND org_id=?", (other_id, ctx.org_id))
            self._refresh_totals(ctx, inv_id)
            self._bump(inv_id)
            audit.record(self.db, ctx, "purchase.line_merge", "purchase_invoice", inv_id,
                         before={"kept": dict(a), "merged": dict(b)})
        return self.get(ctx, inv_id)

    def split_line(self, ctx: Context, inv_id: str, line_id: str, qty: int, expiry, batch_no: str | None,
                   version: int | None) -> dict:
        """U2-9 «+ صلاحية»: part of a line's paid quantity has another expiry. It becomes its own line (its own lot)
        with the same item and prices; the original keeps the rest. The bonus stays on the original line."""
        ctx.require("purchases.edit")
        if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
            raise ValidationFailed("الكمية لازم رقم صحيح أكبر من صفر")
        iso, prec = parse_expiry(expiry, compact=True)
        if not iso:
            raise ValidationFailed("الصلاحية مش مفهومة (مثال: 1128 أو 11/2028)")
        from ..sales.service import local_today
        if date.fromisoformat(iso) < local_today():
            raise ValidationFailed(validation.MESSAGES["expired"], code="expired")
        with self.db.transaction():
            inv = self._draft_for_update(ctx, inv_id, version)
            a = self.db.one("SELECT * FROM purchase_invoice_lines WHERE id=? AND invoice_id=? AND org_id=?",
                            (line_id, inv_id, ctx.org_id))
            if not a:
                raise NotFound("السطر غير موجود")
            if not a["qty"] or qty >= a["qty"]:
                raise ValidationFailed(f"الكمية لازم أقل من كمية السطر ({a['qty'] or 0})", code="split_too_big")
            if iso == a["expiry_date"]:
                raise ValidationFailed("دي نفس صلاحية السطر")
            if a["tax_included"] is not None and validation.line_total_mismatch(dict(a)):
                raise ValidationFailed("صحّح ضريبة السطر وإجماليه قبل التقسيم", code="line_total_mismatch")
            # whole piasters: the new line takes its share rounded down, the original keeps the rest, so the two always
            # add up to what the line had (independent review: a None share dropped the tax from the invoice)
            half_up = lambda v: None if v is None else (2 * v * qty + a["qty"]) // (2 * a["qty"])   # noqa: E731
            # the line's own amount; the distributor's margin is per pack and follows each part's quantity (B3)
            pay = validation.line_amount(dict(a))
            moved_tax = half_up(a["tax_amount"])
            # the new line takes its share of what the line PAYS (tax included, half up); the original keeps the rest,
            # so the invoice total cannot move (Codex P1 on #406: raw total and tax split apart re-read the tax)
            moved_total = None if a["line_total"] is None else half_up(pay)
            n = self.db.one("SELECT coalesce(max(line_no),0)+1 n FROM purchase_invoice_lines WHERE invoice_id=? AND org_id=?",
                            (inv_id, ctx.org_id))["n"]
            row = {**dict(a), "id": new_id(), "line_no": n, "qty": qty, "bonus_qty": 0, "line_total": moved_total,
                   "tax_amount": moved_tax, "expiry_date": iso, "expiry_precision": prec,
                   "batch_no": (str(batch_no).strip() or None) if batch_no else None,
                   "field_sources": json.loads(a["field_sources"] or "{}"), "field_confidence": json.loads(a["field_confidence"] or "{}")}
            row["field_sources"].update(expiry_date="user", qty="user")
            rest_sources = json.loads(a["field_sources"])
            if a["tax_included"] is not None and moved_total is not None:
                row["field_sources"]["line_total"] = rest_sources["line_total"] = "allocated"
            self._insert_line(row)
            rest = {**dict(a), "qty": a["qty"] - qty,
                    "line_total": None if a["line_total"] is None else pay - moved_total,
                    "tax_amount": None if a["tax_amount"] is None else a["tax_amount"] - moved_tax}
            if a["line_total"] is not None and pay is not None:
                # each part stores the total that makes it pay exactly its share: as printed with tax, or, when the
                # validator would read that one as "before tax", the same share minus its tax
                for part, share in ((row, moved_total), (rest, pay - moved_total)):
                    for cand in (share, share - (part["tax_amount"] or 0)):
                        if validation.line_amount({**part, "line_total": cand}) == share:
                            part["line_total"] = cand
                            break
                self.db.execute("UPDATE purchase_invoice_lines SET line_total=? WHERE id=? AND org_id=?",
                                (row["line_total"], row["id"], ctx.org_id))
            if pay is not None and validation.line_amount(row) + validation.line_amount(rest) != pay:
                raise ValidationFailed("التقسيم كان هيغيّر إجمالي السطر؛ قسّمه يدويًا", code="split_changes_total")
            self.db.execute("UPDATE purchase_invoice_lines SET qty=?, line_total=?, tax_amount=?, field_sources=?"
                            " WHERE id=? AND org_id=?",
                            (rest["qty"], rest["line_total"], rest["tax_amount"], json.dumps(rest_sources),
                             line_id, ctx.org_id))
            self._refresh_totals(ctx, inv_id)
            self._bump(inv_id)
            audit.record(self.db, ctx, "purchase.line_split", "purchase_invoice", inv_id,
                         after={"line_id": line_id, "new_line": row["id"], "qty": qty, "expiry": iso})
        return self.get(ctx, inv_id)

    def create_item_from_line(self, ctx: Context, inv_id: str, line_id: str, item: dict,
                              version: int | None, force: bool = False) -> dict:
        """New product the pharmacy never stocked: create it and link the line."""
        ctx.require("items.write")
        ctx.require("purchases.edit")
        # the item and its link are one step: checked first and saved together, or a stale page (or a person
        # who may not edit the invoice) left a new item in the catalogue linked to nothing (#324 S35)
        with self.db.transaction():
            self._draft_for_update(ctx, inv_id, version)
            inv, lines = self._load(ctx, inv_id)
            line = next((l for l in lines if l["id"] == line_id), None)
            if not line:
                raise NotFound("السطر غير موجود")
            data = {"name_en": line["raw_name"], **item}
            if ctx.allowed("items.price"):
                data.setdefault("public_price", line["public_price"])
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
            lines = [dict(r) for r in self.db.all(
                "SELECT * FROM purchase_invoice_lines WHERE invoice_id=? AND org_id=? ORDER BY line_no",
                (inv_id, ctx.org_id))]
            now = next((i for i in self.issues(ctx, dict(inv), lines)
                        if i.code == code and (i.line_id or None) == (line_id or None)), None)
            if now is None:
                raise ValidationFailed("هذا الفرق غير موجود الآن في الفاتورة", code="issue_not_found")
            acks = json.loads(inv["acknowledged_issues"])
            # the figures accepted are kept with the reason: the acceptance covers this difference only (#324 S25)
            acks.append({"code": code, "line_id": line_id, "reason": reason.strip(), "details": now.details,
                         "by": ctx.user_id, "at": accounts.iso(accounts.utcnow())})
            self.db.execute("UPDATE purchase_invoices SET acknowledged_issues=? WHERE id=?",
                            (json.dumps(acks, ensure_ascii=False), inv_id))
            self._bump(inv_id)
            audit.record(self.db, ctx, "purchase.issue_ack", "purchase_invoice", inv_id,
                         after=acks[-1])
        return self.get(ctx, inv_id)

    # ------------------------------------------------------------ approve
    def _price_review(self, ctx: Context, lines: list[dict]) -> dict:
        # Purchase approval already applies the last stated public price for an item.
        incoming = {l["item_id"]: l["public_price"] for l in lines
                    if l["item_id"] and l["public_price"] is not None}
        changes = []
        for iid, price in incoming.items():
            item = self.db.one("SELECT public_price, untracked_public_price, name_en, name_ar, pack_size FROM items"
                               " WHERE id=? AND org_id=?", (iid, ctx.org_id))
            if not item or item["public_price"] == price:
                continue
            batches = [dict(r) for r in self.db.all(
                "SELECT sb.batch_key id, b.batch_no, b.expiry_date, b.public_price,"
                " sum(sb.qty) qty FROM stock_balances sb LEFT JOIN batches b"
                " ON b.id=sb.batch_key AND b.org_id=sb.org_id"
                " WHERE sb.org_id=? AND sb.item_id=? AND sb.qty>0 AND coalesce(b.owner,'pharmacy')='pharmacy'"
                " GROUP BY sb.batch_key ORDER BY b.expiry_date IS NULL, b.expiry_date, sb.batch_key",
                (ctx.org_id, iid))]
            for batch in batches:
                if not batch["id"]:
                    batch["public_price"] = item["untracked_public_price"]
            if batches:
                changes.append({"item_id": iid, "name": item["name_en"] or item["name_ar"],
                                "pack_size": item["pack_size"], "old": item["public_price"],
                                "new": price, "batches": batches})
        return {"changes": changes,
                "token": hashlib.sha256(json.dumps(changes, sort_keys=True).encode()).hexdigest()}

    def price_review_default(self, ctx: Context) -> str:
        ctx.require("purchases.read")
        setting = self.db.one("SELECT value FROM org_settings WHERE org_id=? AND key='purchase_price_default'",
                              (ctx.org_id,))
        return setting["value"] if setting and setting["value"] in ("keep", "all", "per_batch") else "keep"

    def set_price_review_default(self, ctx: Context, value: str) -> str:
        ctx.require("settings.manage")
        if value not in ("keep", "all", "per_batch"):
            raise ValidationFailed("اختار طريقة تسعير الرصيد")
        with self.db.transaction():
            previous = self.price_review_default(ctx)
            self.db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?, 'purchase_price_default', ?)"
                            " ON CONFLICT(org_id, key) DO UPDATE SET value=excluded.value", (ctx.org_id, value))
            audit.record(self.db, ctx, "purchase.price_review_default", "organization", ctx.org_id,
                         before={"default": previous}, after={"default": value})
        return value

    def price_review(self, ctx: Context, inv_id: str) -> dict:
        if not ctx.allowed("purchases.approve"):
            ctx.require("purchases.edit")
        inv, lines = self._load(ctx, inv_id)
        return {**self._price_review(ctx, lines), "default": self.price_review_default(ctx), "version": inv["version"]}

    def _apply_price_review(self, ctx: Context, review: dict, decisions: dict | None, token: str | None,
                            *, require_review: bool) -> list[dict]:
        changes = review["changes"]
        if require_review and changes and (not isinstance(decisions, dict) or token != review["token"]
                                           or decisions.keys() != {c["item_id"] for c in changes}):
            raise Conflict("راجع سعر الرصيد الحالي قبل الاعتماد", code="purchase_price_review")
        recorded = []
        for change in changes:
            choice = (decisions or {}).get(change["item_id"], {"mode": "keep"})
            if not isinstance(choice, dict) or choice.get("mode") not in ("keep", "all", "per_batch"):
                raise ValidationFailed("اختار طريقة تسعير الرصيد")
            mode = choice["mode"]
            if mode != "keep":
                ctx.require("items.price")      # #448: repricing stock needs price authority; "keep" does not
            selected = choice.get("batches", []) if mode == "per_batch" else []
            ids = {b["id"] for b in change["batches"]}
            if not isinstance(selected, list) or any(not isinstance(b, str) or b not in ids for b in selected):
                raise ValidationFailed("اختار من التشغيلات المعروضة")
            applied = []
            for batch in change["batches"]:
                old = batch["public_price"] if batch["public_price"] is not None else change["old"]
                update = mode == "all" or batch["id"] in selected
                new = change["new"] if update else old
                if not batch["id"]:
                    self.db.execute("UPDATE items SET untracked_public_price=? WHERE id=? AND org_id=?",
                                    (new, change["item_id"], ctx.org_id))
                elif new != batch["public_price"]:
                    self.db.execute("UPDATE batches SET public_price=? WHERE id=? AND org_id=?",
                                    (new, batch["id"], ctx.org_id))
                applied.append({"id": batch["id"], "qty": batch["qty"], "old": old, "new": new})
            recorded.append({"item_id": change["item_id"], "mode": mode, "batches": applied})
        return recorded

    def _receiving_batch(self, ctx: Context, item_id: str, line: dict, price: int | None, *,
                         force_batch: bool = False) -> str | None:
        batch_no = (line["batch_no"] or "").strip().upper()
        expiry = line["expiry_date"]
        if not batch_no and not expiry and not force_batch:
            return None
        if price is None:
            return inventory.get_or_create_batch(self.db, ctx, item_id, batch_no, expiry, line["expiry_precision"])
        lots = self.db.all(
            "SELECT id, owner, price_variant, public_price FROM batches"
            " WHERE org_id=? AND item_id=? AND batch_no=? AND expiry_date IS ?"
            " ORDER BY price_variant, rowid", (ctx.org_id, item_id, batch_no, expiry))
        for lot in lots:
            if lot["owner"] == "pharmacy" and lot["public_price"] == price:
                return lot["id"]
        # An empty lot can still receive returns at its original official price.
        batch_id = new_id()
        variant = max((lot["price_variant"] for lot in lots), default=-1) + 1
        self.db.execute("INSERT INTO batches (id, org_id, item_id, batch_no, expiry_date, expiry_precision,"
                        " public_price, price_variant) VALUES (?,?,?,?,?,?,?,?)",
                        (batch_id, ctx.org_id, item_id, batch_no, expiry, line["expiry_precision"], price, variant))
        return batch_id

    def approve(self, ctx: Context, inv_id: str, version: int | None, *, price_decisions: dict | None = None,
                price_token: str | None = None, require_price_review: bool = False) -> dict:
        ctx.require("purchases.approve")
        with self.db.transaction():
            inv = self._draft_for_update(ctx, inv_id, version)
            if inv["supplier_id"]:      # second line of defence: the liability never goes to another pharmacy's supplier
                self._own_supplier(ctx, inv["supplier_id"])
            lines = [dict(r) for r in self.db.all(
                "SELECT * FROM purchase_invoice_lines WHERE invoice_id=? AND org_id=? ORDER BY line_no",
                (inv_id, ctx.org_id))]
            blocking = validation.blocking(self.issues(ctx, inv, lines))
            if blocking:
                raise Conflict(blocking[0].message, code="has_blocking_issues",
                               details={"issues": [i.as_dict() for i in blocking]})
            if inv["invoice_date"]:
                from ..sales.service import local_today
                if date.fromisoformat(inv["invoice_date"]) > local_today():     # U2-0: an import may carry it
                    raise Conflict("تاريخ فاتورة المورد بعد النهارده: مستحيل، صحّح التاريخ قبل الاعتماد",
                                   code="invoice_date_future")
            amounts = [validation.effective_amount(l) for l in lines]
            cost_ups: list[str] = []
            price_changes: list[str] = []
            if any(a < 0 for a in amounts) or inv["extra_discount"] < 0 or inv["extra_tax"] < 0:
                # second line of defence, as for the total below: never a negative unit cost
                raise Conflict("في الفاتورة قيمة بالسالب؛ لا يمكن اعتمادها", code="negative_amount")
            gross = sum(amounts)
            net_total = gross - inv["extra_discount"] + inv["extra_tax"]
            if net_total < 0:     # never a negative supplier balance or unit cost, whatever validation said
                raise Conflict("إجمالي الفاتورة بالسالب (الخصم الإضافي أكبر من إجمالي الأصناف)؛ لا يمكن اعتمادها",
                               code="negative_total", details={"lines_amount": gross, "total": net_total})
            pay = self._pay_now(ctx, inv, net_total)      # checked before anything is written
            price_review = self._price_review(ctx, lines)
            if require_price_review and (price_token is not None or price_review["changes"]) and (
                    version is None or price_token != price_review["token"]):
                raise Conflict("الرصيد أو السعر اتغير. راجع الأسعار تاني قبل الاعتماد", code="purchase_price_review")
            reviewed = self._apply_price_review(ctx, price_review, price_decisions, price_token,
                                                require_review=require_price_review)
            moved = 0
            may_price = ctx.allowed("items.price")
            items = {item_id: self.db.one("SELECT pack_size, public_price FROM items WHERE id=? AND org_id=?",
                                          (item_id, ctx.org_id)) for item_id in {l["item_id"] for l in lines}}
            if any(l["public_price"] is not None and items[l["item_id"]]["public_price"] is None for l in lines):
                ctx.require("items.price")
            packs_of = {l["id"]: items[l["item_id"]]["pack_size"] for l in lines}
            units = {l["id"]: (l["qty"] + (l["bonus_qty"] or 0)) * packs_of[l["id"]] for l in lines}
            all_units = sum(units.values())
            for l, amount in zip(lines, amounts):
                # read again per line: an earlier line of the same item may have changed its public price
                # (the preloaded row skipped the second change and logged a stale previous price, #336 review)
                item = self.db.one("SELECT * FROM items WHERE id=? AND org_id=?", (l["item_id"], ctx.org_id))
                base_qty = units[l["id"]]                       # bonus is free stock
                unit_cost = validation.allocated_unit_cost(amount, gross, net_total, base_qty, all_units)
                price = l["public_price"] if may_price else None     # #448: no authority, stated price ignored
                if not may_price and item["public_price"] is not None and self.db.one(
                        "SELECT 1 FROM batches WHERE org_id=? AND item_id=? AND batch_no=? AND expiry_date IS ?"
                        " AND public_price IS NOT NULL LIMIT 1",
                        (ctx.org_id, item["id"], (l["batch_no"] or "").strip().upper(), l["expiry_date"])):
                    # the batch already has priced lots: the boxes join the one at today's price, never an older one
                    price = item["public_price"]
                if price is not None and item["public_price"] is not None and price != item["public_price"]:
                    # Legacy lots without an explicit price can receive returns as well as hold old stock.
                    self.db.execute("UPDATE batches SET public_price=? WHERE org_id=? AND item_id=?"
                                    " AND owner='pharmacy' AND public_price IS NULL",
                                    (item["public_price"], ctx.org_id, item["id"]))
                    self.db.execute("UPDATE items SET untracked_public_price=? WHERE org_id=? AND id=?"
                                    " AND untracked_public_price IS NULL AND EXISTS (SELECT 1 FROM stock_balances sb"
                                    " WHERE sb.org_id=items.org_id AND sb.item_id=items.id AND sb.batch_key='')",
                                    (item["public_price"], ctx.org_id, item["id"]))
                batch_id = self._receiving_batch(ctx, item["id"], l, price)
                if batch_id is None and price is not None:
                    untracked = self.db.one("SELECT untracked_public_price FROM items WHERE org_id=? AND id=?",
                                            (ctx.org_id, item["id"]))["untracked_public_price"]
                    if untracked is not None and untracked != price:
                        # New boxes without a batch number or expiry still need their own price group.
                        batch_id = self._receiving_batch(ctx, item["id"], l, price, force_batch=True)
                    elif untracked is None:
                        self.db.execute("UPDATE items SET untracked_public_price=? WHERE id=? AND org_id=?",
                                        (price, item["id"], ctx.org_id))
                # the branch is named so ix_moves_item(org_id, branch_id, item_id) answers from the item's own
                # movements; without it every movement of the pharmacy was read for each line (#253: 4 s a
                # line on 3.5 M movements, and the approval holds every screen while it runs)
                last = self.db.one("SELECT unit_cost FROM stock_movements WHERE org_id=? AND branch_id=? AND item_id=? AND"
                                   " movement_type='purchase' AND unit_cost IS NOT NULL ORDER BY +rowid DESC LIMIT 1",
                                   (ctx.org_id, inv["branch_id"], item["id"]))
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
                if may_price and l["public_price"] is not None and l["public_price"] != item["public_price"]:
                    self.db.execute("UPDATE items SET public_price=?, updated_at=? WHERE id=? AND org_id=?",
                                    (l["public_price"], accounts.iso(accounts.utcnow()), item["id"], ctx.org_id))
                    catalog.record_price(self.db, ctx, item["id"], l["public_price"],
                                         item["public_price"], "purchase_invoice", inv_id)
                    audit.record(self.db, ctx, "purchase.catalog_price", "item", item["id"],
                                 before={"public_price": item["public_price"]},
                                 after={"public_price": l["public_price"], "invoice_id": inv_id})
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
                            (ctx.approver_id if not ctx.can("purchases.approve") else ctx.user_id, now, net_total, inv_id))
            audit.record(self.db, ctx, "purchase.approve", "purchase_invoice", inv_id,
                         after={"lines": len(lines), "base_units": moved, "total": net_total,
                                "stock_price_decisions": reviewed})
            if inv["supplier_id"]:      # the supplier's account: we now owe this invoice
                self.db.execute("INSERT INTO supplier_ledger (id, org_id, supplier_id, amount, kind, ref_id, note, created_by)"
                                " VALUES (?,?,?,?,'purchase',?,?,?)", (new_id(), ctx.org_id, inv["supplier_id"], net_total,
                                                                       inv_id, f"فاتورة {inv['invoice_no'] or ''}".strip(), ctx.user_id))
                plan = self.pay_plan(ctx, inv)        # what was applied, kept: a later supplier-type change never rewrites it
                self.db.execute("UPDATE purchase_invoices SET pay_source=?, pay_amount=? WHERE id=? AND org_id=?",
                                (plan["source"], pay["amount"] if pay else (0 if plan["source"] != PAY_CREDIT else None),
                                 inv_id, ctx.org_id))
                if pay:     # «طريقة الدفع»: one ordinary supplier payment, in this transaction (the drawer follows)
                    from ..procurement import suppliers
                    suppliers.pay_from(self.db, ctx, inv["supplier_id"], pay["amount"], pay["source"],
                                       note=f"دفع فاتورة {inv['invoice_no'] or ''}".strip(), ref=pay["ref"],
                                       ref_id=inv_id)
            ids = sorted({l["item_id"] for l in lines if l["item_id"]})
            if ids:
                slots = ",".join("?" for _ in ids)
                self.db.execute(f"UPDATE items SET shortage_at=NULL, shortage_by=NULL WHERE org_id=? AND id IN ({slots})",
                                (ctx.org_id, *ids))
                # A newly received or repriced lot can change a draft's quoted line prices even if its total stays equal.
                self.db.execute("UPDATE sales SET version=version+1 WHERE org_id=? AND status='draft'"
                                f" AND EXISTS (SELECT 1 FROM sale_lines l WHERE l.sale_id=sales.id"
                                f" AND l.org_id=sales.org_id AND l.item_id IN ({slots}))", (ctx.org_id, *ids))
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
            reversed_units = returned_units = credited = 0
            if inv["status"] == "approved":
                ctx.require("purchases.cancel")
                moves = self.db.all("SELECT * FROM stock_movements WHERE org_id=? AND ref_type='purchase_invoice'"
                                    " AND ref_id=? AND movement_type='purchase' ORDER BY rowid", (ctx.org_id, inv_id))
                for m in moves:
                    # read again per movement: an earlier line of this invoice may sit on the same lot
                    receipts = self._lot_receipts(ctx, m["branch_id"], m["item_id"], m["batch_id"])
                    me = next(p for p in receipts if p["id"] == m["id"])
                    returned_units += sum(t[0] for t in me["took"])
                    credited += sum(t[1] for t in me["took"])
                    if me["left"] <= 0:           # every unit of this line already went back to the supplier
                        continue
                    # what later deliveries of the same lot brought in is theirs: without this, cancelling took
                    # another supplier's units, or passed although this invoice's own units were sold (#324 S28)
                    theirs = sum(p["left"] for p in receipts if p["seq"] > me["seq"] and p["ref_id"] != inv_id)
                    have = inventory.batch_qty(self.db, ctx.org_id, m["branch_id"], m["item_id"], m["batch_id"])
                    if have - theirs < me["left"]:
                        raise Conflict("الكمية غير متاحة في المخزون (ربما بيعت)", code="insufficient_stock",
                                       details={"item_id": m["item_id"], "available": max(have - theirs, 0),
                                                "requested": me["left"]})
                    inventory.post_movement(self.db, ctx, item_id=m["item_id"],
                                            qty_delta=-me["left"],
                                            movement_type="purchase_reversal",
                                            batch_id=m["batch_id"], unit_cost=Decimal(m["unit_cost"]),
                                            ref_type="purchase_invoice", ref_id=inv_id,
                                            ref_line_id=m["ref_line_id"], branch_id=m["branch_id"],
                                            note=reason)
                    reversed_units += me["left"]
            else:
                ctx.require("purchases.edit")
            self.db.execute("UPDATE purchase_invoices SET status='cancelled', cancelled_by=?,"
                            " cancelled_at=?, cancel_reason=?, version=version+1 WHERE id=?",
                            (ctx.user_id, accounts.iso(accounts.utcnow()), reason.strip(), inv_id))
            if inv["status"] == "approved" and inv["supplier_id"]:
                posted = self.db.one("SELECT coalesce(sum(amount),0) a FROM supplier_ledger WHERE org_id=? AND ref_id=?"
                                     " AND kind='purchase'", (ctx.org_id, inv_id))["a"]
                # the returns already credited for this invoice's units are not credited again: the invoice and
                # its returns together come to zero, whatever value each return was agreed at
                credit = posted - credited
                if credit:        # also when nothing was posted (a free invoice) but a return of it was credited
                    self.db.execute("INSERT INTO supplier_ledger (id, org_id, supplier_id, amount, kind, ref_id, note,"
                                    " created_by) VALUES (?,?,?,?,'adjustment',?,?,?)",
                                    (new_id(), ctx.org_id, inv["supplier_id"], -credit, inv_id,
                                     f"إلغاء فاتورة {inv['invoice_no'] or ''}".strip(), ctx.user_id))
            audit.record(self.db, ctx, "purchase.cancel", "purchase_invoice", inv_id,
                         before={"status": inv["status"]},
                         after={"reason": reason, "reversed_units": reversed_units,
                                "already_returned_units": returned_units, "already_credited": credited})
        return self.get(ctx, inv_id, with_candidates=False)

    def _lot_receipts(self, ctx: Context, branch_id: str, item_id: str, batch_id: str | None) -> list[dict]:
        return lot_receipts(self.db, ctx, branch_id, item_id, batch_id)


def lot_receipts(db: Database, ctx: Context, branch_id: str, item_id: str, batch_id: str | None) -> list[dict]:
    """Every receipt of one lot in ledger order, with what is still its own: `left` = received, minus what
    its cancellation reversed, minus what went back to its supplier; `took` = those returns as
    (units, piasters credited for them, tax included). A return made from an invoice (part 3 item 6b) names
    its purchase line and is taken from that receipt first; a return by item and lot is taken from that
    supplier's receipts of the lot, oldest first (Codex P1 x2 on #396)."""
    receipts: list[dict] = []
    by_line: dict[tuple, dict] = {}
    for r in db.all(
            "SELECT m.rowid seq, m.id, m.qty_delta, m.movement_type, m.ref_type, m.ref_id, m.ref_line_id,"
            " pi.supplier_id bought_from, sr.supplier_id returned_to, sl.unit_value,"
            " coalesce(sl.tax_value, 0) tax_value, sl.purchase_line_id, sr.purchase_invoice_id"
            " FROM stock_movements m"
            " LEFT JOIN purchase_invoices pi ON m.ref_type='purchase_invoice' AND pi.id=m.ref_id AND pi.org_id=m.org_id"
            " LEFT JOIN supplier_returns sr ON m.movement_type='supplier_return' AND sr.id=m.ref_id AND sr.org_id=m.org_id"
            " LEFT JOIN supplier_return_lines sl ON m.movement_type='supplier_return' AND sl.id=m.ref_line_id"
            " WHERE m.org_id=? AND m.branch_id=? AND m.item_id=? AND m.batch_id IS ?"
            " AND m.movement_type IN ('purchase','opening','purchase_reversal','supplier_return') ORDER BY m.rowid",
            (ctx.org_id, branch_id, item_id, batch_id)):
        if r["movement_type"] in ("purchase", "opening"):
            if r["qty_delta"] > 0:
                p = {"seq": r["seq"], "id": r["id"], "ref_id": r["ref_id"] if r["ref_type"] == "purchase_invoice" else None,
                     "supplier_id": r["bought_from"], "left": r["qty_delta"], "took": []}
                receipts.append(p)
                if p["ref_id"]:
                    by_line[(r["ref_id"], r["ref_line_id"])] = p
        elif r["movement_type"] == "purchase_reversal":
            p = by_line.get((r["ref_id"], r["ref_line_id"]))
            if p:
                p["left"] = max(p["left"] + r["qty_delta"], 0)
        elif r["returned_to"]:
            n, tax = -r["qty_delta"], r["tax_value"]
            named = by_line.get((r["purchase_invoice_id"], r["purchase_line_id"])) if r["purchase_line_id"] else None
            for p in ([named] if named else []) + receipts:
                if n <= 0:
                    break
                if p["supplier_id"] == r["returned_to"] and p["left"] > 0:
                    t = min(n, p["left"])
                    p["left"] -= t
                    p["took"].append((t, t * (r["unit_value"] or 0) + tax, r["purchase_line_id"]))
                    tax = 0                   # the row's tax is credited once, with its first units
                    n -= t
    return receipts
