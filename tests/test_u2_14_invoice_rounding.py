"""U2-14 rounding (owner 22/11: «اعمل حساب خوازيق المستقبل»).

Only the CASH part of a payment is rounded to the nearest step (ties up); card, wallet, transfer and credit stay
exact; a split rounds only what is left for cash. The invoice total stays exact (it is the sale); the rounding is
its own figure (not sales, not discount) in the shift, the day review, the sales report and the drawer. Returns
refund line values and never more than was received; an exchange is never rounded. Off = today's behaviour.
"""
import sqlite3
import tempfile
from dataclasses import replace
from datetime import date
from pathlib import Path

from app.core.errors import Forbidden, ValidationFailed
from app.db import MIGRATIONS_DIR, Database
from app.sales import rounding
from app.sales.cash import CashDrawer
from tests.test_sales import SalesCase


class TestNearest(SalesCase):
    def test_nearest(self):
        n = rounding.nearest
        self.assertEqual([n(1910, 50), n(1925, 50), n(1940, 50), n(1950, 50)], [-10, 25, 10, 0])
        self.assertEqual([n(1912, 25), n(1913, 25), n(1949, 100), n(1950, 100)], [-12, 12, -49, 50])
        self.assertEqual([n(1910, 0), n(0, 50), n(-5, 50)], [0, 0, 0])

    def test_setting(self):
        self.assertEqual(rounding.step(self.db, self.ctx.org_id), 0)          # a new pharmacy: off until chosen
        self.assertEqual(rounding.set_step(self.db, self.ctx, "50"), 50)
        for bad in (30, 25.9, 0.9, "0.9", True, None):
            with self.assertRaises(ValidationFailed):
                rounding.set_step(self.db, self.ctx, bad)
        with self.assertRaises(Forbidden):
            rounding.set_step(self.db, replace(self.ctx, permissions=frozenset()), 25)


class Rounded(SalesCase):
    def setUp(self):
        super().setUp()
        rounding.set_step(self.db, self.ctx, 50)
        self.cd = CashDrawer(self.db)

    def sale(self, units=1, disc=10, customer=None):
        """19.00 a strip (OLD lot); a 0.10 discount makes 18.90, whose cash rounding is +0.10."""
        sid = self.s.create(self.ctx, customer_id=customer)
        d = self.s.add_line(self.ctx, sid, self.pan, units=units)
        if disc:
            self.s.update_line(self.ctx, sid, d["lines"][0]["id"], discount_amount=disc)
        return sid, self.s.get(self.ctx, sid)

    def pay(self, sid, **kw):
        return self.s.complete(self.ctx, sid, safety_decisions=self.ack_all(sid), **kw)

    def row(self, sid):
        return self.db.one("SELECT total, rounding, paid_cash, paid_card, on_credit FROM sales WHERE id=?", (sid,))


class TestOnlyCashIsRounded(Rounded):
    def test_cash_is_rounded_total_stays_exact(self):
        start = self.cd.summary(self.ctx)["expected_cash"]
        sid, d = self.sale()
        self.assertEqual((d["total"], d["rounding"], d["cash_rounding"], d["cash_total"]), (1890, 0, 10, 1900))
        with self.assertRaises(ValidationFailed) as e:                  # the exact amount in cash: not the rule
            self.pay(sid, paid_cash=1890)
        self.assertEqual((e.exception.code, e.exception.details["cash_required"]), ("payment_mismatch", 1900))
        self.pay(sid, paid_cash=1900)
        self.assertEqual(tuple(self.row(sid)), (1890, 10, 1900, 0, 0))
        self.assertEqual(self.cd.summary(self.ctx)["expected_cash"], start + 1900)   # the drawer has the real cash

    def test_card_is_exact(self):
        sid, _ = self.sale()
        with self.assertRaises(ValidationFailed):
            self.pay(sid, paid_card=1900)
        self.pay(sid, paid_card=1890)
        self.assertEqual(tuple(self.row(sid)), (1890, 0, 0, 1890, 0))

    def test_credit_is_exact_and_the_statement_too(self):
        cust = self.s.create_customer(self.ctx, name="عميل آجل", phone="01000000001", credit_limit=100000)
        sid, _ = self.sale(customer=cust)
        self.pay(sid, on_credit=1890)
        self.assertEqual(tuple(self.row(sid)), (1890, 0, 0, 0, 1890))
        self.assertEqual(self.s.balance(self.ctx, cust), 1890)
        self.assertEqual([r["amount"] for r in self.db.all(
            "SELECT amount FROM customer_ledger WHERE customer_id=?", (cust,))], [1890])

    def test_mixed_rounds_only_the_cash_part(self):
        sid, d = self.sale(units=2, disc=0)                                         # 38.00
        self.s.update_line(self.ctx, sid, d["lines"][0]["id"], discount_amount=5)   # 37.95
        with self.assertRaises(ValidationFailed) as e:
            self.pay(sid, paid_card=1000, paid_cash=2795)                           # exact cash part
        self.assertEqual(e.exception.details["cash_required"], 2800)               # 27.95 -> 28.00
        self.pay(sid, paid_card=1000, paid_cash=2800)
        self.assertEqual(tuple(self.row(sid)), (3795, 5, 2800, 1000, 0))

    def test_cash_and_credit_round_only_the_cash(self):
        cust = self.s.create_customer(self.ctx, name="عميل آجل", phone="01000000002", credit_limit=100000)
        sid, _ = self.sale(customer=cust)                                           # 18.90
        self.pay(sid, on_credit=1000, paid_cash=900)                                # 8.90 -> 9.00
        self.assertEqual(tuple(self.row(sid)), (1890, 10, 900, 0, 1000))
        self.assertEqual(self.s.balance(self.ctx, cust), 1000)                      # the credit stays exact

    def test_setting_changed_between_draft_and_payment(self):
        sid, d = self.sale()
        rounding.set_step(self.db, self.ctx, 0)
        with self.assertRaises(ValidationFailed):                    # the screen's old amount: refused, re-shown
            self.pay(sid, paid_cash=d["cash_total"])
        self.pay(sid, paid_cash=1890)


