"""#58: the money paths refuse bad input and out-of-rule actions, and nothing moves when they do.

Found untested by a branch-coverage run of app/sales and app/finance (2026-10-30). Synthetic data only."""
from datetime import timedelta

from app.core import accounts
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.inventory import service as inv
from app.sales.cash import count_total, require_open_shift
from tests.test_cashier_acceptance import Base


def _ago(iso: str, days: int) -> str:
    return (accounts.parse_iso(iso) - timedelta(days=days)).isoformat()


class TestDrawerRefusals(Base):
    def cash_rows(self):
        return self.db.one("SELECT count(*) FROM cash_movements")[0]

    def test_no_open_shift_and_double_open(self):
        with self.assertRaises(Conflict) as e:
            require_open_shift(self.db, self.ctx)
        self.assertEqual(e.exception.code, "no_open_shift")
        for bad in (-1, "100", 1.5, None):
            with self.assertRaises(ValidationFailed):
                self.cd.open(self.ctx, bad)
        self.cd.open(self.ctx, 0)
        with self.assertRaises(Conflict) as e:
            self.cd.open(self.ctx, 0)
        self.assertEqual(e.exception.code, "shift_already_open")

    def test_denominations_are_strict(self):
        self.assertEqual(count_total({"20000": 2, "100": 0, "50": "3"}), (40150, {"20000": 2, "50": 3}))  # zeros dropped
        for bad in ([("20000", 1)], {"abc": 1}, {"20000": "x"}, {"7": 1}, {"20000": -1}, {"20000": 100001}):
            with self.assertRaises(ValidationFailed, msg=bad):
                count_total(bad)

    def test_summary_without_or_with_foreign_shift(self):
        with self.assertRaises(NotFound):
            self.cd.summary(self.ctx)
        with self.assertRaises(NotFound):
            self.cd.summary(self.ctx, "no-such-shift")

    def test_movement_needs_valid_amount_kind_and_reason(self):
        self.cd.open(self.ctx, 0)
        before = self.cash_rows()
        for amount, kind, note in ((0, "payout", "سبب كافي"), (-500, "payout", "سبب كافي"), (1.5, "payout", "سبب كافي"),
                                   (500, "sale", "سبب كافي"), (500, "payout", "  "), (500, "deposit", "ab")):
            with self.assertRaises(ValidationFailed, msg=(amount, kind, note)):
                self.cd.movement(self.ctx, amount, kind, note)
        self.assertEqual(self.cash_rows(), before)                                  # nothing recorded

    def test_close_rejects_bad_counts_and_keeps_the_shift_open(self):
        self.cd.open(self.ctx, 0)
        for bad in (-1, None, "100"):
            with self.assertRaises(ValidationFailed):
                self.cd.close(self.ctx, counted_cash=bad)
        with self.assertRaises(ValidationFailed):
            self.cd.close(self.ctx, denominations={"3": 1})
        require_open_shift(self.db, self.ctx)                                      # still open


