"""Money accounts (F105): every place the pharmacy keeps money has a balance in the program.

Places: the shift drawer (app/sales/cash.py, cash_movements), the main treasury (one per pharmacy, made on
first use) and the owner's own accounts (bank / InstaPay, Vodafone Cash, ...). An account's balance is the
sum of its money_movements rows (append-only, integer piasters, + in / - out). The drawer keeps its own
ledger; a payout from the drawer is a cash_movements row as before.

Rules:
- An account never goes below zero unless the owner allowed it for that account.
- Opening balances come from the E-Stock import through post_opening (the old program's figure as it is);
  the first count after it records the real difference as "difference from the old program", and the owner
  may correct an opening without counting (adjust_opening). Neither is money in or out of the month.
- Shift close moves the counted cash minus the float left for the next shift to the treasury, once per shift.
- A payout names its source: the drawer, an account, or (owner only) "outside the program's accounts".
- Card / transfer money goes to the account the owner routed that payment channel to.
- Every write is audited in the same transaction; callers pass their Idempotency-Key at the API.
"""
from __future__ import annotations

import json
from datetime import date

from ..core import audit
from ..core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from ..core.numbers import whole_number
from ..core.permissions import Context
from ..db import Database, new_id

DRAWER = "drawer"
OUTSIDE = "outside"
TREASURY_NAME = "الخزنة الرئيسية"
DRAWER_NAME = "درج الشيفت"
OUTSIDE_LABEL = "من خارج حسابات البرنامج (جيب المالك)"

ACCOUNT_KINDS = {"treasury": "الخزنة الرئيسية", "bank": "حساب بنكي أو إنستاباي", "wallet": "محفظة (فودافون كاش)",
                 "other": "حساب آخر"}

KINDS = {
    "opening": "رصيد افتتاحي",
    "shift_close": "تقفيل شيفت",
    "transfer": "تحويل بين الحسابات",
    "supplier_payment": "دفع لمورد",
    "expense": "مصروف",
    "payroll": "رواتب وسلف",
    "schedule": "مصروف دوري (إيجار وغيره)",
    "tax": "ضرائب ورسوم",
    "owner_withdrawal": "مسحوبات المالك",
    "owner_deposit": "إيداع من المالك",
    "sale": "مبيعات",
    "customer_payment": "تحصيل من عميل",
    "refund": "مرتجع لعميل",
    "count_difference": "فرق جرد الحساب",
    "opening_adjustment": "تسوية الرصيد الافتتاحي",
}
PAYOUT_KINDS = ("supplier_payment", "expense", "payroll", "schedule", "tax", "owner_withdrawal")
INCOME_KINDS = ("sale", "customer_payment", "refund")
METHODS = ("card", "transfer")


# -------------------------------------------------------------------------- helpers
def _amount(v, *, allow_zero: bool = False, signed: bool = False) -> int:
    try:
        n = whole_number(v)
    except (TypeError, ValueError):
        raise ValidationFailed("المبلغ غير صحيح")
    if n < 0 and not signed or n == 0 and not allow_zero:
        raise ValidationFailed("المبلغ غير صحيح")
    return n


def _note(v, required: bool = False, what: str = "اكتب السبب") -> str | None:
    s = (str(v).strip() if v is not None else "")[:200]
    if required and len(s) < 3:
        raise ValidationFailed(what, code="reason_required")
    return s or None


def is_owner(ctx: Context) -> bool:
    """The owner himself, or the owner's PIN typed on this screen (the same test expenses use)."""
    return ctx.is_owner or bool(ctx.approver_id and "users.manage" in ctx.approver_permissions)


def _account(db: Database, ctx: Context, account_id: str, *, active: bool = True):
    a = db.one("SELECT * FROM money_accounts WHERE id=? AND org_id=?", (account_id, ctx.org_id))
    if not a:
        raise NotFound("الحساب غير موجود")
    if active and not a["is_active"]:
        raise Conflict("الحساب موقوف", code="account_inactive")
    return a


def balance(db: Database, org_id: str, account_id: str) -> int:
    return db.one("SELECT coalesce(sum(amount),0) b FROM money_movements WHERE org_id=? AND account_id=?",
                  (org_id, account_id))["b"]


def find_treasury(db: Database, org_id: str) -> str | None:
    """The treasury's id without creating it (reads run inside a read-only snapshot)."""
    r = db.one("SELECT id FROM money_accounts WHERE org_id=? AND kind='treasury'", (org_id,))
    return r["id"] if r else None


def treasury_id(db: Database, ctx: Context) -> str:
    """The pharmacy's main treasury, created (and audited) the first time it is needed."""
    r = db.one("SELECT id FROM money_accounts WHERE org_id=? AND kind='treasury'", (ctx.org_id,))
    if r:
        return r["id"]
    with db.transaction():
        r = db.one("SELECT id FROM money_accounts WHERE org_id=? AND kind='treasury'", (ctx.org_id,))
        if r:
            return r["id"]
        aid = new_id()
        db.execute("INSERT INTO money_accounts (id, org_id, kind, code, name, created_by) VALUES (?,?,?,?,?,?)",
                   (aid, ctx.org_id, "treasury", "treasury", TREASURY_NAME, ctx.user_id))
        audit.record(db, ctx, "money.account_create", "money_account", aid,
                     after={"kind": "treasury", "name": TREASURY_NAME})
        return aid


def _writable(db: Database, ctx: Context, account: str) -> str:
    """An account id for a write: the placeholder 'treasury' makes the treasury on its first write (the
    balances list shows it before it exists); a key or 'estock:<id>' is resolved; anything else is an id."""
    if account == "treasury":
        return treasury_id(db, ctx)
    return resolve(db, ctx, account)


writable = _writable


def _post(db: Database, ctx: Context, account_id: str, amount: int, kind: str, *, ref_type: str | None = None,
          ref_id: str | None = None, note: str | None = None, meta: dict | None = None, check: bool = True,
          active: bool = True) -> str:
    """One movement on an account, inside the caller's transaction. Refuses a balance below zero unless
    the owner allowed it for this account."""
    if kind not in KINDS:
        raise ValueError(f"unknown money movement kind {kind}")
    a = _account(db, ctx, account_id, active=active)     # active=False: only the opening of a stopped account
    if check and amount < 0 and (a["kind"] == "treasury" or not a["allow_negative"]):
        now = balance(db, ctx.org_id, account_id)
        if now + amount < 0:
            raise Conflict(f"رصيد {a['name']} لا يكفي ({now / 100:,.2f} ج)", code="account_negative",
                           details={"account_id": account_id, "balance": now, "amount": -amount})
    mid = new_id()
    db.execute("INSERT INTO money_movements (id, org_id, account_id, amount, kind, ref_type, ref_id, note, meta,"
               " created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (mid, ctx.org_id, account_id, amount, kind, ref_type, ref_id, note,
                json.dumps(meta, ensure_ascii=False, sort_keys=True) if meta else None, ctx.user_id))
    return mid


# -------------------------------------------------------------------------- the drawer as a source
def _drawer_expected(db: Database, ctx: Context, shift_id: str) -> int:
    s = db.one("SELECT opening_cash FROM cash_shifts WHERE id=? AND org_id=?", (shift_id, ctx.org_id))
    m = db.one("SELECT coalesce(sum(amount),0) t FROM cash_movements WHERE shift_id=? AND org_id=?",
               (shift_id, ctx.org_id))
    return s["opening_cash"] + m["t"]


