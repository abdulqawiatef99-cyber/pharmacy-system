"""Supplier type and account, payments, shortages list."""
from datetime import timedelta

from app import config
from app.catalog import card, service as cat
from app.core import people
from app.core.errors import Forbidden, ValidationFailed
from app.procurement import suppliers as sup
from app.purchasing.service import Purchasing
from tests.test_cashier_acceptance import Base, T

PW = "Staff-Pass-2026"


class TestSuppliers(Base):
    def setUp(self):
        super().setUp()
        self.p = Purchasing(self.db, config.load())
        self.sid = cat.create_supplier(self.db, self.ctx, name="مخزن النهضة")

    def buy(self, packs=5, price=4000, disc=2500):
        inv = self.p.create_manual(self.ctx, supplier_id=self.sid)
        v = self.p.get(self.ctx, inv)["invoice"]["version"]
        self.p.update_header(self.ctx, inv, {"invoice_no": f"N{packs}{price}"}, v)
        v = self.p.get(self.ctx, inv)["invoice"]["version"]
        self.p.add_line(self.ctx, inv, {"item_id": self.pan, "qty": packs, "public_price": price, "discount_bp": disc,
                                        "expiry_date": (T + timedelta(days=500)).isoformat()}, v)
        d = self.p.get(self.ctx, inv)
        return self.p.approve(self.ctx, inv, d["invoice"]["version"])

    def test_type_balance_payment_cancel(self):
        # bought on the account first: a warehouse invoice is paid cash from the drawer by default since the
        # purchase payment method (owner 22/11), and this test is about the payments screen
        inv = self.buy()
        sup.update(self.db, self.ctx, self.sid, {"supplier_type": "warehouse"})
        with self.assertRaises(ValidationFailed):
            sup.update(self.db, self.ctx, self.sid, {"supplier_type": "x"})
        self.assertEqual(sup.balance(self.db, self.ctx.org_id, self.sid), 15000)          # 5 x 40 x 75%
        self.cd.open(self.ctx, 20000)
        acc = sup.pay(self.db, self.ctx, self.sid, 5000, "drawer", "دفعة")
        self.assertEqual(acc["balance"], 10000)
        self.assertEqual(self.cd.summary(self.ctx)["expected_cash"], 15000)              # came out of the drawer
        sup.pay(self.db, self.ctx, self.sid, 1000, "outside")
        self.assertEqual(self.cd.summary(self.ctx)["expected_cash"], 15000)
        self.p.cancel(self.ctx, inv["invoice"]["id"], "فاتورة غلط اتلغت", None)
        self.assertEqual(sup.balance(self.db, self.ctx.org_id, self.sid), -6000)          # we paid 60 for nothing
        people.add_user(self.db, self.ctx, username="pharm", full_name="P", password=PW, role_code="pharmacist")
        ph = self.login("pharm", PW)
        with self.assertRaises(Forbidden):
            sup.pay(self.db, ph, self.sid, 100, "outside")
        self.assertNotIn("balance", sup.listing(self.db, ph)[0])

    def test_shortages_and_cleared_when_bought(self):
        cat.update_item(self.db, self.ctx, self.pan, {"min_stock": 30})
        card.set_shortage(self.db, self.ctx, self.pan, True)
        rows = sup.shortages(self.db, self.ctx)
        self.assertEqual(rows[0]["id"], self.pan)
        self.assertEqual(set(rows[0]["reasons"]), {"marked", "below_min"})
        self.buy(packs=20)
        self.assertIsNone(card.card(self.db, self.ctx, self.pan)["shortage_at"])
        self.assertEqual(sup.shortages(self.db, self.ctx), [])                      # 20 + 40 units > 30

    def test_shortages_show_public_price_and_last_purchase(self):
        """Owner 28/11: public price, and next to the last supplier the discount and pharmacist price it came at."""
        self.buy(packs=5, price=4000, disc=2500)
        card.set_shortage(self.db, self.ctx, self.pan, True)
        r = next(x for x in sup.shortages(self.db, self.ctx) if x["id"] == self.pan)
        self.assertIsNotNone(r["public_price"])
        self.assertEqual(r["last_supplier"], "مخزن النهضة")
        self.assertEqual((r["last_discount_bp"], r["last_net_price"]), (2500, 3000))
        self.assertTrue(r["last_date"])
        people.add_user(self.db, self.ctx, username="pharm2", full_name="P", password=PW, role_code="pharmacist")
        ph = self.login("pharm2", PW)
        if ph.can("inventory.read"):
            r = next(x for x in sup.shortages(self.db, ph) if x["id"] == self.pan)
            self.assertEqual((r["last_discount_bp"], r["last_net_price"]), (None, None))   # costs stay hidden
            self.assertEqual(r["last_supplier"], "مخزن النهضة")


