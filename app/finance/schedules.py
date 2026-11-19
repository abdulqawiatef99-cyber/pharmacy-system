"""Expenses due on a schedule (rent, electricity, internet, insurance...).

Each item has its due day, how often (every 1/2/3/6/12 months), and how many days before
the due day the owner wants a reminder (his choice per item). Paying it goes through the
normal expense flow (approval, drawer), linked to the schedule and the period, so the
reminder stops by itself."""
from __future__ import annotations

import calendar
import json
import re
from datetime import date

from ..core import audit, notify
from ..core.errors import Conflict, NotFound, ValidationFailed
from ..core.numbers import whole_number
from ..core.permissions import Context
from ..db import Database, new_id
from . import expenses as fin

EVERY = (1, 2, 3, 6, 12)


def _due(period: str, day: int) -> date:
    y, m = map(int, period.split("-"))
    return date(y, m, min(day, calendar.monthrange(y, m)[1]))


def _add(period: str, months: int) -> str:
    y, m = map(int, period.split("-"))
    m += months
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


# a period counts as paid unless its expense was rejected before any money left: a rejected payment whose
# cash already left the drawer stays paid, so the same bill is never offered (and paid) a second time (M2-12)
TAKEN = "(status<>'rejected' OR shift_id IS NOT NULL)"


def next_open(db: Database, s: dict, today: date) -> dict | None:
    """First period not paid (or pending) and not skipped, looking up to a year ahead."""
    skipped = set(json.loads(s["skipped"] or "[]"))
    done = {r["schedule_period"] for r in db.all(f"SELECT schedule_period FROM expenses WHERE schedule_id=? AND {TAKEN}",
                                                 (s["id"],))}
    p, limit = s["start_month"], _add(f"{today.year:04d}-{today.month:02d}", 12)
    while p <= limit:
        if p not in done and p not in skipped:
            d = _due(p, s["day_of_month"])
            days = (d - today).days
            state = "late" if days < 0 else "soon" if days <= s["remind_days"] else "later"
            return {"period": p, "due": d.isoformat(), "days": days, "state": state}
        p = _add(p, s["every_months"])
    return None


def listing(db: Database, ctx: Context, today: date | None = None) -> list[dict]:
    ctx.require("expenses.approve")
    today = today or date.today()
    out = []
    for r in db.all("SELECT s.*, c.name category FROM expense_schedules s JOIN expense_categories c ON c.id=s.category_id"
                    " WHERE s.org_id=? ORDER BY s.is_active DESC, s.day_of_month, s.name", (ctx.org_id,)):
        d = dict(r)
        d["next"] = next_open(db, d, today) if d["is_active"] else None
        d["last_paid"] = db.one(f"SELECT max(schedule_period) p FROM expenses WHERE schedule_id=? AND {TAKEN}",
                                (d["id"],))["p"]
        out.append(d)
    order = {"late": 0, "soon": 1, "later": 2}
    out.sort(key=lambda d: (not d["is_active"], order.get((d["next"] or {}).get("state"), 3), (d["next"] or {}).get("due") or "9"))
    return out


