"""U3-5 stocktake while selling: the daily mini-count picks valuable, fast and past-difference items (not ones
counted in the last 2 weeks), opens a blind items count; sales during it adjust the expected qty; the variance
report is ordered by value high -> low."""
from datetime import timedelta
from decimal import Decimal

from app.catalog import service as cat
from app.core.errors import ValidationFailed
from app.inventory import service as inv
from app.inventory.counting import Counting
from tests.test_sales import TODAY, SalesCase


class TestDailyCount(SalesCase):
    def setUp(self):
        super().setUp()
        self.c = Counting(self.db)
        self.dear = cat.create_item(self.db, self.ctx, {"name_en": "Dear Item", "public_price": 100000})
        self.cheap = cat.create_item(self.db, self.ctx, {"name_en": "Cheap Item", "public_price": 100})
        with self.db.transaction():
            for it, cost, q in ((self.dear, "800", 3), (self.cheap, "0.5", 50)):
                b = inv.get_or_create_batch(self.db, self.ctx, it, "B", (TODAY + timedelta(days=500)).isoformat())
                inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=q, batch_id=b, movement_type="purchase",
                                  unit_cost=Decimal(cost))

    def test_pick_and_skip_recent(self):
        picks = self.c.daily_pick(self.ctx, 2)
        self.assertEqual(picks[0]["item_id"], self.dear)              # the most valuable first
        sid = self.c.create_daily(self.ctx, 2)
        for p in picks:
            self.c.record(self.ctx, sid, item_id=p["item_id"], qty=1)
        again = {p["item_id"] for p in self.c.daily_pick(self.ctx, 5)}
        self.assertFalse({p["item_id"] for p in picks} & again)       # counted today: not picked again

    def test_sales_during_count_and_value_order(self):
        sid = self.c.create(self.ctx, title="جرد", scope="items", item_ids=[self.dear, self.cheap, self.pan])
        self.c.record(self.ctx, sid, item_id=self.cheap, qty=40)       # 10 short x 0.50 = 5 EGP
        self.c.record(self.ctx, sid, item_id=self.dear, qty=2)         # 1 short x 800 = 800 EGP
        self.c.record(self.ctx, sid, item_id=self.pan, qty=13)
        s = self.s.create(self.ctx)                                    # a sale during the count
        d = self.s.add_line(self.ctx, s, self.pan, units=2)
        self.s.complete(self.ctx, s, paid_cash=d["total"], safety_decisions=self.ack_all(s))
        v = self.c.variances(self.ctx, sid)
        self.assertEqual([l["item_id"] for l in v["lines"]][:2], [self.dear, self.cheap])
        pan = next(l for l in v["lines"] if l["item_id"] == self.pan)
        self.assertEqual(pan["difference"], 0)                         # the sale is not a shortage

    def test_nothing_to_count(self):
        with self.db.transaction():
            self.db.execute("DELETE FROM stock_balances WHERE org_id=?", (self.ctx.org_id,))
        with self.assertRaises(ValidationFailed):
            self.c.create_daily(self.ctx)


class TestDailyTwice(TestDailyCount):
    def test_second_daily_refused(self):
        from app.core.errors import Conflict
        self.c.create_daily(self.ctx, 2)
        with self.assertRaises(Conflict):
            self.c.create_daily(self.ctx, 2)


from tests.test_owner_plan_priority_1_2 import PlanCase          # noqa: E402


class TestCountItemsAPI(PlanCase):
    def test_the_daily_picks_stay_visible(self):
        sid = self.call("POST", "/api/counts", 201, json={"daily": True})["id"]
        names = {x["name"] for x in self.call("GET", f"/api/counts/{sid}/items")["items"]}
        self.assertTrue(names)
        self.assertTrue(names <= {"Panadol 500mg 24 tab", "Trial Box 3 strips"})
