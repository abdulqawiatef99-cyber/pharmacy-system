"""Short-lived authorization for a supervisor's on-screen approval.

A write challenge comes from a permission refusal and identifies the exact
request the supervisor sees. A grant may authorize that write once.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, replace

from .errors import Forbidden, ValidationFailed
from .permissions import APPROVER_ROLES, PASSWORD_SESSION_PERMISSIONS, PERMISSIONS, Context

TTL_SECONDS = 90
CHALLENGE_TTL = 300
MAX_GRANTS = 500
DISCOUNT = "discount"


@dataclass(frozen=True)
class Intent:
    operation: str
    fingerprint: str


@dataclass(frozen=True)
class Challenge:
    subject: tuple[str, str, str]
    action: str
    permission: str
    intent: Intent
    until: float


@dataclass(frozen=True)
class Grant:
    subject: tuple[str, str, str]
    approver_id: str
    action: str
    perms: frozenset[str]
    until: float
    intent: Intent | None
    used: bool = False


_lock = threading.Lock()
_grants: dict[str, Grant] = {}
_challenges: dict[str, Challenge] = {}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _subject(ctx: Context, session: str) -> tuple[str, str, str]:
    return ctx.org_id, ctx.user_id, _digest(session)


def _store(entries: dict, value, now: float) -> str:
    for key in [k for k, entry in entries.items() if entry.until <= now]:
        del entries[key]
    if len(entries) >= MAX_GRANTS:
        del entries[min(entries, key=lambda key: entries[key].until)]
    token = secrets.token_urlsafe(32)
    entries[_digest(token)] = value
    return token


def clear() -> None:
    with _lock:
        _grants.clear()
        _challenges.clear()


def challenge(ctx: Context, session: str, action: str, permission: str, intent: Intent) -> str:
    now = time.monotonic()
    with _lock:
        return _store(_challenges, Challenge(_subject(ctx, session), action, permission, intent,
                                             now + CHALLENGE_TTL), now)


def eligible(ctx: Context, perm: str) -> bool:
    return ctx.auth_method != "pin" or perm not in PASSWORD_SESSION_PERMISSIONS


def issue(ctx: Context, session: str, approver_id: str, action: str, perm: str, *,
          challenge_token: str = "", previous: str | None = None) -> tuple[str, int]:
    now = time.monotonic()
    subject = _subject(ctx, session)
    perms = {perm}
    intent = None
    with _lock:
        if not action.startswith("GET "):
            origin = _challenges.get(_digest(challenge_token))
            if (origin is None or origin.until <= now
                    or (origin.subject, origin.action, origin.permission) != (subject, action, perm)):
                raise ValidationFailed("اطلب الموافقة من العملية نفسها", code="approval_intent_required")
            intent = origin.intent
        key = _digest(previous) if previous else None
        old = _grants.get(key)
        if (old and not old.used and old.until > now and (old.subject, old.approver_id, old.action, old.intent)
                == (subject, approver_id, action, intent)):
            perms |= old.perms
            del _grants[key]
        grant = Grant(subject, approver_id, action, frozenset(perms), now + TTL_SECONDS, intent)
        return _store(_grants, grant, now), TTL_SECONDS


def apply(ctx: Context, session: str, token: str, action: str, access_of,
          *, intent: Intent | None = None) -> tuple[Context, bool]:
    """Validate and reserve the grant before the handler can start the write."""
    key = _digest(token)
    with _lock:
        grant = _grants.get(key)
        if grant is None or grant.until <= time.monotonic() or grant.subject != _subject(ctx, session):
            raise Forbidden("انتهت مدة الموافقة، اطلبها مرة أخرى", code="approval_expired")
        if grant.action != action or grant.intent != intent:
            raise Forbidden("الموافقة لعملية مختلفة", code="approval_mismatch")
        access = access_of(grant.approver_id)
        if access["role_code"] not in APPROVER_ROLES:
            raise Forbidden("الموافقة من المالك أو المدير فقط", code="approver_invalid")
        perms = frozenset(p for p in grant.perms if p in PERMISSIONS and p in access["permissions"] and eligible(ctx, p))
        if len(perms) + (DISCOUNT in grant.perms) != len(grant.perms):
            raise Forbidden("صلاحية الموافقة اتغيّرت", code="approver_invalid")
        if intent is not None and not grant.used:
            _grants[key] = replace(grant, used=True)
        authorized = replace(ctx, approver_id=grant.approver_id, approver_permissions=perms,
                             approver_discount_bp=access["max_discount_bp"] if DISCOUNT in grant.perms else 0)
        return authorized, grant.used


def restore_for_next_permission(token: str) -> None:
    """The action refused another required permission before committing."""
    key = _digest(token)
    with _lock:
        grant = _grants.get(key)
        if grant and grant.used:
            _grants[key] = replace(grant, used=False)
