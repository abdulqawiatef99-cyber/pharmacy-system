"""Keeping the SQLite file healthy over years of use.

What is done, and when:
- Every backup checks the database (PRAGMA integrity_check, foreign keys, stock balances) on its snapshot and
  refuses to write a backup of a damaged database (backup.create); the owner is told. Nothing to add here.
- Once a day (daily checks): the write-ahead log is checkpointed. Normally a passive checkpoint that blocks
  nobody; when the WAL file has grown past WAL_LIMIT_BYTES (a long report held an old snapshot while sales kept
  writing) it is truncated, waiting at most TRUNCATE_WAIT_MS for readers so the cashier is never held.
- Once a day: logs/server.log and logs/restore.log are trimmed to their newest part. The launcher opens them
  for append and nothing else ever shortened them.
- VACUUM only when a person asks (`python -m app.core.dbhealth vacuum`, with the program stopped): it rewrites
  the whole file, needs free disk for a second copy and locks everything while it runs. The books are
  append-only, so free pages come only from cleaned notifications and sessions: a few megabytes a year.
- No ANALYZE / PRAGMA optimize: see retention.clean (#249) and docs/specs/sqlite-health.md for the measurement.

Never deleted, here or anywhere: the audit log, the safety log, stock movements, invoices (retention.py).
"""
from __future__ import annotations

import json
import logging
import shutil
import sys
from datetime import date
from pathlib import Path

from ..db import Database

log = logging.getLogger("pharm.dbhealth")

WAL_LIMIT_BYTES = 64 * 1024 * 1024
TRUNCATE_WAIT_MS = 1000
BUSY_TIMEOUT_MS = 15000              # what db.py sets on every connection
LOG_MAX_BYTES = 20 * 1024 * 1024
LOG_KEEP_BYTES = 5 * 1024 * 1024
LOG_FILES = ("server.log", "restore.log")


class NotEnoughSpace(RuntimeError):
    pass


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def report(db: Database) -> dict:
    """Sizes of the database file and its WAL, and how much of the file is free pages."""
    c = db.conn
    page_size = c.execute("PRAGMA page_size").fetchone()[0]
    pages = c.execute("PRAGMA page_count").fetchone()[0]
    free = c.execute("PRAGMA freelist_count").fetchone()[0]
    return {"db_bytes": _size(Path(db.path)), "wal_bytes": _size(Path(db.path + "-wal")), "page_size": page_size,
            "page_count": pages, "freelist_count": free, "free_bytes": free * page_size,
            "free_pct": round(free * 100 / pages) if pages else 0,
            "journal_mode": c.execute("PRAGMA journal_mode").fetchone()[0]}


def checkpoint(db: Database, limit_bytes: int = WAL_LIMIT_BYTES) -> dict:
    """Move the WAL into the database file. Passive (never waits, never blocks) unless the WAL file is over
    `limit_bytes`; then one truncating checkpoint that gives up after TRUNCATE_WAIT_MS if a reader is in the way."""
    c = db.conn
    if c.in_transaction:
        raise RuntimeError("a checkpoint cannot run inside a transaction")
    wal = Path(db.path + "-wal")
    out = {"mode": "passive", "wal_bytes_before": _size(wal)}
    busy = c.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()[0]
    if _size(wal) > limit_bytes:
        out["mode"] = "truncate"
        try:
            c.execute(f"PRAGMA busy_timeout = {int(TRUNCATE_WAIT_MS)}")
            busy = c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
        finally:
            c.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    out.update(busy=bool(busy), wal_bytes_after=_size(wal))
    return out


def trim_log(path: Path, max_bytes: int | None = None, keep_bytes: int | None = None) -> int:
    """Shorten a log file over `max_bytes` to its newest `keep_bytes` (from a line start). In place: the
    launcher keeps the file open for append, so it is never renamed or deleted. Returns the bytes removed."""
    max_bytes = LOG_MAX_BYTES if max_bytes is None else max_bytes
    keep_bytes = LOG_KEEP_BYTES if keep_bytes is None else keep_bytes
    try:
        size = path.stat().st_size
        if size <= max_bytes:
            return 0
        with open(path, "r+b") as f:
            f.seek(size - keep_bytes)
            tail = f.read()
            cut = tail.find(b"\n")
            tail = tail[cut + 1:] if cut >= 0 else tail
            head = f"--- log trimmed on {date.today().isoformat()}: {size - len(tail)} older bytes removed ---\n".encode()
            f.seek(0)
            f.write(head + tail)
            f.truncate()
        return size - len(head) - len(tail)
    except OSError as e:
        log.warning("could not trim %s: %s", path.name, e)
        return 0


def vacuum(db: Database) -> dict:
    """Rewrite the database file without its free pages. On demand only, with the program stopped: it needs
    room for a second copy of the file and nothing else can use the database while it runs."""
    c = db.conn
    if c.in_transaction:
        raise RuntimeError("VACUUM cannot run inside a transaction")
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")       # so the file size below is the whole database
    before = report(db)
    folder = Path(db.path).parent
    free = shutil.disk_usage(folder).free
    if free < 2 * before["db_bytes"]:
        raise NotEnoughSpace(f"VACUUM needs about {2 * before['db_bytes']} bytes free in {folder}; {free} are free")
    c.execute("VACUUM")
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return {"before": before, "after": report(db)}


def daily_maintenance(db: Database, *, log_dir: Path, today: date | None = None) -> dict | None:
    """Once per local day (state in db_health.json next to the database, so a restore never rolls it back):
    checkpoint, trim the logs, record the sizes. Returns what was done, or None when today is already done."""
    today = today or date.today()
    state_path = Path(db.path).parent / "db_health.json"
    try:
        st = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    if st.get("last") == today.isoformat():
        return None
    out = {"checkpoint": checkpoint(db),
           "logs": {name: trim_log(Path(log_dir) / name) for name in LOG_FILES},
           "report": report(db)}
    if out["checkpoint"]["busy"] and out["checkpoint"]["mode"] == "truncate":
        log.warning("the WAL file is %d bytes and could not be truncated today (a reader was in the way)",
                    out["checkpoint"]["wal_bytes_after"])
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"last": today.isoformat(), **out}, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(state_path)
    return out


def main(argv: list[str] | None = None, out=print) -> int:
    """python -m app.core.dbhealth report|vacuum [--db PATH]   (vacuum: stop the program first)"""
    import argparse
    ap = argparse.ArgumentParser(prog="python -m app.core.dbhealth", description=main.__doc__)
    ap.add_argument("action", choices=("report", "vacuum"))
    ap.add_argument("--db", default="", help="database file (default: the configured one)")
    a = ap.parse_args(argv)
    path = a.db
    if not path:
        from .. import config
        path = str(config.load().db_path)
    db = Database(path)
    try:
        out(json.dumps(report(db) if a.action == "report" else vacuum(db), ensure_ascii=False))
    except NotEnoughSpace as e:
        out(str(e))
        return 2
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
