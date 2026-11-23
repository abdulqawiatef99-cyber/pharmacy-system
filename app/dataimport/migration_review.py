"""Owner review of ambiguous imported packs and E-Stock customer names."""
from collections import defaultdict
from contextlib import closing
from decimal import Decimal, ROUND_HALF_UP

from ..catalog import repack
from ..catalog.normalize import parse_name
from ..core import audit
from ..core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from ..sales import customers
from . import estock_history as history
from .estock_db import ident

PACK_FORMS = {"tab", "cap", "chew", "eff", "lozenge", "supp", "amp", "vial", "sachet"}
WHOLE_PACK = "item.import_pack_confirmed"
NAME_REVIEWED = "customer.estock_name_reviewed"


def _owner(ctx, *permissions):
    if not ctx.is_owner:
        raise Forbidden("المراجعة دي للمالك فقط")
    for permission in permissions:
        ctx.require(permission)


def _archive(db, ctx):
    if history.available(db):
        try:
            return history._con(db, ctx)
        except NotFound as err:
            if err.code != "no_estock_history":
                raise
    return None


def _pack_hint(item):
    for name in (item["name_en"], item["name_ar"]):
        parsed = parse_name(name or "")
        if parsed.form in PACK_FORMS and (parsed.pack_count or 0) > 1:
            return parsed
    return None


def _imported_item(db, ctx, item_id):
    org = ctx.org_id
    if db.one("SELECT 1 FROM item_price_history WHERE org_id=? AND item_id=? AND source='import'"
              " UNION ALL SELECT 1 FROM audit_log WHERE org_id=? AND entity_type='item' AND entity_id=?"
              " AND action='item.imported' LIMIT 1", (org, item_id, org, item_id)):
        return True
    con = _archive(db, ctx)
    if con is None:
        return False
    with closing(con):
        return ("map_items" in history._Arch(con).tables
                and con.execute("SELECT 1 FROM map_items WHERE program_id=? LIMIT 1", (item_id,)).fetchone() is not None)


def pack_rows(db, ctx):
    _owner(ctx, "items.read", "inventory.read", "costs.read")
    org = ctx.org_id
    imported = {r["id"] for r in db.all(
        "SELECT item_id id FROM item_price_history WHERE org_id=? AND source='import'"
        " UNION SELECT entity_id FROM audit_log WHERE org_id=? AND entity_type='item' AND action='item.imported'",
        (org, org))}
    con = _archive(db, ctx)
    if con is not None:
        with closing(con):
            imported.update(history._Arch(con).map("items").values())

    candidates = {}
    for it in db.all(
        "SELECT id, code, name_en, name_ar, base_unit_name FROM items"
        " WHERE org_id=? AND status='active' AND pack_size=1 AND is_service=0"
        " AND (? OR coalesce(hidden,0)=0)"
        " AND id NOT IN (SELECT entity_id FROM audit_log WHERE org_id=? AND action=?"
        " AND entity_type='item' AND entity_id IS NOT NULL)",
        (org, ctx.can("items.hidden"), org, WHOLE_PACK),
    ):
        if it["id"] not in imported:
            continue
        parsed = _pack_hint(it)
        if parsed:
            candidates[it["id"]] = {**dict(it), "printed_count": parsed.pack_count, "form": parsed.form,
                                     "stock_qty": 0, "stock_value": 0}
    if not candidates:
        return []

    values = defaultdict(Decimal)
    incomplete = set()
    for stock in db.all(
        "SELECT sb.item_id, sum(sb.qty) qty, ic.avg_cost"
        " FROM stock_balances sb JOIN items i ON i.org_id=sb.org_id AND i.id=sb.item_id"
        " LEFT JOIN item_costs ic ON ic.org_id=sb.org_id AND ic.branch_id=sb.branch_id AND ic.item_id=sb.item_id"
        " WHERE sb.org_id=? AND i.pack_size=1 AND sb.qty>0 GROUP BY sb.item_id, sb.branch_id, ic.avg_cost",
        (org,),
    ):
        iid = stock["item_id"]
        if iid in candidates:
            candidates[iid]["stock_qty"] += stock["qty"]
            if stock["avg_cost"] is None:
                incomplete.add(iid)
            else:
                values[iid] += Decimal(stock["avg_cost"]) * stock["qty"] * 100
    for iid, item in candidates.items():
        item["stock_value"] = None if iid in incomplete else int(values[iid].to_integral_value(ROUND_HALF_UP))
    return sorted(candidates.values(), key=lambda r: (
        r["stock_value"] is None, -(r["stock_value"] or 0), r["name_en"] or r["name_ar"] or "", r["id"],
    ))