class TestReportedOnItsOwn(Rounded):
    def test_shift_day_review_and_sales_report(self):
        from app.reports.day_review import day_review
        from app.reports.service import sales_report
        from tests.helpers import today
        a, _ = self.sale()
        self.pay(a, paid_cash=1900)                                     # +0.10
        b, d = self.sale(units=1, disc=40)                              # 18.60 -> 18.50: -0.10
        self.assertEqual(d["cash_rounding"], -10)
        self.pay(b, paid_cash=1850)
        c, _ = self.sale(units=1, disc=20)                              # 18.80 -> 19.00: +0.20
        self.pay(c, paid_cash=1900)
        sales = 1890 + 1860 + 1880
        sh = self.cd.summary(self.ctx)
        self.assertEqual((sh["rounding_total"], sh["cash_by_kind"]["sale"]), (20, sales + 20))   # the drawer
        rep = self.cd.report(self.ctx, sh["id"])
        self.assertEqual((rep["rounding_total"], rep["sales_total"], rep["discount_total"]), (20, sales, 70))
        dr = day_review(self.db, self.ctx, today())["sales"]
        self.assertEqual((dr["total"], dr["discount"], dr["rounding"]), (sales, 70, 20))
        t = sales_report(self.db, self.ctx, today(), today())["totals"]
        self.assertEqual((t["total"], t["discount"], t["rounding"]), (sales, 70, 20))

    def test_profit_uses_line_values(self):
        from app.reports.service import _top_items
        from app.sales.service import local_day_bounds
        from tests.helpers import today
        a, _ = self.sale()
        self.pay(a, paid_cash=1900)
        lo, hi = local_day_bounds(date.fromisoformat(today()))
        top = _top_items(self.db, self.ctx, (self.ctx.org_id, self.ctx.branch_id, lo, hi))
        self.assertEqual(top[0]["revenue"], 1890)

    def test_blind_shift_hides_it(self):
        from app.sales.cash import redact_summary
        blind = replace(self.ctx, permissions=frozenset())
        self.assertNotIn("rounding_total", redact_summary(blind, {"rounding_total": 5, "expected_cash": 1}))


