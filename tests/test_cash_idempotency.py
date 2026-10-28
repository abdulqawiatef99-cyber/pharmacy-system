"""Cash writes and their replay record must commit atomically. Synthetic data only."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from starlette.testclient import TestClient

from app.core import accounts, audit, people
from app.sales.cash import CashDrawer
from tests.test_api import APICase, H


class TestCashIdempotency(APICase):
    def setUp(self):
        super().setUp()
        CashDrawer(self.db).open(self.ctx, 10000)
        self.http_login()
        self.headers = {**H, "Idempotency-Key": "cash-once"}
        self.payload = {"amount": 1000, "kind": "payout", "note": "Synthetic payout"}

    def post(self, **kwargs):
        return self.c.post("/api/cash/movements", headers=self.headers,
                           json=kwargs or self.payload)

    def assert_posted_once(self):
        self.assertEqual(CashDrawer(self.db).summary(self.ctx)["expected_cash"], 9000)
        self.assertEqual(self.db.one("SELECT count(*) FROM cash_movements")[0], 1)
        self.assertEqual(self.db.one("SELECT count(*) FROM audit_log WHERE action='cash.payout'")[0], 1)
        self.assertEqual(audit.verify_chain(self.db), [])

    def test_cache_write_failure_rolls_back_cash_and_audit_then_retry_posts_once(self):
        original = self.db.execute

        def fail(sql, params=()):
            if "INSERT" in sql and "INTO idempotency_keys" in sql:
                raise OSError("Synthetic persistence failure")
            return original(sql, params)

        with patch.object(self.db, "execute", side_effect=fail):
            self.assertEqual(self.post().status_code, 500)
        self.assertEqual(CashDrawer(self.db).summary(self.ctx)["expected_cash"], 10000)
        self.assertEqual(self.db.one("SELECT count(*) FROM cash_movements")[0], 0)
        self.assertEqual(self.db.one("SELECT count(*) FROM audit_log WHERE action='cash.payout'")[0], 0)
        self.assertEqual(self.db.one("SELECT count(*) FROM idempotency_keys")[0], 0)
        self.assertEqual(self.post().status_code, 200)
        self.assert_posted_once()

    def test_response_generation_failure_rolls_back_cash(self):
        with patch.object(CashDrawer, "summary", side_effect=RuntimeError("Synthetic response failure")):
            self.assertEqual(self.post().status_code, 500)
        self.assertEqual(CashDrawer(self.db).summary(self.ctx)["expected_cash"], 10000)
        self.assertEqual(self.db.one("SELECT count(*) FROM cash_movements")[0], 0)
        self.assertEqual(self.post().status_code, 200)
        self.assert_posted_once()

    def test_replay_keeps_original_response_and_one_movement(self):
        first, second = self.post(), self.post()
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(first.content, second.content)
        self.assertEqual(second.headers.get("Idempotent-Replay"), "true")
        self.assert_posted_once()

    def test_changed_request_with_same_key_is_rejected(self):
        self.assertEqual(self.post().status_code, 200)
        changed = self.post(amount=2000, kind="payout", note="Synthetic payout")
        self.assertEqual(changed.json()["error"]["code"], "idempotency_mismatch")
        self.assertGreaterEqual(changed.status_code, 400)
        self.assert_posted_once()

    def test_revoked_cash_permission_cannot_replay_cached_response(self):
        uid = people.add_user(self.db, self.ctx, username="cash-manager", full_name="Synthetic manager",
                              password="Manager-Pass-2026", role_code="manager")
        other = TestClient(self.app)
        self.addCleanup(other.close)
        self.http_login("cash-manager", "Manager-Pass-2026", client=other)
        first = other.post("/api/cash/movements", headers=self.headers, json=self.payload)
        self.assertEqual(first.status_code, 200, first.text)
        people.set_exception(self.db, self.ctx, uid, "cash.manage", "deny")
        repeated = other.post("/api/cash/movements", headers=self.headers, json=self.payload)
        self.assertEqual(repeated.status_code, 403, repeated.text)
        self.assert_posted_once()

    def test_concurrent_identical_requests_post_once(self):
        token = accounts.login(self.db, "owner", "Owner-Pass-2026")["token"]
        barrier = Barrier(2)

        def send(_):
            client = TestClient(self.app)
            try:
                barrier.wait(timeout=10)
                return client.post("/api/cash/movements",
                                   headers={**self.headers, "Authorization": "Bearer " + token},
                                   json=self.payload)
            finally:
                client.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(send, range(2)))
        self.assertEqual([r.status_code for r in responses], [200, 200])
        self.assertEqual(sum(r.headers.get("Idempotent-Replay") == "true" for r in responses), 1)
        self.assert_posted_once()

    def test_key_namespace_is_shared_with_other_post_routes(self):
        r = self.c.post("/api/suppliers", headers=self.headers, json={"name": "Synthetic supplier"})
        self.assertEqual(r.status_code, 201)
        response = self.post()
        self.assertEqual(response.json()["error"]["code"], "idempotency_mismatch")
        self.assertEqual(self.db.one("SELECT count(*) FROM cash_movements")[0], 0)

    def test_no_key_still_allows_two_distinct_manual_operations(self):
        for _ in range(2):
            response = self.c.post("/api/cash/movements", headers=H, json=self.payload)
            self.assertEqual(response.status_code, 200)
        self.assertEqual(CashDrawer(self.db).summary(self.ctx)["expected_cash"], 8000)
        self.assertEqual(self.db.one("SELECT count(*) FROM cash_movements")[0], 2)
