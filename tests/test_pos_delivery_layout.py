"""Device test 11/01: the delivery panel on the sale screen ran off the left edge of the screen (the address
and phone inputs pushed their two columns wider than the side panel, and the whole page scrolled sideways).
Checked in a real browser at the pharmacy PC's size and two smaller ones. Synthetic data only."""
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
  const [base, user, pw, code] = process.argv.slice(1);
  const b = await chromium.launch();
  const out = {};
  for (const [w, h] of [[1366, 768], [1280, 720], [1024, 768]]) {
    const p = await b.newPage({ viewport: { width: w, height: h } });
    await p.goto(base + '/'); await p.waitForSelector('#loginForm');
    await p.evaluate(async ([u, x]) => fetch('/api/auth/login', { method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'pharm' }, body: JSON.stringify({ username: u, password: x }) }), [user, pw]);
    await p.goto(base + '/#/pos'); await p.waitForSelector('#posQ');
    await p.locator('#posQ').focus(); await p.keyboard.type(code, { delay: 5 }); await p.keyboard.press('Enter');
    await p.waitForSelector('table.cart [data-minus]');
    await p.locator('button[data-kind="delivery"]').click(); await p.waitForSelector('.deliv input');
    out[w] = await p.evaluate(() => {
      const outside = [...document.querySelectorAll('.deliv, .deliv label, .deliv input')].filter((e) => {
        const r = e.getBoundingClientRect(); return r.left < -1 || r.right > window.innerWidth + 1; }).length;
      return { pageWider: document.documentElement.scrollWidth > window.innerWidth, outside };
    });
    await p.close();
  }
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class DeliveryPanelFits(APICase):
    def test_the_delivery_panel_stays_on_screen(self):
        import uvicorn
        it = cat.create_item(self.db, self.ctx, {"name_en": "Delivery Item With A Long Name 500mg", "public_price": 1000})
        cat.add_barcode(self.db, self.ctx, it, "6221043010380")
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, it, "B1", (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=20, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("5"))
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
                                "6221043010380"], capture_output=True, text=True, timeout=120, cwd=str(Path(__file__).parent))
        finally:
            server.should_exit = True
            t.join(10)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        out = json.loads(r.stdout.strip().splitlines()[-1])
        for w in ("1366", "1280", "1024"):
            self.assertEqual(out[w], {"pageWider": False, "outside": 0}, (w, out))
