"""SQLite health for years of use (night queue 19/11, item 5): the WAL file is brought back to size, the
database is checked on every backup, VACUUM runs only when asked, and the logs that grow for ever are
trimmed. The books themselves (audit log, safety log, ledgers) are never deleted. Synthetic data only."""
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from app.catalog import service as cat
from app.core import audit, backup, daily, dbhealth, notify, retention
from app.core.autobackup import AutoBackup
from tests.helpers import DBTestCase


def wal(db) -> Path:
    return Path(db.path + "-wal")


class _Case(DBTestCase):
    def fill(self, n=300):
        start = getattr(self, "_filled", 0)
        self._filled = start + n
        with self.db.transaction():
            for i in range(start, start + n):
                cat.create_item(self.db, self.ctx, {"name_en": f"Filler item number {i:05d} " + "x" * 200,
                                                    "pack_size": 1, "public_price": 100}, force=True)


class TestReport(_Case):
    def test_sizes_and_free_pages(self):
        self.fill()
        self.assertGreater(dbhealth.report(self.db)["wal_bytes"], 0)       # the new rows are still in the WAL
        dbhealth.checkpoint(self.db, limit_bytes=0)
        r = dbhealth.report(self.db)
        self.assertEqual(r["db_bytes"], Path(self.db.path).stat().st_size)
        self.assertEqual(r["page_count"] * r["page_size"], r["db_bytes"])
        self.assertEqual((r["free_bytes"], r["free_pct"]), (r["freelist_count"] * r["page_size"], 0))
        self.assertEqual((r["journal_mode"], r["wal_bytes"]), ("wal", 0))


class TestCheckpoint(_Case):
    def test_a_small_wal_gets_a_checkpoint_that_blocks_nobody(self):
        self.fill(50)
        r = dbhealth.checkpoint(self.db)
        self.assertEqual(r["mode"], "passive")

    def test_a_wal_over_the_limit_is_truncated(self):
        self.fill()
        self.assertGreater(wal(self.db).stat().st_size, 100_000)
        r = dbhealth.checkpoint(self.db, limit_bytes=100_000)
        self.assertEqual((r["mode"], r["wal_bytes_after"]), ("truncate", 0))
        self.assertEqual(wal(self.db).stat().st_size, 0)
        self.assertEqual(self.db.one("SELECT count(*) n FROM items")["n"], 300)       # nothing lost

    def test_a_reader_in_the_way_is_not_waited_for_long_and_nothing_breaks(self):
        """A long report holds an old snapshot: the truncate gives up after a short wait instead of stopping
        the cashier, and the next day tries again."""
        self.fill()
        other = sqlite3.connect(self.db.path, isolation_level=None)
        try:
            other.execute("BEGIN")
            other.execute("SELECT count(*) FROM items").fetchone()                  # holds a read snapshot
            self.fill(50)                                                           # frames after that snapshot
            with mock.patch.object(dbhealth, "TRUNCATE_WAIT_MS", 50):
                r = dbhealth.checkpoint(self.db, limit_bytes=100_000)
            self.assertTrue(r["busy"])
            self.assertGreater(r["wal_bytes_after"], 0)
        finally:
            other.close()
        self.assertEqual(self.db.conn.execute("PRAGMA busy_timeout").fetchone()[0], 15000)   # put back
        self.fill(1)                                                                # the program still writes

    def test_never_inside_a_transaction(self):
        with self.db.transaction():
            with self.assertRaises(RuntimeError):
                dbhealth.checkpoint(self.db)


