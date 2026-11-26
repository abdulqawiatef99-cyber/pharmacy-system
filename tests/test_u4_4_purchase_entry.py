"""Purchase entry and posting, using only synthetic catalog and invoices."""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from app.catalog import service as catalog
from app.inventory import service as inventory
from app.purchasing.service import Purchasing
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import APICase, H

EXPIRY = (date.today() + timedelta(days=700)).isoformat()
SETTINGS = "/api/settings/purchase-entry"
ORDER = ["qty", "expiry", "discount", "bonus", "batch", "public_price", "tax_margin"]
BARCODE = "9876500012345"
NEXT_BARCODE = "9876500012346"


class TestPurchaseEntry(APICase):
    def setUp(self):
        super().setUp()
        self.http_login()
        self.purch = Purchasing(self.db, self.s)
        self.company = catalog.create_supplier(self.db, self.ctx, name="Synthetic Company", supplier_type="company")
        self.inv = self.purch.create_manual(self.ctx, supplier_id=self.company)
        self.path = f"/api/purchases/{self.inv}"

    def put_settings(self, **changes):
        result = self.c.put(SETTINGS, json=changes, headers=H)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def line(self, **changes):
        return {"item_id": self.pan, "qty": 2, "public_price": 4000, "discount_bp": 2500,
                "expiry_date": EXPIRY, **changes}

    def test_settings_order_and_thresholds(self):
        self.assertEqual(self.c.get(SETTINGS).json()["field_order"], ORDER)
        new_order = ORDER[1:] + ORDER[:1]
        saved = self.put_settings(field_order=new_order, qty_multiple=3, qty_min=4, public_delta_pct=80,
                                  last_cost_delta_pct=30)
        self.assertEqual(self.c.get(SETTINGS).json(), saved)
        self.assertEqual(self.c.get(self.path).json()["entry_order"], new_order)
        for changes in ({"field_order": ["qty"] * 7}, {"qty_multiple": True}, {"public_delta_pct": -1}):
            self.assertEqual(self.c.put(SETTINGS, json=changes, headers=H).status_code, 422)

    def test_typed_invoice_date_is_stored_in_report_format(self):
        today = date.today()
        result = self.c.patch(self.path, json={"changes": {"invoice_date": today.strftime("%Y%m%d")}}, headers=H)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["invoice"]["invoice_date"], today.isoformat())

    def test_impossible_line_writes_are_atomic_and_have_arabic_limits(self):
        for changes, label in [({"qty": 0}, "الكمية"), ({"public_price": 0}, "سعر الجمهور"),
                               ({"unit_net_price": 0}, "سعر الصيدلي"), ({"discount_bp": 10001}, "100"),
                               ({"distributor_margin": 2100}, "هامش الموزع"),
                               ({"tax_included": 1, "tax_amount": 10000}, "الضريبة"),
                               ({"expiry_date": "01/2000"}, "الصلاحية")]:
            before = self.c.get(self.path).json()
            result = self.c.post(self.path + "/lines", json={"line": self.line(**changes)}, headers=H)
            self.assertEqual(result.status_code, 422, result.text)
            self.assertIn(label, result.json()["error"]["message"])
            self.assertEqual(self.c.get(self.path).json(), before)
        state = self.purch.add_line(self.ctx, self.inv, self.line(), None)
        result = self.c.patch(self.path + "/lines/" + state["lines"][0]["id"],
                              json={"changes": {"qty": -1}}, headers=H)
        self.assertEqual(result.status_code, 422)
        self.assertIn("الكمية", result.json()["error"]["message"])
        self.assertEqual(self.purch.get(self.ctx, self.inv)["lines"][0]["qty"], 2)

    def test_unusual_requires_a_reason_and_changed_values_need_new_confirmation(self):
        self.purch.update_header(self.ctx, self.inv, {"invoice_no": "SYN-1"}, None)
        opening = self.purch.add_line(self.ctx, self.inv, self.line(), None)
        self.purch.approve(self.ctx, self.inv, opening["invoice"]["version"])
        self.put_settings(qty_multiple=2, qty_min=3, public_delta_pct=30, last_cost_delta_pct=20)
        inv = self.purch.create_manual(self.ctx, supplier_id=self.company)
        path = f"/api/purchases/{inv}"
        state = self.purch.add_line(self.ctx, inv, self.line(qty=6, discount_bp=6000), None)
        line_id = state["lines"][0]["id"]
        expected = {"unusual_qty", "unusual_public_price", "unusual_last_cost"}
        self.assertEqual({i["code"] for i in state["issues"] if i["code"].startswith("unusual_")}, expected)
        refused = self.c.post(path + "/approve", json={"version": state["invoice"]["version"]}, headers=H)
        self.assertEqual(refused.status_code, 409, refused.text)
        for code in expected:
            state = self.purch.acknowledge(self.ctx, inv, code, "طلب شراء استثنائي للتجربة", line_id, None)
        self.assertTrue(state["can_approve"])
        changed = self.purch.update_line(self.ctx, inv, line_id, {"qty": 7}, None)
        self.assertFalse(changed["can_approve"])
        self.assertFalse(next(i for i in changed["issues"] if i["code"] == "unusual_qty")["acknowledged"])

    def test_stock_uses_invoice_branch(self):
        branch = replace(self.ctx, branch_id="purchase-stock-branch")
        with self.db.transaction():
            self.db.execute("INSERT INTO branches (id, org_id, name) VALUES (?, ?, 'Synthetic branch')",
                            (branch.branch_id, branch.org_id))
            inventory.post_movement(self.db, branch, item_id=self.pan, qty_delta=7, movement_type="opening",
                                    unit_cost=Decimal("10"))
        invoice = self.purch.create_manual(branch)
        self.purch.add_line(branch, invoice, self.line(), None)
        state = self.c.get(f"/api/purchases/{invoice}").json()
        self.assertEqual(state["lines"][0]["stock_qty"], 7)

    def test_last_cost_checks_the_cost_after_invoice_discount(self):
        opening = self.purch.add_line(self.ctx, self.inv, self.line(), None)
        self.purch.approve(self.ctx, self.inv, opening["invoice"]["version"])
        invoice = self.purch.create_manual(self.ctx, supplier_id=self.company)
        self.purch.add_line(self.ctx, invoice, self.line(), None)
        state = self.purch.update_header(self.ctx, invoice, {"extra_discount": 5700}, None)
        self.assertIn("unusual_last_cost", {i["code"] for i in state["issues"]})
        self.assertFalse(state["can_approve"])

    def test_legacy_negative_bonus_can_be_opened_and_corrected(self):
        draft = self.purch.add_line(self.ctx, self.inv, self.line(), None)
        line_id = draft["lines"][0]["id"]
        with self.db.transaction():
            self.db.execute("UPDATE purchase_invoice_lines SET bonus_qty=-2 WHERE id=?", (line_id,))
        result = self.c.get(self.path)
        self.assertEqual(result.status_code, 200, result.text)
        state = result.json()
        self.assertIn("bonus_invalid", {i["code"] for i in state["issues"]})
        self.assertFalse(state["can_approve"])
        corrected = self.purch.update_line(self.ctx, self.inv, line_id, {"bonus_qty": 0}, None)
        self.assertTrue(corrected["can_approve"])


