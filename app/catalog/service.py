"""Catalog: items, suppliers, barcodes, aliases, supplier codes, price history."""
from __future__ import annotations

import sqlite3
from typing import Any

from ..core import audit, notify
from ..core.errors import Conflict, NotFound, ValidationFailed
from ..core.numbers import whole_number
from ..core.permissions import Context
from ..db import Database, new_id, row_to_dict
from . import matching
from .normalize import (dedupe_key, ingredient_key, normalize_barcode, normalize_form, normalize_text,
                        parse_name, strength_norm, valid_gtin)

ITEM_FIELDS = ("code", "name_en", "name_ar", "active_ingredient", "strength", "dosage_form",
               "manufacturer", "is_drug", "is_imported", "vat_bp", "pack_size",
               "base_unit_name", "pack_unit_name", "public_price", "min_stock", "max_stock", "location",
               "status", "rx_required", "misuse_risk", "sale_note", "is_service", "hide_on_receipt", "hidden", "quick_pin")
DUPLICATE_SCORE = 0.93


# ---------------------------------------------------------------- suppliers
def create_supplier(db: Database, ctx: Context, *, name: str, supplier_type: str = "other",
                    base_discount_local_bp: int | None = None,
                    base_discount_imported_bp: int | None = None,
                    payment_terms: str | None = None, phone: str | None = None) -> str:
    ctx.require("suppliers.write")
    name = (name or "").strip()
    if not name:
        raise ValidationFailed("اسم المورد مطلوب")
    for v in (base_discount_local_bp, base_discount_imported_bp):
        if v is not None and not 0 <= v <= 10000:
            raise ValidationFailed("نسبة الخصم غير صحيحة")
    sid = new_id()
    with db.transaction():
        try:
            db.execute("INSERT INTO suppliers (id, org_id, name, name_norm, supplier_type,"
                       " base_discount_local_bp, base_discount_imported_bp, payment_terms, phone)"
                       " VALUES (?,?,?,?,?,?,?,?,?)",
                       (sid, ctx.org_id, name, normalize_text(name), supplier_type,
                        base_discount_local_bp, base_discount_imported_bp, payment_terms, phone))
        except sqlite3.IntegrityError as e:
            if "UNIQUE" in str(e):
                raise Conflict("المورد موجود بالفعل") from e
            raise ValidationFailed("بيانات المورد غير صحيحة") from e
        audit.record(db, ctx, "supplier.create", "supplier", sid, after={"name": name})
    return sid


def list_suppliers(db: Database, ctx: Context) -> list[dict]:
    ctx.require("suppliers.read")
    return [dict(r) for r in db.all("SELECT * FROM suppliers WHERE org_id=? ORDER BY name",
                                    (ctx.org_id,))]


def get_supplier(db: Database, ctx: Context, supplier_id: str) -> dict:
    ctx.require("suppliers.read")
    r = db.one("SELECT * FROM suppliers WHERE id=? AND org_id=?", (supplier_id, ctx.org_id))
    if not r:
        raise NotFound("المورد غير موجود")
    return dict(r)


def find_supplier_by_name(db: Database, org_id: str, raw: str | None) -> str | None:
    if not raw:
        return None
    norm = normalize_text(raw)
    r = db.one("SELECT id FROM suppliers WHERE org_id=? AND name_norm=?", (org_id, norm))
    if r:
        return r["id"]
    # contained name (e.g. 'شركة ابن سينا فارما' contains 'ابن سينا')
    for s in db.all("SELECT id, name_norm FROM suppliers WHERE org_id=? AND is_active=1", (org_id,)):
        if len(s["name_norm"]) >= 4 and s["name_norm"] in norm:
            return s["id"]
    return None


