"""A backup file that is cut off (USB stick pulled while copying), empty, or not a ZIP at all is reported as a
problem, never an exception, and nothing is restored from it. Plus refusal paths no test reached
(coverage 12/11): a broken compressed database, counts that differ from the manifest. Synthetic data."""
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from app.core import backup
from tests.helpers import DBTestCase

ROOT = Path(__file__).resolve().parents[1]


class TestUnreadableArchives(DBTestCase):
    def setUp(self):
        super().setUp()
        self.up = self.tmp / "uploads"
        self.arch = backup.create(self.db, self.up, self.tmp / "bk")
        self.raw = self.arch.read_bytes()

    def file(self, name, data):
        p = self.tmp / name
        p.write_bytes(data)
        return p

    def bad_files(self):
        half = len(self.raw) // 2
        return {"cut_off": self.file("cut.zip", self.raw[:half]), "empty": self.file("empty.zip", b""),
                "text": self.file("text.zip", "ليست نسخة".encode()),
                "flipped": self.file("flip.zip", self.raw[:half] + bytes([self.raw[half] ^ 0xFF]) + self.raw[half + 1:])}

    def test_verify_and_quick_check_report_instead_of_raising(self):
        for name, p in self.bad_files().items():
            with self.subTest(name):
                self.assertTrue(backup.verify_archive(p)["problems"])
                self.assertTrue(backup.quick_check(p))
        self.assertEqual(backup.verify_archive(self.arch)["problems"], [])
        self.assertEqual(backup.quick_check(self.arch), [])

    def test_restore_from_a_cut_off_file_restores_nothing(self):
        live = Path(self.db.path).read_bytes()
        with self.assertRaises(RuntimeError) as e:
            backup.restore(self.bad_files()["cut_off"], Path(self.db.path), self.up, self.tmp / "pre")
        self.assertIn("nothing restored", str(e.exception))
        self.assertEqual(Path(self.db.path).read_bytes(), live)
        self.assertFalse((self.tmp / "pre").exists())                     # not even a pre-restore copy was made

    def rezip(self, change):
        with zipfile.ZipFile(self.arch) as z:
            members = {n: z.read(n) for n in z.namelist()}
        change(members)
        out = self.tmp / f"re-{len(list(self.tmp.glob('re-*')))}.zip"
        with zipfile.ZipFile(out, "w") as w:
            for n, b in members.items():
                w.writestr(n, b)
        return out

    def test_a_broken_compressed_database_is_refused(self):
        p = self.rezip(lambda m: m.__setitem__("pharmacy.db.xz", b"\xfd7zXZ\x00" + b"\x00" * 64))   # valid CRC, bad xz
        self.assertTrue(any("database unreadable" in x for x in backup.verify_archive(p)["problems"]))
        self.assertTrue(backup.quick_check(p))

    def test_counts_that_differ_from_the_manifest_are_refused(self):
        def edit(m):
            man = json.loads(m["manifest.json"])
            man["counts"]["sales"] += 1                                    # a sale missing from the copy
            m["manifest.json"] = json.dumps(man).encode()
        self.assertIn("record counts differ from the manifest", backup.verify_archive(self.rezip(edit))["problems"])

    def test_a_manifest_that_is_not_json_is_refused(self):
        p = self.rezip(lambda m: m.__setitem__("manifest.json", b"{not json"))
        self.assertTrue(backup.verify_archive(p)["problems"])
        self.assertTrue(backup.quick_check(p))

    def test_the_command_line_says_so_without_a_traceback(self):
        env = {**os.environ, "PHARM_DATA_DIR": str(self.tmp / "cli"), "PYTHONPATH": str(ROOT)}
        r = subprocess.run([sys.executable, "manage.py", "verify", str(self.bad_files()["cut_off"])], cwd=ROOT,
                           env=env, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("PROBLEM:", r.stdout)
        self.assertNotIn("Traceback", r.stderr)
