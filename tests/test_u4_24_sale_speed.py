"""U4-24 / owner 23/11 («a smoother, faster sale screen — navigation and response»):
- back on the sale screen from another screen, the open invoice is there at once, without waiting for the server;
- the kept screen is then checked: a line added elsewhere, or an invoice closed elsewhere, shows right after;
- after a sale that was the only open invoice, the next empty one needs one wait, not two."""
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
  const [base, user, pw, a, bb, bId] = process.argv.slice(1);
""" + LOGIN + r"""
  const out = {};
  const H = { 'Content-Type': 'application/json', 'X-Requested-With': 'pharm' };
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posQ');
  const lines = (n) => p.waitForFunction((n) => document.querySelectorAll('table.cart [data-minus]').length === n, n, { timeout: 15000 });
  await p.locator('#posQ').focus();
  await p.keyboard.type(a, { delay: 2 }); await p.keyboard.press('Enter');
  await lines(1);
  const sid = await p.evaluate(() => document.querySelector('.tab.on')?.dataset.tab);
  // every request the sale screen makes to the server now answers 1.5 s late
  const slow = (r) => setTimeout(() => r.continue().catch(() => {}), 1500);
  for (const u of ['**/api/sales/drafts', '**/api/settings/receipt', '**/api/settings/ui', `**/api/sales/${sid}`]) await p.route(u, slow);
  await p.evaluate(() => { location.hash = '#/home'; });
  await p.waitForFunction(() => !document.querySelector('#posQ'));
  // meanwhile a second line goes on this invoice from elsewhere (another tab of the same cashier)
  await p.evaluate(async ([sid, id, H]) => fetch(`/api/sales/${sid}/lines`, { method: 'POST', headers: H,
      body: JSON.stringify({ item_id: id, packs: 1, units: 0, client_ref: 'elsewhere' }) }), [sid, bId, H]);
  const t0 = Date.now();
  await p.evaluate(() => { location.hash = '#/pos'; });
  await p.waitForFunction(() => document.querySelectorAll('table.cart [data-minus]').length >= 1 && document.querySelector('#posDone'));
  out.backMs = Date.now() - t0;
  out.linesAtOnce = await p.locator('table.cart [data-minus]').count();
  await lines(2);                                             // the check with the server brings the other line
  out.linesAfterCheck = 2;
  out.sameInvoice = (await p.evaluate(() => document.querySelector('.tab.on')?.dataset.tab)) === sid;
  await p.unrouteAll({ behavior: 'ignoreErrors' });
  // closed elsewhere while away: the screen moves to an open invoice, never stays on the closed one
  await p.evaluate(() => { location.hash = '#/home'; });
  await p.waitForFunction(() => !document.querySelector('#posQ'));
  await p.evaluate(async ([sid, H]) => fetch(`/api/sales/${sid}/discard`, { method: 'POST', headers: H, body: '{}' }), [sid, H]);
  await p.evaluate(() => { location.hash = '#/pos'; });
  await p.waitForFunction((sid) => { const t = document.querySelector('.tab.on'); return t && t.dataset.tab !== sid && !document.querySelector(`[data-tab="${sid}"]`); }, sid, { timeout: 15000 });
  out.leftClosed = true;
  // the next invoice after a sale: the list and the new invoice are asked for together
  await p.locator('#posQ').focus();
  await p.keyboard.type(bb, { delay: 2 }); await p.keyboard.press('Enter');
  await lines(1);
  const late = (r) => setTimeout(() => r.continue().catch(() => {}), 700);
  await p.route('**/api/sales/drafts', late);
  await p.route('**/api/sales', (r) => r.request().method() === 'POST' ? late(r) : r.continue());
  const [res] = await Promise.all([p.waitForResponse((r) => r.url().endsWith('/complete')), p.keyboard.press('F9')]);
  const t1 = Date.now();
  out.completed = res.status();
  await p.waitForFunction(() => document.querySelectorAll('table.cart [data-minus]').length === 0 && document.querySelector('#posDone'), null, { timeout: 15000 });
  out.nextMs = Date.now() - t1;
  out.tabs = await p.locator('[data-tab]').count();
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestSaleScreenSpeed(APICase):
    def test_back_at_once_checked_after_and_next_invoice_in_one_wait(self):
        ids, codes = [], []
        for name, bc in (("Speed Alpha", "6221043010380"), ("Speed Beta", "6221043010397")):
            it = cat.create_item(self.db, self.ctx, {"name_en": name, "public_price": 1000})
            cat.add_barcode(self.db, self.ctx, it, bc)
            with self.db.transaction():
                bt = inv.get_or_create_batch(self.db, self.ctx, it, "B1", (date.today() + timedelta(days=400)).isoformat())
                inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=20, batch_id=bt, movement_type="purchase",
                                  unit_cost=Decimal("5"))
            ids.append(it); codes.append(bc)
        CashDrawer(self.db).open(self.ctx, 0)
        out = run_node(self, SCRIPT, "owner", OWNER_PW, codes[0], codes[1], ids[1])
        self.assertLess(out["backMs"], 700, out)                 # the server answers 1.5 s late
        self.assertEqual(out["linesAtOnce"], 1)                  # what the screen kept, at once
        self.assertTrue(out["sameInvoice"])
        self.assertTrue(out["leftClosed"])
        self.assertEqual(out["completed"], 200)
        self.assertLess(out["nextMs"], 1300, out)                # two 0.7 s waits one after the other would be 1.4 s+
        self.assertEqual(out["tabs"], 1)
