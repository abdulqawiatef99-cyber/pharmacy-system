"""The hourly backup must not make the cashier wait (F59 / #304: scans waited 1.3-2.5 s during a backup on the
test PC). Measured here on a synthetic medium pharmacy (1 GB database): one write in every backup waited about
a second, at the moment the temporary snapshot (a full copy of the database) was flushed to disk in one go.
The snapshot is now copied in small steps, each flushed by itself, under one read snapshot so sales made
meanwhile neither get into the copy nor restart it. Synthetic data only."""
import sqlite3
from unittest import mock

from app.catalog import service as cat
from app.core import backup
from app.core.autobackup import AutoBackup
from tests.helpers import DBTestCase


class TestSnapshotCopy(DBTestCase):
    def fill(self, n=200, start=0):
        with self.db.transaction():
            for i in range(start, start + n):
                cat.create_item(self.db, self.ctx, {"name_en": f"Filler item number {i:05d} " + "x" * 300,
                                                    "pack_size": 1, "public_price": 100}, force=True)

    def items(self, path):
        c = sqlite3.connect(str(path))
        try:
            return c.execute("SELECT count(*) FROM items").fetchone()[0], c.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            c.close()

    def test_the_copy_is_made_in_steps_each_flushed(self):
        self.fill()
        steps, flushed = [], []
        with mock.patch.object(backup.os, "fsync", side_effect=lambda fd: flushed.append(fd)):
            backup.snapshot(self.db, self.tmp / "copy.db", pages=16, on_step=lambda remaining, total: steps.append(remaining))
        self.assertGreater(len(steps), 5)
        self.assertEqual(steps, sorted(steps, reverse=True))        # never restarted
        self.assertEqual(steps[-1], 0)
        self.assertGreaterEqual(len(flushed), len(steps))
        self.assertEqual(self.items(self.tmp / "copy.db"), (200, "ok"))

    def test_sales_made_during_the_copy_do_not_restart_it_and_are_not_in_it(self):
        """A stepped SQLite backup starts again from the first page whenever another connection writes; under
        one read snapshot it does not. Checked with a write between every two steps."""
        self.fill()
        other = sqlite3.connect(self.db.path, isolation_level=None)
        steps = []

        def write(remaining, total):
            steps.append(remaining)
            self.assertLess(len(steps), 500, "the copy keeps restarting")
            other.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?,?,?)",
                          (self.ctx.org_id, f"probe-{len(steps)}", "x" * 2000))
        try:
            backup.snapshot(self.db, self.tmp / "copy.db", pages=16, on_step=write)
        finally:
            other.close()
        self.assertEqual(steps, sorted(steps, reverse=True))
        c = sqlite3.connect(str(self.tmp / "copy.db"))
        self.assertEqual(c.execute("SELECT count(*) FROM org_settings WHERE key LIKE 'probe-%'").fetchone()[0], 0)
        self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        c.close()
        self.assertGreater(self.db.one("SELECT count(*) n FROM org_settings WHERE key LIKE 'probe-%'")["n"], 5)

    def test_the_connection_is_left_free_also_after_a_failure(self):
        self.fill(20)

        def boom(remaining, total):
            raise RuntimeError("disk gone")
        with self.assertRaises(RuntimeError):
            backup.snapshot(self.db, self.tmp / "copy.db", pages=4, on_step=boom)
        self.assertFalse(self.db.conn.in_transaction)
        self.fill(1, start=20)                                      # the program still writes
        backup.snapshot(self.db, self.tmp / "copy2.db")
        self.assertEqual(self.items(self.tmp / "copy2.db"), (21, "ok"))

    def test_inside_a_read_snapshot_it_copies_in_one_step(self):
        """A caller that already holds a snapshot on this connection cannot be given a second one."""
        self.fill(20)
        with self.db.read_snapshot():
            with mock.patch.object(type(self.db), "backup_to", wraps=self.db.backup_to) as one_step:
                backup.snapshot(self.db, self.tmp / "copy.db")
            self.assertTrue(one_step.called)
            self.assertTrue(self.db.conn.in_transaction)
        self.assertEqual(self.items(self.tmp / "copy.db"), (20, "ok"))

    def test_a_flush_that_fails_does_not_stop_the_backup(self):
        self.fill(50)
        with mock.patch.object(backup.os, "fsync", side_effect=OSError("not supported here")):
            backup.snapshot(self.db, self.tmp / "copy.db", pages=8)
        self.assertEqual(self.items(self.tmp / "copy.db"), (50, "ok"))

    def test_the_scheduled_backup_uses_it_and_still_verifies(self):
        self.fill(50)
        ab = AutoBackup(self.db, self.tmp / "uploads", self.tmp / "backups", self.tmp / "state")
        with mock.patch.object(backup, "snapshot", wraps=backup.snapshot) as snap, \
                mock.patch.object(type(self.db), "backup_to", side_effect=AssertionError("one-step copy used")):
            st = ab.run()
        self.assertTrue(st["last_ok"], st)
        self.assertTrue(snap.called)
        self.assertEqual(backup.verify_archive(self.tmp / "backups" / st["last_file"])["problems"], [])


class TestBackgroundWorkDoesNotHoldTheCashier(DBTestCase):
    def test_the_interpreter_hands_over_within_half_a_millisecond_once_the_backup_job_starts(self):
        import sys
        from app.core import autobackup, responsive
        old = sys.getswitchinterval()
        ab = AutoBackup(self.db, self.tmp / "uploads", self.tmp / "backups", self.tmp / "state")
        ab.stop_event.wait = lambda timeout=None: ab.stop_event.is_set()
        ab.run = lambda reason="manual": {}
        try:
            sys.setswitchinterval(0.005)                    # Python's default
            with mock.patch.object(ab, "_due", return_value=False):
                ab.start()
            self.assertLessEqual(sys.getswitchinterval(), responsive.SWITCH_INTERVAL + 1e-9)
            sys.setswitchinterval(0.0001)                   # already shorter: left alone
            self.assertAlmostEqual(responsive.apply(), 0.0001, places=6)
        finally:
            ab.stop()
            autobackup._instance = None
            sys.setswitchinterval(old)

    def test_the_activity_log_chain_is_checked_a_block_at_a_time(self):
        """Same verdicts as before, without holding the whole log in memory."""
        from app.core import audit
        with self.db.transaction():
            for i in range(25):
                audit.record(self.db, self.ctx, "test.row", "system", None, after={"i": i})
        with mock.patch.object(audit, "CHAIN_BLOCK", 7), \
                mock.patch.object(type(self.db), "all", side_effect=AssertionError("the whole log was read at once")):
            self.assertEqual(audit.verify_chain(self.db), [])
        self.db.conn.execute("DROP TRIGGER audit_log_no_update")
        with self.db.transaction():
            self.db.execute("UPDATE audit_log SET after_json='{\"i\": 999}' WHERE action='test.row' AND seq=(SELECT max(seq)-3 FROM audit_log)")
        with mock.patch.object(audit, "CHAIN_BLOCK", 7):
            bad = audit.verify_chain(self.db)
        self.assertEqual([p["problem"] for p in bad], ["row_modified"])


if __name__ == "__main__":
    import unittest
    unittest.main()
