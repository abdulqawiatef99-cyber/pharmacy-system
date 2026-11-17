#!/usr/bin/env python3
"""Scale test with synthetic data only (#253): one pharmacy size from an empty database, then timings.

real_years.py adds activity to a copy of the pharmacy's own database on its PC; this script needs no existing
database, so it runs anywhere (cloud, the test PC). It builds a synthetic pharmacy of one size with loadgen.py
(the program's own services on a simulated clock), and at each snapshot measures what real_years.py measures:
start-up, search, the cashier flow, every report over short and long ranges, scanning while a backup and while
the slowest reports run, and backup / verify / restore. The stock check of every snapshot is in the results.

  python scripts/perf/scale.py --profile large --years 1,3 --out-txt /tmp/scale-large.txt --work /tmp/scale
  python scripts/perf/scale.py --profile huge --years 1 --out-txt /tmp/scale-huge.txt
  python scripts/perf/scale.py --profile "" --days 2,3 --items 60 --per-day 8-12 --out-txt /tmp/s.txt   (smoke)

Sizes (real_years.PROFILES): large = 800-1200 sales a day, 15,000 items; huge = 2500-3500 a day, 30,000 items.
Disk: about 10 KB a sale, and the last snapshot needs about 2.5 times its database while it is measured.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import real_years as ry  # noqa: E402  (sets the program path and the time zone)


class Report(ry.Report):
    title = "SCALE TEST (#253) - synthetic data only, timings and row counts"


def machine() -> dict:
    out = {"cpus": os.cpu_count(), "python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version,
           "os": f"{platform.system()} {platform.release()}", "cpu": platform.processor() or platform.machine()}
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                out["cpu"] = line.split(":", 1)[1].strip()
                break
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal"):
                out["ram_mb"] = int(line.split()[1]) // 1024
    except OSError:
        pass
    return out


class Turns:
    """Several sizes generated at once on one machine (one process a core): a measurement runs alone.
    The process that measures holds `measure.lock` in the shared folder (created atomically, so two cannot hold it)
    and waits until the others finished their simulated day (`day.<pid>` files). A generator marks its day first and
    then looks for the lock; if it is there it takes the mark back and waits, so a day never starts under a measurement.
    The simulated clock does not move while a generator waits, so the data is the same as without waiting."""

    def __init__(self, folder: str):
        self.dir = Path(folder) if folder else None
        self.holding = False
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.lock = self.dir / "measure.lock"
            self.mine = self.dir / f"day.{os.getpid()}"

    def day(self, on: bool) -> None:
        if not self.dir:
            return
        if not on:
            self.mine.unlink(missing_ok=True)
            return
        while True:
            self.mine.touch()
            if self.holding or not self.lock.exists():
                return
            self.mine.unlink(missing_ok=True)         # somebody measures: no new day until it is done
            time.sleep(2)

    def measure(self, on: bool) -> None:
        if not self.dir:
            return
        if not on:
            if self.holding:
                self.lock.unlink(missing_ok=True)
                self.holding = False
            return
        while True:
            try:
                os.close(os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                break
            except FileExistsError:                   # another process measures: wait for the turn
                time.sleep(2)
        self.holding = True
        while [p for p in self.dir.glob("day.*") if p != self.mine]:
            time.sleep(2)


def stock_check(db_path: Path) -> dict:
    """The owner's stock check on the snapshot (read-only): every balance against its movements."""
    from app.db import Database
    from app.inventory import integrity
    db = Database(db_path)
    try:
        t = time.perf_counter()
        out = {"ok": True, "counts": {}, "movements": 0, "balances": 0}
        for o in db.all("SELECT id FROM organizations"):
            r = integrity.scan(db, o["id"])
            out["ok"] = out["ok"] and r["ok"]
            out["movements"] += r["movements"]
            out["balances"] += r["balances"]
            for k, v in r["counts"].items():
                out["counts"][k] = out["counts"].get(k, 0) + v
        out["seconds"] = round(time.perf_counter() - t, 1)
        return out
    finally:
        db.close()


