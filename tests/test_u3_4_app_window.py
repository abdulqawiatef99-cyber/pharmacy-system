"""U3-4 the program in its own window: Edge or Chrome with --app and a profile of its own; the default browser when
neither is there, off by PHARM_APP_WINDOW=0, and never on Linux."""
import os
import unittest
from pathlib import Path
from unittest import mock

import launcher


class TestAppWindow(unittest.TestCase):
    def test_finds_edge_first(self):
        env = {"PROGRAMFILES(X86)": r"C:\PF86", "PROGRAMFILES": r"C:\PF", "LOCALAPPDATA": r"C:\L"}
        have = {os.path.join(r"C:\PF86", r"Microsoft\Edge\Application\msedge.exe"),
                os.path.join(r"C:\L", r"Google\Chrome\Application\chrome.exe")}
        with mock.patch.object(launcher.os, "name", "nt"):
            self.assertTrue(launcher.app_browser(env, have.__contains__).endswith("msedge.exe"))
            self.assertIsNone(launcher.app_browser({**env, "PHARM_APP_WINDOW": "0"}, have.__contains__))
            self.assertIsNone(launcher.app_browser(env, lambda p: False))

    def test_not_on_linux(self):
        with mock.patch.object(launcher.os, "name", "posix"):
            self.assertIsNone(launcher.app_browser({"PROGRAMFILES": "x"}, lambda p: True))

    def test_command(self):
        cmd = launcher.app_command("edge.exe", "http://127.0.0.1:8000/", Path("/h/app-window"))
        self.assertIn("--app=http://127.0.0.1:8000/", cmd)
        self.assertTrue(any(c.startswith("--user-data-dir=") for c in cmd))

    def test_falls_back_to_the_default_browser(self):
        with mock.patch.object(launcher, "app_browser", return_value=None), \
                mock.patch.object(launcher.webbrowser, "open") as wb:
            launcher.open_program("http://x/")
        wb.assert_called_once_with("http://x/")
        with mock.patch.object(launcher, "app_browser", return_value="edge.exe"), \
                mock.patch.object(launcher.subprocess, "Popen", side_effect=OSError), \
                mock.patch.object(launcher.webbrowser, "open") as wb:
            launcher.open_program("http://x/")
        wb.assert_called_once_with("http://x/")
