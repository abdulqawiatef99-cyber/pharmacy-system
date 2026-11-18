"""E-Stock's history inside the program (owner 18/11 22:05): old invoices, a customer's items, an item's
history, statements, shift closes, shortages and period totals, read from the archive. Synthetic data only."""
from app.core import people
from app.core.errors import Forbidden, NotFound, ValidationFailed
from app.dataimport import estock_history as h
from tests.test_estock_db_import import Base, base_tables

PW = "Staff-Pass-2026"


class TestHistory(Base):
    def setUp(self):
        super().setUp()
        t = base_tables()
        d = t["Branches_sales_details"]
        d[0].update({"buy_price": "24.0000", "total_sell": "30.0000"})
        d[1].update({"buy_price": "140.0000", "total_sell": "35.0000"})
        t["Sales_header"][0]["bill_number"] = "B-1"
        t["Back_sales_header"][0].update({"insert_date": "2026-11-18T12:00:00.000", "bill_number": "R-1"})
        t["Back_Sales_details"][0].update({"back_price": "30.0000"})
        t["Purchase_header"][0].update({"insert_date": "2026-10-01T09:00:00.000", "bill_number": "P-77"})
        t["Gedo_customers"][0].update({"insert_date": "2026-11-01T10:00:00.000", "gc_type": "بيع آجل", "total": "120.0000"})
        t["Cash_disk_close"][0].update({"insert_date": "2026-11-17T23:00:00.000", "cdc_cash_id": "1"})
        t["Shortcoming"].append({"id": "2", "product_id": "3", "amount": "5", "store_id": "1",
                                 "insert_date": "2026-11-16T10:00:00.000"})
        self.assertTrue(self.run_import(tables=t)["ok"])
        self.cust = self.db.one("SELECT id FROM customers WHERE name='أحمد تجربة'")["id"]
        self.sup = self.db.one("SELECT id FROM suppliers WHERE name='مورد تجربة'")["id"]

    def test_search_and_view_an_old_sale(self):
        rows = h.invoices(self.db, self.ctx, "sale", date_from="2026-11-01", date_to="2026-11-30")
        self.assertEqual({r["estock_id"] for r in rows}, {"1", "2"})
        self.assertTrue(all(r["source"] == "estock" for r in rows))
        self.assertEqual(h.invoices(self.db, self.ctx, "sale", q="B-1")[0]["estock_id"], "1")
        self.assertEqual({r["estock_id"] for r in h.invoices(self.db, self.ctx, "sale", party_id=self.cust)}, {"1", "2"})
        two = [r for r in rows if r["estock_id"] == "2"][0]
        self.assertEqual((two["party"], two["ticket_no"], two["total"]), ("أحمد تجربة", "T-123", "-10.00"))
        inv = h.invoice(self.db, self.ctx, "sale", "1")
        self.assertEqual([(l["item"], l["qty"], l["total"], l["cost"]) for l in inv["lines"]],
                         [("Testamol 500 Tab", "1", "30.00", "24.00"), ("Testaclav 1g Tab", "0.5", "35.00", "140.00")])
        self.assertEqual(inv["lines"][0]["item_id"], self.item("100")["id"])
        with self.assertRaises(NotFound):
            h.invoice(self.db, self.ctx, "sale", "999")

    def test_returns_and_purchases(self):
        r = h.invoices(self.db, self.ctx, "sale_return")
        self.assertEqual((r[0]["bill_number"], r[0]["sale_id"], r[0]["total"]), ("R-1", "1", "30.00"))
        self.assertEqual(h.invoice(self.db, self.ctx, "sale_return", "1")["lines"][0]["returned"], "1")
        p = h.invoices(self.db, self.ctx, "purchase", party_id=self.sup)
        self.assertEqual((p[0]["bill_number"], p[0]["party"], p[0]["total"]), ("P-77", "مورد تجربة", "500.00"))
        self.assertEqual(h.invoice(self.db, self.ctx, "purchase", "1")["lines"][0]["qty"], "20")

    def test_a_customers_items_and_an_items_history(self):
        items = h.customer_items(self.db, self.ctx, self.cust)
        self.assertEqual([(i["item"], i["qty"], i["invoices"]) for i in items],
                         [("Testamol 500 Tab", "1", 1), ("Testaclav 1g Tab", "0.5", 1)])
        hist = h.item_history(self.db, self.ctx, self.item("100")["id"])
        self.assertEqual({r["kind"] for r in hist}, {"sale", "sale_return", "purchase"})
        self.assertEqual(hist[0]["kind"], "sale_return")                  # newest first
        self.assertEqual([r["party"] for r in hist if r["kind"] == "purchase"], ["مورد تجربة"])

    def test_statements_shifts_shortages_and_periods(self):
        st = h.statement(self.db, self.ctx, "customer", self.cust)
        self.assertEqual([(s["for_me"], s["for_him"], s["total"], s["type"]) for s in st],
                         [("150.00", "-30.00", "120.00", "بيع آجل")])
        self.assertEqual(h.statement(self.db, self.ctx, "supplier", self.sup)[0]["for_him"], "600.00")
        sh = h.shift_closes(self.db, self.ctx, date_from="2026-11-17", date_to="2026-11-17")
        self.assertEqual((sh[0]["system_cash"], sh[0]["counted_cash"]), ("1000.00", "999.50"))
        self.assertEqual([(s["item"], s["qty"]) for s in h.shortages(self.db, self.ctx, date_from="2026-11-16")],
                         [("Testaclav 1g Tab", "5")])
        rep = h.period_report(self.db, self.ctx, "2026-10-01", "2026-11-30")
        nov = [r for r in rep if r["period"] == "2026-11"][0]
        self.assertEqual((nov["sales"], nov["sales_count"], nov["returns"]), ("44.00", 2, "30.00"))
        self.assertEqual([r for r in rep if r["period"] == "2026-10"][0]["purchases"], "500.00")
        self.assertEqual(len(h.period_report(self.db, self.ctx, "2026-11-18", "2026-11-18", by="day")), 1)

    def test_permissions_and_inputs(self):
        people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        a = self.login("asst", PW)
        for call in (lambda: h.invoices(self.db, a, "sale"), lambda: h.period_report(self.db, a, "2026-11-01", "2026-11-30"),
                     lambda: h.shift_closes(self.db, a)):
            with self.assertRaises(Forbidden):
                call()
        self.assertNotIn("cost", h.item_history(self.db, a, self.item("100")["id"])[0])   # no costs.read
        for bad in (lambda: h.invoices(self.db, self.ctx, "bank"), lambda: h.invoices(self.db, self.ctx, limit=0),
                    lambda: h.invoices(self.db, self.ctx, date_from="18/11/2026"),
                    lambda: h.period_report(self.db, self.ctx, "2026-11-01", "2026-11-30", by="year")):
            with self.assertRaises(ValidationFailed):
                bad()
        _, other = self.second_tenant()
        with self.assertRaises(NotFound):
            h.customer_items(self.db, other, self.cust)

    def test_without_an_archive(self):
        (self.tmp / "estock-archive.sqlite").chmod(0o644)
        (self.tmp / "estock-archive.sqlite").unlink()
        self.assertFalse(h.available(self.db))
        with self.assertRaises(NotFound):
            h.invoices(self.db, self.ctx)
