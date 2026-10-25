"""More migration tests (SYNTHETIC files — real E-Stock exports still untested)."""
import io
import time
from decimal import Decimal

from openpyxl import Workbook, load_workbook

from app.catalog import service as cat
from app.core.errors import Conflict, ValidationFailed
from app.dataimport.service import DataImport, template_xlsx
from app.inventory import service as inv
from app.sales.service import Sales
from tests.helpers import DBTestCase


def xlsx(rows):
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(list(r))
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


class TestTemplatesAndColumns(DBTestCase):
    def setUp(self):
        super().setUp()
        self.di = DataImport(self.db)

    def test_every_template_round_trips(self):
        samples = {"items": [101, "Panadol 500mg 24 tab", "بانادول", "6221025012345", 2, 40],
                   "opening_stock": [101, None, None, 5, 32, "P1", "05/2028"],
                   "suppliers": ["S1", "ابن سينا", "0100000000", "موزع", 20, 12, 1500],
                   "customers": ["C1", "أ. محمد", "01011111111", 500, 120, ""]}
        for kind in ("items", "opening_stock", "suppliers", "customers"):
            wb = load_workbook(io.BytesIO(template_xlsx(kind)))
            self.assertIn("تعليمات", wb.sheetnames)
            ws = wb["data"]
            ws.append(samples[kind])
            b = io.BytesIO()
            wb.save(b)
            run = self.di.analyze(self.ctx, b.getvalue(), f"{kind}.xlsx", kind=kind)
            rep = self.di.get(self.ctx, run)["report"]
            self.assertEqual(rep["columns"]["missing_required"], [], kind)
            self.assertEqual(rep["counts"].get("create"), 1, (kind, rep))
            self.assertTrue(self.di.apply(self.ctx, run)["reconciliation"]["ok"], kind)

    def test_missing_required_columns_reported(self):
        with self.assertRaises(ValidationFailed) as e:
            self.di.analyze(self.ctx, xlsx([["كود", "الرصيد"], [1, 5]]), "s.xlsx", kind="items")
        self.assertIn(e.exception.code, ("missing_columns", "mapping_required"))
        cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab", "code": "1"})
        with self.assertRaises(ValidationFailed) as e:      # stock file without quantity
            self.di.analyze(self.ctx, xlsx([["كود", "اسم الصنف", "التكلفة"], [1, "Panadol", 5]]),
                            "s.xlsx", kind="opening_stock")
        self.assertEqual(e.exception.code, "missing_columns")
        self.assertIn("الرصيد", e.exception.details["columns"]["missing_required"][0])

    def test_unmapped_headers_listed(self):
        run = self.di.analyze(self.ctx, xlsx([["اسم الصنف", "عمود غريب", "سعر الجمهور"],
                                              ["Panadol 500mg 24 tab", "x", 40]]), "a.xlsx")
        self.assertEqual(self.di.get(self.ctx, run)["report"]["columns"]["unmapped_headers"], ["عمود غريب"])

    def test_profile_learned_from_manual_mapping(self):
        odd = [["Item Desc.", "Sell Pr", "Pk"], ["Panadol 500mg 24 tab", 40, 2]]
        with self.assertRaises(ValidationFailed):
            self.di.analyze(self.ctx, xlsx(odd), "estock.xlsx", source_system="estock")
        self.di.analyze(self.ctx, xlsx(odd), "estock.xlsx", source_system="estock",
                        mapping={0: "name_en", 1: "public_price", 2: "pack_size"},
                        options={"header_row": 0})
        nxt = [["Item Desc.", "Sell Pr", "Pk"], ["Cataflam 50mg 20 tab", 60, 2]]
        run = self.di.analyze(self.ctx, xlsx(nxt), "estock2.xlsx", source_system="estock")
        r = self.di.get(self.ctx, run)
        self.assertTrue(r["report"]["profile_used"])
        self.assertEqual(r["rows"][0]["data"]["public_price"], "60")


