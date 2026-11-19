"""#324 part 4, the report findings never checked before 19/11: R3 (the end of a day when Cairo's clock
goes back), R4 (retail stock value after a lot's price changed), R5 (profit in Invoices against Profit), R6 (the discount breakdown, proven fine for today's sales), R18 (the owner's shift row against
the shift slip). Synthetic data only."""
import os
import time
import unittest
from datetime import date, timedelta
from decimal import Decimal

from app.inventory import service as inv
from app.reports import invoices, owner
from app.reports.service import stock_report
from app.sales.service import local_today
from tests.test_cashier_acceptance import Base

T = date.today()


# ---------------------------------------------------------------- R3
@unittest.skipUnless(hasattr(time, "tzset"), "needs a settable time zone")
class TestR3DayEndWhenTheClockGoesBack(Base):
    """Cairo, 29 October 2026: at 24:00 summer time the clock returns to 23:00, so 23:00-23:59 happens twice."""

    def setUp(self):
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "Africa/Cairo"
        time.tzset()
        super().setUp()
        self.cd.open(self.ctx, 0)

    def tearDown(self):
        super().tearDown()
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        time.tzset()

    def sale_at(self, utc):
        s = self.sell(units=2)
        with self.db.transaction():
            self.db.execute("UPDATE sales SET completed_at=? WHERE id=?", (utc, s["id"]))

    def test_a_whole_day_includes_both_passes_of_the_repeated_hour(self):
        self.sale_at("2026-10-29T20:30:00.000000Z")      # 23:30, first pass (UTC+3)
        self.sale_at("2026-10-29T21:30:00.000000Z")      # 23:30, second pass (UTC+2)
        r = invoices.report(self.db, self.ctx, date_from="2026-10-29", date_to="2026-10-29")
        self.assertEqual(r["totals"]["n"], 2)
        nxt = invoices.report(self.db, self.ctx, date_from="2026-10-30", date_to="2026-10-30")
        self.assertEqual(nxt["totals"]["n"], 0)

    def test_an_end_time_inside_the_repeated_hour_takes_its_later_pass(self):
        self.sale_at("2026-10-29T21:10:00.000000Z")      # 23:10, second pass
        r = invoices.report(self.db, self.ctx, date_from="2026-10-29T00:00", date_to="2026-10-29T23:15")
        self.assertEqual(r["totals"]["n"], 1)

    def test_an_ordinary_day_is_unchanged(self):
        self.sale_at("2026-11-10T21:59:59.000000Z")      # 23:59:59 on 10 November (UTC+2)
        self.sale_at("2026-11-10T22:00:00.000000Z")      # 00:00 on 11 November
        self.assertEqual(invoices.report(self.db, self.ctx, date_from="2026-11-10", date_to="2026-11-10")["totals"]["n"], 1)
        self.assertEqual(invoices.report(self.db, self.ctx, date_from="2026-11-11", date_to="2026-11-11")["totals"]["n"], 1)


# ---------------------------------------------------------------- R4
class TestR4RetailValueByLotPrice(Base):
    def test_a_lot_with_its_own_price_is_valued_at_it(self):
        """One pack at 100 and a newer pack at 125: the sale screen and the expiry report say 225."""
        from app.catalog import service as cat
        it = cat.create_item(self.db, self.ctx, {"name_en": "Two Prices", "pack_size": 1, "public_price": 12500})
        with self.db.transaction():
            for lot, price in (("OLD", 10000), ("NEW", 12500)):
                b = inv.get_or_create_batch(self.db, self.ctx, it, lot, (T + timedelta(days=60)).isoformat())
                self.db.execute("UPDATE batches SET public_price=? WHERE id=?", (price, b))
                inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=1, batch_id=b, movement_type="purchase",
                                  unit_cost=Decimal("50"))
        with self.db.transaction():      # only this item in stock
            self.db.execute("DELETE FROM stock_balances WHERE item_id=?", (self.pan,))
        r = stock_report(self.db, self.ctx)
        self.assertEqual(sum(x["value"] for x in r["expiring"] if x["item_id"] == it), 22500)
        self.assertEqual(r["value_retail"], 22500)


