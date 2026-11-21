"""Shift close helpers (update 2): the float target per denomination (U2-30), which notes stay in the drawer at
the close, the closing report's extra lines (U2-28) and «دوّر على الفرق» (U2-29).

The float target is one JSON value in org_settings (key 'float_target'): {denomination piasters: amount
piasters}; coins (1 EGP and below) share one amount under the key "coins". No schema change.
"""
from __future__ import annotations

import json

from ..core import audit
from ..core.errors import NotFound, ValidationFailed
from ..core.numbers import whole_number
from ..core.permissions import Context
from ..db import Database
from .cash import DENOMINATIONS

COINS = (100, 50, 25)                     # 1 EGP, 50 and 25 piasters: one target for all coins
NOTES = tuple(d for d in DENOMINATIONS if d not in COINS)
# the owner's own float (21/11): 100s 300, 50s 300, 20s 200, 10s 200, 5s 100, coins 50 = 1,150 EGP
DEFAULT_TARGET = {"10000": 30000, "5000": 30000, "2000": 20000, "1000": 20000, "500": 10000, "coins": 5000}
KEYS = tuple(str(d) for d in NOTES) + ("coins",)
MAX_TOTAL = 10_000_00                     # 10,000 EGP: a float bigger than that is a typo


def get_target(db: Database, org_id: str) -> dict:
    r = db.one("SELECT value FROM org_settings WHERE org_id=? AND key='float_target'", (org_id,))
    try:
        v = json.loads(r["value"]) if r else None
    except ValueError:
        v = None
    out = {k: 0 for k in KEYS}
    out.update({k: int(x) for k, x in (v or DEFAULT_TARGET).items() if k in KEYS})
    return out


def save_target(db: Database, ctx: Context, target: dict) -> dict:
    """Owner / manager: how much of each note to keep in the drawer for the next shift."""
    ctx.require("cash.manage")
    if not isinstance(target, dict) or set(target) - set(KEYS):
        raise ValidationFailed("فئات غير صحيحة")
    clean = {}
    for k in KEYS:
        try:
            v = whole_number(target.get(k, 0) or 0)
        except (TypeError, ValueError):
            raise ValidationFailed("المبلغ لازم رقم بالقرش")
        if v < 0:
            raise ValidationFailed("المبلغ مايكونش بالسالب")
        step = 25 if k == "coins" else int(k)
        if v % step:                       # 150 EGP of 100-pound notes is impossible: refused, not rounded
            raise ValidationFailed(f"مبلغ فئة {int(k) // 100 if k != 'coins' else 'الفكة'} لازم يتقسم عليها",
                                   code="not_a_multiple")
        clean[k] = v
    if sum(clean.values()) > MAX_TOTAL:
        raise ValidationFailed("الفكة أكبر من 10,000 ج: راجع الأرقام")
    before = get_target(db, ctx.org_id)
    with db.transaction():
        db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?, 'float_target', ?)"
                   " ON CONFLICT(org_id, key) DO UPDATE SET value=excluded.value", (ctx.org_id, json.dumps(clean)))
        audit.record(db, ctx, "cash.float_target", "org", ctx.org_id, before=before, after=clean)
    return clean


