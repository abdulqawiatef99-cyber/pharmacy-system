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

from ..core import audit
from ..core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id
from ..inventory import service as inventory
from ..safety.engine import Finding, SafetyEngine

NEAR_EXPIRY_DAYS = 30


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


def local_today() -> date:
    from datetime import datetime
    return datetime.now().astimezone().date()


def unit_price(pack_price: int, pack_size: int) -> int:
    """Piasters per base unit, rounded half-up."""
    return int((Decimal(pack_price) / pack_size).quantize(Decimal("1"), ROUND_HALF_UP))


class Sales:
    def __init__(self, db: Database, today: date | None = None):
        self.db = db
        self.today = today or date.today()
        self.safety = SafetyEngine(db)

    # ------------------------------------------------------------ customers
    def create_customer(self, ctx: Context, *, name: str, phone: str | None = None,
                        credit_limit: int = 0, notes: str | None = None) -> str:
        ctx.require("customers.manage")
        if not name.strip():
            raise ValidationFailed("اسم العميل مطلوب")
        if credit_limit < 0:
            raise ValidationFailed("حد الآجل لا يكون بالسالب")
        cid = new_id()
        with self.db.transaction():
            if phone and self.db.one("SELECT 1 FROM customers WHERE org_id=? AND phone=?",
                                     (ctx.org_id, phone.strip())):
                raise Conflict("رقم التليفون مسجل لعميل آخر")
            self.db.execute("INSERT INTO customers (id, org_id, name, phone, credit_limit, notes)"
                            " VALUES (?,?,?,?,?,?)",
                            (cid, ctx.org_id, name.strip(), phone.strip() if phone else None,
                             credit_limit, notes))
            audit.record(self.db, ctx, "customer.create", "customer", cid,
                         after={"name": name, "credit_limit": credit_limit})
        return cid

    def balance(self, ctx: Context, customer_id: str) -> int:
        return int(self.db.one("SELECT coalesce(sum(amount),0) b FROM customer_ledger WHERE org_id=?"
                               " AND customer_id=?", (ctx.org_id, customer_id))["b"])

    def customer_payment(self, ctx: Context, customer_id: str, amount: int, note: str | None = None) -> int:
        ctx.require("customers.manage")
        if amount <= 0:
            raise ValidationFailed("المبلغ لازم يكون أكبر من صفر")
        with self.db.transaction():
            if not self.db.one("SELECT 1 FROM customers WHERE id=? AND org_id=?", (customer_id, ctx.org_id)):
                raise NotFound("العميل غير موجود")
            self.db.execute("INSERT INTO customer_ledger (id, org_id, customer_id, amount, kind, note,"
                            " created_by) VALUES (?,?,?,?, 'payment', ?, ?)",
                            (new_id(), ctx.org_id, customer_id, -amount, note, ctx.user_id))
            audit.record(self.db, ctx, "customer.payment", "customer", customer_id, after={"amount": amount})
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
        return dict(s)

    def _sellable(self, ctx: Context, branch_id: str, item_id: str) -> list[dict]:
        """Batches with stock, FEFO, expired excluded (returned separately)."""
        rows = self.db.all(
            "SELECT sb.batch_key, sb.qty, b.batch_no, b.expiry_date, b.public_price"
            " FROM stock_balances sb LEFT JOIN batches b ON b.id = nullif(sb.batch_key,'')"
            " WHERE sb.org_id=? AND sb.branch_id=? AND sb.item_id=? AND sb.qty > 0"
            " ORDER BY b.expiry_date IS NULL, b.expiry_date", (ctx.org_id, branch_id, item_id))
        today = self.today.isoformat()
        return [dict(r) for r in rows if not r["expiry_date"] or r["expiry_date"] >= today]

    def _available(self, ctx: Context, branch_id: str, item_id: str) -> tuple[int, int]:
        sellable = sum(r["qty"] for r in self._sellable(ctx, branch_id, item_id))
        total = int(self.db.one("SELECT coalesce(sum(qty),0) q FROM stock_balances WHERE org_id=? AND"
                                " branch_id=? AND item_id=? AND qty>0",
                                (ctx.org_id, branch_id, item_id))["q"])
        return sellable, total - sellable

    def add_line(self, ctx: Context, sid: str, item_id: str, *, packs: int = 0, units: int = 0,
                 discount_bp: int = 0) -> dict:
        ctx.require("sales.create")
        if packs < 0 or units < 0 or packs + units == 0:
            raise ValidationFailed("الكمية غير صحيحة")
        if discount_bp:
            ctx.require("sales.discount")
            if not 0 < discount_bp <= 10000:
                raise ValidationFailed("نسبة الخصم غير صحيحة")
        with self.db.transaction():
            s = self._draft(ctx, sid)
            item = self.db.one("SELECT * FROM items WHERE id=? AND org_id=? AND status='active'",
                               (item_id, ctx.org_id))
            if not item:
                raise NotFound("الصنف غير موجود")
            qty = packs * item["pack_size"] + units
            already = self.db.one("SELECT coalesce(sum(qty_base),0) q FROM sale_lines WHERE sale_id=?"
                                  " AND item_id=?", (sid, item_id))["q"]
            sellable, expired = self._available(ctx, s["branch_id"], item_id)
            if already + qty > sellable:
                msg = f"الكمية المتاحة للبيع {sellable} وحدة"
                if expired:
                    msg += f" (يوجد {expired} وحدة منتهية الصلاحية لا تُباع)"
                raise Conflict(msg, code="insufficient_stock",
                               details={"available": sellable, "expired": expired})
            n = self.db.one("SELECT coalesce(max(line_no),0)+1 n FROM sale_lines WHERE sale_id=?", (sid,))["n"]
            partial = 1 if units % item["pack_size"] else 0
            self.db.execute("INSERT INTO sale_lines (id, sale_id, org_id, line_no, item_id, qty_base,"
                            " discount_bp, partial_pack) VALUES (?,?,?,?,?,?,?,?)",
                            (new_id(), sid, ctx.org_id, n, item_id, qty, discount_bp, partial))
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
    def _plan(self, ctx: Context, s: dict, line: dict, item: dict) -> list[dict]:
        need = line["qty_base"]
        plan = []
        for b in self._sellable(ctx, s["branch_id"], line["item_id"]):
            if need == 0:
                break
            take = min(need, b["qty"])
            pack_price = b["public_price"] if b["public_price"] is not None else item["public_price"]
            plan.append({"batch_id": b["batch_key"] or None, "batch_no": b["batch_no"],
                         "expiry_date": b["expiry_date"], "qty": take,
                         "unit_price": unit_price(pack_price, item["pack_size"]) if pack_price is not None else None,
                         "price_source": "batch" if b["public_price"] is not None else "item"})
            need -= take
        if need:
            raise Conflict("الكمية لم تعد متاحة (ربما اتباعت من شاشة أخرى)", code="insufficient_stock")
        return plan

    def get(self, ctx: Context, sid: str) -> dict:
        ctx.require("sales.read")
        s = self.db.one("SELECT * FROM sales WHERE id=? AND org_id=?", (sid, ctx.org_id))
        if not s:
            raise NotFound("الفاتورة غير موجودة")
        s = dict(s)
        lines = []
        subtotal = discount = 0
        for l in self.db.all("SELECT * FROM sale_lines WHERE sale_id=? ORDER BY line_no", (sid,)):
            l = dict(l)
            item = dict(self.db.one("SELECT * FROM items WHERE id=?", (l["item_id"],)))
            l["item"] = {"name": item["name_en"] or item["name_ar"], "strength": item["strength_norm"],
                         "form": item["dosage_form"], "pack_size": item["pack_size"],
                         "base_unit": item["base_unit_name"], "active_ingredient": item["active_ingredient"]}
            if s["status"] == "draft":
                try:
                    plan = self._plan(ctx, s, l, item)
                except Conflict:
                    plan, l["problem"] = [], "insufficient_stock"
            else:
                plan = [dict(a) for a in self.db.all(
                    "SELECT a.*, b.batch_no, b.expiry_date FROM sale_allocations a LEFT JOIN batches b"
                    " ON b.id=a.batch_id WHERE a.sale_line_id=?", (l["id"],))]
                for a in plan:
                    a["qty"] = a.pop("qty_base")
            l["allocations"] = plan
            if any(a.get("unit_price") is None for a in plan):
                l["problem"] = "no_price"
            gross = sum((a["unit_price"] or 0) * a["qty"] for a in plan)
            disc = round(gross * l["discount_bp"] / 10000)
            l["gross"], l["discount"], l["net"] = gross, disc, gross - disc
            if any(a.get("expiry_date") and (date.fromisoformat(a["expiry_date"]) - self.today).days
                   < NEAR_EXPIRY_DAYS for a in plan):
                l["near_expiry"] = True
            subtotal += gross
            discount += disc
            lines.append(l)
        s["lines"] = lines
        if s["status"] == "draft":
            s["subtotal"], s["discount_total"], s["total"] = subtotal, discount, subtotal - discount
        if s["customer_id"]:
            c = self.db.one("SELECT id, name, phone, credit_limit FROM customers WHERE id=?", (s["customer_id"],))
            s["customer"] = {**dict(c), "balance": self.balance(ctx, c["id"])}
        return s

    # ------------------------------------------------------------ safety + complete
    def safety_check(self, ctx: Context, sid: str) -> list[Finding]:
        s = self.get(ctx, sid)
        return self.safety.check(ctx, [{"item_id": l["item_id"], "base_qty": l["qty_base"],
                                        "partial_pack": bool(l["partial_pack"])} for l in s["lines"]],
                                 context_type="sale", context_id=sid)

    def complete(self, ctx: Context, sid: str, *, paid_cash: int = 0, paid_card: int = 0,
                 on_credit: int = 0, safety_decisions: dict[int, str] | None = None,
                 version: int | None = None) -> dict:
        ctx.require("sales.create")
        if min(paid_cash, paid_card, on_credit) < 0:
            raise ValidationFailed("مبالغ الدفع لا تكون بالسالب")
        preview = self.get(ctx, sid)
        if preview["status"] != "draft":
            raise Conflict("الفاتورة مكتملة أو ملغاة", code="not_draft")
        if version is not None and preview["version"] != version:
            raise Conflict("الفاتورة اتعدلت، حدّث الشاشة", code="stale_version")
        if not preview["lines"]:
            raise ValidationFailed("الفاتورة فاضية")
        bad = [l for l in preview["lines"] if l.get("problem")]
        if bad:
            raise Conflict("فيه أصناف غير متاحة أو بدون سعر", code="line_problems",
                           details={"lines": [{"line_no": l["line_no"], "problem": l["problem"]} for l in bad]})
        if paid_cash + paid_card + on_credit != preview["total"]:
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
                raise Conflict("الفاتورة اتعدلت أثناء الإتمام", code="stale_version")
            total = 0
            for l in preview["lines"]:
                item = dict(self.db.one("SELECT * FROM items WHERE id=?", (l["item_id"],)))
                plan = self._plan(ctx, s, l, item)          # re-plan inside the transaction
                cost_row = self.db.one("SELECT avg_cost FROM item_costs WHERE org_id=? AND branch_id=?"
                                       " AND item_id=?", (ctx.org_id, s["branch_id"], l["item_id"]))
                gross = 0
                for a in plan:
                    inventory.post_movement(self.db, ctx, item_id=l["item_id"], qty_delta=-a["qty"],
                                            batch_id=a["batch_id"], movement_type="sale",
                                            unit_cost=None, ref_type="sale", ref_id=sid,
                                            ref_line_id=l["id"], branch_id=s["branch_id"])
                    self.db.execute("INSERT INTO sale_allocations (id, sale_line_id, batch_id, qty_base,"
                                    " unit_price, unit_cost) VALUES (?,?,?,?,?,?)",
                                    (new_id(), l["id"], a["batch_id"], a["qty"], a["unit_price"],
                                     cost_row["avg_cost"] if cost_row else None))
                    gross += a["unit_price"] * a["qty"]
                net = gross - round(gross * l["discount_bp"] / 10000)
                self.db.execute("UPDATE sale_lines SET line_total=? WHERE id=?", (net, l["id"]))
                total += net
            if total != preview["total"]:
                raise Conflict("السعر اتغير أثناء الإتمام، راجع الفاتورة", code="price_changed")
            no = self._next_no(ctx, s["branch_id"], "sale")
            self.db.execute("UPDATE sales SET status='completed', sale_no=?, subtotal=?, discount_total=?,"
                            " total=?, paid_cash=?, paid_card=?, on_credit=?, completed_at=?,"
                            " version=version+1 WHERE id=?",
                            (no, preview["subtotal"], preview["discount_total"], total, paid_cash,
                             paid_card, on_credit, _now(), sid))
            if on_credit:
                self.db.execute("INSERT INTO customer_ledger (id, org_id, customer_id, amount, kind, ref_id,"
                                " created_by) VALUES (?,?,?,?, 'credit_sale', ?, ?)",
                                (new_id(), ctx.org_id, s["customer_id"], on_credit, sid, ctx.user_id))
            audit.record(self.db, ctx, "sale.complete", "sale", sid,
                         after={"no": no, "total": total, "cash": paid_cash, "card": paid_card,
                                "credit": on_credit})
        return self.get(ctx, sid)

    def _next_no(self, ctx: Context, branch_id: str, kind: str) -> int:
        self.db.execute("INSERT INTO branch_counters (org_id, branch_id, kind, next_no) VALUES (?,?,?,1)"
                        " ON CONFLICT(org_id, branch_id, kind) DO NOTHING", (ctx.org_id, branch_id, kind))
        n = self.db.one("SELECT next_no FROM branch_counters WHERE org_id=? AND branch_id=? AND kind=?",
                        (ctx.org_id, branch_id, kind))["next_no"]
        self.db.execute("UPDATE branch_counters SET next_no=next_no+1 WHERE org_id=? AND branch_id=? AND kind=?",
                        (ctx.org_id, branch_id, kind))
        return n

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
        with self.db.transaction():
            s = self.db.one("SELECT * FROM sales WHERE id=? AND org_id=?", (sid, ctx.org_id))
            if not s:
                raise NotFound("الفاتورة غير موجودة")
            if s["status"] != "completed":
                raise Conflict("المرتجع يكون من فاتورة مكتملة فقط")
            if refund_method == "credit" and not s["customer_id"]:
                raise ValidationFailed("الاسترداد على الحساب يحتاج عميل")
            refund = 0
            for line_id, qty in lines.items():
                if not isinstance(qty, int) or qty <= 0:
                    raise ValidationFailed("كمية المرتجع غير صحيحة")
                l = self.db.one("SELECT * FROM sale_lines WHERE id=? AND sale_id=?", (line_id, sid))
                if not l:
                    raise NotFound("السطر غير موجود في الفاتورة")
                allocs = [dict(a) for a in self.db.all(
                    "SELECT * FROM sale_allocations WHERE sale_line_id=? ORDER BY rowid DESC", (line_id,))]
                remaining_returnable = sum(a["qty_base"] - a["returned_qty"] for a in allocs)
                if qty > remaining_returnable:
                    raise Conflict(f"أقصى كمية ممكن ترجع {remaining_returnable}", code="return_exceeds")
                left = qty
                for a in allocs:
                    can = a["qty_base"] - a["returned_qty"]
                    take = min(can, left)
                    if take <= 0:
                        continue
                    inventory.post_movement(self.db, ctx, item_id=l["item_id"], qty_delta=take,
                                            batch_id=a["batch_id"], movement_type="sale_return",
                                            ref_type="sale", ref_id=sid, ref_line_id=line_id,
                                            branch_id=s["branch_id"], note=reason)
                    self.db.execute("UPDATE sale_allocations SET returned_qty=returned_qty+? WHERE id=?",
                                    (take, a["id"]))
                    gross = a["unit_price"] * take
                    refund += gross - round(gross * l["discount_bp"] / 10000)
                    left -= take
                    if left == 0:
                        break
            rid = new_id()
            self.db.execute("INSERT INTO sale_returns (id, org_id, sale_id, total, refund_method, reason,"
                            " created_by) VALUES (?,?,?,?,?,?,?)",
                            (rid, ctx.org_id, sid, refund, refund_method, reason.strip(), ctx.user_id))
            self.db.execute("UPDATE sales SET returned_total=returned_total+? WHERE id=?", (refund, sid))
            if refund_method == "credit":
                self.db.execute("INSERT INTO customer_ledger (id, org_id, customer_id, amount, kind, ref_id,"
                                " created_by) VALUES (?,?,?,?, 'return', ?, ?)",
                                (new_id(), ctx.org_id, s["customer_id"], -refund, rid, ctx.user_id))
            audit.record(self.db, ctx, "sale.return", "sale", sid,
                         after={"return_id": rid, "refund": refund, "method": refund_method, "lines": lines})
        return {"return_id": rid, "refund": refund}

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
            if s["returned_total"]:
                raise Conflict("الفاتورة عليها مرتجع؛ لا يمكن إلغاؤها")
            for a in self.db.all("SELECT a.*, l.item_id FROM sale_allocations a JOIN sale_lines l"
                                 " ON l.id=a.sale_line_id WHERE l.sale_id=?", (sid,)):
                inventory.post_movement(self.db, ctx, item_id=a["item_id"], qty_delta=a["qty_base"],
                                        batch_id=a["batch_id"], movement_type="sale_return",
                                        ref_type="sale_void", ref_id=sid, branch_id=s["branch_id"], note=reason)
            if s["on_credit"]:
                self.db.execute("INSERT INTO customer_ledger (id, org_id, customer_id, amount, kind, ref_id,"
                                " created_by) VALUES (?,?,?,?, 'void', ?, ?)",
                                (new_id(), ctx.org_id, s["customer_id"], -s["on_credit"], sid, ctx.user_id))
            self.db.execute("UPDATE sales SET status='voided', voided_by=?, voided_at=?, void_reason=?,"
                            " version=version+1 WHERE id=?", (ctx.user_id, _now(), reason, sid))
            audit.record(self.db, ctx, "sale.void", "sale", sid, after={"reason": reason})
        return self.get(ctx, sid)

    # ------------------------------------------------------------ reports
    def day_summary(self, ctx: Context, day: str | None = None) -> dict:
        ctx.require("sales.read")
        d = date.fromisoformat(day) if day else local_today()
        lo, hi = local_day_bounds(d)
        r = self.db.one("SELECT count(*) n, coalesce(sum(total),0) total, coalesce(sum(paid_cash),0) cash,"
                        " coalesce(sum(paid_card),0) card, coalesce(sum(on_credit),0) credit,"
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
            out["cost_egp"] = str(cost.quantize(Decimal("0.01")))
            out["gross_profit_egp"] = str((Decimal(out["total"] - out["returns"]) / 100 - cost)
                                          .quantize(Decimal("0.01")))
            out["unknown_cost_lines"] = self.db.one(
                "SELECT count(*) c FROM sale_allocations a JOIN sale_lines l ON l.id=a.sale_line_id"
                " JOIN sales s ON s.id=l.sale_id WHERE s.org_id=? AND s.branch_id=? AND s.status='completed'"
                " AND s.completed_at >= ? AND s.completed_at < ? AND a.unit_cost IS NULL",
                (ctx.org_id, ctx.branch_id, lo, hi))["c"]
        return out
