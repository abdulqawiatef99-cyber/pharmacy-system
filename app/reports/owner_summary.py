"""U3-8 the owner's summary of a closed shift, as plain text ready to copy (sending stays manual: the program sends
nothing). Sales, cash, card / transfer, credit, returns, the drawer difference and the items that ran out.
Owner / manager (cash.see_expected). Read only; every figure comes from the shift report itself."""
from __future__ import annotations

from ..core.errors import Conflict
from ..core.permissions import Context
from ..db import Database


def _egp(p: int | None) -> str:
    p = p or 0
    sign, p = ("-", -p) if p < 0 else ("", p)
    return f"{sign}{p // 100:,}.{p % 100:02d}"


def _local(utc_iso: str | None) -> str:
    """The PC's local time of a stored UTC stamp, as «YYYY-MM-DD HH:MM»."""
    from datetime import datetime, timezone
    if not utc_iso:
        return ""
    try:
        t = datetime.strptime(utc_iso, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).astimezone()
    except ValueError:
        return utc_iso[:16].replace("T", " ")
    return t.strftime("%Y-%m-%d %H:%M")


def shift_summary(db: Database, ctx: Context, shift_id: str) -> dict:
    ctx.require("cash.see_expected")
    from ..sales.cash import CashDrawer
    r = CashDrawer(db).report(ctx, shift_id)
    if r["status"] != "closed":
        raise Conflict("الملخص بيطلع بعد قفل الشيفت", code="shift_open")
    org = db.one("SELECT name FROM organizations WHERE id=?", (ctx.org_id,))["name"]
    cash_sales = (r["cash_by_kind"] or {}).get("sale", 0)
    diff = r["difference"] or 0
    # out of stock now and sold in the last 30 days: what to order first
    out = db.all(
        "SELECT coalesce(i.name_en, i.name_ar) n FROM items i WHERE i.org_id=? AND i.status='active'"
        " AND coalesce(i.is_service,0)=0"
        " AND coalesce((SELECT sum(qty) FROM stock_balances b WHERE b.org_id=i.org_id AND b.item_id=i.id),0) <= 0"
        " AND EXISTS (SELECT 1 FROM stock_movements m WHERE m.org_id=i.org_id AND m.item_id=i.id"
        "   AND m.movement_type='sale' AND m.created_at >= strftime('%Y-%m-%dT%H:%M:%fZ','now','-30 days'))"
        " ORDER BY n LIMIT 15", (ctx.org_id,))
    lines = [
        f"ملخص شيفت {org}",
        f"من {_local(r['opened_at'])} لـ {_local(r['closed_at'])} — قفله {r.get('closed_by_name') or ''}",
        f"المبيعات: {_egp(r['sales_total'])} ج ({r['sales_count']} فاتورة)",
        f"نقدي: {_egp(cash_sales)} ج · بطاقة: {_egp(r['card_total'])} ج · تحويل: {_egp(r['transfer_total'])} ج",
        f"آجل: {_egp(r['credit_total'])} ج",
        f"المرتجع: {_egp(r['returns_total'])} ج ({r['returns_count']}) · الملغي: {r['voided_count']} فاتورة",
        f"الخصومات: {_egp(r['discount_total'])} ج",
        f"المتوقع في الدرج: {_egp(r['expected_cash'])} ج · المعدود: {_egp(r['counted_cash'])} ج · "
        + ("مطابق" if not diff else f"{'عجز' if diff < 0 else 'زيادة'} {_egp(abs(diff))} ج"),
    ]
    if out:
        lines.append("خلصوا (اتباعوا آخر 30 يوم): " + "، ".join(x["n"] for x in out))
    else:
        lines.append("مفيش أصناف خلصت من اللي بيتباع")
    return {"text": "\n".join(lines), "difference": diff, "out_of_stock": [x["n"] for x in out]}
