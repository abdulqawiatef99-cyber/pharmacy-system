"""Cash drawer shifts.

Every cash amount that enters or leaves the drawer is a row in cash_movements
(append-only) inside the same transaction as the sale / refund / payment that
caused it. Closing a shift compares the counted cash with
opening + sum(movements); the difference is recorded, never hidden.
Card and credit amounts never touch the drawer.
"""
from __future__ import annotations

from ..core import audit, notify
import json

from ..core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id


def _now() -> str:
    from ..core.accounts import iso, utcnow
    return iso(utcnow())


def open_shift_id(db: Database, ctx: Context, branch_id: str | None = None) -> str | None:
    r = db.one("SELECT id FROM cash_shifts WHERE org_id=? AND branch_id=? AND status='open'",
               (ctx.org_id, branch_id or ctx.branch_id))
    return r["id"] if r else None


def require_open_shift(db: Database, ctx: Context, branch_id: str | None = None) -> str:
    sid = open_shift_id(db, ctx, branch_id)
    if not sid:
        raise Conflict("الخزينة مغلقة — افتح وردية قبل أي عملية نقدية", code="no_open_shift")
    return sid


def record(db: Database, ctx: Context, amount: int, kind: str, ref_id: str | None = None,
           note: str | None = None, branch_id: str | None = None) -> str | None:
    """Call inside the business transaction. amount 0 is a no-op."""
    if amount == 0:
        return None
    shift = require_open_shift(db, ctx, branch_id)
    db.execute("INSERT INTO cash_movements (id, org_id, shift_id, amount, kind, ref_id, note, created_by)"
               " VALUES (?,?,?,?,?,?,?,?)", (new_id(), ctx.org_id, shift, amount, kind, ref_id, note, ctx.user_id))
    return shift


# Egyptian notes and coins, in piasters
DENOMINATIONS = [20000, 10000, 5000, 2000, 1000, 500, 100, 50, 25]


def count_total(detail: dict) -> tuple[int, dict]:
    """{"20000": 3, "100": 7, ...} -> (total piasters, cleaned detail)."""
    if not isinstance(detail, dict):
        raise ValidationFailed("عدد الفئات غير صحيح")
    clean = {}
    for k, v in detail.items():
        try:
            d, n = int(k), int(v or 0)
        except (TypeError, ValueError):
            raise ValidationFailed("عدد الفئات غير صحيح")
        if d not in DENOMINATIONS or n < 0 or n > 100000:
            raise ValidationFailed("عدد الفئات غير صحيح")
        if n:
            clean[str(d)] = n
    return sum(int(k) * n for k, n in clean.items()), clean


