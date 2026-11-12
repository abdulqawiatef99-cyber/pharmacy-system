"""Suppliers: company or warehouse (they differ for taxes), account balance, payments,
and the shortages list."""
from __future__ import annotations

from datetime import date, timedelta

from ..core import audit, notify
from ..core.errors import Conflict, NotFound, ValidationFailed
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
            try:
                db.execute(f"UPDATE suppliers SET {', '.join(f'{k}=?' for k in changes)} WHERE id=?", (*changes.values(), sid))
            except Exception as e:
                if "UNIQUE" in str(e):                   # renamed onto another supplier's name
                    raise Conflict("يوجد مورد بالاسم نفسه") from e
                raise
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


PAY_METHODS = {"drawer": "نقدًا من الخزينة", "cash": "نقدًا من خارج الخزينة", "bank": "تحويل بنكي",
               "cheque": "شيك", "wallet": "إنستاباي / محفظة"}


def pay(db: Database, ctx: Context, sid: str, amount: int, method: str, note: str | None = None,
        ref: str | None = None) -> dict:
    """A payment to the supplier. 'drawer' takes it out of today's cash drawer (needs an open shift);
    the others are paid from outside the drawer (bank, cheque number in ref, owner's pocket)."""
    ctx.require("suppliers.pay")
    if method == "outside":                      # older screens
        method = "cash"
    if not isinstance(amount, int) or amount <= 0:
        raise ValidationFailed("المبلغ غير صحيح")
    if method not in PAY_METHODS:
        raise ValidationFailed("اختر طريقة الدفع")
    ref = (str(ref or "").strip()[:60]) or None
    if method == "cheque" and not ref:
        raise ValidationFailed("اكتب رقم الشيك")
    s = db.one("SELECT name FROM suppliers WHERE id=? AND org_id=?", (sid, ctx.org_id))
    if not s:
        raise NotFound("المورد غير موجود")
    with db.transaction():
        lid = new_id()
        db.execute("INSERT INTO supplier_ledger (id, org_id, supplier_id, amount, kind, ref_id, note, created_by, pay_method, pay_ref)"
                   " VALUES (?,?,?,?,'payment',?,?,?,?,?)", (lid, ctx.org_id, sid, -amount, None,
                                                            (note or "").strip()[:200] or PAY_METHODS[method],
                                                            ctx.user_id, method, ref))
        if method == "drawer":
            cash.record(db, ctx, -amount, "payout", lid, f"دفع للمورد {s['name']}")
        audit.record(db, ctx, "supplier.payment", "supplier", sid, after={"amount": amount, "method": method, "ref": ref})
        notify.emit(db, ctx, "supplier_payment", "info", f"دفع {amount / 100:,.2f} ج إلى {s['name']}",
                    PAY_METHODS[method] + (f" {ref}" if ref else ""), "supplier", sid)
    return account(db, ctx, sid)


def _entries(db: Database, org_id: str, sid: str | None = None) -> list[dict]:
    """Ledger rows with the date that counts for the account: a purchase on its invoice date,
    everything else on the local day it was recorded."""
    from ..sales.service import _local_date
    rows = db.all(
        "SELECT l.*, p.invoice_no, p.invoice_date, u.full_name by_name, s.name supplier, s.supplier_type FROM supplier_ledger l"
        " JOIN suppliers s ON s.id=l.supplier_id LEFT JOIN purchase_invoices p ON p.id=l.ref_id AND l.kind IN ('purchase','adjustment')"
        " LEFT JOIN users u ON u.id=l.created_by WHERE l.org_id=?" + (" AND l.supplier_id=?" if sid else ""),
        (org_id, sid) if sid else (org_id,))
    out = []
    for r in rows:
        d = dict(r)
        d["day"] = (d["invoice_date"] if d["kind"] == "purchase" and d["invoice_date"] else _local_date(d["created_at"]).isoformat())
        out.append(d)
    out.sort(key=lambda d: (d["day"], d["created_at"]))
    return out


def _period(date_from: str, date_to: str) -> tuple[str, str]:
    try:
        d1, d2 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except (TypeError, ValueError):
        raise ValidationFailed("اختر الفترة (من / إلى)")
    if d2 < d1:
        raise ValidationFailed("تاريخ النهاية قبل البداية")
    return d1.isoformat(), d2.isoformat()


