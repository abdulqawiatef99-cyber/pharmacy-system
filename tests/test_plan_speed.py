"""The purchases tool opens fast (owner 27/11: 27 s on the pharmacy PC; synthetic data only).

The E-Stock archive read is kept across edits that do not change the items' ids and codes (a price edit
used to throw it away); the answer says where its time went."""
from datetime import date

from app.procurement import demand
from app.procurement import purchase_plan as pp
from tests.test_estock_db_import import Base, base_tables


class ArchiveKept(Base):
    def test_a_price_edit_does_not_throw_the_archive_read_away(self):
        t = base_tables()
        self.assertTrue(self.run_import(tables=t)["ok"])
        demand._cache.clear()
        today = date(2026, 11, 22)
        demand.monthly(self.db, self.ctx, months=13, today=today)
        keys = set(demand._cache)
        self.assertTrue(keys)
        with self.db.transaction():
            self.db.execute("UPDATE items SET public_price=public_price+100, updated_at='2026-11-27T10:00:00.000Z'")
        demand.monthly(self.db, self.ctx, months=13, today=today)
        self.assertEqual(set(demand._cache), keys, "an edit that keeps ids and codes must reuse the read")

    def test_a_changed_item_code_does_read_again(self):
        self.assertTrue(self.run_import(tables=base_tables())["ok"])
        demand._cache.clear()
        today = date(2026, 11, 22)
        demand.monthly(self.db, self.ctx, months=13, today=today)
        keys = set(demand._cache)
        with self.db.transaction():
            self.db.execute("UPDATE items SET code='999' WHERE code='100'")
        demand.monthly(self.db, self.ctx, months=13, today=today)
        self.assertNotEqual(set(demand._cache), keys)


class Timings(Base):
    def test_the_plan_reports_where_its_time_went(self):
        self.assertTrue(self.run_import(tables=base_tables())["ok"])
        r = pp.plan(self.db, self.ctx, today=date(2026, 11, 22))
        self.assertEqual(set(r["timings_ms"]), {"sales_history", "stock", "purchases", "orders", "rows"})
        self.assertTrue(all(isinstance(v, int) and v >= 0 for v in r["timings_ms"].values()))
