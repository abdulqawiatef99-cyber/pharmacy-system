"""#253 scale run, B1 and B3: two lookups that read a whole table on every call, on the server's event loop.
They must be answered from an index that narrows to the item / to the system rows."""
from app.inventory import integrity
from tests.helpers import DBTestCase


def plan(db, sql, params):
    return " | ".join(r["detail"] for r in db.all("EXPLAIN QUERY PLAN " + sql, params))


class NoFullReads(DBTestCase):
    def capture(self, needle, call):
        real, seen = self.db.one, []
        self.db.one = lambda sql, *a, **k: (seen.append((sql, a[0] if a else ())), real(sql, *a, **k))[1]
        try:
            call()
        finally:
            del self.db.one
        hits = [q for q in seen if needle in q[0]]
        self.assertTrue(hits, [q[0][:60] for q in seen])
        return hits[0]

    def test_stock_alert_state_reads_only_the_system_rows(self):
        sql, params = self.capture("audit_log", lambda: integrity.last_state(self.db, self.ctx.org_id))
        p = plan(self.db, sql, params)
        self.assertIn("ix_audit_org_entity", p, p)
        self.assertIn("entity_type=?", p, p)

    def test_stock_alert_state_still_finds_what_the_check_records(self):
        self.assertIsNone(integrity.last_state(self.db, self.ctx.org_id))
        with self.db.transaction():
            self.db.execute("UPDATE stock_balances SET qty=qty+1 WHERE org_id=?", (self.ctx.org_id,))
            from app.catalog import service as cat
            from app.inventory import service as inv
            item = cat.create_item(self.db, self.ctx, {"name_en": "Scan probe", "pack_size": 1, "public_price": 100})
            b = inv.get_or_create_batch(self.db, self.ctx, item, "B1", "2030-01-01")
            inv.post_movement(self.db, self.ctx, item_id=item, qty_delta=5, batch_id=b, movement_type="purchase")
            self.db.execute("UPDATE stock_balances SET qty=qty+1 WHERE org_id=? AND item_id=?", (self.ctx.org_id, item))
        integrity.check(self.db, self.ctx.org_id, "test")
        st = integrity.last_state(self.db, self.ctx.org_id)
        self.assertTrue(st and st["alert"], st)

    def test_last_purchase_cost_is_read_from_the_items_own_movements(self):
        import re
        from pathlib import Path
        src = Path("app/purchasing/service.py").read_text(encoding="utf-8")
        m = re.search(r'"(SELECT unit_cost FROM stock_movements[^"]*)"\s*"([^"]*)"', src)
        sql = m.group(1) + m.group(2)
        self.assertIn("branch_id=?", sql)
        p = plan(self.db, sql, ("o", "b", "i"))
        self.assertIn("ix_moves_item", p, p)
        self.assertIn("item_id=?", p, p)
