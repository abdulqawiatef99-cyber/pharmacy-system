"""E-Stock report adapters. Layouts copied from real exports (2026-10-24); the
values are synthetic. No real pharmacy data is stored in the repository."""
import io
import unittest
from datetime import datetime

import openpyxl

from app.catalog.normalize import dedupe_key
from app.core.errors import ValidationFailed
from app.dataimport import estock
from app.dataimport.service import DataImport
from tests.helpers import DBTestCase


def xlsx(rows):
    wb = openpyxl.Workbook()
    for r in rows:
        wb.active.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


CATALOG = [["سعر الجمهور", "الشركة المنتجة", "الاسم الانجليزى", "الاسم العربى", "الكود"],
           [40, "الكان فارم", "ONE TWO THREE 20TAB", "وان تو ثري 20 قرص", 1],
           [27, "غير معرفة", "ACNE FREE CREAM 30gm", "اكني فري كريم", 76],
           [5.9, "غير معرفة", "ACNE FREE 30g  GEL", "اكني فري جل", 77],
           [102.9, "غير معرفة", "LITHIOMOD 400MG 30TAB", "ليثيومود", 30906],
           [4, "إجمالى عدد الأصناف المطبوعة", None, None, None],
           [datetime(2026, 10, 24), "توقيت الطباعة", 1, "رقم الصفحة", None]]


def stock_rows(store2_cost=20.0):
    return [
        ["صــيــدلــيــة", None, None, None, None, None, None, None, None, None, None, None, None, None, None],
        ["الصيدلية", "اسم المخزن", 1, "كود المخزن", "نسبة الربح", "إجمالى البيع", "إجمالى التكلفة",
         "البيع", "التكلفة", "الكمية", "تاريخ الصلاحية", "الوحدة", "الشركة", "اسم الصنف", "الكود"],
        [30, 2303.049, 1612.1343, 147, 102.9, 15.667, datetime(2027, 1, 1), "علبة", "غير معرفة",
         "LITHIOMOD 400MG 30TAB", 30906, None, None, None, None],
        [10, 80, 64, 40, 32, 2, None, "علبة", "الكان فارم", "ONE TWO THREE 20TAB", 1,
         None, None, None, None],
        [2383.049, "إجمالى البيع", 1676.1343, "إجمالى التكلفة", 2, "عدد الاصناف",
         None, None, None, None, None, None, None, None, None],
        ["مستشفيات", "اسم المخزن", 2, "كود المخزن", None, None, None, None, None, None, None, None,
         None, None, None],
        [10, 40, store2_cost, 40, 20, 1, datetime(2028, 3, 1), "علبة", "الكان فارم",
         "ONE TWO THREE 20TAB", 1, None, None, None, None],
        [40, "إجمالى البيع", 20, "إجمالى التكلفة", 1, "عدد الاصناف",
         None, None, None, None, None, None, None, None, None],
        ["إجمالى جميع المخازن", 2423.049, "إجمالى البيع", 1696.1343, "إجمالى التكلفة", 3,
         "عدد الاصناف", None, None, None, None, None, None, None, None],
    ]


class TestAdapters(unittest.TestCase):
    def test_detect_and_catalog(self):
        self.assertEqual(estock.detect(CATALOG), "catalog")
        out = estock.parse_catalog(CATALOG)
        self.assertEqual(out[0], estock.CATALOG_HEADER)
        self.assertEqual(out[1], [1, "ONE TWO THREE 20TAB", "وان تو ثري 20 قرص", "الكان فارم", 40])
        self.assertEqual(len(out), 5)                     # footer lines dropped

    def test_catalog_truncated_refused(self):
        with self.assertRaises(ValidationFailed):
            estock.parse_catalog(CATALOG[:3] + CATALOG[5:])   # footer says 4, has 2

    def test_stock_header_shift_and_totals(self):
        rows = stock_rows()
        self.assertEqual(estock.detect(rows), "stock_by_expiry")
        table, summary = estock.parse_stock(rows)
        # column shift handled: cost is the cost, not the company name
        self.assertEqual(table[1], ["الصيدلية", 30906, "LITHIOMOD 400MG 30TAB", "غير معرفة",
                                    15.667, 102.9, "01/2027"])
        self.assertEqual(summary["stores"]["الصيدلية"]["items"], 2)
        only = estock.parse_stock(rows, {"الصيدلية"})[0]
        self.assertEqual(len(only), 3)

    def test_stock_edited_file_refused(self):
        with self.assertRaises(ValidationFailed):
            estock.parse_stock(stock_rows(store2_cost=25.0))

    def test_pack_size_is_only_suggested(self):
        table = estock.parse_stock(stock_rows())[0]
        sug = estock.suggest_pack_sizes(table)
        self.assertEqual(sug[30906]["suggested"], 3)      # 15.667 = 15 + 2/3
        self.assertNotIn(1, sug)


