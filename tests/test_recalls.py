from datetime import date, timedelta
from decimal import Decimal

from app.catalog import service as cat
from app.core.errors import Conflict, ValidationFailed
from app.inventory import service as inv
from app.recalls.service import Recalls, extract_batches, norm_batch
from tests.helpers import DBTestCase


class TestExtract(DBTestCase):
    def test_proposes_batches_not_dates_or_strengths(self):
        text = ("منشور رقم 13 لسنة 2025 بسحب التشغيلة رقم 2400905 من مستحضر Flumox 1000 mg fct "
                "وكذلك التشغيلات 251116 و251117 بتاريخ 05/2027 تركيز 500mg")
        got = extract_batches(text)
        self.assertIn("2400905", got)
        self.assertIn("251116", got)
        self.assertNotIn("2025", got)
        self.assertNotIn("500MG", got)
        self.assertNotIn("052027", got)

    def test_norm(self):
        self.assertEqual(norm_batch(" ab-12 34 "), "AB1234")


class TestRecalls(DBTestCase):
    def setUp(self):
        super().setUp()
        self.r = Recalls(self.db)
        self.flu = cat.create_item(self.db, self.ctx, {"name_en": "Flumox 1000mg 14 tab"})
        self.other = cat.create_item(self.db, self.ctx, {"name_en": "Concor 5mg 30 tab"})
        exp = (date.today() + timedelta(days=500)).isoformat()
        with self.db.transaction():
            b1 = inv.get_or_create_batch(self.db, self.ctx, self.flu, "2400905", exp)
            inv.post_movement(self.db, self.ctx, item_id=self.flu, qty_delta=6, batch_id=b1,
                              movement_type="purchase", unit_cost=Decimal("10"))
            b2 = inv.get_or_create_batch(self.db, self.ctx, self.other, "2400905", exp)  # collision
            inv.post_movement(self.db, self.ctx, item_id=self.other, qty_delta=3, batch_id=b2,
                              movement_type="purchase", unit_cost=Decimal("10"))

    def add(self, **kw):
        return self.r.add_notice(self.ctx, title="سحب Flumox", notice_no="13", notice_date="2025-04-10",
                                 items=[{"product_name": "Flumox 1000 mg fct",
                                         "batch_numbers": ["2400905"]}], **kw)

    def test_match_and_flag_collisions(self):
        self.add()
        alerts = {a["item_id"]: a for a in self.r.alerts(self.ctx)}
        self.assertEqual(alerts[self.flu]["match_basis"], "batch+name")
        self.assertEqual(alerts[self.flu]["qty_in_stock"], 6)
        self.assertEqual(alerts[self.other]["match_basis"], "batch_only")   # human decides
        with self.assertRaises(ValidationFailed):
            self.r.decide(self.ctx, alerts[self.other]["id"], "not_ours")
        self.r.decide(self.ctx, alerts[self.other]["id"], "not_ours", "منتج وشركة مختلفين")
        self.assertEqual(len(self.r.alerts(self.ctx)), 1)
        # stock is never moved automatically
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.flu)["total_qty"], 6)

    def test_duplicate_notice_and_rematch_idempotent(self):
        nid = self.add()
        with self.assertRaises(Conflict):
            self.add()
        self.assertEqual(self.r.match(self.ctx, nid), 0)

    def test_batch_arriving_after_notice_is_caught(self):
        self.r.add_notice(self.ctx, title="سحب لاحق", items=[{"product_name": "Concor 5mg",
                                                             "batch_numbers": ["X-77"]}])
        self.assertEqual(self.r.alerts(self.ctx), [])
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.other, "x77", None)
            inv.post_movement(self.db, self.ctx, item_id=self.other, qty_delta=1, batch_id=b,
                              movement_type="purchase", unit_cost=Decimal("1"))
        self.r.match_new_batches(self.ctx)
        self.assertEqual([a["batch_no"] for a in self.r.alerts(self.ctx)], ["X77"])

    def test_tenant_isolation(self):
        self.add()
        _, other = self.second_tenant()
        self.assertEqual(self.r.alerts(other), [])
