"""Contracts: a company / union / club whose members get an agreed discount — one rate for
local items and another for imported ones. The owner sets them and links customers; the
cashier only picks the customer, and the discount is applied by itself (still never below
cost without the owner)."""
from __future__ import annotations

from datetime import date

from ..core import audit
from ..core.errors import Conflict, NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id

MAX_BP = 5000


def listing(db: Database, ctx: Context) -> list[dict]:
    ctx.require("sales.read")
    return [dict(r) for r in db.all(
        "SELECT c.*, (SELECT count(*) FROM customers x WHERE x.contract_id=c.id) customers FROM contracts c"
        " WHERE c.org_id=? ORDER BY c.is_active DESC, c.name", (ctx.org_id,))]


def save(db: Database, ctx: Context, data: dict, cid: str | None = None) -> list[dict]:
    ctx.require("contracts.manage")
    cur = None
    if cid:
        cur = db.one("SELECT * FROM contracts WHERE id=? AND org_id=?", (cid, ctx.org_id))
        if not cur:
            raise NotFound("التعاقد غير موجود")
    v = dict(cur) if cur else {"discount_local_bp": 0, "discount_imported_bp": 0, "note": None, "is_active": 1}
    for k in ("name", "discount_local_bp", "discount_imported_bp", "note", "is_active"):
        if k in data:
            v[k] = data[k]
    v["name"] = str(v.get("name") or "").strip()[:80]
    if len(v["name"]) < 2:
        raise ValidationFailed("اكتب اسم الجهة (شركة، نقابة، نادي…)")
    for k in ("discount_local_bp", "discount_imported_bp"):
        if not isinstance(v[k], int) or not 0 <= v[k] <= MAX_BP:
            raise ValidationFailed("الخصم من 0 لـ 50%")
    v["note"] = (str(v.get("note") or "").strip()[:200]) or None
    v["is_active"] = int(bool(v["is_active"]))
    cols = ("name", "discount_local_bp", "discount_imported_bp", "note", "is_active")
    with db.transaction():
        try:
            if cur:
                db.execute(f"UPDATE contracts SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?", (*[v[k] for k in cols], cid))
                audit.record(db, ctx, "contract.update", "contract", cid, before={k: cur[k] for k in cols}, after={k: v[k] for k in cols})
            else:
                cid = new_id()
                db.execute(f"INSERT INTO contracts (id, org_id, created_by, {', '.join(cols)}) VALUES (?,?,?{',?' * len(cols)})",
                           (cid, ctx.org_id, ctx.user_id, *[v[k] for k in cols]))
                audit.record(db, ctx, "contract.create", "contract", cid, after={k: v[k] for k in cols})
        except Exception as e:
            if "UNIQUE" in str(e):
                raise Conflict("فيه تعاقد بنفس الاسم") from e
            raise
    return listing(db, ctx)


def link_customer(db: Database, ctx: Context, customer_id: str, contract_id: str | None) -> None:
    ctx.require("contracts.manage")
    c = db.one("SELECT contract_id FROM customers WHERE id=? AND org_id=?", (customer_id, ctx.org_id))
    if not c:
        raise NotFound("العميل غير موجود")
    if contract_id and not db.one("SELECT 1 FROM contracts WHERE id=? AND org_id=?", (contract_id, ctx.org_id)):
        raise NotFound("التعاقد غير موجود")
    with db.transaction():
        db.execute("UPDATE customers SET contract_id=? WHERE id=?", (contract_id, customer_id))
        audit.record(db, ctx, "customer.contract", "customer", customer_id, before={"contract": c["contract_id"]},
                     after={"contract": contract_id})


def apply(db: Database, sid: str) -> None:
    """Sets the contract of the sale's customer on its lines (inside the caller's transaction).
    Lines with a discount someone typed by hand are left alone."""
    s = db.one("SELECT customer_id FROM sales WHERE id=?", (sid,))
    k = db.one("SELECT k.* FROM customers c JOIN contracts k ON k.id=c.contract_id AND k.is_active=1 WHERE c.id=?",
               (s["customer_id"],)) if s and s["customer_id"] else None
    db.execute("UPDATE sales SET contract_id=? WHERE id=?", (k["id"] if k else None, sid))
    for l in db.all("SELECT l.id, l.discount_bp, l.discount_source, i.is_imported FROM sale_lines l JOIN items i ON i.id=l.item_id"
                    " WHERE l.sale_id=?", (sid,)):
        if l["discount_bp"] and l["discount_source"] != "contract":
            continue
        bp = (k["discount_imported_bp"] if l["is_imported"] else k["discount_local_bp"]) if k else 0
        db.execute("UPDATE sale_lines SET discount_bp=?, discount_source=?, discount_by=NULL, discount_approved_by=NULL WHERE id=?",
                   (bp, "contract" if bp else None, l["id"]))


def report(db: Database, ctx: Context, contract_id: str, date_from: str, date_to: str) -> dict:
    """Invoices of a contract's customers in a period (for settling with the company)."""
    from .service import local_day_bounds
    ctx.require("sales.totals")
    k = db.one("SELECT * FROM contracts WHERE id=? AND org_id=?", (contract_id, ctx.org_id))
    if not k:
        raise NotFound("التعاقد غير موجود")
    try:
        d1, d2 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except (TypeError, ValueError):
        raise ValidationFailed("اختار الفترة")
    rows = [dict(r) for r in db.all(
        "SELECT s.id, s.sale_no, s.completed_at, s.total, s.discount_total, s.returned_total, s.on_credit, c.name customer"
        " FROM sales s JOIN customers c ON c.id=s.customer_id WHERE s.org_id=? AND s.contract_id=? AND s.status='completed'"
        " AND s.completed_at>=? AND s.completed_at<? ORDER BY s.completed_at",
        (ctx.org_id, contract_id, local_day_bounds(d1)[0], local_day_bounds(d2)[1]))]
    keys = ("total", "discount_total", "returned_total", "on_credit")
    return {"contract": dict(k), "from": d1.isoformat(), "to": d2.isoformat(), "rows": rows,
            "totals": {x: sum(r[x] or 0 for r in rows) for x in keys}}
