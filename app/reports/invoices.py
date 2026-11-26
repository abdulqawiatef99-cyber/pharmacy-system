"""تقرير فواتير المبيعات المتقدم (للمدير والصاحب).

فلاتر: من تاريخ وساعة لتاريخ وساعة، نوع الفاتورة (نقدي، فيزا، تحويل، آجل، توصيل، تعاقد، عليها مرتجع،
عليها خصم، ملغاة، معلقة)، رقم الفاتورة أو العميل، صنف موجود في الفاتورة، والموظف.
لكل فاتورة: قبل الخصم، خصم الأصناف، خصم العميل (التعاقد)، بعد الخصم، المرتجع، الصافي، طرق الدفع،
والربح للصاحب (costs.read بس). وسطر إجماليات تحت."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from ..core.errors import ValidationFailed
from ..core.permissions import Context
from ..db import Database
from ..sales.service import line_fixed, line_gross_as_sold, official_prices, outstanding_value, returns_history

TYPES = ("cash", "card", "transfer", "credit", "delivery", "contract", "returned", "discounted", "voided", "draft")
MAX_ROWS = 3000
_F = "%Y-%m-%dT%H:%M:%S.%fZ"


def _utc(local: str, end: bool = False) -> str:
    """'2026-10-25T14:30' (pharmacy local time) -> UTC ISO. A bare date means the whole day.

    The night the clock goes back (Cairo, end of October) one local hour happens twice. The end of a period
    takes the later pass and its start the earlier one, and a whole day ends where the next day begins:
    23:59:59 read as the first pass dropped every sale of the repeated hour from that day (#324 R3)."""
    try:
        if len(local) == 10:
            if end:      # the last instant before the next local day starts, whatever the day's length
                nxt = datetime.combine(date.fromisoformat(local) + timedelta(days=1), time.min)
                return (nxt.astimezone().astimezone(timezone.utc) - timedelta(microseconds=1)).strftime(_F)
            d = datetime.fromisoformat(local + "T00:00")
        else:
            d = datetime.fromisoformat(local)
            if end:
                d = d.replace(second=59, microsecond=999999)
        d = d.replace(fold=1 if end else 0)
    except (TypeError, ValueError):
        raise ValidationFailed("التاريخ أو الساعة غير صحيحين")
    return d.astimezone().astimezone(timezone.utc).strftime(_F)


_CHUNK = 500         # ids per IN (...) query, far below SQLite's limit on bound parameters


def _lines_and_allocations(db: Database, sale_ids: list[str]) -> tuple[dict, dict]:
    """The lines of the listed sales (in line order) and each line's allocations, in a few queries instead of
    one query per invoice and one per line (#113 performance: a month of invoices was ~14,000 queries)."""
    lines_of: dict[str, list] = {}
    allocs_of: dict[str, list] = {}
    line_ids: list[str] = []
    for k in range(0, len(sale_ids), _CHUNK):
        part = sale_ids[k:k + _CHUNK]
        for l in db.all("SELECT l.sale_id, l.id, l.item_id, l.qty_base, l.discount_bp, l.discount_amount, l.invoice_share,"
                        " l.discount_source, l.line_total, i.name_en,"
                        " i.name_ar, i.pack_size, i.pack_unit_name, i.base_unit_name, i.hidden FROM sale_lines l JOIN items i ON i.id=l.item_id"
                        f" WHERE l.sale_id IN ({','.join('?' * len(part))}) ORDER BY l.sale_id, l.line_no", tuple(part)):
            lines_of.setdefault(l["sale_id"], []).append(l)
            line_ids.append(l["id"])
    for k in range(0, len(line_ids), _CHUNK):
        part = line_ids[k:k + _CHUNK]
        for a in db.all("SELECT sale_line_id, id, rowid rid, qty_base, returned_qty, unit_price, pack_price, pack_size, unit_cost"
                        f" FROM sale_allocations WHERE sale_line_id IN ({','.join('?' * len(part))}) ORDER BY rowid", tuple(part)):
            a = dict(a)
            allocs_of.setdefault(a.pop("sale_line_id"), []).append(a)
    return lines_of, allocs_of


def report(db: Database, ctx: Context, *, date_from: str, date_to: str, types: list[str] | None = None,
           q: str | None = None, item_id: str | None = None, user_id: str | None = None, with_lines: bool = False,
           offset: int = 0, totals: bool = True, after: str | None = None, until: str | None = None) -> dict:
    """One page of the matching invoices (MAX_ROWS from `offset`, newest first, a stable order) and the totals
    of EVERY match, whatever the page (#461: the totals used to be those of the listed rows only, so a busy
    period stopped at 3,000 invoices). `totals=False` skips the adding up, for the later pages of an export.

    An export walks the pages while the till keeps selling: it passes the first answer's `as_of` back as
    `until` (later invoices stay out) and each page's `next_after` as `after` (the page starts strictly
    after that invoice, wherever it now sits), so no invoice is listed twice and none is skipped."""
    ctx.require("sales.totals")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValidationFailed("رقم الصفحة غير صحيح")
    lo, hi = _utc(date_from), _utc(date_to, end=True)
    if hi < lo:
        raise ValidationFailed("النهاية قبل البداية")
    as_of = datetime.now(timezone.utc).strftime(_F)
    cursor = None
    try:
        if until:
            as_of = datetime.strptime(until, _F).strftime(_F)
        if after is not None:
            at, _, last_id = after.partition("|")
            cursor = (datetime.strptime(at, _F).strftime(_F), last_id)
            if not last_id or "|" in last_id:
                raise ValueError(after)
    except (TypeError, ValueError):
        raise ValidationFailed("رقم الصفحة غير صحيح")
    if (datetime.strptime(hi, _F) - datetime.strptime(lo, _F)).days > 400:
        raise ValidationFailed("الفترة سنة على الأكثر")
    types = [t for t in (types or []) if t in TYPES]
    drafts = "draft" in types
    when = "s.created_at" if drafts else "s.completed_at"
    head = ("SELECT s.id, s.sale_no, s.status, s.kind, s.subtotal, s.discount_total, s.total, s.delivery_fee, s.paid_cash, s.paid_card,"
            " s.paid_transfer, s.on_credit, s.returned_total, s.created_at, s.completed_at, s.voided_at, s.void_reason, s.contract_id,"
            " c.name customer, c.phone, u.full_name seller, k.name contract,"
            " (SELECT count(*) FROM sale_lines l WHERE l.sale_id=s.id) lines"
            " FROM sales s LEFT JOIN customers c ON c.id=s.customer_id LEFT JOIN users u ON u.id=s.created_by"
            " LEFT JOIN contracts k ON k.id=s.contract_id")
    sql = [f"SELECT s.id, {when} at FROM sales s LEFT JOIN customers c ON c.id=s.customer_id",
           f" WHERE s.org_id=? AND s.branch_id=? AND {when}>=? AND {when}<=?"]
    p: list = [ctx.org_id, ctx.branch_id, lo, min(hi, as_of)]
    if drafts:
        sql.append(" AND s.status='draft' AND EXISTS (SELECT 1 FROM sale_lines l WHERE l.sale_id=s.id)")
    elif "voided" in types:
        sql.append(" AND s.status='voided'")
    else:
        sql.append(" AND s.status='completed'")
    pay = {"cash": "s.paid_cash>0", "card": "s.paid_card>0", "transfer": "s.paid_transfer>0", "credit": "s.on_credit>0",
           "delivery": "s.kind='delivery'", "contract": "s.contract_id IS NOT NULL", "returned": "s.returned_total>0",
           "discounted": "s.discount_total>0"}
    ors = [pay[t] for t in types if t in pay]
    if ors:                                         # any of the chosen kinds
        sql.append(f" AND ({' OR '.join(ors)})")
    q = (q or "").strip()[:60]
    if q:                                           # invoice number, or part of a customer's name / phone
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        cond = ("c.name LIKE ? ESCAPE '\\' OR c.name_alt LIKE ? ESCAPE '\\' OR c.phone LIKE ? ESCAPE '\\'"
                " OR c.phone2 LIKE ? ESCAPE '\\' OR c.phone3 LIKE ? ESCAPE '\\'")
        p += [like] * 5
        if q.isdigit() and len(q) < 4:              # a short number is an invoice number, not a piece of a phone
            cond, p[-5:] = "s.sale_no=?", [int(q)]
        elif q.isdigit() and len(q) <= 9:
            cond += " OR s.sale_no=?"
            p.append(int(q))
        sql.append(f" AND ({cond})")
    if item_id:
        sql.append(" AND EXISTS (SELECT 1 FROM sale_lines l WHERE l.sale_id=s.id AND l.item_id=?)")
        p.append(item_id)
    if user_id:
        sql.append(" AND s.created_by=?")
        p.append(user_id)
    # every match, in one stable order (the id settles equal times): the pages never overlap or skip
    sql.append(f" ORDER BY {when} DESC, s.id DESC")
    found = [(r["at"], r["id"]) for r in db.all("".join(sql), tuple(p))]
    if cursor:                  # strictly after the last invoice of the previous page, in the same order
        offset = next((k for k, key in enumerate(found) if key < cursor), len(found))
    ids = [i for _, i in found]
    page_ids = set(ids[offset:offset + MAX_ROWS])
    truncated = len(ids) > offset + MAX_ROWS
    show_cost = ctx.can("costs.read")
    hidden_ok = ctx.can("items.hidden")
    money = ("subtotal", "item_discount", "customer_discount", "discount_total", "total", "returned_total", "net",
             "paid_cash", "paid_card", "paid_transfer", "on_credit", "delivery_fee")
    tot = {"n": len(ids), **{k: 0 for k in money}, "returned_n": 0, "discounted_n": 0}
    value_all, cost_all, unknown_n = 0, Decimal(0), 0
    rows: list[dict] = []
    # the listed page is always worked out; the rest of the period only for its totals, a chunk at a time
    # (nothing but the running sums is kept, so a year of invoices does not sit in memory)
    todo = ids if totals else ids[offset:offset + MAX_ROWS]
    for k in range(0, len(todo), _CHUNK):
        part = todo[k:k + _CHUNK]
        got = {r["id"]: dict(r) for r in db.all(
            # +s.org_id: the rows are found by their ids; the pharmacy filter must not pick an org-wide index
            f"{head} WHERE s.id IN ({','.join('?' * len(part))}) AND +s.org_id=?", (*part, ctx.org_id))}
        chunk = [got[i] for i in part if i in got]
        lines_of, allocs_of = _lines_and_allocations(db, [r["id"] for r in chunk])
        # the returns of the invoices that have any: their profit uses what each line still holds, as a return does
        history = returns_history(db, ctx.org_id, [sid for sid, ls in lines_of.items() if any(
            a["returned_qty"] for l in ls for a in allocs_of.get(l["id"], []))]) if show_cost else {}
        for r in chunk:
            listed = r["id"] in page_ids
            value_sum, cost_sum = _figures(r, lines_of, allocs_of, history, show_cost, hidden_ok, with_lines and listed)
            if r["status"] == "completed":
                for key in money:
                    tot[key] += r[key]
                tot["returned_n"] += 1 if r["returned_total"] else 0
                tot["discounted_n"] += 1 if r["discount_total"] else 0
                if show_cost:
                    # exact costs are added up and rounded once for the period: rounding each invoice made two
                    # sales costing 5.005 each lose a piastre against Profit and Top Items (#324 R5)
                    value_all += value_sum
                    cost_all += cost_sum
                    unknown_n += 1 if r["unknown_cost"] else 0
            if not show_cost:
                r.pop("profit"), r.pop("unknown_cost")
            if listed:
                rows.append(r)
    if show_cost:
        tot["profit"] = value_all - int(cost_all.to_integral_value(ROUND_HALF_UP))
        tot["unknown_cost_n"] = unknown_n
    sellers = [dict(u) for u in db.all("SELECT u.id, u.full_name name FROM memberships m JOIN users u ON u.id=m.user_id"
                                         " WHERE m.org_id=? ORDER BY u.full_name", (ctx.org_id,))]
    return {"from": date_from, "to": date_to, "types": types, "rows": rows, "totals": tot if totals else None,
            "truncated": truncated, "total_rows": len(ids), "offset": offset, "page_size": MAX_ROWS,
            "as_of": as_of, "next_after": "|".join(found[offset + len(rows) - 1]) if truncated and rows else None,
            "show_cost": show_cost, "sellers": sellers}


def _figures(r: dict, lines_of: dict, allocs_of: dict, history: dict, show_cost: bool, hidden_ok: bool,
             with_lines: bool) -> tuple[int, Decimal]:
    """Fill one invoice's worked-out columns in place (discount split, net, profit, its lines when asked).
    Returns its exact (kept value, kept cost) for the period's profit."""
    r["item_discount"] = r["customer_discount"] = 0
    r["profit"] = 0 if show_cost else None
    r["unknown_cost"] = False
    value_sum, cost_sum = 0, Decimal(0)
    lines = []
    for l in lines_of.get(r["id"], []):
        allocs = allocs_of.get(l["id"], [])
        gross = line_gross_as_sold(allocs, l["discount_bp"], l["line_total"], line_fixed(l))
        net = l["line_total"] if l["line_total"] is not None else gross
        disc = gross - net
        if l["discount_source"] == "contract":
            share = min(disc, l["invoice_share"] or 0)          # the invoice discount: the person's, not the contract's
            r["customer_discount"] += disc - share
            r["item_discount"] += share
        else:
            r["item_discount"] += disc
        returned = sum(a["returned_qty"] for a in allocs)
        if show_cost and r["status"] == "completed":
            kept = {a["id"]: a["qty_base"] - a["returned_qty"] for a in allocs}
            value = outstanding_value(allocs, kept, l["discount_bp"], l["line_total"], l["id"],
                                      history.get(r["id"], []), fixed=line_fixed(l))
            if any(a["unit_cost"] is None and kept[a["id"]] for a in allocs):
                r["unknown_cost"] = True
            value_sum += value
            cost_sum += sum(Decimal(a["unit_cost"] or 0) * kept[a["id"]] * 100 for a in allocs)
        if with_lines:
            prices = official_prices(allocs)
            if sum(p["gross"] for p in prices) != gross:
                prices = official_prices(allocs, per_batch=True)
            lines.append({"item_id": l["item_id"], "name_en": l["name_en"], "name_ar": l["name_ar"], "pack_size": l["pack_size"],
                          "base_unit_name": l["base_unit_name"], "pack_unit_name": l["pack_unit_name"], "qty": l["qty_base"], "returned": returned, "gross": gross,
                          "discount_bp": l["discount_bp"], "invoice_share": l["invoice_share"] or 0,
                          "official_prices": prices, "net": net, "contract": l["discount_source"] == "contract",
                          "hidden": bool(l["hidden"]) and not hidden_ok})
    if show_cost:
        # exact costs are added up and rounded once per invoice (and once for the period by the caller)
        r["profit"] = value_sum - int(cost_sum.to_integral_value(ROUND_HALF_UP))
    if r["status"] == "draft":           # not priced yet: what the open invoice would come to is shown on the cashier
        r["item_discount"] = r["customer_discount"] = 0
    r["net"] = r["total"] - r["returned_total"] if r["status"] == "completed" else 0
    if with_lines:
        for x in lines:
            if x.pop("hidden"):
                x.update(name_en="—", name_ar=None)
        r["items"] = lines
    return value_sum, cost_sum
