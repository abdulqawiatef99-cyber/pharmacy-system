"""Complaints and suggestions typed inside the program. Kept on this PC; the owner downloads
them (or they are read in a development session) — nothing is sent anywhere automatically."""
from __future__ import annotations

from ..db import Database, new_id
from . import notify
from .errors import NotFound, ValidationFailed
from .permissions import Context

KINDS = {"complaint": "شكوى", "suggestion": "اقتراح", "bug": "مشكلة في البرنامج"}


def add(db: Database, ctx: Context, kind: str, text: str, screen: str | None = None) -> dict:
    if kind not in KINDS:
        raise ValidationFailed("اختر النوع")
    text = (text or "").strip()
    if len(text) < 5:
        raise ValidationFailed("اكتب الملاحظة")
    fid = new_id()
    with db.transaction():
        db.execute("INSERT INTO feedback (id, org_id, user_id, kind, text, screen) VALUES (?,?,?,?,?,?)",
                   (fid, ctx.org_id, ctx.user_id, kind, text[:2000], (screen or "")[:100] or None))
        notify.emit(db, ctx, "feedback", "info", f"{KINDS[kind]} جديدة", text[:200], "feedback", fid)
    return {"id": fid}


def listing(db: Database, ctx: Context) -> list[dict]:
    q = ("SELECT f.*, u.full_name by_name FROM feedback f LEFT JOIN users u ON u.id=f.user_id WHERE f.org_id=?")
    p: tuple = (ctx.org_id,)
    if not ctx.can("users.manage"):
        q += " AND f.user_id=?"
        p += (ctx.user_id,)
    return [dict(r) for r in db.all(q + " ORDER BY f.created_at DESC LIMIT 300", p)]


def set_status(db: Database, ctx: Context, fid: str, status: str, reply: str | None = None) -> None:
    ctx.require("users.manage")
    if status not in ("new", "seen", "done"):
        raise ValidationFailed("الحالة غير صحيحة")
    with db.transaction():
        n = db.execute("UPDATE feedback SET status=?, reply=coalesce(?, reply) WHERE id=? AND org_id=?",
                       (status, (reply or "").strip() or None, fid, ctx.org_id)).rowcount
        if not n:
            raise NotFound("غير موجود")
