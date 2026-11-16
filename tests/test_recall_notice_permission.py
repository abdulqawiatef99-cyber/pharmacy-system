"""Recording a recall notice writes data, so it needs more than the right to read stock (R4 review):
inventory.adjust, like deciding on an alert. Reading the alerts and the automatic re-matching after a
purchase approval stay with inventory.read. Synthetic data only."""
from datetime import date, timedelta
from decimal import Decimal

from app.catalog import service as cat
from app.core import accounts
from app.core.errors import Forbidden
from app.inventory import service as inv
from app.recalls.service import Recalls
from tests.helpers import DBTestCase

PW = "Staff-Pass-2026"
NOTICE = {"title": "سحب Flumox", "items": [{"product_name": "Flumox 1000 mg fct", "batch_numbers": ["2400905"]}]}


class TestRecallNoticePermission(DBTestCase):
    def setUp(self):
        super().setUp()
        self.r = Recalls(self.db)
        self.flu = cat.create_item(self.db, self.ctx, {"name_en": "Flumox 1000mg 14 tab"})
        for name, role in (("asst", "assistant"), ("pharm", "pharmacist"), ("mgr", "manager")):
            accounts.add_user(self.db, self.ctx, username=name, full_name=name, password=PW, role_code=role)

    def arrive(self):
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.flu, "2400905",
                                        (date.today() + timedelta(days=500)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.flu, qty_delta=6, batch_id=b,
                              movement_type="purchase", unit_cost=Decimal("10"))

    def test_who_only_reads_stock_cannot_record_a_notice(self):
        self.arrive()
        for name in ("asst", "pharm"):
            ctx = self.login(name, PW)
            self.assertTrue(ctx.can("inventory.read"))
            with self.assertRaises(Forbidden) as e:
                self.r.add_notice(ctx, **NOTICE)
            self.assertEqual(e.exception.details["permission"], "inventory.adjust")
        self.assertEqual(self.db.one("SELECT count(*) n FROM regulatory_notices")["n"], 0)
        self.assertEqual(self.db.one("SELECT count(*) n FROM recall_matches")["n"], 0)
        self.assertEqual(self.db.one("SELECT count(*) n FROM audit_log WHERE action LIKE 'recall.%'")["n"], 0)

    def test_manager_records_and_everyone_with_stock_access_still_reads(self):
        self.arrive()
        self.r.add_notice(self.login("mgr", PW), **NOTICE)
        for name in ("asst", "pharm", "mgr"):
            self.assertEqual([a["batch_no"] for a in self.r.alerts(self.login(name, PW))], ["2400905"])

    def test_rematching_after_a_purchase_still_works_for_who_approved_it(self):
        """Purchasing.approve re-runs the matching as the approving user (a pharmacist has no
        inventory.adjust): a recalled batch arriving later must still raise its alert."""
        self.r.add_notice(self.ctx, **NOTICE)
        self.arrive()
        ph = self.login("pharm", PW)
        self.assertEqual(self.r.match_new_batches(ph), 1)
        self.assertEqual(len(self.r.alerts(ph)), 1)
