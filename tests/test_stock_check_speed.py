"""«مطابقة الأرصدة» with years of data (#304 B12). The check runs at every start, after every automatic backup
and from the owner's button, and it reads the whole stock ledger each time. Two parts were heavier than what
they check: the comparison of each invoice with the stock it moved built three temporary tables with an index
each, and the per-item totals were recomputed even when every lot had just been found right (a sum of equal
parts is equal). These tests pin: one pass and no temporary index, the item question only when a lot is off,
and the same findings as the query before. Synthetic data only."""
import sqlite3

from app.inventory import integrity, service as inv
from app.sales.cash import CashDrawer
from app.sales.service import Sales
from tests.test_stock_integrity_check import Base

# the query before #304 B12, kept as the reference
OLD_SALES = (
    "WITH mv AS (SELECT ref_id sale_id, item_id, sum(qty_delta) moved FROM stock_movements"
    "            WHERE org_id=? AND ref_type IN ('sale','sale_void') GROUP BY ref_id, item_id),"
    " sd AS (SELECT sale_id, item_id, sum(qty_base) sold FROM sale_lines WHERE org_id=? GROUP BY sale_id, item_id),"
    " rt AS (SELECT l.sale_id, l.item_id, sum(a.returned_qty) returned FROM sale_allocations a"
    "        JOIN sale_lines l ON l.id = a.sale_line_id WHERE l.org_id=? GROUP BY l.sale_id, l.item_id)"
    " SELECT s.id sale_id, s.sale_no, s.status, mv.item_id, mv.moved,"
    "  CASE WHEN s.status='completed' THEN coalesce(rt.returned,0) - coalesce(sd.sold,0) ELSE 0 END expected"
    " FROM mv JOIN sales s ON s.id = mv.sale_id AND s.org_id=?"
    " LEFT JOIN sd ON sd.sale_id = mv.sale_id AND sd.item_id = mv.item_id"
    " LEFT JOIN rt ON rt.sale_id = mv.sale_id AND rt.item_id = mv.item_id"
    " WHERE mv.moved <> CASE WHEN s.status='completed' THEN coalesce(rt.returned,0) - coalesce(sd.sold,0) ELSE 0 END")


def rows(found):
    return sorted(tuple(sorted(dict(r).items())) for r in found)


class _Asked:
    """Every statement sent through db.all, in order."""

    def __init__(self, db):
        self.db, self.sql = db, []

    def __enter__(self):
        all_ = self.db.all
        self.db.all = lambda sql, *a, **k: (self.sql.append((sql, a[0] if a else ())), all_(sql, *a, **k))[1]
        return self

    def __exit__(self, *exc):
        del self.db.all


class TestSalesAgainstTheLedger(Base):
    def both(self, org_id=None):
        org_id = org_id or self.ctx.org_id
        return (rows(integrity._sales(self.db, org_id)), rows(self.db.all(OLD_SALES, (org_id,) * 4)))

    def history(self):
        """Sales, a partial return, a full return and a void: nothing wrong yet."""
        s = Sales(self.db)
        a, b, c, d = self.sell(4), self.sell(2), self.sell(6), self.sell(2)
        s.return_items(self.ctx, b["id"], {b["lines"][0]["id"]: 1}, refund_method="card", reason="مرتجع")
        s.return_items(self.ctx, c["id"], {c["lines"][0]["id"]: 6}, refund_method="card", reason="مرتجع")
        s.void(self.ctx, d["id"], "خطأ في الفاتورة")
        return a, b, c, d

    def test_a_clean_history_finds_nothing(self):
        self.history()
        new, old = self.both()
        self.assertEqual((new, old), ([], []))

    def test_every_kind_of_difference_is_found_as_before(self):
        a, b, c, d = self.history()
        e = self.sell(2)
        move = ("INSERT INTO stock_movements (id, org_id, branch_id, item_id, batch_id, qty_delta, movement_type,"
                " ref_type, ref_id) VALUES (?,?,?,?,?,?,?,?,?)")
        org, br = self.ctx.org_id, self.ctx.branch_id
        self.raw(move, ("x1", org, br, self.pan, self.b1, -1, "sale", "sale", a["id"]))            # one more left the shelf
        self.raw("UPDATE sale_lines SET qty_base = 3 WHERE sale_id=?", (b["id"],))                   # the invoice says 3
        self.raw("UPDATE sale_allocations SET returned_qty = 5 WHERE sale_line_id=?", (c["lines"][0]["id"],))
        self.raw(move, ("x2", org, br, self.pan, self.b1, -2, "sale", "sale", d["id"]))            # a voided invoice moved stock
        self.raw(move, ("x3", org, br, self.aug, self.b2, -1, "sale", "sale", e["id"]))            # an item the invoice has no line for
        self.raw(move, ("x4", org, br, self.aug, self.b2, -1, "sale", "sale", "no-such-sale"))     # no invoice: not judged here
        new, old = self.both()
        self.assertEqual(new, old)
        found = {(dict(r)["sale_id"], dict(r)["item_id"]): (dict(r)["moved"], dict(r)["expected"]) for r in new}
        self.assertEqual(found, {(a["id"], self.pan): (-5, -4), (b["id"], self.pan): (-1, -2),
                                 (c["id"], self.pan): (0, -1), (d["id"], self.pan): (-2, 0),
                                 (e["id"], self.aug): (-1, 0)})

    def test_a_line_that_moved_nothing_is_not_judged(self):
        """As before: whether an item is a service can change later, so a line without a movement says nothing."""
        a = self.sell(2)
        self.raw("INSERT INTO sale_lines (id, sale_id, org_id, line_no, item_id, qty_base) VALUES ('ghost',?,?,9,?,4)",
                 (a["id"], self.ctx.org_id, self.aug))
        self.assertEqual(self.both(), ([], []))

    def test_another_pharmacy_is_judged_on_its_own(self):
        a = self.sell(2)
        org, octx = self.second_tenant()
        self.raw("UPDATE sale_lines SET qty_base = 5 WHERE sale_id=?", (a["id"],))
        mine, old = self.both()
        self.assertEqual(mine, old)
        self.assertEqual(len(mine), 1)
        self.assertEqual(self.both(octx.org_id), ([], []))

    def test_one_pass_and_no_temporary_index(self):
        """Before: the lines and the returns were each grouped into a temporary table, each got an index built on
        the spot, and every allocation was looked up to find the few returned ones."""
        self.sell(2)
        with _Asked(self.db) as q:
            integrity._sales(self.db, self.ctx.org_id)
        self.assertEqual(len(q.sql), 1)
        sql, params = q.sql[0]
        plan = [r["detail"] for r in self.db.all("EXPLAIN QUERY PLAN " + sql, params)]
        self.assertFalse([d for d in plan if "AUTOMATIC" in d or "MATERIALIZE" in d or "CORRELATED" in d], plan)
        self.assertEqual(len([d for d in plan if "sale_lines" in d and d.startswith("SCAN")]), 1, plan)
        self.assertIn("SEARCH s USING INDEX sqlite_autoindex_sales_1 (id=?)", plan)       # the invoice by its key
        self.assertEqual(sql.count("org_id=?"), 4)                                         # every table by pharmacy


