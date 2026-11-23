"""Expected failures from QA-1, using synthetic stock and the real browser screens."""
import unittest
from datetime import timedelta
from decimal import Decimal

from app.catalog import service as catalog
from app.inventory import service as inventory
from app.purchasing.service import Purchasing
from app.sales.cash import CashDrawer
from app.sales.service import Sales, local_today
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import APICase


SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, route, probe] = process.argv.slice(1);
""" + LOGIN + r"""
  const dialogs = [];
  p.on('dialog', async d => {
    dialogs.push(d.message());
    if (d.type() === 'confirm') await d.accept(); else await d.dismiss();
  });
  await p.goto(base + '/' + route);
  await p.waitForSelector('#app [data-tour-replay]');
  let out;
  if (probe === 'price') {
    await p.locator('#approve').click();
    await p.waitForSelector('dialog[open], #pinApproved');
    const htmlDialog = await p.locator('dialog[open]').allTextContents();
    const text = [...dialogs, ...htmlDialog].join(' ');
    out = { asked: /المخزون الحالي|الرصيد الحالي|التشغيلات القديمة|إعادة تسعير/.test(text) };
  } else if (probe === 'vat') {
    await p.waitForSelector('#qaQ');
    const fields = await p.locator('#app input, #app select').evaluateAll(fields =>
      fields.map(f => [f.getAttribute('aria-label'), f.dataset.field,
        ...Array.from(f.labels || [], l => l.textContent)].join(' ')));
    out = { fields, vat: fields.some(t => /ضريبة|VAT|tax_amount/i.test(t)) };
  } else if (probe === 'invoice-filter') {
    await p.waitForSelector('#iTable');
    let releaseOld;
    const hold = new Promise(resolve => { releaseOld = resolve; });
    await p.route('**/api/reports/invoices?*', async route => {
      if (new URL(route.request().url()).searchParams.get('types') === 'cash') await hold;
      await route.continue();
    });
    const response = type => p.waitForResponse(r => {
      const url = new URL(r.url());
      return url.pathname === '/api/reports/invoices' && url.searchParams.get('types') === type;
    });
    const older = response('cash');
    await p.click('[data-it=cash]');
    const newer = response('');
    await p.click('[data-it=cash]');
    const current = (await (await newer).json()).totals.n;
    const visibleCount = () => +document.querySelector('#iBox .totals > div b')?.textContent;
    await p.waitForFunction(count => (
      +document.querySelector('#iBox .totals > div b')?.textContent === count
    ), current);
    releaseOld();
    const stale = (await (await older).json()).totals.n;
    // Wait for the older response's render turn. The stale response may be ignored by a fixed screen.
    await p.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    out = { current, stale, visible: await p.evaluate(visibleCount) };
  } else if (probe === 'wallet') {
    const options = await p.locator('#rMethod option').evaluateAll(options =>
      options.filter(o => !o.disabled).map(o => o.textContent));
    out = { options, wallet: options.some(t => /محفظة|تحويل/.test(t)) };
  } else {
    await p.fill('#posQ', 'QA Amber');
    const result = p.locator('#posRes .res').first();
    await result.locator('[data-kind=pack]').waitFor();
    const advertised = await result.locator('.price').innerText();
    await result.locator('[data-kind=pack]').click();
    await p.waitForSelector('#posCart tbody [data-minus]');
    const due = await p.locator('#posSide .due b').innerText();
    out = { advertised, due };
  }
  console.log(JSON.stringify(out));
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
"""


@needs_browser
class QA20261123(APICase):
    def setUp(self):
        super().setUp()
        self.today = local_today()
        self.item = catalog.create_item(
            self.db, self.ctx,
            {"name_en": "QA Amber 20TAB", "pack_size": 2, "public_price": 10000},
        )
        self.expiry = (self.today + timedelta(days=700)).isoformat()
        with self.db.transaction():
            batch = inventory.get_or_create_batch(self.db, self.ctx, self.item, "QA-old", self.expiry)
            self.db.execute("UPDATE batches SET public_price=? WHERE id=?", (8000, batch))
            inventory.post_movement(self.db, self.ctx, item_id=self.item, batch_id=batch,
                                    qty_delta=20, movement_type="opening", unit_cost=Decimal("20"))
        CashDrawer(self.db).open(self.ctx, 100000)
        self.supplier = catalog.create_supplier(self.db, self.ctx, name="QA Company", supplier_type="company")

    def purchase_draft(self):
        p = Purchasing(self.db, self.s)
        sid = p.create_manual(self.ctx, supplier_id=self.supplier)
        p.update_header(self.ctx, sid, {"invoice_no": "QA-HIGHER"}, None)
        p.add_line(self.ctx, sid, {"item_id": self.item, "qty": 2, "public_price": 12000,
                                  "discount_bp": 2000, "batch_no": "QA-new",
                                  "expiry_date": (self.today + timedelta(days=800)).isoformat()}, None)
        p.update_header(self.ctx, sid, {"declared_total": 19200}, None)
        self.assertTrue(p.get(self.ctx, sid)["can_approve"])
        return sid

    @unittest.expectedFailure
    def test_QA_001_purchase_prompts_about_repricing_stock(self):
        sid = self.purchase_draft()
        out = run_node(self, SCRIPT, "owner", OWNER_PW, f"#/purchases/{sid}", "price")
        self.assertTrue(out["asked"], "QA-001: expected a decision about the existing batches")

    @unittest.expectedFailure
    def test_QA_002_sale_result_exposes_the_price_it_will_charge(self):
        out = run_node(self, SCRIPT, "owner", OWNER_PW, "#/pos", "pos")
        self.assertEqual(out["advertised"].split()[0], out["due"].strip(),
                         "QA-002: quote the allocatable batch price")

    @unittest.expectedFailure
    def test_QA_003_manual_purchase_has_a_vat_field(self):
        sid = self.purchase_draft()
        out = run_node(self, SCRIPT, "owner", OWNER_PW, f"#/purchases/{sid}", "vat")
        self.assertTrue(out["vat"], "QA-003: the owner needs to enter the paper's line VAT")

    def finish_sale(self, payment):
        sales = Sales(self.db)
        sid = sales.create(self.ctx)
        sale = sales.add_line(self.ctx, sid, self.item, packs=1)
        sales.complete(self.ctx, sid, payments=[{**payment, "amount": sale["total"]}], version=sale["version"])
        return sid

    @unittest.expectedFailure
    def test_QA_004_guest_wallet_sale_offers_refund_to_original_method(self):
        self.http_login()
        channels = self.c.get("/api/payment-channels").json()["channels"]
        channel = next(c["id"] for c in channels if c["kind"] == "transfer")
        sid = self.finish_sale({"method": "transfer", "channel_id": channel})
        out = run_node(self, SCRIPT, "owner", OWNER_PW, f"#/sales/{sid}", "wallet")
        self.assertTrue(out["wallet"], "QA-004: a transfer-paid sale needs a same-method refund choice")

    @unittest.expectedFailure
    def test_QA_005_latest_invoice_filter_owns_the_totals(self):
        self.http_login()
        channels = self.c.get("/api/payment-channels").json()["channels"]
        card = next(c["id"] for c in channels if c["kind"] == "card")
        self.finish_sale({"method": "cash"})
        self.finish_sale({"method": "card", "channel_id": card})
        out = run_node(self, SCRIPT, "owner", OWNER_PW, "#/sales", "invoice-filter")
        self.assertEqual((out["current"], out["stale"]), (2, 1))
        self.assertEqual(out["visible"], out["current"], "QA-005: ignore an older filter response")
