"""U2-33: a discount never takes a line under its cost, and never past the person's own limit. Refused at the
moment it is typed (line percent, line amount, more units at a discount, invoice discount); only the owner
(`sales.below_cost`) or his approval gets past. The cost is never shown to someone without costs.read."""
from app.core import accounts, people
from app.core.errors import Forbidden
from tests.test_people import PW, People


class TestBelowCost(People):
    # the fixture item: cost 15/unit, price 20/unit -> 25% is the most a discount can be
    def setUp(self):
        super().setUp()
        people.set_role_discount(self.db, self.ctx, "manager", 5000)
        self.m = self.login("manager", PW)

    def refused(self, fn):
        with self.assertRaises(Forbidden) as e:
            fn()
        self.assertEqual(e.exception.details["permission"], "sales.below_cost")
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

    def test_owner_and_owner_approval_pass(self):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=2, discount_bp=3000)
        self.assertEqual(d["lines"][0]["discount_bp"], 3000)      # completion still asks the owner to confirm
        approved = accounts.with_approval(self.db, self.m, self.ids["owner"], "9753")
        sid = self.s.create(self.m)
        self.s.add_line(approved, sid, self.pan, units=2, discount_bp=3000)

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
