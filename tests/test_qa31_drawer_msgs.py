"""#500 follow-ups (QA-31). Synthetic data only.
(a) Voiding an old cash invoice when the drawer lacks the cash: the refusal says what to do.
(b) Shift close with the float box emptied: the preview shows the float the close will really leave
    (the opening cash, or less when the notes cannot make it) and the amount moving to the treasury."""
from pathlib import Path

from app.core.errors import Conflict
from app.sales import shift_tools as st
from app.sales.cash import CashDrawer
from tests import test_sale_exchange
from tests.test_owner_plan_priority_1_2 import PlanCase

JS = (Path(__file__).resolve().parents[1] / "app" / "web" / "app.js").read_text(encoding="utf-8")


class TestVoidEmptyDrawerMessage(test_sale_exchange.Base):
    def empty_next_shift(self):
        self.drawer = CashDrawer(self.db)
        sid = self.sell(self.a, 1)
        self.drawer.close(self.ctx, denominations={"1000": 1}, float_left=0)
        self.drawer.open(self.ctx, 0)
        return sid

    def test_message_says_how_to_put_the_cash_in_the_drawer(self):
        sid = self.empty_next_shift()
        with self.assertRaises(Conflict) as error:
            self.s.void(self.ctx, sid, "إلغاء تجربة")
        e = error.exception
        self.assertEqual(e.code, "drawer_negative")
        self.assertIn("10.00", str(e))                 # the amount owed back to the customer
        self.assertIn("الخزنة", str(e))
        self.assertIn("ألغِ الفاتورة تاني", str(e))
        self.assertEqual(e.details["amount"], 1000)

    def test_other_drawer_outflows_keep_their_message(self):
        sid = self.empty_next_shift()
        with self.assertRaises(Conflict) as error:
            self.s.return_items(self.ctx, sid, {self.line(sid): 1}, refund_method="cash", reason="مرتجع تجربة")
        self.assertNotIn("ألغِ الفاتورة", str(error.exception))


class TestEmptyFloatPreview(PlanCase):
    def setUp(self):
        super().setUp()
        self.shift = CashDrawer(self.db).open(self.ctx, 7500)

    def test_default_float_is_the_one_rule(self):
        self.assertEqual(st.default_float({"5000": 2}, 7500, 10000), 5000)
        self.assertEqual(st.default_float({"5000": 1}, 2000, 5000), 0)
        self.assertEqual(st.default_float({"5000": 2, "2000": 1, "500": 1}, 7500, 12500), 7500)
        self.assertEqual(st.default_float({}, 7500, 3000), 3000)          # no notes: the plain amount

    def test_preview_of_an_emptied_box_matches_the_close(self):
        sp = self.call("POST", "/api/cash/float-preview", json={"denominations": {"5000": 2}, "empty_float": True})
        self.assertEqual((sp["float_left"], sp["to_treasury"], sp["wanted"]), (5000, 5000, 7500))
        r = self.call("POST", "/api/cash/close", json={"denominations": {"5000": 2}, "note": "فرق تجربة",
                                                       "float_left": None, "handover": False})
        self.assertEqual(r["float_left"], sp["float_left"])

    def test_screen_previews_the_emptied_box_and_confirms_before_saving(self):
        for text in ("empty_float: true", "هيفضل في الدرج", "ويتحوّل للخزنة"):
            self.assertTrue(text in JS, text)


class TestCountChangedWhileConfirming(PlanCase):
    """Codex P2 on #505: a note count edited while the preview loads must not be saved with the old count."""

    def test_submit_rechecks_the_count_after_the_preview(self):
        at = JS.index("if (!lastSplit) await showSplit();")
        after = JS[at:at + 700]
        self.assertTrue("countKey() !== key" in after, after)
        self.assertTrue("العدد اتغيّر" in after, after)
