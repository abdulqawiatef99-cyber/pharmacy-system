"""Device test 11/01: "+ new customer" on the sale screen asked with two prompt() boxes, so a phone
already used lost what was typed and nothing showed which field was wrong. It is now a form: the
search text fills the name or the phone, a refusal stays on the form, and one save makes one customer.
Synthetic data only."""
import json
import shutil
import socket
import subprocess
import threading
import time
import unittest
from pathlib import Path

from app.sales.service import Sales
from tests.helpers import OWNER_PW, needs_browser
from tests.test_api import APICase

APP_JS = Path(__file__).resolve().parent.parent / "app" / "web" / "app.js"

SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw] = process.argv.slice(1);
  const b = await chromium.launch();
  const p = await b.newPage();
  const dialogs = [];
  p.on('dialog', async (d) => { dialogs.push(d.message()); await d.dismiss(); });
  const creates = [];
  p.on('response', (r) => { if (r.url().endsWith('/api/customers') && r.request().method() === 'POST') creates.push(r.status()); });
  await p.goto(base + '/');
  await p.waitForSelector('#loginForm');
  await p.evaluate(async ([u, w]) => fetch('/api/auth/login', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'pharm' }, body: JSON.stringify({ username: u, password: w }) }), [user, pw]);
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posCustNew');
  // a phone typed in the search fills the phone field; that phone already belongs to a customer
  await p.locator('#posCust').fill('01000000001');
  await p.locator('#posCustNew').click();
  await p.waitForSelector('#custNewDlg[open]');
  const prefilled = await p.evaluate(() => { const f = document.querySelector('#custNewDlg form');
    return [f.elements.namedItem('name').value, f.elements.namedItem('phone').value, document.activeElement.name]; });
  await p.keyboard.type('عميل وهمي جديد', { delay: 5 });
  await p.keyboard.press('Enter');
  await p.waitForSelector('#custNewDlg [data-err]:not([hidden])', { timeout: 10000 });
  const refused = await p.evaluate(() => ({ open: !!document.querySelector('#custNewDlg[open]'),
    name: document.querySelector('#custNewDlg form').elements.namedItem('name').value }));
  // fix the phone and save with a double click: one customer, chosen on the sale
  await p.locator('#custNewDlg [name=phone]').fill('01000000002');
  await p.locator('#custNewDlg button[value=ok]').dblclick();
  await p.waitForSelector('#custClear', { timeout: 10000 });
  const closed = !(await p.$('#custNewDlg'));
  console.log(JSON.stringify({ dialogs, creates, prefilled, refused, closed, chosen: await p.locator('#posCust').inputValue() }));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


class Wiring(unittest.TestCase):
    def test_no_prompt_box_for_a_new_customer(self):
        js = APP_JS.read_text(encoding="utf-8")
        start = js.index('$("#posCustNew")')
        self.assertNotIn("prompt(", js[start:start + 200])
        self.assertIn("function newCustomerDialog()", js)


@needs_browser
class NewCustomerInBrowser(APICase):
    def test_form_keeps_the_typing_on_a_refusal_and_saves_once(self):
        import uvicorn
        Sales(self.db).create_customer(self.ctx, name="عميل وهمي قديم", phone="01000000001")
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
                               capture_output=True, text=True, timeout=120, cwd=str(Path(__file__).parent))
        finally:
            server.should_exit = True
            t.join(10)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["dialogs"], [])                                 # no prompt() / alert()
        self.assertEqual(out["prefilled"], ["", "01000000001", "name"])
        self.assertEqual(out["refused"], {"open": True, "name": "عميل وهمي جديد"})
        self.assertEqual(out["creates"][-1], 201)
        self.assertEqual(out["creates"].count(201), 1)
        self.assertTrue(out["closed"])
        self.assertEqual(out["chosen"], "عميل وهمي جديد")
        rows = self.db.all("SELECT phone FROM customers WHERE org_id=? AND name=?", (self.ctx.org_id, "عميل وهمي جديد"))
        self.assertEqual([x["phone"] for x in rows], ["01000000002"])
