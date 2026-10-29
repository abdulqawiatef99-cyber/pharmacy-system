"""Receipt settings (80/58 mm), receipt content, barcode labels."""
from app.catalog import card
from app.printing import barcode, settings as rs
from app.core.errors import ValidationFailed
from app.sales import channels
from tests.test_api import H
from tests.test_cashier_acceptance import Base
from tests.test_api import APICase
from app.catalog import service as cat


class TestBarcode(Base):
    def test_ean13_and_code128(self):
        self.assertEqual(len(barcode.ean13_modules("6221043010359")), 95)
        self.assertEqual(barcode.svg("6221043010359")[1], "ean13")
        self.assertEqual(barcode.svg("6221025012345")[1], "code128")       # bad check digit -> still printable
        with self.assertRaises(ValueError):
            barcode.code128_modules("دواء")

    def test_settings(self):
        self.assertEqual(rs.get(self.db, self.ctx.org_id)["paper"], 80)
        cfg = rs.save(self.db, self.ctx, {"paper": 58, "lines": ["شبين الكوم — شارع الجلاء", "0482..."], "names": "both"})
        self.assertEqual((cfg["paper"], cfg["lines"][0]), (58, "شبين الكوم — شارع الجلاء"))
        with self.assertRaises(ValidationFailed):
            rs.save(self.db, self.ctx, {"paper": 72})


class TestReceiptAndLabelsAPI(APICase):
    def test_receipt_and_labels(self):
        channels.ensure_defaults(self.db)
        card.ensure_default_categories(self.db)
        from datetime import date, timedelta
        from decimal import Decimal
        from app.inventory import service as inv
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.pan, "B1", (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=10, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("15"))
        cat.add_barcode(self.db, self.ctx, self.pan, "6221043010359")
        self.http_login()
        self.c.put("/api/settings/receipt", json={"lines": ["شارع الجلاء"], "show_expiry": True}, headers=H)
        self.c.post("/api/cash/open", json={"opening_cash": 0}, headers=H)
        sid = self.c.post("/api/sales", json={}, headers=H).json()["id"]
        d = self.c.post(f"/api/sales/{sid}/lines", json={"item_id": self.pan, "packs": 1}, headers=H).json()
        r = self.c.post(f"/api/sales/{sid}/complete", json={"payments": [{"method": "cash", "amount": d["total"]}],
                                                             "safety_decisions": {}}, headers=H)
        if r.status_code == 409:
            n = len(r.json()["error"]["details"]["findings"])
            r = self.c.post(f"/api/sales/{sid}/complete", json={"payments": [{"method": "cash", "amount": d["total"]}],
                                                                 "safety_decisions": {str(i): "ok" for i in range(n)}}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        page = self.c.get(f"/receipt/{sid}").text
        for s in ("شارع الجلاء", "البائع", "صلاحية", "المرتجع خلال 14 يوم", "نقدي"):
            self.assertIn(s, page)
        self.assertIn("receipt-58.css", self.c.get(f"/receipt/{sid}?w=58").text)
        import hashlib
        from app.api.app import WEB_DIR
        v = hashlib.sha256((WEB_DIR / "receipt.css").read_bytes()).hexdigest()[:10]
        self.assertIn(f"/static/receipt.css?v={v}", page)                  # no stale cached copy after an update
        lab = self.c.get("/labels", params={"items": f"{self.pan}:3", "size": "50x30"}).text
        self.assertEqual(lab.count('class="lb"'), 3)
        self.assertIn("6221043010359", lab)