# -------------------------------------------------------------------- items
def _clean_item(data: dict) -> dict:
    out = {k: data.get(k) for k in ITEM_FIELDS if k in data}
    for k in ("code", "name_en", "name_ar", "active_ingredient", "strength", "manufacturer",
              "location"):
        if k in out and isinstance(out[k], str):
            out[k] = out[k].strip() or None
    if "code" in out and out["code"] is not None:
        out["code"] = str(out["code"]).strip()
    if "dosage_form" in out and out["dosage_form"]:
        out["dosage_form"] = normalize_form(out["dosage_form"]) or str(out["dosage_form"]).lower()
    for k in ("vat_bp", "pack_size", "public_price", "min_stock", "max_stock"):
        if k in out and out[k] is not None:
            try:
                out[k] = whole_number(out[k])
            except (TypeError, ValueError):
                raise ValidationFailed(f"قيمة غير صحيحة في الحقل {k}")
    for k in ("rx_required", "misuse_risk", "is_service", "hide_on_receipt", "hidden", "quick_pin"):
        if k in out:
            out[k] = int(bool(out[k]))
    if "active_ingredient" in out:
        out["ingredient_key"] = ingredient_key(out["active_ingredient"])
    if "sale_note" in out:
        out["sale_note"] = (str(out["sale_note"] or "").strip()[:200]) or None
    if out.get("pack_size") is not None and out["pack_size"] < 1:
        raise ValidationFailed("عدد الوحدات في العبوة يجب أن يكون 1 أو أكثر")
    if out.get("public_price") is not None and out["public_price"] < 0:
        raise ValidationFailed("السعر لا يكون بالسالب")
    return out


def find_duplicates(db: Database, ctx: Context, name: str, *, exclude_id: str | None = None,
                    limit: int = 3) -> list[dict]:
    cands = matching.fuzzy_candidates(db, ctx.org_id, name, limit + 1)
    return [{"item_id": c.item_id, "label": c.label, "score": c.score, "reasons": c.reasons}
            for c in cands if c.score >= DUPLICATE_SCORE and not c.reasons
            and c.item_id != exclude_id][:limit]


def create_item(db: Database, ctx: Context, data: dict, *, barcodes: list[str] | None = None,
                force: bool = False, source: str = "manual",
                dedupe_suffix: str | None = None) -> str:
    """Creates an item. Refuses near-duplicates (same name after normalization,
    including Arabic/English spelling) unless force=True."""
    ctx.require("items.write")
    d = _clean_item(data)
    if d.get("public_price") is not None:
        ctx.require("items.price")
    name = d.get("name_en") or d.get("name_ar")
    if not name:
        raise ValidationFailed("اسم الصنف مطلوب (عربي أو إنجليزي)")
    parsed = parse_name(name)
    d.setdefault("pack_size", 1)
    d["strength_norm"] = strength_norm(d.get("strength")) or parsed.strength
    d["dosage_form"] = d.get("dosage_form") or parsed.form
    d["name_norm"] = parsed.norm
    d["name_ar_norm"] = normalize_text(d.get("name_ar")) or None
    d["dedupe_key"] = dedupe_key(name, d.get("strength"), d.get("dosage_form"), d["pack_size"],
                                 d.get("manufacturer"))
    if dedupe_suffix:
        # a duplicate the user chose to keep (e.g. two E-Stock codes for one product):
        # stored as a separate item, visibly tagged so it can be merged later
        if not force:
            raise ValidationFailed("dedupe_suffix requires force")
        d["dedupe_key"] += f"|dup:{dedupe_suffix}"
    if not force:
        dups = find_duplicates(db, ctx, name)
        if dups:
            raise Conflict("يوجد صنف مشابه جدًا، راجع قبل الإضافة", code="possible_duplicate",
                           details={"duplicates": dups})
    item_id = new_id()
    d["id"], d["org_id"] = item_id, ctx.org_id
    cols = ", ".join(d)
    with db.transaction():
        try:
            db.execute(f"INSERT INTO items ({cols}) VALUES ({', '.join('?' * len(d))})",
                       tuple(d.values()))
        except sqlite3.IntegrityError as e:
            msg = str(e)
            if "dedupe_key" in msg:
                raise Conflict("الصنف موجود بالفعل بنفس الاسم والتركيز والشكل والعبوة",
                               code="duplicate_item") from e
            if "code" in msg:
                raise Conflict("الكود مستخدم لصنف آخر", code="duplicate_code") from e
            raise ValidationFailed(f"بيانات الصنف غير صحيحة: {msg}") from e
        for bc in barcodes or []:
            add_barcode(db, ctx, item_id, bc)
        if d.get("public_price") is not None:
            db.execute("INSERT INTO item_price_history (id, org_id, item_id, public_price, source, changed_by)"
                       " VALUES (?,?,?,?,?,?)", (new_id(), ctx.org_id, item_id, d["public_price"], source, ctx.user_id))
        audit.record(db, ctx, "item.create", "item", item_id, after=d)
    matching.item_changed(db, ctx.org_id, item_id)
    return item_id


