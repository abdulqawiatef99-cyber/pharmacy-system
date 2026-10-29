"""Splitting a pack never leaves fractions: units are whole numbers, full packs cost exactly the
pack price, and the returns of a line add up to exactly what was paid."""
from datetime import timedelta
from decimal import Decimal

from app.catalog import service as cat
from app.inventory import service as inv
from tests.test_cashier_acceptance import Base, T


class TestSplit(Base):
    def setUp(self):
        super().setUp()
        self.x = cat.create_item(self.db, self.ctx, {"name_en": "Strips 3", "pack_size": 3, "public_price": 10000})  # 100.00 / 3
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.x, "S1", (T + timedelta(days=300)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.x, qty_delta=30, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("25"))
        self.cd.open(self.ctx, 0)

    def sale(self, units, disc=0):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.x, units=units, discount_bp=disc)
        f = self.s.safety_check(self.ctx, sid)
        return self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions={i: "ok" for i in range(len(f))})

    def test_full_pack_is_pack_price(self):
        self.assertEqual(self.sale(3)["total"], 10000)              # not 9999
        self.assertEqual(self.sale(6)["total"], 20000)
        self.assertEqual(self.sale(4)["total"], 13333)              # 1 pack + 1 strip
        self.assertEqual(self.sale(2)["total"], 6666)
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.x)["total_qty"], 15)   # whole units only

    def test_returns_add_up_exactly(self):
        s = self.sale(3, disc=1000)                                  # 100 - 10% = 90.00
        self.assertEqual(s["total"], 9000)
        line = s["lines"][0]["id"]
        refunds = [self.s.return_items(self.ctx, s["id"], {line: 1}, refund_method="cash", reason="مرتجع")["refund"]
                   for _ in range(3)]
        self.assertEqual(sum(refunds), 9000)                         # one by one, still exactly what was paid
        self.assertEqual(self.s.get(self.ctx, s["id"])["returned_total"], 9000)

    def test_odd_discount_and_two_returns(self):
        s = self.sale(5, disc=750)                                   # 166.66 gross, 7.5% off
        paid = s["total"]
        line = s["lines"][0]["id"]
        r1 = self.s.return_items(self.ctx, s["id"], {line: 2}, refund_method="cash", reason="مرتجع")["refund"]
        r2 = self.s.return_items(self.ctx, s["id"], {line: 3}, refund_method="cash", reason="مرتجع")["refund"]
        self.assertEqual(r1 + r2, paid)
        self.assertEqual(self.cd.summary(self.ctx)["expected_cash"], 0)   # drawer back to zero exactly


class TestPackAcrossBatches(Base):
    """A whole pack whose strips come from two batches (FEFO) still costs exactly the pack price."""
    def setUp(self):
        super().setUp()
        self.x = cat.create_item(self.db, self.ctx, {"name_en": "Strips 3", "pack_size": 3, "public_price": 10000})
        with self.db.transaction():
            for no, days, q in (("A", 100, 2), ("B", 300, 30)):      # 2 strips left in the older batch
                b = inv.get_or_create_batch(self.db, self.ctx, self.x, no, (T + timedelta(days=days)).isoformat())
                inv.post_movement(self.db, self.ctx, item_id=self.x, qty_delta=q, batch_id=b,
                                  movement_type="purchase", unit_cost=Decimal("25"))
        self.cd.open(self.ctx, 0)

    def sale(self, **qty):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.x, **qty)
        f = self.s.safety_check(self.ctx, sid)
        return d, self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions={i: "ok" for i in range(len(f))})

    def test_one_pack_from_two_batches_is_the_pack_price(self):
        preview, s = self.sale(packs=1)
        self.assertEqual(len(s["lines"][0]["allocations"]), 2)        # really split over A and B
        self.assertEqual((preview["total"], s["total"]), (10000, 10000))   # not 99.99

    def test_returns_still_add_up_to_what_was_paid(self):
        _, s = self.sale(packs=1)
        line = s["lines"][0]["id"]
        refunds = [self.s.return_items(self.ctx, s["id"], {line: 1}, refund_method="cash", reason="مرتجع")["refund"]
                   for _ in range(3)]
        self.assertEqual(sum(refunds), 10000)
        self.assertEqual(self.cd.summary(self.ctx)["expected_cash"], 0)

    def test_report_shows_no_phantom_discount(self):
        from app.reports import invoices
        _, s = self.sale(packs=1)
        row = next(r for r in invoices.report(self.db, self.ctx, date_from=T.isoformat(), date_to=T.isoformat())["rows"] if r["id"] == s["id"])
        self.assertEqual((row["item_discount"], row["customer_discount"]), (0, 0))
