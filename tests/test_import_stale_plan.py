"""An analyzed import run whose plan went stale (another run or another screen posted to the same item
or party in the meantime) must not post opening balances a second time (R4 review)."""
from decimal import Decimal

from app.catalog import service as cat
from app.core.errors import Conflict
from app.dataimport.service import DataImport
from app.inventory import service as inv
from app.procurement import suppliers as sup
from app.sales.service import Sales
from tests.helpers import DBTestCase
from tests.test_dataimport import sheet

STOCK_HEAD = ["كود", "الصنف", "الرصيد", "التكلفة", "التشغيلة", "الصلاحية"]


class StaleCase(DBTestCase):
    def setUp(self):
        super().setUp()
        self.di = DataImport(self.db)
        self.caf = cat.create_item(self.db, self.ctx, {"name_en": "Cataflam 50mg 20 tab", "pack_size": 2,
                                                       "code": "102"})
        self.aug = cat.create_item(self.db, self.ctx, {"name_en": "Augmentin 1gm 14 tab", "pack_size": 2,
                                                       "code": "103"})

    def qty(self, item):
        return inv.stock_for_item(self.db, self.ctx, item)["total_qty"]

    def status(self, run):
        return self.db.one("SELECT status FROM import_runs WHERE id=?", (run,))["status"]


class TestStaleOpeningStock(StaleCase):
    def test_second_run_for_the_same_item_is_refused_whole(self):
        f1 = sheet([STOCK_HEAD, [102, "Cataflam", 5, 49.2, "C1", "05/2028"], [103, "Augmentin", 1, 120, None, None]])
        f2 = sheet([STOCK_HEAD, [102, "Cataflam", 5, 49.2, "C1", "05/2028"]])
        r1 = self.di.analyze(self.ctx, f1, "stock.xlsx", kind="opening_stock")
        r2 = self.di.analyze(self.ctx, f2, "stock-fixed.xlsx", kind="opening_stock")
        self.di.apply(self.ctx, r2)
        self.assertEqual(self.qty(self.caf), 10)
        with self.assertRaises(Conflict) as e:
            self.di.apply(self.ctx, r1)
        self.assertEqual(e.exception.code, "stale_plan")
        self.assertEqual(e.exception.details["count"], 1)
        self.assertEqual(self.qty(self.caf), 10)                 # the file says 5 packs = 10 units
        self.assertEqual(self.qty(self.aug), 0)                  # nothing of the stale run was applied
        self.assertEqual(self.db.one("SELECT count(*) n FROM stock_movements WHERE movement_type='opening'")["n"], 1)
        self.assertEqual(self.status(r1), "analyzed")
        self.assertEqual(inv.verify_balances(self.db, self.ctx.org_id), [])

    def test_movement_from_another_screen_after_analyze(self):
        run = self.di.analyze(self.ctx, sheet([STOCK_HEAD, [102, "Cataflam", 5, 49.2, None, None]]),
                              "s.xlsx", kind="opening_stock")
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=self.caf, qty_delta=4, movement_type="purchase",
                              unit_cost=Decimal("15"))
        with self.assertRaises(Conflict) as e:
            self.di.apply(self.ctx, run)
        self.assertEqual(e.exception.code, "stale_plan")
        self.assertEqual(self.qty(self.caf), 4)

    def test_two_batches_of_one_item_in_one_file_still_apply(self):
        run = self.di.analyze(self.ctx, sheet([STOCK_HEAD, [102, "Cataflam", 5, 49.2, "C1", "05/2028"],
                                               [102, "Cataflam", 3, 49.2, "C2", "09/2028"]]),
                              "s.xlsx", kind="opening_stock")
        rep = self.di.apply(self.ctx, run)
        self.assertTrue(rep["reconciliation"]["ok"])
        self.assertEqual(self.qty(self.caf), 16)


