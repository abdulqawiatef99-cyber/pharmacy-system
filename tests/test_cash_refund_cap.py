"""#142: a cash refund never pays out more cash than the sale brought into the drawer."""
from app.core.errors import Conflict
from tests.test_cashier_acceptance import Base


class TestCashRefundCap(Base):
    def setUp(self):
        super().setUp()
        self.cd.open(self.ctx, 10000)                                   # 100.00 float
        self.cust = self.s.create_customer(self.ctx, name="عميل تجربة", credit_limit=100000)

    def drawer(self):
        return self.cd.summary(self.ctx)["expected_cash"]

    def balance(self):
        return self.db.one("SELECT coalesce(sum(amount), 0) FROM customer_ledger WHERE customer_id=?", (self.cust,))[0]

    def test_cash_refund_on_a_sale_wholly_on_credit_is_refused_and_nothing_moves(self):
        sale = self.sell(units=2, credit=4000, customer=self.cust)        # 40.00 on the account, no cash in
        line = sale["lines"][0]["id"]
        stock = self.db.one("SELECT sum(qty_base - returned_qty) FROM sale_allocations WHERE sale_line_id=?", (line,))[0]
        with self.assertRaises(Conflict) as e:
            self.s.return_items(self.ctx, sale["id"], {line: 2}, refund_method="cash", reason="مرتجع تجربة")
        self.assertEqual(e.exception.code, "cash_refund_exceeds_paid")
        self.assertEqual(e.exception.details["max_cash"], 0)
        self.assertEqual((self.drawer(), self.balance()), (10000, 4000))            # nothing paid out
        self.assertEqual(self.db.one("SELECT count(*) FROM sale_returns")[0], 0)
        self.assertEqual(self.db.one("SELECT sum(qty_base - returned_qty) FROM sale_allocations WHERE sale_line_id=?",
                                     (line,))[0], stock)                            # stock rolled back too
        r = self.s.return_items(self.ctx, sale["id"], {line: 2}, refund_method="credit", reason="مرتجع تجربة")
        self.assertEqual(r["refund"], 4000)
        self.assertEqual((self.drawer(), self.balance()), (10000, 0))               # the debt is cleared instead

    def test_mixed_sale_refunds_cash_only_up_to_the_cash_paid(self):
        sale = self.sell(units=4, cash=3000, credit=5000, customer=self.cust)  # 80.00 = 30.00 cash + 50.00 credit
        line = sale["lines"][0]["id"]
        r = self.s.return_items(self.ctx, sale["id"], {line: 1}, refund_method="cash", reason="مرتجع تجربة")
        self.assertEqual(r["refund"], 2000)                                   # 20.00 of the 30.00 cash
        with self.assertRaises(Conflict) as e:                                # 20.00 more > the 10.00 cash left
            self.s.return_items(self.ctx, sale["id"], {line: 1}, refund_method="cash", reason="مرتجع تجربة")
        self.assertEqual(e.exception.details["max_cash"], 1000)
        self.s.return_items(self.ctx, sale["id"], {line: 1}, refund_method="credit", reason="مرتجع تجربة")
        self.assertEqual(self.drawer(), 10000 + 3000 - 2000)
        self.assertEqual(self.balance(), 5000 - 2000)

    def test_cash_sale_refunds_in_full_as_before(self):
        sale = self.sell(units=2)                                             # 40.00 cash
        line = sale["lines"][0]["id"]
        r = self.s.return_items(self.ctx, sale["id"], {line: 2}, refund_method="cash", reason="مرتجع تجربة")
        self.assertEqual((r["refund"], self.drawer()), (4000, 10000))

    def test_card_or_transfer_sale_can_still_be_refunded_in_cash(self):
        """Owner's rule (11/11): money the shop received by card or transfer may go back in cash."""
        sale = self.sell(units=2, cash=0, card=4000)
        r = self.s.return_items(self.ctx, sale["id"], {sale["lines"][0]["id"]: 1}, refund_method="cash",
                                reason="مرتجع تجربة")
        self.assertEqual((r["refund"], self.drawer()), (2000, 10000 - 2000))

    def test_card_plus_credit_sale_refunds_cash_only_up_to_the_card_part(self):
        sale = self.sell(units=4, cash=0, card=3000, credit=5000, customer=self.cust)   # 30 card + 50 credit
        line = sale["lines"][0]["id"]
        self.s.return_items(self.ctx, sale["id"], {line: 1}, refund_method="cash", reason="مرتجع تجربة")  # 20
        with self.assertRaises(Conflict) as e:                                       # 20 more > the 10 left
            self.s.return_items(self.ctx, sale["id"], {line: 1}, refund_method="cash", reason="مرتجع تجربة")
        self.assertEqual(e.exception.details["max_cash"], 1000)
        self.s.return_items(self.ctx, sale["id"], {line: 1}, refund_method="credit", reason="مرتجع تجربة")
        self.assertEqual(self.balance(), 5000 - 2000)                                 # the credit part goes back
        self.assertEqual(self.drawer(), 10000 - 2000)                                 # to the account, not cash

    def test_card_refund_on_a_sale_wholly_on_credit_is_refused(self):
        """Found in the full review 16/11: a refund to the card passed with no cap, so the customer got card
        money the shop never received and still owed the debt."""
        sale = self.sell(units=2, credit=4000, customer=self.cust)
        line = sale["lines"][0]["id"]
        with self.assertRaises(Conflict) as e:
            self.s.return_items(self.ctx, sale["id"], {line: 2}, refund_method="card", reason="مرتجع تجربة")
        self.assertEqual(e.exception.code, "cash_refund_exceeds_paid")
        self.assertEqual((self.balance(), self.db.one("SELECT count(*) FROM sale_returns")[0]), (4000, 0))

    def test_card_sale_still_refunds_to_the_card(self):
        sale = self.sell(units=2, cash=0, card=4000)
        r = self.s.return_items(self.ctx, sale["id"], {sale["lines"][0]["id"]: 2}, refund_method="card",
                                reason="مرتجع تجربة")
        self.assertEqual((r["refund"], self.drawer()), (4000, 10000))


