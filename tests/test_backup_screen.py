"""F114 (owner, 19/11): a backup that failed or stopped without a word must show on the screen.

The watchdog already knew (`AutoBackup.health`), but the screen asks its own older question in the API layer
(failed, or late after a good one) and so says nothing when no backup was ever made, when the scheduler is dead
or stuck, when the newest backup file is gone from the disk, when the disk is nearly full or the off-device copy
is days old. `AutoBackup.screen()` is the one answer for the screen: the keys the screen already reads, plus the
level, the reasons and a line of text ready to show. Synthetic data only."""
from datetime import datetime, timedelta
from unittest import mock

from app.core import autobackup
from tests.test_backup_watchdog import STALE_TITLE, Usage, _Case, ago


def legacy(ab) -> dict:
    """What `backup_health()` in app/api/app.py answers today (copied as the reference for the old keys)."""
    st, cfg = ab.state(), ab.settings()
    interval = int(cfg.get("interval_min") or 60)
    times = [st.get("last_at")] + ([st.get("last_check_at")] if st.get("last_ok") else [])
    good = None
    for t in times:
        try:
            d = datetime.fromisoformat(t) if t else None
        except (TypeError, ValueError):
            d = None
        if d and d <= datetime.now() and (good is None or d > good):
            good = d
    failed = st.get("last_ok") is False
    late = good is not None and datetime.now() - good > timedelta(minutes=2 * interval)
    return {"last_good_at": good.isoformat(timespec="seconds") if good else None, "ok": st.get("last_ok"),
            "failed": failed, "late": late, "warn": failed or late, "interval_min": interval,
            "last_fail_at": st.get("last_fail_at"), "last_error": st.get("last_error") if failed else None,
            "offsite_error": st.get("offsite_error")}


class TestTheOldKeysStayAsTheyWere(_Case):
    def same(self):
        new, old = self.ab.screen(detail=True), legacy(self.ab)
        self.assertEqual({k: new[k] for k in old}, old)
        return new

    def test_after_a_good_backup(self):
        self.ab.run()
        s = self.same()
        self.assertEqual((s["warn"], s["level"], s["reasons"], s["text"], s["healthy"]), (False, "ok", [], None, True))

    def test_after_a_failed_run(self):
        self.ab.run()
        with mock.patch("app.core.backup.create", side_effect=OSError("disk full")):
            self.ab.run(force=True)
        s = self.same()
        self.assertEqual((s["warn"], s["failed"], s["level"]), (True, True, "error"))
        self.assertIn("فشلت", s["text"])
        self.assertIn(s["last_good_at"][:16].replace("T", " "), s["text"])         # the last good one is named

    def test_a_good_backup_that_is_now_late(self):
        self.ab.run()
        self.set_state(last_at=ago(hours=5), last_check_at=ago(hours=5))
        s = self.same()
        self.assertEqual((s["warn"], s["late"], s["failed"]), (True, True, False))
        self.assertIn("متأخرة", s["text"])

    def test_a_check_that_found_no_changes_keeps_it_fresh(self):
        self.ab.run()
        self.set_state(last_at=ago(hours=30), last_check_at=ago(minutes=10))
        self.assertFalse(self.same()["warn"])


