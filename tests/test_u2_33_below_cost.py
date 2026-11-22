"""U2-33: a discount never takes a line under its cost, and never past the person's own limit. Refused at the
moment it is typed (line percent, line amount, more units at a discount, invoice discount). Update 3 (owner:
«امنعه أساساً»): nobody gets past, the owner and his PIN included; down to cost exactly needs `sales.below_cost`
(owner / manager) and a written reason at payment. The cost is never shown to someone without costs.read."""
from app.core import accounts, people
from app.core.errors import Conflict, Forbidden, ValidationFailed
from tests.test_people import PW, People


class TestBelowCost(People):
    # the fixture item: cost 15/unit, price 20/unit -> 25% is the most a discount can be
    def setUp(self):
        super().setUp()
        people.set_role_discount(self.db, self.ctx, "manager", 5000)
        self.m = self.login("manager", PW)

    def refused(self, fn):
        with self.assertRaises(ValidationFailed) as e:
            fn()
        self.assertEqual(e.exception.code, "below_cost")
        self.assertNotIn("permission", e.exception.details)       # no PIN can pass it
        return e.exception

    def test_percent_on_new_line(self):
        sid = self.s.create(self.m)
        e = self.refused(lambda: self.s.add_line(self.m, sid, self.pan, units=2, discount_bp=3000))
        self.assertNotIn("max_discount", e.details)         # the manager does not see the cost
        self.assertNotIn("15", str(e))
        d = self.s.add_line(self.m, sid, self.pan, units=2, discount_bp=2500)   # at cost: allowed
        self.assertEqual(len(d["lines"]), 1)

    def test_percent_and_amount_on_existing_line(self):
        sid = self.s.create(self.m)
        lid = self.s.add_line(self.m, sid, self.pan, units=2)["lines"][0]["id"]
        self.refused(lambda: self.s.update_line(self.m, sid, lid, discount_bp=2600))
        self.refused(lambda: self.s.update_line(self.m, sid, lid, discount_amount=1100))   # 11 of 40 -> 29 < 30
        d = self.s.get(self.m, sid)
        self.assertEqual(d["lines"][0]["discount"], 0)            # rolled back

    def test_invoice_discount(self):
        sid = self.s.create(self.m)
        self.s.add_line(self.m, sid, self.pan, units=2)
        self.refused(lambda: self.s.set_invoice_discount(self.m, sid, bp=3000))
        self.assertFalse(self.s.get(self.m, sid)["invoice_discount_bp"])

    def test_owner_and_owner_approval_are_refused_too(self):
        sid = self.s.create(self.ctx)
        self.refused(lambda: self.s.add_line(self.ctx, sid, self.pan, units=2, discount_bp=3000))
        approved = accounts.with_approval(self.db, self.m, self.ids["owner"], "9753")
        sid = self.s.create(self.m)
        self.refused(lambda: self.s.add_line(approved, sid, self.pan, units=2, discount_bp=3000))

    def test_at_cost_needs_a_reason_and_the_right(self):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=2, discount_bp=2500)      # 30 = cost exactly
        with self.assertRaises(Conflict) as e:
            self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions={})
        self.assertEqual(e.exception.code, "at_cost_reason")
        done = self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions={}, cost_reason="عميل دائم")
        self.assertEqual(done["status"], "completed")
        a = self.db.one("SELECT after_json FROM audit_log WHERE action='sale.complete' AND entity_id=?", (sid,))
        self.assertIn("عميل دائم", a["after_json"])
        # a pharmacist with a discount limit high enough still cannot sell at cost
        people.set_role_discount(self.db, self.ctx, "pharmacist", 5000)
        ph = self.login("pharmacist", PW)
        sid = self.s.create(ph)
        d = self.s.add_line(ph, sid, self.pan, units=2, discount_bp=2500)
        with self.assertRaises(Forbidden):
            self.s.complete(ph, sid, paid_cash=d["total"], safety_decisions={}, cost_reason="سبب")

    def test_owner_sees_the_most_allowed_when_approval_is_not_his(self):
        # a person with costs.read but not sales.below_cost is told the most he can give
        uid = self.ids["manager"]
        people.set_exception(self.db, self.ctx, uid, "costs.read", "grant")
        m = self.login("manager", PW)
        sid = self.s.create(m)
        e = self.refused(lambda: self.s.add_line(m, sid, self.pan, units=2, discount_bp=3000))
        self.assertEqual(e.details["max_discount"], 1000)

    def test_user_limit_still_applies(self):
        sid = self.s.create(self.ph)
        with self.assertRaises(Forbidden) as e:
            self.s.add_line(self.ph, sid, self.pan, units=2, discount_bp=500)
        self.assertEqual(e.exception.details["permission"], "discount")


