"""«مراجعة اليوم» (U2-8): one page for the owner or a manager to check a day end to end: purchases, supplier
returns, sales, sale returns, discounts, credit, money by payment method, and the drawer (expected vs counted
for the shifts closed that day). Read-only, whole piasters."""
from __future__ import annotations

from datetime import date

from ..core.errors import ValidationFailed
from ..core.permissions import Context
from ..db import Database


def day_review(db: Database, ctx: Context, day: str | None = None) -> dict:
    ctx.require("sales.totals")
    ctx.require("cash.see_expected")
    from ..sales.service import local_day_bounds, local_today
    try:
        d = date.fromisoformat(day) if day else local_today()
    except ValueError:
        raise ValidationFailed("التاريخ غير صحيح")
    lo, hi = local_day_bounds(d)
    o, b = ctx.org_id, ctx.branch_id
    s = dict(db.one(
        "SELECT count(*) n, coalesce(sum(total),0) total, coalesce(sum(discount_total),0) discount,"
        " coalesce(sum(paid_cash),0) cash, coalesce(sum(paid_card),0) card, coalesce(sum(paid_transfer),0) transfer,"
        " coalesce(sum(on_credit),0) credit FROM sales WHERE org_id=? AND branch_id=? AND status='completed'"
        " AND completed_at>=? AND completed_at<?", (o, b, lo, hi)))
    voids = db.one("SELECT count(*) n, coalesce(sum(total),0) t FROM sales WHERE org_id=? AND branch_id=?"
                   " AND status='voided' AND voided_at>=? AND voided_at<?", (o, b, lo, hi))
    rets = {r["m"]: {"count": r["n"], "total": r["t"]} for r in db.all(
        "SELECT r.refund_method m, count(*) n, coalesce(sum(r.total),0) t FROM sale_returns r JOIN sales x ON x.id=r.sale_id"
        " WHERE r.org_id=? AND x.branch_id=? AND r.created_at>=? AND r.created_at<? GROUP BY 1", (o, b, lo, hi))}
    pur = dict(db.one(
        "SELECT count(*) n, coalesce(sum(computed_total),0) total FROM purchase_invoices WHERE org_id=? AND branch_id=?"
        " AND status='approved' AND approved_at>=? AND approved_at<?", (o, b, lo, hi)))
    pur["by_supplier"] = [dict(r) for r in db.all(
        "SELECT coalesce(sp.name, p.supplier_name_raw, '') supplier, count(*) n, coalesce(sum(p.computed_total),0) total"
        " FROM purchase_invoices p LEFT JOIN suppliers sp ON sp.id=p.supplier_id WHERE p.org_id=? AND p.branch_id=?"
        " AND p.status='approved' AND p.approved_at>=? AND p.approved_at<? GROUP BY 1 ORDER BY 3 DESC", (o, b, lo, hi))]
    sret = dict(db.one("SELECT count(*) n, coalesce(sum(total),0) total FROM supplier_returns WHERE org_id=? AND"
                       " branch_id=? AND created_at>=? AND created_at<?", (o, b, lo, hi)))
    shifts = [dict(r) for r in db.all(
        "SELECT s.id, s.opened_at, s.closed_at, s.expected_cash, s.counted_cash, s.difference, u.full_name closed_by"
        " FROM cash_shifts s LEFT JOIN users u ON u.id=s.closed_by WHERE s.org_id=? AND s.branch_id=?"
        " AND s.status='closed' AND s.closed_at>=? AND s.closed_at<? ORDER BY s.closed_at", (o, b, lo, hi))]
    collected = db.one("SELECT coalesce(-sum(amount),0) t FROM customer_ledger WHERE org_id=? AND kind='payment'"
                       " AND created_at>=? AND created_at<?", (o, lo, hi))["t"]
    refunded = sum(v["total"] for v in rets.values())
    return {
        "day": d.isoformat(),
        "sales": {**s, "voided_count": voids["n"], "voided_total": voids["t"]},
        "returns": {"by_method": rets, "total": refunded},
        "net_sales": s["total"] - refunded,
        "by_method": {"cash": s["cash"] - rets.get("cash", {}).get("total", 0), "card": s["card"] - rets.get("card", {}).get("total", 0),
                      "transfer": s["transfer"], "credit": s["credit"] - rets.get("credit", {}).get("total", 0)},
        "credit_collected": collected,
        "purchases": pur, "supplier_returns": sret,
        "drawer": {"shifts": shifts, "expected": sum(x["expected_cash"] or 0 for x in shifts),
                   "counted": sum(x["counted_cash"] or 0 for x in shifts),
                   "difference": sum(x["difference"] or 0 for x in shifts)},
    }
