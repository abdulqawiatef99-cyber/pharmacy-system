"""#131 adversarial money-and-stock audit of develop: invariants the other suites do not already prove.

Each test tries to break one rule the owner relies on when he reconciles the drawer, a customer's or
a supplier's account, the stock or the reports (docs/DAY19_SCRIPT.md, docs/OWNER_TEST_PLAN.md).
Tests that expose a real defect are marked expectedFailure with the issue link, so CI stays green
until the fix lands. Synthetic data only.
"""
import threading
import unittest
from datetime import date, timedelta

from app.catalog import service as cat
from app.inventory import service as inventory
from app.sales import service as sales_service
from app.sales.service import Sales
from tests.test_owner_plan_priority_1_2 import PlanCase


class AuditCase(PlanCase):
    """PlanCase plus: item A (box of 3 strips, 31.00) bought on a real invoice, a credit customer."""

    def setUp(self):
        super().setUp()
        self.a = cat.create_item(self.db, self.ctx, {"name_ar": "صنف مراجعة أ", "pack_size": 3, "public_price": 3100})
        self.sup = cat.create_supplier(self.db, self.ctx, name="مورد مراجعة")
        # 10 boxes + 2 bonus at 20.00 a box: 36 strips cost 200.00, i.e. 5.555556 a strip
        self.pinv, _ = self.manual_purchase(self.sup, "AUD-1", [
            {"raw_name": "x", "item_id": self.a, "qty": 10, "bonus_qty": 2, "unit_net_price": 2000,
             "public_price": 3100, "line_total": 20000}])
        d = self.call("GET", f"/api/purchases/{self.pinv}")
        self.call("POST", f"/api/purchases/{self.pinv}/approve", json={"version": d["invoice"]["version"]})
        self.cust = self.call("POST", "/api/customers", 201, json={"name": "عميل مراجعة", "credit_limit": 10000})["id"]

    # ---------------------------------------------------------------- helpers
    def open_shift(self, opening=10000):
        return self.call("POST", "/api/cash/open", 201, json={"opening_cash": opening})

    def close_shift(self, counted=None):
        cur = self.call("GET", "/api/cash/current")
        counted = cur["expected_cash"] if counted is None else counted
        return self.call("POST", "/api/cash/close", json={"counted_cash": counted, "note": "مراجعة"})

    def ret(self, sid, line_id, qty, method, expected=200, key=None):
        h = {"Idempotency-Key": key} if key else {}
        return self.call("POST", f"/api/sales/{sid}/return", expected, headers=h,
                         json={"lines": {line_id: qty}, "refund_method": method, "reason": "مراجعة"})

    def supplier_balance(self):
        return self.call("GET", f"/api/suppliers/{self.sup}")["balance"]

    def balance(self, cid=None):
        return Sales(self.db).balance(self.ctx, cid or self.cust)


