"""scripts/perf/scale.py (#253): a synthetic pharmacy from an empty database, measured at each snapshot.

A tiny run: every snapshot's stock check is clean, everything is measured, the work folder is gone.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestScale(unittest.TestCase):
    def test_tiny_run_measures_every_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            work = tmp / "work"
            work.mkdir()
            out = tmp / "scale.txt"
            sql = tmp / "proposed.sql"       # a second pass on every snapshot with a proposed index
            sql.write_text("CREATE INDEX IF NOT EXISTS ix_test_alloc_line ON sale_allocations(sale_line_id);\n")
            r = subprocess.run([sys.executable, str(ROOT / "scripts/perf/scale.py"), "--profile", "", "--days", "2,3",
                                "--items", "60", "--customers", "4", "--suppliers", "2", "--per-day", "8-12",
                                "--transfer-share", "0.3", "--attachment-kb", "2", "--repeat", "1", "--end", "2026-01-15",
                                "--out-txt", str(out), "--work", str(work), "--turns", str(tmp / "turns"),
                                "--indexes-sql", str(sql)],
                               capture_output=True, text=True, timeout=600, cwd=str(ROOT))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(list(work.iterdir()), [])
            self.assertEqual(list((tmp / "turns").iterdir()), [])
            data = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "finished")
            self.assertEqual([s["label"] for s in data["snapshots"]],
                             ["custom - 2 days", "custom - 2 days + indexes", "custom - 3 days", "custom - 3 days + indexes"])
            for s in data["snapshots"]:
                self.assertNotIn("error", s)
                self.assertTrue(s["stock_check"]["ok"], s["stock_check"])
                self.assertGreater(s["stock_check"]["movements"], 60)
                if not s["label"].endswith("indexes"):
                    self.assertEqual(s["backup"]["verify_problems"], [])
                self.assertIn("complete sale", s["cashier"])
                for name in ("unsold items (all years)", "order suggestions", "stock check (run now)",
                             "transfers (month)", "expenses (all years)"):
                    self.assertEqual(s["requests"][name]["status"], 200, name)
            self.assertEqual([l.split(": ")[1] for l in data["summary"]], ["ALL PASS"] * 4)
            self.assertIn("stock check: all balances match", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
