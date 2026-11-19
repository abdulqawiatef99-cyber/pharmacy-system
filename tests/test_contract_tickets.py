"""Ticket on contract sales (hospital / company ticket: name, id, number) and the E-Stock tickets from the
archive in the contract report. Synthetic data only."""
from app.core import people
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.sales import contracts as k
from app.sales.service import local_today
from tests.test_cashier_acceptance import Base
from tests.test_estock_db_import import Base as ImportBase

PW = "Staff-Pass-2026"


class _TicketBase(Base):
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


class TestTicket(_TicketBase):

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


class TestCodexReview340(_TicketBase):
    """Codex on #340 @a94f419 (each failed before the fix)."""

    def test_p1_a_cashier_cannot_change_another_cashiers_draft_ticket(self):
        people.add_user(self.db, self.ctx, username="asst2", full_name="B", password=PW, role_code="assistant")
        b = self.login("asst2", PW)
        sid = self.sale(self.cust)                        # asst's draft
        with self.assertRaises(Forbidden):
            k.set_ticket(self.db, b, sid, {"number": "T-X"})
        self.assertIsNone(self.db.one("SELECT ticket_no FROM sales WHERE id=?", (sid,))["ticket_no"])
        k.set_ticket(self.db, self.ctx, sid, {"number": "T-OK"})      # the owner may (sales.totals)

    def test_p1_the_ticket_update_filters_by_organization(self):
        import inspect
        src = inspect.getsource(k.set_ticket)
        upd = src[src.index("UPDATE sales SET ticket_name"):].split(")", 2)[0]
        self.assertIn("org_id=?", upd)


class TestOneClaimAcrossTheMove(ImportBase):
    """Owner 18/11: the November Hikma claim spans E-Stock (1/11 to launch) and the program (launch to 30/11)."""

    def test_claim_lines_from_the_archive_and_the_program_with_tickets(self):
        from tests.test_estock_db_import import base_tables
        from app.sales.cash import CashDrawer
        from app.sales.service import Sales
        t = base_tables()
        d = t["Branches_sales_details"]
        d[0].update({"buy_price": "24.0000", "total_sell": "30.0000"})
        d[1].update({"buy_price": "140.0000", "total_sell": "35.0000", "back_amount": "0.5", "back_price": "70.0000"})
        d.append({"branch_id": "3", "details_id": "3", "sales_id": "2", "product_id": "1", "amount": "2",
                  "sell_price": "30.0000", "buy_price": "24.0000", "total_sell": "60.0000", "back_amount": "1",
                  "back_price": "30.0000", "insert_date": "2026-11-18T11:00:00.000"})
        self.assertTrue(self.run_import(tables=t)["ok"])
        cust = self.db.one("SELECT id FROM customers WHERE name='أحمد تجربة'")["id"]
        kid = k.save(self.db, self.ctx, {"name": "مستشفى تجربة"})[0]["id"]
        k.link_customer(self.db, self.ctx, cust, kid)
        s = Sales(self.db)
        CashDrawer(self.db).open(self.ctx, 0)
        sid = s.create(self.ctx, cust)
        s.add_line(self.ctx, sid, self.item("100")["id"], units=3)
        k.set_ticket(self.db, self.ctx, sid, {"name": "مستشفى تجربة", "number": "P-9"})
        tot = s.get(self.ctx, sid)["total"]
        f = s.safety_check(self.ctx, sid)
        s.complete(self.ctx, sid, paid_cash=tot, safety_decisions={i: "ok" for i in range(len(f))})
        c = k.claim(self.db, self.ctx, kid, "2026-11-01", "2099-12-31")
        got = [(r["source"], r["invoice"], r["ticket_no"], r["item_code"], r["qty"], r["sale"], r["cost"]) for r in c["lines"]]
        # invoice 1's pack of item 100 came back in E-Stock's return documents: not claimed (Codex on #381)
        self.assertNotIn(("estock", "1", None, "100", "1", "30.00", "24.00"), got)
        self.assertIn(("estock", "2", "T-123", "100", "1", "30.00", "24.00"), got)
        self.assertNotIn("102", [g[3] for g in got])                     # fully returned in E-Stock
        prog = [g for g in got if g[0] == "program"]
        self.assertEqual(len(prog), 1)
        self.assertEqual((prog[0][2], prog[0][3], prog[0][4]), ("P-9", "100", "3"))
        self.assertEqual(prog[0][5], f"{tot / 100:.2f}")
        self.assertEqual(prog[0][6], "24.00")                            # 3 units x 8.00 (E-Stock cost per unit)
        self.assertEqual(c["sources"], ["estock", "program"])
        shelf = c["totals"][cust]["stores"]["الصيدلية"]                    # E-Stock's «الصيدليه» + the program: one store
        self.assertEqual(shelf["lines"], 2)
        self.assertEqual(shelf["sale"], f"{30 + tot / 100:.2f}")
        self.assertEqual(shelf["cost"], "48.00")

    def test_cost_needs_costs_read(self):
        from app.core import people
        self.assertTrue(self.run_import()["ok"])
        kid = k.save(self.db, self.ctx, {"name": "مستشفى تجربة"})[0]["id"]
        people.add_user(self.db, self.ctx, username="mgr", full_name="M", password=PW, role_code="manager")
        people.set_exception(self.db, self.ctx, self.db.one("SELECT id FROM users WHERE username='mgr'")["id"],
                             "costs.read", "deny")
        c = k.claim(self.db, self.login("mgr", PW), kid, "2026-11-01", "2026-11-30")
        self.assertFalse(c["show_cost"])
        self.assertTrue(all("cost" not in r for r in c["lines"]))


