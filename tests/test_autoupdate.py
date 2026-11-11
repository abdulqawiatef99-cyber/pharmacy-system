"""#96 automatic updates: every path without the network or a real installer. No real data.

no update · code update (applies at startup, restarts, one notification) · installer update (sha256,
verified backup, silent installer, notification) · `update` saying only the installer can ship it ·
failed selftest -> rollback by the launcher -> failure notification, not retried · blocked during an
open sale or an open shift · only in quiet hours unless the owner allows startup/shift close or presses the
button · announced once · downloaded code must be the requested commit · channels (develop only on the
test PC, review builds pinned, never switched without the owner).
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
from unittest.mock import Mock, patch

from app.core import autoupdate, updater
from app.version import __version__
import launcher
import release_bootstrap as releases
from tests.test_api import APICase, H
from tests.test_atomic_releases import package

CUR, NEW, OLDER = "a" * 40, "b" * 40, "c" * 40
QUIET = __import__("datetime").datetime(2026, 11, 2, 4, 0)        # inside the default quiet hours 03-06
WORK = __import__("datetime").datetime(2026, 11, 1, 13, 0)        # working hours


class _Now:
    """threading.Timer stand-in: the restart runs at once; the long installer watchdog never fires."""
    def __init__(self, delay, fn):
        self.delay, self.fn = delay, fn
        self.daemon = False

    def start(self):
        if self.delay < 60:
            self.fn()


class UpdateCase(APICase):
    def setUp(self):
        super().setUp()
        self.exit = Mock()
        self.run = Mock(return_value=Mock(returncode=0, stdout="ok", stderr=""))
        self.installer = Mock()
        self.installed = {"sha": CUR, "ref": "main", "source": "installer"}
        env = mock.patch.dict(os.environ, {"PHARM_LAUNCHER": "1", "PHARM_GITHUB_TOKEN": "t",
                                           "PHARM_TEST_PC_FILE": str(self.tmp / "no-marker")})
        env.start()
        self.addCleanup(env.stop)
        self.files = ["app/sales/service.py"]
        for target, kw in ((updater, {"installed": lambda _d: self.installed,
                                      "latest_sha": lambda _r, _ref, _t: NEW,
                                      "compare": lambda _r, base, head, _t: {
                                          "files": self.files, "complete": True,
                                          "summary": "إصلاح طباعة الفاتورة"}}),
                           (autoupdate.threading, {"Timer": _Now})):
            for name, value in kw.items():
                p = patch.object(target, name, value)
                p.start()
                self.addCleanup(p.stop)
        self.u = self.agent()

    def agent(self):
        return autoupdate.AutoUpdater(self.db, self.s, root=self.tmp, run=self.run, installer=self.installer,
                                      exit_fn=self.exit, clock=lambda: QUIET)

    def notes(self):
        return [dict(r) for r in self.db.all("SELECT severity, title, body FROM notifications"
                                             " WHERE kind='update' AND org_id=?", (self.org["org_id"],))]

    def open_sale(self):
        from decimal import Decimal
        from app.inventory import service as inv
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=6, movement_type="opening",
                              unit_cost=Decimal("15"))
        self.http_login()
        self.assertEqual(self.c.post("/api/cash/open", headers=H, json={"opening_cash": 0}).status_code, 201)
        sale = self.c.post("/api/sales", headers=H, json={}).json()
        r = self.c.post(f"/api/sales/{sale['id']}/lines", headers=H, json={"item_id": self.pan, "packs": 1})
        self.assertEqual(r.status_code, 200, r.text)
        return sale

    def close_shifts(self):
        with self.db.transaction():
            self.db.execute("UPDATE cash_shifts SET status='closed', closed_at='2026-11-01T00:00:00Z'")


class TestPaths(UpdateCase):
    def test_no_update(self):
        self.installed = {"sha": NEW, "ref": "main", "source": "installer"}
        self.assertIsNone(self.u.tick())
        st = self.u.state()
        self.assertFalse(st["available"])
        self.assertEqual(self.u.apply("manual", self.ctx)["message"], "البرنامج محدّث")
        self.run.assert_not_called()
        self.exit.assert_not_called()

    def test_code_update_applies_in_quiet_hours_and_reports_one_line(self):
        r = self.u.tick()                                  # quiet hours = outside working hours
        self.assertTrue(r["ok"] and r["restarting"], r)
        args = self.run.call_args.args[0]
        self.assertEqual(args[2:], ["update", "--ref", "main", "--sha", NEW])
        self.exit.assert_called_once_with(75)              # the launcher starts the selected code
        self.installer.assert_not_called()
        self.assertTrue(self.db.one("SELECT count(*) n FROM audit_log WHERE action='system.update'")["n"])
        # after the restart (the launcher confirmed the release): one notification, version + what changed
        self.installed = {"sha": NEW, "ref": "main"}
        self.assertTrue(self.agent().finish_attempt())
        [n] = self.notes()
        self.assertEqual(n["severity"], "info")
        self.assertIn(__version__, n["title"])
        self.assertIn(NEW[:7], n["title"])
        self.assertEqual(n["body"], "إصلاح طباعة الفاتورة")
        self.assertFalse(self.u.attempt_path.exists())
        self.assertTrue(self.agent().finish_attempt())     # reported once
        self.assertEqual(len(self.notes()), 1)

    def test_waits_while_the_launcher_still_checks_the_new_release(self):
        self.u.tick()
        home = releases.code_home(self.s.data_dir)
        releases.atomic_json(home / "active-release.json",
                             {"protocol": 1, "active": {"id": "d" * 64}, "previous": None, "pending": True})
        self.assertFalse(self.agent().finish_attempt())
        self.assertEqual(self.notes(), [])

    def test_not_applied_outside_the_allowed_moments(self):
        self.u.started -= autoupdate.STARTUP_WINDOW_S + 1
        self.u.clock = lambda: __import__("datetime").datetime(2026, 11, 1, 13, 0)
        self.assertIsNone(self.u.tick())
        self.assertTrue(self.u.state()["available"])       # known, waiting for a safe moment
        self.run.assert_not_called()
        self.u.clock = lambda: __import__("datetime").datetime(2026, 11, 2, 4, 0)     # quiet hours
        self.assertTrue(self.u.tick()["restarting"])

    def test_startup_and_shift_close_in_working_hours_wait_for_the_owner(self):
        self.u.clock = lambda: WORK                        # just started, in working hours
        self.assertIsNone(self.u.tick())
        autoupdate._instance = self.u
        self.addCleanup(setattr, autoupdate, "_instance", None)
        autoupdate.trigger("shift_close")
        self.assertIsNone(self.u.tick())
        self.run.assert_not_called()
        [n] = self.notes()                                 # the owner is told once, in Arabic, what happens
        self.assertEqual(n["title"], "فيه تحديث جديد للبرنامج")
        self.assertIn("بين الساعة 03:00 و06:00", n["body"])
        self.assertIn("«حدّث الآن»", n["body"])
        self.assertIn("إصلاح طباعة الفاتورة", n["body"])
        self.u.tick()
        self.assertEqual(len(self.notes()), 1)             # announced once per version
        self.assertTrue(self.u.apply("manual", self.ctx)["restarting"])    # the owner's confirmation

    def test_owner_can_allow_startup_and_shift_close(self):
        self.u.update_settings(self.ctx, {"on_events": True})
        self.u.clock = lambda: WORK
        self.assertTrue(self.u.tick()["restarting"])       # first minutes after a start
        self.exit.reset_mock()
        self.u.attempt_path.unlink()                       # as if that restart never happened
        u = self.agent()
        u.clock = lambda: WORK
        u.started -= autoupdate.STARTUP_WINDOW_S + 1
        u.checked_once = True
        self.assertIsNone(u.tick())
        autoupdate._instance = u
        self.addCleanup(setattr, autoupdate, "_instance", None)
        autoupdate.trigger("shift_close")
        self.assertTrue(u.tick()["restarting"])

    def test_installer_update(self):
        self.files = ["requirements.txt", "app/core/updater.py"]
        exe_bytes = b"MZ synthetic installer"
        digest = hashlib.sha256(exe_bytes).hexdigest()

        def download(_url, _token, dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(exe_bytes)
            return digest
        with patch.object(updater, "installer_release", return_value={
                "sha": NEW, "ref": "main", "sha256": digest, "url": "https://api.github.com/x", "size": 1}), \
                patch.object(updater, "download_asset", side_effect=download):
            r = self.u.tick()
        self.assertTrue(r["ok"] and r["installer"], r)
        self.run.assert_not_called()                       # protocol 1 cannot ship it; no code-only try
        exe, log = self.installer.call_args.args
        self.assertEqual(exe.read_bytes(), exe_bytes)
        self.assertTrue(self.u.restarting)           # tills wait while the installer stops the program
        att = json.loads(self.u.attempt_path.read_text(encoding="utf-8"))
        self.assertEqual(att["kind"], "installer")
        backup = self.s.backup_path / att["backup"]
        from app.core import backup as bk
        self.assertEqual(bk.verify_archive(backup)["problems"], [])     # verified backup before installing
        # the installer restarted the program; the launcher adopted the new build
        self.u.restarting = False
        self.installed = {"sha": NEW, "ref": "main", "source": "installer"}
        self.agent().finish_attempt()
        [n] = self.notes()
        self.assertEqual(n["severity"], "info")

    def test_installer_that_never_starts_gives_the_tills_back(self):
        self.files = ["requirements.txt"]
        with patch.object(updater, "installer_release", return_value={
                "sha": NEW, "ref": "main", "sha256": hashlib.sha256(b"x").hexdigest(), "url": "u", "size": 1}), \
                patch.object(updater, "download_asset",
                             side_effect=lambda _u, _t, d: (d.parent.mkdir(parents=True, exist_ok=True),
                                                            d.write_bytes(b"x"), hashlib.sha256(b"x").hexdigest())[2]):
            self.assertTrue(self.u.apply("manual", self.ctx)["installer"])
        self.assertTrue(self.u.restarting)
        self.u._installer_timeout()                        # the watchdog: still running 15 minutes later
        self.assertFalse(self.u.restarting)
        [n] = self.notes()
        self.assertEqual(n["severity"], "error")
        self.assertIn("نسخة احتياطية", n["body"])

    def test_installer_with_a_wrong_sha256_is_never_run(self):
        self.files = ["app/migrations/0099_x.sql"]
        with patch.object(updater, "installer_release", return_value={
                "sha": NEW, "ref": "main", "sha256": "0" * 64, "url": "u", "size": 1}), \
                patch.object(updater, "download_asset", return_value="1" * 64):
            r = self.u.apply("manual", self.ctx)
        self.assertFalse(r["ok"])
        self.assertIn("sha256", r["message"])
        self.installer.assert_not_called()
        self.assertFalse(self.u.restarting)

    def test_installer_not_built_yet_waits(self):
        self.files = ["requirements.txt"]
        with patch.object(updater, "installer_release", return_value={
                "sha": CUR, "ref": "main", "sha256": "0" * 64, "url": "u", "size": 1}):
            r = self.u.apply("manual", self.ctx)
        self.assertFalse(r["ok"])
        self.assertEqual(self.u.state()["waiting"], "installer")
        self.installer.assert_not_called()

    def test_update_command_saying_installer_switches_to_the_installer(self):
        self.run.return_value = Mock(returncode=updater.NEEDS_INSTALLER, stdout="needs installer", stderr="")
        with patch.object(autoupdate.AutoUpdater, "_apply_installer",
                          return_value={"ok": True, "installer": True}) as inst:
            self.assertTrue(self.u.apply("manual", self.ctx)["installer"])
        inst.assert_called_once()
        self.exit.assert_not_called()

    def test_failed_selftest_rolls_back_and_is_reported_not_retried(self):
        self.assertTrue(self.u.tick()["restarting"])
        # the launcher's selftest of the selected code failed: it rolled back and wrote the outcome
        releases.atomic_json(self.s.data_dir / "update_outcome.json",
                             {"ok": False, "kind": "code", "reason": "فحص النسخة الجديدة (selftest) فشل",
                              "ts": 9e12})
        u = self.agent()
        self.assertTrue(u.finish_attempt())
        [n] = self.notes()
        self.assertEqual(n["severity"], "error")
        self.assertIn("selftest", n["body"])
        self.assertIn("رجع البرنامج للنسخة السابقة", n["body"])
        self.assertEqual(u.state()["failed_sha"], NEW)
        self.run.reset_mock()
        self.exit.reset_mock()
        self.assertIsNone(u.tick())                        # the same commit is not tried again by itself
        self.run.assert_not_called()

    def test_failed_candidate_check_changes_nothing_and_is_reported(self):
        self.run.return_value = Mock(returncode=1, stdout="Candidate selftest failed", stderr="")
        r = self.u.tick()
        self.assertFalse(r["ok"])
        self.exit.assert_not_called()
        self.assertFalse(self.u.attempt_path.exists())
        self.assertEqual(self.notes()[0]["severity"], "error")


class TestOpenSale(UpdateCase):
    def test_blocked_during_an_open_sale(self):
        self.open_sale()
        self.assertEqual(self.u.busy(), "فيه فاتورة بيع مفتوحة")
        r = self.u.apply("startup")
        self.assertTrue(r["blocked"])
        self.run.assert_not_called()
        self.exit.assert_not_called()
        api = self.c.post("/api/system/update", headers=H, json={})
        self.assertEqual(api.status_code, 409, api.text)
        self.assertEqual(api.json()["error"]["code"], "update_blocked")

    def test_recent_sales_write_blocks_too(self):
        self.u.touch_sale()
        self.assertEqual(self.u.busy(), "فيه بيع شغال دلوقتي")

    def test_old_parked_draft_does_not_block_forever(self):
        sale = self.open_sale()
        with self.db.transaction():
            self.db.execute("UPDATE sales SET created_at='2026-01-01T08:00:00.000Z' WHERE id=?", (sale["id"],))
        self.close_shifts()
        self.assertIsNone(self.u.busy())                   # kept in the database across the restart

    def test_open_shift_blocks(self):
        self.http_login()
        self.assertEqual(self.c.post("/api/cash/open", headers=H, json={"opening_cash": 0}).status_code, 201)
        self.assertEqual(self.u.busy(), "فيه وردية مفتوحة")
        r = self.u.apply("quiet_hours")
        self.assertTrue(r["blocked"])
        self.assertIn("وردية", r["message"])
        api = self.c.post("/api/system/update", headers=H, json={})
        self.assertEqual(api.status_code, 409, api.text)   # the owner's button waits for the shift too
        self.run.assert_not_called()
        self.close_shifts()
        self.assertIsNone(self.u.busy())

    def test_deferred_restart_runs_while_the_new_release_is_still_pending(self):
        """#99 review: activate() leaves the release pending until the launcher confirms it after the
        restart, so the deferred restart must not wait for finish_attempt()."""
        def update(*_a, **_k):
            home = releases.code_home(self.s.data_dir)
            releases.atomic_json(home / "active-release.json",
                                 {"protocol": 1, "active": {"id": "d" * 64}, "previous": None, "pending": True})
            return Mock(returncode=0, stdout="ok", stderr="")
        self.run.side_effect = update
        with patch.object(autoupdate.AutoUpdater, "busy", side_effect=[None, "فيه بيع شغال دلوقتي"]):
            self.assertFalse(self.u.apply("quiet_hours")["restarting"])
        self.assertTrue(self.u.restart_due)
        self.assertFalse(self.u.finish_attempt())          # pending: only the restart can settle it
        self.u.tick()
        self.exit.assert_called_once_with(75)

    def test_restart_waits_when_the_check_ran_past_quiet_hours(self):
        """#99 review: check and backup can take long; the restart re-checks the allowed moment."""
        def slow(*_a, **_k):
            self.u.clock = lambda: WORK                    # the candidate check ended after 06:00
            return Mock(returncode=0, stdout="ok", stderr="")
        self.run.side_effect = slow
        r = self.u.tick()
        self.assertFalse(r["restarting"])
        self.assertIn("وقت الشغل بدأ", r["message"])
        self.assertTrue(self.u.restart_due)
        self.u.tick()
        self.exit.assert_not_called()
        self.assertTrue(self.u.apply("manual", self.ctx)["restarting"])   # the owner's button restarts it
        self.exit.assert_called_once_with(75)

    def test_installer_not_started_when_the_download_ran_past_quiet_hours(self):
        self.files = ["requirements.txt"]
        digest = hashlib.sha256(b"x").hexdigest()

        def download(_u, _t, dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"x")
            self.u.clock = lambda: WORK
            return digest
        with patch.object(updater, "installer_release", return_value={
                "sha": NEW, "ref": "main", "sha256": digest, "url": "u", "size": 1}), \
                patch.object(updater, "download_asset", side_effect=download):
            r = self.u.tick()
        self.assertTrue(r["blocked"])
        self.installer.assert_not_called()
        self.assertFalse(self.u.restarting)
        self.assertFalse(self.u.attempt_path.exists())

    def test_shift_close_moment_survives_the_sale_cooldown(self):
        """#99 review: a shift closed right after the last sale is still used once the cooldown ends."""
        self.u.update_settings(self.ctx, {"on_events": True})
        self.u.clock = lambda: WORK
        self.u.started -= autoupdate.STARTUP_WINDOW_S + 1
        autoupdate._instance = self.u
        self.addCleanup(setattr, autoupdate, "_instance", None)
        self.u.touch_sale()
        autoupdate.trigger("shift_close")
        self.assertTrue(self.u.tick()["blocked"])
        self.u.last_sale_write -= autoupdate.SALE_WRITE_S + 1     # five minutes later
        self.assertTrue(self.u.tick()["restarting"])
        self.u.pending_since -= autoupdate.SHIFT_CLOSE_KEEP_S + 1  # a stale close is not a moment forever
        self.assertIsNone(self.u._window())

    def test_no_new_shift_while_restarting(self):
        self.http_login()
        self.app.state.autoupdate.restarting = True
        r = self.c.post("/api/cash/open", headers=H, json={"opening_cash": 0})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["error"]["code"], "updating")
        self.assertIsNone(self.db.one("SELECT id FROM cash_shifts WHERE status='open'"))
        self.app.state.autoupdate.restarting = False
        self.assertEqual(self.c.post("/api/cash/open", headers=H, json={"opening_cash": 0}).status_code, 201)
        self.assertEqual(self.app.state.autoupdate.busy(), "فيه بيع شغال دلوقتي")   # counts as activity

    def test_concurrent_state_saves_neither_fail_nor_lose_a_field(self):
        """#99 review: the scheduler and the settings page save the state at the same time."""
        import threading
        errors, start = [], threading.Barrier(8)

        def worker(i):
            try:
                start.wait()
                for n in range(40):
                    self.u._save(**{f"k{i}": n})
            except Exception as e:                     # pragma: no cover - the failure being tested
                errors.append(e)
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(errors, [])
        st = self.u.state()
        self.assertEqual({st[f"k{i}"] for i in range(8)}, {39})
        self.assertEqual(list(self.s.data_dir.glob("update_state*.tmp")), [])

    def test_deferred_restart_waits_for_quiet_hours_unless_the_owner_pressed(self):
        with patch.object(autoupdate.AutoUpdater, "busy", side_effect=[None, "فيه بيع شغال دلوقتي"]):
            self.u.apply("quiet_hours")
        self.u.clock = lambda: WORK                        # the sale ended, but it is working hours now
        self.assertIsNone(self.u.tick())
        self.exit.assert_not_called()
        self.u.clock = lambda: QUIET
        self.u.tick()
        self.exit.assert_called_once_with(75)
        self.exit.reset_mock()
        u = self.agent()
        u.clock = lambda: WORK
        with patch.object(autoupdate.AutoUpdater, "busy", side_effect=[None, "فيه بيع شغال دلوقتي"]):
            u.apply("manual", self.ctx)                    # the owner's button = his confirmation
        u.tick()
        self.exit.assert_called_once_with(75)

    def test_api_sales_writes_count_as_selling(self):
        self.http_login()
        self.c.post("/api/sales", headers=H, json={})
        self.assertEqual(self.app.state.autoupdate.busy(), "فيه بيع شغال دلوقتي")

    def test_sales_writes_wait_while_restarting(self):
        self.http_login()
        self.app.state.autoupdate.restarting = True
        r = self.c.post("/api/sales", headers=H, json={})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["error"]["code"], "updating")
        self.assertEqual(self.c.get("/api/sales").status_code, 200)     # reading still works

    def test_restart_waits_for_a_sale_that_started_meanwhile(self):
        with patch.object(autoupdate.AutoUpdater, "busy", side_effect=[None, "فيه بيع شغال دلوقتي"]):
            r = self.u.apply("startup")
        self.assertFalse(r["restarting"])
        self.assertTrue(self.u.restart_due)
        self.exit.assert_not_called()
        self.assertFalse(self.u.restarting)
        self.u.tick()                                      # the sale is over: restart now
        self.exit.assert_called_once_with(75)


