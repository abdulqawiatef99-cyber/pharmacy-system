"""An open invoice could be changed by an employee who may not even open it: add a line, change a quantity,
remove a line or put a customer on another seller's draft. The request was answered with a refusal (the
invoice is not his to see), but the change had already been saved. The refusal now comes before any write.
Synthetic data only."""
from datetime import date, timedelta
from decimal import Decimal

from app.catalog import service as cat
from app.core import people
from app.core.errors import Forbidden
from app.inventory import service as inv
from app.sales.service import Sales
from tests.helpers import DBTestCase

PW = "Staff-Pass-2026"


class DraftEditOwner(DBTestCase):
    def setUp(self):
        super().setUp()
        self.s = Sales(self.db)
        self.item = cat.create_item(self.db, self.ctx, {"name_en": "Probe 10 tab", "pack_size": 1, "public_price": 1000})
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.item, "B1", (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.item, qty_delta=20, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("5"))
        self.customer = self.s.create_customer(self.ctx, name="عميل وهمي", phone="01000000051", credit_limit=5000)
        people.add_user(self.db, self.ctx, username="asst", full_name="Assistant", password=PW, role_code="assistant")
        people.add_user(self.db, self.ctx, username="mgr", full_name="Manager", password=PW, role_code="manager")
        self.sid = self.s.create(self.ctx)                       # the owner's open invoice
        self.s.add_line(self.ctx, self.sid, self.item, units=1)
        self.line = self.db.one("SELECT id FROM sale_lines WHERE sale_id=?", (self.sid,))["id"]

    def state(self):
        s = self.db.one("SELECT customer_id, kind, version FROM sales WHERE id=?", (self.sid,))
        return dict(s), [tuple(r) for r in self.db.all("SELECT id, qty_base, discount_bp FROM sale_lines WHERE sale_id=?"
                                                       " ORDER BY line_no", (self.sid,))]

    def test_another_seller_cannot_change_an_open_invoice(self):
        other = self.login("asst", PW)
        before = self.state()
        for change in (lambda: self.s.set_details(other, self.sid, {"customer_id": self.customer}),
                       lambda: self.s.set_details(other, self.sid, {"kind": "delivery"}),
                       lambda: self.s.add_line(other, self.sid, self.item, units=3),
                       lambda: self.s.update_line(other, self.sid, self.line, qty_base=7),
                       lambda: self.s.remove_line(other, self.sid, self.line)):
            with self.assertRaises(Forbidden):
                change()
            self.assertEqual(self.state(), before)                 # was: refused, yet saved

    def test_its_seller_and_a_manager_still_can(self):
        self.s.update_line(self.ctx, self.sid, self.line, qty_base=2)
        mgr = self.login("mgr", PW)                                # sales.totals: may open every invoice
        self.s.set_details(mgr, self.sid, {"customer_id": self.customer})
        self.assertEqual(self.state()[0]["customer_id"], self.customer)
        self.assertEqual(self.state()[1][0][1], 2)
