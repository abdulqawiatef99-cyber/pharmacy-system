"""المثيل والبديل، وترتيبهم، وأزرار البيع السريع.

- المثيل: نفس المادة الفعالة (زي ما هي مكتوبة على كارت الصنف) ونفس التركيز ونفس مجموعة الشكل
  (أقراص/كبسول، شراب/معلق، ...). "نفس المادة بتركيز أو شكل تاني" بيطلع في قايمة لوحده، مش متخلط.
- البديل: من المجموعات العلاجية اللي الصاحب بيحددها بنفسه (مواد فعالة أو أصناف). مفيش أي تخمين.
- الترتيب: اللي ليه رصيد الأول؛ وبعدين اللي صلاحيته بتخلص خلال SOON_DAYS (الأقرب الأول)؛
  وبعدين الأغلى (سعر العلبة)؛ وبعدين الأعلى ربح (بالتكلفة المتوسطة).
"""
from __future__ import annotations

import threading
import time
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from ..core import audit
from ..core.errors import NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id
from .normalize import FORM_GROUP, ingredient_key, normalize_text

SOON_DAYS = 180          # "قرب يخلص": اتباع الأول
MAX_MEMBERS = 200
QUICK_DAYS = 30
QUICK_LIMIT = 24
_COLS = ("id, name_en, name_ar, strength, strength_norm, dosage_form, manufacturer, pack_size, base_unit_name,"
         " pack_unit_name, public_price, is_service, hidden, rx_required, misuse_risk, sale_note, category_id,"
         " ingredient_key, active_ingredient, quick_pin")


def _visible(ctx: Context, rows) -> list[dict]:
    out = [dict(r) for r in rows]
    return out if ctx.can("items.hidden") else [r for r in out if not r["hidden"]]


def rank(db: Database, ctx: Context, rows: list[dict], today: date | None = None) -> list[dict]:
    """Adds stock, nearest expiry and (for costs.read) margin, then sorts best-to-offer first."""
    if not rows:
        return rows
    today = today or date.today()
    soon = (today + timedelta(days=SOON_DAYS)).isoformat()
    ids = [r["id"] for r in rows]
    marks = ",".join("?" * len(ids))
    stock, expiry, cost = {}, {}, {}
    if ctx.branch_id:
        for s in db.all(f"SELECT sb.item_id, sum(sb.qty) q, min(CASE WHEN sb.qty>0 THEN b.expiry_date END) e"
                        f" FROM stock_balances sb LEFT JOIN batches b ON b.id=nullif(sb.batch_key,'')"
                        f" WHERE sb.org_id=? AND sb.branch_id=? AND sb.item_id IN ({marks}) GROUP BY sb.item_id",
                        (ctx.org_id, ctx.branch_id, *ids)):
            stock[s["item_id"]], expiry[s["item_id"]] = s["q"] or 0, s["e"]
        for c in db.all(f"SELECT item_id, avg_cost FROM item_costs WHERE org_id=? AND branch_id=? AND item_id IN ({marks})",
                        (ctx.org_id, ctx.branch_id, *ids)):
            if c["avg_cost"] is not None:
                cost[c["item_id"]] = Decimal(c["avg_cost"])
    show_stock, show_cost = ctx.can("inventory.read"), ctx.can("costs.read")
    keyed = []
    for r in rows:
        q, e = stock.get(r["id"], 0), expiry.get(r["id"])
        price = r.get("public_price") or 0
        margin = None
        if r["id"] in cost and price:
            pack_cost = cost[r["id"]] * r["pack_size"] * 100
            margin = int(((Decimal(price) - pack_cost) / Decimal(price) * 10000).to_integral_value(ROUND_HALF_UP))
        is_soon = bool(e and q > 0 and e <= soon)
        r["next_expiry"], r["expiry_soon"] = e, is_soon
        if show_stock:
            r["stock_qty"] = q
        if show_cost:
            r["margin_bp"] = margin
        has = q > 0 or r.get("is_service")
        keyed.append(((0 if has else 1, 0 if is_soon else 1, e if is_soon else "", -price,
                       -(margin if margin is not None else -10 ** 9), normalize_text(r.get("name_en") or r.get("name_ar"))), r))
    keyed.sort(key=lambda k: k[0])
    return [r for _, r in keyed]


