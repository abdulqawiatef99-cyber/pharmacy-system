"""Customer collection retries must commit ledger, cash, audit and key together."""
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from app.core import accounts
from app.api.app import create_app
from app.db import Database
from app.core import audit, people
from app.sales.service import Sales
from app.sales.cash import CashDrawer
from starlette.testclient import TestClient
from tests.r3_debt import owe
from tests.test_api import APICase, H


class TestCustomerPaymentIdempotency(APICase):
    def setUp(self):
        super().setUp()
        CashDrawer(self.db).open(self.ctx, 10000)
        self.customer = Sales(self.db).create_customer(self.ctx, name='Synthetic customer')
        owe(self.db, self.ctx, self.customer, 5000)      # a collection above the balance is refused
        self.http_login()
        self.headers = {**H, 'Idempotency-Key': 'collection-once'}
        self.url = f'/api/customers/{self.customer}/payments'

    def post(self, client=None, **payload):
        return (client or self.c).post(self.url, headers=self.headers,
                                      json=payload or {'amount': 1000, 'method': 'cash'})

    def count(self):
        return self.db.one("SELECT count(*) FROM customer_ledger WHERE kind='payment'")[0]

    def test_failed_cache_write_rolls_back_collection_cash_and_audit(self):
        execute = self.db.execute
        def fail(sql, params=()):
            if 'INSERT' in sql and 'INTO idempotency_keys' in sql:
                raise OSError('Synthetic key persistence failure')
            return execute(sql, params)
        with patch.object(self.db, 'execute', side_effect=fail):
            self.assertEqual(self.post().status_code, 500)
        self.assertEqual(self.count(), 0)
        self.assertEqual(CashDrawer(self.db).summary(self.ctx)['expected_cash'], 10000)
        self.assertEqual(self.db.one("SELECT count(*) FROM audit_log WHERE action='customer.payment'")[0], 0)
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.count(), 1)
        self.assertEqual(audit.verify_chain(self.db), [])

    def test_replay_and_changed_payload(self):
        first, second = self.post(), self.post()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.content, second.content)
        self.assertEqual(second.headers.get('Idempotent-Replay'), 'true')
        self.assertEqual(self.post(amount=2000).json()['error']['code'], 'idempotency_mismatch')
        self.assertEqual(self.count(), 1)

    def test_revoked_permission_cannot_replay_customer_balance(self):
        uid = people.add_user(self.db, self.ctx, username='collector', full_name='Synthetic collector',
                              password='Collector-Pass-2026', role_code='manager')
        other = TestClient(self.app)
        self.addCleanup(other.close)
        self.http_login('collector', 'Collector-Pass-2026', client=other)
        self.assertEqual(self.post(other).status_code, 200)
        people.set_exception(self.db, self.ctx, uid, 'customers.manage', 'deny')
        self.assertEqual(self.post(other).status_code, 403)
        self.assertEqual(self.count(), 1)

    def test_card_collection_replays_without_cash_movement(self):
        first = self.post(amount=1200, method='card')
        second = self.post(amount=1200, method='card')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.content, second.content)
        self.assertEqual(self.count(), 1)
        self.assertEqual(CashDrawer(self.db).summary(self.ctx)['expected_cash'], 10000)

    def test_concurrent_connections_collect_once(self):
        token = accounts.login(self.db, 'owner', 'Owner-Pass-2026')['token']
        independent = Database(self.db.path)
        self.addCleanup(independent.close)
        apps = [self.app, create_app(independent, self.s)]
        barrier = Barrier(2)
        def send(index):
            with TestClient(apps[index]) as client:
                barrier.wait(timeout=10)
                return client.post(self.url, headers={**self.headers, 'Authorization': 'Bearer '+token},
                                   json={'amount': 1000, 'method': 'cash'})
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(send, range(2)))
        self.assertEqual([r.status_code for r in responses], [200, 200])
        self.assertEqual(sum(r.headers.get('Idempotent-Replay') == 'true' for r in responses), 1)
        self.assertEqual(self.count(), 1)
        self.assertEqual(CashDrawer(self.db).summary(self.ctx)['expected_cash'], 11000)
        self.assertEqual(audit.verify_chain(self.db), [])

    def test_response_failure_rolls_back_collection(self):
        original = Sales.customer_payment
        def fail_after_payment(*args, **kwargs):
            original(*args, **kwargs)
            return object()  # response cannot be serialized
        with patch.object(Sales, 'customer_payment', side_effect=fail_after_payment, autospec=True):
            self.assertEqual(self.post().status_code, 500)
        self.assertEqual(self.count(), 0)
        self.assertEqual(CashDrawer(self.db).summary(self.ctx)['expected_cash'], 10000)
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.count(), 1)

    def test_invalid_request_does_not_consume_key(self):
        self.assertGreaterEqual(self.post(amount=-1).status_code, 400)
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.db.one('SELECT count(*) FROM idempotency_keys')[0], 0)
        self.assertEqual(self.post().status_code, 200)

    def test_same_key_cannot_collect_for_another_customer(self):
        self.assertEqual(self.post().status_code, 200)
        other = Sales(self.db).create_customer(self.ctx, name='Other synthetic customer')
        response = self.c.post(f'/api/customers/{other}/payments', headers=self.headers,
                               json={'amount': 1000, 'method': 'cash'})
        self.assertEqual(response.json()['error']['code'], 'idempotency_mismatch')
        self.assertEqual(self.count(), 1)