class TestItemTotals(Base):
    def test_the_item_totals_are_not_recomputed_when_every_lot_is_right(self):
        self.sell(2)
        with _Asked(self.db) as q:
            r = integrity.scan(self.db, self.ctx.org_id)
        self.assertTrue(r["ok"])
        self.assertEqual(r["counts"]["items"], 0)
        self.assertEqual([s for s, _ in q.sql if "GROUP BY branch_id, item_id HAVING" in s], [])

    def test_a_lot_that_is_off_still_gives_the_item_finding(self):
        self.raw("UPDATE stock_balances SET qty = qty + 2 WHERE item_id=?", (self.pan,))
        with _Asked(self.db) as q:
            r = integrity.scan(self.db, self.ctx.org_id)
        self.assertEqual((r["counts"]["batches"], r["counts"]["items"]), (1, 1))
        self.assertEqual((r["items"][0]["ledger_qty"], r["items"][0]["balance_qty"]), (20, 22))
        self.assertEqual(len([s for s, _ in q.sql if "GROUP BY branch_id, item_id HAVING" in s]), 1)

    def test_two_lots_off_that_cancel_out_are_a_lot_finding_only(self):
        """One unit booked on the wrong lot of the same item: the item total is right, the lots are not."""
        with self.db.transaction():
            other = inv.get_or_create_batch(self.db, self.ctx, self.pan, "B2", "2030-01-31")
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=5, batch_id=other, movement_type="purchase")
        self.raw("UPDATE stock_balances SET qty = qty + 1 WHERE item_id=? AND batch_key=?", (self.pan, self.b1))
        self.raw("UPDATE stock_balances SET qty = qty - 1 WHERE item_id=? AND batch_key=?", (self.pan, other))
        r = integrity.scan(self.db, self.ctx.org_id)
        self.assertEqual((r["counts"]["batches"], r["counts"]["items"]), (2, 0))

    def test_the_proof_holds_on_a_random_ledger(self):
        """No lot finding => no item finding, on balances damaged at random (the short cut's only assumption)."""
        import random
        rnd = random.Random(304)
        with self.db.transaction():
            lots = [self.b1] + [inv.get_or_create_batch(self.db, self.ctx, self.pan, f"R{n}", "2030-01-31") for n in range(4)]
            for lot in lots[1:]:
                inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=9, batch_id=lot, movement_type="purchase")
        for _ in range(25):
            c = sqlite3.connect(self.db.path)
            for lot in rnd.sample(lots, rnd.randint(0, 3)):
                c.execute("UPDATE stock_balances SET qty = qty + ? WHERE item_id=? AND batch_key=?",
                          (rnd.choice((-1, 1, 2)), self.pan, lot))
            c.commit()
            c.close()
            lots_off = inv.verify_balances(self.db, self.ctx.org_id)
            items_off = integrity._items(self.db, self.ctx.org_id)
            self.assertTrue(lots_off or not items_off, (lots_off, items_off))
            self.assertEqual(integrity.scan(self.db, self.ctx.org_id)["counts"]["items"], len(items_off))
