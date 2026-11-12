from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.core import accounts, security
from app.db import Database

security.set_cost(2 ** 10)  # fast hashing in tests only

OWNER_PW = "Owner-Pass-2026"

_BROWSER_PROBE = ("const {chromium} = require('playwright');"
                  " process.exit(require('fs').existsSync(chromium.executablePath()) ? 0 : 3)")


def _browser_missing() -> str | None:
    """Why a real browser test cannot run here, or None. Node, the Playwright module and its
    Chromium must all exist (a module without the browser failed instead of skipping, #70)."""
    node = shutil.which("node")
    if not node:
        return "Node not available"
    r = subprocess.run([node, "-e", _BROWSER_PROBE], capture_output=True, timeout=30, check=False,
                       cwd=str(Path(__file__).parent))
    if r.returncode == 3:
        return "Playwright's Chromium is not installed"
    return "Node Playwright not available" if r.returncode else None


def needs_browser(cls):
    """Skip where no browser exists, except where PHARM_REQUIRE_BROWSER=1 (CI): there it must run."""
    why = None if os.environ.get("PHARM_REQUIRE_BROWSER") == "1" else _browser_missing()
    return unittest.skipIf(why is not None, f"{why} (runs in CI)")(cls)


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


def today() -> str:
    """The local day at the moment of the call, for a report that must include what the test just sold.
    The module constants `T = date.today()` are fixed at import, so a suite that runs across midnight
    asked yesterday's report for sales completed today (seen on CI 11/12, 23:58 -> 00:01 UTC)."""
    from app.sales.service import local_today
    return local_today().isoformat()
