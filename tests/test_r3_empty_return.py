"""A sale return with no lines is refused, not saved as a zero return. Synthetic data only."""
import uuid
from datetime import date, timedelta
from decimal import Decimal

from app.core.errors import ValidationFailed
from app.inventory import service as inv
from app.sales.cash import CashDrawer
from app.sales.service import Sales
from tests.test_api import APICase, H


class TestEmptyReturn(APICase):
    def setUp(self):
        super().setUp()
        self.s = Sales(self.db)
        CashDrawer(self.db).open(self.ctx, 0)
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.pan, "B1", (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=20, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("15"))
        self.sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, self.sid, self.pan, units=2)
        self.line = d["lines"][0]["id"]
        self.s.complete(self.ctx, self.sid, paid_cash=d["total"], safety_decisions={})
        self.http_login()

    def post(self, **body):
        return self.c.post(f"/api/sales/{self.sid}/return", headers={**H, "Idempotency-Key": uuid.uuid4().hex},
                           json={"refund_method": "cash", "reason": "لا شيء", **body})

    def returns(self):
        return self.db.one("SELECT count(*) FROM sale_returns WHERE org_id=?", (self.ctx.org_id,))[0]

    def test_no_lines_is_refused(self):
        for lines in ({}, None, [], [self.line], "x"):
            with self.subTest(lines=lines):
                r = self.post(lines=lines)
                self.assertEqual(r.status_code, 422, r.text)
                self.assertEqual(r.json()["error"]["code"], "empty_return")
        self.assertEqual(self.post().json()["error"]["code"], "empty_return")          # the field left out
        with self.assertRaises(ValidationFailed):
            self.s.return_items(self.ctx, self.sid, {}, refund_method="cash", reason="لا شيء")
        self.assertEqual(self.returns(), 0)
        self.assertEqual(self.db.one("SELECT count(*) FROM audit_log WHERE action LIKE 'sale.return%'")[0], 0)

    def test_a_real_return_still_works(self):
        r = self.post(lines={self.line: 1})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.returns(), 1)