class TestReturns(Rounded):
    def test_full_return_gives_back_what_was_paid_partial_line_values(self):
        sid, d = self.sale(units=2, disc=20)                             # 37.80 -> 38.00 (+0.20)
        lid = d["lines"][0]["id"]
        self.pay(sid, paid_cash=3800)
        r1 = self.s.return_items(self.ctx, sid, {lid: 1}, refund_method="cash", reason="test")
        self.assertEqual(r1["refund"], 1890)                            # a line value, never rounded
        r2 = self.s.return_items(self.ctx, sid, {lid: 1}, refund_method="cash", reason="test")
        self.assertEqual(r1["refund"] + r2["refund"], 3800)             # all he paid: no gain, no loss

    def test_rounded_down_never_refunds_more_than_received(self):
        sid, d = self.sale(units=1, disc=40)                             # 18.60 paid 18.50
        self.pay(sid, paid_cash=1850)
        r = self.s.return_items(self.ctx, sid, {d["lines"][0]["id"]: 1}, refund_method="cash", reason="test")
        self.assertEqual(r["refund"], 1850)

    def test_partial_return_never_above_what_was_paid_when_rounded_down(self):
        from app.catalog import service as cat
        from app.inventory import service as inv
        gift = cat.create_item(self.db, self.ctx, {"name_en": "Free Sample", "public_price": 500})
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=gift, qty_delta=3, movement_type="opening")
        sid, d = self.sale(disc=0)                                       # 19.00
        a = d["lines"][0]["id"]
        self.s.add_line(self.ctx, sid, gift, units=1, discount_bp=9800)   # + 0.10 = 19.10 -> 19.00 cash
        d = self.s.get(self.ctx, sid)
        self.assertEqual((d["total"], d["cash_total"]), (1910, 1900))
        self.pay(sid, paid_cash=1900)
        r1 = self.s.return_items(self.ctx, sid, {a: 1}, refund_method="cash", reason="test")
        self.assertEqual(r1["refund"], 1900)                             # capped at what was paid
        g = next(l["id"] for l in d["lines"] if l["item_id"] == gift)
        r2 = self.s.return_items(self.ctx, sid, {g: 1}, refund_method="cash", reason="test")
        self.assertEqual(r1["refund"] + r2["refund"], 1900)

    def test_void_gives_back_the_cash_taken(self):
        start = self.cd.summary(self.ctx)["expected_cash"]
        sid, _ = self.sale()
        self.pay(sid, paid_cash=1900)
        self.s.void(self.ctx, sid, reason="غلط في الفاتورة")
        self.assertEqual(self.cd.summary(self.ctx)["expected_cash"], start)

    def test_exchange_is_never_rounded(self):
        sid, d = self.sale(units=2, disc=0)                              # 38.00
        self.pay(sid, paid_cash=3800)
        new = self.s.create(self.ctx)
        n = self.s.add_line(self.ctx, new, self.pan, units=1)
        self.s.update_line(self.ctx, new, n["lines"][0]["id"], discount_amount=10)   # 18.90
        out = self.s.exchange(self.ctx, sid, {d["lines"][0]["id"]: 1}, new, refund_method="cash", pay_method="cash",
                              reason="تغيير", safety_decisions=self.ack_all(new))
        self.assertEqual(tuple(self.row(out["sale_id"]))[:2], (1890, 0))


class TestOffIsToday(SalesCase):
    def test_off_changes_nothing(self):
        from app.printing.escpos import receipt_lines
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=1)
        self.s.update_line(self.ctx, sid, d["lines"][0]["id"], discount_amount=10)
        d = self.s.get(self.ctx, sid)
        self.assertEqual((d["total"], d["cash_total"], d["cash_rounding"]), (1890, 1890, 0))
        self.s.complete(self.ctx, sid, paid_cash=1890, safety_decisions=self.ack_all(sid))
        sale = self.s.get(self.ctx, sid)
        self.assertEqual((sale["rounding"], sale["paid_cash"]), (0, 1890))
        lines = receipt_lines("x", sale, "now")
        self.assertFalse(any(len(l) > 1 and l[1] == "التقريب" for l in lines))
        self.assertIn(("total", "المطلوب", "18.90"), lines)


class TestReceiptShowsIt(Rounded):
    def test_receipt_rounding_row_and_amount_asked(self):
        from app.printing.escpos import receipt_lines
        sid, _ = self.sale()
        self.pay(sid, paid_cash=1900)
        lines = receipt_lines("x", self.s.get(self.ctx, sid), "now")
        self.assertIn(("row", "التقريب", "+0.10"), lines)
        self.assertIn(("total", "المطلوب", "19.00"), lines)


