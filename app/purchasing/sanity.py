"""Checks based on approved purchases in the receiving branch."""
from decimal import Decimal
from fractions import Fraction

from . import validation


def unusual_issues(db, inv, lines, items, settings):
    org, branch = inv["org_id"], inv["branch_id"]
    by_item = {}
    for line in lines:
        if line["item_id"] in items and line["qty"] and line["qty"] > 0 and (line["bonus_qty"] or 0) >= 0:
            by_item.setdefault(line["item_id"], []).append(line)
    amounts = {line["id"]: validation.effective_amount(line) for line in lines}
    gross = sum(amount for amount in amounts.values() if amount is not None)
    total = gross - inv["extra_discount"] + inv["extra_tax"]
    cost_check = (gross > 0 or total > 0) and total >= 0 and all(
        amount is not None and amount >= 0 for amount in amounts.values())
    all_units = sum((line["qty"] + (line["bonus_qty"] or 0)) * items[item_id]["pack_size"]
                    for item_id, group in by_item.items() for line in group) if gross == 0 else 0

    def issue(code, line, field, **details):
        return validation.Issue("error", code, validation.MESSAGES[code], line["id"], field, details)

    found = []
    for item_id, group in by_item.items():
        item = items[item_id]
        qty = sum(l["qty"] + (l["bonus_qty"] or 0) for l in group)
        if qty >= settings["qty_min"]:
            recent = db.all(
                "SELECT SUM(l.qty + l.bonus_qty) qty FROM purchase_invoice_lines l"
                " JOIN purchase_invoices p ON p.id=l.invoice_id AND p.org_id=l.org_id"
                " WHERE l.org_id=? AND l.item_id=? AND p.branch_id=? AND p.status='approved'"
                " GROUP BY p.id ORDER BY p.approved_at DESC, p.id DESC LIMIT 10", (org, item_id, branch))
            multiplier = settings["qty_multiple"]
            usual_total = sum(r["qty"] or 0 for r in recent)
            if usual_total > 0 and qty * len(recent) > usual_total * multiplier:
                found.append(issue("unusual_qty", group[0], "qty", qty=qty,
                                   max_qty=max(settings["qty_min"] - 1, usual_total * multiplier // len(recent)),
                                   item_id=item_id))
        last = db.one(
            "SELECT m.id, m.unit_cost FROM stock_movements m JOIN purchase_invoices p"
            " ON p.id=m.ref_id AND p.org_id=m.org_id AND p.status='approved'"
            " WHERE m.org_id=? AND m.branch_id=? AND m.item_id=? AND m.ref_type='purchase_invoice'"
            " AND m.movement_type='purchase' AND m.unit_cost IS NOT NULL"
            " ORDER BY +m.rowid DESC LIMIT 1", (org, branch, item_id)) if cost_check else None
        for line in group:
            amount = amounts[line["id"]]
            if amount is None or amount < 0:
                continue
            public, official = line["public_price"], item["public_price"]
            if (amount > 0 and _far(Fraction(amount, line["qty"]), public or official, settings["public_delta_pct"])) or (
                    public is not None and _far(public, official, settings["public_delta_pct"])):
                found.append(issue("unusual_public_price", line, "public_price", public=public,
                                   official=official, amount=amount, qty=line["qty"], item_id=item_id))
            received = (line["qty"] + (line["bonus_qty"] or 0)) * item["pack_size"]
            if last and received > 0:
                cost = validation.allocated_unit_cost(amount, gross, total, received, all_units)
                if _far(cost, Decimal(last["unit_cost"]), settings["last_cost_delta_pct"]):
                    # A ledger entry identifies the reference without returning its restricted price.
                    found.append(issue("unusual_last_cost", line, "unit_net_price", movement=last["id"],
                                       cost=str(cost), received=received))
    validation.apply_acknowledgements(inv, found)
    return found


def _far(value, reference, percentage):
    return reference is not None and reference > 0 and abs(value - reference) * 100 > reference * percentage
