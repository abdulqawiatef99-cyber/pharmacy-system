"""تقارير الصاحب (من تاريخ لتاريخ): تغيير الأسعار، تغيير الوحدات، مبيعات العملاء، أداء الموظفين،
تقفيلات الدرج، المرتجعات، الخصومات، الأصناف اللي ما اتباعتش.

Read-only. Money in piasters. Every report is for the pharmacy's current branch and checks its
own permission; cost-based numbers only for costs.read, expected cash only for cash.see_expected."""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from ..core.permissions import Context
from ..db import Database
from ..sales.service import line_fixed, line_gross_as_sold, retail_value
from .service import _period, local_buckets

MAX_ROWS = 5000
SOURCES = {"manual": "يدوي", "purchase_invoice": "فاتورة شراء", "import": "استيراد"}
UNIT_FIELDS = {"pack_size": "عدد الوحدات في العبوة", "pack_unit_name": "اسم العبوة", "base_unit_name": "اسم الوحدة"}
METHODS = {"cash": "نقدي", "card": "بطاقة", "credit": "على الحساب", "transfer": "المحفظة الأصلية"}


def _out(d1: date, d2: date, rows: list, **kw) -> dict:
    return {"from": d1.isoformat(), "to": d2.isoformat(), "rows": rows[:MAX_ROWS], "truncated": len(rows) > MAX_ROWS, **kw}


# ------------------------------------------------------------------ prices and units
def price_changes(db: Database, ctx: Context, date_from: str, date_to: str) -> dict:
    ctx.require("sales.totals")
    d1, d2, lo, hi = _period(date_from, date_to)
    rows = []
    for r in db.all(
            "SELECT h.observed_at at, h.public_price new, h.previous_price old, h.source, h.ref_id, i.id item_id, i.name_en, i.name_ar,"
            " i.pack_size, u.full_name by_name, p.invoice_no, sp.name supplier FROM item_price_history h JOIN items i ON i.id=h.item_id"
            " LEFT JOIN users u ON u.id=h.changed_by LEFT JOIN purchase_invoices p ON h.source='purchase_invoice' AND p.id=h.ref_id"
            " LEFT JOIN suppliers sp ON sp.id=p.supplier_id"
            " WHERE h.org_id=? AND h.observed_at>=? AND h.observed_at<? AND h.previous_price IS NOT NULL AND h.previous_price<>h.public_price"
            " ORDER BY h.observed_at DESC", (ctx.org_id, lo, hi)):
        d = dict(r)
        d["change"] = d["new"] - d["old"]
        d["change_bp"] = int(round(d["change"] * 10000 / d["old"])) if d["old"] else None
        d["source_label"] = SOURCES.get(d["source"], d["source"])
        rows.append(d)
    up = [r for r in rows if r["change"] > 0]
    return _out(d1, d2, rows, totals={"n": len(rows), "up": len(up), "down": len(rows) - len(up),
                                      "items": len({r["item_id"] for r in rows})})


def unit_changes(db: Database, ctx: Context, date_from: str, date_to: str) -> dict:
    """Changes to how an item is sold: units per pack and the pack/unit names (from the audit log)."""
    ctx.require("sales.totals")
    d1, d2, lo, hi = _period(date_from, date_to)
    rows = []
    for r in db.all(
            "SELECT a.at, a.before_json, a.after_json, i.id item_id, i.name_en, i.name_ar, u.full_name by_name FROM audit_log a"
            " JOIN items i ON i.id=a.entity_id LEFT JOIN users u ON u.id=a.user_id"
            " WHERE a.org_id=? AND a.action='item.update' AND a.at>=? AND a.at<?"
            " AND (json_extract(a.after_json,'$.pack_size') IS NOT NULL OR json_extract(a.after_json,'$.pack_unit_name') IS NOT NULL"
            "  OR json_extract(a.after_json,'$.base_unit_name') IS NOT NULL) ORDER BY a.at DESC", (ctx.org_id, lo, hi)):
        before, after = json.loads(r["before_json"] or "{}"), json.loads(r["after_json"] or "{}")
        ch = [{"field": k, "label": lbl, "before": before.get(k), "after": after[k]}
              for k, lbl in UNIT_FIELDS.items() if k in after and after[k] != before.get(k)]
        if ch:
            rows.append({"at": r["at"], "item_id": r["item_id"], "name_en": r["name_en"], "name_ar": r["name_ar"],
                         "by_name": r["by_name"], "changes": ch})
    return _out(d1, d2, rows, totals={"n": len(rows)})


