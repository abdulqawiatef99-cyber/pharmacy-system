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
from ..core.numbers import whole_number
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
        raise Conflict("الخزينة مغلقة — افتح شيفت قبل أي عملية نقدية", code="no_open_shift")
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


def _sales_of(s) -> tuple[str, tuple]:
    """WHERE part (alias x) for the sales a shift report counts. A closed shift keeps the sales it had at its
    close: a same-day void after the close (it takes the cash out in the shift open at that moment) does not
    rewrite the closed report (final review P2-1)."""
    if s["status"] == "closed":
        return ("x.shift_id=? AND (x.status='completed' OR (x.status='voided' AND x.sale_no IS NOT NULL"
                " AND x.voided_at>=?))", (s["id"], s["closed_at"]))
    return "x.shift_id=? AND x.status='completed'", (s["id"],)


def _returns_of(db: Database, s) -> dict:
    """Returns made while the shift was open, by refund method (final review P2-1). Cash refunds are found
    through their drawer movement in this shift, so the report always reconciles with the drawer. Card and
    credit refunds (and a cash refund of 0, which moves no cash) by the shift's open-close window: return
    times are written by the same clock and format as the shift's (Codex P2 on #323)."""
    by = {m: {"count": 0, "total": 0} for m in ("cash", "card", "credit")}
    c = db.one("SELECT count(*) n, coalesce(sum(r.total),0) t FROM sale_returns r JOIN cash_movements m"
               " ON m.ref_id=r.id AND m.kind='refund' WHERE m.shift_id=? AND m.org_id=?", (s["id"], s["org_id"]))
    by["cash"] = {"count": c["n"], "total": c["t"]}
    for r in db.all("SELECT r.refund_method m, count(*) n, coalesce(sum(r.total),0) t FROM sale_returns r"
                    " JOIN sales x ON x.id=r.sale_id WHERE r.org_id=? AND r.created_at>=?"
                    " AND (? IS NULL OR r.created_at<=?) AND x.org_id=? AND x.branch_id=?"
                    " AND (r.refund_method IN ('card','credit') OR r.total=0) GROUP BY 1",
                    (s["org_id"], s["opened_at"], s["closed_at"], s["closed_at"], s["org_id"], s["branch_id"])):
        cur = by[r["m"]]
        by[r["m"]] = {"count": cur["count"] + r["n"], "total": cur["total"] + r["t"]}
    return by


# Egyptian notes and coins, in piasters
DENOMINATIONS = [20000, 10000, 5000, 2000, 1000, 500, 100, 50, 25]


def count_total(detail: dict) -> tuple[int, dict]:
    """{"20000": 3, "100": 7, ...} -> (total piasters, cleaned detail)."""
    if not isinstance(detail, dict):
        raise ValidationFailed("عدد الفئات غير صحيح")
    clean = {}
    for k, v in detail.items():
        try:
            d, n = int(k), whole_number(v or 0)      # 2.5 notes is refused, never counted as 2
        except (TypeError, ValueError):
            raise ValidationFailed("عدد الفئات غير صحيح")
        if d not in DENOMINATIONS or n < 0 or n > 100000:
            raise ValidationFailed("عدد الفئات غير صحيح")
        if n:
            clean[str(d)] = n
    return sum(int(k) * n for k, n in clean.items()), clean


def redact_summary(ctx: Context, summary: dict) -> dict:
    """Apply current blind-count visibility to a fresh or cached summary."""
    if ctx.can("cash.see_expected"):
        return summary
    out = dict(summary)
    for key in ("expected_cash", "cash_by_kind", "difference", "card_total", "credit_total", "transfer_total",
                "by_channel", "card_refunds", "credit_refunds"):
        out.pop(key, None)
    out["blind"] = True
    return out


