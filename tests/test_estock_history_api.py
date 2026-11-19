"""E-Stock history inside the program (owner 18/11: old invoices usable inside the program, not converted): the
routes over estock_history (#354) and the old-invoice return (#346). Every row says it is E-Stock history.
Synthetic data only."""
from dataclasses import replace

from starlette.testclient import TestClient

from app import config
from app.api.app import create_app
from app.core import people
from app.purchasing.service import Purchasing
from tests.helpers import OWNER_PW
from tests.test_estock_returns import _Base

H = {"X-Requested-With": "pharm"}
PW = "Staff-Pass-2026"


class ApiBase(_Base):
    def setUp(self):
        super().setUp()
        self.s = replace(config.load(), upload_dir=self.tmp / "up", anthropic_api_key=None, data_dir=self.tmp,
                         backup_dir=self.tmp / "backups")
        self.app = create_app(self.db, self.s, Purchasing(self.db, self.s))
        self.c = TestClient(self.app)
        self.login_as("owner", OWNER_PW)
        self.cust = self.db.one("SELECT id FROM customers WHERE name='أحمد تجربة'")["id"]
        self.sup = self.db.one("SELECT id FROM suppliers WHERE name='مورد تجربة'")["id"]

    def login_as(self, user, pw):
        self.c.cookies.clear()
        r = self.c.post("/api/auth/login", json={"username": user, "password": pw}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)

    def get(self, url, expected=200):
        r = self.c.get(url, headers=H)
        self.assertEqual(r.status_code, expected, r.text)
        return r.json()

    def post(self, url, body, key, expected=201):
        r = self.c.post(url, json=body, headers={**H, "Idempotency-Key": key})
        self.assertEqual(r.status_code, expected, r.text)
        return r.json()


class TestHistoryRoutes(ApiBase):
    def test_search_open_and_every_row_is_marked(self):
        self.assertTrue(self.get("/api/estock/available")["available"])
        rows = self.get("/api/estock/invoices?kind=sale")["invoices"]
        self.assertTrue(rows and all(r["source"] == "estock" for r in rows))
        one = self.get(f"/api/estock/invoices/sale/{rows[-1]['estock_id']}")
        self.assertEqual(one["source"], "estock")
        self.assertTrue(all(l["source"] == "estock" for l in one["lines"]))
        by_customer = self.get(f"/api/estock/invoices?kind=sale&party={self.cust}")["invoices"]
        self.assertTrue(by_customer)
        item = self.item("100")["id"]
        self.assertTrue(self.get(f"/api/estock/invoices?kind=sale&item={item}")["invoices"])
        self.assertTrue(self.get(f"/api/estock/items/{item}/history")["history"])
        self.assertTrue(self.get(f"/api/estock/customers/{self.cust}/items")["items"])
        st = self.get(f"/api/estock/statement/supplier/{self.sup}")
        self.assertEqual(st["source"], "estock")
        self.get("/api/estock/invoices?kind=bogus", 422)

    def test_purchases_need_the_purchase_permission(self):
        people.add_user(self.db, self.ctx, username="cashier1", full_name="C", password=PW, role_code="assistant")
        self.login_as("cashier1", PW)
        self.get("/api/estock/invoices?kind=sale", 403)              # sales.totals: owner / manager
        self.get("/api/estock/invoices?kind=purchase")               # assistants read purchases


class TestOldInvoiceReturnRoute(ApiBase):
    def test_return_through_the_route_once_per_key(self):
        lines = {l["line_key"]: l for l in self.get("/api/estock/sales/1/return-lines")["lines"]}
        self.assertEqual(lines["2"]["sold_units"], 7)
        r = self.post("/api/estock/sales/1/return", {"lines": {"2": 7}, "reason": "مرتجع عميل"}, key="ret-1")
        self.assertEqual((r["refund"], r["parts"]), (3500, {"account": 3000, "cash": 500, "card": 0}))
        again = self.c.post("/api/estock/sales/1/return", json={"lines": {"2": 7}, "reason": "مرتجع عميل"},
                            headers={**H, "Idempotency-Key": "ret-1"})
        self.assertEqual(again.json()["refund"], 3500)            # a retry replays, never a second refund
        self.assertEqual(self.ledger(), [-3000])
        self.assertEqual(self.cash(), [-500])

    def test_more_than_was_sold_or_no_key_is_refused(self):
        self.post("/api/estock/sales/1/return", {"lines": {"2": 8}, "reason": "مرتجع عميل"}, key="ret-2", expected=409)
        r = self.c.post("/api/estock/sales/1/return", json={"lines": {"2": 1}, "reason": "مرتجع عميل"}, headers=H)
        self.assertEqual(r.status_code, 422)
        self.post("/api/estock/sales/1/return", {"lines": {}, "reason": "مرتجع عميل"}, key="ret-3", expected=422)


SCREENS = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, cust, item] = process.argv.slice(1);
  const b = await chromium.launch();
  const p = await b.newPage();
  const errs = [];
  p.on('pageerror', (e) => errs.push(String(e)));
  await p.goto(base + '/');
  await p.waitForSelector('#loginForm');
  await p.evaluate(async ([u, w]) => fetch('/api/auth/login', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'pharm' }, body: JSON.stringify({ username: u, password: w }) }), [user, pw]);
  const out = {};
  await p.goto(base + '/#/sales');
  await p.waitForSelector('#esPanel a');
  await p.click('#esPanel a');
  await p.waitForSelector('#esBox [data-go]');
  out.rows = await p.$$eval('#esBox [data-go]', (trs) => trs.map((t) => t.innerText.includes('من إي ستوك')));
  await p.click('#esBox [data-go="1"]');
  await p.waitForSelector('#esRet');
  out.title = await p.locator('h1').innerText();
  await p.click('#esRet');
  await p.waitForSelector('#esRetF [data-line="2"]');
  await p.fill('#esRetF [data-line="2"]', '7');
  await p.fill('#esRetF input[name=reason]', 'مرتجع عميل');
  const [res] = await Promise.all([p.waitForResponse((r) => r.url().endsWith('/return')), p.click('#esRetF button:not([type=button])')]);
  out.ret = [res.status(), (await res.json()).refund];
  await p.goto(base + '/#/customers/' + cust);
  await p.waitForSelector('#esPanel a');
  out.custLinks = await p.$$eval('#esPanel a', (as) => as.length);
  await p.goto(base + '/#/estock-item/' + item);
  await p.waitForSelector('#app table tbody tr');
  out.itemRows = await p.$$eval('#app table tbody tr', (trs) => trs.length);
  out.errs = errs;
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""

from tests.helpers import needs_browser  # noqa: E402
from tests.pos_browser import run_node  # noqa: E402


@needs_browser
class TestHistoryScreens(ApiBase):
    def test_search_open_return_and_the_panels(self):
        out = run_node(self, SCREENS, "owner", OWNER_PW, self.cust, self.item("100")["id"])
        self.assertTrue(out["rows"] and all(out["rows"]))            # every row says it is E-Stock history
        self.assertIn("فاتورة قديمة", out["title"])
        self.assertEqual(out["ret"], [201, 3500])
        self.assertEqual(self.ledger(), [-3000])
        self.assertGreaterEqual(out["custLinks"], 2)
        self.assertGreater(out["itemRows"], 0)
        self.assertEqual(out["errs"], [])
