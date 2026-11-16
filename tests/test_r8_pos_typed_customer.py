"""The customer box on the sale screen is a search field. With customer A already on the invoice and «آجل»
chosen, typing customer B's name and pressing F9 without choosing him from the list completed the sale as a
debt on A's account while the screen showed B's name. The sale now waits until the box and the invoice say
the same customer. Synthetic data only."""
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
  const [base, user, pw] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posDone:not([disabled])');
  await p.locator('.paym [data-m="credit"]').click();
  await p.waitForSelector('.paym [data-m="credit"].on');
  // the cashier types the right customer over the wrong one and presses F9 without choosing him
  await p.locator('#posCust').fill('عميل وهمي ثان');
  await p.waitForSelector('#posCustRes [data-cust]');
  await p.keyboard.press('F9');
  await p.waitForTimeout(1200);
  const first = await p.evaluate(() => ({ toast: document.querySelector('#toast').textContent,
    error: document.querySelector('#toast').className === 'error', box: document.querySelector('#posCust')?.value }));
  // choosing him from the list puts him on the invoice; then the sale goes through
  const pick = p.locator('#posCustRes [data-cust]').first();
  if (await pick.count()) {
    await pick.click();
    await p.waitForFunction(() => document.querySelector('#posCust').value === 'عميل وهمي ثان' && !document.querySelector('#posCustRes [data-cust]'));
    await p.keyboard.press('F9');
    await p.waitForSelector('#posDone[disabled]', { timeout: 5000 }).catch(() => {});
  }
  console.log(JSON.stringify(first));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TypedCustomer(APICase):
    def setUp(self):
        super().setUp()
        self.sales = Sales(self.db)
        self.a = self.sales.create_customer(self.ctx, name="عميل وهمي أول", phone="01000000031", credit_limit=50000)
        self.b = self.sales.create_customer(self.ctx, name="عميل وهمي ثان", phone="01000000032", credit_limit=50000)
        with self.db.transaction():
            batch = inv.get_or_create_batch(self.db, self.ctx, self.pan, "B1", (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=20, batch_id=batch, movement_type="purchase",
                              unit_cost=Decimal("5"))
        sid = self.sales.create(self.ctx, self.a)
        self.sales.add_line(self.ctx, sid, self.pan, packs=1)

    def test_a_name_typed_and_not_chosen_does_not_charge_the_customer_chosen_before(self):
        first = run_node(self, SCRIPT, "owner", OWNER_PW)
        self.assertTrue(first["error"], first)
        self.assertIn("عميل وهمي أول", first["toast"])           # says whose invoice it still is
        self.assertEqual(first["box"], "عميل وهمي ثان")          # what was typed is kept
        self.assertEqual(self.sales.balance(self.ctx, self.a), 0)      # was 4000: the debt went to the wrong customer
        self.assertEqual(self.sales.balance(self.ctx, self.b), 4000)
