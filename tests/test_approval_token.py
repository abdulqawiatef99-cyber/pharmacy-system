"""#137: an on-screen approval lends one permission, on the one API path that asked for it, for 90 s.

Before: the screen kept the approver's PIN for 90 s and sent it with every request, and any route then
ran with all of the approver's permissions (users, settings, backup, cash out, voids). Now the PIN is
checked once by /api/approvals/check, which returns a token bound to the sign-in session, the approver,
the permission and the path. Synthetic data only.
"""
import re
from pathlib import Path

from starlette.testclient import TestClient

from app.core import approvals, people
from tests.helpers import needs_browser
from tests.test_api import APICase, H, approval_headers

PW = "Staff-Pass-2026"
JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")
SUMMARY, USERS = "/api/backup", "/api/users"          # settings.manage / users.manage


class ApprovalToken(APICase):
    def setUp(self):
        super().setUp()
        approvals.clear()
        people.set_pin(self.db, self.ctx, self.ctx.user_id, "9753")
        self.mgr = people.add_user(self.db, self.ctx, username="mgr", full_name="مدير", password=PW,
                                   role_code="manager", pin="2468")
        people.add_user(self.db, self.ctx, username="asst", full_name="مساعد", password=PW, role_code="assistant")
        self.a = TestClient(self.app)
        self.http_login("asst", PW, client=self.a)
        self.owner = self.ctx.user_id

    def test_the_pin_alone_lends_nothing_outside_the_approval_box(self):
        hdr = {**H, "X-Approver": self.owner, "X-Approver-Pin": "9753"}
        self.assertEqual(self.a.get(SUMMARY, headers=hdr).status_code, 403)
        self.assertEqual(self.a.get(USERS, headers=hdr).status_code, 403)

    def test_a_token_works_on_its_path_only(self):
        hdr = approval_headers(self.a, self.owner, "9753", "settings.manage", SUMMARY)
        self.assertIn("X-Approval", hdr)
        self.assertEqual(self.a.get(SUMMARY, headers=hdr).status_code, 200)
        self.assertEqual(self.a.get(SUMMARY, headers=hdr).status_code, 200)      # same screen, within 90 s
        r = self.a.get(USERS, headers=hdr)                                       # another action
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(r.json()["error"]["code"], "approval_required")

    def test_a_token_is_for_one_method_too(self):
        hdr = approval_headers(self.a, self.owner, "9753", "settings.manage", SUMMARY)   # GET: read the status
        self.assertEqual(self.a.get(SUMMARY, headers=hdr).status_code, 200)
        r = self.a.put(SUMMARY, headers=hdr, json={})                                  # PUT: change settings
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(r.json()["error"]["code"], "approval_required")

    def test_a_token_lends_only_the_permission_approved(self):
        hdr = approval_headers(self.a, self.owner, "9753", "sales.void", USERS)  # approved: void, asked on /users
        r = self.a.get(USERS, headers=hdr)
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(r.json()["error"]["details"]["permission"], "users.manage")

    def test_two_permissions_on_one_action_are_asked_one_by_one(self):
        first = self.a.post("/api/approvals/check", json={"permission": "sales.void", "path": USERS},
                            headers={**H, "X-Approver": self.owner, "X-Approver-Pin": "9753"}).json()
        r = self.a.post("/api/approvals/check", json={"permission": "users.manage", "path": USERS,
                                                      "previous": first["token"]},
                        headers={**H, "X-Approver": self.owner, "X-Approver-Pin": "9753"}).json()
        self.assertNotEqual(r["token"], first["token"])
        self.assertEqual(self.a.get(USERS, headers={**H, "X-Approval": r["token"]}).status_code, 200)
        r2 = self.a.get(USERS, headers={**H, "X-Approval": first["token"]})      # the old one is replaced
        self.assertEqual(r2.json()["error"]["code"], "approval_expired")

    def test_the_token_expires(self):
        hdr = approval_headers(self.a, self.owner, "9753", "settings.manage", SUMMARY)
        self.expire_all()
        r = self.a.get(SUMMARY, headers=hdr)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"]["code"], "approval_expired")

    def test_the_token_belongs_to_the_session_that_asked(self):
        hdr = approval_headers(self.a, self.owner, "9753", "settings.manage", SUMMARY)
        other = TestClient(self.app)
        self.http_login("asst", PW, client=other)                               # same user, another session
        r = other.get(SUMMARY, headers=hdr)
        self.assertEqual(r.json()["error"]["code"], "approval_expired")
        self.http_login("mgr", PW, client=other)
        self.assertEqual(other.get(SUMMARY, headers=hdr).json()["error"]["code"], "approval_expired")

    def test_an_approver_who_lost_the_permission_lends_nothing(self):
        audit = "/api/audit?from=2026-01-01&to=2026-01-02"
        hdr = approval_headers(self.a, self.mgr, "2468", "audit.read", "/api/audit")
        self.assertEqual(self.a.get(audit, headers=hdr).status_code, 200)
        people.set_exception(self.db, self.ctx, self.mgr, "audit.read", "deny")
        self.assertEqual(self.a.get(audit, headers=hdr).status_code, 403)

    def test_a_disabled_approver_lends_nothing(self):
        audit = "/api/audit?from=2026-01-01&to=2026-01-02"
        hdr = approval_headers(self.a, self.mgr, "2468", "audit.read", "/api/audit")
        self.assertEqual(self.a.get(audit, headers=hdr).status_code, 200)
        people.set_active(self.db, self.ctx, self.mgr, False)
        r = self.a.get(audit, headers=hdr)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"]["code"], "approver_invalid")

    def expire_all(self):
        with approvals._lock:
            for k, g in list(approvals._grants.items()):
                approvals._grants[k] = g.__class__(**{**g.__dict__, "until": g.until - approvals.TTL_SECONDS - 1})

    def test_a_retry_after_the_approval_expired_replays_and_never_posts_twice(self):
        """The payment committed but its answer was lost; the retry finds the approval expired, the box asks
        again, and the same Idempotency-Key replays the first answer: one payment, not two."""
        self.http_login()
        cid = self.c.post("/api/customers", json={"name": "عميل تجربة"}, headers=H).json()["id"]
        self.assertEqual(self.a.post("/api/cash/open", json={"opening_cash": 0}, headers=H).status_code, 201)
        path = f"/api/customers/{cid}/payments"
        key = {"Idempotency-Key": "pay-once-1"}
        hdr = approval_headers(self.a, self.owner, "9753", "customers.manage", path, "POST")
        first = self.a.post(path, json={"amount": 1500, "method": "cash"}, headers={**hdr, **key})
        self.assertEqual(first.status_code, 200, first.text)
        self.expire_all()
        r = self.a.post(path, json={"amount": 1500, "method": "cash"}, headers={**hdr, **key})
        self.assertEqual(r.json()["error"]["code"], "approval_expired")
        hdr = approval_headers(self.a, self.owner, "9753", "customers.manage", path, "POST")
        again = self.a.post(path, json={"amount": 1500, "method": "cash"}, headers={**hdr, **key})
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.headers.get("Idempotent-Replay"), "true")
        self.assertEqual(again.json(), first.json())
        n = self.db.one("SELECT count(*) n FROM customer_ledger WHERE customer_id=?", (cid,))["n"]
        self.assertEqual(n, 1)

    def test_the_screen_keeps_the_idempotency_key_when_it_asks_again(self):
        self.assertIn('opts.headers["Idempotency-Key"] = _key || newKey();', JS)
        retries = re.findall(r"return api\(path, \{[^}]*\}\)", JS)
        self.assertEqual(len(retries), 2, retries)
        self.assertTrue(all('_key: opts.headers["Idempotency-Key"]' in r for r in retries), retries)

    def test_a_wrong_pin_gets_no_token(self):
        r = self.a.post("/api/approvals/check", json={"permission": "settings.manage", "path": SUMMARY},
                        headers={**H, "X-Approver": self.owner, "X-Approver-Pin": "1111"})
        self.assertEqual(r.status_code, 401)
        self.assertNotIn("token", r.json())
        r = self.a.post("/api/approvals/check", json={"permission": "settings.manage", "path": "/not-api"},
                        headers={**H, "X-Approver": self.owner, "X-Approver-Pin": "9753"})
        self.assertNotIn("token", r.json())

    def test_the_screen_never_keeps_the_pin(self):
        keep = re.findall(r"APPROVAL = \{[^}]*\}", JS)
        self.assertTrue(keep)
        self.assertTrue(all("pin" not in k for k in keep), keep)
        self.assertNotIn('"X-Approver-Pin": APPROVAL', JS)
        self.assertIn('"X-Approval": APPROVAL.token', JS)


SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, approver, pin] = process.argv.slice(1);
  const b = await chromium.launch();
  const p = await b.newPage();
  const seen = [];
  p.on('request', (r) => { if (r.url().includes('/api/')) {
    const h = r.headers();
    seen.push({ path: new URL(r.url()).pathname, pin: h['x-approver-pin'] || null, token: h['x-approval'] || null });
  } });
  await p.goto(base + '/');
  await p.waitForSelector('#loginForm');
  await p.evaluate(async ([u, w]) => fetch('/api/auth/login', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'pharm' }, body: JSON.stringify({ username: u, password: w }) }), [user, pw]);
  await p.goto(base + '/#/audit');
  await p.waitForSelector('#approveDlg[open]');
  await p.locator('#approveDlg [name=who]').selectOption(approver);
  await p.locator('#approveDlg [name=pin]').fill(pin);
  const resp = p.waitForResponse((r) => r.url().includes('/api/audit?') && r.request().headers()['x-approval']);
  await p.locator('#approveDlg button[value=ok]').click();
  const status = (await resp).status();
  await p.waitForSelector('#auBox table');
  console.log(JSON.stringify({ status, seen }));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class ApprovalBoxInBrowser(APICase):
    def test_the_approval_box_sends_the_pin_once_and_the_action_carries_a_token(self):
        import json
        import shutil
        import socket
        import subprocess
        import threading
        import time

        import uvicorn
        approvals.clear()
        people.set_pin(self.db, self.ctx, self.ctx.user_id, "9753")
        people.add_user(self.db, self.ctx, username="asst", full_name="مساعد", password=PW, role_code="assistant")
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
            r = subprocess.run([shutil.which("node"), "-e", SCRIPT, f"http://127.0.0.1:{port}", "asst", PW,
                                self.ctx.user_id, "9753"], capture_output=True, text=True, timeout=120,
                               cwd=str(Path(__file__).parent))
        finally:
            server.should_exit = True
            t.join(10)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["status"], 200)
        with_pin = {s["path"] for s in out["seen"] if s["pin"]}
        self.assertEqual(with_pin, {"/api/approvals/check"})              # the PIN went to the check only
        self.assertTrue(any(s["path"] == "/api/audit" and s["token"] for s in out["seen"]))
        self.assertFalse(any(s["token"] and s["path"] != "/api/audit" for s in out["seen"]))


