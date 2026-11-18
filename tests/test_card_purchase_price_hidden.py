"""Owner decision 17/11 (Heikal finding F1): the purchase price on the item card is hidden by default for every
role except the owner; the owner can open it for one person («عرض التكلفة والربح») and close it again."""
from datetime import timedelta

from app import config
from app.catalog import card, service as cat
from app.core import people
from app.purchasing.service import Purchasing
from tests.test_cashier_acceptance import Base, T

PW = "Staff-Pass-2026"


class TestCardPurchasePrice(Base):
    def setUp(self):
        super().setUp()
        p = Purchasing(self.db, config.load())
        sid = cat.create_supplier(self.db, self.ctx, name="مورد تجربة")
        inv = p.create_manual(self.ctx, supplier_id=sid)
        p.update_header(self.ctx, inv, {"invoice_no": "F1"}, p.get(self.ctx, inv)["invoice"]["version"])
        p.add_line(self.ctx, inv, {"item_id": self.pan, "qty": 5, "public_price": 4000, "discount_bp": 2500,
                                   "expiry_date": (T + timedelta(days=500)).isoformat()},
                   p.get(self.ctx, inv)["invoice"]["version"])
        p.approve(self.ctx, inv, p.get(self.ctx, inv)["invoice"]["version"])
        self.users = {}
        for role in ("assistant", "pharmacist", "manager"):
            uid = people.add_user(self.db, self.ctx, username=role, full_name=role, password=PW, role_code=role)
            self.users[role] = uid["id"] if isinstance(uid, dict) else uid

    def test_owner_sees_purchase_price(self):
        c = card.card(self.db, self.ctx, self.pan)
        self.assertEqual([s["unit_net_price"] for s in c["suppliers"]], [3000])

    def test_every_other_default_role_does_not(self):
        for role in ("assistant", "pharmacist", "manager"):
            with self.subTest(role=role):
                self.assertNotIn("suppliers", card.card(self.db, self.login(role, PW), self.pan))

    def test_owner_opens_it_for_one_person_and_closes_it(self):
        uid = self.users["assistant"]
        people.set_exception(self.db, self.ctx, uid, "costs.read", "grant")
        self.assertIn("suppliers", card.card(self.db, self.login("assistant", PW), self.pan))
        self.assertNotIn("suppliers", card.card(self.db, self.login("pharmacist", PW), self.pan))
        people.set_exception(self.db, self.ctx, uid, "costs.read", "default")
        self.assertNotIn("suppliers", card.card(self.db, self.login("assistant", PW), self.pan))
