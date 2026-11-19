"""Expenses and consumables, and the profit report.

Anyone allowed records an expense; it counts in the accounts only after the owner (or a
manager, up to his limit) approves it. Cash taken from the drawer leaves the drawer when it
is recorded — that is what physically happened — and a rejected one is flagged to the owner.
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from ..core import audit, notify
from ..core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id
from ..sales import cash

DEFAULT_CATEGORIES = ["إيجار", "كهرباء ومياه", "رواتب وأجور", "مستهلكات (أكياس، إيصالات، حبر)", "نظافة",
                      "صيانة", "مواصلات وتوصيل", "تليفون وإنترنت", "ضرائب ورسوم", "أخرى"]
MANAGER_LIMIT_DEFAULT = 50000          # 500 EGP


def ensure_defaults(db: Database) -> None:
    for o in db.all("SELECT id FROM organizations"):
        if db.one("SELECT 1 FROM expense_categories WHERE org_id=? LIMIT 1", (o["id"],)):
            continue
        with db.transaction():
            for i, n in enumerate(DEFAULT_CATEGORIES):
                db.execute("INSERT INTO expense_categories (id, org_id, name, sort) VALUES (?,?,?,?)", (new_id(), o["id"], n, i))


def manager_limit(db: Database, org_id: str) -> int:
    r = db.one("SELECT value FROM org_settings WHERE org_id=? AND key='manager_expense_limit'", (org_id,))
    try:
        return int(r["value"]) if r else MANAGER_LIMIT_DEFAULT
    except ValueError:
        return MANAGER_LIMIT_DEFAULT


def _can_approve(db: Database, ctx: Context, amount: int) -> bool:
    if ctx.role_code == "owner" or (ctx.approver_id and "users.manage" in ctx.approver_permissions):
        return True
    return ctx.allowed("expenses.approve") and amount <= manager_limit(db, ctx.org_id)


def categories(db: Database, ctx: Context) -> list[dict]:
    ctx.require("expenses.record")
    return [dict(r) for r in db.all("SELECT * FROM expense_categories WHERE org_id=? AND is_active=1 ORDER BY sort, name",
                                    (ctx.org_id,))]


def add_category(db: Database, ctx: Context, name: str) -> list[dict]:
    ctx.require("expenses.approve")
    name = (name or "").strip()
    if len(name) < 2:
        raise ValidationFailed("اكتب اسم البند")
    with db.transaction():
        try:
            db.execute("INSERT INTO expense_categories (id, org_id, name, sort) VALUES (?,?,?,"
                       " (SELECT coalesce(max(sort),0)+1 FROM expense_categories WHERE org_id=?))",
                       (new_id(), ctx.org_id, name, ctx.org_id))
        except Exception as e:
            if "UNIQUE" in str(e):
                raise Conflict("البند موجود") from e
            raise
    return categories(db, ctx)


def record(db: Database, ctx: Context, *, category_id: str, amount: int, paid_from: str, note: str | None = None,
           spent_on: str | None = None, kind: str = "expense", move_money: bool = True) -> dict:
    """paid_from: 'drawer', a money account id (F105: the account goes down, never below zero) or 'outside'
    (outside the program's accounts: the owner's choice only). move_money=False books an expense whose money
    already left (salary advances paid earlier): nothing moves and no source is asked."""
    ctx.require("expenses.record")
    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        raise ValidationFailed("المبلغ غير صحيح")
    if not paid_from or not isinstance(paid_from, str):
        raise ValidationFailed("اختر مصدر المبلغ المدفوع")
    from . import money
    # 'outside' by anyone but the owner (an employee paid from his own pocket) is allowed, but always waits
    # for the owner: only he decides money that never passed through the pharmacy's accounts
    src = (money.OUTSIDE if paid_from == money.OUTSIDE else money.check_source(db, ctx, paid_from)) \
        if move_money else money.OUTSIDE
    col = "drawer" if src == money.DRAWER else "outside"
    account_id = None if src in (money.DRAWER, money.OUTSIDE) else src
    try:
        day = date.fromisoformat(spent_on).isoformat() if spent_on else date.today().isoformat()
    except ValueError:
        raise ValidationFailed("التاريخ غير صحيح")
    cat = db.one("SELECT name FROM expense_categories WHERE id=? AND org_id=?", (category_id, ctx.org_id))
    if not cat:
        raise NotFound("البند غير موجود")
    eid = new_id()
    auto = _can_approve(db, ctx, amount) and (src != money.OUTSIDE or not move_money or money.is_owner(ctx))
    if kind == "expense" and cat["name"].startswith("ضرائب"):
        kind = "tax"
    with db.transaction():
        shift = None
        if move_money and src != money.OUTSIDE:
            shift = money.pay_out(db, ctx, src, amount, kind, ref_type="expense", ref_id=eid,
                                  note=f"مصروف: {cat['name']}" + (f" — {note.strip()}" if note else ""))["shift_id"]
        db.execute("INSERT INTO expenses (id, org_id, branch_id, category_id, amount, spent_on, paid_from, account_id, note,"
                   " status, shift_id, created_by, decided_by, decided_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,"
                   " CASE WHEN ? THEN strftime('%Y-%m-%dT%H:%M:%fZ','now') END)",
                   (eid, ctx.org_id, ctx.branch_id, category_id, amount, day, col, account_id,
                    (note or "").strip()[:200] or None, "approved" if auto else "pending", shift, ctx.user_id,
                    ctx.user_id if auto else None, int(auto)))
        where = money.source_label(db, ctx, src) if move_money else "صُرف من قبل"
        audit.record(db, ctx, "expense.record", "expense", eid,
                     after={"amount": amount, "category": cat["name"], "from": src if move_money else "booked",
                            "approved": auto})
        if not auto:
            notify.emit(db, ctx, "expense_pending", "warning", f"مصروف بانتظار موافقتك: {amount / 100:,.2f} ج — {cat['name']}",
                        (note or "") + f" · {where}", "expense", eid)
    return get(db, ctx, eid)


def get(db: Database, ctx: Context, eid: str) -> dict:
    r = db.one("SELECT e.*, c.name category, u.full_name by_name, d.full_name decided_name, a.name account FROM expenses e"
               " JOIN expense_categories c ON c.id=e.category_id LEFT JOIN users u ON u.id=e.created_by"
               " LEFT JOIN users d ON d.id=e.decided_by LEFT JOIN money_accounts a ON a.id=e.account_id AND a.org_id=e.org_id"
               " WHERE e.id=? AND e.org_id=?", (eid, ctx.org_id))
    if not r:
        raise NotFound("المصروف غير موجود")
    return dict(r)


def listing(db: Database, ctx: Context, *, status: str | None = None, month: str | None = None,
            date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    ctx.require("expenses.record")
    q = ("SELECT e.*, c.name category, u.full_name by_name, d.full_name decided_name, a.name account FROM expenses e"
         " JOIN expense_categories c ON c.id=e.category_id LEFT JOIN users u ON u.id=e.created_by"
         " LEFT JOIN users d ON d.id=e.decided_by LEFT JOIN money_accounts a ON a.id=e.account_id AND a.org_id=e.org_id"
         " WHERE e.org_id=?")
    p: list = [ctx.org_id]
    if not ctx.can("expenses.approve"):
        q += " AND e.created_by=?"           # employees see what they recorded
        p.append(ctx.user_id)
    if status:
        q += " AND e.status=?"
        p.append(status)
    if month:
        q += " AND substr(e.spent_on,1,7)=?"
        p.append(month)
    if date_from and date_to:
        q += " AND e.spent_on>=? AND e.spent_on<=?"
        p += [date_from[:10], date_to[:10]]
    q += " ORDER BY e.spent_on DESC, e.created_at DESC LIMIT 5000"
    return [dict(r) for r in db.all(q, tuple(p))]


def decide(db: Database, ctx: Context, eid: str, approve: bool, reason: str | None = None) -> dict:
    e = get(db, ctx, eid)
    if e["status"] != "pending":
        raise Conflict("سبق اتخاذ قرار في هذا المصروف")
    from . import money
    if approve and e["paid_from"] == "outside" and not e["account_id"] and not money.is_owner(ctx):
        raise Forbidden("مصروف من خارج حسابات البرنامج يعتمده المالك", code="approval_required",
                        details={"permission": "users.manage", "label": "اعتماد مصروف من خارج الحسابات"})
    if not _can_approve(db, ctx, e["amount"]):
        if ctx.allowed("expenses.approve"):
            raise Forbidden(f"المبلغ أكبر من حد المدير ({manager_limit(db, ctx.org_id) / 100:,.0f} ج) — يحتاج إلى المالك",
                            code="approval_required", details={"permission": "users.manage", "label": "اعتماد مصروف كبير"})
        ctx.require("expenses.approve")
    if not approve and len((reason or "").strip()) < 3:
        raise ValidationFailed("اكتب سبب الرفض")
    with db.transaction():
        # only while still pending: an approval and a rejection at the same moment cannot both stand
        n = db.execute("UPDATE expenses SET status=?, decided_by=?, decided_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
                       " reject_reason=? WHERE id=? AND org_id=? AND status='pending'",
                       ("approved" if approve else "rejected", ctx.approver_id or ctx.user_id,
                        None if approve else reason.strip(), eid, ctx.org_id)).rowcount
        if not n:
            raise Conflict("سبق اتخاذ قرار في هذا المصروف")
        audit.record(db, ctx, "expense.approve" if approve else "expense.reject", "expense", eid,
                     after={"amount": e["amount"], "reason": reason})
        if not approve and (e["paid_from"] == "drawer" or e["account_id"]):
            where = e["account"] or "الدرج"
            notify.emit(db, ctx, "expense_rejected", "error", f"مصروف رُفض بعد صرفه من {where}: {e['amount'] / 100:,.2f} ج",
                        f"{e['category']} — سجّله {e['by_name']} · السبب: {reason.strip()}", "expense", eid)
    return get(db, ctx, eid)


# -------------------------------------------------------------------------- profit
def profit(db: Database, ctx: Context, date_from: str, date_to: str) -> dict:
    """Owner only. Gross profit = sales (after discounts and returns) - cost of what was sold.
    Net profit = gross - approved expenses of the same period."""
    ctx.require("costs.read")
    from ..sales.service import local_day_bounds
    try:
        d1, d2 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except ValueError:
        raise ValidationFailed("الفترة غير صحيحة")
    if d2 < d1:
        raise ValidationFailed("الفترة غير صحيحة")
    lo, _ = local_day_bounds(d1)
    _, hi = local_day_bounds(d2)
    s = db.one("SELECT count(*) n, coalesce(sum(total - delivery_fee),0) sales, coalesce(sum(delivery_fee),0) fees,"
               " coalesce(sum(discount_total),0) discount, coalesce(sum(returned_total),0) returns FROM sales"
               " WHERE org_id=? AND branch_id=? AND status='completed' AND completed_at>=? AND completed_at<?",
               (ctx.org_id, ctx.branch_id, lo, hi))
    cost, unknown = Decimal(0), 0
    for a in db.all("SELECT a.unit_cost, a.qty_base - a.returned_qty q FROM sale_allocations a JOIN sale_lines l"
                    " ON l.id=a.sale_line_id JOIN sales x ON x.id=l.sale_id WHERE x.org_id=? AND x.branch_id=?"
                    " AND x.status='completed' AND x.completed_at>=? AND x.completed_at<?", (ctx.org_id, ctx.branch_id, lo, hi)):
        if a["unit_cost"] is None:
            unknown += 1
        else:
            cost += Decimal(a["unit_cost"]) * a["q"] * 100
    cost_p = int(cost.to_integral_value(ROUND_HALF_UP))
    from . import reconcile
    claim = reconcile.claim_discounts(db, ctx.org_id, d1.isoformat(), d2.isoformat())        # F108: lowers revenue
    supplier_disc = reconcile.discounts_received(db, ctx.org_id, d1.isoformat(), d2.isoformat())   # F106
    revenue = s["sales"] - s["returns"] - claim
    exp = [dict(r) for r in db.all(
        "SELECT c.name category, sum(e.amount) amount FROM expenses e JOIN expense_categories c ON c.id=e.category_id"
        " WHERE e.org_id=? AND e.branch_id=? AND e.status='approved' AND e.spent_on BETWEEN ? AND ? GROUP BY c.id ORDER BY 2 DESC",
        (ctx.org_id, ctx.branch_id, d1.isoformat(), d2.isoformat()))]
    pending = db.one("SELECT count(*) n, coalesce(sum(amount),0) a FROM expenses WHERE org_id=? AND branch_id=? AND"
                     " status='pending' AND spent_on BETWEEN ? AND ?", (ctx.org_id, ctx.branch_id, d1.isoformat(), d2.isoformat()))
    expenses_total = sum(e["amount"] for e in exp)
    gross = revenue - cost_p + s["fees"]
    return {"from": d1.isoformat(), "to": d2.isoformat(), "invoices": s["n"], "sales": s["sales"], "discount": s["discount"],
            "returns": s["returns"], "revenue": revenue, "delivery_fees": s["fees"], "cost": cost_p, "gross_profit": gross,
            "gross_margin_pct": round(gross / revenue * 100, 1) if revenue else None, "expenses": exp,
            "claim_discounts": claim, "supplier_discounts": supplier_disc,
            "expenses_total": expenses_total, "net_profit": gross - expenses_total + supplier_disc,
            "unknown_cost_lines": unknown,
            "pending_expenses": dict(pending)}
