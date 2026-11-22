"""U2-1 / U2-10 in the browser: Enter and the arrows move inside the purchase line grid, a changed box is saved and
the redraw lands on the next box (no stall, no form submit)."""
from datetime import date, timedelta

from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import APICase

SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, inv] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.goto(base + '/#/purchases/' + inv);
  await p.waitForSelector('table.lines input[data-field="qty"]');
  const at = () => p.evaluate(() => { const a = document.activeElement; return [a.dataset.line || '', a.dataset.field || '']; });
  const lines = await p.$$eval('table.lines input[data-field="qty"]', (x) => x.map((i) => i.dataset.line));
  const out = { lines };
  await p.focus(`input[data-line="${lines[0]}"][data-field="qty"]`);
  await p.keyboard.type('7'); await p.keyboard.press('Enter');            // changed: saved, then the next box
  await p.waitForFunction(() => document.activeElement && document.activeElement.dataset.field === 'bonus_qty');
  out.a = await at();
  await p.keyboard.press('ArrowDown');                                    // same box, next row
  out.b = await at();
  await p.keyboard.press('ArrowUp');
  out.c = await at();
  await p.focus(`input[data-line="${lines[0]}"][data-field="expiry_date"]`);
  await p.keyboard.press('Enter');                                        // last box of a row -> first box of the next
  out.d = await at();
  out.qty = await p.$eval(`input[data-line="${lines[0]}"][data-field="qty"]`, (i) => i.value);
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestPurchaseGridKeys(APICase):
    def test_enter_and_arrows(self):
        from dataclasses import replace
        from app import config
        from app.purchasing.service import Purchasing
        p = Purchasing(self.db, replace(config.load(), upload_dir=self.tmp / "u"))
        inv_id = p.create_manual(self.ctx)
        exp = (date.today() + timedelta(days=700)).isoformat()
        for name in ("Grid A", "Grid B"):
            p.add_line(self.ctx, inv_id, {"raw_name": name, "qty": 2, "public_price": "10", "discount_pct": "20",
                                           "unit_net_price": "8", "batch_no": "B1", "expiry_date": exp}, None)
        out = run_node(self, SCRIPT, "owner", OWNER_PW, inv_id)
        l0, l1 = out["lines"]
        self.assertEqual(out["a"], [l0, "bonus_qty"])
        self.assertEqual(out["b"], [l1, "bonus_qty"])
        self.assertEqual(out["c"], [l0, "bonus_qty"])
        self.assertEqual(out["d"], [l1, "qty"])
        self.assertEqual(out["qty"], "7")           # the box is selected on focus: typing replaces it (U2-2)


class TestPosDeliveryWiring(__import__("unittest").TestCase):
    """U2-10 second freeze (customer → «توصيل للمنزل»): the store/delivery handler is bound to the side panel only
    (the search results' «علبة» / «شريط» buttons also carry data-kind and got a kind PATCH on every redraw), one
    change at a time, and the customer's phone and address fill the delivery."""
    def test_wiring(self):
        from pathlib import Path
        js = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('document.querySelectorAll("[data-kind]").forEach', js)
        h = js[js.index('document.querySelectorAll("#posSide [data-kind]")'):][:700]
        self.assertIn("POS.kindBusy", h)
        self.assertIn("await fillFromCustomer()", h)
