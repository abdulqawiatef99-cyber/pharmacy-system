"""When the server refused to complete a sale because the invoice had changed (a contract discount, a batch
price), the sale screen reloaded the lines but left the side panel as it was: «المطلوب» and the change due
still showed the old total, and the next F9 completed the sale at the new one. A refusal for a total that no
longer matched (`payment_mismatch`) reloaded nothing, so every F9 failed on a screen that looked right.
Synthetic data only."""
import time
from datetime import date, timedelta
from decimal import Decimal

from app.inventory import service as inv
from app.sales import contracts
from app.sales.cash import CashDrawer
from app.sales.service import Sales
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import APICase

SCRIPT = r"""
const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const [base, user, pw, dir] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posDone:not([disabled])');
  const due = () => p.evaluate(() => document.querySelector('#posSide .sum.due b').textContent.trim());
  const shown = await due();
  // the invoice changes behind the screen (the test does it, as another screen or the owner would)
  fs.writeFileSync(dir + '/ready', '1');
  for (let i = 0; i < 200 && !fs.existsSync(dir + '/go'); i++) await p.waitForTimeout(50);
  await p.keyboard.press('F9');
  await p.waitForSelector('#toast.error:not([hidden])', { timeout: 10000 });
  await p.waitForTimeout(700);
  const after = await due();
  // the second F9 sells at the total now on the screen
  await p.keyboard.press('F9');
  await p.waitForSelector('#posDone[disabled]', { timeout: 5000 }).catch(() => {});      // sold: a new, empty invoice
  console.log(JSON.stringify({ shown, after, toast: await p.evaluate(() => document.querySelector('#toast').textContent) }));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TotalAfterRefusal(APICase):
    def setUp(self):
        super().setUp()
        self.sales = Sales(self.db)
        with self.db.transaction():
            self.batch = inv.get_or_create_batch(self.db, self.ctx, self.pan, "B1",
                                                 (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=20, batch_id=self.batch,
                              movement_type="purchase", unit_cost=Decimal("5"))
        CashDrawer(self.db).open(self.ctx, 0)

    def run_script(self, change):
        """Runs the screen; `change` runs once the sale screen shows the invoice, before the first F9."""
        flags = self.tmp / "flags"
        flags.mkdir()

        def behind_the_screen():
            for _ in range(600):
                if (flags / "ready").exists():
                    change()
                    (flags / "go").write_text("1")
                    return
                time.sleep(0.05)
        return run_node(self, SCRIPT, "owner", OWNER_PW, str(flags), behind=behind_the_screen)

    def sold(self):
        r = self.db.one("SELECT total FROM sales WHERE org_id=? AND status='completed'", (self.ctx.org_id,))
        return r["total"] if r else None

    def test_contract_stopped_the_amount_due_on_screen_is_the_new_total(self):
        kid = contracts.save(self.db, self.ctx, {"name": "نقابة وهمية", "discount_local_bp": 1000,
                                                 "discount_imported_bp": 1000})[0]["id"]
        cid = self.sales.create_customer(self.ctx, name="عميل تعاقد وهمي", phone="01000000021")
        contracts.link_customer(self.db, self.ctx, cid, kid)
        sid = self.sales.create(self.ctx, cid)
        self.sales.add_line(self.ctx, sid, self.pan, packs=1)
        out = self.run_script(lambda: contracts.save(self.db, self.ctx, {"is_active": False}, kid))
        self.assertEqual(out["shown"], "36.00")                 # 40.00 less the contract's 10%
        self.assertEqual(out["after"], "40.00", out)            # was still 36.00 after the refusal
        self.assertEqual(self.sold(), 4000)

    def test_batch_price_moved_the_screen_shows_the_new_total_and_sells(self):
        sid = self.sales.create(self.ctx)
        self.sales.add_line(self.ctx, sid, self.pan, packs=1)

        def reprice():                       # as a purchase approved on another screen would
            with self.db.transaction():
                self.db.execute("UPDATE batches SET public_price=4400 WHERE org_id=? AND id=?",
                                (self.ctx.org_id, self.batch))
        out = self.run_script(reprice)
        self.assertEqual(out["shown"], "40.00")
        self.assertEqual(out["after"], "44.00", out)            # was 40.00, and every F9 was refused
        self.assertEqual(self.sold(), 4400, out)
