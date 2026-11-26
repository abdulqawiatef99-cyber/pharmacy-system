"""U4-17 (owner 17/11): the invoice lines sit directly under the search box; quick sale is one compact sideways row
under the invoice, open on an empty invoice, shrunk after the first item, and its button opens it again.
Keyboard: scan, Enter on the empty search goes to «العميل دفع», Enter there completes the sale."""
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
  const [base, user, pw, code] = process.argv.slice(1);
""" + LOGIN + r"""
  const out = {};
  await p.setViewportSize({ width: 1366, height: 768 });
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posQuick .qbtn');
  out.order = await p.evaluate(() => [...document.querySelector('.pos-main').children].map((e) => e.id || e.tagName));
  out.rowTops = await p.evaluate(() => new Set([...document.querySelectorAll('#posQuick .qbtn')].map((b) => b.offsetTop)).size);
  await p.locator('#posQ').focus();
  await p.keyboard.type(code, { delay: 2 }); await p.keyboard.press('Enter');
  await p.waitForFunction(() => document.querySelectorAll('table.cart [data-minus]').length === 1);
  await p.waitForFunction(() => !document.querySelector('#posQuick .qbtn'));
  out.shrunkLabel = await p.locator('#qkTgl').innerText();
  await p.locator('#qkTgl').click();
  await p.waitForSelector('#posQuick .qbtn');
  out.reopened = await p.locator('#posQuick .qbtn').count();
  // review P1/P2 cases first: none of them may send /complete
  let completes = 0;
  p.on('request', (r) => { if (r.url().endsWith('/complete')) completes++; });
  // a scanner sending CR+LF: the second Enter on the now empty search is not a person's Enter
  await p.locator('#posQ').focus();
  await p.keyboard.type(code, { delay: 2 }); await p.keyboard.press('Enter'); await p.keyboard.press('Enter');
  await p.waitForFunction(() => /^2\b/.test(document.querySelector('table.cart tbody tr .qty b')?.innerText ?? ''));
  out.afterCrLf = await p.evaluate(() => document.activeElement.id);
  // a card sale: Enter held down on the empty search never clicks «إتمام البيع»
  await p.locator('[data-m=card]').click();
  await p.locator('#posQ').focus();
  await p.waitForTimeout(200);
  await p.keyboard.down('Enter'); await p.keyboard.down('Enter'); await p.keyboard.down('Enter'); await p.keyboard.up('Enter');
  out.cardFocus = await p.evaluate(() => document.activeElement.id);
  await p.waitForTimeout(400);
  out.heldCompletes = completes;
  await p.locator('[data-m=cash]').click();
  // an amount that is not a number («5e» reads as empty) is refused, never taken as the exact amount
  await p.locator('#payGiven').click();
  await p.keyboard.type('5e', { delay: 60 });
  await p.keyboard.press('Enter');
  await p.waitForTimeout(400);
  out.badCompletes = completes;
  await p.locator('#payGiven').fill('');
  await p.locator('#posQ').focus();
  await p.keyboard.press('Enter');
  out.afterFirstEnter = await p.evaluate(() => document.activeElement.id);
  const [res] = await Promise.all([p.waitForResponse((r) => r.url().endsWith('/complete')), p.keyboard.press('Enter')]);
  out.completed = res.status();
  await p.waitForFunction(() => document.querySelectorAll('table.cart [data-minus]').length === 0, null, { timeout: 10000 });
  await p.waitForSelector('#posQuick .qbtn');                 // the next invoice is empty: the row is open again
  out.openOnNext = true;
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestSaleLayout(APICase):
    def test_layout_quick_row_and_enter_flow(self):
        it = cat.create_item(self.db, self.ctx, {"name_en": "Layout Item", "public_price": 1000, "quick_pin": 1})
        cat.add_barcode(self.db, self.ctx, it, "6221043010373")
        for n in range(8):                                     # enough pinned buttons to need a wrapped grid before
            cat.create_item(self.db, self.ctx, {"name_en": f"Pinned {n}", "public_price": 500, "quick_pin": 1})
        with self.db.transaction():
            bt = inv.get_or_create_batch(self.db, self.ctx, it, "B1", (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=20, batch_id=bt, movement_type="purchase",
                              unit_cost=Decimal("5"))
        CashDrawer(self.db).open(self.ctx, 0)
        out = run_node(self, SCRIPT, "owner", OWNER_PW, "6221043010373")
        order = out["order"]
        self.assertLess(order.index("posCart"), order.index("posQuick"))
        self.assertEqual(order[order.index("posRes") + 1], "posCart")     # lines directly under the search results
        self.assertEqual(out["rowTops"], 1)                                # one sideways row
        self.assertIn("افتح", out["shrunkLabel"])
        self.assertGreater(out["reopened"], 0)
        self.assertEqual(out["afterCrLf"], "posQ")
        self.assertEqual(out["cardFocus"], "posDone")
        self.assertEqual(out["heldCompletes"], 0)
        self.assertEqual(out["badCompletes"], 0)
        self.assertEqual(out["afterFirstEnter"], "payGiven")
        self.assertEqual(out["completed"], 200)
        self.assertTrue(out["openOnNext"])


SERVICES_ONLY = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posQuick h2');
  await p.waitForTimeout(500);
  const svc = await p.locator('#posQuick [data-svc]').count();
  console.log(JSON.stringify({ svc }));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestServicesWithoutQuickItems(APICase):
    """Codex P2 on #468: services stay on the quick row when no item is pinned or sold yet."""
    def test_services_shown_with_an_empty_item_list(self):
        cat.create_item(self.db, self.ctx, {"name_en": "Blood pressure check", "name_ar": "قياس ضغط",
                                            "public_price": 500, "is_service": 1})
        CashDrawer(self.db).open(self.ctx, 0)
        out = run_node(self, SERVICES_ONLY, "owner", OWNER_PW)
        self.assertEqual(out["svc"], 1)
