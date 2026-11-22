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


class TestAppWindowFutureTraps(unittest.TestCase):
    """Owner 22/11 «اعمل حساب خوازيق المستقبل»."""

    def test_one_program_only_a_second_click_opens_the_window(self):
        with mock.patch.object(launcher, "running", return_value=True), \
                mock.patch.object(launcher, "open_program") as op, \
                mock.patch.object(launcher.releases, "stage0_lock") as lock:
            self.assertEqual(launcher.main(), 0)
        op.assert_called_once_with(launcher.URL)
        lock.assert_not_called()                     # no second server, no second launcher

    def test_the_window_never_blocks_printing_or_other_browsers(self):
        cmd = launcher.app_command("edge.exe", launcher.URL, Path("/h/app-window"))
        for bad in ("--kiosk", "--disable-print-preview", "--disable-popup-blocking", "--incognito"):
            self.assertFalse(any(c.startswith(bad) for c in cmd), bad)
        # its own profile: the pharmacy's normal browser (and the manager's tab on the same address) is untouched
        self.assertIn("--user-data-dir=" + str(Path("/h/app-window")), cmd)
        self.assertTrue(launcher.URL.startswith("http://127.0.0.1:"))

    def test_the_old_desktop_shortcut_still_runs_the_same_launcher(self):
        iss = (Path(launcher.ROOT) / "installer" / "pharmacy.iss").read_text(encoding="utf-8")
        shortcuts = [l for l in iss.splitlines() if l.startswith("Name:") and "{user" in l]
        self.assertGreaterEqual(len(shortcuts), 2)
        for l in shortcuts:
            self.assertIn(r'Parameters: """{app}\program\launcher.py"""', l)
        self.assertTrue(callable(launcher.main))


from tests.helpers import OWNER_PW, needs_browser          # noqa: E402
from tests.pos_browser import run_node                     # noqa: E402
from tests.test_api import APICase                         # noqa: E402

APP_SCRIPT = r"""
const { chromium } = require('playwright');
const fs = require('fs'), os = require('os'), path = require('path');
(async () => {
  const [base, user, pw, flags, fn] = process.argv.slice(1);
  // the launcher's own flags (--app and a profile of its own), as Edge / Chrome get them on the pharmacy PC
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'appwin-'));
  const args = JSON.parse(flags).filter((a) => !a.startsWith('--user-data-dir='));
  const ctx = await chromium.launchPersistentContext(dir, { args });
  const p = ctx.pages()[0] || await ctx.newPage();
  await p.goto(base + '/');
  await p.waitForSelector('#loginForm');
  await p.evaluate(async ([u, w]) => fetch('/api/auth/login', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'pharm' }, body: JSON.stringify({ username: u, password: w }) }), [user, pw]);
  await p.goto(base + '/#/shift');
  await p.waitForTimeout(500);
  // printing is a hidden frame, never a pop-up: it works the same in an app window
  const printed = await p.evaluate(async ([u, src]) => {
    const printInFrame = new Function('return ' + src)();     // the program's own function (app.js is a module)
    printInFrame(u);
    const f = document.getElementById('printFrame');
    await new Promise((r) => { f.onload = r; setTimeout(r, 4000); });
    return { loaded: !!f.contentDocument && f.contentDocument.readyState === 'complete', print: typeof f.contentWindow.print };
  }, ['/labels', fn]);     // a print page (labels; the receipt and shift report share its headers)
  // and a plain browser (another profile: the manager's tools) opens the same address at the same time
  const b = await chromium.launch();
  const q = await b.newPage();
  const r = await q.goto(base + '/');
  console.log(JSON.stringify({ printed, plain: r.status() }));
  await b.close(); await ctx.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestAppWindowInChromium(APICase):
    def test_app_window_prints_and_a_normal_tab_still_works(self):
        import json
        import re
        flags = launcher.app_command("chromium", "about:blank", Path("/tmp/x"))[1:]
        js = (Path(launcher.ROOT) / "app" / "web" / "app.js").read_text(encoding="utf-8")
        fn = re.search(r"function printInFrame\(url\) \{.*?\n\}", js, re.S).group(0)
        out = run_node(self, APP_SCRIPT, "owner", OWNER_PW, json.dumps(flags), fn)
        self.assertEqual(out["printed"], {"loaded": True, "print": "function"})
        self.assertEqual(out["plain"], 200)
