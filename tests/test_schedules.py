"""Scheduled expenses: due day, own reminder days, pay through normal approval, skip, daily reminder once."""
from datetime import date

from app.core import daily, notify, people
from app.core.errors import Forbidden, ValidationFailed
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
