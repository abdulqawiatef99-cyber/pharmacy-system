"""Returns to a supplier (مرتجع مشتريات): the stock leaves, the supplier's account goes down by
the agreed value, and a numbered slip is printed for the supplier's representative to sign."""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from ..core import audit, notify
from ..core.errors import Conflict, NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id
from ..inventory import service as inv
from ..inventory.adjust import avg_cost

REASONS = {"expired": "منتهي الصلاحية", "near_expiry": "قارب على الانتهاء", "damaged": "تالف / مكسور",
           "wrong_item": "صنف خاطئ / غير مطلوب", "overstock": "زيادة / راكد", "recall": "سحب من هيئة الدواء", "other": "سبب آخر"}
MAX_LINES = 200


def suggest_value(db: Database, ctx: Context, item_id: str, batch_id: str | None, supplier_id: str | None = None) -> int:
    """The value the return screen proposes. It is a purchase cost, so it is shown only to who may
    record a return and see its values (the same rights create() asks for a typed value)."""
    ctx.require("purchases.approve")
    if not (ctx.can("costs.read") or ctx.can("suppliers.pay")):
        ctx.require("suppliers.pay")
    return _unit_value(db, ctx, item_id, batch_id, supplier_id)


def _half_up(v: Decimal) -> int:
    return int(v.to_integral_value(ROUND_HALF_UP))


def _unit_cost(db: Database, ctx: Context, item_id: str, batch_id: str | None, supplier_id: str | None) -> Decimal | None:
    """Piasters per base unit, exact (not rounded): what THIS supplier was paid for the lot on its last standing
    invoice. A lot can come from two suppliers at two prices, and the latest price of the lot from anyone
    credited supplier A with supplier B's price (#324 S30). When this supplier never invoiced the lot (opening
    stock, another supplier's lot, no supplier chosen yet): the lowest price paid for the lot, so the proposal
    never credits more than someone was paid. Untracked stock without such an invoice: the average cost."""
    if supplier_id:
        r = db.one("SELECT m.unit_cost FROM stock_movements m JOIN purchase_invoices pi ON pi.id=m.ref_id AND pi.org_id=m.org_id"
                   " WHERE m.org_id=? AND m.branch_id=? AND m.item_id=? AND m.batch_id IS ? AND m.movement_type='purchase'"
                   " AND m.ref_type='purchase_invoice' AND m.unit_cost IS NOT NULL AND pi.supplier_id=? AND pi.status='approved'"
                   " ORDER BY m.rowid DESC LIMIT 1", (ctx.org_id, ctx.branch_id, item_id, batch_id, supplier_id))
        if r:
            return Decimal(r["unit_cost"]) * 100
    if batch_id:
        paid = [Decimal(r["unit_cost"]) for r in db.all(
            "SELECT m.unit_cost FROM stock_movements m LEFT JOIN purchase_invoices pi ON m.ref_type='purchase_invoice'"
            " AND pi.id=m.ref_id AND pi.org_id=m.org_id WHERE m.org_id=? AND m.branch_id=? AND m.item_id=? AND m.batch_id=?"
            " AND m.movement_type IN ('purchase','opening') AND m.unit_cost IS NOT NULL"
            " AND coalesce(pi.status,'approved')<>'cancelled'", (ctx.org_id, ctx.branch_id, item_id, batch_id))]
        if paid:
            return min(paid) * 100
    c = avg_cost(db, ctx.org_id, ctx.branch_id, item_id)
    return c * 100 if c is not None else None


def _unit_value(db: Database, ctx: Context, item_id: str, batch_id: str | None, supplier_id: str | None = None) -> int:
    """Whole piasters per base unit, for the screen. No permission check: create() values a return with it
    for a user who never sees the figure."""
    c = _unit_cost(db, ctx, item_id, batch_id, supplier_id)
    return _half_up(c) if c is not None else 0


def _pieces(qty: int, value: int) -> list[tuple[int, int]]:
    """`qty` units worth `value` piasters together, as lines of whole piasters per unit that add up to it
    exactly: three units worth 100 are two at 33 and one at 34 (never 3 x 33 = 99)."""
    base, extra = divmod(value, qty)
    return [(qty, base)] if not extra else [(qty - extra, base), (extra, base + 1)]


