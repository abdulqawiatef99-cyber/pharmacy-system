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
        for bad in (-1, True, 5001, d['total']):
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


class TestKeepChangeFutureTraps(SalesCase):
    """Owner 22/11 «اعمل حساب خوازيق المستقبل»: a real customer only, the drawer and his statement, using it later,
    and a void or a full return gives it back."""

    def setUp(self):
        super().setUp()
        self.cust = self.s.create_customer(self.ctx, name="عميل حقيقي", phone="01000000077")
        self.cd = CashDrawer(self.db)

    def keep(self, cust, kept=100, units=1):
        sid = self.s.create(self.ctx, cust)
        d = self.s.add_line(self.ctx, sid, self.pan, units=units)
        self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid), change_to_credit=kept)
        return sid, d

    def test_not_the_pharmacy_own_account_nor_a_contract_customer(self):
        org = self.db.one("SELECT name FROM organizations WHERE id=?", (self.ctx.org_id,))["name"]
        house = self.s.create_customer(self.ctx, name=org)
        e1 = self.s.create_customer(self.ctx, name="عميل نقدي", notes="كود إي ستوك: 1؛ رقم: 0")
        e12 = self.s.create_customer(self.ctx, name="عميل 12", notes="كود إي ستوك: 12")
        from app.sales import contracts
        contracts.save(self.db, self.ctx, {"name": "نور الحكمة"})
        k = self.db.one("SELECT id FROM contracts WHERE org_id=?", (self.ctx.org_id,))["id"]
        hikma = self.s.create_customer(self.ctx, name="موظف الحكمة")
        contracts.link_customer(self.db, self.ctx, hikma, k)
        for who, code in ((house, "keep_change_house"), (e1, "keep_change_house"), (hikma, "keep_change_contract")):
            with self.assertRaises(ValidationFailed) as e:
                self.keep(who)
            self.assertEqual(e.exception.code, code)
            self.assertEqual(self.s.balance(self.ctx, who), 0)              # nothing written
        self.keep(e12)                                                        # code 12 is a real customer
        self.assertEqual(self.s.balance(self.ctx, e12), -100)

    def test_drawer_has_the_real_cash_and_sales_do_not_include_it(self):
        start = self.cd.summary(self.ctx)
        self.keep(self.cust)                                                  # 19.00, he gave 20.00
        sh = self.cd.summary(self.ctx)
        k0, k1 = start["cash_by_kind"], sh["cash_by_kind"]
        self.assertEqual(k1.get("sale", 0) - k0.get("sale", 0), 1900)         # the sale is 19.00
        self.assertEqual(k1.get("customer_payment", 0) - k0.get("customer_payment", 0), 100)   # on its own line
        self.assertEqual(sh["expected_cash"], start["expected_cash"] + 2000)   # what is really in the drawer

    def test_statement_and_audit_show_it(self):
        from app.sales import customers
        sid, _ = self.keep(self.cust)
        led = customers.profile(self.db, self.ctx, self.cust)["ledger"]
        row = next(r for r in led if r["ref_id"] == sid)
        self.assertEqual((row["amount"], row["kind"]), (-100, "payment"))
        self.assertIn("اتساب رصيد", row["note"])
        a = self.db.one("SELECT after_json FROM audit_log WHERE entity_id=? AND action='sale.complete'", (sid,))
        self.assertIn('"change_to_credit": 100', a["after_json"])

    def test_used_later_up_to_the_balance(self):
        self.keep(self.cust)                                                  # he has 1.00 (no credit limit)
        sid = self.s.create(self.ctx, self.cust)
        d = self.s.add_line(self.ctx, sid, self.pan, units=1)
        from app.core.errors import Conflict
        with self.assertRaises(Conflict):                                     # more than his balance: refused
            self.s.complete(self.ctx, sid, paid_cash=d["total"] - 101, on_credit=101, safety_decisions=self.ack_all(sid))
        self.s.complete(self.ctx, sid, paid_cash=d["total"] - 100, on_credit=100, safety_decisions=self.ack_all(sid))
        self.assertEqual(self.s.balance(self.ctx, self.cust), 0)

    def test_void_gives_it_back(self):
        start = self.cd.summary(self.ctx)["expected_cash"]
        sid, _ = self.keep(self.cust)
        self.s.void(self.ctx, sid, reason="غلط في الفاتورة")
        self.assertEqual(self.cd.summary(self.ctx)["expected_cash"], start)  # all his 20.00 back
        self.assertEqual(self.s.balance(self.ctx, self.cust), 0)
        self.assertEqual(self.s.kept_change(self.ctx, sid), 0)

    def test_full_cash_return_gives_it_back_partial_and_credit_refund_do_not(self):
        start = self.cd.summary(self.ctx)["expected_cash"]
        sid, d = self.keep(self.cust, units=2)                               # 38.00, kept 1.00
        lid = d["lines"][0]["id"]
        self.s.return_items(self.ctx, sid, {lid: 1}, refund_method="cash", reason="test")
        self.assertEqual(self.s.balance(self.ctx, self.cust), -100)          # partial: his credit stays
        self.s.return_items(self.ctx, sid, {lid: 1}, refund_method="cash", reason="test")
        self.assertEqual(self.s.balance(self.ctx, self.cust), 0)             # emptied: the change went back too
        self.assertEqual(self.cd.summary(self.ctx)["expected_cash"], start)
        sid, d = self.keep(self.cust)                                         # refunded to his account: stays
        self.s.return_items(self.ctx, sid, {d["lines"][0]["id"]: 1}, refund_method="credit", reason="test")
        self.assertEqual(self.s.balance(self.ctx, self.cust), -100 - 1900)
