"""Data migration tests. The sheets below are SYNTHETIC; the real E-Stock export
layout is unknown until the owner provides a sample (docs/PROGRESS.md)."""
import io
from decimal import Decimal

from openpyxl import Workbook, load_workbook

from app.catalog import service as cat
from app.core import accounts
from app.core.errors import Conflict, Forbidden, ValidationFailed
from app.dataimport.service import DataImport, export_catalog
from app.inventory import service as inv
from tests.helpers import DBTestCase


def sheet(rows, title_rows=1):
    wb = Workbook()
    ws = wb.active
    for _ in range(title_rows):
        ws.append(["تقرير الأصناف - برنامج الصيدلية"])
    for r in rows:
        ws.append(list(r))
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


ITEMS = [
    ["كود الصنف", "اسم الصنف", "الاسم العربي", "باركود", "سعر الجمهور", "عدد الوحدات", "الشركة"],
    [101, "Panadol 500mg 24 tab", "بانادول 500 مجم", 6221025012345, 40, 2, "GSK"],
    [102, "Cataflam 50mg 20 tab", None, None, "60.00", 2, "Novartis"],
    [103, "Augmentin 1gm 14 tab", "اوجمنتين", "6221025099998", 145, 2, "GSK"],
    [104, None, None, None, 10, 1, None],                               # no name -> error
    [105, "AUGMENTIN 1 GM 14 TAB", None, None, 145, 2, None],          # same item as 103 -> review
    [106, "Brufen 400mg 30 tab", None, "12ab", 35, 3, None],           # bad barcode
]


