"""Attendance (check in / out from the pharmacy PC or a phone on the pharmacy Wi-Fi) and payroll.

Salary of a month = base − absence (base / work days × missing days) − advances + bonus − deduction.
The owner reviews and approves each line; paying it records an approved expense under
"رواتب وأجور" (from the drawer or from outside), so the profit report includes it."""
from __future__ import annotations

import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from ..core import audit
from ..core.errors import Conflict, NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id
from ..sales.service import _local_date, local_today


def _now() -> str:
    from ..core.accounts import iso, utcnow
    return iso(utcnow())


def _member(db: Database, ctx: Context, uid: str) -> None:
    if not db.one("SELECT 1 FROM memberships WHERE org_id=? AND user_id=?", (ctx.org_id, uid)):
        raise NotFound("الموظف غير موجود")


# -------------------------------------------------------------------------- attendance
def status(db: Database, ctx: Context) -> dict:
    today = local_today().isoformat()
    open_row = db.one("SELECT * FROM attendance WHERE org_id=? AND user_id=? AND check_out IS NULL ORDER BY check_in DESC LIMIT 1",
                      (ctx.org_id, ctx.user_id))
    month = today[:7]
    days = db.one("SELECT count(DISTINCT day) d FROM attendance WHERE org_id=? AND user_id=? AND substr(day,1,7)=?",
                  (ctx.org_id, ctx.user_id, month))["d"]
    rows = [dict(r) for r in db.all("SELECT day, check_in, check_out, source FROM attendance WHERE org_id=? AND user_id=?"
                                    " AND substr(day,1,7)=? ORDER BY check_in DESC", (ctx.org_id, ctx.user_id, month))]
    return {"open": dict(open_row) if open_row else None, "today": today, "days_this_month": days, "rows": rows}


def check(db: Database, ctx: Context, action: str, source: str = "pc") -> dict:
    if action not in ("in", "out"):
        raise ValidationFailed("العملية غير صحيحة")
    st = status(db, ctx)
    with db.transaction():
        if action == "in":
            if st["open"]:
                raise Conflict("أنت مسجل حضورًا بالفعل", code="already_in")
            db.execute("INSERT INTO attendance (id, org_id, user_id, day, check_in, source) VALUES (?,?,?,?,?,?)",
                       (new_id(), ctx.org_id, ctx.user_id, st["today"], _now(), source[:10]))
        else:
            if not st["open"]:
                raise Conflict("لا يوجد حضور مفتوح", code="not_in")
            db.execute("UPDATE attendance SET check_out=? WHERE id=?", (_now(), st["open"]["id"]))
    return status(db, ctx)


def month_sheet(db: Database, ctx: Context, month: str) -> dict:
    ctx.require("payroll.manage")
    _month(month)
    people = [dict(r) for r in db.all(
        "SELECT u.id, u.full_name, r.name role_name, coalesce(p.monthly_salary,0) monthly_salary, coalesce(p.work_days,26) work_days"
        " FROM memberships m JOIN users u ON u.id=m.user_id JOIN roles r ON r.id=m.role_id LEFT JOIN staff_pay p"
        " ON p.user_id=u.id AND p.org_id=m.org_id WHERE m.org_id=? AND m.is_active=1 AND r.code<>'owner' ORDER BY u.full_name",
        (ctx.org_id,))]
    for p in people:
        p["days_present"] = db.one("SELECT count(DISTINCT day) d FROM attendance WHERE org_id=? AND user_id=? AND substr(day,1,7)=?",
                                   (ctx.org_id, p["id"], month))["d"]
        p["hours"] = round(db.one(
            "SELECT coalesce(sum((julianday(check_out) - julianday(check_in)) * 24),0) h FROM attendance WHERE org_id=? AND user_id=?"
            " AND substr(day,1,7)=? AND check_out IS NOT NULL", (ctx.org_id, p["id"], month))["h"], 1)
        p["advances"] = db.one("SELECT coalesce(sum(amount),0) a FROM staff_advances WHERE org_id=? AND user_id=? AND month=?",
                               (ctx.org_id, p["id"], month))["a"]
        pr = db.one("SELECT * FROM payroll WHERE org_id=? AND user_id=? AND month=?", (ctx.org_id, p["id"], month))
        p["payroll"] = dict(pr) if pr else None
        p["suggested"] = compute(p["monthly_salary"], p["work_days"], p["days_present"], p["advances"])
        p["attendance"] = [dict(r) for r in db.all(
            "SELECT id, day, check_in, check_out, source, note, edited_by FROM attendance WHERE org_id=? AND user_id=? AND"
            " substr(day,1,7)=? ORDER BY check_in", (ctx.org_id, p["id"], month))]
    return {"month": month, "people": people}


