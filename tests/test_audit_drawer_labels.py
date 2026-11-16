"""#286: the audit screen names drawer commands as the drawer, never as opening the till/shift."""
import unittest

from app.core.audit import ACTION_LABELS


class DrawerAuditLabels(unittest.TestCase):
    def test_drawer_open_is_the_drawer(self):
        self.assertIn("الدرج", ACTION_LABELS["cash.drawer_open"])
        self.assertNotIn("الخزينة", ACTION_LABELS["cash.drawer_open"])

    def test_drawer_auto_has_a_label(self):
        self.assertIn("الدرج", ACTION_LABELS["cash.drawer_auto"])

    def test_shift_open_keeps_its_own_label(self):
        self.assertNotEqual(ACTION_LABELS["cash.shift_open"], ACTION_LABELS["cash.drawer_open"])


if __name__ == "__main__":
    unittest.main()