def _groups_of(db: Database, ctx: Context, item: dict) -> list[dict]:
    refs = [("item", item["id"])] + ([("ingredient", item["ingredient_key"])] if item.get("ingredient_key") else [])
    q = " OR ".join("(m.kind=? AND m.ref=?)" for _ in refs)
    return [dict(g) for g in db.all(
        f"SELECT DISTINCT g.id, g.name, g.note FROM therapeutic_groups g JOIN therapeutic_group_members m ON m.group_id=g.id"
        f" WHERE g.org_id=? AND g.is_active=1 AND ({q}) ORDER BY g.name", (ctx.org_id, *[x for p in refs for x in p]))]


def for_item(db: Database, ctx: Context, item_id: str) -> dict:
    ctx.require("items.read")
    r = db.one(f"SELECT {_COLS}, status FROM items WHERE id=? AND org_id=?", (item_id, ctx.org_id))
    if not r or (r["hidden"] and not ctx.can("items.hidden")):
        raise NotFound("الصنف غير موجود")
    it = dict(r)
    key, grp = it["ingredient_key"], FORM_GROUP.get(it["dosage_form"] or "", it["dosage_form"])
    same, other = [], []
    if key:
        for x in _visible(ctx, db.all(f"SELECT {_COLS} FROM items WHERE org_id=? AND ingredient_key=? AND id<>?"
                                      " AND status='active'", (ctx.org_id, key, item_id))):
            xg = FORM_GROUP.get(x["dosage_form"] or "", x["dosage_form"])
            exact = x["strength_norm"] == it["strength_norm"] and xg == grp
            (same if exact else other).append(x)
    groups = _groups_of(db, ctx, it)
    alts: dict[str, dict] = {}
    skip = {item_id} | {x["id"] for x in same + other}
    for g in groups:
        members = db.all("SELECT kind, ref FROM therapeutic_group_members WHERE group_id=?", (g["id"],))
        keys = [m["ref"] for m in members if m["kind"] == "ingredient" and m["ref"] != key]
        ids = [m["ref"] for m in members if m["kind"] == "item"]
        conds, params = [], []
        if keys:
            conds.append(f"ingredient_key IN ({','.join('?' * len(keys))})")
            params += keys
        if ids:
            conds.append(f"id IN ({','.join('?' * len(ids))})")
            params += ids
        if not conds:
            continue
        for x in _visible(ctx, db.all(f"SELECT {_COLS} FROM items WHERE org_id=? AND status='active' AND ({' OR '.join(conds)})",
                                      (ctx.org_id, *params))):
            if x["id"] in skip:
                continue
            alts.setdefault(x["id"], x | {"groups": []})["groups"].append(g["name"])
    missing = []
    if not key:
        missing.append("ingredient")
    elif not it["strength_norm"]:
        missing.append("strength")
    return {"item": {k: it[k] for k in ("id", "name_en", "name_ar", "strength_norm", "dosage_form", "active_ingredient")},
            "similar": rank(db, ctx, same), "other_strength": rank(db, ctx, other),
            "alternatives": rank(db, ctx, list(alts.values())), "groups": groups, "missing": missing,
            "soon_days": SOON_DAYS}


# ------------------------------------------------------------------ therapeutic groups
def ingredients(db: Database, ctx: Context) -> dict:
    """Every ingredient written on the item cards (for picking group members), plus how many
    medicines have none written (they can't show a similar item)."""
    ctx.require("items.read")
    rows = db.all("SELECT ingredient_key k, active_ingredient a, count(*) n FROM items WHERE org_id=? AND status='active'"
                  " AND ingredient_key IS NOT NULL GROUP BY ingredient_key, active_ingredient ORDER BY ingredient_key, n DESC",
                  (ctx.org_id,))
    out: dict[str, dict] = {}
    for r in rows:
        g = out.setdefault(r["k"], {"key": r["k"], "label": r["a"], "items": 0})
        g["items"] += r["n"]
    missing = db.one("SELECT count(*) n FROM items WHERE org_id=? AND status='active' AND ingredient_key IS NULL"
                     " AND is_service=0 AND coalesce(is_drug,1)=1", (ctx.org_id,))["n"]
    return {"ingredients": list(out.values()), "without_ingredient": missing}


