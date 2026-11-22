"""U4-2: the purchases tool («أداة المشتريات»), rebuilt on demand.monthly() and the owner's weekly-order rules.

Read-only for money and stock: it never orders, never sends anything and never changes a price. The only things
it stores are the order tracking (one JSON value in org_settings, key 'purchase_orders': no database change, so it
ships through «تحديث») and the supplier's own name for an item (item_aliases with the supplier's key, the same
table invoice matching reads, so a remembered name also matches that supplier's next invoice).

Rules (owner's playbook, the 12 COMPLETE months before this one; the current month is partial and would pull every
average down):
- regular      = sold in >= 6 of the 12 months and in >= 2 of the last 3;
- semi-regular = sold in 4 or 5 of the 12;
- irregular    = anything else that sold; dead = no sale in 12 months.
- in the list by default: (regular or semi-regular, AND sold in the last 3 months) and short for the longest
  column (a month), OR marked short (the shortage book / a customer asked), whatever its class.
- monthly average = max(average of the last 3 months, average of the last 6), base units;
- quantity for D days = ceil(avg x D / 30 - stock - arrived not entered - on order), in whole packs;
  stock = non-expired on hand (negative counts as 0); arrived not entered = draft purchase invoices + order lines
  marked arrived whose invoice is not entered yet; on order = order lines marked sent (for SENT_DAYS days).
- medicines only (coalesce(is_drug, 1) = 1) unless supplies are asked for; a shortage-book item always shows.
Discounts: each active supplier's LATEST purchase of the item in the last year, as a real discount (the bonus as
free units, the net price as written, so fixed margins count), side by side; the best is where the item goes.
Discounts are the owner's to see (costs.read), as on the smart order (#402).
"""
from __future__ import annotations

import io
import json
import math
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction

from ..core import audit
from ..core.errors import NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id
from .smart_order import EXPENSIVE_DEFAULT, HISTORY_DAYS, _official_bp, real_discount_bp

PERIODS = (7, 14, 30)
PERIOD_DEFAULT = 7
SENT_DAYS = 14                 # a sent line nobody marked counts as on order this long, then it is "old"
KEEP_DAYS = 120                # tracked orders older than this are dropped from the stored list
MAX_ORDERS = 300
MAX_LINES = 500
MAX_PACKS = 100000
KEY = "purchase_orders"
LINE_STATUS = ("sent", "arrived", "partial", "na")
CLASS_AR = {"regular": "منتظم", "semi": "شبه منتظم", "irregular": "متقطع", "dead": "راكد"}


# ------------------------------------------------------------------ the rules (pure)
def classify(units: list[int]) -> str:
    """units: the 12 complete months, oldest first."""
    n12 = sum(1 for u in units if u > 0)
    n3 = sum(1 for u in units[-3:] if u > 0)
    if n12 >= 6 and n3 >= 2:
        return "regular"
    if 4 <= n12 <= 5:
        return "semi"
    return "irregular" if n12 else "dead"


def monthly_avg(units: list[int]) -> Fraction:
    return max(Fraction(sum(units[-3:]), 3), Fraction(sum(units[-6:]), 6))


def packs_for(avg, days: int, *, stock: int, arrived: int, on_order: int, pack: int) -> int:
    need = Fraction(avg) * days / 30 - max(int(stock), 0) - int(arrived) - int(on_order)
    return math.ceil(need / max(int(pack), 1)) if need > 0 else 0


# ------------------------------------------------------------------ stored orders
def _now(now) -> str:
    if isinstance(now, datetime):
        dt = now
    elif isinstance(now, date):
        dt = datetime(now.year, now.month, now.day, 12, 0, tzinfo=timezone.utc)
    else:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _load(db: Database, org_id: str) -> list[dict]:
    r = db.one("SELECT value FROM org_settings WHERE org_id=? AND key=?", (org_id, KEY))
    try:
        v = json.loads(r["value"]) if r else []
    except (TypeError, ValueError):
        v = []
    return v if isinstance(v, list) else []


