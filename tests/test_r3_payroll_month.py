"""Payroll month text is exactly YYYY-MM, and saving pay settings without a month answers with the sheet.
Synthetic data only."""
import uuid

from app.core import people
from app.core.errors import ValidationFailed
from app.finance import expenses as fin, payroll as pay
from app.sales.service import local_today
from tests.test_api import APICase, H

PW = "Staff-Pass-2026"


class TestPayrollMonth(APICase):
    def setUp(self):
        super().setUp()
        fin.ensure_defaults(self.db)
        self.uid = people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        self.http_login()

    def post(self, **body):
        return self.c.post("/api/staff", headers={**H, "Idempotency-Key": uuid.uuid4().hex}, json=body)

    def advances(self):
        return [tuple(r) for r in self.db.all("SELECT month, amount FROM staff_advances WHERE org_id=?", (self.ctx.org_id,))]

    def test_an_advance_needs_the_month_written_exactly(self):
        for bad in ("2026-1", " 2026-10", "2026-10 ", "2026-10\n", "2026-13", "2026-00", "٢٠٢٦-١٠", "26-10", "2026/10", ""):
            with self.subTest(month=bad):
                with self.assertRaises(ValidationFailed):
                    pay.add_advance(self.db, self.ctx, self.uid, 100000, bad, "سلفة", "outside")
                r = self.post(action="advance", user_id=self.uid, amount=100000, month=bad, paid_from="outside")
                self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(self.advances(), [])
        r = self.post(action="advance", user_id=self.uid, amount=100000, month="2026-10", paid_from="outside")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.advances(), [("2026-10", 100000)])

    def test_the_sheet_and_the_approval_need_it_too(self):
        for bad in ("2026-1", " 2026-10", "2026-10 "):
            with self.subTest(month=bad):
                with self.assertRaises(ValidationFailed):
                    pay.month_sheet(self.db, self.ctx, bad)
                with self.assertRaises(ValidationFailed):
                    pay.approve(self.db, self.ctx, self.uid, bad)
        self.assertEqual(self.db.one("SELECT count(*) FROM payroll")[0], 0)

    def test_pay_settings_without_a_month_saves_and_answers_with_this_month(self):
        r = self.post(action="pay_settings", user_id=self.uid, monthly_salary=300000, work_days=26)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["month"], local_today().isoformat()[:7])
        me = next(p for p in r.json()["people"] if p["id"] == self.uid)
        self.assertEqual((me["monthly_salary"], me["work_days"]), (300000, 26))

    def test_pay_settings_with_a_wrong_month_saves_nothing(self):
        r = self.post(action="pay_settings", user_id=self.uid, monthly_salary=300000, work_days=26, month="2026-1")
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(self.db.one("SELECT count(*) FROM staff_pay WHERE org_id=?", (self.ctx.org_id,))[0], 0)

    def test_an_attendance_fix_without_a_month_answers_too(self):
        day = local_today().isoformat()
        r = self.post(action="fix", user_id=self.uid, day=day, check_in="10:00", check_out="18:00", note="تسجيل يدوي")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.db.one("SELECT count(*) FROM attendance WHERE org_id=?", (self.ctx.org_id,))[0], 1)