class TestCashDrawer(AuditCase):
    def test_mixed_payment_counts_only_the_cash_part_once(self):
        """Cash + card + credit on one sale: the drawer takes the cash part only, the shift shows each part
        once, the customer owes the credit part, and a full return on credit leaves the drawer untouched."""
        self.open_shift()
        card = self.channel("card")
        sid = self.draft(self.cust)
        d = self.line(sid, self.a, packs=2)                      # 62.00
        self.assertEqual(d["total"], 6200)
        done = self.complete(sid, [{"method": "cash", "amount": 3000},
                                   {"method": "card", "amount": 2000, "channel_id": card},
                                   {"method": "credit", "amount": 1200}])
        self.assertEqual((done["paid_cash"], done["paid_card"], done["on_credit"]), (3000, 2000, 1200))
        self.assertEqual(self.drawer(), 13000)
        cur = self.call("GET", "/api/cash/current")
        self.assertEqual((cur["card_total"], cur["credit_total"], cur["sales_count"]), (2000, 1200, 1))
        self.assertEqual(self.balance(), 1200)
        self.ret(sid, done["lines"][0]["id"], 6, "credit")
        self.assertEqual(self.drawer(), 13000)
        self.assertEqual(self.balance(), 1200 - 6200)            # the shop now owes him the rest
        self.assertEqual(self.qty(self.a), 36)

    def test_expected_across_close_and_reopen_counts_each_movement_in_its_own_shift(self):
        """Opening + cash sales - cash refunds - payouts + collections, per shift; a refund made after the
        close of the sale's shift is taken from the new shift and never changes the closed one."""
        self.open_shift(10000)
        s1 = self.sell(self.a, units=3)                           # 31.00 cash
        self.call("POST", f"/api/customers/{self.cust}/payments", json={"amount": 2000, "method": "cash"})
        self.call("POST", "/api/cash/movements", json={"amount": 1500, "kind": "payout", "note": "مراجعة"})
        self.assertEqual(self.drawer(), 10000 + 3100 + 2000 - 1500)
        first = self.close_shift()
        self.assertEqual((first["expected_cash"], first["difference"]), (13600, 0))
        self.open_shift(5000)
        r = self.ret(s1["id"], s1["lines"][0]["id"], 1, "cash")
        self.assertEqual(r["refund"], 1034)                       # 31 = 10.33 + 10.33 + 10.34
        self.assertEqual(self.drawer(), 5000 - 1034)
        old = self.call("GET", f"/api/cash/shifts/{first['id']}")
        self.assertEqual((old["expected_cash"], old["counted_cash"], old["returns_total"]), (13600, 13600, 0))
        self.assertEqual(old["cash_by_kind"].get("sale"), 3100)

    def test_cash_refund_with_the_drawer_closed_is_refused_and_moves_nothing(self):
        self.open_shift()
        s1 = self.sell(self.a, units=3)
        self.close_shift()
        before = self.qty(self.a)
        r = self.ret(s1["id"], s1["lines"][0]["id"], 1, "cash", expected=409)
        self.assertEqual(r["error"]["code"], "no_open_shift")
        self.assertEqual(self.qty(self.a), before)                # the stock did not come back either
        self.assertEqual(self.call("GET", f"/api/sales/{s1['id']}")["returned_total"], 0)

    def test_delivery_cash_enters_the_drawer_only_when_the_courier_hands_it_in(self):
        self.open_shift()
        sid = self.draft()
        self.call("PATCH", f"/api/sales/{sid}", json={"kind": "delivery", "delivery": {"address": "عنوان", "fee": 500}})
        d = self.line(sid, self.a, packs=1)
        self.assertEqual(d["total"], 3600)
        self.complete(sid, [{"method": "cash", "amount": 3600}])
        self.assertEqual(self.drawer(), 10000)
        self.assertEqual(self.call("GET", "/api/cash/current")["with_courier"], 3600)
        first = self.close_shift()
        self.assertEqual(first["expected_cash"], 10000)
        self.open_shift(10000)
        self.call("POST", f"/api/sales/{sid}/delivery", json={"status": "settled"})
        self.assertEqual(self.drawer(), 13600)
        self.assertEqual(self.call("GET", "/api/cash/current")["with_courier"], 0)
        # settling twice is refused, the cash is taken once
        self.call("POST", f"/api/sales/{sid}/delivery", 409, json={"status": "settled"})
        self.assertEqual(self.drawer(), 13600)