class TestMigration(SalesCase):
    def test_existing_pharmacy_gets_half_pound_and_a_chosen_step_is_kept(self):
        sql = (MIGRATIONS_DIR / "0042_invoice_rounding.sql").read_text(encoding="utf-8").split("\n")
        ins = next(l for l in sql if l.startswith("INSERT"))
        with self.db.transaction():
            self.db.execute(ins)
        self.assertEqual(rounding.step(self.db, self.ctx.org_id), 50)
        rounding.set_step(self.db, self.ctx, 25)
        with self.db.transaction():
            self.db.execute(ins)                                          # again: the owner's choice stays
        self.assertEqual(rounding.step(self.db, self.ctx.org_id), 25)

    def test_migrate_on_a_live_database_is_safe_and_runs_once(self):
        """A database at 0041 holding data: 0042 adds the columns at 0, keeps every row, and a second run
        applies nothing (tracked in schema_migrations; the update takes a verified backup before it)."""
        old, tmp = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp()) / "live.db"
        for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if f.stem < "0042":
                (old / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
        db = Database(tmp)
        db.migrate(old)
        with db.transaction():
            db.execute("INSERT INTO organizations (id, name, org_type) VALUES ('o1', 'x', 'pharmacy')")
        applied = db.migrate(MIGRATIONS_DIR)
        self.assertEqual(applied[0], "0042_invoice_rounding")
        self.assertEqual(db.migrate(MIGRATIONS_DIR), [])                  # idempotent
        cols = {r["name"]: r for r in db.all("PRAGMA table_info(sales)")}
        self.assertEqual((cols["rounding"]["notnull"], cols["rounding"]["dflt_value"]), (1, "0"))
        self.assertEqual(db.one("SELECT value FROM org_settings WHERE org_id='o1' AND key='invoice_rounding'")["value"], "50")
        db.close()
        c = sqlite3.connect(tmp)
        with self.assertRaises(sqlite3.OperationalError):                 # a plain re-run would fail: only the
            c.execute("ALTER TABLE sales ADD COLUMN rounding INTEGER NOT NULL DEFAULT 0")   # tracked runner applies it
        c.close()


class TestIndependentReviewFindings(Rounded):
    """The second agent's review of the cash-only rounding."""

    def test_p1_a_return_rounding_never_lands_in_sales_or_profit(self):
        from app.finance.expenses import profit as profit_report
        from app.reports.day_review import day_review
        from tests.helpers import today
        sid, d = self.sale(units=2, disc=20)                             # 37.80 paid 38.00
        lid = d["lines"][0]["id"]
        self.pay(sid, paid_cash=3800)
        self.s.return_items(self.ctx, sid, {lid: 2}, refund_method="cash", reason="test")
        row = self.db.one("SELECT total, returned_total FROM sales WHERE id=?", (sid,))
        self.assertEqual(tuple(row), (3780, 3780))                     # line values: net sales 0, not -0.20
        ret = self.db.one("SELECT total, rounding FROM sale_returns WHERE sale_id=?", (sid,))
        self.assertEqual(tuple(ret), (3800, 20))                       # what he got back, with its rounding
        dr = day_review(self.db, self.ctx, today())
        self.assertEqual((dr["net_sales"], dr["sales"]["rounding"]), (0, 0))
        p = profit_report(self.db, self.ctx, today(), today())
        self.assertEqual((p["revenue"], p["cash_rounding"]), (0, 0))

    def test_p2_shift_and_day_rounding_net_of_returns_and_voids(self):
        from app.reports.day_review import day_review
        from tests.helpers import today
        a, d = self.sale()                                               # +0.10
        self.pay(a, paid_cash=1900)
        b, _ = self.sale()                                               # +0.10, voided
        self.pay(b, paid_cash=1900)
        self.s.void(self.ctx, b, reason="غلط في الفاتورة")
        self.s.return_items(self.ctx, a, {d["lines"][0]["id"]: 1}, refund_method="cash", reason="test")
        sh = self.cd.summary(self.ctx)
        self.assertEqual(sh["rounding_total"], 0)
        self.assertEqual(self.cd.report(self.ctx, sh["id"])["rounding_total"], 0)
        self.assertEqual(day_review(self.db, self.ctx, today())["sales"]["rounding"], 0)

    def test_p2_no_cash_no_rounding_a_card_is_never_short(self):
        sid, _ = self.sale()                                             # 18.90
        for card in (1880, 1850):                                        # 0.10 / 0.40 short, no cash
            with self.assertRaises(ValidationFailed):
                self.pay(sid, paid_card=card)
        cust = self.s.create_customer(self.ctx, name="عميل", phone="01000000003", credit_limit=100000)
        sid, _ = self.sale(customer=cust)
        with self.assertRaises(ValidationFailed):
            self.pay(sid, on_credit=1880)

    def test_p2_a_tiny_cash_part_is_not_rounded_to_nothing(self):
        sid, _ = self.sale()                                             # 18.90
        self.pay(sid, paid_card=1880, paid_cash=10)                     # 0.10 in cash stays 0.10
        self.assertEqual(self.row(sid)["rounding"], 0)
