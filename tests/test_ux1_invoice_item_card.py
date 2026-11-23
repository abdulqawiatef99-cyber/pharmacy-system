"""UX-1 regression coverage for the shared invoice card and live purchase-price warning."""
import json
import shutil
import subprocess
import unittest
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app.catalog import service as cat
from app.core import people
from app.inventory import service as inventory
from app.purchasing.service import Purchasing
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import APICase

PW = "Staff-Pass-2026"
EXPIRY = (date.today() + timedelta(days=700)).isoformat()


@unittest.skipUnless(shutil.which("node"), "Node is needed for the frontend money comparison")
class TestPurchasePriceWarning(unittest.TestCase):
    def test_valid_prices_are_compared_in_piastres(self):
        js = (Path(__file__).resolve().parents[1] / "app/web/app.js").read_text(encoding="utf-8")
        start = js.index("const money =")
        end = js.index("// a typed whole number", start)
        warning = js.index("function purchasePriceWarning(")
        finish = js.index("\n}", warning) + 2
        code = js[start:end] + js[warning:finish] + """
console.log(JSON.stringify([
  purchasePriceWarning(4000, "40.0"), purchasePriceWarning(4000, ""),
  purchasePriceWarning(null, "42.5"), purchasePriceWarning(4000, "40.005"),
  purchasePriceWarning(4000, "42.5"), purchasePriceWarning(0, "0.01"),
  purchasePriceWarning(4000, "0"), purchasePriceWarning(4000, "-1"),
  purchasePriceWarning(0, ".50"), purchasePriceWarning(0, "2.500"),
  purchasePriceWarning(4000, "4.25e1")
]));
"""
        values = json.loads(subprocess.check_output(["node", "-e", code], encoding="utf-8"))
        self.assertEqual(values, ["", "", "", "سعر الجمهور اتغير من 40.00 لـ 40.01", "سعر الجمهور اتغير من 40.00 لـ 42.50",
                                  "سعر الجمهور اتغير من 0.00 لـ 0.01",
                                  "سعر الجمهور اتغير من 40.00 لـ 0.00", "",
                                  "سعر الجمهور اتغير من 0.00 لـ 0.50", "سعر الجمهور اتغير من 0.00 لـ 2.50",
                                  "سعر الجمهور اتغير من 40.00 لـ 42.50"])


class TestRecentInvoicePurchases(APICase):
    def test_purchase_card_batches_and_cost_use_the_receiving_branch(self):
        branch = "ux1-second-branch"
        receiving = replace(self.ctx, branch_id=branch)
        with self.db.transaction():
            self.db.execute("INSERT INTO branches (id, org_id, name) VALUES (?, ?, 'Fixture branch')",
                            (branch, self.ctx.org_id))
            inventory.post_movement(self.db, receiving, item_id=self.pan, qty_delta=4,
                                    movement_type="opening", unit_cost=Decimal("10"))
        purch = Purchasing(self.db, self.s)
        invoice = purch.create_manual(receiving)
        _, other = self.second_tenant()
        foreign = purch.create_manual(other)
        self.http_login()
        path = f"/api/items/{self.pan}/card?recent_purchases=1"
        self.assertEqual(self.c.get(path).json()["stock_qty"], 0)
        scoped = self.c.get(path + f"&purchase={invoice}").json()
        self.assertEqual((scoped["stock_qty"], scoped["avg_cost_pack"]), (4, 2000))
        self.assertEqual(self.c.get(path + f"&purchase={foreign}").status_code, 404)

    def test_history_keeps_multiple_purchases_from_one_supplier_and_respects_cost_access(self):
        supplier = cat.create_supplier(self.db, self.ctx, name="Synthetic History Supplier")
        purch = Purchasing(self.db, self.s)
        for days, qty in [(2, 2), (1, 3), (0, 4)]:
            invoice = purch.create_manual(self.ctx, supplier_id=supplier)
            inv_date = (date.today() - timedelta(days=days)).isoformat()
            purch.update_header(self.ctx, invoice, {"invoice_no": f"HISTORY-{days}", "invoice_date": inv_date}, None)
            state = purch.add_line(self.ctx, invoice, {"item_id": self.pan, "qty": qty, "public_price": 4000,
                                                       "discount_bp": 2500, "expiry_date": EXPIRY}, None)
            if days:
                purch.approve(self.ctx, invoice, state["invoice"]["version"])
        path = f"/api/items/{self.pan}/card"
        self.http_login()
        self.assertNotIn("recent_purchases", self.c.get(path).json())
        entries = self.c.get(path + "?recent_purchases=1").json()["recent_purchases"]
        self.assertEqual([(p["qty"], p["unit_net_price"]) for p in entries], [(3, 3000), (2, 3000)])
        people.add_user(self.db, self.ctx, username="staff", full_name="Synthetic Staff", password=PW,
                        role_code="pharmacist")
        self.http_login("staff", PW)
        self.assertNotIn("recent_purchases", self.c.get(path + "?recent_purchases=1").json())


