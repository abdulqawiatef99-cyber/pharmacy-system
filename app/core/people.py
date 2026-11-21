"""People of a pharmacy: the owner manages users, their role, per-person
exceptions, discount limits and PINs. PINs are for quick login on the pharmacy
PC and for approving an action on someone else's screen."""
from __future__ import annotations

import json
from datetime import timedelta

from ..db import Database, new_id
from . import accounts, audit, security
from .errors import Conflict, Forbidden, NotFound, Unauthorized, ValidationFailed
from .numbers import whole_number
from .permissions import (APPROVER_ROLES, DEFAULT_ROLES, GROUPS, PERMISSIONS, ROLE_ORDER, Context)

MAX_PIN_FAILS = 5
PIN_LOCKOUT = timedelta(minutes=15)
WEAK_PINS = {"1234", "12345", "123456", "4321", "54321", "654321", "1212", "2580", "0852"}


def _member(db: Database, ctx: Context, user_id: str) -> dict:
    m = db.one("SELECT m.*, r.code role_code, r.name role_name, u.username, u.full_name, u.is_active"
               " u_active, u.pin_hash FROM memberships m JOIN roles r ON r.id=m.role_id"
               " JOIN users u ON u.id=m.user_id WHERE m.org_id=? AND m.user_id=?", (ctx.org_id, user_id))
    if not m:
        raise NotFound("المستخدم غير موجود")
    return dict(m)


def _check_pin_format(pin: str) -> None:
    if not pin.isdigit() or not 4 <= len(pin) <= 6:
        raise ValidationFailed("الرقم السري من 4 إلى 6 أرقام")
    if len(set(pin)) == 1 or pin in WEAK_PINS:
        raise ValidationFailed("هذا الرقم السري سهل التخمين، اختر غيره")


# -------------------------------------------------------------------------- PIN
def set_pin(db: Database, ctx: Context, user_id: str, pin: str) -> None:
    if user_id != ctx.user_id:
        ctx.require("users.manage")
    pin = str(pin or "").strip()
    _check_pin_format(pin)
    if _member(db, ctx, user_id)["role_code"] == "owner" and user_id != ctx.user_id:
        raise Forbidden("الرقم السري للمالك يغيّره هو فقط", code="owner_locked")   # #131
    with db.transaction():
        db.execute("UPDATE users SET pin_hash=?, pin_failed=0, pin_locked_until=NULL WHERE id=?",
                   (security.hash_password(pin), user_id))
        audit.record(db, ctx, "user.pin_set", "user", user_id)


def verify_pin(db: Database, user_id: str, pin: str, *, org_id: str) -> dict:
    """Raises on a wrong PIN; 5 wrong tries lock the PIN for 15 minutes."""
    u = db.one("SELECT u.* FROM users u JOIN memberships m ON m.user_id=u.id AND m.org_id=?"
               " WHERE u.id=? AND u.is_active=1 AND m.is_active=1", (org_id, user_id))
    generic = Unauthorized("الرقم السري غير صحيح", code="invalid_pin")
    if u is None or not u["pin_hash"]:
        security.dummy_verify(str(pin))
        raise generic
    now = accounts.utcnow()
    if u["pin_locked_until"] and accounts.parse_iso(u["pin_locked_until"]) > now:
        raise Unauthorized("أُوقف الرقم السري مؤقتًا بسبب محاولات خاطئة، انتظر ربع ساعة", code="pin_locked")
    if not security.verify_password(str(pin), u["pin_hash"]):
        with db.transaction():
            failed = u["pin_failed"] + 1
            locked = accounts.iso(now + PIN_LOCKOUT) if failed >= MAX_PIN_FAILS else None
            db.execute("UPDATE users SET pin_failed=?, pin_locked_until=? WHERE id=?",
                       (0 if locked else failed, locked, user_id))
            audit.record(db, None, "auth.pin_failed", "user", user_id, after={"locked": bool(locked)},
                         org_id=org_id, user_id=user_id)
        raise generic
    if u["pin_failed"]:
        with db.transaction():
            db.execute("UPDATE users SET pin_failed=0 WHERE id=?", (user_id,))
    return dict(u)


