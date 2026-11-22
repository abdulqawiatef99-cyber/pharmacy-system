"""Manager 22/11: «ليه رصيد 3 ج» on the payment of a customer who left his change, one click uses it (never more than
his balance), F10 saves, and the drawer expects only the cash he really paid. Real Chromium."""
from datetime import date, timedelta
from decimal import Decimal

from app.catalog import service as cat
from app.db import new_id
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
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posQ');
  await p.locator('#posQ').focus();
  await p.keyboard.type(code, { delay: 5 }); await p.keyboard.press('Enter');
  await p.waitForFunction(() => document.querySelectorAll('table.cart [data-minus]').length === 1);
  await p.locator('#posCust').fill('عميل الرصيد');
  await p.waitForSelector('#posCustRes [data-cust]');
  await p.locator('#posCustRes [data-cust]').first().click();
  await p.waitForSelector('#useCredit');
  out.button = await p.locator('#useCredit').innerText();
  await p.locator('#useCredit').click();
  await p.waitForSelector('[data-pa]');
  out.parts = await p.$$eval('[data-pm]', (s) => s.map((x, i) => [x.value, document.querySelector(`[data-pa="${i}"]`).value]));
  const [res] = await Promise.all([p.waitForResponse((r) => r.url().endsWith('/complete')),
    (async () => { await p.keyboard.press('F10'); await p.waitForTimeout(300); await p.keyboard.press('F10'); })()]);
  out.status = res.status();
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestUseCreditInOneClick(APICase):
    def test_balance_shown_used_capped_and_the_drawer_counts_only_cash(self):
        it = cat.create_item(self.db, self.ctx, {"name_en": "Credit Item", "public_price": 2000})
        cat.add_barcode(self.db, self.ctx, it, "6221043010373")
        cust = new_id()
        with self.db.transaction():
            bt = inv.get_or_create_batch(self.db, self.ctx, it, "B1", (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=5, batch_id=bt, movement_type="purchase",
                              unit_cost=Decimal("5"))
            self.db.execute("INSERT INTO customers (id, org_id, name, credit_limit) VALUES (?,?,?,0)",
                            (cust, self.ctx.org_id, "عميل الرصيد"))
            self.db.execute("INSERT INTO customer_ledger (id, org_id, customer_id, amount, kind, note) VALUES"
                            " (?,?,?,-300,'payment','باقي اتساب رصيد')", (new_id(), self.ctx.org_id, cust))
        cd = CashDrawer(self.db)
        cd.open(self.ctx, 0)
        out = run_node(self, SCRIPT, "owner", OWNER_PW, "6221043010373")
        self.assertIn("ليه رصيد 3.00", out["button"])
        self.assertEqual(out["parts"], [["credit", "3.00"], ["cash", "17.00"]])      # his 3, never more
        self.assertEqual(out["status"], 200)
        self.assertEqual(cd.summary(self.ctx)["expected_cash"], 1700)               # only the cash he paid
        bal = self.db.one("SELECT sum(amount) b FROM customer_ledger WHERE customer_id=?", (cust,))["b"]
        self.assertEqual(bal, 0)
