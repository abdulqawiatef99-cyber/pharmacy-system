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
from ..purchasing.validation import effective_amount

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
        out.pop("tax_total", None)
        for x in out["lines"]:
            x.pop("unit_value", None)
            x.pop("tax_value", None)
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


# -------------------------------------------------------------------------- part 3 item 6b: return from the invoice
def _invoice(db: Database, ctx: Context, supplier_id: str, invoice_no: str | None = None, invoice_id: str | None = None):
    q = ("SELECT * FROM purchase_invoices WHERE org_id=? AND supplier_id=? AND status='approved' AND "
         + ("id=?" if invoice_id else "invoice_no=?") + " ORDER BY approved_at DESC LIMIT 1")
    r = db.one(q, (ctx.org_id, supplier_id, invoice_id or str(invoice_no or "").strip()))
    if not r:
        raise NotFound("مفيش فاتورة شراء معتمدة بالرقم ده للمورد ده")
    return r


def _invoice_lines(db: Database, ctx: Context, inv_id: str) -> list[dict]:
    """Each line of an approved invoice with what is still returnable of it. Two rules from the Codex reviews of
    part 3 (#396, #397): the line's amount is what approval charged for it, the invoice-level discount and tax
    spread over the lines by amount (half up, the last line takes the rest, so the lines add up to the
    supplier's charge); and a return made earlier by item and lot that took this receipt's units counts as
    returned from this line (paid units first), so the same units are never returned twice."""
    from ..purchasing.service import lot_receipts
    head = db.one("SELECT extra_discount, extra_tax FROM purchase_invoices WHERE id=? AND org_id=?", (inv_id, ctx.org_id))
    rows = [dict(l) for l in db.all(
        "SELECT l.*, i.name_en, i.name_ar, i.pack_size FROM purchase_invoice_lines l JOIN items i ON i.id=l.item_id"
        " WHERE l.invoice_id=? AND l.org_id=? ORDER BY l.line_no", (inv_id, ctx.org_id))]
    amounts = [effective_amount(l) or 0 for l in rows]
    adj, whole = (head["extra_tax"] or 0) - (head["extra_discount"] or 0), sum(amounts)
    shares, given = [], 0
    for k, a in enumerate(amounts):
        part = adj - given if k == len(amounts) - 1 else (_share(adj, a, whole) if adj >= 0 else -_share(-adj, a, whole))
        shares.append(part)
        given += part
    out = []
    for l, amount, share in zip(rows, amounts, shares):
        mv = db.one("SELECT id, batch_id, branch_id FROM stock_movements WHERE org_id=? AND ref_type='purchase_invoice'"
                    " AND ref_id=? AND ref_line_id=? AND movement_type='purchase' ORDER BY rowid LIMIT 1",
                    (ctx.org_id, inv_id, l["id"]))
        back = {r["is_bonus"]: r["q"] for r in db.all(
            "SELECT r.is_bonus, coalesce(sum(r.qty_base),0) q FROM supplier_return_lines r JOIN supplier_returns h"
            " ON h.id=r.return_id AND h.org_id=? WHERE r.purchase_line_id=? GROUP BY r.is_bonus", (ctx.org_id, l["id"]))}
        ps = l["pack_size"] or 1
        paid_units, bonus_units = (l["qty"] or 0) * ps, (l["bonus_qty"] or 0) * ps
        paid_back, bonus_back, left = back.get(0, 0), back.get(1, 0), paid_units + bonus_units
        if mv:
            me = next((p for p in lot_receipts(db, ctx, mv["branch_id"], l["item_id"], mv["batch_id"]) if p["id"] == mv["id"]), None)
            if me:
                by_lot = sum(t[0] for t in me["took"] if not t[2])        # earlier returns by item and lot
                take = min(by_lot, paid_units - paid_back)
                paid_back, bonus_back = paid_back + take, bonus_back + (by_lot - take)
                left = me["left"]
        out.append({"id": l["id"], "line_no": l["line_no"], "item_id": l["item_id"], "name": l["name_en"] or l["name_ar"],
                    "pack_size": ps, "batch_id": mv["batch_id"] if mv else None, "batch_no": l["batch_no"],
                    "expiry_date": l["expiry_date"], "paid_units": paid_units, "bonus_units": bonus_units,
                    "paid_returned": paid_back, "bonus_returned": min(bonus_back, bonus_units), "receipt_left": left,
                    "qty_packs": l["qty"] or 0, "unit_net_price": l["unit_net_price"], "tax_amount": l["tax_amount"] or 0,
                    "amount": amount + share, "invoice_adjustment": share,
                    "line_total": l["line_total"], "on_hand": inv.batch_qty(db, ctx.org_id, mv["branch_id"], l["item_id"],
                                                                            mv["batch_id"]) if mv else 0})
    return out