def _save(db: Database, org_id: str, orders: list[dict], now: str) -> None:
    cut = (datetime.fromisoformat(now.replace("Z", "+00:00")) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    orders = [o for o in orders if str(o.get("created_at", "")) >= cut]
    kept, per = [], {}
    for o in reversed(orders):                 # newest first: the cap is per branch, never one branch for another
        b = o.get("branch_id")
        per[b] = per.get(b, 0) + 1
        if per[b] <= MAX_ORDERS:
            kept.append(o)
    orders = list(reversed(kept))
    db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?,?,?) ON CONFLICT(org_id, key)"
               " DO UPDATE SET value=excluded.value", (org_id, KEY, json.dumps(orders, ensure_ascii=False)))


def _packs(v) -> int:
    from ..core.numbers import whole_number
    try:
        n = whole_number(v)
    except (TypeError, ValueError):
        raise ValidationFailed("الكمية لازم رقم صحيح (علب)")
    if not 1 <= n <= MAX_PACKS:
        raise ValidationFailed("الكمية لازم من 1 لـ 100000 علبة")
    return n


def create_order(db: Database, ctx: Context, *, supplier_id: str, lines: list, now=None) -> dict:
    """The owner sent this order himself (the program never sends): it is tracked from now."""
    ctx.require("purchases.create")
    if not isinstance(lines, list) or not lines or len(lines) > MAX_LINES:
        raise ValidationFailed("الطلبية فاضية أو كبيرة")
    sup = db.one("SELECT id, name FROM suppliers WHERE id=? AND org_id=?", (str(supplier_id or ""), ctx.org_id))
    if not sup:
        raise NotFound("المورد غير موجود")
    at = _now(now)
    out, seen = [], set()
    for ln in lines:
        if not isinstance(ln, dict):
            raise ValidationFailed("بيانات الطلبية غير صحيحة")
        iid = str(ln.get("item_id") or "")
        if iid in seen:
            raise ValidationFailed("الصنف مكرر في الطلبية")
        seen.add(iid)
        packs = _packs(ln.get("packs"))
        it = db.one("SELECT id, coalesce(name_en, name_ar) n FROM items WHERE id=? AND org_id=?", (iid, ctx.org_id))
        if not it:
            raise NotFound("الصنف غير موجود")
        out.append({"item_id": iid, "label": it["n"], "packs": packs, "status": "sent", "got": 0, "at": None})
    order = {"id": new_id(), "supplier_id": sup["id"], "supplier": sup["name"], "branch_id": ctx.branch_id,
             "created_at": at, "created_by": ctx.user_id, "lines": out}
    with db.transaction():
        orders = _load(db, ctx.org_id)
        orders.append(order)
        _save(db, ctx.org_id, orders, at)
        audit.record(db, ctx, "purchase_order.create", "purchase_order", order["id"],
                     after={"supplier_id": sup["id"], "lines": [(x["item_id"], x["packs"]) for x in out]})
    return _shape(order)


def set_line(db: Database, ctx: Context, order_id: str, item_id: str, *, status: str, got=None, now=None) -> dict:
    """sent / arrived (all of it) / partial (got packs of it) / na (the supplier did not have it)."""
    ctx.require("purchases.create")
    if status not in LINE_STATUS:
        raise ValidationFailed("حالة غير صحيحة")
    at = _now(now)
    with db.transaction():
        orders = _load(db, ctx.org_id)
        o = next((x for x in orders if x.get("id") == order_id and x.get("branch_id") == ctx.branch_id), None)
        ln = next((x for x in (o or {}).get("lines", []) if x.get("item_id") == item_id), None)
        if not ln:
            raise NotFound("الطلبية أو الصنف غير موجود")
        before = {"status": ln["status"], "got": ln.get("got", 0)}
        if status == "partial":
            g = _packs(got)
            if g >= int(ln["packs"]):
                raise ValidationFailed("الجزئي لازم أقل من المطلوب؛ لو وصل كله اختار «وصل»")
            ln["got"] = g
        else:
            ln["got"] = int(ln["packs"]) if status == "arrived" else 0
        ln["status"], ln["at"] = status, (at if status in ("arrived", "partial") else None)
        _save(db, ctx.org_id, orders, at)
        audit.record(db, ctx, "purchase_order.line", "purchase_order", order_id, before=before,
                     after={"item_id": item_id, "status": status, "got": ln["got"]})
    return _shape(o)


