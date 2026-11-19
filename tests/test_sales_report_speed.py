"""The year's sales report with years of data (#304 B11). «الأصناف الأكثر مبيعًا» read one row per sale line
of the period into Python (a million rows for a year of a large pharmacy) and added them up there. Now the
database adds up per item; only the lines of invoices with a return are read one by one, because only those
need the return rule. These tests pin: the rows read do not grow with the number of invoices, the invoices of
the period are reached through their own index, and the figures are the ones the old code gave.
Synthetic data only."""
from datetime import date, timedelta
from decimal import Decimal

from app.catalog import service as cat
from app.core import people
from app.inventory import service as inv
from app.reports import service as reports
from app.sales.cash import CashDrawer
from app.sales.service import (Sales, cost_piasters, local_day_bounds, local_today, outstanding_value,
                               returns_history)
from tests.test_cashier_acceptance import Base

T = date.today()


def reference_top_items(db, ctx, p, limit=50):
    """The code before #304 B11, kept here as the reference: one row per sale line, valued in Python."""
    by_item: dict = {}
    partial = []
    for r in db.all(
            "SELECT l.id, l.sale_id, l.item_id, l.discount_bp, l.line_total, sum(a.qty_base - a.returned_qty) qty,"
            " sum(a.returned_qty) returned, sum(a.unit_cost IS NULL) unknown"
            " FROM sale_allocations a JOIN sale_lines l ON l.id=a.sale_line_id JOIN sales s ON s.id=l.sale_id"
            " WHERE s.org_id=? AND l.org_id=s.org_id AND s.branch_id=? AND s.status='completed'"
            " AND s.completed_at>=? AND s.completed_at<? GROUP BY l.id", p):
        d = by_item.setdefault(r["item_id"], {"item_id": r["item_id"], "qty": 0, "revenue": 0, "unknown": 0})
        d["qty"] += r["qty"]
        d["unknown"] += r["unknown"]
        if r["returned"] == 0 and r["line_total"] is not None:
            d["revenue"] += r["line_total"]
        else:
            partial.append((d, dict(r)))
    cols = "a.sale_line_id, a.id, a.rowid rid, a.qty_base, a.returned_qty, a.unit_price, a.pack_price, a.pack_size"
    history = returns_history(db, ctx.org_id, {l["sale_id"] for _, l in partial})
    for d, l in partial:
        allocs = [dict(a) for a in db.all(f"SELECT {cols} FROM sale_allocations a WHERE a.sale_line_id=?", (l["id"],))]
        kept = {a["id"]: a["qty_base"] - a["returned_qty"] for a in allocs}
        d["revenue"] += outstanding_value(allocs, kept, l["discount_bp"], l["line_total"], l["id"],
                                          history.get(l["sale_id"], []))
    out = {}
    for d in sorted(by_item.values(), key=lambda d: d["revenue"], reverse=True)[:limit]:
        row = {"qty": d["qty"], "revenue": d["revenue"]}
        if not d["unknown"]:
            cost = Decimal(0)
            for r in db.all(
                    "SELECT a.unit_cost, sum(a.qty_base - a.returned_qty) q"
                    " FROM sale_allocations a JOIN sale_lines l ON l.id=a.sale_line_id JOIN sales s ON s.id=l.sale_id"
                    " WHERE s.org_id=? AND l.org_id=s.org_id AND s.branch_id=? AND s.status='completed'"
                    " AND s.completed_at>=? AND s.completed_at<? AND l.item_id=? GROUP BY a.unit_cost",
                    (*p, d["item_id"])):
                cost += Decimal(r["unit_cost"]) * r["q"] * 100
            row["profit"] = d["revenue"] - cost_piasters(cost)
        out[d["item_id"]] = row
    return out


class _Rows:
    """Counts the rows every db.all call returns, with its statement."""

    def __init__(self, db):
        self.db, self.seen = db, []

    def __enter__(self):
        all_ = self.db.all

        def counted(sql, *a, **k):
            rows = all_(sql, *a, **k)
            self.seen.append((sql, a[0] if a else (), len(rows)))
            return rows
        self.db.all = counted
        return self

    def __exit__(self, *exc):
        del self.db.all

    @property
    def rows(self):
        return sum(n for _, _, n in self.seen)


