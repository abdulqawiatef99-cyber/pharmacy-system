"""Register configuration and server-side browser identity."""
from __future__ import annotations

from dataclasses import replace
import secrets
from sqlite3 import Row

from ..core import audit, security
from ..core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from ..core.permissions import APPROVER_ROLES, Context
from ..db import Database

COOKIE = "pharm_register"
COOKIE_AGE = 365 * 24 * 60 * 60
UNBOUND = "الجهاز ده مش متربط بدرج — اربطه من الإعدادات"
PUBLIC_COLUMNS = "id, name, receipt_printer, label_printer, drawer_kick, active"


def can_manage(ctx: Context) -> bool:
    return ctx.role_code in APPROVER_ROLES and ctx.auth_method == "password"


def _require_manager(ctx: Context) -> None:
    if not can_manage(ctx):
        raise Forbidden("ربط الأجهزة وتعديل الأدراج للمالك أو المدير")


def for_request(db: Database, ctx: Context, token: str | None, *, local: bool) -> Context:
    if token is not None:
        digest = security.token_hash(token) if len(token) == 43 and token.isascii() else ""
        row = db.one("SELECT id FROM registers WHERE org_id=? AND branch_id=? AND device_hash=? AND active=1",
                     (ctx.org_id, ctx.branch_id, digest))
        return replace(ctx, register_id=row["id"] if row else None, register_device_hash=digest)
    if local:
        legacy = replace(ctx, register_legacy_local=True)
        if current(db, legacy):
            return legacy
    return replace(ctx, register_id=None)


def current(db: Database, ctx: Context, branch_id: str | None = None) -> Row | None:
    branch = branch_id or ctx.branch_id
    if ctx.register_id is None or branch != ctx.branch_id:
        return None
    sql = f"SELECT {PUBLIC_COLUMNS} FROM registers WHERE org_id=? AND branch_id=? AND id=? AND active=1"
    params = (ctx.org_id, branch, ctx.register_id)
    if ctx.register_device_hash is not None:
        sql += " AND device_hash=?"
        params += (ctx.register_device_hash,)
    elif ctx.register_legacy_local:
        sql += " AND legacy_local=1"
    return db.one(sql, params)


def require_current(db: Database, ctx: Context, branch_id: str | None = None) -> Row:
    row = current(db, ctx, branch_id)
    if not row:
        raise Conflict(UNBOUND, code="device_unbound")
    return row


def status(db: Database, ctx: Context) -> dict:
    bound = current(db, ctx)
    manager = can_manage(ctx)
    rows = db.all(f"SELECT {PUBLIC_COLUMNS} FROM registers WHERE org_id=? AND branch_id=? ORDER BY id",
                  (ctx.org_id, ctx.branch_id)) if manager else []
    return {"current": dict(bound) if bound else None, "can_manage": manager, "registers": [dict(r) for r in rows]}


def _get(db: Database, ctx: Context, rid: int) -> Row:
    row = db.one(f"SELECT {PUBLIC_COLUMNS} FROM registers WHERE org_id=? AND branch_id=? AND id=?",
                 (ctx.org_id, ctx.branch_id, rid))
    if not row:
        raise NotFound("الدرج غير موجود")
    return row


def independent_drawer_printer(db: Database, ctx: Context, rid: int, target: str | None) -> bool:
    """A secondary drawer pulse needs a dedicated queue; receipt printing may share one."""
    from ..printing.service import printer_target

    if not target or target == "browser":
        return False
    fallback = printer_target(db, ctx.org_id)
    others = db.all("SELECT id, receipt_printer FROM registers WHERE org_id=? AND branch_id=? AND id<>? AND active=1",
                    (ctx.org_id, ctx.branch_id, rid))
    return not any(target.casefold() == (r["receipt_printer"] or fallback).casefold() for r in others)


