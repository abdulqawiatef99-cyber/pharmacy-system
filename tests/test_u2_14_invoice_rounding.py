"""U2-14 invoice-total rounding: nearest step, its own line, exact drawer, full return gives it back, partial never."""
from dataclasses import replace

from app.core.errors import Forbidden, ValidationFailed
from app.sales import rounding
from app.sales.cash import CashDrawer
from tests.test_sales import SalesCase


class TestNearest(SalesCase):
    def test_nearest(self):
        n = rounding.nearest
        self.assertEqual([n(1910, 50), n(1925, 50), n(1940, 50), n(1950, 50)], [-10, 25, 10, 0])
        self.assertEqual([n(1912, 25), n(1913, 25), n(1949, 100), n(1950, 100)], [-12, 12, -49, 50])
        self.assertEqual([n(1910, 0), n(0, 50), n(-5, 50)], [0, 0, 0])

    def test_setting(self):
        self.assertEqual(rounding.step(self.db, self.ctx.org_id), 0)          # a new pharmacy: off until chosen
        self.assertEqual(rounding.set_step(self.db, self.ctx, "50"), 50)
        with self.assertRaises(ValidationFailed):
            rounding.set_step(self.db, self.ctx, 30)
        with self.assertRaises(Forbidden):
            rounding.set_step(self.db, replace(self.ctx, permissions=frozenset()), 25)


class TestRoundedSale(SalesCase):
    def setUp(self):
        super().setUp()
        rounding.set_step(self.db, self.ctx, 50)

    def sell(self, units):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=units)
        return sid, d

    def test_sale_rounded_on_its_own_line_and_drawer_exact(self):
        cd = CashDrawer(self.db)
        start = cd.summary(self.ctx)["expected_cash"]
        sid, d = self.sell(1)                       # 19.00 (OLD lot), nothing to round
        self.assertEqual((d["total"], d["rounding"]), (1900, 0))
        self.s.update_line(self.ctx, sid, d["lines"][0]["id"], discount_amount=10)  # 18.90 -> 19.00
        d = self.s.get(self.ctx, sid)
        self.assertEqual((d["subtotal"] - d["discount_total"], d["rounding"], d["total"]), (1890, 10, 1900))
        done = self.s.complete(self.ctx, sid, paid_cash=1900, safety_decisions=self.ack_all(sid))
        self.assertEqual((done["total"], done["rounding"]), (1900, 10))
        self.assertEqual(cd.summary(self.ctx)["expected_cash"], start + 1900)

    def test_full_return_gives_the_rounding_back_partial_does_not(self):
        sid, d = self.sell(2)                                                      # 38.00
        lid = d["lines"][0]["id"]
        self.s.update_line(self.ctx, sid, lid, discount_amount=20)                 # 37.80 -> 38.00 (+0.20)
        d = self.s.get(self.ctx, sid)
        self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid))
        r1 = self.s.return_items(self.ctx, sid, {lid: 1}, refund_method="cash", reason="test")
        r2 = self.s.return_items(self.ctx, sid, {lid: 1}, refund_method="cash", reason="test")
        self.assertEqual(r1["refund"] + r2["refund"], 3800)                       # all of what he paid, no more
        self.assertEqual(self.db.one("SELECT rounding FROM sale_returns WHERE id=?", (r2["return_id"],))["rounding"], 20)
        self.assertEqual(self.db.one("SELECT rounding FROM sale_returns WHERE id=?", (r1["return_id"],))["rounding"], 0)

    def test_rounded_down_full_return_refunds_what_was_paid(self):
        sid, d = self.sell(1)
        lid = d["lines"][0]["id"]
        self.s.update_line(self.ctx, sid, lid, discount_amount=40)                 # 18.60 -> 18.50 (-0.10)
        d = self.s.get(self.ctx, sid)
        self.assertEqual(d["total"], 1850)
        self.s.complete(self.ctx, sid, paid_cash=1850, safety_decisions=self.ack_all(sid))
        r = self.s.return_items(self.ctx, sid, {lid: 1}, refund_method="cash", reason="test")
        self.assertEqual(r["refund"], 1850)

    def test_setting_changed_between_draft_and_payment(self):
        sid, d = self.sell(1)
        self.s.update_line(self.ctx, sid, d["lines"][0]["id"], discount_amount=10)
        total = self.s.get(self.ctx, sid)["total"]                                # 19.00 at 0.50
        rounding.set_step(self.db, self.ctx, 0)
        with self.assertRaises(ValidationFailed):                                  # the cashier sees the new total
            self.s.complete(self.ctx, sid, paid_cash=total, safety_decisions=self.ack_all(sid))


class TestRoundingReports(TestRoundedSale):
    def test_day_review_shows_the_rounding(self):
        from app.reports.day_review import day_review
        from tests.helpers import today
        sid, d = self.sell(1)
        self.s.update_line(self.ctx, sid, d["lines"][0]["id"], discount_amount=10)
        self.s.complete(self.ctx, sid, paid_cash=1900, safety_decisions=self.ack_all(sid))
        self.assertEqual(day_review(self.db, self.ctx, today())["sales"]["rounding"], 10)


class TestMigrationSetsExistingPharmacy(TestNearest):
    def test_existing_pharmacy_gets_half_pound(self):
        sql = open("app/migrations/0042_invoice_rounding.sql", encoding="utf-8").read().split("\n")
        ins = next(l for l in sql if l.startswith("INSERT"))
        with self.db.transaction():
            self.db.execute(ins)
        self.assertEqual(rounding.step(self.db, self.ctx.org_id), 50)
