"""scripts/perf/real_years.py (#253) runs on a copy and writes timings only.

A tiny run on a synthetic "live" database made by loadgen.py: the live file must not change, the work folder
must be gone afterwards, and the results must hold no names, barcodes or user names from the database.
"""
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class TestRealYears(unittest.TestCase):
    def test_copy_only_and_timings_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            r = subprocess.run([sys.executable, str(ROOT / "scripts/perf/loadgen.py"), "--out", str(tmp / "fake"),
                                "--days", "2", "--items", "50", "--customers", "4", "--suppliers", "2",   # with transfer screenshots:
                                "--per-day", "6-8", "--transfer-share", "0.5", "--attachment-kb", "2",     # restore needs them
                                "--end", "2026-01-15", "--quiet"],
                               capture_output=True, text=True, timeout=300, cwd=str(ROOT))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            live = tmp / "fake" / "final" / "data"
            self.assertTrue(any((live / "uploads").rglob("*.png")))
            before = sha(live / "pharmacy.db")
            work = tmp / "work"
            work.mkdir()
            out = tmp / "PHARMACY-TEST" / "real-years-results.txt"
            r = subprocess.run([sys.executable, str(ROOT / "scripts/perf/real_years.py"), "--live-data", str(live),
                                "--out-txt", str(out), "--work", str(work), "--days", "2,3", "--items", "40",
                                "--customers", "4", "--suppliers", "2", "--per-day", "6-8", "--attachment-kb", "0", "--profiles", "",
                                "--repeat", "1"], capture_output=True, text=True, timeout=600, cwd=str(ROOT))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(sha(live / "pharmacy.db"), before)            # the live database is only read
            self.assertEqual(list(work.iterdir()), [])                      # the copy is deleted
            text = out.read_text(encoding="utf-8")
            data = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "finished")
            self.assertEqual([s["label"] for s in data["snapshots"]], ["custom - 2 days", "custom - 3 days"])
            for s in data["snapshots"]:
                self.assertNotIn("error", s)
                self.assertEqual(s["requests"]["sales report (all years)"]["status"], 200)
                self.assertIn("complete sale", s["cashier"])
                self.assertEqual(s["purchase"]["approve"]["status"], 200)       # a typed purchase invoice, approved
                self.assertEqual(s["backup"]["verify_problems"], [])
            self.assertIn("SUMMARY", text)
            self.assertEqual([l.split(": ")[1] for l in data["summary"]], ["ALL PASS", "ALL PASS"])
            c = sqlite3.connect(live / "pharmacy.db")
            try:
                secrets = [r[0] for r in c.execute("SELECT name_en FROM items UNION SELECT name_ar FROM items"
                                                   " UNION SELECT barcode FROM item_barcodes UNION SELECT username FROM users"
                                                   " UNION SELECT name FROM suppliers UNION SELECT name FROM organizations")]
            finally:
                c.close()
            both = text + json.dumps(data, ensure_ascii=False)
            for s in secrets:
                if s and len(s) >= 4:
                    self.assertNotIn(s, both)

    def test_sizes_run_in_order_each_on_its_own_copy(self):
        # two pharmacy sizes in one process: the second must get its own copy and a clock reset to today
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            r = subprocess.run([sys.executable, str(ROOT / "scripts/perf/loadgen.py"), "--out", str(tmp / "fake"),
                                "--days", "1", "--items", "40", "--customers", "3", "--suppliers", "2",
                                "--per-day", "5-6", "--attachment-kb", "0", "--quiet"],
                               capture_output=True, text=True, timeout=300, cwd=str(ROOT))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            out = tmp / "r.txt"
            r = subprocess.run([sys.executable, str(ROOT / "scripts/perf/real_years.py"), "--live-data",
                                str(tmp / "fake" / "final" / "data"), "--out-txt", str(out), "--work", str(tmp),
                                "--profiles", "small,medium", "--days", "1", "--repeat", "1", "--attachment-kb", "0"],
                               capture_output=True, text=True, timeout=900, cwd=str(ROOT))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            data = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual([s["label"] for s in data["snapshots"]], ["small - 1 days", "medium - 1 days"])
            for s in data["snapshots"]:
                self.assertNotIn("error", s)
                self.assertEqual(s["days"], 1)
                self.assertGreater(s["stats"]["sales"], 0)
            self.assertLess(data["snapshots"][0]["stats"]["sales"], data["snapshots"][1]["stats"]["sales"])
            self.assertEqual(sorted(p.name for p in tmp.iterdir()), ["fake", "r.json", "r.txt"])   # copies deleted


class TestVerdictAndErrors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "scripts" / "perf"))
        import real_years
        cls.ry = real_years

    def good(self):
        ok = {"median_ms": 1, "max_ms": 2}
        return {"label": "x", "cashier": {"add line": ok}, "requests": {"day report": {**ok, "status": 200}},
                "during_backup": {"status": 200, "scans": 3, "add_line_max_ms": 5},
                "during_report": {"status": 200, "scans": 3, "add_line_max_ms": 5},
                "backup": {"verify_problems": [], "quick_check_problems": []}}

    def test_only_a_complete_clean_measurement_passes(self):
        self.assertEqual(self.ry.summary([self.good()]), ["x: ALL PASS"])
        broken = []
        s = self.good(); s["cashier"]["errors"] = 2; broken.append(s)                  # refused cashier steps
        s = self.good(); s["during_backup"]["status"] = 500; broken.append(s)           # backup failed while scanning
        s = self.good(); s["error"] = "RuntimeError at bench.py:1"; broken.append(s)    # measurement stopped midway
        s = self.good(); del s["cashier"]; broken.append(s)                             # never measured
        s = self.good(); s["requests"]["day report"]["status"] = 500; broken.append(s)
        s = self.good(); s["backup"]["verify_problems"] = ["x"]; broken.append(s)
        for b in broken:
            self.assertTrue(self.ry.summary([b])[0].startswith("x: INCOMPLETE"), b)

    def test_errors_keep_no_message_text(self):
        try:
            raise RuntimeError("the live database failed verification: صيدلية حقيقية: 1 stock balance differences")
        except RuntimeError as e:
            out = self.ry.safe_error(e)
        self.assertTrue(out.startswith("RuntimeError at "), out)
        self.assertNotIn("صيدلية", out)
        self.assertEqual(self.ry.safe_error(SystemExit("no active pharmacy in the database")),
                         "SystemExit: no active pharmacy in the database")


if __name__ == "__main__":
    unittest.main()
