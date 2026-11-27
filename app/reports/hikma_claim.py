"""U3-3 Hikma claim («مطالبة مستشفى نور الحكمة») for any date range (a half month too), per branch: Shibin and
Mit Khalaf, each a customer of the pharmacy. The rules are the agreed ones (runbook `hikma-monthly-claim.md`):
- the pharmacy's own stock: the hospital pays the real cost + half the margin;
- the hospital's own stock (batches owned by the hospital, migration 0039): the pharmacy takes half the margin;
- the hospital's half of the margin is rounded once, on the total (half up), never per line;
- the advance (10,000 a branch by default) is taken off the period, the next advance is added.
Lines are per item, by value high → low. The period's sales are net of returns through the period end.
Returns made inside the period for sales of an earlier period are taken off on
their own line («يُخصم: مرتجعات عن مطالبات سابقة»). The totals are reconciled with the customer's account.

Read only. Owner / anyone with costs.read (the internal copy shows the cost). Money is integer piastres; cost
is summed in Decimal and rounded half up once per figure.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from ..core import audit
from ..core.errors import NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database

KEY = "hikma_claim"
BRANCHES = {"shibin": ("مستشفى نور الحكمة – فرع شبين", 50), "mitkhalaf": ("مستشفى نور الحكمة – فرع ميت خلف", 10)}
DEFAULT_ADVANCE = 1_000_000            # 10,000.00 EGP
OWNERS = ("pharmacy", "hospital")


def _p(d: Decimal) -> int:
    return int(d.quantize(Decimal("1"), ROUND_HALF_UP))


def _half(x: int) -> int:
    """Half of an integer amount of piastres, half up (the one rounding of the claim)."""
    return (x + 1) // 2 if x >= 0 else -((-x + 1) // 2)


def _due(owner: str, value: int, cost: int) -> int:
    base = value if owner == "pharmacy" else value - cost
    return base - _half(value - cost)


def _share(total: int, weights: list[int]) -> list[int]:
    """`total` over the weights, whole piastres that add up to `total` exactly, never capped by the weights
    (Codex P1 on #416: rounded unit prices can sum below the recorded refund). Largest remainders get the rest."""
    w = [max(0, x) for x in weights]
    whole = sum(w)
    if not w:
        return []
    if whole <= 0:
        return [total] + [0] * (len(w) - 1)
    base = [total * x // whole for x in w]
    rest = total - sum(base)
    for i in sorted(range(len(w)), key=lambda i: (-(total * w[i] % whole), i))[:rest]:
        base[i] += 1
    return base


def _by_owner(total: int, parts) -> dict[str, tuple[int, Decimal, list]]:
    """Split the exact booked value using the saved prices of the lots for each owner."""
    from ..sales.service import line_gross
    grouped = {owner: [(q, a) for q, a in parts if a["owner"] == owner and q] for owner in OWNERS}
    weights = [line_gross(grouped[owner]) for owner in OWNERS]
    values = _share(total, weights if any(weights) else [sum(q for q, _ in grouped[owner]) for owner in OWNERS])
    return {owner: (v, sum((Decimal(a["unit_cost"] or 0) * q * 100 for q, a in grouped[owner]), Decimal(0)),
                    grouped[owner]) for owner, v in zip(OWNERS, values)}


def _legacy_refund(before, kept) -> int:
    from ..sales.service import alloc_gross, kept_value, line_fixed
    allocs = [a for _, a in before]
    line = allocs[0]
    args = (line["discount_bp"], line["line_total"])
    fixed = line_fixed(line)
    return (kept_value(allocs, {a["id"]: q for q, a in before}, *args, price=alloc_gross, fixed=fixed)
            - kept_value(allocs, {a["id"]: q for q, a in kept}, *args, price=alloc_gross, fixed=fixed))


def settings(db: Database, org_id: str) -> dict:
    r = db.one("SELECT value FROM org_settings WHERE org_id=? AND key=?", (org_id, KEY))
    try:
        st = json.loads(r["value"]) if r else {}
    except ValueError:
        st = {}
    from ..sales.customers import CODE_SQL
    out = {}
    for k, (title, code) in BRANCHES.items():
        cid = (st.get(k) or {}).get("customer_id")
        if not cid:          # the E-Stock customer code (50 / 10) until the owner picks one
            row = db.one(f"SELECT c.id FROM customers c WHERE c.org_id=? AND {CODE_SQL}=?", (org_id, code))
            cid = row["id"] if row else None
        out[k] = {"title": title, "customer_id": cid, "advance": int((st.get(k) or {}).get("advance", DEFAULT_ADVANCE))}
    return out


def save_settings(db: Database, ctx: Context, branch: str, customer_id: str | None, advance: int) -> dict:
    ctx.require("settings.manage")
    if branch not in BRANCHES:
        raise ValidationFailed("الفرع غير معروف")
    if not isinstance(advance, int) or isinstance(advance, bool) or advance < 0:
        raise ValidationFailed("الدفعة المقدمة غير صحيحة")
    if customer_id is not None and not isinstance(customer_id, str):
        raise ValidationFailed("العميل غير صحيح")
    if customer_id and not db.one("SELECT 1 FROM customers WHERE id=? AND org_id=?", (customer_id, ctx.org_id)):
        raise NotFound("العميل غير موجود")
    r = db.one("SELECT value FROM org_settings WHERE org_id=? AND key=?", (ctx.org_id, KEY))
    try:
        st = json.loads(r["value"]) if r else {}
    except ValueError:
        st = {}
    before = dict(st)
    st[branch] = {"customer_id": customer_id or None, "advance": advance}
    with db.transaction():
        db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?,?,?) ON CONFLICT(org_id, key)"
                   " DO UPDATE SET value=excluded.value", (ctx.org_id, KEY, json.dumps(st)))
        audit.record(db, ctx, "settings.hikma_claim", "organization", ctx.org_id, before=before, after=st)
    return settings(db, ctx.org_id)


