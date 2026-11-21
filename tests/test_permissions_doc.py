"""The final permission list (part 3, A) in docs/PERMISSIONS.md matches the code: every code, label and default."""
import unittest
from pathlib import Path

from app.core.permissions import DEFAULT_ROLES, PERMISSIONS

DOC = Path(__file__).resolve().parents[1] / "docs" / "PERMISSIONS.md"


class TestPermissionList(unittest.TestCase):
    def test_every_permission_and_its_defaults_are_listed(self):
        rows = {}
        for line in DOC.read_text(encoding="utf-8").splitlines():
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) == 7 and cells[1].startswith("`"):
                rows[cells[1].strip("`")] = cells
        self.assertEqual(sorted(rows), sorted(PERMISSIONS))
        for code, cells in rows.items():
            self.assertEqual(cells[2], PERMISSIONS[code], code)
            got = [r for r, c in zip(("owner", "manager", "pharmacist", "assistant"), cells[3:]) if c == "✓"]
            self.assertEqual(got, [r for r in ("owner", "manager", "pharmacist", "assistant")
                                   if code in DEFAULT_ROLES[r][1]], code)
        self.assertIn(f"الإجمالي: {len(PERMISSIONS)} صلاحية", DOC.read_text(encoding="utf-8"))
