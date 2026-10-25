"""Daily checks: expiry and sales spikes become notifications, once."""
from datetime import timedelta
from decimal import Decimal

from app.core import daily, notify
from app.inventory import service as inv
from tests.test_cashier_acceptance import Base, T


class TestDaily(Base):
    def test_expiry_and_once_a_day(self):
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.pan, "OLD", (T - timedelta(days=3)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=4, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("15"))
            b2 = inv.get_or_create_batch(self.db, self.ctx, self.pan, "SOON", (T + timedelta(days=12)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=4, batch_id=b2, movement_type="purchase",
                              unit_cost=Decimal("15"))
        self.assertEqual(daily.run(self.db, T), 2)
        kinds = {n["kind"] for n in notify.listing(self.db, self.ctx)["items"]}
        self.assertEqual(kinds, {"expired_stock", "expiry_soon"})
        self.assertEqual(daily.run(self.db, T), 0)                     # same day: nothing again

    def test_sales_spike(self):
        self.cd.open(self.ctx, 0)
        old = []
        for _ in range(2):                                             # 2 units, 5 weeks ago
            old.append(self.sell(units=1)["id"])
        with self.db.transaction():
            for sid in old:
                self.db.execute("UPDATE sales SET completed_at=? WHERE id=?", ((T - timedelta(days=35)).isoformat() + "T10:00:00.000000Z", sid))
        for _ in range(3):
            self.sell(units=2)                                          # 6 units this week
        daily.run(self.db, T)
        n = [x for x in notify.listing(self.db, self.ctx)["items"] if x["kind"] == "sales_spike"]
        self.assertEqual(len(n), 1)
        self.assertIn("زاد قوي", n[0]["title"])


class TestDailyNoNoise(Base):
    def test_same_batches_not_repeated_next_day(self):
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.pan, "SOON", (T + timedelta(days=12)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=4, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("15"))
            old = inv.get_or_create_batch(self.db, self.ctx, self.pan, "OLD", (T - timedelta(days=3)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=4, batch_id=old, movement_type="purchase",
                              unit_cost=Decimal("15"))
        self.assertEqual(daily.run(self.db, T), 2)
        self.assertEqual(daily.run(self.db, T + timedelta(days=1)), 0)        # nothing new: no noise
        self.assertEqual(daily.run(self.db, T + timedelta(days=7)), 1)        # expired still there: weekly reminder
