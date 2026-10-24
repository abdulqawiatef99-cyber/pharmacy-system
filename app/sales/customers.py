"""Customers: profile, credit account, monthly (chronic) orders the pharmacist confirms."""
from __future__ import annotations

from datetime import date, timedelta

from ..core import audit
from ..core.errors import Conflict, NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id


def _bal_sql() -> str:
    return "coalesce((SELECT sum(amount) FROM customer_ledger l WHERE l.customer_id=c.id),0)"


def listing(db: Database, ctx: Context, q: str = "", only: str | None = None, today: date | None = None) -> list[dict]:
    ctx.require("sales.read")
    today = today or date.today()
    sql = (f"SELECT c.*, {_bal_sql()} balance,"
           " (SELECT min(date(coalesce(r.last_at, r.created_at), '+' || r.every_days || ' days')) FROM customer_refills r"
           "  WHERE r.customer_id=c.id) next_refill"
           " FROM customers c WHERE c.org_id=? AND c.is_active=1")
    params: list = [ctx.org_id]
    if q.strip():
        sql += " AND (c.name LIKE ? OR coalesce(c.phone,'') LIKE ?)"
        params += [f"%{q.strip()}%", f"%{q.strip()}%"]
    sql += " ORDER BY c.name LIMIT 300"
    rows = [dict(r) for r in db.all(sql, tuple(params))]
    soon = (today + timedelta(days=5)).isoformat()
    if only == "debt":
        rows = [r for r in rows if r["balance"] > 0]
    elif only == "refill":
        rows = [r for r in rows if r["next_refill"] and r["next_refill"] <= soon]
    return rows


def update(db: Database, ctx: Context, cid: str, data: dict) -> dict:
    ctx.require("customers.manage")
    c = db.one("SELECT * FROM customers WHERE id=? AND org_id=?", (cid, ctx.org_id))
    if not c:
        raise NotFound("العميل غير موجود")
    ch = {}
    for k in ("name", "phone", "notes"):
        if k in data:
            ch[k] = str(data[k] or "").strip() or None
    if "name" in ch and not ch["name"]:
        raise ValidationFailed("اسم العميل مطلوب")
    if "credit_limit" in data:
        v = data["credit_limit"]
        if not isinstance(v, int) or v < 0:
            raise ValidationFailed("حد الآجل غير صحيح")
        ch["credit_limit"] = v
    if "whatsapp_ok" in data:
        ch["whatsapp_ok"] = int(bool(data["whatsapp_ok"]))
    if ch:
        with db.transaction():
            try:
                db.execute(f"UPDATE customers SET {', '.join(f'{k}=?' for k in ch)} WHERE id=?", (*ch.values(), cid))
            except Exception as e:
                if "UNIQUE" in str(e):
                    raise Conflict("رقم الموبايل مسجل لعميل تاني") from e
                raise
            audit.record(db, ctx, "customer.update", "customer", cid, before={k: c[k] for k in ch}, after=ch)
    return profile(db, ctx, cid)


def profile(db: Database, ctx: Context, cid: str, today: date | None = None) -> dict:
    ctx.require("sales.read")
    c = db.one(f"SELECT c.*, {_bal_sql()} balance FROM customers c WHERE c.id=? AND c.org_id=?", (cid, ctx.org_id))
    if not c:
        raise NotFound("العميل غير موجود")
    out = dict(c)
    out["ledger"] = [dict(r) for r in db.all(
        "SELECT l.amount, l.kind, l.note, l.created_at, l.ref_id, u.full_name by_name FROM customer_ledger l LEFT JOIN users u"
        " ON u.id=l.created_by WHERE l.org_id=? AND l.customer_id=? ORDER BY l.created_at DESC LIMIT 100", (ctx.org_id, cid))]
    mine = "" if ctx.can("sales.totals") else " AND s.created_by=?"
    out["sales"] = [dict(r) for r in db.all(
        "SELECT s.id, s.sale_no, s.total, s.completed_at, s.status FROM sales s WHERE s.org_id=? AND s.customer_id=?"
        f" AND s.status<>'draft'{mine} ORDER BY s.completed_at DESC LIMIT 30",
        (ctx.org_id, cid) + (() if not mine else (ctx.user_id,)))]
    today = today or date.today()
    refills = []
    for r in db.all("SELECT r.*, i.name_en, i.name_ar, i.pack_size, i.base_unit_name,"
                    " coalesce((SELECT sum(qty) FROM stock_balances b WHERE b.item_id=i.id AND b.org_id=r.org_id),0) stock"
                    " FROM customer_refills r JOIN items i ON i.id=r.item_id WHERE r.customer_id=? ORDER BY i.name_en", (cid,)):
        d = dict(r)
        from .service import _local_date
        base = _local_date(d["last_at"] or d["created_at"])
        due = base + timedelta(days=d["every_days"])
        d["due"], d["days_left"], d["in_stock"] = due.isoformat(), (due - today).days, d["stock"] >= d["qty_base"]
        refills.append(d)
    out["refills"] = refills
    out["suggest_refills"] = [dict(r) for r in db.all(
        # bought the same item in 3+ different months: probably monthly medicine
        "SELECT l.item_id, i.name_en, i.name_ar, count(DISTINCT substr(s.completed_at,1,7)) months, max(l.qty_base) qty_base"
        " FROM sale_lines l JOIN sales s ON s.id=l.sale_id JOIN items i ON i.id=l.item_id WHERE s.org_id=? AND s.customer_id=?"
        " AND s.status='completed' AND l.item_id NOT IN (SELECT item_id FROM customer_refills WHERE customer_id=?)"
        " GROUP BY l.item_id HAVING months >= 3 ORDER BY months DESC LIMIT 10", (ctx.org_id, cid, cid))]
    return out


