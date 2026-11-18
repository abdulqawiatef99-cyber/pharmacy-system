"""Contracts: a company / union / club whose members get an agreed discount — one rate for
local items and another for imported ones. The owner sets them and links customers; the
cashier only picks the customer, and the discount is applied by itself (still never below
cost without the owner)."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

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
        raise ValidationFailed("اكتب اسم الجهة (شركة، نقابة، نادٍ…)")
    for k in ("discount_local_bp", "discount_imported_bp"):
        if isinstance(v[k], bool) or not isinstance(v[k], int) or not 0 <= v[k] <= MAX_BP:   # true is not 1 bp
            raise ValidationFailed("الخصم من 0 إلى 50%")
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
                raise Conflict("يوجد تعاقد بالاسم نفسه") from e
            raise
    return listing(db, ctx)


def link_customer(db: Database, ctx: Context, customer_id: str, contract_id: str | None) -> None:
    ctx.require("contracts.manage")
    c = db.one("SELECT contract_id FROM customers WHERE id=? AND org_id=?", (customer_id, ctx.org_id))
    if not c:
        raise NotFound("العميل غير موجود")
    if contract_id is not None and not isinstance(contract_id, str):
        raise ValidationFailed("اختر التعاقد")
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


TICKET_FIELDS = {"name": ("ticket_name", 80), "ref": ("ticket_ref", 40), "number": ("ticket_no", 40)}


def set_ticket(db: Database, ctx: Context, sid: str, data: dict) -> dict:
    """The ticket a contract sale is made on (hospital / company: name, id, number), all optional.
    On an open invoice by the cashier; on a completed one only by who manages contracts (a correction,
    audited). Only on a sale that carries a contract."""
    ctx.require("sales.create")
    if not isinstance(data, dict):
        raise ValidationFailed("بيانات التذكرة غير صحيحة")
    vals = {}
    for key, (col, size) in TICKET_FIELDS.items():
        v = data.get(key)
        if isinstance(v, bool) or (v is not None and not isinstance(v, (str, int))):
            raise ValidationFailed("بيانات التذكرة غير صحيحة")
        vals[col] = (str(v).strip()[:size] if v is not None else "") or None
    with db.transaction():
        s = db.one("SELECT id, status, contract_id, ticket_name, ticket_ref, ticket_no FROM sales"
                   " WHERE id=? AND org_id=?", (sid, ctx.org_id))
        if not s:
            raise NotFound("الفاتورة غير موجودة")
        if s["status"] == "voided":
            raise Conflict("الفاتورة ملغاة")
        if s["status"] == "completed":
            ctx.require("contracts.manage")
        if not s["contract_id"] and any(vals.values()):
            raise ValidationFailed("التذكرة لفواتير التعاقد بس: اختار عميل على تعاقد الأول", code="no_contract")
        before = {c: s[c] for c in vals}
        if before != vals:
            db.execute("UPDATE sales SET ticket_name=?, ticket_ref=?, ticket_no=? WHERE id=?",
                       (vals["ticket_name"], vals["ticket_ref"], vals["ticket_no"], sid))
            audit.record(db, ctx, "sale.ticket", "sale", sid, before=before, after=vals)
    return vals


def estock_history(db: Database, ctx: Context, contract_id: str, d1: date, d2: date) -> list[dict]:
    """E-Stock invoices of this contract's customers in the period, with their tickets, from the read-only
    archive next to the database (estock_archive.py). Empty when there is no archive. Never mixed into
    the program's totals: E-Stock history is shown apart."""
    from ..dataimport import estock_archive
    if db.path == ":memory:":
        return []
    path = Path(db.path).parent / estock_archive.NAME
    if not path.exists():
        return []
    customers = {r["id"]: r["name"] for r in db.all("SELECT id, name FROM customers WHERE org_id=? AND contract_id=?",
                                                    (ctx.org_id, contract_id))}
    if not customers:
        return []
    with closing(estock_archive.open_ro(path)) as con:
        con.row_factory = sqlite3.Row
        have = {r[1].lower() for r in con.execute('PRAGMA table_info("e_Sales_header")')}
        if not {"sales_id", "customer_id", "insert_date"} <= have or not con.execute(
                "SELECT 1 FROM sqlite_master WHERE name='map_customers'").fetchone():
            return []
        pick = lambda c: c if c in have else "NULL"   # noqa: E731 - a column E-Stock may not have
        ids = {r["estock_id"]: r["program_id"] for r in con.execute("SELECT estock_id, program_id FROM map_customers")
               if r["program_id"] in customers}
        if not ids:
            return []
        out = []
        q = (f'SELECT sales_id, customer_id, insert_date, {pick("bill_number")} bill_number, {pick("total_bill_net")} total,'
             f' {pick("ticket_name")} ticket_name, {pick("ticket_id")} ticket_ref, {pick("ticket_num")} ticket_no'
             f' FROM "e_Sales_header" WHERE substr(insert_date, 1, 10) BETWEEN ? AND ? AND customer_id IN'
             f' ({",".join("?" * len(ids))}) ORDER BY insert_date')
        for r in con.execute(q, (d1.isoformat(), d2.isoformat(), *ids)):
            try:
                total = int((Decimal(r["total"] or "0") * 100).quantize(Decimal(1), ROUND_HALF_UP))
            except InvalidOperation:
                total = None
            out.append({"estock_sales_id": r["sales_id"], "bill_number": r["bill_number"], "day": (r["insert_date"] or "")[:10],
                        "customer": customers[ids[r["customer_id"]]], "total": total,
                        "ticket_name": r["ticket_name"], "ticket_ref": r["ticket_ref"], "ticket_no": r["ticket_no"]})
        return out


def report(db: Database, ctx: Context, contract_id: str, date_from: str, date_to: str) -> dict:
    """Invoices of a contract's customers in a period (for settling with the company), with their tickets;
    `estock` = the same customers' invoices from E-Stock's archive, apart from the program's totals."""
    from .service import local_day_bounds
    ctx.require("sales.totals")
    k = db.one("SELECT * FROM contracts WHERE id=? AND org_id=?", (contract_id, ctx.org_id))
    if not k:
        raise NotFound("التعاقد غير موجود")
    try:
        d1, d2 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except (TypeError, ValueError):
        raise ValidationFailed("اختر الفترة")
    rows = [dict(r) for r in db.all(
        "SELECT s.id, s.sale_no, s.completed_at, s.total, s.discount_total, s.returned_total, s.on_credit, c.name customer,"
        " s.ticket_name, s.ticket_ref, s.ticket_no"
        " FROM sales s JOIN customers c ON c.id=s.customer_id WHERE s.org_id=? AND s.contract_id=? AND s.status='completed'"
        " AND s.completed_at>=? AND s.completed_at<? ORDER BY s.completed_at",
        (ctx.org_id, contract_id, local_day_bounds(d1)[0], local_day_bounds(d2)[1]))]
    keys = ("total", "discount_total", "returned_total", "on_credit")
    return {"contract": dict(k), "from": d1.isoformat(), "to": d2.isoformat(), "rows": rows,
            "totals": {x: sum(r[x] or 0 for r in rows) for x in keys},
            "estock": estock_history(db, ctx, contract_id, d1, d2)}
