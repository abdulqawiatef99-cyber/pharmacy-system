"""Per-pharmacy screen customization: top bar, home buttons, Alt+digit shortcuts, hidden columns."""
from app.core import people, ui_settings
from app.core.errors import Forbidden, ValidationFailed
from tests.test_api import APICase, H
from tests.test_cashier_acceptance import Base

PW = "Staff-Pass-2026"


class TestUi(Base):
    def test_defaults_save_and_rules(self):
        d = ui_settings.get(self.db, self.ctx.org_id)
        self.assertEqual(d["nav_main"][0], "pos")
        n = ui_settings.save(self.db, self.ctx, {"nav_main": ["pos", "customers"], "shortcuts": {"1": "customers"},
                                                 "columns": {"invoices": [3, 16]}, "home": ["pos"]})
        self.assertEqual((n["nav_main"], n["shortcuts"], n["columns"]["invoices"], n["home"]),
                         (["pos", "customers"], {"1": "customers"}, [3, 16], ["pos"]))
        self.assertEqual(ui_settings.get(self.db, self.ctx.org_id), n)
        for bad in ({"nav_main": ["nope"]}, {"nav_main": ["pos", "pos"]}, {"shortcuts": {"12": "pos"}},
                    {"shortcuts": {"0": "pos"}}, {"columns": {"invoices": [99]}}, {"columns": {"items": list(range(7))}},
                    {"columns": {"other": [1]}}, {"home": ["pos"] * 2}):
            with self.assertRaises(ValidationFailed, msg=bad):
                ui_settings.save(self.db, self.ctx, bad)
        self.assertEqual(ui_settings.save(self.db, self.ctx, {"reset": True}),
                         {**ui_settings.DEFAULTS, "pos_keys": ui_settings.POS_KEYS_DEFAULT})
        people.add_user(self.db, self.ctx, username="pharm1", full_name="P", password=PW, role_code="pharmacist")
        with self.assertRaises(Forbidden):
            ui_settings.save(self.db, self.login("pharm1", PW), {"home": ["pos"]})

    def test_pos_keys_change_conflict_and_reset(self):
        """U2-19: the sale screen's keys are changed in «تخصيص الواجهة»; a key used twice is refused."""
        d = ui_settings.get(self.db, self.ctx.org_id)
        self.assertEqual(d["pos_keys"]["done"], "F9")
        n = ui_settings.save(self.db, self.ctx, {"pos_keys": {"done": "Ctrl+Enter"}})
        self.assertEqual((n["pos_keys"]["done"], n["pos_keys"]["search"]), ("Ctrl+Enter", "F1"))
        stored = self.db.one("SELECT value FROM org_settings WHERE org_id=? AND key='ui'", (self.ctx.org_id,))["value"]
        self.assertIn('"pos_keys": {"done": "Ctrl+Enter"}', stored)          # only the change is kept
        with self.assertRaises(ValidationFailed) as e:
            ui_settings.save(self.db, self.ctx, {"pos_keys": {"search": "Ctrl+Enter"}})
        self.assertEqual(e.exception.code, "key_conflict")
        # a swap in one request is fine
        n = ui_settings.save(self.db, self.ctx, {"pos_keys": {"search": "F2", "branch_stock": "F1"}})
        self.assertEqual((n["pos_keys"]["search"], n["pos_keys"]["branch_stock"]), ("F2", "F1"))
        for bad in ({"pos_keys": {"done": "F5"}}, {"pos_keys": {"done": "F12"}}, {"pos_keys": {"nope": "F1"}},
                    {"pos_keys": {"pay": "End"}}, {"pos_keys": {"pay": "Home"}}, {"pos_keys": {"done": "Ctrl+Insert"}},
                    {"pos_keys": ["F1"]}):
            with self.assertRaises(ValidationFailed, msg=bad):
                ui_settings.save(self.db, self.ctx, bad)
        n = ui_settings.save(self.db, self.ctx, {"pos_keys": {}})
        self.assertEqual(n["pos_keys"], ui_settings.POS_KEYS_DEFAULT)
        n = ui_settings.save(self.db, self.ctx, {"nav_main": ["pos"]})                # other changes keep them
        self.assertEqual(n["pos_keys"], ui_settings.POS_KEYS_DEFAULT)

    def test_help_pref(self):
        self.assertEqual(people.set_prefs(self.db, self.ctx, {"help": "off"})["help"], "off")
        with self.assertRaises(ValidationFailed):
            people.set_prefs(self.db, self.ctx, {"help": "maybe"})


class TestApi(APICase):
    def test_routes(self):
        self.http_login()
        self.assertIn("ui", self.c.get("/api/me").json())
        r = self.c.put("/api/settings/ui", json={"nav_main": ["pos", "reports"]}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.c.get("/api/settings/ui").json()["nav_main"], ["pos", "reports"])
