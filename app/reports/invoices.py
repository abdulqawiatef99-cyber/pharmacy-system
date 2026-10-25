"""تقرير فواتير المبيعات المتقدم (للمدير والصاحب).

فلاتر: من تاريخ وساعة لتاريخ وساعة، نوع الفاتورة (نقدي، فيزا، تحويل، آجل، توصيل، تعاقد، عليها مرتجع،
عليها خصم، ملغاة، معلقة)، رقم الفاتورة أو العميل، صنف موجود في الفاتورة، والموظف.
لكل فاتورة: قبل الخصم، خصم الأصناف، خصم العميل (التعاقد)، بعد الخصم، المرتجع، الصافي، طرق الدفع،
والربح للصاحب (costs.read بس). وسطر إجماليات تحت."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from ..core.errors import ValidationFailed
from ..core.permissions import Context
from ..db import Database
from ..sales.service import alloc_gross, kept_value

TYPES = ("cash", "card", "transfer", "credit", "delivery", "contract", "returned", "discounted", "voided", "draft")
MAX_ROWS = 3000
_F = "%Y-%m-%dT%H:%M:%S.%fZ"


def _utc(local: str, end: bool = False) -> str:
    """'2026-10-25T14:30' (pharmacy local time) -> UTC ISO. A bare date means the whole day."""
    try:
        if len(local) == 10:
            d = datetime.fromisoformat(local + ("T23:59:59.999999" if end else "T00:00"))
        else:
            d = datetime.fromisoformat(local)
            if end:
                d = d.replace(second=59, microsecond=999999)
    except (TypeError, ValueError):
        raise ValidationFailed("التاريخ أو الساعة غير صحيحين")
    return d.astimezone().astimezone(timezone.utc).strftime(_F)


def report(db: Database, ctx: Context, *, date_from: str, date_to: str, types: list[str] | None = None,
           q: str | None = None, item_id: str | None = None, user_id: str | None = None, with_lines: bool = False) -> dict:
    ctx.require("sales.totals")
    lo, hi = _utc(date_from), _utc(date_to, end=True)
    if hi < lo:
        raise ValidationFailed("النهاية قبل البداية")
    if (datetime.strptime(hi, _F) - datetime.strptime(lo, _F)).days > 400:
        raise ValidationFailed("الفترة سنة على الأكتر")
    types = [t for t in (types or []) if t in TYPES]
    drafts = "draft" in types
    when = "s.created_at" if drafts else "s.completed_at"
    sql = ["SELECT s.id, s.sale_no, s.status, s.kind, s.subtotal, s.discount_total, s.total, s.delivery_fee, s.paid_cash, s.paid_card,"
           " s.paid_transfer, s.on_credit, s.returned_total, s.created_at, s.completed_at, s.voided_at, s.void_reason, s.contract_id,"
           " c.name customer, c.phone, u.full_name seller, k.name contract,"
           " (SELECT count(*) FROM sale_lines l WHERE l.sale_id=s.id) lines"
           " FROM sales s LEFT JOIN customers c ON c.id=s.customer_id LEFT JOIN users u ON u.id=s.created_by"
           " LEFT JOIN contracts k ON k.id=s.contract_id",
           f" WHERE s.org_id=? AND s.branch_id=? AND {when}>=? AND {when}<=?"]
    p: list = [ctx.org_id, ctx.branch_id, lo, hi]
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
    sql.append(f" ORDER BY {when} DESC LIMIT ?")
    p.append(MAX_ROWS + 1)
    rows = [dict(r) for r in db.all("".join(sql), tuple(p))]
    truncated = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]
    show_cost = ctx.can("costs.read")
    hidden_ok = ctx.can("items.hidden")
    for r in rows:
        r["item_discount"] = r["customer_discount"] = 0
        r["profit"] = 0 if show_cost else None
        r["unknown_cost"] = False
        lines = []
        for l in db.all("SELECT l.id, l.item_id, l.qty_base, l.discount_bp, l.discount_source, l.line_total, i.name_en, i.name_ar,"
                        " i.pack_size, i.base_unit_name, i.hidden FROM sale_lines l JOIN items i ON i.id=l.item_id"
                        " WHERE l.sale_id=? ORDER BY l.line_no", (r["id"],)):
            allocs = [dict(a) for a in db.all("SELECT id, qty_base, returned_qty, unit_price, pack_price, pack_size, unit_cost"
                                              " FROM sale_allocations WHERE sale_line_id=?", (l["id"],))]
            gross = sum(alloc_gross(a["qty_base"], a) for a in allocs)
            net = l["line_total"] if l["line_total"] is not None else gross
            disc = gross - net
            if l["discount_source"] == "contract":
                r["customer_discount"] += disc
            else:
                r["item_discount"] += disc
            returned = sum(a["returned_qty"] for a in allocs)
            if show_cost and r["status"] == "completed":
                kept = {a["id"]: a["qty_base"] - a["returned_qty"] for a in allocs}
                value = kept_value(allocs, kept, l["discount_bp"])
                if any(a["unit_cost"] is None and kept[a["id"]] for a in allocs):
                    r["unknown_cost"] = True
                cost = sum(Decimal(a["unit_cost"] or 0) * kept[a["id"]] * 100 for a in allocs)
                r["profit"] += value - int(cost.to_integral_value())
            if with_lines:
                lines.append({"item_id": l["item_id"], "name_en": l["name_en"], "name_ar": l["name_ar"], "pack_size": l["pack_size"],
                              "base_unit_name": l["base_unit_name"], "qty": l["qty_base"], "returned": returned, "gross": gross,
                              "discount_bp": l["discount_bp"], "net": net, "contract": l["discount_source"] == "contract",
                              "hidden": bool(l["hidden"]) and not hidden_ok})
        if r["status"] == "draft":           # not priced yet: what the open invoice would come to is shown on the cashier
            r["item_discount"] = r["customer_discount"] = 0
        r["net"] = r["total"] - r["returned_total"] if r["status"] == "completed" else 0
        if with_lines:
            for x in lines:
                if x.pop("hidden"):
                    x.update(name_en="—", name_ar=None)
            r["items"] = lines
        if not show_cost:
            r.pop("profit"), r.pop("unknown_cost")
    done = [r for r in rows if r["status"] == "completed"]
    tot = {"n": len(rows)}
    for k in ("subtotal", "item_discount", "customer_discount", "discount_total", "total", "returned_total", "net",
              "paid_cash", "paid_card", "paid_transfer", "on_credit", "delivery_fee"):
        tot[k] = sum(r[k] for r in done)
    tot["returned_n"] = sum(1 for r in done if r["returned_total"])
    tot["discounted_n"] = sum(1 for r in done if r["discount_total"])
    if show_cost:
        tot["profit"] = sum(r["profit"] for r in done)
        tot["unknown_cost_n"] = sum(1 for r in done if r["unknown_cost"])
    sellers = [dict(u) for u in db.all("SELECT u.id, u.full_name name FROM memberships m JOIN users u ON u.id=m.user_id"
                                         " WHERE m.org_id=? ORDER BY u.full_name", (ctx.org_id,))]
    return {"from": date_from, "to": date_to, "types": types, "rows": rows, "totals": tot, "truncated": truncated,
            "show_cost": show_cost, "sellers": sellers}
