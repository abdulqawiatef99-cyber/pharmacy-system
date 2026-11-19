"""F105/F106/F108 phase 2: the money accounts wired into the program, through the API. Synthetic data only."""
from app.catalog import service as cat
from app.core import people
from app.finance import money
from app.finance import expenses as fin
from tests.test_owner_plan_priority_1_2 import PlanCase

PW = "Staff-Pass-2026"


class WiringCase(PlanCase):
    def H(self):
        from tests.test_api import H
        return H

    def setUp(self):
        super().setUp()
        fin.ensure_defaults(self.db)
        self.t = money.treasury_id(self.db, self.ctx)
        self.wallet = self.call("POST", "/api/money/accounts", 201, json={"name": "فودافون كاش", "kind": "wallet"})["id"]
        self.paymob = self.call("POST", "/api/money/accounts", 201, json={"name": "باي موب", "kind": "other"})["id"]
        self.card_ch, self.tr_ch = self.channel("card"), self.channel("transfer")
        self.call("PATCH", f"/api/payment-channels/{self.card_ch}", json={"account_id": self.paymob})
        self.call("PATCH", f"/api/payment-channels/{self.tr_ch}", json={"account_id": self.wallet})
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})

    def bal(self, aid):
        return money.balance(self.db, self.ctx.org_id, aid)


class TestIncome(WiringCase):
    def test_card_and_wallet_sales_raise_their_own_accounts_and_a_void_takes_it_back(self):
        a = self.sell(self.pan, payments=[{"method": "card", "amount": 4000, "channel_id": self.card_ch}], units=2)
        self.sell(self.pan, payments=[{"method": "transfer", "amount": 2000, "channel_id": self.tr_ch}], units=1)
        self.assertEqual((self.bal(self.paymob), self.bal(self.wallet)), (4000, 2000))
        sid = a["id"] if "id" in a else a["sale"]["id"]
        self.call("POST", f"/api/sales/{sid}/void", json={"reason": "خطأ في الفاتورة"})
        self.assertEqual(self.bal(self.paymob), 0)

    def test_card_refund_goes_back_through_the_same_account(self):
        a = self.sell(self.pan, payments=[{"method": "card", "amount": 4000, "channel_id": self.card_ch}], units=2)
        d = a if "lines" in a else a["sale"]
        self.call("POST", f"/api/sales/{d['id']}/return", json={"lines": {d["lines"][0]["id"]: 1},
                                                                "refund_method": "card", "reason": "العميل رجّعه"})
        self.assertEqual(self.bal(self.paymob), 2000)

    def test_a_wallet_collection_from_a_customer_raises_that_wallet(self):
        cust = self.call("POST", "/api/customers", 201, json={"name": "عميل تجربة", "credit_limit": 100000})["id"]
        self.sell(self.pan, payments=[{"method": "credit", "amount": 4000}], customer=cust, units=2)
        r = self.call("POST", f"/api/customers/{cust}/payments",
                      json={"amount": 1500, "method": "transfer", "channel_id": self.tr_ch})
        self.assertEqual((r["balance"], self.bal(self.wallet)), (2500, 1500))
        self.assertEqual(self.drawer(), 10000)                 # nothing entered the drawer

    def test_unrouted_card_money_is_not_lost_silently_it_just_lands_nowhere_yet(self):
        self.call("PATCH", f"/api/payment-channels/{self.card_ch}", json={"account_id": None})
        self.sell(self.pan, payments=[{"method": "card", "amount": 4000, "channel_id": self.card_ch}], units=2)
        self.assertEqual(self.bal(self.paymob), 0)


