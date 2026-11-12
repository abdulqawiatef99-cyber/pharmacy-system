"""verify_balances (what every backup, restore and `manage.py verify` runs) reports each (branch, item, batch)
whose cached balance differs from the stock ledger: a balance off by some units, a ledger with no balance row,
and a balance row with no ledger. #113: rewritten as one grouped pass (the old FULL OUTER JOIN took ~100 s
after a synthetic year of sales); the answer must not change."""
import sqlite3
from decimal import Decimal

from app.inventory import service as inv
from tests.test_catalog_inventory import CatalogCase


class TestVerifyBalances(CatalogCase):
    def setUp(self):
        super().setUp()
        with self.db.transaction():
            self.batch = inv.get_or_create_batch(self.db, self.ctx, self.pan, "A1", "2030-01-31")
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=10, batch_id=self.batch,
                              movement_type="opening", unit_cost=Decimal("10"))
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=-3, batch_id=self.batch,
                              movement_type="adjustment")
            inv.post_movement(self.db, self.ctx, item_id=self.aug, qty_delta=5, movement_type="opening",
                              unit_cost=Decimal("20"))

    def raw(self, sql, params=()):
        c = sqlite3.connect(self.db.path)         # bypass the app, as a damaged file or a hand edit would
        c.execute(sql, params)
        c.commit()
        c.close()

    def test_consistent_ledger_reports_nothing(self):
        self.assertEqual(inv.verify_balances(self.db, self.ctx.org_id), [])

    def test_every_kind_of_difference_is_reported_once(self):
        br = self.ctx.branch_id
        self.raw("UPDATE stock_balances SET qty = qty + 2 WHERE item_id=? AND batch_key=?", (self.pan, self.batch))
        self.raw("DELETE FROM stock_balances WHERE item_id=?", (self.aug,))
        self.raw("INSERT INTO stock_balances (org_id, branch_id, item_id, batch_key, qty) VALUES (?,?,?,?,?)",
                 (self.ctx.org_id, br, self.cat50, "", 4))
        got = sorted(inv.verify_balances(self.db, self.ctx.org_id), key=lambda d: d["item_id"])
        want = sorted([
            {"branch_id": br, "item_id": self.pan, "batch_key": self.batch, "ledger_qty": 7, "balance_qty": 9},
            {"branch_id": br, "item_id": self.aug, "batch_key": "", "ledger_qty": 5, "balance_qty": None},
            {"branch_id": br, "item_id": self.cat50, "batch_key": "", "ledger_qty": None, "balance_qty": 4},
        ], key=lambda d: d["item_id"])
        self.assertEqual(got, want)

    def test_other_pharmacy_is_not_read(self):
        _, other = self.second_tenant()
        self.raw("UPDATE stock_balances SET qty = qty + 2 WHERE item_id=?", (self.pan,))
        self.assertEqual(inv.verify_balances(self.db, other.org_id), [])

    def test_one_grouped_pass_not_a_join_of_every_pair(self):
        sql = []
        self.db.conn.set_trace_callback(sql.append)
        try:
            inv.verify_balances(self.db, self.ctx.org_id)
        finally:
            self.db.conn.set_trace_callback(None)
        plan = [r[3] for s in sql if s.lstrip().upper().startswith(("SELECT", "WITH"))
                for r in self.db.conn.execute("EXPLAIN QUERY PLAN " + s).fetchall()]
        self.assertTrue(plan)
        self.assertEqual([d for d in plan if "LEFT-JOIN" in d or "RIGHT-JOIN" in d], [], plan)   # was SCAN l x SCAN b
