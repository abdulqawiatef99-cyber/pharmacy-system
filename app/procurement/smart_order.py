"""Smart order screen («اقتراح الطلبية», U2-36): what to order this week, ranked, with the noise hidden.

Read-only. It never orders anything and writes nothing to the database; the order list the owner builds lives
on the screen and leaves as an Excel file per supplier.

Movement class from the last 90 days (counted in sale invoices, so twenty units to one patient is not "fast"):
- fast (سريع)   : 12 or more sale invoices (about one a week or more)
- medium (متوسط): 4 to 11
- slow (بطيء)   : 1 to 3
- dead (راكد)   : none
Imported monthly history (consumption_history, no invoice count) counts one event per whole pack sold.
U4-1: the E-Stock sales archive counts too (demand.py): before the program's first sale only, returns netted,
one event per invoice; the manual monthly upload is then used only for days the archive does not cover.

Default view: fast / medium items that need ordering, plus any item someone marked short («مسجل ناقصًا», the
customers' requests) whose stock is empty. Slow and dead items show only when a customer asked; the rest is one
click away («اعرض الكل»). The old screen offered one box for an item sold once in 90 days, «متوسطة» everywhere.

Discounts: the real discount of a purchase counts the bonus as free units:
    real = 1 - (net price per pack x paid packs) / (public price per pack x (paid + free) packs)
kept in whole basis points (display and ranking only; no money is computed from it).
"""
from __future__ import annotations

import io
import math
from datetime import date, timedelta

from ..core.errors import NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database
from .service import Procurement

FAST, MEDIUM, SLOW = 12, 4, 1           # sale invoices in the window: fast >= 12, medium >= 4, slow >= 1, else dead
TOP_SELLERS = 50                        # «الأكثر مبيعًا»: the 50 items with most sale invoices
NEAR_EXPIRY_DAYS = 90
HISTORY_DAYS = 365                      # purchases used for last / best supplier
EXPENSIVE_DEFAULT = 30000               # piasters per pack: 300 EGP (owner's approval line in the order playbook)
MAX_ORDER_LINES = 2000
MAX_PACKS = 100000


def movement_class(events: int) -> str:
    if events >= FAST:
        return "fast"
    if events >= MEDIUM:
        return "medium"
    if events >= SLOW:
        return "slow"
    return "dead"


def real_discount_bp(public: int | None, net: int | None, qty: int | None, bonus: int | None,
                     shown_bp: int | None) -> int | None:
    """Real discount in basis points with the bonus as free units; the shown discount when the net price is missing."""
    qty, bonus = int(qty or 0), int(bonus or 0)
    if public and public > 0 and net is not None and qty > 0:
        num, den = net * qty * 10000, public * (qty + bonus)
        return 10000 - (2 * num + den) // (2 * den)          # half up, integers only
    if shown_bp is None:
        return None
    if qty > 0 and bonus > 0:                                # shown discount + bonus, no net price on the line
        paid = (10000 - shown_bp) * qty
        return 10000 - (2 * paid + (qty + bonus)) // (2 * (qty + bonus))
    return int(shown_bp)


def _official_bp(sup: dict | None, imported: bool) -> int | None:
    if not sup:
        return None
    v = sup["base_discount_imported_bp"] if imported else sup["base_discount_local_bp"]
    return int(v) if v else None


