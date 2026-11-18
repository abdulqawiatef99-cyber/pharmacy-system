"""Final pre-launch review (18/11), money P2: P2-1 shift report, P2-2 store sale needs a shift, P2-3 line merge and
discount limit. Each test failed on `develop` @ 5d00d45."""
from dataclasses import replace

from app.core import accounts
from app.core.errors import Conflict, Forbidden
from app.inventory import service as inv
from tests.test_cashier_acceptance import Base

PW = "Assist-Pass-2026"


def _done(t, sid, ctx=None, **pay):
    ctx = ctx or t.ctx
    f = t.s.safety_check(ctx, sid)
    total = t.s.get(ctx, sid)["total"]
    payments = [{"method": m, "amount": a} for m, a in pay.items() if a] or [{"method": "cash", "amount": total}]
    return t.s.complete(ctx, sid, payments=payments, safety_decisions={i: "ok" for i in range(len(f))})


class TestShiftReportCountsEveryRefund(Base):
    """P2-1: card and credit refunds made during the shift show in its report; a same-day void after the
    shift closed does not rewrite the closed report."""

    def sale(self, units=2, customer=None, **pay):
        sid = self.s.create(self.ctx, customer)
        self.s.add_line(self.ctx, sid, self.pan, units=units)
        return _done(self, sid, **pay)

    def test_card_and_credit_refunds_in_the_report(self):
        sh = self.cd.open(self.ctx, 10000)
        cust = self.s.create_customer(self.ctx, name="عميل تجربة", credit_limit=100000)
        a = self.sale(card=4000)
        b = self.sale(customer=cust, credit=4000)
        c = self.sale()                                                          # 40.00 cash
        for sale, method in ((a, "card"), (b, "credit"), (c, "cash")):
            self.s.return_items(self.ctx, sale["id"], {sale["lines"][0]["id"]: 1}, refund_method=method,
                                reason="مرتجع تجربة")                            # 20.00 each
        r = self.cd.report(self.ctx, sh)
        self.assertEqual((r["returns_count"], r["returns_total"]), (3, 6000))
        self.assertEqual((r["card_refunds"], r["credit_refunds"]), (2000, 2000))
        self.assertEqual(r["returns_by_method"]["cash"], {"count": 1, "total": 2000})
        sm = self.cd.summary(self.ctx)
        self.assertEqual((sm["card_refunds"], sm["credit_refunds"]), (2000, 2000))
        self.assertEqual(sm["expected_cash"], 10000 + 4000 - 2000)                # the drawer only sees cash

    def test_refund_times_use_the_shift_format_and_cash_follows_its_drawer_movement(self):
        """Codex P2 on #323: return times had milliseconds and shift times microseconds, so a return in the
        same millisecond as the close compared as later and fell out of the closed report."""
        sh = self.cd.open(self.ctx, 10000)
        a, b = self.sale(), self.sale(card=4000)
        ra = self.s.return_items(self.ctx, a["id"], {a["lines"][0]["id"]: 1}, refund_method="cash", reason="مرتجع")
        rb = self.s.return_items(self.ctx, b["id"], {b["lines"][0]["id"]: 1}, refund_method="card", reason="مرتجع")
        at = self.db.one("SELECT created_at FROM sale_returns WHERE id=?", (rb["return_id"],))["created_at"]
        self.assertRegex(at, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$")
        self.cd.close(self.ctx, 10000 + 4000 - 2000)
        with self.db.transaction():           # a row written by the old default, same millisecond as the close
            closed = self.db.one("SELECT closed_at FROM cash_shifts WHERE id=?", (sh,))["closed_at"]
            self.db.execute("UPDATE sale_returns SET created_at=? WHERE id=?", (closed[:23] + "Z", ra["return_id"]))
            self.db.execute("UPDATE sale_returns SET created_at=? WHERE id=?", (closed, rb["return_id"]))
        r = self.cd.report(self.ctx, sh)
        self.assertEqual(r["returns_by_method"]["cash"], {"count": 1, "total": 2000})   # by its drawer movement
        self.assertEqual(r["returns_by_method"]["card"], {"count": 1, "total": 2000})   # at the close instant

    def test_a_return_in_the_next_shift_stays_out_of_the_closed_one(self):
        sh = self.cd.open(self.ctx, 10000)
        a = self.sale(card=4000)
        self.cd.close(self.ctx, 14000 - 4000, note="تجربة")                     # 100.00 opening, card sale
        self.cd.open(self.ctx, 10000)
        self.s.return_items(self.ctx, a["id"], {a["lines"][0]["id"]: 1}, refund_method="card", reason="مرتجع")
        r = self.cd.report(self.ctx, sh)
        self.assertEqual((r["returns_count"], r["card_refunds"]), (0, 0))

    def test_void_after_the_close_leaves_the_closed_report_as_it_was(self):
        sh = self.cd.open(self.ctx, 10000)
        a = self.sale()                                                          # 40.00 cash
        self.sale(card=4000)
        self.cd.close(self.ctx, 14000)
        before = self.cd.report(self.ctx, sh)
        keys = ("sales_count", "sales_total", "card_total", "voided_count", "expected_cash", "cash_by_kind",
                "by_channel")
        self.assertEqual((before["sales_count"], before["sales_total"]), (2, 8000))
        self.cd.open(self.ctx, 10000)
        self.s.void(self.ctx, a["id"], "خطأ في الفاتورة")                       # same day, after the close
        after = self.cd.report(self.ctx, sh)
        self.assertEqual({k: after[k] for k in keys}, {k: before[k] for k in keys})
        new = self.cd.summary(self.ctx)
        self.assertEqual(new["expected_cash"], 10000 - 4000)                      # the cash left the new drawer


class TestStoreSaleNeedsAShift(Base):
    """P2-2: a card, transfer or credit store sale with no open shift belonged to no shift and appeared in no
    shift report."""

    def test_card_only_store_sale_refused_without_a_shift(self):
        sid = self.s.create(self.ctx)
        self.s.add_line(self.ctx, sid, self.pan, units=2)
        with self.assertRaises(Conflict) as e:
            _done(self, sid, card=4000)
        self.assertEqual(e.exception.code, "no_open_shift")
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.pan)["total_qty"], 20)
        sh = self.cd.open(self.ctx, 0)
        r = _done(self, sid, card=4000)
        self.assertEqual(self.db.one("SELECT shift_id FROM sales WHERE id=?", (r["id"],))["shift_id"], sh)


