"""#461 on the real screen: the invoices report lists one page, its totals are the whole period's, Next and
Previous move through stable pages, and the Excel file gets every invoice. Synthetic data only."""
from unittest import mock

from app.catalog import service as catalog
from app.inventory import service as inventory
from app.reports import invoices
from app.sales.cash import CashDrawer
from app.sales.service import Sales
from decimal import Decimal
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import APICase

SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.goto(base + '/#/sales');
  await p.waitForSelector('#iTable tbody tr[data-i]');
  const snap = async () => ({ rows: await p.locator('#iTable tbody tr[data-i]').count(),
    numbers: await p.locator('#iTable tbody tr[data-i] td:nth-child(2) b').allInnerTexts(),
    count: await p.locator('#iBox .totals > div').first().locator('b').innerText(),
    foot: await p.locator('#iTable tfoot').innerText(),
    badge: await p.locator('#iBox .badge.b-info').first().innerText(),
    prev: await p.locator('#iPrev').isDisabled(), next: await p.locator('#iNext').isDisabled() });
  const pages = [await snap()];
  for (let k = 0; k < 2; k++) {
    const before = pages[pages.length - 1].numbers.join();
    await p.click('#iNext');
    await p.waitForFunction(b => { const x = [...document.querySelectorAll('#iTable tbody tr[data-i] td:nth-child(2) b')].map(e => e.innerText).join(); return x && x !== b; }, before);
    pages.push(await snap());
  }
  // the screen moved by cursors, never by position
  const moves = [];
  p.on('request', r => { const u = new URL(r.url()); if (u.pathname === '/api/reports/invoices') moves.push([u.searchParams.get('after'), u.searchParams.get('offset')]); });
  // the Excel button: a fresh answer, every page fetched (keyset, pinned moment), and a file comes out
  const fetched = [];
  p.on('response', async r => {
    const u = new URL(r.url());
    if (u.pathname === '/api/reports/invoices')
      fetched.push({ after: u.searchParams.get('after'), totals: u.searchParams.get('totals'), rows: (await r.json()).rows.map(x => String(x.sale_no)) });
  });
  const download = p.waitForEvent('download');
  await p.click('#iXl');
  const name = (await download).suggestedFilename();
  const file = { name, fetched: fetched.slice(), moves };
  await p.click('#iPrev');
  await p.waitForFunction(b => [...document.querySelectorAll('#iTable tbody tr[data-i] td:nth-child(2) b')].map(e => e.innerText).join() === b, pages[1].numbers.join());
  console.log(JSON.stringify({ pages, file, back: await snap() }));
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestInvoicePagesOnScreen(APICase):
    def test_pages_totals_and_export(self):
        item = catalog.create_item(self.db, self.ctx, {"name_en": "Pager 10 tab", "pack_size": 1, "public_price": 1000})
        with self.db.transaction():
            inventory.post_movement(self.db, self.ctx, item_id=item, qty_delta=50, movement_type="opening",
                                    unit_cost=Decimal("5"))
        CashDrawer(self.db).open(self.ctx, 0)
        s = Sales(self.db)
        numbers = []
        for _ in range(5):                                      # 5 invoices of 10.00
            sid = s.create(self.ctx)
            d = s.add_line(self.ctx, sid, item, units=1)
            f = s.safety_check(self.ctx, sid)
            numbers.append(str(s.complete(self.ctx, sid, paid_cash=d["total"],
                                          safety_decisions={i: "ok" for i in range(len(f))})["sale_no"]))
        with mock.patch.object(invoices, "MAX_ROWS", 2):
            out = run_node(self, SCRIPT, "owner", OWNER_PW)
        pages = out["pages"]
        self.assertEqual([p["rows"] for p in pages], [2, 2, 1])
        self.assertEqual(sum((p["numbers"] for p in pages), []), numbers[::-1])     # newest first, each once
        for p in pages:
            self.assertEqual(p["count"], "5")                   # the totals are the period's on every page
            self.assertIn("50.00", p["foot"])
        self.assertIn("5", pages[0]["badge"])
        self.assertEqual([(p["prev"], p["next"]) for p in pages], [(True, False), (False, False), (False, True)])
        got = out["file"]["fetched"]
        self.assertEqual(sum((x["rows"] for x in got), []), numbers[::-1])          # every invoice, once, for the file
        self.assertEqual([bool(x["after"]) for x in got], [False, True, True])
        self.assertEqual([x["totals"] for x in got], [None, "0", "0"])              # totals asked once, with the rows
        self.assertTrue(all(offset is None for _, offset in out["file"]["moves"]))  # pages are cut by cursor
        self.assertTrue(out["file"]["name"].endswith(".xlsx"))
        self.assertEqual(out["back"]["numbers"], pages[1]["numbers"])
