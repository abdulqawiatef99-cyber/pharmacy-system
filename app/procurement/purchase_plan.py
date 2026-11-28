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
Discounts: each active supplier's latest purchase in the last year, across the program and the E-Stock archive.
The program wins on the same invoice day. Free units and the net price (including fixed margins) determine
the real discount. Unmapped archive suppliers are visible but cannot receive an order.
Discounts are the owner's to see (costs.read), as on the smart order (#402).
"""
from __future__ import annotations

import io
import json
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
from heapq import merge
from math import floor

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
    return Fraction(max(2 * sum(units[-3:]), sum(units[-6:])), 6)


def packs_for(avg, days: int, *, stock: int, arrived: int, on_order: int, pack: int) -> int:
    avg = avg if isinstance(avg, Fraction) else Fraction(avg)
    cover = max(int(stock), 0) + int(arrived) + int(on_order)
    denominator = 30 * avg.denominator
    need = avg.numerator * days - cover * denominator
    return max(0, -(-need // (denominator * max(int(pack), 1))))


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
    if st & {"sent", "partial"}:
        return "partial"                # a partial line stays open: a later delivery can still be ticked (Codex P2)
    return "done"


def _shape(o: dict) -> dict:
    return {**o, "status": _status(o)}


def list_orders(db: Database, ctx: Context) -> list[dict]:
    ctx.require("purchases.read")
    return [_shape(o) for o in reversed(_load(db, ctx.org_id)) if o.get("branch_id") == ctx.branch_id]


def _invoiced(db: Database, ctx: Context, since: str) -> tuple[dict[str, str], dict[tuple[str, str], list]]:
    """({item: the latest time any supplier's invoice (draft or approved) with it was created},
        {(supplier, item): [[created_at, packs paid + free], ...] oldest first}) for invoices since `since`."""
    latest, per = {}, {}
    for r in db.all("SELECT p.supplier_id s, l.item_id i, p.created_at t, coalesce(l.qty,0) + coalesce(l.bonus_qty,0) n"
                    " FROM purchase_invoices p JOIN purchase_invoice_lines l ON l.invoice_id=p.id WHERE p.org_id=?"
                    " AND p.branch_id=? AND p.status IN ('draft','approved') AND p.created_at>=? AND l.item_id IS NOT NULL"
                    " ORDER BY p.created_at, l.line_no", (ctx.org_id, ctx.branch_id, since)):
        if r["t"] > latest.get(r["i"], ""):
            latest[r["i"]] = r["t"]
        per.setdefault((r["s"], r["i"]), []).append([r["t"], max(int(r["n"]), 0)])
    return latest, per


def _covered(invoices: list, since: str, need: int) -> int:
    """Packs of `need` that invoices entered since `since` cover, oldest invoice first; what they cover is used up,
    so one invoice never clears two orders (Codex P2)."""
    got = 0
    for cell in invoices:
        if got >= need:
            break
        if cell[0] >= since and cell[1] > 0:
            take = min(cell[1], need - got)
            cell[1] -= take
            got += take
    return got


def _tracked(db: Database, ctx: Context, today: date, pack: dict) -> tuple[dict, dict, list]:
    """({item: on order units}, {item: arrived-not-entered units}, [ask-about lines])."""
    keep = _now(today - timedelta(days=KEEP_DAYS))
    orders = [o for o in _load(db, ctx.org_id) if o.get("branch_id") == ctx.branch_id
              and str(o.get("created_at", "")) >= keep]          # past retention: never counted, write or not (Codex P2)
    if not orders:
        return {}, {}, []
    orders.sort(key=lambda o: str(o["created_at"]))            # the oldest order takes an invoice first
    latest, per = _invoiced(db, ctx, orders[0]["created_at"])
    old = _now(today - timedelta(days=SENT_DAYS))
    on_order, arrived, ask = {}, {}, []
    for o in orders:
        for ln in o.get("lines", []):
            iid, sid, p = ln["item_id"], o["supplier_id"], pack.get(ln["item_id"])
            if p is None:
                continue
            invs = per.get((sid, iid), [])
            if ln["status"] == "sent":
                left = int(ln["packs"]) - _covered(invs, o["created_at"], int(ln["packs"]))
                if left > 0 and o["created_at"] >= old:             # nobody marked it for two weeks: not counted
                    on_order[iid] = on_order.get(iid, 0) + left * p
            elif ln["status"] in ("arrived", "partial"):
                # what came is counted until its invoice is entered (before or after the line was ticked)
                got = int(ln.get("got") or 0)
                left = got - _covered(invs, o["created_at"], got)
                if left > 0:
                    arrived[iid] = arrived.get(iid, 0) + left * p
                if latest.get(iid, "") >= (ln.get("at") or o["created_at"]):
                    continue                    # bought since (here or elsewhere): nothing left to ask about
                if ln["status"] == "partial":
                    ask.append({"item_id": iid, "label": ln.get("label"), "supplier": o.get("supplier"),
                                "missing_packs": int(ln["packs"]) - got, "why": "partial"})
            elif ln["status"] == "na":
                if latest.get(iid, "") >= o["created_at"]:
                    continue                    # bought elsewhere since
                ask.append({"item_id": iid, "label": ln.get("label"), "supplier": o.get("supplier"),
                            "missing_packs": int(ln["packs"]), "why": "na"})
    return on_order, arrived, ask


# ------------------------------------------------------------------ the plan
def _live_purchases(db: Database, ctx: Context, since: str, until: str, sups: dict, item_ids):
    params = {"org": ctx.org_id, "branch": ctx.branch_id, "since": since, "until": until,
              "items": None if item_ids is None else json.dumps(list(item_ids))}
    # Drive the join by invoice and use the line's invoice index, even when there are many lines in the org.
    for r in db.all("""
            WITH invoices AS MATERIALIZED (
                SELECT id, supplier_id, approved_at, coalesce(nullif(invoice_date, ''), substr(approved_at,1,10)) day
                FROM purchase_invoices WHERE org_id=:org AND branch_id=:branch AND status='approved'
            )
            SELECT l.item_id, p.supplier_id, p.id inv, p.day, l.qty, l.bonus_qty, l.public_price, l.discount_bp,
                   l.unit_net_price + l.distributor_margin unit_net_price, l.raw_name, l.match_status, l.match_method
            FROM invoices p CROSS JOIN purchase_invoice_lines l ON l.invoice_id=p.id
            WHERE p.day BETWEEN :since AND :until AND +l.org_id=:org AND l.item_id IS NOT NULL AND l.qty>0
              AND (:items IS NULL OR l.item_id IN (SELECT value FROM json_each(:items)))
            ORDER BY p.day DESC, p.approved_at DESC, p.id DESC, l.line_no
            """, params):
        sup = sups.get(r["supplier_id"])
        yield {"item_id": r["item_id"], "supplier_id": r["supplier_id"], "supplier": sup["name"] if sup else None,
               "date": r["day"], "qty": r["qty"], "bonus": r["bonus_qty"] or 0, "public_price": r["public_price"],
               "unit_net_price": r["unit_net_price"], "source": "program", "inv": r["inv"], "raw_name": r["raw_name"],
               "active": bool(sup and sup["is_active"]),
               "discount_bp": real_discount_bp(r["public_price"], r["unit_net_price"], r["qty"],
                                              r["bonus_qty"], r["discount_bp"]),
               "unsure": r["match_status"] == "suggested" or
               (r["match_method"] == "fuzzy" and r["match_status"] != "confirmed")}


def _archive_purchases(db: Database, ctx: Context, since: str, until: str, sups: dict, item_ids):
    from ..dataimport import estock_history as eh
    from ..dataimport.estock_db import piasters
    from .demand import _archive_branch_ok, _id

    if not eh.archive_path(db) or not _archive_branch_ok(db, ctx):
        return
    try:
        con = eh._con(db, ctx)
    except NotFound:
        return                         # no archive for this organization
    with closing(con):
        a = eh._Arch(con)
        head, lines = "Purchase_header", "Purchase_details"
        if not (a.has(head) and a.has(lines)):
            return
        items = a.map("items")
        if item_ids is not None:
            items = {eid: iid for eid, iid in items.items() if iid in item_ids}
        if not items:
            return
        suppliers = a.map("suppliers")
        names = {}
        if a.has("Vendor"):
            names = {_id(r["id"]): eh._txt(r["name"]) for r in a.rows(
                f'SELECT {a.col("Vendor", "vendor_id")} id,'
                f" coalesce(nullif(trim({a.col('Vendor', 'vendor_name_ar')}), ''),"
                f" nullif(trim({a.col('Vendor', 'vendor_name_en')}), ''),"
                f' {a.col("Vendor", "vendor_code")}) name FROM "e_Vendor"')}
        # Use the invoice day even when entry into E-Stock took place on a different day.
        entered = a.col(head, "insert_date", "h")
        bill = a.col(head, "bill_date", "h")
        at = f"coalesce(nullif(trim({bill}), ''), {entered})"
        inv = a.col(head, "purchase_id", "h")
        pid = a.col(lines, "product_id", "l")
        # Exported IDs are text. Match the two numeric renderings and ignore padding before reading a quote.
        product_ids = json.dumps([raw for eid in items for raw in (eid, eid + ".0")])
        sql = f"""SELECT {pid} pid, {inv} inv, substr({at},1,10) day, {a.col(head, "vendor_id", "h")} vid,
                         {a.col(lines, "amount", "l")} qty, {a.col(lines, "bouns", "l")} bonus,
                         {a.col(lines, "sell_price", "l")} public, {a.col(lines, "buy_price", "l")} net
                  FROM "e_Purchase_details" l JOIN "e_Purchase_header" h
                    ON {a.col(lines, "purchase_id", "l")}={inv}
                  WHERE trim({pid}) IN (SELECT value FROM json_each(?)) AND substr({at},1,10) BETWEEN ? AND ?
                  ORDER BY day DESC, replace({at}, ' ', 'T') DESC, {entered} DESC, {inv} DESC, l.rowid"""
        product_prices = None
        for r in con.execute(sql, (product_ids, since, until)):
            qty, bonus = eh._dec(r["qty"]), eh._dec(r["bonus"]) or 0
            if qty is None or qty <= 0 or bonus < 0:
                continue
            public, net = eh._dec(r["public"]), eh._dec(r["net"])
            if (public is None or public <= 0) and a.has("Products"):
                if product_prices is None:
                    product_prices = {_id(row["id"]): row["price"] for row in a.rows(
                        f'SELECT {a.col("Products", "product_id")} id, {a.col("Products", "sell_price")} price'
                        ' FROM "e_Products"')}
                public = eh._dec(product_prices.get(_id(r["pid"])))
            # The discount comes from the exact prices (0.8549 against 1.0000 is 14.51%, not 15.00%). Only the
            # prices shown next to it are rounded to the piaster, and the discount is rounded once, at the end.
            exact_public = public if public is not None and public > 0 else None
            exact_net = net if net is not None and net >= 0 else None
            public = piasters(exact_public) if exact_public is not None else None
            net = piasters(exact_net) if exact_net is not None else None
            disc = None
            if public and exact_net is not None:  # a list price under half a piaster has no discount, as before
                ratio = Fraction(exact_net) * Fraction(qty) / (Fraction(exact_public) * Fraction(qty + bonus))
                disc = 10000 - floor(ratio * 10000 + Fraction(1, 2))  # half up, same rule as real_discount_bp
            vid = _id(r["vid"])
            sid = suppliers.get(vid)
            sup = sups.get(sid)
            yield {"item_id": items[_id(r["pid"])], "supplier_id": sid if sup else None,
                   "supplier": sup["name"] if sup else names.get(vid), "estock_supplier_id": vid,
                   "date": r["day"], "qty": int(qty) if qty == int(qty) else str(qty),
                   "bonus": int(bonus) if bonus == int(bonus) else str(bonus), "public_price": public,
                   "unit_net_price": net, "source": "estock", "inv": r["inv"], "raw_name": None,
                   "active": bool(sup and sup["is_active"]), "discount_bp": disc, "unsure": False}


def _purchases(db: Database, ctx: Context, today: date, sups: dict, item_ids=None) -> tuple[dict, dict]:
    """Latest purchase per item and two distinct invoices per supplier, newest first."""
    if item_ids is not None and not item_ids:
        return {}, {}
    since, until = (today - timedelta(days=HISTORY_DAYS)).isoformat(), today.isoformat()
    live = _live_purchases(db, ctx, since, until, sups, item_ids)
    archive = _archive_purchases(db, ctx, since, until, sups, item_ids)
    last, per = {}, {}
    for rec in merge(live, archive, key=lambda r: (r["date"], r["source"] == "program"), reverse=True):
        iid = rec.pop("item_id")
        last.setdefault(iid, rec)
        sid = rec["supplier_id"]
        if sid is None and rec["source"] == "program":
            continue
        key = ("program", sid) if sid is not None else ("estock", rec["estock_supplier_id"])
        history = per.setdefault(iid, {}).setdefault(key, [])
        if not history or (len(history) == 1 and (history[0]["source"], history[0]["inv"]) !=
                           (rec["source"], rec["inv"])):
            history.append(rec)
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
    items = db.all("SELECT i.id, i.code, i.name_en, i.name_ar, i.pack_size, i.public_price, i.is_imported, i.shortage_at,"
                   " coalesce(i.is_drug, 1) drug, c.mark FROM items i LEFT JOIN categories c ON c.id=i.category_id"
                   " WHERE i.org_id=? AND i.status='active' AND i.is_service=0", (o,))
    pack = {r["id"]: max(int(r["pack_size"] or 1), 1) for r in items}
    sent, arrived_t, ask = _tracked(db, ctx, today, pack)
    see = ctx.can("costs.read")
    needed, counts = [], {"regular": 0, "semi": 0, "irregular": 0, "dead": 0, "hidden": 0}
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
        month_qty = packs_for(avg, 30, stock=on_hand, arrived=arrived, on_order=on_order, pack=pk)
        recent = any(u > 0 for u in units[-3:])
        shown = asked or (cls in ("regular", "semi") and recent and month_qty > 0)
        if not shown:
            if month_qty <= 0:
                continue
            counts["hidden"] += 1
            if scope == "default":
                continue
        qty = {str(n): packs_for(avg, n, stock=on_hand, arrived=arrived, on_order=on_order, pack=pk)
               for n in PERIODS[:-1]}
        qty["30"] = month_qty
        if asked and max(on_hand, 0) + arrived + on_order <= 0:
            qty = {k: max(v, 1) for k, v in qty.items()}     # a customer asked and nothing is coming: one box
        needed.append((it, pk, asked, units, cls, avg, expired, on_hand, arrived, on_order, qty, shown))

    last, per = _purchases(db, ctx, today, sups, {it["id"] for it, *_ in needed})
    rows = []
    for it, pk, asked, units, cls, avg, expired, on_hand, arrived, on_order, qty, shown in needed:
        iid = it["id"]
        counts[cls] += 1
        side = []
        for recs in per.get(iid, {}).values():
            rec = recs[0]
            s = sups.get(rec["supplier_id"]) or {}
            side.append({k: rec.get(k) for k in ("supplier_id", "supplier", "active", "discount_bp", "date",
                                                 "unsure", "raw_name", "source", "estock_supplier_id", "bonus",
                                                 "unit_net_price")} |
                        {"prev_bp": recs[1]["discount_bp"] if len(recs) > 1 else None, "kind": s.get("supplier_type")})
        best = None
        if see:
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
        unsure = next((x for x in (best, lst) if x and x.get("unsure")), None)   # the recommended one too (Codex P2)
        if unsure:
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
            # Use a cost-independent tiebreaker when suppliers share a display name.
            side = sorted(({**x, "discount_bp": None, "prev_bp": None, "bonus": None, "unit_net_price": None} for x in side),
                          key=lambda x: (x["supplier"] or "", x["supplier_id"] or "", x["estock_supplier_id"] or ""))
            lst = {k: lst[k] for k in ("supplier_id", "supplier", "date", "active")} if lst else None
        unsure_raw = unsure["raw_name"] if unsure else None
        side = [{k: v for k, v in x.items() if k not in ("unsure", "raw_name")} for x in side]
        if best:
            best = {k: v for k, v in best.items() if k not in ("unsure", "raw_name")}
        rows.append({"unsure_raw": unsure_raw, "item_id": iid, "code": it["code"], "label": it["name_en"] or it["name_ar"], "pack_size": pk,
                     "public_price": price, "on_hand": on_hand, "arrived": arrived, "on_order": on_order,
                     "expired": expired, "units": units, "avg_month": round(float(avg), 2), "class": cls,
                     "qty": qty, "asked": asked, "shown": shown, "supply": not it["drug"], "flags": flags,
                     "suppliers": side, "best": best, "last": lst, "reasons": reasons})
    rows.sort(key=lambda r: (not r["asked"], -(r["public_price"] or 0) * r["qty"]["30"], r["label"] or ""))
    ids = {r["item_id"] for r in rows}
    no_sup = [{"item_id": r["item_id"], "label": r["label"], "why": "no_supplier"} for r in rows
              if r["shown"] and not any(x["active"] for x in r["suppliers"])]
    ask_about = no_sup + [a for a in ask if a["item_id"] in pack]
    check = [{"item_id": r["item_id"], "label": r["label"], "raw_name": r["unsure_raw"],
              "pack_size": r["pack_size"]} for r in rows if r["unsure_raw"] is not None]
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
