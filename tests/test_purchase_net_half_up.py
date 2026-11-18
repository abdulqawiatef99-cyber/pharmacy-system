"""Derived purchase prices are integer arithmetic rounded half up, like every other money value
(AGENTS.md). float round() is half to even: 10.70 at 25% (802.5 piastres) became 802, and the line
total was built from that already rounded price (R4 review). Synthetic data only."""
import ast
import unittest
from datetime import date
from pathlib import Path

from app.catalog import service as cat
from app.purchasing import validation
from app.purchasing.service import Purchasing
from tests.helpers import DBTestCase


class TestDeriveLine(unittest.TestCase):
    def test_inferred_net_is_half_up(self):
        l = validation.derive_line({"public_price": 1070, "discount_bp": 2500, "qty": 1})
        self.assertEqual(l["unit_net_price"], 803)                      # 802.5 -> 803, not 802
        self.assertEqual(l["line_total"], 803)

    def test_line_total_of_an_inferred_net_is_rounded_once(self):
        l = validation.derive_line({"public_price": 1070, "discount_bp": 2500, "qty": 10})
        self.assertEqual(l["unit_net_price"], 803)
        self.assertEqual(l["line_total"], 8025)                         # 10 x 10.70 x 75%, not 10 x 8.03
        self.assertEqual(l["field_sources"]["line_total"], "inferred")
        l = validation.derive_line({"public_price": 1070, "discount_bp": 2500, "qty": 10, "tax_amount": 100})
        self.assertEqual(l["line_total"], 8125)
        l = validation.derive_line({"public_price": 333, "discount_bp": 3333, "qty": 3})
        self.assertEqual((l["unit_net_price"], l["line_total"]), (222, 666))   # 222.0111 ; 666.0333

    def test_a_given_net_keeps_net_times_qty(self):
        l = validation.derive_line({"public_price": 1070, "discount_bp": 2500, "unit_net_price": 802, "qty": 10})
        self.assertEqual(l["line_total"], 8020)
        l = validation.derive_line({"unit_net_price": 802, "qty": 10, "tax_amount": 5})
        self.assertEqual(l["line_total"], 8025)

    def test_a_printed_line_total_is_never_replaced(self):
        l = validation.derive_line({"public_price": 1070, "discount_bp": 2500, "qty": 10, "line_total": 8020})
        self.assertEqual((l["unit_net_price"], l["line_total"]), (803, 8020))

    def test_inferred_discount_is_half_up(self):
        l = validation.derive_line({"public_price": 80000, "unit_net_price": 79996, "qty": 1})
        self.assertEqual(l["discount_bp"], 1)                           # 0.5 bp -> 1, not 0
        l = validation.derive_line({"public_price": 4000, "unit_net_price": 3200, "qty": 1})
        self.assertEqual(l["discount_bp"], 2000)

    def test_expected_net_in_validate_is_half_up(self):
        line = {"id": "L1", "item_id": "I", "qty": 1, "public_price": 1070, "discount_bp": 2500,
                "unit_net_price": 900, "line_total": 900, "expiry_date": "2099-01-31",
                "field_sources": {"unit_net_price": "user"}}
        issues = validation.validate({"supplier_id": "S", "invoice_no": "1", "invoice_date": "2026-01-01"},
                                     [line], items={}, today=date(2026, 1, 1), short_expiry_days=90,
                                     total_tolerance=100)
        got = [i for i in issues if i.code == "net_price_mismatch"]
        self.assertEqual([i.details["expected"] for i in got], [803])

    def test_no_float_rounding_of_money_in_derive_line(self):
        """round() on a division is float, half to even."""
        src = (Path(__file__).resolve().parent.parent / "app/purchasing/validation.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == "derive_line")
        calls = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "round"]
        self.assertEqual(calls, [])


class TestThroughTheInvoice(DBTestCase):
    def test_typed_public_price_and_discount(self):
        p = Purchasing(self.db)
        item = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab", "public_price": 1070})
        s = cat.create_supplier(self.db, self.ctx, name="مورد")
        iid = p.create_manual(self.ctx, supplier_id=s)
        v = p.get(self.ctx, iid)["invoice"]["version"]
        d = p.add_line(self.ctx, iid, {"item_id": item, "qty": 10, "public_price": 1070, "discount_bp": 2500,
                                       "batch_no": "X1", "expiry_date": "12/2029"}, v)
        line = d["lines"][0]
        self.assertEqual((line["unit_net_price"], line["line_total"]), (803, 8025))
        self.assertEqual([i["code"] for i in d["issues"] if i["level"] == "error"], [])
        d = p.update_line(self.ctx, iid, line["id"], {"qty": 20}, d["invoice"]["version"])
        self.assertEqual(d["lines"][0]["line_total"], 16050)
        d = p.approve(self.ctx, iid, d["invoice"]["version"])
        self.assertEqual(d["invoice"]["computed_total"], 16050)
