"""U3-8 the owner's summary of a closed shift, as plain text ready to copy (sending stays manual: the program sends
nothing). Sales, cash, card / transfer, credit, returns, the drawer difference and the items that ran out.
Owner / manager (cash.see_expected). Read only; every figure comes from the shift report itself.
UX-28 (owner 26/11, theft visibility): each void and each cash refund of the shift is listed (who, when, invoice,
amount, reason), with the day's total over every shift of the branch."""
from __future__ import annotations

from ..core.errors import Conflict
from ..core.permissions import Context
from ..db import Database


def _egp(p: int | None) -> str:
    p = p or 0
    sign, p = ("-", -p) if p < 0 else ("", p)
    return f"{sign}{p // 100:,}.{p % 100:02d}"


def _local(utc_iso: str | None, time_format: str) -> str:
    """The PC's local time of a stored stamp, in the pharmacy's own 12 / 24 h setting (as the shift report)."""
    from ..core import accounts
    from ..printing import settings as rs
    if not utc_iso:
        return ""
    return rs.fmt_when(accounts.parse_iso(utc_iso).astimezone(), time_format)


def _voids_of(db: Database, s) -> list[dict]:
    from ..sales.cash import shift_void_filters

    cols = ("SELECT x.id, x.sale_no, x.total, x.paid_cash, x.voided_at, x.void_reason, v.full_name voided_by_name,"
            " c.full_name sold_by_name FROM sales x LEFT JOIN users v ON v.id=x.voided_by"
            " LEFT JOIN users c ON c.id=x.created_by")
    rows = [dict(r) for where, args in shift_void_filters(s) for r in db.all(f"{cols} WHERE {where}", args)]
    return sorted(rows, key=lambda r: r["voided_at"] or "")


def _cash_refunds_of(db: Database, s) -> list[dict]:
    """Cash refunds paid from this shift's drawer (found through their drawer movement, as the shift report)."""
    return [dict(r) for r in db.all(
        "SELECT coalesce(r.part_of,r.id) id, r.sale_id, x.sale_no, r.total, r.created_at, r.reason, u.full_name by_name, c.full_name sold_by_name"
        " FROM sale_returns r JOIN cash_movements m ON m.ref_id=r.id AND m.kind='refund' AND m.org_id=r.org_id"
        " JOIN sales x ON x.id=r.sale_id AND x.org_id=r.org_id LEFT JOIN users u ON u.id=r.created_by"
        " LEFT JOIN users c ON c.id=x.created_by WHERE m.shift_id=? AND m.org_id=? ORDER BY r.created_at",
        (s["id"], s["org_id"]))]


def day_totals(db: Database, ctx: Context, day=None, branch_id: str | None = None) -> dict:
    """Voids and cash refunds of one local day (default today) in the branch, over every shift."""
    ctx.require("cash.see_expected")
    from ..sales.service import local_day_bounds, local_today
    lo, hi = local_day_bounds(day or local_today())
    branch = branch_id or ctx.branch_id
    # a void is allowed on the sale's own day only, so completed_at bounds the read to an index range of the day
    # (review P2: discarded draft tabs are 'voided' too and grow forever)
    v = db.one("SELECT count(*) n, coalesce(sum(total),0) t FROM sales WHERE org_id=? AND branch_id=?"
               " AND status='voided' AND completed_at>=? AND completed_at<? AND sale_no IS NOT NULL"
               " AND voided_at>=? AND voided_at<?", (ctx.org_id, branch, lo, hi, lo, hi))
    # from the shifts open during the day to their refund movements (shift index), never every movement ever
    # (review P1: cash_movements has no index on ref_id)
    r = db.one("SELECT count(*) n, coalesce(sum(r.total),0) t FROM cash_shifts h"
               " JOIN cash_movements m ON m.shift_id=h.id AND m.kind='refund' AND m.org_id=h.org_id"
               " JOIN sale_returns r ON r.id=m.ref_id AND r.org_id=h.org_id"
               " WHERE h.org_id=? AND h.branch_id=? AND h.opened_at<? AND (h.closed_at IS NULL OR h.closed_at>=?)"
               " AND r.created_at>=? AND r.created_at<?", (ctx.org_id, branch, hi, lo, lo, hi))
    return {"voids": v["n"], "voids_total": v["t"], "cash_refunds": r["n"], "cash_refunds_total": r["t"]}