# ---------------------------------------------------------------- R5
class TestR5ProfitRoundsOnce(Base):
    def test_the_total_profit_is_the_sum_of_exact_costs_rounded_once(self):
        """Two sales of 10.00 whose cost is 5.005 each: 20.00 - 10.01 = 9.99, as Profit and Top Items say."""
        from app.catalog import service as cat
        from app.finance.expenses import profit
        it = cat.create_item(self.db, self.ctx, {"name_en": "Half Piastre", "pack_size": 1, "public_price": 1000})
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, it, "H1", (T + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=10, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("5.005"))
        self.cd.open(self.ctx, 0)
        for _ in range(2):
            sid = self.s.create(self.ctx, None)
            d = self.s.add_line(self.ctx, sid, it, units=1)
            self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions={})
        day = local_today().isoformat()
        r = invoices.report(self.db, self.ctx, date_from=day, date_to=day)
        self.assertEqual(r["totals"]["profit"], 999)
        self.assertEqual(r["totals"]["profit"], profit(self.db, self.ctx, day, day)["gross_profit"])


# ---------------------------------------------------------------- R6
class TestR6DiscountBreakdownOfTodaysSales(Base):
    """R6 is about sales saved by a release before 0.4 (each batch priced on its own): for those the
    breakdown can be a piastre away from the saved discount. A database started at launch holds none. This
    proves the sales the program records now reconcile, on the same shape (one pack split over two batches,
    10.05 at 50%)."""

    def test_the_breakdown_of_a_split_batch_sale_equals_the_saved_discount(self):
        from app.catalog import service as cat
        it = cat.create_item(self.db, self.ctx, {"name_en": "Split Pack", "pack_size": 3, "public_price": 1005})
        with self.db.transaction():
            for lot, qty, days in (("S1", 2, 100), ("S2", 4, 200)):
                b = inv.get_or_create_batch(self.db, self.ctx, it, lot, (T + timedelta(days=days)).isoformat())
                inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=qty, batch_id=b, movement_type="purchase",
                                  unit_cost=Decimal("1"))
        self.cd.open(self.ctx, 0)
        sid = self.s.create(self.ctx, None)
        d = self.s.add_line(self.ctx, sid, it, packs=1, discount_bp=5000)
        sale = self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions={})
        self.assertEqual(self.db.one("SELECT count(*) n FROM sale_allocations a JOIN sale_lines l ON l.id=a.sale_line_id"
                                     " WHERE l.sale_id=?", (sid,))["n"], 2)                # really split
        day = local_today().isoformat()
        row = invoices.report(self.db, self.ctx, date_from=day, date_to=day)["rows"][0]
        self.assertEqual(row["item_discount"] + row["customer_discount"], sale["discount_total"])
        rep = owner.discounts_report(self.db, self.ctx, day, day)
        self.assertEqual(sum(g["discount"] for g in rep["by_who"]), rep["totals"]["discount"])
        self.assertEqual(rep["totals"]["discount"], sale["discount_total"])


# ---------------------------------------------------------------- R18
class TestR18ShiftRowMatchesTheSlip(Base):
    def test_a_sale_voided_in_the_next_shift_stays_in_the_closed_shifts_row(self):
        """Open with 100, sell 40, deposit 50, close at 190, then void the sale during the next shift."""
        self.cd.open(self.ctx, 10000)
        s = self.sell(units=2)
        self.cd.movement(self.ctx, 5000, "deposit", "إيداع فكة")
        first = self.cd.close(self.ctx, 19000)
        self.cd.open(self.ctx, 19000)
        self.s.void(self.ctx, s["id"], "خطأ في الفاتورة")
        day = local_today().isoformat()
        row = next(r for r in owner.shift_closings(self.db, self.ctx, day, day)["rows"] if r["id"] == first["id"])
        slip = self.cd.summary(self.ctx, first["id"])
        self.assertEqual(row["sales_n"], 1)
        self.assertEqual(row["sales_n"], slip["sales_count"])
        self.assertEqual(row["deposits"], 5000)
        # what the row shows adds up to the expected cash it shows
        self.assertEqual(row["opening_cash"] + row["cash_sales"] + row["refunds"] + row["payouts"] + row["deposits"]
                         + row["other"], row["expected_cash"])
        self.assertEqual(row["other"], 0)


if __name__ == "__main__":
    unittest.main()