def from_invoice(db: Database, ctx: Context, supplier_id: str, invoice_no: str) -> dict:
    """The approved purchase invoice of this supplier with this number, with each line's paid and bonus units,
    what was already returned of each, the net price and the tax: the start of a return from the invoice."""
    ctx.require("purchases.approve")
    inv_row = _invoice(db, ctx, supplier_id, invoice_no=invoice_no)
    lines = _invoice_lines(db, ctx, inv_row["id"])
    show = ctx.can("suppliers.pay") or ctx.can("costs.read")
    if not show:
        for l in lines:
            for k in ("unit_net_price", "tax_amount", "line_total", "amount", "invoice_adjustment"):
                l.pop(k, None)
    return {"invoice_id": inv_row["id"], "invoice_no": inv_row["invoice_no"], "invoice_date": inv_row["invoice_date"],
            "lines": lines}


def _share(amount: int, part: int, whole: int) -> int:
    """part/whole of a whole-piaster amount, half up, integers only."""
    return (amount * part * 2 + whole) // (2 * whole) if whole else 0


def line_value(l: dict, paid_units: int) -> tuple[int, int]:
    """(net, tax) credited for `paid_units` of an invoice line: the net invoice price of those units (owner
    #310: the box at the invoice's net price) and their share of the line's tax. Returning everything still
    left of a line credits exactly what is left of it, never a piaster more or less."""
    # what approval charged the supplier for this line (validation.effective_amount, tax included, plus its share
    # of the invoice discount/tax), never a total rebuilt from a rounded unit price (Codex P1 on #396). The tax is
    # never more than that amount: an invoice discount can leave less than the line's tax (Codex P1, round 5)
    charged = max(0, l["amount"] or 0)
    tax = min(l["tax_amount"] or 0, charged)
    total_net = charged - tax
    done = l["paid_returned"]
    if done + paid_units >= l["paid_units"]:      # the rest of the line: what is left, exactly
        return (total_net - _share(total_net, done, l["paid_units"]), tax - _share(tax, done, l["paid_units"]))
    return (_share(total_net, done + paid_units, l["paid_units"]) - _share(total_net, done, l["paid_units"]),
            _share(tax, done + paid_units, l["paid_units"]) - _share(tax, done, l["paid_units"]))


