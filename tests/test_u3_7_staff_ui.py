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


import json as _json                    # noqa: E402
import shutil as _shutil                # noqa: E402
import subprocess as _sp                # noqa: E402
import unittest as _ut                  # noqa: E402
from pathlib import Path as _P          # noqa: E402


@_ut.skipUnless(_shutil.which("node"), "Node not available")
class TestShiftEndClock(_ut.TestCase):
    """Codex P1 on #421: the reminder window, by a controlled clock, day and overnight."""
    def test_minutes_left(self):
        js = (_P(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")
        fn = js[js.index("function shiftEndLeft("):js.index("async function staffUi(")]
        code = fn + """
const at = (h, m) => { const d = new Date(2026, 10, 22, h, m, 0, 0); return d; };
console.log(JSON.stringify([shiftEndLeft(at(17, 50), "18:00"), shiftEndLeft(at(23, 50), "00:00"),
  shiftEndLeft(at(0, 10), "00:30"), shiftEndLeft(at(18, 30), "18:00")]));"""
        r = _sp.run([_shutil.which("node"), "-e", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_json.loads(r.stdout), [10, 10, 20, -30])     # in the window, overnight, after the end
