"""Restore must work exactly when it is needed most: when the live database is damaged.

The pre-restore safety copy is a verified backup when the live data passes verification, and a raw
copy of the database file(s) when it does not; the result says which one was kept."""
import sqlite3
from decimal import Decimal
from pathlib import Path
from unittest import mock

from app.catalog import service as cat
from app.core import backup
from app.db import Database
from app.inventory import service as inv
from tests.helpers import DBTestCase


class TestRestoreOverDamagedLiveDatabase(DBTestCase):
    def setUp(self):
        super().setUp()
        self.up = self.tmp / "uploads"
        item = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab", "pack_size": 2})
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=item, qty_delta=20, movement_type="opening",
                              unit_cost=Decimal("1"))
        self.good = backup.create(self.db, self.up, self.tmp / "bk")
        self.db_path = Path(self.db.path)
        self.db.close()

    def restored_ok(self):
        self.db = Database(self.db_path)
        self.assertEqual(backup.integrity_problems(self.db), [])
        self.assertEqual(self.db.one("SELECT count(*) c FROM items")["c"], 1)

    def test_healthy_live_database_keeps_a_verified_backup(self):
        r = backup.restore(self.good, self.db_path, self.up, self.tmp / "pre")
        self.assertEqual(r["pre_restore_kind"], "verified_backup")
        self.assertEqual(backup.verify_archive(Path(r["pre_restore_backup"]))["problems"], [])
        self.restored_ok()

    def test_live_database_that_fails_verification_is_kept_raw_and_restore_goes_on(self):
        c = sqlite3.connect(self.db_path)
        c.execute("UPDATE stock_balances SET qty = qty + 5")           # bypass the app: balances != ledger
        c.commit()
        c.close()
        r = backup.restore(self.good, self.db_path, self.up, self.tmp / "pre")
        self.assertEqual(r["pre_restore_kind"], "raw_copy")
        self.assertIn("failed verification", r["pre_restore_error"])
        kept = Path(r["pre_restore_backup"])
        self.assertTrue(kept.is_dir())
        self.assertIn("RAW", kept.name)
        c = sqlite3.connect(kept / self.db_path.name)                  # the damaged data, as it was
        self.assertEqual(c.execute("SELECT qty FROM stock_balances").fetchone()[0], 25)
        c.close()
        self.restored_ok()
        self.assertEqual(self.db.one("SELECT qty FROM stock_balances")["qty"], 20)

    def test_live_file_that_is_not_a_database_is_kept_raw_and_restore_goes_on(self):
        garbage = b"garbage" * 1000
        self.db_path.write_bytes(garbage)
        Path(str(self.db_path) + "-wal").write_bytes(b"old wal")
        r = backup.restore(self.good, self.db_path, self.up, self.tmp / "pre")
        self.assertEqual(r["pre_restore_kind"], "raw_copy")
        kept = Path(r["pre_restore_backup"])
        self.assertEqual((kept / self.db_path.name).read_bytes(), garbage)
        self.assertEqual((kept / (self.db_path.name + "-wal")).read_bytes(), b"old wal")
        self.restored_ok()

    def test_no_live_database_keeps_nothing(self):
        r = backup.restore(self.good, self.tmp / "new" / "pharmacy.db", self.tmp / "new" / "up", self.tmp / "pre")
        self.assertIsNone(r["pre_restore_backup"])
        self.assertIsNone(r["pre_restore_kind"])

    def test_no_safety_copy_no_restore(self):
        self.db_path.write_bytes(b"garbage" * 1000)
        with mock.patch("shutil.copy2", side_effect=OSError("disk full")), self.assertRaises(OSError):
            backup.restore(self.good, self.db_path, self.up, self.tmp / "pre")
        self.assertEqual(self.db_path.read_bytes(), b"garbage" * 1000)  # nothing restored without a way back