def _register_balance(db: Database, ctx: Context, register) -> dict:
    scope = (ctx.org_id, ctx.branch_id, register["id"])
    row = db.one("SELECT id, status FROM cash_shifts WHERE org_id=? AND branch_id=? AND register_id=?"
                 " AND status='open'", scope)
    if not row:
        row = db.one("SELECT id, status, counted_cash, float_left FROM cash_shifts"
                     " WHERE org_id=? AND branch_id=? AND register_id=? AND status='closed'"
                     " ORDER BY closed_at DESC LIMIT 1", scope)
    opened = bool(row and row["status"] == "open")
    if opened:
        balance = _drawer_expected(db, ctx, row["id"])
    elif row:
        balance = row["float_left"] if row["float_left"] is not None else (row["counted_cash"] or 0)
    else:
        balance = 0
    return {"shift_id": row["id"] if row else None, "open": opened, "balance": balance,
            "register_id": register["id"], "register_name": register["name"], "active": bool(register["active"])}


def drawer_balance(db: Database, ctx: Context) -> dict:
    """The requesting register's open balance, or the float left in its drawer."""
    from ..sales import registers

    register = registers.current(db, ctx)
    if register:
        return _register_balance(db, ctx, register)
    return {"shift_id": None, "open": False, "balance": 0, "register_id": None, "register_name": None,
            "active": False}


def drawer_totals(db: Database, ctx: Context) -> dict:
    """Management view of every register, including float that must still be accounted for."""
    from ..sales import registers

    ctx.require("cash.see_expected")
    if not registers.can_manage(ctx):
        return {}
    with db.read_snapshot():
        rows = db.all("SELECT id, name, active FROM registers WHERE org_id=? AND branch_id=? ORDER BY id",
                      (ctx.org_id, ctx.branch_id))
        balances = [_register_balance(db, ctx, row) for row in rows]
    return {"registers": balances, "balance": sum(row["balance"] for row in balances)}


def drawer_check(db: Database, ctx: Context, amount: int, branch_id: str | None = None) -> str:
    """Refuse a cash outflow that would take the open drawer below zero."""
    from ..sales import cash
    sid = cash.require_open_shift(db, ctx, branch_id)
    now = _drawer_expected(db, ctx, sid)
    if now - amount < 0:
        details = {"amount": amount}
        message = "النقدية في الدرج لا تكفي. سجّل إيداع في الدرج قبل الصرف"
        if ctx.can("cash.see_expected"):
            details["balance"] = now
            whole, fraction = divmod(max(0, now), 100)
            message = f"النقدية في الدرج لا تكفي. الحد الأقصى المتاح {whole:,}.{fraction:02d} ج"
        raise Conflict(message, code="drawer_negative", details=details)
    return sid


def _drawer_out(db: Database, ctx: Context, amount: int, ref_id: str | None, note: str) -> str | None:
    from ..sales import cash
    return cash.record(db, ctx, -amount, "payout", ref_id, note)


def source_label(db: Database, ctx: Context, source: str | None) -> str:
    if source == DRAWER:
        return DRAWER_NAME
    if source in (None, OUTSIDE):
        return OUTSIDE_LABEL
    a = db.one("SELECT name FROM money_accounts WHERE id=? AND org_id=?", (source, ctx.org_id))
    return a["name"] if a else "حساب غير معروف"


def check_source(db: Database, ctx: Context, source: str | None) -> str:
    """Validate a payout source before anything is written: the drawer, an active account (owner or
    manager), or outside the program's accounts (the owner's explicit choice only)."""
    if source == DRAWER:
        return DRAWER
    if source in (None, "", OUTSIDE):
        if not is_owner(ctx):
            raise Forbidden("الدفع من خارج حسابات البرنامج اختيار المالك وحده؛ اختر الحساب اللي اتدفع منه",
                            code="approval_required",
                            details={"permission": "users.manage", "label": "الدفع من خارج حسابات البرنامج"})
        return OUTSIDE
    ctx.require("cash.manage")
    with db.transaction():
        try:
            aid = _writable(db, ctx, source)
        except NotFound:
            raise ValidationFailed("اختر مصدر المبلغ المدفوع", code="unknown_source")
        _account(db, ctx, aid)
    return aid


def pay_out(db: Database, ctx: Context, source: str | None, amount: int, kind: str, *, ref_type: str,
            ref_id: str | None, note: str) -> dict:
    """A payout (supplier, expense, salary, rent, tax, owner withdrawal) from its source, inside the
    caller's transaction. The caller has already checked its own permission (suppliers.pay, ...)."""
    if kind not in PAYOUT_KINDS:
        raise ValueError(f"not a payout kind {kind}")
    amount = _amount(amount)
    src = check_source(db, ctx, source)
    with db.transaction():
        if src == DRAWER:
            return {"source": DRAWER, "shift_id": _drawer_out(db, ctx, amount, ref_id, note), "movement_id": None}
        if src == OUTSIDE:
            return {"source": OUTSIDE, "shift_id": None, "movement_id": None}
        return {"source": src, "shift_id": None,
                "movement_id": _post(db, ctx, src, -amount, kind, ref_type=ref_type, ref_id=ref_id, note=note)}


# -------------------------------------------------------------------------- accounts
def accounts(db: Database, ctx: Context, *, include_inactive: bool = False) -> list[dict]:
    """Every money place with its balance: the drawer first, then the treasury, then the owner's accounts."""
    ctx.require("cash.see_expected")
    rows = db.all("SELECT a.*, coalesce((SELECT sum(m.amount) FROM money_movements m WHERE m.org_id=a.org_id AND m.account_id=a.id),0)"
                  " balance FROM money_accounts a WHERE a.org_id=?" + ("" if include_inactive else   # a stopped
                  " AND (a.is_active=1 OR EXISTS(SELECT 1 FROM money_movements m WHERE m.org_id=a.org_id AND m.account_id=a.id))")  # one with history stays
                  + " ORDER BY a.kind<>'treasury', a.is_active DESC, a.sort, a.name", (ctx.org_id,))
    d = drawer_balance(db, ctx)
    out = [{"id": DRAWER, "kind": "drawer", "name": DRAWER_NAME, "balance": d["balance"], "shift_open": d["open"],
            "allow_negative": 0, "is_active": 1, "register_id": d["register_id"],
            "register_name": d["register_name"]}]
    if not find_treasury(db, ctx.org_id):              # made on its first write; shown empty until then
        out.append({"id": "treasury", "kind": "treasury", "name": TREASURY_NAME, "kind_label": ACCOUNT_KINDS["treasury"],
                    "balance": 0, "allow_negative": 0, "is_active": 1})
    for r in rows:
        x = dict(r)
        x["kind_label"] = ACCOUNT_KINDS[x["kind"]]
        out.append(x)
    return out


def create_account(db: Database, ctx: Context, name: str, kind: str, *, allow_negative: bool = False) -> dict:
    ctx.require("settings.manage")
    ctx.require("cash.see_expected")         # the answer shows the balance: refused before anything is written
    name = (name or "").strip()[:60]
    if len(name) < 2:
        raise ValidationFailed("اكتب اسم الحساب")
    if kind not in ACCOUNT_KINDS or kind == "treasury":
        raise ValidationFailed("نوع الحساب غير صحيح")
    aid = new_id()
    with db.transaction():
        if db.one("SELECT 1 FROM money_accounts WHERE org_id=? AND name=?", (ctx.org_id, name)):
            raise Conflict("يوجد حساب بالاسم نفسه")
        db.execute("INSERT INTO money_accounts (id, org_id, kind, name, allow_negative, sort, created_by)"
                   " VALUES (?,?,?,?,?, (SELECT coalesce(max(sort),0)+1 FROM money_accounts WHERE org_id=?), ?)",
                   (aid, ctx.org_id, kind, name, int(bool(allow_negative)), ctx.org_id, ctx.user_id))
        audit.record(db, ctx, "money.account_create", "money_account", aid,
                     after={"kind": kind, "name": name, "allow_negative": bool(allow_negative)})
    return get_account(db, ctx, aid)


