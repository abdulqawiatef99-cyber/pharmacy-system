"""Adapters for E-Stock (Modern Soft) report exports ("Excel - Data Only").

E-Stock reports are printed layouts, not tables: titles, per-store sections,
sub-total and grand-total lines, and (in the stock-by-expiry report) a header
row whose first cells hold the store name, which shifts every column name by
four positions relative to the data. Reading them with the generic header
detector would map 'cost' onto the company column, so each known report is
converted here into a clean table with canonical headers, and its printed
totals are checked before anything is returned: a truncated or edited file is
refused instead of half-imported.

Verified against real exports dated 2026-10-24 (layouts documented in
docs/ESTOCK_IMPORT.md). Nothing here guesses a value: unknown layouts return
None so the caller falls back to the generic importer / manual mapping.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction

from ..catalog.normalize import normalize_text
from ..core.errors import ValidationFailed

CATALOG_HEADER = ["الكود", "الاسم الانجليزي", "الاسم العربي", "الشركة", "سعر الجمهور"]
STOCK_HEADER = ["المخزن", "الكود", "اسم الصنف", "الشركة", "الكمية", "التكلفة", "الصلاحية"]
SALES_HEADER = ["الكود", "اسم الصنف", "الشركة", "كمية البيع", "الرصيد", "سعر البيع",
                "إجمالى س.البيع", "إجمالى التكلفة"]

_TOL = Decimal("0.05")          # printed totals are rounded by E-Stock


def _n(v) -> str:
    return normalize_text(str(v)) if v is not None else ""


def _dec(v) -> Decimal:
    return Decimal(str(v))


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def detect(rows: list[list]) -> str | None:
    """Which E-Stock report this is: 'catalog' | 'stock_by_expiry' | 'sales' | None."""
    for r in rows[:10]:
        t = [_n(c) for c in r]
        if t[:5] == [_n(x) for x in ("سعر الجمهور", "الشركة المنتجة", "الاسم الانجليزى",
                                      "الاسم العربى", "الكود")]:
            return "catalog"
        if len(t) >= 15 and t[1] == _n("اسم المخزن") and t[14] == _n("الكود") and \
                t[9] == _n("الكمية"):
            return "stock_by_expiry"
        if any(c == "الموظف" for c in r[:1]) and len(t) > 22:
            return "purchases"
        if t[:9] == [_n(x) for x in ("ن.الربح", "إجمالى التكلفة", "إجمالى س.البيع", "سعر البيع",
                                      "الرصيد", "كمية البيع", "الشركة", "إسم الصنف", "ك.الصنف")]:
            return "sales"
    return None


def _expiry(v):
    """E-Stock stores the printed month as the 1st of that month: keep month precision."""
    if isinstance(v, (datetime, date)):
        return f"{v.month:02d}/{v.year}"
    return v


def _check(label: str, got: Decimal, printed) -> None:
    if printed is None or abs(got - _dec(printed)) > _TOL:
        raise ValidationFailed(f"إجمالي {label} في الملف ({printed}) لا يساوي مجموع الأسطر ({got})؛"
                               " الملف ناقص أو متعدل", code="estock_totals_mismatch")


def parse_catalog(rows: list[list]) -> list[list]:
    out, footer = [CATALOG_HEADER], None
    for r in rows[1:]:
        r = list(r) + [None] * 5
        if isinstance(r[4], int):
            out.append([r[4], r[2], r[3], r[1], r[0]])
        elif _n(r[1]) == _n("إجمالى عدد الأصناف المطبوعة"):
            footer = r[0]
    if footer is None or int(footer) != len(out) - 1:
        raise ValidationFailed(f"عدد الأصناف في الملف ({footer}) لا يساوي الأسطر ({len(out) - 1})",
                               code="estock_totals_mismatch")
    return out


def parse_stock(rows: list[list], stores: set[str] | None = None) -> tuple[list[list], dict]:
    """Stock-by-expiry report → one row per batch. Returns (table, summary).
    `stores`: store names to keep (None = all). Totals are verified per store
    and for the whole file before filtering."""
    out = [STOCK_HEADER]
    summary: dict = {"stores": {}}
    store = None
    acc: dict[str, dict] = {}
    grand = None
    for r in rows:
        r = list(r) + [None] * 15
        if _n(r[1]) == _n("اسم المخزن"):
            store = str(r[0]).strip()
            acc[store] = {"rows": 0, "items": set(), "cost": Decimal(0), "sale": Decimal(0)}
        elif isinstance(r[10], int) and _is_num(r[5]):
            if store is None:
                raise ValidationFailed("سطر بيانات قبل اسم المخزن؛ شكل التقرير غير متوقع",
                                       code="estock_layout")
            code, qty, cost = r[10], r[5], r[4]
            a = acc[store]
            a["rows"] += 1
            a["items"].add(code)
            a["cost"] += _dec(r[2])
            a["sale"] += _dec(r[1])
            if stores is None or store in stores:
                out.append([store, code, r[9], r[8], qty, cost, _expiry(r[6])])
        elif _n(r[1]) == _n("إجمالى البيع") and store:
            acc[store]["closed"] = True
            _check(f"بيع مخزن {store}", acc[store]["sale"], r[0])
            _check(f"تكلفة مخزن {store}", acc[store]["cost"], r[2])
            if int(r[4] or -1) != len(acc[store]["items"]):
                raise ValidationFailed(f"عدد أصناف مخزن {store} لا يطابق", code="estock_totals_mismatch")
        elif _n(r[0]) == _n("إجمالى جميع المخازن"):
            grand = r
    if not acc:
        raise ValidationFailed("مفيش مخازن في الملف", code="estock_layout")
    # a file cut off at the end has no total line for its last store and no grand total (#116)
    open_ = [s for s, a in acc.items() if not a.get("closed")]
    if open_:
        raise ValidationFailed(f"سطر إجمالي مخزن {open_[0]} ناقص؛ الملف ناقص", code="estock_totals_mismatch")
    if grand is None:
        raise ValidationFailed("سطر «إجمالى جميع المخازن» ناقص؛ الملف ناقص", code="estock_totals_mismatch")
    _check("بيع كل المخازن", sum((a["sale"] for a in acc.values()), Decimal(0)), grand[1])
    _check("تكلفة كل المخازن", sum((a["cost"] for a in acc.values()), Decimal(0)), grand[3])
    for s, a in acc.items():
        summary["stores"][s] = {"batches": a["rows"], "items": len(a["items"]),
                                "cost": str(a["cost"].quantize(Decimal("0.01"))),
                                "sale": str(a["sale"].quantize(Decimal("0.01")))}
    return out, summary


def parse_sales(rows: list[list]) -> tuple[list[list], dict]:
    out, footer = [SALES_HEADER], None
    cost = sale = Decimal(0)
    for r in rows[1:]:
        r = list(r) + [None] * 9
        if isinstance(r[8], int):
            out.append([r[8], r[7], r[6], r[5], r[4], r[3], r[2], r[1]])
            cost += _dec(r[1])
            sale += _dec(r[2])
        elif _n(r[1]) == _n("متوسط ن.الربح"):
            footer = r
    if footer is None:
        raise ValidationFailed("سطر الإجمالي ناقص من تقرير المبيعات", code="estock_totals_mismatch")
    _check("تكلفة المبيعات", cost, footer[2])
    _check("قيمة المبيعات", sale, footer[4])
    if int(footer[6]) != len(out) - 1:
        raise ValidationFailed("عدد أصناف تقرير المبيعات لا يطابق", code="estock_totals_mismatch")
    return out, {"items": len(out) - 1, "cost": str(cost), "sale": str(sale)}


def parse_purchases(rows: list[list]) -> tuple[list[dict], dict]:
    """'تقرير فواتير المشتريات بالأصناف': per supplier section, per invoice header
    (date at col 13, line count at col 20, invoice no at col 22), then item lines
    (code col 12, name 11, qty 8, bonus 7, cost 3). Line counts are checked per invoice.
    Supplier 'جرد' = stock adjustments recorded as purchases (flagged, not a purchase)."""
    lines: list[dict] = []
    sup = when = inv = None
    expected: dict[tuple, int] = {}
    got: dict[tuple, int] = {}
    for r in rows:
        r = list(r) + [None] * 25
        if r[1] == "الاسم" and r[3] == "الكود":
            sup = str(r[0]).strip()
        elif r[0] == "الموظف":
            if not isinstance(r[13], (datetime, date)):
                raise ValidationFailed("تاريخ فاتورة مش مفهوم في تقرير المشتريات", code="estock_layout")
            when, inv = r[13], (sup, str(r[22]))
            expected[inv] = int(r[20] or 0)
        elif isinstance(r[12], int) and _is_num(r[8]):
            if inv is None:
                raise ValidationFailed("سطر صنف قبل رأس الفاتورة", code="estock_layout")
            got[inv] = got.get(inv, 0) + 1
            lines.append({"supplier": sup, "date": when, "invoice": inv[1], "code": r[12], "name": r[11],
                          "qty": r[8], "bonus": r[7], "cost": r[3], "adjustment": sup == "جرد"})
    if not expected:
        raise ValidationFailed("مفيش فواتير في الملف", code="estock_layout")
    bad = [k for k, n in expected.items() if got.get(k, 0) != n]
    if bad:
        raise ValidationFailed(f"عدد أصناف {len(bad)} فاتورة مش مطابق لرأسها؛ الملف ناقص",
                               code="estock_totals_mismatch")
    dates = [x["date"] for x in lines]
    return lines, {"invoices": len(expected), "lines": len(lines),
                   "from": min(dates).date().isoformat(), "to": max(dates).date().isoformat()}


def normalize(rows: list[list], kind: str, options: dict | None = None) -> list[list] | None:
    """Entry point used by DataImport.analyze for source_system='estock'."""
    rep = detect(rows)
    if rep == "catalog" and kind == "items":
        return parse_catalog(rows)
    if rep == "stock_by_expiry" and kind == "opening_stock":
        stores = (options or {}).get("estock_stores")
        return parse_stock(rows, set(stores) if stores else None)[0]
    if rep is not None:
        raise ValidationFailed("تقرير E-Stock ده مش مناسب لنوع الاستيراد المختار",
                               code="estock_wrong_report", details={"report": rep, "kind": kind})
    return None


def suggest_pack_sizes(stock_rows: list[list], max_units: int = 100) -> dict[int, dict]:
    """E-Stock keeps fractional packs (15.667 = 15 packs + 2 of 3 strips). Our
    ledger counts whole base units, so such an item needs its units-per-pack.
    This only *suggests* the smallest pack size that turns every fraction of
    the item into whole units; a person must confirm it (it is never applied
    silently). Items whose fractions fit no size up to `max_units` are
    reported with suggestion None."""
    fr: dict[int, list[Decimal]] = {}
    names: dict[int, str] = {}
    for r in stock_rows[1:]:
        q = _dec(r[4])
        if q != q.to_integral_value():
            fr.setdefault(r[1], []).append(q)
            names[r[1]] = r[2]
    out = {}
    for code, qs in fr.items():
        pick = None
        for n in range(2, max_units + 1):
            # E-Stock prints 3 decimals (rounding error <= 0.0005 pack)
            if all(abs(q * n - (q * n).to_integral_value()) <= Decimal("0.0006") * n for q in qs):
                pick = n
                break
        out[code] = {"name": names[code], "fractions": [str(q) for q in qs], "suggested": pick,
                     "as_fraction": [str(Fraction(str(q)).limit_denominator(100)) for q in qs]}
    return out