# ------------------------------------------------------------------ customers
def customer_sales(db: Database, ctx: Context, date_from: str, date_to: str) -> dict:
    ctx.require("sales.totals")
    d1, d2, lo, hi = _period(date_from, date_to)
    rows = [dict(r) for r in db.all(
        "SELECT s.customer_id, c.name, c.phone, count(*) n, sum(s.subtotal) gross, sum(s.discount_total) discount, sum(s.total) total,"
        " sum(s.returned_total) returns, sum(s.on_credit) credit, max(s.completed_at) last_at,"
        # org_id: the ledger's index starts with it; without it the whole ledger was read once per customer
        # (#324 R9, #304 B9), and a row of another pharmacy could be added in
        " (SELECT coalesce(sum(amount),0) FROM customer_ledger WHERE org_id=s.org_id AND customer_id=s.customer_id) balance"
        " FROM sales s LEFT JOIN customers c ON c.id=s.customer_id"
        " WHERE s.org_id=? AND s.branch_id=? AND s.status='completed' AND s.completed_at>=? AND s.completed_at<?"
        " GROUP BY s.customer_id", (ctx.org_id, ctx.branch_id, lo, hi))]
    for r in rows:
        r["net"] = r["total"] - r["returns"]
        r["avg"] = r["total"] // r["n"] if r["n"] else 0
        if not r["customer_id"]:
            r["balance"] = None
    walk_in = next((r for r in rows if not r["customer_id"]), None)
    named = sorted((r for r in rows if r["customer_id"]), key=lambda r: -r["net"])
    tot = {k: sum(r[k] for r in rows) for k in ("n", "gross", "discount", "total", "returns", "net", "credit")}
    tot["customers"] = len(named)
    return _out(d1, d2, named, walk_in=walk_in, totals=tot)