def _month(month: str) -> tuple[int, int]:
    try:
        y, m = (int(x) for x in month.split("-"))
        calendar.monthrange(y, m)
        return y, m
    except (ValueError, TypeError):
        raise ValidationFailed("الشهر غير صحيح")


def compute(base: int, work_days: int, present: int, advances: int, bonus: int = 0, deduction: int = 0) -> dict:
    missing = max(0, work_days - present)
    # whole piasters, half up like every other money rounding (round() of a float went half to even)
    absence = int((Decimal(base) * missing / work_days).quantize(Decimal("1"), ROUND_HALF_UP)) if work_days else 0
    return {"base": base, "absence_deduction": absence, "advances": advances, "bonus": bonus, "deduction": deduction,
            "net": base - absence - advances + bonus - deduction, "missing_days": missing}


def set_pay(db: Database, ctx: Context, uid: str, monthly_salary: int, work_days: int) -> None:
    ctx.require("payroll.manage")
    _member(db, ctx, uid)
    if not isinstance(monthly_salary, int) or monthly_salary < 0 or not isinstance(work_days, int) or not 1 <= work_days <= 31:
        raise ValidationFailed("الراتب أو أيام العمل غير صحيحة")
    with db.transaction():
        db.execute("INSERT INTO staff_pay (org_id, user_id, monthly_salary, work_days) VALUES (?,?,?,?) ON CONFLICT(org_id, user_id)"
                   " DO UPDATE SET monthly_salary=excluded.monthly_salary, work_days=excluded.work_days",
                   (ctx.org_id, uid, monthly_salary, work_days))
        audit.record(db, ctx, "payroll.set_pay", "user", uid, after={"salary": monthly_salary, "work_days": work_days})


def add_advance(db: Database, ctx: Context, uid: str, amount: int, month: str, note: str | None, paid_from: str) -> None:
    """A salary advance (سلفة): paid now, deducted from the chosen month."""
    ctx.require("payroll.manage")
    _member(db, ctx, uid)
    _month(month)
    if not isinstance(amount, int) or amount <= 0:
        raise ValidationFailed("المبلغ غير صحيح")
    from ..sales import cash
    with db.transaction():
        aid = new_id()
        db.execute("INSERT INTO staff_advances (id, org_id, user_id, amount, month, note, created_by) VALUES (?,?,?,?,?,?,?)",
                   (aid, ctx.org_id, uid, amount, month, (note or "").strip()[:200] or None, ctx.user_id))
        if paid_from == "drawer":
            name = db.one("SELECT full_name FROM users WHERE id=?", (uid,))["full_name"]
            cash.record(db, ctx, -amount, "payout", aid, f"سلفة: {name}")
        audit.record(db, ctx, "payroll.advance", "user", uid, after={"amount": amount, "month": month, "from": paid_from})


def fix_attendance(db: Database, ctx: Context, uid: str, day: str, check_in: str, check_out: str | None, note: str) -> None:
    """Owner adds or corrects a day (forgot to check in, power cut...). Always with a note, always audited.
    Times are local 'HH:MM'."""
    ctx.require("payroll.manage")
    _member(db, ctx, uid)
    from datetime import datetime, timezone
    try:
        d = date.fromisoformat(day)
        to_utc = lambda hm: datetime.combine(d, datetime.strptime(hm, "%H:%M").time()).astimezone().astimezone(timezone.utc)  # noqa: E731
        t_in = to_utc(check_in)
        t_out = to_utc(check_out) if check_out else None
    except (ValueError, TypeError):
        raise ValidationFailed("اليوم أو الساعة غير صحيحة")
    if t_out and t_out <= t_in:
        raise ValidationFailed("يجب أن يكون الانصراف بعد الحضور")
    if len((note or "").strip()) < 3:
        raise ValidationFailed("اكتب سبب التعديل")
    f = "%Y-%m-%dT%H:%M:%S.%fZ"
    with db.transaction():
        db.execute("DELETE FROM attendance WHERE org_id=? AND user_id=? AND day=?", (ctx.org_id, uid, d.isoformat()))
        db.execute("INSERT INTO attendance (id, org_id, user_id, day, check_in, check_out, source, note, edited_by)"
                   " VALUES (?,?,?,?,?,?, 'owner', ?, ?)", (new_id(), ctx.org_id, uid, d.isoformat(), t_in.strftime(f),
                                                          t_out.strftime(f) if t_out else None, note.strip(), ctx.user_id))
        audit.record(db, ctx, "attendance.fix", "user", uid, after={"day": day, "in": check_in, "out": check_out, "note": note})


