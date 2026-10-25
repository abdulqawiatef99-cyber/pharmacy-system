"""Service items (no stock), names hidden on the receipt, hidden items."""
from app.catalog import service as cat
from app.catalog.search import search
from app.core import people
from app.core.errors import NotFound
from app.inventory import service as inv
from app.procurement import suppliers as sup
from tests.test_api import APICase, H
from tests.test_cashier_acceptance import Base

PW = "Staff-Pass-2026"


class TestTypes(Base):
    def test_service_item_sells_without_stock(self):
        d = cat.create_item(self.db, self.ctx, {"name_ar": "توصيل للبيت", "public_price": 2000, "is_service": True})
        self.cd.open(self.ctx, 0)
        sid = self.s.create(self.ctx)
        x = self.s.add_line(self.ctx, sid, d, units=3)                   # 3 deliveries, no stock at all
        self.assertEqual(x["total"], 6000)
        f = self.s.safety_check(self.ctx, sid)
        s = self.s.complete(self.ctx, sid, paid_cash=6000, safety_decisions={i: "ok" for i in range(len(f))})
        self.assertFalse(self.db.one("SELECT 1 FROM stock_movements WHERE item_id=?", (d,)))
        r = self.s.return_items(self.ctx, s["id"], {s["lines"][0]["id"]: 1}, refund_method="cash", reason="اتلغى")
        self.assertEqual(r["refund"], 2000)
        self.assertFalse(self.db.one("SELECT 1 FROM stock_movements WHERE item_id=?", (d,)))
        self.assertNotIn(d, [x["id"] for x in sup.shortages(self.db, self.ctx)])   # never "out of stock"

    def test_hidden_item_and_receipt_name(self):
        h = cat.create_item(self.db, self.ctx, {"name_en": "SECRET 10MG", "public_price": 1000, "hidden": True, "hide_on_receipt": True})
        people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        a = self.login("asst", PW)
        self.assertEqual(search(self.db, a, "secret")["items"], [])
        self.assertEqual(len(search(self.db, self.ctx, "secret")["items"]), 1)
        sid = self.s.create(a)
        with self.assertRaises(NotFound):
            self.s.add_line(a, sid, h, units=1)


class TestReceiptHiddenName(APICase):
    def test_receipt_hides_name(self):
        from decimal import Decimal
        self.http_login()
        cat.update_item(self.db, self.ctx, self.pan, {"hide_on_receipt": True})
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.pan, "Q", "2030-01-31")
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=4, batch_id=b, movement_type="purchase", unit_cost=Decimal("10"))
        sid = self.c.post("/api/sales", json={}, headers=H).json()["id"]
        self.c.post(f"/api/sales/{sid}/lines", json={"item_id": self.pan, "packs": 0, "units": 2}, headers=H)
        self.c.post("/api/cash/open", json={"opening_cash": 0}, headers=H)
        r = self.c.post(f"/api/sales/{sid}/complete", json={"payments": [{"method": "cash", "amount": 4000}],
                                                           "safety_decisions": {"0": "ok", "1": "ok", "2": "ok"}}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        page = self.c.get(f"/receipt/{sid}").text
        self.assertNotIn("Panadol", page)
