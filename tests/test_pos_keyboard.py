"""Part 3 owner correction C: selling by keyboard only, fixed defaults with an on-screen legend.
F1 search, arrows choose a line, F2 its stock in the branches, F6 its quantity, Delete removes it, F10 goes to the
payment and saves on the second press, Insert opens a new invoice, F7 goes to return and replace."""
from datetime import date, timedelta
from decimal import Decimal

from app.catalog import card
from app.catalog import service as cat
from app.inventory import service as inv
from app.sales.cash import CashDrawer
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import APICase

SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, a, bb] = process.argv.slice(1);
""" + LOGIN + r"""
  const out = {};
  p.on('dialog', (d) => d.accept());
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posQ');
  out.legend = await p.locator('p.keys').innerText();
  for (const code of [a, bb]) {
    await p.locator('#posQ').focus();
    await p.keyboard.type(code, { delay: 5 }); await p.keyboard.press('Enter');
    await p.waitForFunction((n) => document.querySelectorAll('table.cart [data-minus]').length === n, code === a ? 1 : 2);
  }
  await p.locator('#posQ').focus();
  await p.keyboard.press('ArrowUp');                         // from the empty search line: the last line
  out.selected = await p.locator('table.cart tbody tr.sel').count();
  await p.keyboard.press('ArrowUp');                         // the first line
  out.selIndex = await p.evaluate(() => [...document.querySelectorAll('table.cart tbody tr')].findIndex((r) => r.classList.contains('sel')));
  await p.keyboard.press('F2');
  await p.waitForSelector('dialog[open]');
  out.stock = await p.locator('dialog[open] table').innerText();
  await p.keyboard.press('Enter');
  await p.waitForFunction(() => !document.querySelector('dialog[open]'));
  await p.locator('table.cart').click({ position: { x: 1, y: 1 } }).catch(() => {});
  await p.keyboard.press('ArrowUp'); await p.keyboard.press('ArrowUp');
  await p.keyboard.press('F6');
  await p.waitForSelector('dialog .qtydlg');                 // UX-27: boxes and strips in one dialog
  const mix = await p.locator('dialog [name=p]').count();
  await p.fill(mix ? 'dialog [name=p]' : 'dialog [name=u]', '3');
  if (mix) await p.fill('dialog [name=u]', '0');
  await p.keyboard.press('Enter');
  await p.waitForFunction(() => /^3\b/.test(document.querySelector('table.cart tbody tr .qty b')?.innerText ?? ''));
  out.qty = await p.locator('table.cart tbody tr .qty b').first().innerText();
  await p.keyboard.press('Delete');
  await p.waitForFunction(() => document.querySelectorAll('table.cart [data-minus]').length === 1);
  out.linesAfterDelete = await p.locator('table.cart [data-minus]').count();
  await p.keyboard.press('F1');
  out.focusF1 = await p.evaluate(() => document.activeElement.id);
  await p.keyboard.press('F10');
  out.focusF10 = await p.evaluate(() => !!document.querySelector('#posSide')?.contains(document.activeElement));
  const [res] = await Promise.all([p.waitForResponse((r) => r.url().endsWith('/complete')), p.keyboard.press('F10')]);
  out.saved = res.status();
  await p.waitForFunction(() => document.querySelectorAll('table.cart [data-minus]').length === 0, null, { timeout: 10000 });
  const tabs = await p.locator('[data-tab]').count();
  await p.keyboard.press('Insert');
  await p.waitForFunction((n) => document.querySelectorAll('[data-tab]').length === n + 1, tabs);
  out.newTab = true;
  await p.keyboard.press('F7');
  await p.waitForFunction(() => location.hash === '#/exchange');
  out.exchange = true;
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


class TestBranchStock(APICase):
    def test_stock_per_branch_of_the_item(self):
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=7, movement_type="opening", unit_cost=Decimal("5"))
        r = card.branch_stock(self.db, self.ctx, self.pan)
        self.assertEqual([(b["qty"], b["here"]) for b in r["branches"]], [(7, True)])


@needs_browser
class TestKeyboardSelling(APICase):
    def test_keys(self):
        items = []
        for name, bc in (("Key Alpha", "6221043010359"), ("Key Beta", "6221043010366")):
            it = cat.create_item(self.db, self.ctx, {"name_en": name, "public_price": 1000})
            cat.add_barcode(self.db, self.ctx, it, bc)
            with self.db.transaction():
                bt = inv.get_or_create_batch(self.db, self.ctx, it, "B1", (date.today() + timedelta(days=400)).isoformat())
                inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=20, batch_id=bt, movement_type="purchase",
                                  unit_cost=Decimal("5"))
            items.append(bc)
        CashDrawer(self.db).open(self.ctx, 0)
        out = run_node(self, SCRIPT, "owner", OWNER_PW, *items)
        for k in ("F1", "F2", "F3", "F6", "F10", "F11", "F7", "Insert", "Ctrl+Tab", "Delete"):
            self.assertIn(k, out["legend"])
        self.assertEqual((out["selected"], out["selIndex"]), (1, 0))
        self.assertIn("20", out["stock"])
        self.assertTrue(out["qty"].startswith("3"))
        self.assertEqual(out["linesAfterDelete"], 1)
        self.assertEqual(out["focusF1"], "posQ")
        self.assertTrue(out["focusF10"])
        self.assertEqual(out["saved"], 200)
        self.assertTrue(out["newTab"] and out["exchange"])
