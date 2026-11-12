"""#134 follow-up: the current password asked in an open session (own PIN, own password from the users
screen) cannot be guessed at full speed: 5 tries in 5 minutes per user, then 429. Synthetic data only."""
from app.core import people, security
from tests.helpers import OWNER_PW
from tests.test_api import APICase, H


class ReauthLimit(APICase):
    def setUp(self):
        super().setUp()
        self.http_login()

    def test_guessing_the_current_password_for_the_own_pin_is_slowed(self):
        codes = [self.c.post("/api/me/pin", json={"password": f"wrong-{i}", "pin": "4826"}, headers=H).status_code
                 for i in range(5)]
        self.assertEqual(codes, [422] * 5)
        r = self.c.post("/api/me/pin", json={"password": OWNER_PW, "pin": "4826"}, headers=H)
        self.assertEqual(r.status_code, 429, r.text)                    # even the right one waits now
        h = self.db.one("SELECT pin_hash FROM users WHERE id=?", (self.ctx.user_id,))["pin_hash"]
        self.assertFalse(h and security.verify_password("4826", h))

    def test_the_users_screen_shares_the_limit(self):
        uid = self.ctx.user_id
        for i in range(5):
            self.c.post(f"/api/users/{uid}/pin", json={"pin": "4826", "current_password": f"wrong-{i}"}, headers=H)
        r = self.c.post(f"/api/users/{uid}/pin", json={"pin": "4826", "current_password": OWNER_PW}, headers=H)
        self.assertEqual(r.status_code, 429, r.text)
        self.assertEqual(self.c.post("/api/me/pin", json={"password": OWNER_PW, "pin": "4826"},
                                     headers=H).status_code, 429)

    def test_a_normal_change_still_works_and_other_users_are_not_slowed(self):
        r = self.c.post("/api/me/pin", json={"password": OWNER_PW, "pin": "4826"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        for i in range(5):
            self.c.post("/api/me/pin", json={"password": f"wrong-{i}", "pin": "4826"}, headers=H)
        from starlette.testclient import TestClient
        PW = "Staff-Pass-2026"
        people.add_user(self.db, self.ctx, username="asst", full_name="مساعد", password=PW, role_code="assistant")
        a = TestClient(self.app)
        self.http_login("asst", PW, client=a)
        self.assertEqual(a.post("/api/me/pin", json={"password": PW, "pin": "5172"}, headers=H).status_code, 200)
