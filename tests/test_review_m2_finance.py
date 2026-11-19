"""Codex audit M2 (#324, 19/11), the finance findings: schedules (M2-11, M2-12), payroll (M2-13, M2-14, M2-15),
expense categories and totals (M2-18, M2-21), the replay fingerprint (M2-22). Each test failed on develop
before its fix. Synthetic data only."""
from app.core import people
from app.core.errors import Conflict, Forbidden
from app.finance import expenses as fin, payroll as pay, schedules
from app.sales.service import local_today
from tests.test_api import APICase, H
from tests.test_cashier_acceptance import Base

PW = "Staff-Pass-2026"


class FinBase(Base):
    def setUp(self):
        super().setUp()
        fin.ensure_defaults(self.db)
        self.cat = fin.categories(self.db, self.ctx)[0]["id"]
        self.month = local_today().isoformat()[:7]
        self.cd.open(self.ctx, 1000000)
        people.add_user(self.db, self.ctx, username="mgr", full_name="M", password=PW, role_code="manager", pin="2468")
        self.m = self.login("mgr", PW)


class TestM2_11StaleSchedule(FinBase):
    def _sched(self, amount=10000):
        return schedules.save(self.db, self.ctx, {"name": "إيجار", "category_id": self.cat, "amount": amount,
                                                  "day_of_month": 1, "paid_from": "drawer"})

    def test_a_changed_amount_is_not_paid_from_the_old_screen(self):
        s = self._sched()
        schedules.save(self.db, self.ctx, {"amount": 20000}, s["id"])
        with self.assertRaises(Conflict) as e:
            schedules.pay(self.db, self.ctx, s["id"], period=s["next"]["period"], expected_amount=10000)
        self.assertEqual(e.exception.code, "schedule_changed")

    def test_a_stopped_or_skipped_bill_is_not_paid(self):
        s = self._sched()
        p = s["next"]["period"]
        schedules.skip(self.db, self.ctx, s["id"], p)
        with self.assertRaises(Conflict):
            schedules.pay(self.db, self.ctx, s["id"], period=p)
        s2 = self._sched()
        schedules.save(self.db, self.ctx, {"is_active": False}, s2["id"])
        with self.assertRaises(Conflict):
            schedules.pay(self.db, self.ctx, s2["id"], period=s2["start_month"])


class TestM2_12RejectedAfterTheMoneyLeft(FinBase):
    def test_a_rejected_drawer_payment_keeps_the_month_paid(self):
        s = schedules.save(self.db, self.ctx, {"name": "إيجار", "category_id": self.cat, "amount": 100000,
                                               "day_of_month": 1, "paid_from": "drawer"})
        p = s["next"]["period"]
        e = schedules.pay(self.db, self.m, s["id"], period=p)            # over the manager's limit: pending
        self.assertEqual(e["status"], "pending")
        fin.decide(self.db, self.ctx, e["id"], False, "الورق ناقص")
        again = next(x for x in schedules.listing(self.db, self.ctx) if x["id"] == s["id"])
        self.assertNotEqual((again["next"] or {}).get("period"), p)      # the cash already left: not offered again
        with self.assertRaises(Exception):
            schedules.pay(self.db, self.ctx, s["id"], period=p)


