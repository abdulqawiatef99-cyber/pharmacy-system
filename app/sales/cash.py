"""Cash drawer shifts.

Every cash amount that enters or leaves the drawer is a row in cash_movements
(append-only) inside the same transaction as the sale / refund / payment that
caused it. Closing a shift compares the counted cash with
opening + sum(movements); the difference is recorded, never hidden.
Card and credit amounts never touch the drawer.
"""
from __future__ import annotations

from ..core import audit
from ..core.errors import Conflict, NotFound, ValidationFailed
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
        raise Conflict("الخزنة مقفولة — افتح وردية قبل أي عملية نقدية", code="no_open_shift")
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


class CashDrawer:
    def __init__(self, db: Database):
        self.db = db

    def open(self, ctx: Context, opening_cash: int) -> str:
        ctx.require("sales.create")
        if not isinstance(opening_cash, int) or opening_cash < 0:
            raise ValidationFailed("رصيد بداية الوردية غير صحيح")
        with self.db.transaction():
            if open_shift_id(self.db, ctx):
                raise Conflict("يوجد وردية مفتوحة بالفعل", code="shift_already_open")
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
        card = self.db.one("SELECT coalesce(sum(paid_card),0) c, coalesce(sum(on_credit),0) cr, count(*) n"
                           " FROM sales WHERE shift_id=? AND status='completed'", (shift_id,))
        expected = s["opening_cash"] + sum(by_kind.values())
        return {**dict(s), "cash_by_kind": by_kind, "expected_cash": expected if s["status"] == "open"
                else s["expected_cash"], "card_total": card["c"], "credit_total": card["cr"], "sales_count": card["n"]}

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

    def close(self, ctx: Context, counted_cash: int, note: str | None = None) -> dict:
        ctx.require("cash.manage")
        if not isinstance(counted_cash, int) or counted_cash < 0:
            raise ValidationFailed("المبلغ المعدود غير صحيح")
        with self.db.transaction():
            sid = require_open_shift(self.db, ctx)
            summ = self.summary(ctx, sid)
            diff = counted_cash - summ["expected_cash"]
            if diff and len((note or "").strip()) < 3:
                raise ValidationFailed("فيه فرق في الخزنة؛ اكتب سببه قبل القفل", code="difference_note_required",
                                       details={"expected": summ["expected_cash"], "difference": diff})
            self.db.execute("UPDATE cash_shifts SET status='closed', expected_cash=?, counted_cash=?, difference=?,"
                            " close_note=?, closed_by=?, closed_at=? WHERE id=?",
                            (summ["expected_cash"], counted_cash, diff, note, ctx.user_id, _now(), sid))
            audit.record(self.db, ctx, "cash.shift_close", "cash_shift", sid,
                         after={"expected": summ["expected_cash"], "counted": counted_cash, "difference": diff})
        return self.summary(ctx, sid)
