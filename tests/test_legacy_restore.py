"""R5: a backup written by the release pharmacies run (main @ 69e3c4a, the install.bat setup)
must restore in this version — the move to the installer goes through exactly that.
Fixture: tests/fixtures/legacy_backup_main_69e3c4a.zip (synthetic, see make_legacy_backup.py).
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from app.core import accounts, backup
from app.db import Database
from app.sales import attachments
from tests.fixtures.make_legacy_backup import PNG
from tests.helpers import OWNER_PW

FIXTURE = Path(__file__).parent / "fixtures" / "legacy_backup_main_69e3c4a.zip"


class TestLegacyRestore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pharm-legacy-"))
        self.db_path, self.up = self.tmp / "pharmacy.db", self.tmp / "uploads"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_old_backup_verifies_and_restores(self):
        self.assertEqual(backup.verify_archive(FIXTURE)["problems"], [])
        backup.restore(FIXTURE, self.db_path, self.up, self.tmp / "pre")
        db = Database(self.db_path)
        try:
            db.migrate()
            self.assertEqual(backup.integrity_problems(db), [])
            self.assertEqual(db.one("SELECT count(*) n FROM sales WHERE status='completed'")["n"], 1)
            self.assertEqual(db.one("SELECT count(*) n FROM cash_shifts WHERE closed_at IS NOT NULL")["n"], 1)
            ctx = accounts.resolve_session(db, accounts.login(db, "owner", OWNER_PW)["token"])
            att = db.one("SELECT id FROM sale_attachments WHERE org_id=?", (ctx.org_id,))
            data, mime = attachments.file(db, self.up, ctx, att["id"])  # old machine's path is gone
            self.assertEqual((data, mime), (PNG, "image/png"))
        finally:
            db.close()

    def test_restored_data_backs_up_again(self):
        backup.restore(FIXTURE, self.db_path, self.up, self.tmp / "pre")
        db = Database(self.db_path)
        try:
            db.migrate()
            again = backup.create(db, self.up, self.tmp / "backups")
        finally:
            db.close()
        self.assertEqual(backup.verify_archive(again)["problems"], [])


if __name__ == "__main__":
    unittest.main()