class TestReturns(AuditCase):
    def test_partial_returns_after_line_discount_add_up_to_exactly_what_was_paid(self):
        """Two boxes of 31.00 at 10% off = 55.80; returning the 6 strips one by one refunds 55.80 in total,
        each refund within a piaster of 9.30, and a seventh strip is refused."""
        self.open_shift()
        sid = self.draft()
        d = self.line(sid, self.a, packs=2, discount_bp=1000)
        self.assertEqual(d["total"], 5580)
        done = self.complete(sid, [{"method": "cash", "amount": 5580}])
        lid = done["lines"][0]["id"]
        refunds = [self.ret(sid, lid, 1, "cash")["refund"] for _ in range(6)]
        self.assertEqual(sum(refunds), 5580)
        self.assertTrue(all(abs(r - 930) <= 1 for r in refunds), refunds)   # 9.30 a strip, give or take 0.01
        r = self.ret(sid, lid, 1, "cash", expected=409)
        self.assertEqual(r["error"]["code"], "return_exceeds")
        self.assertEqual(self.drawer(), 10000)
        self.assertEqual(self.qty(self.a), 36)

    def test_returning_more_than_sold_in_one_request_is_refused_whole(self):
        self.open_shift()
        s = self.sell(self.a, units=2)
        r = self.ret(s["id"], s["lines"][0]["id"], 3, "cash", expected=409)
        self.assertEqual(r["error"]["code"], "return_exceeds")
        self.assertEqual((self.qty(self.a), self.drawer()), (34, 10000 + s["total"]))

    def test_return_retried_with_the_same_key_refunds_once(self):
        self.open_shift()
        s = self.sell(self.a, units=3)
        lid = s["lines"][0]["id"]
        first = self.ret(s["id"], lid, 1, "cash", key="ret-1")
        again = self.c.post(f"/api/sales/{s['id']}/return", headers={**self._h(), "Idempotency-Key": "ret-1"},
                            json={"lines": {lid: 1}, "refund_method": "cash", "reason": "مراجعة"})
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json(), first)
        self.assertEqual(self.drawer(), 10000 + 3100 - first["refund"])
        self.assertEqual(self.qty(self.a), 34)
        self.assertEqual(len(self.call("GET", f"/api/sales/{s['id']}")["returns"]), 1)

    def test_complete_twice_with_the_same_request_id_posts_once(self):
        self.open_shift()
        sid = self.draft()
        self.line(sid, self.a, packs=1)
        self.complete(sid, [{"method": "cash", "amount": 3100}])
        again = self.complete(sid, [{"method": "cash", "amount": 3100}])
        self.assertTrue(again.get("replayed"))
        self.assertEqual((self.drawer(), self.qty(self.a)), (13100, 33))

    def test_fast_double_complete_from_two_threads_posts_once(self):
        """Two 'complete' clicks racing (different request ids): one wins, the other is refused, the drawer
        and the stock move once."""
        self.open_shift()
        sid = self.draft()
        d = self.line(sid, self.a, packs=1)
        sales = Sales(self.db)
        results, errors = [], []

        def go(n):
            try:
                results.append(sales.complete(self.ctx, sid, payments=[{"method": "cash", "amount": 3100}],
                                              version=d["version"], request_id=f"race-{n}",
                                              safety_decisions={}))
            except Exception as e:          # noqa: BLE001 - the loser's refusal is the point
                errors.append(e)
        threads = [threading.Thread(target=go, args=(n,)) for n in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(results), 1, errors)
        self.assertEqual((self.drawer(), self.qty(self.a)), (13100, 33))

    def test_void_after_the_shift_closed_takes_the_cash_from_the_open_shift(self):
        self.open_shift()
        s = self.sell(self.a, packs=1)
        first = self.close_shift()
        self.open_shift(5000)
        self.call("POST", f"/api/sales/{s['id']}/void", json={"reason": "مراجعة الإلغاء"})
        self.assertEqual(self.drawer(), 5000 - 3100)
        self.assertEqual(self.qty(self.a), 36)
        old = self.call("GET", f"/api/cash/shifts/{first['id']}")
        self.assertEqual(old["expected_cash"], 13100)

    @unittest.expectedFailure  # https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/142
    def test_cash_refund_on_a_sale_paid_wholly_on_credit_is_refused(self):
        """A sale put wholly on the customer's account brought no cash into the drawer; refunding it in
        cash pays out money that never came in while the customer still owes the sale."""
        self.open_shift()
        sid = self.draft(self.cust)
        self.line(sid, self.a, packs=1)
        done = self.complete(sid, [{"method": "credit", "amount": 3100}])
        self.ret(sid, done["lines"][0]["id"], 3, "cash", expected=409)
        self.assertEqual((self.drawer(), self.balance()), (10000, 3100))

    def _h(self):
        from tests.test_api import H
        return H


