"""Update 2 shift items: U2-26 new shift after a close, U2-28 report lines, U2-29 «دوّر على الفرق», U2-30 float
target per denomination. Synthetic data only."""
import json

from app.core.errors import ValidationFailed
from app.sales import shift_tools as st
from app.sales.cash import CashDrawer
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_owner_plan_priority_1_2 import PlanCase
from tests.test_sales import SalesCase


class TestFloatTarget(SalesCase):
    def test_default_is_the_owners_float(self):
        t = st.get_target(self.db, self.ctx.org_id)
        self.assertEqual(sum(t.values()), 115000)
        self.assertEqual(t["10000"], 30000)

    def test_split_keeps_target_moves_the_rest(self):
        counted = {"20000": 2, "10000": 5, "5000": 6, "2000": 4, "1000": 30, "500": 10, "100": 40, "50": 10, "25": 4}
        sp = st.split_float(counted, st.get_target(self.db, self.ctx.org_id))
        self.assertEqual(sp["keep"], {"10000": 3, "5000": 6, "2000": 4, "1000": 20, "500": 10, "100": 40, "50": 10, "25": 4})
        self.assertEqual(sp["float_left"], 30000 + 30000 + 8000 + 20000 + 5000 + 4000 + 500 + 100)
        self.assertEqual(sp["float_left"] + sp["to_treasury"], sum(int(d) * n for d, n in counted.items()))
        self.assertEqual(sp["short"], 115000 - sp["float_left"])        # fewer 20s and coins than the target

    def test_never_keeps_more_than_counted(self):
        sp = st.split_float({"10000": 1}, st.get_target(self.db, self.ctx.org_id))
        self.assertEqual((sp["keep"], sp["to_treasury"]), ({"10000": 1}, 0))

    def test_save_refuses_impossible_amounts(self):
        with self.assertRaises(ValidationFailed):
            st.save_target(self.db, self.ctx, {"10000": 15000})                   # 150 EGP of 100s
        with self.assertRaises(ValidationFailed):
            st.save_target(self.db, self.ctx, {"10000": -10000})
        with self.assertRaises(ValidationFailed):
            st.save_target(self.db, self.ctx, {"x": 100})
        st.save_target(self.db, self.ctx, {"10000": 20000, "coins": 2500})
        self.assertEqual(st.get_target(self.db, self.ctx.org_id)["10000"], 20000)
        self.assertEqual(st.get_target(self.db, self.ctx.org_id)["5000"], 0)

    def test_float_found_when_greedy_misses_and_amount_only_when_impossible(self):
        sp = st.split_for({"10000": 1, "5000": 1, "2000": 3}, st.get_target(self.db, self.ctx.org_id), 16000)
        self.assertEqual((sp["keep"], sp["float_left"]), ({"10000": 1, "2000": 3}, 16000))       # Codex P2 on #404
        sp = st.split_for({"10000": 1, "5000": 1}, st.get_target(self.db, self.ctx.org_id), 12000)
        self.assertFalse(sp["exact"])
        cd = CashDrawer(self.db)
        r = cd.close(self.ctx, denominations={"10000": 1, "5000": 1, "2000": 3}, note="عدّ تجربة", float_left=16000)
        self.assertEqual(r["float_split"]["keep"], {"10000": 1, "2000": 3})

    def test_close_records_the_notes_kept(self):
        cd = CashDrawer(self.db)
        r = cd.close(self.ctx, denominations={"10000": 5, "5000": 1}, note="عدّ تجربة", float_left=30000)
        self.assertEqual(r["float_left"], 30000)
        self.assertEqual(r["float_split"]["keep"], {"10000": 3})


class TestReportAndHunt(SalesCase):
    def _sale(self, units=1, **pay):
        sid = self.s.create(self.ctx, customer_id=pay.pop("customer", None))
        d = self.s.add_line(self.ctx, sid, self.pan, units=units)
        pay = {k: d["total"] if v == "all" else v for k, v in pay.items()}
        return self.s.complete(self.ctx, sid, safety_decisions=self.ack_all(sid), **pay)

    def test_report_lists_credit_customers_and_discounts(self):
        c = self.s.create_customer(self.ctx, name="عميل آجل", credit_limit=100000)
        self._sale(on_credit="all", customer=c)
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=1)
        d = self.s.update_line(self.ctx, sid, d["lines"][0]["id"], discount_amount=100)
        self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid))
        rep = CashDrawer(self.db).report(self.ctx, CashDrawer(self.db).summary(self.ctx)["id"])
        self.assertEqual([x["customer"] for x in rep["credit_sales"]], ["عميل آجل"])
        self.assertEqual([(x["name"], x["total"]) for x in rep["discount_by_user"]], [("Owner", 100)])

    def test_hunt_finds_fractions_and_mismatch_and_manual_open(self):
        self._sale(paid_cash="all")
        shift = CashDrawer(self.db).summary(self.ctx)["id"]
        with self.db.transaction():                 # an odd-piaster cash sale (imported total 1249.01-like)
            self.db.execute("UPDATE sales SET paid_cash=paid_cash+1, total=total+1 WHERE shift_id=?", (shift,))
            from app.core import audit
            audit.record(self.db, self.ctx, "cash.drawer_open", "cash_shift", shift, after={"reason": "فكة"})
            sale = self.db.one("SELECT id, completed_at FROM sales WHERE shift_id=?", (shift,))
            # a pulse that failed never opened the drawer; an edit after payment is listed
            for aid, action in (("a1", "sale.details"), ("a2", "print.receipt")):     # a reprint is no edit
                self.db.execute("INSERT INTO audit_log (id, org_id, user_id, action, entity_type, entity_id, after_json, at)"
                                " VALUES (?, ?, ?, ?, 'sale', ?, '{}', '9999-01-01T00:00:00Z')",
                                (aid, self.ctx.org_id, self.ctx.user_id, action, sale["id"]))
        h = st.difference_hunt(self.db, self.ctx, shift)
        self.assertEqual(len(h["mismatch"]), 1)               # 1 piaster on the invoice never reached the drawer
        self.assertEqual(len(h["edited_after_payment"]), 1)
        self.assertEqual(h["fractions_piasters"], 1)
        self.assertEqual(len(h["fractions"]), 1)
        self.assertEqual(len(h["manual_openings"]), 1)
        self.assertEqual(h["manual_openings"][0]["reason"], "فكة")
        self.assertTrue(any("قروش" in x for x in h["hints"]))
        _, other = self.second_tenant()
        from app.core.errors import NotFound
        with self.assertRaises(NotFound):
            st.difference_hunt(self.db, other, shift)