# ------------------------------------------------------------------ staff
def staff_performance(db: Database, ctx: Context, date_from: str, date_to: str) -> dict:
    ctx.require("sales.totals")
    d1, d2, lo, hi = _period(date_from, date_to)
    o, b = ctx.org_id, ctx.branch_id
    people: dict[str, dict] = {}

    def p(uid: str) -> dict:
        return people.setdefault(uid, {"user_id": uid, "n": 0, "gross": 0, "discount": 0, "total": 0, "lines": 0,
                                       "returns_n": 0, "returns": 0, "returned_on_his": 0, "voids_n": 0, "voids": 0,
                                       "shifts": 0, "short_n": 0, "difference": 0, "warnings": 0, "overrides": 0,
                                       "hours": None})
    for r in db.all("SELECT s.created_by u, count(*) n, sum(s.subtotal) g, sum(s.discount_total) d, sum(s.total) t,"
                    " sum((SELECT count(*) FROM sale_lines l WHERE l.sale_id=s.id)) lines, sum(s.returned_total) ret FROM sales s"
                    " WHERE s.org_id=? AND s.branch_id=? AND s.status='completed' AND s.completed_at>=? AND s.completed_at<?"
                    " GROUP BY s.created_by", (o, b, lo, hi)):
        x = p(r["u"])
        x.update(n=r["n"], gross=r["g"], discount=r["d"], total=r["t"], lines=r["lines"], returned_on_his=r["ret"])
    for r in db.all("SELECT r.created_by u, count(*) n, sum(r.total) t FROM sale_returns r JOIN sales s ON s.id=r.sale_id"
                    " WHERE r.org_id=? AND s.branch_id=? AND r.created_at>=? AND r.created_at<? GROUP BY r.created_by", (o, b, lo, hi)):
        p(r["u"]).update(returns_n=r["n"], returns=r["t"])
    for r in db.all("SELECT voided_by u, count(*) n, sum(total) t FROM sales WHERE org_id=? AND branch_id=? AND status='voided'"
                    " AND voided_at>=? AND voided_at<? GROUP BY voided_by", (o, b, lo, hi)):
        p(r["u"]).update(voids_n=r["n"], voids=r["t"])
    see_cash = ctx.can("cash.see_expected")
    for r in db.all("SELECT closed_by u, count(*) n, sum(difference<0) short, sum(difference) diff FROM cash_shifts"
                    " WHERE org_id=? AND branch_id=? AND status='closed' AND closed_at>=? AND closed_at<? GROUP BY closed_by", (o, b, lo, hi)):
        x = p(r["u"])
        x["shifts"] = r["n"]
        if see_cash:
            x.update(short_n=r["short"], difference=r["diff"])
    for r in db.all("SELECT user_id u, sum(decision='acknowledged') w, sum(decision='overridden') ov FROM safety_events"
                    " WHERE org_id=? AND at>=? AND at<? GROUP BY user_id", (o, lo, hi)):
        if r["u"] in people or r["w"] or r["ov"]:
            p(r["u"]).update(warnings=r["w"] or 0, overrides=r["ov"] or 0)
    if ctx.can("payroll.manage") or ctx.can("users.manage"):
        for r in db.all("SELECT user_id u, sum((julianday(check_out) - julianday(check_in)) * 24) h FROM attendance"
                        " WHERE org_id=? AND day>=? AND day<=? AND check_out IS NOT NULL GROUP BY user_id",
                        (o, d1.isoformat(), d2.isoformat())):
            p(r["u"])["hours"] = round(r["h"] or 0, 1)
    names = {u["id"]: (u["full_name"], u["role"]) for u in db.all(
        "SELECT u.id, u.full_name, r.name role FROM memberships m JOIN users u ON u.id=m.user_id LEFT JOIN roles r ON r.id=m.role_id"
        " WHERE m.org_id=?", (o,))}
    rows = []
    for uid, x in people.items():
        x["name"], x["role"] = names.get(uid, ("—", None))
        x["net"] = x["total"] - x["returned_on_his"]
        x["avg"] = x["total"] // x["n"] if x["n"] else 0
        x["items_per_sale"] = round(x["lines"] / x["n"], 1) if x["n"] else 0
        x["discount_bp"] = int(round(x["discount"] * 10000 / x["gross"])) if x["gross"] else 0
        x["per_hour"] = int(x["total"] / x["hours"]) if x["hours"] else None
        if not see_cash:
            x.pop("difference"), x.pop("short_n")
        rows.append(x)
    rows.sort(key=lambda x: -x["total"])
    return _out(d1, d2, rows, see_cash=see_cash)


