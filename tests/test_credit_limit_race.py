"""#143: two credit sales for the same customer completed at the same moment cannot pass the limit together.

The limit was checked on the preview, before the write transaction, and not again inside it. Synthetic data only.
"""
from app.sales.service import Sales
from tests.test_owner_plan_priority_1_2 import PlanCase


class CreditLimitRace(PlanCase):
    def setUp(self):
        super().setUp()
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})
        self.cust = self.call("POST", "/api/customers", 201, json={"name": "عميل تجربة", "credit_limit": 15000})["id"]

    def balance(self):
        return Sales(self.db).balance(self.ctx, self.cust)

    def test_two_credit_sales_at_once_cannot_pass_the_limit_together(self):
        s1, s2 = self.draft(self.cust), self.draft(self.cust)
        self.line(s1, self.box, packs=1)                         # 100.00
        self.line(s2, self.box, packs=1)                         # 100.00; together 200.00 > 150.00
        sales = Sales(self.db)
        real = sales.safety_check
        fired = []

        def other_cashier_meanwhile(ctx, sid):
            out = real(ctx, sid)
            if sid == s1 and not fired:                          # s1 has passed its limit check already
                fired.append(1)
                Sales(self.db).complete(self.ctx, s2, payments=[{"method": "credit", "amount": 10000}],
                                        safety_decisions={})
            return out
        sales.safety_check = other_cashier_meanwhile
        stock_before = self.qty(self.box)
        with self.assertRaises(Exception) as e:
            sales.complete(self.ctx, s1, payments=[{"method": "credit", "amount": 10000}], safety_decisions={})
        self.assertEqual(getattr(e.exception, "code", None), "credit_limit")
        self.assertEqual(fired, [1])
        self.assertEqual(self.balance(), 10000)                  # only the second sale is on the account
        self.assertEqual(self.call("GET", f"/api/sales/{s1}")["status"], "draft")
        self.assertEqual(self.qty(self.box), stock_before - 3)   # only the second sale's box left the stock

    def test_a_sale_within_the_limit_still_completes(self):
        done = self.sell(self.box, payments=[{"method": "credit", "amount": 10000}], customer=self.cust, packs=1)
        self.assertEqual(done["on_credit"], 10000)
        self.assertEqual(self.balance(), 10000)
        sid = self.draft(self.cust)
        self.line(sid, self.box, packs=1)
        r = self.complete(sid, [{"method": "credit", "amount": 10000}], expected=409)
        self.assertEqual(r["error"]["code"], "credit_limit")
