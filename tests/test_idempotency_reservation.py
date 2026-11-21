"""Generic Idempotency-Key path: a retried money POST must never post twice. Synthetic data only.

The browser retries a POST with the SAME key after a network drop (app.js `api`). The retry can
arrive (a) after the first request committed but its replay record could not be stored, or
(b) while the first request is still running. Both must end with exactly one effect.
"""
from unittest.mock import patch

from app.catalog import service as cat
from tests.test_api import APICase, H


class TestIdempotencyReservation(APICase):
    def setUp(self):
        super().setUp()
        self.sup = cat.create_supplier(self.db, self.ctx, name="مورد تجريبي")
        self.http_login()
        self.headers = {**H, "Idempotency-Key": "pay-once"}
        self.url = f"/api/suppliers/{self.sup}/pay"
        self.payload = {"amount": 5000, "method": "cash", "note": "Synthetic payment"}

    def pay(self, **kwargs):
        return self.c.post(self.url, headers=self.headers, json=kwargs or self.payload)

    def payments(self):
        return self.db.one("SELECT count(*) FROM supplier_ledger WHERE kind='payment'")[0]

    def test_replay_record_failure_then_retry_does_not_pay_twice(self):
        original = self.db.execute

        def fail(sql, params=()):
            # the step that stores the finished response (after the payment committed)
            if "idempotency_keys" in sql and sql.lstrip().upper().startswith(("UPDATE", "INSERT OR IGNORE")):
                raise OSError("Synthetic persistence failure")
            return original(sql, params)

        with patch.object(self.db, "execute", side_effect=fail):
            self.assertEqual(self.pay().status_code, 500)
        self.assertEqual(self.payments(), 1)              # the payment itself did commit
        retry = self.pay()
        self.assertEqual(retry.status_code, 409, retry.text)
        self.assertEqual(retry.json()["error"]["code"], "idempotency_unconfirmed")
        self.assertEqual(self.payments(), 1)

    def reserve(self, key, path, body_hash="x"):
        """A first attempt that is still running (or died without confirming) holds its key."""
        self.db.conn.execute("BEGIN")
        self.db.execute("INSERT INTO idempotency_keys (org_id, user_id, key, method, path, request_hash,"
                        " status, response) VALUES (?,?,?,?,?,?,0,'')",
                        (self.ctx.org_id, self.ctx.user_id, key, "POST", path, body_hash))
        self.db.conn.execute("COMMIT")

    def test_key_is_reserved_before_the_payment_runs(self):
        from app.procurement import suppliers as sup
        original, seen = sup.pay, []

        def spy(*a, **kw):
            seen.append(self.db.one("SELECT status FROM idempotency_keys WHERE key='pay-once'"))
            return original(*a, **kw)

        with patch.object(sup, "pay", side_effect=spy):
            first = self.pay()
        self.assertEqual(first.status_code, 200, first.text)
        self.assertIsNotNone(seen[0], "no reservation while the action ran: a parallel retry would pay again")
        self.assertEqual(seen[0]["status"], 0)
        replay = self.pay()                                 # once finished, the result replays
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.headers.get("Idempotent-Replay"), "true")
        self.assertEqual(replay.content, first.content)
        self.assertEqual(self.payments(), 1)

    def test_rejected_request_frees_its_key(self):
        bad = self.pay(amount=0, method="cash")
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(self.db.one("SELECT count(*) FROM idempotency_keys")[0], 0)
        self.assertEqual(self.pay(amount=0, method="cash").status_code, 422)   # same answer, not "in progress"
        self.assertEqual(self.payments(), 0)

    def test_unexpected_crash_after_commit_is_never_rerun_with_the_same_key(self):
        from app.procurement import suppliers as sup
        original = sup.pay

        def commit_then_crash(*a, **kw):
            original(*a, **kw)
            raise RuntimeError("Synthetic crash after the payment committed")

        with patch.object(sup, "pay", side_effect=commit_then_crash):
            self.assertEqual(self.pay().status_code, 500)
        self.assertEqual(self.payments(), 1)
        retry = self.pay()
        self.assertEqual(retry.status_code, 409, retry.text)
        self.assertEqual(self.payments(), 1)
        fresh = self.c.post(self.url, headers={**H, "Idempotency-Key": "new-click"}, json=self.payload)
        self.assertEqual(fresh.status_code, 200)          # a deliberate new attempt still works

    def test_normal_replay_and_changed_body(self):
        first, second = self.pay(), self.pay()
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(second.headers.get("Idempotent-Replay"), "true")
        changed = self.pay(amount=7000, method="cash")
        self.assertEqual(changed.json()["error"]["code"], "idempotency_mismatch")
        self.assertEqual(self.payments(), 1)

    def test_retry_while_first_attempt_is_pending_does_not_pay(self):
        import hashlib, json
        raw = json.dumps(self.payload).encode()
        self.reserve("pay-once", self.url, hashlib.sha256(b"POST" + self.url.encode() + raw).hexdigest())
        r = self.c.post(self.url, headers={**self.headers, "Content-Type": "application/json"}, content=raw)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["error"]["code"], "idempotency_unconfirmed")
        self.assertEqual(self.payments(), 0)

    def test_other_money_routes_share_the_protection(self):
        self.reserve("exp-1", "/api/expenses")
        r = self.c.post("/api/expenses", headers={**H, "Idempotency-Key": "exp-1"},
                        json={"category_id": "x", "amount": 100, "paid_from": "outside"})
        self.assertGreaterEqual(r.status_code, 400)
        self.assertIn(r.json()["error"]["code"], ("idempotency_mismatch", "idempotency_unconfirmed"))
        self.assertEqual(self.db.one("SELECT count(*) FROM expenses")[0], 0)