def get_account(db: Database, ctx: Context, account_id: str) -> dict:
    ctx.require("cash.see_expected")
    if account_id == "treasury":
        account_id = find_treasury(db, ctx.org_id) or ""
    a = dict(_account(db, ctx, account_id, active=False))
    a["balance"] = balance(db, ctx.org_id, account_id)
    a["kind_label"] = ACCOUNT_KINDS[a["kind"]]
    return a


def update_account(db: Database, ctx: Context, account_id: str, data: dict) -> dict:
    """Rename, allow/forbid a negative balance, stop/restart. Owner only (settings.manage)."""
    ctx.require("settings.manage")
    ctx.require("cash.see_expected")
    with db.transaction():
        a = _account(db, ctx, account_id, active=False)
        changes: dict = {}
        if "name" in data:
            name = (str(data["name"] or "")).strip()[:60]
            if len(name) < 2:
                raise ValidationFailed("اكتب اسم الحساب")
            if name != a["name"] and db.one("SELECT 1 FROM money_accounts WHERE org_id=? AND name=?", (ctx.org_id, name)):
                raise Conflict("يوجد حساب بالاسم نفسه")
            changes["name"] = name
        bal = balance(db, ctx.org_id, account_id)
        if "allow_negative" in data:
            if a["kind"] == "treasury" and data["allow_negative"]:
                raise ValidationFailed("الخزنة الرئيسية لا تسمح برصيد سالب", code="physical_cash")
            changes["allow_negative"] = int(bool(data["allow_negative"]))
            if not changes["allow_negative"] and bal < 0:
                raise Conflict("رصيد الحساب بالسالب؛ سوّه الأول قبل منع السالب", code="account_negative")
        if "is_active" in data:
            changes["is_active"] = int(bool(data["is_active"]))
            if not changes["is_active"]:
                if a["kind"] == "treasury":
                    raise Conflict("الخزنة الرئيسية لا تُوقف")
                if bal:
                    raise Conflict("الحساب فيه رصيد؛ حوّله لحساب تاني الأول", code="account_not_empty")
        changes = {k: v for k, v in changes.items() if a[k] != v}
        if changes:
            db.execute(f"UPDATE money_accounts SET {', '.join(f'{k}=?' for k in changes)} WHERE id=? AND org_id=?",
                       (*changes.values(), account_id, ctx.org_id))
            audit.record(db, ctx, "money.account_update", "money_account", account_id,
                         before={k: a[k] for k in changes}, after=changes)
    return get_account(db, ctx, account_id)


# Standard accounts: key -> (kind, name). Found by key, made on first use.
STANDARD_ACCOUNTS = {"treasury": ("treasury", TREASURY_NAME), "instapay": ("bank", "إنستاباي"),
                     "vodafone_cash": ("wallet", "فودافون كاش")}
OPENING_SOURCES = {"estock": "رصيد افتتاحي من إي ستوك", "count": "رصيد افتتاحي من العدّ الفعلي"}


def listing(db: Database, ctx: Context) -> list[dict]:
    """The accounts (not the drawer) with their keys, for the E-Stock import to match its money places."""
    ctx.require("cash.see_expected")
    return [dict(r) for r in db.all(
        "SELECT id, code, source_ref, name, kind, is_active, allow_negative FROM money_accounts WHERE org_id=?"
        " ORDER BY kind<>'treasury', sort, name", (ctx.org_id,))]


def resolve(db: Database, ctx: Context, account: str) -> str:
    """An account id from an id, a key ('treasury', 'instapay', 'vodafone_cash', or any code set by
    ensure_account) or a source reference ('estock:2'). Never creates."""
    if account == "treasury":
        t = find_treasury(db, ctx.org_id)
        if t:
            return t
    r = db.one("SELECT id FROM money_accounts WHERE org_id=? AND (id=? OR code=? OR source_ref=?)"
               " ORDER BY id=? DESC LIMIT 1", (ctx.org_id, account, account, account, account))
    if not r:
        raise NotFound("الحساب غير موجود")
    return r["id"]


def ensure_account(db: Database, ctx: Context, *, kind: str, name: str, code: str | None = None,
                   source_ref: str | None = None, is_active: bool = True) -> str:
    """Find or make one account for the import (one per used E-Stock money place, source_ref 'estock:<id>').
    Matched by source_ref, then code, then (kind 'treasury') the main treasury, then the same name; a match
    keeps its id and gains the missing code / source_ref. Owner only; audited. Returns the account id.
    is_active=False makes a new account stopped (an old money place moved as it is but not used: it keeps its
    opening balance and takes no other movement until the owner restarts it); a matched account keeps
    the owner's setting.
    """
    ctx.require("settings.manage")
    if kind not in ACCOUNT_KINDS:
        raise ValidationFailed("نوع الحساب غير صحيح")
    name = (name or "").strip()[:60]
    if len(name) < 2:
        raise ValidationFailed("اكتب اسم الحساب")
    code = (code or "").strip()[:40] or None
    source_ref = (source_ref or "").strip()[:60] or None
    with db.transaction():
        found = None
        if source_ref:
            found = db.one("SELECT * FROM money_accounts WHERE org_id=? AND source_ref=?", (ctx.org_id, source_ref))
        if not found and code:
            found = db.one("SELECT * FROM money_accounts WHERE org_id=? AND code=?", (ctx.org_id, code))
        if not found and kind == "treasury":
            found = db.one("SELECT * FROM money_accounts WHERE id=? AND org_id=?", (treasury_id(db, ctx), ctx.org_id))
        if not found:
            found = db.one("SELECT * FROM money_accounts WHERE org_id=? AND name=?", (ctx.org_id, name))
        if found:
            changes = {}
            for col, v in (("code", code), ("source_ref", source_ref)):
                if v and found[col] != v:
                    if found[col]:
                        raise Conflict(f"الحساب {found['name']} مربوط بمفتاح تاني", code="account_key_taken",
                                       details={"account_id": found["id"], col: found[col]})
                    changes[col] = v
            if changes:
                db.execute(f"UPDATE money_accounts SET {', '.join(f'{k}=?' for k in changes)} WHERE id=? AND org_id=?",
                           (*changes.values(), found["id"], ctx.org_id))
                audit.record(db, ctx, "money.account_update", "money_account", found["id"],
                             before={k: found[k] for k in changes}, after=changes)
            return found["id"]
        if kind == "treasury":                     # unreachable: treasury_id() above always finds or makes it
            raise Conflict("الخزنة الرئيسية موجودة")
        aid = new_id()
        db.execute("INSERT INTO money_accounts (id, org_id, kind, code, source_ref, name, is_active, sort, created_by)"
                   " VALUES (?,?,?,?,?,?,?, (SELECT coalesce(max(sort),0)+1 FROM money_accounts WHERE org_id=?), ?)",
                   (aid, ctx.org_id, kind, code, source_ref, name, int(bool(is_active)), ctx.org_id, ctx.user_id))
        audit.record(db, ctx, "money.account_create", "money_account", aid,
                     after={"kind": kind, "name": name, "code": code, "source_ref": source_ref,
                            "is_active": bool(is_active)})
        return aid


