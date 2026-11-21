"""Update 2 purchases: U2-2 fast expiry entry, U2-22 near-expiry guard (a reason to approve)."""
from datetime import date, timedelta

from app.catalog import service as cat
from app.core.errors import Conflict
from app.db import new_id
from app.inventory import service as inv
from app.purchasing.model import parse_expiry
from tests.test_u2_purchase_base import PurchaseBase


class TestFastExpiry(PurchaseBase):
    def test_compact_forms(self):
        self.assertEqual(parse_expiry("1128"), ("2028-11-30", "month"))
        self.assertEqual(parse_expiry("112028"), ("2028-11-30", "month"))
        self.assertEqual(parse_expiry("11/28"), ("2028-11-30", "month"))
        for bad in ("1328", "2027", "128", "11280"):
            self.assertEqual(parse_expiry(bad), (None, None), bad)


class TestNearExpiryGuard(PurchaseBase):
    def setUp(self):
        super().setUp()
        with self.db.transaction():       # sells one strip a day for 90 days -> 30 a month
            inv.post_movement(self.db, self.ctx, item_id=self.item, qty_delta=-90, movement_type="sale",
                              ref_type="sale", ref_id=new_id(), allow_negative=True)

    def codes(self, inv_id):
        d = self.p.get(self.ctx, inv_id)
        return d, {i["code"]: i for i in d["issues"] if i["level"] == "error" and not i["acknowledged"]}

    def test_more_than_will_sell_needs_a_reason(self):
        exp = (date.today() + timedelta(days=300)).isoformat()      # 210 days before "near": ~210 strips
        inv_id = self.draft(qty=300, expiry=exp)                        # 300 boxes of 1
        d, issues = self.codes(inv_id)
        i = issues["slow_before_expiry"]
        self.assertIn("هتبيع حوالي", i["message"])
        self.assertLess(i["details"]["will_sell"], 300)
        with self.assertRaises(Conflict):
            self.p.approve(self.ctx, inv_id, d["invoice"]["version"])
        d = self.p.acknowledge(self.ctx, inv_id, "slow_before_expiry", "عرض كبير من المورد", i["line_id"],
                               d["invoice"]["version"])
        self.assertNotIn("slow_before_expiry", self.codes(inv_id)[1])
        self.p.approve(self.ctx, inv_id, d["invoice"]["version"])

    def test_a_quantity_that_sells_in_time_passes(self):
        inv_id = self.draft(qty=20, expiry=(date.today() + timedelta(days=400)).isoformat())
        self.assertNotIn("slow_before_expiry", self.codes(inv_id)[1])

    def test_short_expiry_needs_a_reason(self):
        inv_id = self.draft(qty=5, expiry=(date.today() + timedelta(days=100)).isoformat())
        self.assertIn("short_expiry", self.codes(inv_id)[1])

    def test_new_item_never_sold_is_not_flagged(self):
        new = cat.create_item(self.db, self.ctx, {"name_en": "Brand New", "public_price": 1000})
        inv_id = self.draft(qty=300, expiry=(date.today() + timedelta(days=400)).isoformat(), item=new)
        self.assertNotIn("slow_before_expiry", self.codes(inv_id)[1])
