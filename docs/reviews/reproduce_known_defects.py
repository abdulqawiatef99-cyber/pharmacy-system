"""Explicit reproductions of known defects; NOT part of default test discovery.

Run from the repository root:
    python -m unittest docs.reviews.reproduce_known_defects -v

Both tests assert desired safe behavior and are expected to FAIL on baseline
69e3c4a. Only synthetic databases and temporary program folders are used.
No network service, real pharmacy, updater download, or paid API is called.
"""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import updater
from app.sales.cash import CashDrawer
from tests.test_api import APICase, H
from tests.test_updater import archive


class IdempotencyCrashBoundary(APICase):
    def test_retried_cash_payout_must_not_post_twice(self):
        CashDrawer(self.db).open(self.ctx, 10000)
        self.http_login()
        original = self.db.execute

        def fail_before_replay_record(sql, params=()):
            if sql.startswith("INSERT OR IGNORE INTO idempotency_keys"):
                raise OSError("synthetic failure after business commit")
            return original(sql, params)

        headers = {**H, "Idempotency-Key": "review-payout-once"}
        body = {"amount": 1000, "kind": "payout", "note": "synthetic review payout"}
        with patch.object(self.db, "execute", side_effect=fail_before_replay_record):
            response = self.c.post("/api/cash/movements", headers=headers, json=body)
        self.assertEqual(response.status_code, 500)
        # The first request failed at the API, but already committed the payout.
        self.assertEqual(CashDrawer(self.db).summary(self.ctx)["expected_cash"], 9000)
        response = self.c.post("/api/cash/movements", headers=headers, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        observed = CashDrawer(self.db).summary(self.ctx)["expected_cash"]
        self.assertEqual(observed, 9000,
                         f"Same request posted twice: expected 9000, observed {observed}")


class UpdaterPartialReplacement(unittest.TestCase):
    def test_copy_failure_must_not_leave_mixed_program_versions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "app").mkdir()
            (root / "manage.py").write_text("old", encoding="utf-8")
            (root / "app" / "x.py").write_text("old", encoding="utf-8")
            payload = archive({"manage.py": "new", "app/x.py": "new"})
            original = shutil.copy2
            calls = 0

            def copy_then_fail(src, dest, *args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("synthetic disk/copy failure")
                return original(src, dest, *args, **kwargs)

            with patch("app.core.updater.shutil.copy2", side_effect=copy_then_fail):
                with self.assertRaises(OSError):
                    updater.apply_zip(payload, root)
            observed = {
                "manage.py": (root / "manage.py").read_text(encoding="utf-8"),
                "app/x.py": (root / "app" / "x.py").read_text(encoding="utf-8"),
            }
            self.assertEqual(observed, {"manage.py": "old", "app/x.py": "old"},
                             f"Partial update left mixed versions: {observed}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