class TestDeliveryCashRefund(Base):
    """#142: a delivery's cash is with the courier until it is handed in; before that the drawer has
    nothing to pay back."""
    def test_cash_refund_waits_for_the_courier_to_hand_in_the_cash(self):
        self.cd.open(self.ctx, 10000)
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=2)                  # 40.00
        d = self.s.set_details(self.ctx, sid, {"kind": "delivery", "delivery": {"phone": "0100"}})
        f = self.s.safety_check(self.ctx, sid)
        r = self.s.complete(self.ctx, sid, payments=[{"method": "cash", "amount": d["total"]}],
                            safety_decisions={i: "ok" for i in range(len(f))})
        line = r["lines"][0]["id"]
        left = lambda: self.db.one("SELECT sum(qty_base - returned_qty) FROM sale_allocations WHERE sale_line_id=?",
                                   (line,))[0]
        before = left()
        with self.assertRaises(Conflict) as e:                                 # courier still holds the cash
            self.s.return_items(self.ctx, sid, {line: 1}, refund_method="cash", reason="مرتجع تجربة")
        self.assertEqual(e.exception.code, "delivery_not_settled")             # final review P1-2: settle first
        self.assertEqual((self.cd.summary(self.ctx)["expected_cash"], left()), (10000, before))   # nothing moved
        self.s.delivery_status(self.ctx, sid, "settled")                         # 40.00 enters the drawer
        out = self.s.return_items(self.ctx, sid, {line: 1}, refund_method="cash", reason="مرتجع تجربة")
        self.assertEqual(out["refund"], 2000)
        self.assertEqual(self.cd.summary(self.ctx)["expected_cash"], 10000 + 4000 - 2000)
