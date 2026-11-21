"""#70: the real-browser tests skip only for a precise reason, and never where CI requires them."""
import os
import subprocess
import unittest
from unittest import mock

from tests import helpers


def _node(code):
    return mock.patch("tests.helpers.subprocess.run",
                      return_value=subprocess.CompletedProcess([], code, b"", b""))


class TestBrowserProbe(unittest.TestCase):
    def skip_reason(self, env=None):
        with mock.patch.dict(os.environ, env or {}, clear=False):
            if not env:
                os.environ.pop("PHARM_REQUIRE_BROWSER", None)
            cls = helpers.needs_browser(type("T", (), {}))
        return getattr(cls, "__unittest_skip_why__", None) if getattr(cls, "__unittest_skip__", False) else None

    def test_missing_node(self):
        with mock.patch("tests.helpers.shutil.which", return_value=None):
            self.assertEqual(self.skip_reason(), "Node not available (runs in CI)")

    def test_missing_playwright_module(self):
        with mock.patch("tests.helpers.shutil.which", return_value="/bin/node"), _node(1):
            self.assertEqual(self.skip_reason(), "Node Playwright not available (runs in CI)")

    def test_missing_chromium(self):
        with mock.patch("tests.helpers.shutil.which", return_value="/bin/node"), _node(3):
            self.assertEqual(self.skip_reason(), "Playwright's Chromium is not installed (runs in CI)")

    def test_browser_available_runs(self):
        with mock.patch("tests.helpers.shutil.which", return_value="/bin/node"), _node(0):
            self.assertIsNone(self.skip_reason())

    def test_required_never_skips(self):
        with mock.patch("tests.helpers.shutil.which", return_value=None):
            self.assertIsNone(self.skip_reason({"PHARM_REQUIRE_BROWSER": "1"}))