PURCHASE = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, invoice, barcode, expiry] = process.argv.slice(1);
""" + LOGIN + r"""
  p.setDefaultTimeout(5000);
  await p.goto(base + '/#/purchases/' + invoice);
  const card = p.getByRole('dialog', { name: 'كارت الصنف', exact: true });
  const rows = p.locator('table.lines tbody tr');
  const line = rows.nth(0), unlinked = rows.nth(1), newLine = rows.nth(2);
  const price = line.locator('[data-field="public_price"]');
  const warning = line.getByText('سعر الجمهور اتغير من 40.00 لـ 42.50');
  const check = async (pass, message) => { if (!(await pass)) throw Error(message); };

  await p.locator('#qaQ').fill(barcode);
  await p.waitForFunction(() => document.activeElement?.id === 'qaQty');
  await p.locator('#qaQty').fill('7');
  await p.locator('#qaPrice').fill('41');
  await check(p.locator('#qa').getByText('سعر الجمهور اتغير من 40.00 لـ 41.00').isVisible(), 'picker warning');
  await p.keyboard.press('F3');
  await card.getByText('Synthetic Counter Item').waitFor();
  await p.keyboard.press('Escape');
  await card.waitFor({state:'hidden'});
  await check(p.locator('#qaPrice').evaluate(el => el === document.activeElement), 'picker focus');

  await price.fill('42.5');
  await check(warning.isVisible(), 'line warning before blur');
  await price.fill('40');
  await check(warning.count().then(n => n === 0), 'line warning clears');
  await price.fill('42.5');
  await p.evaluate(() => {
    document.addEventListener('keydown', () => { window.cardStarted = performance.now(); }, {once:true, capture:true});
    const observer = new MutationObserver(() => {
      if (document.querySelector('dialog[open]')?.textContent?.includes('UX1-BATCH')) {
        window.cardElapsed = performance.now() - window.cardStarted;
        observer.disconnect();
      }
    });
    observer.observe(document.body, {subtree:true, childList:true});
  });
  await p.keyboard.press('F3');
  await card.getByText('UX1-BATCH', {exact:true}).waitFor();
  await check(card.getByText('40.00', {exact:true}).first().isVisible(), 'public price');
  await check(card.getByText('30.00', {exact:true}).first().isVisible(), 'cost');
  await check(card.getByText(expiry, {exact:true}).first().isVisible(), 'expiry');
  await check(card.getByText('Synthetic Counter Supplier', {exact:true}).first().isVisible(), 'purchase history');
  await check(card.getByText(barcode, {exact:true}).isVisible(), 'barcode');
  await check(card.getByText('UX1-TEST', {exact:true}).isVisible(), 'code');
  await check(card.getByRole('row').filter({hasText:'من غير تشغيلة'}).getByText('35.00', {exact:true}).isVisible(),
              'public price of existing untracked stock');
  await card.getByRole('button', {name:'تعديل', exact:true}).click();
  await card.locator('#bcNew').fill('2000007190023');
  await card.locator('#bcAdd').click();
  await card.getByText('2000007190023', {exact:true}).waitFor();
  await p.keyboard.press('Escape');
  await card.waitFor({state:'hidden'});
  await p.waitForFunction(() => document.activeElement?.dataset.field === 'public_price');
  await p.keyboard.press('F3');
  await card.getByText(barcode, {exact:true}).waitFor();
  await check(card.getByText('2000007190023', {exact:true}).count().then(n => n === 0), 'cancelled barcode edit');
  await card.getByRole('button', {name:'تعديل', exact:true}).click();
  await card.locator('[name="name_en"]').fill('Synthetic Counter Renamed');
  await card.locator('[name="code"]').fill('UX1-UPDATED');
  await card.locator('[name="base_unit_name"]').fill('شريط');
  await card.locator('[name="public_price"]').fill('41');
  await card.getByRole('button', {name:'احفظ', exact:true}).click();
  await card.waitFor({state:'hidden'});
  await line.getByText('Synthetic Counter Renamed', {exact:false}).waitFor();
  await check(price.evaluate(el => el === document.activeElement), 'edited line focus');
  await check(price.inputValue().then(v => +v === 42.5), 'typed invoice price');
  await check(line.getByText('سعر الجمهور اتغير من 41.00 لـ 42.50').isVisible(), 'refreshed price comparison');
  await check(line.getByText('UX1-UPDATED · علبة · 2 شريط', {exact:true}).isVisible(), 'refreshed code and pack');
  await check(p.locator('#qaQty').inputValue().then(v => v === '7'), 'pending picker quantity');
  await check(p.locator('#qaPrice').inputValue().then(v => +v === 41), 'pending picker price');
  await check(p.locator('#qaPriceWarning').isHidden(), 'refreshed picker price comparison');

  await unlinked.getByRole('button', {name:'كارت الصنف', exact:true}).click();
  await card.getByRole('searchbox').fill('Synthetic Counter Renamed');
  await card.getByRole('button', {name:'ربط الصنف', exact:true}).click();
  await card.waitFor({state:'hidden'});
  await unlinked.getByText('Synthetic Counter Renamed', {exact:false}).waitFor();

  await newLine.getByRole('button', {name:'كارت الصنف', exact:true}).click();
  await card.getByRole('button', {name:'صنف جديد', exact:true}).click();
  await card.locator('[name="name_en"]').fill('Brand New Fixture Item');
  await card.getByRole('button', {name:'احفظ', exact:true}).click();
  await card.waitFor({state:'hidden'});
  await newLine.getByText('Brand New Fixture Item', {exact:true}).waitFor();

  const approval = p.getByRole('dialog').filter({hasText:'سعر الرصيد الحالي'});
  p.once('dialog', d => d.accept());
  await p.locator('#approve').click();
  await approval.waitFor();
  const options = await approval.locator('select').first().locator('option').evaluateAll(xs => xs.map(x => x.value));
  await approval.getByRole('button', {name:'رجوع', exact:true}).click();
  console.log(JSON.stringify({elapsed: await p.evaluate(() => window.cardElapsed), options, hash: new URL(p.url()).hash}));
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
"""

SALE = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, barcode, second, unlinkedInvoice] = process.argv.slice(1);
""" + LOGIN + r"""
  p.setDefaultTimeout(5000);
  await p.goto(base + '/#/pos');
  const search = p.locator('#posQ');
  await search.fill(barcode);
  await search.press('Enter');
  const line = p.locator('table.cart tbody tr').filter({hasText:'Synthetic Counter Item'});
  await line.locator('[data-minus]').waitFor();
  const card = p.getByRole('dialog', { name: 'كارت الصنف', exact: true });
  await search.focus();
  await p.keyboard.press('F3');
  await card.getByText('UX1-BATCH', {exact:true}).waitFor();
  const readOnly = await card.getByRole('button', {name:'تعديل', exact:true}).count() === 0;
  const hidesCosts = await card.getByText('متوسط التكلفة', {exact:true}).count() === 0
    && await card.getByText('آخر المشتريات', {exact:true}).count() === 0;
  await p.keyboard.press('Escape');
  await card.waitFor({state:'hidden'});
  const focus = await search.evaluate(el => el === document.activeElement);
  await search.fill(second);
  await search.press('Enter');
  await p.getByText('Synthetic Service Fixture', {exact:true}).first().waitFor();
  const button = line.getByRole('button', {name:'كارت الصنف', exact:true});
  await button.focus();
  await p.keyboard.press('F3');
  await card.getByText('UX1-BATCH', {exact:true}).waitFor();
  await p.keyboard.press('Escape');
  await card.waitFor({state:'hidden'});
  await button.click();
  await card.getByText('UX1-BATCH', {exact:true}).waitFor();
  const out = {readOnly, hidesCosts, focus, pages:b.contexts()[0].pages().length, hash:new URL(p.url()).hash};
  await p.goto(base + '/#/purchases/' + unlinkedInvoice);
  await p.locator('table.lines').getByRole('button', {name:'كارت الصنف', exact:true}).click();
  await card.getByRole('button', {name:'صنف جديد', exact:true}).click();
  const rejected = p.waitForResponse(r => r.url().endsWith('/new-item'));
  await card.getByRole('button', {name:'احفظ', exact:true}).click();
  out.createStatus = (await rejected).status();
  console.log(JSON.stringify(out));
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestInvoiceItemCard(APICase):
    barcode = "2000007190016"

    def setUp(self):
        super().setUp()
        self.it = cat.create_item(self.db, self.ctx, {"name_en": "Synthetic Counter Item", "pack_size": 2,
                                                      "public_price": 4000, "code": "UX1-TEST"})
        cat.add_barcode(self.db, self.ctx, self.it, self.barcode)
        self.supplier = cat.create_supplier(self.db, self.ctx, name="Synthetic Counter Supplier")
        self.purch = Purchasing(self.db, self.s)
        history = self.draft("UX1-HISTORY")
        self.add(history, {"item_id": self.it, "qty": 5, "public_price": 4000,
                           "discount_bp": 2500, "batch_no": "UX1-BATCH"})
        self.purch.approve(self.ctx, history, self.purch.get(self.ctx, history)["invoice"]["version"])
        with self.db.transaction():
            inventory.post_movement(self.db, self.ctx, item_id=self.it, qty_delta=2, batch_id=None,
                                    movement_type="opening", unit_cost=Decimal("15"))
            self.db.execute("UPDATE items SET untracked_public_price=3500 WHERE org_id=? AND id=?",
                            (self.ctx.org_id, self.it))

    def draft(self, number):
        invoice = self.purch.create_manual(self.ctx, supplier_id=self.supplier)
        self.purch.update_header(self.ctx, invoice, {"invoice_no": number}, None)
        return invoice

    def add(self, invoice, line):
        return self.purch.add_line(self.ctx, invoice, {"expiry_date": EXPIRY, **line}, None)

    def test_purchase_card_on_30k_items_keeps_draft_focus_and_approval(self):
        draft = self.draft("UX1-DRAFT")
        self.add(draft, {"item_id": self.it, "qty": 1, "public_price": 4000, "discount_bp": 2500,
                         "batch_no": "UX1-NEXT"})
        for no, name in enumerate(("Unlinked Fixture", "New Fixture"), 1):
            self.add(draft, {"raw_name": name, "qty": 1, "public_price": 4250, "discount_bp": 2500,
                             "batch_no": f"UX1-OTHER-{no}"})
        with self.db.transaction():
            self.db.execute(
                "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<29998)"
                " INSERT INTO items (id, org_id, name_en, name_norm, dedupe_key)"
                " SELECT 'ux1-'||x, ?, 'Fixture '||x, 'fixture '||x, 'ux1-'||x FROM n", (self.ctx.org_id,))
        self.assertEqual(self.db.one("SELECT count(*) n FROM items")["n"], 30_000)
        out = run_node(self, PURCHASE, "owner", OWNER_PW, draft, self.barcode, EXPIRY)
        print(f"UX-1 item card on 30,000 items: {out['elapsed']:.1f} ms")
        self.assertLess(out["elapsed"], 300)
        self.assertEqual(out["options"], ["keep", "all", "per_batch"])
        self.assertEqual(out["hash"], f"#/purchases/{draft}")
        self.assertEqual(self.purch.get(self.ctx, draft)["invoice"]["status"], "draft")

    def test_sale_key_and_button_open_the_same_permission_filtered_card(self):
        second = "2000007190030"
        fixture = cat.create_item(self.db, self.ctx, {"name_en": "Synthetic Service Fixture",
                                                       "public_price": 1000, "is_service": True})
        cat.add_barcode(self.db, self.ctx, fixture, second)
        people.add_user(self.db, self.ctx, username="cashier", full_name="Synthetic Cashier", password=PW,
                        role_code="assistant")
        draft = self.draft("UX1-UNLINKED")
        self.add(draft, {"raw_name": "Unlinked Fixture", "qty": 1, "public_price": 4000, "discount_bp": 2500})
        out = run_node(self, SALE, "cashier", PW, self.barcode, second, draft)
        self.assertEqual(out, {"readOnly": True, "hidesCosts": True, "focus": True,
                               "pages": 1, "hash": "#/pos", "createStatus": 403})