# ------------------------------------------------------------------ drawer closings
def shift_closings(db: Database, ctx: Context, date_from: str, date_to: str) -> dict:
    from ..sales.cash import sale_counted_on_shift

    ctx.require("cash.see_expected")
    d1, d2, lo, hi = _period(date_from, date_to)
    rows = [dict(r) for r in db.all(
        "SELECT c.id, c.opened_at, c.closed_at, c.opening_cash, c.expected_cash, c.counted_cash, c.difference, c.close_note,"
        " uo.full_name opened_by_name, uc.full_name closed_by_name, c.register_id, r.name register_name,"
        # the row says what the shift's own slip says (sales/cash.py): a sale of this shift voided after its
        # close still counts here (the void takes the cash out of the shift open at that moment), and money
        # put in the drawer is shown, so what the row shows adds up to its expected cash (#324 R18)
        " (SELECT count(*) FROM sales x WHERE x.shift_id=c.id AND x.org_id=c.org_id"
        f" AND {sale_counted_on_shift('c.closed_at')}) sales_n,"
        " (SELECT coalesce(sum(amount),0) FROM cash_movements m WHERE m.shift_id=c.id AND m.kind='sale') cash_sales,"
        " (SELECT coalesce(sum(amount),0) FROM cash_movements m WHERE m.shift_id=c.id AND m.kind IN ('refund','void')) refunds,"
        " (SELECT coalesce(sum(amount),0) FROM cash_movements m WHERE m.shift_id=c.id AND m.kind='payout') payouts,"
        " (SELECT coalesce(sum(amount),0) FROM cash_movements m WHERE m.shift_id=c.id AND m.kind='deposit') deposits,"
        " (SELECT coalesce(sum(amount),0) FROM cash_movements m WHERE m.shift_id=c.id"
        "  AND m.kind NOT IN ('sale','refund','void','payout','deposit')) other"
        " FROM cash_shifts c LEFT JOIN users uo ON uo.id=c.opened_by LEFT JOIN users uc ON uc.id=c.closed_by"
        " JOIN registers r ON r.org_id=c.org_id AND r.branch_id=c.branch_id AND r.id=c.register_id"
        " WHERE c.org_id=? AND c.branch_id=? AND c.status='closed' AND c.closed_at>=? AND c.closed_at<? ORDER BY c.closed_at DESC",
        (ctx.org_id, ctx.branch_id, lo, hi))]
    diffs = [r["difference"] or 0 for r in rows]
    return _out(d1, d2, rows, totals={"n": len(rows), "short_n": sum(d < 0 for d in diffs), "over_n": sum(d > 0 for d in diffs),
                                      "short": sum(d for d in diffs if d < 0), "over": sum(d for d in diffs if d > 0),
                                      "net": sum(diffs)})


# ------------------------------------------------------------------ returns
def returns_report(db: Database, ctx: Context, date_from: str, date_to: str) -> dict:
    ctx.require("sales.totals")
    from ..sales.service import _local_date, return_window_days
    d1, d2, lo, hi = _period(date_from, date_to)
    window = return_window_days(db, ctx.org_id)
    rows = []
    for r in db.all(
            "SELECT r.id, r.created_at at, r.total, r.refund_method, r.reason, r.created_by, s.id sale_id, s.sale_no, s.completed_at,"
            " s.created_by sold_by, c.name customer, u.full_name by_name, us.full_name sold_by_name FROM sale_returns r"
            " JOIN sales s ON s.id=r.sale_id LEFT JOIN customers c ON c.id=s.customer_id LEFT JOIN users u ON u.id=r.created_by"
            " LEFT JOIN users us ON us.id=s.created_by WHERE r.org_id=? AND s.branch_id=? AND r.created_at>=? AND r.created_at<?"
            " ORDER BY r.created_at DESC", (ctx.org_id, ctx.branch_id, lo, hi)):
        d = dict(r)
        a = db.one("SELECT after_json FROM audit_log WHERE org_id=? AND entity_type='sale' AND entity_id=? AND action='sale.return'"
                   " AND json_extract(after_json,'$.return_id')=?", (ctx.org_id, d["sale_id"], d["id"]))   # org_id: uses the index
        lines = json.loads(a["after_json"]).get("lines", {}) if a else {}
        items = []
        for lid, q in lines.items():
            it = db.one("SELECT i.name_en, i.name_ar, i.pack_size, i.base_unit_name FROM sale_lines l JOIN items i ON i.id=l.item_id"
                        " WHERE l.id=?", (lid,))
            if it:
                items.append({**dict(it), "qty": q})
        d["items"] = items
        d["method_label"] = METHODS.get(d["refund_method"], d["refund_method"])
        d["age_days"] = (_local_date(d["at"]) - _local_date(d["completed_at"])).days if d["completed_at"] else None
        d["late"] = d["age_days"] is not None and d["age_days"] > window
        d["other_seller"] = d["created_by"] != d["sold_by"]
        rows.append(d)
    by_method = {}
    for r in rows:
        by_method[r["refund_method"]] = by_method.get(r["refund_method"], 0) + r["total"]
    sold = db.one("SELECT coalesce(sum(total),0) t FROM sales WHERE org_id=? AND branch_id=? AND status='completed'"
                  " AND completed_at>=? AND completed_at<?", (ctx.org_id, ctx.branch_id, lo, hi))["t"]
    total = sum(r["total"] for r in rows)
    return _out(d1, d2, rows, window_days=window, totals={
        "n": len(rows), "total": total, "by_method": by_method, "late_n": sum(r["late"] for r in rows),
        "other_n": sum(r["other_seller"] for r in rows), "sales": sold,
        "pct_bp": int(round(total * 10000 / sold)) if sold else None})