def account_for(db: Database, ctx: Context, key: str) -> str:
    """The id of a standard account by its key, made (and audited) the first time."""
    if key not in STANDARD_ACCOUNTS:
        raise ValidationFailed("الحساب غير معروف")
    if key == "treasury":
        return treasury_id(db, ctx)
    r = db.one("SELECT id FROM money_accounts WHERE org_id=? AND code=?", (ctx.org_id, key))
    if r:
        return r["id"]
    kind, name = STANDARD_ACCOUNTS[key]
    return ensure_account(db, ctx, kind=kind, name=name, code=key)


def _opening_of(db: Database, ctx: Context, account_id: str) -> dict | None:
    r = db.one("SELECT after_json FROM audit_log WHERE org_id=? AND entity_type='money_account' AND entity_id=?"
               " AND action='money.opening' ORDER BY seq DESC LIMIT 1", (ctx.org_id, account_id))
    return json.loads(r["after_json"] or "{}") if r else None


def post_opening(db: Database, ctx: Context, account: str, amount: int, *, source: str = "estock",
                 source_ref: str | None = None, note: str | None = None) -> dict:
    """An account's opening balance at launch (owner only). The E-Stock import posts the old program's figure
    with source 'estock' (account = id, standard key or 'estock:<id>'); source 'count' is a real count. The
    E-Stock figure is taken as it is: a negative one is posted even where the account does not allow
    negative, and a later count records the real difference with a reason ("difference from the old
    program"). Once per account, audited, refused once the account has any movement; inside the caller's
    transaction. The same source, source_ref and amount again returns the first result (replayed)."""
    if not is_owner(ctx):
        raise Forbidden("الرصيد الافتتاحي للمالك وحده", code="approval_required",
                        details={"permission": "users.manage", "label": "الرصيد الافتتاحي للحسابات"})
    if source not in OPENING_SOURCES:
        raise ValidationFailed("مصدر الرصيد الافتتاحي غير صحيح")
    amount = _amount(amount, allow_zero=True, signed=True)
    with db.transaction():
        account_id = account_for(db, ctx, account) if account in STANDARD_ACCOUNTS else resolve(db, ctx, account)
        a = _account(db, ctx, account_id, active=False)       # a stopped old money place keeps its balance
        prev = _opening_of(db, ctx, account_id)                  # an opening of 0 has no movement
        if prev is not None:
            if source_ref and (prev.get("source"), prev.get("source_ref"), prev.get("amount")) == (source, source_ref, amount):
                m = db.one("SELECT id FROM money_movements WHERE org_id=? AND account_id=? AND kind='opening'",
                           (ctx.org_id, account_id))
                return {**get_account(db, ctx, account_id), "movement_id": m["id"] if m else None, "replayed": True}
            raise Conflict("الرصيد الافتتاحي للحساب ده متسجل قبل كده؛ أي فرق بعد كده يتسجل بالجرد",
                           code="opening_exists", details={"account_id": account_id, "opening": prev.get("amount")})
        if db.one("SELECT 1 FROM money_movements WHERE org_id=? AND account_id=? LIMIT 1", (ctx.org_id, account_id)):
            raise Conflict(f"{a['name']} عليه حركات؛ الرصيد الافتتاحي لا يتسجل بعدها، سجّل الفرق بالجرد",
                           code="account_has_movements")
        if source == "count" and amount < 0 and not a["allow_negative"]:
            raise ValidationFailed(f"الرصيد الافتتاحي لـ{a['name']} بالسالب والحساب لا يسمح بالسالب",
                                   code="account_negative", details={"account_id": account_id, "amount": amount})
        said = _note(note)
        text = OPENING_SOURCES[source] + (f" — {said}" if said else "")
        meta = {"source": source, "source_ref": source_ref}
        mid = _post(db, ctx, account_id, amount, "opening", ref_type="opening", ref_id=source_ref, note=text,
                    meta=meta, check=False, active=False) if amount else None
        audit.record(db, ctx, "money.opening", "money_account", account_id,
                     after={"amount": amount, "source": source, "source_ref": source_ref, "note": said})
    return {**get_account(db, ctx, account_id), "movement_id": mid, "replayed": False}


def adjust_opening(db: Database, ctx: Context, account: str, correct_balance: int, reason: str) -> dict:
    """The owner corrects an account's balance without counting it (e.g. the old program's figure was
    wrong): the difference is an opening adjustment, apart from count differences, with a required reason.
    Owner only; audited. Kept out of the month's money in / out."""
    if not is_owner(ctx):
        raise Forbidden("تسوية الرصيد الافتتاحي للمالك وحده", code="approval_required",
                        details={"permission": "users.manage", "label": "تسوية الرصيد الافتتاحي"})
    target = _amount(correct_balance, allow_zero=True, signed=True)
    why = _note(reason, required=True, what="اكتب سبب التسوية")
    with db.transaction():
        account_id = account_for(db, ctx, account) if account in STANDARD_ACCOUNTS else resolve(db, ctx, account)
        a = _account(db, ctx, account_id)
        if target < 0 and not a["allow_negative"]:
            raise ValidationFailed("الرصيد الصحيح لا يكون بالسالب في الحساب ده", code="account_negative")
        before = balance(db, ctx.org_id, account_id)
        diff = target - before
        mid = _post(db, ctx, account_id, diff, "opening_adjustment", ref_type="opening_adjustment", note=why,
                    check=False) if diff else None
        audit.record(db, ctx, "money.opening_adjustment", "money_account", account_id,
                     after={"before": before, "correct": target, "difference": diff, "reason": why})
    return {**get_account(db, ctx, account_id), "difference": diff, "movement_id": mid}


def check_openings(db: Database, ctx: Context, figures: dict[str, int]) -> dict:
    """For the import report, right after the move: each account's balance in the program against the old
    program's figure (rounded to whole piasters by the import). Read only."""
    ctx.require("cash.see_expected")
    rows = []
    for key, fig in figures.items():
        expected = _amount(fig, allow_zero=True, signed=True)
        try:
            aid = resolve(db, ctx, key)
            a = db.one("SELECT name FROM money_accounts WHERE id=? AND org_id=?", (aid, ctx.org_id))
            bal, name = balance(db, ctx.org_id, aid), a["name"]
        except NotFound:
            aid, bal, name = None, None, None
        rows.append({"account": key, "account_id": aid, "name": name, "expected": expected, "balance": bal,
                     "difference": None if bal is None else bal - expected, "ok": bal == expected})
    return {"rows": rows, "ok": all(r["ok"] for r in rows)}


