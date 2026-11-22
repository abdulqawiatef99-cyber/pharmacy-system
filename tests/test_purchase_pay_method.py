"""«طريقة الدفع» on the purchase invoice (owner 22/11 21:22 «فين طريقة الدفع للمخازن؟»).

آجل (on the supplier's account; the default for a company), نقدي من الدرج (the default for a warehouse), من الخزنة,
تحويل / محفظة (another money account, with a reference), and part paid now + the rest on the account. At approval the
paid part leaves the chosen money place and reduces the supplier's balance in the same transaction, as one ordinary
supplier payment (so the payments screen sees it once, never twice). Synthetic data only."""
from dataclasses import replace
from datetime import date, timedelta

from app import config
from app.catalog import service as cat
from app.core.errors import DomainError as AppError, Forbidden, ValidationFailed
from app.finance import money
from app.procurement import suppliers
from app.purchasing.service import Purchasing
from app.sales.cash import CashDrawer
from tests.helpers import DBTestCase

FUT = (date.today() + timedelta(days=500)).isoformat()


class Base(DBTestCase):
    def setUp(self):
        super().setUp()
        self.p = Purchasing(self.db, replace(config.load(), upload_dir=self.tmp / "u"))
        self.company = cat.create_supplier(self.db, self.ctx, name="شركة تجربة", supplier_type="company")
        self.store = cat.create_supplier(self.db, self.ctx, name="مخزن تجربة", supplier_type="warehouse")
        self.item = cat.create_item(self.db, self.ctx, {"name_en": "Pay tab", "public_price": 10000})
        self.n = 0

    def draft(self, sup, total=8000, **pay):
        self.n += 1
        inv = self.p.create_manual(self.ctx, supplier_id=sup)
        self.p.update_header(self.ctx, inv, {"invoice_no": f"P-{self.n}", "invoice_date": date.today().isoformat(),
                                             **pay}, None)
        self.p.add_line(self.ctx, inv, {"item_id": self.item, "qty": 1, "unit_net_price": total, "line_total": total,
                                        "batch_no": f"B{self.n}", "expiry_date": FUT}, None)
        return inv

    def approve(self, inv, ctx=None):
        return self.p.approve(ctx or self.ctx, inv, None)

    def balance(self, sup):
        return suppliers.balance(self.db, self.ctx.org_id, sup)

    def drawer(self):
        return money.drawer_balance(self.db, self.ctx)["balance"]

    def payments(self, sup):
        return [dict(r) for r in self.db.all("SELECT * FROM supplier_ledger WHERE supplier_id=? AND kind='payment'", (sup,))]


class TestDefaults(Base):
    def test_company_default_is_on_account(self):
        inv = self.draft(self.company)
        self.assertEqual(self.p.get(self.ctx, inv)["invoice"]["pay"]["source"], "credit")
        self.approve(inv)
        self.assertEqual(self.balance(self.company), 8000)
        self.assertEqual(self.payments(self.company), [])

    def test_warehouse_default_is_cash_from_the_drawer(self):
        CashDrawer(self.db).open(self.ctx, 20000)
        inv = self.draft(self.store)
        self.assertEqual(self.p.get(self.ctx, inv)["invoice"]["pay"]["source"], "drawer")
        before = self.drawer()
        self.approve(inv)
        self.assertEqual(self.balance(self.store), 0)
        self.assertEqual(before - self.drawer(), 8000)                    # the drawer's expected cash follows
        pays = self.payments(self.store)
        self.assertEqual([(p["amount"], p["ref_id"], p["pay_method"]) for p in pays], [(-8000, inv, "drawer")])

    def test_warehouse_on_account_when_chosen(self):
        inv = self.draft(self.store, pay_source="credit")
        self.approve(inv)
        self.assertEqual(self.balance(self.store), 8000)


