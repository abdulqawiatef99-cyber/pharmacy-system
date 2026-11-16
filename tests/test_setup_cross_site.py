"""First-run setup and first-run restore refuse cross-site requests.

Before any pharmacy exists there is no session to protect them, so a web page open in the browser on
the pharmacy PC could POST to them (a form or a text/plain fetch needs no permission from the server).
Both now need the X-Requested-With header the program's own screens send, which a cross-site page
cannot add without a CORS preflight that the server never grants."""
import os
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from starlette.testclient import TestClient

from app import config
from app.api.app import create_app
from app.core import security
from app.db import Database

security.set_cost(2 ** 10)
H = {"X-Requested-With": "pharm"}
BODY = '{"pharmacy":"evil","username":"attacker","password":"Attacker-Pass-2026"}'


class TestSetupCrossSite(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "t.db")
        self.db.migrate()
        self.app = create_app(self.db, replace(config.load(), upload_dir=self.tmp / "up", data_dir=self.tmp))
        self.c = TestClient(self.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_setup_without_the_header_is_refused(self):
        for headers in ({"content-type": "text/plain", "origin": "http://evil.example", "sec-fetch-site": "cross-site"},
                        {"content-type": "application/json"},
                        {"content-type": "text/plain", "x-requested-with": "XMLHttpRequest"}):
            with self.subTest(headers=headers):
                r = self.c.post("/api/setup", content=BODY, headers=headers)
                self.assertEqual(r.status_code, 403, r.text)
                self.assertEqual(r.json()["error"]["code"], "csrf")
                self.assertNotIn("set-cookie", r.headers)
        self.assertEqual(self.db.one("SELECT count(*) n FROM organizations")["n"], 0)
        self.assertTrue(self.c.get("/api/setup").json()["needed"])      # reading the status needs no header

    def test_setup_from_the_programs_own_screen_still_works(self):
        r = self.c.post("/api/setup", content=BODY, headers={**H, "content-type": "application/json"})
        self.assertEqual(r.status_code, 201, r.text)

    def test_restore_without_the_header_is_refused_before_anything_is_written(self):
        files = {"file": ("b.zip", b"not a zip")}
        with mock.patch.dict(os.environ, {"PHARM_LAUNCHER": "1"}), mock.patch("threading.Timer") as timer:
            r = self.c.post("/api/setup/restore", files=files, headers={"origin": "http://evil.example"})
            self.assertEqual(r.status_code, 403, r.text)
            self.assertEqual(r.json()["error"]["code"], "csrf")
            self.assertFalse((self.tmp / "pending_restore.zip").exists())
            r = self.c.post("/api/setup/restore", files=files, headers=H)
            self.assertEqual(r.status_code, 422)                       # reaches the archive check: bad file
            timer.assert_not_called()
