"""Scheduled backups go on when the PC clock moves backwards.

A last-run time in the future (the clock was wrong and then corrected, or a dead CMOS battery reset it)
used to mean "not due" until the clock caught up, with no message. It now means: due."""
from datetime import datetime, timedelta

from app.catalog import service as cat
from app.core.autobackup import AutoBackup
from tests.helpers import DBTestCase


class TestClockMovedBack(DBTestCase):
    def setUp(self):
        super().setUp()
        self.ab = AutoBackup(self.db, self.tmp / "uploads", self.tmp / "backups", self.tmp / "state")
        self.ab.run()
        self.assertFalse(self.ab._due())
        self.future = (datetime.now() + timedelta(days=400)).isoformat(timespec="seconds")

    def set_state(self, **changes):
        st = self.ab.state()
        st.update(changes)
        self.ab._write(self.ab.state_path, st)

    def test_last_backup_in_the_future_is_due(self):
        self.set_state(last_at=self.future, last_check_at=self.future)
        self.assertTrue(self.ab._due())

    def test_after_the_check_it_waits_one_interval_again(self):
        self.set_state(last_at=self.future, last_check_at=self.future)
        self.assertEqual(self.ab.run().get("skipped"), "no_changes")     # nothing changed: only a check
        self.assertFalse(self.ab._due())                                # not a backup attempt every 30 seconds
        cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab", "public_price": 4000})
        old = (datetime.now() - timedelta(minutes=61)).isoformat(timespec="seconds")
        self.set_state(last_check_at=old)
        self.assertTrue(self.ab._due())
        st = self.ab.run()
        self.assertNotIn("skipped", st)
        self.assertLessEqual(datetime.fromisoformat(st["last_at"]), datetime.now())
        self.assertFalse(self.ab._due())

    def test_failed_run_in_the_future_is_retried(self):
        self.set_state(last_ok=False, last_fail_at=self.future)
        self.assertTrue(self.ab._due())

    def test_a_normal_recent_run_is_still_not_due(self):
        self.set_state(last_at=datetime.now().isoformat(timespec="seconds"))
        self.assertFalse(self.ab._due())
