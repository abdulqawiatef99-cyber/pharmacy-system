"""One fixed folder for the owner's scripts, refreshed by every start of the running release (#113, 18/11)."""
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.core import tools_folder as tf

ROOT = Path(__file__).resolve().parents[1]


class TestRefresh(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pharm-tools-"))
        self.rel = self.tmp / "releases" / "abc"
        (self.rel / "scripts" / "windows").mkdir(parents=True)
        for n in ("estock-db-read.bat", "estock-db-read.ps1", "heikal-tests.bat", "start.bat"):
            (self.rel / "scripts" / "windows" / n).write_bytes(f"rem {n} v1\r\n".encode())
        self.home, self.desk = self.tmp / "home", self.tmp / "Desktop"
        self.desk.mkdir()

    def test_copies_only_the_owner_tools_and_keeps_a_desktop_shortcut(self):
        r = tf.refresh(self.rel, self.home, desktop=self.desk)
        tools = self.home / "tools"
        self.assertEqual(sorted(p.name for p in tools.iterdir()), sorted(["estock-db-read.bat", "estock-db-read.ps1", tf.README]))
        self.assertEqual((tools / "estock-db-read.bat").read_bytes(), b"rem estock-db-read.bat v1\r\n")
        url = (self.desk / tf.SHORTCUT).read_text(encoding="utf-8")
        self.assertIn("[InternetShortcut]", url)
        self.assertIn(tools.resolve().as_uri(), url)
        self.assertEqual(set(r["written"]), {"estock-db-read.bat", "estock-db-read.ps1", tf.README})

    def test_same_release_writes_nothing_and_a_new_release_replaces_changed_files(self):
        tf.refresh(self.rel, self.home, desktop=self.desk)
        bat = self.home / "tools" / "estock-db-read.bat"
        before = bat.stat().st_mtime_ns
        self.assertEqual(tf.refresh(self.rel, self.home, desktop=self.desk)["written"], [])   # a running .bat is untouched
        self.assertEqual(bat.stat().st_mtime_ns, before)
        new = self.tmp / "releases" / "def"
        (new / "scripts" / "windows").mkdir(parents=True)
        (new / "scripts" / "windows" / "estock-db-read.bat").write_bytes(b"rem v2\r\n")
        (new / "scripts" / "windows" / "estock-db-import.bat").write_bytes(b"rem import\r\n")
        r = tf.refresh(new, self.home, desktop=self.desk)
        self.assertEqual(sorted(r["written"]), ["estock-db-import.bat", "estock-db-read.bat"])
        self.assertEqual(bat.read_bytes(), b"rem v2\r\n")
        self.assertFalse(list((self.home / "tools").glob("*.part")))

    def test_never_raises(self):
        with mock.patch.object(tf, "_write_if_changed", side_effect=OSError("disk full")), \
                self.assertLogs("pharm.tools", "ERROR"):
            self.assertIsNone(tf.refresh(self.rel, self.home, desktop=self.desk)["shortcut"])

    def test_no_desktop_is_fine(self):
        with mock.patch.object(tf, "desktop_dir", return_value=None):
            r = tf.refresh(self.rel, self.home)
        self.assertIsNone(r["shortcut"])
        self.assertTrue((self.home / "tools" / "estock-db-read.bat").exists())


class TestTheListedScriptsWorkFromTheToolsFolder(unittest.TestCase):
    """Copied out of the program folder, a script must find the installed program by its standard place and
    use %~dp0 only for its own siblings (which are copied too)."""

    def test_each_listed_script(self):
        for name in tf.TOOLS:
            f = ROOT / "scripts" / "windows" / name
            if not f.exists():          # a script of a later change (the list may name it first)
                continue
            text = f.read_text(encoding="utf-8-sig", errors="replace")
            code = "\n".join(l for l in text.splitlines() if not l.strip().upper().startswith("REM"))
            for m in re.finditer(r"%~dp0([^\"\s]*)", code):
                rest = m.group(1)
                ok = (rest in (r"..\..\..\python\pythonw.exe", r"..\..\..", ".")      # the installed-program probe
                      or rest in tf.TOOLS)                                                # a sibling, copied too
                self.assertTrue(ok, f"{name}: %~dp0{rest}")
            if name.endswith(".bat") and "%MANAGE%" in text:
                self.assertIn(r'set "INST=%LOCALAPPDATA%\Programs\PharmacyPlatform"', text, name)

    def test_the_excluded_ones_really_need_the_program_folder(self):
        for name in ("backup.bat", "start.bat", "update.bat", "install.bat", "heikal-tests.bat"):
            self.assertNotIn(name, tf.TOOLS)
            self.assertIn("%~dp0", (ROOT / "scripts" / "windows" / name).read_text(encoding="utf-8", errors="replace"))


class TestServeRefreshesUnderTheLauncherOnly(unittest.TestCase):
    def run_serve(self, launcher):
        import manage
        tmp = Path(tempfile.mkdtemp(prefix="pharm-serve-"))
        s = SimpleNamespace(data_dir=tmp / "data", cookie_secure=False)
        a = SimpleNamespace(host="127.0.0.1", port=8000)
        env = {"PHARM_LAUNCHER": "1"} if launcher else {}
        with mock.patch.dict(os.environ, env), mock.patch("app.core.tools_folder.refresh") as ref, \
                mock.patch("uvicorn.run"), mock.patch.object(manage, "_db"), mock.patch("app.api.app.create_app"):
            if not launcher:
                os.environ.pop("PHARM_LAUNCHER", None)
            manage.cmd_serve(a, s)
        return ref, tmp

    def test_launcher_start_refreshes_the_tools_of_the_running_release(self):
        ref, tmp = self.run_serve(True)
        ref.assert_called_once_with(ROOT, tmp)

    def test_a_developer_run_does_not(self):
        ref, _ = self.run_serve(False)
        ref.assert_not_called()


if __name__ == "__main__":
    unittest.main()
