"""Dead / slow stock ("الرواكد") from E-Stock exports, while E-Stock is still the cashier.

Inputs (Excel - Data Only): stock by expiry, item sales for the last N months,
purchase invoices with items for the same period. Everything is computed from
those files; nothing is written to the database.

Segments (owner's definitions, 2026-10-24):
- dead      : no sale in the period and last real purchase before as_of-6 months (or none)
- stagnant  : no sale and last purchase between as_of-6 and as_of-3 months
- new       : no sale and bought in the last 3 months (still has its chance)
- slow      : sells, but the stock covers more than 12 months of sales
Stock-count adjustments recorded by E-Stock as purchases from 'جرد' are not purchases.
Seasonal and chronic items are NOT excluded automatically (the pharmacist reviews).
"""
from __future__ import annotations

import io
from datetime import date, datetime, timedelta

from ..core.errors import ValidationFailed
from ..dataimport import estock

SEGMENTS = [("dead", "ديد ستوك"), ("stagnant", "راكد"), ("slow", "بطيء"), ("new", "جديد")]


def _rows(data: bytes) -> list[list]:
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001 - any unreadable workbook
        raise ValidationFailed("الملف ليس Excel سليم (صدّره XLSX Data Only)") from e
    return [list(r) for r in wb.active.iter_rows(values_only=True)]


def compute(stock: bytes, sales: bytes, purchases: bytes, *, as_of: date | None = None,
            store: str = "الصيدلية", period_months: int = 12) -> dict:
    files = {"stock": _rows(stock), "sales": _rows(sales), "purchases": _rows(purchases)}
    kinds = {"stock": "stock_by_expiry", "sales": "sales", "purchases": "purchases"}
    for k, rows in files.items():
        got = estock.detect(rows)
        if got != kinds[k]:
            raise ValidationFailed(f"الملف المرفوع مكان '{k}' ليس التقرير الصحيح (وُجد: {got or 'غير معروف'})",
                                   code="wrong_report")
    table, ssum = estock.parse_stock(files["stock"], {store})
    if store not in ssum["stores"]:
        raise ValidationFailed(f"المخزن '{store}' غير موجود في ملف الأرصدة")
    sales_t, sales_sum = estock.parse_sales(files["sales"])
    plines, psum = estock.parse_purchases(files["purchases"])
    as_of = as_of or date.today()
    cut3, cut6 = as_of - timedelta(days=91), as_of - timedelta(days=182)
    month_start = datetime(as_of.year, as_of.month, 1)
    soon = (month_start + timedelta(days=31 * 6)).replace(day=1)

    sold = {r[0]: float(r[3]) for r in sales_t[1:]}
    last_buy: dict[int, tuple] = {}
    adj_only: set[int] = set()
    for p in plines:
        if p["adjustment"]:
            adj_only.add(p["code"])
            continue
        if p["code"] not in last_buy or p["date"] > last_buy[p["code"]][0]:
            last_buy[p["code"]] = (p["date"], p["supplier"])
    items: dict[int, dict] = {}
    batches = []
    for _, code, name, company, qty, cost, exp in table[1:]:
        it = items.setdefault(code, {"code": code, "name": name, "company": company, "qty": 0.0,
                                     "value": 0.0, "exps": []})
        it["qty"] += float(qty)
        it["value"] += float(qty) * float(cost)
        e = datetime.strptime(exp, "%m/%Y") if isinstance(exp, str) and "/" in exp else None
        if e:
            it["exps"].append(e)
            if e < soon:
                batches.append({"code": code, "name": name, "qty": qty, "value": round(float(qty) * float(cost), 2),
                                "expiry": exp, "expired": e < month_start})
    out = {k: [] for k, _ in SEGMENTS}
    for code, it in items.items():
        s = sold.get(code, 0.0)
        lb = last_buy.get(code)
        row = {**{k: it[k] for k in ("code", "name", "company")}, "qty": round(it["qty"], 3),
               "value": round(it["value"], 2), "sold_in_period": s,
               "months_of_cover": round(it["qty"] / (s / period_months), 1) if s else None,
               "nearest_expiry": min(it["exps"]).strftime("%m/%Y") if it["exps"] else None,
               "last_purchase": lb[0].date().isoformat() if lb else None,
               "supplier": lb[1] if lb else None,
               "note": "دخل من الجرد فقط" if code in adj_only and not lb else None}
        if not s:
            seg = "new" if lb and lb[0].date() >= cut3 else ("stagnant" if lb and lb[0].date() >= cut6 else "dead")
        elif row["months_of_cover"] > 12:
            seg = "slow"
        else:
            continue
        out[seg].append(row)
    for v in out.values():
        v.sort(key=lambda r: -r["value"])
    total = sum(it["value"] for it in items.values())
    batches.sort(key=lambda b: (b["expiry"][3:] + b["expiry"][:2], -b["value"]))
    summary = [{"segment": k, "label": lbl, "items": len(out[k]), "value": round(sum(r["value"] for r in out[k]), 2),
                "share": round(100 * sum(r["value"] for r in out[k]) / total, 1) if total else 0}
               for k, lbl in SEGMENTS]
    bought_in_period = [r for k in ("dead", "stagnant") for r in out[k] if r["last_purchase"]]
    return {"as_of": as_of.isoformat(), "store": store, "stock_value": round(total, 2), "summary": summary,
            "purchase_mistakes": {"items": len(bought_in_period),
                                  "value": round(sum(r["value"] for r in bought_in_period), 2)},
            "expiry": {"expired_value": round(sum(b["value"] for b in batches if b["expired"]), 2),
                       "within_6_months_value": round(sum(b["value"] for b in batches if not b["expired"]), 2)},
            "sources": {"stock": ssum["stores"][store], "sales": sales_sum, "purchases": psum},
            "segments": out, "expiry_batches": batches}


