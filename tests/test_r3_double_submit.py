"""A double click or a double Enter on a money form must post once. Synthetic data only.

The screen (app.js `api`) sends the same money POST again within 4 seconds with the SAME Idempotency-Key,
and every one of the five routes behind those forms answers a repeated key without posting twice.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from app.catalog import service as cat
from app.core import people
from app.finance import expenses as fin, schedules
from app.sales.cash import CashDrawer
from app.sales.service import Sales
from tests.r3_debt import owe
from tests.test_api import APICase, H

JS = (Path(__file__).resolve().parents[1] / "app" / "web" / "app.js").read_text(encoding="utf-8")


class TestScreenReusesTheKey(unittest.TestCase):
    def keys(self, calls):
        """Run api() for each (path, body, wait_ms_before) and return the Idempotency-Key each one sent."""
        if not shutil.which("node"):
            self.skipTest("node is not installed")
        start = JS.index("const API = { busy: 0, last: 0 };")
        end = JS.index("\n}\n", JS.index("async function api(")) + 3
        harness = """
let APPROVAL = null, ME = {}, n = 0, clock = 1000; const location = { hash: "" }, sent = [];
Date.now = () => clock;
function newKey() { return "k" + (++n); }
function approvalHeaders() { return {}; }
async function askApproval() { return false; }
async function fetch(path, opts) { sent.push(opts.headers["Idempotency-Key"] ?? null); return { ok: true, status: 200, json: async () => ({}) }; }
"""
        code = harness + JS[start:end] + """
(async () => {
  for (const [path, body, wait] of %s) { clock += wait; await api(path, { method: "POST", body }); }
  console.log(JSON.stringify(sent));
})();
""" % json.dumps(calls)
        out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_the_same_money_post_within_4_seconds_keeps_its_key(self):
        for path, body in (("/api/customers/c1/payments", {"amount": 5000, "method": "cash"}),
                           ("/api/expenses", {"category_id": "x", "amount": 100}),
                           ("/api/staff", {"action": "advance", "user_id": "u", "amount": 100}),
                           ("/api/expense-schedules/s1", {"action": "pay", "amount": None}),
                           ("/api/suppliers/s1/pay", {"amount": 100, "method": "cash"})):
            with self.subTest(path=path):
                a, b = self.keys([[path, body, 0], [path, body, 300]])
                self.assertEqual(a, b)

    def test_a_different_amount_or_a_later_press_is_a_new_request(self):
        p = "/api/customers/c1/payments"
        a, b, c, d = self.keys([[p, {"amount": 5000}, 0], [p, {"amount": 6000}, 100], [p, {"amount": 5000}, 100],
                                [p, {"amount": 5000}, 4500]])
        self.assertNotEqual(a, b)
        self.assertEqual(a, c)
        self.assertNotEqual(a, d)

    def test_scanning_the_same_item_twice_stays_two_lines(self):
        a, b = self.keys([["/api/sales/s1/lines", {"item_id": "i", "units": 1}, 0],
                          ["/api/sales/s1/lines", {"item_id": "i", "units": 1}, 200]])
        self.assertNotEqual(a, b)

    def test_the_five_forms_switch_their_button_off_while_the_request_runs(self):
        self.assertIn("async function withBusy(btn, fn)", JS)
        for marker in ("`/api/suppliers/${id}/pay`", "`/api/customers/${id}/payments`", '"/api/expenses", { method: "POST", body: { category_id',
                       "`/api/expense-schedules/${b.dataset.scpay}`", '"/api/staff", { method: "POST"'):
            with self.subTest(marker=marker):
                at = JS.index(marker)
                self.assertIn("withBusy(", JS[at - 260:at])


class TestARepeatedKeyPostsOnce(APICase):
    def setUp(self):
        super().setUp()
        fin.ensure_defaults(self.db)
        self.cat_id = fin.categories(self.db, self.ctx)[0]["id"]
        CashDrawer(self.db).open(self.ctx, 1000000)
        self.http_login()

    def twice(self, path, body, key):
        h = {**H, "Idempotency-Key": key}
        a = self.c.post(path, headers=h, json=body)
        b = self.c.post(path, headers=h, json=body)
        self.assertIn(a.status_code, (200, 201), a.text)
        self.assertEqual(b.status_code, a.status_code, b.text)
        self.assertEqual(b.headers.get("Idempotent-Replay"), "true")
        self.assertEqual(b.json(), a.json())

    def count(self, sql, *params):
        return self.db.one(sql, params)[0]

    def test_new_expense(self):
        self.twice("/api/expenses", {"category_id": self.cat_id, "amount": 2500, "paid_from": "drawer"}, "e-1")
        self.assertEqual(self.count("SELECT count(*) FROM expenses"), 1)

    def test_scheduled_expense_pay(self):
        s = schedules.save(self.db, self.ctx, {"name": "rent", "category_id": self.cat_id, "amount": 500000,
                                                "day_of_month": 1, "paid_from": "drawer"})
        self.twice(f"/api/expense-schedules/{s['id']}", {"action": "pay", "amount": None}, "s-1")
        self.assertEqual(self.count("SELECT count(*) FROM expenses WHERE schedule_id=?", s["id"]), 1)

    def test_salary_advance(self):
        uid = people.add_user(self.db, self.ctx, username="asst", full_name="A", password="Staff-Pass-2026",
                              role_code="assistant")
        self.twice("/api/staff", {"action": "advance", "user_id": uid, "amount": 10000, "month": "2026-10",
                                  "paid_from": "outside"}, "a-1")
        self.assertEqual(self.count("SELECT count(*) FROM staff_advances"), 1)

    def test_supplier_payment(self):
        sup = cat.create_supplier(self.db, self.ctx, name="مورد تجريبي")
        self.twice(f"/api/suppliers/{sup}/pay", {"amount": 5000, "method": "cash"}, "p-1")
        self.assertEqual(self.count("SELECT count(*) FROM supplier_ledger WHERE kind='payment'"), 1)

    def test_customer_collection(self):
        cid = Sales(self.db).create_customer(self.ctx, name="عميل تجربة", credit_limit=100000)
        owe(self.db, self.ctx, cid, 9000)
        self.twice(f"/api/customers/{cid}/payments", {"amount": 4000, "method": "cash"}, "c-1")
        self.assertEqual(self.count("SELECT count(*) FROM customer_ledger WHERE kind='payment'"), 1)


if __name__ == "__main__":
    unittest.main()
