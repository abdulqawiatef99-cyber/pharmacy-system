"""Codex full review 18/11, part 1 (money), findings M1-M7: one test per finding. Synthetic data only.

M1, M3, M4, M5 failed on develop @ 4021c27 (verified). M2, M6, M7 pass there: they pin the behaviour
the finding questioned, which is correct (not verified)."""
from datetime import date, timedelta
from decimal import Decimal

from starlette.testclient import TestClient

from app.catalog import service as cat
from app.core import people
from app.finance import expenses, payroll
from app.inventory import service as inv
from app.sales.cash import CashDrawer
from app.sales.service import Sales
from tests.helpers import DBTestCase, today
from tests.test_api import APICase, H

T = date.today()
PW = "Staff-Pass-2026"


def _stock(db, ctx, item, qty, cost="15"):
    with db.transaction():
        b = inv.get_or_create_batch(db, ctx, item, "B1", (T + timedelta(days=400)).isoformat())
        inv.post_movement(db, ctx, item_id=item, qty_delta=qty, batch_id=b, movement_type="purchase",
                          unit_cost=Decimal(cost))


def _sell(s, ctx, item, units, **details):
    sid = s.create(ctx)
    d = s.add_line(ctx, sid, item, units=units)
    if details:
        d = s.set_details(ctx, sid, details)
    f = s.safety_check(ctx, sid)
    return s.complete(ctx, sid, payments=[{"method": "cash", "amount": d["total"]}],
                      safety_decisions={i: "ok" for i in range(len(f))}, confirm_below_cost=True)


class TestM1LooseUnitsWorthMoreThanAPack(DBTestCase):
    """A pack of 100 at 10.50 EGP: one unit is 11 piasters (10.5 half up), so 99 loose units (10.89) are
    worth more than the whole pack (10.50). A return that leaves 99 units out of a whole pack must not
    book a negative refund (cash INTO the drawer)."""

    def setUp(self):
        super().setUp()
        self.s, self.cd = Sales(self.db), CashDrawer(self.db)
        self.cd.open(self.ctx, 10000)
        self.item = cat.create_item(self.db, self.ctx, {"name_en": "Loose Tab 100", "pack_size": 100,
                                                        "public_price": 1050})
        _stock(self.db, self.ctx, self.item, 300, cost="0.05")

    def test_no_return_ever_takes_cash_in_and_all_returns_add_up_to_the_line(self):
        sale = _sell(self.s, self.ctx, self.item, 104)          # 1 pack 10.50 + 4 x 0.11 = 10.94
        self.assertEqual(sale["total"], 1094)
        line = sale["lines"][0]["id"]
        drawer = lambda: self.cd.summary(self.ctx)["expected_cash"]
        start = drawer()
        refunds = []
        for qty in (4, 1, 99):                                    # 100 kept, then 99, then 0
            before = drawer()
            r = self.s.return_items(self.ctx, sale["id"], {line: qty}, refund_method="cash", reason="مرتجع تجربة")
            refunds.append(r["refund"])
            self.assertGreaterEqual(r["refund"], 0, f"return of {qty} booked {r['refund']}")
            self.assertLessEqual(drawer(), before)               # a return never puts cash in the drawer
        self.assertEqual(sum(refunds), 1094)                      # exactly what was paid, not a piaster more
        self.assertEqual(drawer(), start - 1094)


