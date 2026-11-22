"""Reports and the home screen. Everything is filtered by what the person may see."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from ..core import notify
from ..core.errors import ValidationFailed
from ..core.permissions import Context
from ..db import Database
from ..sales.service import (cost_piasters, line_fixed, local_day_bounds, local_today, outstanding_value, retail_value,
                             returns_history)


def _period(date_from: str, date_to: str) -> tuple[date, date, str, str]:
    try:
        d1, d2 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except ValueError:
        raise ValidationFailed("الفترة غير صحيحة")
    if d2 < d1 or (d2 - d1).days > 400:
        raise ValidationFailed("الفترة غير صحيحة (سنة على الأكثر)")
    return d1, d2, local_day_bounds(d1)[0], local_day_bounds(d2)[1]


def local_buckets(db: Database, column: str, cols: str, from_where: str, params: tuple):
    """Sums per UTC quarter-hour of `column`, each with the local time of that quarter-hour: the caller groups them
    by local day or hour. One fixed offset for the whole report is wrong across a daylight-saving change (Egypt
    has one again since 2023), and quarter-hours also fit the time zones that are 30 or 45 minutes off."""
    from datetime import datetime, timedelta, timezone
    out = []
    for r in db.all(f"SELECT substr({column}, 1, 13) h, CAST(substr({column}, 15, 2) AS INTEGER) / 15 q, {cols}"
                    f"{from_where} GROUP BY 1, 2 ORDER BY 1, 2", params):
        d = dict(r)
        utc = datetime.strptime(d.pop("h"), "%Y-%m-%dT%H").replace(tzinfo=timezone.utc) + timedelta(minutes=15 * d.pop("q"))
        out.append((utc.astimezone(), d))
    return out


def sales_report(db: Database, ctx: Context, date_from: str, date_to: str) -> dict:
    ctx.require("sales.totals")
    d1, d2, lo, hi = _period(date_from, date_to)
    base = " FROM sales s WHERE s.org_id=? AND s.branch_id=? AND s.status='completed' AND s.completed_at>=? AND s.completed_at<?"
    p = (ctx.org_id, ctx.branch_id, lo, hi)
    # local day/hour: completed_at is UTC and the offset of each moment is its own (daylight saving time), so the
    # sums come per UTC quarter-hour and are put on their local day and hour here (R7, 16/11)
    cols = ("count(*) n, sum(s.total) total, sum(s.paid_cash) cash, sum(s.paid_card) card, sum(s.paid_transfer) transfer,"
            " sum(s.on_credit) credit, sum(s.discount_total) discount, sum(s.returned_total) returns, sum((coalesce(s.rounding,0) - coalesce((SELECT sum(r.rounding) FROM sale_returns r WHERE r.sale_id=s.id AND r.org_id=s.org_id),0))) rounding")
    days: dict = {}
    hours: dict = {}
    for when, r in local_buckets(db, "s.completed_at", cols, base, p):
        d = days.setdefault(when.date().isoformat(), {"day": when.date().isoformat(), **{k: 0 for k in r}})
        h = hours.setdefault(when.hour, {"hour": when.hour, "n": 0, "total": 0})
        for k, v in r.items():
            d[k] += v or 0
        h["n"] += r["n"]
        h["total"] += r["total"] or 0
    daily = [days[k] for k in sorted(days)]
    by_hour = [hours[k] for k in sorted(hours)]
    by_user = [dict(r) for r in db.all(
        f"SELECT u.full_name name, count(*) n, sum(s.total) total, sum(s.discount_total) discount{base.replace('FROM sales s', 'FROM sales s JOIN users u ON u.id=s.created_by')}"
        " GROUP BY s.created_by ORDER BY 3 DESC", p)]
    top = _top_items(db, ctx, p)
    t = db.one(f"SELECT count(*) n, coalesce(sum(s.total),0) total, coalesce(sum(s.returned_total),0) returns,"
               f" coalesce(sum(s.discount_total),0) discount, coalesce(sum((coalesce(s.rounding,0) - coalesce((SELECT sum(r.rounding) FROM sale_returns r WHERE r.sale_id=s.id AND r.org_id=s.org_id),0))),0) rounding{base}", p)
    return {"from": d1.isoformat(), "to": d2.isoformat(), "totals": dict(t), "daily": daily, "by_user": by_user,
            "by_hour": by_hour, "top_items": top}


# a completed sale of the period, through ix_sales_completed. CROSS JOIN keeps the invoices of the period as the
# outer loop: left to itself the planner may start from every line an item ever had (ix_sale_lines_item)
_SOLD = (" FROM sales s CROSS JOIN sale_lines l ON l.sale_id=s.id{allocations}"
         " WHERE s.org_id=? AND l.org_id=s.org_id AND s.branch_id=? AND s.status='completed'"
         " AND s.completed_at>=? AND s.completed_at<?")
_ALLOCATIONS = " CROSS JOIN sale_allocations a ON a.sale_line_id=l.id"
# an invoice something was returned from. A return always writes its `sale_returns` row with the returned
# quantities, in one transaction, also when it refunds 0.00 (a 0.01 strip), so `returned_total` alone is not enough
_RETURNED = ("(s.returned_total > 0 OR EXISTS (SELECT 1 FROM sale_returns r WHERE r.sale_id=s.id"
             " AND r.org_id=s.org_id))")


def _top_items(db: Database, ctx: Context, p: tuple, limit: int = 50) -> list[dict]:
    """Best-selling items: what the customers kept and what it brought in, priced the way each line was
    sold (#145). A line nothing was returned from is worth exactly its recorded total; a line with returns
    is valued like the return screen values it (`outstanding_value`), so the items add up to the invoices.

    The database adds up per item (#304 B11: one row per sale line was a million rows for a year of a large
    pharmacy). Only the lines of invoices with a return, and lines from before `line_total` was recorded, are
    read one by one: only they need the return rule."""
    show_cost = ctx.can("costs.read")
    by_item: dict[str, dict] = {}

    def of(item_id: str) -> dict:
        return by_item.setdefault(item_id, {"item_id": item_id, "qty": 0, "revenue": 0, "unknown": 0, "costs": []})

    # 1. what was kept and at which recorded cost: one row per item and cost (exact: the stored decimal unit
    #    costs, never SQLite REAL). Every completed line has at least one allocation, so every item sold is here
    for r in db.all("SELECT l.item_id, a.unit_cost, sum(a.qty_base - a.returned_qty) q, count(*) n"
                    + _SOLD.format(allocations=_ALLOCATIONS) + " GROUP BY l.item_id, a.unit_cost", p):
        d = of(r["item_id"])
        d["qty"] += r["q"]
        if r["unit_cost"] is None:
            d["unknown"] += r["n"]
        else:
            d["costs"].append((r["unit_cost"], r["q"]))
    # 2. the money of the invoices nothing was returned from: their lines' recorded totals, one row per item
    legacy = False
    for r in db.all("SELECT l.item_id, sum(l.line_total) revenue, sum(l.line_total IS NULL) no_total"
                    + _SOLD.format(allocations="") + f" AND NOT {_RETURNED} GROUP BY l.item_id", p):
        of(r["item_id"])["revenue"] += r["revenue"] or 0
        legacy = legacy or bool(r["no_total"])
    # 3. line by line: the invoices with a return, and (rare, sales from before 0.4) lines without a recorded total
    cols = ("l.id line_id, l.sale_id, l.item_id, l.discount_bp, l.discount_amount, l.invoice_share, l.line_total, a.id, a.rowid rid, a.qty_base,"
            " a.returned_qty, a.unit_price, a.pack_price, a.pack_size")
    one_by_one = [f" AND {_RETURNED}"] + ([f" AND NOT {_RETURNED} AND l.line_total IS NULL"] if legacy else [])
    lines: dict[str, list[dict]] = {}
    for where in one_by_one:
        for a in db.all(f"SELECT {cols}" + _SOLD.format(allocations=_ALLOCATIONS) + where, p):
            lines.setdefault(a["line_id"], []).append(dict(a))
    partial = [allocs for allocs in lines.values()
               if allocs[0]["line_total"] is None or any(a["returned_qty"] for a in allocs)]
    history = returns_history(db, ctx.org_id, {allocs[0]["sale_id"] for allocs in partial}) if partial else {}
    for allocs in lines.values():
        l = allocs[0]
        if l["line_total"] is not None and not any(a["returned_qty"] for a in allocs):
            of(l["item_id"])["revenue"] += l["line_total"]     # an untouched line of an invoice with a return
            continue
        kept = {a["id"]: a["qty_base"] - a["returned_qty"] for a in allocs}
        of(l["item_id"])["revenue"] += outstanding_value(allocs, kept, l["discount_bp"], l["line_total"], l["line_id"],
                                                         history.get(l["sale_id"], []), fixed=line_fixed(l))
    # equal money: by item id, so the fiftieth place does not depend on the order the database answered in
    ranked = sorted(by_item.values(), key=lambda d: (-d["revenue"], d["item_id"]))[:limit]
    names = {r["id"]: dict(r) for r in db.all(
        "SELECT id, name_en, name_ar, pack_size, base_unit_name, hidden FROM items WHERE org_id=? AND id IN (%s)"
        % ",".join("?" * len(ranked)), (ctx.org_id, *[d["item_id"] for d in ranked]))} if ranked else {}
    top = []
    for d in ranked:
        i = names.get(d["item_id"], {})
        if i.get("hidden") and not ctx.can("items.hidden"):
            i = {**i, "name_en": "—", "name_ar": None}       # masked as in Invoices (#324 R7)
        out = {"item_id": d["item_id"], "name_en": i.get("name_en"), "name_ar": i.get("name_ar"),
               "pack_size": i.get("pack_size"), "base_unit_name": i.get("base_unit_name"),
               "qty": d["qty"], "revenue": d["revenue"]}
        if show_cost and not d["unknown"]:
            cost = sum((Decimal(c) * q * 100 for c, q in d["costs"]), Decimal(0))
            out["profit"] = d["revenue"] - cost_piasters(cost)
        top.append(out)
    return top


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
            " AND b.expiry_date <= ? AND (i.hidden=0 OR ?) ORDER BY b.expiry_date LIMIT 500",
            (ctx.org_id, ctx.branch_id, soon, int(ctx.can("items.hidden")))):
        d = dict(r)
        d["days_left"] = (date.fromisoformat(d["expiry_date"]) - today).days
        d["value"] = retail_value(d["qty"], d["price"] or 0, d["pack_size"])
        exp.append(d)
    out["expiring"] = exp
    if ctx.can("costs.read"):
        # cost summed in Decimal from the stored decimal cost (not SQLite REAL) and rounded once per category
        # by the sale's cost rule; retail priced per balance row by the sale's own pricing rule (exact integers)
        cats: dict[str, list] = {}
        # the price of the lot when it has its own (an older pack bought before a price rise is still sold at
        # its printed price): the item's current price for every pack overstated the stock against the sale
        # screen and the expiry list above (#324 R4)
        for r in db.all("SELECT coalesce(c.name, 'بدون قسم') category, i.id item_id, sb.qty, ic.avg_cost,"
                        " coalesce(b.public_price, i.public_price, 0) price, i.pack_size"
                        " FROM stock_balances sb JOIN items i ON i.id=sb.item_id LEFT JOIN categories c ON c.id=i.category_id"
                        " LEFT JOIN batches b ON b.id=nullif(sb.batch_key,'')"
                        " LEFT JOIN item_costs ic ON ic.item_id=i.id AND ic.org_id=sb.org_id AND ic.branch_id=sb.branch_id"
                        " WHERE sb.org_id=? AND sb.branch_id=? AND sb.qty>0", (ctx.org_id, ctx.branch_id)):
            g = cats.setdefault(r["category"], [Decimal(0), {}])
            g[0] += r["qty"] * Decimal(r["avg_cost"] or 0) * 100
            # an item's balances over several batches are one quantity, as a sale prices them (line_gross):
            # one unit here and two there of a pack of 3 is a whole pack, not three loose units
            k = (r["item_id"], r["price"], r["pack_size"])
            g[1][k] = g[1].get(k, 0) + r["qty"]
        out["value_by_category"] = sorted(
            ({"category": name, "cost": cost_piasters(c),
              "retail": sum(retail_value(q, price, ps) for (_, price, ps), q in qty.items()),
              "items": len({item for item, _, _ in qty})}
             for name, (c, qty) in cats.items()), key=lambda x: -x["cost"])
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
        out["alerts"].append({"level": "warning", "text": "لا يوجد شيفت مفتوح — البيع النقدي متوقف", "link": "#/shift"})
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
