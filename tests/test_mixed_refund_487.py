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
        sale = self.sale(cash=4000, card=2000, units=3)
        self.assertEqual(self.split(self.refund(sale)), [("card", 667), ("cash", 1333)])
        self.assertEqual(self.split(self.refund(sale)), [("card", 667), ("cash", 1333)])
        self.assertEqual(self.split(self.refund(sale)), [("card", 666), ("cash", 1334)])
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
        self.assertEqual([r["value"] for r in previous["rows"]], [2000])
        self.assertEqual({p["method"]: p["amount"] for p in report["refund_payments"]},
                         {"card": 500, "credit": 500, "cash": 1000})

    def test_electronic_exchange_cheaper_equal_dearer_keeps_original_account(self):
        from app.catalog import service
        from app.finance import money
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
                    before = money.summary(self.db, self.ctx, self.today, self.today)
                    before_statement = money.statement(self.db, self.ctx, account, self.today, self.today)
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
                    flow = money.summary(self.db, self.ctx, self.today, self.today)
                    self.assertEqual((flow["total_in"] - before["total_in"], flow["total_out"] - before["total_out"]),
                                     (max(0, price - 2000), max(0, 2000 - price)))
                    statement = money.statement(self.db, self.ctx, account, self.today, self.today)
                    self.assertEqual((statement["total_in"] - before_statement["total_in"],
                                      statement["total_out"] - before_statement["total_out"]),
                                     (0, max(0, 2000 - price)))
                    self.assertEqual(out["sale"]["payments"][0]["channel"], sale["payments"][0]["channel"])
                    self.assertEqual(self.split(self.refund(out["sale"]))[0], (method, min(price, 2000)))

    def test_rejected_fresh_wallet_top_up_preserves_inherited_wallet_account(self):
        sale = self.sale(cash=0, wallet=2000)
        self.verify_wallet(sale)
        new_wallet = self.call("POST", "/api/money/accounts", 201,
                               json={"name": "محفظة بديلة", "kind": "wallet"})["id"]
        self.call("PATCH", f"/api/payment-channels/{self.tr_ch}", json={"account_id": new_wallet})
        sid = self.draft()
        self.line(sid, self.pan, units=2)
        out = self.call("POST", f"/api/sales/{sale['id']}/exchange", json={
            "new_sale_id": sid, "lines": {sale["lines"][0]["id"]: 1}, "refund_method": "original",
            "reason": "استبدال تجربة", "pay_method": "transfer", "channel_id": self.tr_ch})
        self.assertEqual((self.bal(self.wallet), self.bal(new_wallet)), (2000, 2000))
        from io import BytesIO
        data = self.call("POST", f"/api/sales/{sid}/attachments",
                         files={"file": ("test.png", BytesIO(PNG), "image/png")})
        self.call("POST", f"/api/attachments/{data['attachments'][-1]['id']}/review",
                  json={"ok": False, "note": "الفرق لم يصل"})
        self.assertEqual((self.bal(self.wallet), self.bal(new_wallet)), (2000, 0))
        self.assertEqual(self.split(self.refund(out["sale"])), [("transfer", 2000)])
        self.assertEqual((self.bal(self.wallet), self.bal(new_wallet)), (0, 0))

    def test_rounding_and_kept_change_stay_with_cash_on_final_return(self):
        customer = self.call("POST", "/api/customers", 201, json={"name": "عميل تجربة", "credit_limit": 100000})["id"]
        from app.sales.rounding import set_step
        set_step(self.db, self.ctx, 100)
        sale = self.sale(cash=1000, card=3049, units=2, customer=customer, keep_change=True, cash_given=1030)
        self.assertEqual(self.split(self.refund(sale)), [("card", 2000)])
        final = self.refund(sale)
        self.assertEqual((self.split(final), final["change_back"]), ([("card", 1049), ("cash", 1000)], 30))
        rounding = self.db.all("SELECT refund_method, rounding FROM sale_returns WHERE rounding<>0")
        self.assertEqual([tuple(r) for r in rounding], [("cash", 49)])
        self.assertEqual(self.drawer(), 10000)

    def test_approved_cash_override_cannot_use_an_unverified_wallet(self):
        from dataclasses import replace
        from app.core.errors import Conflict
        from app.sales.service import Sales
        approved = replace(self.ctx, approver_id="manager-fixture",
                           approver_permissions=frozenset({"sales.refund_other_method"}))
        sale = self.sale(wallet=1000)
        with self.assertRaises(Conflict) as error:
            Sales(self.db).return_items(approved, sale["id"], {sale["lines"][0]["id"]: 1},
                                        refund_method="cash", reason="سبب تجربة")
        self.assertEqual(error.exception.details["max_cash"], 1000)
        self.assertEqual((self.drawer(), self.qty(self.pan)), (11000, 19))

    def test_card_funded_cash_exception_keeps_verified_wallet_available(self):
        sale = self.sale(cash=500, card=1500, wallet=2000, units=2)
        self.verify_wallet(sale)
        from dataclasses import replace
        from app.sales.service import Sales
        approved = replace(self.ctx, approver_id="manager-fixture",
                           approver_permissions=frozenset({"sales.refund_other_method"}))
        service = Sales(self.db)
        line = {sale["lines"][0]["id"]: 1}
        service.return_items(approved, sale["id"], line, refund_method="cash", reason="سبب تجربة")
        paid = service.return_items(self.ctx, sale["id"], line, refund_method="transfer", reason="سبب تجربة",
                                    channel_id=self.tr_ch)
        self.assertEqual(self.split(paid), [("transfer", 2000)])
        self.assertEqual((self.drawer(), self.bal(self.wallet)), (8500, 0))

    def legacy_card_refund(self, sale, wallet_account=None):
        from unittest.mock import patch
        from app.core import audit
        from app.finance import money
        original_post, original_audit = money._post, audit.record

        def post(db, ctx, account, amount, kind, **kwargs):
            if kind == "refund" and kwargs.get("ref_type") == "sale_return":
                kwargs["meta"] = {k: v for k, v in kwargs["meta"].items() if k != "channel_id"}
                account = wallet_account or account
            return original_post(db, ctx, account, amount, kind, **kwargs)

        def record(db, ctx, action, *args, **kwargs):
            if wallet_account and action == "sale.return":
                kwargs["after"] = {k: v for k, v in kwargs["after"].items() if k != "card_only"}
            return original_audit(db, ctx, action, *args, **kwargs)

        with patch.object(money, "_post", side_effect=post), patch.object(audit, "record", side_effect=record):
            first = self.refund(sale, method="card")
        with self.db.transaction():
            self.db.execute("UPDATE sale_returns SET channel_id=NULL WHERE id=?", (first["return_id"],))

    def test_legacy_null_card_refund_does_not_consume_both_channels_on_shared_account(self):
        from app.sales import channels
        alternate = next(c["id"] for c in channels.save(self.db, self.ctx, {
            "name": "بطاقة ثانية", "kind": "card", "account_id": self.paymob}) if c["name"] == "بطاقة ثانية")
        sid = self.draft()
        self.line(sid, self.pan, units=2)
        sale = self.complete(sid, [{"method": "card", "channel_id": c, "amount": 2000}
                                   for c in (self.card_ch, alternate)])
        self.legacy_card_refund(sale)
        final = self.refund(sale)
        self.assertEqual([(p["channel_id"], p["amount"]) for p in final["refund_payments"]], [(alternate, 2000)])
        self.assertEqual(self.bal(self.paymob), 0)

    def test_legacy_card_label_on_wallet_debit_leaves_card_available(self):
        sale = self.sale(cash=0, card=2000, wallet=2000, units=2)
        self.verify_wallet(sale)
        self.legacy_card_refund(sale, wallet_account=self.wallet)
        self.assertEqual(self.split(self.refund(sale)), [("card", 2000)])
        self.assertEqual((self.bal(self.paymob), self.bal(self.wallet)), (0, 0))

    def test_approved_cash_from_two_verified_wallets_consumes_the_amount_once(self):
        from app.sales import channels
        alternate = next(c["id"] for c in channels.listing(self.db, self.ctx)
                         if c["kind"] == "transfer" and c["id"] != self.tr_ch)
        sid = self.draft()
        self.line(sid, self.pan, units=2)
        sale = self.complete(sid, [{"method": "transfer", "channel_id": c, "amount": 2000}
                                   for c in (self.tr_ch, alternate)])
        self.verify_wallet(sale)
        from dataclasses import replace
        from app.sales.service import Sales
        approved = replace(self.ctx, approver_id="manager-fixture",
                           approver_permissions=frozenset({"sales.refund_other_method"}))
        Sales(self.db).return_items(approved, sid, {sale["lines"][0]["id"]: 1},
                                    refund_method="cash", reason="سبب تجربة")
        final = self.refund(sale)
        self.assertEqual([(p["channel_id"], p["amount"]) for p in final["refund_payments"]], [(alternate, 2000)])

    def test_explicit_account_refund_on_mixed_sale_is_capped_by_original_credit(self):
        customer = self.call("POST", "/api/customers", 201, json={"name": "عميل تجربة", "credit_limit": 100000})["id"]
        sale = self.sale(cash=1000, credit=1000, customer=customer)
        err = self.refund(sale, method="credit", status=409)["error"]
        self.assertEqual(err["details"]["max_credit"], 1000)
        self.assertEqual(self.call("GET", f"/api/customers/{customer}")["balance"], 1000)

    def test_cheaper_mixed_exchange_reports_only_external_cash_back(self):
        from app.finance import money
        sale = self.sale(cash=1000, card=1000)
        sid = self.draft()
        self.line(sid, self.pan, units=1)
        from app.sales.service import Sales
        Sales(self.db).update_line(self.ctx, sid, self.db.one("SELECT id FROM sale_lines WHERE sale_id=?", (sid,))[0],
                                   discount_amount=300)
        before = money.summary(self.db, self.ctx, self.today, self.today)
        out = self.call("POST", f"/api/sales/{sale['id']}/exchange", json={
            "new_sale_id": sid, "lines": {sale["lines"][0]["id"]: 1}, "refund_method": "original",
            "reason": "استبدال تجربة"})
        self.assertEqual(self.split({"refund_payments": out["refund_due"]}), [("cash", 300)])
        after = money.summary(self.db, self.ctx, self.today, self.today)
        self.assertEqual((after["total_in"] - before["total_in"], after["total_out"] - before["total_out"]), (0, 300))

    def test_legacy_wallet_source_without_channel_reserves_the_named_wallet(self):
        from unittest.mock import patch
        from app.finance import money
        original_post = money._post

        def post(db, ctx, account, amount, kind, **kwargs):
            if kind == "sale" and kwargs.get("meta", {}).get("method") == "transfer":
                kwargs["meta"] = {"method": "transfer"}
            return original_post(db, ctx, account, amount, kind, **kwargs)

        with patch.object(money, "_post", side_effect=post):
            sale = self.sale(cash=0, card=2000, wallet=2000, units=2)
        self.verify_wallet(sale)
        self.legacy_card_refund(sale, wallet_account=self.wallet)
        error = self.refund(sale, method="transfer", status=409)["error"]
        self.assertEqual(error["details"]["max_transfer"], 0)
        self.assertEqual(self.split(self.refund(sale)), [("card", 2000)])
        self.assertEqual((self.bal(self.paymob), self.bal(self.wallet)), (0, 0))

    def test_legacy_card_refund_consumes_the_card_channel_that_was_routed(self):
        from unittest.mock import patch
        from app.finance import money
        from app.sales import channels
        first_channel = next(c["id"] for c in channels.save(self.db, self.ctx, {
            "name": "بطاقة بلا حساب", "kind": "card"}) if c["name"] == "بطاقة بلا حساب")
        sid = self.draft()
        self.line(sid, self.pan, units=2)
        sale = self.complete(sid, [{"method": "card", "channel_id": c, "amount": 2000}
                                   for c in (first_channel, self.card_ch)])

        def historical_debit(db, ctx, sale_id, method, channel, amount, *, return_id, note, **kwargs):
            money.reverse_sale(db, ctx, sale_id, amount, ref_type="sale_return", note=note,
                               meta={"return_id": return_id, "method": "card"}, card_only=True)
            return [(self.paymob, amount)]

        with patch.object(money, "refund_tender", side_effect=historical_debit):
            first = self.refund(sale, method="card")
        with self.db.transaction():
            self.db.execute("UPDATE sale_returns SET channel_id=NULL WHERE id=?", (first["return_id"],))
        final = self.refund(sale)
        self.assertEqual([(p["channel_id"], p["amount"]) for p in final["refund_payments"]], [(first_channel, 2000)])
        self.assertEqual(self.bal(self.paymob), 0)

    def test_refund_order_is_an_owner_setting_used_on_partial_returns(self):
        path = "/api/settings/sales"
        default = self.call("GET", path)
        self.assertEqual(default.get("refund_order"), ["card", "transfer", "cash", "credit"])
        self.assertIs(default.get("refund_unreviewed_wallet"), False)
        self.assertIs(default.get("refund_cap_mixed_credit"), True)
        order = ["cash", "credit", "transfer", "card"]
        self.call("PUT", path, 422, json={"refund_order": ["cash"] * 4})
        self.call("PUT", path, 422, json={"refund_unreviewed_wallet": "false", "refund_order": order})
        self.assertEqual(self.call("GET", path)["refund_order"], default["refund_order"])
        self.call("PUT", path, json={"refund_order": order})
        self.assertEqual(self.call("GET", path)["refund_order"], order)
        sale = self.sale(cash=2000, card=2000, units=2)
        self.assertEqual(self.split(self.refund(sale)), [("cash", 2000)])
        self.assertEqual(self.split(self.refund(sale)), [("card", 2000)])

    def test_owner_can_allow_pending_wallet_but_not_rejected_or_unconfirmed_remainder(self):
        path = "/api/settings/sales"
        from io import BytesIO

        sale = self.sale(wallet=1000)
        self.call("POST", f"/api/sales/{sale['id']}/attachments",
                  files={"file": ("pending.png", BytesIO(PNG), "image/png")})
        self.refund(sale, status=409)
        self.call("PUT", path, json={"refund_unreviewed_wallet": True})
        self.assertEqual(self.split(self.refund(sale)), [("transfer", 1000), ("cash", 1000)])

        sale = self.sale(wallet=1000)
        self.verify_wallet(sale, amount=600)
        err = self.refund(sale, status=409)["error"]
        self.assertEqual(err["details"]["max_transfer"], 600)
        aid = self.db.one("SELECT id FROM sale_attachments WHERE sale_id=?", (sale["id"],))[0]
        self.call("POST", f"/api/attachments/{aid}/review", json={"ok": False, "note": "التحويل لم يصل"})
        err = self.refund(sale, status=409)["error"]
        self.assertEqual(err["details"]["max_transfer"], 0)

    def test_owner_can_allow_refund_to_store_credit_above_original_credit(self):
        customer = self.call("POST", "/api/customers", 201, json={"name": "عميل تجربة", "credit_limit": 4000})["id"]
        sale = self.sale(cash=1000, credit=1000, customer=customer)
        self.refund(sale, method="credit", status=409)
        self.call("PUT", "/api/settings/sales", json={"refund_cap_mixed_credit": False})
        self.assertEqual(self.split(self.refund(sale, method="credit")), [("credit", 2000)])
        self.assertEqual(self.call("GET", f"/api/customers/{customer}")["balance"], -1000)
        self.assertEqual(self.drawer(), 11000)

    def test_negative_rounding_is_recorded_without_a_zero_cash_line(self):
        from app.catalog import service
        from app.sales.rounding import set_step

        item = service.create_item(self.db, self.ctx, {"name_en": "Rounding fixture", "pack_size": 1, "public_price": 1000})
        self.stock(item, 6, "5")
        set_step(self.db, self.ctx, 100)
        sale = self.sale(cash=1000, card=1951, units=3, item=item)
        self.assertEqual(sale["rounding"], -49)
        self.refund(sale, method="cash")
        self.refund(sale)
        final = self.refund(sale)
        self.assertEqual(self.split(final), [("card", 951)])
        shift = CashDrawer(self.db).report(self.ctx, sale["shift_id"])
        self.assertEqual(shift["returns_by_method"]["cash"]["count"], 1)
        self.assertEqual(self.db.one("SELECT rounding FROM sale_returns WHERE id=?", (final["return_id"],))[0], -49)

    def test_carried_credit_can_lower_debt_after_credit_limit_is_reduced(self):
        from app.catalog import service

        customer = self.call("POST", "/api/customers", 201, json={"name": "عميل تجربة", "credit_limit": 3000})["id"]
        sale = self.sale(cash=1000, credit=1000, customer=customer)
        self.call("PATCH", f"/api/customers/{customer}", json={"credit_limit": 100})
        for price, status in ((2100, 409), (1500, 200)):
            replacement = service.create_item(self.db, self.ctx, {
                "name_en": f"Credit replacement {price}", "pack_size": 1, "public_price": price})
            self.stock(replacement, 6, "5")
            sid = self.draft(customer)
            self.line(sid, replacement, units=1)
            out = self.call("POST", f"/api/sales/{sale['id']}/exchange", status, json={
                "new_sale_id": sid, "lines": {sale["lines"][0]["id"]: 1}, "refund_method": "original",
                "pay_method": "credit", "reason": "استبدال تجربة"})
            if status == 409:
                self.assertEqual(out["error"]["code"], "credit_limit")
                self.assertEqual(self.call("GET", f"/api/customers/{customer}")["balance"], 1000)
            else:
                self.assertEqual(out["sale"]["on_credit"], 500)
                self.assertEqual(self.call("GET", f"/api/customers/{customer}")["balance"], 500)

    def test_exchange_does_not_turn_unreviewed_wallet_money_into_verified_money(self):
        self.call("PUT", "/api/settings/sales", json={"refund_unreviewed_wallet": True})
        sale = self.sale(cash=0, wallet=2000)
        sid = self.draft()
        self.line(sid, self.pan, units=1)
        out = self.call("POST", f"/api/sales/{sale['id']}/exchange", json={
            "new_sale_id": sid, "lines": {sale["lines"][0]["id"]: 1}, "refund_method": "original",
            "reason": "استبدال تجربة"})
        replacement = out["sale"]
        self.assertEqual(replacement["verified_wallet_offset"], 0)
        self.call("PUT", "/api/settings/sales", json={"refund_unreviewed_wallet": False})
        err = self.refund(replacement, status=409)["error"]
        self.assertEqual(err["details"]["max_transfer"], 0)

    def test_split_rows_follow_the_acting_drawer_during_a_cross_register_return(self):
        from dataclasses import replace
        from app.sales.service import Sales

        sale = self.sale(cash=1000, card=1000)
        with self.db.transaction():
            self.db.execute("INSERT INTO registers (org_id,branch_id,id,name) VALUES (?,?,2,'Secondary')",
                            (self.ctx.org_id, self.ctx.branch_id))
        sub = replace(self.ctx, register_id=2)
        secondary = CashDrawer(self.db).open(sub, 10000)
        event = Sales(self.db).return_items(sub, sale["id"], {sale["lines"][0]["id"]: 1},
                                            refund_method="original", reason="مرتجع تجربة")
        rows = self.db.all("SELECT id,part_of,acting_shift_id,register_recorded FROM sale_returns WHERE sale_id=?",
                           (sale["id"],))
        self.assertEqual({(p["acting_shift_id"], p["register_recorded"]) for p in rows}, {(secondary, 1)})
        self.assertEqual({p["part_of"] or p["id"] for p in rows}, {event["return_id"]})
        drawer = CashDrawer(self.db)
        self.assertEqual(drawer.report(self.ctx, sale["shift_id"])["returns_count"], 0)
        report = drawer.report(sub, secondary)
        self.assertEqual((report["returns_count"], report["returns_total"], report["expected_cash"]), (1, 2000, 9000))
