"""Item card: everything about one item on one screen, sections (أقسام), shortage mark."""
from __future__ import annotations

from datetime import date

from ..core import audit, notify
from ..core.errors import Conflict, NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id
from .normalize import valid_gtin

DEFAULT_CATEGORIES = [            # name, no_return, mark — the owner edits these freely
    ("أدوية", 0, None), ("حقن", 0, "injection"), ("ثلاجة", 1, "fridge"),
    ("عناية واستعمال شخصي", 1, None), ("مستحضرات تجميل", 0, None),
    ("مستلزمات طبية", 0, None), ("ألبان وأغذية أطفال", 0, None),
]
MARKS = (None, "fridge", "injection")


def ensure_default_categories(db: Database) -> int:
    n = 0
    for o in db.all("SELECT id FROM organizations"):
        if db.one("SELECT 1 FROM categories WHERE org_id=? LIMIT 1", (o["id"],)):
            continue
        with db.transaction():
            for i, (name, nr, mark) in enumerate(DEFAULT_CATEGORIES):
                db.execute("INSERT INTO categories (id, org_id, name, no_return, mark, sort) VALUES (?,?,?,?,?,?)",
                           (new_id(), o["id"], name, nr, mark, i))
        n += 1
    return n


# -------------------------------------------------------------------------- sections
def list_categories(db: Database, ctx: Context) -> list[dict]:
    ctx.require("items.read")
    return [dict(r) for r in db.all(
        "SELECT c.*, (SELECT count(*) FROM items i WHERE i.category_id=c.id AND i.status='active') items"
        " FROM categories c WHERE c.org_id=? ORDER BY c.is_active DESC, c.sort, c.name", (ctx.org_id,))]


def save_category(db: Database, ctx: Context, data: dict, cat_id: str | None = None) -> dict:
    ctx.require("items.write")
    name = str(data.get("name", "")).strip()
    mark = data.get("mark") or None
    if mark not in MARKS:
        raise ValidationFailed("العلامة غير صحيحة")
    before = None
    if cat_id:
        before = db.one("SELECT * FROM categories WHERE id=? AND org_id=?", (cat_id, ctx.org_id))
        if not before:
            raise NotFound("القسم غير موجود")
        before = dict(before)
    no_return = int(bool(data.get("no_return"))) if "no_return" in data else (before["no_return"] if before else 0)
    if (before["no_return"] if before else 0) != no_return:
        ctx.require("items.no_return")
    if not name and before:
        name = before["name"]
    if len(name) < 2:
        raise ValidationFailed("اكتب اسم القسم")
    active = int(bool(data.get("is_active", 1)))
    with db.transaction():
        try:
            if before:
                db.execute("UPDATE categories SET name=?, no_return=?, mark=?, is_active=? WHERE id=?",
                           (name, no_return, mark, active, cat_id))
            else:
                cat_id = new_id()
                db.execute("INSERT INTO categories (id, org_id, name, no_return, mark, sort) VALUES"
                           " (?,?,?,?,?, (SELECT coalesce(max(sort),0)+1 FROM categories WHERE org_id=?))",
                           (cat_id, ctx.org_id, name, no_return, mark, ctx.org_id))
        except Exception as e:
            if "UNIQUE" in str(e):
                raise Conflict("يوجد قسم بالاسم نفسه") from e
            raise
        after = {"name": name, "no_return": no_return, "mark": mark, "is_active": active}
        audit.record(db, ctx, "category.save", "category", cat_id, before=before, after=after)
        if (before["no_return"] if before else 0) != no_return:
            notify.emit(db, ctx, "no_return_change", "warning",
                        f"قسم «{name}» أصبح {'لا يُقبل إرجاعه' if no_return else 'يُقبل إرجاعه عاديًا'}", None, "category", cat_id)
    return dict(db.one("SELECT * FROM categories WHERE id=?", (cat_id,)))


def set_category(db: Database, ctx: Context, item_ids: list[str], cat_id: str | None) -> int:
    """Put items in a section. Moving an item into or out of a no-return section changes
    what can be returned, so that needs the owner's permission."""
    ctx.require("items.write")
    if not isinstance(item_ids, list) or not item_ids or len(item_ids) > 500:
        raise ValidationFailed("اختر الأصناف (حتى 500 مرة واحدة)")
    target = None
    if cat_id:
        target = db.one("SELECT * FROM categories WHERE id=? AND org_id=? AND is_active=1", (cat_id, ctx.org_id))
        if not target:
            raise NotFound("القسم غير موجود")
    marks = ",".join("?" * len(item_ids))
    rows = db.all(f"SELECT i.id, coalesce(c.no_return,0) nr FROM items i LEFT JOIN categories c ON c.id=i.category_id"
                  f" WHERE i.org_id=? AND i.id IN ({marks})", (ctx.org_id, *item_ids))
    if len(rows) != len(set(item_ids)):
        raise NotFound("يوجد صنف غير موجود")
    new_nr = target["no_return"] if target else 0
    if any(r["nr"] != new_nr for r in rows):
        ctx.require("items.no_return")
    with db.transaction():
        db.execute(f"UPDATE items SET category_id=? WHERE org_id=? AND id IN ({marks})", (cat_id, ctx.org_id, *item_ids))
        audit.record(db, ctx, "item.category", "category", cat_id, after={"items": len(rows)})
    return len(rows)


