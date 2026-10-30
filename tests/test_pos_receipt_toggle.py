"""#81 on the sale screen: the receipt toggle (default off, F4, the owner's amount rule, an explicit choice
kept for that sale only), the request carries the choice, and the HH360 scanner's F12 never opens the browser
tools and sends the code to the search box (a scan with a leading or trailing F12 adds the item once)."""
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
from app.printing import settings as rs
from app.sales.cash import CashDrawer
from tests.helpers import OWNER_PW, needs_browser
from tests.test_api import APICase

JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")
FN = re.search(r"\nfunction receiptWanted\(.*?\n\}\n", JS, re.S).group(0)

RULE = FN + r"""
const on = { auto_receipt: true, auto_receipt_min: 2000 }, off = { auto_receipt: false, auto_receipt_min: 0 };
const s = (total, kind = "store") => ({ total, kind });
console.log(JSON.stringify([
  receiptWanted(s(1999), on), receiptWanted(s(2000), on), receiptWanted(s(2001), on),    // threshold, inclusive
  receiptWanted(s(0), { auto_receipt: true, auto_receipt_min: 0 }),                      // zero: every sale
  receiptWanted(s(999999), off), receiptWanted(s(5000), null),                            // rule off / unknown
  receiptWanted(s(5000), on, false), receiptWanted(s(10), on, true),                      // explicit choice wins
  receiptWanted(s(10, "delivery"), off), receiptWanted(s(10, "delivery"), on, false),     // delivery: always one
]));
"""


@unittest.skipUnless(shutil.which("node"), "Node not available")
class TestReceiptRule(unittest.TestCase):
    def test_threshold_zero_disabled_override_delivery(self):
        r = subprocess.run([shutil.which("node"), "-e", RULE], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), [False, True, True, True, False, False, False, True, True, True])

    def test_override_is_per_sale_and_dropped_after_completion(self):
        self.assertIn("rcpt: {}", JS)
        self.assertIn("POS.rcpt[s.id] = e.target.checked", JS)
        self.assertIn("delete POS.rcpt[SALE.id]", JS)
        self.assertIn("receipt: receiptWanted(SALE, RCFG, POS.rcpt[SALE.id])", JS)
        self.assertNotIn("open_drawer: cash > 0", JS)          # the screen no longer asks for the drawer


SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, code] = process.argv.slice(1);
  const b = await chromium.launch();
  const p = await b.newPage();
  const bodies = [];
  p.on('request', (r) => { if (r.url().endsWith('/complete')) bodies.push(JSON.parse(r.postData())); });
  await p.goto(base + '/');
  await p.evaluate(async ([u, w]) => fetch('/api/auth/login', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'pharm' }, body: JSON.stringify({ username: u, password: w }) }), [user, pw]);
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posQ');
  const out = {};
  // leading F12 while the focus is in the customer field: the code still lands in the search box
  await p.locator('#posCust').click();
  await p.keyboard.press('F12');
  out.focusAfterF12 = await p.evaluate(() => document.activeElement.id);
  await p.keyboard.type(code, { delay: 5 }); await p.keyboard.press('Enter');
  await p.waitForSelector('table.cart [data-minus]', { timeout: 10000 });
  await p.keyboard.press('F12');                                          // trailing F12 adds nothing more
  await p.waitForTimeout(800);
  out.qtyAfterScan = await p.locator('table.cart tbody tr .qty b').first().innerText();
  out.lines = await p.locator('table.cart [data-minus]').count();
  out.defaultOff = !(await p.locator('#posRcpt').isChecked());            // 10.00 is below the 20.00 rule
  await p.keyboard.press('F4');
  out.afterF4 = await p.locator('#posRcpt').isChecked();
  await p.locator('#posQ').focus();
  await p.keyboard.type(code, { delay: 5 }); await p.keyboard.press('Enter');   // total now 20.00: rule says yes
  await p.waitForFunction(() => /^2\b/.test(document.querySelector('table.cart tbody tr .qty b').innerText));
  await p.keyboard.press('F4');                                           // explicit "no" wins over the rule
  out.overrideKept = !(await p.locator('#posRcpt').isChecked());
  await p.keyboard.press('F9');
  await p.waitForFunction(() => document.querySelectorAll('table.cart [data-minus]').length === 0, null, { timeout: 10000 });
  await p.waitForTimeout(500);
  // next sale: the override is gone; the rule decides again
  await p.locator('#posQ').focus();
  await p.keyboard.type(code, { delay: 5 }); await p.keyboard.press('Enter');
  await p.locator('#posQ').focus();
  await p.keyboard.type(code, { delay: 5 }); await p.keyboard.press('Enter');
  await p.waitForFunction(() => /^2\b/.test((document.querySelector('table.cart tbody tr .qty b') || {}).innerText || ''));
  await p.waitForTimeout(300);
  out.nextSaleRule = await p.locator('#posRcpt').isChecked();
  await p.keyboard.press('F9');
  await p.waitForFunction(() => document.querySelectorAll('table.cart [data-minus]').length === 0, null, { timeout: 10000 });
  out.receipts = bodies.map((x) => x.receipt);
  out.lastLink = await p.locator('#posLast').count();
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestReceiptToggleInBrowser(APICase):
    def test_toggle_rule_override_reset_and_scanner_f12(self):
        import uvicorn
        it = cat.create_item(self.db, self.ctx, {"name_en": "Test Item", "public_price": 1000})
        cat.add_barcode(self.db, self.ctx, it, "6221043010359")
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, it, "B1", (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=20, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("5"))
        CashDrawer(self.db).open(self.ctx, 0)
        rs.save(self.db, self.ctx, {"receipt_target": "browser", "auto_receipt": True, "auto_receipt_min": 2000})
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
            r = subprocess.run([shutil.which("node"), "-e", SCRIPT, f"http://127.0.0.1:{port}", "owner", OWNER_PW,
                                "6221043010359"], capture_output=True, text=True, timeout=120, cwd=str(Path(__file__).parent))
        finally:
            server.should_exit = True
            t.join(10)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["focusAfterF12"], "posQ")
        self.assertEqual(out["lines"], 1)
        self.assertTrue(re.match(r"^1\b", out["qtyAfterScan"]), out["qtyAfterScan"])   # once, not twice
        self.assertTrue(out["defaultOff"])
        self.assertTrue(out["afterF4"])
        self.assertTrue(out["overrideKept"])
        self.assertTrue(out["nextSaleRule"])
        self.assertEqual(out["receipts"], [False, True])
        self.assertEqual(out["lastLink"], 1)


if __name__ == "__main__":
    unittest.main()
