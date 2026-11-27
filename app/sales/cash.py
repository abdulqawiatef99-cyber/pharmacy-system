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
from . import registers


def _now() -> str:
    from ..core.accounts import iso, utcnow
    return iso(utcnow())


def _register_shift(db: Database, ctx: Context, register_id: int) -> str | None:
    row = db.one("SELECT id FROM cash_shifts WHERE org_id=? AND branch_id=? AND register_id=? AND status='open'",
                 (ctx.org_id, ctx.branch_id, register_id))
    return row["id"] if row else None


def open_shift_id(db: Database, ctx: Context, branch_id: str | None = None) -> str | None:
    register = registers.current(db, ctx, branch_id)
    return _register_shift(db, ctx, register["id"]) if register else None


def require_open_shift(db: Database, ctx: Context, branch_id: str | None = None) -> str:
    register = registers.require_current(db, ctx, branch_id)
    sid = _register_shift(db, ctx, register["id"])
    if not sid:
        raise Conflict("الدرج ده مفيش فيه شيفت مفتوح — افتح شيفت قبل أي عملية نقدية", code="no_open_shift")
    return sid


def _shift_row(db: Database, ctx: Context, sid: str):
    return db.one("SELECT s.*, r.name register_name FROM cash_shifts s JOIN registers r"
                  " ON r.org_id=s.org_id AND r.branch_id=s.branch_id AND r.id=s.register_id"
                  " WHERE s.id=? AND s.org_id=?", (sid, ctx.org_id))


def _report_access(db: Database, ctx: Context, s) -> None:
    if registers.can_manage(ctx) or (s["status"] == "closed" and s["closed_by"] == ctx.user_id):
        return
    register = registers.current(db, ctx, s["branch_id"])
    if not register or register["id"] != s["register_id"]:
        raise Forbidden("الشيفت ده يخص درج تاني", code="other_register_shift")


def record(db: Database, ctx: Context, amount: int, kind: str, ref_id: str | None = None,
           note: str | None = None, branch_id: str | None = None) -> str | None:
    """Call inside the business transaction. amount 0 is a no-op."""
    if amount == 0:
        return None
    if kind == "payout" and amount < 0:
        from ..finance import money
        shift = money.drawer_check(db, ctx, -amount, branch_id)
    else:
        shift = require_open_shift(db, ctx, branch_id)
    db.execute("INSERT INTO cash_movements (id, org_id, shift_id, amount, kind, ref_id, note, created_by)"
               " VALUES (?,?,?,?,?,?,?,?)", (new_id(), ctx.org_id, shift, amount, kind, ref_id, note, ctx.user_id))
    return shift


def sale_counted_on_shift(closed_at: str) -> str:
    """SQL predicate (sale alias x). A void on another drawer leaves this drawer's sale intact."""
    return ("(x.status='completed' OR (x.status='voided' AND x.sale_no IS NOT NULL"
            f" AND (x.voided_at>={closed_at} OR (x.void_register_recorded=1 AND x.void_shift_id IS NOT x.shift_id))))")


def _sales_of(s) -> tuple[str, tuple]:
    """A closed shift keeps its sales even if they were voided later."""
    close = "?" if s["status"] == "closed" else "NULL"
    args = (s["id"], s["closed_at"]) if close == "?" else (s["id"],)
    return f"x.shift_id=? AND {sale_counted_on_shift(close)}", args


def _channels_of(db: Database, shift, where: str, args: tuple) -> list[dict]:
    channels = [dict(r) for r in db.all(
        "SELECT p.method, p.channel_id, coalesce(c.name,'') channel, sum(p.amount) amount FROM sale_payments p"
        " JOIN sales x ON x.id=p.sale_id LEFT JOIN payment_channels c ON c.id=p.channel_id AND c.org_id=p.org_id"
        f" WHERE {where} AND p.org_id=x.org_id AND p.method IN ('card','transfer') GROUP BY 1,2,3", args)]
    by_id = {(r["method"], r.pop("channel_id")): r for r in channels}
    for r in _non_drawer_returns(db, shift, by_channel=True):
        key = ("transfer", r["channel_id"])
        row = by_id.get(key)
        if row is None:
            channel = db.one("SELECT name FROM payment_channels WHERE org_id=? AND id=?",
                             (shift["org_id"], r["channel_id"]))
            row = {"method": "transfer", "channel": channel["name"] if channel else "", "amount": 0}
            channels.append(row)
        row["refunded"] = row.get("refunded", 0) + r["amount"]
    return channels


