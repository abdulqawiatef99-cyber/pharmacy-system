"""Mixed-tender returns and exchanges through the cashier API, using synthetic payments."""
from app.reports import hikma_claim, owner
from app.sales.cash import CashDrawer
from tests.test_attachments import PNG
from tests.test_money_wiring import WiringCase


class TestMixedRefund(WiringCase):
    def sale(self, cash=1000, card=0, wallet=0, credit=0, *, item=None, units=1, customer=None, **extra):
        sid = self.draft(customer)
        self.line(sid, item or self.pan, units=units)
        return self.complete(sid, [
            {"method": method, "amount": amount, "channel_id": channel}
            for method, amount, channel in (
                ("cash", cash, None), ("card", card, self.card_ch),
                ("transfer", wallet, self.tr_ch), ("credit", credit, None))
            if amount
        ], **extra)

    def refund(self, sale, qty=1, method="original", status=200, key=None):
        return self.call("POST", f"/api/sales/{sale['id']}/return", status,
                         headers={"Idempotency-Key": key} if key else {},
                         json={"lines": {sale["lines"][0]["id"]: qty}, "refund_method": method,
                               "reason": "مرتجع تجربة"})

    def verify_wallet(self, sale, **body):
        from io import BytesIO
        data = self.call("POST", f"/api/sales/{sale['id']}/attachments",
                         files={"file": ("test.png", BytesIO(PNG), "image/png")})
        self.call("POST", f"/api/attachments/{data['attachments'][-1]['id']}/review",
                  json={"ok": True, **body})

    def split(self, refund):
        return [(p["method"], p["amount"]) for p in refund["refund_payments"]]

    def test_indivisible_guest_sale_and_retry(self):
        from app.catalog import service
        item = service.create_item(self.db, self.ctx, {"name_en": "Synthetic 31", "pack_size": 1, "public_price": 3100})
        self.stock(item, 5, "10")
        sale = self.sale(card=2100, item=item)
        first = self.refund(sale, key="mixed-return")
        self.assertEqual(self.refund(sale, key="mixed-return"), first)
        self.assertEqual(self.split(first), [("card", 2100), ("cash", 1000)])
        self.assertEqual((self.qty(item), self.drawer(), self.bal(self.paymob)), (5, 10000, 0))
        for table, where in (
            ("stock_movements", "movement_type='sale_return'"),
            ("cash_movements", "kind='refund'"),
            ("money_movements", "kind='refund'"),
            ("audit_log", "action='sale.return'"),
        ):
            self.assertEqual(self.db.one(f"SELECT count(*) FROM {table} WHERE {where}")[0], 1)
        self.assertEqual(self.refund(sale, status=409)["error"]["code"], "return_exceeds")

    def test_partial_original_order_after_prior_cash_and_credit(self):
        customer = self.call("POST", "/api/customers", 201, json={"name": "عميل تجربة", "credit_limit": 100000})["id"]
        sale = self.sale(cash=2100, card=1901, credit=1999, units=3, customer=customer)
        self.refund(sale, method="cash")
        self.assertEqual(self.split(self.refund(sale)), [("card", 1901), ("cash", 99)])
        self.assertEqual(self.split(self.refund(sale)), [("cash", 1), ("credit", 1999)])
        self.assertEqual(self.call("GET", f"/api/customers/{customer}")["balance"], 0)
        self.assertEqual((self.drawer(), self.bal(self.paymob)), (10000, 0))

    def test_proportional_setting_and_piastre_conservation(self):
        self.assertEqual(self.call("GET", "/api/settings/sales").get("refund_allocation"), "original_order")
        self.call("PUT", "/api/settings/sales", json={"refund_allocation": "proportional"})
        self.call("PUT", "/api/settings/sales", 422, json={"refund_allocation": "invalid"})
        sale = self.sale(cash=3999, card=2001, units=3)
        self.assertEqual(self.split(self.refund(sale)), [("card", 667), ("cash", 1333)])
        self.assertEqual(self.split(self.refund(sale)), [("card", 667), ("cash", 1333)])
        self.assertEqual(self.split(self.refund(sale)), [("card", 667), ("cash", 1333)])
        self.assertEqual((self.drawer(), self.bal(self.paymob)), (10000, 0))

    def test_wallet_verified_cap_and_rollback(self):
        sale = self.sale(wallet=1000)
        error = self.refund(sale, status=409)["error"]
        self.assertEqual(error["code"], "refund_original_limit")
        self.assertEqual(self.qty(self.pan), 19)
        self.verify_wallet(sale, amount=600)
        self.assertEqual(self.refund(sale, status=409)["error"]["details"]["max_transfer"], 600)
        self.assertEqual(self.db.one("SELECT count(*) FROM sale_returns")[0], 0)
        self.call("POST", f"/api/attachments/{self.db.one('SELECT id FROM sale_attachments')[0]}/review",
                  json={"ok": True, "amount": 1000})
        self.assertEqual(self.split(self.refund(sale)), [("transfer", 1000), ("cash", 1000)])
        self.assertEqual((self.drawer(), self.bal(self.wallet)), (10000, 0))

    def test_cash_override_explicit_only_and_consumes_original_capacity(self):
        sale = self.sale(cash=1000, card=3000, units=2)
        self.assertEqual(self.refund(sale, method="cash", status=403)["error"]["code"], "approval_required")
        from dataclasses import replace
        from app.sales.service import Sales
        approved = replace(self.ctx, approver_id="manager-fixture",
                           approver_permissions=frozenset({"sales.refund_other_method"}))
        Sales(self.db).return_items(approved, sale["id"], {sale["lines"][0]["id"]: 1},
                                    refund_method="cash", reason="سبب تجربة")
        self.assertEqual(self.split(self.refund(sale)), [("card", 2000)])
        self.assertEqual(self.drawer(), 9000)

    def test_shift_receipt_and_hikma_show_one_event_with_every_tender(self):
        customer = self.call("POST", "/api/customers", 201, json={"name": "عميل تجربة", "credit_limit": 100000})["id"]
        hikma_claim.save_settings(self.db, self.ctx, "shibin", customer, 0)
        sale = self.sale(card=500, credit=500, customer=customer)
        before = hikma_claim.claim(self.db, self.ctx, "shibin", self.today, self.today)
        self.assertEqual(before["pharmacy"]["value"], 2000)
        result = self.refund(sale)
        report = CashDrawer(self.db).report(self.ctx, sale["shift_id"])
        self.assertEqual((report["returns_count"], report["returns_total"]), (1, 2000))
        self.assertEqual({m: p["total"] for m, p in report["returns_by_method"].items()},
                         {"card": 500, "credit": 500, "cash": 1000, "transfer": 0})
        activity = owner.returns_report(self.db, self.ctx, self.today, self.today)
        self.assertEqual(activity["totals"]["n"], 1)
        self.assertEqual(activity["totals"]["by_method"], {"card": 500, "credit": 500, "cash": 1000})
        receipt = self.call("GET", f"/return-receipt/{result['return_id']}").text
        for fragment in ("20.00", "5.00", "10.00", "بالبطاقة", "على حساب العميل", "نقدًا من الدرج"):
            self.assertIn(fragment, receipt)
        self.assertEqual(hikma_claim.claim(self.db, self.ctx, "shibin", self.today, self.today)["pharmacy"]["value"], 0)
        with self.db.transaction():
            self.db.execute("UPDATE sales SET completed_at='2026-01-05T10:00:00.000000Z' WHERE id=?", (sale["id"],))
        previous = hikma_claim.claim(self.db, self.ctx, "shibin", self.today, self.today)["previous_returns"]
        self.assertEqual(previous["value"], 2000)

    def test_electronic_exchange_cheaper_equal_dearer_keeps_original_account(self):
        from app.catalog import service
        for method, account, channel in (("card", self.paymob, self.card_ch), ("transfer", self.wallet, self.tr_ch)):
            for price in (1700, 2000, 2400):
                with self.subTest(method=method, price=price):
                    sale = self.sale(cash=0, **{("card" if method == "card" else "wallet"): 2000})
                    if method == "transfer":
                        self.verify_wallet(sale)
                    item = service.create_item(self.db, self.ctx, {
                        "name_en": f"Replacement {method} {price}", "pack_size": 1, "public_price": price})
                    self.stock(item, 5, "10")
                    sid = self.draft()
                    draft = self.line(sid, item, units=1)
                    original_balance, drawer = self.bal(account), self.drawer()
                    path = f"/api/sales/{sale['id']}/exchange"
                    payload = {"new_sale_id": sid, "lines": {sale["lines"][0]["id"]: 1}, "refund_method": "original",
                               "reason": "استبدال تجربة", "version": draft["version"], "request_id": sid}
                    preview = self.call("POST", path, json={**payload, "preview": True})
                    self.assertEqual(preview["net"], price - 2000)
                    self.assertEqual(self.bal(account), original_balance)
                    out = self.call("POST", path, json=payload)
                    self.assertEqual(out["net"], price - 2000)
                    self.assertEqual(self.call("POST", path, json=payload)["return_id"], out["return_id"])
                    self.assertEqual(self.bal(account) - original_balance, min(0, price - 2000))
                    self.assertEqual(self.drawer() - drawer, max(0, price - 2000))
                    self.assertEqual(out["sale"]["payments"][0]["channel"], sale["payments"][0]["channel"])
                    self.assertEqual(self.split(self.refund(out["sale"]))[0], (method, min(price, 2000)))