class TestBelowCostPaths(TestBelowCost):
    """Independent review of U2-33: every path that can move a discount under cost, and nothing more."""
    def setUp(self):
        super().setUp()
        from datetime import timedelta
        from decimal import Decimal
        from app.catalog import service as cat
        from app.inventory import service as inv
        from tests.test_cashier_acceptance import T
        self.big = cat.create_item(self.db, self.ctx, {"name_en": "Big", "pack_size": 1, "public_price": 10000})
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.big, "X1", (T + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.big, qty_delta=20, batch_id=b,
                              movement_type="purchase", unit_cost=Decimal("10"))

    def pan_line(self, sid):
        return next(l for l in self.s.get(self.m, sid)["lines"] if l["item_id"] == self.pan)

    def test_invoice_discount_amount_and_remove_line(self):
        sid = self.s.create(self.m)
        self.s.add_line(self.m, sid, self.pan, units=2)                    # 40, cost 30
        lb = next(l["id"] for l in self.s.add_line(self.m, sid, self.big, units=1)["lines"] if l["item_id"] == self.big)
        self.s.set_invoice_discount(self.m, sid, amount=1200)              # Panadol's share ~3.43: fine
        self.refused(lambda: self.s.remove_line(self.m, sid, lb))         # all 12 on Panadol: 28 < 30
        self.assertEqual(len(self.s.get(self.m, sid)["lines"]), 2)         # rolled back
        self.refused(lambda: self.s.set_invoice_discount(self.m, sid, amount=4000))   # Panadol ~11.43: 28.57 < 30

    def test_more_units_from_a_cheaper_batch(self):
        from datetime import timedelta
        from decimal import Decimal
        from app.inventory import service as inv
        from tests.test_cashier_acceptance import T
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.pan, "B2", (T + timedelta(days=500)).isoformat())
            self.db.execute("UPDATE batches SET public_price=3000 WHERE id=?", (b,))
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=4, batch_id=b,
                              movement_type="purchase", unit_cost=Decimal("15"))
        sid = self.s.create(self.m)
        self.s.add_line(self.m, sid, self.pan, units=20, discount_bp=2500)   # exactly at cost
        self.refused(lambda: self.s.add_line(self.m, sid, self.pan, units=4, discount_bp=2500))
        lid = self.pan_line(sid)["id"]
        self.refused(lambda: self.s.update_line(self.m, sid, lid, qty_base=24))

    def test_other_edits_on_an_invoice_at_cost_are_fine(self):
        sid = self.s.create(self.m)
        self.s.add_line(self.m, sid, self.pan, units=2, discount_bp=2500)     # at cost
        self.s.add_line(self.m, sid, self.big, units=1, discount_bp=500)      # another item: fine
        self.s.update_line(self.m, sid, self.pan_line(sid)["id"], qty_base=1)
        self.refused(lambda: self.s.update_line(self.m, sid, self.pan_line(sid)["id"], discount_bp=4000))

    def test_typed_check_and_payment_agree_at_the_boundary(self):
        with self.db.transaction():
            self.db.execute("UPDATE item_costs SET avg_cost='14.997' WHERE item_id=?", (self.pan,))
        sid = self.s.create(self.m)
        lid = self.s.add_line(self.m, sid, self.pan, units=2)["lines"][0]["id"]
        self.refused(lambda: self.s.update_line(self.m, sid, lid, discount_amount=1001))   # 29.99 < 29.994
        d = self.s.update_line(self.m, sid, lid, discount_amount=1000)                     # 30.00 >= 29.994
        self.s.complete(self.m, sid, paid_cash=d["total"], safety_decisions={}, cost_reason="بسعر التكلفة")

    def test_a_price_under_cost_without_discount_is_not_a_discount(self):
        with self.db.transaction():
            self.db.execute("UPDATE item_costs SET avg_cost='25' WHERE item_id=?", (self.pan,))
        sid = self.s.create(self.m)
        lid = self.s.add_line(self.m, sid, self.pan, units=2)["lines"][0]["id"]
        self.s.update_line(self.m, sid, lid, qty_base=3)                    # no discount: not refused here
        self.refused(lambda: self.s.update_line(self.m, sid, lid, discount_bp=100))

    def test_choosing_a_contract_customer_that_takes_the_invoice_discount_under_cost(self):
        """Codex P1 on #409: a contract lowers the line, so the invoice discount it already carries goes under cost."""
        from app.sales import contracts
        k = contracts.save(self.db, self.ctx, {"name": "نقابة", "discount_local_bp": 2000, "discount_imported_bp": 2000})[0]["id"]
        cust = self.s.create_customer(self.ctx, name="عضو", credit_limit=0)
        contracts.link_customer(self.db, self.ctx, cust, k)
        sid = self.s.create(self.m)
        self.s.add_line(self.m, sid, self.pan, units=2)                    # 40, cost 30
        self.s.set_invoice_discount(self.m, sid, amount=500)               # 35: fine
        version = self.s.get(self.m, sid)["version"]
        self.refused(lambda: self.s.set_details(self.m, sid, {"customer_id": cust}))   # 40 - 8 - 5 = 27 < 30
        d = self.s.get(self.m, sid)
        self.assertIsNone(d["customer_id"])                                # rolled back: customer, contract, version
        self.assertEqual((d["lines"][0]["discount"], d["version"]), (500, version))
        approved = accounts.with_approval(self.db, self.m, self.ids["owner"], "9753")
        self.refused(lambda: self.s.set_details(approved, sid, {"customer_id": cust}))   # the owner's PIN: refused too