BROWSER = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, company, warehouse, barcode, nextBarcode] = process.argv.slice(1);
""" + LOGIN + r"""
  p.on('dialog', d => d.dismiss());
  await p.evaluate(async () => {
    const { organization, user } = await (await fetch('/api/me')).json();
    localStorage.setItem(`tour:${organization.id}:${user.id}`, JSON.stringify({ all: 1 }));
  });
  const active = () => p.evaluate(() => document.activeElement.id);
  const visibleOrder = () => p.locator('#qa [data-entry-field]:visible').evaluateAll(xs => xs.map(x => x.dataset.entryField));
  async function open(id) {
    await p.goto(base + '/#/purchases/' + id);
    await p.waitForSelector(`#qa[data-invoice="${id}"] #qaQ`);
    await p.locator('#supType .on').waitFor();
  }
  await open(company);
  const standard = await visibleOrder();
  const batch = p.locator('#qaBatch');
  await batch.fill('SYN-BATCH');
  await batch.focus();
  await p.keyboard.type(barcode);
  await p.keyboard.press('Enter');
  await p.waitForFunction(() => document.activeElement.id === 'qaQty');
  const preserved = await batch.inputValue();
  const qty = await p.inputValue('#qaQty');
  await p.fill('#qaDisc', '25');
  await p.focus('#qaQty');
  const enter = [await active()];
  for (let i = 0; i < 9; i++) {
    await p.keyboard.press('Enter');
    enter.push(await active());
  }
  await p.keyboard.press('Enter');
  const completed = p.locator('[data-purchase-line]');
  await completed.waitFor();
  await p.waitForFunction(() => document.activeElement.id === 'qaQ');
  const autocomplete = await p.locator('#qaQty, #qaBonus, #qaPrice, #qaDisc, #qaNet, #qaBatch, [data-h="invoice_no"], table.lines input').evaluateAll(xs => xs.every(x => x.autocomplete === 'off'));
  const stock = await completed.locator('[data-label="الرصيد"]').innerText();
  const help = await p.getByRole('button', {name: /اشرح الشاشة دي/}).count();
  const scan = async code => {
    await p.keyboard.press('F12');
    await p.keyboard.type(code);
    await p.keyboard.press('Enter');
    await p.waitForFunction(() => document.activeElement.id === 'qaQty');
  };
  await scan(barcode);
  await batch.fill('OLD-LOT');
  await p.fill('#qaExp', '12/2032');
  await p.fill('#qaNet', '10');
  await p.fill('#qaBonus', '3');
  await p.fill('#qaMargin', '5');
  await p.fill('#qaTax', '1');
  await scan(nextBarcode);
  const freshLine = [];
  for (const field of ['qaBatch', 'qaExp', 'qaNet', 'qaBonus', 'qaMargin', 'qaTax']) {
    freshLine.push(await p.inputValue('#' + field));
  }
  await p.fill('#qaDisc', '25');
  await p.focus('#qaNet');
  await p.keyboard.type('9e');
  await p.waitForFunction(() => document.querySelector('#qaNet').validity.badInput);
  await p.click('#qaAdd');
  const badPriceRejected = await p.waitForFunction(() => document.querySelector('#toast').textContent.includes('سعر الصيدلي'),
    null, { timeout: 3000 }).then(() => true, () => false);
  await p.goto(base + '/#/purchases');
  await p.locator('summary').filter({hasText: 'إعدادات إدخال الشراء'}).click();
  await p.locator('[data-order-field="expiry"] [data-move="-1"]').click();
  await p.fill('[name="qty_min"]', '25');
  const saved = p.waitForResponse(r => r.url().endsWith('/api/settings/purchase-entry') && r.request().method() === 'PUT');
  await p.getByRole('button', {name: 'حفظ إعدادات الشراء'}).click();
  const setting = await (await saved).json();
  await open(warehouse);
  const custom = await visibleOrder();
  const bonusHidden = await p.locator('#qaBonusField').isHidden();
  const header = p.locator('[data-h="invoice_no"]');
  await header.focus();
  await p.keyboard.press('F12');
  await p.keyboard.type(barcode);
  await p.keyboard.press('Enter');
  await p.waitForFunction(() => document.activeElement.id === 'qaExp');
  const routed = await header.inputValue();
  const invoiceDate = p.locator('[data-h="invoice_date"]');
  const dateBefore = await invoiceDate.inputValue();
  await invoiceDate.focus();
  await p.keyboard.type(']Q3010' + nextBarcode + '1732121510SYNLOT');
  await p.keyboard.press('Enter');
  await p.waitForFunction(() => document.activeElement.id === 'qaExp');
  const exactExpiry = await p.inputValue('#qaExp');
  const gs1Batch = await batch.inputValue();
  const datePreserved = (await invoiceDate.inputValue()) === dateBefore;
  console.log(JSON.stringify({standard, custom, preserved, qty, enter, autocomplete, stock, help, bonusHidden, routed,
                              savedQtyMin: setting.qty_min, freshLine, badPriceRejected, exactExpiry, gs1Batch, datePreserved}));
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestPurchaseEntryBrowser(APICase):
    def test_order_scanner_stock_and_autocomplete(self):
        company = catalog.create_supplier(self.db, self.ctx, name="Synthetic Company", supplier_type="company")
        warehouse = catalog.create_supplier(self.db, self.ctx, name="Synthetic Warehouse", supplier_type="warehouse")
        item = catalog.create_item(self.db, self.ctx, {"name_en": "Synthetic Scanner Item", "public_price": 4000},
                                   barcodes=[BARCODE])
        catalog.create_item(self.db, self.ctx, {"name_en": "Synthetic Second Scanner Item", "public_price": 5000},
                            barcodes=[NEXT_BARCODE])
        with self.db.transaction():
            inventory.post_movement(self.db, self.ctx, item_id=item, qty_delta=9, movement_type="opening",
                                    unit_cost=Decimal("10"))
        purch = Purchasing(self.db, self.s)
        first = purch.create_manual(self.ctx, supplier_id=company)
        second = purch.create_manual(self.ctx, supplier_id=warehouse)
        out = run_node(self, BROWSER, "owner", OWNER_PW, first, second, BARCODE, NEXT_BARCODE)
        self.assertEqual(out["standard"], ORDER)
        self.assertEqual(out["custom"], ["expiry", "qty", "discount", "batch", "public_price", "tax_margin"])
        self.assertEqual(out["preserved"], "SYN-BATCH")
        self.assertEqual(out["qty"], "1")
        self.assertEqual(out["enter"], ["qaQty", "qaExp", "qaDisc", "qaNet", "qaBonus", "qaBatch",
                                        "qaPrice", "qaMargin", "qaTax", "qaTaxMode"])
        self.assertTrue(out["autocomplete"])
        self.assertIn("9", out["stock"])
        self.assertEqual(out["help"], 1)
        self.assertTrue(out["bonusHidden"])
        self.assertEqual(out["routed"], "")
        self.assertEqual(out["savedQtyMin"], 25)
        self.assertEqual(out["freshLine"], ["", "", "", "0", "", ""])
        self.assertTrue(out["badPriceRejected"])
        self.assertEqual(out["exactExpiry"], "2032-12-15")
        self.assertEqual(out["gs1Batch"], "SYNLOT")
        self.assertTrue(out["datePreserved"])


