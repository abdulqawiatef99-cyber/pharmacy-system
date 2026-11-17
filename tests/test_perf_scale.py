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


TURNS_WORKER = r"""
import os, sys, time
sys.path.insert(0, sys.argv[1])
import scale
real_sleep = time.sleep
scale.time.sleep = lambda s: real_sleep(0.0005)          # the waits of Turns, shortened
folder, rounds = sys.argv[2], int(sys.argv[3])
t = scale.Turns(folder)
inside = os.path.join(folder, "inside")
while not os.path.exists(os.path.join(folder, "go")):     # all workers start their first attempt together
    real_sleep(0.0005)
overlaps = 0
for _ in range(rounds):
    t.measure(True)
    try:
        os.close(os.open(inside, os.O_CREAT | os.O_EXCL | os.O_WRONLY))   # fails when another one is inside too
        real_sleep(0.0005)
        os.unlink(inside)
    except FileExistsError:
        overlaps += 1
    t.measure(False)
print(overlaps)
"""

RESTART_SCRIPT = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import bench, real_years as ry
tmp = Path(sys.argv[2])
data = tmp / "snap" / "data"
data.mkdir(parents=True)
(data / "pharmacy.db").write_bytes(b"")
servers, asked = [], []

class FakeServer:
    def __init__(self, data, at, env_extra=None):
        self.base, self.ready_s, self.stopped = f"fake-{len(servers)}", 0.1, False
        servers.append(self)
    def stop(self):
        self.stopped = True

class FakeClient:
    def __init__(self, base):
        self.base = base
    def login(self, u, p):
        return 200, {}, 1.0, 0
    def req(self, method, path, body=None, timeout=600):
        return 200, {}, 1.0, 0

def timed(client, method, path, body=None, n=5, timeout=600):
    asked.append((path.split("?")[0], client.base))
    gone = "unsold" in path and "from=2026-01-01" in path           # the month report never answers
    return {"median_ms": 1.0, "max_ms": 1.0, "status": "TimeoutError" if gone else 200, "bytes": 0}

bench.Server, bench.Client, bench.timed = FakeServer, FakeClient, timed
bench.facts = lambda p: {"day": "2026-01-15", "end_local": "2026-01-15T20:00", "top_item": "i", "supplier": "s",
                         "stocked": [{"id": "x", "name_en": "Abcd 5mg Tab", "barcode": "2990000000001", "public_price": 100}],
                         "customer": "c", "shift": "h", "counts": {}, "largest_tables": {}}
rec = {"label": "fake"}
ry.measure(data, "owner", ry.date(2025, 12, 1), 1, rec, ry.Report(tmp / "r.txt"), backup=False)
unsold = [(p, b) for p, b in asked if "unsold" in p]
after = asked[asked.index(unsold[0]) + 1]
print(rec.get("restarts"), unsold[0][1] != after[1], servers[0].stopped, all(s.stopped for s in servers), len(servers))
"""


class TestTurns(unittest.TestCase):
    """scale.py --turns: one process measures at a time and no simulated day starts under a measurement."""

    def run_py(self, code, *args, timeout=120):
        return subprocess.run([sys.executable, "-c", code, str(ROOT / "scripts/perf"), *map(str, args)],
                              capture_output=True, text=True, timeout=timeout, cwd=str(ROOT))

    def test_simultaneous_attempts_never_measure_together(self):
        # three processes take the turn 200 times each, the first attempt at the same moment (Codex P2/P1 on #296:
        # "look for a marker, then write your own" let two in together)
        with tempfile.TemporaryDirectory() as tmp:
            ps = [subprocess.Popen([sys.executable, "-c", TURNS_WORKER, str(ROOT / "scripts/perf"), tmp, "200"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(ROOT)) for _ in range(3)]
            (Path(tmp) / "go").touch()
            outs = [p.communicate(timeout=120) for p in ps]
            self.assertEqual([p.returncode for p in ps], [0, 0, 0], outs)
            self.assertEqual([o[0].strip() for o in outs], ["0", "0", "0"])
            self.assertEqual(sorted(f.name for f in Path(tmp).iterdir()), ["go"])       # lock and marks are gone

    def test_a_day_waits_for_the_measurement_and_the_measurement_for_the_day(self):
        code = r"""
