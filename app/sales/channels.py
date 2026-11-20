"""Card machines and transfer wallets (إنستاباي، فودافون كاش، ...) the pharmacy accepts."""
from __future__ import annotations

from ..core import audit
from ..core.errors import Conflict, NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id

DEFAULTS = [("card", "جهاز البطاقات"), ("transfer", "إنستاباي"), ("transfer", "فودافون كاش")]


def ensure_defaults(db: Database) -> None:
    for o in db.all("SELECT id FROM organizations"):
        if db.one("SELECT 1 FROM payment_channels WHERE org_id=? LIMIT 1", (o["id"],)):
            continue
        with db.transaction():
            for i, (k, n) in enumerate(DEFAULTS):
                db.execute("INSERT INTO payment_channels (id, org_id, kind, name, sort) VALUES (?,?,?,?,?)",
                           (new_id(), o["id"], k, n, i))


GROUPS = {"card": "فيزا", "transfer": "محفظة إلكترونية"}      # how the cashier sees them: kind, then the name


def listing(db: Database, ctx: Context) -> list[dict]:
    """Payment methods grouped by kind (owner's design 19/11: «فيزا» -> PayMob / Aman, «محفظة إلكترونية» ->
    InstaPay / Vodafone Cash / Etisalat Cash); each one keeps its own money account (F105 route)."""
    ctx.require("sales.create")
    rows = [dict(r) for r in db.all(
        "SELECT c.*, r.account_id, a.name account FROM payment_channels c LEFT JOIN money_routes r ON r.org_id=c.org_id"
        " AND r.method=c.kind AND r.channel_id=c.id LEFT JOIN money_accounts a ON a.id=r.account_id AND a.org_id=c.org_id"
        " WHERE c.org_id=? ORDER BY c.kind, c.sort, c.name", (ctx.org_id,))]
    for r in rows:
        r["group"] = GROUPS.get(r["kind"], r["kind"])
        if not ctx.can("cash.see_expected"):
            r.pop("account", None)
    return rows


def save(db: Database, ctx: Context, data: dict, cid: str | None = None) -> list[dict]:
    ctx.require("settings.manage")
    name = str(data.get("name", "")).strip()
    kind = data.get("kind")
    if (cid and "name" in data or not cid) and len(name) < 2:
        raise ValidationFailed("اكتب الاسم واختر النوع")
    with db.transaction():
        try:
            if cid:
                row = db.one("SELECT * FROM payment_channels WHERE id=? AND org_id=?", (cid, ctx.org_id))
                if not row:
                    raise NotFound("غير موجود")
                new_kind = kind if kind in ("card", "transfer") else row["kind"]       # regroup
                db.execute("UPDATE payment_channels SET name=?, is_active=?, kind=? WHERE id=?",
                           (name or row["name"], int(bool(data.get("is_active", row["is_active"]))), new_kind, cid))
                if new_kind != row["kind"]:            # its money route follows it to the new group
                    db.execute("UPDATE money_routes SET method=? WHERE org_id=? AND channel_id=?",
                               (new_kind, ctx.org_id, cid))
            else:
                if kind not in ("card", "transfer"):
                    raise ValidationFailed("اكتب الاسم واختر النوع")
                cid = new_id()
                db.execute("INSERT INTO payment_channels (id, org_id, kind, name, sort) VALUES (?,?,?,?,"
                           " (SELECT coalesce(max(sort),0)+1 FROM payment_channels WHERE org_id=?))",
                           (cid, ctx.org_id, kind, name, ctx.org_id))
        except Exception as e:
            if "UNIQUE" in str(e):                       # a new one, or a rename onto an existing name
                raise Conflict("موجود بالفعل") from e
            raise
        audit.record(db, ctx, "payment_channel.save", "payment_channel", cid, after=data)
        if "account_id" in data:                     # the money account this method's money lands in
            from ..finance import money
            k = db.one("SELECT kind FROM payment_channels WHERE id=? AND org_id=?", (cid, ctx.org_id))["kind"]
            money.set_route(db, ctx, k, cid, data.get("account_id") or None)
    return listing(db, ctx)
