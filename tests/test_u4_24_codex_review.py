"""Codex review of #470 (U4-24 kept sale screen):
- P2: once any save on the screen had finished (a quantity, a discount), the check after coming back thought a save
  was still running and never showed a newer invoice (a line added on another PC);
- P1: a discount being typed in pounds kept its amount through the check's redraw but its unit went back to %,
  so 5 ج would have been saved as 5 %."""
from datetime import date, timedelta
from decimal import Decimal

from app.catalog import service as cat
from app.inventory import service as inv
from app.sales.cash import CashDrawer
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import APICase

SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, a, bId] = process.argv.slice(1);
""" + LOGIN + r"""
  const out = {};
  const H = { 'Content-Type': 'application/json', 'X-Requested-With': 'pharm' };
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posQ');
  await p.waitForTimeout(700);                                 // the first-run tour: «تخطّي»
  if (await p.locator('[data-tour-go=skip]').count()) await p.locator('[data-tour-go=skip]').click();
  const lines = (n) => p.waitForFunction((n) => document.querySelectorAll('table.cart [data-minus]').length === n, n, { timeout: 15000 });
  await p.locator('#posQ').focus();
  await p.keyboard.type(a, { delay: 2 }); await p.keyboard.press('Enter');
  await lines(1);
  const sid = await p.evaluate(() => document.querySelector('.tab.on')?.dataset.tab);
  // a save that finished: one more box with «+»
  const [pr] = await Promise.all([p.waitForResponse((r) => r.request().method() === 'PATCH'), p.locator('[data-plus]').first().click()]);
  out.plus = pr.status();
  await p.waitForTimeout(200);
  const away = async () => { await p.evaluate(() => { location.hash = '#/home'; }); await p.waitForFunction(() => !document.querySelector('#posQ')); };
  const elsewhere = (ref) => p.evaluate(async ([sid, id, H, ref]) => fetch(`/api/sales/${sid}/lines`, { method: 'POST', headers: H,
      body: JSON.stringify({ item_id: id, packs: 1, units: 0, client_ref: ref }) }), [sid, bId, H, ref]);
  await away();
  await elsewhere('elsewhere-1');
  await p.evaluate(() => { location.hash = '#/pos'; });
  await lines(2);                                              // P2: the newer invoice is shown
  out.newerShown = true;
  // P1: pounds chosen and an amount typed while the check is late and brings a newer invoice
  await away();
  await elsewhere('elsewhere-2');
  const slow = (r) => setTimeout(() => r.continue().catch(() => {}), 1500);
  for (const u of ['**/api/sales/drafts', '**/api/settings/receipt', '**/api/settings/ui', `**/api/sales/${sid}`]) await p.route(u, slow);
  await p.evaluate(() => { location.hash = '#/pos'; });
  await p.waitForSelector('[data-dunit]');
  const unit = p.locator('[data-dunit]').first(), lineId = await unit.getAttribute('data-dunit');
  await unit.selectOption('egp');
  await p.locator(`[data-disc="${lineId}"]`).click();
  await p.keyboard.type('5', { delay: 60 });
  await p.waitForFunction(() => [...document.querySelectorAll('table.cart tbody tr .qty b')]
    .reduce((t, b) => t + (parseInt(b.innerText) || 0), 0) >= 4, null, { timeout: 15000 });   // the check redrew the invoice
  out.unitAfter = await p.locator(`[data-dunit="${lineId}"]`).inputValue();
  out.amountAfter = await p.locator(`[data-disc="${lineId}"]`).inputValue();
  await p.unrouteAll({ behavior: 'ignoreErrors' });
  await p.locator('table.cart thead').click();                 // leave the box: whatever is typed is saved
  await p.waitForTimeout(800);
  out.saved = await p.evaluate(async ([sid, lineId]) => (await (await fetch(`/api/sales/${sid}`,
      { headers: { 'X-Requested-With': 'pharm' } })).json()).lines.find((l) => l.id === lineId)?.discount_amount, [sid, lineId]);
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestCodexOn470(APICase):
    def test_newer_invoice_after_a_save_and_discount_unit_kept(self):
        ids = []
        for name, bc in (("Review Alpha", "6221043010410"), ("Review Beta", "6221043010427")):
            it = cat.create_item(self.db, self.ctx, {"name_en": name, "public_price": 20000})
            cat.add_barcode(self.db, self.ctx, it, bc)
            with self.db.transaction():
                bt = inv.get_or_create_batch(self.db, self.ctx, it, "B1", (date.today() + timedelta(days=400)).isoformat())
                inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=20, batch_id=bt, movement_type="purchase",
                                  unit_cost=Decimal("50"))
            ids.append(it)
        CashDrawer(self.db).open(self.ctx, 0)
        out = run_node(self, SCRIPT, "owner", OWNER_PW, "6221043010410", ids[1])
        self.assertEqual(out["plus"], 200)
        self.assertTrue(out["newerShown"])
        self.assertEqual((out["unitAfter"], float(out["amountAfter"])), ("egp", 5.0))
        self.assertEqual(out["saved"], 500)                      # 5 ج, never 5 %
