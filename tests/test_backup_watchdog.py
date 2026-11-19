"""A backup that can never stop silently (owner, 18/11: the old program's hourly backup stopped for 35 hours
without a word). The watchdog does not depend on the scheduler thread: it reports the last success, the
last failure, the age and a stale flag; restarts a dead scheduler; tells the owner once a day while the
backup is stale; warns when the disk is low before a run; and writes each of these in the audit log.
Also #324 X27: the off-device copy is retried when the drive comes back. Synthetic data only."""
import json
import threading
import time
from collections import namedtuple
from datetime import datetime, timedelta
from unittest import mock

from app.core import autobackup, notify
from app.core.autobackup import AutoBackup
from tests.helpers import DBTestCase

Usage = namedtuple("Usage", "total used free")
STALE_TITLE = "النسخ الاحتياطي متوقف"
DISK_TITLE = "مساحة القرص قليلة للنسخ الاحتياطي"


def ago(**kw) -> str:
    return (datetime.now() - timedelta(**kw)).isoformat(timespec="seconds")


class _Case(DBTestCase):
    def setUp(self):
        super().setUp()
        self.bdir, self.sdir = self.tmp / "backups", self.tmp / "state"
        self.ab = AutoBackup(self.db, self.tmp / "uploads", self.bdir, self.sdir)

    def tearDown(self):
        if self.ab._started:
            self.ab.run = lambda reason="manual": {}
            self.ab.stop()
        autobackup._instance = None
        super().tearDown()

    def set_state(self, **kw):
        st = self.ab.state()
        st.update(kw)
        self.ab._write(self.ab.state_path, st)

    def notes(self, title):
        return [n for n in notify.listing(self.db, self.ctx)["items"] if n["title"] == title]

    def audits(self, action):
        return [dict(r) for r in self.db.all("SELECT * FROM audit_log WHERE org_id=? AND action=? ORDER BY seq",
                                             (self.ctx.org_id, action))]

    def start(self):
        """Start the threads without the 20 s start delay and without real backups."""
        self.runs = []
        self.ab.stop_event.wait = lambda timeout=None: self.ab.stop_event.is_set()
        self.ab.run = lambda reason="manual": self.runs.append(reason) or {}
        with mock.patch.object(self.ab, "_due", return_value=False):
            self.ab.start()

    def until(self, cond, seconds=5):
        end = time.monotonic() + seconds
        while time.monotonic() < end and not cond():
            time.sleep(0.01)
        return cond()


