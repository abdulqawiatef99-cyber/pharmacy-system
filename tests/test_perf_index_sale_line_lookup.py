"""#160 / #253: the item index on sale_lines must not make the sale screen's "this item on this invoice"
lookup read every line the item ever had. Whatever index SQLite picks, the invoice must narrow it."""
from tests.helpers import DBTestCase

LOOKUPS = (
    "SELECT id, qty_base, client_ref FROM sale_lines WHERE sale_id=? AND item_id=? AND discount_bp=?",
    "SELECT id, qty_base, client_ref FROM sale_lines WHERE sale_id=? AND item_id=? ORDER BY line_no DESC LIMIT 1",
)


class SaleLineLookup(DBTestCase):
    def test_item_index_carries_the_invoice(self):
        cols = [r["name"] for r in self.db.all("PRAGMA index_info('ix_sale_lines_item')")]
        self.assertEqual(cols, ["item_id", "sale_id"])

    def test_the_lookup_is_narrowed_by_the_invoice(self):
        for sql in LOOKUPS:
            params = ("s", "i", 0) if sql.count("?") == 3 else ("s", "i")
            plan = " | ".join(r["detail"] for r in self.db.all("EXPLAIN QUERY PLAN " + sql, params))
            self.assertIn("sale_id=?", plan, plan)
            self.assertNotIn("SCAN", plan, plan)
