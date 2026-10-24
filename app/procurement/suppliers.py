"""Suppliers: company or warehouse (they differ for taxes), account balance, payments,
and the shortages list."""
from __future__ import annotations

from datetime import date, timedelta

from ..core import audit, notify
from ..core.errors import NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id
from ..sales import cash

TYPES = ("company", "warehouse", "distributor", "other")


def balance(db: Database, org_id: str, supplier_id: str) -> int:
    """What the pharmacy owes the supplier (piasters)."""
    return db.one("SELECT coalesce(sum(amount),0) b FROM supplier_ledger WHERE org_id=? AND supplier_id=?",
                  (org_id, supplier_id))["b"]


def listing(db: Database, ctx: Context) -> list[dict]:
    ctx.require("suppliers.read")
    rows = db.all("SELECT s.*, coalesce((SELECT sum(amount) FROM supplier_ledger l WHERE l.supplier_id=s.id),0) balance,"
                  " (SELECT max(invoice_date) FROM purchase_invoices p WHERE p.supplier_id=s.id AND p.status='approved') last_invoice"
                  " FROM suppliers s WHERE s.org_id=? ORDER BY s.is_active DESC, s.name", (ctx.org_id,))
    out = [dict(r) for r in rows]
    if not ctx.can("suppliers.pay") and not ctx.can("costs.read"):
        for r in out:
            r.pop("balance", None)
    return out


def update(db: Database, ctx: Context, sid: str, data: dict) -> dict:
    ctx.require("suppliers.write")
    row = db.one("SELECT * FROM suppliers WHERE id=? AND org_id=?", (sid, ctx.org_id))
    if not row:
        raise NotFound("المورد غير موجود")
    changes = {}
    if "supplier_type" in data:
        if data["supplier_type"] not in TYPES:
            raise ValidationFailed("النوع غير صحيح")
        changes["supplier_type"] = data["supplier_type"]
    for k in ("phone", "payment_terms"):
        if k in data:
            changes[k] = (str(data[k] or "").strip() or None)
    if "name" in data and str(data["name"]).strip():
        from ..catalog.normalize import normalize_text
        changes["name"] = str(data["name"]).strip()
        changes["name_norm"] = normalize_text(changes["name"])
    if "is_active" in data:
        changes["is_active"] = int(bool(data["is_active"]))
    if changes:
        with db.transaction():
            db.execute(f"UPDATE suppliers SET {', '.join(f'{k}=?' for k in changes)} WHERE id=?", (*changes.values(), sid))
            audit.record(db, ctx, "supplier.update", "supplier", sid, before={k: row[k] for k in changes}, after=changes)
    return account(db, ctx, sid)


def account(db: Database, ctx: Context, sid: str) -> dict:
    ctx.require("suppliers.read")
    s = db.one("SELECT * FROM suppliers WHERE id=? AND org_id=?", (sid, ctx.org_id))
    if not s:
        raise NotFound("المورد غير موجود")
    out = dict(s)
    out["invoices"] = [dict(r) for r in db.all(
        "SELECT id, invoice_no, invoice_date, status, computed_total, declared_total FROM purchase_invoices"
        " WHERE org_id=? AND supplier_id=? ORDER BY coalesce(invoice_date, created_at) DESC LIMIT 100", (ctx.org_id, sid))]
    if ctx.can("suppliers.pay") or ctx.can("costs.read"):
        out["balance"] = balance(db, ctx.org_id, sid)
        out["ledger"] = [dict(r) for r in db.all(
            "SELECT l.amount, l.kind, l.note, l.created_at, l.ref_id, u.full_name by_name FROM supplier_ledger l"
            " LEFT JOIN users u ON u.id=l.created_by WHERE l.org_id=? AND l.supplier_id=? ORDER BY l.created_at DESC LIMIT 200",
            (ctx.org_id, sid))]
    return out