class TestPayouts(WiringCase):
    def setUp(self):
        super().setUp()
        self.call("POST", "/api/money/owner", 201, json={"account": "treasury", "amount": 50000, "kind": "owner_deposit"})
        self.cat = fin.categories(self.db, self.ctx)[0]["id"]

    def test_expense_from_an_account_lowers_it_and_never_below_zero(self):
        e = self.call("POST", "/api/expenses", 201, json={"category_id": self.cat, "amount": 20000, "paid_from": self.t})
        self.assertEqual((e["account_id"], self.bal(self.t)), (self.t, 30000))
        r = self.c.post("/api/expenses", json={"category_id": self.cat, "amount": 30001, "paid_from": self.t}, headers=self.H())
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "account_negative"))
        self.assertEqual(self.bal(self.t), 30000)

    def test_outside_by_an_employee_waits_for_the_owner(self):
        people.add_user(self.db, self.ctx, username="mgr", full_name="M", password=PW, role_code="manager", pin="2468")
        m = self.login("mgr", PW)
        e = fin.record(self.db, m, category_id=self.cat, amount=100, paid_from="outside")
        self.assertEqual(e["status"], "pending")
        from app.core.errors import Forbidden
        with self.assertRaises(Forbidden):
            fin.decide(self.db, m, e["id"], True)
        self.assertEqual(fin.decide(self.db, self.ctx, e["id"], True)["status"], "approved")

    def test_supplier_payment_from_an_account_with_an_early_payment_discount(self):
        sup = cat.create_supplier(self.db, self.ctx, name="مورد خصم")
        with self.db.transaction():
            self.db.execute("INSERT INTO supplier_ledger (id, org_id, supplier_id, amount, kind) VALUES ('op', ?, ?, 40000, 'opening')",
                            (self.ctx.org_id, sup))
        r = self.call("POST", f"/api/suppliers/{sup}/pay", json={"source": self.t, "settle": 20000, "discount_bp": 200})
        self.assertEqual((r["paid"], r["discount"], r["balance"]), (19600, 400, 20000))
        self.assertEqual(self.bal(self.t), 50000 - 19600)
        kinds = sorted((x["kind"], x["amount"]) for x in r["ledger"])
        self.assertEqual(kinds, [("discount", -400), ("opening", 40000), ("payment", -19600)])
        p = self.call("GET", f"/api/reports/profit?from={self.today}&to={self.today}")
        self.assertEqual(p["supplier_discounts"], 400)
        r2 = self.c.post(f"/api/suppliers/{sup}/pay", json={"source": self.t, "amount": 40000}, headers=self.H())
        self.assertEqual(r2.status_code, 409)                  # more than the treasury holds
        self.assertEqual(self.call("GET", f"/api/suppliers/{sup}")["balance"], 20000)

    def test_supplier_payment_from_the_drawer_never_below_zero(self):
        sup = cat.create_supplier(self.db, self.ctx, name="مورد درج")
        r = self.c.post(f"/api/suppliers/{sup}/pay", json={"source": "drawer", "amount": 10001}, headers=self.H())
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "drawer_negative"))
        self.call("POST", f"/api/suppliers/{sup}/pay", json={"source": "drawer", "amount": 10000})
        self.assertEqual(self.drawer(), 0)

    def test_salary_advance_from_an_account(self):
        uid = people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        from app.finance import payroll
        payroll.add_advance(self.db, self.ctx, uid, 5000, self.today[:7], "سلفة", self.t)
        self.assertEqual(self.bal(self.t), 45000)

    def test_scheduled_rent_from_its_account(self):
        from app.finance import schedules
        s = schedules.save(self.db, self.ctx, {"name": "إيجار الصيدلية", "category_id": self.cat, "amount": 15000,
                                               "day_of_month": 1, "paid_from": self.t})
        self.assertEqual((s["paid_from"], s["account_id"]), ("outside", self.t))
        schedules.pay(self.db, self.ctx, s["id"], period=s["start_month"])
        self.assertEqual(self.bal(self.t), 35000)


class TestScreensApi(WiringCase):
    def test_accounts_transfer_count_statement_summary_routes(self):
        self.call("POST", "/api/money/owner", 201, json={"account": "treasury", "amount": 50000, "kind": "owner_deposit"})
        self.call("POST", "/api/money/transfer", 201, json={"from": "treasury", "to": self.wallet, "amount": 10000})
        c = self.call("POST", f"/api/money/accounts/{self.wallet}/count", 201, json={"counted": 9900, "reason": "رسوم تحويل"})
        self.assertEqual(c["difference"], -100)
        accs = {a["id"]: a["balance"] for a in self.call("GET", "/api/money/accounts")["accounts"]}
        self.assertEqual((accs[self.t], accs[self.wallet]), (40000, 9900))
        st = self.call("GET", f"/api/money/accounts/{self.wallet}/statement?from={self.today}&to={self.today}")
        self.assertEqual(([x["balance"] for x in st["lines"]], st["closing"]), ([10000, 9900], 9900))
        sm = self.call("GET", f"/api/money/summary?from={self.today}&to={self.today}")
        self.assertEqual(len(sm["periods"]), 1)
        routes = self.call("GET", "/api/money/routes")["routes"]
        self.assertEqual({r["channel_id"]: r["account_id"] for r in routes}, {self.card_ch: self.paymob, self.tr_ch: self.wallet})

    def test_reconcile_page_through_the_api(self):
        sup = cat.create_supplier(self.db, self.ctx, name="مورد مطابقة")
        rows = self.call("GET", "/api/reconcile/parties?party=supplier&q=مطابقة")["parties"]
        self.assertEqual([(r["id"], r["balance"]) for r in rows], [(sup, 0)])
        chk = self.call("POST", "/api/reconcile", json={"check": True, "party": "supplier", "party_id": sup,
                                                        "statement_balance": 12345})
        self.assertEqual(chk["difference"], 12345)
        self.call("POST", "/api/reconcile", 201, json={"party": "supplier", "party_id": sup, "statement_balance": 12345,
                                                       "program_balance": 0, "adj_type": "opening"})
        again = self.c.post("/api/reconcile", json={"party": "supplier", "party_id": sup, "statement_balance": 12345,
                                                    "program_balance": 0, "adj_type": "opening"}, headers=self.H())
        self.assertEqual(again.status_code, 409)               # a retry with the old screen balance never posts twice
        self.assertEqual(len(self.call("GET", f"/api/reconcile/supplier/{sup}")["history"]), 1)

    def test_shift_close_through_the_api_moves_cash_to_the_chosen_account(self):
        self.sell(self.pan, units=2)                           # 40.00 cash
        self.call("POST", "/api/cash/close", json={"counted_cash": 14000, "float_left": 5000, "to_account": "treasury"})
        self.assertEqual(self.bal(self.t), 9000)



