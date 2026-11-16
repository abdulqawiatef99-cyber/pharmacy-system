"""Customer collection: how it was paid is kept, and no more than the customer owes is collected.
Synthetic data only."""
import json
import uuid

from app.core.errors import ValidationFailed
from app.sales.cash import CashDrawer
from app.sales.service import Sales
from tests.r3_debt import owe
from tests.test_api import APICase, H


class TestCustomerCollection(APICase):
    def setUp(self):
        super().setUp()
        self.s = Sales(self.db)
        CashDrawer(self.db).open(self.ctx, 0)
        self.cid = self.s.create_customer(self.ctx, name="عميل تجربة", credit_limit=100000)
        owe(self.db, self.ctx, self.cid, 30000)
        self.http_login()

    def pay(self, **body):
        return self.c.post(f"/api/customers/{self.cid}/payments", headers={**H, "Idempotency-Key": uuid.uuid4().hex},
                           json=body)

    def payment_rows(self):
        return [dict(r) for r in self.db.all("SELECT amount, note FROM customer_ledger WHERE org_id=? AND customer_id=?"
                                             " AND kind='payment' ORDER BY created_at", (self.ctx.org_id, self.cid))]

    def test_a_card_collection_keeps_its_method(self):
        r = self.pay(amount=10000, method="card")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["balance"], 20000)
        self.assertEqual(self.payment_rows(), [{"amount": -10000, "note": "بطاقة"}])
        a = self.db.one("SELECT after_json FROM audit_log WHERE org_id=? AND action='customer.payment'", (self.ctx.org_id,))
        self.assertEqual(json.loads(a["after_json"]), {"amount": 10000, "method": "card"})
        self.assertEqual(self.db.one("SELECT count(*) FROM cash_movements")[0], 0)      # no cash entered the drawer

    def test_a_cash_collection_keeps_its_method_and_the_note(self):
        self.assertEqual(self.s.customer_payment(self.ctx, self.cid, 5000, "دفعة أولى"), 25000)
        self.assertEqual(self.payment_rows(), [{"amount": -5000, "note": "نقدًا — دفعة أولى"}])
        a = self.db.one("SELECT after_json FROM audit_log WHERE org_id=? AND action='customer.payment'", (self.ctx.org_id,))
        self.assertEqual(json.loads(a["after_json"])["method"], "cash")
        self.assertEqual(CashDrawer(self.db).summary(self.ctx)["expected_cash"], 5000)

    def test_more_than_the_balance_is_refused(self):
        r = self.pay(amount=30001, method="cash")
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(r.json()["error"]["code"], "over_collection")
        self.assertEqual(r.json()["error"]["details"], {"balance": 30000, "amount": 30001})
        self.assertEqual(self.payment_rows(), [])
        self.assertEqual(CashDrawer(self.db).summary(self.ctx)["expected_cash"], 0)
        self.assertEqual(self.pay(amount=30000, method="cash").json()["balance"], 0)   # the whole debt is fine
        again = self.pay(amount=100, method="card")                                    # nothing left to collect
        self.assertEqual(again.json()["error"]["code"], "over_collection")

    def test_a_customer_who_owes_nothing_cannot_be_collected_from(self):
        other = self.s.create_customer(self.ctx, name="عميل آخر")
        with self.assertRaises(ValidationFailed) as e:
            self.s.customer_payment(self.ctx, other, 100)
        self.assertEqual(e.exception.code, "over_collection")