class TestVacuumOnDemand(_Case):
    def bloat(self):
        """Pages that were used and are now free (what months of cleaned notifications leave behind)."""
        c = self.db.conn
        c.execute("CREATE TABLE scratch (x BLOB)")
        c.execute("BEGIN")
        c.executemany("INSERT INTO scratch VALUES (?)", [(b"z" * 3000,) for _ in range(400)])
        c.execute("COMMIT")
        c.execute("DROP TABLE scratch")

    def test_vacuum_gives_the_space_back_and_keeps_the_data(self):
        self.bloat()
        before = dbhealth.report(self.db)
        self.assertGreater(before["free_pct"], 10)
        users = self.db.one("SELECT count(*) n FROM users")["n"]
        r = dbhealth.vacuum(self.db)
        self.assertLess(r["after"]["db_bytes"], r["before"]["db_bytes"])
        self.assertEqual(r["after"]["freelist_count"], 0)
        self.assertEqual(self.db.one("SELECT count(*) n FROM users")["n"], users)
        self.assertEqual(backup.integrity_problems(self.db), [])
        self.assertEqual(self.db.one("PRAGMA journal_mode")[0], "wal")              # still WAL afterwards

    def test_vacuum_is_refused_without_room_for_a_second_copy(self):
        self.bloat()
        from collections import namedtuple
        U = namedtuple("U", "total used free")
        with mock.patch.object(dbhealth.shutil, "disk_usage", return_value=U(10 ** 9, 10 ** 9, 1000)):
            with self.assertRaises(dbhealth.NotEnoughSpace):
                dbhealth.vacuum(self.db)

    def test_the_daily_job_never_vacuums(self):
        self.bloat()
        before = Path(self.db.path).stat().st_size
        with mock.patch.object(dbhealth, "vacuum", side_effect=AssertionError("vacuum from the daily job")):
            dbhealth.daily_maintenance(self.db, log_dir=self.tmp / "logs")
        self.assertGreaterEqual(Path(self.db.path).stat().st_size, before)

    def test_command_line_report_and_vacuum(self):
        self.bloat()
        self.db.close()
        out = []
        self.assertEqual(dbhealth.main(["report", "--db", self.db.path], out=out.append), 0)
        self.assertIn("free_pct", out[-1])
        self.assertEqual(dbhealth.main(["vacuum", "--db", self.db.path], out=out.append), 0)
        self.assertEqual(json.loads(out[-1])["after"]["freelist_count"], 0)


class TestLogTrim(_Case):
    def test_a_big_server_log_keeps_its_newest_lines(self):
        p = self.tmp / "logs" / "server.log"
        p.parent.mkdir()
        p.write_text("".join(f"line {i:06d}\n" for i in range(20000)), encoding="utf-8")   # 240 KB
        removed = dbhealth.trim_log(p, max_bytes=100_000, keep_bytes=30_000)
        text = p.read_text(encoding="utf-8")
        self.assertGreater(removed, 200_000)
        self.assertLessEqual(len(text), 30_000 + 200)
        self.assertTrue(text.endswith("line 019999\n"))
        self.assertTrue(text.splitlines()[1].startswith("line "))                 # cut at a line start
        self.assertIn("trimmed", text.splitlines()[0])

    def test_a_small_or_missing_log_is_left_alone(self):
        p = self.tmp / "server.log"
        self.assertEqual(dbhealth.trim_log(p, max_bytes=100, keep_bytes=10), 0)
        p.write_text("short\n", encoding="utf-8")
        self.assertEqual(dbhealth.trim_log(p, max_bytes=100, keep_bytes=10), 0)
        self.assertEqual(p.read_text(encoding="utf-8"), "short\n")

    def test_a_handle_open_for_append_keeps_writing_after_the_trim(self):
        """The launcher holds server.log open for append while the server runs."""
        p = self.tmp / "server.log"
        with open(p, "a", encoding="utf-8") as held:
            held.write("x" * 5000 + "\n")
            held.flush()
            dbhealth.trim_log(p, max_bytes=1000, keep_bytes=100)
            held.write("after the trim\n")
            held.flush()
        text = p.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("after the trim\n"))
        self.assertNotIn("\x00", text)


