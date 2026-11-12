"""Automatic backups: schedule state, smaller format, retention, off-site copy, notifications."""
import json
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app.catalog import service as cat
from app.core import autobackup, backup, notify, people
from app.core.autobackup import AutoBackup, plan_retention
from app.core.errors import Forbidden, ValidationFailed
from app.sales.cash import CashDrawer
from tests.helpers import DBTestCase
from tests.test_api import APICase, H


def name(t: datetime, tag="-auto"):
    return Path(f"pharmacy-backup-{t:%Y%m%d-%H%M%S}{tag}.zip")


class TestRetention(DBTestCase):
    def test_last_ten_plus_daily_and_weekly(self):
        now = datetime(2026, 10, 25, 23, 0)
        files = [name(now - timedelta(minutes=15 * i)) for i in range(4 * 24 * 40)]   # every 15 min, 40 days
        keep = plan_retention(files, 10, 7, 4, today=now.date())
        newest = sorted(files, reverse=True)[:10]
        self.assertTrue(set(newest) <= keep)
        days = {autobackup._stamp(f).date() for f in keep}
        for i in range(7):
            self.assertIn(now.date() - timedelta(days=i), days)
        self.assertLessEqual(len(keep), 10 + 7 + 5)
        self.assertGreaterEqual(min(days), now.date() - timedelta(days=27))  # nothing older than ~4 weeks
        # manual / pre-update files are never candidates
        self.assertEqual(plan_retention([name(now, "")], 1, 0, 0), set())


class TestAutoBackup(DBTestCase):
    def setUp(self):
        super().setUp()
        self.bdir, self.sdir = self.tmp / "backups", self.tmp / "state"
        self.ab = AutoBackup(self.db, self.tmp / "uploads", self.bdir, self.sdir)

    def test_run_skip_force_and_format(self):
        st = self.ab.run()
        self.assertTrue(st["last_ok"], st)
        f = self.bdir / st["last_file"]
        self.assertTrue(f.name.endswith("-auto.zip"))
        with zipfile.ZipFile(f) as z:
            self.assertIn("pharmacy.db.xz", z.namelist())
        self.assertEqual(backup.verify_archive(f)["problems"], [])
        self.assertEqual(self.ab.run().get("skipped"), "no_changes")       # nothing changed
        cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab", "public_price": 4000})
        self.assertNotIn("skipped", self.ab.run())
        self.assertNotIn("skipped", self.ab.run(force=True))
        self.assertEqual(len(list(self.bdir.glob("*-auto.zip"))), 3)

    def test_restore_from_new_format(self):
        cat.create_item(self.db, self.ctx, {"name_en": "Brufen 400mg 30 tab", "public_price": 3000})
        f = self.bdir / self.ab.run()["last_file"]
        target = self.tmp / "restored" / "pharmacy.db"
        backup.restore(f, target, self.tmp / "up2", self.tmp / "pre")
        from app.db import Database
        r = Database(target)
        self.assertEqual(r.one("SELECT count(*) c FROM items")["c"], 1)
        r.close()

    def test_prune_only_touches_auto_files(self):
        self.bdir.mkdir()
        manual = self.bdir / "pharmacy-backup-20200101-000000.zip"
        manual.write_bytes(b"x")
        for i in range(15):
            (self.bdir / name(datetime(2020, 1, 1) + timedelta(minutes=i))).write_bytes(b"x")
        cfg = {**autobackup.DEFAULTS, "keep_daily": 0, "keep_weekly": 0}
        autobackup.prune(self.bdir, cfg)
        self.assertTrue(manual.exists())
        self.assertEqual(len(list(self.bdir.glob("*-auto.zip"))), 10)

    def test_offsite_copy_and_missing_drive_notifies_once(self):
        usb = self.tmp / "usb"
        usb.mkdir()
        self.ab.update_settings(self.ctx, {"offsite_dir": str(usb)})
        st = self.ab.run()
        self.assertTrue((usb / st["last_file"]).exists())
        self.assertIsNone(st["offsite_error"])
        self.ab.update_settings(self.ctx, {"offsite_dir": str(self.tmp / "unplugged")})
        st = self.ab.run(force=True)
        self.assertTrue(st["offsite_error"])
        # copy is recent (just made): no stale warning yet
        self.assertFalse(any(n["kind"] == "backup" for n in notify.listing(self.db, self.ctx)["items"]))
        state = json.loads((self.sdir / "backup_state.json").read_text(encoding="utf-8"))
        state["offsite_at"] = (datetime.now() - timedelta(days=4)).isoformat()
        (self.sdir / "backup_state.json").write_text(json.dumps(state), encoding="utf-8")
        self.ab.run(force=True)
        self.ab.run(force=True)
        kinds = [n["kind"] for n in notify.listing(self.db, self.ctx)["items"]]
        self.assertEqual(kinds.count("backup"), 1)                          # once a day, not every run
        self.assertTrue(self.ab.status(self.ctx)["offsite_stale"])

    def test_offsite_copy_is_verified_and_a_bad_copy_is_not_kept(self):
        """A failing USB stick must not leave a broken file that looks like a good backup."""
        usb = self.tmp / "usb"
        usb.mkdir()
        self.ab.update_settings(self.ctx, {"offsite_dir": str(usb)})
        real_copy = autobackup.shutil.copyfile

        def corrupting_copy(src, dst):
            real_copy(src, dst)
            with open(dst, "r+b") as f:          # one flipped byte, as a bad flash cell would do
                f.seek(100)
                b = f.read(1)
                f.seek(100)
                f.write(bytes([b[0] ^ 0xFF]))

        with patch.object(autobackup.shutil, "copyfile", side_effect=corrupting_copy):
            st = self.ab.run(force=True)
        self.assertTrue(st["last_ok"])                       # the backup on the PC itself is fine
        self.assertIn("تطابق", st["offsite_error"])
        self.assertEqual(list(usb.iterdir()), [])            # neither the bad copy nor a .part remains
        st = self.ab.run(force=True)                         # a healthy drive works again
        self.assertIsNone(st["offsite_error"])
        self.assertEqual(backup._sha256(usb / st["last_file"]), backup._sha256(self.bdir / st["last_file"]))

    def test_settings_validation_and_owner_only(self):
        for bad in ({"interval_min": 45}, {"keep_last": 0}, {"offsite_dir": "relative/path"},
                    {"offsite_dir": str(self.bdir)}):
            with self.assertRaises(ValidationFailed):
                self.ab.update_settings(self.ctx, bad)
        self.assertEqual(self.ab.update_settings(self.ctx, {"interval_min": 15})["interval_min"], 15)
        people.add_user(self.db, self.ctx, username="mgr", full_name="M", password="Staff-Pass-2026",
                        role_code="manager")
        with self.assertRaises(Forbidden):
            self.ab.update_settings(self.login("mgr", "Staff-Pass-2026"), {"interval_min": 60})

    def test_due_and_shift_close_trigger(self):
        self.assertTrue(self.ab._due())
        self.ab.run()
        self.assertFalse(self.ab._due())
        autobackup._instance = self.ab
        try:
            cd = CashDrawer(self.db)
            cd.open(self.ctx, 0)
            cd.close(self.ctx, 0)
            self.assertTrue(self.ab.wake.is_set())
            self.assertEqual(self.ab.pending_reason, "shift_close")
        finally:
            autobackup._instance = None


