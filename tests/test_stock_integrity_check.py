"""#202 part 2: the stock integrity check recomputes every balance from the stock ledger on startup and after
each backup run; a mismatch raises a red alert (error notification + banner on /api/me) and an audit row, and
is never repaired silently. Damage is simulated with raw SQLite writes, as a damaged file or a hand edit
would be. Synthetic data only."""
import sqlite3
import time
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from starlette.testclient import TestClient

from app import config
from app.api.app import create_app
from app.catalog import service as cat
from app.core import audit, notify, people
from app.core.autobackup import AutoBackup
from app.inventory import integrity, service as inv
from app.sales.cash import CashDrawer
from app.sales.service import Sales
from tests.helpers import DBTestCase
from tests.test_api import H

PW = "Staff-Pass-2026"


class Base(DBTestCase):
    def setUp(self):
        super().setUp()
        self.pan = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab", "pack_size": 2,
                                                       "public_price": 4000})
        self.aug = cat.create_item(self.db, self.ctx, {"name_en": "Augmentin 1g 14 tab", "pack_size": 2,
                                                       "public_price": 9000})
        with self.db.transaction():
            self.b1 = inv.get_or_create_batch(self.db, self.ctx, self.pan, "B1",
                                              (date.today() + timedelta(days=400)).isoformat())
            self.b2 = inv.get_or_create_batch(self.db, self.ctx, self.aug, "A1",
                                              (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=20, batch_id=self.b1,
                              movement_type="purchase", unit_cost=Decimal("15"))
            inv.post_movement(self.db, self.ctx, item_id=self.aug, qty_delta=10, batch_id=self.b2,
                              movement_type="purchase", unit_cost=Decimal("40"))

    def raw(self, sql, params=()):
        c = sqlite3.connect(self.db.path)
        c.execute(sql, params)
        c.commit()
        c.close()

    def stock_tables(self):
        return ([tuple(r) for r in self.db.all("SELECT * FROM stock_balances ORDER BY 1,2,3,4")],
                self.db.one("SELECT count(*) n FROM stock_movements")["n"])

    def alerts(self):
        return [n for n in notify.listing(self.db, self.ctx)["items"] if n["kind"] == "stock_integrity"]

    def audits(self, action):
        return self.db.all("SELECT * FROM audit_log WHERE action=? ORDER BY seq", (action,))

    def sell(self, units):
        s = Sales(self.db)
        sid = s.create(self.ctx)
        d = s.add_line(self.ctx, sid, self.pan, units=units)
        f = s.safety_check(self.ctx, sid)
        return s.complete(self.ctx, sid, paid_cash=0, paid_card=d["total"],
                          safety_decisions={i: "ok" for i in range(len(f))})


class TestScan(Base):
    def test_clean_ledger_records_nothing(self):
        self.sell(3)
        r = integrity.check(self.db, self.ctx.org_id, "startup")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["counts"], {k: 0 for k in integrity.KINDS})
        self.assertEqual(self.audits(integrity.MISMATCH), [])
        self.assertEqual(self.alerts(), [])
        self.assertIsNone(integrity.banner(self.db, self.ctx))

    def test_balance_off_is_alerted_audited_and_not_repaired(self):
        self.raw("UPDATE stock_balances SET qty = qty + 2 WHERE item_id=?", (self.pan,))
        before = self.stock_tables()
        r = integrity.check(self.db, self.ctx.org_id, "startup")
        self.assertFalse(r["ok"])
        self.assertEqual((r["counts"]["batches"], r["counts"]["items"]), (1, 1))
        self.assertEqual(r["batches"][0]["ledger_qty"], 20)
        self.assertEqual(r["batches"][0]["balance_qty"], 22)
        self.assertEqual(self.stock_tables(), before)                     # never repaired
        self.assertEqual(len(self.audits(integrity.MISMATCH)), 1)
        self.assertEqual([a["severity"] for a in self.alerts()], ["error"])
        b = integrity.banner(self.db, self.ctx)
        self.assertEqual(b["counts"]["batches"], 1)
        self.assertEqual(audit.verify_chain(self.db), [])
        # the same mismatch again: a new audit row (each detection is recorded), no second notification
        integrity.check(self.db, self.ctx.org_id, "backup")
        self.assertEqual(len(self.audits(integrity.MISMATCH)), 2)
        self.assertEqual(len(self.alerts()), 1)
        # a different mismatch notifies again
        self.raw("UPDATE stock_balances SET qty = qty - 1 WHERE item_id=?", (self.aug,))
        integrity.check(self.db, self.ctx.org_id, "backup")
        self.assertEqual(len(self.alerts()), 2)

    def test_clean_again_is_audited_and_clears_the_banner(self):
        self.raw("UPDATE stock_balances SET qty = qty + 2 WHERE item_id=?", (self.pan,))
        integrity.check(self.db, self.ctx.org_id, "startup")
        self.raw("UPDATE stock_balances SET qty = qty - 2 WHERE item_id=?", (self.pan,))   # the owner's fix
        r = integrity.check(self.db, self.ctx.org_id, "backup")
        self.assertTrue(r["ok"])
        self.assertEqual(len(self.audits(integrity.CLEAN)), 1)
        self.assertIsNone(integrity.banner(self.db, self.ctx))
        integrity.check(self.db, self.ctx.org_id, "backup")              # clean after clean: nothing more
        self.assertEqual(len(self.audits(integrity.CLEAN)), 1)

    def test_missing_balance_row_and_row_without_ledger(self):
        self.raw("DELETE FROM stock_balances WHERE item_id=?", (self.aug,))
        r = integrity.scan(self.db, self.ctx.org_id)
        self.assertEqual((r["counts"]["batches"], r["counts"]["items"]), (1, 1))
        self.assertIsNone(r["batches"][0]["balance_qty"])

    def test_negative_balance(self):
        mid = "00000000-0000-7000-8000-000000000001"
        self.raw("INSERT INTO stock_movements (id, org_id, branch_id, item_id, batch_id, qty_delta, movement_type)"
                 " VALUES (?,?,?,?,?,?,'adjustment')", (mid, self.ctx.org_id, self.ctx.branch_id, self.pan, self.b1, -25))
        self.raw("UPDATE stock_balances SET qty = qty - 25 WHERE item_id=?", (self.pan,))
        r = integrity.scan(self.db, self.ctx.org_id)
        self.assertEqual(r["counts"]["batches"], 0)                      # ledger and cache agree ...
        self.assertEqual(r["counts"]["negative"], 1)                     # ... but below zero is impossible
        self.assertFalse(r["ok"])

    def test_movement_on_another_items_batch(self):
        mid = "00000000-0000-7000-8000-000000000002"
        self.raw("INSERT INTO stock_movements (id, org_id, branch_id, item_id, batch_id, qty_delta, movement_type)"
                 " VALUES (?,?,?,?,?,?,'adjustment')", (mid, self.ctx.org_id, self.ctx.branch_id, self.aug, self.b1, 1))
        self.raw("INSERT INTO stock_balances (org_id, branch_id, item_id, batch_key, qty) VALUES (?,?,?,?,1)",
                 (self.ctx.org_id, self.ctx.branch_id, self.aug, self.b1))
        r = integrity.scan(self.db, self.ctx.org_id)
        self.assertEqual((r["counts"]["batches"], r["counts"]["foreign_batch"]), (0, 1))

    def test_sale_that_moved_other_than_its_invoice(self):
        a = self.sell(4)
        self.sell(2)
        r = integrity.scan(self.db, self.ctx.org_id)
        self.assertTrue(r["ok"], r)
        mid = "00000000-0000-7000-8000-000000000003"     # one unit more left the shelf than the invoice says
        self.raw("INSERT INTO stock_movements (id, org_id, branch_id, item_id, batch_id, qty_delta, movement_type,"
                 " ref_type, ref_id) VALUES (?,?,?,?,?,-1,'sale','sale',?)",
                 (mid, self.ctx.org_id, self.ctx.branch_id, self.pan, self.b1, a["id"]))
        self.raw("UPDATE stock_balances SET qty = qty - 1 WHERE item_id=?", (self.pan,))
        r = integrity.scan(self.db, self.ctx.org_id)
        self.assertEqual((r["counts"]["batches"], r["counts"]["sales"]), (0, 1))
        self.assertEqual((r["sales"][0]["sale_id"], r["sales"][0]["moved"], r["sales"][0]["expected"]),
                         (a["id"], -5, -4))

    def test_invoice_line_quantity_is_the_reference(self):
        a = self.sell(5)
        self.raw("UPDATE sale_lines SET qty_base = 6 WHERE sale_id=?", (a["id"],))   # invoice says 6, shelf gave 5
        r = integrity.scan(self.db, self.ctx.org_id)
        self.assertEqual(r["counts"]["sales"], 1)
        self.assertEqual((r["sales"][0]["moved"], r["sales"][0]["expected"]), (-5, -6))

    def test_returns_and_voids_are_not_mismatches(self):
        s = Sales(self.db)
        a = self.sell(6)
        s.return_items(self.ctx, a["id"], {a["lines"][0]["id"]: 2}, refund_method="card", reason="مرتجع")
        b = self.sell(2)
        s.void(self.ctx, b["id"], "خطأ في الفاتورة")
        self.assertTrue(integrity.scan(self.db, self.ctx.org_id)["ok"])

    def test_other_pharmacy_is_separate(self):
        org, other = self.second_tenant()
        self.raw("UPDATE stock_balances SET qty = qty + 2 WHERE item_id=?", (self.pan,))
        res = integrity.check_all(self.db, "startup")
        self.assertFalse(res[self.ctx.org_id]["ok"])
        self.assertTrue(res[other.org_id]["ok"])
        self.assertIsNone(integrity.banner(self.db, other))

    def test_banner_only_for_who_reads_the_audit(self):
        self.raw("UPDATE stock_balances SET qty = qty + 2 WHERE item_id=?", (self.pan,))
        integrity.check(self.db, self.ctx.org_id, "startup")
        people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        self.assertIsNone(integrity.banner(self.db, self.login("asst", PW)))
        self.assertIsNotNone(integrity.banner(self.db, self.ctx))

    def test_an_older_clean_scan_never_clears_a_newer_alert(self):
        import threading
        real, scanned, go = integrity.scan, threading.Event(), threading.Event()

        def slow(db, org_id):
            r = real(db, org_id)
            if threading.current_thread().name == "older":
                scanned.set()
                go.wait(5)                    # paused after a clean scan, before recording
            return r
        with patch.object(integrity, "scan", side_effect=slow):
            a = threading.Thread(target=integrity.check, args=(self.db, self.ctx.org_id, "startup"), name="older")
            a.start()
            self.assertTrue(scanned.wait(5))
            self.raw("UPDATE stock_balances SET qty = qty + 2 WHERE item_id=?", (self.pan,))
            b = threading.Thread(target=integrity.check, args=(self.db, self.ctx.org_id, "backup"), name="newer")
            b.start()
            time.sleep(0.3)                   # without the lock the newer check records its alert here
            go.set()
            a.join(10)
            b.join(10)
        self.assertIsNotNone(integrity.banner(self.db, self.ctx))
        self.assertEqual(self.audits(integrity.CLEAN), [])

    def test_a_failing_check_never_raises(self):
        with patch.object(integrity, "scan", side_effect=RuntimeError("boom")):
            self.assertEqual(integrity.check_all(self.db, "startup"), {})


