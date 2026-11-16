"""Scheduled expense pay: the month the screen showed is the month paid, once, and only by who may approve
expenses. Synthetic data only."""
import uuid
from pathlib import Path

from starlette.testclient import TestClient

from app.core import people
from app.core.errors import Forbidden, ValidationFailed
from app.finance import expenses as fin, schedules
from app.sales.cash import CashDrawer
from tests.test_api import APICase, H

PW = "Staff-Pass-2026"
JS = (Path(__file__).resolve().parents[1] / "app" / "web" / "app.js").read_text(encoding="utf-8")


def hk():
    return {**H, "Idempotency-Key": uuid.uuid4().hex}


class TestSchedulePayPeriod(APICase):
    def setUp(self):
        super().setUp()
        fin.ensure_defaults(self.db)
        self.cat_id = fin.categories(self.db, self.ctx)[0]["id"]
        CashDrawer(self.db).open(self.ctx, 1000000)
        self.http_login()
        self.s = schedules.save(self.db, self.ctx, {"name": "rent", "category_id": self.cat_id, "amount": 500000,
                                                     "day_of_month": 1, "every_months": 1, "paid_from": "drawer"})
        self.url = f"/api/expense-schedules/{self.s['id']}"

    def rows(self):
        return [r["schedule_period"] for r in self.db.all(
            "SELECT schedule_period FROM expenses WHERE org_id=? AND schedule_id=? ORDER BY schedule_period",
            (self.ctx.org_id, self.s["id"]))]

    def test_a_second_press_with_a_new_key_does_not_pay_the_next_month(self):
        period = self.s["next"]["period"]
        body = {"action": "pay", "amount": None, "period": period}
        a = self.c.post(self.url, headers=hk(), json=body)
        self.assertEqual(a.status_code, 200, a.text)
        self.assertEqual(a.json()["schedule_period"], period)
        b = self.c.post(self.url, headers=hk(), json=body)
        self.assertEqual(b.status_code, 422, b.text)
        self.assertEqual(self.rows(), [period])

    def test_without_a_period_the_first_open_month_is_paid_as_before(self):
        a = self.c.post(self.url, headers=hk(), json={"action": "pay", "amount": None})
        self.assertEqual(a.json()["schedule_period"], self.s["next"]["period"])

    def test_a_month_that_is_not_one_of_its_due_months_is_refused(self):
        two = schedules.save(self.db, self.ctx, {"name": "water", "category_id": self.cat_id, "amount": 1000,
                                                 "day_of_month": 1, "every_months": 2, "start_month": "2026-01"})
        for bad in ("2026-02", "2025-11", "2026-1", " 2026-03", "2026-13", 202603, True):
            with self.subTest(period=bad), self.assertRaises(ValidationFailed):
                schedules.pay(self.db, self.ctx, two["id"], period=bad)
        self.assertEqual(schedules.pay(self.db, self.ctx, two["id"], period="2026-03")["schedule_period"], "2026-03")

    def test_an_assistant_cannot_pay_a_schedule(self):
        people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        asst = self.login("asst", PW)
        self.assertTrue(asst.can("expenses.record"))
        with self.assertRaises(Forbidden):
            schedules.pay(self.db, asst, self.s["id"], amount=999900)
        c2 = TestClient(self.app)
        self.addCleanup(c2.close)
        self.http_login("asst", PW, client=c2)
        r = c2.post(self.url, headers=hk(), json={"action": "pay", "amount": 999900})
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(self.rows(), [])

    def test_the_screen_sends_the_month_it_shows(self):
        self.assertIn('data-scpay="${esc(x.id)}" data-p="${esc(x.next.period)}"', JS)
        self.assertIn('body: { action: "pay", period: b.dataset.p,', JS)
