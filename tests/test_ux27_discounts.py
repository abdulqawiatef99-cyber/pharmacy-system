"""UX-27 PR2 (owner's live use 26/11): a line or invoice discount is typed in an EGP field or a percent field side by
side (typing in one fills the other), the typed one is saved exactly (piastres / basis points), and the screen
remembers the last mode each person used on this PC. Each role's maximum discount still holds. Synthetic data."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from app.core.errors import Forbidden
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_fixed_discounts import FixedCase
from tests.test_owner_plan_priority_1_2 import PlanCase

JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")


def _const(name):
    m = re.search(rf"\nconst {name} = .*?;\n", JS, re.S)
    return m.group(0) if m else ""


def _fn(name):
    m = re.search(rf"\nfunction {name}\(.*?\n\}}\n", JS, re.S)
    return m.group(0) if m else ""


@unittest.skipUnless(shutil.which("node"), "Node not available")
class TestRules(unittest.TestCase):
    def run_js(self, body):
        src = ("const money = (p) => (p == null ? '' : (p / 100).toFixed(2));"
               "const pct = (bp) => (bp == null ? '' : (bp / 100).toString());"
               "const discountOf = (gross, bp) => (bp ? Math.floor((gross * bp + 5000) / 10000) : 0);"
               "const toast = () => {}; const toBp = (v) => (v === '' || v == null ? null : Math.round(parseFloat(v) * 100));"
               "const store = {}; const localStorage = { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; } };"
               "let ME = { user: { id: 'u1' } };"
               "const DISC_MODES = ['egp', 'pct'];"
               + _const("discText") + "".join(_fn(n) for n in ("exactPiasters", "discMode", "setDiscMode", "discOther", "discBody", "discAsk"))
               + body)
        r = subprocess.run([shutil.which("node"), "-e", src], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_one_field_fills_the_other(self):
        out = self.run_js(r"""
console.log(JSON.stringify([
  discOther('egp', '10', 10000), discOther('egp', '3.33', 10000), discOther('egp', '5', 9500),
  discOther('pct', '10', 13333), discOther('pct', '2.5', 999), discOther('pct', '', 1000),
  discOther('egp', '200', 10000),   // more than the line: nothing
  discOther('pct', '101', 1000), discOther('egp', '1.234', 1000), discOther('egp', '5', 0), discOther('egp', '-1', 100),
]));
""")
        self.assertEqual(out, ["10", "3.33", "5.26", "13.33", "0.25", "", "", "", "", "", ""])

    def test_the_saved_body_is_exact(self):
        out = self.run_js(r"""
console.log(JSON.stringify([
  discBody('egp', '3'), discBody('egp', '3.5'), discBody('egp', ''), discBody('egp', '1.234'), discBody('egp', '-2'),
  discBody('pct', '5'), discBody('pct', '2.75'), discBody('pct', ''), discBody('pct', '100.01'), discBody('pct', 'x'),
  discBody('egp', '٢٫٥'), discBody('egp', '2ز5'), discBody('pct', '5%'), discBody('egp', '3 ج'),   // Arabic keyboard
]));
""")
        self.assertEqual(out, [{"discount_amount": 300}, {"discount_amount": 350}, {"discount_amount": 0}, None, None,
                               {"discount_bp": 500}, {"discount_bp": 275}, {"discount_bp": 0}, None, None,
                               {"discount_amount": 250}, {"discount_amount": 250}, {"discount_bp": 500},
                               {"discount_amount": 300}])

    def test_mode_is_per_person_and_drives_a_bare_number(self):
        out = self.run_js(r"""
const a = discMode(); setDiscMode('egp'); const b = discMode();
ME = { user: { id: 'u2' } }; const c = discMode();
ME = { user: { id: 'u1' } }; setDiscMode('nonsense'); const d = discMode();
console.log(JSON.stringify([a, b, c, d, discAsk('5', 'egp'), discAsk('5%', 'egp'), discAsk('5', 'pct'), discAsk('3ج', 'pct'),
  discAsk('5.555', 'pct'), discAsk('5abc', 'pct'), discAsk('101', 'pct')]));