class TestM1ReturnsBookedBeforeTheFix(TestM1LooseUnitsWorthMoreThanAPack):
    """Codex on #331: a line partly returned under the old rule (kept units at the rounded-up unit price,
    no per-line refund in the audit row) and then returned again must still refund exactly what was paid."""

    def _old_rule_return(self, sid, line, qty):
        from unittest.mock import patch
        from app.sales import service as svc
        orig_audit = svc.audit.record

        def old_kept(allocs, kept, bp, line_total=None, price=None):
            # kept_value as it was before #324 M1: loose units at the (rounded) unit price
            g = svc._rule_as_sold(allocs, bp, line_total)((kept[a["id"]], a) for a in allocs)
            value = g - svc.discount_of(g, bp)
            if line_total is None:
                return value
            if all(kept[a["id"]] == a["qty_base"] for a in allocs):
                return line_total
            return min(value, line_total)

        def old_audit(db, ctx, action, et, eid, **kw):
            (kw.get("after") or {}).pop("line_refunds", None)   # the old audit row had no per-line refunds
            return orig_audit(db, ctx, action, et, eid, **kw)

        with patch.object(svc, "kept_value", old_kept), patch.object(svc.Sales, "_line_refunded", lambda *a: 0, create=True), \
                patch.object(svc.audit, "record", old_audit):
            return self.s.return_items(self.ctx, sid, {line: qty}, refund_method="cash", reason="مرتجع قديم")

    def test_codex_scenario_old_return_then_the_rest(self):
        sale = _sell(self.s, self.ctx, self.item, 199)          # 1 pack 10.50 + 99 x 0.11 = 21.39
        self.assertEqual(sale["total"], 2139)
        line = sale["lines"][0]["id"]
        self.assertEqual(self._old_rule_return(sale["id"], line, 1)["refund"], 11)
        r = self.s.return_items(self.ctx, sale["id"], {line: 198}, refund_method="cash", reason="مرتجع تجربة")
        self.assertEqual(r["refund"], 2128)                       # 2139 - 11: settles the line exactly
        self.assertEqual(self.db.one("SELECT returned_total FROM sales WHERE id=?", (sale["id"],))[0], 2139)

    def test_old_return_then_partial_returns_never_exceed_the_line(self):
        sale = _sell(self.s, self.ctx, self.item, 199)
        line = sale["lines"][0]["id"]
        refunds = [self._old_rule_return(sale["id"], line, 1)["refund"]]
        for qty in (98, 1, 99):                                   # 100 kept, 99, then 0
            r = self.s.return_items(self.ctx, sale["id"], {line: qty}, refund_method="cash", reason="مرتجع تجربة")
            self.assertGreaterEqual(r["refund"], 0)
            refunds.append(r["refund"])
            self.assertLessEqual(sum(refunds), 2139)
        self.assertEqual(sum(refunds), 2139)


class TestM2ReturnRetry(APICase):
    """M2 claims a return without an Idempotency-Key (or one over 100 chars) is not replay-safe. True of
    the raw API, but the shipped client sends a 36-char UUID key on every POST and reuses it on a retry
    (app.js `api`), and that path is replay-safe: pinned here."""

    def test_retry_with_the_clients_key_refunds_once(self):
        from app.sales.cash import CashDrawer as CD
        _stock(self.db, self.ctx, self.pan, 10)
        CD(self.db).open(self.ctx, 10000)
        s = Sales(self.db)
        sale = _sell(s, self.ctx, self.pan, 2)
        self.http_login()
        hdr = {**H, "Idempotency-Key": "0b7c6f9e-3d1a-4c55-9d0e-6a2b8f1c4e77"}
        body = {"lines": {sale["lines"][0]["id"]: 1}, "refund_method": "cash", "reason": "مرتجع تجربة"}
        a = self.c.post(f"/api/sales/{sale['id']}/return", json=body, headers=hdr)
        b = self.c.post(f"/api/sales/{sale['id']}/return", json=body, headers=hdr)
        self.assertEqual((a.status_code, b.status_code), (200, 200), b.text)
        self.assertEqual(b.headers.get("Idempotent-Replay"), "true")
        self.assertEqual(self.db.one("SELECT count(*) FROM sale_returns")[0], 1)


class _TwoUsers(APICase):
    def setUp(self):
        super().setUp()
        _stock(self.db, self.ctx, self.pan, 20)
        self.s, self.cd = Sales(self.db), CashDrawer(self.db)
        self.cd.open(self.ctx, 10000)

    def staff(self, username, role):
        uid = people.add_user(self.db, self.ctx, username=username, full_name=username, password=PW, role_code=role)
        c = TestClient(self.app)
        self.http_login(username, PW, client=c)
        return uid, c


