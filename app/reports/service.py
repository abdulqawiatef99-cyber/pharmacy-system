"""Reports and the home screen. Everything is filtered by what the person may see."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from ..core import notify
from ..core.errors import ValidationFailed
from ..core.permissions import Context
from ..db import Database
from ..sales.service import local_day_bounds, local_today


def _period(date_from: str, date_to: str) -> tuple[date, date, str, str]:
    try:
        d1, d2 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except ValueError:
        raise ValidationFailed("الفترة غير صحيحة")
    if d2 < d1 or (d2 - d1).days > 400:
        raise ValidationFailed("الفترة غير صحيحة (سنة على الأكثر)")
    return d1, d2, local_day_bounds(d1)[0], local_day_bounds(d2)[1]


def sales_report(db: Database, ctx: Context, date_from: str, date_to: str) -> dict:
    ctx.require("sales.totals")
    d1, d2, lo, hi = _period(date_from, date_to)
    base = " FROM sales s WHERE s.org_id=? AND s.branch_id=? AND s.status='completed' AND s.completed_at>=? AND s.completed_at<?"
    p = (ctx.org_id, ctx.branch_id, lo, hi)
    # local day/hour: completed_at is UTC; shift by the PC's current UTC offset (Egypt has no DST change mid-report risk worth more)
    from datetime import datetime
    off = datetime.now().astimezone().utcoffset()
    mod = f"{int(off.total_seconds() // 60):+d} minutes"
    daily = [dict(r) for r in db.all(
        f"SELECT date(s.completed_at, '{mod}') day, count(*) n, sum(s.total) total, sum(s.paid_cash) cash, sum(s.paid_card) card,"
        f" sum(s.paid_transfer) transfer, sum(s.on_credit) credit, sum(s.discount_total) discount, sum(s.returned_total) returns"
        f"{base} GROUP BY 1 ORDER BY 1", p)]
    by_user = [dict(r) for r in db.all(
        f"SELECT u.full_name name, count(*) n, sum(s.total) total, sum(s.discount_total) discount{base.replace('FROM sales s', 'FROM sales s JOIN users u ON u.id=s.created_by')}"
        " GROUP BY s.created_by ORDER BY 3 DESC", p)]
    by_hour = [dict(r) for r in db.all(
        f"SELECT CAST(strftime('%H', s.completed_at, '{mod}') AS INTEGER) hour, count(*) n, sum(s.total) total{base} GROUP BY 1 ORDER BY 1", p)]
    show_cost = ctx.can("costs.read")
    top = []
    for r in db.all(
            "SELECT l.item_id, i.name_en, i.name_ar, i.pack_size, i.base_unit_name, sum(a.qty_base - a.returned_qty) qty,"
            " sum(CAST(round((CASE WHEN a.pack_price IS NOT NULL AND a.pack_size > 1"
            "   THEN ((a.qty_base - a.returned_qty) / a.pack_size) * a.pack_price + ((a.qty_base - a.returned_qty) % a.pack_size) * a.unit_price"
            "   ELSE (a.qty_base - a.returned_qty) * a.unit_price END) * (10000 - l.discount_bp) / 10000.0) AS INTEGER)) revenue,"
            " sum((a.qty_base - a.returned_qty) * coalesce(CAST(a.unit_cost AS REAL), 0) * 100) cost,"
            " sum(a.unit_cost IS NULL) unknown"
            " FROM sale_allocations a JOIN sale_lines l ON l.id=a.sale_line_id JOIN sales s ON s.id=l.sale_id JOIN items i ON i.id=l.item_id"
            " WHERE s.org_id=? AND s.branch_id=? AND s.status='completed' AND s.completed_at>=? AND s.completed_at<?"
            " GROUP BY l.item_id ORDER BY revenue DESC LIMIT 50", p):
        d = dict(r)
        if show_cost and not d["unknown"]:
            d["profit"] = d["revenue"] - int(round(d["cost"]))
        d.pop("cost"), d.pop("unknown")
        top.append(d)
    t = db.one(f"SELECT count(*) n, coalesce(sum(s.total),0) total, coalesce(sum(s.returned_total),0) returns,"
               f" coalesce(sum(s.discount_total),0) discount{base}", p)
    return {"from": d1.isoformat(), "to": d2.isoformat(), "totals": dict(t), "daily": daily, "by_user": by_user,
            "by_hour": by_hour, "top_items": top}


def stock_report(db: Database, ctx: Context, today: date | None = None) -> dict:
    ctx.require("inventory.read")
    today = today or local_today()
    soon = (today + timedelta(days=90)).isoformat()
    out: dict = {}
    exp = []
    for r in db.all(
            "SELECT i.id item_id, i.name_en, i.name_ar, i.pack_size, i.base_unit_name, b.batch_no, b.expiry_date, sb.qty,"
            " coalesce(b.public_price, i.public_price) price FROM stock_balances sb JOIN batches b ON b.id=sb.batch_key"
            " JOIN items i ON i.id=sb.item_id WHERE sb.org_id=? AND sb.branch_id=? AND sb.qty>0 AND b.expiry_date IS NOT NULL"
            " AND b.expiry_date <= ? ORDER BY b.expiry_date LIMIT 500", (ctx.org_id, ctx.branch_id, soon)):
        d = dict(r)
        d["days_left"] = (date.fromisoformat(d["expiry_date"]) - today).days
        d["value"] = int(round((d["price"] or 0) * d["qty"] / max(1, d["pack_size"])))
        exp.append(d)
    out["expiring"] = exp
    if ctx.can("costs.read"):
        rows = db.all("SELECT coalesce(c.name, 'بدون قسم') category, sum(sb.qty * coalesce(CAST(ic.avg_cost AS REAL),0) * 100) cost,"
                      " sum(sb.qty * coalesce(i.public_price,0) * 1.0 / i.pack_size) retail, count(DISTINCT i.id) items"
                      " FROM stock_balances sb JOIN items i ON i.id=sb.item_id LEFT JOIN categories c ON c.id=i.category_id"
                      " LEFT JOIN item_costs ic ON ic.item_id=i.id AND ic.org_id=sb.org_id AND ic.branch_id=sb.branch_id"
                      " WHERE sb.org_id=? AND sb.branch_id=? AND sb.qty>0 GROUP BY 1 ORDER BY 2 DESC", (ctx.org_id, ctx.branch_id))
        out["value_by_category"] = [{**dict(r), "cost": int(round(r["cost"])), "retail": int(round(r["retail"]))} for r in rows]
        out["value_cost"] = sum(r["cost"] for r in out["value_by_category"])
        out["value_retail"] = sum(r["retail"] for r in out["value_by_category"])
    return out


def home(db: Database, ctx: Context) -> dict:
    """The first screen: today at a glance and what needs attention, by permission."""
    today = local_today()
    lo, hi = local_day_bounds(today)
    out: dict = {"today": today.isoformat(), "alerts": []}
    mine = db.one("SELECT count(*) n, coalesce(sum(total),0) t FROM sales WHERE org_id=? AND branch_id=? AND status='completed'"
                  " AND created_by=? AND completed_at>=? AND completed_at<?", (ctx.org_id, ctx.branch_id, ctx.user_id, lo, hi))
    out["mine"] = dict(mine)
    if ctx.can("sales.totals"):
        out["sales"] = dict(db.one("SELECT count(*) n, coalesce(sum(total),0) total, coalesce(sum(returned_total),0) returns FROM sales"
                                   " WHERE org_id=? AND branch_id=? AND status='completed' AND completed_at>=? AND completed_at<?",
                                   (ctx.org_id, ctx.branch_id, lo, hi)))
        y_lo, y_hi = local_day_bounds(today - timedelta(days=7))
        out["sales_week_ago"] = db.one("SELECT coalesce(sum(total),0) t FROM sales WHERE org_id=? AND branch_id=? AND status='completed'"
                                       " AND completed_at>=? AND completed_at<?", (ctx.org_id, ctx.branch_id, y_lo, y_hi))["t"]
    sh = db.one("SELECT id, opened_at FROM cash_shifts WHERE org_id=? AND branch_id=? AND status='open'", (ctx.org_id, ctx.branch_id))
    out["shift_open"] = bool(sh)
    if not sh:
        out["alerts"].append({"level": "warning", "text": "لا توجد وردية مفتوحة — البيع النقدي متوقف", "link": "#/shift"})
    if ctx.can("inventory.read"):
        soon = (today + timedelta(days=30)).isoformat()
        n = db.one("SELECT count(*) c FROM stock_balances sb JOIN batches b ON b.id=sb.batch_key WHERE sb.org_id=? AND sb.branch_id=?"
                   " AND sb.qty>0 AND b.expiry_date IS NOT NULL AND b.expiry_date<=?", (ctx.org_id, ctx.branch_id, soon))["c"]
        expired = db.one("SELECT count(*) c FROM stock_balances sb JOIN batches b ON b.id=sb.batch_key WHERE sb.org_id=? AND sb.branch_id=?"
                         " AND sb.qty>0 AND b.expiry_date IS NOT NULL AND b.expiry_date<?", (ctx.org_id, ctx.branch_id, today.isoformat()))["c"]
        if expired:
            out["alerts"].append({"level": "error", "text": f"{expired} تشغيلة انتهت صلاحيتها وبها رصيد — ارفعها من الرف", "link": "#/reports"})
        if n - expired > 0:
            out["alerts"].append({"level": "warning", "text": f"{n - expired} تشغيلة تنتهي صلاحيتها خلال 30 يومًا", "link": "#/reports"})
        short = db.one("SELECT count(*) c FROM items WHERE org_id=? AND shortage_at IS NOT NULL", (ctx.org_id,))["c"]
        if short:
            out["alerts"].append({"level": "warning", "text": f"{short} صنف مسجل ناقصًا", "link": "#/shortages"})
    d = db.one("SELECT count(*) n, coalesce(sum(cash_due),0) c FROM sale_deliveries WHERE org_id=? AND status IN ('out','delivered')",
               (ctx.org_id,)) if ctx.can("sales.create") else {"n": 0}
    if d["n"]:
        out["alerts"].append({"level": "info", "text": f"{d['n']} توصيلة مفتوحة — {d['c'] / 100:,.2f} ج مع مندوب التوصيل", "link": "#/pos"})
    if ctx.can("expenses.approve"):
        e = db.one("SELECT count(*) n FROM expenses WHERE org_id=? AND status='pending'", (ctx.org_id,))["n"]
        if e:
            out["alerts"].append({"level": "warning", "text": f"{e} مصروف بانتظار موافقتك", "link": "#/expenses"})
        from ..finance import schedules
        for sc in schedules.listing(db, ctx, today):
            n = sc["next"]
            if n and n["state"] != "later":
                when = "فات موعده" if n["state"] == "late" else "موعده اليوم" if n["days"] == 0 else f"موعده بعد {n['days']} يوم"
                out["alerts"].append({"level": "error" if n["state"] == "late" else "warning",
                                      "text": f"{sc['name']} {when} ({n['due']})", "link": "#/expenses"})
    soon5 = (today + timedelta(days=5)).isoformat()
    r = db.one("SELECT count(DISTINCT customer_id) c FROM customer_refills WHERE org_id=? AND"
               " date(coalesce(last_at, created_at), '+' || every_days || ' days') <= ?", (ctx.org_id, soon5))["c"] \
        if ctx.can("sales.read") else 0
    if r:
        out["alerts"].append({"level": "info", "text": f"{r} عميل اقترب موعد طلبهم الشهري", "link": "#/customers"})
    if ctx.can("notifications.read"):
        out["notifications_unread"] = notify.unread_count(db, ctx)
    return out
