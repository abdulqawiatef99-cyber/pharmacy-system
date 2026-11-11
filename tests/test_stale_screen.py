"""#153: a tab left open across an update keeps running the old app.js. The page knows the shell it was
loaded with (meta pharm-build), /api/health reports the current one; on a change the screen reloads by itself
where nothing can be lost (sign-in, home, an empty sale screen, no field holding unsaved typing) and otherwise
shows a reload bar."""
import json
import os
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
from unittest import mock

from starlette.testclient import TestClient

from app.api import app as api
from app.catalog import service as cat
from app.inventory import service as inv
from app.sales.cash import CashDrawer
from tests.helpers import OWNER_PW, needs_browser
from tests.test_api import APICase


class StaleCase(APICase):
    def setUp(self):
        super().setUp()
        self.web = self.tmp / "web"
        shutil.copytree(api.WEB_DIR, self.web)
        patch = mock.patch.object(api, "WEB_DIR", self.web)
        patch.start()
        self.addCleanup(patch.stop)
        self.app = api.create_app(self.db, self.s)
        self.c = TestClient(self.app)

    def touch_js(self):
        js = self.web / "app.js"
        js.write_text(js.read_text(encoding="utf-8") + "\n// updated\n", encoding="utf-8")
        st = js.stat()
        os.utime(js, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))   # same second on coarse clocks


class TestBuildId(StaleCase):
    def page_build(self):
        m = re.search(r'<meta name="pharm-build" content="([0-9a-f]{12})">', self.c.get("/").text)
        self.assertIsNotNone(m)
        return m.group(1)

    def test_page_and_health_carry_the_same_build_and_it_follows_the_files(self):
        before = self.page_build()
        self.assertEqual(self.c.get("/api/health").json()["web"], before)
        self.touch_js()                                                     # an update replaced app.js, no restart
        after = self.page_build()
        self.assertNotEqual(after, before)
        self.assertEqual(self.c.get("/api/health").json()["web"], after)

    def test_screen_wiring(self):
        js = (self.web / "app.js").read_text(encoding="utf-8")
        self.assertIn('meta[name="pharm-build"]', js)
        self.assertIn('fetch("/api/health", { cache: "no-store" })', js)
        self.assertIn('id="staleBar"', (self.web / "index.html").read_text(encoding="utf-8"))


SCRIPT = r"""
const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const [base, user, pw, code, jsPath] = process.argv.slice(1);
  const touch = () => {
    fs.appendFileSync(jsPath, '\n// updated ' + Date.now() + '\n');
    const t = new Date(Date.now() + 2000); fs.utimesSync(jsPath, t, t);
  };
  const build = (p) => p.evaluate(() => document.querySelector('meta[name="pharm-build"]').content);
  const b = await chromium.launch();
  const p = await b.newPage();
  const out = {};
  await p.goto(base + '/');
  await p.waitForSelector('#loginForm');
  const b1 = await build(p);
  out.barHiddenAtStart = await p.locator('#staleBar').isHidden();
  // 1. sign-in screen: nothing to lose, the screen reloads by itself
  touch();
  await p.evaluate(() => window.dispatchEvent(new Event('focus')));
  await p.waitForFunction((old) => { const m = document.querySelector('meta[name="pharm-build"]'); return m && m.content !== old; },
                          b1, { timeout: 10000 });
  await p.waitForSelector('#loginForm');
  out.reloadedOnLogin = (await build(p)) !== b1;
  // 2. a sale with a line on the screen: never reloads by itself, shows the bar
  await p.evaluate(async ([u, w]) => fetch('/api/auth/login', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'pharm' }, body: JSON.stringify({ username: u, password: w }) }), [user, pw]);
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posQ');
  await p.locator('#posQ').focus();
  await p.keyboard.type(code, { delay: 5 }); await p.keyboard.press('Enter');
  await p.waitForSelector('table.cart [data-minus]', { timeout: 10000 });
  await p.evaluate(() => { window.__sameDoc = 1; });
  const b2 = await build(p);
  touch();
  await p.evaluate(() => window.dispatchEvent(new Event('focus')));
  await p.waitForSelector('#staleBar:not([hidden])', { timeout: 10000 });
  await p.waitForTimeout(500);
  out.sameDocument = await p.evaluate(() => window.__sameDoc === 1);
  out.cartKept = await p.locator('table.cart [data-minus]').count();
  // 3. the bar reloads the screen on the new files
  await p.locator('#staleBar').click();
  await p.waitForFunction((old) => { const m = document.querySelector('meta[name="pharm-build"]'); return m && m.content !== old; },
                          b2, { timeout: 10000 });
  await p.waitForSelector('#posQ');
  out.barHiddenAfterReload = await p.locator('#staleBar').isHidden();
  // 4. an empty sale screen with a search still being typed: not idle, the bar instead of a reload
  if (await p.locator('table.cart [data-minus]').count()) {
    await p.locator('table.cart [data-minus]').first().click();
    await p.waitForFunction(() => document.querySelectorAll('table.cart [data-minus]').length === 0, null, { timeout: 10000 });
  }
  await p.locator('#posQ').focus();
  await p.keyboard.type('pana', { delay: 5 });
  await p.evaluate(() => { window.__sameDoc = 2; });
  touch();
  await p.evaluate(() => window.dispatchEvent(new Event('focus')));
  await p.waitForSelector('#staleBar:not([hidden])', { timeout: 10000 });
  await p.waitForTimeout(500);
  out.typingKept = await p.evaluate(() => window.__sameDoc === 2 && document.querySelector('#posQ').value === 'pana');
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestStaleScreenInBrowser(StaleCase):
    def test_reload_where_safe_bar_where_a_sale_is_open(self):
        import uvicorn
        it = cat.create_item(self.db, self.ctx, {"name_en": "Test Item", "public_price": 1000})
        cat.add_barcode(self.db, self.ctx, it, "6221043010359")
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, it, "B1", (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=20, batch_id=b, movement_type="purchase",
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
                                "6221043010359", str(self.web / "app.js")],
                               capture_output=True, text=True, timeout=120, cwd=str(Path(__file__).parent))
        finally:
            server.should_exit = True
            t.join(10)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out, {"barHiddenAtStart": True, "reloadedOnLogin": True, "sameDocument": True, "cartKept": 1,
                               "barHiddenAfterReload": True, "typingKept": True})


if __name__ == "__main__":
    unittest.main()