def pin_login(db: Database, org_id: str, user_id: str, pin: str, *, ip: str | None = None,
              user_agent: str | None = None) -> dict:
    verify_pin(db, user_id, pin, org_id=org_id)
    org = db.one("SELECT status FROM organizations WHERE id=?", (org_id,))
    if not org or org["status"] != "active":
        raise Unauthorized("الرقم السري غير صحيح", code="invalid_pin")
    with db.transaction():
        return accounts._new_session(db, user_id, org_id, ip, user_agent, "auth.pin_login")


def quick_users(db: Database) -> list[dict]:
    """Names shown on the quick-login screen of the pharmacy PC (single pharmacy only)."""
    orgs = db.all("SELECT id FROM organizations WHERE status='active'")
    if len(orgs) != 1:
        return []
    return [dict(r) for r in db.all(
        "SELECT u.id, u.full_name, ? org_id FROM users u JOIN memberships m ON m.user_id=u.id"
        " WHERE m.org_id=? AND m.is_active=1 AND u.is_active=1 AND u.pin_hash IS NOT NULL"
        " ORDER BY u.full_name", (orgs[0]["id"], orgs[0]["id"]))]


def approvers(db: Database, ctx: Context) -> list[dict]:
    """Who can approve on this screen: active owner/managers with a PIN, except me."""
    marks = ",".join("?" * len(APPROVER_ROLES))
    return [dict(r) for r in db.all(
        "SELECT u.id, u.full_name, r.name role_name FROM memberships m JOIN users u ON u.id=m.user_id"
        f" JOIN roles r ON r.id=m.role_id WHERE m.org_id=? AND r.code IN ({marks}) AND m.is_active=1"
        " AND u.is_active=1 AND u.pin_hash IS NOT NULL AND u.id<>? ORDER BY r.code='owner' DESC, u.full_name",
        (ctx.org_id, *APPROVER_ROLES, ctx.user_id))]


# -------------------------------------------------------------------------- owner: users screen
def _see_people(ctx: Context) -> None:
    """The users screen: the owner (users.manage), or someone who switches staff permissions (part 3, A)."""
    if not (ctx.allowed("users.manage") or ctx.allowed("users.permissions")):
        ctx.require("users.manage")           # the approval box asks for the owner's right, as before


def roles(db: Database, ctx: Context) -> list[dict]:
    _see_people(ctx)
    rows = {r["code"]: dict(r) for r in db.all(
        "SELECT code, name, permissions, max_discount_bp FROM roles WHERE org_id=?", (ctx.org_id,))}
    out = []
    for code in ROLE_ORDER:
        if code in rows:
            r = rows[code]
            r["permissions"] = sorted(PERMISSIONS) if code == "owner" else json.loads(r["permissions"])
            out.append(r)
    return out


def list_users(db: Database, ctx: Context) -> list[dict]:
    _see_people(ctx)
    out = []
    for m in db.all("SELECT m.user_id id, u.username, u.full_name, u.is_active AND m.is_active active,"
                    " r.code role, r.name role_name, m.perm_grants, m.perm_denies, m.max_discount_bp,"
                    " u.pin_hash IS NOT NULL has_pin FROM memberships m JOIN users u ON u.id=m.user_id"
                    " JOIN roles r ON r.id=m.role_id WHERE m.org_id=?", (ctx.org_id,)):
        d = dict(m)
        d["changes"] = len(json.loads(d.pop("perm_grants"))) + len(json.loads(d.pop("perm_denies"))) + \
            (d["max_discount_bp"] is not None)
        d["active"], d["has_pin"] = bool(d["active"]), bool(d["has_pin"])
        out.append(d)
    out.sort(key=lambda d: (ROLE_ORDER.index(d["role"]) if d["role"] in ROLE_ORDER else 9, d["full_name"]))
    return out