def save(db: Database, ctx: Context, data: dict, rid: int | None = None) -> dict:
    from ..printing.settings import check_target

    _require_manager(ctx)
    if not ctx.branch_id:
        raise ValidationFailed("مفيش فرع متاح")
    with db.transaction():
        before = dict(_get(db, ctx, rid)) if rid is not None else None
        fields = dict(before or {"name": "", "active": 1, "receipt_printer": None,
                                 "label_printer": None, "drawer_kick": False})
        if "name" in data:
            if not isinstance(data["name"], str):
                raise ValidationFailed("اكتب اسم الدرج")
            fields["name"] = data["name"].strip()
        if not 1 <= len(fields["name"]) <= 80:
            raise ValidationFailed("اسم الدرج من حرف واحد لـ ٨٠ حرف")
        for key in ("receipt_printer", "label_printer"):
            if key in data:
                fields[key] = check_target(data[key], allow_browser=key == "receipt_printer") or None
        for key in ("drawer_kick", "active"):
            if key in data:
                value = data[key]
                if type(value) is not bool and not (key == "drawer_kick" and value is None):
                    raise ValidationFailed("اختيار غير صحيح")
                fields[key] = value
        if before is None:
            rid = db.one("SELECT coalesce(max(id),0)+1 id FROM registers WHERE org_id=? AND branch_id=?",
                         (ctx.org_id, ctx.branch_id))["id"]
        if rid != 1 and fields["active"] and fields["drawer_kick"]:
            if not independent_drawer_printer(db, ctx, rid, fields["receipt_printer"]):
                raise ValidationFailed("اختار طابعة إيصال خاصة بالدرج ده قبل تشغيل فتح الدرج",
                                       code="drawer_needs_own_printer")
        if before is None:
            db.execute("INSERT INTO registers (org_id, branch_id, id, name, receipt_printer, label_printer,"
                       " drawer_kick, active) VALUES (?,?,?,?,?,?,?,?)",
                       (ctx.org_id, ctx.branch_id, rid, fields["name"], fields["receipt_printer"],
                        fields["label_printer"], fields["drawer_kick"], fields["active"]))
        else:
            if not fields["active"] and db.one(
                "SELECT 1 FROM cash_shifts WHERE org_id=? AND branch_id=? AND register_id=? AND status='open'",
                (ctx.org_id, ctx.branch_id, rid),
            ):
                raise Conflict("اقفل شيفت الدرج قبل إيقافه", code="register_shift_open")
            db.execute("UPDATE registers SET name=?, receipt_printer=?, label_printer=?, drawer_kick=?, active=?,"
                       " device_hash=CASE WHEN ? THEN device_hash END,"
                       " legacy_local=CASE WHEN ? THEN legacy_local ELSE 0 END"
                       " WHERE org_id=? AND branch_id=? AND id=?",
                       (fields["name"], fields["receipt_printer"], fields["label_printer"], fields["drawer_kick"],
                        fields["active"], fields["active"], fields["active"], ctx.org_id, ctx.branch_id, rid))
        after = dict(_get(db, ctx, rid))
        audit.record(db, ctx, "register.save", "register", str(rid), before=before, after=after)
        return after


def bind(db: Database, ctx: Context, rid: int) -> tuple[dict, str]:
    _require_manager(ctx)
    token = secrets.token_urlsafe(32)
    with db.transaction():
        row = _get(db, ctx, rid)
        if not row["active"]:
            raise Conflict("الدرج متوقف", code="register_inactive")
        if ctx.register_device_hash:
            db.execute("UPDATE registers SET device_hash=NULL WHERE org_id=? AND branch_id=? AND device_hash=?",
                       (ctx.org_id, ctx.branch_id, ctx.register_device_hash))
        db.execute("UPDATE registers SET device_hash=?, legacy_local=0 WHERE org_id=? AND branch_id=? AND id=?",
                   (security.token_hash(token), ctx.org_id, ctx.branch_id, rid))
        audit.record(db, ctx, "register.bind", "register", str(rid), after={"name": row["name"]})
    return dict(row), token


def replay_scope(ctx: Context) -> bytes:
    """Keep existing keys for register 1. Rebinding never returns another register's result."""
    if ctx.register_id == 1:
        return b""
    return f"\0register:{ctx.branch_id}:{ctx.register_id}".encode()


def alert_suffix(db: Database, ctx: Context, branch_id: str) -> str:
    row = current(db, ctx, branch_id)
    return f" · الدرج: {row['name']}" if row else ""