def approve(db: Database, ctx: Context, uid: str, month: str, bonus: int = 0, deduction: int = 0, note: str | None = None) -> dict:
    ctx.require("payroll.manage")
    _month(month)
    sheet = {p["id"]: p for p in month_sheet(db, ctx, month)["people"]}
    p = sheet.get(uid)
    if not p:
        raise NotFound("الموظف غير موجود")
    if p["payroll"] and p["payroll"]["status"] == "paid":
        raise Conflict("هذا الراتب صُرف بالفعل")
    if not isinstance(bonus, int) or not isinstance(deduction, int) or bonus < 0 or deduction < 0:
        raise ValidationFailed("المبالغ غير صحيحة")
    c = compute(p["monthly_salary"], p["work_days"], p["days_present"], p["advances"], bonus, deduction)
    with db.transaction():
        db.execute("INSERT INTO payroll (id, org_id, user_id, month, base, work_days, days_present, absence_deduction, advances,"
                   " bonus, deduction, net, note, approved_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                   " ON CONFLICT(org_id, user_id, month) DO UPDATE SET base=excluded.base, work_days=excluded.work_days,"
                   " days_present=excluded.days_present, absence_deduction=excluded.absence_deduction, advances=excluded.advances,"
                   " bonus=excluded.bonus, deduction=excluded.deduction, net=excluded.net, note=excluded.note, approved_by=excluded.approved_by",
                   (new_id(), ctx.org_id, uid, month, c["base"], p["work_days"], p["days_present"], c["absence_deduction"],
                    c["advances"], bonus, deduction, c["net"], (note or "").strip() or None, ctx.user_id))
        audit.record(db, ctx, "payroll.approve", "user", uid, after={"month": month, **c})
    return month_sheet(db, ctx, month)


def pay(db: Database, ctx: Context, uid: str, month: str, paid_from: str) -> dict:
    ctx.require("payroll.manage")
    pr = db.one("SELECT * FROM payroll WHERE org_id=? AND user_id=? AND month=?", (ctx.org_id, uid, month))
    if not pr:
        raise ValidationFailed("اعتمد الراتب أولًا")
    if pr["status"] == "paid":
        raise Conflict("هذا الراتب صُرف بالفعل")
    from . import expenses as fin
    cat = db.one("SELECT id FROM expense_categories WHERE org_id=? AND name LIKE 'رواتب%' ORDER BY sort LIMIT 1", (ctx.org_id,))
    if not cat:
        fin.ensure_defaults(db)
        cat = db.one("SELECT id FROM expense_categories WHERE org_id=? ORDER BY sort LIMIT 1", (ctx.org_id,))
    name = db.one("SELECT full_name FROM users WHERE id=?", (uid,))["full_name"]
    with db.transaction():          # the expense and the "paid" mark together, or neither
        _pay_tx(db, ctx, pr, name, cat["id"], month, uid, paid_from)
    return month_sheet(db, ctx, month)


def _pay_tx(db: Database, ctx: Context, pr, name: str, cat_id: str, month: str, uid: str, paid_from: str) -> None:
    from . import expenses as fin
    eid = None
    if pr["net"] > 0:
        y, m = _month(month)
        last = date(y, m, calendar.monthrange(y, m)[1])
        spent = min(last, local_today()).isoformat()
        eid = fin.record(db, ctx, category_id=cat_id, amount=pr["net"], paid_from=paid_from,
                         note=f"مرتب {name} — {month}", spent_on=spent)["id"]
        # advances were already paid out when given: book them as salary expense of that month too
        if pr["advances"]:
            fin.record(db, ctx, category_id=cat_id, amount=pr["advances"], paid_from="outside",
                       note=f"سلف {name} — {month} (صُرفت من قبل)", spent_on=spent)
    db.execute("UPDATE payroll SET status='paid', paid_at=?, expense_id=? WHERE id=?", (_now(), eid, pr["id"]))
    audit.record(db, ctx, "payroll.pay", "user", uid, after={"month": month, "net": pr["net"], "from": paid_from})