class TestApi(PlanCase):
    def setUp(self):
        super().setUp()
        CashDrawer(self.db).open(self.ctx, 30000)

    def test_routes_and_printed_report(self):
        t = self.call("GET", "/api/cash/float-target")
        self.assertEqual(t["coins"], 5000)
        self.call("POST", "/api/cash/float-target", 422, json={"target": {"10000": 1}})
        sp = self.call("POST", "/api/cash/float-preview", json={"denominations": {"10000": 4, "100": 3}})
        self.assertEqual(sp["keep"], {"10000": 3, "100": 3})
        self.call("POST", "/api/cash/float-preview", 422, json={"denominations": {"100": -1}})
        r = self.call("POST", "/api/cash/close", json={"denominations": {"10000": 4}, "note": "فرق تجربة",
                                                       "float_left": 30000, "handover": False})
        self.call("GET", f"/api/cash/shifts/{r['id']}/hunt")
        page = self.c.get(f"/shift-report/{r['id']}", headers={"X-Requested-With": "pharm"}).text
        for text in ("فتح الدرج من غير بيع", "توقيع الكاشير", "اتحوّل للخزنة", "مدة الشيفت", "100 × 3"):
            self.assertIn(text, page)
        self.assertNotIn("يدويًا", page)


SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.goto(base + '/#/shift');
  await p.waitForSelector('#shClose');
  await p.fill('#shClose [data-d="10000"]', '5');
  await p.waitForFunction(() => /سيب في الدرج/.test(document.querySelector('#shSplit').textContent));
  const out = { split: await p.textContent('#shSplit'), fl: await p.inputValue('#shClose input[name=fl]') };
  await p.fill('#shClose input[name=note]', 'فرق تجربة');
  await p.click('#shClose button.big');
  await p.waitForSelector('#shNew');
  await p.click('#shNew');
  await p.waitForSelector('#shOpen');
  out.prefill = await p.inputValue('#shOpen input[name=v]');
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestNewShiftButton(PlanCase):
    def setUp(self):
        super().setUp()
        CashDrawer(self.db).open(self.ctx, 30000)

    def test_new_shift_opens_the_form_with_the_float(self):
        out = run_node(self, SCRIPT, "owner", OWNER_PW)
        self.assertEqual(out["fl"], "300.00")
        self.assertIn("سيب في الدرج", out["split"])
        self.assertEqual(out["prefill"], "300.00")


class TestCodexRound1(PlanCase):
    def setUp(self):
        super().setUp()
        CashDrawer(self.db).open(self.ctx, 30000)

    def test_preview_follows_a_typed_float(self):
        sp = self.call("POST", "/api/cash/float-preview", json={"denominations": {"10000": 5}, "float_left": 10000})
        self.assertEqual((sp["keep"], sp["to_treasury"]), ({"10000": 1}, 40000))
        sp = self.call("POST", "/api/cash/float-preview", json={"denominations": {"10000": 5}, "float_left": 5000})
        self.assertFalse(sp["exact"])
        self.call("POST", "/api/cash/float-preview", 422, json={"denominations": {"10000": 1}, "float_left": 20000})


class TestHuntKeepsLaterVoids(SalesCase):
    def test_a_sale_voided_after_the_close_stays_in_the_hunt(self):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=1)
        self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid))
        cd = CashDrawer(self.db)
        shift = cd.summary(self.ctx)["id"]
        cd.close(self.ctx, counted_cash=0, note="عدّ تجربة", float_left=0)
        cd.open(self.ctx, 0)
        self.s.void(self.ctx, sid, "غلط في الفاتورة")
        with self.db.transaction():
            self.db.execute("UPDATE sales SET paid_cash=paid_cash+1, total=total+1 WHERE id=?", (sid,))
        h = st.difference_hunt(self.db, self.ctx, shift)
        self.assertEqual(len(h["fractions"]), 1)
