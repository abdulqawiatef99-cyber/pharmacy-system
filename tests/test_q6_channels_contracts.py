"""Night queue item 6 (test gaps): payment channels, contracts and notifications paths that no test walked.
Found: renaming a card machine or wallet to a name already used was a raw database error (HTTP 500); now the
same "already exists" conflict as adding it twice. Synthetic data only."""
from app.core import notify, people
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.sales import channels, contracts
from tests.helpers import DBTestCase
from tests.test_api import APICase, H

PW = "Staff-Pass-2026"


class Channels(DBTestCase):
    def setUp(self):
        super().setUp()
        channels.ensure_defaults(self.db)
        self.rows = {r["name"]: r for r in channels.listing(self.db, self.ctx)}

    def test_rename_to_a_used_name_is_a_conflict_and_changes_nothing(self):
        vf = self.rows["فودافون كاش"]
        with self.assertRaises(Conflict):
            channels.save(self.db, self.ctx, {"name": "إنستاباي"}, vf["id"])
        self.assertEqual(self.db.one("SELECT name FROM payment_channels WHERE id=?", (vf["id"],))["name"], "فودافون كاش")

    def test_rename_deactivate_and_validation(self):
        vf = self.rows["فودافون كاش"]
        out = channels.save(self.db, self.ctx, {"name": "فودافون كاش 2", "is_active": False}, vf["id"])
        r = next(x for x in out if x["id"] == vf["id"])
        self.assertEqual((r["name"], r["is_active"]), ("فودافون كاش 2", 0))
        r = next(x for x in channels.save(self.db, self.ctx, {}, vf["id"]) if x["id"] == vf["id"])
        self.assertEqual((r["name"], r["is_active"]), ("فودافون كاش 2", 0))    # empty change keeps both
        with self.assertRaises(ValidationFailed):
            channels.save(self.db, self.ctx, {"name": "x", "kind": "card"})
        with self.assertRaises(ValidationFailed):
            channels.save(self.db, self.ctx, {"name": "جهاز 2", "kind": "cash"})
        with self.assertRaises(NotFound):
            channels.save(self.db, self.ctx, {"name": "جهاز 2"}, "no-such-id")
        _, other = self.second_tenant()
        with self.assertRaises(NotFound):                                       # another pharmacy's channel
            channels.save(self.db, other, {"name": "مسروق"}, vf["id"])


class ChannelsApi(APICase):
    def test_rename_conflict_is_409_not_500(self):
        self.http_login()
        rows = {r["name"]: r for r in self.c.get("/api/payment-channels", headers=H).json()["channels"]}
        r = self.c.patch(f"/api/payment-channels/{rows['فودافون كاش']['id']}", json={"name": "إنستاباي"}, headers=H)
        self.assertEqual(r.status_code, 409, r.text)


class Contracts(DBTestCase):
    def test_update_duplicate_missing_and_short_name(self):
        contracts.save(self.db, self.ctx, {"name": "نقابة المهندسين", "discount_local_bp": 500})
        contracts.save(self.db, self.ctx, {"name": "نادي الصيد"})
        ids = {c["name"]: c["id"] for c in contracts.listing(self.db, self.ctx)}
        out = contracts.save(self.db, self.ctx, {"discount_imported_bp": 300, "is_active": False}, ids["نادي الصيد"])
        c = next(x for x in out if x["id"] == ids["نادي الصيد"])
        self.assertEqual((c["discount_imported_bp"], c["is_active"], c["name"]), (300, 0, "نادي الصيد"))
        a = self.db.one("SELECT before_json, after_json FROM audit_log WHERE action='contract.update'")
        self.assertIn('"is_active": 1', a["before_json"])
        with self.assertRaises(Conflict):
            contracts.save(self.db, self.ctx, {"name": "نقابة المهندسين"}, ids["نادي الصيد"])
        with self.assertRaises(NotFound):
            contracts.save(self.db, self.ctx, {"name": "أي جهة"}, "no-such-id")
        with self.assertRaises(ValidationFailed):
            contracts.save(self.db, self.ctx, {"name": " x "})


class Notifications(DBTestCase):
    def test_unread_only_and_marking_some(self):
        with self.db.transaction():
            ids = [notify.emit(self.db, self.ctx, "test", "info", f"إشعار {i}") for i in range(3)]
        with self.assertRaises(ValueError):
            with self.db.transaction():
                notify.emit(self.db, self.ctx, "test", "fatal", "x")
        self.assertEqual(notify.mark_read(self.db, self.ctx, []), 0)
        self.assertEqual(notify.mark_read(self.db, self.ctx, ids[:2]), 2)
        unread = notify.listing(self.db, self.ctx, unread_only=True)
        self.assertEqual([n["id"] for n in unread["items"]], [ids[2]])
        self.assertEqual(unread["unread"], 1)
        with self.assertRaises(ValidationFailed):
            notify.mark_read(self.db, self.ctx, "all")
        with self.assertRaises(ValidationFailed):
            notify.mark_read(self.db, self.ctx, [1, 2])
        _, other = self.second_tenant()
        self.assertEqual(notify.mark_read(self.db, other, [ids[2]]), 0)          # not theirs to read
        self.assertEqual(notify.unread_count(self.db, self.ctx), 1)

    def test_who_may_not_read_counts_zero(self):
        people.add_user(self.db, self.ctx, username="asst1", full_name="A", password=PW, role_code="assistant")
        with self.db.transaction():
            notify.emit(self.db, self.ctx, "test", "warning", "x")
        self.assertEqual(notify.unread_count(self.db, self.login("asst1", PW)), 0)
