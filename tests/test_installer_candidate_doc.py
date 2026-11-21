"""docs/INSTALLER_CANDIDATE.md (manager 19/11): the PRs, the migration order and the device checklist."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestInstallerCandidateDoc(unittest.TestCase):
    def setUp(self):
        self.t = (ROOT / "docs" / "INSTALLER_CANDIDATE.md").read_text(encoding="utf-8")

    def test_migration_order_matches_the_prs(self):
        line = next(l for l in self.t.splitlines() if l.startswith("**Migration order:**"))
        at = [line.index(m) for m in ("0036", "0037", "0038", "0039")]
        self.assertEqual(at, sorted(at))
        for pr in ("#340", "#342", "#348", "#380", "#381"):
            self.assertIn(pr, self.t)

    def test_the_device_checklist_has_the_six_checks(self):
        sec = self.t[self.t.index("## 3. Heikal PC test checklist"):self.t.index("## 4.")]
        self.assertEqual(len(re.findall(r"^\d\. \*\*", sec, re.M)), 6)
        for what in ("pre-migrate", "update", "REHEARSAL", "RECONCILIATION OK", "treasury", "restore"):
            self.assertIn(what, sec)

    def test_fake_data_only(self):
        self.assertIn("fake data", self.t)
        self.assertIn("Synthetic data only", self.t)
