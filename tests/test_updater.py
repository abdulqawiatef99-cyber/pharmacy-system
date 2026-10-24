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
        self.assertEqual((self.root / "app" / "x.py").read_text(encoding="utf-8"), "new")
        self.assertTrue((self.root / "app" / "y.py").exists())
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
        updater.apply_zip(archive({"manage.py": "new", "app/x.py": "new"}), self.root)
        updater.apply_zip(snap.read_bytes(), self.root)                         # roll back
        self.assertEqual((self.root / "manage.py").read_text(encoding="utf-8"), "old")
        with zipfile.ZipFile(snap) as z:
            self.assertFalse(any("data/" in n for n in z.namelist()))           # data never in code zip

    def test_token_errors_are_clear(self):
        with mock.patch.dict("os.environ", {"PHARM_UPDATE_TOKEN_FILE": str(self.root / "none.txt")}, clear=False):
            with mock.patch.dict("os.environ", {}, clear=False) as env:
                env.pop("PHARM_GITHUB_TOKEN", None)
                with self.assertRaises(updater.UpdateError):
                    updater.read_token()
        import urllib.error
        err = urllib.error.HTTPError("u", 401, "x", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(updater.UpdateError):
                updater.latest_sha("o/r", "main", "t")


if __name__ == "__main__":
    unittest.main()