class TestItemsImport(DBTestCase):
    def setUp(self):
        super().setUp()
        self.di = DataImport(self.db)

    def test_dry_run_writes_nothing_and_reports(self):
        run = self.di.analyze(self.ctx, sheet(ITEMS), "estock_items.xlsx", source_system="estock")
        self.assertEqual(self.db.one("SELECT count(*) FROM items")[0], 0)   # dry run
        r = self.di.get(self.ctx, run)
        self.assertEqual(r["report"]["counts"], {"create": 3, "error": 1, "review": 2})
        by_no = {x["data"]["code"]: x for x in r["rows"]}
        self.assertEqual(by_no["104"]["action"], "error")
        self.assertEqual(by_no["105"]["action"], "review")
        self.assertIn("duplicate_item_in_file", [i["code"] for i in by_no["105"]["issues"]])
        self.assertIn("invalid_barcode", [i["code"] for i in by_no["106"]["issues"]])
        self.assertEqual(by_no["101"]["data"]["barcode"], "6221025012345")   # float-safe

    def test_review_required_then_apply_is_atomic_and_once(self):
        data = sheet(ITEMS)          # one file: openpyxl stamps the second it was written
        run = self.di.analyze(self.ctx, data, "estock_items.xlsx")
        with self.assertRaises(Conflict) as e:
            self.di.apply(self.ctx, run)
        self.assertEqual(e.exception.code, "pending_review")
        rows = {x["data"]["code"]: x for x in self.di.get(self.ctx, run)["rows"]}
        self.di.decide(self.ctx, run, rows["105"]["id"], "skip")
        self.di.decide(self.ctx, run, rows["106"]["id"], "create")   # accept without barcode
        with self.assertRaises(ValidationFailed):
            self.di.decide(self.ctx, run, rows["104"]["id"], "create")  # error rows can't
        rep = self.di.apply(self.ctx, run)
        self.assertEqual((rep["applied"]["created"], rep["applied"]["skipped"],
                          rep["applied"]["errors"]), (4, 1, 1))
        self.assertEqual(self.db.one("SELECT count(*) FROM items")[0], 4)
        pan = cat.search_items(self.db, self.ctx, "6221025012345")[0]
        self.assertEqual((pan["code"], pan["public_price"], pan["pack_size"]), ("101", 4000, 2))
        with self.assertRaises(Conflict):
            self.di.apply(self.ctx, run)
        with self.assertRaises(Conflict):        # same file again
            self.di.analyze(self.ctx, data, "again.xlsx")

    def test_reimport_updates_by_code_never_duplicates(self):
        run = self.di.analyze(self.ctx, sheet(ITEMS[:3]), "a.xlsx")
        self.di.apply(self.ctx, run)
        updated = [ITEMS[0], [101, "Panadol 500mg 24 tab", None, None, 42, 2, None]]
        run2 = self.di.analyze(self.ctx, sheet(updated), "b.xlsx")
        r = self.di.get(self.ctx, run2)
        self.assertEqual(r["rows"][0]["action"], "update")
        self.di.apply(self.ctx, run2)
        self.assertEqual(self.db.one("SELECT count(*) FROM items")[0], 2)
        it = cat.search_items(self.db, self.ctx, "6221025012345")[0]
        self.assertEqual(it["public_price"], 4200)
        self.assertEqual(it["name_ar"], "بانادول 500 مجم")       # blank never overwrites

    def test_similar_name_goes_to_review_not_auto_link(self):
        cat.create_item(self.db, self.ctx, {"name_en": "Cataflam 50mg 20 tab", "pack_size": 2})
        run = self.di.analyze(self.ctx, sheet([ITEMS[0], [999, "CATAFLAM 50 MG 20 TAB", None,
                                                          None, 60, 2, None]]), "c.xlsx")
        row = self.di.get(self.ctx, run)["rows"][0]
        self.assertEqual(row["action"], "review")

    def test_failure_mid_apply_rolls_back_everything(self):
        other = cat.create_item(self.db, self.ctx, {"name_en": "Zyrtec 10mg 20 tab"})
        cat.add_barcode(self.db, self.ctx, other, "6221000000017")
        rows = [ITEMS[0], [201, "Allergex 4mg 20 tab", None, None, 15, 1, None],
                [202, "Telfast 180mg 20 tab", None, "6221000000017", 90, 2, None]]
        run = self.di.analyze(self.ctx, sheet(rows), "d.xlsx")
        r = self.di.get(self.ctx, run)
        # row 202's barcode belongs to Zyrtec -> 'update' Zyrtec? no: code 202 unknown, barcode
        # known -> plan says update Zyrtec; force a create decision path to hit the conflict
        self.assertEqual(r["rows"][1]["action"], "update")
        before = self.db.one("SELECT count(*) FROM items")[0]
        with self.db.transaction():
            self.db.execute("UPDATE import_rows SET action='create', target_id=NULL WHERE id=?",
                            (r["rows"][1]["id"],))
        with self.assertRaises(Conflict):
            self.di.apply(self.ctx, run)
        self.assertEqual(self.db.one("SELECT count(*) FROM items")[0], before)  # nothing kept
        self.assertEqual(self.di.get(self.ctx, run)["status"], "analyzed")

    def test_mapping_required_message_with_preview(self):
        data = sheet([["a", "b"], [1, 2]])
        with self.assertRaises(ValidationFailed) as e:
            self.di.analyze(self.ctx, data, "x.xlsx")
        self.assertEqual(e.exception.code, "mapping_required")
        run = self.di.analyze(self.ctx, data, "x.xlsx", mapping={0: "name_en", 1: "public_price"},
                              options={"header_row": 1})
        self.assertEqual(self.di.get(self.ctx, run)["report"]["counts"], {"create": 1})

    def test_permissions(self):
        accounts.add_user(self.db, self.ctx, username="asst", full_name="a",
                          password="Assist-Pass-2026", role_code="assistant")
        actx = self.login("asst", "Assist-Pass-2026")
        with self.assertRaises(Forbidden):
            self.di.analyze(actx, sheet(ITEMS), "x.xlsx")


