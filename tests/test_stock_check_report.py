"""#202 part 4: the owner's report «مطابقة الأرصدة» (POST /api/reports/stock-check, screen #/stock-check). It runs
the stock integrity check now, by the user who asked, shows every difference with item names and batch numbers
and the recent history of checks; it never changes a balance. Synthetic data only."""
import json
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app.catalog import service as cat
from app.core import people
from app.inventory import integrity, service as inv
from tests.helpers import OWNER_PW, needs_browser
from tests.test_api import APICase, H

PW = "Staff-Pass-2026"


class ReportCase(APICase):
    def setUp(self):
        super().setUp()
        with self.db.transaction():
            self.b1 = inv.get_or_create_batch(self.db, self.ctx, self.pan, "B1",
                                              (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=20, batch_id=self.b1,
                              movement_type="purchase", unit_cost=Decimal("15"))

    def raw(self, sql, params=()):
        c = sqlite3.connect(self.db.path)
        c.execute(sql, params)
        c.commit()
        c.close()


class TestReportApi(ReportCase):
    def test_clean(self):
        self.http_login()
        r = self.c.post("/api/reports/stock-check", json={}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertTrue(d["ok"])
        self.assertEqual((d["items_checked"], d["movements"]), (1, 1))
        self.assertEqual(d["history"], [])

    def test_mismatch_named_audited_by_the_owner_and_not_repaired(self):
        self.raw("UPDATE stock_balances SET qty = qty + 3 WHERE item_id=?", (self.pan,))
        before = [tuple(x) for x in self.db.all("SELECT * FROM stock_balances")]
        self.http_login()
        d = self.c.post("/api/reports/stock-check", json={}, headers=H).json()
        self.assertFalse(d["ok"])
        row = d["batches"][0]
        self.assertEqual((row["name"], row["batch_no"], row["ledger_qty"], row["balance_qty"]),
                         ("Panadol 500mg 24 tab", "B1", 20, 23))
        self.assertEqual(d["items"][0]["name"], "Panadol 500mg 24 tab")
        self.assertEqual([tuple(x) for x in self.db.all("SELECT * FROM stock_balances")], before)
        a = self.db.one("SELECT user_id, after_json FROM audit_log WHERE action=?", (integrity.MISMATCH,))
        self.assertEqual(a["user_id"], self.ctx.user_id)
        self.assertEqual(json.loads(a["after_json"])["reason"], "manual")
        self.assertEqual([(h["ok"], h["reason"], h["who"]) for h in d["history"]], [(False, "manual", "Owner")])
        # fixed: the next run is clean, recorded, and the history shows both
        self.raw("UPDATE stock_balances SET qty = qty - 3 WHERE item_id=?", (self.pan,))
        d = self.c.post("/api/reports/stock-check", json={}, headers=H).json()
        self.assertTrue(d["ok"])
        self.assertEqual([h["ok"] for h in d["history"]], [True, False])
        self.assertIsNone(self.c.get("/api/stock/alert", headers=H).json()["alert"])

    def test_another_pharmacys_batch_is_not_shown(self):
        org, other = self.second_tenant()
        theirs = cat.create_item(self.db, other, {"name_en": "Their item", "pack_size": 1, "public_price": 100})
        with self.db.transaction():
            secret = inv.get_or_create_batch(self.db, other, theirs, "THEIR-SECRET-LOT", "2031-01-31")
        mid = "00000000-0000-7000-8000-0000000000aa"       # damage: our movement points at their batch
        self.raw("INSERT INTO stock_movements (id, org_id, branch_id, item_id, batch_id, qty_delta, movement_type)"
                 " VALUES (?,?,?,?,?,1,'adjustment')", (mid, self.ctx.org_id, self.ctx.branch_id, self.pan, secret))
        self.raw("INSERT INTO stock_balances (org_id, branch_id, item_id, batch_key, qty) VALUES (?,?,?,?,1)",
                 (self.ctx.org_id, self.ctx.branch_id, self.pan, secret))
        self.http_login()
        r = self.c.post("/api/reports/stock-check", json={}, headers=H)
        d = r.json()
        self.assertEqual(d["counts"]["foreign_batch"], 1)
        self.assertNotIn("THEIR-SECRET-LOT", r.text)
        self.assertNotIn("2031-01-31", r.text)
        self.assertIsNone(d["foreign_batch"][0]["batch_no"])
        self.assertIsNone(d["foreign_batch"][0]["batch_item_id"])
        self.assertNotIn(theirs, r.text)

    def test_only_owner_and_manager(self):
        people.add_user(self.db, self.ctx, username="pharm1", full_name="P", password=PW, role_code="pharmacist")
        self.http_login("pharm1", PW)
        self.assertEqual(self.c.post("/api/reports/stock-check", json={}, headers=H).status_code, 403)
        self.assertEqual(self.db.one("SELECT count(*) n FROM audit_log WHERE action IN (?,?)",
                                     (integrity.MISMATCH, integrity.CLEAN))["n"], 0)

    def test_get_is_not_a_run(self):
        self.http_login()
        self.assertEqual(self.c.get("/api/reports/stock-check", headers=H).status_code, 405)

    def test_backups_stop_only_for_batch_mismatches(self):
        """Codex P2 on #213: backup.create refuses only a batch difference, so the screen says backups stopped only
        then. Here a sale moved more than its invoice: the backup still works."""
        from app.core import backup
        from app.api import app as api
        from app.sales.cash import CashDrawer
        from app.sales.service import Sales
        CashDrawer(self.db).open(self.ctx, 0)
        s = Sales(self.db)
        sid = s.create(self.ctx)
        d = s.add_line(self.ctx, sid, self.pan, units=2)
        f = s.safety_check(self.ctx, sid)
        s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions={i: "ok" for i in range(len(f))})
        self.raw("UPDATE sale_lines SET qty_base = 3 WHERE sale_id=?", (sid,))
        self.http_login()
        r = self.c.post("/api/reports/stock-check", json={}, headers=H).json()
        self.assertEqual((r["counts"]["sales"], r["counts"]["batches"]), (1, 0))
        self.assertTrue(backup.create(self.db, self.tmp / "up", self.tmp / "bk").exists())
        js = (api.WEB_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn("${r.counts.batches ? `<li>النسخ الاحتياطية بتتوقف", js)

    def test_screen_wiring(self):
        from app.api import app as api
        js = (api.WEB_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn("[/^#\\/stock-check$/, viewStockCheck]", js)
        self.assertIn('href="#/stock-check"', (api.WEB_DIR / "index.html").read_text(encoding="utf-8"))


SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw] = process.argv.slice(1);
  const b = await chromium.launch();
  const p = await (await b.newContext()).newPage();
  const out = {};
  await p.goto(base + '/');
  await p.waitForSelector('#loginForm');
  await p.fill('#loginForm [name=username]', user);
  await p.fill('#loginForm [name=password]', pw);
  await p.press('#loginForm [name=password]', 'Enter');
  await p.waitForSelector('#stockAlert:not([hidden])', { timeout: 15000 });   // banner after a normal sign-in
  out.banner = (await p.textContent('#stockAlert')).includes('مطابقة الأرصدة');
  await p.click('#stockAlert');
  await p.waitForSelector('#scBox table', { timeout: 15000 });
  out.bad = (await p.textContent('#scBox')).includes('Panadol 500mg 24 tab');
  out.badge = (await p.textContent('#scBox .badge')).includes('✖');
  await fetch(base + '/__fix');                                                // the fix happens outside the screen
  await p.click('#scRun');
  await p.waitForFunction(() => (document.querySelector('#scBox .badge') || {}).textContent?.includes('✔'), null, { timeout: 15000 });
  await p.waitForSelector('#stockAlert', { state: 'hidden', timeout: 15000 });
  out.cleared = true;
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestReportInBrowser(ReportCase):
    def test_banner_after_sign_in_opens_the_report_and_clears_when_fixed(self):
        import uvicorn
        from starlette.responses import PlainTextResponse
        from starlette.routing import Route
        self.raw("UPDATE stock_balances SET qty = qty + 3 WHERE item_id=?", (self.pan,))
        integrity.check_all(self.db, "startup")

        def fix(_req):
            self.raw("UPDATE stock_balances SET qty = qty - 3 WHERE item_id=?", (self.pan,))
            return PlainTextResponse("ok")
        self.app.router.routes.insert(0, Route("/__fix", fix))
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
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1]),
                         {"banner": True, "bad": True, "badge": True, "cleared": True})