class TestChannels(UpdateCase):
    def test_channel_never_changes_without_the_owner(self):
        self.installed = {"sha": CUR, "ref": "main", "source": "installer"}
        (self.tmp / "no-marker").write_text("")           # even on a test PC, main stays main
        for _ in range(3):
            self.u.tick()
        self.assertEqual(self.u.channel(), "main")
        self.assertTrue(all(c.args[0][2:5] == ["update", "--ref", "main"] for c in self.run.call_args_list))
        self.installed = {"sha": NEW, "ref": "develop", "source": "installer"}
        self.assertEqual(self.agent().channel(), "develop")     # develop stays develop
        self.assertEqual(self.db.one("SELECT count(*) n FROM audit_log WHERE action='update.settings'")["n"], 0)

    def test_pharmacy_cannot_pick_develop(self):
        from app.core.errors import ValidationFailed
        with self.assertRaises(ValidationFailed):
            self.u.update_settings(self.ctx, {"channel": "develop"})
        self.assertEqual(self.u.channel(), "main")

    def test_test_pc_can_pick_develop(self):
        (self.tmp / "no-marker").write_text("")
        self.u.update_settings(self.ctx, {"channel": "develop"})
        self.assertEqual(self.u.channel(), "develop")
        self.u.tick()
        self.assertEqual(self.run.call_args.args[0][2:5], ["update", "--ref", "develop"])
        n = self.db.one("SELECT count(*) n FROM audit_log WHERE action='update.settings'")["n"]
        self.assertEqual(n, 1)

    def test_develop_installer_follows_develop_and_review_build_is_pinned(self):
        self.installed = {"sha": CUR, "ref": "develop", "source": "installer"}
        self.assertEqual(self.u.channel(), "develop")
        self.assertTrue(self.u.develop_allowed())
        self.installed = {"sha": CUR, "ref": "claude/42-x", "source": "installer"}
        self.assertIsNone(self.u.channel())
        self.assertIsNone(self.u.tick())
        self.run.assert_not_called()

    def test_settings_api_and_validation(self):
        self.http_login()
        r = self.c.put("/api/system/update/settings", headers=H,
                       json={"auto": False, "quiet_start": 1, "quiet_end": 5, "check_hours": 6})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["settings"]["quiet_start"], 1)
        self.assertEqual(self.c.put("/api/system/update/settings", headers=H,
                                    json={"quiet_start": 25}).status_code, 422)
        self.assertIsNone(self.agent().tick())             # auto off: checked, never applied
        self.run.assert_not_called()

    def test_quiet_hours_wrap_midnight(self):
        from datetime import datetime
        self.assertTrue(autoupdate.in_quiet_hours(datetime(2026, 1, 1, 4), 3, 6))
        self.assertFalse(autoupdate.in_quiet_hours(datetime(2026, 1, 1, 6), 3, 6))
        self.assertTrue(autoupdate.in_quiet_hours(datetime(2026, 1, 1, 23), 22, 2))
        self.assertTrue(autoupdate.in_quiet_hours(datetime(2026, 1, 1, 1), 22, 2))
        self.assertFalse(autoupdate.in_quiet_hours(datetime(2026, 1, 1, 12), 22, 2))