""")
        self.assertEqual(out, ["pct", "egp", "pct", "egp", {"discount_amount": 500}, {"discount_bp": 500},
                               {"discount_bp": 500}, {"discount_amount": 300}, None, None, None])

    def test_the_dropdown_is_gone(self):
        self.assertNotIn("data-dunit", JS)
        self.assertNotIn("posInvDiscU", JS)
        self.assertIn('data-damt="${esc(l.id)}"', JS)
        self.assertIn('id="posInvAmt"', JS)


class TestRoleLimit(FixedCase):
    """The role's maximum is the server's: an EGP amount is held to it as its percent of the line."""

    def test_both_modes_held_to_the_role_max(self):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.cheap, units=3)               # 30.00, cashier max 5%
        lid = d["lines"][0]["id"]
        d = self.s.update_line(self.cashier, sid, lid, discount_amount=150)  # exactly 5%
        self.assertEqual(d["lines"][0]["net"], 2850)
        with self.assertRaises(Forbidden):
            self.s.update_line(self.cashier, sid, lid, discount_amount=151)
        with self.assertRaises(Forbidden):
            self.s.update_line(self.cashier, sid, lid, discount_bp=501)
        with self.assertRaises(Forbidden):
            self.s.set_invoice_discount(self.cashier, sid, amount=151)


SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw] = process.argv.slice(1);
""" + LOGIN + r"""
  const saved = [];
  p.on('request', (r) => { if (/\/lines\/|invoice-discount/.test(r.url()) && ['PATCH', 'PUT'].includes(r.method())) saved.push(JSON.parse(r.postData())); });
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posQ');
  await p.fill('#posQ', 'Trial Box');
  await p.waitForSelector('#posRes [data-add]');
  await p.click('#posRes [data-add][data-kind=pack]');
  await p.waitForSelector('#posCart [data-damt]');
  const out = { firstMode: await p.evaluate(() => document.querySelector('#posCart .disc2 input').hasAttribute('data-disc')) };
  // EGP typed: the percent fills, the amount is saved
  await p.fill('#posCart [data-damt]', '10');
  out.pctShown = await p.inputValue('#posCart [data-disc]');
  await p.press('#posCart [data-damt]', 'Tab');
  await p.waitForFunction(() => document.querySelector('#posCart tbody tr td.num b').textContent === '90.00');
  out.net1 = await p.textContent('#posCart tbody tr td.num b');
  // the screen opens again with EGP first (remembered for this person, once the save succeeded)
  await p.reload();
  await p.waitForSelector('#posCart [data-damt]');
  out.egpFirst = await p.evaluate(() => document.querySelector('#posCart .disc2 input').hasAttribute('data-damt'));
  out.amtKept = await p.inputValue('#posCart [data-damt]');
  out.pctKept = await p.inputValue('#posCart [data-disc]');
  // percent typed: the EGP fills, the percent is saved
  await p.fill('#posCart [data-disc]', '5');
  out.amtShown = await p.inputValue('#posCart [data-damt]');
  await p.press('#posCart [data-disc]', 'Tab');
  await p.waitForFunction(() => document.querySelector('#posCart tbody tr td.num b').textContent === '95.00');
  // invoice discount in EGP: its percent is of what is left after the line discount
  await p.waitForSelector('#posInvAmt');
  await p.fill('#posInvAmt', '5');
  out.invPct = await p.inputValue('#posInvDisc');
  await p.press('#posInvAmt', 'Tab');
  await p.waitForFunction(() => document.querySelector('#posCart tbody tr td.num b').textContent === '90.00');
  out.mode = await p.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith('discMode:')).map((k) => localStorage.getItem(k)));
  out.saved = saved;
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

    def test_egp_and_percent_side_by_side(self):
        out = run_node(self, SCRIPT, "owner", OWNER_PW)
        self.assertTrue(out["firstMode"])                 # a new person starts with percent first, as before
        self.assertEqual(out["pctShown"], "10")
        self.assertEqual(out["net1"], "90.00")
        self.assertTrue(out["egpFirst"])
        self.assertEqual((out["amtKept"], out["pctKept"]), ("10.00", "10"))
        self.assertEqual(out["amtShown"], "5.00")
        self.assertEqual(out["invPct"], "5.26")           # 5.00 of 95.00
        self.assertEqual(out["mode"], ["egp"])
        # what was saved is the typed field, never the derived one
        self.assertEqual(out["saved"], [{"discount_amount": 1000}, {"discount_bp": 500}, {"amount": 500}])


if __name__ == "__main__":
    unittest.main()