def split_float(counted: dict, target: dict) -> dict:
    """From the counted notes {denomination: count} and the target, which notes stay and which go to the
    treasury. Never keeps more notes than were counted; coins fill their shared amount largest first."""
    keep, move = {}, {}
    for d in NOTES:
        n = int(counted.get(str(d), 0) or 0)
        k = min(n, target.get(str(d), 0) // d)
        keep[str(d)], move[str(d)] = k, n - k
    left = target.get("coins", 0)
    for d in COINS:
        n = int(counted.get(str(d), 0) or 0)
        k = min(n, left // d)
        left -= k * d
        keep[str(d)], move[str(d)] = k, n - k
    kept = sum(int(d) * n for d, n in keep.items())
    return {"keep": {d: n for d, n in keep.items() if n}, "move": {d: n for d, n in move.items() if n},
            "float_left": kept, "to_treasury": sum(int(d) * n for d, n in move.items()),
            "short": max(0, sum(target.values()) - kept)}


# --------------------------------------------------------------------------------- report extras (U2-28)
def report_extras(db: Database, ctx: Context, s: dict) -> dict:
    """Credit sales with the customer, discounts by seller, the notes kept / moved at the close."""
    from .cash import _sales_of
    where, args = _sales_of(s)
    where, args = f"+x.org_id=? AND {where}", (ctx.org_id, *args)      # tenant filter on every read (Codex P1); + keeps the shift index
    credit = [dict(r) for r in db.all(
        "SELECT x.sale_no, x.on_credit, c.name customer FROM sales x LEFT JOIN customers c ON c.id=x.customer_id"
        f" WHERE {where} AND x.on_credit>0 ORDER BY x.sale_no", args)]
    disc = [dict(r) for r in db.all(
        "SELECT u.full_name name, count(*) n, sum(x.discount_total) total FROM sales x LEFT JOIN users u"
        f" ON u.id=x.created_by WHERE {where} AND x.discount_total>0 GROUP BY x.created_by ORDER BY 3 DESC", args)]
    kept = None
    a = db.one("SELECT after_json FROM audit_log WHERE org_id=? AND entity_type='cash_shift' AND entity_id=?"
               " AND action='cash.float_split' ORDER BY at DESC LIMIT 1", (ctx.org_id, s["id"]))
    if a:
        kept = json.loads(a["after_json"] or "{}")
    return {"credit_sales": credit, "discount_by_user": disc, "float_split": kept}


def split_for(counted: dict, target: dict, float_left: int | None = None) -> dict:
    """The owner's target split, or, when the closer typed his own float, the notes that make that amount exactly
    (largest first, then smallest first); when no notes make it, no note list (the amount only)."""
    sp = split_float(counted, target)
    if float_left is None or sp["float_left"] == float_left:
        return sp
    for order in (True, False):
        sp = split_float(counted, _target_for(counted, float_left, largest_first=order))
        if sp["float_left"] == float_left:
            return sp
    total = sum(int(d) * n for d, n in counted.items())
    return {"keep": {}, "move": {}, "float_left": float_left, "to_treasury": total - float_left, "short": 0, "exact": False}


def record_split(db: Database, ctx: Context, shift_id: str, counted: dict | None, float_left: int) -> None:
    """Inside the close transaction: which notes stayed, for the printed report."""
    if not counted:
        return
    sp = split_for(counted, get_target(db, ctx.org_id), float_left)
    audit.record(db, ctx, "cash.float_split", "cash_shift", shift_id, after={"keep": sp["keep"], "move": sp["move"],
                                                                              "float_left": float_left})


def _target_for(counted: dict, amount: int, *, largest_first: bool = True) -> dict:
    """A target that keeps `amount` from the counted notes, largest or smallest first."""
    t, left = {k: 0 for k in KEYS}, amount
    for d in sorted(DENOMINATIONS, reverse=largest_first):
        n = min(int(counted.get(str(d), 0) or 0), left // d)
        left -= n * d
        key = "coins" if d in COINS else str(d)
        t[key] += n * d
    return t


# ------------------------------------------------------------------------------ difference hunt (U2-29)
def difference_hunt(db: Database, ctx: Context, shift_id: str) -> dict:
    """Where a drawer difference usually comes from, listed for the owner: cash sales in piaster fractions no
    coin can pay, sales paid without cash that opened the drawer, sales whose payments do not add up to the
    total, manual drawer openings, and returns / voids / payouts made during the shift."""
    ctx.require("cash.see_expected")
    s = db.one("SELECT * FROM cash_shifts WHERE id=? AND org_id=?", (shift_id, ctx.org_id))
    if not s:
        raise NotFound("الشيفت غير موجود")
    s = dict(s)
    from .cash import _sales_of
    where, args = _sales_of(s)
    where, args = f"+x.org_id=? AND {where}", (ctx.org_id, *args)      # tenant filter on every read (Codex P1); + keeps the shift index
    sales = [dict(r) for r in db.all(
        "SELECT x.id, x.sale_no, x.kind, x.total, x.paid_cash, x.paid_card, x.paid_transfer, x.on_credit, x.completed_at"
        f" FROM sales x WHERE {where} ORDER BY x.sale_no", args)]     # the report's scope: later voids stay
    fractions = [x for x in sales if x["paid_cash"] % 25]
    # paid in cash on the invoice vs what really reached this drawer for it (the completion refuses paid != total,
    # so the useful check is the drawer's own movement)
    in_drawer = {r["ref_id"]: r["t"] for r in db.all(
        "SELECT ref_id, sum(amount) t FROM cash_movements WHERE shift_id=? AND org_id=? AND kind='sale' GROUP BY ref_id",
        (shift_id, ctx.org_id))}
    mismatch = [x for x in sales if x["kind"] != "delivery"           # a courier's cash arrives when it is handed in
                and x["paid_cash"] != in_drawer.get(x["id"], 0)]
    # from this shift's sales to their audit rows (the #113 pattern), and only pulses really sent to the drawer:
    # a failed pulse never opened it
    audits = [dict(r) for r in db.all(
        "SELECT x.id sale_id, x.completed_at, a.action, a.at, a.after_json FROM sales x CROSS JOIN audit_log a"
        " ON a.org_id=x.org_id AND a.entity_type='sale' AND a.entity_id=x.id"
        " WHERE +x.org_id=? AND x.shift_id=?", (ctx.org_id, shift_id))]
    opened = {a["sale_id"] for a in audits if a["action"] == "cash.drawer_auto"
              and json.loads(a["after_json"] or "{}").get("outcome") == "sent"}
    edited = sorted({a["sale_id"] for a in audits if a["completed_at"] and a["at"] > a["completed_at"]
                     and a["action"] not in ("sale.complete", "cash.drawer_auto", "sale.return", "sale.void")})
    edits = [x for x in sales if x["id"] in edited]
    noncash_open = [x for x in sales if x["id"] in opened and x["paid_cash"] == 0]
    manual = [dict(r) for r in db.all(
        "SELECT a.at, a.after_json, u.full_name by_name FROM audit_log a LEFT JOIN users u ON u.id=a.user_id"
        " WHERE a.org_id=? AND a.action='cash.drawer_open' AND a.entity_type='cash_shift' AND a.entity_id=? ORDER BY a.at",
        (ctx.org_id, shift_id))]
    for m in manual:
        m.update(json.loads(m.pop("after_json") or "{}"))
    moves = [dict(r) for r in db.all(
        "SELECT m.kind, m.amount, m.note, m.created_at, u.full_name by_name FROM cash_movements m LEFT JOIN users u"
        " ON u.id=m.created_by WHERE m.shift_id=? AND m.org_id=? AND m.kind IN ('refund','void','payout','deposit')"
        " ORDER BY m.created_at", (shift_id, ctx.org_id))]
    frac_total = sum(x["paid_cash"] % 25 for x in sales)          # the part no coin can pay
    hints = []
    if fractions:
        hints.append(f"{len(fractions)} فاتورة نقدي فيها قروش مالهاش عملة (زي 1249.01): الفرق ده طبيعي ومش سرقة")
    if noncash_open:
        hints.append(f"{len(noncash_open)} فاتورة اتدفعت ببطاقة أو تحويل أو آجل والدرج اتفتح: اتأكد إن محدش أخد منها فكة")
    if mismatch:
        hints.append(f"{len(mismatch)} فاتورة النقدي فيها مش زي اللي دخل الدرج")
    if edits:
        hints.append(f"{len(edits)} فاتورة اتعدلت بعد الدفع: راجعها")
    if manual:
        hints.append(f"الدرج اتفتح باليد {len(manual)} مرة: راجع الأسباب")
    if moves:
        hints.append(f"{len(moves)} حركة مرتجع أو إلغاء أو صرف أو إيداع في الشيفت")
    if not hints:
        hints.append("مفيش سبب ظاهر في السيستم: غالبًا فكة اتدّت غلط أو فلوس اتحطت في الدرج من غير ما تتسجل")
    return {"shift_id": shift_id, "difference": s["difference"], "fractions": fractions,
            "fractions_piasters": frac_total, "noncash_drawer_open": noncash_open, "mismatch": mismatch, "edited_after_payment": edits,
            "manual_openings": manual, "movements": moves, "hints": hints}