class TestUpdaterPieces(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.base, True)

    def _zipball(self, sha, *, comment=True, top=None):
        import io
        import zipfile
        buf = io.BytesIO()
        top = top or f"owner-repo-{sha[:7]}"
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(f"{top}/manage.py", "print('x')\n")
            z.writestr(f"{top}/app/__init__.py", "")
            if comment:
                z.comment = sha.encode()
        return buf.getvalue()

    def test_downloaded_code_must_be_the_requested_commit(self):
        updater.verify_source(self._zipball(NEW), NEW)
        updater.verify_source(self._zipball(NEW, comment=False), NEW)
        for data in (self._zipball(OLDER), self._zipball(NEW, top="owner-repo-x"),
                     self._zipball(OLDER, top=f"owner-repo-{NEW[:7]}"), b"not a zip"):
            with self.assertRaises(updater.UpdateError):
                updater.verify_source(data, NEW)

    def test_damaged_download_is_refused(self):
        data = bytearray(self._zipball(NEW))
        i = data.index(b"print('x')")
        data[i] ^= 0xFF                                    # flip a byte inside a member: CRC no longer matches
        with self.assertRaises(updater.UpdateError):
            updater.verify_source(bytes(data), NEW)

    def test_update_command_checks_the_download_before_staging(self):
        import manage
        with patch.dict(os.environ, {"PHARM_DATA_DIR": str(self.base / "d"), "PHARM_GITHUB_TOKEN": "t"}), \
                patch.object(updater, "installed", return_value={"sha": CUR, "ref": "main"}), \
                patch.object(updater, "download", return_value=self._zipball(OLDER)), \
                patch.object(updater, "apply_zip") as stage:
            with self.assertRaises(SystemExit) as stop:
                manage.main(["update", "--ref", "main", "--sha", NEW])
        self.assertIn("مش للنسخة المطلوبة", str(stop.exception.code))
        stage.assert_not_called()

    def test_which_changes_need_the_installer(self):
        self.assertEqual(updater.needs_installer(["app/x.py", "docs/a.md"]), [])
        for p in ("requirements.txt", "app/migrations/0040_x.sql", "launcher.py", "release_bootstrap.py",
                  "installer/pharmacy.iss", ".github/workflows/windows-installer.yml"):
            self.assertEqual(updater.needs_installer([p, "app/x.py"]), [p])

    def test_installer_reasons_between_trees(self):
        a, b = self.base / "a", self.base / "b"
        for root in (a, b):
            (root / "app" / "migrations").mkdir(parents=True)
            (root / "launcher.py").write_text("same")
            (root / "app" / "x.py").write_text(str(root))
        self.assertEqual(updater.installer_reasons(a, b), [])
        (b / "launcher.py").write_text("changed")
        (b / "app" / "migrations" / "0002.sql").write_text("new")
        self.assertEqual(updater.installer_reasons(a, b), ["app/migrations/0002.sql", "launcher.py"])

    def test_summary_prefers_pr_titles(self):
        commits = [{"commit": {"message": "fix typo"}},
                   {"commit": {"message": "Merge pull request #95 from o/b\n\nطباعة أسرع للفاتورة"}},
                   {"commit": {"message": "Merge branch 'develop' into x"}}]
        self.assertEqual(updater.summarize(commits), "طباعة أسرع للفاتورة")
        self.assertEqual(updater.summarize([{"commit": {"message": "a\nb"}}, {"commit": {"message": "c"}}]), "a · c")

    def test_unknown_base_is_not_a_complete_diff(self):
        self.assertFalse(updater.compare("o/r", None, NEW, "t")["complete"])

    def test_installer_command_line(self):
        line = updater.installer_args(Path("C:/Users/Pharm User/AppData/x/PharmacySetup.exe"),
                                      Path("C:/Users/Pharm User/AppData/x/install.log"))
        self.assertTrue(line.startswith('start "" "'))
        for flag in ("/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RELAUNCH=1", '"/LOG='):
            self.assertIn(flag, line)
        with self.assertRaises(updater.UpdateError):
            updater.installer_args(Path('C:/a"&calc/x.exe'), Path("C:/l.log"))

    def test_asset_download_drops_the_token_on_redirect_and_returns_sha256(self):
        import io
        import urllib.error
        body = b"synthetic installer bytes"
        calls = []

        def fake_open(url, headers):
            calls.append((url, dict(headers)))
            if url.startswith("https://api.github.com"):
                raise urllib.error.HTTPError(url, 302, "Found", {"Location": "https://storage.example/x?sig=1"}, None)
            return io.BytesIO(body)
        with patch.object(updater, "_open", side_effect=fake_open):
            got = updater.download_asset("https://api.github.com/repos/o/r/releases/assets/1", "secret",
                                         self.base / "setup.exe")
        self.assertEqual(got, hashlib.sha256(body).hexdigest())
        self.assertEqual((self.base / "setup.exe").read_bytes(), body)
        self.assertIn("Authorization", calls[0][1])
        self.assertNotIn("Authorization", calls[1][1])      # the token never leaves api.github.com

    def test_release_manifest_must_match_the_published_digest(self):
        import io
        rel = {"assets": [{"name": "PharmacySetup.exe", "url": "u1", "digest": "sha256:" + "1" * 64},
                          {"name": "installer.json", "url": "u2"}]}
        man = json.dumps({"sha": NEW, "ref": "main", "sha256": "2" * 64}).encode()
        with patch.object(updater, "_json", return_value=rel), \
                patch.object(updater, "_asset", return_value=io.BytesIO(man)):
            with self.assertRaises(updater.UpdateError):
                updater.installer_release("o/r", "main", "t")
        rel["assets"][0]["digest"] = "sha256:" + "2" * 64
        with patch.object(updater, "_json", return_value=rel), \
                patch.object(updater, "_asset", return_value=io.BytesIO(man)):
            self.assertEqual(updater.installer_release("o/r", "main", "t")["sha"], NEW)


