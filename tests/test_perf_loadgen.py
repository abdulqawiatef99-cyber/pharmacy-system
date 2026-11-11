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


if __name__ == "__main__":
    unittest.main()