def _status(o: dict) -> str:
    st = {ln["status"] for ln in o.get("lines", [])}
    if st == {"sent"}:
        return "sent"
    if st <= {"arrived"}:
        return "arrived"
    if "sent" in st:
        return "partial"
    return "done"


def _shape(o: dict) -> dict:
    return {**o, "status": _status(o)}


def list_orders(db: Database, ctx: Context) -> list[dict]:
    ctx.require("purchases.read")
    return [_shape(o) for o in reversed(_load(db, ctx.org_id)) if o.get("branch_id") == ctx.branch_id]


def _invoiced(db: Database, ctx: Context, since: str) -> dict[tuple[str, str], str]:
    """(supplier, item) -> the latest time a purchase invoice (draft or approved) of that supplier with that item
    was created, for invoices since `since`."""
    out = {}
    for r in db.all("SELECT p.supplier_id s, l.item_id i, max(p.created_at) t FROM purchase_invoices p"
                    " JOIN purchase_invoice_lines l ON l.invoice_id=p.id WHERE p.org_id=? AND p.branch_id=?"
                    " AND p.status IN ('draft','approved') AND p.created_at>=? AND l.item_id IS NOT NULL"
                    " GROUP BY p.supplier_id, l.item_id", (ctx.org_id, ctx.branch_id, since)):
        out[(r["s"], r["i"])] = r["t"]
        if r["t"] > out.get((None, r["i"]), ""):
            out[(None, r["i"])] = r["t"]          # any supplier: the item was bought elsewhere
    return out


def _tracked(db: Database, ctx: Context, today: date, pack: dict) -> tuple[dict, dict, list]:
    """({item: on order units}, {item: arrived-not-entered units}, [ask-about lines])."""
    orders = [o for o in _load(db, ctx.org_id) if o.get("branch_id") == ctx.branch_id]
    if not orders:
        return {}, {}, []
    since = min(str(o["created_at"]) for o in orders)
    inv = _invoiced(db, ctx, since)
    old = _now(today - timedelta(days=SENT_DAYS))
    on_order, arrived, ask = {}, {}, []
    for o in orders:
        for ln in o.get("lines", []):
            iid, sid, p = ln["item_id"], o["supplier_id"], pack.get(ln["item_id"])
            if p is None:
                continue
            entered = inv.get((sid, iid))
            if ln["status"] == "sent":
                if (entered and entered >= o["created_at"]) or o["created_at"] < old:
                    continue                    # its invoice came, or nobody marked it for two weeks
                on_order[iid] = on_order.get(iid, 0) + int(ln["packs"]) * p
            elif ln["status"] in ("arrived", "partial"):
                # its invoice is entered (before or after the line was ticked): the draft or the stock counts it
                if not (entered and entered >= o["created_at"]):
                    arrived[iid] = arrived.get(iid, 0) + int(ln.get("got") or 0) * p
                bought = inv.get((None, iid))
                if bought and bought >= (ln.get("at") or o["created_at"]):
                    continue                    # bought since (here or elsewhere): nothing left to ask about
                if ln["status"] == "partial":
                    ask.append({"item_id": iid, "label": ln.get("label"), "supplier": o.get("supplier"),
                                "missing_packs": int(ln["packs"]) - int(ln.get("got") or 0), "why": "partial"})
            elif ln["status"] == "na":
                if (inv.get((None, iid)) or "") >= o["created_at"]:
                    continue                    # bought elsewhere since
                ask.append({"item_id": iid, "label": ln.get("label"), "supplier": o.get("supplier"),
                            "missing_packs": int(ln["packs"]), "why": "na"})
    return on_order, arrived, ask


