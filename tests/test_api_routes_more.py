"""API routes the screens use that no test had called (found by a line-coverage run of app/api/app.py).

Payroll through /api/staff (fix a day, advance, approve, pay once), scheduled expenses (pay with a typed
amount, skip a month), the receipt line order setting, feedback replies, marking many items at once and a
refill reminder removed. Synthetic data only.
"""
from app.core import people
from app.finance import expenses as fin
from tests.test_owner_plan_priority_1_2 import PlanCase

PW = "Staff-Pass-2026"


class StaffAPI(PlanCase):
    def setUp(self):
        super().setUp()
        fin.ensure_defaults(self.db)
        self.uid = people.add_user(self.db, self.ctx, username="staff1", full_name="موظف", password=PW,
                                   role_code="assistant")
        self.month = self.today[:7]

    def staff(self, expected=200, **body):
        return self.call("POST", "/api/staff", expected, json={"user_id": self.uid, "month": self.month, **body})

    def person(self, sheet):
        return next(p for p in sheet["people"] if p["id"] == self.uid)

    def test_a_salary_month_through_the_screen(self):
        self.staff(action="pay_settings", monthly_salary=520000, work_days=1)        # one working day: no absence
        self.staff(action="fix", day=f"{self.month}-01", check_in="10:00", check_out="18:00", note="نسي يسجل")
        self.staff(422, action="fix", day=f"{self.month}-02", check_in="18:00", check_out="10:00", note="غلط")
        self.staff(action="advance", amount=50000, paid_from="outside", note="سلفة")
        sheet = self.staff(action="approve", bonus=10000, deduction=0, note="شهر تجربة")
        p = self.person(sheet)
        self.assertEqual(p["days_present"], 1)
        self.assertEqual(p["payroll"]["net"], 520000 - 50000 + 10000)
        self.staff(action="pay", paid_from="outside")
        self.staff(409, action="pay", paid_from="outside")                     # paid once
        self.staff(409, action="approve", bonus=0, deduction=0)                 # and its figures stay
        self.staff(422, action="explode")
        exp = [e for e in fin.listing(self.db, self.ctx) if e["category"].startswith("رواتب")]
        self.assertEqual(sorted(e["amount"] for e in exp), [50000, 480000])    # the advance + the net

    def test_bonus_and_deduction_must_be_whole_piasters(self):
        self.staff(action="pay_settings", monthly_salary=520000, work_days=26)
        self.staff(422, action="approve", bonus=100.5, deduction=0)
        self.staff(422, action="approve", bonus=0, deduction=-1)
        self.assertIsNone(self.person(self.call("GET", f"/api/staff?month={self.month}"))["payroll"])

    def test_staff_cannot_open_the_payroll(self):
        from starlette.testclient import TestClient
        a = TestClient(self.app)
        self.http_login("staff1", PW, client=a)
        r = a.post("/api/staff", json={"action": "approve", "user_id": self.uid, "month": self.month},
                   headers={"X-Requested-With": "pharm"})
        self.assertEqual(r.status_code, 403)


class ScheduleAPI(PlanCase):
    def setUp(self):
        super().setUp()
        fin.ensure_defaults(self.db)
        self.cat = fin.categories(self.db, self.ctx)[0]["id"]
        self.s = self.call("POST", "/api/expense-schedules", json={
            "name": "فاتورة الكهرباء", "category_id": self.cat, "amount": "", "day_of_month": 10, "remind_days": 3})

    def test_a_bill_without_a_fixed_amount_is_paid_with_the_typed_amount_once(self):
        sid = self.s["id"]
        self.call("POST", f"/api/expense-schedules/{sid}", 422, json={"action": "pay", "paid_from": "outside"})
        self.call("POST", f"/api/expense-schedules/{sid}", 422, json={"action": "pay", "amount": 123.5,
                                                                      "paid_from": "outside"})
        e = self.call("POST", f"/api/expense-schedules/{sid}", json={"action": "pay", "amount": "45000",
                                                                     "paid_from": "outside"})
        self.assertEqual(e["amount"], 45000)
        again = self.call("POST", f"/api/expense-schedules/{sid}", json={"action": "pay", "amount": 47000,
                                                                         "paid_from": "outside"})
        self.assertGreater(again["schedule_period"], e["schedule_period"])    # the next month, not this one twice
        periods = [r["schedule_period"] for r in self.db.all(
            "SELECT schedule_period FROM expenses WHERE org_id=? AND schedule_id=?", (self.ctx.org_id, sid))]
        self.assertEqual(len(periods), len(set(periods)))

    def test_skipping_a_month(self):
        sid = self.s["id"]
        out = self.call("POST", f"/api/expense-schedules/{sid}", json={"action": "skip", "period": self.today[:7]})
        mine = next(x for x in out["schedules"] if x["id"] == sid)
        self.assertIn(self.today[:7], mine["skipped"] if isinstance(mine["skipped"], list) else str(mine["skipped"]))
        self.call("POST", f"/api/expense-schedules/{sid}", 422, json={"action": "skip", "period": "next month"})


class SmallSettingsAPI(PlanCase):
    def test_receipt_line_order(self):
        self.call("PUT", "/api/settings/sales", 422, json={"line_order": "random"})
        r = self.call("PUT", "/api/settings/sales", json={"line_order": "alpha"})
        self.assertEqual(r["line_order"], "alpha")
        a = self.db.one("SELECT before_json, after_json FROM audit_log WHERE org_id=? AND action='settings.line_order'",
                        (self.ctx.org_id,))
        self.assertIn("alpha", a["after_json"])

    def test_feedback_reply_and_status(self):
        f = self.call("POST", "/api/feedback", 201, json={"kind": "suggestion", "text": "زرار أكبر للبيع", "screen": "pos"})
        items = self.call("POST", "/api/feedback", json={"id": f["id"], "status": "done", "reply": "اتعمل"})["items"]
        mine = next(x for x in items if x["id"] == f["id"])
        self.assertEqual((mine["status"], mine["reply"]), ("done", "اتعمل"))
        self.call("POST", "/api/feedback", 422, json={"id": f["id"], "status": "lost"})
        self.call("POST", "/api/feedback", 404, json={"id": "missing", "status": "seen"})

    def test_marking_many_items_at_once(self):
        r = self.call("POST", "/api/items/flags", json={"item_ids": [self.pan, self.box, "not-mine"], "rx_required": True})
        self.assertEqual(r["updated"], 2)                                     # only this pharmacy's items
        self.assertEqual(self.db.one("SELECT rx_required FROM items WHERE id=?", (self.box,))["rx_required"], 1)
        self.call("POST", "/api/items/flags", 422, json={"item_ids": [], "rx_required": True})
        self.call("POST", "/api/items/flags", 422, json={"item_ids": [self.pan]})
        self.call("POST", "/api/items/flags", 422, json={"item_ids": ["x"] * 501, "rx_required": True})

    def test_a_refill_reminder_is_removed(self):
        cid = self.call("POST", "/api/customers", 201, json={"name": "عميل تجربة"})["id"]
        self.call("POST", f"/api/customers/{cid}/refills", json={"item_id": self.pan, "qty_base": 2, "every_days": 30})
        n = lambda: self.db.one("SELECT count(*) n FROM customer_refills WHERE org_id=? AND customer_id=?",  # noqa: E731
                                (self.ctx.org_id, cid))["n"]
        self.assertEqual(n(), 1)
        self.call("POST", f"/api/customers/{cid}/refills", json={"item_id": self.pan, "remove": True})
        self.assertEqual(n(), 0)
