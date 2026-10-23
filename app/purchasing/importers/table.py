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
                     parse_decimal, parse_expiry, parse_int, parse_percent)

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
    "discount_pct": ["خصم", "الخصم", "نسبه الخصم", "خصم %", "disc", "disc %", "discount",
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


def _cell_text(v: Any) -> str:
    return normalize_text(str(v)) if v is not None else ""


def _header_field(text: str) -> str | None:
    if not text:
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
                             r"\s*[:\-#]?\s*([A-Za-z0-9][A-Za-z0-9\-/]{0,30})", re.I),
    "invoice_date": re.compile(r"(?:تاريخ الفاتوره|التاريخ|invoice\s*date|date)"
                               r"\s*[:\-]?\s*(\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4})", re.I),
    "supplier_name": re.compile(r"(?:اسم المورد|المورد|supplier|vendor)\s*[:\-]\s*(.{2,60})", re.I),
}
_LIGHT = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه",
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
    # metadata usually sits above the table
    meta_cells = [str(c) for r in rows[:hi] for c in r if c not in (None, "")]
    # 'label' cell followed by value cell on the same row: join neighbours
    for r in rows[:hi]:
        cells = [str(c) for c in r if c not in (None, "")]
        meta_cells += [f"{a}: {b}" for a, b in zip(cells, cells[1:])]
    _scan_meta(meta_cells + (extra_text or []), inv)

    raw_lines: list[dict[str, Any]] = []
    for r in rows[hi + 1:]:
        vals = {f: (r[j] if j < len(r) else None) for j, f in cmap.items()}
        name = str(vals.get("raw_name") or "").strip()
        texts = [_cell_text(c) for c in r if c not in (None, "")]
        if not any(texts):
            continue
        if not name or name.lower() in ("none", "nan"):
            # totals row: label in some cell + number
            if any(any(w == t or t.startswith(w) for w in _TOTAL_WORDS) for t in texts):
                nums = [parse_decimal(c) for c in r if parse_decimal(c) is not None]
                if nums and inv.declared_total is None:
                    inv.declared_total = nums[-1]
                    inv.header_sources["declared_total"] = "read"
                    inv.header_confidence["declared_total"] = 0.9
            continue
        if any(normalize_text(name).startswith(w) for w in _TOTAL_WORDS):
            nums = [parse_decimal(c) for c in r if parse_decimal(c) is not None]
            if nums and inv.declared_total is None:
                inv.declared_total = nums[-1]
                inv.header_sources["declared_total"] = "read"
                inv.header_confidence["declared_total"] = 0.9
            continue
        raw_lines.append(vals)

    discounts, converted = normalize_percent_column(
        [parse_percent(v.get("discount_pct")) if not _is_fraction_text(v.get("discount_pct"))
         else parse_decimal(v.get("discount_pct")) for v in raw_lines])
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


def _is_fraction_text(v) -> bool:
    d = parse_decimal(v)
    return d is not None and d <= 1 and "%" not in str(v)


def _text(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, float) and v == int(v):
        v = int(v)
    s = str(v).strip()
    return s or None
