"""F105/F106/F108 screens in a real browser: the accounts list with balances, a transfer, an account count with
its required reason, the supplier payment with an early-payment discount, the reconciliation row, and the
cashier's grouped payment methods. Synthetic data only."""
import unittest

from app.catalog import service as cat
from app.finance import money
from app.sales import channels
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import run_node
from tests.test_api import APICase

SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, sup] = process.argv.slice(1);
  const b = await chromium.launch();
  const p = await b.newPage();
  p.on('dialog', async (d) => { await d.accept(); });
  const errs = [];
  p.on('pageerror', (e) => errs.push(String(e)));
  await p.goto(base + '/');
  await p.waitForSelector('#loginForm');
  await p.evaluate(async ([u, w]) => fetch('/api/auth/login', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'pharm' }, body: JSON.stringify({ username: u, password: w }) }), [user, pw]);
  const out = {};
  await p.goto(base + '/#/money');
  await p.waitForSelector('#mTr');
  out.rows = await p.$$eval('#app table tbody tr', (trs) => trs.map((t) => t.children[0].innerText.trim()));
  await p.selectOption('#mTr select[name=from]', { index: 1 });         // the treasury
  const insta = await p.$eval('#mTr select[name=to]', (s) => [...s.options].find((o) => o.text.includes('إنستاباي')).value);
  await p.selectOption('#mTr select[name=to]', insta);
  await p.fill('#mTr input[name=a]', '100');
  await p.click('#mTr button');
  await p.waitForFunction(() => [...document.querySelectorAll('#app table tbody tr')].some((t) => t.innerText.includes('إنستاباي') && t.innerText.includes('100.00')));
  // count InstaPay: a difference without a reason is refused, with a reason it is saved
  await p.click('[data-count][data-name="إنستاباي"]');
  await p.fill('#mCountF input[name=c]', '99');
  const [refusal] = await Promise.all([p.waitForResponse((r) => r.url().includes('/count')), p.click('#mCountF button')]);
  out.refused = [refusal.status(), await p.locator('#mCountF').count()];
  await p.fill('#mCountF input[name=r]', 'رسوم تحويل');
  await p.click('#mCountF button');
  await p.waitForFunction(() => [...document.querySelectorAll('#app table tbody tr')].some((t) => t.innerText.includes('إنستاباي') && t.innerText.includes('99.00')));
  await p.waitForSelector('#mSumBox h3');
  out.summary = await p.locator('#mSumBox').innerText();
  // supplier: pay 196 from the treasury with a 2% early-payment discount on 200
  await p.goto(base + '/#/suppliers/' + sup);
  await p.waitForSelector('#supPay');
  await p.selectOption('#supPay select[name=dm]', 'pct');
  await p.fill('#supPay input[name=a]', '200');
  await p.fill('#supPay input[name=d]', '2');
  out.hint = await p.locator('#supPayHint').innerText();
  const [paid] = await Promise.all([p.waitForResponse((r) => r.url().endsWith('/pay')), p.click('#supPay button:not([type=button])')]);
  out.paid = paid.status();
  await p.waitForFunction(() => document.body.innerText.includes('خصم تعجيل دفع'));   // the account re-drawn
  // reconciliation row
  await p.goto(base + '/#/reconcile');
  await p.waitForSelector('#rcBox [data-save]', { timeout: 8000 }).catch(async () => { console.error(JSON.stringify(errs), await p.locator('#app').innerText()); throw new Error('no rows'); });
  const row = p.locator('#rcBox tr', { hasText: 'مورد شاشة' });
  await row.locator('[data-st]').fill('500');
  await row.locator('[data-type]').selectOption('opening');
  await row.locator('[data-save]').click();
  await p.waitForFunction(() => [...document.querySelectorAll('#rcBox tr')].some((t) => t.innerText.includes('مورد شاشة') && t.children[1].innerText.trim() === '500.00'));
  // cashier: groups first, then the method
  await p.goto(base + '/#/pos');
  await p.waitForSelector('.paym');
  out.methods = await p.$$eval('.paym button', (bs) => bs.map((x) => x.innerText.trim()));
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class MoneyScreens(APICase):
    def test_accounts_transfer_count_supplier_discount_reconcile_and_cashier_groups(self):
        channels.ensure_defaults(self.db)
        t = money.treasury_id(self.db, self.ctx)
        money.create_account(self.db, self.ctx, "إنستاباي", "bank")
        money.owner_movement(self.db, self.ctx, t, 100000, "owner_deposit")
        sup = cat.create_supplier(self.db, self.ctx, name="مورد شاشة")
        with self.db.transaction():
            self.db.execute("INSERT INTO supplier_ledger (id, org_id, supplier_id, amount, kind) VALUES ('o', ?, ?, 30000, 'opening')",
                            (self.ctx.org_id, sup))
        out = run_node(self, SCRIPT, "owner", OWNER_PW, sup)
        self.assertEqual(out["rows"][:2], ["درج الشيفت", "الخزنة الرئيسية"])
        self.assertEqual(out["refused"], [422, 1])             # refused, still on the count form: the reason was missing
        self.assertIn("الفلوس", out["summary"] + "الفلوس")
        self.assertIn("196.00", out["hint"])
        self.assertEqual(out["paid"], 200)
        self.assertEqual(money.balance(self.db, self.ctx.org_id, t), 100000 - 10000 - 19600)
        from app.procurement import suppliers as sp
        self.assertEqual(sp.balance(self.db, self.ctx.org_id, sup), 500 * 100)
        self.assertEqual(out["methods"], ["نقدي", "فيزا", "محفظة إلكترونية", "آجل"])


if __name__ == "__main__":
    unittest.main()
