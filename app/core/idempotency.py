"""Atomic replay protection for synchronous database-only operations.

Callers must authorize the current actor before invoking this helper. The action
and response serialization must be synchronous, with no await or external side
effects. Used by manual cash movements and customer collections.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

from .errors import ValidationFailed
from .permissions import Context
from ..db import Database

TTL_DAYS = 7
# Old replay records are removed at most this often per database (and once a day by retention.clean). The table
# has no index on created_at, so the clean-up reads every stored answer: on every fresh keyed POST that was a
# whole-table scan under the write lock to delete nothing (Codex audit M2-25, 19/11).
PRUNE_EVERY_SECONDS = 3600
_pruned_at: dict[str, float] = {}
_prune_lock = threading.Lock()


def prune(db: Database, ttl_days: int = TTL_DAYS, *, force: bool = False) -> int:
    """Delete the replay records older than `ttl_days`, of every pharmacy; returns how many. Call it inside the
    caller's write transaction. Unless forced it does nothing when this database was cleaned within the hour: a
    record then lives up to an hour longer than its days, which only lengthens the protection."""
    now = time.monotonic()
    with _prune_lock:
        last = _pruned_at.get(str(db.path))
        if not force and last is not None and now - last < PRUNE_EVERY_SECONDS:
            return 0
        _pruned_at[str(db.path)] = now
    return db.execute("DELETE FROM idempotency_keys WHERE created_at < datetime('now', ?)",
                      (f"-{ttl_days} days",)).rowcount


def run(db: Database, ctx: Context, *, key: str, method: str, path: str,
        request_hash: str, ttl_days: int,
        action: Callable[[], tuple[int, str]]) -> tuple[int, str, bool]:
    """Return (status, JSON text, replayed); commit action + cache as one unit.

    BEGIN IMMEDIATE serializes the lookup and write across connections/processes,
    not just one Python lock. Empty/oversized keys preserve legacy no-cache
    behavior, but response-generation failure still rolls back the cash write.
    """
    use_key = bool(key) and len(key) <= 100
    with db.transaction():
        if use_key:
            hit = db.one("SELECT request_hash, status, response FROM idempotency_keys"
                         " WHERE org_id=? AND user_id=? AND key=?",
                         (ctx.org_id, ctx.user_id, key))
            if hit:
                if hit["request_hash"] != request_hash:
                    raise ValidationFailed("نفس مفتاح الطلب استُخدم لطلب مختلف",
                                           code="idempotency_mismatch")
                return hit["status"], hit["response"], True
        status, response = action()
        if use_key:
            prune(db, ttl_days)
            db.execute("INSERT INTO idempotency_keys (org_id, user_id, key, method, path,"
                       " request_hash, status, response) VALUES (?,?,?,?,?,?,?,?)",
                       (ctx.org_id, ctx.user_id, key, method, path,
                        request_hash, status, response))
        return status, response, False
