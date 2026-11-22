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


def suggestions(db: Database, ctx: Context, change: int) -> dict:
    ctx.require("sales.create")
    if isinstance(change, bool) or not isinstance(change, int) or change < 0 or change > 10_000_000:
        raise ValidationFailed("الباقي غير صحيح")
    from .service import local_today
    o, b = ctx.org_id, ctx.branch_id
    top = min(change, MAX_PRICE)
    items = []
    if top >= MIN_PRICE:
        today = local_today()
        since = (today - timedelta(days=SOLD_DAYS)).isoformat()
        # one whole pack at the shelf price: no loose units are cut for this
        rows = db.all(
            "SELECT i.id, coalesce(i.name_ar, i.name_en) name, i.name_en, i.public_price price, i.pack_size,"
            " (SELECT sum(sb.qty) FROM stock_balances sb JOIN batches bt ON bt.id=sb.batch_key WHERE sb.org_id=i.org_id"
            "   AND sb.branch_id=? AND sb.item_id=i.id AND sb.qty>0 AND bt.expiry_date>=?) stock,"
            " (SELECT min(bt.expiry_date) FROM stock_balances sb JOIN batches bt ON bt.id=sb.batch_key"
            "   WHERE sb.org_id=i.org_id AND sb.branch_id=? AND sb.item_id=i.id AND sb.qty>0 AND bt.expiry_date>=?) expiry,"
            " (SELECT c.avg_cost FROM item_costs c WHERE c.org_id=i.org_id AND c.branch_id=? AND c.item_id=i.id) avg_cost"
            " FROM items i LEFT JOIN categories cat ON cat.id=i.category_id AND cat.org_id=i.org_id"
            " WHERE i.org_id=? AND i.status='active' AND coalesce(i.is_service,0)=0 AND coalesce(i.rx_required,0)=0"
            " AND coalesce(cat.mark,'')<>'fridge' AND i.public_price BETWEEN ? AND ?",
            (b, today.isoformat(), b, today.isoformat(), b, o, MIN_PRICE, top))
        sold = {r["item_id"]: r["q"] for r in db.all(
            "SELECT l.item_id, sum(l.qty_base) q FROM sale_lines l JOIN sales s ON s.id=l.sale_id"
            " WHERE s.org_id=? AND s.branch_id=? AND s.status='completed' AND s.completed_at>=? GROUP BY l.item_id",
            (o, b, since))}
        cand = []
        for r in rows:
            if (r["stock"] or 0) < (r["pack_size"] or 1) or not r["expiry"]:
                continue                     # a whole pack on the shelf, not expired
            margin = None
            if r["avg_cost"] is not None:
                margin = r["price"] - Decimal(r["avg_cost"]) * (r["pack_size"] or 1) * 100
            cand.append((r, margin))
        cand.sort(key=lambda x: (x[0]["expiry"][:7],          # the month: within it, margin decides
                                 -(x[1] if x[1] is not None else Decimal(-10 ** 9)),
                                 -sold.get(x[0]["id"], 0), x[0]["name"] or ""))
        items = [{"id": r["id"], "name": r["name"], "name_en": r["name_en"], "price": r["price"],
                  "expiry": r["expiry"]} for r, _ in cand[:LIMIT]]
    last = db.one("SELECT counted_detail FROM cash_shifts WHERE org_id=? AND branch_id=? AND status='closed'"
                  " ORDER BY closed_at DESC LIMIT 1", (o, b))
    no_coins = False
    if last and last["counted_detail"]:
        try:
            no_coins = not int(json.loads(last["counted_detail"]).get("100") or 0)
        except (ValueError, TypeError, AttributeError):
            no_coins = False
    return {"change": change, "items": items, "no_pound_coins": no_coins}