def get_item(db: Database, ctx: Context, item_id: str) -> dict:
    ctx.require("items.read")
    r = db.one("SELECT * FROM items WHERE id=? AND org_id=?", (item_id, ctx.org_id))
    if not r:
        raise NotFound("الصنف غير موجود")
    item = dict(r)
    item["barcodes"] = [b["barcode"] for b in db.all(
        "SELECT barcode FROM item_barcodes WHERE org_id=? AND item_id=?", (ctx.org_id, item_id))]
    return item


def update_item(db: Database, ctx: Context, item_id: str, changes: dict) -> dict:
    ctx.require("items.write")
    before = get_item(db, ctx, item_id)
    d = _clean_item(changes)
    if not d:
        return before
    if "pack_size" in d and d["pack_size"] != before["pack_size"] and db.one(
            "SELECT 1 FROM stock_movements WHERE org_id=? AND item_id=? LIMIT 1", (ctx.org_id, item_id)):
        # stock is stored in base units computed with the old pack size; changing it
        # silently would mis-state quantities and unit prices
        raise Conflict("لا يمكن تغيير عدد الوحدات في العبوة لصنف له حركات مخزون؛ صحح الرصيد بالجرد أولًا"
                       " أو أنشئ صنفًا جديدًا", code="pack_size_locked")
    if "public_price" in d:
        if d["public_price"] == before.get("public_price"):
            del d["public_price"]
        else:
            ctx.require("items.price")
    if not d:
        return before
    if "hidden" in d and bool(d["hidden"]) != bool(before.get("hidden")):
        ctx.require("items.hidden")          # hiding or showing an item is the hidden-items right (#324 X12)
    if d.get("is_service") and not before.get("is_service") and db.one(
            "SELECT 1 FROM stock_movements WHERE org_id=? AND item_id=? LIMIT 1", (ctx.org_id, item_id)):
        # a service sells with no batch, expiry, recall hold or stock movement, and its past sales would be
        # reversed without stock (#324 S17 / X10)
        raise Conflict("الصنف ده ليه حركات مخزون؛ مايتحولش لخدمة. اعمل صنف خدمة جديد", code="service_has_stock")
    merged = {**before, **d}
    name = merged.get("name_en") or merged.get("name_ar")
    if not name:
        raise ValidationFailed("اسم الصنف مطلوب")
    if any(k in d for k in ("name_en", "name_ar", "strength", "dosage_form", "pack_size",
                            "manufacturer")):
        p = parse_name(name)
        d["name_norm"] = p.norm
        d["strength_norm"] = strength_norm(merged.get("strength")) or p.strength
        d["dedupe_key"] = dedupe_key(name, merged.get("strength"), merged.get("dosage_form"),
                                     merged["pack_size"], merged.get("manufacturer"))
        if "|dup:" in (before.get("dedupe_key") or ""):
            d["dedupe_key"] += before["dedupe_key"][before["dedupe_key"].index("|dup:"):]
    if "name_ar" in d:
        d["name_ar_norm"] = normalize_text(d.get("name_ar")) or None
    d["updated_at"] = _now()
    sets = ", ".join(f"{k}=?" for k in d)
    with db.transaction():
        try:
            db.execute(f"UPDATE items SET {sets} WHERE id=? AND org_id=?",
                       (*d.values(), item_id, ctx.org_id))
        except sqlite3.IntegrityError as e:
            raise Conflict("التعديل يجعل الصنف مكررًا مع صنف آخر") from e
        if "public_price" in d and d["public_price"] != before.get("public_price"):
            record_price(db, ctx, item_id, d["public_price"], before.get("public_price"), "manual")
            old = before.get("public_price")
            notify.emit(db, ctx, "price_change", "warning",
                        f"تحديث سعر الجمهور: {merged.get('name_en') or merged.get('name_ar')}",
                        f"{(old or 0) / 100:,.2f} ← {(d['public_price'] or 0) / 100:,.2f} ج", "item", item_id)
        audit.record(db, ctx, "item.update", "item", item_id,
                     before={k: before.get(k) for k in d if k != "updated_at"},
                     after={k: v for k, v in d.items() if k != "updated_at"})
    matching.item_changed(db, ctx.org_id, item_id)
    return get_item(db, ctx, item_id)


