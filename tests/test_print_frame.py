"""Trial #2 (#36, Codex on #37): the browser fallback prints from a hidden frame, never a pop-up.

`window.open` after an `await` is a pop-up the browser may block (it did on the pharmacy PC). The first
test checks the source in CI; the second drives a real Chromium against the running app where Node +
Playwright exist (skipped elsewhere, like the raqm tests), clicking a button whose handler awaits the
API before printing."""
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

from app.inventory import service as inv
from app.sales.cash import CashDrawer
from app.sales.service import Sales
from tests.helpers import OWNER_PW, needs_browser
from tests.test_api import APICase

JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")



class TestPrintFallbackSource(unittest.TestCase):
    def test_printable_pages_are_never_opened_as_popups(self):
        self.assertNotRegex(JS, r"window\.open\(`/(receipt|labels|shift-report)")
        for page in ("/receipt/${done.id}", "/receipt/${last.id}", "/shift-report/${id}", "/labels?items="):
            self.assertIn("printInFrame(`" + page, JS)

    def test_frame_is_hidden_and_loads_the_page(self):
        body = re.search(r"function printInFrame\(url\) \{(.*?)\n\}", JS, re.S).group(1)
        self.assertIn('createElement("iframe")', body)
        self.assertIn("f.src = url", body)
        self.assertIn("width:0;height:0", body)


SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw] = process.argv.slice(1);
  const b = await chromium.launch();
  const ctx = await b.newContext();
  // every frame records print() instead of opening a dialog
  await ctx.addInitScript(() => { window.print = () => { window.top.__printed = (window.top.__printed || []).concat(location.pathname); }; });
  const p = await ctx.newPage();
  let popups = 0;
  ctx.on('page', () => { popups += 1; });
  await p.goto(base + '/');
  // the app's first check (no session -> 401 -> #/login) must finish before we log in and change the hash,
  // or its late answer sends the page back to the login screen (seen once on CI, #91)
  await p.waitForSelector('#loginForm');
  const st = await p.evaluate(async ([u, w]) => (await fetch('/api/auth/login', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'pharm' }, body: JSON.stringify({ username: u, password: w }) })).status, [user, pw]);
  await p.goto(base + '/#/system');
  await p.locator('#rcTry').click();
  await p.waitForFunction(() => (window.__printed || []).length > 0, null, { timeout: 15000 });
  const out = await p.evaluate(() => ({ printed: window.__printed,
      frame: !!document.querySelector('#printFrame'),
      text: document.querySelector('#printFrame').contentDocument.body.innerText }));
  console.log(JSON.stringify({ login: st, popups, ...out }));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestPrintFallbackInBrowser(APICase):
    def test_receipt_prints_from_hidden_frame_after_await(self):
        import uvicorn
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.pan, "B1", (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=10, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("15"))
        CashDrawer(self.db).open(self.ctx, 0)
        s = Sales(self.db)
        sid = s.create(self.ctx)
        d = s.add_line(self.ctx, sid, self.pan, units=1)
        f = s.safety_check(self.ctx, sid)
        s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions={i: "x" for i in range(len(f))})
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
            r = subprocess.run([shutil.which("node"), "-e", SCRIPT, f"http://127.0.0.1:{port}", "owner", OWNER_PW],
                               capture_output=True, text=True, timeout=90, cwd=str(Path(__file__).parent))
        finally:
            server.should_exit = True
            t.join(10)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["login"], 200)
        self.assertEqual(out["popups"], 0)                       # no new tab / window
        self.assertTrue(out["frame"])
        self.assertEqual(out["printed"], [f"/receipt/{sid}"])    # printed inside the frame, not the app
        self.assertIn("المطلوب", out["text"])                    # the receipt itself loaded (not blocked)


if __name__ == "__main__":
    unittest.main()