class TestStalePartyBalances(StaleCase):
    def test_supplier_balance_is_not_posted_by_two_runs(self):
        cat.create_supplier(self.db, self.ctx, name="مورد ألف")
        sid = self.db.one("SELECT id FROM suppliers WHERE name='مورد ألف'")["id"]
        r1 = self.di.analyze(self.ctx, sheet([["اسم المورد", "الرصيد"], ["مورد ألف", 1000]]), "s.xlsx",
                             kind="suppliers")
        r2 = self.di.analyze(self.ctx, sheet([["اسم المورد", "الرصيد"], ["مورد جيم", 5], ["مورد ألف", 1000]]),
                             "s2.xlsx", kind="suppliers")
        self.di.apply(self.ctx, r1)
        with self.assertRaises(Conflict) as e:
            self.di.apply(self.ctx, r2)
        self.assertEqual(e.exception.code, "stale_plan")
        self.assertEqual(sup.balance(self.db, self.ctx.org_id, sid), 100000)
        self.assertIsNone(self.db.one("SELECT 1 FROM suppliers WHERE name='مورد جيم'"))
        self.assertEqual(self.status(r2), "analyzed")

    def test_customer_balance_is_not_posted_by_two_runs(self):
        cid = Sales(self.db).create_customer(self.ctx, name="أحمد علي", phone="01012345678")
        head = ["اسم العميل", "هاتف", "الرصيد"]
        r1 = self.di.analyze(self.ctx, sheet([head, ["أحمد علي", "01012345678", 250]]), "c.xlsx", kind="customers")
        r2 = self.di.analyze(self.ctx, sheet([head, ["أحمد علي", "01012345678", 250], ["منى", None, 0]]),
                             "c2.xlsx", kind="customers")
        self.di.apply(self.ctx, r1)
        with self.assertRaises(Conflict) as e:
            self.di.apply(self.ctx, r2)
        self.assertEqual(e.exception.code, "stale_plan")
        self.assertEqual(Sales(self.db).balance(self.ctx, cid), 25000)

    def test_party_without_a_balance_in_the_file_is_not_stale(self):
        cat.create_supplier(self.db, self.ctx, name="مورد ألف")
        r1 = self.di.analyze(self.ctx, sheet([["اسم المورد", "الرصيد"], ["مورد ألف", 1000]]), "s.xlsx",
                             kind="suppliers")
        r2 = self.di.analyze(self.ctx, sheet([["اسم المورد", "التليفون", "الرصيد"], ["مورد ألف", "0100", 0]]),
                             "s2.xlsx", kind="suppliers")
        self.di.apply(self.ctx, r1)
        self.assertTrue(self.di.apply(self.ctx, r2)["reconciliation"]["ok"])


class TestLinkDecisionOnOpeningStock(StaleCase):
    def setUp(self):
        super().setUp()
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=self.caf, qty_delta=10, movement_type="purchase",
                              unit_cost=Decimal("3"))

    def test_link_to_an_item_with_history_is_refused(self):
        from app.core.errors import ValidationFailed
        run = self.di.analyze(self.ctx, sheet([["كود", "الصنف", "الرصيد", "التكلفة"], [102, "Cataflam", 5, 49.2]]),
                              "s.xlsx", kind="opening_stock")
        row = self.di.get(self.ctx, run)["rows"][0]
        self.assertEqual([i["code"] for i in row["issues"]], ["item_has_history"])
        with self.assertRaises(ValidationFailed):
            self.di.decide(self.ctx, run, row["id"], f"link:{self.caf}")
        self.assertIsNone(self.db.one("SELECT decision FROM import_rows WHERE id=?", (row["id"],))["decision"])
        with self.assertRaises(Conflict) as e:
            self.di.apply(self.ctx, run)
        self.assertEqual(e.exception.code, "pending_review")
        self.assertEqual(self.qty(self.caf), 10)

    def test_link_of_another_row_to_an_item_with_history_is_refused(self):
        """The row's own item is clean, but the decision points at one that has history."""
        from app.core.errors import ValidationFailed
        run = self.di.analyze(self.ctx, sheet([["كود", "الصنف", "الرصيد", "التكلفة"], [103, "Augmentin", 5, 120]]),
                              "s.xlsx", kind="opening_stock")
        row = self.di.get(self.ctx, run)["rows"][0]
        with self.assertRaises(ValidationFailed):
            self.di.decide(self.ctx, run, row["id"], f"link:{self.caf}")

    def test_a_link_decision_stored_before_the_rule_does_not_apply(self):
        run = self.di.analyze(self.ctx, sheet([["كود", "الصنف", "الرصيد", "التكلفة"], [102, "Cataflam", 5, 49.2]]),
                              "s.xlsx", kind="opening_stock")
        row = self.di.get(self.ctx, run)["rows"][0]
        with self.db.transaction():
            self.db.execute("UPDATE import_rows SET decision=? WHERE id=?", (f"link:{self.caf}", row["id"]))
        with self.assertRaises(Conflict) as e:
            self.di.apply(self.ctx, run)
        self.assertEqual(e.exception.code, "stale_plan")
        self.assertEqual(self.qty(self.caf), 10)

    def test_link_to_an_item_without_history_still_works(self):
        run = self.di.analyze(self.ctx, sheet([["كود", "الصنف", "الرصيد", "التكلفة"], [999, "Augmentin 1gm 14 tab", 5, 120]]),
                              "s.xlsx", kind="opening_stock")
        row = self.di.get(self.ctx, run)["rows"][0]
        self.assertEqual(row["action"], "review")                 # matched by name only
        self.di.decide(self.ctx, run, row["id"], f"link:{self.aug}")
        self.di.apply(self.ctx, run)
        self.assertEqual(self.qty(self.aug), 10)
