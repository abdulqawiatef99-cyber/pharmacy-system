"""Cashier / sales.

Rules:
- Stock leaves FEFO (earliest expiry first). Expired batches are never sold.
- Price = the public price printed on the batch being sold (Egyptian law: old
  stock keeps its old price), divided by pack size for partial packs. If a batch
  has no recorded price, the item's current public price is used and the line
  is flagged in the receipt data.
- A sale cannot complete until the safety engine findings are resolved
  (acknowledged / overridden with reason by an authorized user).
- Credit sales need a customer with enough remaining credit limit.
- Completed sales are immutable: corrections are returns (partial) or void
  (same day, permission sales.void) — both post reversing stock movements.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from ..core import audit, notify
from ..core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id
from ..inventory import service as inventory
from ..safety.engine import Finding, SafetyEngine
from . import cash

NEAR_EXPIRY_DAYS = 30
DEFAULT_RETURN_DAYS = 7          # owner, part 3 item 6a: default 7 days; Settings can change it


def egp(piasters: int) -> str:
    return f"{piasters / 100:,.2f}"


def return_window_days(db: Database, org_id: str) -> int:
    r = db.one("SELECT value FROM org_settings WHERE org_id=? AND key='return_window_days'", (org_id,))
    try:
        return max(0, int(r["value"])) if r else DEFAULT_RETURN_DAYS
    except ValueError:
        return DEFAULT_RETURN_DAYS


LINE_ORDERS = ("alpha", "entry")


def line_order(db: Database, org_id: str) -> str:
    """How invoice lines are shown, saved for reprint and printed: alphabetical (default: the
    pharmacist collects A, then B... in one walk along the shelves) or as they were entered."""
    r = db.one("SELECT value FROM org_settings WHERE org_id=? AND key='line_order'", (org_id,))
    return r["value"] if r and r["value"] in LINE_ORDERS else "alpha"


def sort_lines(lines: list[dict], order: str, names: str = "en") -> list[dict]:
    if order != "alpha":
        return sorted(lines, key=lambda l: l["line_no"])

    def key(l):
        it = l["item"]
        n = (it.get("name_ar") if names == "ar" else it.get("name_en")) or it.get("name") or ""
        return (n.strip().casefold(), l["line_no"])
    return sorted(lines, key=key)


def discount_denied(ctx: Context, discount_bp: int) -> Forbidden:
    return Forbidden(f"الخصم المسموح لك {ctx.discount_limit_bp / 100:g}% فقط", code="approval_required",
                     details={"permission": "discount", "label": f"خصم {discount_bp / 100:g}%",
                              "requested_bp": discount_bp, "limit_bp": ctx.discount_limit_bp})


def _now() -> str:
    from ..core.accounts import iso, utcnow
    return iso(utcnow())


def local_day_bounds(day: date) -> tuple[str, str]:
    """UTC ISO bounds of a calendar day in the pharmacy PC's local timezone
    (timestamps are stored in UTC; 'today' means the pharmacy's today)."""
    from datetime import datetime, time, timedelta, timezone
    start = datetime.combine(day, time.min).astimezone().astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time.min).astimezone().astimezone(timezone.utc)
    f = "%Y-%m-%dT%H:%M:%S.%fZ"
    return start.strftime(f), end.strftime(f)


def _local_date(utc_iso: str) -> date:
    from datetime import datetime, timezone
    return datetime.strptime(utc_iso, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).astimezone().date()


def local_today() -> date:
    from datetime import datetime
    return datetime.now().astimezone().date()


def item_warnings(db: Database, item_ids: list[str], *, org_id: str | None = None,
                  branch_id: str | None = None) -> list[Finding]:
    """The owner's own marks on items: the pharmacist must confirm each one before the sale
    completes, and the confirmation is logged with his name (safety_events).
    With org_id (and the branch being sold from): also a notice, which does not stop the sale, for an item
    with stock in a batch named in a recall notice nobody has decided yet (recall_matches 'pending')."""
    out = []
    for iid in dict.fromkeys(item_ids):
        it = db.one("SELECT id, name_en, name_ar, rx_required, misuse_risk, sale_note FROM items WHERE id=?", (iid,))
        if not it:
            continue
        name = it["name_en"] or it["name_ar"]
        if org_id:
            pend = [r["batch_no"] for r in db.all(
                "SELECT DISTINCT b.batch_no FROM recall_matches m JOIN batches b ON b.id=m.batch_id"
                " JOIN stock_balances sb ON sb.org_id=m.org_id AND sb.item_id=m.item_id AND sb.batch_key=m.batch_id"
                " WHERE m.org_id=? AND m.item_id=? AND m.status='pending' AND sb.qty>0 AND (? IS NULL OR sb.branch_id=?)"
                " AND NOT EXISTS (SELECT 1 FROM recall_matches c WHERE c.org_id=m.org_id AND c.batch_id=m.batch_id"
                "                 AND c.status='confirmed') ORDER BY b.batch_no", (org_id, iid, branch_id, branch_id))]
            if pend:
                out.append(Finding(code="item_recall_pending", severity="high", item_ids=[iid], requires="none", basis="owner",
                                   message=f"{name}: تشغيلة {'، '.join(pend)} عليها منشور سحب لم يُراجَع بعد."
                                           " راجع تنبيهات السحب قبل صرفها."))
        if it["rx_required"]:
            out.append(Finding(code="item_rx", severity="high", item_ids=[iid], requires="acknowledge", basis="owner",
                               message=f"{name}: يُصرف بوصفة طبية فقط (عليه غرامة إذا صُرف دون وصفة طبية). تأكد من الوصفة الطبية."))
        if it["misuse_risk"]:
            out.append(Finding(code="item_misuse", severity="moderate", item_ids=[iid], requires="acknowledge", basis="owner",
                               message=f"{name}: صنف يُساء استخدامه. تأكد من الحاجة إليه والكمية."))
        if it["sale_note"]:
            out.append(Finding(code="item_note", severity="info", item_ids=[iid], requires="acknowledge", basis="owner",
                               message=f"{name}: {it['sale_note']}"))
    return out


def warnings_report(db: Database, ctx: Context, date_from: str, date_to: str) -> dict:
    """Who confirmed which item warning on which sale (owner / manager)."""
    ctx.require("sales.totals")
    try:
        d1, d2 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except (TypeError, ValueError):
        raise ValidationFailed("اختر الفترة")
    rows = [dict(r) for r in db.all(
        "SELECT e.at, e.finding_code, e.message, e.reason, u.full_name by_name, s.id sale_id, s.sale_no, s.status"
        " FROM safety_events e LEFT JOIN users u ON u.id=e.user_id LEFT JOIN sales s ON s.id=e.context_id"
        " WHERE e.org_id=? AND e.finding_code IN ('item_rx','item_misuse','item_note') AND e.decision='acknowledged'"
        " AND e.at>=? AND e.at<? ORDER BY e.at DESC", (ctx.org_id, local_day_bounds(d1)[0], local_day_bounds(d2)[1]))]
    by = {}
    for r in rows:
        by[r["finding_code"]] = by.get(r["finding_code"], 0) + 1
    return {"from": d1.isoformat(), "to": d2.isoformat(), "rows": rows, "counts": by}


