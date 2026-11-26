"""U4-22 on the sale screen: a scan while the focus is in the discount box, the customer box or on the
«إتمام البيع» button adds the item, and none of those fields receives the digits or the scanner's Enter.
A person typing in the same fields is untouched."""
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
  const [base, user, pw, a, bb] = process.argv.slice(1);
""" + LOGIN + r"""
  const out = {};
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posQ');
  const lines = () => p.locator('table.cart [data-minus]').count();
  const UNITS = () => [...document.querySelectorAll('table.cart tbody tr .qty b')].reduce((t, b) => t + (parseInt(b.innerText) || 0), 0);
  const waitUnits = (n) => p.waitForFunction((n) => [...document.querySelectorAll('table.cart tbody tr .qty b')]
    .reduce((t, b) => t + (parseInt(b.innerText) || 0), 0) === n, n);
  const waitLines = (n) => p.waitForFunction((n) => document.querySelectorAll('table.cart [data-minus]').length === n, n);
  const scan = async (code) => { await p.keyboard.type(code, { delay: 2 }); await p.keyboard.press('Enter'); };
  await p.locator('#posQ').focus();
  await scan(a);
  await waitLines(1);
  // a discount typed by hand, then a scan while the cursor is still in it
  const disc = p.locator('[data-disc]').first();
  await disc.click();
  await p.keyboard.type('5', { delay: 150 });
  await scan(bb);
  await waitLines(2);
  out.discAfterScan = await p.locator('[data-disc]').first().inputValue();
  // the customer box: digits never land there, and no customer search runs for them
  await p.locator('#posCust').click();
  await p.keyboard.type('ab', { delay: 150 });
  await scan(a);
  await waitUnits(3);
  out.custAfterScan = await p.locator('#posCust').inputValue();
  // the done button: the scanner's Enter must not complete the sale
  let completes = 0;
  p.on('request', (r) => { if (r.url().endsWith('/complete')) completes++; });
  await p.locator('#posDone').focus();
  await scan(bb);
  await waitUnits(4);
  await p.waitForTimeout(300);
  out.completes = completes;
  out.units = await p.evaluate(UNITS);
  // a person typing a number in the payment box keeps it
  const given = p.locator('#payGiven');
  if (await given.count()) { await given.click(); await p.keyboard.type('100', { delay: 120 }); out.given = await given.inputValue(); }
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestScanFromAnyField(APICase):
    def test_scan_from_other_fields(self):
        codes = []
        for name, bc in (("Scan Alpha", "6221043010359"), ("Scan Beta", "6221043010366")):
            it = cat.create_item(self.db, self.ctx, {"name_en": name, "public_price": 1000})
            cat.add_barcode(self.db, self.ctx, it, bc)
            with self.db.transaction():
                bt = inv.get_or_create_batch(self.db, self.ctx, it, "B1", (date.today() + timedelta(days=400)).isoformat())
                inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=20, batch_id=bt, movement_type="purchase",
                                  unit_cost=Decimal("5"))
            codes.append(bc)
        CashDrawer(self.db).open(self.ctx, 0)
        out = run_node(self, SCRIPT, "owner", OWNER_PW, *codes)
        self.assertEqual(out["discAfterScan"], "5")           # the typed discount was saved, not lost
        self.assertNotRegex(out["custAfterScan"], r"\d")       # the digits never landed there
        self.assertEqual(out["completes"], 0)
        self.assertEqual(out["units"], 4)                     # four scans, four units, nothing else
        if "given" in out:
            self.assertEqual(out["given"], "100")


CLICK_SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, a] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posQ');
  await p.waitForTimeout(700);                                 // the first-run tour: «تخطّي»
  if (await p.locator('[data-tour-go=skip]').count()) await p.locator('[data-tour-go=skip]').click();
  await p.locator('#posQ').focus();
  await p.keyboard.type(a, { delay: 2 }); await p.keyboard.press('Enter');
  await p.waitForSelector('table.cart [data-minus]', { timeout: 10000 });
  // a discount typed fast, then a mouse click elsewhere: the whole value is saved
  await p.locator('[data-disc]').first().click();
  await p.keyboard.type('15', { delay: 2 });
  await p.locator('table.cart thead').click();
  await p.waitForFunction(() => document.querySelector('[data-disc]')?.value === '15', null, { timeout: 10000 });
  await p.waitForTimeout(400);
  // a delivery address typed fast, then a mouse click on «إتمام البيع» at once
  await p.locator('button[data-kind="delivery"]').click();
  await p.waitForSelector('[data-dl="address"]');
  await p.locator('[data-dl="address"]').click();
  await p.keyboard.type('شارع التجربة 7', { delay: 2 });
  const [res] = await Promise.all([p.waitForResponse((r) => r.url().endsWith('/complete'), { timeout: 15000 }), p.locator('#posDone').click()]);
  console.log(JSON.stringify({ completed: res.status() }));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestClickAfterFastTyping(APICase):
    """Codex P1 on #462: typing held back for a moment is saved when the cashier clicks away or completes."""
    def test_click_saves_the_whole_text(self):
        it = cat.create_item(self.db, self.ctx, {"name_en": "Click Item", "public_price": 1000})
        cat.add_barcode(self.db, self.ctx, it, "6221043010403")
        with self.db.transaction():
            bt = inv.get_or_create_batch(self.db, self.ctx, it, "B1", (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=20, batch_id=bt, movement_type="purchase",
                              unit_cost=Decimal("5"))
        CashDrawer(self.db).open(self.ctx, 0)
        out = run_node(self, CLICK_SCRIPT, "owner", OWNER_PW, "6221043010403")
        self.assertEqual(out["completed"], 200)
        d = self.db.one("SELECT d.address FROM sale_deliveries d JOIN sales s ON s.id=d.sale_id WHERE s.status='completed'"
                        " ORDER BY s.completed_at DESC LIMIT 1")
        self.assertEqual(d["address"], "شارع التجربة 7")
        line = self.db.one("SELECT l.discount_bp FROM sale_lines l JOIN sales s ON s.id=l.sale_id WHERE s.status='completed'")
        self.assertEqual(line["discount_bp"], 1500)
