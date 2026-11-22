"""«مفيش فكة» (U2-20): when the drawer has no change for the customer, suggest a few small in-stock items whose
price fits in the change (1 to 10 EGP), so the customer takes an item instead of the coins. Ranked nearest
expiry first (it should leave the shelf), then the pharmacy's margin, then what sells fastest. Never a fridge
item, a prescription item, a service or anything without stock. The cost is used for the order only, never
returned. Also says whether the last closed shift was counted with no one-pound coins (the hint at payment)."""
from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

from ..core.errors import ValidationFailed
from ..core.permissions import Context
from ..db import Database

MIN_PRICE, MAX_PRICE = 100, 1000         # piasters: 1 to 10 EGP
LIMIT = 5
SOLD_DAYS = 30


def suggestions(db: Database, ctx: Context, change: int, sale_id: str | None = None) -> dict:
    """`sale_id`: the open invoice; units already on it are not offered again."""
    ctx.require("sales.create")
    if isinstance(change, bool) or not isinstance(change, int) or change < 0 or change > 10_000_000:
        raise ValidationFailed("الباقي غير صحيح")
    from .service import Sales, local_today
    o, b = ctx.org_id, ctx.branch_id
    top = min(change, MAX_PRICE)
    items = []
    if top >= MIN_PRICE and b:
        sales = Sales(db)                   # the sale's own stock rule: expired, recalled, hospital-owned left out
        since = (local_today() - timedelta(days=SOLD_DAYS)).isoformat()
        in_cart = {}
        if sale_id:
            in_cart = {r["item_id"]: r["q"] for r in db.all(
                "SELECT l.item_id, sum(l.qty_base) q FROM sale_lines l JOIN sales s ON s.id=l.sale_id"
                " WHERE s.id=? AND s.org_id=? GROUP BY l.item_id", (sale_id, o))}
        hidden = "" if ctx.can("items.hidden") else " AND coalesce(i.hidden,0)=0"
        # candidates: a price that could fit (the item's, or a batch's own price) and something on the shelf
        rows = db.all(
            "SELECT i.id, coalesce(i.name_ar, i.name_en) name, i.name_en, i.public_price, i.pack_size,"
            " (SELECT c.avg_cost FROM item_costs c WHERE c.org_id=i.org_id AND c.branch_id=? AND c.item_id=i.id) avg_cost"
            " FROM items i LEFT JOIN categories cat ON cat.id=i.category_id AND cat.org_id=i.org_id"
            " WHERE i.org_id=? AND i.status='active' AND coalesce(i.is_service,0)=0 AND coalesce(i.rx_required,0)=0"
            " AND coalesce(cat.mark,'')<>'fridge'" + hidden +
            " AND EXISTS (SELECT 1 FROM stock_balances sb LEFT JOIN batches bt ON bt.id=nullif(sb.batch_key,'')"
            "   WHERE sb.org_id=i.org_id AND sb.branch_id=? AND sb.item_id=i.id AND sb.qty>0"
            "   AND coalesce(bt.public_price, i.public_price) BETWEEN ? AND ?)",
            (b, o, b, MIN_PRICE, top))
        sold = {r["item_id"]: r["q"] for r in db.all(
            "SELECT l.item_id, sum(l.qty_base) q FROM sale_lines l JOIN sales s ON s.id=l.sale_id"
            " WHERE s.org_id=? AND s.branch_id=? AND s.status='completed' AND s.completed_at>=? GROUP BY l.item_id",
            (o, b, since))}
        cand = []
        for r in rows:
            ps = r["pack_size"] or 1
            if r["id"] in in_cart:
                continue          # already on the invoice: a merge into its line can reprice it (Codex P2 on #411)
            # FEFO, as the sale takes it: the first whole pack, in one batch, so it has one price
            start, first, pos = 0, None, 0
            for bt in sales._sellable(ctx, b, r["id"]):
                if pos <= start < pos + bt["qty"]:
                    first = bt if start + ps <= pos + bt["qty"] else None
                    break
                pos += bt["qty"]
            if first is None:
                continue
            # the price the sale charges: the batch's own price, else the item's (one whole pack, no loose units)
            price = first["public_price"] if first["public_price"] is not None else r["public_price"]
            if price is None or not MIN_PRICE <= price <= top:
                continue
            margin = None
            if r["avg_cost"] is not None:
                margin = price - Decimal(r["avg_cost"]) * ps * 100
            cand.append((r, price, first["expiry_date"], margin))
        cand.sort(key=lambda x: ((x[2] or "9999-99")[:7],           # the month: within it, margin decides
                                 -(x[3] if x[3] is not None else Decimal(-10 ** 9)),
                                 -sold.get(x[0]["id"], 0), x[0]["name"] or ""))
        items = [{"id": r["id"], "name": r["name"], "name_en": r["name_en"], "price": price, "expiry": exp}
                 for r, price, exp, _ in cand[:LIMIT]]
    last = db.one("SELECT counted_detail FROM cash_shifts WHERE org_id=? AND branch_id=? AND status='closed'"
                  " ORDER BY closed_at DESC LIMIT 1", (o, b))
    no_coins = False
    if last and last["counted_detail"]:
        try:
            no_coins = not int(json.loads(last["counted_detail"]).get("100") or 0)
        except (ValueError, TypeError, AttributeError):
            no_coins = False
    return {"change": change, "items": items, "no_pound_coins": no_coins}