class TestCodexReview348(WiringCase):
    """#348 Codex: P1 a void / card refund reverses the account the sale really went to, whatever the route is
    now; P2 a schedule switched to 'outside' forgets its old account."""

    def _card_sale(self):
        a = self.sell(self.pan, payments=[{"method": "card", "amount": 4000, "channel_id": self.card_ch}], units=2)
        return a if "lines" in a else a["sale"]

    def test_void_after_a_reroute_takes_it_back_from_the_original_account(self):
        d = self._card_sale()
        self.call("PATCH", f"/api/payment-channels/{self.card_ch}", json={"account_id": self.wallet})
        self.call("POST", f"/api/sales/{d['id']}/void", json={"reason": "خطأ في الفاتورة"})
        self.assertEqual((self.bal(self.paymob), self.bal(self.wallet)), (0, 0))

    def test_void_after_a_regroup_still_takes_it_back(self):
        d = self._card_sale()
        self.call("PATCH", f"/api/payment-channels/{self.card_ch}", json={"kind": "transfer"})
        self.call("POST", f"/api/sales/{d['id']}/void", json={"reason": "خطأ في الفاتورة"})
        self.assertEqual(self.bal(self.paymob), 0)

    def test_card_refund_after_a_reroute_comes_from_the_original_account(self):
        d = self._card_sale()
        self.call("PATCH", f"/api/payment-channels/{self.card_ch}", json={"account_id": self.wallet})
        self.call("POST", f"/api/sales/{d['id']}/return", json={"lines": {d["lines"][0]["id"]: 1},
                                                               "refund_method": "card", "reason": "العميل رجّعه"})
        self.assertEqual((self.bal(self.paymob), self.bal(self.wallet)), (2000, 0))

    def test_a_schedule_switched_to_outside_forgets_its_account(self):
        from app.finance import schedules
        cat_id = fin.categories(self.db, self.ctx)[0]["id"]
        s = schedules.save(self.db, self.ctx, {"name": "إيجار", "category_id": cat_id, "amount": 100, "day_of_month": 1,
                                               "paid_from": self.t})
        s = schedules.save(self.db, self.ctx, {"paid_from": "outside"}, s["id"])
        self.assertEqual((s["paid_from"], s["account_id"]), ("outside", None))


class TestCodexReview348b(PlanCase):
    """#348 Codex, second pass: a schedule saved with the treasury placeholder before the treasury exists; the
    expense listing names the account an expense was paid from."""

    def setUp(self):
        super().setUp()
        fin.ensure_defaults(self.db)
        self.cat = fin.categories(self.db, self.ctx)[0]["id"]

    def test_schedule_with_the_placeholder_treasury_on_a_fresh_pharmacy(self):
        from app.finance import schedules
        self.assertIsNone(money.find_treasury(self.db, self.ctx.org_id))
        s = schedules.save(self.db, self.ctx, {"name": "إيجار", "category_id": self.cat, "amount": 100,
                                               "day_of_month": 1, "paid_from": "treasury"})
        self.assertEqual(s["account_id"], money.find_treasury(self.db, self.ctx.org_id))

    def test_the_expense_list_names_the_account(self):
        t = money.treasury_id(self.db, self.ctx)
        money.owner_movement(self.db, self.ctx, t, 5000, "owner_deposit")
        fin.record(self.db, self.ctx, category_id=self.cat, amount=100, paid_from=t)
        rows = fin.listing(self.db, self.ctx)
        self.assertEqual(rows[0]["account"], money.TREASURY_NAME)