POS_SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, approver, pin, code] = process.argv.slice(1);
  const b = await chromium.launch();
  const p = await b.newPage();
  const seen = [];
  p.on('request', (r) => { if (r.url().includes('/api/')) {
    const h = r.headers();
    seen.push({ method: r.method(), path: new URL(r.url()).pathname, pin: h['x-approver-pin'] || null, token: h['x-approval'] || null });
  } });
  await p.goto(base + '/');
  await p.waitForSelector('#loginForm');
  await p.evaluate(async ([u, w]) => fetch('/api/auth/login', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'pharm' }, body: JSON.stringify({ username: u, password: w }) }), [user, pw]);
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posQ');
  await p.locator('#posQ').focus();
  await p.keyboard.type(code, { delay: 5 }); await p.keyboard.press('Enter');
  await p.waitForSelector('table.cart [data-disc]', { timeout: 10000 });
  await p.locator('table.cart [data-disc]').fill('10');                    // above his 5%
  await p.locator('table.cart [data-disc]').dispatchEvent('change');
  await p.waitForSelector('#approveDlg[open]');
  await p.locator('#approveDlg [name=who]').selectOption(approver);
  await p.locator('#approveDlg [name=pin]').fill(pin);
  const resp = p.waitForResponse((r) => r.request().method() === 'PATCH' && r.request().headers()['x-approval']);
  await p.locator('#approveDlg button[value=ok]').click();
  const status = (await resp).status();
  await p.waitForFunction(() => document.querySelector('table.cart [data-disc]').value === '10');
  console.log(JSON.stringify({ status, seen }));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class DiscountApprovalOnTheSaleScreen(APICase):
    def test_a_discount_above_the_limit_is_approved_once_with_a_token(self):
        import json
        import shutil
        import socket
        import subprocess
        import threading
        import time
        from datetime import date, timedelta
        from decimal import Decimal

        import uvicorn

        from app.catalog import service as cat
        from app.inventory import service as inv
        approvals.clear()
        people.set_pin(self.db, self.ctx, self.ctx.user_id, "9753")
        uid = people.add_user(self.db, self.ctx, username="asst", full_name="مساعد", password=PW, role_code="assistant")
        people.set_discount(self.db, self.ctx, uid, 500)
        it = cat.create_item(self.db, self.ctx, {"name_en": "Approval Item", "public_price": 1000})
        cat.add_barcode(self.db, self.ctx, it, "6221043010366")
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
            r = subprocess.run([shutil.which("node"), "-e", POS_SCRIPT, f"http://127.0.0.1:{port}", "asst", PW,
                                self.ctx.user_id, "9753", "6221043010366"], capture_output=True, text=True, timeout=120,
                               cwd=str(Path(__file__).parent))
        finally:
            server.should_exit = True
            t.join(10)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["status"], 200)
        self.assertEqual({s["path"] for s in out["seen"] if s["pin"]}, {"/api/approvals/check"})
        line = self.db.one("SELECT l.discount_bp, l.discount_approved_by FROM sale_lines l JOIN sales s ON s.id=l.sale_id"
                           " WHERE s.org_id=? AND l.item_id=?", (self.ctx.org_id, it))
        self.assertEqual((line["discount_bp"], line["discount_approved_by"]), (1000, self.ctx.user_id))
