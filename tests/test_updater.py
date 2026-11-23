"""Code updater: never touches data, refuses bad archives, can roll back."""
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from app.core import updater


def archive(files: dict[str, str], top="owner-repo-abc123") -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for name, text in files.items():
            z.writestr(f"{top}/{name}" if top else name, text)
    return b.getvalue()


class TestUpdater(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "app").mkdir()
        (self.root / "manage.py").write_text("old", encoding="utf-8")
        (self.root / "app" / "x.py").write_text("old", encoding="utf-8")
        (self.root / "data").mkdir()
        (self.root / "data" / "pharmacy.db").write_text("REAL DATA", encoding="utf-8")

    def test_new_code_replaces_old_and_data_is_untouched(self):
        z = archive({"manage.py": "new", "app/x.py": "new", "app/y.py": "added",
                     "data/pharmacy.db": "EVIL", "backups/x": "EVIL", ".venv/x": "EVIL"})
        res = updater.apply_zip(z, self.root)
        selected = Path(res["release"])
        self.assertEqual((self.root / "app" / "x.py").read_text(encoding="utf-8"), "old")
        self.assertEqual((selected / "app" / "x.py").read_text(encoding="utf-8"), "new")
        self.assertTrue((selected / "app" / "y.py").exists())
        self.assertEqual((self.root / "data" / "pharmacy.db").read_text(encoding="utf-8"), "REAL DATA")
        self.assertFalse((self.root / "backups").exists())
        self.assertEqual(res["files"], 3)

    def test_bad_archives_change_nothing(self):
        for bad in (b"not a zip",
                    archive({"readme.txt": "x"}),                                # not our program
                    archive({"manage.py": "x", "app/a.py": "x"}, top=None),      # no single top folder
                    archive({"manage.py": "x", "app/a.py": "x", "../evil.py": "x"})):
            with self.assertRaises(updater.UpdateError):
                updater.apply_zip(bad, self.root)
        self.assertEqual((self.root / "manage.py").read_text(encoding="utf-8"), "old")

    def test_snapshot_is_a_valid_update_archive_for_rollback(self):
        snap = updater.snapshot(self.root, self.root / "backups")
        candidate = updater.apply_zip(archive({"manage.py": "new", "app/x.py": "new"}), self.root)
        self.assertEqual((Path(candidate["release"]) / "manage.py").read_text(), "new")
        previous = updater.apply_zip(snap.read_bytes(), self.root)  # prepare previous complete code
        self.assertEqual((Path(previous["release"]) / "manage.py").read_text(), "old")
        self.assertEqual((self.root / "manage.py").read_text(encoding="utf-8"), "old")
        with zipfile.ZipFile(snap) as z:
            self.assertFalse(any("data/" in n for n in z.namelist()))           # data never in code zip

    def test_token_errors_are_clear(self):
        with mock.patch.dict("os.environ", {"PHARM_UPDATE_TOKEN_FILE": str(self.root / "none.txt")}, clear=False):
            with mock.patch.dict("os.environ", {}, clear=False) as env:
                env.pop("PHARM_GITHUB_TOKEN", None)
                self.assertIsNone(updater.read_token())   # public repo: no key means anonymous, not an error
        import urllib.error
        err = urllib.error.HTTPError("u", 401, "x", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(updater.UpdateError):
                updater.latest_sha("o/r", "main", "t")


if __name__ == "__main__":
    unittest.main()


from app.core import smart_update, update_sign
import release_bootstrap as releases
from tests.test_api import APICase, H


class TestUpdateAPI(APICase):
    def test_owner_sees_signed_status_failure_and_can_prepare_an_update(self):
        self.http_login()
        with mock.patch.object(update_sign, "trusted_keys", return_value=[b"k" * 32]), \
                mock.patch.object(smart_update, "bootstrap_level", return_value=releases.BOOTSTRAP), \
                mock.patch.object(smart_update, "read_manifest", side_effect=updater.UpdateError("No signed release")):
            st = self.c.get("/api/system/update").json()
        self.assertEqual(st["smart"]["error"], "No signed release")
        self.assertFalse(st["available"])
        done = mock.Mock(returncode=0, stdout="SMART-PREPARE", stderr="")
        with mock.patch("subprocess.run", return_value=done) as run:
            result = self.c.post("/api/system/update", headers=H, json={"action": "prepare"})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()["ok"])
        self.assertEqual(run.call_args.args[0][-2:], ["update", "--prepare"])
        self.assertEqual(self.db.one("SELECT count(*) FROM audit_log WHERE action='system.update.prepare'")[0], 1)


