"""Card machines and transfer wallets: editing and refusals (coverage 12/11: the edit path and the
duplicate-name conflict were never run). A rename onto an existing name was a server error."""
from app.core import people
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.sales import channels
from tests.helpers import DBTestCase
from tests.test_api import APICase, H

PW = "Staff-Pass-2026"


class TestChannels(DBTestCase):
    def setUp(self):
        super().setUp()
        channels.ensure_defaults(self.db)
        self.by = {c["name"]: c for c in channels.listing(self.db, self.ctx)}

    def test_defaults_once(self):
        channels.ensure_defaults(self.db)
        self.assertEqual(len(channels.listing(self.db, self.ctx)), len(channels.DEFAULTS))

    def test_add_rename_stop(self):
        out = channels.save(self.db, self.ctx, {"kind": "transfer", "name": "محفظة اتصالات"})
        new = next(c for c in out if c["name"] == "محفظة اتصالات")
        self.assertEqual(new["sort"], max(c["sort"] for c in out))
        self.assertTrue(self.db.one("SELECT 1 FROM audit_log WHERE action='payment_channel.save' AND entity_id=?",
                                    (new["id"],)))
        channels.save(self.db, self.ctx, {"name": "اتصالات كاش", "is_active": False}, new["id"])
        row = self.db.one("SELECT name, is_active FROM payment_channels WHERE id=?", (new["id"],))
        self.assertEqual((row["name"], row["is_active"]), ("اتصالات كاش", 0))
        channels.save(self.db, self.ctx, {"is_active": True}, new["id"])            # name not sent: kept
        self.assertEqual(self.db.one("SELECT name FROM payment_channels WHERE id=?", (new["id"],))[0], "اتصالات كاش")

    def test_same_name_is_a_conflict_on_add_and_on_rename(self):
        with self.assertRaises(Conflict):
            channels.save(self.db, self.ctx, {"kind": "transfer", "name": "إنستاباي"})
        with self.assertRaises(Conflict):
            channels.save(self.db, self.ctx, {"name": "إنستاباي"}, self.by["فودافون كاش"]["id"])
        self.assertEqual(self.db.one("SELECT name FROM payment_channels WHERE id=?",
                                     (self.by["فودافون كاش"]["id"],))[0], "فودافون كاش")

    def test_refusals(self):
        for bad in ({"kind": "cash", "name": "درج"}, {"kind": "card"}, {"kind": "card", "name": "x"}):
            with self.assertRaises(ValidationFailed, msg=bad):
                channels.save(self.db, self.ctx, bad)
        with self.assertRaises(ValidationFailed):
            channels.save(self.db, self.ctx, {"name": " "}, self.by["إنستاباي"]["id"])
        with self.assertRaises(NotFound):
            channels.save(self.db, self.ctx, {"name": "جهاز 2"}, "no-such-id")
        people.add_user(self.db, self.ctx, username="asst1", full_name="A", password=PW, role_code="assistant")
        with self.assertRaises(Forbidden):
            channels.save(self.db, self.login("asst1", PW), {"kind": "card", "name": "جهاز 2"})

    def test_another_pharmacy_cannot_touch_ours(self):
        _, other = self.second_tenant()
        with self.assertRaises(NotFound):
            channels.save(self.db, other, {"name": "مسروق"}, self.by["إنستاباي"]["id"])


class TestChannelsAPI(APICase):
    def test_rename_onto_existing_name_is_409_not_500(self):
        channels.ensure_defaults(self.db)
        self.http_login()
        chs = {c["name"]: c["id"] for c in self.c.get("/api/payment-channels").json()["channels"]}
        r = self.c.patch(f"/api/payment-channels/{chs['فودافون كاش']}", json={"name": "إنستاباي", "is_active": True},
                         headers=H)
        self.assertEqual(r.status_code, 409, r.text)
