"""U3-1: correcting a wrong pack size on an item that already has stock (e.g. RANXAPIN 21TAB entered as 6 strips,
really 3).

Stock is kept in base units counted with the old pack size, so changing the number alone would mis-state every
balance and every unit price. A correction therefore converts, in one transaction, with its audit row:
- every lot's balance: new = old × new_pack / old_pack (the boxes on the shelf stay the same boxes). If a lot holds
  a part of a box that does not convert to whole units, the correction is refused: count that item first;
- the average cost per base unit (× old / new), so the cost of a box does not change;
- min / max stock (base units), half up;
- the pack size itself.
Each balance change is an append-only 'adjustment' movement (ref_type 'pack_fix') with the reason.

Refused while the item is on an unfinished sale or in an open stock count (both hold base units of the old size).
After it, a return or void of a sale completed before the correction, or the cancel of a purchase approved before
it, would put back old-size units; `inventory.service.post_movement` refuses those (`guard_reversal`).
Open credit invoices that sold the item are only counted and shown (U3-1): their money does not change.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from ..core import audit
from ..core.errors import Conflict, NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database
from ..inventory import service as inv

ACTION = "item.pack_corrected"


def _half_up_scale(q: int, num: int, den: int) -> int:
    return (2 * q * num + den) // (2 * den)


def preview(db: Database, ctx: Context, item_id: str, new_pack: int) -> dict:
    """What a correction would do, without doing it."""
    ctx.require("items.write")
    ctx.require("inventory.adjust")
    it = db.one("SELECT id, pack_size, base_unit_name, min_stock, max_stock, is_service FROM items"
                " WHERE id=? AND org_id=?", (item_id, ctx.org_id))
    if not it:
        raise NotFound("الصنف غير موجود")
    if not isinstance(new_pack, int) or isinstance(new_pack, bool) or not 1 <= new_pack <= 10000:
        raise ValidationFailed("عدد الوحدات في العبوة لازم يكون رقم صحيح من 1 لـ 10000")
    old = it["pack_size"]
    if new_pack == old:
        raise ValidationFailed("ده نفس عدد الوحدات الحالي")
    lots = db.all("SELECT branch_id, batch_key, qty FROM stock_balances WHERE org_id=? AND item_id=? AND qty<>0",
                  (ctx.org_id, item_id))
    bad = [dict(l) for l in lots if (l["qty"] * new_pack) % old]
    from ..reports.item_health import open_credit_sales
    return {"item_id": item_id, "old_pack": old, "new_pack": new_pack, "unit": it["base_unit_name"],
            "lots": [{**dict(l), "new_qty": l["qty"] * new_pack // old} for l in lots if not (l["qty"] * new_pack) % old],
            "not_whole": bad,
            "drafts": db.one("SELECT count(DISTINCT s.id) n FROM sales s JOIN sale_lines l ON l.sale_id=s.id"
                             " WHERE s.org_id=? AND l.org_id=? AND l.item_id=? AND s.status='draft'",
                             (ctx.org_id, ctx.org_id, item_id))["n"],
            "counting": db.one("SELECT count(DISTINCT c.id) n FROM count_sessions c JOIN count_entries e ON e.session_id=c.id"
                               " WHERE c.org_id=? AND e.org_id=? AND e.item_id=? AND c.status='open'",
                               (ctx.org_id, ctx.org_id, item_id))["n"],
            "open_credit": open_credit_sales(db, ctx, item_id)}


def correct_pack(db: Database, ctx: Context, item_id: str, new_pack: int, reason: str) -> dict:
    reason = (reason or "").strip()[:200]
    if len(reason) < 3:
        raise ValidationFailed("اكتب سبب التصحيح")
    with db.transaction():
        p = preview(db, ctx, item_id, new_pack)
        if p["drafts"]:
            raise Conflict("الصنف موجود في فاتورة بيع لسه ما اتقفلتش: اقفلها أو احذف السطر الأول", code="pack_in_draft")
        if p["counting"]:
            raise Conflict("الصنف في جرد مفتوح: اعتمد الجرد أو الغيه الأول", code="pack_in_count")
        if p["not_whole"]:
            raise Conflict("فيه رصيد مايتحولش لوحدات كاملة بالعبوة الجديدة (علبة مفتوحة): اعمل جرد للصنف الأول وبعدين صحّح العبوة",
                           code="pack_not_whole", details={"lots": p["not_whole"]})
        old = p["old_pack"]
        for l in p["lots"]:
            delta = l["new_qty"] - l["qty"]
            if delta:
                cost = db.one("SELECT avg_cost FROM item_costs WHERE org_id=? AND branch_id=? AND item_id=?",
                              (ctx.org_id, l["branch_id"], item_id))
                inv.post_movement(db, ctx, item_id=item_id, qty_delta=delta, movement_type="adjustment",
                                  batch_id=l["batch_key"] or None, branch_id=l["branch_id"],
                                  unit_cost=Decimal(cost["avg_cost"]) if cost else None, ref_type="pack_fix",
                                  ref_id=item_id, note=f"تصحيح العبوة {old} ← {new_pack}: {reason}",
                                  reason="entry_error", allow_negative=True)
        for c in db.all("SELECT branch_id, avg_cost FROM item_costs WHERE org_id=? AND item_id=?", (ctx.org_id, item_id)):
            new = (Decimal(c["avg_cost"]) * old / new_pack).quantize(inv.COST_Q, ROUND_HALF_UP)
            db.execute("UPDATE item_costs SET avg_cost=?, updated_at=? WHERE org_id=? AND branch_id=? AND item_id=?",
                       (str(new), inv._now(), ctx.org_id, c["branch_id"], item_id))
        it = db.one("SELECT min_stock, max_stock FROM items WHERE id=? AND org_id=?", (item_id, ctx.org_id))
        mn = _half_up_scale(it["min_stock"] or 0, new_pack, old)
        mx = None if it["max_stock"] is None else _half_up_scale(it["max_stock"], new_pack, old)
        db.execute("UPDATE items SET pack_size=?, min_stock=?, max_stock=?, updated_at=? WHERE id=? AND org_id=?",
                   (new_pack, mn, mx, inv._now(), item_id, ctx.org_id))
        audit.record(db, ctx, ACTION, "item", item_id, {"pack_size": old},
                     {"pack_size": new_pack, "reason": reason, "lots": len(p["lots"]), "open_credit": p["open_credit"]})
    return {**p, "done": True}


def corrected_at(db: Database, org_id: str, item_id: str) -> str | None:
    r = db.one("SELECT max(at) a FROM audit_log WHERE org_id=? AND entity_type='item' AND entity_id=? AND action=?",
               (org_id, item_id, ACTION))
    return r["a"] if r else None


def guard_reversal(db: Database, org_id: str, item_id: str, movement_type: str, ref_type: str | None,
                   ref_id: str | None) -> None:
    """Refuses putting back old-size units: a return / void of a sale completed before the pack correction, or the
    cancel of a purchase approved before it."""
    if movement_type not in ("sale_return", "purchase_reversal") or not ref_id:
        return
    if ref_type in ("sale", "sale_void"):
        q = "SELECT completed_at d FROM sales WHERE id=? AND org_id=?"
    elif ref_type == "purchase_invoice":
        q = "SELECT approved_at d FROM purchase_invoices WHERE id=? AND org_id=?"
    else:
        return
    fixed = corrected_at(db, org_id, item_id)
    if not fixed:
        return
    doc = db.one(q, (ref_id, org_id))
    if doc and doc["d"] and doc["d"] < fixed:
        raise Conflict("عدد الوحدات في عبوة الصنف ده اتصحح بعد العملية دي، فكمياتها القديمة مش هترجع صح."
                       " رجّع الكمية بتسوية مخزون أو بجرد للصنف", code="pack_corrected_after")
