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
           note: str | None = None, branch_id: str | None = None, *,
           exchange_sale_id: str | None = None) -> str | None:
    """Call inside the business transaction. amount 0 is a no-op."""
    if amount == 0:
        return None
    shift = require_open_shift(db, ctx, branch_id)
    offset = 0
    if amount < 0 and exchange_sale_id is not None:
        offset = db.one("SELECT coalesce(sum(amount),0) FROM cash_movements"
                        " WHERE org_id=? AND shift_id=? AND kind='sale' AND ref_id=?",
                        (ctx.org_id, shift, exchange_sale_id))[0]
    if amount + offset < 0:
        from ..finance import money
        money.drawer_check(db, ctx, -amount, branch_id)
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


def _channels_of(db: Database, where: str, args: tuple, refunds: list[dict]) -> list[dict]:
    channels = [dict(r) for r in db.all(
        "SELECT p.method, p.channel_id, coalesce(c.name,'') channel, sum(p.amount) amount FROM sale_payments p"
        " JOIN sales x ON x.id=p.sale_id LEFT JOIN payment_channels c ON c.id=p.channel_id AND c.org_id=p.org_id"
        f" WHERE {where} AND p.org_id=x.org_id AND p.method IN ('card','transfer') GROUP BY 1,2,3", args)]
    by_id = {(r["method"], r.pop("channel_id")): r for r in channels}
    for r in refunds:
        method = r["method"]
        if method not in ("card", "transfer") or (method == "card" and r["channel_id"] is None):
            continue
        key = (method, r["channel_id"])
        if key not in by_id:
            by_id[key] = {"method": method, "channel": r["channel"], "amount": 0}
            channels.append(by_id[key])
        row = by_id[key]
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


def _return_rows(db: Database, s) -> list[dict]:
    """Cash belongs to its drawer movement; new non-cash rows carry a shift ID.
    Older returns and non-cash returns posted without a shift use the original time rule."""
    return [dict(r) for r in db.all(
        " WITH in_shift AS ("
        " SELECT r.* FROM cash_movements m JOIN sale_returns r ON r.id=m.ref_id AND r.org_id=m.org_id"
        " WHERE m.shift_id=? AND m.org_id=? AND m.kind='refund'"
        " UNION ALL"
        " SELECT r.* FROM sale_returns r JOIN sales x ON x.id=r.sale_id AND x.org_id=r.org_id"
        " WHERE r.org_id=? AND x.branch_id=? AND r.created_at>=? AND (? IS NULL OR r.created_at<=?)"
        " AND (r.shift_id=? OR r.shift_id IS NULL) AND (r.refund_method<>'cash' OR r.total=0))"
        " SELECT r.id, coalesce(r.part_of,r.id) return_id, r.sale_id, x.sale_no, r.created_at,"
        " r.refund_method method, r.total amount, r.rounding, r.channel_id, coalesce(c.name,'') channel,"
        " r.reason, u.full_name by, sold.full_name sold_by FROM in_shift r"
        " JOIN sales x ON x.id=r.sale_id AND x.org_id=r.org_id"
        " LEFT JOIN users u ON u.id=r.created_by LEFT JOIN users sold ON sold.id=x.created_by"
        " LEFT JOIN payment_channels c ON c.id=r.channel_id AND c.org_id=r.org_id"
        " ORDER BY r.created_at,r.id", (s["id"], s["org_id"], s["org_id"], s["branch_id"], s["opened_at"],
                                       s["closed_at"], s["closed_at"], s["id"]))]