class CashDrawer:
    def __init__(self, db: Database):
        self.db = db

    def open(self, ctx: Context, opening_cash: int) -> str:
        ctx.require("sales.create")
        if not isinstance(opening_cash, int) or opening_cash < 0:
            raise ValidationFailed("رصيد بداية الوردية غير صحيح")
        with self.db.transaction():
            if open_shift_id(self.db, ctx):
                raise Conflict("توجد وردية مفتوحة بالفعل", code="shift_already_open")
            sid = new_id()
            self.db.execute("INSERT INTO cash_shifts (id, org_id, branch_id, opening_cash, opened_by, opened_at)"
                            " VALUES (?,?,?,?,?,?)", (sid, ctx.org_id, ctx.branch_id, opening_cash,
                                                      ctx.user_id, _now()))
            audit.record(self.db, ctx, "cash.shift_open", "cash_shift", sid, after={"opening_cash": opening_cash})
        return sid

    def summary(self, ctx: Context, shift_id: str | None = None) -> dict:
        ctx.require("sales.read")
        shift_id = shift_id or open_shift_id(self.db, ctx)
        if not shift_id:
            raise NotFound("لا توجد وردية مفتوحة")
        s = self.db.one("SELECT * FROM cash_shifts WHERE id=? AND org_id=?", (shift_id, ctx.org_id))
        if not s:
            raise NotFound("الوردية غير موجودة")
        by_kind = {r["kind"]: r["t"] for r in self.db.all(
            "SELECT kind, sum(amount) t FROM cash_movements WHERE shift_id=? GROUP BY kind", (shift_id,))}
        card = self.db.one("SELECT coalesce(sum(paid_card),0) c, coalesce(sum(paid_transfer),0) t,"
                           " coalesce(sum(on_credit),0) cr, count(*) n"
                           " FROM sales WHERE shift_id=? AND status='completed'", (shift_id,))
        by_channel = [dict(r) for r in self.db.all(
            "SELECT p.method, coalesce(c.name, '') channel, sum(p.amount) amount FROM sale_payments p"
            " JOIN sales s ON s.id=p.sale_id LEFT JOIN payment_channels c ON c.id=p.channel_id"
            " WHERE s.shift_id=? AND s.status='completed' AND p.method IN ('card','transfer') GROUP BY 1,2", (shift_id,))]
        with_courier = self.db.one("SELECT coalesce(sum(d.cash_due),0) c FROM sale_deliveries d JOIN sales s"
                                   " ON s.id=d.sale_id WHERE s.org_id=? AND s.branch_id=? AND d.status IN"
                                   " ('out','delivered')", (ctx.org_id, s["branch_id"]))["c"]
        expected = s["opening_cash"] + sum(by_kind.values())
        out = {**dict(s), "cash_by_kind": by_kind, "expected_cash": expected if s["status"] == "open"
               else s["expected_cash"], "card_total": card["c"], "transfer_total": card["t"], "credit_total": card["cr"],
               "sales_count": card["n"], "by_channel": by_channel, "with_courier": with_courier}
        if not ctx.can("cash.see_expected"):
            # blind count: the person closing the drawer must not know the target number
            for k in ("expected_cash", "cash_by_kind", "difference", "card_total", "credit_total", "transfer_total",
                      "by_channel"):
                out.pop(k, None)
            out["blind"] = True
        return out

    def _expected(self, shift_id: str) -> int:
        s = self.db.one("SELECT opening_cash FROM cash_shifts WHERE id=?", (shift_id,))
        m = self.db.one("SELECT coalesce(sum(amount),0) t FROM cash_movements WHERE shift_id=?", (shift_id,))
        return s["opening_cash"] + m["t"]

    def movement(self, ctx: Context, amount: int, kind: str, note: str) -> None:
        """Payout (مصروف من الدرج) or deposit by an authorized user, with a reason."""
        ctx.require("cash.manage")
        if kind not in ("payout", "deposit") or not isinstance(amount, int) or amount <= 0:
            raise ValidationFailed("بيانات الحركة غير صحيحة")
        if len((note or "").strip()) < 3:
            raise ValidationFailed("اكتب سبب الحركة")
        with self.db.transaction():
            shift = record(self.db, ctx, -amount if kind == "payout" else amount, kind, note=note.strip())
            audit.record(self.db, ctx, f"cash.{kind}", "cash_shift", shift, after={"amount": amount, "note": note})

    def close(self, ctx: Context, counted_cash: int | None = None, note: str | None = None,
              denominations: dict | None = None) -> dict:
        ctx.require("cash.close")
        detail = None
        if denominations is not None:
            counted_cash, detail = count_total(denominations)
        if not isinstance(counted_cash, int) or counted_cash < 0:
            raise ValidationFailed("المبلغ المعدود غير صحيح")
        with self.db.transaction():
            sid = require_open_shift(self.db, ctx)
            expected = self._expected(sid)
            diff = counted_cash - expected
            if diff and ctx.can("cash.see_expected") and len((note or "").strip()) < 3:
                raise ValidationFailed("يوجد فرق في الخزينة؛ اكتب سببه قبل الإغلاق", code="difference_note_required",
                                       details={"expected": expected, "difference": diff})
            self.db.execute("UPDATE cash_shifts SET status='closed', expected_cash=?, counted_cash=?, difference=?,"
                            " close_note=?, closed_by=?, closed_at=?, counted_detail=? WHERE id=?",
                            (expected, counted_cash, diff, note, ctx.user_id, _now(),
                             json.dumps(detail) if detail else None, sid))
            audit.record(self.db, ctx, "cash.shift_close", "cash_shift", sid,
                         after={"expected": expected, "counted": counted_cash, "difference": diff})
            if diff:
                who = self.db.one("SELECT full_name FROM users WHERE id=?", (ctx.user_id,))["full_name"]
                notify.emit(self.db, ctx, "cash_difference", "error" if diff < 0 else "warning",
                            f"{'عجز' if diff < 0 else 'زيادة'} في الخزينة {abs(diff) / 100:,.2f} ج",
                            f"إغلاق الوردية: {who}" + (f" · {note.strip()}" if (note or "").strip() else ""),
                            "cash_shift", sid)
        from ..core import autobackup
        autobackup.trigger("shift_close")
        return self.report(ctx, sid)

    def report(self, ctx: Context, shift_id: str) -> dict:
        """Closing report. The person who closed sees the result (shortage / surplus) only after
        his count is saved; the owner and managers see every shift."""
        ctx.require("sales.read")
        s = self.db.one("SELECT * FROM cash_shifts WHERE id=? AND org_id=?", (shift_id, ctx.org_id))
        if not s:
            raise NotFound("الوردية غير موجودة")
        own_closed = s["status"] == "closed" and s["closed_by"] == ctx.user_id
        if not (ctx.can("cash.see_expected") or own_closed):
            raise Forbidden("تقرير الوردية للمدير والمالك", code="forbidden")
        full = {**dict(s)}
        k = {r["kind"]: r["t"] for r in self.db.all(
            "SELECT kind, sum(amount) t FROM cash_movements WHERE shift_id=? GROUP BY kind", (shift_id,))}
        sales = self.db.one("SELECT count(*) n, coalesce(sum(total),0) total, coalesce(sum(paid_card),0) card,"
                            " coalesce(sum(paid_transfer),0) transfer, coalesce(sum(on_credit),0) credit,"
                            " coalesce(sum(discount_total),0) discount FROM sales WHERE shift_id=? AND status='completed'",
                            (shift_id,))
        voided = self.db.one("SELECT count(*) n FROM sales WHERE shift_id=? AND status='voided' AND sale_no IS NOT NULL",
                             (shift_id,))["n"]
        returns = self.db.one("SELECT count(*) n, coalesce(sum(r.total),0) t FROM sale_returns r JOIN cash_movements m"
                              " ON m.ref_id=r.id WHERE m.shift_id=?", (shift_id,))
        by_channel = [dict(r) for r in self.db.all(
            "SELECT p.method, coalesce(c.name, '') channel, sum(p.amount) amount FROM sale_payments p"
            " JOIN sales x ON x.id=p.sale_id LEFT JOIN payment_channels c ON c.id=p.channel_id"
            " WHERE x.shift_id=? AND x.status='completed' AND p.method IN ('card','transfer') GROUP BY 1,2", (shift_id,))]
        names = {u["id"]: u["full_name"] for u in self.db.all(
            "SELECT id, full_name FROM users WHERE id IN (?,?)", (s["opened_by"], s["closed_by"] or ""))}
        expected = s["expected_cash"] if s["status"] == "closed" else self._expected(shift_id)
        full.update(cash_by_kind=k, sales_count=sales["n"], sales_total=sales["total"], card_total=sales["card"],
                    transfer_total=sales["transfer"], credit_total=sales["credit"], discount_total=sales["discount"],
                    voided_count=voided, returns_count=returns["n"], returns_total=returns["t"], by_channel=by_channel,
                    expected_cash=expected, opened_by_name=names.get(s["opened_by"]),
                    closed_by_name=names.get(s["closed_by"]),
                    counted_detail=json.loads(s["counted_detail"]) if s["counted_detail"] else None)
        return full

    def recent(self, ctx: Context, limit: int = 30) -> list[dict]:
        ctx.require("cash.see_expected")
        return [dict(r) for r in self.db.all(
            "SELECT s.id, s.status, s.opened_at, s.closed_at, s.opening_cash, s.expected_cash, s.counted_cash,"
            " s.difference, s.close_note, u.full_name closed_by_name FROM cash_shifts s LEFT JOIN users u"
            " ON u.id=s.closed_by WHERE s.org_id=? AND s.branch_id=? ORDER BY s.opened_at DESC LIMIT ?",
            (ctx.org_id, ctx.branch_id, min(limit, 200)))]
