"""The cashier hot paths and the owner's daily reports never read a whole growing table (#113 performance).

On synthetic years of data (scripts/perf, docs/PERFORMANCE.md) a full scan of sale_allocations, sales or the
audit log costs nothing on a test database and seconds (minutes, in a loop) after a few years. SQLite plans a
query from the schema alone until statistics exist, so the plan seen here is the plan a fresh pharmacy gets.
Each test runs the real service calls, records every statement they send, and checks its EXPLAIN QUERY PLAN.
"""
import re
import unittest
from datetime import timedelta

from app.catalog import card as item_card
from app.reports import invoices, owner
from app.sales.service import local_today
from tests.test_sales import SalesCase

# tables that grow with every sale, every day, for years
GROWING = {"sales", "sale_lines", "sale_allocations", "sale_payments", "stock_movements", "audit_log", "safety_events",
           "cash_movements", "customer_ledger", "supplier_ledger", "purchase_invoice_lines"}


# index columns that do not narrow a growing table: every row of the pharmacy's history shares them
BROAD = {"org_id", "branch_id", "status", "entity_type", "kind"}


_NOT_ALIAS = {"WHERE", "ON", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "CROSS", "GROUP", "ORDER", "LIMIT",
              "UNION", "USING", "NATURAL", "HAVING", "WINDOW", "SET", "VALUES"}


def _aliases(sql: str) -> dict[str, str]:
    """alias -> table for every ``FROM/JOIN table [AS] alias`` in the statement: SQLite's plan names the alias."""
    out = {}
    for t, a in re.findall(r"\b(?:FROM|JOIN)\s+(\w+)(?:\s+(?:AS\s+)?(\w+))?", sql, re.I):
        out[t] = t
        if a and a.upper() not in _NOT_ALIAS:
            out[a] = t
    return out


def _reads_history(detail: str, aliases: dict[str, str] | None = None) -> bool:
    """True when a plan step reads a growing table whole: a SCAN, an index built on the fly for the query
    (AUTOMATIC ... INDEX: a scan each time), or a SEARCH narrowed only by columns all the history shares."""
    words = detail.split()
    if len(words) < 2 or (aliases or {}).get(words[1], words[1]) not in GROWING:
        return False
    if words[0] == "SCAN" or "AUTOMATIC" in detail:
        return True
    if words[0] == "SEARCH" and "(" in detail:
        cols = {c.split("=")[0].split(">")[0].split("<")[0].strip() for c in detail[detail.index("(") + 1:-1].split(" AND ")}
        return cols <= BROAD
    return False


class TestPlanReader(unittest.TestCase):
    def test_aliases_resolve_to_their_table(self):
        sql = ("SELECT s.id FROM sales s JOIN sale_lines AS l ON l.sale_id=s.id LEFT JOIN stock_movements m ON 1"
               " WHERE s.org_id=?")
        names = _aliases(sql)
        self.assertEqual((names["s"], names["l"], names["m"]), ("sales", "sale_lines", "stock_movements"))
        self.assertTrue(_reads_history("SCAN m", names))
        self.assertTrue(_reads_history("SEARCH s USING INDEX ix_sales_org (org_id=? AND branch_id=? AND status=?)", names))
        self.assertFalse(_reads_history("SEARCH s USING INDEX ix_x (org_id=? AND completed_at>?)", names))
        self.assertFalse(_reads_history("SCAN items", _aliases("SELECT * FROM items WHERE 1")))
        self.assertEqual(_aliases("SELECT * FROM sales WHERE id=?"), {"sales": "sales"})


class QueryRecorder:
    def __init__(self, db):
        self.db, self.sql = db, []

    def __enter__(self):
        self.db.conn.set_trace_callback(self.sql.append)
        return self

    def __exit__(self, *exc):
        self.db.conn.set_trace_callback(None)

    def selects(self):
        return [s for s in self.sql if s.lstrip().upper().startswith(("SELECT", "WITH"))]

    def full_scans(self):
        out = []
        for s in dict.fromkeys(self.selects()):
            names = _aliases(s)
            for row in self.db.conn.execute("EXPLAIN QUERY PLAN " + s).fetchall():
                if _reads_history(row[3], names):
                    out.append((row[3], " ".join(s.split())[:200]))
        return out


class TestNoFullScans(SalesCase):
    def setUp(self):
        super().setUp()
        self.cust = self.s.create_customer(self.ctx, name="عميل اختبار", credit_limit=100000)
        self.sales = []
        for k in range(3):
            sid = self.s.create(self.ctx, self.cust if k == 2 else None)
            d = self.s.add_line(self.ctx, sid, self.pan, units=1)
            self.sales.append(self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid)))
        r = self.s.return_items(self.ctx, self.sales[0]["id"], {self.sales[0]["lines"][0]["id"]: 1},
                                refund_method="cash", reason="اختبار")
        self.return_id = r["return_id"]
        self.today = local_today().isoformat()
        self.month = (local_today() - timedelta(days=30)).isoformat()

    def assertNoScan(self, rec):
        self.maxDiff = None
        self.assertEqual([p for p, _ in rec.full_scans()], [], rec.full_scans())

    def test_lookups_fixed_without_new_indexes(self):
        # the return receipt and the returns report find the return's audit record through the audit index, and an
        # item's movements use the (org, branch, item) index; their other reads need the indexes of 0036
        with QueryRecorder(self.db) as rec:
            item_card.movements(self.db, self.ctx, self.pan, self.month, self.today)
        self.assertNoScan(rec)
        with QueryRecorder(self.db) as rec:
            self.s.return_receipt(self.ctx, self.return_id)
            owner.returns_report(self.db, self.ctx, self.month, self.today)
        self.assertEqual([p for p, q in rec.full_scans() if "audit_log" in q.split(" WHERE")[0]], [], rec.full_scans())

    def test_invoice_report_query_count_does_not_grow_with_invoices(self):
        def count():
            with QueryRecorder(self.db) as rec:
                invoices.report(self.db, self.ctx, date_from=self.month, date_to=self.today, with_lines=True)
            return len(rec.selects())
        before = count()
        for _ in range(6):
            sid = self.s.create(self.ctx)
            d = self.s.add_line(self.ctx, sid, self.pan, units=1)
            self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid))
        self.assertEqual(count(), before)            # was 1 + one per invoice + one per line