class TestDailyMaintenance(_Case):
    def test_once_a_day_and_recorded(self):
        logs = self.tmp / "logs"
        logs.mkdir()
        (logs / "server.log").write_text("y" * 300 + "\n", encoding="utf-8")
        with mock.patch.object(dbhealth, "LOG_MAX_BYTES", 100), mock.patch.object(dbhealth, "LOG_KEEP_BYTES", 50):
            first = dbhealth.daily_maintenance(self.db, log_dir=logs)
            again = dbhealth.daily_maintenance(self.db, log_dir=logs)
        self.assertEqual(first["checkpoint"]["mode"], "passive")
        self.assertGreater(first["logs"]["server.log"], 0)
        self.assertIsNone(again)                                                   # same day: nothing
        st = json.loads((Path(self.db.path).parent / "db_health.json").read_text(encoding="utf-8"))
        self.assertEqual(st["last"], date.today().isoformat())
        self.assertIn("db_bytes", st["report"])
        tomorrow = date.today() + timedelta(days=1)
        self.assertIsNotNone(dbhealth.daily_maintenance(self.db, log_dir=logs, today=tomorrow))

    def test_the_daily_checks_run_it_and_survive_its_failure(self):
        with mock.patch.object(dbhealth, "daily_maintenance", side_effect=RuntimeError("disk")) as m, \
                self.assertLogs("pharm.daily", level="ERROR"):
            daily.run(self.db)
        self.assertTrue(m.called)
        with mock.patch.object(dbhealth, "daily_maintenance", return_value=None) as m:
            daily.run(self.db)
        self.assertEqual(m.call_args.kwargs["log_dir"], Path(self.db.path).parent.parent / "logs")


class TestIntegrityOnEveryBackup(_Case):
    def test_a_damaged_database_stops_the_backup_and_tells_the_owner(self):
        ab = AutoBackup(self.db, self.tmp / "uploads", self.tmp / "backups", self.tmp / "state")
        with mock.patch("app.core.backup.integrity_problems", return_value=["sqlite integrity_check: row 5 missing from index"]), \
                self.assertLogs("pharm.backup", level="ERROR"):
            st = ab.run()
        self.assertFalse(st["last_ok"])
        self.assertIn("integrity_check", st["last_error"])
        self.assertEqual(list((self.tmp / "backups").glob("*.zip")), [])
        self.assertTrue([n for n in notify.listing(self.db, self.ctx)["items"] if n["kind"] == "backup" and n["severity"] == "error"])

    def test_a_good_backup_records_that_the_database_was_checked(self):
        ab = AutoBackup(self.db, self.tmp / "uploads", self.tmp / "backups", self.tmp / "state")
        with mock.patch("app.core.backup.integrity_problems", wraps=backup.integrity_problems) as chk:
            st = ab.run()
        self.assertTrue(st["last_ok"])
        self.assertTrue(chk.called)


class TestTheBooksAreKept(_Case):
    def test_retention_never_touches_the_audit_log_or_the_ledgers(self):
        with self.db.transaction():
            audit.record(self.db, self.ctx, "test.old", "system", None)
        n = self.db.one("SELECT count(*) n FROM audit_log")["n"]
        retention.clean(self.db)
        dbhealth.daily_maintenance(self.db, log_dir=self.tmp)
        self.assertEqual(self.db.one("SELECT count(*) n FROM audit_log")["n"], n)
        it = cat.create_item(self.db, self.ctx, {"name_en": "Ledger Row", "pack_size": 1, "public_price": 100})
        with self.db.transaction():
            from app.inventory import service as inv
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=1, movement_type="opening")
        for table in ("audit_log", "stock_movements"):
            with self.assertRaises(sqlite3.DatabaseError, msg=table):
                with self.db.transaction():
                    self.db.execute(f"DELETE FROM {table}")
        guards = {r["name"] for r in self.db.all("SELECT name FROM sqlite_master WHERE type='trigger'")}
        self.assertLessEqual({"audit_log_no_delete", "stock_movements_no_delete", "safety_events_no_delete"}, guards)

    def test_the_policy_names_what_is_kept_and_what_is_cleaned(self):
        z = retention.sizes(self.db, Path(self.db.path), self.tmp / "up", self.tmp / "bk")
        self.assertEqual(z["policy"]["notifications_days"], 180)
        self.assertEqual(z["policy"]["kept_for_ever"], ["audit_log", "safety_events", "stock_movements", "sales", "purchase_invoices"])
        self.assertEqual(z["policy"]["server_log_max_mb"], dbhealth.LOG_MAX_BYTES // (1024 * 1024))


if __name__ == "__main__":
    import unittest
    unittest.main()