def save(db: Database, ctx: Context, data: dict, sid: str | None = None) -> dict:
    ctx.require("expenses.approve")
    cur = None
    if sid:
        cur = db.one("SELECT * FROM expense_schedules WHERE id=? AND org_id=?", (sid, ctx.org_id))
        if not cur:
            raise NotFound("البند المجدول غير موجود")
    v = dict(cur) if cur else {"remind_days": 3, "every_months": 1, "paid_from": "outside", "account_id": None, "is_active": 1,
                               "start_month": date.today().strftime("%Y-%m"), "amount": None, "note": None}
    for k in ("name", "category_id", "amount", "day_of_month", "every_months", "start_month", "remind_days",
              "paid_from", "account_id", "note", "is_active"):
        if k in data:
            v[k] = data[k]
    if "paid_from" in data and "account_id" not in data:
        v["account_id"] = None               # a new source replaces the old account (#348 Codex P2)
    v["name"] = str(v.get("name") or "").strip()[:80]
    if len(v["name"]) < 2:
        raise ValidationFailed("اكتب اسم البند (مثال: إيجار الصيدلية)")
    if not db.one("SELECT 1 FROM expense_categories WHERE id=? AND org_id=?", (v.get("category_id"), ctx.org_id)):
        raise ValidationFailed("اختر بند المصروف")
    if v["amount"] in ("", 0):
        v["amount"] = None
    if v["amount"] is not None and (isinstance(v["amount"], bool) or not isinstance(v["amount"], int)
                                    or not 0 < v["amount"] <= 2 ** 53 - 1):          # true is not 1 piaster
        raise ValidationFailed("المبلغ غير صحيح")
    try:
        # 15.5 or true is refused, never cut to 15 or 1
        v["day_of_month"], v["every_months"], v["remind_days"] = (whole_number(v["day_of_month"]), whole_number(v["every_months"]),
                                                                  whole_number(v["remind_days"]))
    except (TypeError, ValueError, KeyError):
        raise ValidationFailed("اكتب يوم الاستحقاق وعدد أيام التذكير")
    if not 1 <= v["day_of_month"] <= 31:
        raise ValidationFailed("يوم الاستحقاق من 1 إلى 31")
    if v["every_months"] not in EVERY:
        raise ValidationFailed("التكرار غير صحيح")
    if not 0 <= v["remind_days"] <= 60:
        raise ValidationFailed("التذكير من 0 إلى 60 يومًا قبل الموعد")
    # the source: the drawer, a money account (F105), or outside the program's accounts
    src = v.get("account_id") if v.get("paid_from") == "outside" and v.get("account_id") else v.get("paid_from")
    if src not in ("drawer", "outside"):
        from . import money
        try:                                  # the placeholder 'treasury' makes the treasury on this first write
            src = money.writable(db, ctx, str(src or ""))
        except NotFound:
            raise ValidationFailed("اختر مصدر الدفع")
        v["paid_from"], v["account_id"] = "outside", src
    else:
        v["paid_from"], v["account_id"] = src, None
    try:
        date.fromisoformat(str(v["start_month"]) + "-01")
    except ValueError:
        raise ValidationFailed("أول شهر غير صحيح")
    v["note"] = (str(v.get("note") or "").strip()[:200]) or None
    v["is_active"] = int(bool(v["is_active"]))
    cols = ("name", "category_id", "amount", "day_of_month", "every_months", "start_month", "remind_days", "paid_from",
            "account_id", "note", "is_active")
    with db.transaction():
        if cur:
            db.execute(f"UPDATE expense_schedules SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?", (*[v[k] for k in cols], sid))
            audit.record(db, ctx, "expense_schedule.update", "expense_schedule", sid,
                         before={k: cur[k] for k in cols}, after={k: v[k] for k in cols})
        else:
            sid = new_id()
            db.execute(f"INSERT INTO expense_schedules (id, org_id, created_by, {', '.join(cols)}) VALUES (?,?,?{',?' * len(cols)})",
                       (sid, ctx.org_id, ctx.user_id, *[v[k] for k in cols]))
            audit.record(db, ctx, "expense_schedule.create", "expense_schedule", sid, after={k: v[k] for k in cols})
    return next(s for s in listing(db, ctx) if s["id"] == sid)


def _get(db: Database, ctx: Context, sid: str) -> dict:
    s = db.one("SELECT * FROM expense_schedules WHERE id=? AND org_id=?", (sid, ctx.org_id))
    if not s:
        raise NotFound("البند المجدول غير موجود")
    return dict(s)


def _own_period(s: dict, period) -> str:
    """The month sent by the screen, only if it is written YYYY-MM and is one of this item's due months."""
    if not isinstance(period, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period):
        raise ValidationFailed("الشهر غير صحيح")
    (y, m), (y0, m0) = (map(int, x.split("-")) for x in (period, s["start_month"]))
    months = (y - y0) * 12 + (m - m0)
    if months < 0 or months % s["every_months"]:
        raise ValidationFailed("هذا الشهر ليس من مواعيد هذا البند")
    return period