def _lines(db: Database, org: str, sale_ids: list[str], end: str) -> list[dict]:
    from ..sales.service import kept_at, line_fixed, outstanding_value, returns_history
    if not sale_ids:
        return []
    hist = returns_history(db, org, sale_ids, dated=True)
    out = []
    for k in range(0, len(sale_ids), 500):
        part = sale_ids[k:k + 500]
        lines = db.all(f"SELECT l.*, coalesce(i.name_en, i.name_ar) name, i.code FROM sale_lines l JOIN items i ON i.id=l.item_id"
                       f" WHERE l.org_id=? AND l.sale_id IN ({','.join('?' * len(part))})", (org, *part))
        for l in lines:
            allocs = [dict(a) for a in db.all(
                "SELECT a.rowid rid, a.*, coalesce(b.owner,'pharmacy') owner FROM sale_allocations a LEFT JOIN batches b"
                " ON b.id=a.batch_id AND b.org_id=? WHERE a.sale_line_id=?", (org, l["id"]))]
            if not allocs:
                continue
            events = hist.get(l["sale_id"], [])
            kept = kept_at(allocs, l["id"], events, end)
            if not any(kept.values()):
                continue
            fx = {"fixed": line_fixed(l)} if line_fixed(l) else {}
            history = [r for r in events if r["returned_at"] < end]
            value = outstanding_value(allocs, kept, l["discount_bp"], l["line_total"], l["id"], history, **fx)
            parts = [(kept[a["id"]], a) for a in allocs]
            for owner, (v, cost, owned) in _by_owner(value, parts).items():
                if not owned:
                    continue
                out.append({"item_id": l["item_id"], "name": l["name"], "code": l["code"], "owner": owner,
                            "qty": sum(q for q, _ in owned), "value": v, "cost": cost,
                            "no_cost": any(not a["unit_cost"] for _, a in owned)})
    return out