class TestUpdateWithoutKey(APICase):
    def test_a_pc_without_a_key_file_still_sees_signed_updates(self):
        self.http_login()
        with mock.patch.dict("os.environ", {"PHARM_UPDATE_TOKEN_FILE": str(self.tmp / "missing.txt")}) as env, \
                mock.patch.object(update_sign, "trusted_keys", return_value=[b"k" * 32]), \
                mock.patch.object(smart_update, "bootstrap_level", return_value=releases.BOOTSTRAP), \
                mock.patch.object(smart_update, "read_manifest", return_value={
                    "sha": "e" * 40, "commit_time": 20, "channel": "main",
                    "python": smart_update.python_tag(), "migrations": [], "notes": [],
                }) as manifest:
            env.pop("PHARM_GITHUB_TOKEN", None)
            st = self.c.get("/api/system/update").json()
        self.assertIsNone(manifest.call_args.args[0].token)
        self.assertEqual((st["latest"], st["available"]), ("e" * 40, True))


class TestUpdateChannel(APICase):
    """The signed manifest is read from the channel selected by the installer."""

    def status(self, info, latest="b" * 40):
        def signed(source, _keys):
            return {"sha": latest, "commit_time": 20, "channel": source.channel,
                    "python": smart_update.python_tag(), "migrations": [], "notes": []}

        with mock.patch.object(updater, "installed", return_value={"commit_time": 10, **info}), \
                mock.patch.object(updater, "read_token", return_value="t"), \
                mock.patch.object(update_sign, "trusted_keys", return_value=[b"k" * 32]), \
                mock.patch.object(smart_update, "bootstrap_level", return_value=releases.BOOTSTRAP), \
                mock.patch.object(smart_update, "read_manifest", side_effect=signed) as manifest:
            status = self.c.get("/api/system/update").json()
        return status, manifest

    def test_channel_rules(self):
        self.assertEqual(updater.channel({}), "main")
        self.assertEqual(updater.channel({"ref": "main"}), "main")
        self.assertEqual(updater.channel({"ref": "develop", "source": "installer"}), "develop")
        self.assertIsNone(updater.channel({"ref": "review/work"}))

    def test_develop_installer_checks_develop_and_sees_an_update(self):
        self.http_login()
        st, manifest = self.status({"sha": "a" * 40, "source": "installer", "ref": "develop"})
        self.assertEqual(manifest.call_args.args[0].channel, "develop")
        self.assertEqual((st["channel"], st["available"], st["latest"]), ("develop", True, "b" * 40))
        self.assertNotIn("candidate", st)

    def test_main_and_unknown_installs_still_use_main(self):
        import itertools

        self.http_login()
        clock = itertools.count(10_000, 1000)
        for info in ({"sha": "a" * 40, "source": "installer", "ref": "main"}, {"sha": "a" * 40}, {}):
            with mock.patch("app.api.app.time.time", side_effect=lambda: next(clock)):
                st, manifest = self.status(info, latest="a" * 40)
            self.assertEqual(manifest.call_args.args[0].channel, "main", info)
            self.assertEqual((st["channel"], st["available"]), ("main", info.get("sha") != "a" * 40), info)

    def test_button_uses_signed_prepare(self):
        self.http_login()
        done = mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch("subprocess.run", return_value=done) as run:
            result = self.c.post("/api/system/update", headers=H, json={"action": "prepare"})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(run.call_args.args[0][-2:], ["update", "--prepare"])

    def test_work_branch_install_has_no_channel(self):
        self.http_login()
        with mock.patch.object(updater, "contains", return_value=False) as has:
            st, manifest = self.status({"sha": "a" * 40, "source": "installer", "ref": "review/work"})
        self.assertEqual(has.call_args.args[1:3], ("a" * 40, "main"))
        manifest.assert_not_called()
        self.assertEqual((st["channel"], st["candidate"], st["available"]), (None, "review/work", False))
