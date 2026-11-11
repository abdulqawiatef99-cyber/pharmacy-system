"""#114 on the invoice screen's return form: the refund method defaults to how the sale was paid (a credit
sale goes back to the customer's account, not cash from the drawer), and a double click on «تسجيل المرتجع»
records one return, not two."""
import json
import re
import shutil
import socket
import subprocess
import threading
import time
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app.catalog import service as cat
from app.inventory import service as inv
from app.sales.service import Sales
from tests.helpers import OWNER_PW, needs_browser
from tests.test_api import APICase

JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")
FN = re.search(r"\nfunction refundDefault\(.*?\n\}\n", JS, re.S).group(0)

RULE = FN + r"""
const s = (paid_cash, paid_card, on_credit, customer_id = "c1", paid_transfer = 0) =>
  ({ paid_cash, paid_card, on_credit, customer_id, paid_transfer });
console.log(JSON.stringify([
  refundDefault(s(0, 0, 5000)),            // all on credit -> the customer's account
  refundDefault(s(0, 2000, 3000)),         // card + credit, no cash -> account
  refundDefault(s(2000, 0, 3000)),         // cash + credit -> cash (the cashier splits by hand if needed)
  refundDefault(s(0, 5000, 0)),            // card only -> card
  refundDefault(s(5000, 0, 0)),            // cash -> cash
  refundDefault(s(0, 0, 5000, null)),      // no customer (cannot happen for credit): never an option it lacks
  refundDefault(s(0, 2000, 0, null, 3000)) // card + transfer -> cash, as before
]));
"""


@unittest.skipUnless(shutil.which("node"), "Node not available")
class TestRefundDefault(unittest.TestCase):
    def test_default_follows_the_payment(self):
        r = subprocess.run([shutil.which("node"), "-e", RULE], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), ["credit", "credit", "cash", "card", "cash", "cash", "cash"])


SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, sid] = process.argv.slice(1);
  const b = await chromium.launch();
  const p = await b.newPage();
  let posts = 0;
  p.on('request', (r) => { if (r.method() === 'POST' && r.url().endsWith('/return')) posts++; });
  await p.goto(base + '/');
  await p.waitForSelector('#loginForm');
  await p.evaluate(async ([u, w]) => fetch('/api/auth/login', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'pharm' }, body: JSON.stringify({ username: u, password: w }) }), [user, pw]);
  await p.goto(base + '/#/sales/' + sid);
  await p.waitForSelector('#rMethod');
  const out = { method: await p.locator('#rMethod').inputValue() };
  await p.locator('[data-ret]').first().fill('1');
  await p.locator('#rReason').fill('العميل غيّر رأيه');
  await p.locator('#rGo').dblclick();
  await p.waitForTimeout(1500);
  out.posts = posts;
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestReturnFormInBrowser(APICase):
    def test_credit_sale_defaults_to_account_and_double_click_posts_once(self):
        import uvicorn
        it = cat.create_item(self.db, self.ctx, {"name_en": "Test Strip", "pack_size": 3, "public_price": 3000})
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, it, "B1", (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=30, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("5"))
        s = Sales(self.db)
        cust = s.create_customer(self.ctx, name="عميل آجل", credit_limit=100000)
        sid = s.create(self.ctx, customer_id=cust)
        d = s.add_line(self.ctx, sid, it, units=3)
        s.complete(self.ctx, sid, on_credit=d["total"])
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=port, log_level="error"))
        t = threading.Thread(target=server.run, daemon=True)
        t.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            r = subprocess.run([shutil.which("node"), "-e", SCRIPT, f"http://127.0.0.1:{port}", "owner", OWNER_PW, sid],
                               capture_output=True, text=True, timeout=120, cwd=str(Path(__file__).parent))
        finally:
            server.should_exit = True
            t.join(10)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["method"], "credit")
        self.assertEqual(out["posts"], 1)
        rets = self.db.all("SELECT refund_method, total FROM sale_returns WHERE sale_id=?", (sid,))
        self.assertEqual([(x["refund_method"], x["total"]) for x in rets], [("credit", 1000)])
        self.assertEqual(s.balance(self.ctx, cust), 2000)
        self.assertFalse(self.db.one("SELECT count(*) c FROM cash_movements WHERE kind='refund'")["c"])
