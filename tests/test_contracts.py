"""Contracts: agreed discount local/imported applied by itself; manual discounts untouched; owner only."""
from app.catalog import service as cat
from app.core import notify, people
from app.core.errors import Forbidden, ValidationFailed
from app.sales import contracts as k
from app.sales.service import local_today
from tests.test_cashier_acceptance import Base

PW = "Staff-Pass-2026"


class TestContracts(Base):
    def test_auto_discount_and_rules(self):
        with self.assertRaises(ValidationFailed):
            k.save(self.db, self.ctx, {"name": "نقابة", "discount_local_bp": 6000})
        cid = k.save(self.db, self.ctx, {"name": "نقابة المهندسين", "discount_local_bp": 1000, "discount_imported_bp": 500})[0]["id"]
        people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        a = self.login("asst", PW)
        with self.assertRaises(Forbidden):
            k.save(self.db, a, {"name": "x y"})
        cust = self.s.create_customer(self.ctx, name="م. أحمد")
        k.link_customer(self.db, self.ctx, cust, cid)
        self.cd.open(self.ctx, 0)
        sid = self.s.create(a)
        self.s.add_line(a, sid, self.pan, units=2)                      # before the customer: no discount
        self.assertEqual(self.s.get(a, sid)["lines"][0]["discount_bp"], 0)
        d = self.s.set_details(a, sid, {"customer_id": cust})
        line = d["lines"][0]
        self.assertEqual((line["discount_bp"], line["discount_source"]), (1000, "contract"))   # local item
        self.assertEqual(d["contract"]["name"], "نقابة المهندسين")
        self.assertEqual(d["total"], 3600)                               # 40 - 10%
        f = self.s.safety_check(a, sid)
        self.s.complete(a, sid, paid_cash=3600, safety_decisions={i: "ok" for i in range(len(f))})
        self.assertNotIn("discount", [n["kind"] for n in notify.listing(self.db, self.ctx)["items"]])   # expected, no alarm
        r = k.report(self.db, self.ctx, cid, local_today().isoformat(), local_today().isoformat())
        self.assertEqual((len(r["rows"]), r["totals"]["discount_total"]), (1, 400))
        # removing the customer removes the contract discount
        sid2 = self.s.create(self.ctx, cust)
        self.s.add_line(self.ctx, sid2, self.pan, units=2)
        self.assertEqual(self.s.get(self.ctx, sid2)["lines"][0]["discount_bp"], 1000)
        self.assertEqual(self.s.set_details(self.ctx, sid2, {"customer_id": None})["lines"][0]["discount_bp"], 0)

    def test_imported_rate_and_manual_kept(self):
        cid = k.save(self.db, self.ctx, {"name": "شركة X", "discount_local_bp": 1000, "discount_imported_bp": 300})[0]["id"]
        cat.update_item(self.db, self.ctx, self.pan, {"is_imported": 1})
        cust = self.s.create_customer(self.ctx, name="عميل")
        k.link_customer(self.db, self.ctx, cust, cid)
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=2)
        self.s.update_line(self.ctx, sid, d["lines"][0]["id"], discount_bp=200)     # typed by hand
        d = self.s.set_details(self.ctx, sid, {"customer_id": cust})
        self.assertEqual((d["lines"][0]["discount_bp"], d["lines"][0]["discount_source"]), (200, None))
        sid2 = self.s.create(self.ctx, cust)
        self.assertEqual(self.s.add_line(self.ctx, sid2, self.pan, units=2)["lines"][0]["discount_bp"], 300)
