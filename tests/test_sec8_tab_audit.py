"""SEC8 (theft): closing an open sale tab that has items, or removing an item from it, left no trace.
Now each writes an audit row (user, time, item count, units, value) on the open shift, the owner's shift summary
shows the count and value per employee, and closing a tab with items raises an alert. Synthetic data only."""
import json
from datetime import timedelta
from decimal import Decimal

from app.catalog import service as cat
from app.inventory import service as inv
from app.reports.owner_summary import shift_summary
from app.sales.cash import CashDrawer
from tests.test_sales import TODAY, SalesCase


class TestTabAudit(SalesCase):
    def setUp(self):
        super().setUp()
        self.shift = CashDrawer(self.db).summary(self.ctx)["id"]
        self.vit = cat.create_item(self.db, self.ctx, {"name_en": "Vitamin C 1g 10 tab", "public_price": 3000})
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.vit, "V1", (TODAY + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.vit, qty_delta=10, batch_id=b,
                              movement_type="purchase", unit_cost=Decimal("12"))

    def rows(self, action):
        return [dict(r) for r in self.db.all(
            "SELECT user_id, entity_type, entity_id, after_json, at FROM audit_log WHERE org_id=? AND action=?"
            " ORDER BY seq", (self.ctx.org_id, action))]

    def tab(self, units=(1, 2)):
        sid = self.s.create(self.ctx)
        for item, u in zip((self.pan, self.vit), units):
            d = self.s.add_line(self.ctx, sid, item, units=u)
        return sid, d

    def test_discarding_a_tab_with_items_is_audited_on_the_shift(self):
        sid, d = self.tab()
        self.s.discard(self.ctx, sid)
        [row] = self.rows("sale.tab_discarded")
        after = json.loads(row["after_json"])
        self.assertEqual((row["user_id"], row["entity_type"], row["entity_id"]), (self.ctx.user_id, "cash_shift", self.shift))
        self.assertEqual((after["sale_id"], after["items"], after["units"], after["value"]), (sid, 2, 3, d["total"]))
        self.assertTrue(row["at"])
        self.assertTrue(self.db.one("SELECT 1 FROM notifications WHERE org_id=? AND kind='tab_discarded'", (self.ctx.org_id,)))

    def test_an_empty_tab_leaves_no_row(self):
        self.s.discard(self.ctx, self.s.create(self.ctx))
        self.assertEqual(self.rows("sale.tab_discarded"), [])

    def test_removing_a_line_is_audited_with_its_value(self):
        sid, d = self.tab()
        line = d["lines"][1]
        self.s.remove_line(self.ctx, sid, line["id"])
        [row] = self.rows("sale.line_removed")
        after = json.loads(row["after_json"])
        self.assertEqual((row["entity_type"], row["entity_id"]), ("cash_shift", self.shift))
        self.assertEqual((after["sale_id"], after["items"], after["units"], after["value"]),
                         (sid, 1, line["qty_base"], line["net"]))

    def test_voiding_a_draft_with_items_is_audited_too(self):
        sid, d = self.tab((1,))
        self.s.void(self.ctx, sid, "تبويب اتقفل تجربة")
        [row] = self.rows("sale.tab_discarded")
        self.assertEqual(json.loads(row["after_json"])["value"], d["total"])

    def test_owner_summary_shows_count_and_value_per_employee(self):
        sid, d = self.tab()
        removed = d["lines"][0]
        self.s.remove_line(self.ctx, sid, removed["id"])
        left = self.s.get(self.ctx, sid)["total"]
        self.s.discard(self.ctx, sid)
        CashDrawer(self.db).close(self.ctx, counted_cash=50000, float_left=50000)
        r = shift_summary(self.db, self.ctx, self.shift)
        [e] = r["tab_losses"]
        self.assertEqual((e["discarded"], e["discarded_value"], e["removed"], e["removed_value"]),
                         (1, left, 1, removed["net"]))
        self.assertIn("تبويبات اتقفلت بأصناف", r["text"])
        self.assertIn("أصناف اتشالت من الفاتورة", r["text"])
