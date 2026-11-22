"""«خليها رصيد للعميل»: the change a customer leaves is kept as his credit in the same posting as the sale: the
cash stays in the drawer, his account goes down by it, once; refused without a customer, without cash, or too big."""
from app.core.errors import ValidationFailed
from app.db import new_id
from app.sales.cash import CashDrawer
from tests.test_owner_plan_priority_1_2 import PlanCase
from tests.test_sales import SalesCase


class TestKeepChange(SalesCase):
    def setUp(self):
        super().setUp()
        self.cust = new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO customers (id, org_id, name, credit_limit) VALUES (?,?,?,?)",
                            (self.cust, self.ctx.org_id, "عميل تجربة", 0))

    def test_change_kept_as_credit(self):
        cd = CashDrawer(self.db)
        start = cd.summary(self.ctx)["expected_cash"]
        sid = self.s.create(self.ctx, self.cust)
        d = self.s.add_line(self.ctx, sid, self.pan, units=1)                  # 19.00, he gives 20.00
        self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid), change_to_credit=100)
        self.assertEqual(cd.summary(self.ctx)["expected_cash"], start + 2000)  # the whole 20.00 stays in the drawer
        self.assertEqual(self.s.balance(self.ctx, self.cust), -100)          # he has 1.00 with the pharmacy

    def test_refusals(self):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=1)
        with self.assertRaises(ValidationFailed):                              # no customer
            self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid), change_to_credit=100)
        sid = self.s.create(self.ctx, self.cust)
        d = self.s.add_line(self.ctx, sid, self.pan, units=1)
        for bad in (-1, True, 100_001):
            with self.assertRaises(ValidationFailed):
                self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid), change_to_credit=bad)
        self.assertEqual(self.s.balance(self.ctx, self.cust), 0)               # nothing written


class TestKeepChangeAPI(PlanCase):
    def test_retry_keeps_it_once(self):
        cust = self.call("POST", "/api/customers", 201, json={"name": "عميل تجربة"})["id"]
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})
        sid = self.draft(cust)
        d = self.line(sid, self.pan, packs=1)
        body = {"payments": [{"method": "cash", "amount": d["total"]}], "cash_given": d["total"] + 150, "keep_change": True,
                "request_id": "rq-keep-1", "safety_decisions": {}}
        self.call("POST", f"/api/sales/{sid}/complete", json=body)
        self.call("POST", f"/api/sales/{sid}/complete", json=body)                      # a retry
        self.assertEqual(self.call("GET", f"/api/customers/{cust}")["balance"], -150)
