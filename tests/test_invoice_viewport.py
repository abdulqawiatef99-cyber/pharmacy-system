"""Long invoices and compact table controls in the cashier's browser."""
from datetime import date, timedelta
from decimal import Decimal

from app.catalog import service as catalog
from app.inventory import service as inventory
from app.purchasing.service import Purchasing
from app.sales.service import Sales
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import APICase

SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, purchase, screen] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.evaluate(async () => {
    const { organization, user } = await (await fetch('/api/me')).json();
    localStorage.setItem(`tour:${organization.id}:${user.id}`, JSON.stringify({ all: 1 }));
  });
  await p.setViewportSize({ width: 1366, height: 768 });
  const sale = screen === 'sale';
  const table = sale ? 'table.cart' : 'table.lines';
  const action = sale ? '#posDone' : '#approve';
  await p.goto(base + (sale ? '/#/pos' : '/#/purchases/' + purchase));
  await p.waitForFunction((sel) => document.querySelectorAll(sel + ' tbody tr').length === 24, table);
  if (!sale) await p.waitForSelector('#pTabs .tab.on');
  await p.evaluate(() => document.fonts.ready);
  const snapshot = () => p.evaluate(([table, action]) => {
    const t = document.querySelector(table), pane = t.closest('.tablewrap');
    const rect = pane.getBoundingClientRect(), btn = document.querySelector(action).getBoundingClientRect();
    const rows = [...t.tBodies[0].rows];
    const header = document.querySelector('#topbar').getBoundingClientRect().bottom;
    return {
      visible: rows.filter((row) => {
        const r = row.getBoundingClientRect();
        return r.top >= Math.max(header, rect.top, t.tHead.rows[0].cells[0].getBoundingClientRect().bottom)
          && r.bottom <= Math.min(rect.bottom, innerHeight);
      }).length,
      height: rows[1].getBoundingClientRect().height,
      actionTop: btn.top,
      actionVisible: btn.top >= header && btn.bottom <= innerHeight,
      y: scrollY,
      scroll: pane.scrollTop,
      minButton: Math.min(...[...rows[1].querySelectorAll('button')].map((b) => b.offsetHeight)),
      minText: Math.min(...[...rows[1].querySelectorAll('td, td *'), ...t.tHead.querySelectorAll('th')]
        .filter((el) => el.getClientRects().length && !el.classList.contains('sr'))
        .map((el) => parseFloat(getComputedStyle(el).fontSize))),
    };
  }, [table, action]);
  const out = { initial: await snapshot() };
  if (sale) {
    await p.setViewportSize({ width: 1280, height: 1024 });
    await p.evaluate(() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))));
    out.tall = await snapshot();
    await p.setViewportSize({ width: 1366, height: 768 });
  }
  out.rtl = await p.locator(table).evaluate((t) => getComputedStyle(t).direction === 'rtl');
  await p.locator(table).evaluate((t) => { t.closest('.tablewrap').scrollTop = 1000; });
  out.scrolled = await snapshot();
  if (sale) {
    await p.locator(table).evaluate((t) => { t.closest('.tablewrap').scrollTop = 0; });
    await p.focus('#posQ');
    await p.keyboard.press('ArrowUp');
    out.arrow = await snapshot();
    out.lastSelected = await p.locator(table + ' tbody tr').last().evaluate((row) => row.classList.contains('sel'));
    await p.locator('button[data-kind="delivery"]').click();
    await p.waitForSelector('.deliv');
    await p.setViewportSize({ width: 1024, height: 600 });
    await p.evaluate(() => {
      document.documentElement.dataset.theme = 'dark';
      return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    });
    const compact = () => p.evaluate(() => {
      const selectors = ['#posQ', '#posCust', '.sum.due', '[data-m="cash"]', '[data-m="card"]',
                         '[data-m="transfer"]', '[data-m="credit"]', '#posDone', '#posDone kbd'];
      return Object.fromEntries(selectors.map((selector) => {
        const el = document.querySelector(selector), rect = el.getBoundingClientRect();
        const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
        return [selector, { visible: el.contains(hit) && rect.top >= 0 && rect.bottom <= innerHeight,
                            top: rect.top }];
      }));
    });
    out.shortBar = await compact();
    out.delivery = await snapshot();
    await p.focus('#posQ');
    await p.keyboard.press('F10');
    out.focus = await p.evaluate(() => document.querySelector("#posSide").contains(document.activeElement));
    out.deliveryAfterFocus = await snapshot();
    await p.setViewportSize({ width: 390, height: 650 });
    await p.evaluate(() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))));
    await p.locator(table).evaluate((t) => { t.closest(".tablewrap").scrollTop = 0; });
    out.phone = await snapshot();
    out.dark = await p.locator('#posDone').evaluate((btn) => getComputedStyle(btn).colorScheme === 'dark');
    out.phoneBar = await compact();
    await p.locator(table).evaluate((t) => { t.closest('.tablewrap').scrollTop = 150; });
    out.phoneBarAfterScroll = await compact();
    await p.setViewportSize({ width: 1366, height: 768 });
    await p.goto(base + '/#/me');
    const choice = p.locator('input[name=density][value=comfortable]');
    out.hasDensity = await choice.count();
    if (out.hasDensity) {
      out.compactDefault = await p.locator('input[name=density][value=compact]').isChecked();
      const [response] = await Promise.all([
        p.waitForResponse((r) => r.url().endsWith('/api/me/prefs') && r.request().method() === 'PUT'),
        choice.check(),
      ]);
      out.savedDensity = (await response.json()).density;
      await p.goto(base + '/#/pos');
      await p.waitForSelector('table.cart tbody tr');
      out.comfortable = await snapshot();
    }
  } else {
    await p.evaluate(() => scrollTo(0, 250));
    out.purchaseAfterPageScroll = await snapshot();
    await p.goto(base + '/#/purchases');
    out.list = await p.locator('table tbody tr', { hasText: 'VIEWPORT-1' }).evaluate((row) => ({
      padding: parseFloat(getComputedStyle(row.cells[0]).paddingBlockStart),
      minText: Math.min(...[...row.querySelectorAll('td, td *')].filter((el) => el.getClientRects().length)
        .map((el) => parseFloat(getComputedStyle(el).fontSize))),
    }));
  }
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestInvoiceViewport(APICase):
    def setUp(self):
        super().setUp()
        sale = Sales(self.db)
        sale_id = sale.create(self.ctx)
        purchase = Purchasing(self.db, self.s)
        supplier = catalog.create_supplier(self.db, self.ctx, name="Synthetic Supplier")
        self.purchase_id = purchase.create_manual(self.ctx, supplier_id=supplier)
        purchase.update_header(self.ctx, self.purchase_id, {"invoice_no": "VIEWPORT-1"}, None)
        expiry = (date.today() + timedelta(days=730)).isoformat()
        for i in range(24):
            item = catalog.create_item(self.db, self.ctx, {"name_en": f"Synthetic Item {i:02}",
                                                           "pack_size": 2, "public_price": 1000})
            with self.db.transaction():
                batch = inventory.get_or_create_batch(self.db, self.ctx, item, "B1", expiry)
                inventory.post_movement(self.db, self.ctx, item_id=item, qty_delta=20, batch_id=batch,
                                        movement_type="purchase", unit_cost=Decimal("5"))
            sale.add_line(self.ctx, sale_id, item, packs=1)
            purchase.add_line(self.ctx, self.purchase_id, {"item_id": item, "qty": 1, "public_price": 1000,
                                                             "discount_bp": 2000, "batch_no": "B1",
                                                             "expiry_date": expiry}, None)

    def check_scrolling(self, out):
        self.assertTrue(out["rtl"], out)
        self.assertTrue(out["initial"]["actionVisible"], out)
        self.assertGreater(out["scrolled"]["scroll"], 0, out)
        self.assertEqual(out["scrolled"]["y"], out["initial"]["y"], out)
        self.assertEqual(out["scrolled"]["actionTop"], out["initial"]["actionTop"], out)
        self.assertGreaterEqual(out["initial"]["minButton"], 32, out)
        self.assertGreaterEqual(out["initial"]["minText"], 13, out)

    def test_sale_scroll_and_keyboard_at_cashier_size(self):
        out = run_node(self, SCRIPT, "owner", OWNER_PW, self.purchase_id, "sale")
        self.check_scrolling(out)
        self.assertGreater(out["initial"]["visible"], 4, out)
        self.assertGreater(out["tall"]["visible"], out["initial"]["visible"], out)
        self.assertTrue(out["tall"]["actionVisible"], out)
        self.assertTrue(out["lastSelected"], out)
        self.assertGreater(out["arrow"]["scroll"], 0, out)
        self.assertEqual(out["arrow"]["y"], out["initial"]["y"], out)
        self.assertTrue(out["delivery"]["actionVisible"], out)
        self.assertTrue(out["focus"], out)
        self.assertTrue(out["deliveryAfterFocus"]["actionVisible"], out)
        self.assertTrue(out["phone"]["actionVisible"], out)
        self.assertGreater(out["phone"]["visible"], 0, out)
        self.assertTrue(out["dark"], out)
        self.assertTrue(all(control["visible"] for control in out["shortBar"].values()), out)
        self.assertTrue(all(control["visible"] for control in out["phoneBar"].values()), out)
        self.assertEqual(out["phoneBar"], out["phoneBarAfterScroll"], out)
        self.assertTrue(out["hasDensity"] and out["compactDefault"], out)
        self.assertEqual(out["savedDensity"], "comfortable", out)
        self.assertGreater(out["comfortable"]["height"], out["initial"]["height"], out)

    def test_purchase_scroll_and_list_density(self):
        out = run_node(self, SCRIPT, "owner", OWNER_PW, self.purchase_id, "purchase")
        self.check_scrolling(out)
        self.assertGreater(out["purchaseAfterPageScroll"]["y"], 0, out)
        self.assertTrue(out["purchaseAfterPageScroll"]["actionVisible"], out)
        self.assertLess(out["list"]["padding"], 10, out)
        self.assertGreaterEqual(out["list"]["minText"], 13, out)
