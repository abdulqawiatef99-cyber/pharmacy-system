"""UX-27 PR1 (owner's live use 26/11): a box and a strip of the same item go on ONE line in one go
(«1 علبة + 1 شريط»), priced to the piastre (whole boxes at the box price, the rest at the strip price) and never
beyond stock; the stock reads «2 علبة + 3 شريط» wherever an item is picked on the sale screen. Synthetic data."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from app.core.errors import DomainError
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_owner_plan_priority_1_2 import PlanCase
from tests.test_sales import SalesCase

JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")


def _const(name):
    m = re.search(rf"\nconst {name} = (?:[^\n]*;\n|.*?\n\}};\n)", JS, re.S)
    return m.group(0) if m else ""


def _fn(name):
    m = re.search(rf"\nfunction {name}\(.*?\n\}}\n", JS, re.S)
    return m.group(0) if m else ""


@unittest.skipUnless(shutil.which("node"), "Node not available")
class TestRules(unittest.TestCase):
    def run_js(self, body):
        src = "const esc = (v) => String(v ?? '');\n" + "".join(
            _const(n) for n in ("unitName", "packName", "fmtStock", "qtyText")) + _fn("mixedQty") + body
        r = subprocess.run([shutil.which("node"), "-e", src], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_boxes_and_strips_to_base_units(self):
        out = self.run_js(r"""
console.log(JSON.stringify([
  mixedQty("1", "1", 3), mixedQty(2, 0, 3), mixedQty("", "2", 3), mixedQty("0", "5", 3),
  mixedQty("١", "٢", 3),                       // Arabic digits
  mixedQty("0", "0", 3), mixedQty("", "", 3),  // nothing asked
  mixedQty("1.5", "0", 3), mixedQty("-1", "4", 3), mixedQty("1", "x", 3), mixedQty("1e2", "0", 3),
  mixedQty("4", "", 1),
]));
""")
        self.assertEqual(out, [4, 6, 2, 5, 5, None, None, None, None, None, None, 4])

    def test_stock_and_line_read_in_boxes_and_strips(self):
        out = self.run_js(r"""