def count(db: Database, ctx: Context, account_id: str, counted: int, reason: str | None = None) -> dict:
    """Count an account (cash in the treasury, the bank statement, the wallet's balance). A difference is
    recorded with a required reason, like the shift difference."""
    ctx.require("cash.manage")
    ctx.require("cash.see_expected")
    counted = _amount(counted, allow_zero=True, signed=True)
    with db.transaction():
        account_id = treasury_id(db, ctx) if account_id == "treasury" else resolve(db, ctx, account_id)
        a = _account(db, ctx, account_id)
        if counted < 0 and not a["allow_negative"]:
            raise ValidationFailed("المبلغ المعدود غير صحيح")
        expected = balance(db, ctx.org_id, account_id)
        diff = counted - expected
        why = _note(reason, required=bool(diff), what="يوجد فرق في الحساب؛ اكتب سببه")
        # the first count after an opening taken from E-Stock measures the old program, not this month
        opening = _opening_of(db, ctx, account_id)
        old = bool(opening and opening.get("source") == "estock"
                   and not db.one("SELECT 1 FROM money_counts WHERE org_id=? AND account_id=? LIMIT 1",
                               (ctx.org_id, account_id)))
        mid = None
        if diff:
            mid = new_id()
            db.execute("INSERT INTO money_movements (id, org_id, account_id, amount, kind, ref_type, note, meta, created_by)"
                       " VALUES (?,?,?,?, 'count_difference', 'money_count', ?, ?, ?)",
                       (mid, ctx.org_id, account_id, diff, why,
                        json.dumps({"against": "old_program"}) if old else None, ctx.user_id))
        cid = new_id()
        db.execute("INSERT INTO money_counts (id, org_id, account_id, expected, counted, difference, reason,"
                   " movement_id, created_by) VALUES (?,?,?,?,?,?,?,?,?)",
                   (cid, ctx.org_id, account_id, expected, counted, diff, why, mid, ctx.user_id))
        audit.record(db, ctx, "money.count", "money_account", account_id,
                     after={"expected": expected, "counted": counted, "difference": diff, "reason": why,
                            "against_old_program": old})
        if diff:
            from ..core import notify
            title = (f"فرق عن البرنامج القديم في {a['name']} {abs(diff) / 100:,.2f} ج" if old else
                     f"{'عجز' if diff < 0 else 'زيادة'} في {a['name']} {abs(diff) / 100:,.2f} ج")
            notify.emit(db, ctx, "money_count_difference", "warning" if old or diff > 0 else "error", title,
                        why or "", "money_account", account_id)
    return {"count_id": cid, "account_id": account_id, "expected": expected, "counted": counted,
            "difference": diff, "against_old_program": old, "balance": balance(db, ctx.org_id, account_id)}


# -------------------------------------------------------------------------- moving money
def transfer(db: Database, ctx: Context, from_source: str, to_source: str, amount: int,
             note: str | None = None) -> dict:
    """Move money between the drawer and the accounts (e.g. a cash pickup from the drawer to the treasury,
    treasury to the bank)."""
    ctx.require("cash.manage")
    amount = _amount(amount)
    if from_source == to_source:
        raise ValidationFailed("اختر حسابين مختلفين")
    if OUTSIDE in (from_source, to_source):
        raise ValidationFailed("استخدم مسحوبات أو إيداع المالك للفلوس اللي خارج الحسابات")
    tid = new_id()
    with db.transaction():
        from_source, to_source = (x if x == DRAWER else _writable(db, ctx, x) for x in (from_source, to_source))
        if from_source == to_source:
            raise ValidationFailed("اختر حسابين مختلفين")
        for x in (from_source, to_source):
            if x != DRAWER:
                _account(db, ctx, x)
        names = (source_label(db, ctx, from_source), source_label(db, ctx, to_source))
        said = _note(note)
        text = f"تحويل من {names[0]} إلى {names[1]}" + (f" — {said}" if said else "")
        if from_source == DRAWER:
            _drawer_out(db, ctx, amount, tid, text)
        else:
            _post(db, ctx, from_source, -amount, "transfer", ref_type="transfer", ref_id=tid, note=text)
        if to_source == DRAWER:
            from ..sales import cash
            cash.record(db, ctx, amount, "deposit", tid, text)
        else:
            _post(db, ctx, to_source, amount, "transfer", ref_type="transfer", ref_id=tid, note=text)
        audit.record(db, ctx, "money.transfer", "money_transfer", tid,
                     after={"from": from_source, "to": to_source, "amount": amount, "note": said})
    return {"transfer_id": tid, "from": from_source, "to": to_source, "amount": amount}


def owner_movement(db: Database, ctx: Context, source: str, amount: int, kind: str, note: str | None = None) -> dict:
    """The owner takes money out (withdrawal) or puts his own money in (deposit). Not an expense: it never
    enters the profit report."""
    ctx.require("cash.manage")
    if kind not in ("owner_withdrawal", "owner_deposit"):
        raise ValidationFailed("نوع الحركة غير صحيح")
    amount = _amount(amount)
    said = _note(note)
    text = KINDS[kind] + (f" — {said}" if said else "")
    ref = new_id()
    with db.transaction():
        if source == DRAWER:
            if kind == "owner_withdrawal":
                _drawer_out(db, ctx, amount, ref, text)
            else:
                from ..sales import cash
                cash.record(db, ctx, amount, "deposit", ref, text)
            mid = None
        else:
            if source in (None, "", OUTSIDE):
                raise ValidationFailed("اختر الحساب")
            source = _writable(db, ctx, source)
            mid = _post(db, ctx, source, -amount if kind == "owner_withdrawal" else amount, kind,
                        ref_type="owner", ref_id=ref, note=text)
        audit.record(db, ctx, f"money.{kind}", "money_account", source,
                     after={"amount": amount, "note": said, "ref": ref})
    return {"ref_id": ref, "movement_id": mid, "source": source, "amount": amount, "kind": kind}


def shift_close_transfer(db: Database, ctx: Context, shift_id: str, float_left: int | None = None, *,
                         to_account: str | None = None, received_by: str | None = None) -> dict:
    """At shift close: what was counted minus the float left for the next shift goes to the treasury, as one
    recorded movement. Called inside the close transaction, after the shift row is closed. A second call for
    the same shift changes nothing and returns the first result."""
    ctx.require("cash.close")
    with db.transaction():
        s = db.one("SELECT id, status, opening_cash, counted_cash, float_left FROM cash_shifts WHERE id=? AND org_id=?",
                   (shift_id, ctx.org_id))
        if not s:
            raise NotFound("الشيفت غير موجود")
        if s["status"] != "closed" or s["counted_cash"] is None:
            raise Conflict("الشيفت لم يُقفل بعد", code="shift_not_closed")
        if s["float_left"] is not None:
            m = db.one("SELECT id, amount FROM money_movements WHERE org_id=? AND kind='shift_close' AND ref_id=?",
                       (ctx.org_id, shift_id))
            return {"shift_id": shift_id, "float_left": s["float_left"], "to_treasury": m["amount"] if m else 0,
                    "movement_id": m["id"] if m else None, "replayed": True}
        counted = s["counted_cash"]
        if float_left is None:
            left = min(s["opening_cash"], counted)
        else:
            left = _amount(float_left, allow_zero=True)
            if left > counted:
                raise ValidationFailed("الفكة اللي هتفضل في الدرج أكبر من المعدود", code="float_too_large",
                                       details={"counted": counted, "float_left": left})
        moved = counted - left
        db.execute("UPDATE cash_shifts SET float_left=? WHERE id=? AND org_id=? AND float_left IS NULL",
                   (left, shift_id, ctx.org_id))
        mid = None
        target = None
        if moved:
            target = _writable(db, ctx, to_account or "treasury")
            name = _account(db, ctx, target)["name"]
            mid = _post(db, ctx, target, moved, "shift_close", ref_type="cash_shift", ref_id=shift_id,
                        note=f"تقفيل شيفت: النقدية اتنقلت إلى {name}",
                        meta={"received_by": received_by or ctx.user_id})
        audit.record(db, ctx, "money.shift_close", "cash_shift", shift_id,
                     after={"counted": counted, "float_left": left, "to_treasury": moved, "to_account": target,
                            "received_by": received_by or ctx.user_id})
    return {"shift_id": shift_id, "float_left": left, "to_treasury": moved, "movement_id": mid, "replayed": False}


# -------------------------------------------------------------------------- card / transfer routing
def route_for(db: Database, org_id: str, method: str, channel_id: str | None) -> str | None:
    if method not in METHODS:
        return None
    r = None
    if channel_id:
        r = db.one("SELECT account_id FROM money_routes WHERE org_id=? AND method=? AND channel_id=?",
                   (org_id, method, channel_id))
    r = r or db.one("SELECT account_id FROM money_routes WHERE org_id=? AND method=? AND channel_id=''",
                    (org_id, method))
    return r["account_id"] if r else None


