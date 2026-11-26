"""Turn a grid of cells (from Excel, CSV or a PDF table) into an ExtractedInvoice.

Supplier sheets differ in column order, language and extra header rows, so we
locate the header row by matching known Arabic/English column names, then map
columns to fields. Values are taken as-is ('read'); anything derived later is
marked 'inferred' by the validation step.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from ...catalog.normalize import normalize_text
from ..model import (ExtractedInvoice, ExtractedLine, normalize_percent_column, parse_date,
                     parse_decimal, parse_expiry, parse_int, parse_percent, parse_qty_bonus)

# field -> synonyms (already normalized: lowercase, Arabic letters unified)
HEADERS: dict[str, list[str]] = {
    "raw_name": ["الصنف", "اسم الصنف", "الاسم", "البيان", "اسم الدواء", "الدواء", "item",
                 "item name", "description", "product", "product name", "name", "صنف",
                 "اسم المنتج", "المنتج", "trade name"],
    "raw_code": ["كود", "الكود", "كود الصنف", "code", "item code", "رقم الصنف", "sku", "ref"],
    "raw_barcode": ["باركود", "الباركود", "barcode", "ean", "gtin"],
    "qty": ["الكميه", "كميه", "الكمية", "كمية", "العدد", "عدد", "qty", "quantity", "qnty",
            "ك"],
    "bonus_qty": ["بونص", "البونص", "هديه", "مجاني", "bonus", "free", "foc", "free qty"],
    "public_price": ["سعر الجمهور", "سعر البيع", "ج", "سعر الجمهور للعبوه", "public price",
                     "pub price", "retail price", "consumer price", "selling price", "price",
                     "السعر", "سعر"],
    "discount_pct": ["خصم 1", "خصم", "الخصم", "نسبه الخصم", "خصم %", "disc", "disc %", "discount",
                     "discount %", "dis %", "%"],
    "unit_net_price": ["سعر الصيدلي", "صافي السعر", "سعر الوحده", "الصافي", "سعر الشراء",
                       "net price", "unit price", "pharmacy price", "net", "cost"],
    "tax_amount": ["ضريبه", "الضريبه", "ض ق م", "vat", "tax", "vat amount"],
    "line_total": ["الاجمالي", "اجمالي", "الاجمالى", "القيمه", "المبلغ", "total", "amount",
                   "line total", "net amount", "value", "الصافي الاجمالي"],
    "batch_no": ["التشغيله", "تشغيله", "رقم التشغيله", "batch", "batch no", "lot", "lot no",
                 "bn", "b n"],
    "expiry_date": ["الصلاحيه", "صلاحيه", "تاريخ الصلاحيه", "انتهاء", "exp", "expiry",
                    "exp date", "expiry date", "ed"],
}
_SYN = {normalize_text(s): f for f, ss in HEADERS.items() for s in ss}
_TOTAL_WORDS = ("الاجمالي", "اجمالي", "total", "grand total", "المجموع", "صافي الفاتوره",
                "net total", "الصافي", "المطلوب")


_PUBLIC_TOTAL_HEADERS = {normalize_text(s) for s in (
    "اجمالي جمهور", "اجمالي الجمهور", "اجمالي سعر الجمهور",
    "الاجمالي جمهور", "الاجمالي الجمهور", "الاجمالي سعر الجمهور",
)}
_COUNT_FOOTERS = ("عدد الاصناف", "عدد البنود", "item count")
_ITEM_VALUES = ("qty", "bonus_qty", "public_price", "unit_net_price", "line_total")
_EMPTY_ITEM_VALUES = {"", "-", "—", "–"}


def _starts_with_label(text: str, labels: tuple[str, ...]) -> bool:
    return any(text == label or text.startswith(label + " ") for label in labels)


def _cell_text(v: Any) -> str:
    return normalize_text(str(v)) if v is not None else ""


def _header_field(text: str) -> str | None:
    if not text or text in _PUBLIC_TOTAL_HEADERS:
        return None
    if text in _SYN:
        return _SYN[text]
    t = text.replace("%", " %").strip()
    if t in _SYN:
        return _SYN[t]
    # contained phrase, longest synonym wins (e.g. 'سعر الجمهور للعلبه')
    best = None
    for syn, f in _SYN.items():
        if len(syn) >= 3 and re.search(rf"(^|\s){re.escape(syn)}(\s|$)", text):
            if best is None or len(syn) > len(best[0]):
                best = (syn, f)
    return best[1] if best else None


def find_header(rows: list[list[Any]], scan: int = 40) -> tuple[int, dict[int, str]] | None:
    best: tuple[int, dict[int, str]] | None = None
    for i, row in enumerate(rows[:scan]):
        cmap: dict[int, str] = {}
        for j, v in enumerate(row):
            f = _header_field(_cell_text(v))
            if f and f not in cmap.values():
                cmap[j] = f
        if "raw_name" in cmap.values() and len(cmap) >= 3:
            if best is None or len(cmap) > len(best[1]):
                best = (i, cmap)
    return best


_META = {
    "invoice_no": re.compile(r"(?:رقم الفاتوره|فاتوره رقم|invoice\s*(?:no|number|#)\.?|inv\.?\s*no\.?)"
                             r"[\s:\-#]*([A-Za-z0-9][A-Za-z0-9\-/]{0,30})", re.I),
    "invoice_date": re.compile(r"(?:تاريخ الفاتوره|التاريخ|invoice\s*date|date)"
                               r"[\s:\-]*(\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4})", re.I),
    "supplier_name": re.compile(r"(?:اسم المورد|المورد|supplier|vendor)\s*[:\-]+\s*(.{2,60})", re.I),
}
_LIGHT = str.maketrans({"٪": "%", "أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه",
                        "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
                        "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9"})


def _light(s: str) -> str:
    """Arabic letter/digit unification only; keeps case and punctuation so
    values like 'INV-1001' survive intact."""
    return re.sub(r"\s+", " ", s.translate(_LIGHT)).strip()


def _scan_meta(texts: Iterable[str], inv: ExtractedInvoice) -> None:
    for raw in texts:
        t = _light(str(raw))
        if not t:
            continue
        for key, rx in _META.items():
            if getattr(inv, key):
                continue
            m = rx.search(t)
            if not m:
                continue
            val = m.group(1).strip()
            if key == "invoice_date":
                val = parse_date(val)
                if not val:
                    continue
            setattr(inv, key, val)
            inv.header_sources[key] = "read"
            inv.header_confidence[key] = 0.9


def grid_to_invoice(rows: list[list[Any]], method: str = "structured",
                    extra_text: list[str] | None = None) -> ExtractedInvoice:
    inv = ExtractedInvoice(method=method)
    found = find_header(rows)
    if not found:
        inv.warnings.append("header_not_found")
        _scan_meta([str(c) for r in rows[:30] for c in r if c is not None] + (extra_text or []), inv)
        return inv
    hi, cmap = found
    public_total_columns = {i for i, cell in enumerate(rows[hi]) if _cell_text(cell) in _PUBLIC_TOTAL_HEADERS}
    # metadata usually sits above the table
    meta_cells = [str(c) for r in rows[:hi] for c in r if c not in (None, "")]
    # 'label' cell followed by value cell on the same row: join neighbours
    for r in rows[:hi]:
        cells = [str(c) for c in r if c not in (None, "")]
        meta_cells += [f"{a}: {b}" for a, b in zip(cells, cells[1:])]
    _scan_meta(meta_cells + (extra_text or []), inv)
    inv.header_texts = [str(c)[:120] for r in rows[:hi] for c in r if c not in (None, "")][:40]

    raw_lines: list[dict[str, Any]] = []
    for r in rows[hi + 1:]:
        vals = {f: (r[j] if j < len(r) else None) for j, f in cmap.items()}
        name = str(vals.get("raw_name") or "").strip()
        texts = [_cell_text(c) for c in r if c not in (None, "")]
        label = normalize_text(name)
        no_name = not label or label in ("none", "nan")
        if _starts_with_label(label, _COUNT_FOOTERS):
            continue
        if _starts_with_label(label, _TOTAL_WORDS) or (
            no_name and any(_starts_with_label(text, _TOTAL_WORDS) for text in texts)
        ):
            if inv.declared_total is None:
                declared = parse_decimal(vals.get("line_total"))
                if declared is None:
                    nums = [value for i, cell in enumerate(r) if i not in public_total_columns
                            if (value := parse_decimal(cell)) is not None]
                    if nums:
                        declared = nums[-1]
                if declared is not None:
                    inv.declared_total = declared
                    inv.header_sources["declared_total"] = "read"
                    inv.header_confidence["declared_total"] = 0.9
            continue
        if no_name or re.fullmatch(r"[+-]?\d+(?:[.,٫]\d+)?", name):
            continue
        if not any(vals.get(field) is not None and str(vals[field]).strip() not in _EMPTY_ITEM_VALUES
                   for field in _ITEM_VALUES):
            continue
        raw_lines.append(vals)

    # a value written with "%" is a percent as it stands ("0.5%" is half a percent): a column holding one is
    # never read as fractions (it became 50%, #324 S21)
    signed = any(_has_percent_sign(v.get("discount_pct")) for v in raw_lines)
    values = [parse_percent(v.get("discount_pct")) if not _is_fraction_text(v.get("discount_pct"))
              else parse_decimal(v.get("discount_pct")) for v in raw_lines]
    discounts, converted = (values, False) if signed else normalize_percent_column(values)
    if converted:
        inv.warnings.append("discount_fraction_converted")

    for vals, disc in zip(raw_lines, discounts):
        line = ExtractedLine(raw_name=str(vals["raw_name"]).strip())
        for f, raw in vals.items():
            if f == "raw_name":
                continue
            if f in ("raw_code", "raw_barcode", "batch_no"):
                val = _text(raw)
                if f == "raw_barcode" and isinstance(raw, float):
                    val = str(int(raw))
            elif f in ("qty", "bonus_qty"):
                val = parse_int(raw)
                if f == "bonus_qty" and not val and line.sources.get("bonus_qty") == "read":
                    continue        # "10+2" already gave the bonus: an empty or 0 bonus cell must not erase it (#324 S20)
                qb = parse_qty_bonus(raw) if f == "qty" and val is None else None
                if qb and vals.get("bonus_qty") in (None, "", 0):
                    val = qb[0]
                    line.bonus_qty = qb[1]
                    line.sources["bonus_qty"], line.confidence["bonus_qty"] = "read", 1.0
            elif f == "discount_pct":
                val = disc
            elif f == "expiry_date":
                val, prec = parse_expiry(raw)
                line.expiry_precision = prec
            else:
                val = parse_decimal(raw)
            if raw not in (None, "") and val is None:
                line.confidence[f] = 0.0           # present but unreadable -> must review
                line.sources[f] = "read"
                inv.warnings.append(f"unparsed:{f}")
                continue
            if val is not None:
                setattr(line, f, val)
                line.sources[f] = "read"
                line.confidence[f] = 1.0
        line.sources["raw_name"] = "read"
        line.confidence["raw_name"] = 1.0
        inv.lines.append(line)
    if not inv.lines:
        inv.warnings.append("no_lines")
    return inv


def _has_percent_sign(v) -> bool:
    return "%" in str(v) or "٪" in str(v)


def _is_fraction_text(v) -> bool:
    d = parse_decimal(v)
    return d is not None and d <= 1 and not _has_percent_sign(v)


def _text(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, float) and v == int(v):
        v = int(v)
    s = str(v).strip()
    return s or None