class TestM2_13To15Payroll(FinBase):
    def setUp(self):
        super().setUp()
        self.uid = people.add_user(self.db, self.ctx, username="asst", full_name="أحمد", password=PW, role_code="assistant")
        people.set_exception(self.db, self.ctx, self.db.one("SELECT id FROM users WHERE username='mgr'")["id"],
                             "payroll.manage", "grant")
        self.m = self.login("mgr", PW)
        pay.set_pay(self.db, self.ctx, self.uid, 520000, 26)
        pay.fix_attendance(self.db, self.ctx, self.uid, f"{self.month}-01", "10:00", "18:00", "تسجيل")

    def test_m2_13_a_salary_the_manager_may_not_approve_is_not_paid(self):
        pay.approve(self.db, self.m, self.uid, self.month, bonus=100000)
        net = self.db.one("SELECT net FROM payroll WHERE user_id=?", (self.uid,))["net"]
        self.assertGreater(net, 50000)                                   # above the manager's expense limit
        before = self.cd.summary(self.ctx)["expected_cash"]
        with self.assertRaises(Forbidden):
            pay.pay(self.db, self.m, self.uid, self.month, "drawer")
        self.assertEqual(self.cd.summary(self.ctx)["expected_cash"], before)
        self.assertEqual(self.db.one("SELECT status FROM payroll WHERE user_id=?", (self.uid,))["status"], "approved")

    def test_m2_14_pay_follows_the_approval_that_was_shown(self):
        pay.approve(self.db, self.ctx, self.uid, self.month)
        shown = self.db.one("SELECT net FROM payroll WHERE user_id=?", (self.uid,))["net"]
        pay.approve(self.db, self.ctx, self.uid, self.month, bonus=5000)  # another tab
        with self.assertRaises(Conflict) as e:
            pay.pay(self.db, self.ctx, self.uid, self.month, "drawer", expected_net=shown)
        self.assertEqual(e.exception.code, "payroll_changed")

    def test_m2_14_a_fixed_attendance_day_asks_for_a_new_approval(self):
        pay.approve(self.db, self.ctx, self.uid, self.month)
        pay.fix_attendance(self.db, self.ctx, self.uid, f"{self.month}-02", "10:00", "18:00", "نسي يسجل")
        with self.assertRaises(Conflict) as e:
            pay.pay(self.db, self.ctx, self.uid, self.month, "drawer")
        self.assertEqual(e.exception.code, "payroll_changed")

    def test_m2_15_a_leaving_employee_stays_on_this_months_sheet(self):
        people.set_active(self.db, self.ctx, self.uid, False)
        ids = [p["id"] for p in pay.month_sheet(self.db, self.ctx, self.month)["people"]]
        self.assertIn(self.uid, ids)
        pay.approve(self.db, self.ctx, self.uid, self.month)
        next_month = f"{int(self.month[:4]) + (self.month[5:] == '12')}-{(int(self.month[5:]) % 12) + 1:02d}"
        self.assertNotIn(self.uid, [p["id"] for p in pay.month_sheet(self.db, self.ctx, next_month)["people"]])


class TestM2_18And21Expenses(FinBase):
    def test_m2_18_a_new_category_is_audited(self):
        fin.add_category(self.db, self.ctx, "بند تجربة")
        self.assertTrue(self.db.one("SELECT 1 FROM audit_log WHERE action='expense_category.create'"))

    def test_m2_21_totals_cover_the_whole_period(self):
        with self.db.transaction():
            for i in range(5003):
                self.db.execute("INSERT INTO expenses (id, org_id, branch_id, category_id, amount, spent_on, paid_from,"
                                " status, created_by) VALUES (?,?,?,?,?,?, 'outside', 'approved', ?)",
                                (f"e{i}", self.ctx.org_id, self.ctx.branch_id, self.cat, 1000, local_today().isoformat(),
                                 self.ctx.user_id))
        t = fin.totals(self.db, self.ctx, date_from=local_today().isoformat(), date_to=local_today().isoformat())
        self.assertEqual((t["approved"], t["approved_n"]), (5003 * 1000, 5003))


class TestM2_22FingerprintHasTheQuery(APICase):
    def test_the_same_key_with_another_month_is_refused_not_replayed(self):
        self.http_login()
        uid = people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        body = {"action": "pay_settings", "user_id": uid, "monthly_salary": 100000, "work_days": 26}
        h = {**H, "Idempotency-Key": "same-key"}
        a = self.c.post("/api/staff?month=2026-10", json=body, headers=h)
        b = self.c.post("/api/staff?month=2026-11", json=body, headers=h)
        self.assertEqual(a.status_code, 200, a.text)
        self.assertEqual((b.status_code, b.json()["error"]["code"]), (422, "idempotency_mismatch"))