def save_refill(db: Database, ctx: Context, cid: str, item_id: str, qty_base: int, every_days: int = 30) -> dict:
    ctx.require("customers.manage")
    if not isinstance(qty_base, int) or qty_base <= 0 or not isinstance(every_days, int) or not 7 <= every_days <= 180:
        raise ValidationFailed("الكمية أو المدة غير صحيحة")
    if not db.one("SELECT 1 FROM customers WHERE id=? AND org_id=?", (cid, ctx.org_id)) or \
            not db.one("SELECT 1 FROM items WHERE id=? AND org_id=?", (item_id, ctx.org_id)):
        raise NotFound("العميل أو الصنف غير موجود")
    with db.transaction():
        db.execute("INSERT INTO customer_refills (id, org_id, customer_id, item_id, qty_base, every_days) VALUES (?,?,?,?,?,?)"
                   " ON CONFLICT(customer_id, item_id) DO UPDATE SET qty_base=excluded.qty_base, every_days=excluded.every_days",
                   (new_id(), ctx.org_id, cid, item_id, qty_base, every_days))
        audit.record(db, ctx, "customer.refill_save", "customer", cid, after={"item": item_id, "qty": qty_base, "days": every_days})
    return profile(db, ctx, cid)


def remove_refill(db: Database, ctx: Context, cid: str, item_id: str) -> dict:
    ctx.require("customers.manage")
    with db.transaction():
        db.execute("DELETE FROM customer_refills WHERE customer_id=? AND item_id=? AND org_id=?", (cid, item_id, ctx.org_id))
        audit.record(db, ctx, "customer.refill_remove", "customer", cid, after={"item": item_id})
    return profile(db, ctx, cid)


def prepare_refill(db: Database, ctx: Context, cid: str) -> str:
    """Open a cashier invoice for this customer with his monthly items (only what is in stock).
    The pharmacist reviews it and completes it like any sale."""
    from .service import Sales
    s = Sales(db)
    sid = s.create(ctx, cid)
    added = 0
    for r in db.all("SELECT item_id, qty_base FROM customer_refills WHERE customer_id=? AND org_id=?", (cid, ctx.org_id)):
        try:
            s.add_line(ctx, sid, r["item_id"], units=r["qty_base"])
            added += 1
        except Conflict:
            continue                     # not enough stock: left out, the screen shows it as missing
    if not added:
        s.discard(ctx, sid)
        raise Conflict("مفيش ولا صنف من الطلب الشهري متوفر", code="nothing_in_stock")
    return sid


def mark_refilled(db: Database, sale_id: str) -> None:
    """Called when a sale completes: items of the customer's monthly list restart their count."""
    s = db.one("SELECT customer_id, completed_at FROM sales WHERE id=?", (sale_id,))
    if not s or not s["customer_id"]:
        return
    db.execute("UPDATE customer_refills SET last_at=? WHERE customer_id=? AND item_id IN"
               " (SELECT item_id FROM sale_lines WHERE sale_id=?)", (s["completed_at"], s["customer_id"], sale_id))
