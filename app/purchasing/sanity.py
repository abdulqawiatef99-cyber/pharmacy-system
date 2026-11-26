"""Checks based on approved purchases in the receiving branch."""
import hashlib
from fractions import Fraction

from . import validation


def unusual_issues(db, inv, lines, items, settings):
    org, branch = inv["org_id"], inv["branch_id"]
    by_item = {}
    for line in lines:
        if line["item_id"] in items and line["qty"] and line["qty"] > 0:
            by_item.setdefault(line["item_id"], []).append(line)

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
            "SELECT m.unit_cost FROM stock_movements m JOIN purchase_invoices p"
            " ON p.id=m.ref_id AND p.org_id=m.org_id AND p.status='approved'"
            " WHERE m.org_id=? AND m.branch_id=? AND m.item_id=? AND m.ref_type='purchase_invoice'"
            " AND m.movement_type='purchase' AND m.unit_cost IS NOT NULL"
            " ORDER BY +m.rowid DESC LIMIT 1", (org, branch, item_id))
        last_cost = Fraction(last["unit_cost"]) * 100 * item["pack_size"] if last else None
        for line in group:
            amount = validation.effective_amount(line)
            if amount is None or amount <= 0:
                continue
            public, official = line["public_price"], item["public_price"]
            if _far(Fraction(amount, line["qty"]), public or official, settings["public_delta_pct"]) or (
                    public is not None and _far(public, official, settings["public_delta_pct"])):
                found.append(issue("unusual_public_price", line, "public_price", public=public,
                                   official=official, amount=amount, qty=line["qty"], item_id=item_id))
            received = line["qty"] + (line["bonus_qty"] or 0)
            if _far(Fraction(amount, received), last_cost, settings["last_cost_delta_pct"]):
                # Bind the reason to the comparison without returning the last cost to users who cannot see it.
                snapshot = hashlib.sha256(f"{item_id}/{amount}/{received}/{last_cost}".encode()).hexdigest()
                found.append(issue("unusual_last_cost", line, "unit_net_price", snapshot=snapshot))
    validation.apply_acknowledgements(inv, found)
    return found


def _far(value, reference, percentage):
    return reference is not None and reference > 0 and abs(value - reference) * 100 > reference * percentage