def routes(db: Database, ctx: Context) -> list[dict]:
    ctx.require("cash.see_expected")
    return [dict(r) for r in db.all(
        "SELECT r.method, r.channel_id, coalesce(c.name,'') channel, r.account_id, a.name account FROM money_routes r"
        " JOIN money_accounts a ON a.id=r.account_id AND a.org_id=r.org_id"
        " LEFT JOIN payment_channels c ON c.id=r.channel_id AND c.org_id=r.org_id"
        " WHERE r.org_id=? ORDER BY r.method, r.channel_id", (ctx.org_id,))]


def set_route(db: Database, ctx: Context, method: str, channel_id: str | None, account_id: str | None) -> list[dict]:
    """Owner: card / transfer money of this channel (or of the method, channel None) lands in this account.
    account_id None removes the route."""
    ctx.require("settings.manage")
    ctx.require("cash.see_expected")
    if method not in METHODS:
        raise ValidationFailed("طريقة الدفع غير صحيحة")
    ch = channel_id or ""
    with db.transaction():
        if ch and not db.one("SELECT 1 FROM payment_channels WHERE id=? AND org_id=? AND kind=?",
                             (ch, ctx.org_id, method)):
            raise NotFound("وسيلة الدفع غير موجودة")
        before = db.one("SELECT account_id FROM money_routes WHERE org_id=? AND method=? AND channel_id=?",
                        (ctx.org_id, method, ch))
        if account_id:
            account_id = _writable(db, ctx, account_id)
            _account(db, ctx, account_id)
            db.execute("INSERT INTO money_routes (org_id, method, channel_id, account_id, updated_by) VALUES (?,?,?,?,?)"
                       " ON CONFLICT (org_id, method, channel_id) DO UPDATE SET account_id=excluded.account_id,"
                       " updated_by=excluded.updated_by, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
                       (ctx.org_id, method, ch, account_id, ctx.user_id))
        else:
            db.execute("DELETE FROM money_routes WHERE org_id=? AND method=? AND channel_id=?", (ctx.org_id, method, ch))
        audit.record(db, ctx, "money.route", "money_route", f"{method}:{ch}",
                     before={"account_id": before["account_id"] if before else None}, after={"account_id": account_id})
    return routes(db, ctx)


def receive(db: Database, ctx: Context, method: str, channel_id: str | None, amount: int, kind: str, *,
            ref_type: str, ref_id: str | None, note: str | None = None, check: bool = False) -> str | None:
    """Card / transfer money of a sale or a customer collection (amount > 0), or a card refund (kind 'refund',
    amount < 0), on the routed account, inside the caller's transaction. No route = nothing posted (the
    reports list it as not routed). Cash never comes here: it is the drawer's."""
    if kind not in INCOME_KINDS:
        raise ValueError(f"not an income kind {kind}")
    if not isinstance(amount, int) or isinstance(amount, bool) or amount == 0:
        return None
    acc = route_for(db, ctx.org_id, method, channel_id)
    if not acc:
        return None
    # a refund back to the card / wallet is what really happened at the machine: it is recorded even if it
    # takes the account below zero (never blocks a customer's return); the count then shows the truth
    return _post(db, ctx, acc, amount, kind, ref_type=ref_type, ref_id=ref_id, note=note, check=check,
                 meta={"method": method, "channel_id": channel_id})


def _card_refund_account(db: Database, ctx: Context, sale_id: str) -> str | None:
    """Where a card refund leaves from when the sale itself put nothing on an account (a cash sale refunded to
    the card, or a card sale from before its channel was routed or before 0038): the sale's own card channel,
    else the card method's route, else the only routed card channel."""
    p = db.one("SELECT channel_id FROM sale_payments WHERE org_id=? AND sale_id=? AND method='card'"
               " ORDER BY rowid LIMIT 1", (ctx.org_id, sale_id))
    acc = route_for(db, ctx.org_id, "card", p["channel_id"] if p else None)
    if acc:
        return acc
    only = db.all("SELECT DISTINCT r.account_id FROM money_routes r JOIN payment_channels c ON c.id=r.channel_id"
                  " AND c.org_id=r.org_id WHERE r.org_id=? AND r.method='card' AND c.is_active=1", (ctx.org_id,))
    return only[0]["account_id"] if len(only) == 1 else None


def reverse_sale(db: Database, ctx: Context, sale_id: str, amount: int | None = None, *, ref_type: str,
                 note: str | None = None, meta: dict | None = None, card_only: bool = False) -> list[str]:
    """Take a sale's card / wallet money back (a void: all of it; a card refund: `amount`) from the accounts the
    sale really went to, read from its own movements, never from today's routes (#348 Codex P1). Inside the
    caller's transaction; recorded even below zero or on a stopped account (a return is never blocked)."""
    card = (" AND (json_extract(meta, '$.method')='card' OR"
            " (kind='refund' AND json_extract(meta, '$.method') IS NULL))") if card_only else ""
    rows = db.all("SELECT account_id, sum(amount) net FROM money_movements WHERE org_id=? AND ref_id=?"
                  " AND kind IN ('sale','refund')" + card + " GROUP BY account_id"
                  + (" HAVING sum(CASE WHEN kind='sale' THEN amount ELSE 0 END)>0" if card_only else "")
                  + " ORDER BY min(rowid)", (ctx.org_id, sale_id))
    left, out = amount, []
    for r in rows:
        take = r["net"] if left is None else min(r["net"], left)
        if take <= 0:
            continue
        out.append(_post(db, ctx, r["account_id"], -take, "refund", ref_type=ref_type, ref_id=sale_id, note=note,
                         meta=meta, check=False, active=False))
        if left is not None:
            left -= take
            if left <= 0:
                break
    # a card refund may be more than the sale's card part (a split sale refunded in full to the card, which the
    # return rules allow): the rest also goes back through the sale's own card account (#348 Codex P1)
    if left and rows:
        out.append(_post(db, ctx, rows[0]["account_id"], -left, "refund", ref_type=ref_type, ref_id=sale_id, note=note,
                         meta=meta, check=False, active=False))
    elif left and not rows:
        # the sale put nothing on an account, yet the card machine paid the customer: the money still leaves
        # the card's account, or the balances would show money the pharmacy no longer has (Codex on #381)
        acc = _card_refund_account(db, ctx, sale_id)
        if acc:
            out.append(_post(db, ctx, acc, -left, "refund", ref_type=ref_type, ref_id=sale_id, note=note,
                             meta=meta, check=False, active=False))
    return out


def refund_wallet(db: Database, ctx: Context, sale_id: str, channel_id: str | None, amount: int,
                  *, return_id: str, note: str) -> str | None:
    """Debit the wallet account recorded for this sale, even if the route has since changed."""
    source = db.all(
        "SELECT account_id, meta FROM money_movements WHERE org_id=? AND ref_id=? AND kind='sale'"
        " AND ref_type='sale' AND json_extract(meta, '$.method')='transfer'",
        (ctx.org_id, sale_id))
    accounts = set()
    for row in source:
        meta = json.loads(row["meta"])
        if "channel_id" in meta and meta["channel_id"] == channel_id:
            accounts.add(row["account_id"])
    legacy = {row["account_id"] for row in source if "channel_id" not in json.loads(row["meta"])}
    if legacy:
        channels = db.all("SELECT DISTINCT channel_id FROM sale_payments"
                          " WHERE org_id=? AND sale_id=? AND method='transfer'", (ctx.org_id, sale_id))
        if len(channels) > 1:
            raise Conflict("حساب المحفظة الأصلي مش محدد في الفاتورة القديمة", code="ambiguous_wallet_account")
        accounts.update(legacy)
    if len(accounts) > 1:
        raise Conflict("المحفظة اتسجلت على أكتر من حساب في الفاتورة دي", code="ambiguous_wallet_account")
    account = next(iter(accounts), None)
    if account:
        _post(db, ctx, account, -amount, "refund", ref_type="sale_return", ref_id=sale_id, note=note,
              meta={"return_id": return_id, "method": "transfer", "channel_id": channel_id}, check=False, active=False)
    return account


