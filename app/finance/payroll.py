"""Attendance (check in / out from the pharmacy PC or a phone on the pharmacy Wi-Fi) and payroll.

Salary of a month = base − absence (base / work days × missing days) − advances + bonus − deduction.
The owner reviews and approves each line; paying it records an approved expense under
"رواتب وأجور" (from the drawer or from outside), so the profit report includes it."""
from __future__ import annotations

import calendar
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from ..core import audit
from ..core.errors import Conflict, NotFound, ValidationFailed, Forbidden
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
        " ON p.user_id=u.id AND p.org_id=m.org_id WHERE m.org_id=? AND r.code<>'owner'"
        # a person whose login was stopped stays on the sheet of a month he has attendance, an advance or a
        # salary in, so his final month can be approved and paid (M2-15)
        " AND (m.is_active=1 OR EXISTS(SELECT 1 FROM attendance a WHERE a.org_id=m.org_id AND a.user_id=u.id"
        "  AND substr(a.day,1,7)=?) OR EXISTS(SELECT 1 FROM staff_advances v WHERE v.org_id=m.org_id AND v.user_id=u.id"
        "  AND v.month=?) OR EXISTS(SELECT 1 FROM payroll y WHERE y.org_id=m.org_id AND y.user_id=u.id AND y.month=?))"
        " ORDER BY u.full_name", (ctx.org_id, month, month, month))]
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
    """Exactly YYYY-MM: "2026-1" or " 2026-10" named a month no salary is ever computed for, so an advance
    booked on it was never deducted."""
    if not isinstance(month, str) or not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", month):
        raise ValidationFailed("الشهر غير صحيح")
    try:
        y, m = int(month[:4]), int(month[5:])
        calendar.monthrange(y, m)
        return y, m
    except (ValueError, TypeError):
        raise ValidationFailed("الشهر غير صحيح")


def sheet_month(month: str | None) -> str:
    """The month whose sheet answers an action that is not about a month (pay settings, an attendance fix):
    the one sent, checked before anything is saved, or the current month when none was sent."""
    if month in (None, ""):
        return local_today().isoformat()[:7]
    _month(month)
    return month


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
    if not paid_from or not isinstance(paid_from, str):
        raise ValidationFailed("اختر مصدر المبلغ المدفوع")
    from . import money
    src = money.check_source(db, ctx, paid_from)
    with db.transaction():
        # a month already paid cannot take a new advance: it would never be deducted or booked
        if db.one("SELECT 1 FROM payroll WHERE org_id=? AND user_id=? AND month=? AND status='paid'",
                  (ctx.org_id, uid, month)):
            raise Conflict("راتب هذا الشهر صُرف بالفعل؛ سجّل السلفة على الشهر التالي", code="payroll_paid")
        aid = new_id()
        acc = None if src in (money.DRAWER, money.OUTSIDE) else src
        db.execute("INSERT INTO staff_advances (id, org_id, user_id, amount, month, note, created_by, account_id)"
                   " VALUES (?,?,?,?,?,?,?,?)",
                   (aid, ctx.org_id, uid, amount, month, (note or "").strip()[:200] or None, ctx.user_id, acc))
        name = db.one("SELECT full_name FROM users WHERE id=?", (uid,))["full_name"]
        money.pay_out(db, ctx, src, amount, "payroll", ref_type="staff_advance", ref_id=aid, note=f"سلفة: {name}")
        audit.record(db, ctx, "payroll.advance", "user", uid, after={"amount": amount, "month": month, "from": src})


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
        # a salary paid meanwhile from another screen keeps the figures it was paid with
        if db.one("SELECT 1 FROM payroll WHERE org_id=? AND user_id=? AND month=? AND status='paid'",
                  (ctx.org_id, uid, month)):
            raise Conflict("هذا الراتب صُرف بالفعل")
        db.execute("INSERT INTO payroll (id, org_id, user_id, month, base, work_days, days_present, absence_deduction, advances,"
                   " bonus, deduction, net, note, approved_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                   " ON CONFLICT(org_id, user_id, month) DO UPDATE SET base=excluded.base, work_days=excluded.work_days,"
                   " days_present=excluded.days_present, absence_deduction=excluded.absence_deduction, advances=excluded.advances,"
                   " bonus=excluded.bonus, deduction=excluded.deduction, net=excluded.net, note=excluded.note, approved_by=excluded.approved_by",
                   (new_id(), ctx.org_id, uid, month, c["base"], p["work_days"], p["days_present"], c["absence_deduction"],
                    c["advances"], bonus, deduction, c["net"], (note or "").strip() or None, ctx.user_id))
        audit.record(db, ctx, "payroll.approve", "user", uid, after={"month": month, **c})
    return month_sheet(db, ctx, month)


