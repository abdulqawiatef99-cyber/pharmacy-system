"""Device test 11/01: on a delivery sale, F9 typed while the address field still had the focus was refused
("the delivery needs the address or phone"): the address is saved when the field is left, and F9 did not
leave it. Completing now saves a field still being typed in first. Synthetic data only."""
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
  const p = await b.newPage();
  const completes = [];
  p.on('response', (r) => { if (r.url().endsWith('/complete')) completes.push(r.status()); });
  await p.goto(base + '/');
  await p.waitForSelector('#loginForm');
  await p.evaluate(async ([u, w]) => fetch('/api/auth/login', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'pharm' }, body: JSON.stringify({ username: u, password: w }) }), [user, pw]);
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posQ');
  await p.locator('#posQ').focus();
  await p.keyboard.type(code, { delay: 5 }); await p.keyboard.press('Enter');
  await p.waitForSelector('table.cart [data-minus]', { timeout: 10000 });
  await p.locator('button[data-kind="delivery"]').click();
  await p.waitForSelector('[data-dl="address"]');
  await p.locator('[data-dl="address"]').click();
  await p.keyboard.type('شارع التجربة 5', { delay: 5 });
  await p.keyboard.press('F9');                                           // without leaving the field
  await p.waitForFunction(() => document.querySelectorAll('table.cart [data-minus]').length === 0, null, { timeout: 10000 });
  console.log(JSON.stringify({ completes }));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


class Wiring(__import__("unittest").TestCase):
    def test_complete_waits_for_a_delivery_field_being_typed_in(self):
        js = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")
        start = js.index("async function complete(findings = null) {")
        submit = js.index("async function finishSale(findings, sid) {", start)
        self.assertIn("active?.dataset?.dl", js[start:submit])
        self.assertLess(js.index("active.blur()", start), js.index("app.inert = true", start))
        self.assertIn("await POS.saving;", js[submit:submit + 250])


@needs_browser
class DeliveryF9InBrowser(APICase):
    def test_f9_in_the_address_field_completes_the_delivery(self):
        import uvicorn
        it = cat.create_item(self.db, self.ctx, {"name_en": "Delivery Item", "public_price": 1000})
        cat.add_barcode(self.db, self.ctx, it, "6221043010373")
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
                                "6221043010373"], capture_output=True, text=True, timeout=120, cwd=str(Path(__file__).parent))
        finally:
            server.should_exit = True
            t.join(10)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1])["completes"], [200])
        d = self.db.one("SELECT d.address, s.status FROM sale_deliveries d JOIN sales s ON s.id=d.sale_id"
                        " WHERE d.org_id=? ORDER BY s.created_at DESC LIMIT 1", (self.ctx.org_id,))
        self.assertEqual((d["address"], d["status"]), ("شارع التجربة 5", "completed"))