def record_price(db: Database, ctx: Context, item_id: str, new_price: int, old_price: int | None,
                 source: str, ref_id: str | None = None) -> None:
    db.execute("INSERT INTO item_price_history (id, org_id, item_id, public_price, previous_price,"
               " source, ref_id, changed_by) VALUES (?,?,?,?,?,?,?,?)",
               (new_id(), ctx.org_id, item_id, new_price, old_price, source, ref_id, ctx.user_id))


def add_barcode(db: Database, ctx: Context, item_id: str, barcode: str) -> str:
    ctx.require("items.write")
    bc = normalize_barcode(barcode)
    if not bc or len(bc) < 6:
        raise ValidationFailed("باركود غير صحيح")
    with db.transaction():
        if not db.one("SELECT 1 FROM items WHERE id=? AND org_id=?", (item_id, ctx.org_id)):
            raise NotFound("الصنف غير موجود")
        existing = db.one("SELECT item_id FROM item_barcodes WHERE org_id=? AND barcode=?",
                          (ctx.org_id, bc))
        if existing:
            if existing["item_id"] == item_id:
                return bc
            raise Conflict("الباركود مسجل على صنف آخر", code="barcode_taken",
                           details={"item_id": existing["item_id"]})
        db.execute("INSERT INTO item_barcodes (org_id, barcode, item_id) VALUES (?,?,?)",
                   (ctx.org_id, bc, item_id))
        audit.record(db, ctx, "item.barcode_add", "item", item_id,
                     after={"barcode": bc, "gtin_valid": valid_gtin(bc)})
    return bc


def add_alias(db: Database, ctx: Context, item_id: str, raw: str, *, supplier_id: str | None = None,
              source: str = "manual") -> None:
    norm = normalize_text(raw)
    if not norm:
        return
    with db.transaction():
        existing = db.one("SELECT item_id FROM item_aliases WHERE org_id=? AND supplier_key=?"
                          " AND alias_norm=?", (ctx.org_id, supplier_id or "", norm))
        if existing:
            if existing["item_id"] != item_id:
                # a human re-mapped this spelling: newest confirmation wins, audited
                db.execute("UPDATE item_aliases SET item_id=?, source=?, created_by=? WHERE org_id=?"
                           " AND supplier_key=? AND alias_norm=?",
                           (item_id, source, ctx.user_id, ctx.org_id, supplier_id or "", norm))
                audit.record(db, ctx, "item.alias_remap", "item", item_id,
                             before={"item_id": existing["item_id"]}, after={"alias": raw})
            return
        db.execute("INSERT INTO item_aliases (id, org_id, item_id, supplier_key, alias_norm,"
                   " alias_raw, source, created_by) VALUES (?,?,?,?,?,?,?,?)",
                   (new_id(), ctx.org_id, item_id, supplier_id or "", norm, raw, source, ctx.user_id))


def set_supplier_code(db: Database, ctx: Context, supplier_id: str, code: str, item_id: str) -> None:
    code = str(code).strip()
    if not code:
        return
    with db.transaction():
        db.execute("INSERT INTO supplier_item_codes (org_id, supplier_id, code, item_id)"
                   " VALUES (?,?,?,?) ON CONFLICT(org_id, supplier_id, code)"
                   " DO UPDATE SET item_id=excluded.item_id",
                   (ctx.org_id, supplier_id, code, item_id))


def search_items(db: Database, ctx: Context, q: str, limit: int = 20) -> list[dict]:
    from .search import search
    return search(db, ctx, q, limit)["items"]


def _now() -> str:
    from ..core.accounts import iso, utcnow
    return iso(utcnow())


def item_row(db: Database, ctx: Context, item_id: str) -> dict[str, Any] | None:
    return row_to_dict(db.one("SELECT * FROM items WHERE id=? AND org_id=?", (item_id, ctx.org_id)))
