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
from . import estock
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
PARTY_HEADERS: dict[str, list[str]] = {
    "name": ["الاسم", "اسم المورد", "المورد", "اسم العميل", "العميل", "name", "supplier",
             "supplier name", "customer", "customer name"],
    "code": ["كود", "الكود", "كود المورد", "كود العميل", "رقم", "code", "id"],
    "phone": ["التليفون", "تليفون", "الهاتف", "هاتف", "الهاتف", "رقم الهاتف", "phone", "mobile", "tel"],
    "phone2": ["هاتف 2", "موبايل2", "تليفون 2", "تليفون2", "الموبايل 2", "رقم اخر", "phone 2", "phone2", "mobile 2"],
    "phone3": ["هاتف 3", "موبايل3", "تليفون 3", "تليفون3", "الموبايل 3", "phone 3", "phone3", "mobile 3"],
    "name_alt": ["الاسم الانجليزي", "اسم انجليزي", "الاسم التاني", "اسم اخر", "english name", "name en", "name 2"],
    "address1": ["العنوان", "عنوان", "العنوان 1", "عنوان 1", "address", "address 1", "address1"],
    "address2": ["العنوان 2", "عنوان 2", "عنوان اخر", "address 2", "address2"],
    "supplier_type": ["نوع المورد", "النوع", "type"],
    "discount_local": ["خصم محلي", "خصم المحلي", "local discount", "discount local"],
    "discount_imported": ["خصم مستورد", "خصم المستورد", "imported discount", "discount imported"],
    "balance": ["الرصيد", "رصيد", "المديونيه", "الحساب", "المستحق", "balance", "opening balance", "due"],
    "credit_limit": ["حد الائتمان", "حد الاجل", "الحد", "credit limit", "limit"],
    "notes": ["ملاحظات", "notes", "remarks"],
}
HEADERS_BY_KIND = {"items": ITEM_HEADERS, "opening_stock": ITEM_HEADERS,
                   "suppliers": PARTY_HEADERS, "customers": PARTY_HEADERS}
_SYN_BY_KIND = {k: {normalize_text(x): f for f, xs in h.items() for x in xs}
                for k, h in HEADERS_BY_KIND.items()}
_SYN = _SYN_BY_KIND["items"]
FIELDS = set(ITEM_HEADERS)
FIELDS_BY_KIND = {k: set(h) for k, h in HEADERS_BY_KIND.items()}

# each tuple = "at least one of these columns must exist"
REQUIRED = {"items": [("name_en", "name_ar")],
            "opening_stock": [("code", "barcode", "name_en", "name_ar"), ("qty",)],
            "suppliers": [("name",)], "customers": [("name",)]}
RECOMMENDED = {"items": ["code", "barcode", "public_price", "pack_size"],
               "opening_stock": ["code", "cost", "batch_no", "expiry"],
               "suppliers": ["balance", "phone"], "customers": ["phone", "balance", "credit_limit"]}
KEY_FIELDS = {"items": {"name_en", "name_ar", "code", "barcode"},
              "opening_stock": {"name_en", "name_ar", "code", "barcode"},
              "suppliers": {"name"}, "customers": {"name"}}
LABELS_AR = {"code": "الكود", "name_en": "اسم الصنف", "name_ar": "الاسم العربي", "barcode": "الباركود",
             "public_price": "سعر الجمهور", "cost": "التكلفة", "qty": "الرصيد", "batch_no": "التشغيلة",
             "expiry": "الصلاحية", "manufacturer": "الشركة", "active_ingredient": "المادة الفعالة",
             "dosage_form": "الشكل", "strength": "التركيز", "pack_size": "عدد الوحدات",
             "min_stock": "حد الطلب", "is_imported": "محلي/مستورد", "is_drug": "دواء/غير دواء",
             "location": "الموقع", "name": "الاسم", "phone": "التليفون", "supplier_type": "نوع المورد",
             "discount_local": "خصم محلي", "discount_imported": "خصم مستورد", "balance": "الرصيد",
             "credit_limit": "حد الائتمان", "notes": "ملاحظات", "phone2": "هاتف 2", "phone3": "هاتف 3",
             "name_alt": "اسم آخر", "address1": "العنوان", "address2": "العنوان 2"}