class TestSaleRefusals(Base):
    def setUp(self):
        super().setUp()
        self.cd.open(self.ctx, 0)

    def stock(self):
        return inv.stock_for_item(self.db, self.ctx, self.pan)["total_qty"]

    def draft(self):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=2)
        return sid, d

    def test_complete_refuses_negative_stale_empty_and_completed(self):
        sid, d = self.draft()
        with self.assertRaises(ValidationFailed):                                   # older screens: three amounts
            self.s.complete(self.ctx, sid, paid_cash=d["total"] + 100, paid_card=-100)
        with self.assertRaises(Conflict) as e:
            self.s.complete(self.ctx, sid, paid_cash=d["total"], version=d["version"] - 1)
        self.assertEqual(e.exception.code, "stale_version")
        empty = self.s.create(self.ctx)
        with self.assertRaises(ValidationFailed):
            self.s.complete(self.ctx, empty, paid_cash=0)
        self.assertEqual(self.stock(), 20)                                          # nothing sold yet
        done = self.sell()
        with self.assertRaises(Conflict) as e:
            self.s.complete(self.ctx, done["id"], paid_cash=done["total"])
        self.assertEqual(e.exception.code, "not_draft")
        self.assertEqual(self.stock(), 18)                                          # sold once, not twice

    def test_remove_missing_line(self):
        sid, _ = self.draft()
        with self.assertRaises(NotFound):
            self.s.remove_line(self.ctx, sid, "no-such-line")

    def test_void_rules(self):
        with self.assertRaises(ValidationFailed):                                   # a reason is required
            self.s.void(self.ctx, "x", "قصير")
        with self.assertRaises(NotFound):
            self.s.void(self.ctx, "no-such-sale", "سبب الإلغاء هنا")
        sid, _ = self.draft()                                                       # a draft is simply dropped
        self.assertEqual(self.s.void(self.ctx, sid, "عميل غيّر رأيه")["status"], "voided")
        self.assertEqual(self.stock(), 20)
        sale = self.sell()
        self.s.void(self.ctx, sale["id"], "غلط في الفاتورة")
        self.assertEqual(self.stock(), 20)                                          # stock returned once
        with self.assertRaises(Conflict):                                           # not twice
            self.s.void(self.ctx, sale["id"], "غلط في الفاتورة")
        self.assertEqual(self.stock(), 20)

    def test_void_refused_after_return_or_on_another_day(self):
        sale = self.sell()
        self.s.return_items(self.ctx, sale["id"], {sale["lines"][0]["id"]: 1}, refund_method="cash", reason="مش محتاجه")
        with self.assertRaises(Conflict):
            self.s.void(self.ctx, sale["id"], "غلط في الفاتورة")
        old = self.sell()
        with self.db.transaction():
            self.db.execute("UPDATE sales SET completed_at=? WHERE id=?", (_ago(old["completed_at"], 2), old["id"]))
        with self.assertRaises(Forbidden):
            self.s.void(self.ctx, old["id"], "غلط في الفاتورة")
        self.assertEqual(self.db.one("SELECT status FROM sales WHERE id=?", (old["id"],))["status"], "completed")

    def test_invoice_list_dates(self):
        with self.assertRaises(ValidationFailed):
            self.s.list_sales(self.ctx, date_from="2026-13-01", date_to="2026-10-01")
        with self.assertRaises(ValidationFailed):
            self.s.list_sales(self.ctx, date_from="2026-10-30", date_to="2026-10-01")
        accounts.add_user(self.db, self.ctx, username="asst2", full_name="مساعد", password="Assist-Pass-2026",
                          role_code="assistant")
        actx = self.login("asst2", "Assist-Pass-2026")
        self.assertFalse(actx.can("sales.totals"))
        with self.assertRaises(Forbidden):                                          # earlier days: managers and owner only
            self.s.list_sales(actx, date_from="2026-10-01", date_to="2026-10-30")


class TestExpenseRefusals(Base):
    def setUp(self):
        super().setUp()
        from app.finance import expenses as fin
        self.fin = fin
        fin.ensure_defaults(self.db)
        self.cat = fin.categories(self.db, self.ctx)[0]["id"]
        self.cd.open(self.ctx, 100000)

    def test_record_rejects_bad_input_and_moves_no_cash(self):
        fin = self.fin
        expected = self.cd.summary(self.ctx)["expected_cash"]
        for kw in ({"amount": 0}, {"amount": -100}, {"amount": 12.5}, {"paid_from": "safe"},
                   {"spent_on": "2026-02-30"}):
            args = {"category_id": self.cat, "amount": 3000, "paid_from": "drawer", **kw}
            with self.assertRaises(ValidationFailed, msg=kw):
                fin.record(self.db, self.ctx, **args)
        with self.assertRaises(NotFound):
            fin.record(self.db, self.ctx, category_id="no-such", amount=3000, paid_from="drawer")
        self.assertEqual(self.cd.summary(self.ctx)["expected_cash"], expected)       # drawer untouched
        self.assertEqual(self.db.one("SELECT count(*) FROM expenses")[0], 0)

    def test_decision_is_final(self):
        fin = self.fin
        from app.core import people
        people.add_user(self.db, self.ctx, username="asst3", full_name="A", password="Staff-Pass-2026", role_code="assistant")
        e = fin.record(self.db, self.login("asst3", "Staff-Pass-2026"), category_id=self.cat, amount=3000, paid_from="outside")
        fin.decide(self.db, self.ctx, e["id"], True)
        for approve, reason in ((True, None), (False, "متأخر")):
            with self.assertRaises(Conflict):                                       # approved stays approved
                fin.decide(self.db, self.ctx, e["id"], approve, reason)

    def test_category_names(self):
        fin = self.fin
        with self.assertRaises(ValidationFailed):
            fin.add_category(self.db, self.ctx, " x ")
        fin.add_category(self.db, self.ctx, "صيانة تكييف")
        with self.assertRaises(Conflict):
            fin.add_category(self.db, self.ctx, "صيانة تكييف")

    def test_manager_limit_setting_and_profit_period(self):
        fin = self.fin
        with self.db.transaction():
            self.db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?, 'manager_expense_limit', 'oops')",
                            (self.ctx.org_id,))
        self.assertEqual(fin.manager_limit(self.db, self.ctx.org_id), fin.MANAGER_LIMIT_DEFAULT)   # a bad value is not 0
        for f, t in (("2026-10-30", "2026-10-01"), ("bad", "2026-10-01")):
            with self.assertRaises(ValidationFailed):
                fin.profit(self.db, self.ctx, f, t)
