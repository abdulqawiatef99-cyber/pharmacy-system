"""An invoice opened from a customer's page ("فاتورة له", or his monthly order) kept the payment choice
of the invoice that was on the sale screen before: «آجل» chosen for customer A stayed chosen on the new
invoice of customer B, so a cash sale to B could be completed as a debt on B's account. Every other way of
opening an invoice (a tab, a new invoice, after a sale) starts at cash. Synthetic data only."""
from datetime import date, timedelta
from decimal import Decimal

from app.inventory import service as inv
from app.sales.service import Sales
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import APICase

SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, other] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posCust');
  // invoice 1: customer A, paid on his account, left open
  await p.locator('#posCust').fill('عميل وهمي أول');
  await p.locator('#posCustRes [data-cust]').first().click();
  await p.waitForSelector('#custClear');
  await p.locator('.paym [data-m="credit"]').click();
  await p.waitForSelector('.paym [data-m="credit"].on');
  await p.locator('#splitOn').click();
  await p.waitForSelector('#partAdd');
  await p.locator('#splitOff').click();
  await p.waitForSelector('.paym [data-m="credit"].on');
  // invoice 2: opened from customer B's page
  await p.goto(base + '/#/customers/' + other);
  await p.waitForSelector('#cuSale');
  await p.locator('#cuSale').click();
  await p.waitForSelector('#custClear');
  const out = await p.evaluate(() => ({ customer: document.querySelector('#posCust').value,
    method: document.querySelector('.paym button.on')?.dataset.m || null, tabs: document.querySelectorAll('.tab[data-tab]').length }));
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class StalePaymentMethod(APICase):
    def setUp(self):
        super().setUp()
        sales = Sales(self.db)
        sales.create_customer(self.ctx, name="عميل وهمي أول", phone="01000000011", credit_limit=50000)
        self.other = sales.create_customer(self.ctx, name="عميل وهمي ثان", phone="01000000012", credit_limit=50000)
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.pan, "B1", (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=20, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("5"))

    def test_invoice_opened_from_a_customer_page_starts_at_cash(self):
        out = run_node(self, SCRIPT, "owner", OWNER_PW, self.other)
        self.assertEqual(out["customer"], "عميل وهمي ثان")
        self.assertEqual(out["tabs"], 2)                       # the first invoice is still open, untouched
        self.assertEqual(out["method"], "cash")                # was "credit": the choice made for the other customer