def _voided_elsewhere(db: Database, s) -> int:
    """U2-14 (Codex P2 on #415): the cash rounding of sales of an earlier shift voided in this one. Their cash left
    this drawer here, so their rounding leaves this shift's figure too; the earlier shift keeps its report."""
    # once per sale (Codex P2 on #424): a void can move cash twice (its own cash and a kept change given back)
    # from the shift's few void movements to their sales by id (CROSS JOIN keeps that order; no sales scan)
    return db.one("SELECT coalesce(sum(x.rounding),0) r FROM (SELECT DISTINCT m.ref_id sid FROM cash_movements m"
                  " WHERE m.shift_id=? AND m.org_id=? AND m.kind='void') v CROSS JOIN sales x ON x.id=v.sid"
                  " WHERE x.org_id=? AND (x.shift_id IS NULL OR x.shift_id<>?)",
                  (s["id"], s["org_id"], s["org_id"], s["id"]))["r"]


def _non_drawer_returns(db: Database, shift, *, by_channel: bool = False):
    if by_channel:
        select = "r.channel_id, sum(r.total) amount"
        kind = "r.refund_method='transfer'"
        grouping = "r.channel_id"
    else:
        select = "r.refund_method m, count(*) n, coalesce(sum(r.total),0) t, coalesce(sum(r.rounding),0) rd"
        kind = "(r.refund_method IN ('card','credit','transfer') OR r.total=0)"
        grouping = "r.refund_method"
    rows = db.all(f"SELECT {select} FROM sale_returns r WHERE r.org_id=? AND r.acting_shift_id=?"
                  f" AND {kind} GROUP BY {grouping}", (shift["org_id"], shift["id"]))
    if shift["register_id"] == 1:
        # Before register attribution, non-cash refunds belonged to the branch's only shift by time.
        rows += db.all(f"SELECT {select} FROM sale_returns r JOIN sales x ON x.id=r.sale_id AND x.org_id=r.org_id"
                       " WHERE r.org_id=? AND r.register_recorded=0 AND x.branch_id=? AND r.created_at>=?"
                       f" AND r.created_at<=? AND {kind} GROUP BY {grouping}",
                       (shift["org_id"], shift["branch_id"], shift["opened_at"], shift["closed_at"] or "9999-12-31"))
    return rows


def _returns_of(db: Database, s) -> dict:
    """Cash refunds come from drawer movements. Other returns use the shift recorded when they occurred."""
    by = {m: {"count": 0, "total": 0, "rounding": 0} for m in ("cash", "card", "credit", "transfer")}
    c = db.one("SELECT count(*) n, coalesce(sum(r.total),0) t, coalesce(sum(r.rounding),0) rd FROM sale_returns r"
               " JOIN cash_movements m ON m.ref_id=r.id AND m.kind='refund' AND m.org_id=r.org_id"
               " WHERE m.shift_id=? AND m.org_id=?",
               (s["id"], s["org_id"]))
    by["cash"] = {"count": c["n"], "total": c["t"], "rounding": c["rd"]}
    for r in _non_drawer_returns(db, s):
        cur = by[r["m"]]
        by[r["m"]] = {"count": cur["count"] + r["n"], "total": cur["total"] + r["t"], "rounding": cur["rounding"] + r["rd"]}
    return by


def shift_void_filters(s) -> list[tuple[str, tuple]]:
    """The shift that acted on each void, with existing attribution for pre-register records."""
    filters = [("x.org_id=? AND x.void_shift_id=?", (s["org_id"], s["id"]))]
    if s["register_id"] == 1:
        from .service import _local_date, local_day_bounds

        close = s["closed_at"] or "9999-12-31"
        day0 = local_day_bounds(_local_date(s["opened_at"]))[0]
        filters.extend([
            ("x.org_id=? AND x.shift_id=? AND x.void_register_recorded=0 AND x.voided_at<?",
             (s["org_id"], s["id"], close)),
            ("x.org_id=? AND x.branch_id=? AND x.completed_at>=? AND x.completed_at<?"
             " AND (x.shift_id IS NULL OR x.shift_id<>?) AND x.void_register_recorded=0"
             " AND x.voided_at>=? AND x.voided_at<?",
             (s["org_id"], s["branch_id"], day0, close, s["id"], s["opened_at"], close)),
        ])
    return [(f"{where} AND x.status='voided' AND x.sale_no IS NOT NULL", args) for where, args in filters]


