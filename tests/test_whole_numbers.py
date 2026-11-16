"""Typed whole numbers are refused when they are not whole, never cut down (AGENTS.md: no truncation).

`int()` turned a counted 2.5 strips into 2 (the count then adjusted the stock), 2.5 notes in the
drawer count into 2, 26.5 work days into 26 and a 7.5-day return window into 7, each without a word.
Synthetic data only.
"""
from app.core import people
from tests.test_owner_plan_priority_1_2 import PlanCase

PW = "Staff-Pass-2026"


class WholeNumbers(PlanCase):
    def test_a_stock_count_of_two_and_a_half_strips_is_refused(self):
        cid = self.call("POST", "/api/counts", 201, json={"title": "جرد تجربة", "scope": "items",
                                                          "item_ids": [self.pan]})["id"]
        for bad in (18.5, "18.5", True, None, "x"):
            with self.subTest(bad=bad):
                self.call("POST", f"/api/counts/{cid}/entries", 422, json={"item_id": self.pan, "qty": bad})
        v = self.call("GET", f"/api/counts/{cid}/variances")
        self.assertEqual(v["lines"], [])                                    # nothing was recorded
        self.call("POST", f"/api/counts/{cid}/entries", json={"item_id": self.pan, "qty": "18"})
        self.call("POST", f"/api/counts/{cid}/entries", json={"item_id": self.pan, "qty": 18.0})   # whole: fine
        v = self.call("GET", f"/api/counts/{cid}/variances")
        self.assertEqual([(l["item_id"], l["difference"]) for l in v["lines"]], [(self.pan, -2)])

    def test_a_drawer_count_of_two_and_a_half_notes_is_refused(self):
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})
        for bad in ({"10000": 1.5}, {"10000": "1.5"}, {"10000": True}, {"10000": -1}, {"333": 1}):
            with self.subTest(bad=bad):
                self.call("POST", "/api/cash/close", 422, json={"denominations": bad, "note": "تجربة"})
        self.assertIn("expected_cash", self.call("GET", "/api/cash/current"))   # the shift is still open
        r = self.call("POST", "/api/cash/close", json={"denominations": {"10000": 1}, "note": "تجربة"})
        self.assertEqual((r["counted_cash"], r["difference"]), (10000, 0))

    def test_work_days_must_be_whole(self):
        uid = people.add_user(self.db, self.ctx, username="staff1", full_name="موظف", password=PW, role_code="assistant")
        for bad in (26.5, "26.5", True):
            with self.subTest(bad=bad):
                self.call("POST", "/api/staff", 422, json={"action": "pay_settings", "user_id": uid,
                                                           "monthly_salary": 600000, "work_days": bad,
                                                           "month": self.today[:7]})
        self.assertIsNone(self.db.one("SELECT work_days FROM staff_pay WHERE user_id=?", (uid,)))
        self.call("POST", "/api/staff", json={"action": "pay_settings", "user_id": uid, "monthly_salary": 600000,
                                              "work_days": 26, "month": self.today[:7]})
        self.assertEqual(self.db.one("SELECT work_days FROM staff_pay WHERE user_id=?", (uid,))["work_days"], 26)

    def test_the_return_window_must_be_whole_days(self):
        before = self.call("GET", "/api/settings/sales")["return_window_days"]
        for bad in (7.5, "7.5", True):
            with self.subTest(bad=bad):
                self.call("PUT", "/api/settings/sales", 422, json={"return_window_days": bad})
        self.assertEqual(self.call("GET", "/api/settings/sales")["return_window_days"], before)
        self.assertEqual(self.call("PUT", "/api/settings/sales", json={"return_window_days": "7"})["return_window_days"], 7)


class ScreensSendWhatWasTyped(__import__("unittest").TestCase):
    """The count and return screens cut 2.5 to 2 with parseInt before the server could refuse it."""

    def test_count_and_return_screens_do_not_cut(self):
        from pathlib import Path
        js = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('const qty = typedWhole($("#cntQty").value);', js)
        self.assertNotIn('parseInt($("#cntQty").value', js)
        self.assertIn("const v = typedWhole(i.value); if (Number(v) > 0) lines[i.dataset.ret] = v;", js)
        self.assertNotIn("const v = parseInt(i.value, 10); if (v > 0) lines[i.dataset.ret] = v;", js)


class WholeNumberRange(PlanCase):
    """A number too big for the browser to hold exactly (or for SQLite at all) is refused with 422.
    Before: 10**21 as an opening float reached SQLite and answered 500 (OverflowError)."""

    def test_the_helper(self):
        from app.core.numbers import MAX_SAFE, whole_number
        self.assertEqual(whole_number(MAX_SAFE), MAX_SAFE)
        self.assertEqual(whole_number(-MAX_SAFE), -MAX_SAFE)
        for bad in (MAX_SAFE + 1, -MAX_SAFE - 1, 10**21, 1e21, "62212345678906221234567890", 2**63):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                whole_number(bad)

    def test_an_opening_float_too_big_is_a_422_not_a_500(self):
        for bad in (10**21, 1e21, 2**63, "62212345678906221234567890"):
            with self.subTest(bad=bad):
                self.call("POST", "/api/cash/open", 422, json={"opening_cash": bad})
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})
        self.call("POST", "/api/cash/close", 422, json={"counted_cash": 10**21})
