"""UX-27 PR3 (owner 26/11, final): automatic rounding none / 0.25 / 0.5 / 1 EGP to the nearest (U2-14, unchanged)
plus a manual round button at cash payment, within a small limit the owner sets (default 1.00, 0 = off, at most
5.00). The manual rounding is stored as sales.rounding, so the drawer, reports and returns treat it like the
automatic one: returns after a manual round never refund more than was received. Synthetic data."""
import json
import re
import shutil
import subprocess
import unittest
from dataclasses import replace
from pathlib import Path

from app.core.errors import Forbidden, ValidationFailed
from app.sales import rounding
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_owner_plan_priority_1_2 import PlanCase
from tests.test_u2_14_invoice_rounding import Rounded

JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")


class TestLimitSetting(Rounded):
    def test_default_bounds_and_permission(self):
        self.assertEqual(rounding.manual_limit(self.db, self.ctx.org_id), 100)
        self.assertEqual(rounding.set_manual_limit(self.db, self.ctx, 50), 50)
        self.assertEqual(rounding.manual_limit(self.db, self.ctx.org_id), 50)
        for bad in (-1, 501, 2.5, "50", True, None):
            with self.assertRaises(ValidationFailed):
                rounding.set_manual_limit(self.db, self.ctx, bad)
        with self.assertRaises(Forbidden):
            rounding.set_manual_limit(self.db, replace(self.ctx, permissions=frozenset()), 25)
        self.assertEqual(self.s.get(self.ctx, self.s.create(self.ctx))["manual_rounding_limit"], 50)


class TestManualRound(Rounded):
    def test_round_down_to_the_pound_is_stored_as_the_rounding(self):
        sid, d = self.sale(units=2, disc=20)                             # 37.80 (auto 0.50 -> 38.00)
        self.assertEqual(d["total"], 3780)
        start = self.cd.summary(self.ctx)["expected_cash"]
        self.pay(sid, paid_cash=3700, manual_rounding=-80)               # the customer has 37.00
        self.assertEqual(tuple(self.row(sid)), (3780, -80, 3700, 0, 0))
        self.assertEqual(self.cd.summary(self.ctx)["expected_cash"], start + 3700)
        a = self.db.one("SELECT after_json FROM audit_log WHERE action='sale.complete' AND entity_id=?", (sid,))
        self.assertTrue(json.loads(a["after_json"])["manual_rounding"])

    def test_refusals(self):
        sid, _ = self.sale(units=2, disc=20)                             # 37.80
        for value, paid in ((-180, 3600), (-81, 3699), (1.5, 3780), (True, 3781), ("-80", 3700), (0, 3780),
                            (120, 3900)):
            with self.assertRaises(ValidationFailed, msg=value):
                self.pay(sid, paid_cash=paid, manual_rounding=value)
        with self.assertRaises(ValidationFailed):                        # paid must match total + manual
            self.pay(sid, paid_cash=3800, manual_rounding=-80)
        with self.assertRaises(ValidationFailed):                        # no cash part: nothing to round
            self.pay(sid, paid_card=3700, manual_rounding=-80)
        rounding.set_manual_limit(self.db, self.ctx, 0)
        with self.assertRaises(ValidationFailed) as e:
            self.pay(sid, paid_cash=3700, manual_rounding=-80)
        self.assertEqual(e.exception.code, "manual_rounding_off")
        self.assertEqual(self.row(sid)["paid_cash"], 0)                  # still a draft

    def test_only_the_pound_just_below_or_above(self):
        # review P1: a bigger jump inside the limit would be a hidden discount / overcharge past the discount limit
        rounding.set_manual_limit(self.db, self.ctx, 500)
        sid, _ = self.sale(units=2, disc=20)                             # 37.80
        for value, paid in ((-280, 3500), (220, 4000), (-180, 3600)):
            with self.assertRaises(ValidationFailed, msg=value) as e:
                self.pay(sid, paid_cash=paid, manual_rounding=value)
            self.assertEqual(e.exception.code, "manual_rounding_whole")
        a, _ = self.sale(units=2, disc=0)                                # 38.00: already a whole pound
        for value, paid in ((-100, 3700), (100, 3900)):
            with self.assertRaises(ValidationFailed):
                self.pay(a, paid_cash=paid, manual_rounding=value)

    def test_a_split_takes_no_manual_rounding(self):
        sid, _ = self.sale(units=2, disc=20)                             # 37.80: 20.00 card + 17.80 cash
        with self.assertRaises(ValidationFailed) as e:
            self.pay(sid, paid_card=2000, paid_cash=1800, manual_rounding=20)
        self.assertEqual(e.exception.code, "manual_rounding_no_cash")

    def test_full_return_after_a_manual_round_down_never_refunds_more(self):
        sid, d = self.sale(units=2, disc=20)
        lid = d["lines"][0]["id"]
        self.pay(sid, paid_cash=3700, manual_rounding=-80)
        r1 = self.s.return_items(self.ctx, sid, {lid: 1}, refund_method="cash", reason="test")
        r2 = self.s.return_items(self.ctx, sid, {lid: 1}, refund_method="cash", reason="test")
        self.assertEqual(r1["refund"], 1890)                            # a line value
        self.assertEqual(r1["refund"] + r2["refund"], 3700)              # exactly what he paid

    def test_full_return_after_a_manual_round_up(self):
        sid, d = self.sale(units=2, disc=20)
        self.pay(sid, paid_cash=3800, manual_rounding=20)
        r = self.s.return_items(self.ctx, sid, {d["lines"][0]["id"]: 2}, refund_method="cash", reason="test")
        self.assertEqual(r["refund"], 3800)

    def test_void_after_a_manual_round_gives_back_the_cash_taken(self):
        start = self.cd.summary(self.ctx)["expected_cash"]
        sid, _ = self.sale(units=2, disc=20)
        self.pay(sid, paid_cash=3700, manual_rounding=-80)
        self.s.void(self.ctx, sid, reason="غلط في الفاتورة")
        self.assertEqual(self.cd.summary(self.ctx)["expected_cash"], start)

    def test_exchange_takes_no_manual_rounding(self):
        sid, _ = self.sale(units=1, disc=10)                             # 18.90
        with self.assertRaises(ValidationFailed) as e:
            self.s.complete(self.ctx, sid, paid_cash=1800, manual_rounding=-90, round_cash=False,
                            safety_decisions=self.ack_all(sid))
        self.assertEqual(e.exception.code, "manual_rounding_no_cash")


