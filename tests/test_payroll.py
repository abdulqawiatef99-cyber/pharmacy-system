"""Attendance and payroll."""
from app.core import people
from app.core.errors import Conflict, Forbidden, ValidationFailed
from app.finance import expenses as fin, payroll as pay
from app.sales.service import local_today
from tests.test_api import APICase, H
from tests.test_cashier_acceptance import Base

PW = "Staff-Pass-2026"


class TestPayroll(Base):
    def setUp(self):
        super().setUp()
        fin.ensure_defaults(self.db)
        self.uid = people.add_user(self.db, self.ctx, username="asst", full_name="أحمد", password=PW, role_code="assistant")
        self.a = self.login("asst", PW)
        self.month = local_today().isoformat()[:7]

    def test_check_in_out(self):
        st = pay.check(self.db, self.a, "in")
        self.assertIsNotNone(st["open"])
        with self.assertRaises(Conflict):
            pay.check(self.db, self.a, "in")
        st = pay.check(self.db, self.a, "out")
        self.assertIsNone(st["open"])
        self.assertEqual(st["days_this_month"], 1)
        with self.assertRaises(Forbidden):
            pay.month_sheet(self.db, self.a, self.month)

    def test_salary_month(self):
        pay.set_pay(self.db, self.ctx, self.uid, 520000, 26)                    # 5,200 EGP, 200/day
        for d in ("01", "02", "03"):
            pay.fix_attendance(self.db, self.ctx, self.uid, f"{self.month}-{d}", "10:00", "18:00", "تسجيل يدوي")
        with self.assertRaises(ValidationFailed):
            pay.fix_attendance(self.db, self.ctx, self.uid, f"{self.month}-04", "18:00", "10:00", "غلط")
        self.cd.open(self.ctx, 100000)
        pay.add_advance(self.db, self.ctx, self.uid, 50000, self.month, "سلفة", "drawer")
        self.assertEqual(self.cd.summary(self.ctx)["expected_cash"], 50000)
        p = next(x for x in pay.month_sheet(self.db, self.ctx, self.month)["people"] if x["id"] == self.uid)
        self.assertEqual((p["days_present"], p["hours"]), (3, 24.0))
        self.assertEqual(p["suggested"]["absence_deduction"], 460000)               # 23 missing days x 200
        sheet = pay.approve(self.db, self.ctx, self.uid, self.month, bonus=10000)
        pr = next(x for x in sheet["people"] if x["id"] == self.uid)["payroll"]
        self.assertEqual(pr["net"], 520000 - 460000 - 50000 + 10000)
        pay.pay(self.db, self.ctx, self.uid, self.month, "outside")
        with self.assertRaises(Conflict):
            pay.pay(self.db, self.ctx, self.uid, self.month, "outside")
        exp = [e for e in fin.listing(self.db, self.ctx) if e["category"].startswith("رواتب")]
        self.assertEqual(sorted(e["amount"] for e in exp), [20000, 50000])          # net + the advance


class TestStaffAPI(APICase):
    def test_attendance_api(self):
        self.http_login()
        r = self.c.post("/api/attendance", json={"action": "in"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["open"]["source"], "pc")
        month = local_today().isoformat()[:7]
        self.assertEqual(self.c.get("/api/staff", params={"month": month}).json()["people"], [])


class AbsenceRounding(__import__("unittest").TestCase):
    def test_absence_deduction_is_whole_piasters_half_up(self):
        from app.finance.payroll import compute
        # 6500.13 / 26 = 250.005 a day: half up 25001 piasters (round() of the float gave 25000, half to even)
        r = compute(650013, 26, 25, 0)
        self.assertEqual((r["absence_deduction"], r["net"]), (25001, 625012))
        self.assertEqual(compute(25, 2, 1, 0)["absence_deduction"], 13)
        self.assertEqual(compute(650013, 26, 26, 0)["absence_deduction"], 0)
        self.assertEqual(compute(650013, 26, 0, 0)["absence_deduction"], 650013)    # the whole month, never more
        self.assertEqual(compute(1000, 0, 0, 0)["absence_deduction"], 0)
        self.assertIsInstance(r["absence_deduction"], int)