import os, sys, threading, time
sys.path.insert(0, sys.argv[1])
import scale
real_sleep = time.sleep
scale.time.sleep = lambda s: real_sleep(0.002)
folder = sys.argv[2]
me = scale.Turns(folder)
lock, other_day = os.path.join(folder, "measure.lock"), os.path.join(folder, "day.999999")
out = []
# 1. somebody else measures: my day does not start, and leaves no mark while it waits
open(lock, "w").close()
th = threading.Thread(target=lambda: me.day(True)); th.start(); real_sleep(0.1)
out.append(th.is_alive())
os.unlink(lock); th.join(5)
out.append((not th.is_alive(), os.path.exists(me.mine)))
me.day(False)
# 2. somebody else is in the middle of a day: I hold the lock but do not measure until that day ends
open(other_day, "w").close()
th = threading.Thread(target=lambda: me.measure(True)); th.start(); real_sleep(0.1)
out.append((th.is_alive(), os.path.exists(lock)))
os.unlink(other_day); th.join(5)
out.append(not th.is_alive())
me.measure(False)
out.append(os.listdir(folder))
print(out)
"""
        with tempfile.TemporaryDirectory() as tmp:
            r = self.run_py(code, tmp)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "[True, (True, True), (True, True), True, []]")


SNAPSHOT_TURN = r"""
import os, sqlite3, sys, threading, time
sys.path.insert(0, sys.argv[1])
import scale
real_sleep = time.sleep
scale.time.sleep = lambda s: real_sleep(0.002)
tmp = sys.argv[2]
data, turns = os.path.join(tmp, "snap", "data"), os.path.join(tmp, "turns")
os.makedirs(data); os.makedirs(turns)
c = sqlite3.connect(os.path.join(data, "pharmacy.db"))
c.execute("CREATE TABLE sales (status TEXT, completed_at TEXT)")
c.execute("INSERT INTO sales VALUES ('completed', '2026-01-14T10:00:00.000Z'), ('completed', '2026-01-15T10:00:00.000Z')")
c.commit(); c.close()
lock = os.path.join(turns, "measure.lock")
calls, out = [], []

def fake(snap, rec, start, a, report):
    calls.append(os.path.exists(lock))
    if len(calls) == 2:
        raise RuntimeError("measurement failed")

scale.measure_snapshot = fake
args = ["--snapshot", data, "--label", "kept", "--turns", turns, "--out-txt", os.path.join(tmp, "r.txt")]
# 1. somebody else measures: the kept snapshot waits, then measures holding the lock, then gives it back
open(lock, "w").close()
th = threading.Thread(target=lambda: scale.main(args)); th.start(); real_sleep(0.3)
out.append((th.is_alive(), calls == []))
os.unlink(lock); th.join(20)
out.append((not th.is_alive(), list(calls), os.listdir(turns)))
# 2. a measurement that fails gives the turn back too
try:
    scale.main(args)
    out.append("no error")
except RuntimeError:
    out.append((calls, os.listdir(turns)))
print(out)
"""

SETUP_TURN = r"""
import os, sys, threading, time
sys.path.insert(0, sys.argv[1])
import scale, loadgen
real_sleep = time.sleep
scale.time.sleep = lambda s: real_sleep(0.002)
tmp = sys.argv[2]
turns = os.path.join(tmp, "turns")
os.makedirs(turns)
lock = os.path.join(turns, "measure.lock")
seen = []
real_setup = loadgen.Generator.setup

def setup(self):
    seen.append(("setup", os.path.exists(lock), [f for f in os.listdir(turns) if f.startswith("day.")] != []))
    raise SystemExit("stop here")                 # the rest of the run is not under test

loadgen.Generator.setup = setup
args = ["--profile", "", "--days", "1", "--items", "5", "--per-day", "1-1", "--turns", turns,
        "--out-txt", os.path.join(tmp, "r.txt"), "--work", tmp]
open(lock, "w").close()
th = threading.Thread(target=lambda: scale.main(args)); th.start(); real_sleep(0.4)
waited = th.is_alive() and seen == []
os.unlink(lock); th.join(20)
print(waited, seen, os.listdir(turns))
"""


class TestTurnsInRuns(unittest.TestCase):
    """The two places of a run that take a turn besides the simulated days (Codex on #296)."""

    def run_py(self, code, tmp):
        return subprocess.run([sys.executable, "-c", code, str(ROOT / "scripts/perf"), tmp],
                              capture_output=True, text=True, timeout=120, cwd=str(ROOT))

    def test_a_kept_snapshot_waits_for_its_turn_and_gives_it_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.run_py(SNAPSHOT_TURN, tmp)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip().splitlines()[-1],
                             "[(True, True), (True, [True], []), ([True, True], [])]")

    def test_the_generator_setup_waits_for_a_measurement(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.run_py(SETUP_TURN, tmp)
            # it waited while the lock was there; setup then ran without the lock and with its own day mark;
            # nothing is left in the turns folder
            self.assertEqual(r.stdout.strip().splitlines()[-1], "True [('setup', False, True)] []", r.stderr)


class TestMeasureOrder(unittest.TestCase):
    def test_a_report_that_never_answers_gets_a_new_server(self):
        # its query goes on inside the server after the client gave up; the next request must not be measured
        # under it (Codex P2 on #296). Fake server and client: only the order of real_years.measure is under test.
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run([sys.executable, "-c", RESTART_SCRIPT, str(ROOT / "scripts/perf"), tmp],
                               capture_output=True, text=True, timeout=120, cwd=str(ROOT))
            self.assertEqual(r.returncode, 0, r.stderr)
            # one restart; the request after the silent one went to another server; every server was stopped;
            # 3 servers: the first, the one after the restart, the second start-up at the end
            self.assertEqual(r.stdout.strip(), "1 True True True 3")


if __name__ == "__main__":
    unittest.main()
