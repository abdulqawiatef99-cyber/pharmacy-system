"""A book correction (manual adjustment, another count) posted after an item was counted must not
be applied a second time when the count is approved (R4 review)."""
from decimal import Decimal

from app.catalog import service as cat
from app.core.errors import Conflict
from app.inventory import adjust
from app.inventory import service as inv
from app.inventory.counting import Counting
from app.sales.service import Sales
from tests.helpers import DBTestCase


class TestAdjustedSinceCount(DBTestCase):
    def setUp(self):
        super().setUp()
        self.cnt = Counting(self.db)
        self.item = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab",
                                                        "public_price": 4000})
        self.other = cat.create_item(self.db, self.ctx, {"name_en": "Concor 5mg 30 tab"})
        with self.db.transaction():
            self.batch = inv.get_or_create_batch(self.db, self.ctx, self.item, "RC123", "2029-01-31")
            inv.post_movement(self.db, self.ctx, item_id=self.item, qty_delta=8, batch_id=self.batch,
                              movement_type="purchase", unit_cost=Decimal("15"))
            inv.post_movement(self.db, self.ctx, item_id=self.other, qty_delta=4,
                              movement_type="purchase", unit_cost=Decimal("2"))

    def qty(self, item=None):
        return inv.stock_for_item(self.db, self.ctx, item or self.item)["total_qty"]

    def line(self, sid, item=None):
        return next(l for l in self.cnt.variances(self.ctx, sid)["lines"]
                    if l["item_id"] == (item or self.item))

    def test_manual_adjust_after_count_blocks_approval_until_recount(self):
        sid = self.cnt.create(self.ctx, title="c", scope="items", item_ids=[self.item])
        self.cnt.record(self.ctx, sid, item_id=self.item, qty=5)       # shelf has 5, system 8
        adjust.adjust(self.db, self.ctx, self.item, set_to=5, reason="lost")
        self.assertEqual(self.qty(), 5)
        l = self.line(sid)
        self.assertEqual(l["problem"], "adjusted_since_count")
        self.assertIn("أعد", l["problem_message"])
        with self.assertRaises(Conflict) as e:
            self.cnt.approve(self.ctx, sid)
        self.assertEqual(e.exception.code, "count_problems")
        self.assertIn("Panadol", str(e.exception))
        self.assertEqual(self.qty(), 5)                                # nothing subtracted twice
        self.cnt.record(self.ctx, sid, item_id=self.item, qty=5, recount=True)
        self.assertIsNone(self.line(sid).get("problem"))
        rep = self.cnt.approve(self.ctx, sid)
        self.assertEqual(rep["adjustment_lines"], 0)
        self.assertEqual(self.qty(), 5)
        self.assertEqual(inv.verify_balances(self.db, self.ctx.org_id), [])

    def test_batch_level_count_then_manual_adjust(self):
        sid = self.cnt.create(self.ctx, title="c", scope="items", item_ids=[self.item])
        self.cnt.record(self.ctx, sid, item_id=self.item, qty=5, batch_id=self.batch)
        adjust.adjust(self.db, self.ctx, self.item, set_to=5, reason="lost")
        self.assertEqual(self.line(sid)["problem"], "adjusted_since_count")
        with self.assertRaises(Conflict):
            self.cnt.approve(self.ctx, sid)
        self.assertEqual(self.qty(), 5)

    def test_two_overlapping_counts_do_not_both_post_the_shortage(self):
        s1 = self.cnt.create(self.ctx, title="full")
        s2 = self.cnt.create(self.ctx, title="one item", scope="items", item_ids=[self.item])
        self.cnt.record(self.ctx, s1, item_id=self.item, qty=5)
        self.cnt.record(self.ctx, s1, item_id=self.other, qty=4)
        self.cnt.record(self.ctx, s2, item_id=self.item, qty=5)
        self.cnt.approve(self.ctx, s2)
        self.assertEqual(self.qty(), 5)
        self.assertEqual(self.line(s1)["problem"], "adjusted_since_count")
        self.assertIsNone(self.line(s1, self.other).get("problem"))    # only the touched item
        with self.assertRaises(Conflict):
            self.cnt.approve(self.ctx, s1)
        self.assertEqual(self.qty(), 5)
        self.cnt.record(self.ctx, s1, item_id=self.item, qty=5, recount=True)
        self.cnt.approve(self.ctx, s1)
        self.assertEqual(self.qty(), 5)

    def test_adjustment_before_the_count_and_sales_after_it_are_not_problems(self):
        adjust.adjust(self.db, self.ctx, self.item, set_to=7, reason="lost")   # before counting
        sid = self.cnt.create(self.ctx, title="c", scope="items", item_ids=[self.item])
        self.cnt.record(self.ctx, sid, item_id=self.item, qty=6)
        sales = Sales(self.db)
        sale = sales.create(self.ctx)
        d = sales.add_line(self.ctx, sale, self.item, units=2)
        sales.complete(self.ctx, sale, paid_card=d["total"])
        l = self.line(sid)
        self.assertIsNone(l.get("problem"))
        self.assertEqual(l["moves_after_count"], -2)
        self.cnt.approve(self.ctx, sid)
        self.assertEqual(self.qty(), 4)                                # 6 counted - 2 sold
