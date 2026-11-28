"""The owner's rules live only in docs/OWNER_RULES.md; a cancelled rule must not come back anywhere."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_rules", ROOT / "scripts" / "check_rules.py")
check_rules = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_rules)


class TestCancelledRules(unittest.TestCase):
    def test_no_cancelled_rule_in_the_repository(self):
        hits = check_rules.scan(check_rules.tracked_files())
        self.assertEqual(hits, [], "\n".join(hits))

    def test_the_guard_catches_a_cancelled_rule(self):
        self.assertTrue(check_rules.PATTERN.search("Installing on the pharmacy PC waits for «نزّله»"))
        self.assertTrue(check_rules.PATTERN.search("إي ستوك هو الكاشير"))
        self.assertFalse(check_rules.PATTERN.search("زرار «نزّلها» بينزّل الملف"))

    def test_rules_file_is_the_single_source(self):
        text = (ROOT / "docs" / "OWNER_RULES.md").read_text(encoding="utf-8")
        self.assertIn("آخر تعديل:", text.splitlines()[1])
        for name in ("CLAUDE.md", "AGENTS.md"):
            self.assertIn("docs/OWNER_RULES.md", (ROOT / name).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
