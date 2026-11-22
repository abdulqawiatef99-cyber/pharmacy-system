"""Stock that goes up or down outside buying and selling: every change has a reason.

- Expired or damaged stock is destroyed (إعدام): movement 'damage'.
- Lost / not found, entry mistakes, surplus found, recall: movement 'adjustment'.
Each movement keeps the item's average cost at that moment, so the loss report can value it.
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from ..core import audit, notify
from ..core.errors import Conflict, NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id
from . import service as inv

REASONS = {
    "expired": "منتهي الصلاحية",
    "damaged": "تالف / هالك",
    "lost": "مفقود / غير موجود",
    "entry_error": "خطأ في الإدخال",
    "found": "زيادة وُجدت",
    "recall": "سحب من هيئة الدواء",
    "other": "سبب آخر",
}
DESTROY = ("expired", "damaged")          # stock that is physically destroyed
UP_OK = ("found", "entry_error", "other")  # reasons that can add stock


def label(code: str | None) -> str:
    return REASONS.get(code or "", code or "")


def avg_cost(db: Database, org_id: str, branch_id: str, item_id: str) -> Decimal | None:
    r = db.one("SELECT avg_cost FROM item_costs WHERE org_id=? AND branch_id=? AND item_id=?", (org_id, branch_id, item_id))
    return Decimal(r["avg_cost"]) if r and r["avg_cost"] is not None else None


def check_reason(reason: str, note: str | None, up: bool) -> str | None:
    if reason not in REASONS:
        raise ValidationFailed("اختر السبب")
    if up and reason not in UP_OK:
        raise ValidationFailed("هذا السبب لنقص الرصيد فقط")
    if not up and reason == "found":
        raise ValidationFailed("هذا السبب لزيادة الرصيد فقط")
    note = (note or "").strip()[:200] or None
    if reason == "other" and not note:
        raise ValidationFailed("اكتب السبب")
    return note


def take(db: Database, ctx: Context, item_id: str, qty: int, *, batch_id: str | None, reason: str, note: str | None,
         ref_type: str, ref_id: str | None, branch_id: str | None = None) -> int:
    """Removes `qty` base units (from one batch, or the earliest-expiring first). Returns the value in piasters.
    Must run inside the caller's transaction."""
    branch_id = branch_id or ctx.branch_id
    cost = avg_cost(db, ctx.org_id, branch_id, item_id)
    mtype = "damage" if reason in DESTROY else "adjustment"
    event = new_id()      # one removal, however many lots it takes: the loss report values it as one (ref_line_id)
    if batch_id:
        plan = [(batch_id, qty)]
    else:
        plan, left = [], qty
        for r in db.all("SELECT sb.batch_key, sb.qty FROM stock_balances sb LEFT JOIN batches b ON b.id = nullif(sb.batch_key,'')"
                        " WHERE sb.org_id=? AND sb.branch_id=? AND sb.item_id=? AND sb.qty > 0"
                        " ORDER BY b.expiry_date IS NULL, b.expiry_date", (ctx.org_id, branch_id, item_id)):
            n = min(left, r["qty"])
            plan.append((r["batch_key"] or None, n))
            left -= n
            if not left:
                break
        if left:
            raise Conflict("الكمية أكبر من الرصيد المسجل", code="insufficient_stock")
    for b, n in plan:
        inv.post_movement(db, ctx, item_id=item_id, qty_delta=-n, batch_id=b, movement_type=mtype, unit_cost=cost,
                          ref_type=ref_type, ref_id=ref_id, ref_line_id=event, note=note or label(reason), reason=reason,
                          branch_id=branch_id)
    return int((cost * qty * 100).to_integral_value(ROUND_HALF_UP)) if cost is not None else 0


