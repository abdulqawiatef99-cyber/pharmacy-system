"""Scheduled expenses: due day, own reminder days, pay through normal approval, skip, daily reminder once."""
from datetime import date

from app.core import daily, notify, people
from app.core.errors import Conflict, Forbidden, ValidationFailed
from app.finance import expenses as fin, schedules as sc
from tests.helpers import DBTestCase

PW = "Staff-Pass-2026"


class TestSchedules(DBTestCase):
    def setUp(self):
        super().setUp()
        fin.ensure_defaults(self.db)
        self.cat = fin.categories(self.db, self.ctx)[0]["id"]

    def test_due_states_pay_skip_and_reminders(self):
        s = sc.save(self.db, self.ctx, {"name": "إيجار الصيدلية", "category_id": self.cat, "amount": 500000, "day_of_month": 31,
                                         "remind_days": 7, "start_month": "2026-02"})
        feb = sc.next_open(self.db, s, date(2026, 2, 20))
        self.assertEqual((feb["due"], feb["state"], feb["days"]), ("2026-02-28", "later", 8))      # 31 -> month end
        self.assertEqual(sc.next_open(self.db, s, date(2026, 2, 21))["state"], "soon")            # his 7 days
        self.assertEqual(sc.next_open(self.db, s, date(2026, 3, 1))["state"], "late")
        e = sc.pay(self.db, self.ctx, s["id"], period="2026-02")
        self.assertEqual((e["amount"], e["status"], e["schedule_period"]), (500000, "approved", "2026-02"))
        with self.assertRaises(ValidationFailed):
            sc.pay(self.db, self.ctx, s["id"], period="2026-02")                                   # twice
        self.assertEqual(sc.next_open(self.db, s, date(2026, 3, 1))["period"], "2026-03")
        sc.skip(self.db, self.ctx, s["id"], "2026-03")
        s = dict(self.db.one("SELECT * FROM expense_schedules WHERE id=?", (s["id"],)))
        self.assertEqual(sc.next_open(self.db, s, date(2026, 3, 1))["period"], "2026-04")
        # electricity: no fixed amount, remind the same day only
        el = sc.save(self.db, self.ctx, {"name": "كهربا", "category_id": self.cat, "day_of_month": 10, "remind_days": 0,
                                          "every_months": 2, "start_month": "2026-04"})
        with self.assertRaises(ValidationFailed):
            sc.pay(self.db, self.ctx, el["id"])                                                    # amount needed
        st: dict = {}
        self.assertEqual(sc.remind(self.db, self.ctx, date(2026, 4, 10), st), 1)                   # electricity today (rent 20 days away)
        self.assertEqual(sc.remind(self.db, self.ctx, date(2026, 4, 11), st), 1)                   # electricity now late
        self.assertEqual(sc.remind(self.db, self.ctx, date(2026, 4, 12), st), 0)                   # not again
        self.assertEqual(sc.remind(self.db, self.ctx, date(2026, 4, 23), st), 1)                   # rent: 7 days before
        kinds = [n["kind"] for n in notify.listing(self.db, self.ctx)["items"]]
        self.assertEqual(kinds.count("expense_due"), 3)

    def test_validation_and_permission(self):
        for bad in ({"name": "x"}, {"name": "إيجار", "category_id": self.cat, "day_of_month": 40},
                    {"name": "إيجار", "category_id": self.cat, "day_of_month": 5, "remind_days": 90},
                    {"name": "إيجار", "category_id": self.cat, "day_of_month": 5, "every_months": 5}):
            with self.assertRaises(ValidationFailed):
                sc.save(self.db, self.ctx, bad)
        people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        with self.assertRaises(Forbidden):
            sc.save(self.db, self.login("asst", PW), {"name": "إيجار", "category_id": self.cat, "day_of_month": 5})

    def test_daily_run_includes_schedules(self):
        sc.save(self.db, self.ctx, {"name": "إنترنت", "category_id": self.cat, "amount": 30000, "day_of_month": 5,
                                     "remind_days": 2, "start_month": "2026-10"})
        self.assertGreaterEqual(daily.run(self.db, date(2026, 10, 4)), 1)
        self.assertEqual(daily.run(self.db, date(2026, 10, 4)), 0)


class TestPaidOnce(DBTestCase):
    """Two screens acting on the same money at the same moment: the check before the write is repeated
    inside it, so it happens once."""

    def setUp(self):
        super().setUp()
        fin.ensure_defaults(self.db)
        self.cat = fin.categories(self.db, self.ctx)[0]["id"]

    def after_first(self, prefix, action):
        real, fired = self.db.one, []

        def one(sql, params=()):
            out = real(sql, params)
            if not fired and sql.startswith(prefix):
                fired.append(1)
                action()
            return out
        self.db.one = one
        return fired

    def test_a_scheduled_expense_is_recorded_once_for_its_period(self):
        s = sc.save(self.db, self.ctx, {"name": "إيجار الصيدلية", "category_id": self.cat, "amount": 500000,
                                         "day_of_month": 1, "remind_days": 3})
        fired = self.after_first("SELECT 1 FROM expenses WHERE org_id=? AND schedule_id",
                                 lambda: sc.pay(self.db, self.ctx, s["id"], paid_from="outside"))
        with self.assertRaises(ValidationFailed):
            sc.pay(self.db, self.ctx, s["id"], paid_from="outside")
        self.assertEqual(fired, [1])
        self.assertEqual(self.db.one("SELECT count(*) n FROM expenses WHERE org_id=? AND schedule_id=?",
                                     (self.ctx.org_id, s["id"]))["n"], 1)

    def test_an_approval_and_a_rejection_at_the_same_moment_do_not_both_stand(self):
        people.add_user(self.db, self.ctx, username="asst", full_name="مساعد", password=PW, role_code="assistant")
        a = self.login("asst", PW)
        e = fin.record(self.db, a, category_id=self.cat, amount=12000, paid_from="outside", note="فاتورة كهرباء")
        self.assertEqual(e["status"], "pending")
        real, fired = fin.get, []

        def get_then_reject(db, ctx, eid):
            out = real(db, ctx, eid)
            if not fired:
                fired.append(1)
                fin.decide(self.db, self.ctx, eid, False, "مكرر")
            return out
        fin.get = get_then_reject
        try:
            with self.assertRaises(Conflict):
                fin.decide(self.db, self.ctx, e["id"], True)
        finally:
            fin.get = real
        self.assertEqual(fin.get(self.db, self.ctx, e["id"])["status"], "rejected")
        self.assertEqual(self.db.one("SELECT count(*) n FROM audit_log WHERE org_id=? AND action IN"
                                     " ('expense.approve','expense.reject')", (self.ctx.org_id,))["n"], 1)
