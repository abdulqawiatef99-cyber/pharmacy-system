"""Build the review workbook and the system import files from build/*.csv.

Usage: python -I scripts/build_outputs.py <originals_dir> <build_dir> <output_dir>
"""
import csv
import hashlib
import json
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_catalog import SUPPLIER_ORDER, SUPPLIERS  # noqa: E402

FONT = "Arial"
HEAD = PatternFill("solid", fgColor="1F4E78")
WARN = PatternFill("solid", fgColor="FFF2CC")
BEST = PatternFill("solid", fgColor="E2EFDA")


def read(p):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def num(v):
    if v in ("", None):
        return None
    d = Decimal(v)
    return int(d) if d == d.to_integral() else float(d)


def piasters(v):
    return "" if v in ("", None) else int((Decimal(v) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def bp(v):
    return "" if v in ("", None) else int((Decimal(v) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def sheet(wb, title, headers, rows, widths=None, rtl_cols=(), number_cols=()):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(name=FONT, bold=True, color="FFFFFF")
        c.fill = HEAD
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for r in rows:
        ws.append(r)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for i, h in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(h, 14)
    for col in number_cols:
        idx = headers.index(col) + 1
        for (c,) in ws.iter_rows(min_row=2, min_col=idx, max_col=idx):
            c.number_format = "#,##0.00"
    for col in rtl_cols:
        idx = headers.index(col) + 1
        for (c,) in ws.iter_rows(min_row=2, min_col=idx, max_col=idx):
            c.alignment = Alignment(horizontal="right", readingOrder=2)
    return ws


def main(orig, build, out):
    orig, build, out = Path(orig), Path(build), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    offers = read(build / "offers.csv")
    items = read(build / "catalog_items.csv")
    comp = read(build / "comparison.csv")
    unc = read(build / "uncertain_matches.csv")
    val = read(build / "validation_findings.csv")
    metrics = json.loads((build / "catalog_metrics.json").read_text())
    pdf_metrics = json.loads((build / "pdf_extract_metrics.json").read_text())

    wb = Workbook()
    ws = wb.active
    ws.title = "README"
    ws.sheet_view.rightToLeft = True
    lines = [
        "كتالوج الموردين الموحد — نسخة 2026-10-31",
        "",
        "قواعد التكلفة (مؤكدة من صاحب الصيدلية):",
        "• النهضة وابن خلدون والوطنية: التكلفة = سعر الجمهور × (1 − الخصم ÷ 100). الشراء كاش، وخصم «كاش سيارة» هو المطبق في الوطنية.",
        "• فيوتشر: عمود «السعر» هو الصافي، من غير خصم إضافي.",
        "• المصطفى: مفيش سعر جمهور. التكلفة = الأقل بين «سعر ج» و«نصف جملة»، والعمودين محفوظين.",
        "• الحد الأدنى للطلب موجود، لكنه مش محسوب في التكلفة.",
        "• ابن خلدون أسعاره بتاريخ 28 أكتوبر، والباقي بتاريخ 31 أكتوبر.",
        "",
        "الشيتات:",
        "• Catalog: الأصناف الموحدة، مرتبة أبجدياً، الدواء الأول وبعده الإكسسوار.",
        "• Comparison: الأصناف اللي عند مورّدين أو أكتر، بنفس سعر الجمهور، وفيها أرخص مورد والتوفير.",
        "• Uncertain_Matches: أزواج متشابهة مااتدمجتش. اكتب same أو different في آخر عمود.",
        "• Offers: كل سطر من الملفات الأصلية زي ما هو، ومعاه المصدر والصفحة والنص الخام.",
        "• Validation: نتائج الفحص والملاحظات.",
        "• Sources: الملفات الأصلية وبصمة التحقق (SHA-256).",
        "",
        "تنبيهات:",
        "• المطابقة التقريبية (fuzzy) بتتعلم عليها علامة في عمود flags. راجعها قبل ما تعتمد عليها.",
        "• تصنيف drug_warehouse_only_unverified معناه إن الصنف موجود في مخازن الأدوية بس، ومحتاج مراجعة يدوية.",
        "• أسماء ملفات PDF (فيوتشر بالذات) اتصلح ترتيب حروفها آلياً، والنص الخام محفوظ في Offers.",
        "",
        f"الإحصائيات: {metrics['offers']} سطر مصدر ← {metrics['items']} صنف موحد، منهم "
        f"{metrics['items_multi_supplier']} صنف عند أكتر من مورد.",
    ]
    for ln in lines:
        ws.append([ln])
    ws.column_dimensions["A"].width = 130
    for (c,) in ws.iter_rows():
        c.font = Font(name=FONT, size=11, bold=c.row == 1)
        c.alignment = Alignment(horizontal="right", readingOrder=2, wrap_text=True)

    import hashlib as _h
    src_rows = []
    for sid in SUPPLIER_ORDER:
        s = SUPPLIERS[sid]
        f = orig / s["file"]
        src_rows.append([sid, s["name"], s["name_ar"], s["group"], s["file"],
                         _h.sha256(f.read_bytes()).hexdigest(), metrics["offers_by_supplier"].get(sid),
                         s["price_date"], {"drug_wh": "public*(1-discount/100), cash tier"}.get(
                             s["group"], "net price column" if sid == "future" else "min(سعر ج, نصف جمله)")])
    sheet(wb, "Sources", ["supplier_key", "supplier", "name_ar", "type", "file", "sha256", "rows", "price_date",
                          "cost_rule"], src_rows, {"file": 40, "sha256": 66, "cost_rule": 34}, ("name_ar", "file"))

    cat_h = ["item_id", "category", "category_basis", "display_name", "public_price", "best_supplier", "best_cost",
             "supplier_count", "suppliers", "strengths", "form", "pack_count", "other_public_prices", "flags",
             "name_norm"]
    ws = sheet(wb, "Catalog", cat_h, [[i["item_id"], i["category"], i["category_basis"], i["display_name"],
                                        num(i["public_price"]), i["best_supplier"], num(i["best_cost"]),
                                        int(i["supplier_count"]), i["suppliers"], i["strengths"], i["form"],
                                        i["pack_count"], i["other_public_prices"], i["flags"], i["name_norm"]]
                                       for i in items],
               {"display_name": 48, "category_basis": 26, "suppliers": 30, "flags": 36, "name_norm": 40},
               ("display_name", "name_norm"), ("public_price", "best_cost"))

    cmp_h = ["item_id", "category", "display_name", "public_price"] + [f"cost_{s}" for s in SUPPLIER_ORDER] + \
            ["best_cost", "best_supplier", "second_cost", "saving", "saving_pct", "note"]
    rows = []
    for n, c in enumerate(comp, start=2):
        rng = f"E{n}:I{n}"
        rows.append([c["item_id"], c["category"], c["display_name"], num(c["public_price"])] +
                    [num(c[f"cost_{s}"]) for s in SUPPLIER_ORDER] +
                    [f"=MIN({rng})",
                     f'=INDEX({{"{"\",\"".join(SUPPLIER_ORDER)}"}},MATCH(J{n},{rng},0))',
                     f"=SMALL({rng},2)", f"=L{n}-J{n}", f"=IF(L{n}>0,M{n}/L{n},0)", c["note"]])
    ws = sheet(wb, "Comparison", cmp_h, rows, {"display_name": 46, "note": 40, "best_supplier": 14},
               ("display_name",), ["public_price"] + [f"cost_{s}" for s in SUPPLIER_ORDER] +
               ["best_cost", "second_cost", "saving"])
    for (c,) in ws.iter_rows(min_row=2, min_col=14, max_col=14):
        c.number_format = "0.0%"
    for (c,) in ws.iter_rows(min_row=2, min_col=10, max_col=10):
        c.fill = BEST

    unc_h = list(unc[0].keys()) if unc else ["empty"]
    sheet(wb, "Uncertain_Matches", unc_h, [[r[h] for h in unc_h] for r in unc],
          {"name_a": 44, "name_b": 44, "reason": 40, "decision(fill: same/different)": 22}, ("name_a", "name_b"))
    ws = wb["Uncertain_Matches"]
    for (c,) in ws.iter_rows(min_row=2, min_col=len(unc_h), max_col=len(unc_h)):
        c.fill = WARN

    off_h = ["offer_id", "item_id", "supplier", "supplier_code", "name_raw", "public_price", "discount_pct",
             "net_cost", "cost_basis", "mostafa_nesf_gomla", "mostafa_sear_g", "match_method", "form", "strengths",
             "pack_count", "price_tag_in_name", "price_date", "parse_notes", "raw_text"]
    rows = []
    for n, o in enumerate(offers, start=2):
        cost = num(o["net_cost"])
        if o["supplier"] in ("wataniya", "ibn_khaldun", "nahda") and o["public_price"] and o["discount_pct"]:
            cost = f"=ROUND(F{n}*(1-G{n}/100),2)"
        rows.append([o["offer_id"], o["item_id"], o["supplier"], o["supplier_code"], o["name_raw"],
                     num(o["public_price"]), num(o["discount_pct"]), cost, o["cost_basis"],
                     num(o["mostafa_nesf_gomla"]), num(o["mostafa_sear_g"]), o["match_method"], o["form"],
                     o["strengths"], o["pack_count"], o["price_tag_in_name"], o["price_date"], o["parse_notes"],
                     o["raw_text"]])
    sheet(wb, "Offers", off_h, rows, {"name_raw": 46, "cost_basis": 30, "raw_text": 50, "offer_id": 24},
          ("name_raw",), ("public_price", "net_cost", "mostafa_nesf_gomla", "mostafa_sear_g"))

    vrows = [[v["scope"], v["check"], v["detail"]] for v in val]
    for k, m in pdf_metrics.items():
        vrows.insert(0, [k, "pdf_extraction", json.dumps(m)])
    sheet(wb, "Validation", ["scope", "check", "detail"], vrows, {"check": 36, "detail": 110})

    wb.calculation.fullCalcOnLoad = True
    wb_path = out / "supplier_catalog_2026-10-31.xlsx"
    wb.save(wb_path)

    # ---- import files for the pharmacy system (money in integer piasters, discounts in basis points)
    imp = out / "import"
    imp.mkdir(exist_ok=True)

    def w(name, header, rows):
        with open(imp / name, "w", newline="", encoding="utf-8") as f:
            cw = csv.writer(f)
            cw.writerow(header)
            cw.writerows(rows)

    w("suppliers.csv", ["supplier_key", "name", "name_ar", "supplier_type", "payment_terms", "notes"],
      [[s, SUPPLIERS[s]["name"], SUPPLIERS[s]["name_ar"], "warehouse", "cash",
        "drug warehouse" if SUPPLIERS[s]["group"] == "drug_wh" else "supplies/cosmetics warehouse"]
       for s in SUPPLIER_ORDER])
    w("items.csv", ["code", "name_ar", "name_norm", "strength", "dosage_form", "is_drug", "category",
                    "category_basis", "pack_size", "public_price_piasters", "dedupe_key", "flags"],
      [[i["item_id"], i["display_name"], i["name_norm"], i["strengths"], i["form"],
        1 if i["category"] == "medicine" else 0, i["category"], i["category_basis"], 1,
        piasters(i["public_price"]),
        "sc:" + hashlib.sha1(f'{i["name_norm"]}|{i["public_price"]}'.encode()).hexdigest()[:16], i["flags"]]
       for i in items])
    w("item_aliases.csv", ["item_code", "supplier_key", "alias_raw", "source"],
      sorted({(o["item_id"], o["supplier"], o["name_raw"], "import") for o in offers}))
    w("supplier_item_codes.csv", ["supplier_key", "supplier_code", "item_code"],
      sorted({(o["supplier"], o["supplier_code"], o["item_id"]) for o in offers if o["supplier_code"]}))
    w("supplier_prices.csv", ["item_code", "supplier_key", "offer_id", "public_price_piasters", "discount_bp",
                              "net_cost_piasters", "price_date", "cost_basis"],
      [[o["item_id"], o["supplier"], o["offer_id"], piasters(o["public_price"]), bp(o["discount_pct"]),
        piasters(o["net_cost"]), o["price_date"], o["cost_basis"]] for o in offers])
    print("workbook:", wb_path)
    for p in sorted(imp.iterdir()):
        print(p.name, sum(1 for _ in open(p, encoding="utf-8")) - 1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