def access_matrix(db: Database, ctx: Context, user_id: str) -> dict:
    """Every permission with: role default, this person's value, and whether it is an exception."""
    _see_people(ctx)
    m = _member(db, ctx, user_id)
    role_perms = {r["code"]: set(r["permissions"]) for r in roles(db, ctx)}
    grants, denies = set(json.loads(m["perm_grants"])), set(json.loads(m["perm_denies"]))
    eff = accounts.effective_access(db, ctx.org_id, user_id)
    groups = []
    for title, codes in GROUPS:
        groups.append({"title": title, "items": [{
            "code": c, "label": PERMISSIONS[c],
            "roles": {rc: c in ps for rc, ps in role_perms.items()},
            "value": c in eff["permissions"],
            "exception": None if m["role_code"] == "owner" else
            ("grant" if c in grants else "deny" if c in denies else None)} for c in codes]})
    r_disc = {r["code"]: r["max_discount_bp"] for r in roles(db, ctx)}
    return {"user": {"id": user_id, "full_name": m["full_name"], "username": m["username"],
                     "role": m["role_code"], "role_name": m["role_name"],
                     "active": bool(m["u_active"] and m["is_active"]), "has_pin": bool(m["pin_hash"])},
            "groups": groups, "role_discount_bp": r_disc,
            "max_discount_bp": m["max_discount_bp"], "effective_discount_bp": eff["max_discount_bp"]}


def _guard_owner(m: dict) -> None:
    if m["role_code"] == "owner":
        raise Forbidden("للمالك كل الصلاحيات ولا يستطيع أحد تغييرها", code="owner_locked")


def set_exception(db: Database, ctx: Context, user_id: str, perm: str, mode: str) -> dict:
    """mode: 'grant' (open for this person), 'deny' (close for this person), 'default'.
    Every permission can be switched per person; the role is only the starting template (owner, part 3 A).
    The owner (users.manage) switches anything for anyone but himself. Someone with users.permissions (the
    manager by default) switches a staff member's permissions, not his own, not the owner's or another
    manager's, never the two people rights, and opens only what he holds himself."""
    if perm not in PERMISSIONS or mode not in ("grant", "deny", "default"):
        raise ValidationFailed("بيانات غير صحيحة")
    if perm == "users.manage" and mode == "grant":
        raise Forbidden("إدارة المستخدمين للمالك فقط", code="owner_only")
    m = _member(db, ctx, user_id)
    _guard_owner(m)
    if not ctx.allowed("users.manage"):
        ctx.require("users.permissions")
        if user_id == ctx.user_id:
            raise Forbidden("مينفعش تغيّر صلاحياتك بنفسك", code="self_permissions")
        if m["role_code"] in APPROVER_ROLES:
            raise Forbidden("صلاحيات المدير يغيّرها المالك بس", code="owner_only")
        if perm in ("users.manage", "users.permissions"):
            raise Forbidden("الصلاحية دي يغيّرها المالك بس", code="owner_only")
        role_has_it = perm in set(json.loads(db.one("SELECT permissions FROM roles WHERE id=? AND org_id=?",
                                                     (m["role_id"], ctx.org_id))["permissions"]))
        opens = mode == "grant" or (mode == "default" and role_has_it)     # 'default' can lift a deny (Codex P1 on #396)
        if opens and not ctx.can(perm):
            raise Forbidden("مينفعش تفتح صلاحية إنت نفسك معندكش", code="beyond_own")
    grants, denies = set(json.loads(m["perm_grants"])), set(json.loads(m["perm_denies"]))
    grants.discard(perm)
    denies.discard(perm)
    role_has = perm in set(json.loads(db.one("SELECT permissions FROM roles WHERE id=? AND org_id=?",
                                             (m["role_id"], ctx.org_id))["permissions"]))
    if mode == "grant" and not role_has:
        grants.add(perm)
    elif mode == "deny" and role_has:
        denies.add(perm)
    with db.transaction():
        db.execute("UPDATE memberships SET perm_grants=?, perm_denies=? WHERE id=?",
                   (json.dumps(sorted(grants)), json.dumps(sorted(denies)), m["id"]))
        audit.record(db, ctx, "user.permission", "user", user_id, after={"permission": perm, "mode": mode})
    return access_matrix(db, ctx, user_id)


