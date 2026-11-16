"""#249: on the pharmacy PC, after the E-Stock import (30k items), the stock report took 118 s and the
whole program stopped answering. Two causes: planner statistics gathered while the database was
small (PRAGMA optimize) made SQLite scan whole tables, and the report ran on the server's event loop."""
import asyncio
import sqlite3

from app.core import retention
from app.db import Database
from app.reports import service as rep_svc
from tests.test_api import APICase, H

VALUE_SQL = ("SELECT coalesce(c.name,'x'), i.id, sb.qty, ic.avg_cost FROM stock_balances sb"
             " JOIN items i ON i.id=sb.item_id LEFT JOIN categories c ON c.id=i.category_id"
             " LEFT JOIN item_costs ic ON ic.item_id=i.id AND ic.org_id=sb.org_id AND ic.branch_id=sb.branch_id"
             " WHERE sb.org_id=? AND sb.branch_id=? AND sb.qty>0")


def plan(path) -> str:
    c = sqlite3.connect(str(path))
    try:
        return " | ".join(r[3] for r in c.execute("EXPLAIN QUERY PLAN " + VALUE_SQL, ("o", "b")))
    finally:
        c.close()


class TestStalePlannerStats(APICase):
    def _stale_stats(self):
        """What the PC had: statistics saying items and item_costs are tiny, and balances many."""
        c = sqlite3.connect(self.db.path)
        c.execute("ANALYZE")
        c.execute("DELETE FROM sqlite_stat1")
        c.executemany("INSERT INTO sqlite_stat1 VALUES (?,?,?)", [
            ("items", "sqlite_autoindex_items_1", "2 1"),
            ("item_costs", "sqlite_autoindex_item_costs_1", "1 1 1 1"),
            ("stock_balances", "sqlite_autoindex_stock_balances_1", "3000 3000 3000 1 1"),
            ("categories", "sqlite_autoindex_categories_1", "7 1"),
        ])
        c.commit()
        c.close()

    def test_stale_statistics_are_dropped_at_start_and_the_report_uses_the_keys(self):
        self._stale_stats()
        self.assertIn("SCAN i", plan(self.db.path))            # the 118-second plan
        fresh = Database(self.db.path)
        fresh.migrate()                                         # what every program start runs
        fresh.close()
        p = plan(self.db.path)
        self.assertNotIn("SCAN i", p)
        self.assertIn("SEARCH i USING", p)
        self.assertIn("SEARCH ic USING", p)
        c = sqlite3.connect(self.db.path)
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE name LIKE 'sqlite_stat%'")}
        c.close()
        self.assertEqual(names, set())

    def test_an_open_connection_stops_using_the_dropped_statistics(self):
        self._stale_stats()
        other = Database(self.db.path)
        other.one("SELECT 1")                                   # opened while the stale statistics exist
        self.assertTrue(self.db.drop_planner_stats())
        detail = " | ".join(r[3] for r in other.all("EXPLAIN QUERY PLAN " + VALUE_SQL, ("o", "b")))
        self.assertIn("SEARCH i USING", detail)
        self.assertFalse(self.db.drop_planner_stats())          # nothing left to drop
        other.close()

    def test_the_daily_clean_never_creates_statistics(self):
        self.db.drop_planner_stats()
        retention.clean(self.db)
        self.assertIsNone(self.db.one("SELECT name FROM sqlite_master WHERE name='sqlite_stat1'"))


class TestReportsOffTheEventLoop(APICase):
    def test_reports_run_in_a_worker_thread_inside_one_read_snapshot(self):
        seen = {}
        real = rep_svc.stock_report

        def spy(db, ctx, *a, **kw):
            try:
                asyncio.get_running_loop()
                seen["on_loop"] = True
            except RuntimeError:
                seen["on_loop"] = False
            seen["snapshot"] = db.conn.in_transaction
            return real(db, ctx, *a, **kw)

        rep_svc.stock_report = spy
        try:
            self.http_login()
            r = self.c.get("/api/reports/stock", headers=H)
        finally:
            rep_svc.stock_report = real
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(seen["on_loop"])      # the event loop (cashier, /api/health) stays free
        self.assertTrue(seen["snapshot"])      # cost and retail come from the same moment
        self.assertFalse(self.db.conn.in_transaction)

    def test_every_report_endpoint_still_answers(self):
        self.http_login()
        d = "2026-01-01"
        for path in ("/api/home", f"/api/reports/sales?from={d}&to={d}", "/api/reports/stock",
                     f"/api/reports/profit?from={d}&to={d}", f"/api/reports/transfers?from={d}&to={d}",
                     f"/api/reports/item-warnings?from={d}&to={d}", f"/api/reports/invoices?from={d}&to={d}",
                     f"/api/reports/losses?from={d}&to={d}"):
            r = self.c.get(path, headers=H)
            self.assertEqual(r.status_code, 200, (path, r.text[:200]))

    def test_no_writes_inside_a_read_snapshot(self):
        with self.db.read_snapshot():
            with self.assertRaises(RuntimeError):
                with self.db.transaction():
                    pass
        with self.db.transaction():            # and writes work again after it
            pass