def smart_order(db: Database, ctx: Context, *, today: date | None = None, scope: str = "default",
                cover_days: int = 30, lead_days: int = 2, safety_days: int = 5, window_days: int = 90,
                expensive_from: int = EXPENSIVE_DEFAULT) -> dict:
    ctx.require("purchases.read")
    if scope not in ("default", "all"):
        raise ValidationFailed("نطاق غير صحيح")
    p = Procurement(db, today=today)
    today = p.today
    o, b = ctx.org_id, ctx.branch_id
    start = (today - timedelta(days=window_days)).isoformat()
    d = p._all_items(ctx, window_days)
    from . import demand
    arch, arch_from = demand.archive_window(db, ctx, f"{start}T00:00:00")
    events = {r["item_id"]: int(r["n"]) for r in db.all(
        "WITH w AS MATERIALIZED (SELECT item_id, ref_id FROM stock_movements WHERE org_id=? AND +branch_id=?"
        " AND movement_type='sale' AND created_at >= ?)"
        " SELECT item_id, count(DISTINCT coalesce(ref_id, '')) n FROM w GROUP BY item_id", (o, b, start))}
    soon = (today + timedelta(days=NEAR_EXPIRY_DAYS)).isoformat()
    near = {r["item_id"]: r["e"] for r in db.all(
        "SELECT sb.item_id, min(bt.expiry_date) e FROM stock_balances sb JOIN batches bt ON bt.id=sb.batch_key"
        " WHERE sb.org_id=? AND sb.branch_id=? AND sb.qty>0 AND bt.expiry_date IS NOT NULL AND bt.expiry_date>=?"
        " AND bt.expiry_date<=? GROUP BY sb.item_id", (o, b, today.isoformat(), soon))}
    sups = {r["id"]: dict(r) for r in db.all(
        "SELECT id, name, base_discount_local_bp, base_discount_imported_bp, is_active FROM suppliers WHERE org_id=?", (o,))}
    since = (today - timedelta(days=HISTORY_DAYS)).isoformat()
    last: dict[str, dict] = {}
    best: dict[str, dict] = {}
    per_sup: dict[tuple, bool] = {}
    for r in db.all(
            "SELECT l.item_id, p.supplier_id, coalesce(p.invoice_date, substr(p.approved_at,1,10)) day, p.approved_at,"
            " l.qty, l.bonus_qty, l.public_price, l.discount_bp, l.unit_net_price FROM purchase_invoice_lines l"
            " JOIN purchase_invoices p ON p.id=l.invoice_id WHERE p.org_id=? AND p.branch_id=? AND p.status='approved'"
            " AND l.item_id IS NOT NULL AND l.qty>0 AND p.approved_at>=? ORDER BY p.approved_at DESC, l.line_no",
            (o, b, since)):
        disc = real_discount_bp(r["public_price"], r["unit_net_price"], r["qty"], r["bonus_qty"], r["discount_bp"])
        sup = sups.get(r["supplier_id"])
        rec = {"supplier_id": r["supplier_id"], "supplier": sup["name"] if sup else None, "discount_bp": disc,
               "shown_bp": r["discount_bp"], "qty": r["qty"], "bonus": r["bonus_qty"] or 0, "date": r["day"],
               "active": bool(sup and sup["is_active"])}
        last.setdefault(r["item_id"], rec)
        key = (r["item_id"], r["supplier_id"])
        if key in per_sup or not r["supplier_id"]:
            continue                     # each supplier's latest price only: an old deal is not today's offer
        per_sup[key] = True              # marked before the discount check: an unknown latest discount still hides
        if disc is None:                 # the older deals (Codex P2 on #402)
            continue
        cur = best.get(r["item_id"])
        if sup and not sup["is_active"]:
            continue                     # a stopped supplier is not where the order goes
        if cur is None or disc > cur["discount_bp"]:
            best[r["item_id"]] = rec
    rows, counts = [], {"fast": 0, "medium": 0, "slow": 0, "dead": 0, "hidden": 0}
    horizon = lead_days + cover_days + safety_days
    for it in db.all("SELECT i.id, i.code, i.name_en, i.name_ar, i.pack_size, i.public_price, i.min_stock, i.is_imported,"
                     " i.shortage_at, i.rx_required, c.mark FROM items i LEFT JOIN categories c ON c.id=i.category_id"
                     " WHERE i.org_id=? AND i.status='active' AND i.is_service=0", (o,)):
        iid, pack = it["id"], max(int(it["pack_size"] or 1), 1)
        periods = d["periods"].get(iid, [])
        if arch_from:                    # the archive covers these days: never the manual upload as well
            periods = [x for x in periods if str(x["period_end"]) < arch_from]
        a_units, a_events = arch.get(iid, (0, 0))
        u = p._usage_of(d["sold"].get(iid, 0) + a_units, periods, window_days)
        total, expired = d["stock"].get(iid, (0, 0))
        on_hand, on_order = total - expired, d["on_order"].get(iid, 0)
        # imported months carry no invoice count: one event per pack sold inside the window (a strip counts)
        ev = events.get(iid, 0) + a_events + (math.ceil(u["history_qty"] / pack) if u["history_qty"] > 0 else 0)
        cls = movement_class(ev)
        asked = bool(it["shortage_at"])
        daily = u["daily"]
        need = max(daily * horizon, it["min_stock"] or 0) - on_hand - on_order
        packs = math.ceil(need / pack) if need > 0 else 0
        if asked and packs == 0 and on_hand + on_order <= 0:
            packs = 1                    # a customer asked and the shelf is empty: one box
        days_left = round(max(on_hand, 0) / daily, 1) if daily > 0 else None
        below_min = bool(it["min_stock"]) and on_hand + on_order < it["min_stock"]     # the owner's own rule
        shown = packs > 0 and (cls in ("fast", "medium") or below_min or (asked and on_hand + on_order <= 0))
        if not packs and not asked:
            continue
        if not shown:
            counts["hidden"] += 1
            if scope == "default":
                continue
        counts[cls] += 1
        bst, lst = best.get(iid), last.get(iid)
        official = _official_bp(sups.get(bst["supplier_id"]) if bst else None, bool(it["is_imported"]))
        flags = []
        price = it["public_price"]
        if price is not None and price >= expensive_from:
            flags.append("expensive")
        if it["mark"] == "fridge":
            flags.append("fridge")
        if official is not None and bst and bst["discount_bp"] < official:
            flags.append("below_official")
        reasons = []
        if daily > 0:
            reasons.append(f"بيع {ev} مرة في {window_days} يوم، حوالي {daily:.2f} وحدة في اليوم")
            reasons.append(f"الرصيد {on_hand} يكفي {days_left:.0f} يوم" if on_hand > 0 else "الرصيد صفر")
        if a_events:
            reasons.append(f"منها {a_events} فاتورة من أرشيف إي ستوك")
        if asked:
            reasons.append("مسجل ناقصًا (طلبه عميل)")
        if on_order:
            reasons.append(f"في الطريق (مسودات شراء): {on_order} وحدة")
        if expired:
            reasons.append(f"منتهي الصلاحية ومش محسوب في الرصيد: {expired} وحدة")
        if not shown:
            reasons.append("مخفي من العرض الأساسي: حركته " + {"slow": "بطيئة", "dead": "راكدة"}.get(cls, "قليلة"))
        rows.append({
            "item_id": iid, "code": it["code"], "label": it["name_en"] or it["name_ar"], "pack_size": pack,
            "public_price": price, "on_hand": on_hand, "on_order": on_order, "expired": expired,
            "events": ev, "sold": u["sold_in_system"] + int(round(u["history_qty"])), "daily": round(daily, 3),
            "days_left": days_left, "movement": cls, "suggested_packs": packs, "asked": asked,
            "near_expiry": near.get(iid), "top_rank": None, "shown": shown,
            "last": lst, "best": bst, "official_bp": official, "flags": flags, "reasons": reasons})
    for n, row in enumerate(sorted((x for x in rows if x["events"] > 0), key=lambda x: -x["events"])[:TOP_SELLERS]):
        row["top_rank"] = n
    # most urgent first: empty shelves a customer asked for, then the fewest days left, then the busiest
    if not ctx.can("costs.read"):
        # purchase discounts and bonuses are the owner's to see (as on the item card, app/catalog/card.py): the
        # others get the last supplier and date only (Codex P1 on #402)
        for row in rows:
            if row["last"]:
                row["last"] = {k: row["last"][k] for k in ("supplier_id", "supplier", "date", "active")}
            row["best"], row["official_bp"] = None, None
            row["flags"] = [f for f in row["flags"] if f != "below_official"]
    rows.sort(key=lambda r: (not (r["asked"] and r["on_hand"] <= 0),
                             r["days_left"] if r["days_left"] is not None else (0 if r["on_hand"] <= 0 else 1e9),
                             -r["events"]))
    used = sorted({(r["best"] or r["last"] or {}).get("supplier_id") for r in rows} - {None},
                  key=lambda s: sups[s]["name"] if s in sups else "")
    return {"rows": rows, "counts": counts, "suppliers": [{"id": s, "name": sups[s]["name"]} for s in used if s in sups],
            "expensive_from": expensive_from, "window_days": window_days, "estock_archive": bool(arch_from)}


