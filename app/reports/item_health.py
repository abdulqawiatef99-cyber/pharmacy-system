"""U3-1 «فحص بيانات الأصناف»: item data health report (owner / manager). Read only.

Each row is one item with one problem: what is wrong, in plain Arabic, and what the program thinks the right value
might be. Nothing is changed here: the screen's «صحّح» button opens the item card, and the person decides.

Checks (worst first):
- pack_vs_name: the count written in the name (e.g. «21TAB») does not fit the pack size. Real case: RANXAPIN 10MG
  21TAB with a pack of 6 strips; 21 tablets cannot be 6 equal strips, the real box is 3 strips of 7.
- unit_price_split: the box price does not divide by the units in it, so units sold one by one add up to a different
  total than the box (strip price × strips ≠ box price).
- no_public_price: public price 0 or missing (not a service).
- price_vs_purchase: public price far (> 25%) from the public price on the last approved purchase invoice.
- cost_above_public: average cost of a box above its public price (only for someone who may see costs).
- duplicate_name: two or more active items with the same normalized name.
- barcode_is_other_code: an item's barcode is another item's internal code (a scan may pick the wrong item).
- stock_no_expiry: stock on hand with no expiry date (not a service).
- negative_stock: the balance is below zero.

Every query filters by org_id. Money is integer piastres; the cost check compares in Decimal, never float.
"""
from __future__ import annotations

from decimal import Decimal

from ..catalog.normalize import parse_name
from ..core.permissions import Context
from ..db import Database

ORDER = {"bad": 0, "warn": 1}
# base-unit names that mean "one tablet / capsule": the pack size must equal the count in the name
_PIECE_UNITS = {"قرص", "اقراص", "أقراص", "كبسولة", "كبسوله", "كبسولات", "tab", "tablet", "tabs", "cap", "capsule", "caps"}
_STRIP_UNITS = {"شريط", "شرائط", "strip", "strips"}
_COUNTED_FORMS = {"tab", "cap", "chew", "eff", "lozenge", "supp", "amp", "vial", "sachet"}
PRICE_GAP_BP = 2500          # 25%


def _egp(p: int | None) -> str:
    if p is None:
        return "—"
    sign, p = ("-", -p) if p < 0 else ("", p)
    return f"{sign}{p // 100:,}.{p % 100:02d}"