class TestScale(Base):
    """Codex P2 on #209: the sales check once ran a subquery per invoice line over all allocations (no index on
    sale_line_id), quadratic in the history. It must stay one pass per table."""

    def bulk(self, n):
        c = sqlite3.connect(self.db.path)
        c.execute("PRAGMA foreign_keys = OFF")
        org, br, uid = self.ctx.org_id, self.ctx.branch_id, self.ctx.user_id
        c.executemany("INSERT INTO sales (id, org_id, branch_id, created_by, status, sale_no) VALUES (?,?,?,?,'completed',?)",
                      [(f"s{i}", org, br, uid, 10_000 + i) for i in range(n)])
        c.executemany("INSERT INTO sale_lines (id, sale_id, org_id, line_no, item_id, qty_base) VALUES (?,?,?,1,?,1)",
                      [(f"l{i}", f"s{i}", org, self.pan) for i in range(n)])
        c.executemany("INSERT INTO sale_allocations (id, sale_line_id, batch_id, qty_base, unit_price) VALUES (?,?,?,1,2000)",
                      [(f"a{i}", f"l{i}", self.b1) for i in range(n)])
        c.executemany("INSERT INTO stock_movements (id, org_id, branch_id, item_id, batch_id, qty_delta, movement_type,"
                      " ref_type, ref_id, ref_line_id) VALUES (?,?,?,?,?,-1,'sale','sale',?,?)",
                      [(f"m{i}", org, br, self.pan, self.b1, f"s{i}", f"l{i}") for i in range(n)])
        c.execute("INSERT INTO stock_movements (id, org_id, branch_id, item_id, batch_id, qty_delta, movement_type)"
                  " VALUES ('m-in',?,?,?,?,?,'purchase')", (org, br, self.pan, self.b1, n))   # stock for every sale
        c.commit()
        c.close()

    def test_sales_check_has_no_per_line_subquery(self):
        sql = []
        self.db.conn.set_trace_callback(sql.append)
        try:
            integrity._sales(self.db, self.ctx.org_id)
        finally:
            self.db.conn.set_trace_callback(None)
        plan = [r[3] for q in sql if q.lstrip().upper().startswith("WITH")
                for r in self.db.conn.execute("EXPLAIN QUERY PLAN " + q).fetchall()]
        self.assertTrue(plan)
        self.assertEqual([d for d in plan if "CORRELATED" in d], [], plan)

    def test_large_history_is_checked_quickly(self):
        self.bulk(30_000)                       # ~ years of one-line sales; the old query took minutes here
        t = time.perf_counter()
        r = integrity.scan(self.db, self.ctx.org_id)
        took = time.perf_counter() - t
        self.assertTrue(r["ok"], r["counts"])
        self.assertLess(took, 20, f"scan took {took:.1f} s")


