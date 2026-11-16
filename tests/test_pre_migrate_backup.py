"""An installer upgrade brings new migrations: the live database is backed up before they run.

manage.py _db() (every command, and `serve` on each start) takes a verified backup tagged
"pre-migrate" when the database already holds a pharmacy and migrations are pending, and stops
without migrating when that backup cannot be made. New, empty and up-to-date databases take none."""
import io
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest import mock

import app.db as dbmod
import manage
from app import config
from app.core import accounts, backup, security
from app.db import Database

security.set_cost(2 ** 10)


class TestBackupBeforeMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pharm-premig-"))
        data = self.tmp / "data"
        self.s = replace(config.load(), data_dir=data, db_path=data / "pharmacy.db", upload_dir=data / "uploads",
                         backup_dir=None)
        self.out = io.StringIO()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def open(self, **kw):
        with redirect_stdout(self.out), redirect_stderr(self.out):
            return manage._db(self.s, **kw)

    def with_pharmacy(self):
        db = self.open(create=True)
        accounts.create_organization(db, name="صيدلية تجربة", owner_username="owner", owner_full_name="O",
                                     owner_password="Owner-Pass-2026")
        db.close()

    def new_installer(self):
        """The same program with one more migration, as a new installer brings."""
        mig = self.tmp / "mig"
        shutil.copytree(dbmod.MIGRATIONS_DIR, mig)
        (mig / "9999_new.sql").write_text("UPDATE organizations SET name = 'changed by migration';\n", encoding="utf-8")
        migrate, pending = Database.migrate, Database.pending_migrations
        p1 = mock.patch.object(Database, "migrate", lambda db, directory=mig: migrate(db, directory))
        p2 = mock.patch.object(Database, "pending_migrations", lambda db, directory=mig: pending(db, directory))
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def backups(self):
        return sorted(self.s.backup_path.glob("*.zip")) if self.s.backup_path.exists() else []

    def test_pending_migration_on_a_pharmacy_is_backed_up_first(self):
        self.with_pharmacy()
        self.assertEqual(self.backups(), [])                           # a new database took no backup
        self.new_installer()
        db = self.open(create=True)
        self.assertEqual(db.one("SELECT name FROM organizations")["name"], "changed by migration")
        db.close()
        made = self.backups()
        self.assertEqual(len(made), 1)
        self.assertTrue(made[0].name.endswith("-pre-migrate.zip"))
        self.assertEqual(backup.verify_archive(made[0])["problems"], [])
        backup.restore(made[0], self.tmp / "r" / "pharmacy.db", self.tmp / "r" / "up", self.tmp / "pre")
        old = Database(self.tmp / "r" / "pharmacy.db")
        try:                                                           # the data as it was before the migration
            self.assertEqual(old.one("SELECT name FROM organizations")["name"], "صيدلية تجربة")
            self.assertIsNone(old.one("SELECT 1 FROM schema_migrations WHERE version='9999_new'"))
        finally:
            old.close()
        self.open().close()                                            # next start: nothing pending
        self.assertEqual(len(self.backups()), 1)

    def test_failed_backup_stops_before_migrating(self):
        self.with_pharmacy()
        self.new_installer()
        with mock.patch("app.core.backup.create", side_effect=OSError("disk full")), \
                self.assertRaises(SystemExit) as e:
            self.open(create=True)
        self.assertIn("disk full", str(e.exception.code))
        self.assertIn("لم يتم تحديث", str(e.exception.code))
        db = Database(self.s.db_path)
        try:
            self.assertEqual(db.one("SELECT name FROM organizations")["name"], "صيدلية تجربة")
            self.assertIsNone(db.one("SELECT 1 FROM schema_migrations WHERE version='9999_new'"))
        finally:
            db.close()

    def test_a_backup_that_fails_its_check_stops_too(self):
        self.with_pharmacy()
        self.new_installer()
        with mock.patch("app.core.backup.quick_check", return_value=["database checksum does not match the manifest"]), \
                self.assertRaises(SystemExit):
            self.open(create=True)
        self.assertEqual(self.backups(), [])
        db = Database(self.s.db_path)
        try:
            self.assertIsNone(db.one("SELECT 1 FROM schema_migrations WHERE version='9999_new'"))
        finally:
            db.close()

    def test_empty_database_migrates_without_a_backup(self):
        self.open(create=True).close()                                 # migrated, no pharmacy yet
        self.new_installer()
        with mock.patch("app.core.backup.create", side_effect=AssertionError("no backup of an empty database")):
            db = self.open(create=True)
        self.assertIsNotNone(db.one("SELECT 1 FROM schema_migrations WHERE version='9999_new'"))
        db.close()
        self.assertEqual(self.backups(), [])

    def test_nothing_pending_takes_no_backup(self):
        self.with_pharmacy()
        with mock.patch("app.core.backup.create", side_effect=AssertionError("nothing is pending")):
            self.open().close()
        self.assertEqual(self.backups(), [])

    def test_pending_migrations_is_read_only(self):
        db = Database(self.tmp / "x.db")
        try:
            self.assertEqual(db.pending_migrations(), [f.stem for f in sorted(dbmod.MIGRATIONS_DIR.glob("*.sql"))])
            self.assertEqual(db.all("SELECT name FROM sqlite_master"), [])
            db.migrate()
            self.assertEqual(db.pending_migrations(), [])
        finally:
            db.close()
