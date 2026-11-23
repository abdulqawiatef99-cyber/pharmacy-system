"""Regressions from QA-1, using synthetic stock and the real browser screens."""
import json
import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from app.catalog import service as catalog
from app.core.errors import Conflict, Forbidden
from app.finance import money
from app.sales import attachments
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
    out = { asked: text.includes('السعر اتغير من 100.00 لـ 120.00: تغيّر سعر الرصيد الحالي (10 علبة) للسعر الجديد؟'),
      options: await p.locator('dialog[open] select option').evaluateAll(xs => xs.map(x => x.value)),
      chosen: await p.locator('dialog[open] select').evaluateAll(xs => xs.map(x => x.value)) };
  } else if (probe === 'purchase-search') {
    await p.waitForSelector('#puBox tbody tr');
    await p.fill('#puQ', 'QA-HIGHER');
    out = { count: await p.locator('#puBox tbody tr').count(), total: await p.locator('#puBox > p').innerText() };
  } else if (probe === 'official') {
    out = { detail: await p.locator('#app table').first().innerText(),
      totals: await p.locator('#app .totals').first().innerText(),
      receipt: await (await p.request.get(base + '/receipt/' + route.split('/').pop())).text() };
  } else if (probe === 'vat') {
    await p.waitForSelector('#qaQ');
    const fields = await p.locator('#app input, #app select').evaluateAll(fields =>
      fields.map(f => [f.getAttribute('aria-label'), f.dataset.field,
        ...Array.from(f.labels || [], l => l.textContent)].join(' ')));
    out = { fields, vat: fields.some(t => /ضريبة|VAT|tax_amount/i.test(t)) };
    if (out.vat) {
      const edit = p.locator('[data-field=tax_amount]').first();
      const saved = p.waitForResponse(r => r.url().includes('/lines/') && r.request().method() === 'PATCH');
      await edit.fill('0.01');
      await edit.press('Tab');
      const data = await (await saved).json();
      out.savedVat = data.lines[0].tax_amount;
      out.savedMode = data.lines[0].tax_included;
      out.total = data.invoice.computed_total;
    }
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
  } else if (probe === 'repeat-return') {
    const ids = [];
    for (let n = 0; n < 2; n++) {
      await p.locator('[data-ret]').fill('1');
      await p.fill('#rReason', 'QA repeat return');
      const response = p.waitForResponse(r => r.url().endsWith('/return') && r.request().method() === 'POST');
      await p.click('#rGo');
      const result = await (await response).json();
      ids.push(result.return_id);
      await p.waitForFunction(count => document.querySelectorAll('a[href^="/return-receipt/"]').length >= count, n + 1,
                              { timeout: 5000 });
    }
    out = { ids };
  } else if (probe === 'wallet') {
    const options = await p.locator('#rMethod option').evaluateAll(options =>
      options.filter(o => !o.disabled).map(o => o.textContent));
    out = { options, wallet: options.some(t => /محفظة|تحويل/.test(t)) };
  } else {
    await p.fill('#posQ', 'QA Amber');
    const result = p.locator('#posRes .res').first();
    await result.locator('[data-kind=pack]').waitFor();
    const advertised = await result.locator('.price').innerText();
    const prices = await result.innerText();
    await result.locator('[data-kind=pack]').click();
    await p.waitForSelector('#posCart tbody [data-minus]');
    const due = await p.locator('#posSide .due b').innerText();
    out = { advertised, due, prices, line: await p.locator('#posCart tbody tr').first().innerText() };
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

    def purchase_draft(self, invoice_no="QA-HIGHER"):
        p = Purchasing(self.db, self.s)
        sid = p.create_manual(self.ctx, supplier_id=self.supplier)
        p.update_header(self.ctx, sid, {"invoice_no": invoice_no}, None)
        p.add_line(self.ctx, sid, {"item_id": self.item, "qty": 2, "public_price": 12000,
                                  "discount_bp": 2000, "batch_no": "QA-new",
                                  "expiry_date": (self.today + timedelta(days=800)).isoformat()}, None)
        p.update_header(self.ctx, sid, {"declared_total": 19200}, None)
        self.assertTrue(p.get(self.ctx, sid)["can_approve"])
        return sid

    def test_QA_001_purchase_prompts_about_repricing_stock(self):
        sid = self.purchase_draft()
        out = run_node(self, SCRIPT, "owner", OWNER_PW, f"#/purchases/{sid}", "price")
        self.assertTrue(out["asked"], "QA-001: expected a decision about the existing batches")
        self.assertEqual(out["options"], ["keep", "all", "per_batch"])
        self.assertEqual(out["chosen"], ["keep"])

    def test_purchase_prices_require_current_decision_and_keep_same_lot(self):
        from app.core.permissions import DEFAULT_ROLES

        purchasing = Purchasing(self.db, self.s)
        sid = self.purchase_draft()
        line = purchasing.get(self.ctx, sid)["lines"][0]
        purchasing.update_line(self.ctx, sid, line["id"], {"batch_no": "QA-old", "expiry_date": self.expiry}, None)
        assistant = replace(self.ctx, role_code="assistant", permissions=frozenset(DEFAULT_ROLES["assistant"][1]))
        self.assertEqual(purchasing.price_review(assistant, sid)["default"], "keep")
        review = purchasing.price_review(self.ctx, sid)
        with self.assertRaises(Forbidden):
            purchasing.approve(assistant, sid, review["version"], require_price_review=True)
        with self.assertRaises(Conflict):
            purchasing.approve(self.ctx, sid, review["version"], require_price_review=True)
        self.add_new_stock()
        with self.assertRaises(Conflict):
            purchasing.approve(self.ctx, sid, review["version"], require_price_review=True,
                               price_token=review["token"], price_decisions={self.item: {"mode": "keep"}})
        review = purchasing.price_review(assistant, sid)
        approver = replace(assistant, approver_id=self.ctx.user_id,
                           approver_permissions=frozenset({"purchases.approve", "items.price"}))
        purchasing.approve(approver, sid, review["version"], require_price_review=True,
                           price_token=review["token"], price_decisions={self.item: {"mode": "keep"}})
        prices = self.db.all("SELECT public_price FROM batches WHERE org_id=? AND item_id=? AND batch_no='QA-OLD'"
                             " ORDER BY price_variant", (self.ctx.org_id, self.item))
        self.assertEqual([r["public_price"] for r in prices], [8000, 12000])
        sale = Sales(self.db).add_line(self.ctx, Sales(self.db).create(self.ctx), self.item, packs=1)
        self.assertEqual(sale["total"], 8000)
        audit = self.db.one("SELECT after_json FROM audit_log WHERE org_id=? AND entity_id=?"
                            " AND action='purchase.approve'", (self.ctx.org_id, sid))
        after = json.loads(audit["after_json"])
        self.assertEqual(after["approved_by"], self.ctx.user_id)
        self.assertEqual(after["stock_price_decisions"][0]["mode"], "keep")

    def test_repricing_stock_needs_price_authority_and_keep_ignores_the_new_price(self):
        from app.core.permissions import DEFAULT_ROLES

        purchasing = Purchasing(self.db, self.s)
        sid = self.purchase_draft()
        assistant = replace(self.ctx, role_code="assistant", permissions=frozenset(DEFAULT_ROLES["assistant"][1]))
        approver = replace(assistant, approver_id=self.ctx.user_id,
                           approver_permissions=frozenset({"purchases.approve"}))
        review = purchasing.price_review(assistant, sid)
        self.assertTrue(review["changes"])
        with self.assertRaises(Forbidden):
            purchasing.approve(approver, sid, review["version"], require_price_review=True,
                               price_token=review["token"], price_decisions={self.item: {"mode": "all"}})
        self.assertEqual(purchasing.get(self.ctx, sid)["invoice"]["status"], "draft")
        purchasing.approve(approver, sid, review["version"], require_price_review=True,
                           price_token=review["token"], price_decisions={self.item: {"mode": "keep"}})
        self.assertEqual(catalog.get_item(self.db, self.ctx, self.item)["public_price"], 10000)
        prices = {r["public_price"] for r in self.db.all(
            "SELECT public_price FROM batches WHERE org_id=? AND item_id=?", (self.ctx.org_id, self.item))}
        self.assertNotIn(12000, prices)

    def test_depleted_lot_keeps_its_price_when_the_old_boxes_return(self):
        sales = Sales(self.db)
        sold_id = sales.create(self.ctx)
        sold = sales.add_line(self.ctx, sold_id, self.item, packs=10)
        sold = sales.complete(self.ctx, sold_id, paid_cash=sold["total"], version=sold["version"])
        purchasing = Purchasing(self.db, self.s)
        sid = self.purchase_draft()
        line = purchasing.get(self.ctx, sid)["lines"][0]
        purchasing.update_line(self.ctx, sid, line["id"], {"batch_no": "QA-old", "expiry_date": self.expiry}, None)
        review = purchasing.price_review(self.ctx, sid)
        self.assertEqual(review["changes"], [])
        purchasing.approve(self.ctx, sid, review["version"], require_price_review=True,
                           price_token=review["token"], price_decisions={})
        sales.return_items(self.ctx, sold_id, {sold["lines"][0]["id"]: 2},
                           refund_method="cash", reason="QA old-price return")
        next_sale = sales.add_line(self.ctx, sales.create(self.ctx), self.item, packs=1)
        self.assertEqual(next_sale["total"], 8000)

    def test_receiving_a_new_price_never_releases_a_recalled_lot(self):
        from app.recalls.service import Recalls

        recalls = Recalls(self.db)
        recalls.add_notice(self.ctx, title="QA synthetic recall",
                           items=[{"product_name": "QA Amber 20TAB", "batch_numbers": ["QA-old"]}])
        recalls.decide(self.ctx, recalls.alerts(self.ctx)[0]["id"], "confirmed")
        purchasing = Purchasing(self.db, self.s)
        sid = self.purchase_draft()
        line = purchasing.get(self.ctx, sid)["lines"][0]
        purchasing.update_line(self.ctx, sid, line["id"], {"batch_no": "QA-old", "expiry_date": self.expiry}, None)
        review = purchasing.price_review(self.ctx, sid)
        purchasing.approve(self.ctx, sid, review["version"], require_price_review=True,
                           price_token=review["token"], price_decisions={self.item: {"mode": "keep"}})
        sales = Sales(self.db)
        with self.assertRaises(Conflict) as e:
            sales.add_line(self.ctx, sales.create(self.ctx), self.item, packs=1)
        self.assertEqual(e.exception.code, "insufficient_stock")

    def test_cannot_mark_a_recalled_lot_handled_with_a_price_variant_on_the_shelf(self):
        from app.recalls.service import Recalls

        purchasing = Purchasing(self.db, self.s)
        sid = self.purchase_draft()
        line = purchasing.get(self.ctx, sid)["lines"][0]
        purchasing.update_line(self.ctx, sid, line["id"], {"batch_no": "QA-old", "expiry_date": self.expiry}, None)
        review = purchasing.price_review(self.ctx, sid)
        purchasing.approve(self.ctx, sid, review["version"], require_price_review=True,
                           price_token=review["token"], price_decisions={self.item: {"mode": "keep"}})
        recalls = Recalls(self.db)
        recalls.add_notice(self.ctx, title="QA synthetic recall",
                           items=[{"product_name": "QA Amber 20TAB", "batch_numbers": ["QA-old"]}])
        original = next(a for a in recalls.alerts(self.ctx) if a["qty_in_stock"] == 20)
        recalls.decide(self.ctx, original["id"], "confirmed")
        with self.db.transaction():
            inventory.post_movement(self.db, self.ctx, item_id=self.item, batch_id=original["batch_id"],
                                    qty_delta=-20, movement_type="damage")
        with self.assertRaises(Conflict):
            recalls.decide(self.ctx, original["id"], "handled")
        sales = Sales(self.db)
        with self.assertRaises(Conflict):
            sales.add_line(self.ctx, sales.create(self.ctx), self.item, packs=1)

    def test_purchase_price_update_invalidates_a_draft_even_when_total_is_unchanged(self):
        other = catalog.create_item(self.db, self.ctx, {"name_en": "QA second box", "public_price": 10000})
        with self.db.transaction():
            batch = inventory.get_or_create_batch(self.db, self.ctx, other, "QA-second", self.expiry)
            inventory.post_movement(self.db, self.ctx, item_id=other, batch_id=batch,
                                    qty_delta=5, movement_type="opening", unit_cost=Decimal("20"))
        sales = Sales(self.db)
        sid = sales.create(self.ctx)
        sales.add_line(self.ctx, sid, self.item, packs=1)
        priced = sales.add_line(self.ctx, sid, other, packs=1)
        self.assertEqual(priced["total"], 18000)

        purchasing = Purchasing(self.db, self.s)
        draft = self.purchase_draft()
        line = purchasing.get(self.ctx, draft)["lines"][0]
        purchasing.update_line(self.ctx, draft, line["id"], {"public_price": 9000}, None)
        purchasing.add_line(self.ctx, draft, {"item_id": other, "qty": 1, "public_price": 9000, "discount_bp": 2000,
                                               "batch_no": "QA-new-second", "expiry_date": self.expiry}, None)
        purchasing.update_header(self.ctx, draft, {"declared_total": 21600}, None)
        review = purchasing.price_review(self.ctx, draft)
        purchasing.approve(self.ctx, draft, review["version"], require_price_review=True, price_token=review["token"],
                           price_decisions={item: {"mode": "all"} for item in (self.item, other)})
        self.assertEqual(sales.get(self.ctx, sid)["total"], 18000)
        with self.assertRaises(Conflict) as e:
            sales.complete(self.ctx, sid, paid_cash=18000, version=priced["version"])
        self.assertEqual(e.exception.code, "stale_version")

    def test_purchase_keep_preserves_legacy_untracked_stock(self):
        with self.db.transaction():
            inventory.post_movement(self.db, self.ctx, item_id=self.item, qty_delta=2,
                                    movement_type="opening", unit_cost=Decimal("20"))
        purchasing = Purchasing(self.db, self.s)
        sid = self.purchase_draft()
        review = purchasing.price_review(self.ctx, sid)
        purchasing.approve(self.ctx, sid, review["version"], require_price_review=True,
                           price_token=review["token"], price_decisions={self.item: {"mode": "keep"}})
        sales = Sales(self.db)
        quote = {"id": self.item, "pack_size": 2, "public_price": 12000,
                 "untracked_public_price": 10000, "is_service": 0}
        sales.quote_items(self.ctx, sales.create(self.ctx), [quote])
        self.assertEqual({r["price"]: r["qty"] for r in quote["price_stock"]}, {8000: 20, 12000: 4, 10000: 2})

    def test_purchase_reprices_all_or_the_selected_batch(self):
        self.add_new_stock()
        with self.db.transaction():
            self.db.execute("UPDATE batches SET public_price=11000 WHERE org_id=? AND batch_no='QA-LATER'",
                            (self.ctx.org_id,))
        purchasing = Purchasing(self.db, self.s)
        sid = self.purchase_draft()
        review = purchasing.price_review(self.ctx, sid)
        chosen = review["changes"][0]["batches"][0]["id"]
        purchasing.approve(self.ctx, sid, review["version"], require_price_review=True,
                           price_token=review["token"],
                           price_decisions={self.item: {"mode": "per_batch", "batches": [chosen]}})
        self.assertEqual(self.db.one("SELECT public_price FROM batches WHERE id=?", (chosen,))[0], 12000)
        later = self.db.one("SELECT public_price FROM batches WHERE org_id=? AND batch_no='QA-LATER'", (self.ctx.org_id,))
        self.assertEqual(later[0], 11000)

        another = self.purchase_draft("QA-ALL")
        line = purchasing.get(self.ctx, another)["lines"][0]
        purchasing.update_line(self.ctx, another, line["id"], {"public_price": 14000}, None)
        purchasing.update_header(self.ctx, another, {"declared_total": 22400}, None)
        purchasing.set_price_review_default(self.ctx, "all")
        review = purchasing.price_review(self.ctx, another)
        self.assertEqual(review["default"], "all")
        purchasing.approve(self.ctx, another, review["version"], require_price_review=True,
                           price_token=review["token"], price_decisions={self.item: {"mode": "all"}})
        prices = self.db.all("SELECT DISTINCT public_price FROM batches WHERE org_id=? AND item_id=?",
                             (self.ctx.org_id, self.item))
        self.assertEqual([x[0] for x in prices], [14000])

    def test_QA_002_sale_result_exposes_the_price_it_will_charge(self):
        self.add_new_stock()
        out = run_node(self, SCRIPT, "owner", OWNER_PW, "#/pos", "pos")
        self.assertEqual(out["advertised"].split()[0], out["due"].strip(),
                         "QA-002: quote the allocatable batch price")
        self.assertIn("120.00", out["prices"])
        self.assertIn("80.00", out["line"])

    def add_new_stock(self):
        with self.db.transaction():
            later = (self.today + timedelta(days=800)).isoformat()
            batch = inventory.get_or_create_batch(self.db, self.ctx, self.item, "QA-later", later)
            self.db.execute("UPDATE batches SET public_price=? WHERE id=?", (12000, batch))
            inventory.post_movement(self.db, self.ctx, item_id=self.item, batch_id=batch,
                                    qty_delta=4, movement_type="opening", unit_cost=Decimal("20"))

    def test_QA_006_011_official_prices_and_discount_on_both_receipts(self):
        from app.printing.escpos import receipt_lines

        self.add_new_stock()
        sales = Sales(self.db)
        sid = sales.create(self.ctx)
        sale = sales.add_line(self.ctx, sid, self.item, packs=11)
        sale = sales.set_invoice_discount(self.ctx, sid, bp=1000)
        sales.complete(self.ctx, sid, payments=[{"method": "cash", "amount": sale["total"]}], version=sale["version"])
        sale = sales.get(self.ctx, sid)
        out = run_node(self, SCRIPT, "owner", OWNER_PW, f"#/sales/{sid}", "official")
        thermal = str(receipt_lines("QA", sale, ""))
        for screen in (out["detail"], out["receipt"], thermal):
            self.assertIn("80.00", screen)
            self.assertIn("120.00", screen)
        for screen in (out["totals"], out["receipt"], thermal):
            self.assertLess(screen.index("الإجمالي"), screen.index("الخصم"))
            self.assertLess(screen.index("الخصم"), screen.index("المطلوب"))
            self.assertIn("920.00", screen)
            self.assertIn("92.00", screen)
            self.assertIn("828.00", screen)
        self.assertEqual(sale["lines"][0]["invoice_share"], 9200)

    def test_official_prices_leave_the_discounted_strip_refund_unchanged(self):
        with self.db.transaction():
            self.db.execute("UPDATE batches SET public_price=10000 WHERE org_id=? AND batch_no='QA-OLD'",
                            (self.ctx.org_id,))
        other = catalog.create_item(self.db, self.ctx, {"name_en": "QA Cyan", "public_price": 20000,
                                                        "is_service": 1})
        sales = Sales(self.db)
        sid = sales.create(self.ctx)
        sales.add_line(self.ctx, sid, self.item, packs=1, units=1, discount_bp=1000)
        sales.add_line(self.ctx, sid, other, packs=1)
        sale = sales.set_invoice_discount(self.ctx, sid, bp=1000)
        sale = sales.complete(self.ctx, sid, payments=[{"method": "cash", "amount": sale["total"]}],
                              version=sale["version"])
        self.assertEqual((sale["subtotal"], sale["discount_total"], sale["total"]), (35000, 4850, 30150))
        line = next(l for l in sale["lines"] if l["item_id"] == self.item)
        self.assertEqual(sales.return_items(self.ctx, sid, {line["id"]: 1}, refund_method="cash",
                                            reason="QA customer return")["refund"], 4050)

    def test_QA_013_purchase_search_filters_the_approved_total(self):
        purchasing = Purchasing(self.db, self.s)
        sid = self.purchase_draft()
        purchasing.approve(self.ctx, sid, None)
        other = self.purchase_draft("QA-OTHER")
        purchasing.approve(self.ctx, other, None)
        out = run_node(self, SCRIPT, "owner", OWNER_PW, "#/purchases", "purchase-search")
        self.assertEqual(out["count"], 1)
        self.assertIn("1 فاتورة", out["total"])
        self.assertIn("192.00", out["total"])

    def test_QA_003_manual_purchase_has_a_vat_field(self):
        sid = self.purchase_draft()
        out = run_node(self, SCRIPT, "owner", OWNER_PW, f"#/purchases/{sid}", "vat")
        self.assertTrue(out["vat"], "QA-003: the owner needs to enter the paper's line VAT")
        self.assertEqual((out["savedVat"], out["savedMode"], out["total"]), (1, 0, 19201))

    def test_manual_vat_modes_use_exact_payable_and_preserve_line_tax(self):
        purchasing = Purchasing(self.db, self.s)
        for mode, expected in ((0, 607), (1, 606)):
            sid = purchasing.create_manual(self.ctx, supplier_id=self.supplier)
            purchasing.update_header(self.ctx, sid, {"invoice_no": f"QA-VAT-{mode}", "declared_total": expected}, None)
            purchase = purchasing.add_line(self.ctx, sid, {
                "item_id": self.item, "qty": 10, "public_price": 101, "discount_bp": 4000,
                "tax_amount": 1, "tax_included": mode, "batch_no": f"QA-VAT-{mode}", "expiry_date": self.expiry,
            }, None)
            self.assertEqual(purchase["invoice"]["computed_total"], expected)
            self.assertEqual((purchase["lines"][0]["tax_amount"], purchase["lines"][0]["tax_included"]), (1, mode))
            self.assertTrue(purchase["can_approve"])
            purchasing.approve(self.ctx, sid, None)
            self.assertEqual(self.db.one("SELECT amount FROM supplier_ledger WHERE org_id=? AND ref_id=?",
                                         (self.ctx.org_id, sid))[0], expected)

    def test_typed_total_cannot_omit_one_piastre_of_explicit_additive_vat(self):
        purchasing = Purchasing(self.db, self.s)
        sid = purchasing.create_manual(self.ctx, supplier_id=self.supplier)
        purchasing.update_header(self.ctx, sid, {"invoice_no": "QA-VAT-EXACT", "declared_total": 10001}, None)
        draft = purchasing.add_line(self.ctx, sid, {
            "item_id": self.item, "qty": 1, "unit_net_price": 10000, "line_total": 10000,
            "tax_amount": 1, "tax_included": 0, "batch_no": "QA-VAT-EXACT", "expiry_date": self.expiry,
        }, None)
        self.assertFalse(draft["can_approve"])
        with self.assertRaises(Conflict):
            purchasing.approve(self.ctx, sid, None)
        corrected = purchasing.update_line(self.ctx, sid, draft["lines"][0]["id"], {"line_total": 10001}, None)
        self.assertTrue(corrected["can_approve"])

    def finish_sale(self, payment):
        sales = Sales(self.db)
        sid = sales.create(self.ctx)
        sale = sales.add_line(self.ctx, sid, self.item, packs=1)
        sales.complete(self.ctx, sid, payments=[{**payment, "amount": sale["total"]}], version=sale["version"])
        return sid

    def test_QA_004_guest_wallet_sale_offers_refund_to_original_method(self):
        self.http_login()
        channels = self.c.get("/api/payment-channels").json()["channels"]
        channel = next(c["id"] for c in channels if c["kind"] == "transfer")
        sid = self.finish_sale({"method": "transfer", "channel_id": channel})
        out = run_node(self, SCRIPT, "owner", OWNER_PW, f"#/sales/{sid}", "wallet")
        self.assertTrue(out["wallet"], "QA-004: a transfer-paid sale needs a same-method refund choice")

    def test_wallet_refund_requires_confirmation_and_debits_the_original_route(self):
        self.http_login()
        channel = next(c["id"] for c in self.c.get("/api/payment-channels").json()["channels"] if c["kind"] == "transfer")
        original = money.create_account(self.db, self.ctx, "QA wallet A", "wallet")["id"]
        rerouted = money.create_account(self.db, self.ctx, "QA wallet B", "wallet")["id"]
        money.set_route(self.db, self.ctx, "transfer", channel, original)
        sid = self.finish_sale({"method": "transfer", "channel_id": channel})
        sales = Sales(self.db)
        line = sales.get(self.ctx, sid)["lines"][0]["id"]
        with self.assertRaises(Conflict):
            sales.return_items(self.ctx, sid, {line: 1}, refund_method="transfer", reason="QA return")
        proof = attachments.attach(self.db, self.tmp, self.ctx, sid, b"%PDF synthetic transfer proof")
        attachments.review(self.db, self.ctx, proof["attachments"][0]["id"], True, amount=4000)
        money.set_route(self.db, self.ctx, "transfer", channel, rerouted)
        with self.assertRaises(Conflict):
            sales.return_items(self.ctx, sid, {line: 2}, refund_method="transfer", reason="QA return")
        returned = sales.return_items(self.ctx, sid, {line: 1}, refund_method="transfer", reason="QA return")
        self.assertEqual(returned["refund"], 4000)
        debit = self.db.one("SELECT account_id, amount FROM money_movements WHERE org_id=? AND ref_id=? AND kind='refund'",
                            (self.ctx.org_id, sid))
        self.assertEqual(tuple(debit), (original, -4000))
        self.assertEqual(sales.get(self.ctx, sid)["returns"][0]["channel_id"], channel)
        summary = CashDrawer(self.db).summary(self.ctx)
        self.assertEqual(summary["transfer_refunds"], 4000)
        wallet = next(ch for ch in summary["by_channel"] if ch["method"] == "transfer")
        self.assertEqual((wallet["amount"], wallet["refunded"]), (8000, 4000))

    def test_legacy_refund_from_a_distinct_card_account_leaves_wallet_room(self):
        from unittest.mock import patch
        from app.core import audit

        self.http_login()
        channels = {c["kind"]: c["id"] for c in self.c.get("/api/payment-channels").json()["channels"]}
        card = money.create_account(self.db, self.ctx, "QA card account", "bank")["id"]
        wallet = money.create_account(self.db, self.ctx, "QA original wallet", "wallet")["id"]
        money.set_route(self.db, self.ctx, "card", channels["card"], card)
        money.set_route(self.db, self.ctx, "transfer", channels["transfer"], wallet)
        sales = Sales(self.db)
        sid = sales.create(self.ctx)
        sale = sales.add_line(self.ctx, sid, self.item, packs=1)
        sale = sales.complete(self.ctx, sid, version=sale["version"], payments=[
            {"method": "card", "channel_id": channels["card"], "amount": 4000},
            {"method": "transfer", "channel_id": channels["transfer"], "amount": 4000},
        ])
        proof = attachments.attach(self.db, self.tmp, self.ctx, sid, b"%PDF synthetic split transfer proof")
        attachments.review(self.db, self.ctx, proof["attachments"][0]["id"], True)
        line = sale["lines"][0]["id"]
        record = audit.record

        def legacy_audit(*args, **kwargs):
            if args[2] == "sale.return":
                kwargs["after"].pop("card_only", None)
            return record(*args, **kwargs)

        with patch.object(audit, "record", side_effect=legacy_audit):
            sales.return_items(self.ctx, sid, {line: 1}, refund_method="card", reason="QA legacy card return")
        result = sales.return_items(self.ctx, sid, {line: 1}, refund_method="transfer", reason="QA wallet return")
        self.assertEqual(result["refund"], 4000)
        balances = self.db.all("SELECT account_id, sum(amount) net FROM money_movements WHERE org_id=? AND ref_id=?"
                               " GROUP BY account_id", (self.ctx.org_id, sid))
        self.assertEqual({r["account_id"]: r["net"] for r in balances}, {card: 0, wallet: 0})

    def test_an_unrouted_wallet_refund_never_debits_a_later_route(self):
        self.http_login()
        channel = next(c["id"] for c in self.c.get("/api/payment-channels").json()["channels"] if c["kind"] == "transfer")
        money.set_route(self.db, self.ctx, "transfer", channel, None)
        sid = self.finish_sale({"method": "transfer", "channel_id": channel})
        proof = attachments.attach(self.db, self.tmp, self.ctx, sid, b"%PDF synthetic unrouted transfer proof")
        attachments.review(self.db, self.ctx, proof["attachments"][0]["id"], True)
        routed_later = money.create_account(self.db, self.ctx, "QA wallet routed later", "wallet")["id"]
        money.set_route(self.db, self.ctx, "transfer", channel, routed_later)
        sales = Sales(self.db)
        line = sales.get(self.ctx, sid)["lines"][0]["id"]
        sales.return_items(self.ctx, sid, {line: 1}, refund_method="transfer", reason="QA original channel")
        self.assertIsNone(self.db.one("SELECT id FROM money_movements WHERE org_id=? AND ref_id=?",
                                      (self.ctx.org_id, sid)))
        self.assertEqual(sales.get(self.ctx, sid)["returns"][0]["channel_id"], channel)

    def test_two_identical_partial_returns_after_success_each_post_once(self):
        sid = self.finish_sale({"method": "cash"})
        result = run_node(self, SCRIPT, "owner", OWNER_PW, f"#/sales/{sid}", "repeat-return")
        self.assertEqual(len(set(result["ids"])), 2)
        self.assertEqual(len(Sales(self.db).get(self.ctx, sid)["returns"]), 2)

    def test_QA_005_latest_invoice_filter_owns_the_totals(self):
        self.http_login()
        channels = self.c.get("/api/payment-channels").json()["channels"]
        card = next(c["id"] for c in channels if c["kind"] == "card")
        self.finish_sale({"method": "cash"})
        self.finish_sale({"method": "card", "channel_id": card})
        out = run_node(self, SCRIPT, "owner", OWNER_PW, "#/sales", "invoice-filter")
        self.assertEqual((out["current"], out["stale"]), (2, 1))
        self.assertEqual(out["visible"], out["current"], "QA-005: ignore an older filter response")