def reset_to_role(db: Database, ctx: Context, user_id: str) -> dict:
    ctx.require("users.manage")
    m = _member(db, ctx, user_id)
    _guard_owner(m)
    with db.transaction():
        db.execute("UPDATE memberships SET perm_grants='[]', perm_denies='[]', max_discount_bp=NULL"
                   " WHERE id=?", (m["id"],))
        audit.record(db, ctx, "user.reset_permissions", "user", user_id)
    return access_matrix(db, ctx, user_id)


def _bp(v) -> int | None:
    if v is None or v == "":
        return None
    try:
        v = whole_number(v)               # 12.5 or true is refused, never truncated to 12 or 1
    except (TypeError, ValueError):
        raise ValidationFailed("نسبة الخصم غير صحيحة")
    if not 0 <= v <= 10000:
        raise ValidationFailed("نسبة الخصم من 0 إلى 100%")
    return v


def set_discount(db: Database, ctx: Context, user_id: str, max_discount_bp) -> dict:
    """None = back to the role's limit."""
    ctx.require("users.manage")
    m = _member(db, ctx, user_id)
    _guard_owner(m)
    v = _bp(max_discount_bp)
    with db.transaction():
        db.execute("UPDATE memberships SET max_discount_bp=? WHERE id=?", (v, m["id"]))
        audit.record(db, ctx, "user.discount_limit", "user", user_id,
                     before={"max_discount_bp": m["max_discount_bp"]}, after={"max_discount_bp": v})
    return access_matrix(db, ctx, user_id)


def set_role_discount(db: Database, ctx: Context, role_code: str, max_discount_bp) -> None:
    ctx.require("users.manage")
    if role_code == "owner" or role_code not in DEFAULT_ROLES:
        raise ValidationFailed("الدور غير صحيح")
    v = _bp(max_discount_bp)
    if v is None:
        raise ValidationFailed("اكتب النسبة")
    with db.transaction():
        db.execute("UPDATE roles SET max_discount_bp=? WHERE org_id=? AND code=?", (v, ctx.org_id, role_code))
        audit.record(db, ctx, "role.discount_limit", "role", role_code, after={"max_discount_bp": v})


def set_role(db: Database, ctx: Context, user_id: str, role_code: str) -> dict:
    ctx.require("users.manage")
    if role_code == "owner":
        raise Forbidden("للصيدلية مالك واحد فقط", code="one_owner")
    m = _member(db, ctx, user_id)
    _guard_owner(m)
    r = db.one("SELECT id FROM roles WHERE org_id=? AND code=?", (ctx.org_id, role_code))
    if not r:
        raise NotFound("الدور غير موجود")
    with db.transaction():
        # exceptions were relative to the old role: start clean on the new one
        db.execute("UPDATE memberships SET role_id=?, perm_grants='[]', perm_denies='[]',"
                   " max_discount_bp=NULL WHERE id=?", (r["id"], m["id"]))
        audit.record(db, ctx, "user.role", "user", user_id, before={"role": m["role_code"]},
                     after={"role": role_code})
    return access_matrix(db, ctx, user_id)


def set_active(db: Database, ctx: Context, user_id: str, active: bool) -> dict:
    ctx.require("users.manage")
    m = _member(db, ctx, user_id)
    _guard_owner(m)
    with db.transaction():
        db.execute("UPDATE memberships SET is_active=? WHERE id=?", (int(bool(active)), m["id"]))
        if not active:
            db.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND org_id=? AND revoked_at IS NULL",
                       (accounts.iso(accounts.utcnow()), user_id, ctx.org_id))
        audit.record(db, ctx, "user.active", "user", user_id, after={"active": bool(active)})
    return access_matrix(db, ctx, user_id)


