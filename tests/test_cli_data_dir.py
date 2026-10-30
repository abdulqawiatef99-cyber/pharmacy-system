"""#80: maintenance commands never create a new empty database (wrong folder / no PHARM_DATA_DIR), backups
default to the backups folder next to the data, and on Windows the default data folder is the installed one."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import config

ROOT = Path(__file__).resolve().parent.parent


class TestCliRefusesEmptyDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pharm-cli-"))
        env = {k: v for k, v in os.environ.items() if not k.startswith("PHARM_")}
        # LOCALAPPDATA points into the test folder: on Windows (CI) the default data folder must not exist either
        self.env = {**env, "PYTHONPATH": str(ROOT), "PYTHONUTF8": "1", "LOCALAPPDATA": str(self.tmp / "local")}

    def run_cli(self, *args, cwd=None, **extra):
        return subprocess.run([sys.executable, str(ROOT / "manage.py"), *args], cwd=cwd or self.tmp,
                              env={**self.env, **extra}, capture_output=True, text=True, encoding="utf-8", timeout=120)

    def test_wrong_folder_creates_nothing_and_fails(self):
        for args in (["backup"], ["verify"], ["feature", "org", "sales"],
                     ["import-estock", "--catalog", "a", "--stock", "b", "--username", "u", "--password", "p"]):
            r = self.run_cli(*args)
            self.assertNotEqual(r.returncode, 0, args)
            self.assertIn("لا توجد قاعدة بيانات", r.stderr, args)
            self.assertNotIn("backup written", r.stdout)
            self.assertFalse((self.tmp / "data").exists(), args)              # nothing was created
            self.assertFalse((self.tmp / "backups").exists(), args)
            self.assertFalse((self.tmp / "local").exists(), args)

    def test_init_then_backup_lands_next_to_the_data(self):
        data = self.tmp / "home" / "data"
        r = self.run_cli("init", "--pharmacy", "صيدلية تجربة", "--username", "owner", "--password", "Owner-Pass-2026",
                         PHARM_DATA_DIR=str(data))
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_cli("backup", PHARM_DATA_DIR=str(data))                   # no --dest
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(list((self.tmp / "home" / "backups").glob("*.zip"))), 1)
        self.assertFalse((self.tmp / "backups").exists())                       # not in the current folder
        self.assertEqual(self.run_cli("verify", PHARM_DATA_DIR=str(data)).returncode, 0)

    def test_existing_but_empty_database_is_not_backed_up(self):
        data = self.tmp / "empty"
        data.mkdir()
        from app.db import Database
        Database(data / "pharmacy.db").migrate()
        r = self.run_cli("backup", PHARM_DATA_DIR=str(data))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("فاضية", r.stderr)


class TestDefaultDataDir(unittest.TestCase):
    def test_windows_default_is_the_installed_folder(self):
        with mock.patch.object(config, "os", SimpleNamespace(name="nt", environ={"LOCALAPPDATA": "/u/AppData/Local"})):
            self.assertEqual(config.default_data_dir(), Path("/u/AppData/Local") / "PharmacyPlatform" / "data")
        with mock.patch.object(config, "os", SimpleNamespace(name="posix", environ={})):
            self.assertEqual(config.default_data_dir(), Path("data"))

    def test_release_bootstrap_uses_the_same_folder(self):
        # Codex on #91: code selection (active / pending release) reads the same data folder as the commands
        import release_bootstrap as rb
        for name, env in (("nt", {"LOCALAPPDATA": "/u/AppData/Local"}), ("posix", {}),
                          ("nt", {"LOCALAPPDATA": "/u/L", "PHARM_DATA_DIR": "/srv/d"})):
            fake = SimpleNamespace(name=name, environ=env)
            with mock.patch.object(config, "os", fake), mock.patch.object(rb, "os", fake), \
                    mock.patch.dict(os.environ, {k: v for k, v in env.items() if k == "PHARM_DATA_DIR"}, clear=False):
                if "PHARM_DATA_DIR" not in env:
                    os.environ.pop("PHARM_DATA_DIR", None)
                expect = config.load().data_dir if "PHARM_DATA_DIR" in env else config.default_data_dir().resolve()
                self.assertEqual(rb.data_dir(), expect, (name, env))

    def test_env_still_wins(self):
        with mock.patch.dict(os.environ, {"PHARM_DATA_DIR": "/srv/pharm/data"}):
            self.assertEqual(config.load().data_dir, Path("/srv/pharm/data").resolve())


if __name__ == "__main__":
    unittest.main()