# ------------------------------------------------------------------ the plan
def _purchases(db: Database, ctx: Context, today: date, sups: dict) -> tuple[dict, dict]:
    """({item: last purchase}, {item: {supplier: {latest, prev}}}) over the last year, newest first."""
    since = (today - timedelta(days=HISTORY_DAYS)).isoformat()
    last, per = {}, {}
    for r in db.all(
            "SELECT l.item_id, p.supplier_id, p.id inv, coalesce(p.invoice_date, substr(p.approved_at,1,10)) day, l.qty, l.bonus_qty,"
            " l.public_price, l.discount_bp, l.unit_net_price, l.raw_name, l.match_status, l.match_method"
            " FROM purchase_invoice_lines l JOIN purchase_invoices p ON p.id=l.invoice_id WHERE p.org_id=? AND"
            " p.branch_id=? AND p.status='approved' AND l.item_id IS NOT NULL AND l.qty>0 AND p.approved_at>=?"
            " ORDER BY p.approved_at DESC, l.line_no", (ctx.org_id, ctx.branch_id, since)):
        disc = real_discount_bp(r["public_price"], r["unit_net_price"], r["qty"], r["bonus_qty"], r["discount_bp"])
        sup = sups.get(r["supplier_id"])
        rec = {"supplier_id": r["supplier_id"], "supplier": sup["name"] if sup else None, "discount_bp": disc,
               "date": r["day"], "qty": r["qty"], "bonus": r["bonus_qty"] or 0, "public_price": r["public_price"],
               "active": bool(sup and sup["is_active"]), "raw_name": r["raw_name"], "inv": r["inv"],
               "unsure": r["match_status"] == "suggested" or (r["match_method"] == "fuzzy" and r["match_status"] != "confirmed")}
        last.setdefault(r["item_id"], rec)
        if r["supplier_id"]:
            cell = per.setdefault(r["item_id"], {}).setdefault(r["supplier_id"], [])
            if not cell or (len(cell) == 1 and cell[0]["inv"] != r["inv"]):
                cell.append(rec)                # the latest invoice, then the one before it (never the same one)
    return last, per


