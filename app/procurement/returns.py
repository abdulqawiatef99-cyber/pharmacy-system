"""Returns to a supplier (مرتجع مشتريات): the stock leaves, the supplier's account goes down by
the agreed value, and a numbered slip is printed for the supplier's representative to sign."""
from __future__ import annotations

from datetime import date

from ..core import audit, notify
from ..core.errors import Conflict, NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id
from ..inventory import service as inv
from ..inventory.adjust import avg_cost

REASONS = {"expired": "منتهي الصلاحية", "near_expiry": "قرب ينتهي", "damaged": "تالف / مكسور",
           "wrong_item": "صنف غلط / مش مطلوب", "overstock": "زيادة / راكد", "recall": "سحب من هيئة الدواء", "other": "سبب تاني"}
MAX_LINES = 200


def suggest_value(db: Database, ctx: Context, item_id: str, batch_id: str | None) -> int:
    """Piasters per base unit: the net price paid for that batch on its purchase invoice,
    else the item's average cost."""
    if batch_id:
        r = db.one("SELECT unit_cost FROM stock_movements WHERE org_id=? AND item_id=? AND batch_id=? AND movement_type IN"
                   " ('purchase','opening') AND unit_cost IS NOT NULL ORDER BY created_at DESC LIMIT 1", (ctx.org_id, item_id, batch_id))
        if r:
            from decimal import Decimal
            return int((Decimal(r["unit_cost"]) * 100).to_integral_value())
    c = avg_cost(db, ctx.org_id, ctx.branch_id, item_id)
    return int((c * 100).to_integral_value()) if c is not None else 0


def create(db: Database, ctx: Context, supplier_id: str, lines: list[dict], note: str | None = None) -> dict:
    ctx.require("purchases.approve")
    sup = db.one("SELECT id, name FROM suppliers WHERE id=? AND org_id=?", (supplier_id, ctx.org_id))
    if not sup:
        raise NotFound("المورد غير موجود")
    if not isinstance(lines, list) or not lines or len(lines) > MAX_LINES:
        raise ValidationFailed("ضيف الأصناف اللي راجعة")
    clean = []
    for ln in lines:
        item_id, batch_id = str(ln.get("item_id") or ""), (ln.get("batch_id") or None)
        qty, reason = ln.get("qty_base"), ln.get("reason")
        if not db.one("SELECT 1 FROM items WHERE id=? AND org_id=?", (item_id, ctx.org_id)):
            raise NotFound("صنف غير موجود")
        if batch_id and not db.one("SELECT 1 FROM batches WHERE id=? AND item_id=?", (batch_id, item_id)):
            raise NotFound("التشغيلة مش بتاعة الصنف")
        if not isinstance(qty, int) or qty <= 0:
            raise ValidationFailed("الكمية غير صحيحة")
        if reason not in REASONS:
            raise ValidationFailed("اختار سبب المرتجع لكل صنف")
        value = ln.get("unit_value")
        if value is None:
            value = suggest_value(db, ctx, item_id, batch_id)
        if not isinstance(value, int) or value < 0:
            raise ValidationFailed("قيمة الوحدة غير صحيحة")
        clean.append((item_id, batch_id, qty, value, reason))
    total = sum(q * v for _, _, q, v, _ in clean)
    rid = new_id()
    with db.transaction():
        db.execute("INSERT INTO branch_counters (org_id, branch_id, kind, next_no) VALUES (?,?,'supplier_return',1)"
                   " ON CONFLICT(org_id, branch_id, kind) DO NOTHING", (ctx.org_id, ctx.branch_id))
        no = db.one("SELECT next_no FROM branch_counters WHERE org_id=? AND branch_id=? AND kind='supplier_return'",
                    (ctx.org_id, ctx.branch_id))["next_no"]
        db.execute("UPDATE branch_counters SET next_no=next_no+1 WHERE org_id=? AND branch_id=? AND kind='supplier_return'",
                   (ctx.org_id, ctx.branch_id))
        db.execute("INSERT INTO supplier_returns (id, org_id, branch_id, supplier_id, return_no, total, note, created_by)"
                   " VALUES (?,?,?,?,?,?,?,?)", (rid, ctx.org_id, ctx.branch_id, supplier_id, no, total,
                                                (note or "").strip()[:200] or None, ctx.user_id))
        for item_id, batch_id, qty, value, reason in clean:
            lid = new_id()
            db.execute("INSERT INTO supplier_return_lines (id, return_id, item_id, batch_id, qty_base, unit_value, reason)"
                       " VALUES (?,?,?,?,?,?,?)", (lid, rid, item_id, batch_id, qty, value, reason))
            if batch_id:
                inv.post_movement(db, ctx, item_id=item_id, qty_delta=-qty, batch_id=batch_id, movement_type="supplier_return",
                                  unit_cost=avg_cost(db, ctx.org_id, ctx.branch_id, item_id), ref_type="supplier_return",
                                  ref_id=rid, ref_line_id=lid, note=f"مرتجع للمورد {sup['name']}", reason=reason)
            else:
                left = qty
                for r in db.all("SELECT sb.batch_key, sb.qty FROM stock_balances sb LEFT JOIN batches b ON b.id=nullif(sb.batch_key,'')"
                                " WHERE sb.org_id=? AND sb.branch_id=? AND sb.item_id=? AND sb.qty>0 ORDER BY b.expiry_date IS NULL,"
                                " b.expiry_date", (ctx.org_id, ctx.branch_id, item_id)):
                    n = min(left, r["qty"])
                    inv.post_movement(db, ctx, item_id=item_id, qty_delta=-n, batch_id=r["batch_key"] or None,
                                      movement_type="supplier_return", unit_cost=avg_cost(db, ctx.org_id, ctx.branch_id, item_id),
                                      ref_type="supplier_return", ref_id=rid, ref_line_id=lid,
                                      note=f"مرتجع للمورد {sup['name']}", reason=reason)
                    left -= n
                    if not left:
                        break
                if left:
                    raise Conflict("الكمية أكبر من الرصيد", code="insufficient_stock")
        if total:
            db.execute("INSERT INTO supplier_ledger (id, org_id, supplier_id, amount, kind, ref_id, note, created_by)"
                       " VALUES (?,?,?,?,'return',?,?,?)", (new_id(), ctx.org_id, supplier_id, -total, rid,
                                                          f"مرتجع رقم {no}", ctx.user_id))
        audit.record(db, ctx, "supplier.return", "supplier_return", rid,
                     after={"supplier": supplier_id, "no": no, "total": total, "lines": len(clean)})
        notify.emit(db, ctx, "supplier_return", "info", f"مرتجع رقم {no} لـ {sup['name']} — {total / 100:,.2f} ج",
                    f"{len(clean)} صنف", "supplier_return", rid)
    return get(db, ctx, rid)