class TestCredit(AuditCase):
    def test_credit_limit_is_enforced_against_the_balance_including_collections_and_returns(self):
        self.open_shift()
        sid = self.draft(self.cust)
        self.line(sid, self.a, packs=3)                           # 93.00
        done = self.complete(sid, [{"method": "credit", "amount": 9300}])
        sid2 = self.draft(self.cust)
        self.line(sid2, self.a, units=1)                          # 10.33 more would pass 100.00
        r = self.complete(sid2, [{"method": "credit", "amount": 1033}], expected=409)
        self.assertEqual(r["error"]["code"], "credit_limit")
        self.ret(sid, done["lines"][0]["id"], 3, "credit")          # one box back onto the account
        self.assertEqual(self.balance(), 6200)
        self.complete(sid2, [{"method": "credit", "amount": 1033}])
        self.assertEqual(self.balance(), 7233)
        self.assertEqual(self.drawer(), 10000)                    # none of this touched the drawer

    @unittest.expectedFailure  # https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/143
    def test_two_credit_sales_at_once_cannot_pass_the_limit_together(self):
        """Two cashiers complete credit sales for the same customer at the same moment. The limit is checked
        before the write transaction and not again inside it, so both pass a check that only one should."""
        self.open_shift()
        s1, s2 = self.draft(self.cust), self.draft(self.cust)
        self.line(s1, self.a, packs=2)                            # 62.00
        self.line(s2, self.a, packs=2)                            # 62.00; together 124.00 > 100.00
        sales = Sales(self.db)
        real = sales.safety_check
        fired = []

        def other_cashier_meanwhile(ctx, sid):
            out = real(ctx, sid)
            if sid == s1 and not fired:                           # s1 passed its limit check already
                fired.append(1)
                Sales(self.db).complete(self.ctx, s2, payments=[{"method": "credit", "amount": 6200}],
                                        safety_decisions={})
            return out
        sales.safety_check = other_cashier_meanwhile
        try:
            sales.complete(self.ctx, s1, payments=[{"method": "credit", "amount": 6200}], safety_decisions={})
        except Exception:                   # noqa: BLE001 - refusing the second one is the right outcome
            pass
        self.assertLessEqual(self.balance(), 10000)


class TestStock(AuditCase):
    def test_strips_and_boxes_never_create_or_lose_a_unit_and_the_ledger_matches(self):
        self.open_shift()
        self.sell(self.a, units=1)
        self.sell(self.a, units=2)
        self.sell(self.a, packs=1, units=1)
        self.assertEqual(self.qty(self.a), 36 - 1 - 2 - 4)
        self.assertEqual(inventory.verify_balances(self.db, self.ctx.org_id), [])
        rows = self.db.all("SELECT qty_delta, created_by, movement_type FROM stock_movements WHERE item_id=?", (self.a,))
        self.assertTrue(all(r["created_by"] and r["movement_type"] for r in rows))
        self.assertEqual(sum(r["qty_delta"] for r in rows), 29)

    def test_fefo_sells_the_earliest_expiry_and_never_an_expired_batch(self):
        item = cat.create_item(self.db, self.ctx, {"name_en": "FEFO probe", "pack_size": 1, "public_price": 1000})
        today = sales_service.local_today()
        with self.db.transaction():
            for no, days, qty in (("LATE", 400, 5), ("SOON", 40, 2), ("GONE", -1, 9)):
                b = inventory.get_or_create_batch(self.db, self.ctx, item, no, (today + timedelta(days=days)).isoformat())
                inventory.post_movement(self.db, self.ctx, item_id=item, qty_delta=qty, batch_id=b,
                                        movement_type="purchase", unit_cost=None)
        self.open_shift()
        sid = self.draft()
        r = self.line(sid, item, 409, units=8)                    # 7 sellable; the 9 expired are not
        self.assertEqual(r["error"]["details"], {"available": 7, "expired": 9})
        done = self.sell(item, units=3)
        self.assertEqual([(a["batch_no"], a["qty"]) for a in done["lines"][0]["allocations"]],
                         [("SOON", 2), ("LATE", 1)])

    def test_purchase_reversal_after_part_was_sold_is_refused_and_never_goes_negative(self):
        self.open_shift()
        self.sell(self.a, packs=1)
        r = self.call("POST", f"/api/purchases/{self.pinv}/cancel", 409, json={"reason": "مراجعة العكس"})
        self.assertEqual(r["error"]["code"], "insufficient_stock")
        self.assertEqual(self.supplier_balance(), 20000)          # the supplier's account is untouched too
        self.assertEqual(self.qty(self.a), 33)
        neg = self.db.one("SELECT count(*) c FROM stock_balances WHERE qty < 0")["c"]
        self.assertEqual(neg, 0)

    @unittest.expectedFailure  # https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/144
    def test_returning_a_whole_batch_to_the_supplier_credits_exactly_what_was_paid_for_it(self):
        """36 strips bought for 200.00 (10 boxes + 2 bonus). Sending all 36 back at the suggested value must
        clear the 200.00 off the supplier's account, not 200.16: the per-strip value 5.555556 is rounded to
        5.56 before it is multiplied by the quantity."""
        self.assertEqual(self.supplier_balance(), 20000)
        batch = self.db.one("SELECT id FROM batches WHERE item_id=?", (self.a,))["id"]
        self.call("POST", "/api/supplier-returns", json={"supplier_id": self.sup, "lines": [
            {"item_id": self.a, "batch_id": batch, "qty_base": 36, "reason": "expired"}]})
        self.assertEqual(self.qty(self.a), 0)
        self.assertEqual(self.supplier_balance(), 0)

    def _h(self):
        from tests.test_api import H
        return H