class SalesReportCase(Base):
    def setUp(self):
        super().setUp()
        self.cd.open(self.ctx, 0)
        self.day = local_today().isoformat()
        self.p = (self.ctx.org_id, self.ctx.branch_id, local_day_bounds(local_today())[0],
                  local_day_bounds(local_today())[1])

    def item(self, name, price, pack=1, lots=((10, "5"),)):
        """An item with one lot per (quantity, cost); cost None = bought at an unknown cost."""
        i = cat.create_item(self.db, self.ctx, {"name_en": name, "pack_size": pack, "public_price": price})
        with self.db.transaction():
            for n, (qty, cost) in enumerate(lots):
                b = inv.get_or_create_batch(self.db, self.ctx, i, f"L{n}", (T + timedelta(days=200 + n)).isoformat())
                inv.post_movement(self.db, self.ctx, item_id=i, qty_delta=qty, batch_id=b, movement_type="purchase",
                                  unit_cost=None if cost is None else Decimal(cost))
        return i

    def sale(self, lines, discount_bp=0):
        """One cash invoice of [(item, units), ...]."""
        sid = self.s.create(self.ctx, None)
        for item, units in lines:
            d = self.s.add_line(self.ctx, sid, item, units=units, **({"discount_bp": discount_bp} if discount_bp else {}))
        f = self.s.safety_check(self.ctx, sid)
        return self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions={i: "ok" for i in range(len(f))})

    def top(self, ctx=None):
        return {t["item_id"]: {k: t[k] for k in ("qty", "revenue", "profit") if k in t}
                for t in reports.sales_report(self.db, ctx or self.ctx, self.day, self.day)["top_items"]}


class TestRowsRead(SalesReportCase):
    def test_the_rows_read_do_not_grow_with_the_number_of_invoices(self):
        for _ in range(9):
            self.sell(units=2)
        with _Rows(self.db) as q:
            top = reports._top_items(self.db, self.ctx, self.p)
        self.assertEqual((top[0]["qty"], top[0]["revenue"], top[0]["profit"]), (18, 36000, 36000 - 27000))
        # one item sold at one cost: a row of money, a row of quantity and cost, a row for the name
        self.assertLessEqual(q.rows, 3, [(n, s[:60]) for s, _, n in q.seen])

    def test_only_the_lines_of_invoices_with_a_return_are_read_one_by_one(self):
        for _ in range(6):
            self.sell(units=2)
        s = self.sell(units=2)
        self.s.return_items(self.ctx, s["id"], {s["lines"][0]["id"]: 1}, refund_method="cash", reason="مرتجع")
        with _Rows(self.db) as q:
            top = reports._top_items(self.db, self.ctx, self.p)
        self.assertEqual((top[0]["qty"], top[0]["revenue"]), (13, 26000))
        per_line = [n for sql, _, n in q.seen if "a.returned_qty" in sql and "GROUP BY" not in sql]
        self.assertEqual(per_line, [1])                                # the returned invoice's one allocation

    def test_the_number_of_questions_is_fixed(self):
        other = self.item("Second 10 tab", 1500)
        for _ in range(4):
            self.sell(units=2)
            self.sale([(other, 1)])
        with _Rows(self.db) as q:
            reports._top_items(self.db, self.ctx, self.p)
        # quantities and costs, money, the invoices with a return (none), names
        self.assertEqual(len(q.seen), 4, [s[:70] for s, _, _ in q.seen])
        for _ in range(5):
            self.sell(units=2)
            self.sale([(other, 1)])
        with _Rows(self.db) as more:
            reports._top_items(self.db, self.ctx, self.p)
        self.assertEqual((len(more.seen), more.rows), (len(q.seen), q.rows))

    def test_each_question_starts_from_the_invoices_of_the_period(self):
        """Not from every line an item ever had (`ix_sale_lines_item`): that reads the years outside the period."""
        s = self.sell(units=2)
        self.s.return_items(self.ctx, s["id"], {s["lines"][0]["id"]: 1}, refund_method="cash", reason="مرتجع")
        with _Rows(self.db) as q:
            reports._top_items(self.db, self.ctx, self.p)
        asked = [(sql, params) for sql, params, _ in q.seen if "FROM sales s" in sql]
        self.assertEqual(len(asked), 3)                                # money, quantities, the returned invoices
        for sql, params in asked:
            steps = [r["detail"] for r in self.db.all("EXPLAIN QUERY PLAN " + sql, params)]
            first = next(d for d in steps if d.startswith(("SEARCH", "SCAN")))
            self.assertIn("SEARCH s USING INDEX ix_sales_completed", first, steps)
            self.assertFalse([d for d in steps if d.startswith("SCAN")], steps)
            self.assertFalse([d for d in steps if "ix_sale_lines_item" in d], steps)
            self.assertIn("org_id=?", sql)


