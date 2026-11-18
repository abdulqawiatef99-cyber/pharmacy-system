"""The load generator behind docs/PERFORMANCE.md stays runnable and its data stays valid (#113 performance).

Only a tiny run here (2 days, 40 items): the real 1/3/5-year runs and the benchmarks live in scripts/perf and
never run in CI. The generator moves the clock process-wide, so it runs in its own process.
"""
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.core import backup
from app.db import Database
from app.inventory import integrity

ROOT = Path(__file__).resolve().parents[1]


class TestLoadGenerator(unittest.TestCase):
    def test_tiny_run_is_consistent_and_dated(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "gen"
            r = subprocess.run([sys.executable, str(ROOT / "scripts/perf/loadgen.py"), "--out", str(out), "--days", "2",
                                "--items", "40", "--customers", "10", "--suppliers", "3", "--per-day", "6-8",
                                "--transfer-share", "0.3", "--attachment-kb", "2", "--end", "2026-01-15", "--quiet"],
                               capture_output=True, text=True, timeout=300, cwd=str(ROOT))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            info = json.loads((out / "loadgen.json").read_text())
            data = out / "final" / "data"
            db = Database(data / "pharmacy.db")
            try:
                self.assertEqual(backup.integrity_problems(db), [])          # what `manage.py verify` checks
                n = db.one("SELECT count(*) n, count(DISTINCT substr(completed_at,1,10)) d FROM sales"
                           " WHERE status='completed'")
                self.assertGreaterEqual(n["n"], 10)
                self.assertEqual(n["d"], 2)                                    # spread over the simulated days
                self.assertEqual(db.one("SELECT min(substr(completed_at,1,10)) d FROM sales WHERE status='completed'")["d"],
                                 "2026-01-14")
                self.assertGreater(db.one("SELECT count(*) n FROM cash_shifts WHERE status='closed'")["n"], 0)
                self.assertGreater(db.one("SELECT count(*) n FROM supplier_ledger")["n"], 0)
            finally:
                db.close()
            c = sqlite3.connect(data / "pharmacy.db")
            try:   # the generator's speed-up index never reaches a snapshot: snapshots have the program's schema
                self.assertEqual(c.execute("SELECT count(*) FROM sqlite_master WHERE name LIKE 'perf_%'").fetchone()[0], 0)
            finally:
                c.close()
            self.assertEqual(info["snapshots"][-1]["label"], "final")

    def test_credit_sale_over_the_limit_is_paid_in_cash(self):
        # a credit sale over the remaining limit is refused by the program; the generator must not abort on it
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "gen"
            r = subprocess.run([sys.executable, str(ROOT / "scripts/perf/loadgen.py"), "--out", str(out), "--days", "2",
                                "--items", "40", "--customers", "2", "--suppliers", "2", "--per-day", "40-40",
                                "--credit-limit", "1", "--attachment-kb", "0", "--end", "2026-01-15", "--quiet"],
                               capture_output=True, text=True, timeout=300, cwd=str(ROOT))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            db = Database(out / "final" / "data" / "pharmacy.db")
            try:
                self.assertEqual(db.one("SELECT count(*) n FROM sales WHERE on_credit>0")["n"], 0)
                self.assertGreaterEqual(db.one("SELECT count(*) n FROM sales WHERE status='completed'")["n"], 40)
            finally:
                db.close()

    def test_remembered_name_matches_give_the_same_data(self):
        # the generator remembers fuzzy name matches (loadgen.install_fast_matching) and adds generator-only indexes
        # (loadgen.GEN_INDEXES) to be fast enough for large pharmacies; the data must be what the program gives
        # without them: same seed, both ways (--plain), compared
        with tempfile.TemporaryDirectory() as tmp:
            got = {}
            for mode, extra in (("fast", []), ("exact", ["--plain"])):
                out = Path(tmp) / mode
                r = subprocess.run([sys.executable, str(ROOT / "scripts/perf/loadgen.py"), "--out", str(out), "--days", "18",
                                    "--items", "150", "--customers", "10", "--suppliers", "3", "--per-day", "30-30",
                                    "--attachment-kb", "0", "--end", "2026-01-15", "--quiet", *extra],
                                   capture_output=True, text=True, timeout=600, cwd=str(ROOT))
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                info = json.loads((out / "loadgen.json").read_text())
                db = Database(out / "final" / "data" / "pharmacy.db")
                try:
                    org = db.one("SELECT id FROM organizations")["id"]
                    scan = integrity.scan(db, org)                     # the owner's stock check: every balance matches
                    self.assertTrue(scan["ok"], scan["counts"])
                    self.assertEqual(backup.integrity_problems(db), [])
                    tables = [r["name"] for r in db.all("SELECT name FROM sqlite_master WHERE type='table'"
                                                        " AND name NOT LIKE 'sqlite_%' ORDER BY name")]

                    def rows(sql):
                        return [tuple(r) for r in db.all(sql)]
                    got[mode] = {
                        "stats": info["snapshots"][-1]["stats"], "finding_codes": info["finding_codes"],
                        "counts": {t: db.one(f"SELECT count(*) n FROM {t}")["n"] for t in tables},
                        "sales": rows("SELECT sale_no, status, completed_at, subtotal, discount_total, total, paid_cash,"
                                      " paid_card, paid_transfer, on_credit FROM sales ORDER BY created_at, sale_no"),
                        "sale_lines": rows("SELECT s.sale_no, l.line_no, i.name_en, l.qty_base, l.line_total FROM sale_lines l"
                                           " JOIN sales s ON s.id=l.sale_id JOIN items i ON i.id=l.item_id"
                                           " ORDER BY s.created_at, s.sale_no, l.line_no"),
                        "stock": rows("SELECT i.name_en, sum(b.qty) FROM stock_balances b JOIN items i ON i.id=b.item_id"
                                      " GROUP BY 1 ORDER BY 1"),
                        "movements": rows("SELECT i.name_en, m.movement_type, count(*), sum(m.qty_delta) FROM stock_movements m"
                                          " JOIN items i ON i.id=m.item_id GROUP BY 1, 2 ORDER BY 1, 2"),
                        "purchase_lines": rows("SELECT raw_name, i.name_en, match_method, match_score, match_status, qty,"
                                               " line_total FROM purchase_invoice_lines l LEFT JOIN items i ON i.id=l.item_id"
                                               " ORDER BY l.raw_name, l.qty, l.line_total"),
                        "safety_events": rows("SELECT finding_code, severity, decision, message, count(*) FROM safety_events"
                                              " GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4"),
                        "cash": rows("SELECT kind, count(*), sum(amount) FROM cash_movements GROUP BY 1 ORDER BY 1"),
                    }
                finally:
                    db.close()
            self.assertIn("lookalike", got["exact"]["finding_codes"])     # the remembered matches were really used
            # 18 days: some items were bought again; that line is matched by alias (the remembered own-name score)
            self.assertIn("alias", {r[2] for r in got["exact"]["purchase_lines"]})
            self.assertGreater(got["exact"]["stats"]["sales"], 40)
            for key in got["exact"]:
                self.assertEqual(got["fast"][key], got["exact"][key], key)


if __name__ == "__main__":
    unittest.main()