def to_xlsx(rep: dict) -> bytes:
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "الملخص"
    ws.append(["الشريحة", "عدد الأصناف", "التكلفة المحبوسة", "% من المخزون"])
    for s in rep["summary"]:
        ws.append([s["label"], s["items"], s["value"], s["share"]])
    ws.append([])
    ws.append(["قيمة المخزون بالتكلفة", None, rep["stock_value"]])
    ws.append(["اشتُريت خلال الفترة ولم تُبع (أخطاء مشتريات)", rep["purchase_mistakes"]["items"],
               rep["purchase_mistakes"]["value"]])
    ws.append(["منتهي الصلاحية", None, rep["expiry"]["expired_value"]])
    ws.append(["ستنتهي خلال 6 أشهر", None, rep["expiry"]["within_6_months_value"]])
    ws.append([f"بتاريخ {rep['as_of']} — مخزن {rep['store']}. الأصناف الموسمية والمزمنة غير مستبعدة: راجعها قبل البيع."])
    head = ["الكود", "الصنف", "الشركة", "الرصيد (علبة)", "التكلفة المحبوسة", "بيع الفترة", "الرصيد يكفي (شهر)",
            "أقرب صلاحية", "آخر شراء", "المورد", "ملاحظة"]
    for k, lbl in SEGMENTS:
        w = wb.create_sheet(lbl)
        w.append(head)
        for r in rep["segments"][k]:
            w.append([r["code"], r["name"], r["company"], r["qty"], r["value"], r["sold_in_period"],
                      r["months_of_cover"], r["nearest_expiry"] or "غير مسجلة", r["last_purchase"] or "قبل الفترة",
                      r["supplier"], r["note"]])
    w = wb.create_sheet("منتهي وقريب")
    w.append(["الكود", "الصنف", "الكمية", "التكلفة", "الصلاحية", "الحالة"])
    for b in rep["expiry_batches"]:
        w.append([b["code"], b["name"], b["qty"], b["value"], b["expiry"], "منتهي" if b["expired"] else "خلال 6 أشهر"])
    for w in wb.worksheets:
        w.sheet_view.rightToLeft = True
        for c in w[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="1F4E79")
        for i, col in enumerate(w.columns, 1):
            w.column_dimensions[get_column_letter(i)].width = min(45, max(10, max(len(str(c.value or "")) for c in col) + 2))
        w.freeze_panes = "A2"
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()
