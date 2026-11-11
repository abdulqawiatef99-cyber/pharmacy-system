"""Security audit #131: the owner's password and PIN can be changed only by the owner himself, and only
with his current password. Before the fix, anyone at a signed-in owner session (left open on the shared
pharmacy PC) could set a new owner PIN or password from the users screen without knowing the password,
locking the owner out (there is no recovery) or using the new PIN for owner approvals later. A cashier
holding an owner approval (X-Approver, kept 90 s by the screen) could also set the owner's PIN."""
import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

from starlette.testclient import TestClient

from app.core import people, security
from tests.test_api import APICase, H
from tests.helpers import OWNER_PW, needs_browser

PW = "Staff-Pass-2026"


class TestOwnerCredentials(APICase):
    def setUp(self):
        super().setUp()
        people.set_pin(self.db, self.ctx, self.ctx.user_id, "9753")
        self.owner = self.ctx.user_id

    def _pin_hash(self, uid):
        return self.db.one("SELECT pin_hash FROM users WHERE id=?", (uid,))["pin_hash"]

    def test_open_owner_session_cannot_change_owner_pin_without_password(self):
        self.http_login()
        r = self.c.post(f"/api/users/{self.owner}/pin", json={"pin": "4826"}, headers=H)
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(r.json()["error"]["code"], "current_password_required")
        self.assertTrue(security.verify_password("9753", self._pin_hash(self.owner)))
        r = self.c.post(f"/api/users/{self.owner}/pin", json={"pin": "4826", "current_password": "wrong-pass-1"},
                        headers=H)
        self.assertEqual(r.status_code, 422, r.text)
        self.assertTrue(security.verify_password("9753", self._pin_hash(self.owner)))
        r = self.c.post(f"/api/users/{self.owner}/pin", json={"pin": "4826", "current_password": OWNER_PW},
                        headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(security.verify_password("4826", self._pin_hash(self.owner)))

    def test_open_owner_session_cannot_change_owner_password_without_password(self):
        self.http_login()
        new = "Taken-Over-2026"
        r = self.c.post(f"/api/users/{self.owner}/password", json={"password": new}, headers=H)
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(r.json()["error"]["code"], "current_password_required")
        self.assertEqual(self.c.get("/api/me").status_code, 200)          # session not revoked
        fresh = TestClient(self.app)
        self.http_login(client=fresh)                                       # old password still works
        r = self.c.post(f"/api/users/{self.owner}/password", json={"password": new, "current_password": OWNER_PW},
                        headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.http_login(password=new, client=TestClient(self.app))

    def test_cashier_with_owner_approval_cannot_set_owner_pin(self):
        people.add_user(self.db, self.ctx, username="asst", full_name="مساعد", password=PW,
                        role_code="assistant", pin="8642")
        uid = people.add_user(self.db, self.ctx, username="pharm", full_name="صيدلي", password=PW,
                              role_code="pharmacist")
        a = TestClient(self.app)
        self.http_login("asst", PW, client=a)
        hdr = {**H, "X-Approver": self.owner, "X-Approver-Pin": "9753"}
        r = a.post(f"/api/users/{self.owner}/pin", json={"pin": "4826"}, headers=hdr)
        self.assertEqual(r.status_code, 403, r.text)
        self.assertTrue(security.verify_password("9753", self._pin_hash(self.owner)))
        # the owner (or his approval) still sets a staff member's PIN as before
        r = a.post(f"/api/users/{uid}/pin", json={"pin": "5172"}, headers=hdr)
        self.assertEqual(r.status_code, 200, r.text)
        self.http_login(client=self.c)
        r = self.c.post(f"/api/users/{uid}/pin", json={"pin": "5283"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)


SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, uid] = process.argv.slice(1);
  const b = await chromium.launch();
  const p = await b.newPage();
  await p.goto(base + '/');
  await p.waitForSelector('#loginForm');
  await p.evaluate(async ([u, w]) => fetch('/api/auth/login', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'pharm' }, body: JSON.stringify({ username: u, password: w }) }), [user, pw]);
  await p.goto(base + '/#/users/' + uid);
  await p.waitForSelector('#uPin [name=current]');
  await p.locator('#uPin [name=current]').fill(pw);
  await p.locator('#uPin [name=pin]').fill('4826');
  const resp = p.waitForResponse((r) => r.url().endsWith('/pin') && r.request().method() === 'POST');
  await p.locator('#uPin button').click();
  console.log(JSON.stringify({ status: (await resp).status() }));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestOwnPinFormInBrowser(APICase):
    def test_owner_page_asks_current_password_and_saves_pin(self):
        import uvicorn
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
                                self.ctx.user_id], capture_output=True, text=True, timeout=120,
                               cwd=str(Path(__file__).parent))
        finally:
            server.should_exit = True
            t.join(10)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1])["status"], 200)
        h = self.db.one("SELECT pin_hash FROM users WHERE id=?", (self.ctx.user_id,))["pin_hash"]
        self.assertTrue(security.verify_password("4826", h))
