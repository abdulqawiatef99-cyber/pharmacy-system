"""Card machines and transfer wallets (إنستاباي، فودافون كاش، ...) the pharmacy accepts."""
from __future__ import annotations

from ..core import audit
from ..core.errors import Conflict, NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id

DEFAULTS = [("card", "مكنة الفيزا"), ("transfer", "إنستاباي"), ("transfer", "فودافون كاش")]


def ensure_defaults(db: Database) -> None:
    for o in db.all("SELECT id FROM organizations"):
        if db.one("SELECT 1 FROM payment_channels WHERE org_id=? LIMIT 1", (o["id"],)):
            continue
        with db.transaction():
            for i, (k, n) in enumerate(DEFAULTS):
                db.execute("INSERT INTO payment_channels (id, org_id, kind, name, sort) VALUES (?,?,?,?,?)",
                           (new_id(), o["id"], k, n, i))


def listing(db: Database, ctx: Context) -> list[dict]:
    ctx.require("sales.create")
    return [dict(r) for r in db.all("SELECT * FROM payment_channels WHERE org_id=? ORDER BY kind, sort, name",
                                    (ctx.org_id,))]


def save(db: Database, ctx: Context, data: dict, cid: str | None = None) -> list[dict]:
    ctx.require("settings.manage")
    name = str(data.get("name", "")).strip()
    kind = data.get("kind")
    with db.transaction():
        if cid:
            row = db.one("SELECT * FROM payment_channels WHERE id=? AND org_id=?", (cid, ctx.org_id))
            if not row:
                raise NotFound("غير موجود")
            db.execute("UPDATE payment_channels SET name=?, is_active=? WHERE id=?",
                       (name or row["name"], int(bool(data.get("is_active", row["is_active"]))), cid))
        else:
            if kind not in ("card", "transfer") or len(name) < 2:
                raise ValidationFailed("اكتب الاسم واختار النوع")
            try:
                db.execute("INSERT INTO payment_channels (id, org_id, kind, name, sort) VALUES (?,?,?,?,"
                           " (SELECT coalesce(max(sort),0)+1 FROM payment_channels WHERE org_id=?))",
                           (new_id(), ctx.org_id, kind, name, ctx.org_id))
            except Exception as e:
                if "UNIQUE" in str(e):
                    raise Conflict("موجود بالفعل") from e
                raise
        audit.record(db, ctx, "payment_channel.save", "payment_channel", cid, after=data)
    return listing(db, ctx)
