"""Trial #4 (#41): the sale screen lists results while typing, and the barcode scanner still works.

Server: a middle word of the Arabic name is found. Source check (CI): typing searches without adding.
Browser (where Node + Playwright exist, skipped elsewhere): type "تجربة" with no Enter -> the item is
listed and nothing is added; then a scanner-style barcode + Enter adds the item exactly once."""
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
from app.catalog.search import search
from app.inventory import service as inv
from app.sales.cash import CashDrawer
from tests.helpers import DBTestCase, OWNER_PW
from tests.test_api import APICase

JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")


def _playwright() -> bool:
    node = shutil.which("node")
    return bool(node) and subprocess.run([node, "-e", "require('playwright')"], capture_output=True).returncode == 0


class TestArabicMiddleWord(DBTestCase):
    def test_second_word_of_arabic_name(self):
        cat.create_item(self.db, self.ctx, {"name_en": "Test Item", "name_ar": "صنف تجربة", "public_price": 1000})
        for q in ("تجربة", "تجربه", "تجر", "صنف تجربة"):
            self.assertEqual([i["name_ar"] for i in search(self.db, self.ctx, q)["items"]], ["صنف تجربة"], q)


class TestLiveSearchSource(unittest.TestCase):
    def test_typing_searches_without_adding(self):
        body = JS[JS.index('const q = $("#posQ");'):JS.index("bindPosExtras();")]
        self.assertIn('q.addEventListener("input"', body)
        self.assertRegex(body, r"search\(false, true\)")                        # live search
        self.assertIn("if (!live && res.length === 1", body)                     # live never auto-adds
        self.assertIn("if (mine !== seq) return;", body)                         # stale answers dropped
        self.assertRegex(body, r"/\^\\d\+\$/\.test\(v\)\) \{")                 # digits wait for the scanner's Enter
        # Codex on #42: every keystroke drops answers still on their way (also when no new search starts)
        handler = body[body.index('q.addEventListener("input"'):body.index('q.addEventListener("keydown"')]
        self.assertLess(handler.index("seq++"), handler.index("if (v.length < 2"))


SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, code] = process.argv.slice(1);
  const b = await chromium.launch();
  const p = await b.newPage();
  await p.goto(base + '/');
  await p.evaluate(async ([u, w]) => fetch('/api/auth/login', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'pharm' }, body: JSON.stringify({ username: u, password: w }) }), [user, pw]);
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posQ');
  await p.locator('#posQ').pressSequentially('تجربة', { delay: 60 });       // a person typing, no Enter
  await p.waitForSelector('#posRes .res', { timeout: 10000 });
  await p.waitForTimeout(600);
  const listed = await p.locator('#posRes .res').count();
  const linesAfterTyping = await p.locator('table.cart [data-minus]').count();
  await p.locator('#posQ').fill('');
  await p.locator('#posQ').pressSequentially(code, { delay: 5 });          // a scanner: fast digits, then Enter
  await p.keyboard.press('Enter');
  await p.waitForSelector('table.cart [data-minus]', { timeout: 10000 });
  await p.waitForTimeout(800);
  const linesAfterScan = await p.locator('table.cart [data-minus]').count();
  const qty = await p.locator('table.cart tbody tr .qty b').first().innerText();
  // Codex on #42: a slow answer for "صنف" arrives after the text became "1" -> it must not show
  await p.route('**/api/items?q=*', async (route) => { await new Promise((r) => setTimeout(r, 1200)); await route.continue(); });
  await p.locator('#posQ').fill('');
  await p.locator('#posQ').pressSequentially('صنف', { delay: 30 });
  await p.waitForTimeout(400);                                             // the search for "صنف" has started
  await p.locator('#posQ').fill('1');
  await p.waitForTimeout(1800);                                            // its answer has arrived by now
  const staleShown = await p.locator('#posRes .res').count();
  console.log(JSON.stringify({ listed, linesAfterTyping, linesAfterScan, qty, staleShown }));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@unittest.skipUnless(_playwright(), "Node + Playwright not available here (runs where they are)")
class TestLiveSearchInBrowser(APICase):
    def test_type_without_enter_then_scan(self):
        import uvicorn
        it = cat.create_item(self.db, self.ctx, {"name_en": "Test Item", "name_ar": "صنف تجربة", "public_price": 1000})
        cat.add_barcode(self.db, self.ctx, it, "6221043010359")
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, it, "B1", (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=10, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("5"))
        CashDrawer(self.db).open(self.ctx, 0)
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
                                "6221043010359"], capture_output=True, text=True, timeout=90, cwd=str(Path(__file__).parent))
        finally:
            server.should_exit = True
            t.join(10)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertGreaterEqual(out["listed"], 1)               # found while typing, no Enter
        self.assertEqual(out["linesAfterTyping"], 0)            # and nothing was added
        self.assertEqual(out["linesAfterScan"], 1)              # the scan added it once
        self.assertTrue(re.match(r"^1\b", out["qty"]), out["qty"])
        self.assertEqual(out["staleShown"], 0)                  # an old answer never shows for new text


if __name__ == "__main__":
    unittest.main()