def _detect(rows: list[list], kind: str = "items") -> tuple[int, dict[int, str]] | None:
    syn, keys = _SYN_BY_KIND[kind], KEY_FIELDS[kind]
    best = None
    for i, row in enumerate(rows[:30]):
        cmap: dict[int, str] = {}
        for j, v in enumerate(row):
            t = normalize_text(str(v)) if v is not None else ""
            f = syn.get(t)
            if f and f not in cmap.values():
                cmap[j] = f
        if keys & set(cmap.values()) and len(cmap) >= (1 if kind in ("suppliers", "customers") else 2) \
                and (best is None or len(cmap) > len(best[1])):
            best = (i, cmap)
    return best


def _header_sig(row: list) -> str:
    cells = [normalize_text(str(c)) for c in row if c not in (None, "")]
    return hashlib.sha256("|".join(cells).encode()).hexdigest()[:32]


def column_report(kind: str, header_row: list, cmap: dict[int, str]) -> dict:
    found = set(cmap.values())
    missing_req = [" أو ".join(LABELS_AR.get(f, f) for f in group) for group in REQUIRED[kind]
                   if not found & set(group)]
    return {"found": sorted(found),
            "missing_required": missing_req,
            "missing_recommended": [LABELS_AR.get(f, f) for f in RECOMMENDED[kind] if f not in found],
            "unmapped_headers": [str(c) for j, c in enumerate(header_row)
                                 if c not in (None, "") and j not in cmap]}


