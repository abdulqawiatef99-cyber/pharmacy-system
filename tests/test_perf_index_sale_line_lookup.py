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


class LastPurchaseCostLookup(DBTestCase):
    """The order report's per-item lookup (procurement) has no branch; the approval's has one since #303."""
    SQL = ("SELECT unit_cost FROM stock_movements WHERE org_id=? AND item_id=? AND"
           " movement_type='purchase' AND unit_cost IS NOT NULL ORDER BY rowid DESC LIMIT 1")

    def test_it_is_answered_from_the_item_index_without_sorting(self):
        plan = " | ".join(r["detail"] for r in self.db.all("EXPLAIN QUERY PLAN " + self.SQL, ("o", "i")))
        self.assertIn("ix_moves_item_type", plan, plan)
        self.assertNotIn("TEMP B-TREE", plan, plan)
