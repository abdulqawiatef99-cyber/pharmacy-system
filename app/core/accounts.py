"""Organizations, users, memberships, sessions, entitlements."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from ..db import Database, new_id
from . import audit, security
from .errors import Conflict, Forbidden, NotFound, Unauthorized, ValidationFailed
from .permissions import DEFAULT_ROLES, PERMISSIONS, Context

SESSION_TTL = timedelta(hours=12)
SESSION_IDLE = timedelta(hours=2)
MAX_FAILED_LOGINS = 5
LOCKOUT = timedelta(minutes=15)

DEFAULT_FEATURES = ("catalog", "purchasing", "inventory", "sales", "counting", "procurement",
                    "recalls", "safety", "dataimport")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------
def create_organization(db: Database, *, name: str, org_type: str = "pharmacy",
                        branch_name: str = "الفرع الرئيسي", owner_username: str,
                        owner_full_name: str, owner_password: str) -> dict:
    """Bootstrap a tenant: org + main branch + default roles + owner user."""
    problems = security.check_password_policy(owner_password)
    if problems:
        raise ValidationFailed("؛ ".join(problems))
    with db.transaction():
        org_id, branch_id = new_id(), new_id()
        db.execute("INSERT INTO organizations (id, org_type, name) VALUES (?,?,?)",
                   (org_id, org_type, name))
        db.execute("INSERT INTO branches (id, org_id, name) VALUES (?,?,?)",
                   (branch_id, org_id, branch_name))
        role_ids = {}
        for code, (role_name, perms) in DEFAULT_ROLES.items():
            rid = new_id()
            role_ids[code] = rid
            db.execute("INSERT INTO roles (id, org_id, code, name, permissions, is_system)"
                       " VALUES (?,?,?,?,?,1)", (rid, org_id, code, role_name, json.dumps(perms)))
        for feature in DEFAULT_FEATURES:
            db.execute("INSERT INTO org_entitlements (org_id, feature) VALUES (?,?)",
                       (org_id, feature))
        user_id = _insert_user(db, owner_username, owner_full_name, owner_password)
        db.execute("INSERT INTO memberships (id, user_id, org_id, role_id) VALUES (?,?,?,?)",
                   (new_id(), user_id, org_id, role_ids["owner"]))
        audit.record(db, None, "org.create", "organization", org_id,
                     after={"name": name, "type": org_type}, org_id=org_id, user_id=user_id)
    return {"org_id": org_id, "branch_id": branch_id, "owner_user_id": user_id,
            "role_ids": role_ids}


def _insert_user(db: Database, username: str, full_name: str, password: str) -> str:
    username = username.strip()
    if not username or len(username) < 3:
        raise ValidationFailed("اسم المستخدم قصير")
    if db.one("SELECT 1 FROM users WHERE username=?", (username,)):
        raise Conflict("اسم المستخدم مستخدم بالفعل")
    uid = new_id()
    db.execute("INSERT INTO users (id, username, full_name, password_hash) VALUES (?,?,?,?)",
               (uid, username, full_name.strip(), security.hash_password(password)))
    return uid


def add_user(db: Database, ctx: Context, *, username: str, full_name: str, password: str,
             role_code: str, branch_id: str | None = None) -> str:
    ctx.require("users.manage")
    problems = security.check_password_policy(password)
    if problems:
        raise ValidationFailed("؛ ".join(problems))
    with db.transaction():
        role = db.one("SELECT id FROM roles WHERE org_id=? AND code=?", (ctx.org_id, role_code))
        if not role:
            raise NotFound("الدور غير موجود")
        if branch_id and not db.one("SELECT 1 FROM branches WHERE id=? AND org_id=?",
                                    (branch_id, ctx.org_id)):
            raise NotFound("الفرع غير موجود")
        uid = _insert_user(db, username, full_name, password)
        db.execute("INSERT INTO memberships (id, user_id, org_id, role_id, branch_id)"
                   " VALUES (?,?,?,?,?)", (new_id(), uid, ctx.org_id, role["id"], branch_id))
        audit.record(db, ctx, "user.create", "user", uid,
                     after={"username": username, "role": role_code, "branch_id": branch_id})
    return uid


# --------------------------------------------------------------------------
def login(db: Database, username: str, password: str, *, org_id: str | None = None,
          ip: str | None = None, user_agent: str | None = None) -> dict:
    """Returns {'token', 'expires_at', 'org_id'}. Uniform error message for
    unknown user / wrong password; temporary lockout after repeated failures."""
    generic = Unauthorized("اسم المستخدم أو كلمة المرور غير صحيحة", code="invalid_credentials")
    user = db.one("SELECT * FROM users WHERE username=?", (username.strip(),))
    if user is None:
        security.dummy_verify(password)
        raise generic
    now = utcnow()
    if user["locked_until"] and parse_iso(user["locked_until"]) > now:
        raise Unauthorized("الحساب مقفول مؤقتًا بسبب محاولات خاطئة متكررة، حاول بعد قليل",
                           code="locked")
    if not security.verify_password(password, user["password_hash"]):
        with db.transaction():
            failed = user["failed_logins"] + 1
            locked = iso(now + LOCKOUT) if failed >= MAX_FAILED_LOGINS else None
            db.execute("UPDATE users SET failed_logins=?, locked_until=? WHERE id=?",
                       (0 if locked else failed, locked, user["id"]))
            audit.record(db, None, "auth.login_failed", "user", user["id"],
                         after={"ip": ip, "locked": bool(locked)}, user_id=user["id"])
        raise generic
    if not user["is_active"]:
        raise generic

    memberships = db.all(
        "SELECT m.org_id FROM memberships m JOIN organizations o ON o.id=m.org_id"
        " WHERE m.user_id=? AND m.is_active=1 AND o.status='active'", (user["id"],))
    orgs = [m["org_id"] for m in memberships]
    if not orgs:
        raise Forbidden("المستخدم غير مرتبط بأي صيدلية فعالة")
    if org_id is None:
        if len(orgs) > 1:
            raise ValidationFailed("اختر الصيدلية", code="org_required", details={"orgs": orgs})
        org_id = orgs[0]
    elif org_id not in orgs:
        raise generic

    token = security.new_token()
    expires = now + SESSION_TTL
    with db.transaction():
        updates = {"failed_logins": 0, "locked_until": None}
        if security.needs_rehash(user["password_hash"]):
            updates["password_hash"] = security.hash_password(password)
        sets = ", ".join(f"{k}=?" for k in updates)
        db.execute(f"UPDATE users SET {sets} WHERE id=?", (*updates.values(), user["id"]))
        db.execute("INSERT INTO sessions (id, token_hash, user_id, org_id, created_at, expires_at,"
                   " last_seen_at, ip, user_agent) VALUES (?,?,?,?,?,?,?,?,?)",
                   (new_id(), security.token_hash(token), user["id"], org_id, iso(now),
                    iso(expires), iso(now), ip, (user_agent or "")[:200]))
        audit.record(db, None, "auth.login", "user", user["id"], after={"ip": ip},
                     org_id=org_id, user_id=user["id"])
    return {"token": token, "expires_at": iso(expires), "org_id": org_id}


def resolve_session(db: Database, token: str, ip: str | None = None) -> Context:
    if not token:
        raise Unauthorized("سجّل الدخول أولًا")
    row = db.one(
        "SELECT s.*, u.is_active AS user_active, m.role_id, m.branch_id, m.is_active AS m_active,"
        " o.status AS org_status FROM sessions s"
        " JOIN users u ON u.id=s.user_id"
        " JOIN memberships m ON m.user_id=s.user_id AND m.org_id=s.org_id"
        " JOIN organizations o ON o.id=s.org_id"
        " WHERE s.token_hash=?", (security.token_hash(token),))
    now = utcnow()
    if (row is None or row["revoked_at"] or parse_iso(row["expires_at"]) <= now
            or parse_iso(row["last_seen_at"]) + SESSION_IDLE <= now
            or not row["user_active"] or not row["m_active"] or row["org_status"] != "active"):
        raise Unauthorized("انتهت الجلسة، سجّل الدخول مرة أخرى", code="session_expired")
    role = db.one("SELECT permissions FROM roles WHERE id=?", (row["role_id"],))
    perms = frozenset(p for p in json.loads(role["permissions"]) if p in PERMISSIONS)
    branch_id = row["branch_id"]
    if branch_id is None:
        b = db.one("SELECT id FROM branches WHERE org_id=? AND is_active=1 ORDER BY created_at LIMIT 1",
                   (row["org_id"],))
        branch_id = b["id"] if b else None
    # throttle last_seen writes to once a minute
    if parse_iso(row["last_seen_at"]) + timedelta(minutes=1) <= now:
        with db.transaction():
            db.execute("UPDATE sessions SET last_seen_at=? WHERE id=?", (iso(now), row["id"]))
    return Context(user_id=row["user_id"], org_id=row["org_id"], branch_id=branch_id,
                   permissions=perms, ip=ip)


def logout(db: Database, token: str) -> None:
    with db.transaction():
        db.execute("UPDATE sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                   (iso(utcnow()), security.token_hash(token)))


def change_password(db: Database, ctx: Context, old: str, new: str) -> None:
    user = db.one("SELECT * FROM users WHERE id=?", (ctx.user_id,))
    if not security.verify_password(old, user["password_hash"]):
        raise ValidationFailed("كلمة المرور الحالية غير صحيحة")
    problems = security.check_password_policy(new)
    if problems:
        raise ValidationFailed("؛ ".join(problems))
    with db.transaction():
        db.execute("UPDATE users SET password_hash=?, password_changed_at=? WHERE id=?",
                   (security.hash_password(new), iso(utcnow()), ctx.user_id))
        # revoke every other session of this user
        db.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                   (iso(utcnow()), ctx.user_id))
        audit.record(db, ctx, "user.password_change", "user", ctx.user_id)


def me(db: Database, ctx: Context) -> dict:
    u = db.one("SELECT id, username, full_name FROM users WHERE id=?", (ctx.user_id,))
    o = db.one("SELECT id, name, org_type FROM organizations WHERE id=?", (ctx.org_id,))
    return {"user": dict(u), "organization": dict(o), "branch_id": ctx.branch_id,
            "permissions": sorted(ctx.permissions)}


# --------------------------------------------------------------------------
def require_feature(db: Database, ctx: Context, feature: str) -> dict:
    row = db.one("SELECT * FROM org_entitlements WHERE org_id=? AND feature=?",
                 (ctx.org_id, feature))
    if not row or not row["enabled"] or (
            row["valid_until"] and row["valid_until"] < iso(utcnow())):
        raise Forbidden("هذه الأداة غير مفعّلة في اشتراكك", code="feature_disabled",
                        details={"feature": feature})
    return json.loads(row["limits_json"])


def check_usage(db: Database, ctx: Context, feature: str, amount: int = 1) -> None:
    """Raise if using `amount` more would exceed the monthly limit (no write)."""
    limits = require_feature(db, ctx, feature)
    monthly = limits.get("monthly")
    if monthly is None:
        return
    row = db.one("SELECT used FROM usage_counters WHERE org_id=? AND feature=? AND period=?",
                 (ctx.org_id, feature, utcnow().strftime("%Y-%m")))
    if (row["used"] if row else 0) + amount > monthly:
        raise Forbidden("وصلت للحد الشهري لهذه الخدمة", code="usage_limit",
                        details={"feature": feature, "limit": monthly})


def consume_usage(db: Database, ctx: Context, feature: str, amount: int = 1) -> int:
    """Atomically count usage of a metered feature (e.g. AI extractions)
    against its monthly limit. Raises Forbidden when the limit would be exceeded."""
    limits = require_feature(db, ctx, feature)
    period = utcnow().strftime("%Y-%m")
    with db.transaction():
        row = db.one("SELECT used FROM usage_counters WHERE org_id=? AND feature=? AND period=?",
                     (ctx.org_id, feature, period))
        used = (row["used"] if row else 0) + amount
        monthly = limits.get("monthly")
        if monthly is not None and used > monthly:
            raise Forbidden("وصلت للحد الشهري لهذه الخدمة", code="usage_limit",
                            details={"feature": feature, "limit": monthly})
        db.execute("INSERT INTO usage_counters (org_id, feature, period, used) VALUES (?,?,?,?)"
                   " ON CONFLICT(org_id, feature, period) DO UPDATE SET used=excluded.used",
                   (ctx.org_id, feature, period, used))
    return used


def set_entitlement(db: Database, org_id: str, feature: str, *, enabled: bool = True,
                    limits: dict | None = None, operator: str = "platform") -> None:
    """Platform-operator only (CLI / future admin console). Deliberately takes
    no tenant Context: a pharmacy owner must never be able to grant himself
    features or raise his own limits. Not exposed through the tenant API."""
    with db.transaction():
        db.execute("INSERT INTO org_entitlements (org_id, feature, enabled, limits_json)"
                   " VALUES (?,?,?,?) ON CONFLICT(org_id, feature) DO UPDATE SET"
                   " enabled=excluded.enabled, limits_json=excluded.limits_json",
                   (org_id, feature, int(enabled), json.dumps(limits or {})))
        audit.record(db, None, "entitlement.set", "entitlement", feature,
                     after={"enabled": enabled, "limits": limits, "operator": operator},
                     org_id=org_id)