class TestNames(unittest.TestCase):
    """False 'same item' merges found on the real 30k-item E-Stock catalog."""

    def test_distinct_items_keep_distinct_keys(self):
        pairs = [("ACNE FREE CREAM 30gm", "ACNE FREE 30g GEL"),
                 ("ALMITAT 15G OINTMENT", "Almitat 15gm cream"),
                 ("ALPRAX 1mg 10/TAB", "ALPRAX 1mg S.R. 10/TAB"),
                 ("ALVEOLIN 100ML SYRUP", "ALVEOLIN-P 100ML SYRUP"),
                 ("ANTI- A-TEST SERUM AMP", "ANTI- D TEST SERUM AMP")]
        for a, b in pairs:
            self.assertNotEqual(dedupe_key(a, None, None, 1), dedupe_key(b, None, None, 1), (a, b))

    def test_same_item_written_differently_still_matches(self):
        self.assertEqual(dedupe_key("AMPICILLIN  500 MG  12 CAP", None, None, 1),
                         dedupe_key("AMPICILLIN 500 mg 12 CAP", None, None, 1))


class TestEstockImport(DBTestCase):
    def test_catalog_then_stock_end_to_end(self):
        di = DataImport(self.db)
        run = di.analyze(self.ctx, xlsx(CATALOG), "c.xlsx", kind="items", source_system="estock")
        self.assertEqual(di.get(self.ctx, run)["report"]["counts"], {"create": 4})
        di.apply(self.ctx, run)
        run = di.analyze(self.ctx, xlsx(stock_rows()), "s.xlsx", kind="opening_stock",
                         source_system="estock", options={"estock_stores": ["الصيدلية"]})
        rows = di.get(self.ctx, run)["rows"]
        lith = [r for r in rows if r["data"]["code"] == "30906"][0]
        self.assertEqual(lith["action"], "error")         # fractional until pack size confirmed
        with self.assertRaises(ValidationFailed):
            di.apply(self.ctx, run)
        # after a person confirms 3 strips per pack, a fresh analysis posts whole units
        item = self.db.one("SELECT id FROM items WHERE code='30906'")[0]
        from app.catalog import service as cat
        cat.update_item(self.db, self.ctx, item, {"pack_size": 3})
        di.discard(self.ctx, run)
        run = di.analyze(self.ctx, xlsx(stock_rows()), "s2.xlsx", kind="opening_stock",
                         source_system="estock", options={"estock_stores": ["الصيدلية"]})
        rep = di.apply(self.ctx, run)
        self.assertTrue(rep["reconciliation"]["ok"])
        self.assertEqual(rep["applied"]["posted_base_units"], 47 + 2)
        b = self.db.one("SELECT expiry_date FROM batches WHERE item_id=?", (item,))[0]
        self.assertEqual(b, "2027-01-31")                 # month precision, end of month


if __name__ == "__main__":
    unittest.main()


class TestMakers(DBTestCase):
    def test_same_generic_two_makers_are_two_items(self):
        rows = [["الكود", "الاسم الانجليزي", "الشركة"],
                [1, "AMPICILLIN 500 MG 12 CAP", "ايبيكو"],
                [2, "AMPICILLIN 500mg 12 cap", "مصر"],          # other maker -> separate item
                [3, "Ampicillin 500 mg 12 caps", None],          # maker unknown -> human decides
                [4, "BETAVAL 15g OINT", "ادكو"],
                [5, "BETAVAL C 15g OINT", "ادكو"]]               # 'C' is part of the name
        di = DataImport(self.db)
        run = di.analyze(self.ctx, xlsx(rows), "m.xlsx", kind="items")
        acts = {r["data"]["code"]: r["action"] for r in di.get(self.ctx, run)["rows"]}
        self.assertEqual(acts, {"1": "create", "2": "create", "3": "review", "4": "create", "5": "create"})
        # the human keeps it as its own item: stored separately, tagged as a possible duplicate
        row3 = [r for r in di.get(self.ctx, run)["rows"] if r["data"]["code"] == "3"][0]
        di.decide(self.ctx, run, row3["id"], "create")
        di.apply(self.ctx, run)
        self.assertEqual(self.db.one("SELECT count(*) FROM items")[0], 5)
        self.assertIn("|dup:3", self.db.one("SELECT dedupe_key FROM items WHERE code='3'")[0])


class TestBatches(DBTestCase):
    def test_two_batches_of_one_item_are_not_duplicates(self):
        di = DataImport(self.db)
        run = di.analyze(self.ctx, xlsx([["الكود", "الاسم الانجليزي"], [1, "Panadol 500mg 24 tab"]]),
                         "i.xlsx", kind="items")
        di.apply(self.ctx, run)
        stock = [["الكود", "الكمية", "التكلفة", "الصلاحية"], [1, 2, 30, "05/2027"], [1, 3, 32, "09/2028"],
                 [1, 3, 32, "09/2028"]]                    # last line repeats the same batch
        run = di.analyze(self.ctx, xlsx(stock), "s.xlsx", kind="opening_stock")
        acts = [r["action"] for r in di.get(self.ctx, run)["rows"]]
        self.assertEqual(acts, ["create", "create", "review"])
