"""A void after a return that refunded nothing must not put the returned stock back a second time.

The void was refused only when `sales.returned_total` was not zero. A return of a line sold for nothing
(100% discount: a gift line) refunds 0, so that total stays 0 and the void posted the whole line again."""
from app.core.errors import Conflict
from app.inventory import service as inv
from tests.test_sales import SalesCase


class TestVoidAfterFreeReturn(SalesCase):
    # update 3 (U2-33): a 100% discount on an item with a known cost is under cost and refused for everyone, so the
    # gift line is an item whose cost was never recorded (opening stock with no cost), as old data can hold
    def setUp(self):
        super().setUp()
        from app.catalog import service as cat
        self.gift = cat.create_item(self.db, self.ctx, {"name_en": "Gift Sample", "public_price": 500})
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=self.gift, qty_delta=5, movement_type="opening")

    def _sale_with_gift_line(self):
        sid = self.s.create(self.ctx)
        self.s.add_line(self.ctx, sid, self.pan, units=2)                       # paid line
        d = self.s.add_line(self.ctx, sid, self.gift, units=1, discount_bp=10000)  # gift line
        gift = next(l for l in d["lines"] if l["discount_bp"] == 10000)
        d = self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid))
        return sid, gift["id"]

    def _qty(self):
        return (inv.stock_for_item(self.db, self.ctx, self.pan)["total_qty"]
                + inv.stock_for_item(self.db, self.ctx, self.gift)["total_qty"])

    def test_void_refused_after_a_zero_refund_return(self):
        before = self._qty()
        sid, gift_line = self._sale_with_gift_line()
        self.assertEqual(self._qty(), before - 3)
        r = self.s.return_items(self.ctx, sid, {gift_line: 1}, refund_method="cash", reason="هدية رجعت")
        self.assertEqual(r["refund"], 0)
        self.assertEqual(self._qty(), before - 2)
        with self.assertRaises(Conflict):
            self.s.void(self.ctx, sid, "إلغاء بعد مرتجع")
        self.assertEqual(self._qty(), before - 2)            # never more stock than was bought
        self.assertEqual(inv.verify_balances(self.db, self.ctx.org_id), [])

    def test_void_without_a_return_still_works(self):
        before = self._qty()
        sid, _ = self._sale_with_gift_line()
        self.assertEqual(self.s.void(self.ctx, sid, "إلغاء عادي")["status"], "voided")
        self.assertEqual(self._qty(), before)