def pay(db: Database, ctx: Context, uid: str, month: str, paid_from: str, expected_net: int | None = None) -> dict:
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
        # again under the write lock: two clicks from two screens must not pay the salary twice
        pr = db.one("SELECT * FROM payroll WHERE org_id=? AND user_id=? AND month=?", (ctx.org_id, uid, month))
        if pr["status"] == "paid":
            raise Conflict("هذا الراتب صُرف بالفعل")
        # an advance given after the approval would be paid again in full and never booked: approve again first
        now_adv = db.one("SELECT coalesce(sum(amount),0) a FROM staff_advances WHERE org_id=? AND user_id=? AND month=?",
                         (ctx.org_id, uid, month))["a"]
        if now_adv != pr["advances"]:
            raise Conflict("تغيّرت السلف بعد اعتماد الراتب؛ اعتمد الراتب مرة أخرى قبل الصرف", code="payroll_changed",
                           details={"approved_advances": pr["advances"], "advances_now": now_adv})
        # the approval as it was shown, and still made from today's salary and attendance (M2-14)
        if expected_net is not None and expected_net != pr["net"]:
            raise Conflict("الراتب اتعدّل من شاشة تانية؛ حدّث الشاشة قبل الصرف", code="payroll_changed",
                           details={"net": pr["net"]})
        cur = db.one("SELECT coalesce(p.monthly_salary,0) s, coalesce(p.work_days,26) w FROM memberships m"
                     " LEFT JOIN staff_pay p ON p.org_id=m.org_id AND p.user_id=m.user_id WHERE m.org_id=? AND m.user_id=?",
                     (ctx.org_id, uid))
        present = db.one("SELECT count(DISTINCT day) d FROM attendance WHERE org_id=? AND user_id=? AND substr(day,1,7)=?",
                         (ctx.org_id, uid, month))["d"]
        if cur and (cur["s"], cur["w"], present) != (pr["base"], pr["work_days"], pr["days_present"]):
            raise Conflict("الحضور أو المرتب اتغير بعد الاعتماد؛ اعتمد الراتب مرة أخرى قبل الصرف", code="payroll_changed")
        # the salary expense must be approved now: never pay out cash for an expense left pending (M2-13)
        for amount in (pr["net"], pr["advances"]):
            if amount > 0 and not fin._can_approve(db, ctx, amount):
                raise Forbidden(f"الراتب أكبر من حد المدير ({fin.manager_limit(db, ctx.org_id) / 100:,.0f} ج) — يحتاج إلى المالك",
                                code="approval_required", details={"permission": "users.manage", "label": "صرف راتب كبير"})
        # money that never passed through the pharmacy's accounts is the owner's decision (as for expenses): a
        # manager's salary "from outside" would be marked paid with its expense still pending (#348 Codex)
        from . import money
        if paid_from == money.OUTSIDE and pr["net"] > 0 and not money.is_owner(ctx):
            raise Forbidden("راتب من خارج حسابات البرنامج يصرفه المالك", code="approval_required",
                            details={"permission": "users.manage", "label": "صرف راتب من خارج الحسابات"})
        _pay_tx(db, ctx, pr, name, cat["id"], month, uid, paid_from)
    return month_sheet(db, ctx, month)


def _pay_tx(db: Database, ctx: Context, pr, name: str, cat_id: str, month: str, uid: str, paid_from: str) -> None:
    from . import expenses as fin
    eid = None
    y, m = _month(month)
    last = date(y, m, calendar.monthrange(y, m)[1])
    spent = min(last, local_today()).isoformat()
    if pr["net"] > 0:
        eid = fin.record(db, ctx, category_id=cat_id, amount=pr["net"], paid_from=paid_from,
                         note=f"مرتب {name} — {month}", spent_on=spent, kind="payroll")["id"]
    # advances were already paid out when given: book them as salary expense of that month too, also when
    # they took the whole salary and nothing is left to pay now
    if pr["advances"]:
        fin.record(db, ctx, category_id=cat_id, amount=pr["advances"], paid_from="outside",
                   note=f"سلف {name} — {month} (صُرفت من قبل)", spent_on=spent, move_money=False)
    db.execute("UPDATE payroll SET status='paid', paid_at=?, expense_id=? WHERE id=?", (_now(), eid, pr["id"]))
    audit.record(db, ctx, "payroll.pay", "user", uid, after={"month": month, "net": pr["net"], "from": paid_from})
