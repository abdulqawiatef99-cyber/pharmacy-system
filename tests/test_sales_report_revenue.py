"""#145: the sales report's per-item revenue is what the invoices charged, priced the way each line was sold.

The report used to price each allocation on its own in SQL and round the discounted net in floating point,
so a box split by FEFO over two batches, or a half-piaster discount, drifted from the invoice by a piaster.
Synthetic data only.
"""
from datetime import timedelta
from decimal import Decimal

from app.catalog import service as cat
from app.inventory import service as inventory
from app.sales import service as sales_service
from tests.test_owner_plan_priority_1_2 import PlanCase


class TopItemsRevenue(PlanCase):
    def setUp(self):
        super().setUp()
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})
        self.day = sales_service.local_today().isoformat()

    def top(self):
        rep = self.call("GET", f"/api/reports/sales?from={self.day}&to={self.day}")
        return {t["item_id"]: t for t in rep["top_items"]}, rep

    def split_box(self, name="Split probe", cost=None):
        """A box of 3 strips at 31.00 with 2 strips in the older batch: one box comes from two batches."""
        item = cat.create_item(self.db, self.ctx, {"name_en": name, "pack_size": 3, "public_price": 3100})
        today = sales_service.local_today()
        with self.db.transaction():
            for no, days, qty in (("S1", 100, 2), ("S2", 200, 4)):
                b = inventory.get_or_create_batch(self.db, self.ctx, item, no, (today + timedelta(days=days)).isoformat())
                inventory.post_movement(self.db, self.ctx, item_id=item, qty_delta=qty, batch_id=b,
                                        movement_type="purchase", unit_cost=cost)
        return item

    def test_a_box_split_over_two_batches_is_the_box_price(self):
        item = self.split_box()
        done = self.sell(item, packs=1)
        self.assertEqual(len(done["lines"][0]["allocations"]), 2)
        self.assertEqual(done["total"], 3100)
        top, rep = self.top()
        self.assertEqual(top[item]["revenue"], 3100)                  # was 30.99 (10.33 x 3)
        self.assertEqual(top[item]["qty"], 3)
        self.assertNotIn("profit", top[item])                         # cost unknown

    def test_the_discount_is_rounded_half_up_like_the_invoice(self):
        item = cat.create_item(self.db, self.ctx, {"name_en": "Half probe", "pack_size": 1, "public_price": 1035})
        self.stock(item, 5, "5")
        sid = self.draft()
        self.line(sid, item, units=1, discount_bp=1000)               # 10.35 - 1.035 -> 1.04 off
        done = self.complete(sid, [{"method": "cash", "amount": 931}])
        self.assertEqual(done["total"], 931)
        top, _ = self.top()
        self.assertEqual(top[item]["revenue"], 931)                   # was 9.32
        self.assertEqual(top[item]["profit"], 931 - 500)

    def test_revenue_after_partial_returns_is_what_the_customer_kept(self):
        item = self.split_box("Return probe", cost=Decimal("7"))
        sid = self.draft()
        self.line(sid, item, packs=1, discount_bp=1000)               # 31.00 - 3.10 = 27.90
        done = self.complete(sid, [{"method": "cash", "amount": 2790}])
        lid = done["lines"][0]["id"]
        r = self.call("POST", f"/api/sales/{sid}/return", json={"lines": {lid: 1}, "refund_method": "cash",
                                                                "reason": "مراجعة"})
        top, rep = self.top()
        self.assertEqual(top[item]["qty"], 2)
        self.assertEqual(top[item]["revenue"], 2790 - r["refund"])    # exactly what is left of the invoice
        self.assertEqual(top[item]["profit"], 2790 - r["refund"] - 1400)
        # the per-item revenue adds up to the report's own totals (sales less returns)
        totals = rep["totals"]
        self.assertEqual(sum(t["revenue"] for t in rep["top_items"]), totals["total"] - totals["returns"])

    def test_many_lines_with_returns_are_each_valued_on_their_own_allocations(self):
        """Several sales with returns in the period, read in one batch: each line keeps its own value."""
        a, b = self.split_box("Batch A", cost=Decimal("7")), self.split_box("Batch B", cost=Decimal("7"))
        refunds = {}
        for item, keep_bp in ((a, 0), (b, 750)):
            sid = self.draft()
            d = self.line(sid, item, packs=1, discount_bp=keep_bp)
            done = self.complete(sid, [{"method": "cash", "amount": d["total"]}])
            lid = done["lines"][0]["id"]
            r = self.call("POST", f"/api/sales/{sid}/return", json={"lines": {lid: 2}, "refund_method": "cash",
                                                                    "reason": "مراجعة"})
            refunds[item] = d["total"] - r["refund"]
        self.sell(self.box, units=1)                                  # an untouched line in the same period
        top, rep = self.top()
        self.assertEqual({k: top[k]["revenue"] for k in (a, b)}, refunds)
        self.assertEqual(top[self.box]["revenue"], 3333)
        totals = rep["totals"]
        self.assertEqual(sum(t["revenue"] for t in rep["top_items"]), totals["total"] - totals["returns"])

    def test_a_return_that_refunded_nothing_is_still_valued(self):
        """A pack of 2 at 0.01: returning one strip refunds 0.00, so the sale shows no returned money; the
        line is still read and valued (0.01 kept)."""
        item = cat.create_item(self.db, self.ctx, {"name_en": "Penny probe", "pack_size": 2, "public_price": 1})
        self.stock(item, 4, "0")
        done = self.sell(item, packs=1)
        self.assertEqual(done["total"], 1)
        r = self.call("POST", f"/api/sales/{done['id']}/return", json={"lines": {done["lines"][0]["id"]: 1},
                                                                       "refund_method": "cash", "reason": "مراجعة"})
        self.assertEqual(r["refund"], 0)
        top, _ = self.top()
        self.assertEqual((top[item]["qty"], top[item]["revenue"]), (1, 1))

    def test_a_line_sold_under_the_old_per_batch_rule_keeps_its_recorded_total(self):
        """A sale recorded before 0.4 priced each batch alone (2 x 10.33 + 10.33 = 30.99, #72): the report
        shows the money that sale actually took, not today's rule."""
        item = self.split_box("Legacy probe")
        done = self.sell(item, packs=1)
        with self.db.transaction():                                    # what the old release recorded
            self.db.execute("UPDATE sale_lines SET line_total=3099 WHERE id=?", (done["lines"][0]["id"],))
            self.db.execute("UPDATE sales SET total=3099 WHERE id=?", (done["id"],))
        top, _ = self.top()
        self.assertEqual(top[item]["revenue"], 3099)

    def test_ordered_by_revenue_and_limited_to_fifty(self):
        items = []
        for n in range(52):
            it = cat.create_item(self.db, self.ctx, {"name_en": f"Many {n:02d}", "pack_size": 1, "public_price": 100 + n})
            self.stock(it, 1, "1")
            items.append(it)
        sid = self.draft()
        for it in items:
            self.line(sid, it, units=1)
        d = self.call("GET", f"/api/sales/{sid}")
        self.complete(sid, [{"method": "cash", "amount": d["total"]}])
        _, rep = self.top()
        rev = [t["revenue"] for t in rep["top_items"]]
        self.assertEqual(len(rev), 50)
        self.assertEqual(rev, sorted(rev, reverse=True))
        self.assertEqual(rev[0], 151)
        self.assertEqual(rep["top_items"][0]["name_en"], "Many 51")
