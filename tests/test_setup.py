"""First-run wizard: only on an empty database, only from the pharmacy PC, only once."""
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from starlette.testclient import TestClient

from app import config
from app.api.app import create_app
from app.core import security
from app.db import Database

security.set_cost(2 ** 10)
H = {"X-Requested-With": "pharm"}
BODY = {"pharmacy": "صيدلية تجربة", "username": "owner", "password": "Owner-Pass-2026"}


class TestSetup(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "t.db")
        self.db.migrate()
        self.app = create_app(self.db, replace(config.load(), upload_dir=self.tmp / "up"))

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_first_run_creates_owner_and_logs_in_once(self):
        c = TestClient(self.app)
        self.assertTrue(c.get("/api/setup").json()["needed"])
        r = c.post("/api/setup", headers=H, json=BODY)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(c.get("/api/me").status_code, 200)            # logged in by the cookie
        self.assertFalse(c.get("/api/setup").json()["needed"])
        r = TestClient(self.app).post("/api/setup", headers=H, json={**BODY, "username": "second"})
        self.assertEqual(r.status_code, 409)                           # never a second owner

    def test_refused_from_another_machine(self):
        c = TestClient(self.app, client=("192.168.1.50", 5000))
        self.assertEqual(c.post("/api/setup", headers=H, json=BODY).status_code, 403)

    def test_weak_password_refused(self):
        r = TestClient(self.app).post("/api/setup", headers=H, json={**BODY, "password": "123"})
        self.assertEqual(r.status_code, 422)
        self.assertTrue(TestClient(self.app).get("/api/setup").json()["needed"])

    def test_restore_only_under_launcher_and_only_when_empty(self):
        c = TestClient(self.app)
        files = {"file": ("b.zip", b"not a zip")}
        self.assertEqual(c.post("/api/setup/restore", headers=H, files=files).status_code, 422)  # no launcher
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"PHARM_LAUNCHER": "1"}), mock.patch("threading.Timer") as t:
            r = c.post("/api/setup/restore", headers=H, files=files)
            self.assertEqual(r.status_code, 422)                       # bad archive refused, no restart
            t.assert_not_called()
            c.post("/api/setup", headers=H, json=BODY)
            self.assertEqual(c.post("/api/setup/restore", headers=H, files=files).status_code, 409)
