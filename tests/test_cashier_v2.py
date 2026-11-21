"""Cashier v2: open invoice tabs, split payment with machine/wallet, delivery invoices, line edits."""
from app.core import people
from app.core.errors import Conflict, Forbidden, ValidationFailed
from app.sales import channels
from tests.test_api import APICase, H
from tests.test_cashier_acceptance import Base

PW = "Staff-Pass-2026"


class V2(Base):
    def setUp(self):
        super().setUp()
        channels.ensure_defaults(self.db)
        ch = {c["name"]: c["id"] for c in channels.listing(self.db, self.ctx)}
        self.visa, self.insta = ch["جهاز البطاقات"], ch["إنستاباي"]
        self.cd.open(self.ctx, 10000)

    def draft(self, ctx=None, units=2):
        ctx = ctx or self.ctx
        sid = self.s.create(ctx)
        return self.s.add_line(ctx, sid, self.pan, units=units)       # 2 units = 40.00

    def done(self, d, payments, ctx=None):
        ctx = ctx or self.ctx
        f = self.s.safety_check(ctx, d["id"])
        return self.s.complete(ctx, d["id"], payments=payments, safety_decisions={i: "ok" for i in range(len(f))})


class TestPayments(V2):
    def test_split_cash_card_transfer(self):
        d = self.draft()
        r = self.done(d, [{"method": "cash", "amount": 1000}, {"method": "card", "amount": 2000, "channel_id": self.visa},
                          {"method": "transfer", "amount": 1000, "channel_id": self.insta}])
        self.assertEqual((r["paid_cash"], r["paid_card"], r["paid_transfer"]), (1000, 2000, 1000))
        self.assertEqual([p["channel"] for p in r["payments"]], [None, "جهاز البطاقات", "إنستاباي"])
        sm = self.cd.summary(self.ctx)
        self.assertEqual(sm["expected_cash"], 11000)                     # only cash in the drawer
        self.assertEqual({(b["method"], b["channel"]): b["amount"] for b in sm["by_channel"]},
                         {("card", "جهاز البطاقات"): 2000, ("transfer", "إنستاباي"): 1000})

    def test_machine_or_wallet_required_and_totals_must_match(self):
        d = self.draft()
        with self.assertRaises(ValidationFailed) as e:
            self.done(d, [{"method": "card", "amount": 4000}])
        self.assertEqual(e.exception.code, "channel_required")
        with self.assertRaises(ValidationFailed):
            self.done(d, [{"method": "transfer", "amount": 4000, "channel_id": self.visa}])    # a machine is not a wallet
        with self.assertRaises(ValidationFailed):
            self.done(d, [{"method": "cash", "amount": 3000}])

    def test_old_three_amounts_still_work(self):
        d = self.draft()
        f = self.s.safety_check(self.ctx, d["id"])
        r = self.s.complete(self.ctx, d["id"], paid_cash=4000, safety_decisions={i: "ok" for i in range(len(f))})
        self.assertEqual(r["payments"], [{"method": "cash", "amount": 4000, "channel": None}])


class TestTabsAndLines(V2):
    def test_tabs_are_per_person_and_close(self):
        people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        a = self.login("asst", PW)
        mine1, mine2, theirs = self.draft()["id"], self.draft()["id"], self.draft(a)["id"]
        self.assertEqual([d["id"] for d in self.s.list_drafts(self.ctx)], [mine1, mine2])
        with self.assertRaises(Forbidden):
            self.s.discard(self.ctx, theirs)
        self.s.discard(self.ctx, mine1)
        self.assertEqual([d["id"] for d in self.s.list_drafts(self.ctx)], [mine2])

    def test_edit_quantity_and_discount_on_a_line(self):
        d = self.draft()
        line = d["lines"][0]["id"]
        d = self.s.update_line(self.ctx, d["id"], line, qty_base=4)
        self.assertEqual(d["total"], 8000)
        with self.assertRaises(Conflict):
            self.s.update_line(self.ctx, d["id"], line, qty_base=99)
        d = self.s.update_line(self.ctx, d["id"], line, discount_bp=1000)
        self.assertEqual(d["total"], 7200)
        people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        a = self.login("asst", PW)
        d2 = self.draft(a)
        with self.assertRaises(Forbidden):
            self.s.update_line(a, d2["id"], d2["lines"][0]["id"], discount_bp=500)