class TestPaidNow(Base):
    def test_part_now_rest_on_account(self):
        CashDrawer(self.db).open(self.ctx, 20000)
        inv = self.draft(self.company, pay_source="drawer", pay_amount=3000)
        before = self.drawer()
        self.approve(inv)
        self.assertEqual(self.balance(self.company), 5000)
        self.assertEqual(before - self.drawer(), 3000)

    def test_from_the_treasury(self):
        tid = money.treasury_id(self.db, self.ctx)
        with self.db.transaction():
            money._post(self.db, self.ctx, tid, 50000, "opening", note="رصيد")
        inv = self.draft(self.company, pay_source="treasury")
        self.approve(inv)
        self.assertEqual(self.balance(self.company), 0)
        bal = next(a for a in money.accounts(self.db, self.ctx) if a["kind"] == "treasury")["balance"]
        self.assertEqual(bal, 42000)

    def test_wallet_with_reference(self):
        w = money.create_account(self.db, self.ctx, "محفظة تجربة", "wallet", allow_negative=True)["id"]
        inv = self.draft(self.company, pay_source=w, pay_ref="TX-77")
        self.approve(inv)
        p = self.payments(self.company)[0]
        self.assertEqual((p["amount"], p["pay_ref"], p["pay_method"]), (-8000, "TX-77", "wallet"))
        self.assertEqual(self.balance(self.company), 0)

    def test_wallet_needs_a_reference(self):
        w = money.create_account(self.db, self.ctx, "محفظة 2", "wallet", allow_negative=True)["id"]
        inv = self.draft(self.company, pay_source=w)
        with self.assertRaises(ValidationFailed):
            self.approve(inv)
        self.assertEqual(self.p.get(self.ctx, inv)["invoice"]["status"], "draft")

    def test_paid_more_than_the_invoice_is_refused(self):
        CashDrawer(self.db).open(self.ctx, 20000)
        with self.assertRaises(ValidationFailed):
            self.draft(self.company, pay_source="drawer", pay_amount=-1)
        inv = self.draft(self.company, pay_source="drawer", pay_amount=9000)
        with self.assertRaises(AppError):
            self.approve(inv)
        self.assertEqual(self.p.get(self.ctx, inv)["invoice"]["status"], "draft")
        self.assertEqual(self.balance(self.company), 0)

    def test_drawer_without_open_shift_refuses_and_writes_nothing(self):
        inv = self.draft(self.store)
        with self.assertRaises(AppError):
            self.approve(inv)
        self.assertEqual(self.p.get(self.ctx, inv)["invoice"]["status"], "draft")
        self.assertEqual(self.db.one("SELECT count(*) n FROM stock_movements")["n"], 0)

    def test_drawer_below_zero_is_refused(self):
        CashDrawer(self.db).open(self.ctx, 1000)
        inv = self.draft(self.store)
        with self.assertRaises(AppError):
            self.approve(inv)
        self.assertEqual(self.balance(self.store), 0)
        self.assertEqual(self.p.get(self.ctx, inv)["invoice"]["status"], "draft")

    def test_unknown_source_refused_at_entry(self):
        inv = self.p.create_manual(self.ctx, supplier_id=self.company)
        with self.assertRaises(AppError):
            self.p.update_header(self.ctx, inv, {"pay_source": "nonsense"}, None)

    def test_payment_counted_once_on_the_supplier_account(self):
        CashDrawer(self.db).open(self.ctx, 20000)
        inv = self.draft(self.company, pay_source="drawer", pay_amount=3000)
        self.approve(inv)
        acc = suppliers.account(self.db, self.ctx, self.company)
        self.assertEqual(acc["balance"], 5000)
        self.assertEqual(len(self.payments(self.company)), 1)
        with self.assertRaises(AppError):               # the rest only; more is an advance (owner/manager + reason)
            suppliers.pay(self.db, self.ctx, self.company, 6000, "drawer")
        suppliers.pay(self.db, self.ctx, self.company, 5000, "drawer")
        self.assertEqual(self.balance(self.company), 0)

    def test_pharmacist_may_approve_but_not_pay(self):
        """purchases.approve without suppliers.pay (review P1): refused before anything is written, passes on account."""
        from app.core import people
        people.add_user(self.db, self.ctx, username="ph1", full_name="ص", password="Staff-Pass-2026",
                        role_code="pharmacist")
        ph = self.login("ph1", "Staff-Pass-2026")
        self.assertTrue(ph.can("purchases.approve"))
        self.assertFalse(ph.can("suppliers.pay"))
        CashDrawer(self.db).open(self.ctx, 20000)
        inv = self.draft(self.store)
        with self.assertRaises(Forbidden):
            self.approve(inv, ph)
        self.assertEqual(self.p.get(self.ctx, inv)["invoice"]["status"], "draft")
        self.assertEqual(self.db.one("SELECT count(*) n FROM stock_movements")["n"], 0)
        self.assertEqual(self.balance(self.store), 0)
        self.p.update_header(self.ctx, inv, {"pay_source": "credit"}, None)
        self.approve(inv, ph)
        self.assertEqual(self.balance(self.store), 8000)

    def test_supplier_credit_is_used_first(self):
        """The supplier holds 30 of ours: an 80 invoice can be paid 50 now at most, never an advance (review P2)."""
        CashDrawer(self.db).open(self.ctx, 20000)
        suppliers.pay(self.db, self.ctx, self.store, 3000, "drawer", "دفعة مقدمة متفق عليها")
        inv = self.draft(self.store)
        with self.assertRaises(ValidationFailed) as e:
            self.approve(inv)
        self.assertEqual(e.exception.code, "pay_above_due")
        self.p.update_header(self.ctx, inv, {"pay_amount": 5000}, None)
        self.approve(inv)
        self.assertEqual(self.balance(self.store), 0)

    def test_cancel_after_paid_at_approval_leaves_the_payment(self):
        CashDrawer(self.db).open(self.ctx, 20000)
        inv = self.draft(self.store)
        self.approve(inv)
        self.p.cancel(self.ctx, inv, "فاتورة غلط", None)
        self.assertEqual(self.balance(self.store), -8000)      # the money left: the supplier now owes it back
        self.assertEqual(len(self.payments(self.store)), 1)

    def test_approved_invoice_keeps_the_plan_it_was_paid_with(self):
        inv = self.draft(self.company)
        self.approve(inv)
        suppliers.update(self.db, self.ctx, self.company, {"supplier_type": "warehouse"})
        pay = self.p.get(self.ctx, inv)["invoice"]["pay"]
        self.assertEqual((pay["source"], pay["default"]), ("credit", False))

    def test_inactive_account_refused_at_entry_and_stored_by_id(self):
        w = money.create_account(self.db, self.ctx, "محفظة 3", "wallet", allow_negative=True)["id"]
        inv = self.p.create_manual(self.ctx, supplier_id=self.company)
        d = self.p.update_header(self.ctx, inv, {"pay_source": w}, None)
        self.assertEqual(d["invoice"]["pay_source"], w)
        with self.db.transaction():
            self.db.execute("UPDATE money_accounts SET is_active=0 WHERE id=?", (w,))
        with self.assertRaises(AppError):
            self.p.update_header(self.ctx, inv, {"pay_source": w}, None)


class TestMigration(DBTestCase):
    def test_open_drafts_stay_on_account_and_approved_are_untouched(self):
        import tempfile
        from pathlib import Path
        from app.db import MIGRATIONS_DIR, Database
        old, tmp = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp()) / "live.db"
        for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if f.stem < "0044":
                (old / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
        db = Database(tmp)
        db.migrate(old)
        with db.transaction():
            db.execute("INSERT INTO organizations (id, name, org_type) VALUES ('o1', 'x', 'pharmacy')")
            db.execute("INSERT INTO branches (id, org_id, name) VALUES ('b1', 'o1', 'b')")
            for i, st in (("d1", "draft"), ("a1", "approved")):
                db.execute("INSERT INTO purchase_invoices (id, org_id, branch_id, source_type, status)"
                           " VALUES (?,'o1','b1','manual',?)", (i, st))
        db.migrate(MIGRATIONS_DIR)
        rows = {r["id"]: (r["pay_source"], r["pay_amount"]) for r in db.all("SELECT * FROM purchase_invoices")}
        self.assertEqual(rows, {"d1": ("credit", None), "a1": (None, None)})