def shift_summary(db: Database, ctx: Context, shift_id: str) -> dict:
    ctx.require("cash.see_expected")
    from ..sales.cash import CashDrawer
    r = CashDrawer(db).report(ctx, shift_id)
    if r["status"] != "closed":
        raise Conflict("الملخص بيطلع بعد قفل الشيفت", code="shift_open")
    org = db.one("SELECT name FROM organizations WHERE id=?", (ctx.org_id,))["name"]
    from ..printing import settings as rs
    tf = rs.get(db, ctx.org_id)["time_format"]
    k = r["cash_by_kind"] or {}
    cash_sales = k.get("sale", 0)
    cash_back = -(k.get("refund", 0) + k.get("void", 0))          # review P2: what left the drawer for returns / voids
    diff = r["difference"] or 0
    # out of stock now and sold in the last 30 days: what to order first
    out = db.all(
        "SELECT coalesce(i.name_en, i.name_ar) n FROM items i WHERE i.org_id=? AND i.status='active'"
        " AND coalesce(i.is_service,0)=0" + ("" if ctx.can("items.hidden") else " AND coalesce(i.hidden,0)=0") +
        " AND coalesce((SELECT sum(qty) FROM stock_balances b WHERE b.org_id=i.org_id AND b.item_id=i.id"
        "   AND b.branch_id=?),0) <= 0"
        " AND EXISTS (SELECT 1 FROM stock_movements m WHERE m.org_id=i.org_id AND m.item_id=i.id"
        "   AND m.branch_id=? AND m.movement_type='sale' AND m.created_at >= strftime('%Y-%m-%dT%H:%M:%fZ','now','-30 days'))"
        " ORDER BY n LIMIT 15", (ctx.org_id, r["branch_id"], r["branch_id"]))
    lines = [
        f"ملخص شيفت {org} — {r['register_name']}",
        f"من {_local(r['opened_at'], tf)} لـ {_local(r['closed_at'], tf)} — قفله {r.get('closed_by_name') or ''}",
        f"المبيعات: {_egp(r['sales_total'])} ج ({r['sales_count']} فاتورة)",
        f"نقدي: {_egp(cash_sales)} ج" + (f" (رجع منه {_egp(cash_back)} ج مرتجع وإلغاء)" if cash_back else "") + f" · بطاقة: {_egp(r['card_total'])} ج · تحويل: {_egp(r['transfer_total'])} ج",
        f"آجل: {_egp(r['credit_total'])} ج",
        f"المرتجع: {_egp(r['returns_total'])} ج ({r['returns_count']}) · الملغي: {r['voided_count']} فاتورة",
        f"الخصومات: {_egp(r['discount_total'])} ج",
        f"المتوقع في الدرج: {_egp(r['expected_cash'])} ج · المعدود: {_egp(r['counted_cash'])} ج · "
        + ("مطابق" if not diff else f"{'عجز' if diff < 0 else 'زيادة'} {_egp(abs(diff))} ج"),
    ]
    shift = db.one("SELECT * FROM cash_shifts WHERE id=? AND org_id=?", (shift_id, ctx.org_id))
    voids = [{"sale_id": v["id"], "sale_no": v["sale_no"], "amount": v["total"], "cash": v["paid_cash"],
              "at": v["voided_at"], "by": v["voided_by_name"], "sold_by": v["sold_by_name"], "reason": v["void_reason"]}
             for v in _voids_of(db, shift)]
    refunds = [{"return_id": x["id"], "sale_id": x["sale_id"], "sale_no": x["sale_no"], "amount": x["total"], "at": x["created_at"],
                "by": x["by_name"], "sold_by": x["sold_by_name"], "reason": x["reason"]} for x in _cash_refunds_of(db, shift)]
    from ..sales.service import _local_date
    the_day = _local_date(shift["opened_at"])
    day = day_totals(db, ctx, the_day, shift["branch_id"])

    def who(e):
        return (e["by"] or "") + (f" (باعها {e['sold_by']})" if e["sold_by"] and e["sold_by"] != e["by"] else "")
    if not (voids or refunds):
        lines.append("مفيش إلغاء ولا مرتجع نقدي في الشيفت ده")
    for e in voids:
        lines.append(f"• إلغاء فاتورة {e['sale_no']} — {_egp(e['amount'])} ج — {_local(e['at'], tf)} — {who(e)}"
                     f" — السبب: {(e['reason'] or '').strip()}")
    for e in refunds:
        lines.append(f"• مرتجع نقدي فاتورة {e['sale_no']} — {_egp(e['amount'])} ج — {_local(e['at'], tf)} — {who(e)}"
                     f" — السبب: {(e['reason'] or '').strip()}")
    labels = {"card": "بطاقة", "transfer": "محفظة", "credit": "على حساب العميل"}
    for p in r["refund_payments"]:
        if p["method"] in labels:
            channel = f" · {p['channel']}" if p["channel"] else ""
            lines.append(f"• مرتجع فاتورة {p['sale_no']} — {labels[p['method']]}{channel} — {_egp(p['amount'])} ج")
    lines.append(f"إجمالي اليوم {the_day.isoformat()} (كل الشيفتات): إلغاء {day['voids']} فاتورة بـ {_egp(day['voids_total'])} ج · "
                 f"مرتجع نقدي {day['cash_refunds']} بـ {_egp(day['cash_refunds_total'])} ج")
    if out:
        lines.append("خلصوا (اتباعوا آخر 30 يوم): " + "، ".join(x["n"] for x in out))
    else:
        lines.append("مفيش أصناف خلصت من اللي بيتباع")
    return {"text": "\n".join(lines), "difference": diff, "out_of_stock": [x["n"] for x in out],
            "voids": voids, "cash_refunds": refunds, "day": day, "day_date": the_day.isoformat(),
            "register_name": r["register_name"], "refund_payments": r["refund_payments"]}
