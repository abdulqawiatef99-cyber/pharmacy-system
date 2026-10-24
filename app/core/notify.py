"""Notifications for the owner and managers (anyone with notifications.read):
discounts, returns, voids, selling below cost, cash differences, price changes,
higher purchase costs. Emitted inside the same transaction as the action, so a
discount can never happen without its notification. Append-only."""
from __future__ import annotations

from ..db import Database, new_id
from .errors import ValidationFailed
from .permissions import Context

SEVERITIES = ("info", "warning", "error")


def emit(db: Database, ctx: Context, kind: str, severity: str, title: str, body: str | None = None,
         entity_type: str | None = None, entity_id: str | None = None) -> str:
    if severity not in SEVERITIES:
        raise ValueError(severity)
    nid = new_id()
    db.execute("INSERT INTO notifications (id, org_id, branch_id, kind, severity, title, body, entity_type,"
               " entity_id, actor_id, approver_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (nid, ctx.org_id, ctx.branch_id, kind, severity, title[:200], (body or "")[:500] or None,
                entity_type, entity_id, ctx.user_id, ctx.approver_id))
    return nid


def _name(db: Database, uid: str | None) -> str | None:
    if not uid:
        return None
    r = db.one("SELECT full_name FROM users WHERE id=?", (uid,))
    return r["full_name"] if r else None


def listing(db: Database, ctx: Context, *, unread_only: bool = False, limit: int = 50) -> dict:
    ctx.require("notifications.read")
    q = ("SELECT n.*, (SELECT 1 FROM notification_reads r WHERE r.notification_id=n.id AND r.user_id=?) seen"
         " FROM notifications n WHERE n.org_id=?")
    if unread_only:
        q += " AND NOT EXISTS (SELECT 1 FROM notification_reads r WHERE r.notification_id=n.id AND r.user_id=?)"
    q += " ORDER BY n.created_at DESC LIMIT ?"
    params = (ctx.user_id, ctx.org_id) + ((ctx.user_id,) if unread_only else ()) + (min(limit, 200),)
    names: dict = {}
    items = []
    for r in db.all(q, params):
        d = dict(r)
        d["seen"] = bool(d["seen"])
        for k in ("actor_id", "approver_id"):
            if d[k] and d[k] not in names:
                names[d[k]] = _name(db, d[k])
        d["actor"] = names.get(d["actor_id"])
        d["approver"] = names.get(d["approver_id"])
        items.append(d)
    return {"items": items, "unread": unread_count(db, ctx)}


def unread_count(db: Database, ctx: Context) -> int:
    if not ctx.can("notifications.read"):
        return 0
    return db.one("SELECT count(*) c FROM notifications n WHERE n.org_id=? AND NOT EXISTS (SELECT 1 FROM"
                  " notification_reads r WHERE r.notification_id=n.id AND r.user_id=?)",
                  (ctx.org_id, ctx.user_id))["c"]


def mark_read(db: Database, ctx: Context, ids: list[str] | None = None) -> int:
    """ids None = all."""
    ctx.require("notifications.read")
    if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)):
        raise ValidationFailed("بيانات غير صحيحة")
    with db.transaction():
        q = ("INSERT OR IGNORE INTO notification_reads (notification_id, user_id) SELECT id, ? FROM"
             " notifications WHERE org_id=?")
        params: tuple = (ctx.user_id, ctx.org_id)
        if ids is not None:
            if not ids:
                return 0
            q += f" AND id IN ({','.join('?' * len(ids))})"
            params += tuple(ids)
        return db.execute(q, params).rowcount
