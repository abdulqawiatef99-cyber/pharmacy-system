import argparse
from pathlib import Path
from unittest import mock

import manage
from app.core import people
from app.core.errors import Conflict
from app.finance import money
from app.sales.cash import CashDrawer
from tests.helpers import DBTestCase
from tests.test_api import APICase, H, approval_headers


class TestCashPayoutPolicy(DBTestCase):
    def test_manual_cash_payout_checks_current_drawer_funds(self):
        drawer = CashDrawer(self.db)
        shift = drawer.open(self.ctx, 100)
        drawer.movement(self.ctx, 100, "payout", "Synthetic settlement")
        with self.assertRaises(Conflict) as refused:
            drawer.movement(self.ctx, 1, "payout", "Synthetic settlement")
        self.assertEqual(refused.exception.code, "drawer_negative")
        self.assertEqual(drawer.summary(self.ctx, shift)["expected_cash"], 0)
        self.assertEqual(self.db.one("SELECT count(*) n FROM audit_log WHERE org_id=? AND action='cash.payout'",
                                     (self.ctx.org_id,))["n"], 1)


    def test_physical_treasury_cannot_be_overdrawn(self):
        treasury = money.treasury_id(self.db, self.ctx)
        with self.db.transaction():
            self.db.execute("UPDATE money_accounts SET allow_negative=1 WHERE org_id=? AND id=?",
                            (self.ctx.org_id, treasury))
        with self.assertRaises(Conflict) as refused:
            money.owner_movement(self.db, self.ctx, treasury, 1, "owner_withdrawal", "Synthetic withdrawal")
        self.assertEqual(refused.exception.code, "account_negative")
        self.assertEqual(money.balance(self.db, self.ctx.org_id, treasury), 0)


class TestAuthenticationPolicy(APICase):
    def test_password_admission_keeps_a_correct_login_available_on_the_same_peer(self):
        for _ in range(11):
            response = self.c.post("/api/auth/login", json={"username": "owner", "password": "incorrect"},
                                   headers=H)
            self.assertEqual(response.json()["error"]["code"], "invalid_credentials")
        self.http_login()
        alerts = self.db.all("SELECT 1 FROM notifications WHERE org_id=? AND kind='failed_sign_in'",
                             (self.ctx.org_id,))
        self.assertEqual(len(alerts), 1)

    def test_pin_sign_in_applies_browser_origin_admission(self):
        user = people.add_user(self.db, self.ctx, username="staff-fixture", full_name="Staff fixture",
                               password="Fixture-Pass-2026", role_code="assistant", pin="5791")
        payload = {"user_id": user, "pin": "5791"}
        rejected = self.c.post("/api/auth/pin", json=payload, headers={**H, "Origin": "http://elsewhere.test"})
        self.assertEqual(rejected.json()["error"]["code"], "csrf")
        self.assertNotIn("set-cookie", rejected.headers)
        allowed = self.c.post("/api/auth/pin", json=payload, headers={**H, "Origin": "http://testserver"})
        self.assertEqual(allowed.status_code, 200, allowed.text)

    def test_update_api_refuses_retired_unsigned_action(self):
        self.http_login()
        with mock.patch("subprocess.run") as launch:
            response = self.c.post("/api/system/update", json={"action": "classic"}, headers=H)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "signed_update_required")
        launch.assert_not_called()

    def test_update_cli_requires_signed_bundle_before_reading_an_offline_zip(self):
        args = argparse.Namespace(candidate_check=False, self_check=False, prepare=False, apply_prepared=None,
                                  select_prepared=False, rollback=False, from_zip="fixture.zip", check=False)
        with mock.patch.object(Path, "read_bytes") as read:
            with self.assertRaisesRegex(SystemExit, "signed release bundle"):
                manage.cmd_update(args, self.s)
        read.assert_not_called()

    def test_write_approval_requires_intent_at_issuance(self):
        people.set_pin(self.db, self.ctx, self.ctx.user_id, "5791")
        people.add_user(self.db, self.ctx, username="staff-fixture", full_name="Staff fixture",
                        password="Fixture-Pass-2026", role_code="assistant")
        self.http_login("staff-fixture", "Fixture-Pass-2026")
        response = self.c.post("/api/approvals/check", headers={**H, "X-Approver": self.ctx.user_id,
                               "X-Approver-Pin": "5791"}, json={"permission": "items.write",
                               "path": "/api/items", "method": "POST"})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("token", response.json())


    def test_approved_response_replays_only_to_a_currently_authorized_request(self):
        people.set_pin(self.db, self.ctx, self.ctx.user_id, "5791")
        people.add_user(self.db, self.ctx, username="staff-fixture", full_name="Staff fixture",
                        password="Fixture-Pass-2026", role_code="assistant")
        self.http_login("staff-fixture", "Fixture-Pass-2026")
        payload = {"item": {"name_en": "Synthetic Approval Item"}}
        path = "/api/items"
        key = {"Idempotency-Key": "catalog-fixture-operation"}
        approved = approval_headers(self.c, self.ctx.user_id, "5791", "items.write", path, "POST",
                                    payload=payload, key=key["Idempotency-Key"])
        created = self.c.post(path, json=payload, headers=approved)
        self.assertEqual(created.status_code, 201, created.text)
        refused = self.c.post(path, json=payload, headers={**H, **key})
        self.assertEqual(refused.json()["error"]["code"], "permissions_changed")
        replay = self.c.post(path, json=payload, headers=approved)
        self.assertEqual(replay.json(), created.json())
        self.assertEqual(replay.headers.get("Idempotent-Replay"), "true")
