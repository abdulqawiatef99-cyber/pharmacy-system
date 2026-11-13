"""Contracts: agreed discount local/imported applied by itself; manual discounts untouched; owner only."""
from app.catalog import service as cat
from app.core import notify, people
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
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


class TestContractEdits(Base):
    """Editing, stopping and refusals: paths no test reached (coverage 12/11)."""

    def make(self, **kw):
        return k.save(self.db, self.ctx, {"name": "نقابة الأطباء", "discount_local_bp": 1000,
                                          "discount_imported_bp": 500, **kw})[0]["id"]

    def test_new_rates_reach_the_next_sale_and_the_change_is_audited(self):
        cid = self.make()
        cust = self.s.create_customer(self.ctx, name="د. منى")
        k.link_customer(self.db, self.ctx, cust, cid)
        k.save(self.db, self.ctx, {"discount_local_bp": 1500}, cid)
        row = self.db.one("SELECT before_json, after_json FROM audit_log WHERE action='contract.update'")
        self.assertIn('"discount_local_bp": 1000', row["before_json"])
        self.assertIn('"discount_local_bp": 1500', row["after_json"])
        sid = self.s.create(self.ctx, cust)
        self.assertEqual(self.s.add_line(self.ctx, sid, self.pan, units=2)["lines"][0]["discount_bp"], 1500)

    def test_a_stopped_contract_gives_no_discount(self):
        cid = self.make()
        cust = self.s.create_customer(self.ctx, name="د. منى")
        k.link_customer(self.db, self.ctx, cust, cid)
        k.save(self.db, self.ctx, {"is_active": 0}, cid)
        sid = self.s.create(self.ctx, cust)
        line = self.s.add_line(self.ctx, sid, self.pan, units=2)["lines"][0]
        self.assertEqual((line["discount_bp"], line["discount_source"]), (0, None))

    def test_same_name_twice_is_a_conflict(self):
        self.make()
        k.save(self.db, self.ctx, {"name": "شركة النيل"})
        other = next(c for c in k.listing(self.db, self.ctx) if c["name"] == "شركة النيل")
        with self.assertRaises(Conflict):
            self.make()
        with self.assertRaises(Conflict):                                   # renaming onto it too
            k.save(self.db, self.ctx, {"name": "نقابة الأطباء"}, other["id"])

    def test_refusals(self):
        cid = self.make()
        for bad in ({"name": "x"}, {"name": " "}, {"discount_local_bp": -1}, {"discount_imported_bp": 5001},
                    {"discount_local_bp": "1000"}, {"discount_local_bp": 10.5},
                    {"discount_local_bp": True}, {"discount_imported_bp": False}):
            with self.assertRaises(ValidationFailed, msg=bad):
                k.save(self.db, self.ctx, bad, cid)
        self.assertEqual(self.db.one("SELECT discount_local_bp FROM contracts WHERE id=?", (cid,))[0], 1000)
        with self.assertRaises(NotFound):
            k.save(self.db, self.ctx, {"name": "جهة"}, "no-such-id")

    def test_link_and_unlink_and_other_pharmacy(self):
        cid = self.make()
        cust = self.s.create_customer(self.ctx, name="عميل")
        with self.assertRaises(NotFound):
            k.link_customer(self.db, self.ctx, cust, "no-such-contract")
        with self.assertRaises(NotFound):
            k.link_customer(self.db, self.ctx, "no-such-customer", cid)
        for bad in (["x"], {"id": cid}, 7):
            with self.assertRaises(ValidationFailed, msg=bad):           # a JSON list or number is not an id
                k.link_customer(self.db, self.ctx, cust, bad)
        k.link_customer(self.db, self.ctx, cust, cid)
        k.link_customer(self.db, self.ctx, cust, None)
        self.assertIsNone(self.db.one("SELECT contract_id FROM customers WHERE id=?", (cust,))[0])
        _, other = self.second_tenant()
        with self.assertRaises(NotFound):                                   # another pharmacy's contract
            k.report(self.db, other, cid, "2026-01-01", "2026-01-31")

    def test_report_needs_a_period(self):
        cid = self.make()
        for a, b in (("", ""), ("2026-13-01", "2026-12-31"), (None, None)):
            with self.assertRaises(ValidationFailed):
                k.report(self.db, self.ctx, cid, a, b)