class TestParties(DBTestCase):
    def setUp(self):
        super().setUp()
        self.di = DataImport(self.db)

    def test_suppliers_with_balances(self):
        data = xlsx([["اسم المورد", "التليفون", "خصم محلي", "خصم مستورد", "الرصيد"],
                     ["ابن سينا فارما", "0100 000 0000", "20%", 0.12, "15,000.50"],
                     ["فارما أوفرسيز", None, 18, 12, None],
                     [None, "0123", 1, 1, 1]])
        run = self.di.analyze(self.ctx, data, "sup.xlsx", kind="suppliers")
        r = self.di.get(self.ctx, run)
        self.assertEqual(r["report"]["counts"], {"create": 2, "error": 1})
        rep = self.di.apply(self.ctx, run)
        self.assertTrue(rep["reconciliation"]["ok"])
        self.assertEqual(rep["reconciliation"]["ledger_balance_total"], 1500050)
        s = {x["name"]: x for x in cat.list_suppliers(self.db, self.ctx)}
        self.assertEqual((s["ابن سينا فارما"]["base_discount_local_bp"],
                          s["ابن سينا فارما"]["base_discount_imported_bp"]), (2000, 1200))
        # a second file with a balance for the same supplier is NOT silently posted again
        run2 = self.di.analyze(self.ctx, xlsx([["اسم المورد", "الرصيد"], ["ابن سينا فارما", 15000.5]]),
                               "sup2.xlsx", kind="suppliers")
        self.assertEqual(self.di.get(self.ctx, run2)["rows"][0]["action"], "review")

    def test_customers_balances_and_dedupe(self):
        data = xlsx([["اسم العميل", "هاتف", "حد الائتمان", "الرصيد"],
                     ["أحمد علي", "01012345678", 1000, 250],
                     ["أحمد علي", "01012345678", 1000, 250],          # duplicate row in file
                     ["منى", None, 0, 0]])
        run = self.di.analyze(self.ctx, data, "c.xlsx", kind="customers")
        rows = self.di.get(self.ctx, run)["rows"]
        self.assertEqual([x["action"] for x in rows], ["create", "review", "create"])
        self.di.decide(self.ctx, run, rows[1]["id"], "skip")
        rep = self.di.apply(self.ctx, run)
        self.assertTrue(rep["reconciliation"]["ok"])
        cid = self.db.one("SELECT id FROM customers WHERE phone='01012345678'")[0]
        self.assertEqual(Sales(self.db).balance(self.ctx, cid), 25000)

    def test_failure_rolls_back_balances(self):
        cat.create_supplier(self.db, self.ctx, name="مخزن النور")
        data = xlsx([["اسم المورد", "الرصيد"], ["مورد جديد", 100], ["مخزن النور", 50]])
        run = self.di.analyze(self.ctx, data, "s.xlsx", kind="suppliers")
        rows = self.di.get(self.ctx, run)["rows"]
        with self.db.transaction():   # simulate a plan that would collide on create
            self.db.execute("UPDATE import_rows SET action='create', target_id=NULL WHERE id=?", (rows[1]["id"],))
        with self.assertRaises(Conflict):
            self.di.apply(self.ctx, run)
        self.assertEqual(self.db.one("SELECT count(*) FROM supplier_ledger")[0], 0)
        self.assertEqual(self.db.one("SELECT count(*) FROM suppliers")[0], 1)


class TestItemEdgeCases(DBTestCase):
    def setUp(self):
        super().setUp()
        self.di = DataImport(self.db)

    def test_conflicts_detected(self):
        data = xlsx([["كود", "اسم الصنف", "باركود", "سعر الجمهور", "التكلفة"],
                     [1, "Panadol 500mg 24 tab", "6221025012345", 40, 50],      # price below cost
                     [2, "Cataflam 50mg 20 tab", "6221025012345", 60, 40],      # same barcode, other code
                     [1, "Other name 5mg tab", None, 10, 5],                    # same code
                     [3, "  ", None, None, None]])
        run = self.di.analyze(self.ctx, data, "x.xlsx")
        rows = {x["row_no"]: x for x in self.di.get(self.ctx, run)["rows"]}
        codes = {n: {i["code"] for i in x["issues"]} for n, x in rows.items()}
        self.assertIn("price_below_cost", codes[2])
        self.assertIn("duplicate_barcode_in_file", codes[3])
        self.assertIn("duplicate_code_in_file", codes[4])
        self.assertEqual(rows[5]["action"], "error")

    def test_large_file_performance(self):
        rows = [["كود", "اسم الصنف", "سعر الجمهور", "عدد الوحدات"]]
        rows += [[i, f"Item{i:05d} {i % 50 + 1}0mg {i % 7 + 10} tab", 10 + i % 90, 1 + i % 3]
                 for i in range(3000)]
        t = time.time()
        run = self.di.analyze(self.ctx, xlsx(rows), "big.xlsx")
        t_an = time.time() - t
        t = time.time()
        rep = self.di.apply(self.ctx, run)
        t_ap = time.time() - t
        self.assertEqual(rep["applied"]["created"], 3000)
        self.assertTrue(rep["reconciliation"]["ok"])
        print(f"\n[perf] 3000 items: analyze {t_an:.1f}s, apply {t_ap:.1f}s")
        self.assertLess(t_an + t_ap, 120)
