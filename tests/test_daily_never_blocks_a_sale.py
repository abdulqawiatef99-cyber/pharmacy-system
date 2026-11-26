"""The once-a-day checks never make a sale wait. Measured on a synthetic 30,000-item, 2-year pharmacy
(scripts/perf/loadgen.py, seed 253): an item added while daily.run was looking for expiring lots and sales
spikes waited 206 ms. Those reads ran inside a write transaction (BEGIN IMMEDIATE + the write lock), so
every sale queued behind them. On a slower PC with more history that grows to seconds.
Now the reads run first, with no lock; only the short notification and state write takes the lock."""
import threading
import time
from datetime import timedelta
from decimal import Decimal

from app.core import daily, notify
from app.db import Database
from app.inventory import service as inv
from tests.test_cashier_acceptance import Base, T

HOLD = 0.4          # seconds each heavy daily query is made to take


class TestDailyNeverBlocksASale(Base):
    def test_a_write_during_the_daily_reads_does_not_wait(self):
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.pan, "OLD", (T - timedelta(days=3)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=4, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("15"))
        reading, in_tx = threading.Event(), []
        real_all, real_one = Database.all, Database.one

        def slow(real):
            def wrapped(db, sql, *a, **k):
                if "stock_balances" in sql or "WITH s AS" in sql:        # the daily stock / sales-spike reads
                    in_tx.append(db.conn.in_transaction)
                    reading.set()
                    time.sleep(HOLD)
                return real(db, sql, *a, **k)
            return wrapped

        waited = []

        def cashier():
            reading.wait(5)
            time.sleep(0.05)                                            # the daily read is under way
            t = time.perf_counter()
            with self.db.transaction():                                 # what a sale does first
                self.db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?, 'probe', '1')"
                                " ON CONFLICT(org_id, key) DO UPDATE SET value=excluded.value", (self.ctx.org_id,))
            waited.append(time.perf_counter() - t)

        Database.all, Database.one = slow(real_all), slow(real_one)
        try:
            th = threading.Thread(target=cashier)
            th.start()
            sent = daily.run(self.db, T)
            th.join(10)
        finally:
            Database.all, Database.one = real_all, real_one
        self.assertTrue(in_tx)
        self.assertFalse(any(in_tx), "a daily read ran inside the write transaction")
        self.assertLess(waited[0], HOLD / 2, f"the sale waited {waited[0]:.3f} s for the daily checks")
        # and the checks still did their work, once
        self.assertEqual(sent, 1)
        self.assertEqual({n["kind"] for n in notify.listing(self.db, self.ctx)["items"]}, {"expired_stock"})
        self.assertEqual(daily.run(self.db, T), 0)