def template_xlsx(kind: str) -> bytes:
    """Empty import template + an instructions sheet (Arabic)."""
    from openpyxl import Workbook
    if kind not in HEADERS_BY_KIND:
        raise ValidationFailed("نوع قالب غير معروف")
    order = {"items": ["code", "name_en", "name_ar", "barcode", "pack_size", "public_price",
                       "active_ingredient", "strength", "dosage_form", "manufacturer", "is_imported",
                       "is_drug", "min_stock", "location"],
             "opening_stock": ["code", "barcode", "name_en", "qty", "cost", "batch_no", "expiry"],
             "suppliers": ["code", "name", "phone", "supplier_type", "discount_local",
                           "discount_imported", "balance"],
             "customers": ["code", "name", "name_alt", "phone", "phone2", "phone3", "address1", "address2",
                           "credit_limit", "balance", "notes"]}[kind]
    wb = Workbook()
    ws = wb.active
    ws.title = "data"
    ws.append([LABELS_AR[f] for f in order])
    ins = wb.create_sheet("تعليمات")
    req = {f for g in REQUIRED[kind] for f in g}
    notes = {"qty": "بالعلبة افتراضيًا (أو بالوحدة الصغرى إذا اخترت ذلك عند الرفع)",
             "expiry": "مثال: 05/2027 أو 31/05/2027 — إذا لم تكن معروفة فاتركها فارغة (ممنوع التخمين)",
             "cost": "تكلفة العلبة (أو الوحدة حسب اختيارك)", "public_price": "بالجنيه للعلبة",
             "balance": "بالجنيه — للمورد: المستحق له علينا. للعميل: المستحق عليه لنا",
             "pack_size": "عدد الشرائط/الوحدات في العلبة", "is_imported": "محلي أو مستورد",
             "discount_local": "نسبة مئوية مثل 20", "discount_imported": "نسبة مئوية مثل 12",
             "credit_limit": "بالجنيه — 0 يعني بدون آجل"}
    ins.append(["العمود", "إلزامي؟", "ملاحظات"])
    for f in order:
        ins.append([LABELS_AR[f], "نعم" if f in req else "لا", notes.get(f, "")])
    ins.append([])
    ins.append(["يمكنك رفع ملف التصدير من البرنامج القديم كما هو؛ وإذا اختلفت أسماء الأعمدة ستظهر لك صفحة لربطها مرة واحدة."])
    from ..core.xlsx_safe import as_text
    as_text(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_YES = {normalize_text(w) for w in ("1", "1.0", "نعم", "yes", "true", "مستورد", "مستوردة", "imported", "دواء", "drug")}
_NO = {normalize_text(w) for w in ("0", "0.0", "لا", "no", "false", "محلي", "محلية", "local", "غير دواء", "non drug",
                                   "nondrug")}


def _bool(v) -> int | None:
    """Yes/no columns as old programs export them; anything else is None (the row shows unknown_..., never a guess).
    Both sides go through normalize_text, so spelling variants (ة/ه, ى/ي) match."""
    t = normalize_text(str(v)) if v not in (None, "") else ""
    if t in _YES:
        return 1
    if t in _NO:
        return 0
    return None


def _read(data: bytes, filename: str, import_kind: str | None = None) -> list[list]:
    kind, _ = files.sniff(data, filename)
    if kind == "excel":
        sheets = files.read_excel_sheets(data)
        if import_kind:          # first sheet whose columns are recognised
            for _, rows in sheets:
                if _detect(rows, import_kind):
                    return rows
        return sheets[0][1] if sheets else []
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
        if kind not in HEADERS_BY_KIND:
            raise ValidationFailed("نوع استيراد غير معروف")
        if kind == "opening_stock":
            ctx.require("inventory.adjust")
        if kind == "suppliers":
            ctx.require("suppliers.write")
        if kind == "customers":
            ctx.require("customers.manage")
        options = {"qty_unit": "pack", **(options or {})}
        if options["qty_unit"] not in ("pack", "base"):
            raise ValidationFailed("وحدة الكمية يجب أن تكون pack أو base")
        rows = _read(data, filename, None if mapping else kind)
        if source_system == "estock" and not mapping:
            rows = estock.normalize(rows, kind, options) or rows
        preview = [[str(c) if c is not None else "" for c in r] for r in rows[:8]]
        profile_used = False
        if mapping:
            bad = set(mapping.values()) - FIELDS_BY_KIND[kind]
            if bad:
                raise ValidationFailed(f"حقول غير معروفة: {', '.join(sorted(bad))}")
            header_row, cmap = int(options.get("header_row", 0)), {int(k): v for k, v in mapping.items()}
            if header_row < len(rows):
                self._save_profile(ctx, source_system, kind, rows[header_row], cmap)
        else:
            found = self._from_profile(ctx, source_system, kind, rows) or _detect(rows, kind)
            profile_used = bool(found and found[2:] == (True,))
            if not found:
                raise ValidationFailed("لم أتعرف على أعمدة الملف؛ حدد الأعمدة يدويًا",
                                       code="mapping_required",
                                       details={"preview": preview, "fields": {
                                           f: LABELS_AR.get(f, f) for f in sorted(FIELDS_BY_KIND[kind])}})
            header_row, cmap = found[0], found[1]
        cols = column_report(kind, rows[header_row] if header_row < len(rows) else [], cmap)
        if cols["missing_required"]:
            raise ValidationFailed("أعمدة إلزامية ناقصة: " + "، ".join(cols["missing_required"]),
                                   code="missing_columns", details={"columns": cols, "preview": preview})
        sha = hashlib.sha256(data).hexdigest()
        if self.db.one("SELECT 1 FROM import_runs WHERE org_id=? AND kind=? AND file_sha256=?"
                       " AND status='applied'", (ctx.org_id, kind, sha)):
            raise Conflict("هذا الملف استُورد من قبل", code="already_applied")

        run_id = new_id()
        planned = []
        seen_code: dict[str, int] = {}
        seen_bc: dict[str, int] = {}
        seen_key: dict[str, int] = {}
        for idx, r in enumerate(rows[header_row + 1:], start=header_row + 2):
            raw = {cmap[j]: (r[j] if j < len(r) else None) for j in cmap}
            if all(v in (None, "") for v in raw.values()):
                continue
            if kind in ("suppliers", "customers"):
                d, issues = self._normalize_party(raw)
                action, target, more = self._plan_party(ctx, kind, d)
                issues += more
                key = d.get("phone") if kind == "customers" and d.get("phone") else d.get("_norm")
                if key:
                    if key in seen_key:
                        issues.append({"code": "duplicate_in_file", "other_row": seen_key[key]})
                        if action in ("create", "update"):
                            action = "review"
                    else:
                        seen_key[key] = idx
                planned.append((idx, raw, d, action, target, issues))
                continue
            d, issues = self._normalize(raw, options)
            if kind == "items":
                action, target, more = self._plan_item(ctx, d)
                if d.get("cost") and d.get("public_price") and \
                        Decimal(d["public_price"]) < Decimal(d["cost"]):
                    more.append({"code": "price_below_cost"})
            else:
                action, target, more = self._plan_stock(ctx, d, options)
            issues += more
            # duplicates inside the file itself
            if kind == "items" and d.get("_dedupe_base"):
                maker = d["_dedupe"][len(d["_dedupe_base"]):]
                prev = seen_key.setdefault(d["_dedupe_base"], [])
                # same name: duplicate when both makers match or one of them is unknown
                other = next((i for m, i in prev if m == maker or not m or not maker), None)
                if other is not None:
                    issues.append({"code": "duplicate_item_in_file", "other_row": other})
                    if action in ("create", "update", "link"):
                        action = "review"
                prev.append((maker, idx))
            if kind == "opening_stock":   # one item has many batches: a duplicate is the same batch twice
                bkey = "|".join(str(d.get(k) or "") for k in ("expiry_date", "batch_no", "cost"))
                ids = ((d.get("code") and d["code"] + "|" + bkey, seen_code, "duplicate_batch_in_file"),
                       (d.get("barcode") and d["barcode"] + "|" + bkey, seen_bc, "duplicate_batch_in_file"))
            else:
                ids = ((d.get("code"), seen_code, "duplicate_code_in_file"),
                       (d.get("barcode"), seen_bc, "duplicate_barcode_in_file"))
            for key, store, code in ids:
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
            report["columns"] = cols
            report["profile_used"] = profile_used
            self.db.execute("UPDATE import_runs SET report=? WHERE id=?",
                            (json.dumps(report, ensure_ascii=False), run_id))
            audit.record(self.db, ctx, "import.analyze", "import_run", run_id,
                         after={"kind": kind, "file": filename, "counts": report["counts"]})
        return run_id

    # ------------------------------------------------------------ profiles
    def _save_profile(self, ctx: Context, source_system: str, kind: str, header: list,
                      cmap: dict[int, str]) -> None:
        by_text = {normalize_text(str(header[j])): f for j, f in cmap.items()
                   if j < len(header) and header[j] not in (None, "")}
        if not by_text:
            return
        with self.db.transaction():
            self.db.execute("INSERT INTO import_profiles (org_id, source_system, kind, header_sig, mapping)"
                            " VALUES (?,?,?,?,?) ON CONFLICT(org_id, source_system, kind, header_sig)"
                            " DO UPDATE SET mapping=excluded.mapping, updated_at=excluded.updated_at",
                            (ctx.org_id, source_system, kind, _header_sig(header),
                             json.dumps(by_text, ensure_ascii=False)))

    def _from_profile(self, ctx: Context, source_system: str, kind: str, rows: list[list]):
        profiles = {r["header_sig"]: json.loads(r["mapping"]) for r in self.db.all(
            "SELECT header_sig, mapping FROM import_profiles WHERE org_id=? AND source_system=? AND kind=?",
            (ctx.org_id, source_system, kind))}
        if not profiles:
            return None
        for i, row in enumerate(rows[:30]):
            m = profiles.get(_header_sig(row))
            if m:
                cmap = {j: m[normalize_text(str(c))] for j, c in enumerate(row)
                        if c not in (None, "") and normalize_text(str(c)) in m}
                return (i, cmap, True)
        return None

    # ------------------------------------------------------------ suppliers / customers
    def _normalize_party(self, raw: dict) -> tuple[dict, list[dict]]:
        d: dict[str, Any] = {}
        issues: list[dict] = []
        for k in ("name", "code", "phone", "phone2", "phone3", "name_alt", "address1", "address2", "supplier_type", "notes"):
            v = raw.get(k)
            if isinstance(v, float) and v == int(v):
                v = int(v)
            d[k] = str(v).strip() if v not in (None, "") else None
        for pk in ("phone", "phone2", "phone3"):
            if not d.get(pk):
                continue
            d[pk] = "".join(ch for ch in d[pk].translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
                            if ch.isdigit() or ch == "+") or None
            if d[pk] and len(d[pk]) == 10 and d[pk][0] == "1":     # Excel dropped the leading 0 of a mobile
                d[pk] = "0" + d[pk]
            if d[pk] and len(d[pk]) < 7:
                issues.append({"code": "invalid_phone", "value": raw.get(pk)})
                d[pk] = None
        for k in ("balance", "credit_limit"):
            v = raw.get(k)
            if v not in (None, ""):
                dec = parse_decimal(v)
                if dec is None or (k == "credit_limit" and dec < 0):
                    issues.append({"code": f"invalid_{k}", "value": str(v)})
                else:
                    d[k] = str(dec)
        for k in ("discount_local", "discount_imported"):
            v = raw.get(k)
            if v not in (None, ""):
                dec = parse_decimal(v)
                if dec is not None and 0 < dec <= 1:
                    dec = dec * 100
                if dec is None or not 0 <= dec <= 100:
                    issues.append({"code": f"invalid_{k}", "value": str(v)})
                else:
                    d[k] = str(dec)
        d["_norm"] = normalize_text(d["name"]) if d.get("name") else None
        d["_name"] = d.get("name")
        return d, issues

    def _plan_party(self, ctx: Context, kind: str, d: dict) -> tuple[str, str | None, list[dict]]:
        if not d.get("name"):
            return "error", None, [{"code": "missing_name"}]
        issues: list[dict] = []
        if kind == "suppliers":
            r = self.db.one("SELECT id FROM suppliers WHERE org_id=? AND name_norm=?", (ctx.org_id, d["_norm"]))
            if not r:
                return "create", None, issues
            has = self.db.one("SELECT 1 FROM supplier_ledger WHERE org_id=? AND supplier_id=? LIMIT 1",
                              (ctx.org_id, r["id"]))
            if has and d.get("balance") not in (None, "0"):
                return "review", r["id"], [{"code": "party_has_history"}]
            return "update", r["id"], issues
        if d.get("phone"):
            r = self.db.one("SELECT id FROM customers WHERE org_id=? AND phone=?", (ctx.org_id, d["phone"]))
            if r:
                has = self.db.one("SELECT 1 FROM customer_ledger WHERE org_id=? AND customer_id=? LIMIT 1",
                                  (ctx.org_id, r["id"]))
                if has and d.get("balance") not in (None, "0"):
                    return "review", r["id"], [{"code": "party_has_history"}]
                return "update", r["id"], issues
        same = self.db.one("SELECT id FROM customers WHERE org_id=? AND name=?", (ctx.org_id, d["name"]))
        if same:
            return "review", same["id"], [{"code": "same_name_exists"}]
        return "create", None, issues

    def _apply_party(self, ctx: Context, kind: str, d: dict, action: str, target: str | None,
                     run_id: str, done: dict) -> None:
        bal = to_piasters(Decimal(d["balance"])) if d.get("balance") is not None else 0
        if kind == "suppliers":
            if action == "create":
                stype = {"موزع": "distributor", "مخزن": "warehouse", "شركه": "company"}.get(
                    normalize_text(d.get("supplier_type") or ""), "other")
                target = catalog.create_supplier(
                    self.db, ctx, name=d["name"], supplier_type=stype, phone=d.get("phone"),
                    base_discount_local_bp=int((Decimal(d["discount_local"]) * 100).to_integral_value(ROUND_HALF_UP)) if d.get("discount_local") else None,
                    base_discount_imported_bp=int((Decimal(d["discount_imported"]) * 100).to_integral_value(ROUND_HALF_UP)) if d.get("discount_imported") else None)
                done["created"] += 1
            else:
                done["updated" if action == "update" else "linked"] += 1
            if bal:
                self.db.execute("INSERT INTO supplier_ledger (id, org_id, supplier_id, amount, kind, ref_id,"
                                " note, created_by) VALUES (?,?,?,?, 'opening', ?, 'رصيد افتتاحي', ?)",
                                (new_id(), ctx.org_id, target, bal, run_id, ctx.user_id))
        else:
            from ..sales.service import Sales
            limit = to_piasters(Decimal(d["credit_limit"])) if d.get("credit_limit") else 0
            if action == "create":
                target = Sales(self.db).create_customer(
                    ctx, name=d["name"], phone=d.get("phone"), credit_limit=limit, notes=d.get("notes"),
                    **{k: d.get(k) for k in ("name_alt", "address1", "address2", "phone2", "phone3")})
                done["created"] += 1
            else:
                if d.get("credit_limit") is not None:
                    self.db.execute("UPDATE customers SET credit_limit=? WHERE id=? AND org_id=?",
                                    (limit, target, ctx.org_id))
                done["updated" if action == "update" else "linked"] += 1
            if bal:
                self.db.execute("INSERT INTO customer_ledger (id, org_id, customer_id, amount, kind, ref_id,"
                                " note, created_by) VALUES (?,?,?,?, 'adjustment', ?, 'رصيد افتتاحي', ?)",
                                (new_id(), ctx.org_id, target, bal, run_id, ctx.user_id))
        if bal:
            done["balance_rows"] += 1
            done["posted_balance"] += bal

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
                                      d.get("pack_size") or 1, d.get("manufacturer"))
            d["_dedupe_base"] = dedupe_key(name, d.get("strength"), d.get("dosage_form"),
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
        base, full = d["_dedupe_base"], d["_dedupe"]
        if full != base:      # known maker: same maker, or an existing item with unknown maker
            dup = self.db.one("SELECT id FROM items WHERE org_id=? AND (dedupe_key=? OR dedupe_key=?)",
                              (ctx.org_id, full, base))
        else:                 # unknown maker: any maker
            dup = self.db.one("SELECT id FROM items WHERE org_id=? AND (dedupe_key=? OR"
                              " substr(dedupe_key, 1, ?)=?)", (ctx.org_id, base, len(base) + 3, base + "|m:"))
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

    def _plan_stock(self, ctx: Context, d: dict, options: dict | None = None
                    ) -> tuple[str, str | None, list[dict]]:
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
        issues: list[dict] = []
        if (options or {}).get("qty_unit", "pack") == "pack":
            pack = self.db.one("SELECT pack_size FROM items WHERE id=?", (item_id,))["pack_size"]
            mue = Decimal(str((options or {}).get("max_unit_error", 0)))
            if _whole_units(Decimal(d["qty"]), pack, mue) is None:
                return "error", item_id, [{"code": "fractional_qty_needs_pack_size",
                                           "qty": d["qty"], "pack_size": pack,
                                           "hint": "حدد عدد الوحدات (الشرايط) في العلبة لهذا الصنف أولًا"}]
            exact = Decimal(d["qty"]) * pack
            if abs(exact - exact.to_integral_value(ROUND_HALF_UP)) > Decimal("0.0006") * pack:
                issues.append({"code": "rounded_units", "from": str(exact),
                               "to": int(exact.to_integral_value(ROUND_HALF_UP))})
        if not d.get("expiry_date") and not d.get("batch_no"):
            issues.append({"code": "no_batch_or_expiry"})  # will be posted untracked
        if d.get("cost") is None:
            issues.append({"code": "cost_missing"})
        elif Decimal(d["cost"]) == 0:          # a zero cost would silently lower the average cost
            return "review", item_id, issues + [{"code": "zero_cost"}]
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
                "source_value_total": str(value_total.quantize(Decimal("0.01"), ROUND_HALF_UP))}

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
            raise ValidationFailed("السطر به خطأ؛ صحّح الملف أو تجاهل السطر")
        if decision.startswith("link:"):
            target_table = {"suppliers": "suppliers", "customers": "customers"}.get(run["kind"], "items")
            if not self.db.one(f"SELECT 1 FROM {target_table} WHERE id=? AND org_id=?",
                               (decision[5:], ctx.org_id)):
                raise NotFound("السجل المراد الربط به غير موجود")
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
            if run["kind"] == "suppliers":
                ctx.require("suppliers.write")
            if run["kind"] == "customers":
                ctx.require("customers.manage")
            rows = [dict(r) for r in self.db.all(
                "SELECT * FROM import_rows WHERE run_id=? ORDER BY row_no", (run_id,))]
            pending = [r["row_no"] for r in rows if r["action"] == "review" and not r["decision"]]
            if pending:
                raise Conflict("توجد أسطر تحتاج إلى قرار قبل التطبيق", code="pending_review",
                               details={"rows": pending[:50], "count": len(pending)})
            if run["kind"] == "opening_stock":
                frac = [r["row_no"] for r in rows if r["decision"] != "skip" and any(
                    i["code"] == "fractional_qty_needs_pack_size" for i in json.loads(r["issues"]))]
                if frac:
                    raise ValidationFailed("توجد أصناف كميتها كسور وعدد الوحدات في عبوتها غير محدد؛"
                                           " حدد عدد الوحدات أو تجاهل هذه الأسطر صراحةً",
                                           code="fractional_qty", details={"rows": frac[:50],
                                                                          "count": len(frac)})
            options = json.loads(run["options"])
            done = {"created": 0, "updated": 0, "linked": 0, "skipped": 0, "errors": 0,
                    "stock_rows": 0, "posted_qty_packs": Decimal(0), "posted_base_units": 0,
                    "balance_rows": 0, "posted_balance": 0}
            expected_balance = 0
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
                    dup = r["decision"] == "create" and any(
                        i["code"] in ("duplicate_item_in_file", "same_item_exists", "duplicate_code_in_file")
                        for i in json.loads(r["issues"]))
                    self._apply_item(ctx, d, action, target, run_id, done,
                                     keep_duplicate=d.get("code") or r["row_no"] if dup else None)
                elif run["kind"] in ("suppliers", "customers"):
                    if d.get("balance") is not None:
                        expected_balance += to_piasters(Decimal(d["balance"]))
                    self._apply_party(ctx, run["kind"], d, action, target, run_id, done)
                else:
                    self._apply_stock(ctx, d, target, run_id, options, done)
            now = _now()
            done["posted_qty_packs"] = str(done["posted_qty_packs"])
            report = json.loads(run["report"])
            report["applied"] = done
            accounted = done["created"] + done["updated"] + done["linked"] + done["skipped"] + \
                done["errors"] + done["stock_rows"]
            if run["kind"] == "opening_stock":
                accounted = done["stock_rows"] + done["skipped"] + done["errors"]
            if accounted != len(rows):
                raise Conflict("عدد الأسطر المطبقة لا يساوي أسطر الملف؛ تم إلغاء العملية",
                               code="reconciliation_failed", details={"rows": len(rows), "accounted": accounted})
            report["reconciliation"] = {"ok": True, "rows_in_file": len(rows), "rows_accounted": accounted}
            if run["kind"] in ("suppliers", "customers"):
                table = "supplier_ledger" if run["kind"] == "suppliers" else "customer_ledger"
                posted = self.db.one(f"SELECT coalesce(sum(amount),0) a, count(*) n FROM {table}"
                                     " WHERE org_id=? AND ref_id=?", (ctx.org_id, run_id))
                rec = {"ok": posted["a"] == expected_balance == done["posted_balance"],
                       "file_balance_total": expected_balance, "ledger_balance_total": posted["a"],
                       "ledger_rows": posted["n"], "rows_in_file": len(rows), "rows_accounted": accounted}
                report["reconciliation"] = rec
                if not rec["ok"]:
                    raise Conflict("الأرصدة بعد الاستيراد لا تطابق الملف؛ تم إلغاء العملية بالكامل",
                                   code="reconciliation_failed", details=rec)
            if run["kind"] == "opening_stock":
                report["reconciliation"] = {**self._reconcile(ctx, run_id, report),
                                            "rows_in_file": len(rows), "rows_accounted": accounted}
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
                    done: dict, keep_duplicate=None) -> None:
        fields = {k: d.get(k) for k in ("code", "name_en", "name_ar", "manufacturer",
                                        "active_ingredient", "strength", "dosage_form",
                                        "pack_size", "min_stock", "location", "is_imported",
                                        "is_drug") if d.get(k) is not None}
        if d.get("public_price") is not None:
            fields["public_price"] = to_piasters(Decimal(d["public_price"]))
        if action == "create":
            item_id = catalog.create_item(self.db, ctx, fields, force=True, source="import",
                                          dedupe_suffix=str(keep_duplicate) if keep_duplicate else None)
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
        base = _whole_units(qty, pack, Decimal(str(options.get("max_unit_error", 0)))) \
            if options.get("qty_unit") == "pack" else (
            int(qty) if qty == qty.to_integral_value() else None)
        if base is None:
            raise ValidationFailed(f"الكمية {qty} لا تساوي عدد صحيح من الوحدات للصنف {item['name_en'] or item['name_ar']}",
                                   code="fractional_qty")
        base_units = base
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
    from ..core.xlsx_safe import as_text
    as_text(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _whole_units(qty: Decimal, pack: int, max_unit_error: Decimal | None = None) -> int | None:
    """Packs -> base units. Exports print 3 decimals (15.667 = 15 + 2/3 packs),
    so a rounding error up to 0.0006 pack per unit of pack size is accepted.
    E-Stock also accumulates drift on split items (0.336 for 1/3); the caller
    may allow a larger error in base units (options['max_unit_error']) — each
    such row is reported as 'rounded_units'. Anything else returns None."""
    base = qty * pack
    whole = base.to_integral_value(ROUND_HALF_UP)
    tol = max(Decimal("0.0006") * pack, max_unit_error or Decimal(0))
    return int(whole) if abs(base - whole) <= tol else None


def _now() -> str:
    from ..core.accounts import iso, utcnow
    return iso(utcnow())
