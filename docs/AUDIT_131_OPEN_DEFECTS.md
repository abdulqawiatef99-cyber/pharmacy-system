# #131 audit: reproductions of the defects still open

These tests come from the money-and-stock audit (#131). Each one fails on `develop` until its issue is fixed.
They are kept here, not in the suite with `expectedFailure`, so no failure is suppressed (AGENTS.md, Codex P1
on #147). A fix PR copies its test into `tests/test_audit_money_stock.py` (the class named below, base `AuditCase`
there), shows it failing before the fix and passing after, and deletes its section here.

## #143 — class `TestCredit`

```python
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
```

## #144 — class `TestStock`

```python
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
```

## #145 — class `TestReports`

```python
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
```

## #146 — class `TestReports`

```python
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
```
