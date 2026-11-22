"""Update 3 new screens open in the browser with no script error (signed in as the owner, English and Arabic)."""
import json

from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import APICase

SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, routes] = process.argv.slice(1);
""" + LOGIN + r"""
  const errors = [];
  p.on('pageerror', (e) => errors.push(String(e)));
  const out = {};
  for (const [hash, sel] of JSON.parse(routes)) {
    await p.goto(base + '/' + hash);
    try { await p.waitForSelector(sel, { timeout: 8000 }); out[hash] = true; } catch { out[hash] = false; }
  }
  out.errors = errors;
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestUpdate3Screens(APICase):
    ROUTES = []

    def test_screens_open(self):
        out = run_node(self, SCRIPT, "owner", OWNER_PW, json.dumps(self.routes()))
        self.assertEqual(out.pop("errors"), [])
        self.assertTrue(all(out.values()), out)

    def routes(self):
        return self.ROUTES

    def setUp(self):
        super().setUp()
        from app.db import new_id
        self.cust = new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO customers (id, org_id, name, credit_limit) VALUES (?,?,?,?)",
                            (self.cust, self.ctx.org_id, "عميل تجربة", 0))

    def routes(self):
        return [[f"#/customers/{self.cust}", "#cUsual"]]


POS_USUAL = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posCust');
  await p.locator('#posCust').fill('عميل');
  await p.waitForSelector('#posCustRes [data-cust]');
  await p.locator('#posCustRes [data-cust]').first().click();
  await p.waitForSelector('#posUsual [data-usual]', { timeout: 10000 });
  const labels = await p.$$eval('#posUsual [data-usual]', (x) => x.map((b) => b.textContent.replace(/\s+/g, ' ').trim()));
  for (const i of [0, 1]) {
    await p.locator(`#posUsual [data-usual="${i}"]`).click();
    await p.waitForFunction((n) => document.querySelectorAll('table.cart [data-minus]').length >= n, i + 1, { timeout: 10000 });
  }
  await p.waitForTimeout(300);
  console.log(JSON.stringify({ labels }));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestPosUsualClick(APICase):
    """Codex P1 on #419: the POS usual-item button adds his usual quantity, a whole box and loose units."""
    def test_click_adds_the_usual_quantity(self):
        from datetime import timedelta
        from decimal import Decimal
        from app.catalog import service as cat
        from app.inventory import service as inv
        from app.sales.cash import CashDrawer
        from app.sales.service import Sales, local_today
        box = cat.create_item(self.db, self.ctx, {"name_en": "Usual Box 3 strips", "pack_size": 3, "public_price": 3000})
        loose = cat.create_item(self.db, self.ctx, {"name_en": "Usual Loose 10 tab", "pack_size": 10, "public_price": 1000})
        with self.db.transaction():
            for it in (box, loose):
                b = inv.get_or_create_batch(self.db, self.ctx, it, "U1", (local_today() + timedelta(days=500)).isoformat())
                inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=60, batch_id=b, movement_type="purchase",
                                  unit_cost=Decimal("1"))
        cust = Sales(self.db).create_customer(self.ctx, name="عميل الأدوية", credit_limit=0)
        CashDrawer(self.db).open(self.ctx, 0)
        s = Sales(self.db)
        for it, units in ((box, 3), (box, 3), (loose, 4)):           # box: twice (first), loose: 4 units
            sid = s.create(self.ctx, cust)
            d = s.add_line(self.ctx, sid, it, units=units)
            f = s.safety_check(self.ctx, sid)
            s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions={i: "ok" for i in range(len(f))})
        out = run_node(self, POS_USUAL, "owner", OWNER_PW)
        self.assertEqual(len(out["labels"]), 2)
        lines = self.db.all("SELECT i.name_en, l.qty_base FROM sales s JOIN sale_lines l ON l.sale_id=s.id"
                            " JOIN items i ON i.id=l.item_id WHERE s.status='draft' AND s.customer_id=?", (cust,))
        self.assertEqual(sorted((r["name_en"], r["qty_base"]) for r in lines),
                         [("Usual Box 3 strips", 3), ("Usual Loose 10 tab", 4)])   # one box, 4 loose units