def pay(db: Database, ctx: Context, sid: str, amount: int, method: str, note: str | None = None) -> dict:
    """A payment to the supplier. 'drawer' takes it out of today's cash drawer (needs an open shift);
    'outside' = paid from outside the drawer (bank, owner's pocket)."""
    ctx.require("suppliers.pay")
    if not isinstance(amount, int) or amount <= 0:
        raise ValidationFailed("المبلغ غير صحيح")
    if method not in ("drawer", "outside"):
        raise ValidationFailed("اختار الدفع منين")
    s = db.one("SELECT name FROM suppliers WHERE id=? AND org_id=?", (sid, ctx.org_id))
    if not s:
        raise NotFound("المورد غير موجود")
    with db.transaction():
        lid = new_id()
        db.execute("INSERT INTO supplier_ledger (id, org_id, supplier_id, amount, kind, ref_id, note, created_by)"
                   " VALUES (?,?,?,?,'payment',?,?,?)", (lid, ctx.org_id, sid, -amount, None,
                                                        (note or "").strip()[:200] or ("من الدرج" if method == "drawer" else "من برّه الدرج"),
                                                        ctx.user_id))
        if method == "drawer":
            cash.record(db, ctx, -amount, "payout", lid, f"دفع للمورد {s['name']}")
        audit.record(db, ctx, "supplier.payment", "supplier", sid, after={"amount": amount, "method": method})
        notify.emit(db, ctx, "supplier_payment", "info", f"دفع {amount / 100:,.2f} ج لـ {s['name']}",
                    "من الدرج" if method == "drawer" else "من برّه الدرج", "supplier", sid)
    return account(db, ctx, sid)


# -------------------------------------------------------------------------- shortages
def shortages(db: Database, ctx: Context, today: date | None = None) -> list[dict]:
    """Items to order: marked short by someone, at/below their reorder level, or sold out while
    they sold in the last 60 days."""
    ctx.require("inventory.read")
    since = ((today or date.today()) - timedelta(days=60)).isoformat()
    rows = db.all(
        "WITH st AS (SELECT item_id, sum(qty) q FROM stock_balances WHERE org_id=? AND branch_id=? GROUP BY item_id),"
        " sold AS (SELECT l.item_id, sum(l.qty_base) q FROM sale_lines l JOIN sales s ON s.id=l.sale_id"
        "  WHERE s.org_id=? AND s.status='completed' AND s.completed_at>=? GROUP BY l.item_id)"
        " SELECT i.id, i.name_en, i.name_ar, i.pack_size, i.base_unit_name, i.pack_unit_name, i.min_stock, i.max_stock,"
        "  i.shortage_at, i.location, coalesce(st.q,0) stock, coalesce(sold.q,0) sold_60d,"
        "  (SELECT sp.name FROM purchase_invoice_lines pl JOIN purchase_invoices p ON p.id=pl.invoice_id JOIN suppliers sp"
        "   ON sp.id=p.supplier_id WHERE pl.item_id=i.id AND p.status='approved' ORDER BY p.approved_at DESC LIMIT 1) last_supplier"
        " FROM items i LEFT JOIN st ON st.item_id=i.id LEFT JOIN sold ON sold.item_id=i.id"
        " WHERE i.org_id=? AND i.status='active' AND (i.shortage_at IS NOT NULL OR (i.min_stock>0 AND coalesce(st.q,0)<=i.min_stock)"
        "  OR (coalesce(st.q,0)<=0 AND coalesce(sold.q,0)>0))"
        " ORDER BY i.shortage_at IS NULL, coalesce(sold.q,0) DESC LIMIT 500",
        (ctx.org_id, ctx.branch_id, ctx.org_id, since, ctx.org_id))
    out = []
    for r in rows:
        d = dict(r)
        d["reasons"] = [x for x, ok in (("marked", d["shortage_at"]), ("below_min", d["min_stock"] and d["stock"] <= d["min_stock"]),
                                        ("sold_out", d["stock"] <= 0 and d["sold_60d"] > 0)) if ok]
        target = d["max_stock"] or (d["min_stock"] * 2 if d["min_stock"] else d["sold_60d"] // 2)
        d["suggest_base"] = max(0, (target or 0) - max(0, d["stock"]))
        out.append(d)
    return out