# ------------------------------------------------------------------ discounts
def discounts_report(db: Database, ctx: Context, date_from: str, date_to: str) -> dict:
    """Discount given in the period: overall percentage, by who gave it (contract discounts apart),
    by day, and the invoices with a discount."""
    ctx.require("sales.totals")
    d1, d2, lo, hi = _period(date_from, date_to)
    o, b = ctx.org_id, ctx.branch_id
    t = db.one("SELECT count(*) n, coalesce(sum(subtotal),0) gross, coalesce(sum(discount_total),0) discount,"
               " coalesce(sum(discount_total>0),0) n_disc FROM sales WHERE org_id=? AND branch_id=? AND status='completed'"
               " AND completed_at>=? AND completed_at<?", (o, b, lo, hi))
    by_who: dict[str, dict] = {}
    for l in db.all("SELECT l.id, l.discount_bp, l.discount_amount, l.invoice_share, l.discount_source, l.line_total,"
                    " coalesce(l.discount_by, CASE WHEN l.invoice_share>0 THEN s.invoice_discount_by END) discount_by,"
                    " coalesce(l.discount_approved_by, CASE WHEN l.invoice_share>0 THEN s.invoice_discount_approved_by END)"
                    " discount_approved_by, s.invoice_discount_by inv_by, ui.full_name inv_name,"
                    " l.discount_by line_by, (SELECT full_name FROM users WHERE id=l.discount_by) line_by_name,"
                    " l.discount_approved_by line_approved, s.invoice_discount_approved_by inv_approved,"
                    " ub.full_name by_name, ua.full_name approved_name FROM sale_lines l JOIN sales s ON s.id=l.sale_id"
                    " LEFT JOIN users ui ON ui.id=s.invoice_discount_by"
                    " LEFT JOIN users ub ON ub.id=coalesce(l.discount_by, CASE WHEN l.invoice_share>0 THEN s.invoice_discount_by END)"
                    " LEFT JOIN users ua ON ua.id=coalesce(l.discount_approved_by,"
                    " CASE WHEN l.invoice_share>0 THEN s.invoice_discount_approved_by END)"
                    " WHERE s.org_id=? AND s.branch_id=? AND s.status='completed' AND s.completed_at>=? AND s.completed_at<?"
                    " AND (l.discount_bp>0 OR l.discount_amount>0 OR l.invoice_share>0)", (o, b, lo, hi)):
        gross = line_gross_as_sold([dict(a) for a in db.all(
            "SELECT qty_base, unit_price, pack_price, pack_size FROM sale_allocations WHERE sale_line_id=?", (l["id"],))],
            l["discount_bp"], l["line_total"], line_fixed(l))
        disc = gross - (l["line_total"] if l["line_total"] is not None else gross)
        parts = [(disc, "contract" if l["discount_source"] == "contract" else (l["discount_by"] or "?"), l["by_name"],
                  bool(l["discount_approved_by"]))]
        if l["invoice_share"]:
            # the invoice discount is the person who gave it, not the contract's nor the line's (reviews of update 1);
            # each part keeps its own approval (Codex on #400)
            share = min(disc, l["invoice_share"])
            own_key = "contract" if l["discount_source"] == "contract" else (l["line_by"] or "?")
            parts = [(disc - share, own_key, l["line_by_name"], bool(l["line_approved"])),
                     (share, l["inv_by"] or "?", l["inv_name"], bool(l["inv_approved"]))]
        merged: dict[str, list] = {}                     # one person, one entry per line (Codex on #400)
        for amount, key, name, ok in parts:
            if not amount and len(parts) > 1:
                continue
            m = merged.setdefault(key, [0, name, False])
            m[0] += amount
            m[1] = m[1] or name
            m[2] = m[2] or ok
        for key, (amount, name, ok) in merged.items():
            g = by_who.setdefault(key, {"key": key, "name": "خصم التعاقدات" if key == "contract" else (name or "—"),
                                        "lines": 0, "gross": 0, "discount": 0, "approved_lines": 0})
            g["lines"] += 1
            g["gross"] += gross
            g["discount"] += amount
            g["approved_lines"] += 1 if ok else 0
    for g in by_who.values():
        g["discount_bp"] = int(round(g["discount"] * 10000 / g["gross"])) if g["gross"] else 0
    days: dict = {}
    for when, r in local_buckets(
            db, "completed_at", "count(*) n, sum(subtotal) gross, sum(discount_total) discount",
            " FROM sales WHERE org_id=? AND branch_id=? AND status='completed' AND completed_at>=? AND completed_at<?", (o, b, lo, hi)):
        d = days.setdefault(when.date().isoformat(), {"day": when.date().isoformat(), "n": 0, "gross": 0, "discount": 0})
        for k, v in r.items():
            d[k] += v or 0
    daily = [days[k] for k in sorted(days)]
    for d in daily:
        d["discount_bp"] = int(round(d["discount"] * 10000 / d["gross"])) if d["gross"] else 0
    rows = [dict(r) for r in db.all(
        "SELECT s.id, s.sale_no, s.completed_at, s.subtotal gross, s.discount_total discount, s.total, c.name customer, u.full_name by_name"
        " FROM sales s LEFT JOIN customers c ON c.id=s.customer_id LEFT JOIN users u ON u.id=s.created_by"
        " WHERE s.org_id=? AND s.branch_id=? AND s.status='completed' AND s.completed_at>=? AND s.completed_at<? AND s.discount_total>0"
        " ORDER BY s.discount_total DESC", (o, b, lo, hi))]
    for r in rows:
        r["discount_bp"] = int(round(r["discount"] * 10000 / r["gross"])) if r["gross"] else 0
    tot = dict(t)
    tot["discount_bp"] = int(round(tot["discount"] * 10000 / tot["gross"])) if tot["gross"] else 0
    return _out(d1, d2, rows, totals=tot, by_who=sorted(by_who.values(), key=lambda g: -g["discount"]), daily=daily)


