"""#337 review P2 (a fresh approval after a corrected request) and #324 S1 (a recalled lot received again
after its alert was handled), both decided before launch. Synthetic data only; each test failed on 4fc279f."""
from datetime import date, timedelta
from decimal import Decimal

from starlette.testclient import TestClient

from app.catalog import service as cat
from app.core import approvals, people
from app.db import new_id
from app.inventory import adjust, service as inv
from app.recalls.service import Recalls
from app.sales.cash import CashDrawer
from app.sales.service import Sales
from tests.helpers import DBTestCase
from tests.test_api import APICase, H
from tests.test_review_324_money import PW, _sell

T = date.today()


def _receive(db, ctx, item, qty, lot):
    with db.transaction():
        b = inv.get_or_create_batch(db, ctx, item, lot, (T + timedelta(days=400)).isoformat())
        inv.post_movement(db, ctx, item_id=item, qty_delta=qty, batch_id=b, movement_type="purchase",
                          unit_cost=Decimal("15"))
    return b


class TestApprovalAfterACorrectedRequest(APICase):
    """The browser sends its still-valid token as `previous` when it asks for the PIN again; the new
    approval used to keep the binding to the rejected request, so the corrected one was refused."""

    def setUp(self):
        super().setUp()
        approvals.clear()
        people.set_pin(self.db, self.ctx, self.ctx.user_id, "9753")
        cid = new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO categories (id, org_id, name, no_return, mark, sort) VALUES (?,?,?,?,?,?)",
                            (cid, self.ctx.org_id, "مستحضرات", 1, None, 1))
        self.item = cat.create_item(self.db, self.ctx, {"name_en": "Cream", "pack_size": 1, "public_price": 1000})
        with self.db.transaction():
            self.db.execute("UPDATE items SET category_id=? WHERE id=?", (cid, self.item))
        _receive(self.db, self.ctx, self.item, 10, "C1")
        CashDrawer(self.db).open(self.ctx, 100000)
        people.add_user(self.db, self.ctx, username="asst", full_name="asst", password=PW, role_code="assistant")
        self.sale = _sell(Sales(self.db), self.login("asst", PW), self.item, 3)
        self.a = TestClient(self.app)
        self.http_login("asst", PW, client=self.a)
        self.path = f"/api/sales/{self.sale['id']}/return"
        self.line = self.sale["lines"][0]["id"]

    def approve(self, previous=None):
        r = self.a.post("/api/approvals/check", headers={**H, "X-Approver": self.ctx.user_id, "X-Approver-Pin": "9753"},
                        json={"permission": "sales.return_restricted", "method": "POST", "path": self.path,
                              "previous": previous})
        return r.json()["token"]

    def ret(self, token, qty):
        return self.a.post(self.path, json={"lines": {self.line: qty}, "refund_method": "cash", "reason": "مرتجع"},
                           headers={**H, "X-Approval": token})

    def test_a_fresh_pin_approval_works_for_the_corrected_request(self):
        first = self.approve()
        bad = self.ret(first, 9)                                     # more than was sold: refused
        self.assertIn(bad.status_code, (409, 422), bad.text)
        fresh = self.approve(previous=first)                         # the PIN is typed again
        ok = self.ret(fresh, 1)
        self.assertEqual(ok.status_code, 200, ok.text)

    def test_the_fresh_approval_is_still_used_once(self):
        fresh = self.approve(previous=self.approve())
        self.assertEqual(self.ret(fresh, 1).status_code, 200)
        self.assertEqual(self.ret(fresh, 1).status_code, 403)


class TestS1RecalledLotReceivedAgain(DBTestCase):
    def setUp(self):
        super().setUp()
        self.item = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg", "pack_size": 1, "public_price": 1000})
        self.batch = _receive(self.db, self.ctx, self.item, 5, "AB01")
        self.r = Recalls(self.db)
        self.r.add_notice(self.ctx, title="سحب", items=[{"product_name": "Panadol 500mg", "batch_numbers": ["AB01"]}])
        self.m = self.r.alerts(self.ctx)[0]["id"]
        self.r.decide(self.ctx, self.m, "confirmed")
        adjust.adjust(self.db, self.ctx, self.item, set_to=0, batch_id=self.batch, reason="damaged", note="سحب")
        self.r.decide(self.ctx, self.m, "handled")

    def sellable(self):
        return Sales(self.db)._held(self.ctx, self.ctx.branch_id, self.item)[0]

    def test_units_of_the_lot_received_after_handling_are_held(self):
        _receive(self.db, self.ctx, self.item, 2, "AB01")            # any way in, even without matching
        self.assertEqual(self.sellable(), 0)

    def test_matching_reopens_the_alert_as_confirmed_with_the_new_stock(self):
        _receive(self.db, self.ctx, self.item, 2, "AB01")
        self.r.match_new_batches(self.ctx)
        m = self.db.one("SELECT status, qty_in_stock FROM recall_matches WHERE id=?", (self.m,))
        self.assertEqual((m["status"], m["qty_in_stock"]), ("confirmed", 2))
        self.assertEqual(self.sellable(), 0)

    def test_another_lot_stays_sellable(self):
        _receive(self.db, self.ctx, self.item, 4, "ZZ99")
        self.r.match_new_batches(self.ctx)
        self.assertEqual(self.sellable(), 4)
        self.assertEqual(self.db.one("SELECT status FROM recall_matches WHERE id=?", (self.m,))["status"], "handled")
