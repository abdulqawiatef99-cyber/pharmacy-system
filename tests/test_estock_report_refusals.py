"""E-Stock sales and purchases reports (used by the dead-stock report): odd or edited files are refused with
a clear message, never a server error. Synthetic files in the real layouts (coverage 12/11: parse_sales and
parse_purchases had no direct test)."""
import unittest
from datetime import date, datetime

from app.core.errors import ValidationFailed
from app.dataimport import estock
from app.procurement import deadstock
from tests.test_deadstock import SALES, purchases
from tests.test_estock import stock_rows, xlsx


def sales(**footer):
    rows = [r[:] for r in SALES]
    for i, v in footer.items():
        rows[-1][int(i[1:])] = v
    return rows


class TestSales(unittest.TestCase):
    def test_reads_the_real_layout(self):
        table, s = estock.parse_sales(sales())
        self.assertEqual((s["items"], s["cost"], s["sale"]), (1, "32", "40"))
        self.assertEqual(table[1][0], 1)

    def test_item_count_written_as_text_or_float(self):
        for v in ("1", 1.0, " 1 "):
            with self.subTest(v=v):
                self.assertEqual(estock.parse_sales(sales(c6=v))[1]["items"], 1)

    def test_refusals(self):
        for name, rows in {"no footer": SALES[:-1], "count missing": sales(c6=None), "count not a number": sales(c6="x"),
                           "count wrong": sales(c6=2), "count fractional": sales(c6=1.5),
                           "count huge exponent": sales(c6="1e10000000"),
                           "cost edited": sales(c2=33)}.items():
            with self.subTest(name), self.assertRaises(ValidationFailed) as e:
                estock.parse_sales(rows)
            self.assertEqual(e.exception.code, "estock_totals_mismatch")


class TestPurchases(unittest.TestCase):
    def test_reads_invoices_and_flags_stock_counts(self):
        lines, s = estock.parse_purchases(purchases([("ابن سينا", 30906, datetime(2026, 9, 1)),
                                                     ("جرد", 1, datetime(2026, 10, 1))]))
        self.assertEqual([(x["code"], x["adjustment"]) for x in lines], [(30906, False), (1, True)])
        self.assertEqual((s["invoices"], s["lines"], s["from"], s["to"]), (2, 2, "2026-09-01", "2026-10-01"))

    def test_a_date_only_cell_reads_like_a_date_time(self):
        lines, s = estock.parse_purchases(purchases([("ابن سينا", 1, date(2026, 9, 1))]))
        self.assertEqual((lines[0]["date"], s["from"]), (datetime(2026, 9, 1), "2026-09-01"))

    def test_invoices_without_lines(self):
        p = purchases([("ابن سينا", 1, datetime(2026, 9, 1))])
        p[3][20] = 0
        p.pop()                                            # header says 0 lines, and there are none
        lines, s = estock.parse_purchases(p)
        self.assertEqual((lines, s["invoices"], s["from"], s["to"]), ([], 1, None, None))

    def test_refusals(self):
        def edited(f):
            p = purchases([("ابن سينا", 1, datetime(2026, 9, 1))])
            f(p)
            return p
        cases = {"line count text": (edited(lambda p: p[3].__setitem__(20, "x")), "estock_layout"),
                 "date not a date": (edited(lambda p: p[3].__setitem__(13, "أمس")), "estock_layout"),
                 "line before header": (edited(lambda p: p.insert(2, p[4][:])), "estock_layout"),
                 "line missing": (edited(lambda p: p.pop()), "estock_totals_mismatch"),
                 "no invoices": ([["صيدلية"]], "estock_layout")}
        for name, (rows, code) in cases.items():
            with self.subTest(name), self.assertRaises(ValidationFailed) as e:
                estock.parse_purchases(rows)
            self.assertEqual(e.exception.code, code)


class TestDeadStockWithDateCells(unittest.TestCase):
    def test_the_report_works_with_date_only_purchase_dates(self):
        r = deadstock.compute(xlsx(stock_rows()), xlsx(SALES), xlsx(purchases([("ابن سينا", 30906, date(2026, 9, 1))])),
                              as_of=date(2026, 10, 24))
        self.assertIn(30906, [x["code"] for x in r["segments"]["new"]])