def set_shortage(db: Database, ctx: Context, item_id: str, on: bool) -> None:
    ctx.require("items.write")
    with db.transaction():
        n = db.execute("UPDATE items SET shortage_at=?, shortage_by=? WHERE id=? AND org_id=?",
                       ((date.today().isoformat(), ctx.user_id) if on else (None, None)) + (item_id, ctx.org_id)).rowcount
        if not n:
            raise NotFound("الصنف غير موجود")
        audit.record(db, ctx, "item.shortage", "item", item_id, after={"on": bool(on)})


# -------------------------------------------------------------------------- the card
def _months_back(today: date, n: int) -> list[str]:
    y, m, out = today.year, today.month, []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]


def card(db: Database, ctx: Context, item_id: str, today: date | None = None) -> dict:
    ctx.require("items.read")
    it = db.one("SELECT i.*, c.name category, c.no_return, c.mark FROM items i LEFT JOIN categories c"
                " ON c.id=i.category_id WHERE i.id=? AND i.org_id=?", (item_id, ctx.org_id))
    if not it or (it["hidden"] and not ctx.can("items.hidden")):
        raise NotFound("الصنف غير موجود")
    out = dict(it)
    out["barcodes"] = [{"code": b["barcode"], "gtin": valid_gtin(b["barcode"])} for b in db.all(
        "SELECT barcode FROM item_barcodes WHERE org_id=? AND item_id=? ORDER BY barcode", (ctx.org_id, item_id))]
    branch = ctx.branch_id
    if ctx.can("inventory.read") and branch:
        out["batches"] = [dict(r) for r in db.all(
            "SELECT sb.qty, nullif(sb.batch_key,'') batch_id, b.batch_no, b.expiry_date, b.public_price,"
            " (SELECT s.name FROM stock_movements m JOIN purchase_invoices p ON p.id=m.ref_id"
            "  JOIN suppliers s ON s.id=p.supplier_id WHERE m.batch_id=b.id AND m.movement_type='purchase'"
            "  ORDER BY m.rowid DESC LIMIT 1) supplier"
            " FROM stock_balances sb LEFT JOIN batches b ON b.id=nullif(sb.batch_key,'')"
            " WHERE sb.org_id=? AND sb.branch_id=? AND sb.item_id=? AND sb.qty<>0"
            " ORDER BY b.expiry_date IS NULL, b.expiry_date", (ctx.org_id, branch, item_id))]
        out["stock_qty"] = sum(b["qty"] for b in out["batches"])
    months = _months_back(today or date.today(), 12)
    sold = {r["ym"]: r["q"] for r in db.all(
        "SELECT substr(s.completed_at,1,7) ym, sum(a.qty_base - a.returned_qty) q FROM sale_allocations a"
        " JOIN sale_lines l ON l.id=a.sale_line_id JOIN sales s ON s.id=l.sale_id"
        " WHERE s.org_id=? AND l.item_id=? AND s.status='completed' AND s.completed_at >= ? GROUP BY 1",
        (ctx.org_id, item_id, months[0]))}
    imported = {r["ym"]: r["q"] for r in db.all(
        "SELECT substr(period_start,1,7) ym, sum(qty_base) q FROM consumption_history WHERE org_id=? AND item_id=?"
        " AND period_start >= ? AND period_end < date(period_start, '+40 days') GROUP BY 1",
        (ctx.org_id, item_id, months[0]))}
    out["monthly"] = [{"month": m, "qty": int(sold.get(m, 0) or imported.get(m, 0) or 0),
                       "source": "sales" if m in sold else "import" if m in imported else None} for m in months]
    if ctx.can("costs.read") or ctx.can("purchases.read"):
        sup = db.all(
            "SELECT s.name supplier, s.supplier_type, max(p.invoice_date) last_date, l.discount_bp, l.unit_net_price, l.public_price"
            " FROM purchase_invoice_lines l JOIN purchase_invoices p ON p.id=l.invoice_id JOIN suppliers s ON s.id=p.supplier_id"
            " WHERE l.org_id=? AND l.item_id=? AND p.status='approved' GROUP BY s.id ORDER BY l.unit_net_price",
            (ctx.org_id, item_id))
        out["suppliers"] = [dict(r) for r in sup]
    if ctx.can("costs.read") and branch:
        c = db.one("SELECT avg_cost FROM item_costs WHERE org_id=? AND branch_id=? AND item_id=?",
                   (ctx.org_id, branch, item_id))
        if c and c["avg_cost"] is not None and it["public_price"]:
            pack_cost = float(c["avg_cost"]) * it["pack_size"] * 100
            out["avg_cost_pack"] = round(pack_cost)
            out["margin_pct"] = round((it["public_price"] - pack_cost) / it["public_price"] * 100, 1)
    if it["shortage_by"]:
        u = db.one("SELECT full_name FROM users WHERE id=?", (it["shortage_by"],))
        out["shortage_by_name"] = u["full_name"] if u else None
    out["price_history"] = [dict(r) for r in db.all(
        "SELECT public_price, previous_price, source, observed_at FROM item_price_history WHERE org_id=? AND item_id=?"
        " ORDER BY observed_at DESC LIMIT 5", (ctx.org_id, item_id))]
    return out


