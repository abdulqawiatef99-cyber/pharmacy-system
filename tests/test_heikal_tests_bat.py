"""scripts/windows/heikal-tests.bat: the one-click restore drill + scale run for the test PC (#253, G5)."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BAT = ROOT / "scripts" / "windows" / "heikal-tests.bat"


class HeikalTestsBat(unittest.TestCase):
    def setUp(self):
        self.raw = BAT.read_bytes()
        self.text = self.raw.decode("ascii")

    def test_crlf_line_ends(self):
        # cmd.exe mis-reads labels and goto in LF-only batch files
        self.assertNotIn(b"\n", self.raw.replace(b"\r\n", b""))

    def test_restore_never_touches_live_data(self):
        lines = self.text.splitlines()
        set_at = next(i for i, l in enumerate(lines) if l.startswith('set "PHARM_DATA_DIR=%DRILL%'))
        restore_at = next(i for i, l in enumerate(lines) if "manage.py restore" in l)
        clear_at = next(i for i, l in enumerate(lines) if l.strip() == 'set "PHARM_DATA_DIR="')
        self.assertLess(set_at, restore_at)
        self.assertLess(restore_at, clear_at)
        self.assertEqual(self.text.count("manage.py restore"), 1)

    def test_referenced_scripts_and_profiles_exist(self):
        # read PROFILES from the source: importing real_years changes the process time zone
        src = (ROOT / "scripts" / "perf" / "real_years.py").read_text(encoding="utf-8")
        block = src.split("PROFILES = {", 1)[1].split("\n}", 1)[0]
        known = set(re.findall(r'^\s*"(\w+)":', block, re.M))
        self.assertIn("medium", known)
        for p in re.findall(r"--profile (\w+)", self.text):
            self.assertIn(p, known)
        self.assertTrue((ROOT / "scripts" / "perf" / "scale.py").is_file())
        self.assertTrue((ROOT / "manage.py").is_file())


if __name__ == "__main__":
    unittest.main()