class TestHealth(_Case):
    def test_after_a_backup_everything_is_fine(self):
        self.ab.run()
        h = self.ab.health()
        self.assertEqual((h["ok"], h["level"], h["stale"], h["reasons"]), (True, "ok", False, []))
        self.assertEqual(h["last_good_at"], self.ab.state()["last_at"])
        self.assertEqual(h["limit_min"], 120)
        self.assertLessEqual(h["age_min"], 1)
        self.assertEqual(h["scheduler"], "stopped")                 # nobody started it in this test

    def test_last_success_older_than_two_intervals_is_stale(self):
        self.ab.run()
        self.set_state(last_at=ago(minutes=121), last_check_at=None)
        h = self.ab.health()
        self.assertEqual((h["ok"], h["level"], h["stale"], h["reasons"]), (False, "error", True, ["stale"]))
        self.assertGreaterEqual(h["age_min"], 121)
        self.set_state(last_at=ago(minutes=119))
        self.assertFalse(self.ab.health()["stale"])

    def test_the_interval_setting_moves_the_limit(self):
        self.ab.run()
        self.ab.update_settings(self.ctx, {"interval_min": 15})
        self.set_state(last_at=ago(minutes=31), last_check_at=None)
        h = self.ab.health()
        self.assertEqual((h["limit_min"], h["stale"]), (30, True))

    def test_a_no_changes_check_keeps_it_fresh_while_the_last_backup_was_good(self):
        self.ab.run()
        self.set_state(last_at=ago(hours=30), last_check_at=ago(minutes=10))
        h = self.ab.health()
        self.assertEqual((h["stale"], h["last_good_at"][:10]), (False, ago(hours=30)[:10]))
        self.assertEqual(h["last_fresh_at"], self.ab.state()["last_check_at"])
        self.set_state(last_ok=False, last_fail_at=ago(minutes=5), last_error="disk full")
        h = self.ab.health()                                        # a check after a failure proves nothing
        self.assertEqual((h["stale"], sorted(h["reasons"])), (True, ["failed", "stale"]))

    def test_one_failed_run_is_reported_but_is_not_yet_stale(self):
        self.ab.run()
        self.set_state(last_ok=False, last_fail_at=ago(minutes=1), last_error="disk full")
        h = self.ab.health()
        self.assertEqual((h["ok"], h["level"], h["stale"], h["reasons"]), (False, "error", False, ["failed"]))
        self.assertEqual(h["last_fail_at"], self.ab.state()["last_fail_at"])

    def test_times_in_the_future_prove_nothing(self):
        """The PC clock moved back: a 'last backup' from tomorrow is not a fresh backup."""
        self.ab.run()
        self.set_state(last_at=(datetime.now() + timedelta(days=1)).isoformat(timespec="seconds"), last_check_at=None)
        h = self.ab.health()
        self.assertIsNone(h["last_fresh_at"])
        self.assertIsNone(h["age_min"])

    def test_never_backed_up_is_stale_only_after_two_intervals_of_running(self):
        self.start()
        self.assertEqual(self.ab.health()["reasons"], [])
        self.ab.started_at = datetime.now() - timedelta(minutes=121)
        h = self.ab.health()
        self.assertEqual((h["stale"], h["reasons"]), (True, ["never"]))

    def test_the_summary_for_everyone_has_no_error_text_and_the_owners_has(self):
        self.ab.run()
        self.set_state(last_ok=False, last_fail_at=ago(minutes=1), last_error="C:\\secret\\path is full")
        self.assertNotIn("last_error", self.ab.health())
        st = self.ab.status(self.ctx)
        self.assertEqual(st["health"]["last_error"], "C:\\secret\\path is full")
        self.assertEqual(st["state"]["last_error"], "C:\\secret\\path is full")      # existing fields stay
        for k in ("last_ok", "last_at", "last_fail_at"):
            self.assertIn(k, st["state"])
        self.assertEqual(st["settings"]["interval_min"], 60)

    def test_a_hand_edited_state_or_settings_file_never_breaks_the_summary(self):
        self.sdir.mkdir(parents=True, exist_ok=True)
        self.ab.state_path.write_text(json.dumps({"last_ok": True, "last_at": "garbage", "last_check_at": 5}), encoding="utf-8")
        self.ab.cfg_path.write_text(json.dumps({"interval_min": "soon"}), encoding="utf-8")
        h = self.ab.health()
        self.assertEqual((h["limit_min"], h["last_fresh_at"]), (120, None))


class TestSchedulerWatch(_Case):
    def test_a_dead_scheduler_thread_is_restarted_recorded_and_told_once(self):
        self.start()
        dead = threading.Thread(target=lambda: None)
        dead.start()
        dead.join()
        self.ab.thread = dead                                       # the scheduler thread died
        self.ab.watch_once()
        self.assertTrue(self.ab.thread.is_alive())
        self.assertIsNot(self.ab.thread, dead)
        self.assertEqual(len(self.audits("backup.scheduler_restart")), 1)
        self.assertEqual(len(self.notes("توقف جدول النسخ الاحتياطي وأُعيد تشغيله")), 1)
        h = self.ab.health()
        self.assertEqual((h["scheduler"], h["scheduler_restarts"]), ("running", 1))
        autobackup.trigger("after_restart")                         # and it works again
        self.assertTrue(self.until(lambda: "after_restart" in self.runs), self.runs)

    def test_reading_the_summary_restarts_it_too(self):
        """A third path: the top bar polls the summary, so even with both threads gone it heals."""
        self.start()
        self.ab.watch_stop.set()
        self.ab.watch_thread.join(timeout=5)
        self.ab.watch_stop.clear()
        dead = threading.Thread(target=lambda: None)
        dead.start()
        dead.join()
        self.ab.thread = dead
        h = self.ab.health()
        self.assertTrue(self.ab.thread.is_alive() and self.ab.watch_thread.is_alive())
        self.assertEqual(h["scheduler"], "running")

    def test_a_scheduler_stuck_inside_a_run_is_reported_without_waiting_for_it(self):
        self.start()
        self.ab.run_started = time.monotonic() - autobackup.STUCK_SECONDS - 1
        got = []
        with self.ab.lock:                                          # the stuck run holds the backup lock
            t = threading.Thread(target=lambda: got.append(self.ab.watch_once()))
            t.start()
            t.join(timeout=5)
            self.assertFalse(t.is_alive(), "the watchdog waited for the backup lock")
        self.assertEqual((got[0]["scheduler"], got[0]["stale"]), ("stuck", True))
        self.assertIn("scheduler_stuck", got[0]["reasons"])
        self.assertEqual(len(self.notes(STALE_TITLE)), 1)

    def test_the_watchdog_thread_checks_by_itself_and_survives_a_bad_check(self):
        calls = []

        def check():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return {}
        self.ab.watch_once = check
        with mock.patch.object(autobackup, "WATCH_SECONDS", 0.01), self.assertLogs("pharm.backup", level="ERROR"):
            self.start()
            self.assertTrue(self.until(lambda: len(calls) >= 3), calls)
        self.assertTrue(self.ab.watch_thread.is_alive())

    def test_stop_ends_both_threads(self):
        self.start()
        sched, watch = self.ab.thread, self.ab.watch_thread
        self.ab.stop()
        self.assertFalse(sched.is_alive() or watch.is_alive())
        self.assertEqual(self.ab.health()["scheduler"], "stopped")
        self.assertFalse(self.ab.thread.is_alive())                 # reading the summary did not restart it