const it = { pack_size: 3, base_unit_name: "شريط", pack_unit_name: "pack" };
const line = { pack_size: 3, base_unit: "شريط", pack_unit: "علبة" };
console.log(JSON.stringify([
  fmtStock(9, it), fmtStock(11, it), fmtStock(2, it), fmtStock(0, it), fmtStock(null, it),
  fmtStock(7, { pack_size: 1, base_unit_name: "unit" }),
  fmtStock(5, { pack_size: 2, base_unit_name: "unit", pack_unit_name: "زجاجة" }),
  qtyText(4, line), qtyText(6, line), qtyText(2, line), qtyText(3, { pack_size: 1, base_unit: "unit" }),
]));
""")
        self.assertEqual(out, ["3 علبة", "3 علبة + 2 شريط", "2 شريط", "0", "",
                               "7 وحدة", "2 زجاجة + 1 وحدة",
                               "1 علبة + 1 شريط", "2 علبة", "2 شريط", "3 وحدة"])

    def test_the_screen_uses_them(self):
        self.assertIn("data-qp=", JS)                      # boxes field on each cart line
        self.assertIn("data-qu=", JS)                      # strips field on each cart line
        self.assertIn("data-mix=", JS)                     # «علبة + شريط» in the search results
        self.assertIn("e.altKey && r.pack_size > 1", JS)   # Alt+Enter adds both
        self.assertIn("qtyDialog(l.item, l.qty_base)", JS)   # F6 asks for both
        self.assertIn('class="qs muted small"', JS)        # the quick row shows the stock


class TestServer(SalesCase):
    """The line already carries boxes and strips: one request, one line, exact piastres, never beyond stock."""

    def test_one_add_one_line_exact_price(self):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, packs=1, units=1)      # 3 strips, all from OLD (38.00 box)
        self.assertEqual(len(d["lines"]), 1)
        self.assertEqual(d["lines"][0]["qty_base"], 3)
        self.assertEqual(d["lines"][0]["gross"], 3800 + 1900)

    def test_beyond_stock_is_refused_and_nothing_is_added(self):
        sid = self.s.create(self.ctx)
        with self.assertRaises(DomainError):
            self.s.add_line(self.ctx, sid, self.pan, packs=6, units=2)      # 14 > 13
        self.assertEqual(self.s.get(self.ctx, sid)["lines"], [])
        d = self.s.add_line(self.ctx, sid, self.pan, packs=6, units=1)      # 13 = all of it
        lid = d["lines"][0]["id"]
        with self.assertRaises(DomainError):
            self.s.update_line(self.ctx, sid, lid, qty_base=14)
        self.assertEqual(self.s.get(self.ctx, sid)["lines"][0]["qty_base"], 13)

    def test_refusal_reads_in_boxes_and_strips(self):
        sid = self.s.create(self.ctx)
        with self.assertRaises(DomainError) as e:
            self.s.add_line(self.ctx, sid, self.pan, packs=7)
        self.assertIn("الكمية المتاحة للبيع 6 علبة + 1 وحدة", str(e.exception))
        d = self.s.add_line(self.ctx, sid, self.pan, units=1)
        with self.assertRaises(DomainError) as e:
            self.s.update_line(self.ctx, sid, d["lines"][0]["id"], qty_base=20)
        self.assertIn("6 علبة + 1 وحدة", str(e.exception))

    def test_qty_words(self):
        from app.sales.service import qty_words
        it = {"pack_size": 3, "base_unit_name": "شريط", "pack_unit_name": "pack"}
        self.assertEqual([qty_words(q, it) for q in (0, 2, 3, 11)], ["0", "2 شريط", "1 علبة", "3 علبة + 2 شريط"])
        self.assertEqual(qty_words(5, {"pack_size": 1, "base_unit_name": "unit"}), "5 وحدة")

    def test_sold_mixed_line_takes_exact_stock_and_money(self):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, packs=2, units=1)      # 5: OLD 3 (38.00) + NEW 2 (40.00)
        self.assertEqual(d["lines"][0]["gross"], 3800 + 1900 + 4000)
        done = self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid),
                               round_cash=False)
        self.assertEqual(done["total"], 9700)
        left = self.db.one("SELECT sum(qty) q FROM stock_balances WHERE item_id=?", (self.pan,))["q"]
        self.assertEqual(left, 13 - 5)


SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw] = process.argv.slice(1);
""" + LOGIN + r"""
  p.on('dialog', (d) => d.accept());
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posQ');
  await p.fill('#posQ', 'Trial Box');
  await p.waitForSelector('#posRes [data-mix]');
  const out = { res: await p.textContent('#posRes'), mixLabel: await p.textContent('#posRes [data-mix]') };
  // Alt+Enter on the searched text opens the box + strip dialog (defaults 1 + 1)
  await p.press('#posQ', 'Alt+Enter');
  await p.waitForSelector('dialog .qtydlg');
  out.dlg = [await p.inputValue('dialog [name=p]'), await p.inputValue('dialog [name=u]')];
  out.avail = await p.textContent('dialog .qtydlg');
  await p.click('dialog button[value=ok]');
  await p.waitForSelector('#posCart [data-qp]');
  out.lines1 = await p.locator('#posCart [data-qp]').count();
  out.qty1 = await p.textContent('#posCart .qty b');
  out.total1 = await p.textContent('#posCart tbody tr td.num b');
  // type the strips on the line: 1 box + 2 strips, saved when leaving the pair
  await p.fill('#posCart [data-qu]', '2');
  await p.press('#posCart [data-qu]', 'Enter');
  await p.waitForFunction(() => /\+ 2/.test(document.querySelector('#posCart .qty b').textContent));
  out.qty2 = await p.textContent('#posCart .qty b');
  out.total2 = await p.textContent('#posCart tbody tr td.num b');
  // beyond stock (10 boxes + 1 strip > 30 strips): refused, the line stays as it was
  await p.fill('#posCart [data-qp]', '10');
  await p.fill('#posCart [data-qu]', '1');
  await p.press('#posCart [data-qu]', 'Enter');
  await p.waitForSelector('#toast.error:not([hidden])');
  out.refusal = await p.textContent('#toast');
  await p.waitForTimeout(400);
  out.qty3 = await p.textContent('#posCart .qty b');
  out.lines3 = await p.locator('#posCart [data-qp]').count();
  // F6 on the line: both fields
  await p.focus('#posQ');
  await p.keyboard.press('F6');
  await p.waitForSelector('dialog .qtydlg');
  out.f6 = [await p.inputValue('dialog [name=p]'), await p.inputValue('dialog [name=u]')];
  await p.fill('dialog [name=p]', '2');
  await p.fill('dialog [name=u]', '0');
  await p.click('dialog button[value=ok]');
  await p.waitForFunction(() => document.querySelector('#posCart .qty b').textContent.trim() === '2 علبة');
  out.total4 = await p.textContent('#posCart tbody tr td.num b');
  // review P1: F9 while the boxes field still has the typed number saves it first, then sells
  await p.fill('#posCart [data-qp]', '3');
  const [res] = await Promise.all([p.waitForResponse((r) => r.url().endsWith('/complete')), p.keyboard.press('F9')]);
  out.f9 = res.status();
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

    def test_box_and_strip_in_one_go(self):
        out = run_node(self, SCRIPT, "owner", OWNER_PW)
        self.assertIn("رصيد: 10 علبة", out["res"])                 # 30 strips = 10 boxes of 3
        self.assertIn("علبة + وحدة", out["mixLabel"])
        self.assertEqual(out["dlg"], ["1", "1"])
        self.assertIn("المتاح: 10 علبة", out["avail"])
        self.assertEqual(out["lines1"], 1)
        self.assertEqual(out["qty1"].strip(), "1 علبة + 1 وحدة")
        self.assertEqual(out["total1"], "133.33")                  # 100.00 + 33.33
        self.assertEqual(out["qty2"].strip(), "1 علبة + 2 وحدة")
        self.assertEqual(out["total2"], "166.66")                  # 100.00 + 2 x 33.33
        self.assertEqual(out["qty3"].strip(), "1 علبة + 2 وحدة")   # refused beyond stock
        self.assertEqual(out["lines3"], 1)
        self.assertIn("الكمية المتاحة للبيع 10 علبة", out["refusal"])
        self.assertEqual(out["f6"], ["1", "2"])
        self.assertEqual(out["total4"], "200.00")
        self.assertEqual(out["f9"], 200)
        row = self.db.one("SELECT total FROM sales WHERE status='completed'")
        self.assertEqual(row["total"], 30000)                      # the typed 3 boxes, not the 2 shown before


if __name__ == "__main__":
    unittest.main()
