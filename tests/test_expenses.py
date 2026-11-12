"""Expenses with approval, manager limit, drawer, profit report."""
from datetime import timedelta

from app.core import accounts, notify, people
from app.core.errors import Forbidden, ValidationFailed
from app.finance import expenses as fin
from tests.test_api import APICase, H
from tests.test_cashier_acceptance import Base, T
from tests.helpers import today

PW = "Staff-Pass-2026"


class TestExpenses(Base):
    def setUp(self):
        super().setUp()
        fin.ensure_defaults(self.db)
        self.cat = fin.categories(self.db, self.ctx)[0]["id"]
        self.cd.open(self.ctx, 100000)
        people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        people.add_user(self.db, self.ctx, username="mgr", full_name="M", password=PW, role_code="manager", pin="2468")
        self.a, self.m = self.login("asst", PW), self.login("mgr", PW)

    def test_pending_until_approved_and_drawer(self):
        e = fin.record(self.db, self.a, category_id=self.cat, amount=3000, paid_from="drawer", note="أكياس")
        self.assertEqual(e["status"], "pending")
        self.assertEqual(self.cd.summary(self.ctx)["expected_cash"], 97000)      # physically left the drawer
        self.assertEqual(notify.listing(self.db, self.ctx)["items"][0]["kind"], "expense_pending")
        with self.assertRaises(Forbidden):
            fin.decide(self.db, self.a, e["id"], True)
        self.assertEqual(fin.decide(self.db, self.m, e["id"], True)["status"], "approved")   # within the 500 limit

    def test_manager_limit_and_reject(self):
        e = fin.record(self.db, self.a, category_id=self.cat, amount=80000, paid_from="outside")
        with self.assertRaises(Forbidden):
            fin.decide(self.db, self.m, e["id"], True)
        with self.assertRaises(ValidationFailed):
            fin.decide(self.db, self.ctx, e["id"], False)
        self.assertEqual(fin.decide(self.db, self.ctx, e["id"], False, "مش مصروف صيدلية")["status"], "rejected")
        own = fin.record(self.db, self.ctx, category_id=self.cat, amount=200000, paid_from="outside")
        self.assertEqual(own["status"], "approved")                              # the owner's own: no waiting
        self.assertEqual(len(fin.listing(self.db, self.a)), 1)                   # employees see their own

    def test_profit(self):
        self.sell(units=4)                                                       # 80.00, cost 4 x 15 = 60
        fin.record(self.db, self.ctx, category_id=self.cat, amount=1500, paid_from="outside")
        fin.record(self.db, self.a, category_id=self.cat, amount=700, paid_from="outside")   # pending: not counted
        r = fin.profit(self.db, self.ctx, today(), today())
        self.assertEqual((r["revenue"], r["cost"], r["gross_profit"], r["expenses_total"], r["net_profit"]),
                         (8000, 6000, 2000, 1500, 500))
        self.assertEqual(r["pending_expenses"]["n"], 1)
        with self.assertRaises(Forbidden):
            fin.profit(self.db, self.m, today(), today())

    def test_a_drawer_expense_rejected_after_payout_alerts_the_owner(self):
        # the cash already left the drawer: rejecting it changes nothing in the drawer, it raises an error alert
        e = fin.record(self.db, self.a, category_id=self.cat, amount=3000, paid_from="drawer", note="تجربة")
        fin.decide(self.db, self.m, e["id"], False, "مش مصروف صيدلية")
        self.assertEqual(self.cd.summary(self.ctx)["expected_cash"], 97000)
        alert = [n for n in notify.listing(self.db, self.ctx)["items"] if n["kind"] == "expense_rejected"]
        self.assertEqual(len(alert), 1)
        self.assertEqual(alert[0]["severity"], "error")
        self.assertIn("30.00", alert[0]["title"])
        self.assertIn("مش مصروف صيدلية", alert[0]["body"])
        o = fin.record(self.db, self.a, category_id=self.cat, amount=1000, paid_from="outside")
        fin.decide(self.db, self.m, o["id"], False, "مكرر")                     # not from the drawer: no alert
        self.assertEqual(len([n for n in notify.listing(self.db, self.ctx)["items"] if n["kind"] == "expense_rejected"]), 1)
        r = fin.profit(self.db, self.ctx, today(), today())
        self.assertEqual(r["expenses_total"], 0)                                 # rejected never counts as an expense

    def test_listing_filters(self):
        day = T.isoformat()[:10]
        old = (T - timedelta(days=40)).isoformat()[:10]
        a = fin.record(self.db, self.ctx, category_id=self.cat, amount=100, paid_from="outside", spent_on=old)
        b = fin.record(self.db, self.a, category_id=self.cat, amount=200, paid_from="outside", spent_on=day)
        ids = lambda **kw: {x["id"] for x in fin.listing(self.db, self.ctx, **kw)}       # noqa: E731
        self.assertEqual(ids(status="pending"), {b["id"]})
        self.assertEqual(ids(status="approved"), {a["id"]})
        self.assertEqual(ids(month=old[:7]), {a["id"]})
        self.assertEqual(ids(date_from=day, date_to=day), {b["id"]})
        self.assertEqual(ids(date_from=old, date_to=day), {a["id"], b["id"]})
        self.assertEqual({x["id"] for x in fin.listing(self.db, self.a)}, {b["id"]})   # employees: their own only
        with self.assertRaises(ValidationFailed):
            fin.record(self.db, self.ctx, category_id=self.cat, amount=100, paid_from="outside", spent_on="2026-13-01")


class TestExpensesAPI(APICase):
    def test_flow(self):
        fin.ensure_defaults(self.db)
        self.http_login()
        r = self.c.get("/api/expenses").json()
        cat = r["categories"][0]["id"]
        r = self.c.post("/api/expenses", json={"category_id": cat, "amount": 1000, "paid_from": "outside"}, headers=H)
        self.assertEqual(r.status_code, 201, r.text)
        d = today()
        self.assertEqual(self.c.get("/api/reports/profit", params={"from": d, "to": d}).json()["expenses_total"], 1000)
