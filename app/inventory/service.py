"""Inventory: batches, the immutable stock ledger, balances and average cost.

All quantity changes go through post_movement(); nothing else writes
stock_balances or item_costs. Balances can always be re-derived from the
ledger and verify_balances() proves they match.
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from ..core.errors import Conflict, NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id

COST_Q = Decimal("0.000001")


def _now() -> str:
    from ..core.accounts import iso, utcnow
    return iso(utcnow())


def get_or_create_batch(db: Database, ctx: Context, item_id: str, batch_no: str | None,
                        expiry_date: str | None, expiry_precision: str | None = None) -> str | None:
    """Returns a batch id, or None when neither batch number nor expiry is known
    (untracked stock)."""
    if not db.one("SELECT 1 FROM items WHERE id=? AND org_id=?", (item_id, ctx.org_id)):
        raise NotFound("الصنف غير موجود")
    batch_no = (batch_no or "").strip().upper()
    if not batch_no and not expiry_date:
        return None
    if expiry_date:
        try:
            date.fromisoformat(expiry_date)
        except ValueError as e:
            raise ValidationFailed("تاريخ صلاحية غير صحيح") from e
    r = db.one("SELECT id FROM batches WHERE org_id=? AND item_id=? AND batch_no=?"
               " AND expiry_date IS ?", (ctx.org_id, item_id, batch_no, expiry_date))
    if r:
        return r["id"]
    bid = new_id()
    db.execute("INSERT INTO batches (id, org_id, item_id, batch_no, expiry_date, expiry_precision)"
               " VALUES (?,?,?,?,?,?)",
               (bid, ctx.org_id, item_id, batch_no, expiry_date,
                expiry_precision if expiry_date else None))
    return bid


def _item_qty(db: Database, org_id: str, branch_id: str, item_id: str) -> int:
    r = db.one("SELECT coalesce(sum(qty),0) q FROM stock_balances WHERE org_id=? AND branch_id=?"
               " AND item_id=?", (org_id, branch_id, item_id))
    return int(r["q"])


def batch_qty(db: Database, org_id: str, branch_id: str, item_id: str, batch_id: str | None) -> int:
    r = db.one("SELECT qty FROM stock_balances WHERE org_id=? AND branch_id=? AND item_id=?"
               " AND batch_key=?", (org_id, branch_id, item_id, batch_id or ""))
    return int(r["qty"]) if r else 0


def post_movement(db: Database, ctx: Context, *, item_id: str, qty_delta: int,
                  movement_type: str, batch_id: str | None = None,
                  unit_cost: Decimal | None = None, ref_type: str | None = None,
                  ref_id: str | None = None, ref_line_id: str | None = None,
                  note: str | None = None, allow_negative: bool = False,
                  branch_id: str | None = None, reason: str | None = None) -> str:
    """Must be called inside db.transaction() together with the business
    document that causes it."""
    branch_id = branch_id or ctx.branch_id
    if not branch_id:
        raise ValidationFailed("لا يوجد فرع محدد")
    if not isinstance(qty_delta, int) or isinstance(qty_delta, bool) or qty_delta == 0:     # true is not 1 unit
        raise ValidationFailed("الكمية يجب أن تكون عددًا صحيحًا غير صفري")
    if not db.one("SELECT 1 FROM items WHERE id=? AND org_id=?", (item_id, ctx.org_id)):
        raise NotFound("الصنف غير موجود")
    if not db.one("SELECT 1 FROM branches WHERE id=? AND org_id=?", (branch_id, ctx.org_id)):
        raise NotFound("الفرع غير موجود")
    if batch_id and not db.one("SELECT 1 FROM batches WHERE id=? AND org_id=? AND item_id=?",
                               (batch_id, ctx.org_id, item_id)):
        raise NotFound("التشغيلة لا تخص هذا الصنف")
    if movement_type in ("sale_return", "purchase_reversal", "supplier_return"):
        from ..catalog.repack import guard_reversal          # U3-1: no old-size units back after a pack fix
        guard_reversal(db, ctx.org_id, item_id, movement_type, ref_type, ref_id, batch_id)
    current_batch = batch_qty(db, ctx.org_id, branch_id, item_id, batch_id)
    if qty_delta < 0 and current_batch + qty_delta < 0 and not allow_negative:
        raise Conflict("الكمية غير متاحة في المخزون (ربما بيعت)", code="insufficient_stock",
                       details={"item_id": item_id, "available": current_batch,
                                "requested": -qty_delta})
    item_qty_before = _item_qty(db, ctx.org_id, branch_id, item_id)

    mid = new_id()
    cost_str = str(unit_cost.quantize(COST_Q, ROUND_HALF_UP)) if unit_cost is not None else None
    db.execute("INSERT INTO stock_movements (id, org_id, branch_id, item_id, batch_id, qty_delta,"
               " unit_cost, movement_type, ref_type, ref_id, ref_line_id, note, created_by, reason)"
               " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (mid, ctx.org_id, branch_id, item_id, batch_id, qty_delta, cost_str, movement_type,
                ref_type, ref_id, ref_line_id, note, ctx.user_id, reason))
    db.execute("INSERT INTO stock_balances (org_id, branch_id, item_id, batch_key, qty)"
               " VALUES (?,?,?,?,?) ON CONFLICT(org_id, branch_id, item_id, batch_key)"
               " DO UPDATE SET qty = qty + excluded.qty",
               (ctx.org_id, branch_id, item_id, batch_id or "", qty_delta))

    if unit_cost is not None and movement_type in ("purchase", "opening", "purchase_reversal"):
        _update_avg_cost(db, ctx.org_id, branch_id, item_id, item_qty_before, qty_delta, unit_cost)
    return mid


def rescale_avg_cost(db: Database, org_id: str, item_id: str, old_pack: int, new_pack: int) -> None:
    """U3-1 pack-size correction: the average cost per base unit × old / new in every branch, so a box keeps its
    cost. Inside the caller's transaction."""
    for r in db.all("SELECT branch_id FROM item_costs WHERE org_id=? AND item_id=?", (org_id, item_id)):
        _update_avg_cost(db, org_id, r["branch_id"], item_id, 0, 0, Decimal(0), scale=(old_pack, new_pack))