def strip_suggestions(count: int) -> list[int]:
    """Strip counts that split `count` tablets into equal strips of 5..15 (the usual sizes), fewest strips first."""
    return [s for s in range(1, count + 1) if count % s == 0 and 5 <= count // s <= 15]


def pack_problem(name: str, pack_size: int, base_unit: str | None) -> dict | None:
    """The pack check alone (also used by the item card when the pack is being corrected)."""
    p = parse_name(name or "")
    if not p.pack_count or p.form not in _COUNTED_FORMS or p.pack_count < 2:
        return None
    n, ps, unit = p.pack_count, max(1, int(pack_size or 1)), (base_unit or "").strip().lower()
    if unit in _PIECE_UNITS:
        if ps != n:
            return {"count": n, "suggest": [n], "text": f"الاسم مكتوب فيه {n} والعبوة متسجلة {ps} {base_unit} — المفروض {n}"}
        return None
    if ps == 1:
        return None                          # sold as a whole box only: nothing to split
    if ps > n or n % ps:
        sug = strip_suggestions(n)
        hint = (" — غالبًا " + " أو ".join(f"{s} شريط × {n // s}" for s in sug[:3])) if sug else ""
        return {"count": n, "suggest": sug,
                "text": f"الاسم مكتوب فيه {n} ومتسجلة العلبة {ps} {base_unit or 'وحدة'}: {n} مايتقسمش على {ps} بالتساوي{hint}"}
    if unit in _STRIP_UNITS and ps == n:
        return {"count": n, "suggest": strip_suggestions(n),
                "text": f"العلبة متسجلة {ps} شريط، ده نفس عدد الأقراص في الاسم ({n}) — اتأكد إن الوحدة شريط مش قرص"}
    return None


def _row(it, key: str, status: str, text: str, **extra) -> dict:
    return {"item_id": it["id"], "name": it["name_en"] or it["name_ar"] or "", "code": it["code"],
            "key": key, "status": status, "text": text, **extra}


def item_health(db: Database, ctx: Context) -> dict:
    ctx.require("items.price")              # owner and manager by default (U3-1)
    org = ctx.org_id
    costs = ctx.can("costs.read")
    items = db.all(
        "SELECT i.id, i.code, i.name_en, i.name_ar, i.name_norm, i.pack_size, i.base_unit_name, i.public_price,"
        " i.is_service, coalesce(sb.q,0) stock, coalesce(sb.no_exp,0) no_exp"
        " FROM items i LEFT JOIN (SELECT s.item_id, sum(s.qty) q,"
        "   sum(CASE WHEN s.qty>0 AND (s.batch_key='' OR b.expiry_date IS NULL) THEN s.qty ELSE 0 END) no_exp"
        "   FROM stock_balances s LEFT JOIN batches b ON b.id=nullif(s.batch_key,'') AND b.org_id=s.org_id"
        "   WHERE s.org_id=? GROUP BY s.item_id) sb ON sb.item_id=i.id"
        " WHERE i.org_id=? AND i.status='active'", (org, org))
    last_pub = {r["item_id"]: r["public_price"] for r in db.all(       # one pass (review P2: no per-line subquery)
        "SELECT item_id, public_price FROM (SELECT l.item_id, l.public_price, row_number() OVER"
        " (PARTITION BY l.item_id ORDER BY p.approved_at DESC, l.line_no DESC) rn"
        " FROM purchase_invoice_lines l JOIN purchase_invoices p ON p.id=l.invoice_id AND p.org_id=l.org_id"
        " WHERE l.org_id=? AND p.status='approved' AND l.item_id IS NOT NULL AND l.public_price>0) WHERE rn=1", (org,))}
    avg = {}
    if costs:
        avg = {r["item_id"]: Decimal(r["avg_cost"]) for r in db.all(
            "SELECT item_id, avg_cost FROM item_costs WHERE org_id=? AND branch_id=?", (org, ctx.branch_id))}
    dup_names: dict[str, list] = {}
    for it in items:
        dup_names.setdefault(it["name_norm"], []).append(it)
    codes = {r["code"]: r["id"] for r in items if r["code"]}
    by_id = {r["id"]: r for r in items}
    bc_rows = db.all("SELECT barcode, item_id FROM item_barcodes WHERE org_id=?", (org,))
    out: list[dict] = []
    for it in items:
        ps, pub, svc = max(1, it["pack_size"] or 1), it["public_price"], bool(it["is_service"])
        pk = pack_problem(it["name_en"] or it["name_ar"] or "", ps, it["base_unit_name"])
        if pk and not svc:
            out.append(_row(it, "pack_vs_name", "bad", pk["text"], suggest=pk["suggest"], pack_size=ps,
                            open_credit=open_credit_sales(db, ctx, it["id"])))
        if not svc and ps > 1 and pub and pub % ps:
            unit = pub // ps + (1 if (pub % ps) * 2 >= ps else 0)       # half up, the way a unit is priced
            out.append(_row(it, "unit_price_split", "warn",
                            f"سعر العلبة {_egp(pub)} مايتقسمش على {ps}: الوحدة بتتباع {_egp(unit)} و{ps} وحدات = {_egp(unit * ps)}"))
        if not svc and not pub:
            out.append(_row(it, "no_public_price", "bad" if it["stock"] > 0 else "warn",
                            "سعر الجمهور صفر أو مش متسجل" + (" وعليه رصيد" if it["stock"] > 0 else "")))
        lp = last_pub.get(it["id"])
        if pub and lp and abs(pub - lp) * 10000 > PRICE_GAP_BP * lp:
            out.append(_row(it, "price_vs_purchase", "warn",
                            f"سعر الجمهور {_egp(pub)} بعيد عن سعر آخر فاتورة شراء {_egp(lp)}"))
        if costs and pub and it["id"] in avg:
            box_cost = avg[it["id"]] * ps * 100                          # EGP per base unit -> piastres per box
            if box_cost > pub:
                out.append(_row(it, "cost_above_public", "bad",
                                f"تكلفة العلبة ({_egp(int(box_cost))}) أعلى من سعر الجمهور ({_egp(pub)})"))
        if it["stock"] < 0:
            out.append(_row(it, "negative_stock", "bad", f"الرصيد بالسالب ({it['stock']} {it['base_unit_name']}) — اعمل جرد للصنف"))
        if not svc and it["no_exp"] > 0:
            out.append(_row(it, "stock_no_expiry", "warn", f"{it['no_exp']} {it['base_unit_name']} في المخزون من غير تاريخ صلاحية"))
    for norm, group in dup_names.items():
        if len(group) > 1:
            names = "، ".join((g["code"] or g["name_en"] or "") for g in group)
            for g in group:
                out.append(_row(g, "duplicate_name", "warn", f"الاسم ده متسجل {len(group)} مرات ({names}) — ادمجهم أو فرّق الاسم"))
    for b in bc_rows:
        other = codes.get(b["barcode"])
        if other and other != b["item_id"]:
            it = by_id.get(b["item_id"])
            if it:
                out.append(_row(it, "barcode_is_other_code", "warn",
                                f"الباركود {b['barcode']} هو نفسه كود صنف تاني — المسح ممكن يجيب الصنف الغلط"))
    out.sort(key=lambda r: (ORDER[r["status"]], r["key"], r["name"]))
    counts: dict[str, int] = {}
    for r in out:
        counts[r["key"]] = counts.get(r["key"], 0) + 1
    return {"rows": out, "counts": counts, "items_checked": len(items), "costs": costs}


def open_credit_sales(db: Database, ctx: Context, item_id: str) -> int:
    """Completed credit invoices selling this item whose customer still has a balance (U3-1: warn before a pack fix).
    The customer's balance is not split per invoice, so an invoice already paid by a customer who owes for another
    one is counted too: the screen says «لعملاء عليهم رصيد», never «unpaid»."""
    return db.one(
        "SELECT count(DISTINCT s.id) n FROM sales s JOIN sale_lines l ON l.sale_id=s.id AND l.org_id=s.org_id"
        " WHERE s.org_id=? AND l.item_id=? AND s.status='completed' AND s.on_credit>0 AND s.customer_id IS NOT NULL"
        " AND (SELECT coalesce(sum(amount),0) FROM customer_ledger c WHERE c.org_id=s.org_id"
        "      AND c.customer_id=s.customer_id) > 0", (ctx.org_id, item_id))["n"]
