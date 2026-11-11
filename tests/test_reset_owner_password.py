"""#151: a forgotten owner password can be reset only on the pharmacy PC (manage.py), never over the network."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import manage
from app import config
from app.core import accounts
from app.core.errors import Unauthorized
from app.db import Database

OLD, NEW = "Owner-Pass-2026", "New-Owner-Pass-2027"


class TestResetOwnerPassword(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pharm-reset-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = mock.patch.dict(os.environ, {"PHARM_DATA_DIR": str(self.tmp / "data")})
        env.start()
        self.addCleanup(env.stop)
        with mock.patch("sys.stdout"):
            manage.main(["init", "--pharmacy", "صيدلية تجربة", "--username", "owner", "--password", OLD])
        self.db = Database(config.load().db_path)
        self.session = accounts.login(self.db, "owner", OLD)

    def reset(self, *answers, args=()):
        with mock.patch("getpass.getpass", side_effect=list(answers)), mock.patch("sys.stdout"):
            manage.main(["reset-owner-password", *args])

    def can_login(self, pw):
        try:
            accounts.login(self.db, "owner", pw)
            return True
        except Unauthorized:
            return False

    def test_reset_sets_the_new_password_signs_out_and_is_audited(self):
        self.assertEqual(self.db.one("SELECT count(*) FROM sessions WHERE revoked_at IS NULL")[0], 1)
        self.reset(NEW, NEW)
        self.assertEqual(self.db.one("SELECT count(*) FROM sessions WHERE revoked_at IS NULL")[0], 0)  # signed out
        self.assertTrue(self.can_login(NEW))
        self.assertFalse(self.can_login(OLD))
        a = self.db.one("SELECT user_id, after_json FROM audit_log WHERE action='user.password_reset'")
        self.assertEqual(a["user_id"], "system")
        self.assertIn("local_cli", a["after_json"])

    def test_mismatch_or_weak_password_changes_nothing(self):
        for answers in ((NEW, NEW + "x"), ("short", "short")):
            with self.assertRaises(SystemExit):
                self.reset(*answers)
            self.assertTrue(self.can_login(OLD), answers)
        self.assertEqual(self.db.one("SELECT count(*) FROM audit_log WHERE action='user.password_reset'")[0], 0)

    def test_unknown_owner_name_stops(self):
        with self.assertRaises(SystemExit):
            self.reset(NEW, NEW, args=("--username", "nobody"))
        self.assertTrue(self.can_login(OLD))

    def test_two_owners_need_the_username(self):
        with self.db.transaction():
            accounts.create_organization(self.db, name="صيدلية تانية", owner_username="owner2",
                                         owner_full_name="مالك تاني", owner_password=OLD)
        with self.assertRaises(SystemExit):
            self.reset(NEW, NEW)
        self.reset(NEW, NEW, args=("--username", "owner2"))
        self.assertTrue(self.can_login(OLD))                                       # the first owner untouched
        accounts.login(self.db, "owner2", NEW)


if __name__ == "__main__":
    unittest.main()
