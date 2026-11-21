"""U2-8 «مراجعة اليوم»: one day's sales, returns by method, discounts, credit, purchases and drawer; owner/manager only."""
from app.core.errors import Forbidden
from app.reports.day_review import day_review
from app.sales.cash import CashDrawer
from tests.helpers import today
from tests.test_sales import SalesCase


class TestDayReview(SalesCase):
    def test_sale_return_and_drawer(self):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=2)
        self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid))
        self.s.return_items(self.ctx, sid, {d["lines"][0]["id"]: 1}, refund_method="cash", reason="test")
        CashDrawer(self.db).close(self.ctx, counted_cash=0, note="عدّ تجربة", float_left=0)
        r = day_review(self.db, self.ctx, today())
        refund = r["returns"]["by_method"]["cash"]["total"]
        self.assertEqual(r["sales"]["n"], 1)
        self.assertEqual(r["net_sales"], d["total"] - refund)
        self.assertEqual(r["by_method"]["cash"], d["total"] - refund)
        self.assertEqual(len(r["drawer"]["shifts"]), 1)
        self.assertEqual(r["drawer"]["expected"], 50000 + d["total"] - refund)

    def test_owner_or_manager_only(self):
        from dataclasses import replace
        staff = replace(self.ctx, permissions=self.ctx.permissions - {"sales.totals"})
        with self.assertRaises(Forbidden):
            day_review(self.db, staff)


class TestDayReviewVoids(SalesCase):
    def test_void_taken_out_on_its_day_and_kept_on_the_sale_day(self):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=2)
        self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid))
        self.s.void(self.ctx, sid, "غلط في الفاتورة")
        r = day_review(self.db, self.ctx, today())
        self.assertEqual(r["sales"]["n"], 1)                       # it was sold today
        self.assertEqual(r["sales"]["voided_total"], d["total"])   # and voided today
        self.assertEqual(r["by_method"]["cash"], 0)                # the drawer got nothing in the end
        self.assertEqual(r["net_sales"], 0)
