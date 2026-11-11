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


class TestBackupDefaultsOfOtherCommands(unittest.TestCase):
    """Codex on #91: restore, import-estock --apply and update also back up next to the data by default,
    and an explicit folder still wins."""

    def setUp(self):
        # resolve(): Windows temp paths come as 8.3 short names (RUNNER~1); the app resolves the long one
        self.tmp = Path(tempfile.mkdtemp(prefix="pharm-cli-")).resolve()
        self.data = self.tmp / "home" / "data"
        # only this data folder: the Windows release check sets PHARM_DB_PATH / PHARM_BACKUP_DIR for the whole run
        env = {k: v for k, v in os.environ.items() if not k.startswith("PHARM_")}
        self.env = mock.patch.dict(os.environ, {**env, "PHARM_DATA_DIR": str(self.data)}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        import manage
        self.manage = manage
        # init in its own process: an open connection here would lock the database files on Windows
        r = subprocess.run([sys.executable, str(ROOT / "manage.py"), "init", "--pharmacy", "صيدلية تجربة",
                            "--username", "owner", "--password", "Owner-Pass-2026"], capture_output=True, text=True,
                           encoding="utf-8", env={**os.environ, "PYTHONPATH": str(ROOT)}, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.cwd = os.getcwd()
        os.chdir(self.tmp)                                      # a relative default would land here
        self.addCleanup(os.chdir, self.cwd)

    def dests(self, argv):
        from app.core import backup
        seen = []

        def stop(db, uploads, dest):
            seen.append(Path(dest))
            raise RuntimeError("stop after choosing the folder")
        with mock.patch.object(backup, "create", side_effect=stop), self.assertRaises(RuntimeError):
            self.manage.main(argv)
        return seen

    def test_restore_pre_restore_snapshot(self):
        r = subprocess.run([sys.executable, str(ROOT / "manage.py"), "backup"], cwd=self.tmp, capture_output=True,
                           text=True, encoding="utf-8", env={**os.environ, "PYTHONPATH": str(ROOT)}, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        arch = next((self.tmp / "home" / "backups").glob("*.zip"))
        r = subprocess.run([sys.executable, str(ROOT / "manage.py"), "restore", str(arch), "--yes"], cwd=self.tmp,
                           capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONPATH": str(ROOT)},
                           timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(list((self.tmp / "home" / "backups" / "pre-restore").iterdir()))
        self.assertFalse((self.tmp / "backups").exists())

    def test_import_estock_apply(self):
        for name in ("cat.xlsx", "stock.xlsx"):
            (self.tmp / name).write_bytes(b"")
        base = ["import-estock", "--catalog", "cat.xlsx", "--stock", "stock.xlsx", "--username", "owner",
                "--password", "Owner-Pass-2026", "--apply"]
        self.assertEqual(self.dests(base), [self.tmp / "home" / "backups"])
        self.assertEqual(self.dests(base + ["--backup-dir", str(self.tmp / "mine")]), [self.tmp / "mine"])

    def test_update_remembers_its_channel(self):
        """#107: an update from develop records ref=develop, so the next check follows develop; a zip keeps
        the channel that was installed."""
        import subprocess as sp
        from app.core import backup, updater
        import release_bootstrap as releases
        staged = {"release": str(self.tmp), "id": "r1", "files": 1}
        seen = []
        (self.tmp / "u.zip").write_bytes(b"x")
        with mock.patch.object(updater, "read_token", return_value="t"), \
                mock.patch.object(updater, "latest_sha", return_value="b" * 40) as latest, \
                mock.patch.object(updater, "download", return_value=b"zip"), \
                mock.patch.object(updater, "verify_source"), \
                mock.patch.object(updater, "installed", return_value={"sha": "a" * 40, "ref": "develop"}), \
                mock.patch.object(updater, "apply_zip", return_value=staged), \
                mock.patch.object(releases, "check_compatible"), \
                mock.patch.object(updater, "installer_reasons", return_value=[]), \
                mock.patch.object(sp, "run", return_value=SimpleNamespace(returncode=0)), \
                mock.patch.object(backup, "create", return_value=self.tmp / "b.zip"), \
                mock.patch.object(backup, "verify_archive", return_value={"problems": []}), \
                mock.patch.object(releases, "activate", side_effect=lambda *a, **k: seen.append(a[3])):
            self.manage.main(["update", "--ref", "develop"])
            self.manage.main(["update", "--from-zip", "u.zip"])
            self.manage.main(["update"])          # no --ref on a develop install stays on develop, not main
        self.assertEqual([c[0][1] for c in latest.call_args_list], ["develop", "develop"])
        self.assertEqual([m["ref"] for m in seen], ["develop", "develop", "develop"])
        self.assertEqual(seen[0]["sha"], "b" * 40)

    def test_update_from_zip(self):
        import subprocess as sp
        from app.core import updater
        import release_bootstrap as releases
        (self.tmp / "u.zip").write_bytes(b"x")
        staged = {"release": str(self.tmp), "id": "r1", "files": 1}
        with mock.patch.object(updater, "apply_zip", return_value=staged), \
                mock.patch.object(releases, "check_compatible"), \
                mock.patch.object(updater, "installer_reasons", return_value=[]), \
                mock.patch.object(sp, "run", return_value=SimpleNamespace(returncode=0)):
            self.assertEqual(self.dests(["update", "--from-zip", "u.zip"]), [self.tmp / "home" / "backups"])
            self.assertEqual(self.dests(["update", "--from-zip", "u.zip", "--backup-dir", "mine"]), [Path("mine")])