def remove_barcode(db: Database, ctx: Context, item_id: str, barcode: str) -> None:
    ctx.require("items.write")
    with db.transaction():
        n = db.execute("DELETE FROM item_barcodes WHERE org_id=? AND item_id=? AND barcode=?",
                       (ctx.org_id, item_id, barcode)).rowcount
        if not n:
            raise NotFound("الباركود غير مسجل على هذا الصنف")
        audit.record(db, ctx, "item.barcode_remove", "item", item_id, before={"barcode": barcode})


MOVE_LABELS = {"purchase": "شراء", "purchase_reversal": "إلغاء شراء", "sale": "بيع", "sale_return": "مرتجع بيع",
               "supplier_return": "مرتجع للمورد", "adjustment": "تسوية / جرد", "opening": "رصيد افتتاحي",
               "transfer_in": "تحويل وارد", "transfer_out": "تحويل صادر", "damage": "تالف / إعدام"}


def movements(db: Database, ctx: Context, item_id: str, date_from: str, date_to: str) -> dict:
    """Everything that moved this item in a period: balance at the start, each movement with its
    invoice / count and who did it, and the balance at the end (in base units)."""
    from ..sales.service import local_day_bounds
    ctx.require("inventory.read")
    it = db.one("SELECT id, name_en, name_ar, pack_size, base_unit_name FROM items WHERE id=? AND org_id=?", (item_id, ctx.org_id))
    if not it:
        raise NotFound("الصنف غير موجود")
    try:
        d1, d2 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except (TypeError, ValueError):
        raise ValidationFailed("اختر الفترة")
    if d2 < d1:
        raise ValidationFailed("تاريخ النهاية قبل البداية")
    lo, hi = local_day_bounds(d1)[0], local_day_bounds(d2)[1]
    # every branch of the pharmacy, spelled out so the (org, branch, item) index is used instead of reading all movements
    branches = "branch_id IN (SELECT id FROM branches WHERE org_id=?)"
    opening = db.one(f"SELECT coalesce(sum(qty_delta),0) q FROM stock_movements WHERE org_id=? AND {branches} AND item_id=?"
                     " AND created_at<?", (ctx.org_id, ctx.org_id, item_id, lo))["q"]
    rows = db.all(
        "SELECT m.created_at, m.movement_type, m.qty_delta, m.ref_type, m.ref_id, m.note, b.batch_no, b.expiry_date,"
        " u.full_name by_name, s.sale_no, p.invoice_no, sp.name supplier FROM stock_movements m"
        " LEFT JOIN batches b ON b.id=m.batch_id LEFT JOIN users u ON u.id=m.created_by"
        " LEFT JOIN sales s ON s.id=m.ref_id AND m.movement_type IN ('sale','sale_return')"
        " LEFT JOIN purchase_invoices p ON p.id=m.ref_id AND m.movement_type IN ('purchase','purchase_reversal')"
        " LEFT JOIN suppliers sp ON sp.id=p.supplier_id"
        f" WHERE m.org_id=? AND m.{branches} AND m.item_id=? AND m.created_at>=? AND m.created_at<? ORDER BY m.created_at, m.rowid",
        (ctx.org_id, ctx.org_id, item_id, lo, hi))
    bal, out = opening, []
    for r in rows:
        bal += r["qty_delta"]
        d = dict(r)
        d["label"], d["balance"] = MOVE_LABELS.get(r["movement_type"], r["movement_type"]), bal
        out.append(d)
    by = {}
    for r in out:
        by[r["movement_type"]] = by.get(r["movement_type"], 0) + r["qty_delta"]
    return {"item": dict(it), "from": d1.isoformat(), "to": d2.isoformat(), "opening": opening, "closing": bal,
            "rows": out, "totals": [{"type": k, "label": MOVE_LABELS.get(k, k), "qty": v} for k, v in by.items()]}