class TestOpeningStock(DBTestCase):
    def setUp(self):
        super().setUp()
        self.di = DataImport(self.db)
        run = self.di.analyze(self.ctx, sheet(ITEMS[:4]), "items.xlsx")
        self.di.apply(self.ctx, run)

    def stock_sheet(self, rows):
        return sheet([["كود", "الصنف", "الرصيد", "التكلفة", "التشغيلة", "الصلاحية"]] + rows)

    def test_opening_balances_reconciled(self):
        data = self.stock_sheet([
            [101, "Panadol", 5, 32, "P1", "05/2028"],
            [102, "Cataflam", 2.5, 49.2, None, None],       # 2.5 packs x 2 strips = 5 strips
            [103, "Augmentin", 0, 120, None, None],          # zero -> skip
        ])
        run = self.di.analyze(self.ctx, data, "stock.xlsx", kind="opening_stock")
        r = self.di.get(self.ctx, run)
        self.assertEqual(r["report"]["source_qty_total"], "7.5")
        self.assertIn("no_batch_or_expiry", [i["code"] for i in r["rows"][1]["issues"]])
        rep = self.di.apply(self.ctx, run)
        self.assertTrue(rep["reconciliation"]["ok"])
        self.assertEqual(rep["applied"]["posted_base_units"], 15)
        pan = cat.search_items(self.db, self.ctx, "6221025012345")[0]
        s = inv.stock_for_item(self.db, self.ctx, pan["id"])
        self.assertEqual((s["total_qty"], Decimal(s["avg_cost"])), (10, Decimal("16")))
        self.assertEqual(s["batches"][0]["expiry_date"], "2028-05-31")
        # missing expiry is NOT invented
        caf = self.db.one("SELECT id FROM items WHERE code='102'")[0]
        cs = inv.stock_for_item(self.db, self.ctx, caf)
        self.assertIsNone(cs["batches"][0]["expiry_date"])

    def test_fractional_units_rejected_atomically(self):
        data = self.stock_sheet([[101, "Panadol", 5, 32, None, None],
                                 [102, "Cataflam", 1.3, 49.2, None, None]])  # 2.6 strips
        run = self.di.analyze(self.ctx, data, "s.xlsx", kind="opening_stock")
        row = [r for r in self.di.get(self.ctx, run)["rows"] if r["data"].get("code") == "102"][0]
        self.assertEqual(row["action"], "error")       # flagged before apply, not at apply time
        self.assertEqual(row["issues"][0]["code"], "fractional_qty_needs_pack_size")
        with self.assertRaises(ValidationFailed):          # never a silent partial import
            self.di.apply(self.ctx, run)
        self.assertEqual(self.db.one("SELECT count(*) FROM stock_movements")[0], 0)

    def test_no_opening_balance_over_existing_history(self):
        pan = self.db.one("SELECT id FROM items WHERE code='101'")[0]
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=pan, qty_delta=4,
                              movement_type="purchase", unit_cost=Decimal("15"))
        run = self.di.analyze(self.ctx, self.stock_sheet([[101, "Panadol", 5, 32, None, None]]),
                              "s.xlsx", kind="opening_stock")
        row = self.di.get(self.ctx, run)["rows"][0]
        self.assertEqual(row["action"], "review")
        with self.assertRaises(ValidationFailed):
            self.di.decide(self.ctx, run, row["id"], "create")

    def test_unknown_item_is_error(self):
        run = self.di.analyze(self.ctx, self.stock_sheet([[999, "Unknown thing", 3, 1, None, None]]),
                              "s.xlsx", kind="opening_stock")
        self.assertEqual(self.di.get(self.ctx, run)["rows"][0]["action"], "error")

    def test_export_round_trip(self):
        data = export_catalog(self.db, self.ctx)
        ws = load_workbook(io.BytesIO(data)).active
        rows = list(ws.iter_rows(values_only=True))
        self.assertEqual(rows[0][1], "code")
        self.assertEqual(len(rows) - 1, 3)


class TestYesNoColumns(DBTestCase):
    """«محلي/مستورد» and «دواء/غير دواء» as old programs write them (coverage 12/11: never run by a test).
    Before: "مستوردة", "محلية" and a number written as 1.0 / 0.0 came out unknown."""

    def test_values(self):
        from app.dataimport.service import _bool
        for v in ("نعم", "مستورد", "مستوردة", "مستورده", "Imported", "TRUE", True, 1, "1", "1.0", 1.0, "دواء", " yes "):
            with self.subTest(v=v):
                self.assertEqual(_bool(v), 1)
        for v in ("لا", "محلي", "محلى", "محلية", "محليه", "Local", "false", False, 0, "0", "0.0", 0.0, "غير دواء"):
            with self.subTest(v=v):
                self.assertEqual(_bool(v), 0)
        for v in ("x", "2", "ربما", None, ""):
            with self.subTest(v=v):
                self.assertIsNone(_bool(v))

    def test_through_an_import_file(self):
        di = DataImport(self.db)
        rows = [["كود الصنف", "اسم الصنف", "سعر الجمهور", "محلي/مستورد", "دواء/غير دواء"],
                [201, "Synthetic Imported 10 tab", 50, "مستوردة", "دواء"],
                [202, "Synthetic Local 10 tab", 20, "محلية", "غير دواء"],
                [203, "Synthetic Unknown 10 tab", 20, "؟", 1.0]]
        r = di.get(self.ctx, di.analyze(self.ctx, sheet(rows), "flags.xlsx"))
        by = {x["data"]["code"]: x for x in r["rows"]}
        self.assertEqual((by["201"]["data"]["is_imported"], by["201"]["data"]["is_drug"]), (1, 1))
        self.assertEqual((by["202"]["data"]["is_imported"], by["202"]["data"]["is_drug"]), (0, 0))
        self.assertIsNone(by["203"]["data"]["is_imported"])
        self.assertIn("unknown_is_imported", [i["code"] for i in by["203"]["issues"]])    # shown, never guessed
        self.assertEqual(by["203"]["data"]["is_drug"], 1)