class TestCodexReview340Claim(ImportBase):
    """Codex on #340 @783e668 (each failed before the fix)."""

    def setUp(self):
        super().setUp()
        self.assertTrue(self.run_import()["ok"])
        self.kid = k.save(self.db, self.ctx, {"name": "مستشفى تجربة"})[0]["id"]

    def sell(self, cust, item, units, cost):
        from decimal import Decimal
        from app.inventory import service as inv
        from app.sales.cash import CashDrawer
        from app.sales.service import Sales
        s = Sales(self.db)
        if not self.db.one("SELECT 1 FROM cash_shifts WHERE status='open'"):
            CashDrawer(self.db).open(self.ctx, 0)
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, item, "C1", "2030-01-01")
            inv.post_movement(self.db, self.ctx, item_id=item, qty_delta=units, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal(cost))
        sid = s.create(self.ctx, cust)
        s.add_line(self.ctx, sid, item, units=units)
        tot = s.get(self.ctx, sid)["total"]
        f = s.safety_check(self.ctx, sid)
        s.complete(self.ctx, sid, paid_cash=tot, safety_decisions={i: "ok" for i in range(len(f))})

    def test_p1_costs_are_summed_exactly_then_rounded_once(self):
        from app.catalog import service as cat
        cust = self.s_customer("عميل تكلفة")
        item = cat.create_item(self.db, self.ctx, {"name_en": "Third Tab", "pack_size": 1, "public_price": 100})
        self.sell(cust, item, 2, "0.333333")
        self.sell(cust, item, 2, "0.333333")
        c = k.claim(self.db, self.ctx, self.kid, "2026-01-01", "2099-12-31")
        self.assertEqual(c["totals"][cust]["stores"]["الصيدلية"]["cost"], "1.33")      # 4 x 0.333333, rounded once

    def test_p2_two_customers_with_one_name_stay_apart(self):
        from app.catalog import service as cat
        a, b = self.s_customer("محمد"), self.s_customer("محمد")
        item = cat.create_item(self.db, self.ctx, {"name_en": "Name Tab", "pack_size": 1, "public_price": 100})
        self.sell(a, item, 1, "1")
        self.sell(b, item, 1, "1")
        c = k.claim(self.db, self.ctx, self.kid, "2026-01-01", "2099-12-31")
        self.assertEqual(set(c["totals"]), {a, b})
        self.assertEqual(c["totals"][a]["name"], "محمد")
        self.assertEqual({l["customer_id"] for l in c["lines"]}, {a, b})

    def test_p1_the_allocation_read_is_scoped_by_organization(self):
        import inspect
        src = inspect.getsource(k.claim)
        q = src[src.index("FROM sale_allocations a"):].split("history", 1)[0]
        self.assertIn("pl.org_id=?", q)

    def s_customer(self, name):
        from app.sales.service import Sales
        cid = Sales(self.db).create_customer(self.ctx, name=name)
        k.link_customer(self.db, self.ctx, cid, self.kid)
        return cid


