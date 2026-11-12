#!/usr/bin/env python3
"""Run `manage.py serve` with Python's clock moved to a past day (benchmarks of synthetic snapshots only).

A 1-year snapshot ends on its own last day; measured "today" it would look idle and its batches expired.
This wrapper starts the clock at --at and lets it run at real speed, so "today" in the cashier screen and
the day report is the snapshot's last day. Only Python's clock moves (simclock.install_python); SQLite's
'now' is untouched, so query speed is the program's own.

  python scripts/perf/serve_at.py --at 2022-11-12T20:00 --port 8765     (PHARM_DATA_DIR set by the caller)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("TZ", "Africa/Cairo")
if hasattr(time, "tzset"):
    time.tzset()


class _RunningClock:
    """SimClock-compatible: now_utc moves with real time from the chosen start."""

    def __init__(self, start: datetime):
        self._start = start.astimezone(timezone.utc)
        self._t0 = time.monotonic()

    @property
    def now_utc(self) -> datetime:
        from datetime import timedelta
        return self._start + timedelta(seconds=time.monotonic() - self._t0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", default="", help="local start time, e.g. 2022-11-12T20:00 (empty = real clock)")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    t0 = time.perf_counter()
    if a.at:
        from simclock import install_python
        install_python(_RunningClock(datetime.fromisoformat(a.at).astimezone()))
    import manage
    from app import config
    s = config.load()
    os.environ["PHARM_PERF_IMPORT_S"] = f"{time.perf_counter() - t0:.3f}"
    manage.cmd_serve(argparse.Namespace(host="127.0.0.1", port=a.port), s)


if __name__ == "__main__":
    main()