def plan(db: Database, ctx: Context, *, today: date | None = None, scope: str = "default", supplies: bool = False,
         expensive_from: int = EXPENSIVE_DEFAULT) -> dict:
    ctx.require("purchases.read")
    if scope not in ("default", "all"):
        raise ValidationFailed("نطاق غير صحيح")
    from . import demand
    from .service import Procurement
    p = Procurement(db, today=today)
    today = p.today
    o, b = ctx.org_id, ctx.branch_id
    m = demand.monthly(db, ctx, months=13, today=today)
    months = m["months"][:-1]                           # the 12 complete months
    d = p._all_items(ctx, 1)                            # stock (expired apart) and draft invoices only
    sups = {r["id"]: dict(r) for r in db.all(
        "SELECT id, name, supplier_type, base_discount_local_bp, base_discount_imported_bp, is_active FROM suppliers"
        " WHERE org_id=?", (o,))}
    last, per = _purchases(db, ctx, today, sups)
    items = db.all("SELECT i.id, i.code, i.name_en, i.name_ar, i.pack_size, i.public_price, i.is_imported, i.shortage_at,"
                   " coalesce(i.is_drug, 1) drug, c.mark FROM items i LEFT JOIN categories c ON c.id=i.category_id"
                   " WHERE i.org_id=? AND i.status='active' AND i.is_service=0", (o,))
    pack = {r["id"]: max(int(r["pack_size"] or 1), 1) for r in items}
    sent, arrived_t, ask = _tracked(db, ctx, today, pack)
    see = ctx.can("costs.read")
    rows, counts = [], {"regular": 0, "semi": 0, "irregular": 0, "dead": 0, "hidden": 0}
    for it in items:
        iid, pk = it["id"], pack[it["id"]]
        asked = bool(it["shortage_at"])
        if not (it["drug"] or supplies or asked):
            continue
        hist = m["items"].get(iid, {})
        units = [int(hist.get(mo, {}).get("units", 0)) for mo in months]
        cls = classify(units)
        avg = monthly_avg(units)
        total, expired = d["stock"].get(iid, (0, 0))
        on_hand = total - expired
        arrived = d["on_order"].get(iid, 0) + arrived_t.get(iid, 0)
        on_order = sent.get(iid, 0)
        qty = {str(n): packs_for(avg, n, stock=on_hand, arrived=arrived, on_order=on_order, pack=pk) for n in PERIODS}
        if asked and max(on_hand, 0) + arrived + on_order <= 0:
            qty = {k: max(v, 1) for k, v in qty.items()}     # a customer asked and nothing is coming: one box
        recent = any(u > 0 for u in units[-3:])
        shown = asked or (cls in ("regular", "semi") and recent and qty["30"] > 0)
        if not shown:
            if qty["30"] <= 0:
                continue
            counts["hidden"] += 1
            if scope == "default":
                continue
        counts[cls] += 1
        sp = per.get(iid, {})
        side = []
        for sid, recs in sp.items():
            s = sups.get(sid) or {}
            side.append({"supplier_id": sid, "supplier": s.get("name"), "active": bool(s.get("is_active")),
                         "discount_bp": recs[0]["discount_bp"], "date": recs[0]["date"],
                         "prev_bp": recs[1]["discount_bp"] if len(recs) > 1 else None,
                         "kind": s.get("supplier_type")})
        side.sort(key=lambda x: (not x["active"], -(x["discount_bp"] if x["discount_bp"] is not None else -1)))
        best = next((x for x in side if x["active"] and x["discount_bp"] is not None), None) \
            or next((x for x in side if x["active"]), None)
        lst = last.get(iid)
        flags = []
        price = it["public_price"]
        if price is not None and price >= expensive_from:
            flags.append("expensive")
        if it["mark"] == "fridge":
            flags.append("fridge")
        official = _official_bp(sups.get(best["supplier_id"]) if best else None, bool(it["is_imported"]))
        if best and best["discount_bp"] is not None:
            if official is not None and best["discount_bp"] < official:
                flags.append("below_official")
            ref = [x for x in ((lst or {}).get("discount_bp"), best["prev_bp"]) if x is not None]
            if ref and best["discount_bp"] < max(ref):
                flags.append("below_last")
        if lst and lst["unsure"]:
            flags.append("unsure_match")
        reasons = [f"{CLASS_AR[cls]}: اتباع في {sum(1 for u in units if u > 0)} شهر من 12"
                   f" ({sum(1 for u in units[-3:] if u > 0)} من آخر 3)"]
        if avg:
            reasons.append(f"متوسط الشهر {float(avg) / pk:.1f} علبة")
        if asked:
            reasons.append("مسجل ناقصًا (كشكول النواقص / طلب عميل)")
        if arrived:
            reasons.append(f"وصل ومش متسجل: {arrived} وحدة")
        if on_order:
            reasons.append(f"متطلب ولسه موصلش: {on_order} وحدة")
        if expired:
            reasons.append(f"منتهي ومش محسوب: {expired} وحدة")
        if not see:
            # which supplier is cheaper is itself the discount (Codex P1 on #402): by name, no best
            side = sorted(({**x, "discount_bp": None, "prev_bp": None} for x in side), key=lambda x: x["supplier"] or "")
            best = None
            lst = {k: lst[k] for k in ("supplier_id", "supplier", "date", "active")} if lst else None
            flags = [f for f in flags if f not in ("below_official", "below_last")]
        rows.append({"item_id": iid, "code": it["code"], "label": it["name_en"] or it["name_ar"], "pack_size": pk,
                     "public_price": price, "on_hand": on_hand, "arrived": arrived, "on_order": on_order,
                     "expired": expired, "units": units, "avg_month": round(float(avg), 2), "class": cls,
                     "qty": qty, "asked": asked, "shown": shown, "supply": not it["drug"], "flags": flags,
                     "suppliers": side, "best": best, "last": lst, "reasons": reasons})
    rows.sort(key=lambda r: (not r["asked"], -(r["public_price"] or 0) * r["qty"]["30"], r["label"] or ""))
    ids = {r["item_id"] for r in rows}
    no_sup = [{"item_id": r["item_id"], "label": r["label"], "why": "no_supplier"} for r in rows
              if r["shown"] and not any(x["active"] for x in r["suppliers"])]
    ask_about = no_sup + [a for a in ask if a["item_id"] in pack]
    check = [{"item_id": r["item_id"], "label": r["label"], "raw_name": last[r["item_id"]]["raw_name"],
              "pack_size": r["pack_size"]} for r in rows if "unsure_match" in r["flags"] and r["item_id"] in last]
    used = sorted({(r["best"] or {}).get("supplier_id") for r in rows} - {None},  # empty without costs.read
                  key=lambda s: sups[s]["name"] if s in sups else "")
    return {"rows": rows, "months": months, "periods": list(PERIODS), "period_default": PERIOD_DEFAULT,
            "counts": counts, "ask_about": ask_about, "check_names": check, "expensive_from": expensive_from,
            "suppliers": [{"id": s, "name": sups[s]["name"], "kind": sups[s]["supplier_type"]} for s in used],
            "all_suppliers": [{"id": k, "name": v["name"]} for k, v in sups.items() if v["is_active"]],
            "shown_ids": len(ids), "estock_archive": bool(m.get("archive_from"))}