def create(db: Database, ctx: Context, supplier_id: str, lines: list[dict], note: str | None = None) -> dict:
    ctx.require("purchases.approve")
    sup = db.one("SELECT id, name FROM suppliers WHERE id=? AND org_id=?", (supplier_id, ctx.org_id))
    if not sup:
        raise NotFound("المورد غير موجود")
    if not isinstance(lines, list) or not lines or len(lines) > MAX_LINES:
        raise ValidationFailed("أضف الأصناف المرتجعة")
    clean = []
    for ln in lines:
        item_id, batch_id = str(ln.get("item_id") or ""), (ln.get("batch_id") or None)
        qty, reason = ln.get("qty_base"), ln.get("reason")
        if not db.one("SELECT 1 FROM items WHERE id=? AND org_id=?", (item_id, ctx.org_id)):
            raise NotFound("صنف غير موجود")
        if batch_id and not db.one("SELECT 1 FROM batches WHERE id=? AND org_id=? AND item_id=?",
                                   (batch_id, ctx.org_id, item_id)):
            raise NotFound("التشغيلة لا تخص الصنف")
        if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:     # true is not 1 unit
            raise ValidationFailed("الكمية غير صحيحة")
        if reason not in REASONS:
            raise ValidationFailed("اختر سبب المرتجع لكل صنف")
        value = ln.get("unit_value")
        if value is not None:
            if not (ctx.can("costs.read") or ctx.allowed("suppliers.pay")):
                # a typed value credits the supplier's account with that amount: only who may see the values
                ctx.require("suppliers.pay")
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValidationFailed("قيمة الوحدة غير صحيحة")
        cost = _unit_cost(db, ctx, item_id, batch_id, supplier_id)
        # the proposal the screen was shown and sent back untouched. The screen may have asked before the
        # supplier was chosen (it then got the lot's lowest price): both proposals mean "at the purchase
        # price", and that price is this supplier's own. Keeping the blind figure credited supplier B with
        # supplier A's lower price (Codex on #347)
        proposed = {_unit_value(db, ctx, item_id, batch_id, supplier_id), _unit_value(db, ctx, item_id, batch_id)}
        if value is None or (cost is not None and value in proposed):
            # at the purchase price: the quantity is multiplied by the exact cost and rounded once. Rounding
            # the unit first credited 99 piasters for a whole three-unit pack bought for 100 (#324 S31)
            pieces = _pieces(qty, _half_up(cost * qty) if cost is not None else 0)
        else:
            pieces = [(qty, value)]
        clean.append((item_id, batch_id, reason, pieces))
    total = sum(q * v for _, _, _, pieces in clean for q, v in pieces)
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
        for item_id, batch_id, reason, pieces in clean:
            for qty, value in pieces:
                # no lot named = the stock kept without a lot, and only that: it used to take the
                # earliest-expiring dated lot instead and leave the untracked row untouched (#324 S29)
                if not batch_id and inv.batch_qty(db, ctx.org_id, ctx.branch_id, item_id, None) < qty:
                    raise Conflict("الكمية أكبر من الرصيد المسجل بدون تشغيلة؛ اختر التشغيلة", code="insufficient_stock")
                lid = new_id()
                db.execute("INSERT INTO supplier_return_lines (id, return_id, item_id, batch_id, qty_base, unit_value, reason)"
                           " VALUES (?,?,?,?,?,?,?)", (lid, rid, item_id, batch_id, qty, value, reason))
                inv.post_movement(db, ctx, item_id=item_id, qty_delta=-qty, batch_id=batch_id, movement_type="supplier_return",
                                  unit_cost=avg_cost(db, ctx.org_id, ctx.branch_id, item_id), ref_type="supplier_return",
                                  ref_id=rid, ref_line_id=lid, note=f"مرتجع للمورد {sup['name']}", reason=reason)
        if total:
            db.execute("INSERT INTO supplier_ledger (id, org_id, supplier_id, amount, kind, ref_id, note, created_by)"
                       " VALUES (?,?,?,?,'return',?,?,?)", (new_id(), ctx.org_id, supplier_id, -total, rid,
                                                          f"مرتجع رقم {no}", ctx.user_id))
        audit.record(db, ctx, "supplier.return", "supplier_return", rid,
                     after={"supplier": supplier_id, "no": no, "total": total, "lines": len(clean)})
        notify.emit(db, ctx, "supplier_return", "info", f"مرتجع رقم {no} إلى {sup['name']} — {total / 100:,.2f} ج",
                    f"{len(clean)} صنف", "supplier_return", rid)
    if not ctx.can("purchases.read"):
        # saved: answer without the slip this person may not read, never a 403 after the commit (#324 S6)
        return {"ok": True, "id": rid, "return_no": no}
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
        " JOIN items i ON i.id=l.item_id LEFT JOIN batches b ON b.id=l.batch_id WHERE l.return_id=?"
        " ORDER BY i.name_en, l.rowid", (rid,))]
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
        raise ValidationFailed("اختر الفترة")
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
