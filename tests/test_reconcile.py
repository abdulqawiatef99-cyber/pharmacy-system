"""F106 supplier early-payment discount and F108 party balance reconciliation. Synthetic data only."""
import sqlite3

from app.catalog import service as cat
from app.core import people
from app.core.errors import Conflict, Forbidden, ValidationFailed
from app.finance import reconcile as rc
from app.procurement import suppliers as sup
from tests.helpers import today
from tests.test_cashier_acceptance import Base

PW = "Staff-Pass-2026"


class Case(Base):
    def setUp(self):
        super().setUp()
        people.add_user(self.db, self.ctx, username="mgr", full_name="M", password=PW, role_code="manager", pin="2468")
        people.add_user(self.db, self.ctx, username="pharm", full_name="P", password=PW, role_code="pharmacist")
        self.m, self.ph = self.login("mgr", PW), self.login("pharm", PW)
        self.sup = cat.create_supplier(self.db, self.ctx, name="مورد تجربة")
        self.cust = self.s.create_customer(self.ctx, name="مستشفى تجربة", credit_limit=10000000)
        with self.db.transaction():
            self.db.execute("INSERT INTO supplier_ledger (id, org_id, supplier_id, amount, kind) VALUES ('o1',?,?,100000,'opening')",
                            (self.ctx.org_id, self.sup))
            self.db.execute("INSERT INTO customer_ledger (id, org_id, customer_id, amount, kind)"
                            " VALUES ('c1',?,?,50000,'credit_sale')", (self.ctx.org_id, self.cust))

    def sbal(self):
        return sup.balance(self.db, self.ctx.org_id, self.sup)


class TestSplit(Case):
    def test_paid_plus_discount_or_settle_with_percent(self):
        self.assertEqual(rc.split_payment(paid=9800, discount=200), (9800, 200))
        self.assertEqual(rc.split_payment(settle=10000, percent_bp=200), (9800, 200))
        self.assertEqual(rc.split_payment(settle=3333, percent_bp=150), (3283, 50))   # 49.995 half up = 50
        for bad in (dict(paid=0), dict(paid=100, discount=-1), dict(settle=100, percent_bp=10000),
                    dict(settle=100, percent_bp=200, paid=98), dict(paid=10.5)):
            with self.assertRaises(ValidationFailed):
                rc.split_payment(**bad)


class TestSupplierDiscount(Case):
    def test_discount_lowers_the_balance_only_and_is_its_own_kind(self):
        with self.db.transaction():
            lid = rc.supplier_discount(self.db, self.m, self.sup, 2000, payment_id="P1", note="تعجيل دفع")
        self.assertEqual(self.sbal(), 98000)
        row = self.db.one("SELECT kind, amount, ref_id FROM supplier_ledger WHERE id=?", (lid,))
        self.assertEqual(tuple(row), ("discount", -2000, "P1"))
        self.assertTrue(self.db.one("SELECT 1 FROM audit_log WHERE action='supplier.discount' AND entity_id=?", (self.sup,)))
        self.assertEqual(self.db.one("SELECT count(*) n FROM money_movements")["n"], 0)        # no account moved
        self.assertEqual(rc.discounts_received(self.db, self.ctx.org_id, today(), today()), 2000)
        self.assertIsNone(rc.supplier_discount(self.db, self.m, self.sup, 0, payment_id="P2"))
        with self.assertRaises(Forbidden):
            rc.supplier_discount(self.db, self.ph, self.sup, 100, payment_id="P3")