class TestStatement(TestSuppliers):
    def test_period_statement_and_all_suppliers(self):
        from datetime import date
        from app.sales.service import local_today
        today = local_today()
        self.buy()                                                                    # 150
        self.cd.open(self.ctx, 20000)
        sup.pay(self.db, self.ctx, self.sid, 5000, "drawer")
        sup.pay(self.db, self.ctx, self.sid, 3000, "cheque", ref="000123")
        with self.assertRaises(ValidationFailed):
            sup.pay(self.db, self.ctx, self.sid, 100, "cheque")                       # cheque needs its number
        st = sup.statement(self.db, self.ctx, self.sid, today.isoformat(), today.isoformat())
        self.assertEqual((st["opening"], st["purchases"], st["payments"], st["closing"]), (0, 15000, 8000, 7000))
        self.assertEqual({m["method"]: m["amount"] for m in st["by_method"]}, {"drawer": 5000, "cheque": 3000})
        self.assertEqual([l["balance"] for l in st["lines"]], [15000, 10000, 7000])
        nxt = (today + timedelta(days=1)).isoformat()
        st2 = sup.statement(self.db, self.ctx, self.sid, nxt, nxt)                   # next period starts from 70
        self.assertEqual((st2["opening"], st2["closing"], st2["lines"]), (7000, 7000, []))
        other = cat.create_supplier(self.db, self.ctx, name="شركة فاركو")
        sup.update(self.db, self.ctx, other, {"supplier_type": "company"})
        all_ = sup.statements(self.db, self.ctx, today.isoformat(), today.isoformat())
        self.assertEqual(all_["totals"]["closing"], 7000)
        self.assertEqual(sup.statements(self.db, self.ctx, today.isoformat(), today.isoformat(), "company")["rows"], [])
        with self.assertRaises(ValidationFailed):
            sup.statement(self.db, self.ctx, self.sid, nxt, today.isoformat())
        people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        with self.assertRaises(Forbidden):
            sup.statements(self.db, self.login("asst", PW), today.isoformat(), today.isoformat())
        self.assertIsInstance(date.fromisoformat(st["from"]), date)


class TestExport(Base):
    def test_xlsx_money_rtl_and_limits(self):
        import io
        from openpyxl import load_workbook
        from app.reports.export import xlsx
        data = xlsx({"title": "صيدلية — المبيعات", "sheets": [{"name": "يوم/بيوم", "columns": [{"label": "اليوم", "type": "date"},
                     {"label": "الإجمالي", "type": "money"}, {"label": "ملاحظة"}], "rows": [["2026-10-25", 123456, "x"], ["bad", "zz", None]],
                     "total": ["الإجمالي", 123456, None]}]})
        ws = load_workbook(io.BytesIO(data)).active
        self.assertTrue(ws.sheet_view.rightToLeft)
        self.assertEqual(ws.title, "يوم بيوم")                        # "/" not allowed in sheet names
        self.assertEqual(ws.cell(4, 2).value, 1234.56)
        self.assertEqual(ws.cell(5, 2).value, "zz")                   # bad number kept as text, not lost
        self.assertEqual(ws.cell(6, 2).value, 1234.56)
        for bad in ({}, {"sheets": []}, {"sheets": [{"columns": [], "rows": []}]}):
            with self.assertRaises(ValidationFailed):
                xlsx(bad)


class TestSupplierRename(Base):
    def test_renaming_onto_another_suppliers_name_is_a_conflict_not_a_500(self):
        from app.core.errors import Conflict
        a = cat.create_supplier(self.db, self.ctx, name="مخزن النهضة")
        b = cat.create_supplier(self.db, self.ctx, name="مخزن الشروق")
        for name in ("مخزن النهضة", "  مخزن النهضة  "):
            with self.assertRaises(Conflict, msg=name):
                sup.update(self.db, self.ctx, b, {"name": name, "phone": "0100"})
        row = self.db.one("SELECT name, phone FROM suppliers WHERE id=?", (b,))
        self.assertEqual((row["name"], row["phone"]), ("مخزن الشروق", None))      # nothing half-saved
        self.assertEqual(sup.update(self.db, self.ctx, a, {"name": "مخزن النهضة الجديد"})["name"], "مخزن النهضة الجديد")