BENCHMARK = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, inv] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.evaluate(async () => {
    const { organization, user } = await (await fetch('/api/me')).json();
    localStorage.setItem(`tour:${organization.id}:${user.id}`, JSON.stringify({ all: 1 }));
  });
  await p.goto(base + '/#/purchases/' + inv);
  await p.waitForSelector('#qaQ');
  const samples = [];
  for (let i = 1; i <= 21; i++) {
    await p.keyboard.press('F12');
    await p.keyboard.type(String(9880000000000 + i));
    await p.keyboard.press('Enter');
    await p.waitForFunction(() => document.activeElement.id === 'qaQty');
    await p.fill('#qaDisc', '25');
    await p.focus('#qaTaxMode');
    // Measure the final key event through the rendered line and restored item focus.
    await p.evaluate(() => {
      const form = document.querySelector('#qa');
      window.addTiming = new Promise(resolve => {
        form.addEventListener('keydown', () => {
          const start = performance.now();
          document.addEventListener('focusin', function ready(event) {
            if (event.target.id !== 'qaQ' || form.isConnected) return;
            document.removeEventListener('focusin', ready);
            resolve(performance.now() - start);
          });
        }, {capture: true, once: true});
      });
    });
    const response = p.waitForResponse(r => r.url().endsWith('/lines') && r.request().method() === 'POST');
    await p.keyboard.press('Enter');
    const result = await response;
    if (!result.ok()) throw new Error(await result.text());
    const elapsed = await p.evaluate(() => window.addTiming);
    if (i > 1) samples.push(elapsed);
  }
  console.log(JSON.stringify(samples));
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestPurchaseEntryPerformance(APICase):
    def test_keyboard_add_line_at_30000_items(self):
        import statistics
        company = catalog.create_supplier(self.db, self.ctx, name="Synthetic benchmark supplier", supplier_type="company")
        purch = Purchasing(self.db, self.s)
        invoice = purch.create_manual(self.ctx, supplier_id=company)
        with self.db.transaction():
            self.db.execute(
                "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<29999)"
                " INSERT INTO items (id, org_id, name_en, name_norm, dedupe_key, public_price, is_drug)"
                " SELECT 'pur-bench-'||x, ?, 'Synthetic bench '||x, 'synthetic bench '||x, 'pur-bench-'||x, 4000, 0"
                " FROM n", (self.ctx.org_id,))
            for n in range(1, 22):
                catalog.add_barcode(self.db, self.ctx, f"pur-bench-{n}", str(9880000000000 + n))
        self.assertEqual(self.db.one("SELECT count(*) n FROM items WHERE org_id=?", (self.ctx.org_id,))["n"], 30_000)
        samples = run_node(self, BENCHMARK, "owner", OWNER_PW, invoice)
        self.assertEqual(len(purch.get(self.ctx, invoice)["lines"]), 21)
        p95 = sorted(samples)[18]
        print(f"Purchase add-line at 30,000 items, 20 runs: median {statistics.median(samples):.1f} ms, "
              f"p95 {p95:.1f} ms, max {max(samples):.1f} ms")
        self.assertLess(p95, 300)


