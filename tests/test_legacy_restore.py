"""R5: a backup written by the release pharmacies run (main, the install.bat setup) must
restore in this version — the move to the installer goes through exactly that.
The old backup is written at test time by main's own code (tests/fixtures/make_legacy_backup.py).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.core import accounts, backup
from app.db import Database
from app.sales import attachments
from tests.fixtures.make_legacy_backup import PNG
from tests.helpers import OWNER_PW

MAKER = Path(__file__).parent / "fixtures" / "make_legacy_backup.py"


class TestLegacyRestore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = os.environ.get("PHARM_RELEASE_SRC")
        if not src or not (Path(src) / "app").is_dir():
            raise unittest.SkipTest("release checkout not available (CI sets PHARM_RELEASE_SRC)")
        cls.out = Path(tempfile.mkdtemp(prefix="pharm-release-"))
        env = {**os.environ, "PYTHONPATH": src, "PHARM_DATA_DIR": str(cls.out / "data")}
        r = subprocess.run([sys.executable, str(MAKER), str(cls.out / "backups")], cwd=src, env=env,
                           capture_output=True, text=True, timeout=120, check=False)
        if r.returncode:
            shutil.rmtree(cls.out, ignore_errors=True)
            raise AssertionError("release code could not write a backup:\n" + r.stderr[-2000:])
        cls.archive = Path(r.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pharm-legacy-"))
        self.db_path, self.up = self.tmp / "pharmacy.db", self.tmp / "uploads"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_old_backup_verifies_and_restores(self):
        self.assertEqual(backup.verify_archive(self.archive)["problems"], [])
        backup.restore(self.archive, self.db_path, self.up, self.tmp / "pre")
        db = Database(self.db_path)
        try:
            db.migrate()
            self.assertEqual(backup.integrity_problems(db), [])
            ctx = accounts.resolve_session(db, accounts.login(db, "owner", OWNER_PW)["token"])
            self.assertEqual(db.one("SELECT count(*) n FROM sales WHERE org_id=? AND status='completed'",
                                    (ctx.org_id,))["n"], 1)
            self.assertEqual(db.one("SELECT count(*) n FROM cash_shifts WHERE org_id=? AND closed_at IS NOT NULL",
                                    (ctx.org_id,))["n"], 1)
            att = db.one("SELECT id FROM sale_attachments WHERE org_id=?", (ctx.org_id,))
            data, mime = attachments.file(db, self.up, ctx, att["id"])  # the old machine's path is gone
            self.assertEqual((data, mime), (PNG, "image/png"))
        finally:
            db.close()

    def test_restored_data_backs_up_again(self):
        backup.restore(self.archive, self.db_path, self.up, self.tmp / "pre")
        db = Database(self.db_path)
        try:
            db.migrate()
            again = backup.create(db, self.up, self.tmp / "backups")
        finally:
            db.close()
        self.assertEqual(backup.verify_archive(again)["problems"], [])


if __name__ == "__main__":
    unittest.main()