def measure_snapshot(snap: Path, rec: dict, start, a, report) -> None:
    """Stock check, the measurements, and with --indexes-sql the requests again with the proposed indexes."""
    report.progress(f"{rec['label']}: stock check")
    rec["stock_check"] = stock_check(snap / "pharmacy.db")
    if not rec["stock_check"]["ok"]:
        rec["error"] = f"stock check found differences: {rec['stock_check']['counts']}"
    # a backup copies the database, compresses the copy and opens it again to check it (backup.create,
    # quick_check): about 1.7 times the database in free disk on top of the database itself
    size = (snap / "pharmacy.db").stat().st_size
    room = ry.free_bytes(snap) >= 1.7 * size + 500_000_000
    if not room:
        rec["backup_skipped"] = (f"free disk {ry.free_bytes(snap) / 1e9:.1f} GB, a backup of this"
                                 f" {size / 1e9:.1f} GB database needs about {1.7 * size / 1e9 + 0.5:.1f} GB")
        report.note(f"{rec['label']}: backup not measured: {rec['backup_skipped']}")
    ry.measure(snap, "owner", start, a.repeat, rec, report, backup=room)
    if a.indexes_sql:
        # the same snapshot again with proposed indexes (a migration file that is not in the program yet):
        # what the owner would get after that migration. Requests only, no second backup.
        rec2 = {k: rec[k] for k in ("profile", "days", "gen_s", "stats", "stock_check") if k in rec}
        rec2.update(label=rec["label"] + " + indexes", indexes_sql=Path(a.indexes_sql).name)
        report.data["snapshots"].append(rec2)
        report.progress(f"{rec2['label']}: creating the indexes")
        t = time.perf_counter()
        c = sqlite3.connect(snap / "pharmacy.db")
        c.executescript(Path(a.indexes_sql).read_text(encoding="utf-8"))
        if a.analyze:
            c.execute("ANALYZE")
        c.close()
        rec2["indexes_s"] = round(time.perf_counter() - t, 1)
        ry.measure(snap, "owner", start, a.repeat, rec2, report, backup=False)