def _need_money(ctx: Context) -> None:
    if not (ctx.allowed("suppliers.pay") or ctx.allowed("costs.read")):
        ctx.require("suppliers.pay")


def statement(db: Database, ctx: Context, sid: str, date_from: str, date_to: str) -> dict:
    """Account statement for closing the month with the supplier: balance at the start, every
    invoice / return / payment (with how it was paid) in the period, totals, balance at the end."""
    _need_money(ctx)
    lo, hi = _period(date_from, date_to)
    s = db.one("SELECT id, name, supplier_type, phone, payment_terms FROM suppliers WHERE id=? AND org_id=?", (sid, ctx.org_id))
    if not s:
        raise NotFound("المورد غير موجود")
    rows = _entries(db, ctx.org_id, sid)
    opening = sum(r["amount"] for r in rows if r["day"] < lo)
    period = [r for r in rows if lo <= r["day"] <= hi]
    run, lines = opening, []
    for r in period:
        run += r["amount"]
        lines.append({"day": r["day"], "kind": r["kind"], "amount": r["amount"], "balance": run, "invoice_no": r["invoice_no"],
                      "ref_id": r["ref_id"], "note": r["note"], "pay_method": r["pay_method"], "pay_ref": r["pay_ref"], "by": r["by_name"]})
    tot = {k: sum(r["amount"] for r in period if r["kind"] == k) for k in ("opening", "purchase", "return", "payment", "adjustment")}
    by_method: dict[str, int] = {}
    for r in period:
        if r["kind"] == "payment":
            k = r["pay_method"] or "unknown"
            by_method[k] = by_method.get(k, 0) - r["amount"]
    return {"supplier": dict(s), "from": lo, "to": hi, "opening": opening, "closing": run, "lines": lines,
            "purchases": tot["purchase"], "returns": -tot["return"], "payments": -tot["payment"],
            "adjustments": tot["adjustment"] + tot["opening"], "invoices": sum(1 for r in period if r["kind"] == "purchase"),
            "by_method": [{"method": k, "label": PAY_METHODS.get(k, "غير مسجلة"), "amount": v} for k, v in by_method.items()]}


def statements(db: Database, ctx: Context, date_from: str, date_to: str, supplier_type: str | None = None) -> dict:
    """All suppliers for one period (the monthly claims of companies and warehouses)."""
    _need_money(ctx)
    lo, hi = _period(date_from, date_to)
    if supplier_type and supplier_type not in TYPES:
        raise ValidationFailed("النوع غير صحيح")
    acc: dict[str, dict] = {}
    for r in _entries(db, ctx.org_id):
        if supplier_type and r["supplier_type"] != supplier_type:
            continue
        a = acc.setdefault(r["supplier_id"], {"supplier_id": r["supplier_id"], "name": r["supplier"], "type": r["supplier_type"],
                                              "opening": 0, "purchases": 0, "returns": 0, "payments": 0, "adjustments": 0, "invoices": 0})
        if r["day"] < lo:
            a["opening"] += r["amount"]
        elif r["day"] <= hi:
            k = {"purchase": "purchases", "return": "returns", "payment": "payments"}.get(r["kind"], "adjustments")
            a[k] += -r["amount"] if k in ("returns", "payments") else r["amount"]
            a["invoices"] += r["kind"] == "purchase"
    rows = []
    for a in acc.values():
        a["closing"] = a["opening"] + a["purchases"] - a["returns"] - a["payments"] + a["adjustments"]
        if a["opening"] or a["purchases"] or a["returns"] or a["payments"] or a["adjustments"]:
            rows.append(a)
    rows.sort(key=lambda a: -a["closing"])
    keys = ("opening", "purchases", "returns", "payments", "adjustments", "closing", "invoices")
    return {"from": lo, "to": hi, "rows": rows, "totals": {k: sum(a[k] for a in rows) for k in keys}}


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
        " WHERE i.org_id=? AND i.status='active' AND i.is_service=0 AND (i.shortage_at IS NOT NULL OR (i.min_stock>0 AND coalesce(st.q,0)<=i.min_stock)"
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
