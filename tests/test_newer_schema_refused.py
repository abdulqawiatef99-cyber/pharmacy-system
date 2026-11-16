"""Data written by a newer version of the program is never opened or restored by an older one.

schema_migrations rows without a migration file mean the database is ahead of this code: migrate()
raises instead of starting on a schema it does not know, and a backup of such data fails verification
(so restore and the first-run restore refuse it) with a message that says why."""
import os
import subprocess
import sys
from pathlib import Path

from app.core import backup
from app.db import Database, MigrationError
from tests.helpers import DBTestCase

ROOT = Path(__file__).resolve().parent.parent


def make_newer(db: Database, version: str = "9999_from_the_future") -> None:
    with db._write_lock:
        db.conn.execute("BEGIN IMMEDIATE")
        db.conn.execute("INSERT INTO schema_migrations VALUES (?, 'x', datetime('now'))", (version,))
        db.conn.execute("COMMIT")


class TestNewerSchema(DBTestCase):
    def test_migrate_refuses_a_database_from_a_newer_program(self):
        self.assertEqual(self.db.unknown_migrations(), [])
        make_newer(self.db)
        self.assertEqual(self.db.unknown_migrations(), ["9999_from_the_future"])
        with self.assertRaises(MigrationError) as e:
            self.db.migrate()
        self.assertIn("newer version of the program", str(e.exception))
        self.assertIn("9999_from_the_future", str(e.exception))

    def test_nothing_is_applied_before_refusing(self):
        d = self.tmp / "mig"
        d.mkdir()
        (d / "0001_a.sql").write_text("CREATE TABLE a (x INTEGER);")
        (d / "0003_c.sql").write_text("CREATE TABLE c (x INTEGER);")
        db2 = Database(self.tmp / "m.db")
        try:
            db2.migrate(d)
            (d / "0003_c.sql").unlink()                                # older program: 0003 unknown ...
            (d / "0002_b.sql").write_text("CREATE TABLE b (x INTEGER);")   # ... and something of its own pending
            with self.assertRaises(MigrationError):
                db2.migrate(d)
            self.assertIsNone(db2.one("SELECT 1 FROM sqlite_master WHERE name='b'"))
        finally:
            db2.close()

    def test_backup_of_newer_data_fails_verification_and_is_not_restored(self):
        good = backup.create(self.db, self.tmp / "up", self.tmp / "bk")
        self.assertEqual(backup.verify_archive(good)["problems"], [])
        make_newer(self.db)
        arch = backup.create(self.db, self.tmp / "up", self.tmp / "bk2")   # backing it up is still allowed
        problems = backup.verify_archive(arch)["problems"]
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("newer version of the program", problems[0])
        target = self.tmp / "r" / "pharmacy.db"
        with self.assertRaises(RuntimeError):
            backup.restore(arch, target, self.tmp / "r" / "up", self.tmp / "pre")
        self.assertFalse(target.exists())
        with self.assertRaises(RuntimeError):                          # not an attachment problem: never waved through
            backup.restore(arch, target, self.tmp / "r" / "up", self.tmp / "pre", allow_missing_attachments=True)
        self.assertFalse(target.exists())

    def test_cli_stops_with_a_clear_message(self):
        data = self.tmp / "cli"
        env = {**os.environ, "PHARM_DATA_DIR": str(data), "PYTHONPATH": str(ROOT), "PYTHONUTF8": "1"}
        run = lambda *a: subprocess.run([sys.executable, "manage.py", *a], cwd=ROOT, env=env,  # noqa: E731
                                        capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(run("init", "--pharmacy", "x", "--username", "cli", "--password", "Cli-Pass-2026").returncode, 0)
        db = Database(data / "pharmacy.db")
        make_newer(db)
        db.close()
        r = run("verify")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("نسخة أحدث من البرنامج", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
