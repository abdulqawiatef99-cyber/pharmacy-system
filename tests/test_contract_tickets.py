"""Ticket on contract sales (hospital / company ticket: name, id, number) and the E-Stock tickets from the
archive in the contract report. Synthetic data only."""
from app.core import people
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.sales import contracts as k
from app.sales.service import local_today
from tests.test_cashier_acceptance import Base
from tests.test_estock_db_import import Base as ImportBase

PW = "Staff-Pass-2026"


class TestTicket(Base):
    def setUp(self):
        super().setUp()
        self.kid = k.save(self.db, self.ctx, {"name": "مستشفى تجربة", "discount_local_bp": 1000})[0]["id"]
        self.cust = self.s.create_customer(self.ctx, name="عميل تعاقد")
        k.link_customer(self.db, self.ctx, self.cust, self.kid)
        self.cd.open(self.ctx, 0)
        people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        self.a = self.login("asst", PW)

    def sale(self, cust):
        sid = self.s.create(self.a, cust)
        self.s.add_line(self.a, sid, self.pan, units=2)
        return sid

    def complete(self, sid):
        d = self.s.get(self.a, sid)
        f = self.s.safety_check(self.a, sid)
        self.s.complete(self.a, sid, paid_cash=d["total"], safety_decisions={i: "ok" for i in range(len(f))})

    def test_ticket_on_a_contract_sale_shows_in_the_report(self):
        sid = self.sale(self.cust)
        got = k.set_ticket(self.db, self.a, sid, {"name": " مستشفى تجربة ", "ref": 77, "number": "T-1"})
        self.assertEqual(got, {"ticket_name": "مستشفى تجربة", "ticket_ref": "77", "ticket_no": "T-1"})
        self.complete(sid)
        day = local_today().isoformat()
        row = k.report(self.db, self.ctx, self.kid, day, day)["rows"][0]
        self.assertEqual((row["ticket_name"], row["ticket_ref"], row["ticket_no"]), ("مستشفى تجربة", "77", "T-1"))
        self.assertEqual(self.db.one("SELECT count(*) n FROM audit_log WHERE action='sale.ticket'")["n"], 1)

    def test_ticket_is_optional_and_only_for_contract_sales(self):
        other = self.s.create_customer(self.ctx, name="عميل عادي")
        sid = self.sale(other)
        with self.assertRaises(ValidationFailed):
            k.set_ticket(self.db, self.a, sid, {"number": "T-2"})
        self.assertEqual(k.set_ticket(self.db, self.a, sid, {}), {"ticket_name": None, "ticket_ref": None, "ticket_no": None})
        for bad in ({"number": True}, {"number": ["x"]}, "T-3"):
            with self.assertRaises(ValidationFailed):
                k.set_ticket(self.db, self.a, self.sale(self.cust), bad)

    def test_after_completion_only_who_manages_contracts_corrects_it(self):
        sid = self.sale(self.cust)
        self.complete(sid)
        with self.assertRaises(Forbidden):
            k.set_ticket(self.db, self.a, sid, {"number": "T-9"})
        k.set_ticket(self.db, self.ctx, sid, {"number": "T-9"})
        self.assertEqual(self.db.one("SELECT ticket_no FROM sales WHERE id=?", (sid,))["ticket_no"], "T-9")

    def test_voided_and_other_pharmacy_refused(self):
        sid = self.sale(self.cust)
        self.complete(sid)
        self.s.void(self.ctx, sid, "فاتورة اتعملت بالغلط")
        with self.assertRaises(Conflict):
            k.set_ticket(self.db, self.ctx, sid, {"number": "T-1"})
        _, other = self.second_tenant()
        with self.assertRaises(NotFound):
            k.set_ticket(self.db, other, sid, {"number": "T-1"})


class TestEstockTickets(ImportBase):
    def test_contract_report_shows_the_estock_invoices_with_their_tickets(self):
        self.assertTrue(self.run_import()["ok"])
        cust = self.db.one("SELECT id FROM customers WHERE name='أحمد تجربة'")["id"]
        kid = k.save(self.db, self.ctx, {"name": "مستشفى تجربة"})[0]["id"]
        self.assertEqual(k.report(self.db, self.ctx, kid, "2026-11-18", "2026-11-18")["estock"], [])   # nobody linked
        k.link_customer(self.db, self.ctx, cust, kid)
        r = k.report(self.db, self.ctx, kid, "2026-11-18", "2026-11-18")
        self.assertEqual(r["rows"], [])
        self.assertEqual([(e["estock_sales_id"], e["ticket_no"], e["total"]) for e in r["estock"]],
                         [("1", None, 5400), ("2", "T-123", -1000)])
        self.assertEqual(r["estock"][1]["ticket_name"], "مستشفى تجربة")
        self.assertEqual(r["totals"]["total"], 0)                 # E-Stock history is never in the program's totals
        self.assertEqual(k.report(self.db, self.ctx, kid, "2026-11-19", "2026-11-30")["estock"], [])