def claim(db: Database, ctx: Context, branch: str, date_from: str, date_to: str) -> dict:
    ctx.require("costs.read")
    ctx.require("sales.totals")
    from ..sales.service import local_day_bounds
    if branch not in BRANCHES:
        raise ValidationFailed("الفرع غير معروف")
    try:
        d1, d2 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except (TypeError, ValueError):
        raise ValidationFailed("اختر الفترة")
    if d2 < d1 or (d2 - d1).days > 92:
        raise ValidationFailed("الفترة من يوم لـ 3 شهور")
    org = ctx.org_id
    st = settings(db, org)[branch]
    cid = st["customer_id"]
    if not cid:
        raise ValidationFailed("اختار عميل الفرع ده الأول (مفيش عميل بكود إي ستوك المتفق عليه)", code="no_customer")
    lo, hi = local_day_bounds(d1)[0], local_day_bounds(d2)[1]
    sales = [r["id"] for r in db.all("SELECT id FROM sales WHERE org_id=? AND customer_id=? AND status='completed'"
                                     " AND completed_at>=? AND completed_at<?", (org, cid, lo, hi))]
    rows: dict[tuple, dict] = {}
    for x in _lines(db, org, sales, hi):
        g = rows.setdefault((x["owner"], x["item_id"]), {**x, "qty": 0, "value": 0, "cost": Decimal(0), "no_cost": False})
        g["qty"] += x["qty"]
        g["value"] += x["value"]
        g["cost"] += x["cost"]
        g["no_cost"] = g["no_cost"] or x["no_cost"]
    out = {}
    for owner in OWNERS:
        ls = sorted((r for (o, _), r in rows.items() if o == owner), key=lambda r: (-r["value"], r["name"] or ""))
        value = sum(r["value"] for r in ls)
        cost = _p(sum((r["cost"] for r in ls), Decimal(0)))
        margin = value - cost
        hospital_half = _half(margin)            # rounded once on the total
        due = _due(owner, value, cost)
        for r in ls:
            r["cost"] = _p(r["cost"])
            r["margin"] = r["value"] - r["cost"]
            r["margin_bp"] = (r["margin"] * 10000 // r["value"]) if r["value"] else 0
            r["below_cost"] = r["value"] <= r["cost"]
        out[owner] = {"lines": ls, "value": value, "cost": cost, "margin": margin, "hospital_half": hospital_half, "due": due}
    # returns made inside the period for sales of an earlier period: their due comes off, each unit by the rule of
    # the lot it came from (review P1): pharmacy lot = cost + half the margin, hospital lot = half the margin.
    # The units of a return are the line's newest lots first, the way the return took them (_take).
    back_rows = []
    pv = {owner: [0, Decimal(0)] for owner in OWNERS}
    period = [dict(r) for r in db.all(
        "SELECT coalesce(r.part_of,r.id) id, sum(r.total-r.rounding) total, r.created_at,"
        " s.id sid, s.sale_no, s.completed_at FROM sale_returns r"
        " JOIN sales s ON s.id=r.sale_id AND s.org_id=r.org_id WHERE r.org_id=? AND s.customer_id=?"
        " AND r.created_at>=? AND r.created_at<? AND s.completed_at<?"
        " GROUP BY coalesce(r.part_of,r.id), r.created_at, s.id", (org, cid, lo, hi, lo))]
    in_period = {r["id"]: r for r in period}
    # each sale's returns are replayed oldest first, from before the period too, so a lot taken by an earlier return
    # is never taken again (Codex P1 on #416); only the returns inside the period are counted
    from ..sales.service import line_gross, returns_history
    prior_sales = sorted({r["sid"] for r in period})
    hist = returns_history(db, org, prior_sales, dated=True)
    replay = []
    line_balances: dict[str, int] = {}
    for sid in prior_sales:
        consumed: dict = {}
        totals = {x["id"]: x["total"] for x in db.all(
            "SELECT coalesce(part_of,id) id, sum(total-rounding) total FROM sale_returns"
            " WHERE org_id=? AND sale_id=? GROUP BY 1", (org, sid))}
        for after in hist.get(sid, []):
            if after["returned_at"] < hi:
                replay.append((after, consumed, totals.get(after.get("return_id"), 0)))
    for after, consumed, ret_total in replay:
        r = in_period.get(after.get("return_id"))
        lines, refunds = after.get("lines") or {}, after.get("line_refunds") or {}
        per_line = []
        for lid, q in lines.items():
            q = int(q)
            al = [dict(x) for x in db.all(
                        "SELECT a.rowid rid, a.id, a.qty_base, a.unit_cost, a.unit_price, a.pack_price, a.pack_size,"
                        " l.line_total, l.discount_bp, l.discount_amount, l.invoice_share,"
                        " coalesce(b.owner,'pharmacy') owner"
                        " FROM sale_allocations a JOIN sale_lines l ON l.id=a.sale_line_id AND l.org_id=? LEFT JOIN batches b"
                        " ON b.id=a.batch_id AND b.org_id=l.org_id WHERE a.sale_line_id=? ORDER BY a.rowid DESC", (org, lid))]
            left, takes, before, kept = q, [], [], []
            for x in al:
                remaining = x["qty_base"] - consumed.get(x["rid"], 0)
                t = min(left, remaining)
                before.append((remaining, x))
                kept.append((remaining - t, x))
                if t > 0:
                    takes.append((t, x))
                    consumed[x["rid"]] = consumed.get(x["rid"], 0) + t
                    left -= t
            if takes:
                per_line.append((lid, takes, before, kept))
        if refunds:
            values = [int(refunds.get(lid, 0)) for lid, *_ in per_line]
        else:
            # Older audit entries have no line amounts. Replay their original discount rule
            # when it reconciles to the recorded refund, or allocate its exact total by value.
            values = [_legacy_refund(before, kept) for _, _, before, kept in per_line]
            if sum(values) != ret_total or any(v < 0 for v in values):
                weights = [line_gross(takes) for _, takes, _, _ in per_line]
                values = _share(ret_total, weights)
        for (lid, takes, before, kept), line_v in zip(per_line, values):
            booked = _by_owner(line_v, takes)
            line_total = takes[0][1]["line_total"]
            if line_total is not None:
                previous = line_balances.get(lid, line_total)
                line_balances[lid] = max(0, previous - line_v)
                if line_v <= previous and r is not None:
                    # Round each owner's share of the line balance, then take the difference. Separately
                    # splitting each partial refund can assign the same discount piastre to two owners.
                    opening, closing = _by_owner(previous, before), _by_owner(line_balances[lid], kept)
                    booked = {owner: (opening[owner][0] - closing[owner][0], cost, parts)
                              for owner, (_, cost, parts) in booked.items()}
            if r is not None:
                for owner, (v, cost, _) in booked.items():
                    pv[owner][0] += v
                    pv[owner][1] += cost
        if r is not None:
            back_rows.append({"sale_no": r["sale_no"], "sold_at": r["completed_at"], "returned_at": r["created_at"], "value": r["total"]})
    back_v = sum(v for v, _ in pv.values())
    back_c = _p(sum((c for _, c in pv.values()), Decimal(0)))
    opening = {owner: [0, Decimal(0)] for owner in OWNERS}
    for row in _lines(db, org, prior_sales, lo):
        opening[row["owner"]][0] += row["value"]
        opening[row["owner"]][1] += row["cost"]
    back_due = 0
    for owner, (value, cost) in opening.items():
        returned_value, returned_cost = pv[owner]
        # Round each half-margin on the owner totals before and after the returns. The
        # difference reverses the same liability when a partial return crosses periods.
        back_due += _due(owner, value, _p(cost)) - _due(owner, value - returned_value, _p(cost - returned_cost))
    back = back_rows
    due = out["pharmacy"]["due"] + out["hospital"]["due"] - back_due
    adv = st["advance"]
    # The customer's account for the period's own invoices, including refunds posted through the period end.
    # Cash and card payments are shown separately from the customer account.
    acct = {"debit": 0, "back": 0}
    paid = 0
    for k in range(0, len(sales), 500):
        part = sales[k:k + 500]
        q = ",".join("?" * len(part))
        row = db.one(f"SELECT coalesce(sum(CASE WHEN kind='credit_sale' THEN amount END),0) debit,"
                     f" coalesce(sum(CASE WHEN kind='void' THEN amount END),0) back FROM customer_ledger"
                     f" WHERE org_id=? AND customer_id=? AND ref_id IN ({q})", (org, cid, *part))
        ret = db.one(f"SELECT coalesce(sum(l.amount),0) t FROM customer_ledger l JOIN sale_returns r ON r.id=l.ref_id"
                     f" AND r.org_id=l.org_id WHERE l.org_id=? AND l.customer_id=? AND l.kind='return'"
                     f" AND r.created_at<? AND r.sale_id IN ({q})", (org, cid, hi, *part))
        cashrow = db.one(f"SELECT coalesce(sum(paid_cash+paid_card+paid_transfer),0) p FROM sales WHERE org_id=? AND id IN ({q})",
                         (org, *part))
        acct["debit"] += row["debit"]
        acct["back"] += row["back"] + ret["t"]
        paid += cashrow["p"]
    sold = out["pharmacy"]["value"] + out["hospital"]["value"]
    account_net = acct["debit"] + acct["back"]
    no_cost = sum(1 for o in ("pharmacy", "hospital") for l in out[o]["lines"] if l.get("no_cost"))
    return {"branch": branch, "title": st["title"], "customer_id": cid, "from": d1.isoformat(), "to": d2.isoformat(),
            "pharmacy": out["pharmacy"], "hospital": out["hospital"],
            "previous_returns": {"rows": back, "value": back_v, "cost": back_c, "due": back_due,
                                 "by_owner": {owner: pv[owner][0] for owner in OWNERS}},
            "due": due, "advance": adv, "net": due - adv, "next_advance": adv, "total": due - adv + adv,
            "invoices": len(sales), "no_cost_lines": no_cost,
            "account": {"debit": acct["debit"], "returns": -acct["back"], "net": account_net, "paid_not_on_account": paid,
                        "matched_bp": (min(sold, account_net) * 10000 // max(sold, account_net)) if max(sold, account_net) > 0 else 10000}}