def list_groups(db: Database, ctx: Context) -> list[dict]:
    ctx.require("items.read")
    out = []
    for g in db.all("SELECT * FROM therapeutic_groups WHERE org_id=? ORDER BY is_active DESC, name", (ctx.org_id,)):
        d = dict(g)
        d["members"] = []
        for m in db.all("SELECT m.kind, m.ref, m.label, i.name_en, i.name_ar FROM therapeutic_group_members m"
                        " LEFT JOIN items i ON m.kind='item' AND i.id=m.ref WHERE m.group_id=? ORDER BY m.kind, m.label, i.name_en",
                        (g["id"],)):
            x = dict(m)
            if x["kind"] == "ingredient":
                x["items"] = db.one("SELECT count(*) n FROM items WHERE org_id=? AND status='active' AND ingredient_key=?",
                                    (ctx.org_id, x["ref"]))["n"]
            d["members"].append(x)
        out.append(d)
    return out


def save_group(db: Database, ctx: Context, data: dict, gid: str | None = None) -> list[dict]:
    ctx.require("items.groups")
    name = str(data.get("name") or "").strip()[:80]
    if len(name) < 2:
        raise ValidationFailed("اكتب اسم المجموعة (مثال: مضادات الحموضة)")
    norm = normalize_text(name)
    members = data.get("members")
    if not isinstance(members, list) or len(members) > MAX_MEMBERS:
        raise ValidationFailed("أعضاء المجموعة غير صحيحين")
    clean: dict[tuple, str | None] = {}
    for m in members:
        kind = (m or {}).get("kind")
        if kind == "ingredient":
            label = str(m.get("label") or "").strip()[:120]
            k = ingredient_key(label)
            if not k:
                raise ValidationFailed("اكتب اسم المادة الفعالة")
            clean.setdefault(("ingredient", k), label)
        elif kind == "item":
            iid = str(m.get("ref") or "")
            if not db.one("SELECT 1 FROM items WHERE id=? AND org_id=?", (iid, ctx.org_id)):
                raise NotFound("صنف غير موجود")
            clean.setdefault(("item", iid), None)
        else:
            raise ValidationFailed("نوع العضو غير صحيح")
    if len(clean) < 2:
        raise ValidationFailed("تحتاج المجموعة إلى مادتين أو صنفين على الأقل ليكونا بديلين لبعضهما")
    dup = db.one("SELECT id FROM therapeutic_groups WHERE org_id=? AND name_norm=?", (ctx.org_id, norm))
    if dup and dup["id"] != gid:
        raise ValidationFailed("توجد مجموعة بالاسم نفسه")
    active = int(bool(data.get("is_active", True)))
    note = (str(data.get("note") or "").strip()[:200]) or None
    with db.transaction():
        if gid:
            cur = db.one("SELECT * FROM therapeutic_groups WHERE id=? AND org_id=?", (gid, ctx.org_id))
            if not cur:
                raise NotFound("المجموعة غير موجودة")
            before = {"name": cur["name"], "members": len(db.all("SELECT 1 FROM therapeutic_group_members WHERE group_id=?", (gid,)))}
            db.execute("UPDATE therapeutic_groups SET name=?, name_norm=?, note=?, is_active=? WHERE id=?", (name, norm, note, active, gid))
            db.execute("DELETE FROM therapeutic_group_members WHERE group_id=?", (gid,))
            action = "therapeutic_group.update"
        else:
            gid, before = new_id(), None
            db.execute("INSERT INTO therapeutic_groups (id, org_id, name, name_norm, note, is_active, created_by) VALUES (?,?,?,?,?,?,?)",
                       (gid, ctx.org_id, name, norm, note, active, ctx.user_id))
            action = "therapeutic_group.create"
        for (kind, ref), label in clean.items():
            db.execute("INSERT INTO therapeutic_group_members (group_id, kind, ref, label) VALUES (?,?,?,?)", (gid, kind, ref, label))
        audit.record(db, ctx, action, "therapeutic_group", gid, before=before,
                     after={"name": name, "members": len(clean), "active": active})
    return list_groups(db, ctx)


def delete_group(db: Database, ctx: Context, gid: str) -> list[dict]:
    ctx.require("items.groups")
    g = db.one("SELECT name FROM therapeutic_groups WHERE id=? AND org_id=?", (gid, ctx.org_id))
    if not g:
        raise NotFound("المجموعة غير موجودة")
    with db.transaction():
        db.execute("DELETE FROM therapeutic_group_members WHERE group_id=?", (gid,))
        db.execute("DELETE FROM therapeutic_groups WHERE id=?", (gid,))
        audit.record(db, ctx, "therapeutic_group.delete", "therapeutic_group", gid, before={"name": g["name"]})
    return list_groups(db, ctx)