def pay(db: Database, ctx: Context, sid: str, amount: int | None = None, paid_from: str | None = None,
        note: str | None = None, period: str | None = None, expected_amount: int | None = None) -> dict:
    """Records one period's expense (normal approval rules) and moves the schedule on. With `period` (the month
    the screen showed) exactly that month is paid or refused; a second press never pays the month after it."""
    ctx.require("expenses.approve")
    s = _get(db, ctx, sid)
    if period is not None:
        period = _own_period(s, period)
    else:
        period = (next_open(db, s, date.today()) or {}).get("period")
    if not period:
        raise ValidationFailed("لا يوجد موعد مفتوح لهذا البند")
    amount = amount if amount is not None else s["amount"]
    if not amount:
        raise ValidationFailed("اكتب المبلغ هذه المرة")
    taken = (f"SELECT 1 FROM expenses WHERE org_id=? AND schedule_id=? AND schedule_period=? AND {TAKEN}",
             (ctx.org_id, sid, period))
    if db.one(*taken):
        raise ValidationFailed("هذا الشهر سُجّل من قبل")
    with db.transaction():
        # again under the write lock: two screens must not record this period twice
        if db.one(*taken):
            raise ValidationFailed("هذا الشهر سُجّل من قبل")
        # the bill as it is now, not as an old screen showed it (M2-11)
        now = _get(db, ctx, sid)
        if not now["is_active"]:
            raise Conflict("البند ده اتوقف؛ حدّث الشاشة", code="schedule_changed")
        if period in set(json.loads(now["skipped"] or "[]")):
            raise Conflict("الشهر ده اتعلّم «ليس هذه المرة»؛ حدّث الشاشة", code="schedule_changed")
        if expected_amount is not None and (amount if amount is not None else now["amount"]) != expected_amount:
            raise Conflict("مبلغ البند اتغير من شاشة تانية؛ حدّث الشاشة", code="schedule_changed",
                           details={"amount": now["amount"]})
        e = fin.record(db, ctx, category_id=s["category_id"], amount=amount,
                       paid_from=paid_from or s.get("account_id") or s["paid_from"],
                       note=(note or s["name"]) + f" ({period})", kind="schedule")
        db.execute("UPDATE expenses SET schedule_id=?, schedule_period=? WHERE id=?", (sid, period, e["id"]))
    return {**fin.get(db, ctx, e["id"]), "schedule_period": period}


def skip(db: Database, ctx: Context, sid: str, period: str) -> list[dict]:
    """"Not this time" for one period (rent paid yearly in advance, a month without a bill)."""
    ctx.require("expenses.approve")
    s = _get(db, ctx, sid)
    try:
        date.fromisoformat(f"{period}-01")
    except (TypeError, ValueError):
        raise ValidationFailed("الشهر غير صحيح")
    sk = set(json.loads(s["skipped"] or "[]")) | {period}
    with db.transaction():
        db.execute("UPDATE expense_schedules SET skipped=? WHERE id=?", (json.dumps(sorted(sk)), sid))
        audit.record(db, ctx, "expense_schedule.skip", "expense_schedule", sid, after={"period": period})
    return listing(db, ctx)


def remind(db: Database, ctx: Context, today: date, state: dict) -> int:
    """Called once a day: one reminder when an item enters its reminder window, one more if
    its day passes unpaid. `state` remembers what was sent."""
    sent = 0
    seen = state.setdefault("exp_sched", {})
    for r in db.all("SELECT * FROM expense_schedules WHERE org_id=? AND is_active=1", (ctx.org_id,)):
        s = dict(r)
        n = next_open(db, s, today)
        if not n or n["state"] == "later":
            continue
        key = f"{s['id']}:{n['period']}:{n['state']}"
        if key in seen:
            continue
        amt = f" — {s['amount'] / 100:,.2f} ج" if s["amount"] else ""
        with db.transaction():
            if n["state"] == "late":
                notify.emit(db, ctx, "expense_due", "error", f"موعد {s['name']} فات ولم يُسجَّل{amt}",
                            f"كان يوم {n['due']}", "expense_schedule", s["id"])
            else:
                when = "اليوم" if n["days"] == 0 else "غدًا" if n["days"] == 1 else f"بعد {n['days']} يوم"
                notify.emit(db, ctx, "expense_due", "warning", f"{s['name']} ميعاده {when}{amt}",
                            f"يوم {n['due']}", "expense_schedule", s["id"])
        seen[key] = today.isoformat()
        sent += 1
    state["exp_sched"] = {k: v for k, v in seen.items() if (today - date.fromisoformat(v)).days < 400}
    return sent
