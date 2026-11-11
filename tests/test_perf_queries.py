"""The cashier hot paths and the owner's daily reports never read a whole growing table (#113 performance).

docs/PERFORMANCE.md measured these on synthetic 1/3/5-year data: a full scan of sale_allocations, sales or the
audit log costs nothing on a test database and seconds (minutes, in a loop) after a few years. SQLite plans a
query from the schema alone until statistics exist, so the plan seen here is the plan a fresh pharmacy gets.
Each test runs the real service calls, records every statement they send, and checks its EXPLAIN QUERY PLAN.
"""
from datetime import timedelta

from app.catalog import card as item_card
from app.reports import invoices, owner
from app.reports import service as reports
from app.sales import customers as cu
from app.sales.cash import CashDrawer
from app.sales.service import local_today
from tests.test_sales import SalesCase

# tables that grow with every sale, every day, for years
GROWING = {"sales", "sale_lines", "sale_allocations", "sale_payments", "stock_movements", "audit_log", "safety_events",
           "cash_movements", "customer_ledger", "supplier_ledger", "purchase_invoice_lines"}


# index columns that do not narrow a growing table: every row of the pharmacy's history shares them
BROAD = {"org_id", "branch_id", "status", "entity_type", "kind"}


def _reads_history(detail: str) -> bool:
    """True when a plan step reads a growing table whole: a SCAN, an index built on the fly for the query
    (AUTOMATIC ... INDEX: a scan each time), or a SEARCH narrowed only by columns all the history shares."""
    words = detail.split()
    if len(words) < 2 or words[1] not in GROWING:
        return False
    if words[0] == "SCAN" or "AUTOMATIC" in detail:
        return True
    if words[0] == "SEARCH" and "(" in detail:
        cols = {c.split("=")[0].split(">")[0].split("<")[0].strip() for c in detail[detail.index("(") + 1:-1].split(" AND ")}
        return cols <= BROAD
    return False


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
            for row in self.db.conn.execute("EXPLAIN QUERY PLAN " + s).fetchall():
                if _reads_history(row[3]):
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

    def test_cashier_flow(self):
        with QueryRecorder(self.db) as rec:
            sid = self.s.create(self.ctx)
            d = self.s.add_line(self.ctx, sid, self.pan, units=1)
            done = self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid))
            self.s.get(self.ctx, done["id"])                                   # receipt / reprint
            self.s.return_items(self.ctx, done["id"], {done["lines"][0]["id"]: 1}, refund_method="cash", reason="اختبار")
            self.s.return_receipt(self.ctx, self.return_id)
            CashDrawer(self.db).summary(self.ctx)                              # the cashier's drawer panel
        self.assertNoScan(rec)

    def test_daily_lists_and_reports(self):
        drawer = CashDrawer(self.db)
        shift = self.db.one("SELECT id FROM cash_shifts WHERE status='open'")["id"]
        with QueryRecorder(self.db) as rec:
            self.s.list_sales(self.ctx, day=self.today)
            self.s.day_summary(self.ctx, self.today)
            invoices.report(self.db, self.ctx, date_from=self.month, date_to=self.today)
            reports.sales_report(self.db, self.ctx, self.month, self.today)
            reports.home(self.db, self.ctx)
            owner.returns_report(self.db, self.ctx, self.month, self.today)
            owner.discounts_report(self.db, self.ctx, self.month, self.today)
            drawer.report(self.ctx, shift)
            item_card.card(self.db, self.ctx, self.pan)
            item_card.movements(self.db, self.ctx, self.pan, self.month, self.today)
            cu.profile(self.db, self.ctx, self.cust)
        self.assertNoScan(rec)

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