def legacy_wallet_refund_reserve(db: Database, ctx: Context, sale_id: str) -> int:
    """Reserve legacy card returns unless a debit proves the payout came from a distinct card account."""
    legacy = db.all(
        "SELECT r.id, r.total FROM sale_returns r WHERE r.org_id=? AND r.sale_id=?"
        " AND r.refund_method='card' AND NOT EXISTS (SELECT 1 FROM audit_log a"
        " WHERE a.org_id=r.org_id AND a.entity_type='sale' AND a.entity_id=r.sale_id AND a.action='sale.return'"
        " AND json_extract(a.after_json,'$.return_id')=r.id AND json_extract(a.after_json,'$.card_only')=1)",
        (ctx.org_id, sale_id))
    if not legacy:
        return 0
    by_method = {"card": set(), "transfer": set()}
    movements = db.all("SELECT account_id, amount, ref_type, kind, meta FROM money_movements"
                       " WHERE org_id=? AND ref_id=? AND ref_type IN ('sale','sale_return')",
                       (ctx.org_id, sale_id))
    for row in movements:
        method = json.loads(row["meta"] or "{}").get("method")
        if row["kind"] == "sale" and method in by_method:
            by_method[method].add(row["account_id"])
    card_only = by_method["card"] - by_method["transfer"]
    proven = {}
    for row in movements:
        if row["kind"] == "refund" and row["account_id"] in card_only:
            rid = json.loads(row["meta"] or "{}").get("return_id")
            proven[rid] = proven.get(rid, 0) - row["amount"]
    return sum(max(0, r["total"] - proven.get(r["id"], 0)) for r in legacy)


def settle_transfer_review(db: Database, ctx: Context, sale_id: str, received: int) -> int:
    """After a transfer proof review: the sale's routed transfer money that did not come is taken back off its
    account(s), and given back if a later review confirms it (Codex on #381). One correction movement per change,
    kind 'sale' with ref_type 'transfer_review', so the account's net for the sale is what was confirmed and a
    later void reverses only that. Returns the amount posted (negative = taken off). Inside the caller's
    transaction; never blocked by the balance."""
    s = db.one("SELECT status, sale_no, paid_transfer FROM sales WHERE id=? AND org_id=?", (sale_id, ctx.org_id))
    if not s or s["status"] != "completed" or not s["paid_transfer"]:
        return 0
    got = {}
    for r in db.all("SELECT account_id, amount, meta FROM money_movements WHERE org_id=? AND ref_id=? AND kind='sale'"
                    " AND ref_type='sale' ORDER BY rowid", (ctx.org_id, sale_id)):
        if json.loads(r["meta"] or "{}").get("method") == "transfer":
            got[r["account_id"]] = got.get(r["account_id"], 0) + r["amount"]
    routed = sum(got.values())
    if not routed:
        return 0
    fixed = {r["account_id"]: r["t"] for r in db.all(
        "SELECT account_id, sum(amount) t FROM money_movements WHERE org_id=? AND ref_id=? AND ref_type='transfer_review'"
        " GROUP BY account_id", (ctx.org_id, sale_id))}
    short = max(0, min(routed, s["paid_transfer"] - max(0, received)))      # what should be off the accounts
    delta = -short - sum(fixed.values())                                     # < 0: take more off; > 0: give back
    posted, note = 0, f"تحويل فاتورة {s['sale_no']}: المؤكد وصوله {received / 100:,.2f} ج"
    for acc, amt in (got.items() if delta < 0 else reversed(list(got.items()))):
        if not delta:
            break
        if delta < 0:      # take off what is still on this account for the sale
            step = -min(-delta, amt + fixed.get(acc, 0))
        else:              # give back only what was taken off this account
            step = min(delta, -fixed.get(acc, 0))
        if step:
            _post(db, ctx, acc, step, "sale", ref_type="transfer_review", ref_id=sale_id, note=note,
                  meta={"method": "transfer", "received": received}, check=False, active=False)
            fixed[acc] = fixed.get(acc, 0) + step
            delta -= step
            posted += step
    if posted:
        audit.record(db, ctx, "money.transfer_review", "sale", sale_id,
                     after={"received": received, "posted": posted, "off_accounts": -sum(fixed.values())})
    return posted


# -------------------------------------------------------------------------- reports
def _bounds(date_from: str | None, date_to: str | None) -> tuple[date, date, str, str]:
    from ..sales.service import local_day_bounds, local_today
    try:
        d1 = date.fromisoformat(date_from) if date_from else local_today()
        d2 = date.fromisoformat(date_to) if date_to else d1
    except (TypeError, ValueError):
        raise ValidationFailed("الفترة غير صحيحة")
    if d2 < d1:
        raise ValidationFailed("تاريخ النهاية قبل البداية")
    if (d2 - d1).days > 1830:
        raise ValidationFailed("الفترة أطول من خمس سنين")
    return d1, d2, local_day_bounds(d1)[0], local_day_bounds(d2)[1]


def movements(db: Database, ctx: Context, *, account_id: str | None = None, kind: str | None = None,
              date_from: str | None = None, date_to: str | None = None, limit: int = 500) -> dict:
    """Account movements with filters (owner / manager)."""
    ctx.require("cash.see_expected")
    from ..sales.service import _local_date
    d1, d2, lo, hi = _bounds(date_from, date_to)
    q = ("SELECT m.*, a.name account, u.full_name by_name FROM money_movements m JOIN money_accounts a ON a.id=m.account_id"
         " AND a.org_id=m.org_id LEFT JOIN users u ON u.id=m.created_by WHERE m.org_id=? AND m.created_at>=? AND m.created_at<?")
    p: list = [ctx.org_id, lo, hi]
    if account_id:
        if account_id == "treasury":
            account_id = find_treasury(db, ctx.org_id) or ""
        q += " AND m.account_id=?"
        p.append(account_id)
    if kind:
        if kind not in KINDS:
            raise ValidationFailed("نوع الحركة غير صحيح")
        q += " AND m.kind=?"
        p.append(kind)
    q += " ORDER BY m.created_at DESC, m.rowid DESC LIMIT ?"
    p.append(max(1, min(int(limit), 5000)))
    rows = []
    for r in db.all(q, tuple(p)):
        d = dict(r)
        d["day"] = _local_date(d["created_at"]).isoformat()
        d["kind_label"] = KINDS[d["kind"]]
        d["meta"] = json.loads(d["meta"]) if d["meta"] else None
        rows.append(d)
    return {"from": d1.isoformat(), "to": d2.isoformat(), "rows": rows,
            "total_in": sum(r["amount"] for r in rows if r["amount"] > 0),
            "total_out": -sum(r["amount"] for r in rows if r["amount"] < 0)}


