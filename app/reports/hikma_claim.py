"""U3-3 Hikma claim («مطالبة مستشفى نور الحكمة») for any date range (a half month too), per branch: Shibin and
Mit Khalaf, each a customer of the pharmacy. The rules are the agreed ones (runbook `hikma-monthly-claim.md`):
- the pharmacy's own stock: the hospital pays the real cost + half the margin;
- the hospital's own stock (batches owned by the hospital, migration 0039): the pharmacy takes half the margin;
- the hospital's half of the margin is rounded once, on the total (half up), never per line;
- the advance (10,000 a branch by default) is taken off the period, the next advance is added.
Lines are per item, by value high → low. Sale values are the invoices' own net values after any return made
up to now (as E-Stock nets them); returns made inside the period for sales of an earlier period are taken off on
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


def _p(d: Decimal) -> int:
    return int(d.quantize(Decimal("1"), ROUND_HALF_UP))


def _half(x: int) -> int:
    """Half of an integer amount of piastres, half up (the one rounding of the claim)."""
    return (x + 1) // 2 if x >= 0 else -((-x + 1) // 2)


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
    if customer_id and not db.one("SELECT 1 FROM customers WHERE id=? AND org_id=?", (customer_id, ctx.org_id)):
        raise NotFound("العميل غير موجود")
    r = db.one("SELECT value FROM org_settings WHERE org_id=? AND key=?", (ctx.org_id, KEY))
    st = json.loads(r["value"]) if r else {}
    before = dict(st)
    st[branch] = {"customer_id": customer_id or None, "advance": advance}
    with db.transaction():
        db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?,?,?) ON CONFLICT(org_id, key)"
                   " DO UPDATE SET value=excluded.value", (ctx.org_id, KEY, json.dumps(st)))
        audit.record(db, ctx, "settings.hikma_claim", "organization", ctx.org_id, before=before, after=st)
    return settings(db, ctx.org_id)


def _lines(db: Database, org: str, sale_ids: list[str]) -> list[dict]:
    from ..sales.service import line_fixed, outstanding_value, returns_history
    if not sale_ids:
        return []
    hist = returns_history(db, org, sale_ids)
    out = []
    for k in range(0, len(sale_ids), 500):
        part = sale_ids[k:k + 500]
        lines = db.all(f"SELECT l.*, coalesce(i.name_en, i.name_ar) name, i.code FROM sale_lines l JOIN items i ON i.id=l.item_id"
                       f" WHERE l.org_id=? AND l.sale_id IN ({','.join('?' * len(part))})", (org, *part))
        for l in lines:
            allocs = [dict(a) for a in db.all(
                "SELECT a.*, coalesce(b.owner,'pharmacy') owner FROM sale_allocations a LEFT JOIN batches b"
                " ON b.id=a.batch_id AND b.org_id=? WHERE a.sale_line_id=?", (org, l["id"]))]
            if not allocs:
                continue
            kept = {a["id"]: a["qty_base"] - a["returned_qty"] for a in allocs}
            qty = sum(kept.values())
            if qty <= 0:
                continue
            fx = {"fixed": line_fixed(l)} if line_fixed(l) else {}
            value = outstanding_value(allocs, kept, l["discount_bp"], l["line_total"], l["id"], hist.get(l["sale_id"], []), **fx)
            # the hospital's own lots and the pharmacy's are split by units; the value follows the units
            for owner in ("pharmacy", "hospital"):
                part_a = [a for a in allocs if a["owner"] == owner and kept[a["id"]] > 0]
                if not part_a:
                    continue
                q = sum(kept[a["id"]] for a in part_a)
                cost = sum((Decimal(a["unit_cost"]) * kept[a["id"]] * 100 for a in part_a if a["unit_cost"]), Decimal(0))
                v = value if q == qty else value * q // qty
                out.append({"item_id": l["item_id"], "name": l["name"], "code": l["code"], "owner": owner, "qty": q,
                            "value": v, "cost": cost, "no_cost": any(not a["unit_cost"] for a in part_a)})
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
    for x in _lines(db, org, sales):
        g = rows.setdefault((x["owner"], x["item_id"]), {**x, "qty": 0, "value": 0, "cost": Decimal(0), "no_cost": False})
        g["qty"] += x["qty"]
        g["value"] += x["value"]
        g["cost"] += x["cost"]
        g["no_cost"] = g["no_cost"] or x["no_cost"]
    out = {}
    for owner in ("pharmacy", "hospital"):
        ls = sorted((r for (o, _), r in rows.items() if o == owner), key=lambda r: (-r["value"], r["name"] or ""))
        value = sum(r["value"] for r in ls)
        cost = _p(sum((r["cost"] for r in ls), Decimal(0)))
        margin = value - cost
        hospital_half = _half(margin)            # rounded once on the total
        due = value - hospital_half if owner == "pharmacy" else margin - hospital_half
        for r in ls:
            r["cost"] = _p(r["cost"])
            r["margin"] = r["value"] - r["cost"]
            r["margin_bp"] = (r["margin"] * 10000 // r["value"]) if r["value"] else 0
            r["below_cost"] = r["value"] <= r["cost"]
        out[owner] = {"lines": ls, "value": value, "cost": cost, "margin": margin, "hospital_half": hospital_half, "due": due}
    # returns made inside the period for sales of an earlier period: their due comes off (pharmacy stock rule)
    back_v = back_c = 0
    back = []
    for r in db.all("SELECT r.id, r.total, r.created_at, s.id sid, s.sale_no, s.completed_at FROM sale_returns r"
                    " JOIN sales s ON s.id=r.sale_id AND s.org_id=r.org_id WHERE r.org_id=? AND s.customer_id=?"
                    " AND r.created_at>=? AND r.created_at<? AND s.completed_at<?", (org, cid, lo, hi, lo)):
        a = db.one("SELECT after_json FROM audit_log WHERE org_id=? AND entity_type='sale' AND entity_id=?"
                   " AND action='sale.return' AND after_json LIKE ?", (org, r["sid"], f'%"{r["id"]}"%'))
        lines = (json.loads(a["after_json"]) if a else {}).get("lines") or {}
        c = Decimal(0)
        for lid, q in lines.items():
            al = db.all("SELECT qty_base, unit_cost FROM sale_allocations WHERE sale_line_id=?", (lid,))
            n = sum(x["qty_base"] for x in al)
            if n:
                c += sum((Decimal(x["unit_cost"] or 0) * x["qty_base"] for x in al), Decimal(0)) / n * int(q) * 100
        back_v += r["total"]
        back_c += _p(c)
        back.append({"sale_no": r["sale_no"], "sold_at": r["completed_at"], "returned_at": r["created_at"], "value": r["total"]})
    back_due = back_v - _half(back_v - back_c)
    due = out["pharmacy"]["due"] + out["hospital"]["due"] - back_due
    adv = st["advance"]
    # the customer's account in the same period: what the invoices put on it, net of returns to the account
    acct = db.one("SELECT coalesce(sum(CASE WHEN kind='credit_sale' THEN amount END),0) debit,"
                  " coalesce(sum(CASE WHEN kind IN ('return','void') THEN amount END),0) back"
                  " FROM customer_ledger WHERE org_id=? AND customer_id=? AND created_at>=? AND created_at<?",
                  (org, cid, lo, hi))
    sold = out["pharmacy"]["value"] + out["hospital"]["value"]
    account_net = acct["debit"] + acct["back"]
    return {"branch": branch, "title": st["title"], "customer_id": cid, "from": d1.isoformat(), "to": d2.isoformat(),
            "pharmacy": out["pharmacy"], "hospital": out["hospital"],
            "previous_returns": {"rows": back, "value": back_v, "cost": back_c, "due": back_due},
            "due": due, "advance": adv, "net": due - adv, "next_advance": adv, "total": due - adv + adv,
            "invoices": len(sales),
            "account": {"debit": acct["debit"], "returns": -acct["back"], "net": account_net,
                        "matched_bp": (min(sold, account_net) * 10000 // max(sold, account_net)) if max(sold, account_net) > 0 else 10000}}
