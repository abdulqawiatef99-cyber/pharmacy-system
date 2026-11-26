"""#324 part 1, the three P2s verified on 18/11 and queued for tonight (19/11): M8 a void in a later shift,
M9 a delivery settled in a shift it was not completed in, M11 a sale worth nothing with no payment line.
Each test failed on develop `4d6db99`. Synthetic data only."""
from app.core.errors import ValidationFailed
from tests.test_cashier_acceptance import Base


class TestM8VoidInALaterShift(Base):
    def test_the_shift_that_paid_the_void_back_counts_it(self):
        self.cd.open(self.ctx, 10000)
        sale = self.sell(units=2)                                            # 40.00 cash in shift A
        a = self.cd.close(self.ctx, 14000)["id"]
        b = self.cd.open(self.ctx, 14000)
        self.s.void(self.ctx, sale["id"], "خطأ في الفاتورة")                  # same day, in shift B
        rb = self.cd.report(self.ctx, b)
        self.assertEqual((rb["cash_by_kind"].get("void"), rb["voided_count"]), (-4000, 1))
        ra = self.cd.report(self.ctx, a)
        self.assertEqual((ra["sales_count"], ra["voided_count"]), (1, 0))   # A keeps what it had at its close


class TestM9DeliverySettledLater(Base):
    def _delivery(self):
        sid = self.s.create(self.ctx)
        self.s.add_line(self.ctx, sid, self.pan, units=2)
        d = self.s.set_details(self.ctx, sid, {"kind": "delivery", "delivery": {"phone": "0100"}})
        f = self.s.safety_check(self.ctx, sid)
        return self.s.complete(self.ctx, sid, payments=[{"method": "cash", "amount": d["total"]}],
                               safety_decisions={i: "ok" for i in range(len(f))})

    def test_a_delivery_completed_with_no_shift_belongs_to_the_shift_that_got_its_cash(self):
        sale = self._delivery()                                              # no shift open: shift_id NULL
        b = self.cd.open(self.ctx, 0)
        self.s.delivery_status(self.ctx, sale["id"], "settled")
        rb = self.cd.report(self.ctx, b)
        self.assertEqual((rb["cash_by_kind"].get("sale"), rb["sales_count"], rb["sales_total"]), (4000, 1, 4000))

    def test_a_delivery_already_in_a_shift_stays_there(self):
        a = self.cd.open(self.ctx, 0)
        sale = self._delivery()
        self.cd.close(self.ctx, 0)
        self.cd.open(self.ctx, 0)
        self.s.delivery_status(self.ctx, sale["id"], "settled")
        self.assertEqual(self.db.one("SELECT shift_id FROM sales WHERE id=?", (sale["id"],))["shift_id"], a)


class TestM11NothingToPay(Base):
    def test_a_sale_worth_nothing_completes_with_no_payment_line(self):
        self.cd.open(self.ctx, 0)
        sid = self.s.create(self.ctx)
        with self.db.transaction():   # U2-33: nobody sells under cost; this test is about a zero total, so the cost is unknown
            self.db.execute("DELETE FROM item_costs WHERE item_id=?", (self.pan,))
        self.s.add_line(self.ctx, sid, self.pan, units=1, discount_bp=10000)  # 100% discount
        f = self.s.safety_check(self.ctx, sid)
        r = self.s.complete(self.ctx, sid, payments=[], safety_decisions={i: "ok" for i in range(len(f))},
                            confirm_below_cost=True)
        self.assertEqual((r["status"], r["total"]), ("completed", 0))

    def test_no_payment_line_on_a_sale_worth_something_is_still_refused(self):
        self.cd.open(self.ctx, 0)
        sid = self.s.create(self.ctx)
        self.s.add_line(self.ctx, sid, self.pan, units=1)
        f = self.s.safety_check(self.ctx, sid)
        with self.assertRaises(ValidationFailed):
            self.s.complete(self.ctx, sid, payments=[], safety_decisions={i: "ok" for i in range(len(f))})
