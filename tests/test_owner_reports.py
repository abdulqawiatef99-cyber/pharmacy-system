"""Owner reports: one small day of work, then every report must agree with it."""
from datetime import date

from app.catalog import service as cat
from app.core import people
from app.core.errors import Forbidden
from app.inventory import service as inv
from app.reports import owner
from tests.test_api import APICase, H
from tests.test_cashier_acceptance import Base

PW = "Staff-Pass-2026"
D = date.today().isoformat()


class TestOwnerReports(Base):
    def setUp(self):
        super().setUp()
        self.cd.open(self.ctx, 10000)
        self.a = self.sell(units=2)                                           # 40.00 cash, walk-in
        self.cust = self.s.create_customer(self.ctx, name="أحمد", credit_limit=100000)
        self.b = self.sell(units=2, credit=4000, customer=self.cust)          # 40.00 on credit
        sid = self.s.create(self.ctx)
        self.s.add_line(self.ctx, sid, self.pan, units=2, discount_bp=1000)   # 10% off 40.00 = 4.00
        f = self.s.safety_check(self.ctx, sid)
        self.c = self.s.complete(self.ctx, sid, paid_cash=3600, safety_decisions={i: "ok" for i in range(len(f))})
        self.s.return_items(self.ctx, self.a["id"], {self.a["lines"][0]["id"]: 1}, refund_method="cash", reason="مش محتاجه")
        v = self.sell(units=2)
        self.s.void(self.ctx, v["id"], "غلط في الفاتورة")
        self.cd.close(self.ctx, counted_cash=10000 + 4000 + 3600 - 2000 - 500, note="ناقص خمسة")   # 5.00 short

    def test_customers_staff_returns_discounts_shifts(self):
        c = owner.customer_sales(self.db, self.ctx, D, D)
        self.assertEqual([(r["name"], r["n"], r["total"], r["credit"]) for r in c["rows"]], [("أحمد", 1, 4000, 4000)])
        self.assertEqual(c["rows"][0]["balance"], 4000)
        self.assertEqual((c["walk_in"]["n"], c["walk_in"]["total"], c["walk_in"]["returns"]), (2, 7600, 2000))
        self.assertEqual(c["totals"]["net"], 4000 + 7600 - 2000)

        st = owner.staff_performance(self.db, self.ctx, D, D)["rows"]
        me = st[0]
        self.assertEqual((me["n"], me["total"], me["discount"], me["returns_n"], me["returns"], me["voids_n"]), (3, 11600, 400, 1, 2000, 1))
        self.assertEqual((me["shifts"], me["short_n"], me["difference"]), (1, 1, -500))
        self.assertEqual(me["discount_bp"], round(400 * 10000 / 12000))

        r = owner.returns_report(self.db, self.ctx, D, D)
        self.assertEqual(r["totals"]["total"], 2000)
        self.assertEqual(r["rows"][0]["items"][0]["qty"], 1)
        self.assertEqual(r["rows"][0]["method_label"], "نقدي")
        self.assertFalse(r["rows"][0]["late"])

        d = owner.discounts_report(self.db, self.ctx, D, D)
        self.assertEqual((d["totals"]["gross"], d["totals"]["discount"], d["totals"]["n_disc"]), (12000, 400, 1))
        self.assertEqual(sum(g["discount"] for g in d["by_who"]), d["totals"]["discount"])     # lines agree with invoices
        self.assertEqual(d["rows"][0]["discount_bp"], 1000)

        sh = owner.shift_closings(self.db, self.ctx, D, D)
        self.assertEqual((sh["totals"]["n"], sh["totals"]["short"], sh["rows"][0]["difference"]), (1, -500, -500))
        self.assertEqual(sh["rows"][0]["sales_n"], 3)

    def test_prices_units_unsold(self):
        cat.update_item(self.db, self.ctx, self.pan, {"public_price": 4400})
        cat.update_item(self.db, self.ctx, self.pan, {"pack_unit_name": "علبة", "base_unit_name": "شريط"})
        p = owner.price_changes(self.db, self.ctx, D, D)
        self.assertEqual([(r["old"], r["new"], r["change_bp"], r["source_label"]) for r in p["rows"]], [(4000, 4400, 1000, "يدوي")])
        self.assertTrue(p["rows"][0]["by_name"])
        u = owner.unit_changes(self.db, self.ctx, D, D)
        self.assertEqual({c["field"] for c in u["rows"][0]["changes"]}, {"pack_unit_name", "base_unit_name"})
        x = cat.create_item(self.db, self.ctx, {"name_en": "Never Sold 5mg", "pack_size": 1, "public_price": 1000}, force=True)
        from decimal import Decimal
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, x, "N1", "2031-01-31")
            inv.post_movement(self.db, self.ctx, item_id=x, qty_delta=3, batch_id=b, movement_type="purchase", unit_cost=Decimal("7"))
        un = owner.unsold_items(self.db, self.ctx, D, D)
        self.assertEqual([r["item_id"] for r in un["rows"]], [x])                     # the sold item is not here
        self.assertEqual((un["rows"][0]["cost_value"], un["rows"][0]["price_value"]), (2100, 3000))

    def test_permissions(self):
        people.add_user(self.db, self.ctx, username="asst1", full_name="A", password=PW, role_code="assistant")
        a = self.login("asst1", PW)
        for fn in owner.REPORTS.values():
            with self.assertRaises(Forbidden):
                fn(self.db, a, D, D)
        people.add_user(self.db, self.ctx, username="pharm1", full_name="P", password=PW, role_code="pharmacist")
        ph = self.login("pharm1", PW)
        with self.assertRaises(Forbidden):
            owner.shift_closings(self.db, ph, D, D)


class TestApi(APICase):
    def test_route(self):
        self.http_login()
        for k in owner.REPORTS:
            r = self.c.get(f"/api/reports/owner/{k}?from={D}&to={D}", headers=H)
            self.assertEqual(r.status_code, 200, (k, r.text))
        self.assertEqual(self.c.get(f"/api/reports/owner/nope?from={D}&to={D}", headers=H).status_code, 404)
