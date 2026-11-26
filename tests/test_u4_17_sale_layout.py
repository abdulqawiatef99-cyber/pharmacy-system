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
        self.assertEqual(out["afterFirstEnter"], "payGiven")
        self.assertEqual(out["completed"], 200)
        self.assertTrue(out["openOnNext"])