def alloc_gross(qty: int, a: dict) -> int:
    """Price of `qty` units of one allocation: whole packs at the pack price, the rest at the unit
    price. So 3 units of a 100.00 pack of 3 cost exactly 100.00, not 3 x 33.33."""
    ps, pp = a.get("pack_size") or 1, a.get("pack_price")
    if pp is None or ps <= 1:
        return a["unit_price"] * qty
    return (qty // ps) * pp + (qty % ps) * a["unit_price"]


def _kept_gross(qty: int, a: dict) -> int:
    """`alloc_gross` for valuing kept units on a return: the loose units of a pack are never worth more
    than the whole pack. A unit price rounded up (a pack of 100 at 10.50 -> 0.11) made 99 loose units
    (10.89) worth more than a pack, so the return that broke the pack booked a negative refund (#324 M1).
    Unchanged wherever (pack_size - 1) loose units cost no more than a pack."""
    ps, pp = a.get("pack_size") or 1, a.get("pack_price")
    if pp is None or ps <= 1:
        return a["unit_price"] * qty
    return (qty // ps) * pp + min((qty % ps) * a["unit_price"], pp)


def line_gross(parts, price=alloc_gross) -> int:
    """Price of a line from its (qty, allocation) parts. Allocations with the same price (a pack
    split over two batches by FEFO) are priced as one quantity, so a whole pack is still exactly
    the pack price; parts at different batch prices are priced each at its own."""
    groups: dict[tuple, list] = {}
    for qty, a in parts:
        g = groups.setdefault((a["unit_price"], a.get("pack_price"), a.get("pack_size") or 1), [0, a])
        g[0] += qty
    return sum(price(q, a) for q, a in groups.values())


def discount_of(gross: int, bp: int) -> int:
    """Discount in piasters, rounded half up (the same everywhere: screen, sale, return)."""
    return int((Decimal(gross) * bp / 10000).quantize(Decimal("1"), ROUND_HALF_UP)) if bp else 0


def _per_batch_gross(parts, price=alloc_gross) -> int:
    """How the release before 0.4 priced a line: each batch on its own (a pack split over two batches
    by FEFO came to 2 x 33.33 + 33.33 = 99.99). Only for reading sales it recorded (#72)."""
    return sum(price(q, a) for q, a in parts)


def _rule_as_sold(allocs: list[dict], bp: int, line_total: int | None):
    """The pricing rule that produced this line's recorded total, so returns and reports of a sale
    use the money it was actually sold for; the current rule when the record says nothing."""
    if line_total is not None:
        for rule in (line_gross, _per_batch_gross):
            g = rule((a["qty_base"], a) for a in allocs)
            if g - discount_of(g, bp) == line_total:
                return rule
    return line_gross


def line_gross_as_sold(allocs: list[dict], bp: int, line_total: int | None) -> int:
    """Gross of a whole line under the rule it was sold with."""
    return _rule_as_sold(allocs, bp, line_total)((a["qty_base"], a) for a in allocs)


def kept_value(allocs: list[dict], kept: dict, bp: int, line_total: int | None = None,
               price=_kept_gross) -> int:
    """Net value of the units of a line the customer still keeps (after discount), priced the way the
    line was sold. With the recorded total: the whole line is worth exactly that and never more, so
    all the returns of a line add up to what was paid for it, not a piaster more or less.
    `price=alloc_gross` is the rule before #324 M1, only to replay returns booked with it."""
    g = _rule_as_sold(allocs, bp, line_total)(((kept[a["id"]], a) for a in allocs), price=price)
    value = g - discount_of(g, bp)
    if line_total is None:
        return value
    if all(kept[a["id"]] == a["qty_base"] for a in allocs):
        return line_total
    return min(value, line_total)


def _take(allocs: list[dict], kept: dict, qty: int) -> None:
    """Remove `qty` returned units from `kept` in the order a return takes them: newest allocation first
    (by `rid`, the allocation's rowid, when the caller read it; else in the order given)."""
    if all("rid" in a for a in allocs):
        allocs = sorted(allocs, key=lambda a: a["rid"], reverse=True)
    for a in allocs:
        t = min(kept[a["id"]], qty)
        kept[a["id"]] -= t
        qty -= t
        if not qty:
            break


def returns_history(db: Database, org_id: str, sale_ids) -> dict[str, list[dict]]:
    """The `sale.return` audit rows of these sales, oldest first, as {sale_id: [after, ...]}: what each return
    took from which line (and, since #324 M1, what it refunded per line). Few queries, on the audit index."""
    ids, out = sorted(set(sale_ids)), {}
    for k in range(0, len(ids), 500):
        part = ids[k:k + 500]
        for r in db.all("SELECT entity_id, after_json FROM audit_log WHERE org_id=? AND entity_type='sale'"
                        f" AND entity_id IN ({','.join('?' * len(part))}) AND action='sale.return'"
                        " ORDER BY entity_id, at, rowid", (org_id, *part)):
            out.setdefault(r["entity_id"], []).append(json.loads(r["after_json"] or "{}"))
    return out


def outstanding_value(allocs: list[dict], kept: dict, bp: int, line_total: int | None, line_id: str,
                      history: list[dict]) -> int:
    """What is still paid for the kept units of a line: its recorded total minus the refunds already booked
    for it. The one reference for returns and reports, so they never disagree (#331): a return booked under
    the rule before #324 M1 valued kept units differently, and `kept_value` alone no longer matches it.
    Without a recorded total, or a history that does not explain the units returned: `kept_value`."""
    if line_total is None:
        return kept_value(allocs, kept, bp, None)
    if all(kept[a["id"]] == a["qty_base"] for a in allocs):
        return line_total
    replay = {a["id"]: a["qty_base"] for a in allocs}
    refunded = 0
    for after in history:
        qty = int((after.get("lines") or {}).get(line_id) or 0)
        if not qty:
            continue
        if line_id in (after.get("line_refunds") or {}):
            refunded += int(after["line_refunds"][line_id])
            _take(allocs, replay, qty)
        else:                                   # booked before per-line refunds were stored: replay its rule
            old = kept_value(allocs, replay, bp, line_total, price=alloc_gross)
            _take(allocs, replay, qty)
            refunded += old - kept_value(allocs, replay, bp, line_total, price=alloc_gross)
    if replay != {a["id"]: kept[a["id"]] for a in allocs}:
        return kept_value(allocs, kept, bp, line_total)
    return max(0, line_total - refunded)


def unit_price(pack_price: int, pack_size: int) -> int:
    """Piasters per base unit, rounded half-up."""
    return int((Decimal(pack_price) / pack_size).quantize(Decimal("1"), ROUND_HALF_UP))


def retail_value(qty: int, pack_price: int, pack_size: int) -> int:
    """What `qty` base units sell for at the shelf price: the sale's own rule (whole packs at the pack
    price, the rest at `unit_price`), so a report shows the money a sale would take, not a new rounding."""
    ps = max(1, pack_size or 1)
    return alloc_gross(qty, {"unit_price": unit_price(pack_price, ps), "pack_price": pack_price, "pack_size": ps})


def cost_piasters(cost: Decimal) -> int:
    """An exact cost in piasters (from the decimal average cost) as whole piasters, half up: the rule
    the sale already uses for a line's cost (below-cost warning)."""
    return int(cost.to_integral_value(ROUND_HALF_UP))


class Sales:
    def __init__(self, db: Database, today: date | None = None):
        self.db = db
        self.today = today or date.today()
        self.safety = SafetyEngine(db)

    # ------------------------------------------------------------ customers
    def create_customer(self, ctx: Context, *, name: str, phone: str | None = None,
                        credit_limit: int = 0, notes: str | None = None, **more) -> str:
        from . import customers as cu
        ctx.require("customers.manage")
        if not name.strip():
            raise ValidationFailed("اسم العميل مطلوب")
        if credit_limit < 0:
            raise ValidationFailed("حد الآجل لا يكون بالسالب")
        d = {"name": name.strip(), "phone": cu.clean_phone(phone), "notes": notes or None}
        for k in ("name_alt", "address1", "address2"):
            d[k] = str(more.get(k) or "").strip() or None
        for k in ("phone2", "phone3"):
            d[k] = cu.clean_phone(more.get(k))
        cid = new_id()
        with self.db.transaction():
            cu.check_phones(self.db, ctx.org_id, [d[k] for k in ("phone", "phone2", "phone3") if d[k]])
            self.db.execute(f"INSERT INTO customers (id, org_id, credit_limit, {', '.join(d)}) VALUES (?,?,?{',?' * len(d)})",
                            (cid, ctx.org_id, credit_limit, *d.values()))
            audit.record(self.db, ctx, "customer.create", "customer", cid,
                         after={"name": name, "credit_limit": credit_limit})
        return cid

    def balance(self, ctx: Context, customer_id: str) -> int:
        return int(self.db.one("SELECT coalesce(sum(amount),0) b FROM customer_ledger WHERE org_id=?"
                               " AND customer_id=?", (ctx.org_id, customer_id))["b"])

    COLLECTION_METHODS = {"cash": "نقدًا", "card": "بطاقة", "transfer": "محفظة إلكترونية"}

    def customer_payment(self, ctx: Context, customer_id: str, amount: int, note: str | None = None,
                         method: str = "cash", channel_id: str | None = None) -> int:
        ctx.require("customers.manage")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise ValidationFailed("يجب أن يكون المبلغ أكبر من صفر")
        if method not in self.COLLECTION_METHODS:
            raise ValidationFailed("طريقة الدفع غير صحيحة")
        with self.db.transaction():
            if not self.db.one("SELECT 1 FROM customers WHERE id=? AND org_id=?", (customer_id, ctx.org_id)):
                raise NotFound("العميل غير موجود")
            # under the write lock: two screens together cannot collect more than the customer owes
            owed = self.balance(ctx, customer_id)
            if amount > owed:
                raise ValidationFailed(
                    "المبلغ أكبر من المستحق على العميل" if owed > 0 else "لا يوجد مبلغ مستحق على هذا العميل",
                    code="over_collection", details={"balance": owed, "amount": amount})
            ch = None
            if method != "cash" and channel_id:
                ch = self.db.one("SELECT id, name FROM payment_channels WHERE id=? AND org_id=? AND kind=?",
                                 (channel_id, ctx.org_id, method))
                if not ch:
                    raise ValidationFailed("وسيلة الدفع غير موجودة")
            if method == "cash":
                cash.record(self.db, ctx, amount, "customer_payment", customer_id, note)
            # how it was paid stays with the row (no column for it): a card collection has no drawer movement
            said = (str(note).strip() if note else "")[:200]
            how = self.COLLECTION_METHODS[method] + (f" ({ch['name']})" if ch else "") + (f" — {said}" if said else "")
            lid = new_id()
            self.db.execute("INSERT INTO customer_ledger (id, org_id, customer_id, amount, kind, note,"
                            " created_by) VALUES (?,?,?,?, 'payment', ?, ?)",
                            (lid, ctx.org_id, customer_id, -amount, how, ctx.user_id))
            if method != "cash":                      # card / wallet money lands in its own account (F105)
                from ..finance import money
                money.receive(self.db, ctx, method, ch["id"] if ch else None, amount, "customer_payment",
                              ref_type="customer_ledger", ref_id=lid, note=how)
            audit.record(self.db, ctx, "customer.payment", "customer", customer_id,
                         after={"amount": amount, "method": method})
        return self.balance(ctx, customer_id)

    # ------------------------------------------------------------ draft
    def create(self, ctx: Context, customer_id: str | None = None) -> str:
        ctx.require("sales.create")
        if not ctx.branch_id:
            raise ValidationFailed("لا يوجد فرع محدد")
        if customer_id and not self.db.one("SELECT 1 FROM customers WHERE id=? AND org_id=?",
                                           (customer_id, ctx.org_id)):
            raise NotFound("العميل غير موجود")
        sid = new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO sales (id, org_id, branch_id, customer_id, created_by)"
                            " VALUES (?,?,?,?,?)", (sid, ctx.org_id, ctx.branch_id, customer_id, ctx.user_id))
        return sid

    def _draft(self, ctx: Context, sid: str) -> dict:
        s = self.db.one("SELECT * FROM sales WHERE id=? AND org_id=?", (sid, ctx.org_id))
        if not s:
            raise NotFound("الفاتورة غير موجودة")
        if s["status"] != "draft":
            raise Conflict("الفاتورة مكتملة أو ملغاة", code="not_draft")
        # before any write: an open invoice is changed by whoever may open it (its seller, or sales.totals /
        # an approver). The check used to come only with the answer, after the change was already saved.
        self._may_view(ctx, dict(s))
        return dict(s)

    def _stock_rows(self, ctx: Context, branch_id: str, item_id: str) -> list[dict]:
        """Batches with stock, FEFO, each marked `expired` and `recalled` (a recall the pharmacy confirmed
        for this batch: status 'confirmed'; 'pending' and 'not_ours' do not hold the stock, and 'handled'
        holds only units of the lot that came in after it was handled: the same recalled lot received again
        by any way, a purchase, a return or a count, #324 S1). Compared at the ledger's millisecond precision,
        a receipt in the same millisecond as the decision counts as after it (the safe side). A batch owned
        by the hospital (0039: E-Stock's store 2, owner 19/11) is never the pharmacy's to sell: left out."""
        if not hasattr(self, "_owner_sql"):      # 0039 present? (a database before it has no batch owner)
            has = any(r["name"] == "owner" for r in self.db.all("PRAGMA table_info(batches)"))
            self._owner_sql = " AND coalesce(b.owner,'pharmacy')='pharmacy'" if has else ""
        rows = self.db.all(
            "SELECT sb.batch_key, sb.qty, b.batch_no, b.expiry_date, b.public_price,"
            " EXISTS (SELECT 1 FROM recall_matches m WHERE m.org_id=sb.org_id AND m.batch_id=sb.batch_key"
            "         AND (m.status='confirmed' OR (m.status='handled' AND EXISTS ("
            "              SELECT 1 FROM stock_movements sm WHERE sm.org_id=sb.org_id AND sm.branch_id=sb.branch_id"
            "              AND sm.item_id=sb.item_id AND sm.batch_id=m.batch_id"
            "              AND sm.qty_delta>0 AND sm.created_at >= strftime('%Y-%m-%dT%H:%M:%fZ', m.decided_at))))) recalled"
            " FROM stock_balances sb LEFT JOIN batches b ON b.id = nullif(sb.batch_key,'')"
            " WHERE sb.org_id=? AND sb.branch_id=? AND sb.item_id=? AND sb.qty > 0" + self._owner_sql +
            " ORDER BY b.expiry_date IS NULL, b.expiry_date", (ctx.org_id, branch_id, item_id))
        today = self.today.isoformat()
        return [{**dict(r), "expired": bool(r["expiry_date"] and r["expiry_date"] < today)} for r in rows]

    def _sellable(self, ctx: Context, branch_id: str, item_id: str) -> list[dict]:
        """Batches with stock, FEFO; expired and recalled ones excluded (counted separately)."""
        return [r for r in self._stock_rows(ctx, branch_id, item_id) if not r["expired"] and not r["recalled"]]

    def _held(self, ctx: Context, branch_id: str, item_id: str) -> tuple[int, int, int]:
        """(sellable, expired, recalled) base units; a batch both expired and recalled counts as expired."""
        rows = self._stock_rows(ctx, branch_id, item_id)
        expired = sum(r["qty"] for r in rows if r["expired"])
        recalled = sum(r["qty"] for r in rows if r["recalled"] and not r["expired"])
        return sum(r["qty"] for r in rows) - expired - recalled, expired, recalled

    def _available(self, ctx: Context, branch_id: str, item_id: str) -> tuple[int, int]:
        sellable, expired, recalled = self._held(ctx, branch_id, item_id)
        return sellable, expired + recalled

    @staticmethod
    def _short(sellable: int, expired: int, recalled: int) -> Conflict:
        msg = f"الكمية المتاحة للبيع {sellable} وحدة"
        if expired:
            msg += f" (يوجد {expired} وحدة منتهية الصلاحية لا تُباع)"
        if recalled:
            msg += f" (يوجد {recalled} وحدة من تشغيلة عليها منشور سحب مؤكد لا تُباع)"
        return Conflict(msg, code="insufficient_stock",
                        details={"available": sellable, "expired": expired, **({"recalled": recalled} if recalled else {})})

    def add_line(self, ctx: Context, sid: str, item_id: str, *, packs: int = 0, units: int = 0,
                 discount_bp: int = 0, client_ref: str | None = None) -> dict:
        """client_ref: id generated by the cashier screen per scan; a retried or
        double-clicked request with the same ref does not add a second line."""
        ctx.require("sales.create")
        if client_ref and (',' in client_ref or len(client_ref) > 64):
            raise ValidationFailed("client_ref غير صحيح")
        if client_ref and self.db.one("SELECT 1 FROM sale_lines WHERE sale_id=? AND org_id=?"
                                      " AND instr(',' || client_ref || ',', ?) > 0", (sid, ctx.org_id, f",{client_ref},")):
            return self.get(ctx, sid)
        if packs < 0 or units < 0 or packs + units == 0:
            raise ValidationFailed("الكمية غير صحيحة")
        if discount_bp:
            if not 0 < discount_bp <= 10000:
                raise ValidationFailed("نسبة الخصم غير صحيحة")
            if discount_bp > ctx.discount_limit_bp:
                raise discount_denied(ctx, discount_bp)
        with self.db.transaction():
            s = self._draft(ctx, sid)
            item = self.db.one("SELECT * FROM items WHERE id=? AND org_id=? AND status='active'",
                               (item_id, ctx.org_id))
            if not item:
                raise NotFound("الصنف غير موجود")
            if item["hidden"] and not ctx.allowed("items.hidden"):
                raise NotFound("الصنف غير موجود")
            qty = packs * item["pack_size"] + units
            already = self.db.one("SELECT coalesce(sum(qty_base),0) q FROM sale_lines WHERE sale_id=?"
                                  " AND item_id=?", (sid, item_id))["q"]
            sellable, expired, recalled = (10 ** 9, 0, 0) if item["is_service"] else self._held(ctx, s["branch_id"], item_id)
            if already + qty > sellable:
                raise self._short(sellable, expired, recalled)
            # the same item again (scanned twice, quick button tapped twice) goes on its line, so whole
            # packs are priced as packs and not as loose units on separate lines
            # only into a line with the same discount (final review P2-3): units scanned without a discount never
            # take a discount someone typed (or approved) for the line before. A contract line takes them: the
            # contract's discount is the customer's on every unit and is set again below.
            same = self.db.one("SELECT id, qty_base, client_ref FROM sale_lines WHERE sale_id=? AND item_id=?"
                               " AND ((discount_bp=? AND coalesce(discount_source, '')<>'contract')"
                               "      OR (?=0 AND discount_source='contract'))"
                               " ORDER BY line_no DESC LIMIT 1", (sid, item_id, discount_bp, discount_bp))
            if same:
                total = same["qty_base"] + qty
                refs = ",".join(filter(None, [*(same["client_ref"] or "").split(",")[-19:], client_ref])) or None
                self.db.execute("UPDATE sale_lines SET qty_base=?, partial_pack=?, client_ref=? WHERE id=?",
                                (total, 1 if total % item["pack_size"] else 0, refs, same["id"]))
                if s["customer_id"]:
                    from . import contracts
                    contracts.apply(self.db, sid)
                self.db.execute("UPDATE sales SET version=version+1 WHERE id=?", (sid,))
                return self.get(ctx, sid)
            n = self.db.one("SELECT coalesce(max(line_no),0)+1 n FROM sale_lines WHERE sale_id=?", (sid,))["n"]
            partial = 1 if units % item["pack_size"] else 0
            self.db.execute("INSERT INTO sale_lines (id, sale_id, org_id, line_no, item_id, qty_base,"
                            " discount_bp, partial_pack, client_ref, discount_by, discount_approved_by)"
                            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (new_id(), sid, ctx.org_id, n, item_id, qty, discount_bp, partial,
                             client_ref, ctx.user_id if discount_bp else None,
                             ctx.approver_id if discount_bp and ctx.discount_limit_bp > ctx.max_discount_bp
                             else None))
            if s["customer_id"]:
                from . import contracts
                contracts.apply(self.db, sid)
            self.db.execute("UPDATE sales SET version=version+1 WHERE id=?", (sid,))
        return self.get(ctx, sid)

    def remove_line(self, ctx: Context, sid: str, line_id: str) -> dict:
        ctx.require("sales.create")
        with self.db.transaction():
            self._draft(ctx, sid)
            n = self.db.execute("DELETE FROM sale_lines WHERE id=? AND sale_id=?", (line_id, sid)).rowcount
            if not n:
                raise NotFound("السطر غير موجود")
            self.db.execute("UPDATE sales SET version=version+1 WHERE id=?", (sid,))
        return self.get(ctx, sid)

    # ------------------------------------------------------------ pricing preview
    def _plan(self, ctx: Context, s: dict, line: dict, item: dict, used: dict | None = None) -> list[dict]:
        """`used` (preview of a draft): units the earlier lines of this invoice already take from each batch,
        so a second line of the same item is priced from the batch it will really leave from. At completion
        the earlier lines have already left the stock, so nothing is passed."""
        need = line["qty_base"]
        if item.get("is_service"):          # sold without stock: one allocation, no batch, no movement
            pp = item["public_price"]
            return [{"batch_id": None, "batch_no": None, "expiry_date": None, "qty": need,
                     "unit_price": unit_price(pp, item["pack_size"]) if pp is not None else None,
                     "pack_price": pp, "pack_size": item["pack_size"], "price_source": "item"}]
        plan = []
        for b in self._sellable(ctx, s["branch_id"], line["item_id"]):
            if need == 0:
                break
            free = b["qty"] - (used.get((line["item_id"], b["batch_key"]), 0) if used is not None else 0)
            take = min(need, free)
            if take <= 0:
                continue
            if used is not None:
                used[(line["item_id"], b["batch_key"])] = used.get((line["item_id"], b["batch_key"]), 0) + take
            pack_price = b["public_price"] if b["public_price"] is not None else item["public_price"]
            plan.append({"batch_id": b["batch_key"] or None, "batch_no": b["batch_no"],
                         "expiry_date": b["expiry_date"], "qty": take,
                         "unit_price": unit_price(pack_price, item["pack_size"]) if pack_price is not None else None,
                         "pack_price": pack_price, "pack_size": item["pack_size"],
                         "price_source": "batch" if b["public_price"] is not None else "item"})
            need -= take
        if need:
            raise Conflict("الكمية لم تعد متاحة (ربما بيعت من شاشة أخرى)", code="insufficient_stock")
        return plan

    def get(self, ctx: Context, sid: str) -> dict:
        ctx.require("sales.read")
        s = self.db.one("SELECT * FROM sales WHERE id=? AND org_id=?", (sid, ctx.org_id))
        if not s:
            raise NotFound("الفاتورة غير موجودة")
        s = dict(s)
        self._may_view(ctx, s)
        lines = []
        subtotal = discount = 0
        used: dict = {}
        for l in self.db.all("SELECT * FROM sale_lines WHERE sale_id=? ORDER BY line_no", (sid,)):
            l = dict(l)
            item = dict(self.db.one("SELECT * FROM items WHERE id=?", (l["item_id"],)))
            cat = self.db.one("SELECT name, mark, no_return FROM categories WHERE id=?",
                              (item["category_id"],)) if item.get("category_id") else None
            l["item"] = {"name": item["name_en"] or item["name_ar"], "name_en": item["name_en"],
                         "name_ar": item["name_ar"], "strength": item["strength_norm"],
                         "form": item["dosage_form"], "pack_size": item["pack_size"],
                         "base_unit": item["base_unit_name"], "pack_unit": item["pack_unit_name"],
                         "active_ingredient": item["active_ingredient"],
                         "rx_required": bool(item.get("rx_required")), "misuse_risk": bool(item.get("misuse_risk")),
                         "sale_note": item.get("sale_note"), "is_service": bool(item.get("is_service")),
                         "hide_on_receipt": bool(item.get("hide_on_receipt")),
                         "mark": cat["mark"] if cat else None, "no_return": bool(cat["no_return"]) if cat else False}
            if s["status"] == "draft":
                try:
                    plan = self._plan(ctx, s, l, item, used)
                except Conflict:
                    plan, l["problem"] = [], "insufficient_stock"
            else:
                plan = [dict(a) for a in self.db.all(
                    "SELECT a.*, b.batch_no, b.expiry_date FROM sale_allocations a LEFT JOIN batches b"
                    " ON b.id=a.batch_id WHERE a.sale_line_id=?", (l["id"],))]
                for a in plan:
                    a["qty"] = a.pop("qty_base")
                    if not ctx.can("costs.read"):
                        a.pop("unit_cost", None)     # the average cost is not the cashier's to see (#324 X13)
            l["allocations"] = plan
            if any(a.get("unit_price") is None for a in plan):
                l["problem"] = "no_price"
            gross = line_gross((a["qty"], a) for a in plan if a.get("unit_price") is not None)
            disc = discount_of(gross, l["discount_bp"])
            l["gross"], l["discount"], l["net"] = gross, disc, gross - disc
            if any(a.get("expiry_date") and (date.fromisoformat(a["expiry_date"]) - self.today).days
                   < NEAR_EXPIRY_DAYS for a in plan):
                l["near_expiry"] = True
            subtotal += gross
            discount += disc
            lines.append(l)
        from ..printing import settings as ps
        s["line_order"] = line_order(self.db, ctx.org_id)
        s["lines"] = sort_lines(lines, s["line_order"], ps.get(self.db, ctx.org_id).get("names", "en"))
        if s["status"] == "draft":
            s["subtotal"], s["discount_total"] = subtotal, discount
            s["total"] = subtotal - discount + (s["delivery_fee"] or 0)
        d = self.db.one("SELECT * FROM sale_deliveries WHERE sale_id=?", (sid,))
        s["delivery"] = dict(d) if d else None
        from .attachments import listing as att_listing
        s["attachments"] = att_listing(self.db, ctx, sid)
        s["returns"] = [dict(r) for r in self.db.all(
            "SELECT r.id, r.total, r.refund_method, r.reason, r.created_at, u.full_name by_name FROM sale_returns r"
            " LEFT JOIN users u ON u.id=r.created_by WHERE r.sale_id=? ORDER BY r.created_at", (sid,))]
        s["payments"] = [dict(r) for r in self.db.all(
            "SELECT p.method, p.amount, c.name channel FROM sale_payments p LEFT JOIN payment_channels c"
            " ON c.id=p.channel_id WHERE p.sale_id=? ORDER BY p.rowid", (sid,))]
        if s["customer_id"]:
            c = self.db.one("SELECT id, name, name_alt, phone, address1, credit_limit FROM customers WHERE id=?", (s["customer_id"],))
            s["customer"] = {**dict(c), "balance": self.balance(ctx, c["id"])}
        if s.get("contract_id"):
            k = self.db.one("SELECT id, name, discount_local_bp, discount_imported_bp FROM contracts WHERE id=?", (s["contract_id"],))
            s["contract"] = dict(k) if k else None
        return s

    @staticmethod
    def _may_view(ctx: Context, s: dict) -> None:
        """An employee sees his own invoices; other people's need sales.totals (or an
        approver, e.g. to return someone else's invoice)."""
        if s["created_by"] != ctx.user_id and not ctx.can("sales.totals"):
            ctx.require("sales.return_others")

    # ------------------------------------------------------------ safety + complete
    def safety_check(self, ctx: Context, sid: str) -> list[Finding]:
        s = self.get(ctx, sid)
        found = self.safety.check(ctx, [{"item_id": l["item_id"], "base_qty": l["qty_base"],
                                         "partial_pack": bool(l["partial_pack"])} for l in s["lines"]],
                                  context_type="sale", context_id=sid)
        return item_warnings(self.db, [l["item_id"] for l in s["lines"]], org_id=ctx.org_id,
                             branch_id=s["branch_id"]) + found   # the owner's marks first

    def complete(self, ctx: Context, sid: str, *, paid_cash: int = 0, paid_card: int = 0,
                 on_credit: int = 0, safety_decisions: dict[int, str] | None = None,
                 version: int | None = None, request_id: str | None = None,
                 confirm_below_cost: bool = False, payments: list[dict] | None = None) -> dict:
        """request_id: generated once per 'complete' click. Retrying with the same
        id after a lost response returns the completed sale instead of failing,
        and can never complete it twice."""
        ctx.require("sales.create")
        if request_id:
            done = self.db.one("SELECT status, complete_request_id FROM sales WHERE id=? AND org_id=?",
                               (sid, ctx.org_id))
            if done and done["status"] == "completed" and done["complete_request_id"] == request_id:
                out = self.get(ctx, sid)
                out["replayed"] = True
                return out
        self.get(ctx, sid)        # the caller may see this invoice (_may_view) before anything is written to it
        if self._reprice_contract(ctx, sid):
            # a contract stopped, changed or unlinked since the draft was priced: the draft now carries
            # today's terms and the cashier confirms the new total, as with any price that moved
            raise Conflict("تغيّر خصم التعاقد على هذه الفاتورة، راجع الإجمالي الجديد ثم أتمم البيع",
                           code="price_changed", details={"reason": "contract"})
        if payments is None:          # older screens: three amounts
            payments = [{"method": m, "amount": a} for m, a in
                        (("cash", paid_cash), ("card", paid_card), ("credit", on_credit)) if a]
            if min(paid_cash, paid_card, on_credit) < 0:
                raise ValidationFailed("مبالغ الدفع لا تكون بالسالب")
        payments = self._check_payments(ctx, payments)
        paid_cash = sum(p["amount"] for p in payments if p["method"] == "cash")
        paid_card = sum(p["amount"] for p in payments if p["method"] == "card")
        paid_transfer = sum(p["amount"] for p in payments if p["method"] == "transfer")
        on_credit = sum(p["amount"] for p in payments if p["method"] == "credit")
        preview = self.get(ctx, sid)
        delivery = preview["kind"] == "delivery"
        if preview["status"] != "draft":
            raise Conflict("الفاتورة مكتملة أو ملغاة", code="not_draft")
        if version is not None and preview["version"] != version:
            raise Conflict("عُدّلت الفاتورة، حدّث الشاشة", code="stale_version")
        if not preview["lines"]:
            raise ValidationFailed("الفاتورة فارغة")
        bad = [l for l in preview["lines"] if l.get("problem")]
        if bad:
            raise Conflict("توجد أصناف غير متاحة أو بدون سعر", code="line_problems",
                           details={"lines": [{"line_no": l["line_no"], "problem": l["problem"]} for l in bad]})
        # every store sale belongs to a shift, card, transfer or credit too: a sale with no shift appears in no
        # shift report, so its card and credit amounts escaped the close (final review P2-2)
        if not delivery and not cash.open_shift_id(self.db, ctx, preview["branch_id"]):
            raise Conflict("الخزينة مغلقة — افتح شيفت قبل البيع", code="no_open_shift")
        if delivery and not (preview["delivery"] and (preview["delivery"]["address"] or preview["delivery"]["phone"])):
            raise ValidationFailed("فاتورة التوصيل تحتاج إلى العنوان أو رقم الهاتف", code="delivery_details")
        if paid_cash + paid_card + paid_transfer + on_credit != preview["total"]:
            raise ValidationFailed("مجموع الدفع لا يساوي إجمالي الفاتورة", code="payment_mismatch",
                                   details={"total": preview["total"]})
        if on_credit:
            if not preview["customer_id"]:
                raise ValidationFailed("البيع الآجل يحتاج تحديد العميل", code="customer_required")
            c = preview["customer"]
            if c["balance"] + on_credit > c["credit_limit"]:
                raise Conflict("المبلغ يتجاوز حد الآجل للعميل", code="credit_limit",
                               details={"balance": c["balance"], "limit": c["credit_limit"]})
        findings = self.safety_check(ctx, sid)
        if not self.safety.resolve(ctx, findings, safety_decisions or {}, context_type="sale",
                                   context_id=sid):
            raise Conflict("تنبيهات سلامة دوائية تحتاج قرار قبل إتمام البيع", code="safety_unresolved",
                           details={"findings": [f.as_dict() for f in findings]})
        with self.db.transaction():
            s = self._draft(ctx, sid)
            if s["version"] != preview["version"]:
                raise Conflict("عُدّلت الفاتورة أثناء الإتمام", code="stale_version")
            # the contract terms again under the posting lock: a rate changed after the check above would
            # otherwise post at the old price (#324 M2-08)
            if self._reprice_contract(ctx, sid):
                raise Conflict("تغيّر خصم التعاقد على هذه الفاتورة، راجع الإجمالي الجديد ثم أتمم البيع",
                               code="price_changed", details={"reason": "contract"})
            if on_credit:            # #143: again inside the write lock, so two cashiers cannot pass it together
                c = self.db.one("SELECT credit_limit FROM customers WHERE id=? AND org_id=?",
                                (s["customer_id"], ctx.org_id))
                bal = self.balance(ctx, s["customer_id"])
                if not c or bal + on_credit > c["credit_limit"]:
                    raise Conflict("المبلغ يتجاوز حد الآجل للعميل", code="credit_limit",
                                   details={"balance": bal, "limit": c["credit_limit"] if c else 0})
            total = subtotal = discount = 0
            below_cost = []
            for l in preview["lines"]:
                item = dict(self.db.one("SELECT * FROM items WHERE id=?", (l["item_id"],)))
                plan = self._plan(ctx, s, l, item)          # re-plan inside the transaction
                cost_row = self.db.one("SELECT avg_cost FROM item_costs WHERE org_id=? AND branch_id=?"
                                       " AND item_id=?", (ctx.org_id, s["branch_id"], l["item_id"]))
                for a in plan:
                    if not item["is_service"]:
                        inventory.post_movement(self.db, ctx, item_id=l["item_id"], qty_delta=-a["qty"],
                                                batch_id=a["batch_id"], movement_type="sale",
                                                unit_cost=None, ref_type="sale", ref_id=sid,
                                                ref_line_id=l["id"], branch_id=s["branch_id"])
                    self.db.execute("INSERT INTO sale_allocations (id, sale_line_id, batch_id, qty_base,"
                                    " unit_price, unit_cost, pack_price, pack_size) VALUES (?,?,?,?,?,?,?,?)",
                                    (new_id(), l["id"], a["batch_id"], a["qty"], a["unit_price"],
                                     cost_row["avg_cost"] if cost_row else None, a["pack_price"], a["pack_size"]))
                gross = line_gross((a["qty"], a) for a in plan)
                disc = discount_of(gross, l["discount_bp"])
                net = gross - disc
                # each line against the one the cashier confirmed: two price changes that cancel out keep the
                # total but not the lines (#324 M2-07)
                if (gross, disc) != (l["gross"], l["discount"]):
                    raise Conflict("تغيّر السعر أثناء الإتمام، راجع الفاتورة", code="price_changed")
                subtotal += gross
                discount += disc
                if l["discount_bp"] and cost_row and cost_row["avg_cost"] is not None:
                    cost = Decimal(cost_row["avg_cost"]) * l["qty_base"] * 100   # avg_cost is EGP per base unit
                    if Decimal(net) < cost:
                        below_cost.append({"line_no": l["line_no"], "name": l["item"]["name"],
                                           "net": net, "cost": cost_piasters(cost)})
                self.db.execute("UPDATE sale_lines SET line_total=? WHERE id=?", (net, l["id"]))
                total += net
            if below_cost:
                ctx.require("sales.below_cost")
                if not confirm_below_cost:
                    raise Conflict("الخصم ينزل بالسعر عن التكلفة — هذا البيع بخسارة", code="below_cost_confirm",
                                   details={"lines": below_cost})
            total += s["delivery_fee"] or 0
            if total != preview["total"]:
                raise Conflict("تغيّر السعر أثناء الإتمام، راجع الفاتورة", code="price_changed")
            no = self._next_no(ctx, s["branch_id"], "sale")
            if delivery:              # the courier carries the cash until he hands it in
                shift = cash.open_shift_id(self.db, ctx, s["branch_id"])
                self.db.execute("UPDATE sale_deliveries SET status='out', cash_due=?, out_at=? WHERE sale_id=?",
                                (paid_cash, _now(), sid))
            else:
                shift = cash.record(self.db, ctx, paid_cash, "sale", sid, branch_id=s["branch_id"]) \
                    or cash.require_open_shift(self.db, ctx, s["branch_id"])   # again inside the write lock
            self.db.execute("UPDATE sales SET shift_id=? WHERE id=?", (shift, sid))
            from ..finance import money
            for p in payments:
                self.db.execute("INSERT INTO sale_payments (id, org_id, sale_id, method, channel_id, amount)"
                                " VALUES (?,?,?,?,?,?)", (new_id(), ctx.org_id, sid, p["method"],
                                                          p.get("channel_id"), p["amount"]))
                if p["method"] in money.METHODS:            # card / wallet money lands in its own account (F105)
                    money.receive(self.db, ctx, p["method"], p.get("channel_id"), p["amount"], "sale",
                                  ref_type="sale", ref_id=sid, note=f"فاتورة {no}")
            self.db.execute("UPDATE sales SET status='completed', sale_no=?, subtotal=?, discount_total=?,"
                            " total=?, paid_cash=?, paid_card=?, paid_transfer=?, on_credit=?, completed_at=?,"
                            " complete_request_id=?, version=version+1 WHERE id=?",
                            (no, subtotal, discount, total, paid_cash,
                             paid_card, paid_transfer, on_credit, _now(), request_id, sid))
            if paid_transfer:   # a proof already reviewed on the draft counts now the transfer is on its account (#348)
                from .attachments import transfer_state
                money.settle_transfer_review(self.db, ctx, sid,
                                             transfer_state(self.db, ctx.org_id, sid, paid_transfer)["received"])
            if on_credit:
                self.db.execute("INSERT INTO customer_ledger (id, org_id, customer_id, amount, kind, ref_id,"
                                " created_by) VALUES (?,?,?,?, 'credit_sale', ?, ?)",
                                (new_id(), ctx.org_id, s["customer_id"], on_credit, sid, ctx.user_id))
            audit.record(self.db, ctx, "sale.complete", "sale", sid,
                         after={"no": no, "total": total, "cash": paid_cash, "card": paid_card,
                                "transfer": paid_transfer, "credit": on_credit, "kind": s["kind"],
                                "below_cost": below_cost or None})
            from . import customers as _cust
            _cust.mark_refilled(self.db, sid)
            disc_lines = [l for l in preview["lines"] if l["discount_bp"] and l.get("discount_source") != "contract"]
            if disc_lines:
                names = "، ".join(f"{l['item']['name']} {l['discount_bp'] / 100:g}%" for l in disc_lines[:4])
                notify.emit(self.db, ctx, "discount", "warning",
                            f"خصم {egp(preview['discount_total'])} ج — فاتورة {no}", names, "sale", sid)
            if below_cost:
                notify.emit(self.db, ctx, "below_cost", "error", f"بيع تحت التكلفة — فاتورة {no}",
                            "، ".join(f"{b['name']}: {egp(b['net'])} والتكلفة {egp(b['cost'])}"
                                     for b in below_cost[:4]), "sale", sid)
        return self.get(ctx, sid)

    def _reprice_contract(self, ctx: Context, sid: str) -> bool:
        """Put the customer's contract terms of this moment on a draft (saved); True when a line's discount
        changed, so the total the cashier saw is no longer the total."""
        from . import contracts
        q = ("SELECT l.id, l.discount_bp, l.discount_source FROM sale_lines l JOIN sales s ON s.id=l.sale_id"
             " WHERE l.sale_id=? AND s.org_id=? ORDER BY l.line_no")

        def state():
            head = self.db.one("SELECT contract_id FROM sales WHERE id=? AND org_id=?", (sid, ctx.org_id))
            return head["contract_id"], [tuple(r) for r in self.db.all(q, (sid, ctx.org_id))]

        with self.db.transaction():
            s = self.db.one("SELECT status FROM sales WHERE id=? AND org_id=?", (sid, ctx.org_id))
            if not s or s["status"] != "draft":
                return False                      # the checks below answer for a missing or finished sale
            before = state()
            contracts.apply(self.db, sid)
            if state() == before:
                return False
            if state()[1] != before[1]:           # the header alone (contract tag) is not a new price
                self.db.execute("UPDATE sales SET version=version+1 WHERE id=? AND org_id=?", (sid, ctx.org_id))
            audit.record(self.db, ctx, "sale.contract_repriced", "sale", sid,
                         before={"contract": before[0], "lines": [[bp, src] for _, bp, src in before[1]]},
                         after={"contract": state()[0], "lines": [[bp, src] for _, bp, src in state()[1]]})
            return state()[1] != before[1]

    def _next_no(self, ctx: Context, branch_id: str, kind: str) -> int:
        self.db.execute("INSERT INTO branch_counters (org_id, branch_id, kind, next_no) VALUES (?,?,?,1)"
                        " ON CONFLICT(org_id, branch_id, kind) DO NOTHING", (ctx.org_id, branch_id, kind))
        n = self.db.one("SELECT next_no FROM branch_counters WHERE org_id=? AND branch_id=? AND kind=?",
                        (ctx.org_id, branch_id, kind))["next_no"]
        self.db.execute("UPDATE branch_counters SET next_no=next_no+1 WHERE org_id=? AND branch_id=? AND kind=?",
                        (ctx.org_id, branch_id, kind))
        return n

    # ------------------------------------------------------------ payments
    def _check_payments(self, ctx: Context, payments) -> list[dict]:
        # an empty list is a sale worth nothing (100% discount, #324 M11): the total check below still refuses
        # it for any sale that costs something
        if not isinstance(payments, list) or len(payments) > 6:
            raise ValidationFailed("طريقة الدفع غير صحيحة")
        channels = {r["id"]: dict(r) for r in self.db.all(
            "SELECT * FROM payment_channels WHERE org_id=? AND is_active=1", (ctx.org_id,))}
        out = []
        for p in payments:
            if not isinstance(p, dict):
                raise ValidationFailed("طريقة الدفع غير صحيحة")
            m, amount, ch = p.get("method"), p.get("amount"), p.get("channel_id") or None
            # true is not 1 piaster (#324 M2-19)
            if m not in ("cash", "card", "transfer", "credit") or isinstance(amount, bool) \
                    or not isinstance(amount, int) or amount < 0:
                raise ValidationFailed("طريقة الدفع غير صحيحة")
            if amount == 0:
                continue
            if m in ("card", "transfer"):
                kinds = [c for c in channels.values() if c["kind"] == m]
                if kinds and (ch not in channels or channels[ch]["kind"] != m):
                    raise ValidationFailed("اختر " + ("الجهاز" if m == "card" else "المحفظة / طريقة التحويل"),
                                           code="channel_required", details={"method": m})
                if not kinds:
                    ch = None
            else:
                ch = None
            out.append({"method": m, "amount": amount, "channel_id": ch})
        return out

    # ------------------------------------------------------------ open invoices (tabs) and details
    def list_drafts(self, ctx: Context) -> list[dict]:
        ctx.require("sales.create")
        out = []
        for r in self.db.all("SELECT id, kind, created_at FROM sales WHERE org_id=? AND branch_id=? AND"
                             " status='draft' AND created_by=? ORDER BY created_at", (ctx.org_id, ctx.branch_id,
                                                                                    ctx.user_id)):
            n = self.db.one("SELECT count(*) c FROM sale_lines WHERE sale_id=?", (r["id"],))["c"]
            out.append({**dict(r), "lines": n})
        return out

    def discard(self, ctx: Context, sid: str) -> None:
        """Close an open invoice tab: only a draft, and only by whoever opened it."""
        ctx.require("sales.create")
        with self.db.transaction():
            s = self._draft(ctx, sid)
            if s["created_by"] != ctx.user_id:
                raise Forbidden("هذه الفاتورة مفتوحة باسم شخص آخر")
            self.db.execute("UPDATE sales SET status='voided', voided_by=?, voided_at=?, void_reason=? WHERE id=?",
                            (ctx.user_id, _now(), "تبويب أُغلق قبل البيع", sid))
            self.db.execute("UPDATE sale_deliveries SET status='cancelled' WHERE sale_id=?", (sid,))

    def set_details(self, ctx: Context, sid: str, data: dict) -> dict:
        """On an open invoice: customer, store/delivery, delivery address/phone/courier/fee."""
        ctx.require("sales.create")
        with self.db.transaction():
            s = self._draft(ctx, sid)
            if "customer_id" in data:
                cid = data["customer_id"] or None
                if cid and not self.db.one("SELECT 1 FROM customers WHERE id=? AND org_id=?", (cid, ctx.org_id)):
                    raise NotFound("العميل غير موجود")
                self.db.execute("UPDATE sales SET customer_id=? WHERE id=?", (cid, sid))
                from . import contracts
                contracts.apply(self.db, sid)
            if "kind" in data:
                if data["kind"] not in ("store", "delivery"):
                    raise ValidationFailed("نوع الفاتورة غير صحيح")
                self.db.execute("UPDATE sales SET kind=? WHERE id=?", (data["kind"], sid))
                if data["kind"] == "delivery":
                    self.db.execute("INSERT OR IGNORE INTO sale_deliveries (sale_id, org_id) VALUES (?,?)",
                                    (sid, ctx.org_id))
                else:
                    # back to a store sale: its delivery draft goes with it, so nothing later (void, the courier
                    # lists) can mistake this sale for a delivery whose cash never reached the drawer
                    self.db.execute("UPDATE sales SET delivery_fee=0 WHERE id=?", (sid,))
                    self.db.execute("DELETE FROM sale_deliveries WHERE sale_id=? AND org_id=? AND status='draft'",
                                    (sid, ctx.org_id))
            d = data.get("delivery")
            if isinstance(d, dict):
                kind = self.db.one("SELECT kind FROM sales WHERE id=?", (sid,))["kind"]
                if kind != "delivery":
                    raise ValidationFailed("الفاتورة ليست توصيلًا")
                fee = d.get("fee", None)
                if fee is not None:
                    if isinstance(fee, bool) or not isinstance(fee, int) or not 0 <= fee <= 100000:   # #324 M2-19
                        raise ValidationFailed("رسوم التوصيل غير صحيحة")
                    self.db.execute("UPDATE sales SET delivery_fee=? WHERE id=?", (fee, sid))
                for k in ("address", "phone", "courier"):
                    if k in d:
                        self.db.execute(f"UPDATE sale_deliveries SET {k}=? WHERE sale_id=?",
                                        ((str(d[k] or "").strip()[:200] or None), sid))
            self.db.execute("UPDATE sales SET version=version+1 WHERE id=?", (sid,))
        return self.get(ctx, sid)

    def update_line(self, ctx: Context, sid: str, line_id: str, *, qty_base: int | None = None,
                    discount_bp: int | None = None) -> dict:
        ctx.require("sales.create")
        with self.db.transaction():
            s = self._draft(ctx, sid)
            l = self.db.one("SELECT * FROM sale_lines WHERE id=? AND sale_id=?", (line_id, sid))
            if not l:
                raise NotFound("السطر غير موجود")
            if qty_base is not None:
                if not isinstance(qty_base, int) or qty_base <= 0:
                    raise ValidationFailed("الكمية غير صحيحة")
                # more units at a typed discount is a bigger discount: the person changing the quantity must be
                # allowed that rate (or bring an approver), as when it was typed (final review P2-3)
                if qty_base > l["qty_base"] and discount_bp is None and l["discount_bp"] \
                        and l["discount_source"] != "contract":
                    if l["discount_bp"] > ctx.discount_limit_bp:
                        raise discount_denied(ctx, l["discount_bp"])
                    if l["discount_bp"] > ctx.max_discount_bp:
                        self.db.execute("UPDATE sale_lines SET discount_approved_by=? WHERE id=?",
                                        (ctx.approver_id, line_id))
                others = self.db.one("SELECT coalesce(sum(qty_base),0) q FROM sale_lines WHERE sale_id=? AND"
                                     " item_id=? AND id<>?", (sid, l["item_id"], line_id))["q"]
                svc = self.db.one("SELECT is_service FROM items WHERE id=?", (l["item_id"],))["is_service"]
                sellable, expired, recalled = (10 ** 9, 0, 0) if svc else self._held(ctx, s["branch_id"], l["item_id"])
                if others + qty_base > sellable:
                    raise self._short(sellable, expired, recalled)
                item = self.db.one("SELECT pack_size FROM items WHERE id=?", (l["item_id"],))
                self.db.execute("UPDATE sale_lines SET qty_base=?, partial_pack=? WHERE id=?",
                                (qty_base, 1 if qty_base % item["pack_size"] else 0, line_id))
            if discount_bp is not None:
                if not isinstance(discount_bp, int) or not 0 <= discount_bp <= 10000:
                    raise ValidationFailed("نسبة الخصم غير صحيحة")
                if discount_bp > ctx.discount_limit_bp:
                    raise discount_denied(ctx, discount_bp)
                self.db.execute("UPDATE sale_lines SET discount_bp=?, discount_source=NULL, discount_by=?, discount_approved_by=? WHERE id=?",
                                (discount_bp, ctx.user_id if discount_bp else None,
                                 ctx.approver_id if discount_bp and discount_bp > ctx.max_discount_bp else None, line_id))
            self.db.execute("UPDATE sales SET version=version+1 WHERE id=?", (sid,))
        return self.get(ctx, sid)

    # ------------------------------------------------------------ home delivery
    def delivery_status(self, ctx: Context, sid: str, status: str) -> dict:
        """out -> delivered -> settled. 'settled' = the courier handed the cash: it enters the drawer now."""
        ctx.require("sales.create")
        if status not in ("delivered", "settled"):
            raise ValidationFailed("الحالة غير صحيحة")
        with self.db.transaction():
            d = self.db.one("SELECT d.*, s.status sale_status, s.branch_id, s.sale_no FROM sale_deliveries d"
                            " JOIN sales s ON s.id=d.sale_id WHERE d.sale_id=? AND d.org_id=?", (sid, ctx.org_id))
            if not d or d["sale_status"] != "completed":
                raise NotFound("فاتورة التوصيل غير موجودة")
            order = ["out", "delivered", "settled"]
            if d["status"] not in order or order.index(status) <= order.index(d["status"]):
                raise Conflict("هذه الحالة سُجّلت من قبل", code="delivery_state")
            if status == "settled":
                shift = cash.record(self.db, ctx, d["cash_due"], "sale", sid, f"تسليم مبلغ توصيل الفاتورة {d['sale_no']}",
                                    branch_id=d["branch_id"]) or cash.open_shift_id(self.db, ctx, d["branch_id"])
                # completed while no shift was open: it belongs to the shift its cash came into (#324 M9)
                if shift:
                    self.db.execute("UPDATE sales SET shift_id=? WHERE id=? AND org_id=? AND shift_id IS NULL",
                                    (shift, sid, ctx.org_id))
                self.db.execute("UPDATE sale_deliveries SET status='settled', settled_at=?, settled_by=?,"
                                " delivered_at=coalesce(delivered_at, ?) WHERE sale_id=?",
                                (_now(), ctx.user_id, _now(), sid))
            else:
                self.db.execute("UPDATE sale_deliveries SET status='delivered', delivered_at=? WHERE sale_id=?",
                                (_now(), sid))
            audit.record(self.db, ctx, f"delivery.{status}", "sale", sid, after={"cash": d["cash_due"]})
        # committed: a cashier who may settle but not open another cashier's invoice still gets a success,
        # else the 403 released the retry key and the retry answered delivery_state (#324 M3)
        try:
            return self.get(ctx, sid)
        except Forbidden:
            return {"id": sid, "sale_no": d["sale_no"], "delivery": {"status": status}}

    def open_deliveries(self, ctx: Context) -> list[dict]:
        ctx.require("sales.create")
        return [dict(r) for r in self.db.all(
            "SELECT d.*, s.sale_no, s.total, s.completed_at, c.name customer FROM sale_deliveries d JOIN sales s"
            " ON s.id=d.sale_id LEFT JOIN customers c ON c.id=s.customer_id WHERE d.org_id=? AND s.branch_id=?"
            " AND d.status IN ('out','delivered') ORDER BY s.completed_at", (ctx.org_id, ctx.branch_id))]

    # ------------------------------------------------------------ returns / void
    def return_items(self, ctx: Context, sid: str, lines: dict[str, int], *, refund_method: str,
                     reason: str) -> dict:
        """lines: {sale_line_id: base_units_returned}. Stock goes back to the
        same batches it left from (latest-taken first)."""
        ctx.require("sales.return")
        if refund_method not in ("cash", "card", "credit"):
            raise ValidationFailed("طريقة الاسترداد غير صحيحة")
        if len((reason or "").strip()) < 3:
            raise ValidationFailed("اكتب سبب المرتجع")
        if not isinstance(lines, dict) or not lines:      # a return of nothing is not a return
            raise ValidationFailed("اختر الأصناف والكميات المرتجعة", code="empty_return")
        with self.db.transaction():
            s = self.db.one("SELECT * FROM sales WHERE id=? AND org_id=?", (sid, ctx.org_id))
            if not s:
                raise NotFound("الفاتورة غير موجودة")
            if s["status"] != "completed":
                raise Conflict("المرتجع يكون من فاتورة مكتملة فقط")
            if s["created_by"] != ctx.user_id:
                ctx.require("sales.return_others")
            days = return_window_days(self.db, ctx.org_id)
            age = (self.today - _local_date(s["completed_at"])).days
            if age > days:
                ctx.require("sales.return_late")
            if refund_method == "credit" and not s["customer_id"]:
                raise ValidationFailed("الاسترداد على الحساب يحتاج عميل")
            if s["kind"] == "delivery":
                # the courier may still hold the money (or bring the goods back): a return before the settlement
                # would refund, restock or reduce a debt for a sale whose outcome is not known yet
                d = self.db.one("SELECT status FROM sale_deliveries WHERE sale_id=? AND org_id=?", (sid, ctx.org_id))
                if not d or d["status"] != "settled":
                    raise Conflict("لم تُسوَّ فاتورة التوصيل بعد؛ سجّل تسليم مبلغ التوصيل أولًا ثم أجرِ المرتجع",
                                   code="delivery_not_settled")
            refund = 0
            line_refunds: dict[str, int] = {}
            history: list[dict] | None = None
            restricted: list[str] = []
            for line_id, qty in lines.items():
                if not isinstance(qty, int) or qty <= 0:
                    raise ValidationFailed("كمية المرتجع غير صحيحة")
                l = self.db.one("SELECT * FROM sale_lines WHERE id=? AND sale_id=?", (line_id, sid))
                if not l:
                    raise NotFound("السطر غير موجود في الفاتورة")
                nr = self.db.one("SELECT c.name, i.name_en, i.name_ar FROM items i JOIN categories c ON c.id=i.category_id"
                                 " WHERE i.id=? AND c.no_return=1", (l["item_id"],))
                if nr:
                    restricted.append(f"{nr['name_en'] or nr['name_ar']} ({nr['name']})")
                    if not ctx.allowed("sales.return_restricted"):
                        raise Forbidden(f"هذا الصنف من قسم «{nr['name']}» ولا يُقبل إرجاعه ولا استبداله",
                                        code="approval_required",
                                        details={"permission": "sales.return_restricted",
                                                 "label": f"مرتجع صنف من قسم «{nr['name']}»"})
                allocs = [dict(a) for a in self.db.all(
                    "SELECT *, rowid rid FROM sale_allocations WHERE sale_line_id=? ORDER BY rowid DESC", (line_id,))]
                is_service = bool(self.db.one("SELECT is_service FROM items WHERE id=?", (l["item_id"],))["is_service"])
                remaining_returnable = sum(a["qty_base"] - a["returned_qty"] for a in allocs)
                if qty > remaining_returnable:
                    raise Conflict(f"أقصى كمية يمكن إرجاعها {remaining_returnable}", code="return_exceeds")
                left = qty
                kept = {a["id"]: a["qty_base"] - a["returned_qty"] for a in allocs}
                before = kept_value(allocs, kept, l["discount_bp"], l["line_total"])
                if history is None:
                    history = returns_history(self.db, ctx.org_id, [sid]).get(sid, [])
                rest = outstanding_value(allocs, kept, l["discount_bp"], l["line_total"], line_id, history)
                for a in allocs:
                    can = a["qty_base"] - a["returned_qty"]
                    take = min(can, left)
                    if take <= 0:
                        continue
                    if not is_service:
                        inventory.post_movement(self.db, ctx, item_id=l["item_id"], qty_delta=take,
                                                batch_id=a["batch_id"], movement_type="sale_return",
                                                ref_type="sale", ref_id=sid, ref_line_id=line_id,
                                                branch_id=s["branch_id"], note=reason)
                    self.db.execute("UPDATE sale_allocations SET returned_qty=returned_qty+? WHERE id=?",
                                    (take, a["id"]))
                    kept[a["id"]] -= take
                    left -= take
                    if left == 0:
                        break
                # refund = what the kept units were worth before minus after: all the returns of a
                # line add up to exactly what was paid for it, never a piaster more or less
                r = before - kept_value(allocs, kept, l["discount_bp"], l["line_total"])
                if l["line_total"] is not None:
                    # reconciled with what this line still holds (outstanding_value, the reports' figure too):
                    # never more than the line in all, never negative, and the return that empties the line
                    # settles it exactly
                    r = rest if not any(kept.values()) else max(0, min(r, rest))
                line_refunds[line_id] = r
                refund += r
            if refund_method in ("cash", "card"):
                # a refund to the card follows the same cap: money never received cannot go back to a card either
                # #142 (owner's rule, 11/11): cash can be paid back only for money the shop actually received
                # on this sale (cash, card or transfer; a courier's cash only once it was handed in), minus what
                # was already paid back in cash or to the card. The part sold on the customer's account goes
                # back to the account, so a debt can never turn into cash out of the drawer.
                # (a delivery sale reaches here only once settled, so its courier cash is in the drawer)
                received = (s["paid_cash"] or 0) + (s["paid_card"] or 0) + (s["paid_transfer"] or 0)
                # a transfer the owner or a manager marked "not received" is money the shop never got: it funds no
                # refund, in cash or to a card (#324 M2-01)
                # ... and only the part confirmed when a reviewer approved part of it (#324 M2-01)
                unreceived = False
                if s["paid_transfer"]:
                    from .attachments import transfer_state
                    got = transfer_state(self.db, ctx.org_id, sid, s["paid_transfer"])["received"]
                    unreceived = got < s["paid_transfer"]
                    received -= s["paid_transfer"] - got
                refunded = self.db.one("SELECT coalesce(sum(total), 0) FROM sale_returns WHERE sale_id=? AND org_id=?"
                                       " AND refund_method IN ('cash', 'card')", (sid, ctx.org_id))[0]
                can = max(0, received - refunded)
                if refund > can:
                    how = "نقدًا" if refund_method == "cash" else "على البطاقة"
                    if unreceived:
                        raise Conflict(f"تحويل هذه الفاتورة لم يتأكد وصوله كاملًا، فلا يُرد {how} (الحد الأقصى {egp(can)} ج)",
                                       code="cash_refund_exceeds_paid",
                                       details={"max_cash": can, "refund": refund, "transfer_not_received": True})
                    raise Conflict(f"هذا الجزء من الفاتورة كان آجلًا على حساب العميل فلا يُرد {how} (الحد الأقصى"
                                   f" {egp(can)} ج)؛ أعد الباقي على حساب العميل",
                                   code="cash_refund_exceeds_paid", details={"max_cash": can, "refund": refund})
            rid = new_id()
            # created_at by the same clock and format as the shift times: the shift report places card and
            # credit refunds by them (Codex P2 on #323; the table default has milliseconds only)
            self.db.execute("INSERT INTO sale_returns (id, org_id, sale_id, total, refund_method, reason,"
                            " created_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
                            (rid, ctx.org_id, sid, refund, refund_method, reason.strip(), ctx.user_id, _now()))
            self.db.execute("UPDATE sales SET returned_total=returned_total+? WHERE id=?", (refund, sid))
            if refund_method == "credit":
                self.db.execute("INSERT INTO customer_ledger (id, org_id, customer_id, amount, kind, ref_id,"
                                " created_by) VALUES (?,?,?,?, 'return', ?, ?)",
                                (new_id(), ctx.org_id, s["customer_id"], -refund, rid, ctx.user_id))
            if refund_method == "cash":
                cash.record(self.db, ctx, -refund, "refund", rid, reason, branch_id=s["branch_id"])
            if refund_method == "card" and refund:
                # back through the card / wallet the sale was paid with (the first one, if it was split)
                from ..finance import money
                money.reverse_sale(self.db, ctx, sid, refund, ref_type="sale_return", note=f"مرتجع فاتورة {s['sale_no']}",
                                   meta={"return_id": rid})
            audit.record(self.db, ctx, "sale.return", "sale", sid,
                         after={"return_id": rid, "refund": refund, "method": refund_method, "lines": lines,
                                "line_refunds": line_refunds})
            notify.emit(self.db, ctx, "return", "error" if restricted else "warning"
                        if age > days or s["created_by"] != ctx.user_id else "info", f"مرتجع {egp(refund)} ج — فاتورة {s['sale_no']}",
                        f"السبب: {reason.strip()}" + (f" · بعد {age} يوم" if age > days else "")
                        + (" · الفاتورة باعها شخص آخر" if s["created_by"] != ctx.user_id else "")
                        + (f" · ⚠ أصناف لا يُقبل إرجاعها: {'، '.join(restricted)}" if restricted else ""), "sale", sid)
        return {"return_id": rid, "refund": refund}

    def find_for_return(self, ctx: Context, q: str, limit: int = 20) -> list[dict]:
        """Completed sales to return from, by invoice number, item code or barcode, or customer phone or name
        (part 3 item 6a). Newest first; each line says sold / already returned / still returnable and its net
        after the discount, and each sale whether it is inside the return window. Only sales the person may see."""
        ctx.require("sales.return")
        term = (q or "").strip()
        if not term:
            return []
        ids: list[str] = []
        args_base = (ctx.org_id,)
        if term.isdigit() and len(term) <= 9:
            ids += [r["id"] for r in self.db.all("SELECT id FROM sales WHERE org_id=? AND status='completed' AND sale_no=?"
                                                 " ORDER BY completed_at DESC LIMIT ?", (*args_base, int(term), limit))]
        ids += [r["id"] for r in self.db.all(
            "SELECT DISTINCT s.id, s.completed_at FROM sales s JOIN sale_lines l ON l.sale_id=s.id JOIN items i ON i.id=l.item_id"
            " WHERE s.org_id=? AND s.status='completed' AND (i.code=? OR i.id IN (SELECT item_id FROM item_barcodes"
            " WHERE barcode=?)) ORDER BY s.completed_at DESC LIMIT ?", (ctx.org_id, term, term, limit))]
        ids += [r["id"] for r in self.db.all(
            "SELECT s.id FROM sales s JOIN customers c ON c.id=s.customer_id WHERE s.org_id=? AND s.status='completed'"
            " AND (c.phone=? OR c.name LIKE ?) ORDER BY s.completed_at DESC LIMIT ?",
            (ctx.org_id, term, f"%{term}%" if len(term) >= 3 else term, limit))]
        days = return_window_days(self.db, ctx.org_id)
        out, seen = [], set()
        for sid in ids:
            if sid in seen or len(out) >= limit:
                continue
            seen.add(sid)
            try:
                sale = self.get(ctx, sid)
            except Forbidden:
                continue
            age = (self.today - _local_date(sale["completed_at"])).days
            lines = []
            for l in sale["lines"]:
                sold = sum(a["qty"] for a in l["allocations"])
                back = sum(a.get("returned_qty") or 0 for a in l["allocations"])
                lines.append({"id": l["id"], "item_id": l["item_id"], "name": l["item"]["name"],
                              "code": l["item"].get("code"), "sold": sold, "returned": back,
                              "returnable": sold - back, "net": l["net"], "discount_bp": l["discount_bp"]})
            out.append({"id": sid, "sale_no": sale["sale_no"], "completed_at": sale["completed_at"], "total": sale["total"],
                        "customer": (sale.get("customer") or {}).get("name"), "customer_id": sale["customer_id"],
                        "paid_cash": sale["paid_cash"], "on_credit": sale["on_credit"], "paid_card": sale["paid_card"],
                        "age_days": age, "in_window": age <= days, "window_days": days, "lines": lines})
        return out

    # ------------------------------------------------------------ part 3 item 6a: return + replacement in one go
    def return_value(self, ctx: Context, sid: str, lines: dict[str, int], *, refund_method: str) -> int:
        """What a return would pay back, after the discount and every return rule, WITHOUT writing anything:
        the return runs inside a transaction that is rolled back (part 3 item 6a, the three totals)."""
        if self.db.conn.in_transaction:
            raise RuntimeError("return_value runs only at the top level")

        class _Undo(Exception):
            pass
        try:
            with self.db.transaction():
                r = self.return_items(ctx, sid, lines, refund_method=refund_method, reason="معاينة المرتجع")
                raise _Undo(r["refund"])
        except _Undo as u:
            return u.args[0]

    def exchange(self, ctx: Context, sid: str, lines: dict[str, int], new_sid: str, *, refund_method: str,
                 pay_method: str = "cash", reason: str, channel_id: str | None = None, version: int | None = None,
                 request_id: str | None = None, safety_decisions: dict[int, str] | None = None,
                 confirm_below_cost: bool = False) -> dict:
        """A sale return and its replacement sale as ONE operation (one transaction: both or neither).

        The returned value R follows every rule of `return_items` (window, who sold it, no-return sections, the
        cash cap of #142) and goes back by `refund_method`. The new sale N is then paid first from that same
        place up to R, so the two cancel out, and only the net is settled: N > R -> the customer pays N - R by
        `pay_method`; N < R -> he gets R - N back by `refund_method`. The returned goods go back to stock as a
        sale return (never a purchase). Returns {"returned": R, "new_total": N, "net": N - R, ...}."""
        ctx.require("sales.return")
        ctx.require("sales.create")
        # the refund in an exchange goes back as cash or to the customer's account only: a card refund lands on the
        # account that took the first payment, the replacement's card on the channel chosen now, and the two would
        # not cancel (Codex P1 on #396). A card customer gets the net back or pays it by card through pay_method.
        if pay_method not in ("cash", "card", "transfer", "credit") or refund_method not in ("cash", "credit"):
            raise ValidationFailed("طريقة الدفع غير صحيحة")
        if sid == new_sid:
            raise ValidationFailed("فاتورة الاستبدال لازم تكون فاتورة جديدة")
        if request_id:
            done = self.db.one("SELECT status, complete_request_id FROM sales WHERE id=? AND org_id=?",
                               (new_sid, ctx.org_id))
            if done and done["status"] == "completed" and done["complete_request_id"] == request_id:
                a = self.db.one("SELECT after_json FROM audit_log WHERE org_id=? AND entity_type='sale' AND entity_id=?"
                                " AND action='sale.exchange'", (ctx.org_id, new_sid))
                return {**json.loads(a["after_json"]), "sale": self.get(ctx, new_sid), "replayed": True}
        with self.db.transaction():
            orig = self.db.one("SELECT * FROM sales WHERE id=? AND org_id=?", (sid, ctx.org_id))
            new = self.db.one("SELECT * FROM sales WHERE id=? AND org_id=?", (new_sid, ctx.org_id))
            if not orig or not new:
                raise NotFound("الفاتورة غير موجودة")
            if new["status"] != "draft":
                raise Conflict("فاتورة الاستبدال مكتملة أو ملغاة", code="not_draft")
            if new["kind"] == "delivery":     # the courier would carry the offset as cash due (Codex P1 on #396)
                raise ValidationFailed("الاستبدال بيكون في الصيدلية بس، مش توصيل", code="exchange_delivery")
            if orig["customer_id"] and new["customer_id"] and orig["customer_id"] != new["customer_id"]:
                raise ValidationFailed("فاتورة الاستبدال لنفس عميل الفاتورة الأصلية", code="customer_mismatch")
            if orig["customer_id"] and not new["customer_id"]:      # the replacement belongs to the same customer
                self.db.execute("UPDATE sales SET customer_id=? WHERE id=?", (orig["customer_id"], new_sid))
            ret = self.return_items(ctx, sid, lines, refund_method=refund_method,
                                    reason=f"استبدال: {(reason or '').strip()}" if len((reason or "").strip()) >= 3 else reason)
            r_value = ret["refund"]
            n_total = self.get(ctx, new_sid)["total"]
            offset = min(r_value, n_total)
            payments = []
            if offset:
                payments.append({"method": refund_method, "amount": offset, "channel_id": channel_id})
            if n_total - offset:
                payments.append({"method": pay_method, "amount": n_total - offset, "channel_id": channel_id})
            if refund_method == "card" and pay_method == "card" and len(payments) == 2:
                payments = [{"method": "card", "amount": n_total, "channel_id": channel_id}]
            sale = self.complete(ctx, new_sid, payments=payments, version=version, request_id=request_id,
                                 safety_decisions=safety_decisions, confirm_below_cost=confirm_below_cost)
            out = {"return_id": ret["return_id"], "original_sale_id": sid, "sale_id": new_sid,
                   "returned": r_value, "new_total": n_total, "net": n_total - r_value,
                   "refund_method": refund_method, "pay_method": pay_method,
                   "settled": {"pay": max(0, n_total - r_value), "refund": max(0, r_value - n_total)}}
            audit.record(self.db, ctx, "sale.exchange", "sale", new_sid, after=out)
        return {**out, "sale": sale}

    def return_receipt(self, ctx: Context, rid: str) -> dict:
        """One return with its items, for the printed return slip."""
        r = self.db.one("SELECT r.*, u.full_name by_name FROM sale_returns r LEFT JOIN users u ON u.id=r.created_by"
                        " WHERE r.id=? AND r.org_id=?", (rid, ctx.org_id))
        if not r:
            raise NotFound("المرتجع غير موجود")
        sale = self.get(ctx, r["sale_id"])
        a = self.db.one("SELECT after_json FROM audit_log WHERE org_id=? AND entity_type='sale' AND entity_id=?"
                        " AND action='sale.return' AND json_extract(after_json, '$.return_id')=?",
                        (ctx.org_id, r["sale_id"], rid))      # org_id first: the audit index, not a scan of the whole log
        qty = json.loads(a["after_json"]).get("lines", {}) if a else {}
        lines = [{**l, "returned_now": int(qty[l["id"]])} for l in sale["lines"] if l["id"] in qty]
        return {"return": dict(r), "sale": sale, "lines": lines}

    def void(self, ctx: Context, sid: str, reason: str) -> dict:
        ctx.require("sales.void")
        if len((reason or "").strip()) < 5:
            raise ValidationFailed("اكتب سبب الإلغاء")
        with self.db.transaction():
            s = self.db.one("SELECT * FROM sales WHERE id=? AND org_id=?", (sid, ctx.org_id))
            if not s:
                raise NotFound("الفاتورة غير موجودة")
            if s["status"] == "draft":
                self.db.execute("UPDATE sales SET status='voided', voided_by=?, voided_at=?, void_reason=?"
                                " WHERE id=?", (ctx.user_id, _now(), reason, sid))
                return self.get(ctx, sid)
            if s["status"] != "completed":
                raise Conflict("الفاتورة ملغاة بالفعل")
            lo, hi = local_day_bounds(local_today())
            if not lo <= s["completed_at"] < hi:
                raise Forbidden("إلغاء الفاتورة مسموح في نفس اليوم فقط؛ استخدم المرتجع", code="void_too_late")
            # any return, not only one that refunded money: a line sold for nothing (100% discount) returns
            # with a refund of 0, and voiding after it would put the same units on the shelf a second time
            if s["returned_total"] or self.db.one("SELECT 1 FROM sale_returns WHERE sale_id=? AND org_id=?",
                                                  (sid, ctx.org_id)):
                raise Conflict("الفاتورة عليها مرتجع؛ لا يمكن إلغاؤها")
            for a in self.db.all("SELECT a.*, l.item_id FROM sale_allocations a JOIN sale_lines l"
                                 " ON l.id=a.sale_line_id JOIN items i ON i.id=l.item_id WHERE l.sale_id=? AND i.is_service=0",
                                 (sid,)):
                inventory.post_movement(self.db, ctx, item_id=a["item_id"], qty_delta=a["qty_base"],
                                        batch_id=a["batch_id"], movement_type="sale_return",
                                        ref_type="sale_void", ref_id=sid, branch_id=s["branch_id"], note=reason)
            # the courier branch only for a delivery sale: a store sale may still carry a delivery draft row
            # (made delivery, then back to store), and its cash IS in the drawer and must leave it on void
            dl = self.db.one("SELECT status FROM sale_deliveries WHERE sale_id=? AND org_id=?",
                             (sid, ctx.org_id)) if s["kind"] == "delivery" else None
            if dl and dl["status"] != "settled":     # the courier still holds the cash: nothing leaves the drawer
                self.db.execute("UPDATE sale_deliveries SET status='cancelled' WHERE sale_id=? AND org_id=?",
                                (sid, ctx.org_id))
            else:
                cash.record(self.db, ctx, -s["paid_cash"], "void", sid, reason, branch_id=s["branch_id"])
            from ..finance import money
            money.reverse_sale(self.db, ctx, sid, ref_type="sale_void", note=f"إلغاء فاتورة {s['sale_no']}")
            if s["on_credit"]:
                self.db.execute("INSERT INTO customer_ledger (id, org_id, customer_id, amount, kind, ref_id,"
                                " created_by) VALUES (?,?,?,?, 'void', ?, ?)",
                                (new_id(), ctx.org_id, s["customer_id"], -s["on_credit"], sid, ctx.user_id))
            self.db.execute("UPDATE sales SET status='voided', voided_by=?, voided_at=?, void_reason=?,"
                            " version=version+1 WHERE id=?", (ctx.user_id, _now(), reason, sid))
            audit.record(self.db, ctx, "sale.void", "sale", sid, after={"reason": reason})
            notify.emit(self.db, ctx, "void", "error", f"إلغاء فاتورة {s['sale_no']} — {egp(s['total'])} ج",
                        f"السبب: {reason.strip()}", "sale", sid)
        return self.get(ctx, sid)

    # ------------------------------------------------------------ reports
    def list_sales(self, ctx: Context, *, day: str | None = None, sale_no: int | None = None,
                   limit: int = 200, date_from: str | None = None, date_to: str | None = None) -> list[dict]:
        """Completed/voided invoices of one local day (default today), newest first;
        or one invoice by its number."""
        ctx.require("sales.read")
        q = ("SELECT s.id, s.sale_no, s.status, s.total, s.paid_cash, s.paid_card, s.paid_transfer, s.on_credit,"
             " s.returned_total, s.completed_at, s.voided_at, c.name customer,"
             " (SELECT count(*) FROM sale_lines l WHERE l.sale_id=s.id) lines"
             " FROM sales s LEFT JOIN customers c ON c.id=s.customer_id"
             " WHERE s.org_id=? AND s.branch_id=? AND s.status IN ('completed','voided')")   # = not draft; IN uses the index
        params: list = [ctx.org_id, ctx.branch_id]
        mine_only = not ctx.can("sales.totals")
        if mine_only and not (sale_no is not None and ctx.allowed("sales.return_others")):
            q += " AND s.created_by=?"
            params.append(ctx.user_id)
        if sale_no is not None:
            q += " AND s.sale_no=?"
            params.append(sale_no)
        elif date_from and date_to:
            if mine_only:
                raise Forbidden("فواتير الأيام السابقة للمدير والمالك فقط", code="forbidden",
                                details={"permission": "sales.totals"})
            try:
                d1, d2 = date.fromisoformat(date_from), date.fromisoformat(date_to)
            except ValueError:
                raise ValidationFailed("التاريخ غير صحيح")
            if d2 < d1:
                raise ValidationFailed("تاريخ النهاية قبل البداية")
            q += " AND s.completed_at>=? AND s.completed_at<?"
            params += [local_day_bounds(d1)[0], local_day_bounds(d2)[1]]
            limit = 5000
        else:
            d = date.fromisoformat(day) if day else local_today()
            if mine_only and d != local_today():
                raise Forbidden("فواتير الأيام السابقة للمدير والمالك فقط", code="forbidden",
                                details={"permission": "sales.totals"})
            lo, hi = local_day_bounds(d)
            q += " AND s.completed_at>=? AND s.completed_at<?"
            params += [lo, hi]
        q += " ORDER BY s.completed_at DESC LIMIT ?"
        params.append(min(limit, 5000))
        return [dict(r) for r in self.db.all(q, tuple(params))]

    def day_summary(self, ctx: Context, day: str | None = None) -> dict:
        ctx.require("sales.read")
        if not ctx.can("sales.totals"):
            raise Forbidden("إجمالي المبيعات للمدير والمالك فقط", code="forbidden",
                            details={"permission": "sales.totals"})
        d = date.fromisoformat(day) if day else local_today()
        lo, hi = local_day_bounds(d)
        r = self.db.one("SELECT count(*) n, coalesce(sum(total),0) total, coalesce(sum(paid_cash),0) cash,"
                        " coalesce(sum(paid_card),0) card, coalesce(sum(paid_transfer),0) transfer,"
                        " coalesce(sum(on_credit),0) credit,"
                        " coalesce(sum(returned_total),0) returns FROM sales WHERE org_id=? AND branch_id=?"
                        " AND status='completed' AND completed_at >= ? AND completed_at < ?",
                        (ctx.org_id, ctx.branch_id, lo, hi))
        out = dict(r)
        out["day"] = d.isoformat()
        if ctx.can("costs.read"):
            cost = Decimal(0)
            for a in self.db.all(
                    "SELECT a.unit_cost, a.qty_base - a.returned_qty q FROM sale_allocations a"
                    " JOIN sale_lines l ON l.id=a.sale_line_id JOIN sales s ON s.id=l.sale_id"
                    " WHERE s.org_id=? AND s.branch_id=? AND s.status='completed' AND s.completed_at >= ?"
                    " AND s.completed_at < ?", (ctx.org_id, ctx.branch_id, lo, hi)):
                if a["unit_cost"] is not None:
                    cost += Decimal(a["unit_cost"]) * a["q"]
            cost = cost.quantize(Decimal("0.01"), ROUND_HALF_UP)        # rounded once: cost + profit = net
            out["cost_egp"] = str(cost)
            out["gross_profit_egp"] = str(Decimal(out["total"] - out["returns"]) / 100 - cost)
            out["unknown_cost_lines"] = self.db.one(
                "SELECT count(*) c FROM sale_allocations a JOIN sale_lines l ON l.id=a.sale_line_id"
                " JOIN sales s ON s.id=l.sale_id WHERE s.org_id=? AND s.branch_id=? AND s.status='completed'"
                " AND s.completed_at >= ? AND s.completed_at < ? AND a.unit_cost IS NULL",
                (ctx.org_id, ctx.branch_id, lo, hi))["c"]
        return out
