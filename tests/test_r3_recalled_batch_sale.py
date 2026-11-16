"""A batch with a confirmed recall is not sold; a recall nobody decided yet only warns. Synthetic data only."""
from datetime import timedelta
from decimal import Decimal

from app.catalog import service as cat
from app.core.errors import Conflict
from app.inventory import service as inv
from app.recalls.service import Recalls
from app.sales.cash import CashDrawer
from app.sales.service import Sales, local_today
from tests.helpers import DBTestCase

T = local_today()


class TestRecalledBatchSale(DBTestCase):
    def setUp(self):
        super().setUp()
        self.s, self.r = Sales(self.db), Recalls(self.db)
        CashDrawer(self.db).open(self.ctx, 0)
        self.item = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab", "pack_size": 2, "public_price": 4000})
        self.stock("RC123", 8, 300)           # sold first (earlier expiry)
        self.stock("OK777", 6, 500)

    def stock(self, batch_no, qty, days):
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.item, batch_no, (T + timedelta(days=days)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.item, qty_delta=qty, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("15"))
        return b

    def notice(self, batch_no="RC123"):
        self.r.add_notice(self.ctx, title="سحب تشغيلة", items=[{"product_name": "Panadol 500mg", "batch_numbers": [batch_no]}])
        return self.r.alerts(self.ctx)[0]["id"]

    def sold_batches(self, units):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.item, units=units)
        f = self.s.safety_check(self.ctx, sid)
        done = self.s.complete(self.ctx, sid, paid_card=d["total"], safety_decisions={i: "ok" for i in range(len(f))})
        return [(a["batch_no"], a["qty"]) for l in done["lines"] for a in l["allocations"]]

    def test_a_confirmed_recall_takes_the_batch_out_of_sale(self):
        self.r.decide(self.ctx, self.notice(), "confirmed")
        self.assertEqual(self.sold_batches(2), [("OK777", 2)])               # FEFO would have taken RC123
        sid = self.s.create(self.ctx)
        with self.assertRaises(Conflict) as e:                              # 4 left that may be sold, 8 held
            self.s.add_line(self.ctx, sid, self.item, units=5)
        self.assertEqual(e.exception.code, "insufficient_stock")
        self.assertEqual(e.exception.details, {"available": 4, "expired": 0, "recalled": 8})
        self.assertIn("منشور سحب", e.exception.message)
        d = self.s.add_line(self.ctx, sid, self.item, units=2)
        with self.assertRaises(Conflict) as e:
            self.s.update_line(self.ctx, sid, d["lines"][0]["id"], qty_base=5)
        self.assertEqual(e.exception.details["recalled"], 8)
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.item)["total_qty"], 12)   # the held stock is still counted

    def test_a_recall_confirmed_after_the_draft_stops_its_completion(self):
        mid = self.notice()
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.item, units=10)             # needs RC123 too
        self.r.decide(self.ctx, mid, "confirmed")
        with self.assertRaises(Conflict) as e:
            self.s.complete(self.ctx, sid, paid_card=d["total"], safety_decisions={})
        self.assertEqual(e.exception.code, "line_problems")

    def test_a_pending_recall_warns_and_does_not_stop_the_sale(self):
        self.notice()
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.item, units=2)
        f = [x for x in self.s.safety_check(self.ctx, sid) if x.code == "item_recall_pending"]
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0].requires, f[0].item_ids), ("none", [self.item]))
        self.assertIn("RC123", f[0].message)
        done = self.s.complete(self.ctx, sid, paid_card=d["total"], safety_decisions={})     # no decision needed
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["lines"][0]["allocations"][0]["batch_no"], "RC123")

    def stays_sellable(self, status, note=None):
        self.r.decide(self.ctx, self.notice(), status, note)
        sid = self.s.create(self.ctx)
        self.s.add_line(self.ctx, sid, self.item, units=2)
        self.assertEqual([x.code for x in self.s.safety_check(self.ctx, sid) if x.code == "item_recall_pending"], [])
        self.assertEqual(self.sold_batches(1), [("RC123", 1)])

    def test_not_ours_stays_sellable_without_a_warning(self):
        self.stays_sellable("not_ours", "ليست تشغيلتنا")

    def test_handled_stays_sellable_without_a_warning(self):
        self.stays_sellable("handled")

    def test_another_pharmacys_recall_does_not_hold_this_stock(self):
        _, other = self.second_tenant()
        oi = cat.create_item(self.db, other, {"name_en": "Panadol 500mg 24 tab", "pack_size": 2, "public_price": 4000})
        with self.db.transaction():
            ob = inv.get_or_create_batch(self.db, other, oi, "RC123", (T + timedelta(days=300)).isoformat())
            inv.post_movement(self.db, other, item_id=oi, qty_delta=5, batch_id=ob, movement_type="purchase", unit_cost=Decimal("15"))
        r2 = Recalls(self.db)
        r2.add_notice(other, title="سحب تشغيلة", items=[{"product_name": "Panadol 500mg", "batch_numbers": ["RC123"]}])
        r2.decide(other, r2.alerts(other)[0]["id"], "confirmed")
        self.assertEqual(self.sold_batches(2), [("RC123", 2)])
        sid = self.s.create(self.ctx)
        self.s.add_line(self.ctx, sid, self.item, units=2)
        self.assertEqual([x.code for x in self.s.safety_check(self.ctx, sid) if x.code == "item_recall_pending"], [])
