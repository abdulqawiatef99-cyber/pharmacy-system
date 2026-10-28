"""Synthetic purchase-to-shift acceptance through HTTP, including a DB reopen.

Complements test_day_consistency (service-level reports) and
test_cashier_acceptance (cashier rules); no real supplier file or hardware.
"""
from datetime import date, timedelta

from starlette.testclient import TestClient
from app.api.app import create_app
from app.core import audit
from app.db import Database
from app.inventory import service as inventory
from tests.fixtures.make_invoices import excel_invoice
from tests.test_api import APICase, H


class TestOperatingCycleAPI(APICase):
    def request(self, method, url, expected=200, **kwargs):
        response = self.c.request(method, url, headers=H, **kwargs)
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def stock(self):
        return self.request('GET', f'/api/items/{self.pan}')['stock']['total_qty']

    def sale(self, units, *, customer=None, discount=0, cash=0, credit=0):
        draft = self.request('POST', '/api/sales', expected=201,
                             json={'customer_id': customer})
        sid = draft['id']
        draft = self.request('POST', f'/api/sales/{sid}/lines',
                             json={'item_id': self.pan, 'units': units, 'discount_bp': discount})
        findings = self.request('POST', f'/api/sales/{sid}/check', json={})['findings']
        return self.request('POST', f'/api/sales/{sid}/complete',
                            json={'paid_cash': cash, 'on_credit': credit, 'version': draft['version'],
                                  'safety_decisions': {str(i): 'ok' for i in range(len(findings))},
                                  'request_id': 'synthetic-payment-'+sid})

    def test_purchase_bonus_cash_discount_return_credit_collection_close_and_reopen(self):
        self.http_login()
        expiry = (date.today() + timedelta(days=700)).strftime('%m/%Y')
        data = excel_invoice([('PANADOL 500 MG 24 TAB', 10, 2, 40, 20, 32, 320,
                               'SYNTHETIC-BATCH', expiry, None)])
        draft = self.request('POST', '/api/purchases/import', expected=201,
                             files={'file': ('synthetic.xlsx', data, 'application/octet-stream')})
        self.assertEqual(self.stock(), 0)  # draft never changes stock or supplier debt
        self.assertEqual(self.db.one('SELECT count(*) FROM supplier_ledger')[0], 0)
        self.assertTrue(draft['can_approve'], draft['issues'])
        invoice_id = draft['invoice']['id']
        self.request('POST', f'/api/purchases/{invoice_id}/approve',
                     json={'version': draft['invoice']['version']})
        self.assertEqual(self.stock(), 24)  # 10 paid + 2 bonus packs, 2 units each
        self.assertEqual(self.db.one("SELECT sum(amount) FROM supplier_ledger WHERE kind='purchase'")[0], 32000)
        self.request('POST', '/api/cash/open', expected=201, json={'opening_cash': 10000})
        cash_sale = self.sale(3, discount=1000, cash=5400)
        self.assertEqual((cash_sale['subtotal'], cash_sale['discount_total'], cash_sale['total']),
                         (6000, 600, 5400))
        self.request('POST', f"/api/sales/{cash_sale['id']}/return",
                     json={'lines': {cash_sale['lines'][0]['id']: 1}, 'refund_method': 'cash',
                           'reason': 'Synthetic partial return'})
        customer = self.request('POST', '/api/customers', expected=201,
                                json={'name': 'Synthetic credit customer', 'credit_limit': 10000})['id']
        self.sale(2, customer=customer, credit=4000)
        payment = self.request('POST', f'/api/customers/{customer}/payments',
                               json={'amount': 1500, 'method': 'cash'})
        self.assertEqual(payment['balance'], 2500)
        self.assertEqual(self.stock(), 20)
        before = self.request('GET', '/api/cash/current')
        self.assertEqual(before['expected_cash'], 15100)  # float + 5400 - 1800 + 1500
        closed = self.request('POST', '/api/cash/close', json={'counted_cash': 15100})
        self.assertEqual((closed['status'], closed['difference']), ('closed', 0))
        self.assertEqual(inventory.verify_balances(self.db, self.ctx.org_id), [])
        self.assertEqual(audit.verify_chain(self.db), [])
        day = date.today().isoformat()
        report = self.request('GET', f'/api/reports/sales?from={day}&to={day}')['totals']
        self.assertEqual((report['n'], report['total'], report['returns'], report['discount']),
                         (2, 9400, 1800, 600))
        self.assertEqual(self.db.one('SELECT sum(qty_delta) FROM stock_movements WHERE item_id=?',
                                     (self.pan,))[0], 20)
        self.assertEqual(self.db.one('SELECT sum(amount) FROM customer_ledger WHERE customer_id=?',
                                     (customer,))[0], 2500)
        # Close every original connection, then verify persisted state through a fresh app.
        self.c.close()
        path = self.db.path
        self.db.close()
        self.db = Database(path)
        self.c = TestClient(create_app(self.db, self.s))
        self.addCleanup(self.c.close)
        self.http_login()
        self.assertEqual(self.stock(), 20)
        self.assertEqual(self.request('GET', f'/api/reports/sales?from={day}&to={day}')['totals'], report)
        persisted = self.db.one('SELECT expected_cash,counted_cash,difference,status FROM cash_shifts WHERE id=?',
                                (closed['id'],))
        self.assertEqual(tuple(persisted), (15100, 15100, 0, 'closed'))
        self.assertEqual(self.db.one('PRAGMA integrity_check')[0], 'ok')
        self.assertEqual(audit.verify_chain(self.db), [])