def order_xlsx(db: Database, ctx: Context, groups: list) -> bytes:
    """The order the owner built, one sheet per supplier (item, code, barcode, boxes): to send or type into the
    supplier's ordering app. Nothing is saved."""
    ctx.require("purchases.read")
    if not isinstance(groups, list) or not groups:
        raise ValidationFailed("الطلبية فاضية")
    from openpyxl import Workbook
    from ..core.numbers import whole_number
    from ..core.xlsx_safe import as_text
    wb = Workbook()
    wb.remove(wb.active)
    n, titles = 0, set()
    for g in groups:
        if not isinstance(g, dict) or not isinstance(g.get("lines"), list):
            raise ValidationFailed("بيانات الطلبية غير صحيحة")
        sid = g.get("supplier_id") or None
        name = "بدون مورد سابق"
        if sid:
            s = db.one("SELECT name FROM suppliers WHERE id=? AND org_id=?", (str(sid), ctx.org_id))
            if not s:
                raise NotFound("المورد غير موجود")
            name = s["name"]
        base = "".join(ch for ch in name if ch not in "[]:*?/\\")[:28] or "مورد"
        title, k = base, 1
        while title.lower() in titles:              # Excel titles ignore case; the counter always moves on
            k += 1
            title = f"{base[:31 - len(str(k)) - 1]}_{k}"
        titles.add(title.lower())
        ws = wb.create_sheet(title)
        ws.sheet_view.rightToLeft = True
        ws.append(["المورد", name])
        ws.append(["الصنف", "الكود", "الباركود", "الكمية (علبة)"])
        for ln in g["lines"]:
            n += 1
            if n > MAX_ORDER_LINES or not isinstance(ln, dict):
                raise ValidationFailed("الطلبية كبيرة أو غير صحيحة")
            try:
                packs = whole_number(ln.get("packs"))
            except (TypeError, ValueError):
                raise ValidationFailed("الكمية لازم رقم صحيح")
            if not 1 <= packs <= MAX_PACKS:
                raise ValidationFailed("الكمية لازم من 1 لـ 100000 علبة")
            it = db.one("SELECT code, name_en, name_ar FROM items WHERE id=? AND org_id=?", (str(ln.get("item_id")), ctx.org_id))
            if not it:
                raise NotFound("الصنف غير موجود")
            bc = db.one("SELECT barcode FROM item_barcodes WHERE org_id=? AND item_id=? LIMIT 1", (ctx.org_id, str(ln["item_id"])))
            ws.append([it["name_en"] or it["name_ar"], it["code"], bc["barcode"] if bc else None, packs])
    as_text(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
