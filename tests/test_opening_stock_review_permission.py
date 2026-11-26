"""Opening-stock review: a row decision is consumed by a later authorized apply, so saving one needs the
same permission as analyzing and applying (#461, older review #324 S7). Synthetic data only."""
from app.core import people
from app.dataimport.service import DataImport
from app.core.errors import Forbidden
from tests.helpers import DBTestCase
from tests.test_dataimport import ITEMS, sheet

STOCK = [["كود", "الصنف", "الرصيد", "التكلفة"], [101, "Panadol", 3, 32]]


class TestOpeningStockReviewPermission(DBTestCase):
    def setUp(self):
        super().setUp()
        self.di = DataImport(self.db)
        self.di.apply(self.ctx, self.di.analyze(self.ctx, sheet(ITEMS[:4]), "items.xlsx"))
        self.run_id = self.di.analyze(self.ctx, sheet(STOCK), "s.xlsx", kind="opening_stock")
        self.row = self.di.get(self.ctx, self.run_id)["rows"][0]
        self.other = self.db.one("SELECT id FROM items WHERE code='103'")["id"]

    def staff(self, name, *grants):
        uid = people.add_user(self.db, self.ctx, username=name, full_name="x", password="Staff-Pass-2026",
                              role_code="assistant")
        for g in grants:
            people.set_exception(self.db, self.ctx, uid, g, "grant")
        return self.login(name, "Staff-Pass-2026")

    def state(self):
        return (self.db.one("SELECT decision, action FROM import_rows WHERE id=?", (self.row["id"],))["decision"],
                self.db.one("SELECT count(*) c FROM audit_log")["c"],
                self.db.one("SELECT count(*) c FROM stock_movements")["c"])

    def test_import_access_alone_cannot_decide_analyze_or_apply(self):
        ctx = self.staff("importer", "items.import")
        before = self.state()
        for decision in (f"link:{self.other}", "skip", "create"):
            with self.assertRaises(Forbidden):
                self.di.decide(ctx, self.run_id, self.row["id"], decision)
        with self.assertRaises(Forbidden):
            self.di.analyze(ctx, sheet(STOCK), "s2.xlsx", kind="opening_stock")
        with self.assertRaises(Forbidden):
            self.di.apply(ctx, self.run_id)
        self.assertEqual(self.state(), before)           # linkage, audit trail and stock unchanged

    def test_stock_adjust_alone_is_not_enough_either(self):
        ctx = self.staff("counter", "inventory.adjust")
        before = self.state()
        with self.assertRaises(Forbidden):
            self.di.decide(ctx, self.run_id, self.row["id"], "skip")
        self.assertEqual(self.state(), before)

    def test_both_permissions_can_decide(self):
        ctx = self.staff("both", "items.import", "inventory.adjust")
        self.di.decide(ctx, self.run_id, self.row["id"], "skip")
        self.assertEqual(self.state()[0], "skip")

    def test_an_items_run_still_needs_only_import_access(self):
        ctx = self.staff("importer2", "items.import")
        run = self.di.analyze(ctx, sheet(ITEMS[:3]), "again.xlsx")
        row = self.di.get(ctx, run)["rows"][0]
        self.di.decide(ctx, run, row["id"], "skip")

    def test_import_access_alone_cannot_read_or_cancel_the_run(self):
        ctx = self.staff("importer3", "items.import")
        before = self.state()
        with self.assertRaises(Forbidden):
            self.di.get(ctx, self.run_id)                 # quantities and costs of the count
        with self.assertRaises(Forbidden):
            self.di.discard(ctx, self.run_id)             # an authorized user's review, cancelled
        self.assertEqual(self.state(), before)
        self.assertEqual(self.db.one("SELECT status FROM import_runs WHERE id=?", (self.run_id,))["status"], "analyzed")

    def test_both_permissions_can_read_and_cancel(self):
        ctx = self.staff("both2", "items.import", "inventory.adjust")
        self.assertEqual(len(self.di.get(ctx, self.run_id)["rows"]), 1)
        self.di.discard(ctx, self.run_id)
        self.assertEqual(self.db.one("SELECT status FROM import_runs WHERE id=?", (self.run_id,))["status"], "discarded")

    def test_refusal_comes_before_the_run_status_is_told(self):
        ctx = self.staff("importer4", "items.import")
        self.di.discard(self.ctx, self.run_id)
        with self.assertRaises(Forbidden):
            self.di.decide(ctx, self.run_id, self.row["id"], "skip")


class TestPartyImportRunsNeedTheirOwnPermission(DBTestCase):
    """The same gap for supplier and customer files: names, phones and balances."""
    FILES = {"suppliers": ([["اسم المورد", "تليفون"], ["Synthetic Pharma Co", "01000000001"]], "suppliers.write"),
             "customers": ([["اسم العميل", "تليفون"], ["Synthetic Customer", "01000000002"]], "customers.manage")}

    def test_import_access_alone_cannot_read_decide_or_cancel(self):
        di = DataImport(self.db)
        uid = people.add_user(self.db, self.ctx, username="importer", full_name="x", password="Staff-Pass-2026",
                              role_code="assistant")
        people.set_exception(self.db, self.ctx, uid, "items.import", "grant")
        for kind, (rows, perm) in self.FILES.items():
            with self.subTest(kind=kind):
                people.set_exception(self.db, self.ctx, uid, perm, "deny")
                ctx = self.login("importer", "Staff-Pass-2026")
                run = di.analyze(self.ctx, sheet(rows), f"{kind}.xlsx", kind=kind)
                row = di.get(self.ctx, run)["rows"][0]
                audits = self.db.one("SELECT count(*) c FROM audit_log")["c"]
                with self.assertRaises(Forbidden):
                    di.get(ctx, run)
                with self.assertRaises(Forbidden):
                    di.decide(ctx, run, row["id"], "skip")
                with self.assertRaises(Forbidden):
                    di.discard(ctx, run)
                self.assertEqual(self.db.one("SELECT count(*) c FROM audit_log")["c"], audits)
                self.assertEqual(self.db.one("SELECT status FROM import_runs WHERE id=?", (run,))["status"], "analyzed")