def measure_existing(a, report) -> int:
    """--snapshot: a data folder kept from an earlier run (--keep-last, or loadgen.py) is measured where it is."""
    import loadgen
    from datetime import datetime, timezone
    snap = Path(a.snapshot).resolve()
    c = sqlite3.connect(snap / "pharmacy.db")
    loadgen.drop_gen_indexes(c)
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    first, last, n = c.execute("SELECT min(completed_at), max(completed_at), count(*) FROM sales WHERE status='completed'").fetchone()
    c.close()
    day = lambda t: datetime.strptime(t, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).astimezone().date()
    start = day(first) - timedelta(days=1)
    rec = {"label": a.label or snap.parent.name, "profile": a.profile or "custom", "days": (day(last) - start).days}
    report.data["plan"] = {"snapshot": rec["label"], "machine": machine()}
    report.data["snapshots"].append(rec)
    turns = Turns(a.turns)
    try:
        if a.turns:
            report.progress(f"{rec['label']}: waiting for its turn to measure")
        turns.measure(True)
        measure_snapshot(snap, rec, start, a, report)
        report.data["status"] = "finished"
    except BaseException as e:
        rec["error"] = ry.safe_error(e)
        report.data["status"] = "failed"
        raise
    finally:
        turns.measure(False)
        report.data["summary"] = ry.summary(report.data["snapshots"])
        report.progress("done")
    print("\n".join(report.data["summary"]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default="large", help=f"one of {', '.join(ry.PROFILES)}; empty = the options below")
    ap.add_argument("--out-txt", required=True, help="results text file; a .json is written next to it")
    ap.add_argument("--work", default="", help="folder for the temporary database (default: the system temp folder)")
    ap.add_argument("--years", default="1", help="snapshots, in years of activity, e.g. 1,3")
    ap.add_argument("--days", default="", help="snapshots in days (overrides --years; smoke runs)")
    ap.add_argument("--end", default="", help="last simulated day (default: today)")
    ap.add_argument("--items", type=int, default=4000)
    ap.add_argument("--suppliers", type=int, default=12)
    ap.add_argument("--customers", type=int, default=300)
    ap.add_argument("--credit-limit", type=int, default=500000)
    ap.add_argument("--per-day", default="150-300")
    ap.add_argument("--transfer-share", type=float, default=0.02)
    ap.add_argument("--attachment-kb", type=float, default=60)
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--seed", type=int, default=253)
    ap.add_argument("--turns", default="", help="a folder shared by scale.py runs started together: only one measures"
                    " at a time and the others wait meanwhile, so the timings are not disturbed")
    ap.add_argument("--indexes-sql", default="", help="a .sql file of CREATE INDEX statements (a proposed migration):"
                    " every snapshot is measured a second time with them (requests only)")
    ap.add_argument("--analyze", action="store_true", help="with --indexes-sql: also run ANALYZE before the second pass")
    ap.add_argument("--snapshot", default="", help="measure this existing synthetic data folder instead of generating"
                    " (kept with --keep-last or made by loadgen.py); it is measured in place and so changed")
    ap.add_argument("--label", default="", help="with --snapshot: its name in the results")
    ap.add_argument("--keep-last", default="", help="move the last snapshot's data folder here instead of deleting it")
    a = ap.parse_args(argv)
    if a.profile:
        if a.profile not in ry.PROFILES:
            raise SystemExit(f"unknown size {a.profile!r}; choose from {list(ry.PROFILES)}")
        for k, v in ry.PROFILES[a.profile].items():
            setattr(a, k, v)
    pname = a.profile or "custom"
    days = sorted({int(x) for x in a.days.split(",") if x} if a.days
                  else {int(round(float(y) * 365)) for y in a.years.split(",") if y})
    labels = {d: f"{d} days" if a.days else f"{d / 365:g} year" + ("s" if d != 365 else "") for d in days}
    report = Report(Path(a.out_txt).resolve())
    if a.snapshot:
        return measure_existing(a, report)
    report.data["plan"] = {"size": pname, "per_day": a.per_day, "items": a.items, "customers": a.customers,
                           "suppliers": a.suppliers, "snapshot_days": days, "machine": machine()}
    work = Path(tempfile.mkdtemp(prefix="pharm-scale-", dir=a.work or None))
    turns = Turns(a.turns)

    import loadgen
    a.out, a.years, a.days, a.quiet, a.snapshots = str(work / "gen"), days[-1] / 365, days[-1], True, ""
    gen = loadgen.Generator(a)
    db_path = gen.work / "pharmacy.db"
    try:
        report.progress(f"{pname}: catalog, suppliers, customers, first purchases")
        t0 = time.perf_counter()
        turns.day(True)                               # the setup is generating too: not under a measurement
        try:
            gen.setup()
            loadgen.install_fast_matching(gen.db, gen.owner.org_id)
            gen.restock(gen.start, initial=True)
        finally:
            turns.day(False)
        report.data["plan"]["setup_s"] = round(time.perf_counter() - t0, 1)
        recent: list = []
        t1 = time.perf_counter()
        lo, hi = gen.lo, gen.hi
        d = 0
        for target in days:
            stopped = False
            while d < target:
                if ry.free_bytes(work) < 1_500_000_000:
                    report.note(f"{pname}: disk almost full after {d} days: stopped adding days")
                    stopped = True
                    break
                d += 1
                turns.day(True)
                try:
                    gen.run_day(gen.start + timedelta(days=d), recent)
                finally:
                    turns.day(False)
                if d % 10 == 0:
                    rate = gen.stats["sales"] / (time.perf_counter() - t1)
                    left = (days[-1] - d) * (lo + hi) / 2 / rate / 3600
                    report.progress(f"{pname}: day {d} of {days[-1]}, {gen.stats['sales']} sales, {rate:.0f} sales a second,"
                                    f" about {left:.1f} h of generating left, database {db_path.stat().st_size / 1e9:.1f} GB")
            last = stopped or target == days[-1]
            rec = {"label": f"{pname} - " + (labels[target] if not stopped else f"{d} days (stopped early)"),
                   "profile": pname, "days": d,
                   "gen_s": round(time.perf_counter() - t1), "stats": dict(gen.stats)}
            report.data["snapshots"].append(rec)
            snap = work / f"snap-{d}" / "data"
            snap.mkdir(parents=True)
            report.progress(f"{rec['label']}: waiting for its turn to measure" if a.turns else f"{rec['label']}: snapshot")
            turns.measure(True)
            report.progress(f"{rec['label']}: snapshot")
            if last:                                  # the last one: the generator's own file, no second copy on disk
                gen.db.close()
                shutil.move(str(db_path), str(snap / "pharmacy.db"))
                if gen.upload_dir.exists():
                    shutil.move(str(gen.upload_dir), str(snap / "uploads"))
            else:
                gen.db.backup_to(snap / "pharmacy.db")
                if gen.upload_dir.exists():
                    shutil.copytree(gen.upload_dir, snap / "uploads")
            c = sqlite3.connect(snap / "pharmacy.db")  # the snapshot keeps the program's own schema only
            loadgen.drop_gen_indexes(c)
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.close()
            try:
                measure_snapshot(snap, rec, gen.start, a, report)
            except Exception as e:                       # keep what was measured, go on with the next snapshot
                rec["error"] = ry.safe_error(e)
            finally:
                turns.measure(False)
                if a.keep_last and last:
                    shutil.move(str(snap), a.keep_last)
                shutil.rmtree(snap.parent, ignore_errors=True)
            report.data["summary"] = ry.summary(report.data["snapshots"])
            report.write()
            if stopped:
                break
        report.data["status"] = "finished"
    except BaseException as e:
        report.data["status"] = "failed"
        report.note(ry.safe_error(e))
        raise
    finally:
        turns.day(False)
        turns.measure(False)
        try:
            gen.db.close()
        except Exception:
            pass
        shutil.rmtree(work, ignore_errors=True)
        report.progress("done")
    print("\n".join(report.data.get("summary") or []))
    return 0


if __name__ == "__main__":
    sys.exit(main())
