"""U3-7 dhikr strip (on for all, off for some) and per-employee work hours: owner only, per pharmacy."""
from dataclasses import replace

from app.core import people, staff_ui
from app.core.errors import Forbidden, ValidationFailed
from tests.helpers import DBTestCase


class TestStaffUi(DBTestCase):
    def setUp(self):
        super().setUp()
        self.uid = people.add_user(self.db, self.ctx, username="emp1", full_name="موظف", password="Staff-Pass-2026",
                                   role_code="assistant")
        self.emp = self.login("emp1", "Staff-Pass-2026")

    def test_dhikr_and_hours(self):
        self.assertEqual(staff_ui.mine(self.db, self.emp)["dhikr"], [])                     # off by default
        staff_ui.save(self.db, self.ctx, {"dhikr_on": True, "hours": {self.uid: {"start": "10:00", "end": "18:00"}}})
        m = staff_ui.mine(self.db, self.emp)
        self.assertIn("سبحان الله", m["dhikr"])
        self.assertEqual(m["hours"], {"start": "10:00", "end": "18:00"})
        staff_ui.save(self.db, self.ctx, {"dhikr_off_users": [self.uid]})
        self.assertEqual(staff_ui.mine(self.db, self.emp)["dhikr"], [])
        self.assertTrue(staff_ui.mine(self.db, self.ctx)["dhikr"])                          # others still see it

    def test_validation_and_rights(self):
        with self.assertRaises(ValidationFailed):
            staff_ui.save(self.db, self.ctx, {"hours": {self.uid: {"start": "25:00", "end": "18:00"}}})
        with self.assertRaises(ValidationFailed):
            staff_ui.save(self.db, self.ctx, {"dhikr_off_users": ["someone-else"]})
        with self.assertRaises(Forbidden):
            staff_ui.save(self.db, self.emp, {"dhikr_on": True})
        _, other = self.second_tenant()
        with self.assertRaises(ValidationFailed):                                          # not his employee
            staff_ui.save(self.db, other, {"hours": {self.uid: {"start": "10:00", "end": "18:00"}}})