def review_pack(db, ctx, item_id, pack_size, reason, base_unit_name):
    _owner(ctx, "items.write", "inventory.adjust")
    if type(pack_size) is not int or not 1 <= pack_size <= 10000:
        raise ValidationFailed("عدد الوحدات في العبوة لازم يكون رقم صحيح من 1 لـ 10000")
    if not isinstance(reason, str) or len(reason.strip()) < 3:
        raise ValidationFailed("اكتب سبب مراجعة العبوة")
    with db.transaction():
        it = db.one("SELECT pack_size, hidden, is_service, status, name_en, name_ar FROM items WHERE org_id=? AND id=?",
                    (ctx.org_id, item_id))
        if not it or (it["hidden"] and not ctx.can("items.hidden")):
            raise NotFound("الصنف غير موجود")
        reviewed = db.one("SELECT 1 FROM audit_log WHERE org_id=? AND entity_type='item' AND entity_id=? AND action=?",
                          (ctx.org_id, item_id, WHOLE_PACK))
        if it["pack_size"] != 1 or reviewed or it["is_service"] or it["status"] != "active":
            raise Conflict("بيانات العبوة اتغيرت أو اتراجعت؛ حدّث القائمة", code="stale_pack_review")
        if not _pack_hint(it) or not _imported_item(db, ctx, item_id):
            raise NotFound("الصنف مش موجود في مراجعة العبوات")
        if pack_size > 1:
            if not base_unit_name:
                raise ValidationFailed("اكتب اسم الوحدة الصغيرة")
            repack.correct_pack(db, ctx, item_id, pack_size, reason, expected_pack=1, base_unit_name=base_unit_name)
        else:
            audit.record(db, ctx, WHOLE_PACK, "item", item_id, after={"reason": reason.strip()[:200]})
    return {"done": True}


def _name_key(value):
    return " ".join((value or "").casefold().split())


def _name_candidates(db, ctx, only=None):
    con = _archive(db, ctx)
    if con is None:
        return []
    with closing(con):
        arch = history._Arch(con)
        party_map = arch.map("customers")
        if only is not None:
            party_map = {e: p for e, p in party_map.items() if p == only}
        if not party_map:
            return []
        legacy = {}
        table = "Customer"
        if arch.has(table):
            cols = ", ".join(arch.col(table, c) for c in
                             ("customer_id", "customer_code", "customer_name_ar", "customer_name_en"))
            for raw_id, code, ar, en in con.execute(f'SELECT {cols} FROM "e_{table}"'):
                old_id = ident(raw_id)
                if old_id in party_map:
                    legacy[old_id] = tuple(history._txt(v) for v in (code, ar, en))
        snapshots = defaultdict(dict)
        # A ticket can name a different person. Only customer-name columns are account name evidence.
        for table in ("Sales_header", "Back_sales_header"):
            if not arch.has(table):
                continue
            party = arch.col(table, "customer_id")
            fields = [arch.col(table, c) for c in ("customer_name", "customer_name_ar", "customer_name_en")]
            fields = [c for c in fields if c != "NULL"]
            if not fields:
                continue
            for raw_id, *values in con.execute(f'SELECT DISTINCT {party}, {", ".join(fields)} FROM "e_{table}"'):
                cid = party_map.get(ident(raw_id))
                if cid:
                    for value in values:
                        name = history._txt(value)
                        if name:
                            snapshots[cid].setdefault(_name_key(name), name)

    scoped = (ctx.org_id,) if only is None else (ctx.org_id, only)
    customer_rows = db.all("SELECT id, name FROM customers WHERE org_id=?"
                           + ("" if only is None else " AND id=?"), scoped)
    current = {r["id"]: r for r in customer_rows}
    output = {}
    for old_id, cid in party_map.items():
        if cid not in current:
            continue
        code, ar, en = legacy.get(old_id, (None, None, None))
        row = output.setdefault(cid, {"customer_id": cid, "current_name": current[cid]["name"],
                                       "estock_code": None,
                                       "name_ar": None, "name_en": None, "history": [], "options": []})
        for field, value in (("estock_code", code), ("name_ar", ar), ("name_en", en)):
            row[field] = row[field] or value
        row["options"].extend(name for name in (ar, en) if name)

    rows = []
    for cid, row in output.items():
        row["history"] = sorted(snapshots[cid].values(), key=_name_key)
        options = {_name_key(name): name for name in (*row["options"], *row["history"], row["current_name"])}
        if len(options) > 1:
            row["options"] = list(options.values())
            rows.append(row)
    return sorted(rows, key=lambda r: (_name_key(r["current_name"]), r["customer_id"]))


def customer_rows(db, ctx):
    _owner(ctx, "customers.manage", "sales.read")
    reviewed = {r["entity_id"]: r["name"] for r in db.all(
        "SELECT entity_id, json_extract(after_json, '$.name') name FROM audit_log"
        " WHERE org_id=? AND entity_type='customer' AND action=? ORDER BY seq", (ctx.org_id, NAME_REVIEWED))}
    return [r for r in _name_candidates(db, ctx) if reviewed.get(r["customer_id"]) != r["current_name"]]


def apply_customer_name(db, ctx, customer_id, name, current_name):
    _owner(ctx, "customers.manage", "sales.read")
    rows = _name_candidates(db, ctx, only=customer_id)
    if not rows:
        raise NotFound("العميل مش موجود في مراجعة أسماء إي ستوك")
    if name not in rows[0]["options"]:
        raise ValidationFailed("اختار اسم من قائمة المراجعة")
    with db.transaction():
        current = db.one("SELECT name FROM customers WHERE org_id=? AND id=?", (ctx.org_id, customer_id))
        reviewed = db.one("SELECT json_extract(after_json, '$.name') name FROM audit_log"
                          " WHERE org_id=? AND entity_type='customer' AND entity_id=? AND action=?"
                          " ORDER BY seq DESC LIMIT 1", (ctx.org_id, customer_id, NAME_REVIEWED))
        if not current or current["name"] != current_name or (reviewed and reviewed["name"] == current_name):
            raise Conflict("اسم العميل اتغيّر أو اتراجع؛ حدّث القائمة وراجعه تاني")
        if current_name != name:
            customers.update(db, ctx, customer_id, {"name": name})
        audit.record(db, ctx, NAME_REVIEWED, "customer", customer_id, before={"name": current_name}, after={"name": name})
    return {"done": True}
