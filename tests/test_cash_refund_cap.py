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

    def test_card_sale_cannot_be_refunded_in_cash(self):
        sale = self.sell(units=2, cash=0, card=4000)
        with self.assertRaises(Conflict) as e:
            self.s.return_items(self.ctx, sale["id"], {sale["lines"][0]["id"]: 1}, refund_method="cash",
                                reason="مرتجع تجربة")
        self.assertEqual(e.exception.code, "cash_refund_exceeds_paid")
        self.assertEqual(self.drawer(), 10000)