class TestWarehouseBonusHidden(APICase):
    """Independent review of #463 (P1): the screen hides bonus for a warehouse supplier, but a bonus already on the
    line (supplier switched from company, or an imported invoice) would still post to stock and spread the cost.
    Approval must stop with an Arabic reason until it is cleared."""

    def test_stored_bonus_on_a_warehouse_invoice_blocks_approval(self):
        from app.core.errors import Conflict
        p = Purchasing(self.db, self.s)
        company = catalog.create_supplier(self.db, self.ctx, name="Synthetic Co B", supplier_type="company")
        warehouse = catalog.create_supplier(self.db, self.ctx, name="Synthetic Store B", supplier_type="warehouse")
        sid = p.create_manual(self.ctx, supplier_id=company)
        p.update_header(self.ctx, sid, {"invoice_no": "SYN-WH-BONUS"}, None)
        p.add_line(self.ctx, sid, {"item_id": self.pan, "qty": 2, "bonus_qty": 3, "public_price": 4000,
                                   "discount_bp": 2500, "expiry_date": EXPIRY}, None)
        p.update_header(self.ctx, sid, {"supplier_id": warehouse}, None)
        got = p.get(self.ctx, sid)
        self.assertEqual(got["invoice"]["supplier_type"], "warehouse")
        codes = [i["code"] for i in got["issues"] if i["level"] == "error"]
        self.assertIn("warehouse_bonus", codes)
        self.assertFalse(got["can_approve"])
        with self.assertRaises(Conflict):
            p.approve(self.ctx, sid, got["invoice"]["version"])
        moved = self.db.one("SELECT count(*) c FROM stock_movements WHERE ref_id=?", (sid,))["c"]
        self.assertEqual(moved, 0)