def reset_password(db: Database, ctx: Context, user_id: str, password: str) -> None:
    ctx.require("users.manage")
    m = _member(db, ctx, user_id)
    if m["role_code"] == "owner" and user_id != ctx.user_id:
        raise Forbidden("كلمة سر المالك يغيّرها هو فقط", code="owner_locked")
    problems = security.check_password_policy(password)
    if problems:
        raise ValidationFailed("؛ ".join(problems))
    with db.transaction():
        db.execute("UPDATE users SET password_hash=?, password_changed_at=?, failed_logins=0,"
                   " locked_until=NULL WHERE id=?",
                   (security.hash_password(password), accounts.iso(accounts.utcnow()), user_id))
        db.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                   (accounts.iso(accounts.utcnow()), user_id))
        audit.record(db, ctx, "user.password_reset", "user", user_id)


def add_user(db: Database, ctx: Context, *, username: str, full_name: str, password: str,
             role_code: str, pin: str | None = None) -> str:
    if role_code == "owner":
        raise Forbidden("للصيدلية مالك واحد فقط", code="one_owner")
    if pin:
        _check_pin_format(str(pin))
    uid = accounts.add_user(db, ctx, username=username, full_name=full_name, password=password,
                            role_code=role_code)
    if pin:
        set_pin(db, ctx, uid, str(pin))
    return uid


# -------------------------------------------------------------------------- screen preferences
PREF_DEFAULTS = {"theme": "light", "accent": None, "font": "m", "lang": "ar", "names": "both", "help": "on"}
_PREF_CHOICES = {"theme": ("light", "dark", "contrast"), "font": ("s", "m", "l", "xl"),
                 "lang": ("ar", "en"), "names": ("both", "en", "ar"), "help": ("on", "off")}


def _luminance(hex6: str) -> float:
    def ch(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex6[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast_with_white(hex6: str) -> float:
    return round(1.05 / (_luminance(hex6) + 0.05), 2)


def get_prefs(db: Database, user_id: str) -> dict:
    r = db.one("SELECT prefs FROM users WHERE id=?", (user_id,))
    try:
        stored = json.loads(r["prefs"]) if r else {}
    except ValueError:
        stored = {}
    return {**PREF_DEFAULTS, **{k: v for k, v in stored.items() if k in PREF_DEFAULTS}}


def set_prefs(db: Database, ctx: Context, changes: dict) -> dict:
    """Each person's own screen: colors, font size, language, item names. Defaults are the
    recommended setup; 'reset' brings them back."""
    if changes.get("reset"):
        new = dict(PREF_DEFAULTS)
    else:
        new = get_prefs(db, ctx.user_id)
        for k, v in changes.items():
            if k in _PREF_CHOICES:
                if v not in _PREF_CHOICES[k]:
                    raise ValidationFailed("اختيار غير صحيح")
                new[k] = v
            elif k == "accent":
                if v in (None, ""):
                    new["accent"] = None
                    continue
                v = str(v).strip().upper()
                if not (len(v) == 7 and v[0] == "#" and all(c in "0123456789ABCDEF" for c in v[1:])):
                    raise ValidationFailed("اللون غير صحيح")
                cr = contrast_with_white(v)
                if cr < 4.5:
                    raise ValidationFailed(f"هذا اللون فاتح: لن يُقرأ النص الأبيض عليه (التباين {cr} والمطلوب 4.5 على الأقل)",
                                           code="low_contrast", details={"contrast": cr})
                new["accent"] = v
    with db.transaction():
        db.execute("UPDATE users SET prefs=? WHERE id=?", (json.dumps(new), ctx.user_id))
    return new