class TestDelivery(V2):
    def test_cash_stays_with_courier_until_settled(self):
        d = self.draft()
        with self.assertRaises(ValidationFailed):
            self.s.set_details(self.ctx, d["id"], {"delivery": {"address": "x"}})       # not a delivery yet
        d = self.s.set_details(self.ctx, d["id"], {"kind": "delivery", "delivery": {"address": "شارع الجلاء",
                                                                                   "phone": "0100", "courier": "محمد", "fee": 1000}})
        self.assertEqual(d["total"], 5000)
        r = self.done(d, [{"method": "cash", "amount": 5000}])
        self.assertEqual((r["delivery"]["status"], r["delivery"]["cash_due"]), ("out", 5000))
        sm = self.cd.summary(self.ctx)
        self.assertEqual((sm["expected_cash"], sm["with_courier"]), (10000, 5000))
        self.assertEqual(len(self.s.open_deliveries(self.ctx)), 1)
        self.s.delivery_status(self.ctx, d["id"], "delivered")
        self.s.delivery_status(self.ctx, d["id"], "settled")
        sm = self.cd.summary(self.ctx)
        self.assertEqual((sm["expected_cash"], sm["with_courier"]), (15000, 0))
        with self.assertRaises(Conflict):
            self.s.delivery_status(self.ctx, d["id"], "settled")

    def test_delivery_needs_address_or_phone_and_void_before_settle(self):
        d = self.s.set_details(self.ctx, self.draft()["id"], {"kind": "delivery"})
        with self.assertRaises(ValidationFailed):
            self.done(d, [{"method": "cash", "amount": 4000}])
        d = self.s.set_details(self.ctx, d["id"], {"delivery": {"phone": "0100"}})
        self.done(d, [{"method": "cash", "amount": 4000}])
        self.s.void(self.ctx, d["id"], "العميل رفض الاستلام")
        self.assertEqual(self.cd.summary(self.ctx)["expected_cash"], 10000)          # nothing taken out of the drawer


class TestCashierAPI(APICase):
    def test_channels_drafts_details(self):
        channels.ensure_defaults(self.db)
        self.http_login()
        chs = self.c.get("/api/payment-channels").json()["channels"]
        self.assertEqual(len(chs), 3)
        r = self.c.post("/api/payment-channels", json={"kind": "card", "name": "مكنة البنك الأهلي"}, headers=H)
        self.assertEqual(len(r.json()["channels"]), 4)
        sid = self.c.post("/api/sales", json={}, headers=H).json()["id"]
        self.assertEqual([d["id"] for d in self.c.get("/api/sales/drafts").json()["drafts"]], [sid])
        r = self.c.patch(f"/api/sales/{sid}", json={"kind": "delivery", "delivery": {"phone": "0100", "fee": 500}}, headers=H)
        self.assertEqual((r.json()["kind"], r.json()["total"]), ("delivery", 500))
        self.assertEqual(self.c.post(f"/api/sales/{sid}/discard", json={}, headers=H).status_code, 200)


class TestShiftCount(V2):
    def test_denominations_report_and_who_sees_it(self):
        self.done(self.draft(), [{"method": "cash", "amount": 4000}])           # drawer: 100 + 40
        with self.assertRaises(ValidationFailed):          # the owner sees the difference before closing: needs a reason
            self.cd.close(self.ctx, denominations={"10000": 1, "2000": 1, "1000": 1, "500": 1, "25": 4})
        r = self.cd.close(self.ctx, denominations={"10000": 1, "2000": 1, "1000": 1, "500": 1, "25": 4}, note="فكة")
        self.assertEqual((r["counted_cash"], r["difference"]), (13600, -400))
        self.assertEqual(r["counted_detail"], {"10000": 1, "2000": 1, "1000": 1, "500": 1, "25": 4})
        self.assertEqual((r["sales_count"], r["sales_total"]), (1, 4000))
        from app.sales.cash import count_total
        with self.assertRaises(ValidationFailed):
            count_total({"300": 1})
        people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        a = self.login("asst", PW)
        with self.assertRaises(Forbidden):
            self.cd.report(a, r["id"])                     # someone else's shift
        self.cd.open(a, 0)
        mine = self.cd.close(a, denominations={})
        self.assertEqual(mine["difference"], 0)            # his own, after closing: he sees it
        self.assertEqual(len(self.cd.recent(self.ctx)), 2)


class TestShiftAPI(APICase):
    def test_close_with_denominations_and_print_page(self):
        self.http_login()
        self.c.post("/api/cash/open", json={"opening_cash": 5000}, headers=H)
        r = self.c.post("/api/cash/close", json={"denominations": {"5000": 1}}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        page = self.c.get(f"/shift-report/{r.json()['id']}")
        self.assertIn("تقرير إغلاق الشيفت", page.text)
        self.assertIn("مطابق", page.text)