def adjust(db: Database, ctx: Context, item_id: str, *, qty_delta: int | None = None, set_to: int | None = None,
           batch_id: str | None = None, reason: str, note: str | None = None) -> dict:
    """Change one item's stock by hand, with a reason. set_to=0 means "zero it"."""
    ctx.require("inventory.adjust")
    item = db.one("SELECT id, name_en, name_ar FROM items WHERE id=? AND org_id=?", (item_id, ctx.org_id))
    if not item:
        raise NotFound("الصنف غير موجود")
    if batch_id and not db.one("SELECT 1 FROM batches WHERE id=? AND item_id=?", (batch_id, item_id)):
        raise NotFound("التشغيلة لا تخص هذا الصنف")
    if set_to is not None and (not isinstance(set_to, int) or isinstance(set_to, bool) or set_to < 0):
        raise ValidationFailed("الرصيد الجديد غير صحيح")
    name = item["name_en"] or item["name_ar"]
    with db.transaction():
        # read under the write lock: two "set to 12" read before it both added 2 and ended at 14 (#324 S16)
        current = inv.batch_qty(db, ctx.org_id, ctx.branch_id, item_id, batch_id) if batch_id else \
            db.one("SELECT coalesce(sum(qty),0) q FROM stock_balances WHERE org_id=? AND branch_id=? AND item_id=?",
                   (ctx.org_id, ctx.branch_id, item_id))["q"]
        if set_to is not None:
            qty_delta = set_to - current
        if not isinstance(qty_delta, int) or isinstance(qty_delta, bool) or qty_delta == 0:
            raise ValidationFailed("لا يوجد تغيير في الرصيد")
        note = check_reason(reason, note, qty_delta > 0)
        if qty_delta > 0:
            cost = avg_cost(db, ctx.org_id, ctx.branch_id, item_id)
            inv.post_movement(db, ctx, item_id=item_id, qty_delta=qty_delta, batch_id=batch_id, movement_type="adjustment",
                              unit_cost=cost, ref_type="manual_adjust", note=note or label(reason), reason=reason)
            value = int((cost * qty_delta * 100).to_integral_value(ROUND_HALF_UP)) if cost is not None else 0
        else:
            if -qty_delta > current:
                raise Conflict("الكمية أكبر من الرصيد المسجل", code="insufficient_stock")
            value = -take(db, ctx, item_id, -qty_delta, batch_id=batch_id, reason=reason, note=note,
                          ref_type="manual_adjust", ref_id=None)
        audit.record(db, ctx, "stock.adjust", "item", item_id, before={"qty": current},
                     after={"qty": current + qty_delta, "reason": reason, "note": note, "batch": batch_id, "value": value})
        if value < 0:
            notify.emit(db, ctx, "stock_loss", "warning", f"{label(reason)}: {name} — {-qty_delta} وحدة ({-value / 100:,.2f} ج بالتكلفة)",
                        note, "item", item_id)
    return {"item_id": item_id, "before": current, "after": current + qty_delta, "value": value}


def loss_report(db: Database, ctx: Context, date_from: str, date_to: str) -> dict:
    """Stock that left without a sale in a period, by reason, valued at cost (owner / costs.read)."""
    from ..sales.service import local_day_bounds
    ctx.require("costs.read")
    try:
        d1, d2 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except (TypeError, ValueError):
        raise ValidationFailed("اختر الفترة")
    rows = db.all(
        "SELECT m.id, m.ref_line_id, m.created_at, m.movement_type, m.reason, m.qty_delta, m.unit_cost, m.note, m.ref_type, m.ref_id,"
        " i.id item_id, i.name_en, i.name_ar, i.pack_size, i.base_unit_name, b.batch_no, b.expiry_date, u.full_name by_name,"
        " sp.name supplier FROM stock_movements m JOIN items i ON i.id=m.item_id LEFT JOIN batches b ON b.id=m.batch_id"
        " LEFT JOIN users u ON u.id=m.created_by LEFT JOIN supplier_returns r ON r.id=m.ref_id AND m.movement_type='supplier_return'"
        " LEFT JOIN suppliers sp ON sp.id=r.supplier_id"
        " WHERE m.org_id=? AND m.movement_type IN ('adjustment','damage','supplier_return') AND m.created_at>=? AND m.created_at<?"
        " AND coalesce(m.ref_type,'')<>'pack_fix'"          # a pack-size correction keeps the value (U3-1 review P1)
        " ORDER BY m.created_at", (ctx.org_id, local_day_bounds(d1)[0], local_day_bounds(d2)[1]))
    out, by, exact = [], {}, {}
    for r in rows:
        d = dict(r)
        event = d.pop("ref_line_id") or d["id"]      # the removal this movement belongs to (take()), or itself
        del d["id"]
        cost = Decimal(r["unit_cost"]) * r["qty_delta"] * 100 if r["unit_cost"] else None
        d["value"] = int(cost.to_integral_value(ROUND_HALF_UP)) if cost is not None else None
        key = "supplier_return" if r["movement_type"] == "supplier_return" else (r["reason"] or "count")
        d["reason_label"] = "مرتجع للمورد" if key == "supplier_return" else ("تسوية جرد" if key == "count" else label(key))
        g = by.setdefault(key, {"reason": key, "label": d["reason_label"], "qty_out": 0, "qty_in": 0, "value": 0, "unknown_cost": 0})
        if r["qty_delta"] < 0:
            g["qty_out"] += -r["qty_delta"]
        else:
            g["qty_in"] += r["qty_delta"]
        if cost is None:
            g["unknown_cost"] += 1
        else:
            exact[(key, event)] = exact.get((key, event), Decimal(0)) + cost
        out.append(d)
    # one removal is valued once, as its adjustment recorded it: three units taken from three lots at a third
    # of a pound each are 1.00, not three movements rounded to 0.33 (#324 S32). Separate removals keep their
    # own rounding and are then added as whole piasters, so the report equals the sum of their audit records
    for (key, _), v in exact.items():
        by[key]["value"] += int(v.to_integral_value(ROUND_HALF_UP))
    groups = sorted(by.values(), key=lambda g: g["value"])
    return {"from": d1.isoformat(), "to": d2.isoformat(), "rows": out, "groups": groups,
            "total": sum(g["value"] for g in groups if g["reason"] != "supplier_return"),
            "returned_to_suppliers": sum(g["value"] for g in groups if g["reason"] == "supplier_return")}