class TestM3DeliverySettledByAnotherCashier(_TwoUsers):
    """An assistant (no sales.totals) settles a delivery sold by someone else: the settlement commits,
    so the answer must be a success, not a 403 that makes the retry fail with delivery_state."""

    def test_settle_answers_success_and_a_retry_replays(self):
        sale = _sell(self.s, self.ctx, self.pan, 2, kind="delivery", delivery={"phone": "0100"})
        _, c = self.staff("asst", "assistant")
        hdr = {**H, "Idempotency-Key": "settle-1"}
        drawer = self.cd.summary(self.ctx)["expected_cash"]
        r = c.post(f"/api/sales/{sale['id']}/delivery", json={"status": "settled"}, headers=hdr)
        self.assertEqual(r.status_code, 200, r.text)
        retry = c.post(f"/api/sales/{sale['id']}/delivery", json={"status": "settled"}, headers=hdr)
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.headers.get("Idempotent-Replay"), "true")
        self.assertEqual(self.cd.summary(self.ctx)["expected_cash"], drawer + sale["total"])   # once
        self.assertNotIn("lines", r.json())                       # no invoice detail he may not open


class TestM4M5ShiftClose(_TwoUsers):
    def _close(self, c, key, counted):
        return c.post("/api/cash/close", json={"counted_cash": counted, "note": "عد تجربة"},
                      headers={**H, "Idempotency-Key": key})

    def test_m5_close_without_sales_read_succeeds_and_retry_never_closes_the_next_shift(self):
        uid, c = self.staff("closer", "assistant")
        people.set_exception(self.db, self.ctx, uid, "sales.read", "deny")
        first = self._close(c, "close-1", 10000)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["difference"], 0)
        self.assertNotIn("sales_total", first.json())            # no sales figures without sales.read
        nxt = self.cd.open(self.ctx, 5000)                        # the next cashier opens a new shift
        retry = self._close(c, "close-1", 10000)
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.headers.get("Idempotent-Replay"), "true")
        self.assertEqual(self.db.one("SELECT status FROM cash_shifts WHERE id=?", (nxt,))[0], "open")

    def test_m4_replay_after_permission_revoked_is_refused(self):
        uid, c = self.staff("mgr", "manager")
        first = self._close(c, "close-2", 10000)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertIn("sales_total", first.json())
        people.set_exception(self.db, self.ctx, uid, "cash.close", "deny")
        retry = self._close(c, "close-2", 10000)
        self.assertEqual(retry.status_code, 403, retry.text)

    def test_m4_replay_follows_current_report_visibility(self):
        uid, c = self.staff("mgr2", "manager")
        self.assertEqual(self._close(c, "close-3", 10000).status_code, 200)
        people.set_exception(self.db, self.ctx, uid, "sales.read", "deny")
        retry = self._close(c, "close-3", 10000)
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertNotIn("sales_total", retry.json())            # the stored full report is not replayed


class TestM6PayrollAbsence(DBTestCase):
    """M6: 100.01 EGP over 2 work days, 1 missing: 50.005 EGP is 5000.5 piasters, half up = 5001. That is
    the project's one rounding rule (half up, the rule of discount_of), applied once; nothing is lost."""

    def test_absence_rounds_half_up_once_and_parts_add_up(self):
        r = payroll.compute(10001, 2, 1, 0)
        self.assertEqual(r["absence_deduction"], 5001)
        self.assertEqual(r["net"] + r["absence_deduction"], 10001)


class TestM7ProfitCost(DBTestCase):
    """M7: the profit report sums exact decimal costs and rounds the total once, half up (the same rule
    as cost_piasters / discount_of); it does not round each unit."""

    def test_cost_total_rounded_once_half_up(self):
        s, cd = Sales(self.db), CashDrawer(self.db)
        cd.open(self.ctx, 0)
        item = cat.create_item(self.db, self.ctx, {"name_en": "Cost Probe", "pack_size": 1, "public_price": 2000})
        _stock(self.db, self.ctx, item, 10, cost="10.005")        # 1000.5 piasters a unit
        _sell(s, self.ctx, item, 1)
        self.assertEqual(expenses.profit(self.db, self.ctx, today(), today())["cost"], 1001)
        _sell(s, self.ctx, item, 1)                               # 2 x 1000.5 = 2001 exactly, not 1001 + 1001
        self.assertEqual(expenses.profit(self.db, self.ctx, today(), today())["cost"], 2001)