class TestStaleAlert(_Case):
    def stale(self):
        self.ab.run()
        self.set_state(last_at=ago(hours=35), last_check_at=None)

    def test_one_notification_a_day_while_stale_each_in_the_audit_log(self):
        self.stale()
        self.ab.watch_once()
        self.ab.watch_once()
        self.assertEqual(len(self.notes(STALE_TITLE)), 1)
        n = self.notes(STALE_TITLE)[0]
        self.assertEqual(n["severity"], "error")
        self.assertIn("35", n["body"])                              # hours since the last good backup
        self.assertEqual(len(self.audits("backup.stale")), 1)
        w = json.loads(self.ab.watch_path.read_text(encoding="utf-8"))
        w["stale_notified"] = ago(hours=25)
        self.ab._write(self.ab.watch_path, w)
        self.ab.watch_once()
        self.assertEqual(len(self.notes(STALE_TITLE)), 2)
        self.assertEqual(len(self.audits("backup.stale")), 2)

    def test_recovery_is_recorded_and_the_next_stop_is_told_again(self):
        self.stale()
        self.ab.watch_once()
        self.ab.run(force=True)
        self.ab.watch_once()
        self.assertEqual(len(self.audits("backup.recovered")), 1)
        self.set_state(last_at=ago(hours=5), last_check_at=None)
        self.ab.watch_once()                                        # stale again the same day: told again
        self.assertEqual(len(self.notes(STALE_TITLE)), 2)

    def test_a_healthy_backup_writes_nothing(self):
        self.ab.run()
        before = self.db.one("SELECT count(*) n FROM audit_log")["n"]
        self.ab.watch_once()
        self.assertEqual(self.db.one("SELECT count(*) n FROM audit_log")["n"], before)
        self.assertEqual(self.notes(STALE_TITLE), [])

    def test_a_failure_and_its_recovery_are_in_the_audit_log(self):
        with mock.patch("app.core.backup.create", side_effect=OSError("disk full")), \
                self.assertLogs("pharm.backup", level="ERROR"):
            self.ab.run()
            self.ab.run(force=True)
        self.assertEqual(len(self.audits("backup.failed")), 1)       # with its notification, once in 6 hours
        self.assertIn("disk full", self.audits("backup.failed")[0]["after_json"])
        self.ab.run(force=True)
        self.assertEqual(len(self.audits("backup.recovered")), 1)

    def test_its_own_records_do_not_make_the_next_check_back_up_again(self):
        """The failure notice and audit rows are writes too; counted as 'changes' they forced one more
        backup after every warning."""
        with mock.patch("app.core.backup.create", side_effect=OSError("disk full")), \
                self.assertLogs("pharm.backup", level="ERROR"):
            self.ab.run()
        self.ab.run()                                               # succeeds, records the recovery
        self.assertEqual(self.ab.run().get("skipped"), "no_changes")


