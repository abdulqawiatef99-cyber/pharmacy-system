"""A draft priced with a contract discount is re-priced when the contract was stopped, changed or the
customer unlinked before the sale is completed. Synthetic data only."""
from app.core.errors import Conflict
from app.sales import contracts as k
from tests.test_cashier_acceptance import Base


class TestStaleContractDiscount(Base):
    def setUp(self):
        super().setUp()
        self.cd.open(self.ctx, 0)
        self.kid = k.save(self.db, self.ctx, {"name": "نقابة تجربة", "discount_local_bp": 2000, "discount_imported_bp": 0})[0]["id"]
        self.cust = self.s.create_customer(self.ctx, name="عميل تجربة")
        k.link_customer(self.db, self.ctx, self.cust, self.kid)
        self.sid = self.s.create(self.ctx, self.cust)
        self.draft = self.s.add_line(self.ctx, self.sid, self.pan, units=2)
        self.assertEqual(self.draft["total"], 3200)                       # 40 - 20%

    def complete(self, total, **kw):
        return self.s.complete(self.ctx, self.sid, paid_cash=total, safety_decisions={}, **kw)

    def refused_then_full_price(self):
        with self.assertRaises(Conflict) as e:
            self.complete(3200, version=self.draft["version"])
        self.assertEqual(e.exception.code, "price_changed")
        d = self.s.get(self.ctx, self.sid)
        self.assertEqual((d["status"], d["total"], d["discount_total"], d["contract_id"]), ("draft", 4000, 0, None))
        self.assertEqual((d["lines"][0]["discount_bp"], d["lines"][0]["discount_source"]), (0, None))
        self.assertGreater(d["version"], self.draft["version"])
        done = self.complete(4000, version=d["version"])
        self.assertEqual((done["status"], done["total"], done["discount_total"], done["contract_id"]),
                         ("completed", 4000, 0, None))
        self.assertEqual(self.db.one("SELECT count(*) FROM audit_log WHERE org_id=? AND action='sale.contract_repriced'"
                                     " AND entity_id=?", (self.ctx.org_id, self.sid))[0], 1)

    def test_a_stopped_contract(self):
        k.save(self.db, self.ctx, {"is_active": False}, self.kid)
        self.refused_then_full_price()

    def test_an_unlinked_customer(self):
        k.link_customer(self.db, self.ctx, self.cust, None)
        self.refused_then_full_price()

    def test_a_changed_rate_is_the_rate_sold_at(self):
        k.save(self.db, self.ctx, {"discount_local_bp": 500}, self.kid)
        with self.assertRaises(Conflict) as e:
            self.complete(3200)
        self.assertEqual(e.exception.code, "price_changed")
        self.assertEqual(self.complete(3800)["discount_total"], 200)

    def test_an_unchanged_contract_sells_as_before(self):
        done = self.complete(3200, version=self.draft["version"])
        self.assertEqual((done["total"], done["discount_total"], done["contract_id"]), (3200, 800, self.kid))
        self.assertEqual(self.db.one("SELECT count(*) FROM audit_log WHERE action='sale.contract_repriced'")[0], 0)

    def test_a_discount_typed_by_hand_is_left_alone(self):
        self.s.update_line(self.ctx, self.sid, self.draft["lines"][0]["id"], discount_bp=300)
        k.save(self.db, self.ctx, {"is_active": False}, self.kid)
        d = self.s.get(self.ctx, self.sid)
        done = self.complete(d["total"], version=d["version"])   # same total: nothing for the cashier to confirm
        self.assertEqual((done["lines"][0]["discount_bp"], done["discount_total"]), (300, 120))
        self.assertIsNone(done["contract_id"])               # but the sale no longer counts for the contract