def statement(db: Database, ctx: Context, account: str, date_from: str | None = None,
              date_to: str | None = None) -> dict:
    """One account's statement: the balance before the period, every movement with the running balance, the
    balance at the end (owner / manager)."""
    ctx.require("cash.see_expected")
    from ..sales.service import _local_date
    d1, d2, lo, hi = _bounds(date_from, date_to)
    aid = find_treasury(db, ctx.org_id) if account == "treasury" else resolve(db, ctx, account)
    if not aid:
        return {"account": {"id": "treasury", "name": TREASURY_NAME, "kind": "treasury"}, "from": d1.isoformat(),
                "to": d2.isoformat(), "opening": 0, "closing": 0, "lines": [], "total_in": 0, "total_out": 0}
    a = dict(_account(db, ctx, aid, active=False))
    opening = db.one("SELECT coalesce(sum(amount),0) b FROM money_movements WHERE org_id=? AND account_id=?"
                     " AND created_at<?", (ctx.org_id, aid, lo))["b"]
    run, lines = opening, []
    for r in db.all("SELECT m.*, u.full_name by_name FROM money_movements m LEFT JOIN users u ON u.id=m.created_by"
                    " WHERE m.org_id=? AND m.account_id=? AND m.created_at>=? AND m.created_at<?"
                    " ORDER BY m.created_at, m.rowid", (ctx.org_id, aid, lo, hi)):
        run += r["amount"]
        d = dict(r)
        d.update(day=_local_date(d["created_at"]).isoformat(), kind_label=KINDS[d["kind"]], balance=run,
                 meta=json.loads(d["meta"]) if d["meta"] else None)
        lines.append(d)
    return {"account": {k: a[k] for k in ("id", "name", "kind", "is_active", "allow_negative")},
            "from": d1.isoformat(), "to": d2.isoformat(), "opening": opening, "closing": run, "lines": lines,
            "total_in": sum(x["amount"] for x in lines if x["amount"] > 0),
            "total_out": -sum(x["amount"] for x in lines if x["amount"] < 0)}


def _drawer_kind(r) -> str:
    """What a drawer payout / deposit was for, from the row it belongs to (primary-key lookups only)."""
    if r["kind"] != "payout" and r["kind"] != "deposit":
        return {"sale": "sale", "customer_payment": "customer_payment", "refund": "refund", "void": "void"}[r["kind"]]
    if r["is_expense"]:
        return "expense"
    if r["is_supplier"]:
        return "supplier_payment"
    if r["is_advance"]:
        return "payroll"
    if r["is_transfer"]:
        return "transfer"
    note = r["note"] or ""
    if note.startswith(KINDS["owner_withdrawal"]) or note.startswith(KINDS["owner_deposit"]):
        return "owner_withdrawal" if r["amount"] < 0 else "owner_deposit"
    return "drawer_payout" if r["amount"] < 0 else "drawer_deposit"


SUMMARY_EXTRA = {"drawer_payout": "صرف من الدرج", "drawer_deposit": "إيداع في الدرج", "void": "إلغاء فاتورة",
                 "old_program_difference": "فرق عن البرنامج القديم"}
# moves between the pharmacy's own places, opening balances and their corrections (the old program's
# figure against the real money): shown, but not money in or out of the period
NOT_A_FLOW = ("transfer", "shift_close", "opening", "opening_adjustment", "old_program_difference")


def summary(db: Database, ctx: Context, date_from: str | None = None, date_to: str | None = None,
            by: str = "day") -> dict:
    """Where the money went, per day or month: money in and out of the drawer and of every account, by kind
    (drawer payouts named by what they paid). Moves between the pharmacy's own places (shift close to the
    treasury, transfers) and opening balances are listed apart and kept out of the in / out totals."""
    ctx.require("cash.see_expected")
    if by not in ("day", "month"):
        raise ValidationFailed("اختر يومي أو شهري")
    from ..sales.service import _local_date
    from datetime import datetime, timezone
    d1, d2, lo, hi = _bounds(date_from, date_to)
    periods: dict[str, dict] = {}

    def add(day: date, place: str, kind: str, amount: int) -> None:
        key = day.isoformat() if by == "day" else day.isoformat()[:7]
        p = periods.setdefault(key, {"period": key, "in": 0, "out": 0, "by_kind": {}, "by_place": {}})
        k = p["by_kind"].setdefault(kind, {"kind": kind, "label": KINDS.get(kind) or SUMMARY_EXTRA.get(kind, kind),
                                           "in": 0, "out": 0, "flow": kind not in NOT_A_FLOW})
        pl = p["by_place"].setdefault(place, {"place": place, "in": 0, "out": 0})
        side = "in" if amount > 0 else "out"
        for x in ((k, pl) if kind in NOT_A_FLOW else (p, k, pl)):
            x[side] += abs(amount)

    for r in db.all("SELECT m.amount, m.kind, m.meta, m.created_at, a.name FROM money_movements m JOIN money_accounts a"
                    " ON a.id=m.account_id AND a.org_id=m.org_id WHERE m.org_id=? AND m.created_at>=? AND m.created_at<?", (ctx.org_id, lo, hi)):
        kind = r["kind"]
        if kind == "count_difference" and r["meta"] and json.loads(r["meta"]).get("against") == "old_program":
            kind = "old_program_difference"
        add(_local_date(r["created_at"]), r["name"], kind, r["amount"])
    # the drawer's sales cash by hour (a day can hold thousands of sales), its payouts one by one (few)
    for r in db.all("SELECT m.kind, substr(m.created_at,1,13) h, sum(m.amount) amount FROM cash_movements m"
                    " JOIN cash_shifts s ON s.id=m.shift_id WHERE m.org_id=? AND s.branch_id=?"
                    " AND m.kind IN ('sale','customer_payment','refund','void') AND m.created_at>=? AND m.created_at<?"
                    " GROUP BY 1,2", (ctx.org_id, ctx.branch_id, lo, hi)):
        if r["amount"]:
            at = datetime.strptime(r["h"], "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).astimezone().date()
            add(at, DRAWER_NAME, _drawer_kind(r), r["amount"])
    for r in db.all(
            "SELECT m.kind, m.amount, m.created_at, m.note,"
            " EXISTS(SELECT 1 FROM expenses e WHERE e.id=m.ref_id AND e.org_id=m.org_id) is_expense,"
            " EXISTS(SELECT 1 FROM supplier_ledger l WHERE l.id=m.ref_id AND l.org_id=m.org_id) is_supplier,"
            " EXISTS(SELECT 1 FROM staff_advances v WHERE v.id=m.ref_id AND v.org_id=m.org_id) is_advance,"
            " EXISTS(SELECT 1 FROM money_movements t WHERE t.org_id=m.org_id AND t.ref_id=m.ref_id"
            "  AND t.kind='transfer') is_transfer"
            " FROM cash_movements m JOIN cash_shifts s ON s.id=m.shift_id WHERE m.org_id=? AND s.branch_id=?"
            " AND m.kind IN ('payout','deposit') AND m.created_at>=? AND m.created_at<?",
            (ctx.org_id, ctx.branch_id, lo, hi)):
        add(_local_date(r["created_at"]), DRAWER_NAME, _drawer_kind(r), r["amount"])
    out = []
    for key in sorted(periods):
        p = periods[key]
        out.append({**p, "net": p["in"] - p["out"],
                    "by_kind": sorted(p["by_kind"].values(), key=lambda x: (not x["flow"], -(x["in"] + x["out"]))),
                    "by_place": sorted(p["by_place"].values(), key=lambda x: x["place"])})
    return {"from": d1.isoformat(), "to": d2.isoformat(), "by": by, "periods": out,
            "total_in": sum(p["in"] for p in out), "total_out": sum(p["out"] for p in out)}