class TestLineMergeAndDiscountLimit(Base):
    """P2-3: a scan with no discount merged into a line that had a discount (approved by someone else), so the
    new units took that discount; and raising the quantity of a discounted line skipped the discount limit."""

    def setUp(self):
        super().setUp()
        accounts.add_user(self.db, self.ctx, username="asst", full_name="a", password=PW, role_code="assistant")
        self.asst = self.login("asst", PW)
        self.approved = replace(self.asst, approver_id=self.ctx.user_id, approver_discount_bp=1000)
        self.sid = self.s.create(self.asst)
        self.line = self.s.add_line(self.approved, self.sid, self.pan, units=2, discount_bp=1000)["lines"][0]

    def test_plain_scan_goes_on_its_own_line(self):
        d = self.s.add_line(self.asst, self.sid, self.pan, units=1)
        self.assertEqual(sorted((l["qty_base"], l["discount_bp"]) for l in d["lines"]), [(1, 0), (2, 1000)])
        d = self.s.add_line(self.asst, self.sid, self.pan, units=1)                 # a second plain scan merges
        self.assertEqual(sorted((l["qty_base"], l["discount_bp"]) for l in d["lines"]), [(2, 0), (2, 1000)])
        d = self.s.add_line(self.approved, self.sid, self.pan, units=2, discount_bp=1000)   # same discount: merges
        self.assertEqual(sorted((l["qty_base"], l["discount_bp"]) for l in d["lines"]), [(2, 0), (4, 1000)])

    def test_more_units_at_a_discount_need_the_limit(self):
        with self.assertRaises(Forbidden) as e:
            self.s.update_line(self.asst, self.sid, self.line["id"], qty_base=6)
        self.assertEqual(e.exception.code, "approval_required")
        self.assertEqual(self.s.get(self.asst, self.sid)["lines"][0]["qty_base"], 2)
        self.s.update_line(self.asst, self.sid, self.line["id"], qty_base=1)        # fewer units: allowed
        d = self.s.update_line(self.approved, self.sid, self.line["id"], qty_base=6)
        self.assertEqual(d["lines"][0]["qty_base"], 6)
        row = self.db.one("SELECT discount_approved_by FROM sale_lines WHERE id=?", (self.line["id"],))
        self.assertEqual(row["discount_approved_by"], self.ctx.user_id)
