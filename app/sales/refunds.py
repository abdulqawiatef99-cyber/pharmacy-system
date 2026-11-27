"""Remaining tender capacity and integer allocation for an original-tender return."""
from ..core.errors import Conflict
from ..finance import money
from .attachments import transfer_state

METHODS = ("card", "transfer", "cash", "credit")
ALLOCATION_POLICIES = ("original_order", "proportional")


def allocation_policy(db, org):
    row = db.one("SELECT value FROM org_settings WHERE org_id=? AND key='refund_allocation'", (org,))
    return row["value"] if row and row["value"] in ALLOCATION_POLICIES else ALLOCATION_POLICIES[0]


def original_capacity(db, ctx, sale, rounding_left):
    org, sid = ctx.org_id, sale["id"]
    received = {(r["method"], r["channel_id"]): r["amount"] for r in db.all(
        "SELECT method, channel_id, sum(amount) amount FROM sale_payments WHERE org_id=? AND sale_id=?"
        " AND method IN ('card','transfer') GROUP BY method, channel_id ORDER BY min(rowid)", (org, sid))}
    if sale["paid_card"] and not any(m == "card" for m, _ in received):
        received["card", None] = sale["paid_card"]
    received["cash", None] = sale["paid_cash"] or 0
    received["credit", None] = (sale["on_credit"] or 0) if sale["customer_id"] else 0

    used = {(r["refund_method"], r["channel_id"]): r["amount"] for r in db.all(
        "SELECT refund_method, channel_id, sum(total) amount FROM sale_returns WHERE org_id=? AND sale_id=?"
        " GROUP BY refund_method, channel_id", (org, sid))}
    caps = {key: max(0, amount - used.get(key, 0)) for key, amount in received.items()}

    # Older explicit card refunds have a NULL channel. Consume them against the original card rows in
    # payment order, which is also the order used by the original account reversal.
    card_unknown = used.get(("card", None), 0) if ("card", None) not in received else 0
    cash_other = max(0, used.get(("cash", None), 0) - received["cash", None])
    for key in caps:
        if key[0] == "card":
            take = min(caps[key], card_unknown + cash_other)
            caps[key] -= take
            from_card = min(take, card_unknown)
            card_unknown -= from_card
            cash_other -= take - from_card

    wallets = [key for key in caps if key[0] == "transfer"]
    if wallets:
        verified = transfer_state(db, org, sid, sale["paid_transfer"])["verified"]
        # A partial review does not identify which fresh wallet was confirmed. Only the guaranteed
        # amount on each channel is spendable. An exchange offset is already confirmed on its channel.
        short = sale["paid_transfer"] - verified
        offsets = {r["channel_id"]: r["amount"] for r in db.all(
            "SELECT channel_id, sum(verified_offset) amount FROM sale_payments WHERE org_id=? AND sale_id=?"
            " AND method='transfer' GROUP BY channel_id", (org, sid))}
        legacy = money.legacy_wallet_refund_reserve(db, ctx, sid)
        for key in wallets:
            caps[key] = max(0, caps[key] - min(short, received[key] - offsets.get(key[1], 0))
                            - legacy - cash_other)

    # Keep the unpaid positive rounding available for the return that gives it back.
    caps["cash", None] = max(0, caps["cash", None] - rounding_left)
    return [(key, cap) for method in METHODS for key, cap in caps.items() if key[0] == method]


def allocate(db, ctx, sale, amount, rounding, rounding_left, *, only=None):
    from .service import egp
    caps = original_capacity(db, ctx, sale, rounding_left)
    if only:
        caps = [(key, cap) for key, cap in caps if key[0] == only]
    total = sum(cap for _, cap in caps)
    if amount > total:
        limits = {method: sum(cap for (m, _), cap in caps if m == method) for method in METHODS}
        raise Conflict(f"المبلغ المتاح على وسائل الدفع الأصلية {egp(total)} ج "
                       f"(بطاقة {egp(limits['card'])}، محفظة {egp(limits['transfer'])}، "
                       f"نقدي {egp(limits['cash'])}، حساب العميل {egp(limits['credit'])})",
                       code="refund_original_limit", details={"refund": amount, **{
                           f"max_{method}": cap for method, cap in limits.items()}})
    # A positive cash rounding belongs to cash even if another tender would otherwise take it.
    fixed_cash = min(max(0, rounding), dict(caps).get(("cash", None), 0))
    left = amount - fixed_cash
    caps = [(key, cap - fixed_cash if key == ("cash", None) else cap) for key, cap in caps]
    shares = [0] * len(caps)
    if left and allocation_policy(db, ctx.org_id) == "proportional":
        basis = total - fixed_cash
        parts = [divmod(left * cap, basis) for _, cap in caps]
        shares = [whole for whole, _ in parts]
        order = sorted(range(len(caps)), key=lambda i: -parts[i][1])
        for i in order[:left - sum(shares)]:
            shares[i] += 1
    else:
        for i, (_, cap) in enumerate(caps):
            shares[i] = min(cap, left)
            left -= shares[i]
    return [{"method": method, "channel_id": channel, "amount": share + (fixed_cash if method == "cash" else 0)}
            for ((method, channel), _), share in zip(caps, shares)
            if share or (method == "cash" and rounding)]


def payments(db, org, return_id):
    """The rows of a receipt, including those of a historical single-tender return."""
    return [dict(r) for r in db.all(
        "SELECT r.id, r.refund_method method, r.total amount, r.channel_id, c.name channel"
        " FROM sale_returns r LEFT JOIN payment_channels c ON c.id=r.channel_id AND c.org_id=r.org_id"
        " WHERE r.org_id=? AND (r.id=? OR r.part_of=?) ORDER BY r.rowid", (org, return_id, return_id))]


def exchange_parts(parts, replacement_total):
    """Allocate the replacement offset in refund order and expose only the outstanding payout."""
    left = replacement_total
    for part in parts:
        offset = min(left, part["amount"])
        left -= offset
        yield part, offset


def exchange_due(parts, replacement_total):
    return [{"method": p["method"], "channel_id": p["channel_id"], "channel": p["channel"],
             "amount": p["amount"] - offset}
            for p, offset in exchange_parts(parts, replacement_total) if p["amount"] > offset]