# ------------------------------------------------------------------ items not sold
_IDS = 500           # item ids per IN (...) query, far below SQLite's limit on bound parameters
UNSOLD_SHORT_DAYS = 62


def unsold_items(db: Database, ctx: Context, date_from: str, date_to: str) -> dict:
    """Items in stock now that had no sale in the period, most money tied up first.

    Built from the period's sales, not item by item: the old single query asked, for every item in stock,
    "was it sold in the period?", "when was it last sold?" and "when did it last arrive?", the last one
    without the pharmacy id its index starts with, so the whole stock ledger was read once per unsold item
    (174 s for one month in a medium pharmacy, F104 / #324 R8 / #304 B7)."""
    ctx.require("sales.totals")
    ctx.require("inventory.read")
    d1, d2, lo, hi = _period(date_from, date_to)
    o, b = ctx.org_id, ctx.branch_id
    show_cost, see_hidden = ctx.can("costs.read"), ctx.can("items.hidden")
    # "was it sold in the period?", the cheaper way round for the period asked:
    # - a short period: the items on the period's own invoices, read once (a month is a twelfth of a year's lines);
    # - a long one: item by item, stopping at the first sale found (over years almost every item has one at once;
    #   reading every invoice line of several years instead would be the slow part).
    short = (d2 - d1).days <= UNSOLD_SHORT_DAYS
    sold: set = set()
    probe, probe_params = "", ()
    if short:
        sold = {r["item_id"] for r in db.all(
            "SELECT DISTINCT l.item_id FROM sales s JOIN sale_lines l ON l.sale_id=s.id WHERE s.org_id=? AND s.branch_id=?"
            " AND s.status='completed' AND s.completed_at>=? AND s.completed_at<?", (o, b, lo, hi))}
    else:
        # CROSS JOIN fixes the order: the item's own lines first, then each line's invoice. Left to choose,
        # SQLite starts from every invoice of the period, once per item
        probe = (" AND NOT EXISTS (SELECT 1 FROM sale_lines l CROSS JOIN sales s ON s.id=l.sale_id WHERE l.item_id=i.id"
                 " AND s.org_id=i.org_id AND s.branch_id=sb.branch_id AND s.status='completed'"
                 " AND s.completed_at>=? AND s.completed_at<?)")
        probe_params = (lo, hi)
    rows = []
    for r in db.all(
            "SELECT i.id item_id, i.name_en, i.name_ar, i.pack_size, i.base_unit_name, i.public_price, i.hidden, c.name category,"
            " sum(sb.qty) qty, min(CASE WHEN sb.qty>0 THEN bt.expiry_date END) expiry, ic.avg_cost"
            " FROM stock_balances sb JOIN items i ON i.id=sb.item_id AND i.org_id=sb.org_id"
            " LEFT JOIN batches bt ON bt.id=nullif(sb.batch_key,'') LEFT JOIN categories c ON c.id=i.category_id"
            " LEFT JOIN item_costs ic ON ic.item_id=i.id AND ic.org_id=i.org_id AND ic.branch_id=sb.branch_id"
            f" WHERE sb.org_id=? AND sb.branch_id=? AND i.status='active' AND i.is_service=0{probe}"
            " GROUP BY i.id HAVING sum(sb.qty) > 0", (o, b, *probe_params)):
        d = dict(r)
        if d["item_id"] in sold or (d.pop("hidden") and not see_hidden):
            continue
        d.pop("hidden", None)
        d["last_sold"] = d["last_in"] = None
        d["price_value"] = retail_value(d["qty"], d["public_price"] or 0, d["pack_size"])   # the sale's own rule
        cost = d.pop("avg_cost")
        if show_cost:
            d["cost_value"] = int((Decimal(cost) * d["qty"] * 100).to_integral_value(ROUND_HALF_UP)) if cost is not None else None
        rows.append(d)
    by_id = {d["item_id"]: d for d in rows}
    ids = list(by_id)
    for k in range(0, len(ids), _IDS):     # the two dates, only for the items listed, each from the item's own rows
        part = ids[k:k + _IDS]
        marks = ",".join("?" * len(part))
        for r in db.all("SELECT l.item_id, max(s.completed_at) t FROM sale_lines l CROSS JOIN sales s ON s.id=l.sale_id"
                        f" WHERE l.item_id IN ({marks}) AND s.org_id=? AND s.branch_id=? AND s.status='completed'"
                        " GROUP BY l.item_id", (*part, o, b)):
            by_id[r["item_id"]]["last_sold"] = r["t"]
        for r in db.all("SELECT m.item_id, max(m.created_at) t FROM stock_movements m WHERE m.org_id=? AND m.branch_id=?"
                        f" AND m.item_id IN ({marks}) AND m.movement_type IN ('purchase','opening') GROUP BY m.item_id",
                        (o, b, *part)):
            by_id[r["item_id"]]["last_in"] = r["t"]
    key = "cost_value" if show_cost else "price_value"
    rows.sort(key=lambda r: -(r.get(key) or 0))
    tot = {"n": len(rows), "price_value": sum(r["price_value"] for r in rows)}
    if show_cost:
        tot["cost_value"] = sum(r["cost_value"] or 0 for r in rows)
        tot["unknown_cost"] = sum(r["cost_value"] is None for r in rows)
    return _out(d1, d2, rows, totals=tot)


REPORTS = {"prices": price_changes, "units": unit_changes, "customers": customer_sales, "staff": staff_performance,
           "shifts": shift_closings, "returns": returns_report, "discounts": discounts_report, "unsold": unsold_items}