class TestWhatTheScreenDidNotSee(_Case):
    def test_no_backup_was_ever_made(self):
        """The old answer: last_good_at None, late False, warn False. Nothing on the screen, for ever."""
        self.ab.started_at = datetime.now() - timedelta(hours=5)
        self.assertFalse(legacy(self.ab)["warn"])
        s = self.ab.screen()
        self.assertEqual((s["warn"], s["level"], s["reasons"]), (True, "error", ["never"]))
        self.assertIn("لم تُعمل أي نسخة احتياطية", s["text"])

    def test_the_scheduler_thread_is_dead(self):
        import threading
        self.start()
        self.set_state(last_ok=True, last_at=ago(minutes=5), last_file=None)
        dead = threading.Thread(target=lambda: None)
        dead.start()
        dead.join()
        with mock.patch.object(self.ab, "_ensure_threads"):                         # as if it could not be restarted
            self.ab.thread = dead
            self.assertFalse(legacy(self.ab)["warn"])
            s = self.ab.screen()
        self.assertEqual((s["warn"], s["level"]), (True, "error"))
        self.assertIn("scheduler_dead", s["reasons"])
        self.assertIn("متوقف", s["text"])

    def test_a_run_that_never_ends(self):
        self.start()
        self.set_state(last_ok=True, last_at=ago(minutes=5), last_file=None)
        self.ab.run_started = autobackup.time.monotonic() - autobackup.STUCK_SECONDS - 5
        self.assertFalse(legacy(self.ab)["warn"])
        s = self.ab.screen()
        self.assertIn("scheduler_stuck", s["reasons"])
        self.assertEqual(s["level"], "error")
        self.assertIn("لم تنتهِ", s["text"])

    def test_the_newest_backup_file_is_gone_from_the_disk(self):
        self.ab.run()
        name = self.ab.state()["last_file"]
        self.assertTrue(self.ab.health()["ok"])
        (self.bdir / name).unlink()                                                 # deleted by hand, or a dead disk
        self.assertFalse(legacy(self.ab)["warn"])
        s = self.ab.screen()
        self.assertEqual((s["warn"], s["level"], s["reasons"]), (True, "error", ["file_missing"]))
        self.assertIn("غير موجود", s["text"])
        h = self.ab.health()
        self.assertEqual((h["ok"], h["stale"]), (False, True))                      # the watchdog tells the owner too

    def test_a_backup_file_cut_short_counts_as_gone(self):
        self.ab.run()
        f = self.bdir / self.ab.state()["last_file"]
        f.write_bytes(f.read_bytes()[:100])
        self.assertEqual(self.ab.screen()["reasons"], ["file_missing"])

    def test_the_next_run_makes_a_new_backup_when_the_file_is_gone_although_nothing_changed(self):
        self.ab.run()
        first = self.ab.state()["last_file"]
        (self.bdir / first).unlink()
        out = self.ab.run()
        self.assertNotEqual(out.get("skipped"), "no_changes")                       # before: skipped for ever
        st = self.ab.state()
        self.assertTrue((self.bdir / st["last_file"]).is_file())
        self.assertTrue(self.ab.health()["ok"])
        self.assertEqual(self.ab.run().get("skipped"), "no_changes")                # and then it rests again

    def test_the_watchdog_tells_the_owner_about_a_missing_file(self):
        self.ab.run()
        (self.bdir / self.ab.state()["last_file"]).unlink()
        self.ab.watch_once()
        notes = self.notes(STALE_TITLE)
        self.assertEqual(len(notes), 1)
        self.assertIn("غير موجود", notes[0]["body"])
        self.assertEqual(self.audits("backup.stale")[-1]["after_json"].count("file_missing"), 1)

    def test_a_nearly_full_disk_is_a_warning_not_an_error(self):
        self.ab.run()
        with mock.patch("app.core.autobackup.shutil.disk_usage", return_value=Usage(10 ** 12, 10 ** 12, 200 * 1024 ** 2)):
            self.ab.run()
        self.assertFalse(legacy(self.ab)["warn"])
        s = self.ab.screen()
        self.assertEqual((s["warn"], s["level"], s["reasons"]), (True, "warning", ["low_disk"]))
        self.assertIn("مساحة القرص", s["text"])

    def test_an_off_device_copy_that_is_days_old(self):
        off = self.tmp / "usb"
        off.mkdir()
        self.ab.update_settings(self.ctx, {"offsite_dir": str(off)})
        self.ab.run()
        self.assertTrue(self.ab.screen()["healthy"])                                # copied just now
        self.set_state(offsite_at=ago(days=autobackup.OFFSITE_STALE_DAYS + 1), offsite_error="المجلد غير موجود")
        s = self.ab.screen()
        self.assertEqual((s["warn"], s["level"], s["reasons"]), (True, "warning", ["offsite_stale"]))
        self.assertIn("خارج الجهاز", s["text"])

    def test_no_off_device_folder_set_is_not_a_warning(self):
        self.ab.run()
        self.assertEqual(self.ab.settings().get("offsite_dir"), "")
        self.assertEqual(self.ab.screen()["reasons"], [])

    def test_a_backup_kept_without_some_attachment_files(self):
        self.ab.run()
        self.set_state(last_warning="2 attachment file(s) missing or changed; the backup holds the data without them")
        s = self.ab.screen()
        self.assertEqual((s["level"], s["reasons"]), ("warning", ["incomplete"]))
        self.assertIn("ناقصها", s["text"])


class TestWhoSeesWhat(_Case):
    def test_without_detail_no_error_text_and_no_path_leaves(self):
        self.ab.run()
        with mock.patch("app.core.backup.create", side_effect=OSError(r"C:\Users\someone\secret-folder is full")):
            self.ab.run(force=True)
        self.set_state(offsite_error=r"E:\backups: المجلد غير موجود")
        short, full = self.ab.screen(), self.ab.screen(detail=True)
        self.assertNotIn("last_error", short)
        self.assertNotIn("offsite_error", short)
        self.assertNotIn("secret-folder", str(short))
        self.assertIn("secret-folder", full["last_error"])
        self.assertEqual(full["offsite_error"], r"E:\backups: المجلد غير موجود")
        self.assertEqual((short["warn"], short["level"], short["reasons"]), (full["warn"], full["level"], full["reasons"]))

    def test_the_line_says_what_this_person_can_do(self):
        self.set_state(last_ok=False, last_at=None, last_fail_at=ago(minutes=3), last_error="x")
        owner, cashier = self.ab.screen(detail=True), self.ab.screen()
        self.assertIn("افتح «البرنامج والنسخ الاحتياطي»", owner["text"])
        self.assertIn("بلّغ صاحب الصيدلية", cashier["text"])
        self.assertNotIn("افتح", cashier["text"])

    def test_the_worst_reason_leads_the_line(self):
        self.ab.run()
        with mock.patch("app.core.backup.create", side_effect=OSError("x")):
            self.ab.run(force=True)
        self.set_state(low_disk=True, free_bytes=1)
        s = self.ab.screen()
        self.assertEqual(s["level"], "error")
        self.assertEqual(s["reasons"][0], "failed")
        self.assertTrue(s["text"].startswith("✕"))
        self.assertIn("مساحة القرص", s["text"])                                     # the second thing is still said

    def test_reading_it_touches_neither_the_database_nor_the_state(self):
        self.ab.run()
        seen = []
        self.db.conn.set_trace_callback(seen.append)
        before = (self.ab.state_path.read_bytes(), self.ab.state_path.stat().st_mtime_ns)
        try:
            for _ in range(5):
                self.ab.screen(detail=True)
        finally:
            self.db.conn.set_trace_callback(None)
        self.assertEqual(seen, [])
        self.assertEqual((self.ab.state_path.read_bytes(), self.ab.state_path.stat().st_mtime_ns), before)
