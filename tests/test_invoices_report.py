"""Advanced sales-invoices report: filters, columns and totals agree with the invoices."""
from datetime import date, datetime, timedelta

from app.catalog import service as cat
from app.core import people
from app.core.errors import Forbidden, ValidationFailed
from app.reports import invoices
from tests.test_api import APICase, H
from tests.test_cashier_acceptance import Base

PW = "Staff-Pass-2026"
D = date.today().isoformat()


class TestInvoices(Base):
    def setUp(self):
        super().setUp()
        self.cd.open(self.ctx, 0)
        self.a = self.sell(units=2)                                            # cash 40.00, cost 2 x 15 = 30
        self.cust = self.s.create_customer(self.ctx, name="منى سالم", phone="01012345678", credit_limit=100000)
        self.b = self.sell(units=2, credit=4000, customer=self.cust)           # credit 40.00
        sid = self.s.create(self.ctx)
        self.s.add_line(self.ctx, sid, self.pan, units=2, discount_bp=1000)    # 10% item discount
        f = self.s.safety_check(self.ctx, sid)
        self.c = self.s.complete(self.ctx, sid, paid_cash=0, paid_card=3600, safety_decisions={i: "ok" for i in range(len(f))})
        self.s.return_items(self.ctx, self.a["id"], {self.a["lines"][0]["id"]: 1}, refund_method="cash", reason="مش محتاجه")
        v = self.sell(units=2)
        self.s.void(self.ctx, v["id"], "غلط في الفاتورة")
        self.other = cat.create_item(self.db, self.ctx, {"name_en": "Other 5mg", "public_price": 500}, force=True)

    def rep(self, **kw):
        return invoices.report(self.db, self.ctx, date_from=kw.pop("f", D), date_to=kw.pop("t", D), **kw)

    def test_columns_and_totals(self):
        r = self.rep()
        self.assertEqual(r["totals"]["n"], 3)                                  # voided not mixed in
        t = r["totals"]
        self.assertEqual((t["subtotal"], t["item_discount"], t["total"], t["returned_total"], t["net"]), (12000, 400, 11600, 2000, 9600))
        self.assertEqual((t["paid_cash"], t["paid_card"], t["on_credit"]), (4000, 3600, 4000))
        a = next(x for x in r["rows"] if x["id"] == self.a["id"])
        self.assertEqual((a["net"], a["profit"]), (2000, 2000 - 1500))         # kept 1 unit: 20.00 - 15.00
        c = next(x for x in r["rows"] if x["id"] == self.c["id"])
        self.assertEqual((c["item_discount"], c["profit"]), (400, 3600 - 3000))
        self.assertEqual(t["profit"], 500 + 1000 + 600)

    def test_filters(self):
        ids = lambda **kw: {x["id"] for x in self.rep(**kw)["rows"]}
        self.assertEqual(ids(types=["credit"]), {self.b["id"]})
        self.assertEqual(ids(types=["card", "credit"]), {self.b["id"], self.c["id"]})        # any of the kinds
        self.assertEqual(ids(types=["returned"]), {self.a["id"]})
        self.assertEqual(ids(types=["discounted"]), {self.c["id"]})
        self.assertEqual(len(ids(types=["voided"])), 1)
        self.assertEqual(ids(q="منى"), {self.b["id"]})
        self.assertEqual(ids(q="0101234"), {self.b["id"]})
        self.assertEqual(ids(q=str(self.c["sale_no"])), {self.c["id"]})
        self.assertEqual(ids(item_id=self.other), set())
        self.assertEqual(len(ids(item_id=self.pan)), 3)
        self.assertEqual(ids(q="100%_"), set())                                # LIKE wildcards are literal
        later = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
        self.assertEqual(ids(f=later, t=later[:10]), set())                    # from a time after every sale
        # (t=later's own date: near midnight "now + 1h" is already tomorrow, so t=today would be before f)
        sid = self.s.create(self.ctx)
        self.s.add_line(self.ctx, sid, self.pan, units=1)
        self.assertEqual(ids(types=["draft"]), {sid})                          # open (held) invoices

    def test_lines_and_permissions(self):
        r = self.rep(with_lines=True)
        a = next(x for x in r["rows"] if x["id"] == self.a["id"])
        self.assertEqual((a["items"][0]["qty"], a["items"][0]["returned"], a["items"][0]["net"]), (2, 1, 4000))
        with self.assertRaises(ValidationFailed):
            self.rep(f=D, t="2020-01-01")
        people.add_user(self.db, self.ctx, username="pharm1", full_name="P", password=PW, role_code="pharmacist")
        with self.assertRaises(Forbidden):
            invoices.report(self.db, self.login("pharm1", PW), date_from=D, date_to=D)
        people.add_user(self.db, self.ctx, username="mgr1", full_name="M", password=PW, role_code="manager")
        m = invoices.report(self.db, self.login("mgr1", PW), date_from=D, date_to=D)
        self.assertNotIn("profit", m["rows"][0])                               # no cost numbers without costs.read
        self.assertNotIn("profit", m["totals"])


class TestApi(APICase):
    def test_route(self):
        self.http_login()
        r = self.c.get(f"/api/reports/invoices?from={D}T00:00&to={D}T23:59&types=cash,credit&lines=1", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("sellers", r.json())