# ------------------------------------------------------------------ quick sale
# The best-seller count reads every sale line of the last 30 days (0.4 s on a large pharmacy, #253) each time a
# sale screen opens. The ranking is kept for a few minutes; the items themselves (active, hidden, names) are
# read fresh every time, so a stopped or hidden item never stays on a button.
_TOP_TTL = 600.0
_top_cache: dict[tuple, tuple[float, list[str]]] = {}
_top_lock = threading.Lock()


def _best_sellers(db: Database, ctx: Context, start: str) -> list[dict]:
    key = (ctx.org_id, ctx.branch_id, start)
    now = time.monotonic()
    with _top_lock:
        hit = _top_cache.get(key)
    if hit and now - hit[0] < _TOP_TTL:
        ids = hit[1]
    else:
        ids = [r["item_id"] for r in db.all(
            "SELECT l.item_id, count(DISTINCT s.id) n FROM sale_lines l JOIN sales s ON s.id=l.sale_id"
            " JOIN items i ON i.id=l.item_id"
            " WHERE s.org_id=? AND s.branch_id=? AND s.status='completed' AND s.completed_at>=? AND i.status='active'"
            " GROUP BY l.item_id ORDER BY n DESC, coalesce(i.name_en, i.name_ar) LIMIT ?",
            (ctx.org_id, ctx.branch_id, start, QUICK_LIMIT * 2))]
        with _top_lock:
            if len(_top_cache) > 200:
                _top_cache.clear()
            _top_cache[key] = (now, ids)
    if not ids:
        return []
    rows = {r["id"]: dict(r) for r in db.all(
        f"SELECT {_COLS} FROM items WHERE org_id=? AND status='active' AND id IN ({','.join('?' * len(ids))})",
        (ctx.org_id, *ids))}
    return [rows[i] for i in ids if i in rows]


def forget_best_sellers() -> None:
    with _top_lock:
        _top_cache.clear()


def quick_items(db: Database, ctx: Context, today: date | None = None) -> dict:
    """Buttons for the cashier: items the owner pinned, then the best sellers of the last 30 days
    (by number of sales), up to QUICK_LIMIT."""
    ctx.require("sales.create")
    from ..sales.service import local_day_bounds
    today = today or date.today()
    pinned = _visible(ctx, db.all(f"SELECT {_COLS} FROM items WHERE org_id=? AND status='active' AND quick_pin=1"
                                  " ORDER BY coalesce(name_en, name_ar)", (ctx.org_id,)))[:QUICK_LIMIT]
    start = local_day_bounds(today - timedelta(days=QUICK_DAYS - 1))[0]
    seen = {p["id"] for p in pinned}
    top = []
    for d in _best_sellers(db, ctx, start):
        if d["id"] in seen or (d["hidden"] and not ctx.can("items.hidden")):
            continue
        top.append(d)
        if len(pinned) + len(top) >= QUICK_LIMIT:
            break
    for p in pinned:
        p["pinned"] = True
    rows = pinned + top
    if rows and ctx.branch_id and ctx.can("inventory.read"):
        ids = [r["id"] for r in rows]
        q = {r["item_id"]: r["q"] for r in db.all(
            f"SELECT item_id, sum(qty) q FROM stock_balances WHERE org_id=? AND branch_id=? AND item_id IN ({','.join('?' * len(ids))})"
            " GROUP BY item_id", (ctx.org_id, ctx.branch_id, *ids))}
        for r in rows:
            r["stock_qty"] = q.get(r["id"], 0)
    return {"items": rows, "days": QUICK_DAYS, "limit": QUICK_LIMIT}


def backfill_ingredient_keys(db: Database) -> int:
    rows = db.all("SELECT id, active_ingredient FROM items WHERE ingredient_key IS NULL AND active_ingredient IS NOT NULL"
                  " AND active_ingredient <> ''")
    n = 0
    with db.transaction():
        for r in rows:
            k = ingredient_key(r["active_ingredient"])
            if k:
                db.execute("UPDATE items SET ingredient_key=? WHERE id=?", (k, r["id"]))
                n += 1
    return n