class TestInstallerWiring(unittest.TestCase):
    """What runs only on Windows/GitHub, kept from drifting (compiled by the Windows installer workflow)."""
    ROOT = Path(__file__).resolve().parent.parent

    def test_silent_install_starts_the_program_again(self):
        iss = (self.ROOT / "installer" / "pharmacy.iss").read_text(encoding="utf-8")
        code = iss[iss.index("procedure DeinitializeSetup();"):]
        self.assertIn("ExpandConstant('{param:RELAUNCH|0}') = '1'", code)
        self.assertIn("launcher.py') + '\" --no-browser'", code)
        self.assertIn("ewNoWait", code)

    def test_both_channels_publish_an_installer_with_its_sha256(self):
        wf = (self.ROOT / ".github" / "workflows" / "windows-installer.yml").read_text(encoding="utf-8")
        self.assertIn("branches: [main, develop]", wf)
        self.assertIn("group: windows-installer-${{ github.ref }}", wf)   # develop never cancels a main build
        for tag in updater.INSTALLER_TAGS.values():
            self.assertIn(f"$tag = '{tag}'", wf)
        self.assertIn("Get-FileHash $exe.FullName -Algorithm SHA256", wf)
        self.assertIn("dist/PharmacySetup.exe dist/installer.json", wf)
        self.assertIn("needs: gate", wf)                                   # only a verified merge publishes