# ------------------------------------------------------------------ names, Excel, message
def supplier_names(db: Database, ctx: Context, supplier_id: str | None, item_ids: list[str]) -> dict[str, str]:
    """The supplier's own name for each item: the one the owner remembered, else the name on that supplier's
    latest invoice line, else ours."""
    out = {}
    for iid in item_ids:
        it = db.one("SELECT coalesce(name_en, name_ar) n FROM items WHERE id=? AND org_id=?", (iid, ctx.org_id))
        if not it:
            raise NotFound("الصنف غير موجود")
        name = None
        if supplier_id:
            a = db.one("SELECT alias_raw FROM item_aliases WHERE org_id=? AND item_id=? AND supplier_key=? AND"
                       " source='manual' ORDER BY created_at DESC, rowid DESC LIMIT 1", (ctx.org_id, iid, supplier_id))
            if a:
                name = a["alias_raw"]
            else:
                ln = db.one("SELECT l.raw_name FROM purchase_invoice_lines l JOIN purchase_invoices p ON p.id=l.invoice_id"
                            " WHERE p.org_id=? AND p.supplier_id=? AND l.item_id=? AND p.status='approved'"
                            " AND trim(coalesce(l.raw_name,'')) NOT IN ('', 'x') ORDER BY p.approved_at DESC LIMIT 1",
                            (ctx.org_id, supplier_id, iid))
                name = ln["raw_name"] if ln else None
        out[iid] = (name or it["n"] or "").strip()
    return out


def remember_name(db: Database, ctx: Context, supplier_id: str, item_id: str, name: str) -> None:
    ctx.require("purchases.create")
    from ..catalog import service as cat
    if not db.one("SELECT 1 FROM suppliers WHERE id=? AND org_id=?", (str(supplier_id or ""), ctx.org_id)):
        raise NotFound("المورد غير موجود")
    if not db.one("SELECT 1 FROM items WHERE id=? AND org_id=?", (str(item_id or ""), ctx.org_id)):
        raise NotFound("الصنف غير موجود")
    name = str(name or "").strip()
    if not name or len(name) > 200:
        raise ValidationFailed("اكتب اسم الصنف عند المورد")
    with db.transaction():
        cat.add_alias(db, ctx, item_id, name, supplier_id=supplier_id, source="manual")
        # the newest remembered name is the one used: bump it when the same spelling is saved again
        from ..catalog.normalize import normalize_text
        db.execute("UPDATE item_aliases SET created_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), alias_raw=?, source='manual'"
                   " WHERE org_id=?"
                   " AND supplier_key=? AND alias_norm=?", (name, ctx.org_id, supplier_id, normalize_text(name)))
        audit.record(db, ctx, "purchase_order.supplier_name", "item", item_id,
                     after={"supplier_id": supplier_id, "name": name})


