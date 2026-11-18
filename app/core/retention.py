"""Keeping the database small without losing anything that matters.

Never deleted: invoices (sales and purchases), customer and supplier accounts, cash drawer,
expenses, stock movements, the audit log — the law requires keeping the books for years and
they are small (a busy pharmacy adds roughly 20-40 MB a year; backups are compressed ~10x).

Cleaned once a day: notifications older than 180 days, expired or closed sign-in sessions after
30 days, and the short-lived retry cache. Old automatic backups are pruned by the backup job.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from ..db import Database

log = logging.getLogger("pharm.retention")
NOTIFICATION_DAYS = 180
SESSION_DAYS = 30


def clean(db: Database) -> dict:
    out = {}
    with db.transaction():
        old = f"-{NOTIFICATION_DAYS} days"
        db.execute("DELETE FROM notification_reads WHERE notification_id IN (SELECT id FROM notifications WHERE"
                   " created_at < strftime('%Y-%m-%dT%H:%M:%fZ','now',?))", (old,))
        out["notifications"] = db.execute("DELETE FROM notifications WHERE created_at < strftime('%Y-%m-%dT%H:%M:%fZ','now',?)",
                                          (old,)).rowcount
        out["sessions"] = db.execute(
            "DELETE FROM sessions WHERE (expires_at < strftime('%Y-%m-%dT%H:%M:%fZ','now',?) OR"
            " (revoked_at IS NOT NULL AND revoked_at < strftime('%Y-%m-%dT%H:%M:%fZ','now',?)))",
            (f"-{SESSION_DAYS} days", f"-{SESSION_DAYS} days")).rowcount
    # No PRAGMA optimize / ANALYZE: statistics gathered on a small database go stale after a bulk load
    # and made the planner scan whole tables (#249); the queries are built for SQLite's default plans.
    return out


def _dir_size(p: Path) -> int:
    total = 0
    if p and p.exists():
        for root, _, files in os.walk(p):
            for f in files:
                try:
                    total += (Path(root) / f).stat().st_size
                except OSError:
                    pass
    return total


def sizes(db: Database, db_path: Path, upload_dir: Path, backup_dir: Path) -> dict:
    """What takes space on the disk, for the program page."""
    def f(p: Path) -> int:
        try:
            return p.stat().st_size
        except OSError:
            return 0
    first = db.one("SELECT min(completed_at) d FROM sales WHERE status='completed'")["d"]
    return {"db": f(db_path) + f(db_path.with_name(db_path.name + "-wal")), "uploads": _dir_size(upload_dir),
            "backups": _dir_size(backup_dir),
            "rows": {t: db.one(f"SELECT count(*) c FROM {t}")["c"] for t in ("sales", "sale_lines", "purchase_invoices",
                                                                           "stock_movements", "audit_log", "notifications")},
            "since": first[:10] if first else None,
            "policy": {"notifications_days": NOTIFICATION_DAYS, "sessions_days": SESSION_DAYS}}