def handover_pin_on(db: Database, org_id: str) -> bool:
    """Owner setting: the person who receives the shift's cash confirms with his PIN (default on)."""
    r = db.one("SELECT value FROM org_settings WHERE org_id=? AND key='shift_handover_pin'", (org_id,))
    return not r or r["value"] not in ("0", "false", "off")


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
                "by_channel", "card_refunds", "credit_refunds", "transfer_refunds", "rounding_total"):
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
            register = registers.require_current(self.db, ctx)
            if _register_shift(self.db, ctx, register["id"]):
                raise Conflict("يوجد شيفت مفتوح بالفعل", code="shift_already_open")
            sid = new_id()
            self.db.execute("INSERT INTO cash_shifts (id, org_id, branch_id, register_id, opening_cash, opened_by, opened_at)"
                            " VALUES (?,?,?,?,?,?,?)", (sid, ctx.org_id, ctx.branch_id, register["id"], opening_cash,
                                                        ctx.user_id, _now()))
            audit.record(self.db, ctx, "cash.shift_open", "cash_shift", sid, after={"opening_cash": opening_cash})
        return sid

    def summary(self, ctx: Context, shift_id: str | None = None) -> dict:
        ctx.require("sales.read")
        if shift_id is None:
            registers.require_current(self.db, ctx)
            shift_id = open_shift_id(self.db, ctx)
        if not shift_id:
            raise NotFound("لا يوجد شيفت مفتوح")
        s = _shift_row(self.db, ctx, shift_id)
        if not s:
            raise NotFound("الشيفت غير موجود")
        _report_access(self.db, ctx, s)
        by_kind = {r["kind"]: r["t"] for r in self.db.all(
            "SELECT kind, sum(amount) t FROM cash_movements WHERE shift_id=? GROUP BY kind", (shift_id,))}
        where, args = _sales_of(s)
        card = self.db.one("SELECT coalesce(sum(paid_card),0) c, coalesce(sum(paid_transfer),0) t, coalesce(sum(rounding),0) rd,"
                           f" coalesce(sum(on_credit),0) cr, count(*) n FROM sales x WHERE {where}", args)
        by_channel = _channels_of(self.db, s, where, args)
        rets = _returns_of(self.db, s)
        # from the deliveries still with a courier (few), not from every sale of the branch (#113)
        with_courier = self.db.one("SELECT coalesce(sum(d.cash_due),0) c FROM sale_deliveries d CROSS JOIN sales s"
                                   " ON s.id=d.sale_id WHERE d.org_id=? AND d.status IN ('out','delivered')"
                                   " AND s.org_id=? AND s.branch_id=?", (ctx.org_id, ctx.org_id, s["branch_id"]))["c"]
        expected = s["opening_cash"] + sum(by_kind.values())
        out = {**dict(s), "cash_by_kind": by_kind, "expected_cash": expected if s["status"] == "open"
               else s["expected_cash"], "card_total": card["c"], "transfer_total": card["t"], "credit_total": card["cr"],
               # U2-14: the shift's cash rounding, less what its returns gave back (review P2)
               "rounding_total": card["rd"] - sum(r.pop("rounding") for r in rets.values()) - _voided_elsewhere(self.db, s),
               "sales_count": card["n"], "by_channel": by_channel, "with_courier": with_courier,
               "card_refunds": rets["card"]["total"], "credit_refunds": rets["credit"]["total"],
               "transfer_refunds": rets["transfer"]["total"]}
        return redact_summary(ctx, out)

    def _expected(self, shift_id: str) -> int:
        s = self.db.one("SELECT opening_cash FROM cash_shifts WHERE id=?", (shift_id,))
        m = self.db.one("SELECT coalesce(sum(amount),0) t FROM cash_movements WHERE shift_id=?", (shift_id,))
        return s["opening_cash"] + m["t"]

    def movement(self, ctx: Context, amount: int, kind: str, note: str) -> None:
        """Payout (مصروف من الدرج) or deposit by an authorized user, with a reason."""
        ctx.require("cash.manage")
        if kind not in ("payout", "deposit") or type(amount) is not int or amount <= 0:
            raise ValidationFailed("بيانات الحركة غير صحيحة")
        if len((note or "").strip()) < 3:
            raise ValidationFailed("اكتب سبب الحركة")
        with self.db.transaction():
            shift = record(self.db, ctx, -amount if kind == "payout" else amount, kind, note=note.strip())
            audit.record(self.db, ctx, f"cash.{kind}", "cash_shift", shift, after={"amount": amount, "note": note})

    def _for_close(self, ctx: Context, shift_id: str | None):
        register = registers.require_current(self.db, ctx)
        if shift_id is None:
            return _shift_row(self.db, ctx, require_open_shift(self.db, ctx))
        shift = _shift_row(self.db, ctx, shift_id)
        if shift and shift["branch_id"] == ctx.branch_id:
            if shift["register_id"] != register["id"] and not registers.can_manage(ctx):
                raise Forbidden("إغلاق شيفت درج تاني للمالك أو المدير", code="other_register_shift")
            if shift["status"] == "open":
                return shift
        raise Conflict("الشيفت ده اتقفل أو اتغيّر. حدّث الشاشة وعدّ الدرج من جديد", code="shift_changed")

    def close(self, ctx: Context, counted_cash: int | None = None, note: str | None = None,
              denominations: dict | None = None, shift_id: str | None = None, *, float_left: int | None = None,
              to_account: str | None = None, handover: bool | None = None) -> dict:
        """Close the open shift. Like E-Stock's «تقفيل درج الكاشير»: the counted cash minus the float left for
        the next shift (default: this shift's opening cash) moves to a money account (default the main
        treasury) in the same transaction (F105). When the money is handed to another person, he confirms
        with his own PIN (the approval on this request); the closer keeps it only if he may manage cash.
        shift_id: the shift the count screen showed. A count made for a shift another till has closed since
        never closes the next one (#324 M2-06)."""
        ctx.require("cash.close")
        detail = None
        if denominations is not None:
            counted_cash, detail = count_total(denominations)
        if not isinstance(counted_cash, int) or counted_cash < 0:
            raise ValidationFailed("المبلغ المعدود غير صحيح")
        from ..finance import money
        with self.db.transaction():
            shift = self._for_close(ctx, shift_id)
            sid = shift["id"]
            if float_left is None and handover is None and not ctx.can("cash.manage"):
                left = counted_cash          # nobody received it: everything stays in the drawer for the next shift
            else:
                left = min(shift["opening_cash"], counted_cash) if float_left is None else float_left
            if not isinstance(left, int) or isinstance(left, bool) or left < 0:
                raise ValidationFailed("الفكة اللي هتفضل في الدرج غير صحيحة")
            if left > counted_cash:
                raise ValidationFailed("الفكة اللي هتفضل في الدرج أكبر من المعدود", code="float_too_large",
                                       details={"counted": counted_cash, "float_left": left})
            receiver = self._receiver(ctx, counted_cash - left, handover)
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
            money.shift_close_transfer(self.db, ctx, sid, left, to_account=to_account, received_by=receiver)
            from . import shift_tools
            shift_tools.record_split(self.db, ctx, sid, detail, left)      # which notes stayed (U2-28 report)
            if diff:
                who = self.db.one("SELECT full_name FROM users WHERE id=?", (ctx.user_id,))["full_name"]
                notify.emit(self.db, ctx, "cash_difference", "error" if diff < 0 else "warning",
                            f"{'عجز' if diff < 0 else 'زيادة'} في الخزينة {abs(diff) / 100:,.2f} ج",
                            f"إغلاق الشيفت: {who} · الدرج: {shift['register_name']}" + (f" · {note.strip()}" if (note or "").strip() else ""),
                            "cash_shift", sid)
        from ..core import autobackup
        autobackup.trigger("shift_close")
        return self.close_result(ctx, sid)

    def _receiver(self, ctx: Context, moved: int, handover: bool | None) -> str:
        """Who takes the cash moved out at close. The closer keeps it only with cash.manage; anyone else hands
        it over, and the receiver types his own PIN (owner setting 'shift_handover_pin', on by default). A
        cashier who closes with no float and no hand-over leaves all the cash in the drawer (nothing moves)."""
        if moved <= 0:
            return ctx.user_id
        if handover is None:
            handover = not ctx.can("cash.manage")      # a float given by a cashier means the cash is handed over
        if not handover:
            if not ctx.can("cash.manage"):
                raise ValidationFailed("سلّم النقدية لمدير أو للمالك", code="handover_required")
            return ctx.user_id
        if not handover_pin_on(self.db, ctx.org_id):
            return ctx.approver_id or ctx.user_id
        if not ctx.approver_id or ctx.approver_id == ctx.user_id or "cash.manage" not in ctx.approver_permissions:
            raise Forbidden("اللي هيستلم النقدية يكتب الرقم السري بتاعه", code="approval_required",
                            details={"permission": "cash.manage", "label": "استلام نقدية الشيفت"})
        return ctx.approver_id

    def close_result(self, ctx: Context, shift_id: str) -> dict:
        """What the closer is shown after the close, by his permissions now: the full report with
        sales.read, else only his count against the drawer. Never a refusal after the close committed
        (it released the retry key, and the retry closed the next shift, #324 M5); also the answer to a
        replayed close, so a stored report never outlives a revoked permission (#324 M4)."""
        ctx.require("cash.close")
        if ctx.can("sales.read"):
            return self.report(ctx, shift_id)
        s = self.db.one("SELECT id, status, expected_cash, counted_cash, difference, closed_by, float_left FROM cash_shifts"
                        " WHERE id=? AND org_id=?", (shift_id, ctx.org_id))
        if not s or s["status"] != "closed" or s["closed_by"] != ctx.user_id:
            raise NotFound("الشيفت غير موجود")
        return {k: s[k] for k in ("id", "status", "expected_cash", "counted_cash", "difference", "float_left")}

    def report(self, ctx: Context, shift_id: str) -> dict:
        """Closing report. The person who closed sees the result (shortage / surplus) only after
        his count is saved; the owner and managers see every shift."""
        ctx.require("sales.read")
        s = _shift_row(self.db, ctx, shift_id)
        if not s:
            raise NotFound("الشيفت غير موجود")
        _report_access(self.db, ctx, s)
        own_closed = s["status"] == "closed" and s["closed_by"] == ctx.user_id
        if not (ctx.can("cash.see_expected") or own_closed):
            raise Forbidden("تقرير الشيفت للمدير والمالك", code="forbidden")
        full = {**dict(s)}
        k = {r["kind"]: r["t"] for r in self.db.all(
            "SELECT kind, sum(amount) t FROM cash_movements WHERE shift_id=? GROUP BY kind", (shift_id,))}
        where, args = _sales_of(s)
        sales = self.db.one("SELECT count(*) n, coalesce(sum(total),0) total, coalesce(sum(paid_card),0) card,"
                            " coalesce(sum(paid_transfer),0) transfer, coalesce(sum(on_credit),0) credit,"
                            f" coalesce(sum(discount_total),0) discount, coalesce(sum(rounding),0) rounding FROM sales x WHERE {where}", args)
        voided = sum(self.db.one(f"SELECT count(*) n FROM sales x WHERE {clause}", args)["n"]
                     for clause, args in shift_void_filters(s))
        rets = _returns_of(self.db, s)
        by_channel = _channels_of(self.db, s, where, args)
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
                    rounding_total=sales["rounding"] - sum(r.pop("rounding") for r in rets.values())
                    - _voided_elsewhere(self.db, s),
                    voided_count=voided, returns_count=sum(r["count"] for r in rets.values()),
                    returns_total=sum(r["total"] for r in rets.values()), returns_by_method=rets,
                    card_refunds=rets["card"]["total"], credit_refunds=rets["credit"]["total"],
                    transfer_refunds=rets["transfer"]["total"], by_channel=by_channel,
                    expected_cash=expected, opened_by_name=names.get(s["opened_by"]),
                    closed_by_name=names.get(s["closed_by"]),
                    counted_detail=json.loads(s["counted_detail"]) if s["counted_detail"] else None,
                    drawer_manual=manual, drawer_manual_count=len(manual), drawer_auto_count=auto)
        from . import shift_tools
        full.update(shift_tools.report_extras(self.db, ctx, dict(s)))
        return full

    def recent(self, ctx: Context, limit: int = 30, register_id: int | None = None) -> list[dict]:
        ctx.require("cash.see_expected")
        if not registers.can_manage(ctx):
            register_id = registers.require_current(self.db, ctx)["id"]
        return [dict(r) for r in self.db.all(
            "SELECT s.id, s.status, s.opened_at, s.closed_at, s.opening_cash, s.expected_cash, s.counted_cash,"
            " s.difference, s.close_note, u.full_name closed_by_name, s.register_id, r.name register_name"
            " FROM cash_shifts s LEFT JOIN users u ON u.id=s.closed_by JOIN registers r"
            " ON r.org_id=s.org_id AND r.branch_id=s.branch_id AND r.id=s.register_id"
            " WHERE s.org_id=? AND s.branch_id=?" + (" AND s.register_id=?" if register_id is not None else "")
            + " ORDER BY s.opened_at DESC LIMIT ?",
            (ctx.org_id, ctx.branch_id) + ((register_id,) if register_id is not None else ()) + (min(limit, 200),))]