class TestCodexReview340c(ImportBase):
    def test_p2_a_negative_archived_buy_price_is_an_unknown_cost(self):
        from tests.test_estock_db_import import base_tables
        t = base_tables()
        t["Branches_sales_details"][0].update({"buy_price": "-24.0000", "total_sell": "30.0000"})
        t["Back_Sales_details"] = []                      # the line stays sold (no return document)
        self.assertTrue(self.run_import(tables=t)["ok"])
        cust = self.db.one("SELECT id FROM customers WHERE name='أحمد تجربة'")["id"]
        kid = k.save(self.db, self.ctx, {"name": "مستشفى تجربة"})[0]["id"]
        k.link_customer(self.db, self.ctx, cust, kid)
        c = k.claim(self.db, self.ctx, kid, "2026-11-01", "2026-11-30")
        line = [r for r in c["lines"] if r["item_code"] == "100"][0]
        self.assertIsNone(line["cost"])
        shelf = c["totals"][cust]["stores"]["الصيدلية"]
        self.assertGreaterEqual(shelf["cost_unknown"], 1)
        self.assertFalse(shelf["cost"].startswith("-"))


class TestArchiveBoundToItsPharmacy(ImportBase):
    """The contract report and claim read the E-Stock archive through estock_archive.open_for (#345 review):
    only the pharmacy it was imported for, and only this branch's lines."""

    def _contract(self):
        cust = self.db.one("SELECT id FROM customers WHERE name='أحمد تجربة'")["id"]
        kid = k.save(self.db, self.ctx, {"name": "مستشفى تجربة"})[0]["id"]
        k.link_customer(self.db, self.ctx, cust, kid)
        return kid

    def test_an_archive_of_another_pharmacy_is_not_read(self):
        import sqlite3
        from contextlib import closing
        from pathlib import Path
        from app.dataimport import estock_archive
        self.assertTrue(self.run_import()["ok"])
        kid = self._contract()
        self.assertTrue(k.report(self.db, self.ctx, kid, "2026-11-18", "2026-11-18")["estock"])
        with closing(sqlite3.connect(Path(self.db.path).parent / estock_archive.NAME)) as con, con:
            con.execute("UPDATE meta SET value='another-org' WHERE key='org_id'")
        self.assertEqual(k.report(self.db, self.ctx, kid, "2026-11-18", "2026-11-18")["estock"], [])
        self.assertEqual([r for r in k.claim(self.db, self.ctx, kid, "2026-11-01", "2026-11-30")["lines"]
                          if r["source"] == "estock"], [])

    def test_the_claim_takes_only_this_branchs_lines(self):
        from tests.test_estock_db_import import base_tables
        t = base_tables()
        t["co_inf"] = [{"branch_id": "3", "co_name": "x"}]
        t["Branches_sales_details"].append({"branch_id": "2", "details_id": "1", "sales_id": "1", "product_id": "1",
                                            "amount": "99", "sell_price": "30.0000", "total_sell": "2970.0000",
                                            "insert_date": "2026-11-18T10:00:00.000"})
        self.assertTrue(self.run_import(tables=t)["ok"])
        kid = self._contract()
        qty = [r["qty"] for r in k.claim(self.db, self.ctx, kid, "2026-11-01", "2026-11-30")["lines"]
               if r["source"] == "estock" and r["item_code"] == "100"]
        self.assertNotIn("99", qty)


