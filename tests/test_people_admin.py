"""The owner's users screen: a person's own discount limit, switching a person off, and resetting a password.

These three actions had no test (core/people.py at 83% line coverage). Each is checked through the API the
screen uses, with what it must change, what it must leave alone and the audit row. Synthetic data only.
"""
import json

from starlette.testclient import TestClient

from app.core import people
from tests.test_api import APICase, H

PW = "Staff-Pass-2026"
NEW = "Fresh-Pass-2027"


class PeopleAdmin(APICase):
    def setUp(self):
        super().setUp()
        self.http_login()
        self.uid = people.add_user(self.db, self.ctx, username="asst", full_name="مساعد", password=PW,
                                   role_code="assistant", pin="8642")
        self.a = TestClient(self.app)
        self.http_login("asst", PW, client=self.a)

    def act(self, action, expected=200, uid=None, **body):
        r = self.c.post(f"/api/users/{uid or self.uid}/{action}", json=body, headers=H)
        self.assertEqual(r.status_code, expected, r.text)
        return r.json()

    def last_audit(self, action):
        r = self.db.one("SELECT before_json, after_json, user_id FROM audit_log WHERE action=? ORDER BY rowid DESC LIMIT 1",
                        (action,))
        return r and {"before": json.loads(r["before_json"] or "null"), "after": json.loads(r["after_json"] or "null"),
                      "by": r["user_id"]}

    def limit(self):
        return self.db.one("SELECT m.max_discount_bp FROM memberships m WHERE m.user_id=?", (self.uid,))["max_discount_bp"]

    # ------------------------------------------------------------------ discount limit
    def test_own_discount_limit_then_back_to_the_role(self):
        self.act("discount", max_discount_bp=750)
        self.assertEqual(self.limit(), 750)
        self.assertEqual(self.last_audit("user.discount_limit")["after"], {"max_discount_bp": 750})
        self.act("discount", max_discount_bp=None)
        self.assertIsNone(self.limit())
        self.assertEqual(self.last_audit("user.discount_limit")["before"], {"max_discount_bp": 750})

    def test_bad_discount_limits_are_refused_and_change_nothing(self):
        self.act("discount", max_discount_bp=500)
        for bad in ("abc", -1, 10001, [5], 12.5, True, "7.5"):           # never truncated to a whole number
            with self.subTest(bad=bad):
                self.act("discount", 422, max_discount_bp=bad)
                self.assertEqual(self.limit(), 500)

    def test_the_owners_limit_cannot_be_changed(self):
        self.act("discount", 403, uid=self.ctx.user_id, max_discount_bp=100)

    def test_only_the_users_manager_changes_limits(self):
        r = self.a.post(f"/api/users/{self.uid}/discount", json={"max_discount_bp": 10000}, headers=H)
        self.assertEqual(r.status_code, 403)
        self.assertIsNone(self.limit())

    # ------------------------------------------------------------------ switching a person off
    def test_switching_off_ends_the_session_and_blocks_password_and_pin_sign_in(self):
        self.assertEqual(self.a.get("/api/me").status_code, 200)
        self.act("active", active=False)
        self.assertEqual(self.a.get("/api/me").status_code, 401)                     # the open session ends
        r = TestClient(self.app).post("/api/auth/login", json={"username": "asst", "password": PW}, headers=H)
        self.assertEqual(r.status_code, 403, r.text)                                  # no active pharmacy for him
        self.assertEqual(TestClient(self.app).post("/api/auth/pin", json={"user_id": self.uid, "pin": "8642"},
                                                   headers=H).status_code, 401)
        quick = self.c.get("/api/auth/quick-users").json()["users"]
        self.assertNotIn(self.uid, [u["id"] for u in quick])
        self.assertEqual(self.last_audit("user.active")["after"], {"active": False})
        self.assertEqual(self.c.get("/api/me").status_code, 200)                     # the owner is still in
        self.act("active", active=True)
        self.http_login("asst", PW, client=TestClient(self.app))

    def test_the_owner_cannot_be_switched_off(self):
        self.act("active", 403, uid=self.ctx.user_id, active=False)
        self.assertEqual(self.c.get("/api/me").status_code, 200)

    # ------------------------------------------------------------------ password reset
    def test_reset_password_ends_sessions_and_clears_legacy_failure_state(self):
        with self.db.transaction():
            self.db.execute("UPDATE users SET failed_logins=3, locked_until=? WHERE id=?",
                            ("2099-01-01T00:00:00.000000Z", self.uid))
        self.act("password", password=NEW)
        self.assertEqual(self.a.get("/api/me").status_code, 401)                     # his open session ends
        state = self.db.one("SELECT failed_logins, locked_until FROM users WHERE id=?", (self.uid,))
        self.assertEqual((state["failed_logins"], state["locked_until"]), (0, None))
        fresh = TestClient(self.app)
        self.assertEqual(fresh.post("/api/auth/login", json={"username": "asst", "password": PW},
                                    headers=H).status_code, 401)
        self.http_login("asst", NEW, client=fresh)
        a = self.last_audit("user.password_reset")
        self.assertEqual(a["by"], self.ctx.user_id)
        self.assertNotIn(NEW, json.dumps(a))                                           # never the password itself

    def test_a_weak_password_is_refused(self):
        self.act("password", 422, password="123")
        self.http_login("asst", PW, client=TestClient(self.app))                       # the old one still works

    def test_an_unknown_action_or_user_is_refused(self):
        self.act("explode", 422)
        self.act("discount", 404, uid="missing", max_discount_bp=100)