class TestLauncherSelftest(unittest.TestCase):
    """The launcher runs selftest on a newly selected release and rolls back by itself when it fails."""
    def setUp(self):
        self.base = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.base, True)
        self.root = self.base / "program"
        self.app_home = self.base / "home"
        self.home = self.app_home / "data" / "code-releases"
        (self.root / "app" / "migrations").mkdir(parents=True)
        (self.root / "manage.py").write_text("old")
        (self.root / "requirements.txt").write_text("same")
        files = {"manage.py": "new", "app/x.py": "new", "release_bootstrap.py": "",
                 "release_protocol.json": '{"protocol":1}', "requirements.txt": "same"}
        self.staged = updater.apply_zip(package(files), self.root, code_home=self.home)
        releases.activate(self.root, self.home, self.staged["id"], {"sha": NEW}, expected_active=None)

    def start(self, selftest_ok):
        process = Mock()
        process.wait.return_value = 0
        with patch.object(launcher, "ROOT", self.root), patch.object(launcher, "home", return_value=self.app_home), \
                patch.object(launcher, "running", return_value=False), \
                patch.object(launcher, "candidate_ready", return_value=True), \
                patch.object(launcher, "selftest", return_value=selftest_ok) as st, \
                patch.object(launcher.threading, "Thread"), \
                patch.object(launcher.subprocess, "Popen", return_value=process) as spawn:
            self.assertEqual(launcher.main([]), 0)
        return st, spawn

    def test_failed_selftest_selects_previous_code(self):
        st, spawn = self.start(False)
        self.assertEqual(Path(st.call_args.args[0]), Path(self.staged["release"]))
        self.assertEqual(Path(spawn.call_args.args[0][1]).parent, self.root)    # old code serves
        self.assertIsNone(releases.read_state(self.home)["active"])
        out = json.loads((self.app_home / "data" / "update_outcome.json").read_text(encoding="utf-8"))
        self.assertFalse(out["ok"])
        self.assertIn("selftest", out["reason"])

    def test_passing_selftest_confirms(self):
        self.start(True)
        self.assertFalse(releases.read_state(self.home)["pending"])
        out = json.loads((self.app_home / "data" / "update_outcome.json").read_text(encoding="utf-8"))
        self.assertTrue(out["ok"])

    def test_no_browser_when_started_by_the_installer(self):
        with patch.object(launcher, "running", return_value=True), \
                patch.object(launcher.webbrowser, "open") as browser:
            self.assertEqual(launcher.main(["--no-browser"]), 0)
            browser.assert_not_called()
            launcher.main([])
            browser.assert_called_once()


if __name__ == "__main__":
    unittest.main()