class TestDiskSpace(_Case):
    def test_low_space_before_a_run_warns_once_a_day_and_is_recorded(self):
        with mock.patch.object(autobackup.shutil, "disk_usage", return_value=Usage(10 ** 12, 10 ** 12, 200 * 1024 ** 2)):
            st = self.ab.run()
            self.ab.run(force=True)
        self.assertTrue(st["last_ok"])                              # the backup is still attempted
        self.assertEqual((st["low_disk"], st["free_bytes"]), (True, 200 * 1024 ** 2))
        self.assertEqual(len(self.notes(DISK_TITLE)), 1)
        self.assertEqual(self.notes(DISK_TITLE)[0]["severity"], "warning")
        self.assertEqual(len(self.audits("backup.low_disk")), 1)
        h = self.ab.health()
        self.assertEqual((h["ok"], h["level"], h["low_disk"], h["reasons"], h["stale"]), (False, "warning", True, ["low_disk"], False))

    def test_enough_space_is_quiet_and_clears_the_flag(self):
        with mock.patch.object(autobackup.shutil, "disk_usage", return_value=Usage(10 ** 12, 10 ** 12, 200 * 1024 ** 2)):
            self.ab.run()
        with mock.patch.object(autobackup.shutil, "disk_usage", return_value=Usage(10 ** 12, 0, 50 * 1024 ** 3)):
            st = self.ab.run(force=True)
        self.assertEqual((st["low_disk"], st["free_bytes"]), (False, 50 * 1024 ** 3))
        self.assertTrue(self.ab.health()["ok"])

    def test_the_warning_clears_when_space_is_freed_even_if_nothing_changed(self):
        """Codex P2 on #350: the "nothing changed" path returned before the disk was measured, so the badge
        stayed on after the owner freed space, until the next sale."""
        with mock.patch.object(autobackup.shutil, "disk_usage", return_value=Usage(10 ** 12, 10 ** 12, 200 * 1024 ** 2)):
            self.assertTrue(self.ab.run()["low_disk"])
        with mock.patch.object(autobackup.shutil, "disk_usage", return_value=Usage(10 ** 12, 0, 50 * 1024 ** 3)):
            st = self.ab.run()
        self.assertEqual(st.get("skipped"), "no_changes")
        self.assertEqual((st["low_disk"], st["free_bytes"]), (False, 50 * 1024 ** 3))
        self.assertTrue(self.ab.health()["ok"])

    def test_space_running_low_between_backups_is_noticed_without_forcing_a_backup(self):
        self.ab.run()
        with mock.patch.object(autobackup.shutil, "disk_usage", return_value=Usage(10 ** 12, 10 ** 12, 200 * 1024 ** 2)):
            first = self.ab.run()
            again = self.ab.run()
        self.assertEqual((first.get("skipped"), again.get("skipped")), ("no_changes", "no_changes"))
        self.assertTrue(again["low_disk"])
        self.assertEqual(len(self.notes(DISK_TITLE)), 1)
        self.assertEqual(len(self.audits("backup.low_disk")), 1)

    def test_space_is_measured_against_the_data_size(self):
        """Plenty for a small pharmacy is not enough for a large database: twice (database + last backup)."""
        self.set_state(last_bytes=3 * 1024 ** 3)
        with mock.patch.object(autobackup.shutil, "disk_usage", return_value=Usage(10 ** 12, 0, 5 * 1024 ** 3)):
            self.assertTrue(self.ab.run()["low_disk"])

    def test_a_disk_that_cannot_be_measured_does_not_stop_the_backup(self):
        with mock.patch.object(autobackup.shutil, "disk_usage", side_effect=OSError("no such drive")):
            st = self.ab.run()
        self.assertEqual((st["last_ok"], st["low_disk"], st["free_bytes"]), (True, False, None))


class TestX27OffsiteCatchUp(_Case):
    def test_the_copy_is_made_when_the_drive_comes_back_with_nothing_changed(self):
        usb = self.tmp / "usb"
        self.ab.update_settings(self.ctx, {"offsite_dir": str(usb)})
        st = self.ab.run()                                          # drive not connected
        self.assertTrue(st["last_ok"] and st["offsite_error"])
        usb.mkdir()
        again = self.ab.run()                                       # nothing changed since
        self.assertEqual(again.get("skipped"), "no_changes")
        self.assertTrue((usb / st["last_file"]).is_file())
        self.assertIsNone(self.ab.state()["offsite_error"])
        self.assertTrue(self.ab.state()["offsite_at"])

    def test_a_copy_already_there_is_not_made_again(self):
        usb = self.tmp / "usb"
        usb.mkdir()
        self.ab.update_settings(self.ctx, {"offsite_dir": str(usb)})
        st = self.ab.run()
        first = (usb / st["last_file"]).stat().st_mtime_ns
        with mock.patch.object(autobackup.shutil, "copyfile", side_effect=AssertionError("copied again")):
            self.ab.run()
        self.assertEqual((usb / st["last_file"]).stat().st_mtime_ns, first)


if __name__ == "__main__":
    import unittest
    unittest.main()
