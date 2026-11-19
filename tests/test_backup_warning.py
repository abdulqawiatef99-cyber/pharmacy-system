"""Night queue C (19/11): «آخر نسخة احتياطية» in the top bar and on the home screen, red when the last automatic
backup failed or the last good one is older than two intervals. A run that found no changes counts as good.
Synthetic data only."""
import unittest
from datetime import datetime, timedelta

from app.core.autobackup import AutoBackup
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import APICase, H


class BackupHealth(APICase):
    def setUp(self):
        super().setUp()
        self.ab = AutoBackup(self.db, self.s.upload_dir, self.s.backup_path, self.s.data_dir)
        self.http_login()

    def state(self, **st):
        self.ab._write(self.ab.state_path, st)
        return self.c.get("/api/backup/health", headers=H).json()

    def ago(self, minutes):
        return (datetime.now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")

    def test_fresh_late_failed_and_no_changes(self):
        h = self.state(last_ok=True, last_at=self.ago(30))
        self.assertEqual((h["warn"], h["late"], h["failed"]), (False, False, False))
        h = self.state(last_ok=True, last_at=self.ago(3 * 60))                # default interval 60: late
        self.assertEqual((h["warn"], h["late"]), (True, True))
        h = self.state(last_ok=True, last_at=self.ago(3 * 60), last_check_at=self.ago(10))   # no changes since: fine
        self.assertFalse(h["warn"])
        h = self.state(last_ok=False, last_at=self.ago(20), last_fail_at=self.ago(1), last_error="disk full")
        self.assertEqual((h["warn"], h["failed"], h["last_error"]), (True, True, "disk full"))
        home = self.c.get("/api/home", headers=H).json()["backup"]
        self.assertTrue(home["warn"] and home["failed"])

    def test_a_clock_moved_back_never_hides_a_late_backup(self):
        future = (datetime.now() + timedelta(days=2)).isoformat(timespec="seconds")
        h = self.state(last_ok=True, last_at=future)
        self.assertIsNone(h["last_good_at"])

    def test_only_the_owner_sees_it(self):
        from app.core import people
        people.add_user(self.db, self.ctx, username="cashier1", full_name="C", password="Staff-Pass-2026", role_code="assistant")
        self.state(last_ok=False)
        self.http_login("cashier1", "Staff-Pass-2026")
        self.assertEqual(self.c.get("/api/backup/health", headers=H).status_code, 403)


SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.goto(base + '/#/home');
  await p.waitForSelector('#backupAlert:not([hidden])');
  const out = { bar: await p.locator('#backupAlert').innerText(), when: await p.locator('#backupWhen').innerText(),
                badges: [await p.locator('#bkBadge').isVisible(), !(await p.locator('#bkBadgeSys').getAttribute('hidden'))] };
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class BackupWarningScreen(APICase):
    def test_red_bar_and_the_top_bar_time(self):
        ab = AutoBackup(self.db, self.s.upload_dir, self.s.backup_path, self.s.data_dir)
        ab._write(ab.state_path, {"last_ok": False, "last_at": "2026-11-18T23:00:00", "last_fail_at": "2026-11-19T01:00:00"})
        out = run_node(self, SCRIPT, "owner", OWNER_PW)
        self.assertIn("فشلت", out["bar"])
        self.assertIn("آخر نسخة احتياطية", out["when"])
        self.assertEqual(out["badges"], [True, True])                # also on «المزيد» and on the system link


SWITCH = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw] = process.argv.slice(1);
""" + LOGIN.replace("[user, pw]", "['cashier1', 'Staff-Pass-2026']") + r"""
  await p.goto(base + '/#/home');
  await p.waitForSelector('#logout');
  await p.waitForTimeout(300);
  const asCashier = await p.locator('#backupAlert').isHidden();
  await p.click('#logout');
  await p.waitForSelector('#loginForm');
  await p.evaluate(async ([u, w]) => fetch('/api/auth/login', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'pharm' }, body: JSON.stringify({ username: u, password: w }) }), [user, pw]);
  await p.goto(base + '/#/home');
  await p.waitForSelector('#backupAlert:not([hidden])', { timeout: 5000 });   // now, not at the next 5-minute tick
  console.log(JSON.stringify({ asCashier, bar: await p.locator('#backupAlert').innerText() }));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class BackupWarningAfterAnotherLogin(APICase):
    def test_the_owner_sees_it_at_once_after_a_cashier_logged_out_in_the_same_tab(self):
        """#351 Codex P2."""
        from app.core import people
        people.add_user(self.db, self.ctx, username="cashier1", full_name="C", password="Staff-Pass-2026", role_code="assistant")
        ab = AutoBackup(self.db, self.s.upload_dir, self.s.backup_path, self.s.data_dir)
        ab._write(ab.state_path, {"last_ok": False, "last_at": "2026-11-18T23:00:00", "last_fail_at": "2026-11-19T01:00:00"})
        out = run_node(self, SWITCH, "owner", OWNER_PW)
        self.assertTrue(out["asCashier"])
        self.assertIn("فشلت", out["bar"])


if __name__ == "__main__":
    unittest.main()