class TestClaimLocalDay(_TicketBase):
    """Codex on #340 @a5985e2: a program sale's claim day is its local (Cairo) day, not its UTC day."""

    def setUp(self):
        import os
        import time
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "Africa/Cairo"
        time.tzset()
        super().setUp()

    def tearDown(self):
        import os
        import time
        super().tearDown()
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        time.tzset()

    def test_p2_a_sale_just_after_midnight_is_dated_its_local_day(self):
        sid = self.sale(self.cust)
        self.complete(sid)
        with self.db.transaction():                     # 00:30 on 16 July 2026 in Cairo (UTC+3)
            self.db.execute("UPDATE sales SET completed_at='2026-07-15T21:30:00.000000Z' WHERE id=?", (sid,))
        lines = k.claim(self.db, self.ctx, self.kid, "2026-07-16", "2026-07-16")["lines"]
        self.assertEqual([r["day"] for r in lines if r["source"] == "program"], ["2026-07-16"])


class TestCodexReview381Claim(ImportBase):
    """Codex on #381 (#340's claim): an E-Stock return kept only in its return documents (Back_Sales_details,
    not the line's back_amount) is taken off the claim once, as the old-invoice return counts it."""

    def _claim(self, t):
        self.assertTrue(self.run_import(tables=t)["ok"])
        cust = self.db.one("SELECT id FROM customers WHERE name='أحمد تجربة'")["id"]
        kid = k.save(self.db, self.ctx, {"name": "مستشفى تجربة"})[0]["id"]
        k.link_customer(self.db, self.ctx, cust, kid)
        return k.claim(self.db, self.ctx, kid, "2026-11-01", "2026-11-30")

    def test_p1_a_document_only_return_is_not_claimed(self):
        from tests.test_estock_db_import import base_tables
        t = base_tables()                       # invoice 1: one pack of item 100, returned in Back_Sales_details only
        lines = [r for r in self._claim(t)["lines"] if r["source"] == "estock" and r["invoice"] == "1"]
        self.assertNotIn("100", [r["item_code"] for r in lines])

    def test_p1_counted_once_across_two_lines_of_the_same_item(self):
        from tests.test_estock_db_import import base_tables
        t = base_tables()
        d = t["Branches_sales_details"]
        d.append(dict(d[0], details_id="9", amount="1", total_sell="30.0000"))   # a second pack of item 100
        lines = [r for r in self._claim(t)["lines"] if r["source"] == "estock" and r["item_code"] == "100"
                 and r["invoice"] == "1"]
        self.assertEqual([r["qty"] for r in lines], ["1"])                       # 2 sold, 1 returned: 1 claimed


class TestCodexReview340d(TestCodexReview381Claim):
    """Codex on #340 @9bf8793 (each failed before the fix)."""

    def test_p1_a_program_return_on_an_old_invoice_is_not_claimed(self):
        from app.sales import estock_returns as er
        from app.sales.cash import CashDrawer
        from tests.test_estock_db_import import base_tables
        t = base_tables()
        t["Back_Sales_details"], t["Back_sales_header"] = [], []
        self.assertTrue(self.run_import(tables=t)["ok"])
        cust = self.db.one("SELECT id FROM customers WHERE name='أحمد تجربة'")["id"]
        kid = k.save(self.db, self.ctx, {"name": "مستشفى تجربة"})[0]["id"]
        k.link_customer(self.db, self.ctx, cust, kid)
        before = [r for r in k.claim(self.db, self.ctx, kid, "2026-11-01", "2026-11-30")["lines"]
                  if r["source"] == "estock" and r["invoice"] == "1" and r["item_code"] == "100"]
        self.assertEqual([r["qty"] for r in before], ["1"])
        CashDrawer(self.db).open(self.ctx, 0)
        er.return_items(self.db, self.ctx, "1", {"1": 3}, reason="مرتجع عميل", key="r1")   # the whole pack, 3 units
        after = [r for r in k.claim(self.db, self.ctx, kid, "2026-11-01", "2026-11-30")["lines"]
                 if r["source"] == "estock" and r["invoice"] == "1" and r["item_code"] == "100"]
        self.assertEqual(after, [])

    def test_p2_return_documents_match_ids_written_as_decimals(self):
        from tests.test_estock_db_import import base_tables
        t = base_tables()
        t["Back_Sales_details"][0].update({"sales_id": "1.0", "product_id": "1.0"})
        lines = [r for r in self._claim(t)["lines"] if r["source"] == "estock" and r["invoice"] == "1"]
        self.assertNotIn("100", [r["item_code"] for r in lines])

    # the inherited tests run here too: they must keep passing