def get(db: Database, ctx: Context, rid: str) -> dict:
    ctx.require("purchases.read")
    r = db.one("SELECT r.*, s.name supplier, s.supplier_type, u.full_name by_name FROM supplier_returns r"
               " JOIN suppliers s ON s.id=r.supplier_id LEFT JOIN users u ON u.id=r.created_by WHERE r.id=? AND r.org_id=?",
               (rid, ctx.org_id))
    if not r:
        raise NotFound("المرتجع غير موجود")
    out = dict(r)
    out["lines"] = [dict(x) | {"reason_label": REASONS.get(x["reason"], x["reason"])} for x in db.all(
        "SELECT l.*, i.name_en, i.name_ar, i.pack_size, i.base_unit_name, b.batch_no, b.expiry_date FROM supplier_return_lines l"
        " JOIN items i ON i.id=l.item_id LEFT JOIN batches b ON b.id=l.batch_id WHERE l.return_id=? ORDER BY i.name_en", (rid,))]
    if not (ctx.can("suppliers.pay") or ctx.can("costs.read")):
        out.pop("total", None)
        for x in out["lines"]:
            x.pop("unit_value", None)
    return out


def listing(db: Database, ctx: Context, date_from: str, date_to: str, supplier_id: str | None = None) -> dict:
    from ..sales.service import local_day_bounds
    ctx.require("purchases.read")
    try:
        d1, d2 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except (TypeError, ValueError):
        raise ValidationFailed("اختار الفترة")
    q = ("SELECT r.id, r.return_no, r.total, r.created_at, r.note, s.name supplier, u.full_name by_name,"
         " (SELECT count(*) FROM supplier_return_lines l WHERE l.return_id=r.id) lines FROM supplier_returns r"
         " JOIN suppliers s ON s.id=r.supplier_id LEFT JOIN users u ON u.id=r.created_by"
         " WHERE r.org_id=? AND r.created_at>=? AND r.created_at<?")
    p: list = [ctx.org_id, local_day_bounds(d1)[0], local_day_bounds(d2)[1]]
    if supplier_id:
        q += " AND r.supplier_id=?"
        p.append(supplier_id)
    rows = [dict(x) for x in db.all(q + " ORDER BY r.created_at DESC", tuple(p))]
    show = ctx.can("suppliers.pay") or ctx.can("costs.read")
    if not show:
        for x in rows:
            x.pop("total", None)
    return {"from": d1.isoformat(), "to": d2.isoformat(), "rows": rows,
            "total": sum(x.get("total", 0) for x in rows) if show else None}