class TestCodexReview348c(WiringCase):
    """#348 Codex, third pass (P1): a split cash + card sale refunded in full to the card posts the whole refund
    to the card's account, not only the card part."""

    def test_full_card_refund_of_a_split_sale(self):
        a = self.sell(self.pan, payments=[{"method": "cash", "amount": 2000},
                                          {"method": "card", "amount": 2000, "channel_id": self.card_ch}], units=2)
        d = a if "lines" in a else a["sale"]
        self.call("POST", f"/api/sales/{d['id']}/return", json={"lines": {d["lines"][0]["id"]: 2},
                                                               "refund_method": "card", "reason": "العميل رجّعه"})
        self.assertEqual(self.bal(self.paymob), 2000 - 4000)


class TestLaunchGateCodex381(WiringCase):
    """Installer gate, Codex on the candidate (#381), two P1s in the money wiring:
    1. a card refund of a sale that put nothing on an account (a cash sale, or a card sale from before 0038 or
       from before its channel was routed) still takes the money from the card's account;
    2. a routed transfer approved for only part of it leaves on its account only the part that came, with one
       audited correction movement that later reviews move up or down, never twice."""

    def sale_id(self, a):
        return (a if "lines" in a else a["sale"])["id"], (a if "lines" in a else a["sale"])["lines"][0]["id"]

    def test_a_cash_sale_refunded_to_the_card_debits_the_card_account(self):
        sid, line = self.sale_id(self.sell(self.pan, units=2))                      # 40.00 cash
        self.call("POST", f"/api/sales/{sid}/return", json={"lines": {line: 1}, "refund_method": "card",
                                                            "reason": "العميل رجّعه"})
        self.assertEqual(self.bal(self.paymob), -2000)                               # the card machine paid it

    def test_a_card_sale_made_before_its_channel_was_routed(self):
        self.call("PATCH", f"/api/payment-channels/{self.card_ch}", json={"account_id": None})
        sid, line = self.sale_id(self.sell(self.pan, payments=[{"method": "card", "amount": 4000,
                                                                "channel_id": self.card_ch}], units=2))
        self.call("PATCH", f"/api/payment-channels/{self.card_ch}", json={"account_id": self.paymob})
        self.call("POST", f"/api/sales/{sid}/return", json={"lines": {line: 1}, "refund_method": "card",
                                                            "reason": "العميل رجّعه"})
        self.assertEqual(self.bal(self.paymob), -2000)

    def proof(self, sid, data):
        import io
        r = self.c.post(f"/api/sales/{sid}/attachments", files={"file": ("t.png", io.BytesIO(data), "image/png")},
                        headers=self.H())
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["attachments"][-1]["id"]

    def review(self, aid, **body):
        return self.call("POST", f"/api/attachments/{aid}/review", json={"ok": True, **body})

    def test_a_part_approved_transfer_leaves_only_that_part_on_the_account(self):
        from tests.test_attachments import PNG
        sid, _ = self.sale_id(self.sell(self.pan, payments=[{"method": "transfer", "amount": 4000,
                                                             "channel_id": self.tr_ch}], units=2))
        self.assertEqual(self.bal(self.wallet), 4000)
        a = self.proof(sid, PNG)
        self.review(a, amount=1000)
        self.assertEqual(self.bal(self.wallet), 1000)
        self.review(a, amount=1000)                                                  # the same review again
        self.assertEqual(self.bal(self.wallet), 1000)
        b = self.proof(sid, PNG + b"2")
        self.review(b, amount=3000)                                                  # the rest came after all
        self.assertEqual(self.bal(self.wallet), 4000)
        rows = self.db.all("SELECT amount FROM money_movements WHERE org_id=? AND ref_id=? AND ref_type='transfer_review'",
                           (self.ctx.org_id, sid))
        self.assertEqual(sorted(r["amount"] for r in rows), [-3000, 3000])
        self.assertTrue(self.db.one("SELECT 1 FROM audit_log WHERE action='money.transfer_review' AND entity_id=?",
                                    (sid,)))

    def test_a_transfer_marked_not_received_leaves_nothing_and_a_void_after_it_does_not_double(self):
        from tests.test_attachments import PNG
        sid, _ = self.sale_id(self.sell(self.pan, payments=[{"method": "transfer", "amount": 4000,
                                                             "channel_id": self.tr_ch}], units=2))
        a = self.proof(sid, PNG)
        self.call("POST", f"/api/attachments/{a}/review", json={"ok": False, "note": "لم يصل"})
        self.assertEqual(self.bal(self.wallet), 0)
        self.call("POST", f"/api/sales/{sid}/void", json={"reason": "خطأ في الفاتورة"})
        self.assertEqual(self.bal(self.wallet), 0)                                   # never below what came