class TestSameFigures(SalesReportCase):
    def build(self):
        """Every shape a line can have, in one day."""
        split = self.item("Split 3 strips", 3100, pack=3, lots=((2, "7"), (13, "8")))      # one box over two lots
        blind = self.item("Unknown cost", 900, lots=((10, None),))
        penny = self.item("Penny 2 strips", 1, pack=2, lots=((4, "0"),))
        legacy = self.item("Before line totals", 1250, lots=((10, "6"),))
        for units in (2, 3, 1):
            self.sell(units=units)
        self.sale([(split, 3), (blind, 2)])                                    # two lines, one over two lots
        self.sale([(split, 1)], discount_bp=1000)
        part = self.sale([(self.pan, 4), (blind, 1)])                           # one line partly returned, one kept
        self.s.return_items(self.ctx, part["id"], {part["lines"][0]["id"]: 1}, refund_method="cash", reason="مرتجع")
        whole = self.sale([(split, 3)])                                         # returned in full
        self.s.return_items(self.ctx, whole["id"], {whole["lines"][0]["id"]: 3}, refund_method="cash", reason="مرتجع")
        twice = self.sale([(self.pan, 4)], discount_bp=750)                     # two returns of one line
        for _ in range(2):
            self.s.return_items(self.ctx, twice["id"], {twice["lines"][0]["id"]: 1}, refund_method="cash", reason="مرتجع")
        free = self.sale([(penny, 2)])                                          # a return that refunds 0.00
        r = self.s.return_items(self.ctx, free["id"], {free["lines"][0]["id"]: 1}, refund_method="cash", reason="مرتجع")
        self.assertEqual(r["refund"], 0)
        old = self.sale([(legacy, 2), (self.pan, 2)])                           # a line from before `line_total`
        gone = self.sell(units=2)
        self.s.void(self.ctx, gone["id"], "خطأ في الفاتورة")
        before = self.sell(units=2)                                             # outside the period
        with self.db.transaction():
            self.db.execute("UPDATE sale_lines SET line_total=NULL WHERE id=?", (old["lines"][0]["id"],))
            self.db.execute("UPDATE sales SET completed_at='2026-01-10T10:00:00.000000Z' WHERE id=?", (before["id"],))
        return {"split": split, "blind": blind, "penny": penny, "legacy": legacy}

    def test_every_shape_of_line_gives_the_old_figures(self):
        i = self.build()
        want = reference_top_items(self.db, self.ctx, self.p)
        got = self.top()
        self.assertEqual(got, want)
        self.assertEqual(set(got), {self.pan, *i.values()})
        self.assertNotIn("profit", got[i["blind"]])                             # a cost nobody recorded
        self.assertEqual((got[i["penny"]]["qty"], got[i["penny"]]["revenue"]), (1, 1))
        self.assertEqual(got[i["legacy"]]["revenue"], 2500)
        rep = reports.sales_report(self.db, self.ctx, self.day, self.day)       # the report's own cross-check (#145)
        self.assertEqual(sum(t["revenue"] for t in rep["top_items"]), rep["totals"]["total"] - rep["totals"]["returns"])

    def test_without_the_right_to_see_costs_there_is_no_profit(self):
        i = self.build()
        uid = people.add_user(self.db, self.ctx, username="manager1", full_name="Manager", password="Manager-Pass-2026",
                              role_code="manager")
        people.set_exception(self.db, self.ctx, uid, "costs.read", "deny")
        got = self.top(self.login("manager1", "Manager-Pass-2026"))
        self.assertEqual(set(got), {self.pan, *i.values()})
        self.assertFalse([t for t in got.values() if "profit" in t])
        self.assertEqual({k: v["revenue"] for k, v in got.items()}, {k: v["revenue"] for k, v in self.top().items()})

    def test_items_with_the_same_money_come_in_a_fixed_order(self):
        a, b, c = (self.item(f"Tie {n}", 700) for n in "abc")
        for item in (c, a, b):
            self.sale([(item, 1)])
        order = [t["item_id"] for t in reports._top_items(self.db, self.ctx, self.p)]
        self.assertEqual(order, sorted([a, b, c]))                              # by item id, whatever was sold first

    def test_another_pharmacy_is_not_added_in(self):
        self.sell(units=2)
        org, octx = self.second_tenant()
        theirs = cat.create_item(self.db, octx, {"name_en": "Theirs 10 tab", "pack_size": 1, "public_price": 5000})
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, octx, theirs, "O1", (T + timedelta(days=300)).isoformat())
            inv.post_movement(self.db, octx, item_id=theirs, qty_delta=5, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("20"))
        CashDrawer(self.db).open(octx, 0)
        s2 = Sales(self.db)
        sid = s2.create(octx, None)
        d = s2.add_line(octx, sid, theirs, units=1)
        f = s2.safety_check(octx, sid)
        done = s2.complete(octx, sid, paid_cash=d["total"], safety_decisions={i: "ok" for i in range(len(f))})
        s2.return_items(octx, done["id"], {done["lines"][0]["id"]: 1}, refund_method="cash", reason="مرتجع")
        self.assertEqual(self.top(), {self.pan: {"qty": 2, "revenue": 4000, "profit": 4000 - 3000}})
        theirs_top = {t["item_id"]: t for t in reports.sales_report(self.db, octx, self.day, self.day)["top_items"]}
        self.assertEqual(set(theirs_top), {theirs})
        self.assertEqual((theirs_top[theirs]["qty"], theirs_top[theirs]["revenue"]), (0, 0))