class TestBackupAPI(APICase):
    def test_status_and_run_now(self):
        self.http_login()
        r = self.c.put("/api/backup", json={"interval_min": 30, "keep_last": 12}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["settings"]["interval_min"], 30)
        r = self.c.post("/api/backup/run", json={}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["state"]["last_ok"])
        self.assertEqual(len(r.json()["files"]), 1)


class TestAutoBackupFailures(DBTestCase):
    """A failed backup never takes the program down, tells the owner once (not every attempt), and is
    retried after one interval; settings refuse what they cannot honour."""

    def setUp(self):
        super().setUp()
        self.bdir, self.sdir = self.tmp / "backups", self.tmp / "state"
        self.ab = AutoBackup(self.db, self.tmp / "uploads", self.bdir, self.sdir)

    def fails(self):
        from app.core import notify
        return [n for n in notify.listing(self.db, self.ctx)["items"] if n["title"] == "فشلت النسخة الاحتياطية التلقائية"]

    def test_a_failure_is_recorded_and_told_once_then_cleared_by_a_success(self):
        from unittest import mock
        with mock.patch("app.core.backup.create", side_effect=OSError("disk full")), \
                self.assertLogs("pharm.backup", level="ERROR"):
            st = self.ab.run()
            self.assertEqual((st["last_ok"], st["last_error"]), (False, "disk full"))
            self.ab.run(force=True)                                   # fails again within 6 hours
        self.assertEqual(len(self.fails()), 1)
        st = self.ab.run(force=True)
        self.assertTrue(st["last_ok"])
        self.assertIsNone(st["last_error"])
        self.assertIsNone(st.get("fail_notified"))                    # the next failure is told again

    def test_a_backup_that_fails_its_own_check_is_deleted(self):
        from unittest import mock
        with mock.patch("app.core.backup.quick_check", return_value=["damaged archive"]), \
                self.assertLogs("pharm.backup", level="ERROR"):
            st = self.ab.run()
        self.assertFalse(st["last_ok"])
        self.assertIn("damaged archive", st["last_error"])
        self.assertEqual(list(self.bdir.glob("*.zip")), [])           # no half-good file left to restore from

    def test_when_a_backup_is_due(self):
        from datetime import datetime, timedelta
        self.assertTrue(self.ab._due())                               # never ran
        self.ab.run()
        self.assertFalse(self.ab._due())
        st = self.ab.state()
        old = (datetime.now() - timedelta(minutes=61)).isoformat(timespec="seconds")
        st.update(last_at=old, last_check_at=old)
        self.ab._write(self.ab.state_path, st)
        self.assertTrue(self.ab._due())                               # default interval: 60 minutes
        st.update(last_ok=False, last_fail_at=datetime.now().isoformat(timespec="seconds"))
        self.ab._write(self.ab.state_path, st)
        self.assertFalse(self.ab._due())                              # a failure is retried after one interval

    def test_settings_refuse_what_they_cannot_honour(self):
        before = self.ab.settings()
        for changes in ({"interval_min": 7}, {"keep_last": "x"}, {"keep_last": 0}, {"keep_last": 101},
                        {"keep_daily": -1}, {"keep_weekly": 53}, {"keep_last": 2.5}, {"keep_last": True},
                        {"offsite_dir": "relative/folder"}, {"offsite_dir": str(self.bdir)}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValidationFailed):
                    self.ab.update_settings(self.ctx, changes)
                self.assertEqual(self.ab.settings(), before)
        new = self.ab.update_settings(self.ctx, {"keep_last": 5, "on_shift_close": False, "offsite_dir": ""})
        self.assertEqual((new["keep_last"], new["on_shift_close"], new["offsite_dir"]), (5, False, ""))
