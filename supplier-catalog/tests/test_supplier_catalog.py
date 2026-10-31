"""Tests for the supplier catalog pipeline.

Run from data/supplier-catalog:  python -m pytest -q tests
The integration tests need the build/ outputs (run scripts/run_all.sh first).
"""
import csv
import sys
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from normalize import match_key, parse_name, tidy_display  # noqa: E402
from textfix import fix_visual_arabic  # noqa: E402

BUILD = ROOT / "build"


def test_visual_arabic_is_reversed_with_digits_kept():
    assert fix_visual_arabic("تيز تيد با") == "اب ديت زيت"
    assert fix_visual_arabic("لوسبك١٢ مجم٥٠٠ نيلليسوكيبا") == "ابيكوسيللين ٥٠٠مجم ١٢كبسول"
    assert fix_visual_arabic("صرق٢٠ ٥/١٢٫٥ لفوتبا") == "ابتوفل ٥/١٢٫٥ ٢٠قرص"


def test_spelling_variants_share_a_key():
    a = parse_name("ابروفيل ١٥٠ مجم اقراص")
    b = parse_name("ابروفیل ١٥٠ مجم اقراص")       # Persian yeh from Ibn Khaldun PDF
    assert match_key(a) == match_key(b)
    assert a["strengths"] == ["150mg"] and a["form"] == "tab"


def test_price_tag_is_not_part_of_the_product_key():
    p = parse_name("اباندروكير اقراص سعر ٨٠جنيه")
    assert p["price_tags"] == [80.0]
    assert "80" not in p["numbers"]
    assert parse_name("فيفى شيلد كريم نص بلوك سعر ج٣٢٠جديد")["price_tags"] == [320.0]


def test_different_strengths_never_share_a_key():
    assert match_key(parse_name("ابروفال 150مجم اقراص")) != match_key(parse_name("ابروفال 300مجم اقراص"))


def test_display_name_drops_price_tag_and_fixes_glued_unit():
    assert "سعر" not in tidy_display("اباندروكير اقراص سعر ٨٠جنيه")
    assert tidy_display("ML_اكس لا حريمي الجانس 120").endswith("120 ml")


def need_build():
    if not (BUILD / "offers.csv").exists():
        pytest.skip("build outputs missing; run scripts/run_all.sh")


def rows(name):
    with open(BUILD / name, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def test_every_source_row_is_preserved():
    need_build()
    cnt = Counter(o["supplier"] for o in rows("offers.csv"))
    # Excel row counts verified against the originals; PDF counts against an independent
    # count of numbers in the discount/public-price column zone.
    assert cnt == {"wataniya": 4248, "mostafa": 1732, "nahda": 7808, "ibn_khaldun": 3879, "future": 2793}


def test_excel_rows_match_originals():
    need_build()
    for sid, fname in (("wataniya", "wataniya_مخزن.xlsx"), ("mostafa", "mostafa_اوردر_اسبوعى_مستلزمات.xlsx")):
        ws = openpyxl.load_workbook(ROOT / "originals" / fname, read_only=True, data_only=True).active
        data = [r for r in list(ws.iter_rows(values_only=True))[1:] if any(v not in (None, "") for v in r)]
        assert sum(o["supplier"] == sid for o in rows("offers.csv")) == len(data)


def test_every_offer_belongs_to_one_item_and_costs_are_positive():
    need_build()
    items = {i["item_id"] for i in rows("catalog_items.csv")}
    for o in rows("offers.csv"):
        assert o["item_id"] in items
        assert Decimal(o["net_cost"]) > 0


def test_drug_warehouse_cost_rule():
    need_build()
    for o in rows("offers.csv"):
        if o["supplier"] in ("wataniya", "nahda", "ibn_khaldun"):
            exp = (Decimal(o["public_price"]) * (100 - Decimal(o["discount_pct"])) / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)
            assert abs(Decimal(o["net_cost"]) - exp) <= Decimal("0.005"), o["offer_id"]


def test_comparison_best_is_minimum_and_like_for_like():
    need_build()
    for c in rows("comparison.csv"):
        costs = [Decimal(c[k]) for k in c if k.startswith("cost_") and c[k]]
        assert len(costs) >= 2
        assert Decimal(c["best_cost"]) == min(costs)
        assert Decimal(c["saving"]) >= 0


def test_matched_drug_offers_share_public_price_unless_flagged():
    need_build()
    items = {i["item_id"]: i for i in rows("catalog_items.csv")}
    by = {}
    for o in rows("offers.csv"):
        if o["public_price"]:
            by.setdefault(o["item_id"], set()).add(Decimal(o["public_price"]))
    for iid, prices in by.items():
        if len(prices) > 1:
            assert "multiple_public_prices" in items[iid]["flags"]