def _returns_of(rows: list[dict]) -> dict:
    by = {m: {"count": 0, "total": 0, "rounding": 0} for m in ("cash", "card", "credit", "transfer")}
    seen = set()
    for row in rows:
        method = row["method"]
        group = (row["return_id"], method)
        by[method]["count"] += group not in seen
        by[method]["total"] += row["amount"]
        by[method]["rounding"] += row["rounding"]
        seen.add(group)
    return by


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
                "by_channel", "card_refunds", "credit_refunds", "transfer_refunds", "rounding_total", "refund_payments"):
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
        card = self.db.one("SELECT coalesce(sum(paid_card),0) c, coalesce(sum(paid_transfer),0) t, coalesce(sum(rounding),0) rd,"
                           f" coalesce(sum(on_credit),0) cr, count(*) n FROM sales x WHERE {where}", args)
        refund_payments = _return_rows(self.db, s)
        by_channel = _channels_of(self.db, where, args, refund_payments)
        rets = _returns_of(refund_payments)
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
               "transfer_refunds": rets["transfer"]["total"], "refund_payments": refund_payments}
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
        from . import shift_tools
        with self.db.transaction():
            sid = require_open_shift(self.db, ctx)
            if shift_id is not None and shift_id != sid:
                raise Conflict("أُغلق هذا الشيفت وفُتح شيفت جديد بعده. حدّث الشاشة وعُدّ الخزينة من جديد",
                               code="shift_changed")
            s0 = self.db.one("SELECT opening_cash FROM cash_shifts WHERE id=? AND org_id=?", (sid, ctx.org_id))
            if float_left is None and handover is None and not ctx.can("cash.manage"):
                left = counted_cash          # nobody received it: everything stays in the drawer for the next shift
            else:
                left = min(s0["opening_cash"], counted_cash) if float_left is None else float_left
            if not isinstance(left, int) or isinstance(left, bool) or left < 0:
                raise ValidationFailed("الفكة اللي هتفضل في الدرج غير صحيحة")
            if left > counted_cash:
                raise ValidationFailed("الفكة اللي هتفضل في الدرج أكبر من المعدود", code="float_too_large",
                                       details={"counted": counted_cash, "float_left": left})
            split = None
            if detail and float_left is None and left:
                # nobody typed a float: the default (the opening cash) becomes what the notes can make (review of #500)
                left = shift_tools.largest_makeable(detail, left)
            if detail:
                split = shift_tools.split_for(detail, shift_tools.get_target(self.db, ctx.org_id), left)
                if split.get("exact") is False:
                    raise ValidationFailed("الفئات المعدودة ما تكوّنش الفكة اللي هتفضل في الدرج. اختار مبلغ من الفئات دي،"
                                           " أو بدّل الفئات وعدّها تاني قبل التقفيل",
                                           code="float_split_impossible", details={"float_left": left})
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
            if split is not None:
                audit.record(self.db, ctx, "cash.float_split", "cash_shift", sid,
                             after={"keep": split["keep"], "move": split["move"], "float_left": left})
            if diff:
                who = self.db.one("SELECT full_name FROM users WHERE id=?", (ctx.user_id,))["full_name"]
                notify.emit(self.db, ctx, "cash_difference", "error" if diff < 0 else "warning",
                            f"{'عجز' if diff < 0 else 'زيادة'} في الخزينة {abs(diff) / 100:,.2f} ج",
                            f"إغلاق الشيفت: {who}" + (f" · {note.strip()}" if (note or "").strip() else ""),
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
                            f" coalesce(sum(discount_total),0) discount, coalesce(sum(rounding),0) rounding FROM sales x WHERE {where}", args)
        # voided while this shift was open (a void after the close belongs to the shift open at that time)
        voided = self.db.one("SELECT count(*) n FROM sales WHERE shift_id=? AND status='voided' AND sale_no IS NOT NULL"
                             " AND (? IS NULL OR voided_at<?)", (shift_id, s["closed_at"], s["closed_at"]))["n"]
        # and the same-day voids of an earlier shift's sales made while this one was open: their money left this
        # drawer (#324 M8); the earlier shift keeps them as sales it had at its close
        # a void is allowed on the sale's own day only, so such a sale was completed on or after the local start of
        # the day this shift opened: that bound keeps the read to the index range of a day, not every void ever
        from .service import _local_date, local_day_bounds
        day0 = local_day_bounds(_local_date(s["opened_at"]))[0]
        # a sale is completed before it is voided, so the close time also bounds completed_at (an old shift's
        # report never reads the voids that came after it, #352 Codex P2)
        voided += self.db.one("SELECT count(*) n FROM sales WHERE org_id=? AND branch_id=? AND status='voided'"
                              " AND completed_at>=? AND completed_at<? AND sale_no IS NOT NULL"
                              " AND (shift_id IS NULL OR shift_id<>?) AND voided_at>=? AND (? IS NULL OR voided_at<?)",
                              (ctx.org_id, s["branch_id"], day0, s["closed_at"] or "9999-12-31", shift_id,
                               s["opened_at"], s["closed_at"], s["closed_at"]))["n"]
        refund_payments = _return_rows(self.db, s)
        rets = _returns_of(refund_payments)
        by_channel = _channels_of(self.db, where, args, refund_payments)
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
                    voided_count=voided, returns_count=len({p["return_id"] for p in refund_payments}),
                    returns_total=sum(r["total"] for r in rets.values()), returns_by_method=rets,
                    card_refunds=rets["card"]["total"], credit_refunds=rets["credit"]["total"],
                    transfer_refunds=rets["transfer"]["total"], by_channel=by_channel, refund_payments=refund_payments,
                    expected_cash=expected, opened_by_name=names.get(s["opened_by"]),
                    closed_by_name=names.get(s["closed_by"]),
                    counted_detail=json.loads(s["counted_detail"]) if s["counted_detail"] else None,
                    drawer_manual=manual, drawer_manual_count=len(manual), drawer_auto_count=auto)
        from . import shift_tools
        full.update(shift_tools.report_extras(self.db, ctx, dict(s)))
        return full

    def recent(self, ctx: Context, limit: int = 30) -> list[dict]:
        ctx.require("cash.see_expected")
        return [dict(r) for r in self.db.all(
            "SELECT s.id, s.status, s.opened_at, s.closed_at, s.opening_cash, s.expected_cash, s.counted_cash,"
            " s.difference, s.close_note, u.full_name closed_by_name FROM cash_shifts s LEFT JOIN users u"
            " ON u.id=s.closed_by WHERE s.org_id=? AND s.branch_id=? ORDER BY s.opened_at DESC LIMIT ?",
            (ctx.org_id, ctx.branch_id, min(limit, 200)))]
