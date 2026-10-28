"""Atomic replay protection for synchronous database-only operations.

Callers must authorize the current actor before invoking this helper. The action
and response serialization must be synchronous, with no await or external side
effects. This currently serves only the manual cash movement HTTP route.
"""
from __future__ import annotations

from collections.abc import Callable

from .errors import ValidationFailed
from .permissions import Context
from ..db import Database


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
            db.execute("DELETE FROM idempotency_keys WHERE created_at < datetime('now', ?)",
                       (f"-{ttl_days} days",))
            db.execute("INSERT INTO idempotency_keys (org_id, user_id, key, method, path,"
                       " request_hash, status, response) VALUES (?,?,?,?,?,?,?,?)",
                       (ctx.org_id, ctx.user_id, key, method, path,
                        request_hash, status, response))
        return status, response, False
