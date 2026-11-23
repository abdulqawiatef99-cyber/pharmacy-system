"""#98 on the sale screen: receipt printer switched off, cash sale with a receipt. The screen must show ONE red
warning that names both the receipt and the drawer (each toast replaced the last one, so the receipt warning
was hidden under the drawer one), never "sent", and the Windows queue must be empty afterwards."""
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest import mock

from PIL import Image

from app.catalog import service as cat
from app.inventory import service as inv
from app.printing import escpos, settings as rs
from app.sales.cash import CashDrawer
from tests.helpers import OWNER_PW, needs_browser
from tests.test_api import APICase
from tests.test_spooler import FakeSpooler

SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, code] = process.argv.slice(1);
  const b = await chromium.launch();
  const p = await b.newPage();
  await p.goto(base + '/');
  await p.waitForSelector('#loginForm');
  await p.evaluate(async ([u, w]) => fetch('/api/auth/login', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'pharm' }, body: JSON.stringify({ username: u, password: w }) }), [user, pw]);
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posQ');
  await p.locator('#posQ').focus();
  await p.keyboard.type(code, { delay: 5 }); await p.keyboard.press('Enter');
  await p.waitForSelector('table.cart [data-minus]', { timeout: 10000 });
  const out = { receiptOn: await p.locator('#posRcpt').isChecked() };
  await p.keyboard.press('F9');
  await p.waitForFunction(() => document.querySelectorAll('table.cart [data-minus]').length === 0, null, { timeout: 15000 });
  await p.waitForSelector('#toast.error:not([hidden])', { timeout: 10000 });
  await p.waitForTimeout(300);
  out.toast = await p.locator('#toast').innerText();
  out.printFrame = await p.locator('#printFrame').count();                 // the browser receipt was offered
  await p.waitForTimeout(8000);                                             // past the normal 7 s error toast
  out.stillShown = await p.locator('#toast.error:not([hidden])').count();
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestPrinterOffWarningInBrowser(APICase):
    def test_one_warning_names_receipt_and_drawer_and_the_queue_is_empty(self):
        import uvicorn
        it = cat.create_item(self.db, self.ctx, {"name_en": "Test Item", "public_price": 1000})
        cat.add_barcode(self.db, self.ctx, it, "6221043010359")
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, it, "B1", (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=20, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("5"))
        CashDrawer(self.db).open(self.ctx, 0)
        rs.save(self.db, self.ctx, {"receipt_target": "win:POS-80C", "drawer_timing": "with",
                                    "auto_receipt": True, "auto_receipt_min": 0})
        spool = FakeSpooler("keeps")                                              # the printer is switched off
        for p in (mock.patch.dict(sys.modules, {"win32print": spool}), mock.patch.object(escpos, "SPOOL_WAIT", 0.0),
                  mock.patch.object(escpos, "render_receipt", return_value=Image.new("L", (16, 8), 255))):
            p.start()
            self.addCleanup(p.stop)
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
        self.assertTrue(out["receiptOn"])
        self.assertIn("الإيصال لم يُطبع مباشرة", out["toast"])
        self.assertIn("لا تستجيب", out["toast"])
        self.assertIn("أمر فتح الدرج لم يصل للطابعة", out["toast"])
        self.assertNotIn("أُرسل", out["toast"])
        self.assertEqual(out["printFrame"], 1)
        self.assertEqual(out["stillShown"], 1)
        self.assertEqual((spool.deleted, spool.jobs), ([7, 7], {}))   # both jobs cancelled; nothing happens later
        self.assertEqual(spool.data[0], escpos.INIT + escpos.DRAWER_KICK)
        self.assertNotIn(escpos.DRAWER_KICK, spool.data[1])