class TestReconcile(Case):
    def test_check_shows_the_difference_without_writing(self):
        r = rc.check(self.db, self.m, "supplier", self.sup, 97000)
        self.assertEqual((r["program_balance"], r["difference"]), (100000, -3000))
        self.assertEqual(self.db.one("SELECT count(*) n FROM party_reconciliations")["n"], 0)

    def test_matching_statement_is_kept_without_a_ledger_row(self):
        r = rc.record(self.db, self.m, "supplier", self.sup, 100000, program_balance=100000)
        self.assertEqual((r["difference"], r["ledger_id"]), (0, None))
        self.assertEqual(len(rc.history(self.db, self.m, "supplier", self.sup)), 1)

    def test_difference_needs_type_and_note_except_opening(self):
        with self.assertRaises(ValidationFailed) as e:
            rc.record(self.db, self.m, "supplier", self.sup, 90000, program_balance=100000)
        self.assertEqual(e.exception.code, "type_required")
        with self.assertRaises(ValidationFailed):
            rc.record(self.db, self.m, "supplier", self.sup, 90000, program_balance=100000, adj_type="correction")
        r = rc.record(self.db, self.m, "supplier", self.sup, 90000, program_balance=100000, adj_type="opening")
        self.assertEqual((r["difference"], r["balance"]), (-10000, 90000))
        self.assertEqual(self.db.one("SELECT kind FROM supplier_ledger WHERE id=?", (r["ledger_id"],))["kind"], "adjustment")
        self.assertEqual(rc.discounts_received(self.db, self.ctx.org_id, today(), today()), 0)   # no profit effect

    def test_a_retry_never_posts_twice(self):
        rc.record(self.db, self.m, "supplier", self.sup, 120000, program_balance=100000, adj_type="opening")
        with self.assertRaises(Conflict) as e:
            rc.record(self.db, self.m, "supplier", self.sup, 120000, program_balance=100000, adj_type="opening")
        self.assertEqual(e.exception.code, "balance_changed")
        self.assertEqual(self.sbal(), 120000)

    def test_early_discount_is_supplier_only_and_counts_in_profit(self):
        r = rc.record(self.db, self.m, "supplier", self.sup, 98500, program_balance=100000, adj_type="early_discount",
                      note="خصم تعجيل الشهر")
        self.assertEqual(self.db.one("SELECT kind FROM supplier_ledger WHERE id=?", (r["ledger_id"],))["kind"], "discount")
        self.assertEqual(rc.discounts_received(self.db, self.ctx.org_id, today(), today()), 1500)
        with self.assertRaises(ValidationFailed):       # a discount never raises what we owe
            rc.record(self.db, self.m, "supplier", self.sup, 99000, program_balance=98500, adj_type="early_discount",
                      note="غلط")

    def test_claim_discount_for_customers_lowers_revenue(self):
        with self.assertRaises(ValidationFailed):
            rc.record(self.db, self.m, "supplier", self.sup, 90000, program_balance=100000, adj_type="claim_discount",
                      note="غلط")
        r = rc.record(self.db, self.m, "customer", self.cust, 47000, program_balance=50000, adj_type="claim_discount",
                      note="مطالبة الشهر")
        self.assertEqual(r["balance"], 47000)
        self.assertEqual(rc.claim_discounts(self.db, self.ctx.org_id, today(), today()), 3000)
        with self.assertRaises(ValidationFailed):
            rc.record(self.db, self.m, "customer", self.cust, 46000, program_balance=47000, adj_type="early_discount",
                      note="غلط")

    def test_owner_or_manager_only(self):
        for fn in (lambda c: rc.check(self.db, c, "supplier", self.sup, 1),
                   lambda c: rc.parties(self.db, c),
                   lambda c: rc.record(self.db, c, "supplier", self.sup, 1, program_balance=100000, adj_type="opening")):
            with self.assertRaises(Forbidden):
                fn(self.ph)

    def test_one_page_lists_suppliers_and_customers_with_search(self):
        rows = rc.parties(self.db, self.m)
        self.assertIn(("supplier", self.sup, 100000), [(r["party"], r["id"], r["balance"]) for r in rows])
        self.assertIn(("customer", self.cust, 50000), [(r["party"], r["id"], r["balance"]) for r in rows])
        found = rc.parties(self.db, self.m, "supplier", q="تجربة")
        self.assertEqual([r["id"] for r in found], [self.sup])
        rc.record(self.db, self.m, "supplier", self.sup, 100000, program_balance=100000)
        self.assertEqual(rc.parties(self.db, self.m, "supplier")[0]["last"]["difference"], 0)

    def test_other_pharmacy_cannot_reach_the_party(self):
        _, octx = self.second_tenant()
        from app.core.errors import NotFound
        with self.assertRaises((NotFound, Forbidden)):
            rc.check(self.db, octx, "supplier", self.sup, 1)

    def test_post_adjustment_for_the_import(self):
        with self.db.transaction():
            lid = rc.post_adjustment(self.db, self.ctx, "supplier", self.sup, 2500, "opening", note="الرصيد اللي أكده المالك",
                                     ref_id="run-1", statement_balance=102500)
        rec = self.db.one("SELECT adj_type, difference, ledger_id, ref_id, program_balance FROM party_reconciliations")
        self.assertEqual(tuple(rec), ("opening", 2500, lid, "run-1", 100000))
        with self.assertRaises(sqlite3.DatabaseError), self.db.transaction():
            self.db.execute("DELETE FROM party_reconciliations")