class CashDrawer:
    def __init__(self, db: Database):
        self.db = db

    def open(self, ctx: Context, opening_cash: int) -> str:
        ctx.require("sales.create")
        if not isinstance(opening_cash, int) or opening_cash < 0:
            raise ValidationFailed("رصيد بداية الشيفت غير صحيح")
        with self.db.transaction():
            if open_shift_id(self.db, ctx):
                raise Conflict("يوجد شيفت مفتوح بالفعل", code="shift_already_open")
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
            raise NotFound("لا يوجد شيفت مفتوح")
        s = self.db.one("SELECT * FROM cash_shifts WHERE id=? AND org_id=?", (shift_id, ctx.org_id))
        if not s:
            raise NotFound("الشيفت غير موجود")
        by_kind = {r["kind"]: r["t"] for r in self.db.all(
            "SELECT kind, sum(amount) t FROM cash_movements WHERE shift_id=? GROUP BY kind", (shift_id,))}
        where, args = _sales_of(s)
        card = self.db.one("SELECT coalesce(sum(paid_card),0) c, coalesce(sum(paid_transfer),0) t,"
                           f" coalesce(sum(on_credit),0) cr, count(*) n FROM sales x WHERE {where}", args)
        by_channel = [dict(r) for r in self.db.all(
            "SELECT p.method, coalesce(c.name, '') channel, sum(p.amount) amount FROM sale_payments p"
            " JOIN sales x ON x.id=p.sale_id LEFT JOIN payment_channels c ON c.id=p.channel_id"
            f" WHERE {where} AND p.method IN ('card','transfer') GROUP BY 1,2", args)]
        rets = _returns_of(self.db, s)
        # from the deliveries still with a courier (few), not from every sale of the branch (#113)
        with_courier = self.db.one("SELECT coalesce(sum(d.cash_due),0) c FROM sale_deliveries d CROSS JOIN sales s"
                                   " ON s.id=d.sale_id WHERE d.org_id=? AND d.status IN ('out','delivered')"
                                   " AND s.org_id=? AND s.branch_id=?", (ctx.org_id, ctx.org_id, s["branch_id"]))["c"]
        expected = s["opening_cash"] + sum(by_kind.values())
        out = {**dict(s), "cash_by_kind": by_kind, "expected_cash": expected if s["status"] == "open"
               else s["expected_cash"], "card_total": card["c"], "transfer_total": card["t"], "credit_total": card["cr"],
               "sales_count": card["n"], "by_channel": by_channel, "with_courier": with_courier,
               "card_refunds": rets["card"]["total"], "credit_refunds": rets["credit"]["total"]}
        return redact_summary(ctx, out)

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
                            f"إغلاق الشيفت: {who}" + (f" · {note.strip()}" if (note or "").strip() else ""),
                            "cash_shift", sid)
        from ..core import autobackup
        autobackup.trigger("shift_close")
        return self.close_result(ctx, sid)

    def close_result(self, ctx: Context, shift_id: str) -> dict:
        """What the closer is shown after the close, by his permissions now: the full report with
        sales.read, else only his count against the drawer. Never a refusal after the close committed
        (it released the retry key, and the retry closed the next shift, #324 M5); also the answer to a
        replayed close, so a stored report never outlives a revoked permission (#324 M4)."""
        ctx.require("cash.close")
        if ctx.can("sales.read"):
            return self.report(ctx, shift_id)
        s = self.db.one("SELECT id, status, expected_cash, counted_cash, difference, closed_by FROM cash_shifts"
                        " WHERE id=? AND org_id=?", (shift_id, ctx.org_id))
        if not s or s["status"] != "closed" or s["closed_by"] != ctx.user_id:
            raise NotFound("الشيفت غير موجود")
        return {k: s[k] for k in ("id", "status", "expected_cash", "counted_cash", "difference")}

    def report(self, ctx: Context, shift_id: str) -> dict:
        """Closing report. The person who closed sees the result (shortage / surplus) only after
        his count is saved; the owner and managers see every shift."""
        ctx.require("sales.read")
        s = self.db.one("SELECT * FROM cash_shifts WHERE id=? AND org_id=?", (shift_id, ctx.org_id))
        if not s:
            raise NotFound("الشيفت غير موجود")
        own_closed = s["status"] == "closed" and s["closed_by"] == ctx.user_id
        if not (ctx.can("cash.see_expected") or own_closed):
            raise Forbidden("تقرير الشيفت للمدير والمالك", code="forbidden")
        full = {**dict(s)}
        k = {r["kind"]: r["t"] for r in self.db.all(
            "SELECT kind, sum(amount) t FROM cash_movements WHERE shift_id=? GROUP BY kind", (shift_id,))}
        where, args = _sales_of(s)
        sales = self.db.one("SELECT count(*) n, coalesce(sum(total),0) total, coalesce(sum(paid_card),0) card,"
                            " coalesce(sum(paid_transfer),0) transfer, coalesce(sum(on_credit),0) credit,"
                            f" coalesce(sum(discount_total),0) discount FROM sales x WHERE {where}", args)
        # voided while this shift was open (a void after the close belongs to the shift open at that time)
        voided = self.db.one("SELECT count(*) n FROM sales WHERE shift_id=? AND status='voided' AND sale_no IS NOT NULL"
                             " AND (? IS NULL OR voided_at<?)", (shift_id, s["closed_at"], s["closed_at"]))["n"]
        # and the same-day voids of an earlier shift's sales made while this one was open: their money left this
        # drawer (#324 M8); the earlier shift keeps them as sales it had at its close
        # a void is allowed on the sale's own day only, so such a sale was completed on or after the local start of
        # the day this shift opened: that bound keeps the read to the index range of a day, not every void ever
        from .service import _local_date, local_day_bounds
        day0 = local_day_bounds(_local_date(s["opened_at"]))[0]
        voided += self.db.one("SELECT count(*) n FROM sales WHERE org_id=? AND branch_id=? AND status='voided'"
                              " AND completed_at>=? AND sale_no IS NOT NULL AND (shift_id IS NULL OR shift_id<>?)"
                              " AND voided_at>=? AND (? IS NULL OR voided_at<?)",
                              (ctx.org_id, s["branch_id"], day0, shift_id, s["opened_at"], s["closed_at"],
                               s["closed_at"]))["n"]
        rets = _returns_of(self.db, s)
        by_channel = [dict(r) for r in self.db.all(
            "SELECT p.method, coalesce(c.name, '') channel, sum(p.amount) amount FROM sale_payments p"
            " JOIN sales x ON x.id=p.sale_id LEFT JOIN payment_channels c ON c.id=p.channel_id"
            f" WHERE {where} AND p.method IN ('card','transfer') GROUP BY 1,2", args)]
        names = {u["id"]: u["full_name"] for u in self.db.all(
            "SELECT id, full_name FROM users WHERE id IN (?,?)", (s["opened_by"], s["closed_by"] or ""))}
        # drawer-open commands (#81): manual ones with their reason, apart from the automatic checkout pulses.
        # A command sent is not proof the drawer physically opened (the printer cannot report it).
        manual = [{"at": r["at"], "by": r["full_name"], **json.loads(r["after_json"] or "{}")} for r in self.db.all(
            "SELECT a.at, a.after_json, u.full_name FROM audit_log a LEFT JOIN users u ON u.id=a.user_id"
            " WHERE a.org_id=? AND a.action='cash.drawer_open' AND a.entity_type='cash_shift' AND a.entity_id=?"
            " ORDER BY a.at", (ctx.org_id, shift_id))]
        # from the shift's sales to their audit rows, not from every sale's audit rows in the log (#113)
        auto = self.db.one("SELECT count(*) n FROM sales x CROSS JOIN audit_log a ON a.org_id=x.org_id"
                           " AND a.entity_type='sale' AND a.entity_id=x.id"
                           " WHERE +x.org_id=? AND x.shift_id=? AND a.action='cash.drawer_auto'"   # +: shift index
                           " AND json_extract(a.after_json, '$.outcome')='sent'", (ctx.org_id, shift_id))["n"]
        expected = s["expected_cash"] if s["status"] == "closed" else self._expected(shift_id)
        full.update(cash_by_kind=k, sales_count=sales["n"], sales_total=sales["total"], card_total=sales["card"],
                    transfer_total=sales["transfer"], credit_total=sales["credit"], discount_total=sales["discount"],
                    voided_count=voided, returns_count=sum(r["count"] for r in rets.values()),
                    returns_total=sum(r["total"] for r in rets.values()), returns_by_method=rets,
                    card_refunds=rets["card"]["total"], credit_refunds=rets["credit"]["total"], by_channel=by_channel,
                    expected_cash=expected, opened_by_name=names.get(s["opened_by"]),
                    closed_by_name=names.get(s["closed_by"]),
                    counted_detail=json.loads(s["counted_detail"]) if s["counted_detail"] else None,
                    drawer_manual=manual, drawer_manual_count=len(manual), drawer_auto_count=auto)
        return full

    def recent(self, ctx: Context, limit: int = 30) -> list[dict]:
        ctx.require("cash.see_expected")
        return [dict(r) for r in self.db.all(
            "SELECT s.id, s.status, s.opened_at, s.closed_at, s.opening_cash, s.expected_cash, s.counted_cash,"
            " s.difference, s.close_note, u.full_name closed_by_name FROM cash_shifts s LEFT JOIN users u"
            " ON u.id=s.closed_by WHERE s.org_id=? AND s.branch_id=? ORDER BY s.opened_at DESC LIMIT ?",
            (ctx.org_id, ctx.branch_id, min(limit, 200)))]
