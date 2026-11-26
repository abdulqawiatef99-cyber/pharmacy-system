"""Device test 11/01: a quick-sale button stayed "ناقص" (out of stock) after the item was bought, until the page
was reloaded, because the screen kept its first list for the whole session. Coming back to the sale screen
now reads the list again. Synthetic data only."""
import json
import shutil
import socket
import subprocess
import threading
import time
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app.catalog import service as cat
from app.inventory import service as inv
from tests.helpers import OWNER_PW, needs_browser
from tests.test_api import APICase

SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw] = process.argv.slice(1);
  const b = await chromium.launch();
  const p = await b.newPage();
  await p.goto(base + '/');
  await p.waitForSelector('#loginForm');
  await p.evaluate(async ([u, w]) => fetch('/api/auth/login', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'pharm' }, body: JSON.stringify({ username: u, password: w }) }), [user, pw]);
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posQuick .qbtn');
  const before = await p.locator('#posQuick .qbtn').first().isDisabled();
  console.log('RESTOCK');
  await new Promise((r) => process.stdin.once('data', r));               // the test adds the stock
  await p.goto(base + '/#/home');
  await p.waitForSelector('#topbar:not([hidden])');
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posQuick .qbtn');
  // the buttons are drawn again when the fresh list arrives, so for a moment there may be none
  await p.waitForFunction(() => { const q = document.querySelector('#posQuick .qbtn'); return !!q && !q.disabled; },
      null, { timeout: 10000 });
  const after = await p.locator('#posQuick .qbtn').first().isDisabled();
  console.log(JSON.stringify({ before, after }));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


class QuickListWiring(__import__("unittest").TestCase):
    def test_the_sale_screen_reads_the_quick_list_again_on_every_visit(self):
        js = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")
        start = js.index("async function viewPos(afterCheckout = false) {")
        self.assertIn("QUICK = null;", js[start:start + 300])


@needs_browser
class QuickListInBrowser(APICase):
    def test_a_bought_item_is_no_longer_out_of_stock_on_the_quick_buttons(self):
        import uvicorn
        it = cat.create_item(self.db, self.ctx, {"name_en": "Quick Item", "public_price": 1000, "quick_pin": 1})
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
            proc = subprocess.Popen([shutil.which("node"), "-e", SCRIPT, f"http://127.0.0.1:{port}", "owner", OWNER_PW],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    cwd=str(Path(__file__).parent))
            line = proc.stdout.readline()
            self.assertEqual(line.strip(), "RESTOCK", proc.stderr.read() if not line else line)
            with self.db.transaction():                                   # the purchase arrives meanwhile
                b = inv.get_or_create_batch(self.db, self.ctx, it, "B1", (date.today() + timedelta(days=400)).isoformat())
                inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=5, batch_id=b, movement_type="purchase",
                                  unit_cost=Decimal("5"))
            out, err = proc.communicate("go\n", timeout=120)
        finally:
            server.should_exit = True
            t.join(10)
        self.assertEqual(proc.returncode, 0, err[-2000:])
        res = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(res, {"before": True, "after": False})