def _lines(db: Database, ctx: Context, supplier_id, lines) -> list[tuple[str, int, int]]:
    """[(supplier's name, packs, public price)] dearest first."""
    if not isinstance(lines, list) or not lines or len(lines) > MAX_LINES:
        raise ValidationFailed("الطلبية فاضية أو كبيرة")
    out = []
    for ln in lines:
        if not isinstance(ln, dict):
            raise ValidationFailed("بيانات الطلبية غير صحيحة")
        iid = str(ln.get("item_id") or "")
        packs = _packs(ln.get("packs"))
        it = db.one("SELECT public_price FROM items WHERE id=? AND org_id=?", (iid, ctx.org_id))
        if not it:
            raise NotFound("الصنف غير موجود")
        name = supplier_names(db, ctx, supplier_id, [iid])[iid]
        out.append((name, packs, int(it["public_price"] or 0)))
    out.sort(key=lambda x: (-x[2], x[0]))
    return out


def _supplier(db: Database, ctx: Context, sid) -> str:
    if not sid:
        return "بدون مورد سابق"
    s = db.one("SELECT name FROM suppliers WHERE id=? AND org_id=?", (str(sid), ctx.org_id))
    if not s:
        raise NotFound("المورد غير موجود")
    return s["name"]


def order_xlsx(db: Database, ctx: Context, groups: list) -> bytes:
    """One sheet per supplier: the supplier's name for the item + boxes, dearest first. Nothing is saved."""
    ctx.require("purchases.read")
    if not isinstance(groups, list) or not groups or len(groups) > 50:
        raise ValidationFailed("الطلبية فاضية")
    from openpyxl import Workbook
    from ..core.xlsx_safe import as_text
    wb = Workbook()
    wb.remove(wb.active)
    titles, total = set(), 0
    for g in groups:
        if not isinstance(g, dict):
            raise ValidationFailed("بيانات الطلبية غير صحيحة")
        sid = g.get("supplier_id") or None
        name = _supplier(db, ctx, sid)
        lines = _lines(db, ctx, sid, g.get("lines"))
        total += len(lines)
        if total > 2000:
            raise ValidationFailed("الطلبية كبيرة")
        base = "".join(ch for ch in name if ch not in "[]:*?/\\")[:28] or "مورد"
        title, k = base, 1
        while title.lower() in titles:
            k += 1
            title = f"{base[:31 - len(str(k)) - 1]}_{k}"
        titles.add(title.lower())
        ws = wb.create_sheet(title)
        ws.sheet_view.rightToLeft = True
        ws.append(["المورد", name])
        ws.append(["الصنف", "الكمية (علبة)"])
        for n, q, _ in lines:
            ws.append([n, q])
        ws.column_dimensions["A"].width = 40
    as_text(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def message_draft(db: Database, ctx: Context, supplier_id, lines) -> str:
    """A draft for the owner to copy and send himself. The program never sends it."""
    ctx.require("purchases.read")
    name = _supplier(db, ctx, supplier_id)
    org = db.one("SELECT name FROM organizations WHERE id=?", (ctx.org_id,))
    body = "\n".join(f"{i}) {n} × {q} علبة" for i, (n, q, _) in enumerate(_lines(db, ctx, supplier_id, lines), 1))
    return (f"السلام عليكم يا {name}،\nطلبية {org['name'] if org else 'الصيدلية'}:\n{body}\n\n"
            "لو فيه صنف ناقص أو له بديل، بلغونا قبل الطباعة من فضلكم، وابعتوا صورة الفاتورة قبل الطباعة.\nشكرًا.")
