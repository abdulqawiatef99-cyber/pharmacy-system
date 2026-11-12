"""On-screen approvals (#137): the owner or a manager types his PIN once, the server checks it and
hands the screen a short-lived approval token. The token is bound to the person's sign-in session, the
approver, the permission(s) he approved and the one action (method + API path) that asked for them; it
never lends the approver's other permissions, and the screen never keeps the PIN.

Tokens live in this process only (a restart clears them: the screen simply asks again).
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, replace

from .errors import Forbidden
from .permissions import APPROVER_ROLES, PERMISSIONS, Context

TTL_SECONDS = 90
MAX_GRANTS = 500
DISCOUNT = "discount"            # not a permission: the approver's discount limit for this screen


@dataclass(frozen=True)
class Grant:
    org_id: str
    user_id: str
    session: str                 # sha256 of the actor's session token
    approver_id: str
    action: str
    perms: frozenset[str]
    until: float


_lock = threading.Lock()
_grants: dict[str, Grant] = {}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def clear() -> None:
    with _lock:
        _grants.clear()


def issue(ctx: Context, session_token: str, approver_id: str, action: str, perm: str,
          previous: str | None = None, now: float | None = None) -> tuple[str, int]:
    """A token for `perm` on `action`, after the approver's PIN was verified for this request. A still valid
    token of the same session, approver and action keeps what it already allowed (an action that needs
    two approvals asks twice, once per permission). Returns (token, seconds it lasts)."""
    now = time.monotonic() if now is None else now
    session = _digest(session_token)
    perms = {perm}
    with _lock:
        for k in [k for k, g in _grants.items() if g.until <= now]:
            del _grants[k]
        key = _digest(previous) if previous else None
        old = _grants.get(key) if key else None
        if old and (old.org_id, old.user_id, old.session, old.approver_id, old.action) == \
                (ctx.org_id, ctx.user_id, session, approver_id, action) and old.until > now:
            perms |= old.perms
            del _grants[key]
        if len(_grants) >= MAX_GRANTS:
            del _grants[min(_grants, key=lambda k: _grants[k].until)]
        token = secrets.token_urlsafe(32)
        _grants[_digest(token)] = Grant(ctx.org_id, ctx.user_id, session, approver_id, action, frozenset(perms),
                                        now + TTL_SECONDS)
    return token, TTL_SECONDS


def apply(ctx: Context, session_token: str, token: str, action: str, access_of, now: float | None = None) -> Context:
    """The request carries an approval token: lend the approved permissions (only those the approver
    still has) for this action. `access_of(approver_id)` is accounts.effective_access for the org."""
    now = time.monotonic() if now is None else now
    with _lock:
        g = _grants.get(_digest(token))
    if g is None or g.until <= now or g.org_id != ctx.org_id or g.user_id != ctx.user_id \
            or g.session != _digest(session_token):
        raise Forbidden("انتهت مدة الموافقة، اطلبها مرة أخرى", code="approval_expired")
    if g.action != action:
        return ctx                                   # approved for another action: lends nothing here
    access = access_of(g.approver_id)
    if access["role_code"] not in APPROVER_ROLES:
        raise Forbidden("الموافقة من المالك أو المدير فقط", code="approver_invalid")
    perms = frozenset(p for p in g.perms if p in PERMISSIONS and p in access["permissions"])
    return replace(ctx, approver_id=g.approver_id, approver_permissions=perms,
                   approver_discount_bp=access["max_discount_bp"] if DISCOUNT in g.perms else 0)