class TestRoute(PlanCase):
    def test_get_and_put(self):
        self.assertEqual(self.call("GET", "/api/settings/rounding"), {"step": 0, "manual_limit": 100})
        self.call("PUT", "/api/settings/rounding", 422, json={"step": 50, "manual_limit": 600})
        self.assertEqual(self.call("GET", "/api/settings/rounding"), {"step": 0, "manual_limit": 100})  # nothing saved
        self.assertEqual(self.call("PUT", "/api/settings/rounding", json={"manual_limit": 50}),
                         {"step": 0, "manual_limit": 50})


def _fn(name):
    m = re.search(rf"\nfunction {name}\(.*?\n\}}\n", JS, re.S)
    return m.group(0) if m else ""


@unittest.skipUnless(shutil.which("node"), "Node not available")
class TestChoices(unittest.TestCase):
    def test_whole_pound_below_and_above_within_the_limit(self):
        src = _fn("manualRoundChoices") + r"""console.log(JSON.stringify([
  manualRoundChoices(3780, 100), manualRoundChoices(3780, 50), manualRoundChoices(3730, 50), manualRoundChoices(3700, 100),
  manualRoundChoices(3780, 0), manualRoundChoices(50, 100), manualRoundChoices(0, 100),
]));"""
        r = subprocess.run([shutil.which("node"), "-e", src], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), [[-80, 20], [20], [-30], [], [], [50], []])

    def test_setup_hint_no_longer_says_a_line_prints(self):
        self.assertNotIn("التقريب بيظهر سطر لوحده في الفاتورة", JS)
        self.assertIn("الإجمالي في الإيصال بيطلع بعد التقريب", JS)


SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posQ');
  await p.fill('#posQ', 'Trial Box');
  await p.waitForSelector('#posRes [data-add]');
  await p.click('#posRes [data-add][data-kind=unit]');          // 33.33
  await p.waitForSelector('#posSide [data-mround]');
  const out = { choices: await p.$$eval('#posSide [data-mround]', (b) => b.map((x) => x.textContent.trim())) };
  out.dueAuto = await p.textContent('#posSide .sum.due b');
  await p.fill('#payGiven', '50');                              // the tender typed first (Codex on #492)
  await p.click('#posSide [data-mround="-33"]');
  await p.waitForSelector('#posSide [data-mround="off"]');
  out.dueManual = await p.textContent('#posSide .sum.due b');
  out.given = await p.inputValue('#payGiven');
  out.change = await p.textContent('#changeV');
  out.label = await p.textContent('#posSide .panel');
  const [res] = await Promise.all([p.waitForResponse((r) => r.url().endsWith('/complete')), p.click('#posDone')]);
  out.status = res.status();
  out.sent = JSON.parse(res.request().postData());
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestScreen(PlanCase):
    def setUp(self):
        super().setUp()
        from app.sales.cash import CashDrawer
        CashDrawer(self.db).open(self.ctx, 10000)
        rounding.set_step(self.db, self.ctx, 50)

    def test_round_button_at_cash_payment(self):
        out = run_node(self, SCRIPT, "owner", OWNER_PW)
        self.assertEqual(out["choices"], ["33.00 ج", "34.00 ج"])
        self.assertEqual(out["dueAuto"], "33.50")                     # nearest half pound
        self.assertEqual(out["dueManual"], "33.00")
        self.assertEqual((out["given"], out["change"]), ("50", "17.00"))     # kept, change against the new total
        self.assertEqual(out["sent"]["cash_given"], 5000)
        self.assertIn("تقريب يدوي", out["label"])
        self.assertEqual(out["status"], 200)
        self.assertEqual(out["sent"]["manual_rounding"], -33)
        self.assertEqual(out["sent"]["payments"][0]["amount"], 3300)
        row = self.db.one("SELECT total, rounding, paid_cash FROM sales WHERE status='completed'")
        self.assertEqual(tuple(row), (3333, -33, 3300))


if __name__ == "__main__":
    unittest.main()