class TestReports(AuditCase):
    @unittest.expectedFailure  # https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/145
    def test_top_items_revenue_equals_what_the_invoices_charged(self):
        """The sales report's per-item revenue must equal the line totals the customers paid (less returns).
        A box split by FEFO over two batches at the same price is 31.00 on the invoice."""
        self.open_shift()
        item = cat.create_item(self.db, self.ctx, {"name_en": "Split probe", "pack_size": 3, "public_price": 3100})
        today = sales_service.local_today()
        with self.db.transaction():
            for no, days, qty in (("S1", 100, 2), ("S2", 200, 4)):
                b = inventory.get_or_create_batch(self.db, self.ctx, item, no, (today + timedelta(days=days)).isoformat())
                inventory.post_movement(self.db, self.ctx, item_id=item, qty_delta=qty, batch_id=b,
                                        movement_type="purchase", unit_cost=None)
        done = self.sell(item, packs=1)
        self.assertEqual(done["total"], 3100)
        rep = self.call("GET", f"/api/reports/sales?from={today}&to={today}")
        top = {t["item_id"]: t for t in rep["top_items"]}
        self.assertEqual(top[item]["revenue"], 3100)

    @unittest.expectedFailure  # https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/145
    def test_top_items_revenue_rounds_the_discount_like_the_invoice(self):
        """10.35 at 10% off: the invoice takes 1.04 off (half up) and charges 9.31; the report rounds the net
        in floating point instead and shows 9.32."""
        self.open_shift()
        item = cat.create_item(self.db, self.ctx, {"name_en": "Half probe", "pack_size": 1, "public_price": 1035})
        self.stock(item, 5, "5")
        sid = self.draft()
        self.line(sid, item, units=1, discount_bp=1000)
        done = self.complete(sid, [{"method": "cash", "amount": 931}])
        self.assertEqual(done["total"], 931)
        today = sales_service.local_today()
        rep = self.call("GET", f"/api/reports/sales?from={today}&to={today}")
        self.assertEqual({t["item_id"]: t for t in rep["top_items"]}[item]["revenue"], 931)

    @unittest.expectedFailure  # https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/146
    def test_a_refund_made_today_for_yesterdays_sale_is_in_todays_summary_and_yesterdays_stays_closed(self):
        """The drawer pays a refund today; the day summary and the sales report put it on the day of the
        original sale instead, so today's report misses it and a day already reconciled changes later."""
        self.open_shift()
        s = self.sell(self.a, units=3)
        yesterday = sales_service.local_today() - timedelta(days=1)
        lo, _ = sales_service.local_day_bounds(yesterday)
        with self.db.transaction():                       # the sale was made yesterday
            self.db.execute("UPDATE sales SET completed_at=? WHERE id=?", (lo.replace("00:00:00", "12:00:00"), s["id"]))
        before = self.call("GET", f"/api/sales/summary?day={yesterday}")
        r = self.ret(s["id"], s["lines"][0]["id"], 1, "cash")
        after = self.call("GET", f"/api/sales/summary?day={yesterday}")
        today = self.call("GET", "/api/sales/summary")
        self.assertEqual(after["returns"], before["returns"])          # a reconciled day does not move
        self.assertEqual(today["returns"], r["refund"])                # the refund the drawer paid today

    def test_day_summary_profit_uses_the_cost_paid_with_bonus_spread(self):
        """Selling one box of A at 31.00 when a strip cost 200.00/36: profit 31.00 - 16.67 = 14.33."""
        self.open_shift()
        self.sell(self.a, packs=1)
        s = self.call("GET", "/api/sales/summary")
        self.assertEqual((s["total"], s["cost_egp"], s["gross_profit_egp"]), (3100, "16.67", "14.33"))


if __name__ == "__main__":
    unittest.main()