def _update_avg_cost(db: Database, org_id: str, branch_id: str, item_id: str,
                     qty_before: int, delta: int, unit_cost: Decimal, scale: tuple[int, int] | None = None) -> None:
    r = db.one("SELECT avg_cost FROM item_costs WHERE org_id=? AND branch_id=? AND item_id=?",
               (org_id, branch_id, item_id))
    old = Decimal(r["avg_cost"]) if r else None
    qty_after = qty_before + delta
    if scale is not None:
        if old is None:
            return
        new = old * scale[0] / scale[1]
    elif delta > 0:
        if old is None or qty_before <= 0:
            new = unit_cost
        else:
            new = (old * qty_before + unit_cost * delta) / qty_after
    else:  # reversal of a purchase: take the reversed layer out of the average
        if old is None:
            return
        if qty_after <= 0:
            new = old
        else:
            new = (old * qty_before - unit_cost * (-delta)) / qty_after
            if new <= 0:
                new = old
    db.execute("INSERT INTO item_costs (org_id, branch_id, item_id, avg_cost, updated_at)"
               " VALUES (?,?,?,?,?) ON CONFLICT(org_id, branch_id, item_id)"
               " DO UPDATE SET avg_cost=excluded.avg_cost, updated_at=excluded.updated_at",
               (org_id, branch_id, item_id, str(new.quantize(COST_Q, ROUND_HALF_UP)), _now()))


def stock_for_item(db: Database, ctx: Context, item_id: str, branch_id: str | None = None) -> dict:
    ctx.require("inventory.read")
    branch_id = branch_id or ctx.branch_id
    rows = db.all(
        "SELECT sb.batch_key, sb.qty, b.batch_no, b.expiry_date, b.expiry_precision"
        " FROM stock_balances sb LEFT JOIN batches b ON b.id = nullif(sb.batch_key,'')"
        " WHERE sb.org_id=? AND sb.branch_id=? AND sb.item_id=? AND sb.qty<>0"
        " ORDER BY b.expiry_date IS NULL, b.expiry_date",
        (ctx.org_id, branch_id, item_id))
    out = {"item_id": item_id, "total_qty": sum(r["qty"] for r in rows),
           "batches": [dict(r) for r in rows]}
    if ctx.can("costs.read"):
        c = db.one("SELECT avg_cost FROM item_costs WHERE org_id=? AND branch_id=? AND item_id=?",
                   (ctx.org_id, branch_id, item_id))
        out["avg_cost"] = c["avg_cost"] if c else None
    return out


def verify_balances(db: Database, org_id: str) -> list[dict]:
    """Returns every (branch,item,batch) whose cached balance differs from the
    ledger. Empty list == consistent."""
    # One pass over both sides grouped together: the old FULL OUTER JOIN of the grouped ledger with the balances
    # had no index on either side and compared every pair, ~100 s after a year of sales (#113, PERFORMANCE.md).
    # ledger_qty / balance_qty stay NULL when that side has no row, as before.
    return [dict(r) for r in db.all(
        "SELECT branch_id, item_id, bk batch_key,"
        "  CASE WHEN sum(nl) > 0 THEN sum(lq) END ledger_qty, CASE WHEN sum(nb) > 0 THEN sum(bq) END balance_qty"
        " FROM (SELECT branch_id, item_id, coalesce(batch_id,'') bk, qty_delta lq, 1 nl, 0 bq, 0 nb"
        "       FROM stock_movements WHERE org_id=?"
        "       UNION ALL SELECT branch_id, item_id, batch_key, 0, 0, qty, 1 FROM stock_balances WHERE org_id=?)"
        " GROUP BY branch_id, item_id, bk HAVING sum(lq) <> sum(bq)", (org_id, org_id))]
