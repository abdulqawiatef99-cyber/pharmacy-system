"""POST /api/procurement/offer worked in float pounds: 2.5 packs became 2 (int()), the cost was
public_price / 100 * packs * (1 - pct / 100) and stored in the audit row as a float. Now packs
must be whole, the discount and the cash are exact hundredths, and the cost is whole piasters
with the same half-up discount as a sale. Synthetic items only."""
from app.catalog import service as cat
from app.procurement.service import Procurement
from app.core.errors import ValidationFailed
from tests.test_api import APICase, H


class OfferInWholeNumbers(APICase):
    def setUp(self):
        super().setUp()
        self.http_login()
        self.it = cat.create_item(self.db, self.ctx, {"name_en": "Offer Item", "public_price": 1001})

    def post(self, **b):
        return self.c.post("/api/procurement/offer", headers=H, json={"item_id": self.it, **b})

    def test_cost_is_whole_piasters_rounded_half_up_like_a_sale(self):
        r = self.post(packs=3, discount_pct="12.5")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["cost"], 2628)            # 3003 - 375.375 -> 3003 - 375
        self.assertIsInstance(r.json()["cost"], int)
        row = self.db.one("SELECT after_json FROM audit_log WHERE org_id=? AND action='procurement.offer_eval'"
                          " ORDER BY seq DESC LIMIT 1", (self.ctx.org_id,))
        self.assertIn('"cost": 2628', row["after_json"])
        self.assertIn('"discount_bp": 1250', row["after_json"])

    def test_fractional_packs_are_refused_not_cut(self):
        for packs in (2.5, "2.5", True, "x"):
            with self.subTest(packs=packs):
                self.assertEqual(self.post(packs=packs, discount_pct=10).status_code, 422)

    def test_discount_and_cash_take_at_most_two_decimals(self):
        for b in ({"discount_pct": "12.345"}, {"discount_pct": "nan"}, {"discount_pct": True},
                  {"discount_pct": 10, "cash_available": "26.275"}, {"discount_pct": 10, "cash_available": "inf"},
                  {"discount_pct": 100}, {"discount_pct": -1}, {"discount_pct": 10, "cash_available": -5}):
            with self.subTest(b=b):
                self.assertEqual(self.post(packs=1, **b).status_code, 422, b)

    def test_cash_is_compared_exactly(self):
        enough = self.post(packs=3, discount_pct="12.5", cash_available="26.28").json()
        self.assertNotIn("التكلفة أكبر من السيولة المتاحة", enough["reasons"])
        short = self.post(packs=3, discount_pct="12.5", cash_available="26.27").json()
        self.assertIn("التكلفة أكبر من السيولة المتاحة", short["reasons"])
        self.assertEqual(short["verdict"], "no")

    def test_service_refuses_floats(self):
        p = Procurement(self.db)
        for kw in ({"packs": 2.5, "discount_bp": 0}, {"packs": 1, "discount_bp": 12.5},
                   {"packs": 1, "discount_bp": 0, "cash_available": 10.5}):
            with self.subTest(kw=kw), self.assertRaises(ValidationFailed):
                p.evaluate_offer(self.ctx, self.it, **kw)
