"""Supplier type and account, payments, shortages list."""
from datetime import timedelta

from app import config
from app.catalog import card, service as cat
from app.core import people
from app.core.errors import Forbidden, ValidationFailed
from app.procurement import suppliers as sup
from app.purchasing.service import Purchasing
from tests.test_cashier_acceptance import Base, T

PW = "Staff-Pass-2026"


class TestSuppliers(Base):
    def setUp(self):
        super().setUp()
        self.p = Purchasing(self.db, config.load())
        self.sid = cat.create_supplier(self.db, self.ctx, name="مخزن النهضة")

    def buy(self, packs=5, price=4000, disc=2500):
        inv = self.p.create_manual(self.ctx, supplier_id=self.sid)
        v = self.p.get(self.ctx, inv)["invoice"]["version"]
        self.p.update_header(self.ctx, inv, {"invoice_no": f"N{packs}{price}"}, v)
        v = self.p.get(self.ctx, inv)["invoice"]["version"]
        self.p.add_line(self.ctx, inv, {"item_id": self.pan, "qty": packs, "public_price": price, "discount_bp": disc,
                                        "expiry_date": (T + timedelta(days=500)).isoformat()}, v)
        d = self.p.get(self.ctx, inv)
        return self.p.approve(self.ctx, inv, d["invoice"]["version"])

    def test_type_balance_payment_cancel(self):
        sup.update(self.db, self.ctx, self.sid, {"supplier_type": "warehouse"})
        with self.assertRaises(ValidationFailed):
            sup.update(self.db, self.ctx, self.sid, {"supplier_type": "x"})
        inv = self.buy()
        self.assertEqual(sup.balance(self.db, self.ctx.org_id, self.sid), 15000)          # 5 x 40 x 75%
        self.cd.open(self.ctx, 20000)
        acc = sup.pay(self.db, self.ctx, self.sid, 5000, "drawer", "دفعة")
        self.assertEqual(acc["balance"], 10000)
        self.assertEqual(self.cd.summary(self.ctx)["expected_cash"], 15000)              # came out of the drawer
        sup.pay(self.db, self.ctx, self.sid, 1000, "outside")
        self.assertEqual(self.cd.summary(self.ctx)["expected_cash"], 15000)
        self.p.cancel(self.ctx, inv["invoice"]["id"], "فاتورة غلط اتلغت", None)
        self.assertEqual(sup.balance(self.db, self.ctx.org_id, self.sid), -6000)          # we paid 60 for nothing
        people.add_user(self.db, self.ctx, username="pharm", full_name="P", password=PW, role_code="pharmacist")
        ph = self.login("pharm", PW)
        with self.assertRaises(Forbidden):
            sup.pay(self.db, ph, self.sid, 100, "outside")
        self.assertNotIn("balance", sup.listing(self.db, ph)[0])

    def test_shortages_and_cleared_when_bought(self):
        cat.update_item(self.db, self.ctx, self.pan, {"min_stock": 30})
        card.set_shortage(self.db, self.ctx, self.pan, True)
        rows = sup.shortages(self.db, self.ctx)
        self.assertEqual(rows[0]["id"], self.pan)
        self.assertEqual(set(rows[0]["reasons"]), {"marked", "below_min"})
        self.buy(packs=20)
        self.assertIsNone(card.card(self.db, self.ctx, self.pan)["shortage_at"])
        self.assertEqual(sup.shortages(self.db, self.ctx), [])                      # 20 + 40 units > 30