def create_from_invoice(db: Database, ctx: Context, supplier_id: str, invoice_id: str, lines: list[dict], *,
                        credit_note_no: str | None = None, note: str | None = None,
                        partial_bonus_reason: str | None = None) -> dict:
    """A return to the supplier from one approved purchase invoice. lines: [{purchase_line_id, paid_units,
    bonus_units, reason}]. Paid units are credited at the line's net price plus their share of its tax; bonus
    units go back on their own line at 0. Default settlement: on the supplier's account. A line that came with
    bonus units is returned whole (paid + bonus, owner #310) unless the person may return part of it
    (`purchases.return_bonus_part`) and writes why."""
    ctx.require("purchases.approve")
    sup = db.one("SELECT id, name FROM suppliers WHERE id=? AND org_id=?", (supplier_id, ctx.org_id))
    if not sup:
        raise NotFound("المورد غير موجود")
    if not isinstance(lines, list) or not lines or len(lines) > MAX_LINES:
        raise ValidationFailed("أضف الأصناف المرتجعة")
    cn = (str(credit_note_no).strip()[:40] or None) if credit_note_no else None
    why = (partial_bonus_reason or "").strip()[:200]
    with db.transaction():
        inv_row = _invoice(db, ctx, supplier_id, invoice_id=invoice_id)
        if inv_row["branch_id"] != ctx.branch_id:      # its units are that branch's stock (independent review)
            raise Conflict("الفاتورة دي اتسلمت في فرع تاني؛ المرتجع منها بيتعمل من نفس الفرع", code="other_branch")
        by_id = {l["id"]: l for l in _invoice_lines(db, ctx, inv_row["id"])}
        plan, partial = [], []
        ids = [str(ln.get("purchase_line_id") or "") for ln in lines if isinstance(ln, dict)]
        if len(ids) != len(lines) or len(set(ids)) != len(ids):      # one entry per line (Codex P1 on #396)
            raise ValidationFailed("كل سطر من الفاتورة مرة واحدة بس", code="duplicate_line")
        for ln in lines:
            l = by_id.get(str(ln.get("purchase_line_id") or ""))
            if not l:
                raise NotFound("السطر مش من الفاتورة دي")
            pu, bu, reason = ln.get("paid_units", 0), ln.get("bonus_units", 0), ln.get("reason")
            for v in (pu, bu):
                if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                    raise ValidationFailed("الكمية غير صحيحة")
            if not pu and not bu:
                continue
            if reason not in REASONS:
                raise ValidationFailed("اختر سبب المرتجع لكل صنف")
            if (pu > l["paid_units"] - l["paid_returned"] or bu > l["bonus_units"] - l["bonus_returned"]
                    or pu + bu > l["receipt_left"]):
                raise Conflict(f"«{l['name']}»: الكمية أكبر من اللي فاضل من السطر ده", code="return_exceeds")
            if l["bonus_units"] and (pu != l["paid_units"] - l["paid_returned"] or bu != l["bonus_units"] - l["bonus_returned"]):
                partial.append(l["name"])
            if inv.batch_qty(db, ctx.org_id, ctx.branch_id, l["item_id"], l["batch_id"]) < pu + bu:
                raise Conflict(f"«{l['name']}»: الرصيد الموجود من التشغيلة دي أقل من الكمية", code="insufficient_stock")
            plan.append((l, pu, bu, reason))
        if not plan:
            raise ValidationFailed("اكتب كمية المرتجع", code="empty_return")
        if partial:
            ctx.require("purchases.return_bonus_part")
            if len(why) < 3:
                raise ValidationFailed("السطر ده عليه بونص: بيترجع كله (المدفوع والبونص) — أو اكتب سبب المرتجع الجزئي",
                                       code="bonus_line_whole", details={"items": partial})
        db.execute("INSERT INTO branch_counters (org_id, branch_id, kind, next_no) VALUES (?,?,'supplier_return',1)"
                   " ON CONFLICT(org_id, branch_id, kind) DO NOTHING", (ctx.org_id, ctx.branch_id))
        no = db.one("SELECT next_no FROM branch_counters WHERE org_id=? AND branch_id=? AND kind='supplier_return'",
                    (ctx.org_id, ctx.branch_id))["next_no"]
        db.execute("UPDATE branch_counters SET next_no=next_no+1 WHERE org_id=? AND branch_id=? AND kind='supplier_return'",
                   (ctx.org_id, ctx.branch_id))
        rid = new_id()
        rows, total, tax_total = [], 0, 0
        for l, pu, bu, reason in plan:
            if pu:
                net, tax = line_value(l, pu)
                for q, v in _pieces(pu, net):
                    rows.append((l, q, v, 0, reason, 0))
                if tax:
                    rows[-1] = (*rows[-1][:5], tax)          # the line's tax on its last paid row
                total += net + tax
                tax_total += tax
            if bu:
                rows.append((l, bu, 0, 1, reason, 0))
        db.execute("INSERT INTO supplier_returns (id, org_id, branch_id, supplier_id, return_no, total, note, created_by,"
                   " purchase_invoice_id, credit_note_no, tax_total) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (rid, ctx.org_id, ctx.branch_id, supplier_id, no, total,
                    ((note or "").strip() + (f" — مرتجع جزئي من سطر عليه بونص: {why}" if partial else "")).strip(" —")[:300] or None,
                    ctx.user_id, inv_row["id"], cn, tax_total))
        for l, q, v, bonus, reason, tax in rows:
            lid = new_id()
            db.execute("INSERT INTO supplier_return_lines (id, return_id, item_id, batch_id, qty_base, unit_value, reason,"
                       " purchase_line_id, is_bonus, tax_value) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (lid, rid, l["item_id"], l["batch_id"], q, v, reason, l["id"], bonus, tax))
            inv.post_movement(db, ctx, item_id=l["item_id"], qty_delta=-q, batch_id=l["batch_id"],
                              movement_type="supplier_return", unit_cost=avg_cost(db, ctx.org_id, ctx.branch_id, l["item_id"]),
                              ref_type="supplier_return", ref_id=rid, ref_line_id=lid,
                              note=f"مرتجع للمورد {sup['name']} من فاتورة {inv_row['invoice_no'] or ''}".strip(), reason=reason)
        if total:
            db.execute("INSERT INTO supplier_ledger (id, org_id, supplier_id, amount, kind, ref_id, note, created_by)"
                       " VALUES (?,?,?,?,'return',?,?,?)", (new_id(), ctx.org_id, supplier_id, -total, rid,
                                                          f"مرتجع رقم {no}" + (f" — إشعار {cn}" if cn else ""), ctx.user_id))
        audit.record(db, ctx, "supplier.return", "supplier_return", rid,
                     after={"supplier": supplier_id, "no": no, "total": total, "tax": tax_total, "lines": len(rows),
                            "invoice": inv_row["id"], "credit_note": cn, "partial_bonus": why if partial else None})
        notify.emit(db, ctx, "supplier_return", "warning" if partial else "info",
                    f"مرتجع رقم {no} إلى {sup['name']} — {total / 100:,.2f} ج",
                    f"من فاتورة {inv_row['invoice_no'] or ''}" + (f" · مرتجع جزئي من سطر عليه بونص: {why}" if partial else ""),
                    "supplier_return", rid)
    if not ctx.can("purchases.read"):
        return {"ok": True, "id": rid, "return_no": no}
    return get(db, ctx, rid)
