"""Customers: profile, credit, monthly orders prepared for the cashier."""
from datetime import timedelta

from app.core.errors import Conflict
from app.sales import customers as cu
from app.sales.service import Sales
from tests.test_api import APICase, H
from tests.test_cashier_acceptance import Base, T


class TestCustomers(Base):
    def setUp(self):
        super().setUp()
        self.cd.open(self.ctx, 0)
        self.cid = self.s.create_customer(self.ctx, name="أ. محمود", phone="0100", credit_limit=50000)

    def test_profile_credit_and_consent(self):
        self.sell(credit=4000, customer=self.cid)
        p = cu.profile(self.db, self.ctx, self.cid)
        self.assertEqual(p["balance"], 4000)
        self.assertEqual([r["name"] for r in cu.listing(self.db, self.ctx, only="debt")], ["أ. محمود"])
        p = cu.update(self.db, self.ctx, self.cid, {"whatsapp_ok": True, "credit_limit": 100000})
        self.assertEqual((p["whatsapp_ok"], p["credit_limit"]), (1, 100000))

    def test_monthly_order_prepare_and_restart(self):
        cu.save_refill(self.db, self.ctx, self.cid, self.pan, 2, 30)
        p = cu.profile(self.db, self.ctx, self.cid)
        self.assertEqual(p["refills"][0]["days_left"], 30)
        self.assertTrue(p["refills"][0]["in_stock"])
        sid = cu.prepare_refill(self.db, self.ctx, self.cid)
        d = self.s.get(self.ctx, sid)
        self.assertEqual((d["customer_id"], d["lines"][0]["qty_base"]), (self.cid, 2))
        f = self.s.safety_check(self.ctx, sid)
        self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions={i: "ok" for i in range(len(f))})
        later = cu.profile(self.db, self.ctx, self.cid, today=T + timedelta(days=27))
        self.assertEqual(later["refills"][0]["days_left"], 3)
        self.assertEqual([r["name"] for r in cu.listing(self.db, self.ctx, only="refill", today=T + timedelta(days=27))], ["أ. محمود"])
        cu.remove_refill(self.db, self.ctx, self.cid, self.pan)
        with self.assertRaises(Exception):
            cu.prepare_refill(self.db, self.ctx, self.cid)

    def test_suggest_from_3_months(self):
        for i, days in enumerate((0, 35, 70)):
            sid = self.s.create(self.ctx, self.cid)
            d = self.s.add_line(self.ctx, sid, self.pan, units=1)
            f = self.s.safety_check(self.ctx, sid)
            self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions={i: "ok" for i in range(len(f))})
            with self.db.transaction():
                self.db.execute("UPDATE sales SET completed_at=? WHERE id=?",
                                ((T - timedelta(days=days)).isoformat() + "T10:00:00.000000Z", sid))
        self.assertEqual(cu.profile(self.db, self.ctx, self.cid)["suggest_refills"][0]["item_id"], self.pan)


class TestCustomersAPI(APICase):
    def test_list_profile(self):
        self.http_login()
        cid = self.c.post("/api/customers", json={"name": "أم يوسف", "phone": "0111"}, headers=H).json()["id"]
        self.assertEqual(len(self.c.get("/api/customers/all").json()["customers"]), 1)
        r = self.c.patch(f"/api/customers/{cid}", json={"notes": "ضغط"}, headers=H)
        self.assertEqual(r.json()["notes"], "ضغط")
