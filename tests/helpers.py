from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from app.core import accounts, security
from app.db import Database

security.set_cost(2 ** 10)  # fast hashing in tests only

OWNER_PW = "Owner-Pass-2026"


class DBTestCase(unittest.TestCase):
    """Fresh, fully migrated SQLite file per test + one pharmacy tenant."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pharm-test-"))
        self.db = Database(self.tmp / "test.db")
        self.db.migrate()
        self.org = accounts.create_organization(
            self.db, name="صيدلية الاختبار", owner_username="owner",
            owner_full_name="Owner", owner_password=OWNER_PW)
        self.ctx = self.login("owner", OWNER_PW)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def login(self, username, password, org_id=None):
        tok = accounts.login(self.db, username, password, org_id=org_id)["token"]
        return accounts.resolve_session(self.db, tok)

    def second_tenant(self):
        org = accounts.create_organization(
            self.db, name="صيدلية أخرى", owner_username="other",
            owner_full_name="Other", owner_password="Other-Pass-2026")
        return org, self.login("other", "Other-Pass-2026")