class TestWhenItRuns(Base):
    def test_after_every_backup_run_even_a_refused_one(self):
        ab = AutoBackup(self.db, self.tmp / "uploads", self.tmp / "backups", self.tmp / "state")
        st = ab.run()
        self.assertTrue(st["last_ok"])
        self.assertEqual(self.audits(integrity.MISMATCH), [])
        self.raw("UPDATE stock_balances SET qty = qty + 2 WHERE item_id=?", (self.pan,))
        st = ab.run(force=True)
        self.assertFalse(st["last_ok"])                                  # backup.create refuses a mismatched db
        self.assertEqual(len(self.audits(integrity.MISMATCH)), 1)
        self.assertEqual(len(self.alerts()), 1)
        self.assertIn("backup", self.audits(integrity.MISMATCH)[0]["after_json"])

    def test_also_when_the_backup_is_skipped_for_no_changes(self):
        ab = AutoBackup(self.db, self.tmp / "uploads", self.tmp / "backups", self.tmp / "state")
        self.assertTrue(ab.run()["last_ok"])
        self.raw("UPDATE stock_balances SET qty = qty + 2 WHERE item_id=?", (self.pan,))   # outside the program
        st = ab.run()
        self.assertEqual(st.get("skipped"), "no_changes")          # the fingerprint did not see it ...
        self.assertEqual(len(self.audits(integrity.MISMATCH)), 1)  # ... the stock check did
        self.assertEqual(len(self.alerts()), 1)

    def test_on_startup_and_the_banner_in_me(self):
        self.raw("UPDATE stock_balances SET qty = qty + 2 WHERE item_id=?", (self.pan,))
        s = replace(config.load(), upload_dir=self.tmp / "up", anthropic_api_key=None, data_dir=self.tmp,
                    backup_dir=self.tmp / "backups")
        with patch.object(AutoBackup, "start"), patch("app.core.daily.DailyChecks.start"), \
                patch("app.core.daily.DailyChecks.stop"):
            app = create_app(self.db, s, auto_backup=True)
            with TestClient(app) as c:
                for _ in range(100):                                     # the check runs in the background
                    if self.audits(integrity.MISMATCH):
                        break
                    time.sleep(0.05)
                self.assertEqual(len(self.audits(integrity.MISMATCH)), 1)
                self.assertIn("startup", self.audits(integrity.MISMATCH)[0]["after_json"])
                r = c.post("/api/auth/login", json={"username": "owner", "password": "Owner-Pass-2026"}, headers=H)
                self.assertEqual(r.status_code, 200, r.text)
                me = c.get("/api/me", headers=H).json()
                self.assertEqual(me["stock_alert"]["counts"]["batches"], 1)
                # the login answer has no banner, so the screens ask for it on their own (Codex P2 on #209)
                self.assertNotIn("stock_alert", r.json().get("me", {}))
                self.assertEqual(c.get("/api/stock/alert", headers=H).json()["alert"]["counts"]["batches"], 1)
            people.add_user(self.db, self.ctx, username="asst2", full_name="A", password=PW, role_code="assistant")
            with TestClient(create_app(self.db, s)) as c2:
                c2.post("/api/auth/login", json={"username": "asst2", "password": PW}, headers=H)
                self.assertEqual(c2.get("/api/stock/alert", headers=H).status_code, 403)
                self.assertEqual(TestClient(create_app(self.db, s)).get("/api/stock/alert", headers=H).status_code, 401)


if __name__ == "__main__":
    import unittest
    unittest.main()
