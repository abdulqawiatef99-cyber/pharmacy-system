"""#324 audit M2, the sales and cash findings in app/sales and app/api (first half): M2-03 cash received below the
cash part, M2-06 an old count screen closing a new shift, M2-07 offsetting price changes, M2-08 a contract rate
changed under the posting, M2-19 malformed money inputs, M2-20 one buyer under two phone formats, M2-26 opening
a shift answered with 403 after it committed, and M2-25 (the routes' half). Each test failed on develop `5592748`
(M2-25 on `c5470e1`, after the core half merged). Synthetic data only."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from starlette.testclient import TestClient

from app import config
from app.api.app import create_app
from app.catalog import service as cat
from app.core import people
from app.core.errors import Conflict, ValidationFailed
from app.inventory import service as inv
from app.purchasing.service import Purchasing
from app.sales import contracts as k
from app.sales import customers as cu
from tests.helpers import OWNER_PW
from tests.test_cashier_acceptance import T, Base

H = {"X-Requested-With": "pharm"}
PW = "Staff-Pass-2026"
JS = (Path(__file__).resolve().parents[1] / "app" / "web" / "app.js").read_text(encoding="utf-8")


class ApiBase(Base):
    def setUp(self):
        super().setUp()
        st = replace(config.load(), upload_dir=self.tmp / "up", anthropic_api_key=None, data_dir=self.tmp,
                     backup_dir=self.tmp / "backups")
        self.c = TestClient(create_app(self.db, st, Purchasing(self.db, st)))
        self.login_as("owner", OWNER_PW)

    def login_as(self, user, pw):
        self.c.cookies.clear()
        r = self.c.post("/api/auth/login", json={"username": user, "password": pw}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)

    def post(self, url, body, key):
        return self.c.post(url, json=body, headers={**H, "Idempotency-Key": key})

    def draft(self, units=2):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=units)
        f = self.s.safety_check(self.ctx, sid)
        return sid, d["total"], {str(i): "ok" for i in range(len(f))}

    def drawer(self):
        return [r["amount"] for r in self.db.all("SELECT amount FROM cash_movements WHERE kind='sale'")]


class TestM2_03CashReceivedBelowTheCashPart(ApiBase):
    def test_a_typed_amount_below_the_cash_part_is_refused_before_posting(self):
        self.cd.open(self.ctx, 0)
        sid, total, dec = self.draft()
        r = self.post(f"/api/sales/{sid}/complete", {"payments": [{"method": "cash", "amount": total}],
                                                     "safety_decisions": dec, "cash_given": total - 1000}, "g-1")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (422, "cash_given_short"), r.text)
        self.assertEqual(self.db.one("SELECT status FROM sales WHERE id=?", (sid,))["status"], "draft")
        self.assertEqual(self.drawer(), [])

    def test_no_amount_typed_or_enough_still_sells(self):
        self.cd.open(self.ctx, 0)
        for key, given in (("g-2", 0), ("g-3", None)):
            sid, total, dec = self.draft()
            body = {"payments": [{"method": "cash", "amount": total}], "safety_decisions": dec}
            if given is not None:
                body["cash_given"] = given
            self.assertEqual(self.post(f"/api/sales/{sid}/complete", body, key).status_code, 200)
        sid, total, dec = self.draft()
        r = self.post(f"/api/sales/{sid}/complete", {"payments": [{"method": "cash", "amount": total}],
                                                     "safety_decisions": dec, "cash_given": total + 1000}, "g-4")
        self.assertEqual(r.status_code, 200, r.text)

    def test_the_old_three_amounts_are_checked_too(self):
        self.cd.open(self.ctx, 0)
        sid, total, dec = self.draft()
        r = self.post(f"/api/sales/{sid}/complete", {"paid_cash": total, "safety_decisions": dec,
                                                     "cash_given": 100}, "g-5")
        self.assertEqual(r.status_code, 422, r.text)

    def test_the_screen_stops_before_sending(self):
        at = JS.index("const gv = toPiasters($(\"#payGiven\")?.value)")
        block = JS[at:at + 900]
        self.assertIn("cashPart", block)
        self.assertLess(block.index("given < cashPart"), block.index("btn.disabled = true"))


class TestM2_06OldCountScreen(ApiBase):
    def test_a_count_for_a_closed_shift_never_closes_the_next(self):
        a = self.cd.open(self.ctx, 0)
        self.cd.close(self.ctx, 0)
        b = self.cd.open(self.ctx, 5000)
        r = self.post("/api/cash/close", {"counted_cash": 5000, "shift_id": a}, "c-1")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "shift_changed"), r.text)
        self.assertEqual(self.db.one("SELECT status FROM cash_shifts WHERE id=?", (b,))["status"], "open")
        r = self.post("/api/cash/close", {"counted_cash": 5000, "shift_id": b}, "c-2")
        self.assertEqual(r.status_code, 200, r.text)

    def test_the_service_checks_it_inside_the_close(self):
        a = self.cd.open(self.ctx, 0)
        self.cd.close(self.ctx, 0)
        self.cd.open(self.ctx, 0)
        with self.assertRaises(Conflict) as e:
            self.cd.close(self.ctx, 0, shift_id=a)
        self.assertEqual(e.exception.code, "shift_changed")

    def test_the_screen_sends_the_shift_it_shows(self):
        at = JS.index('await api("/api/cash/close"')
        self.assertIn("shift_id: sh.id", JS[at:at + 200])


class TestM2_07OffsettingPriceChanges(Base):
    def test_each_line_is_checked_under_the_lock(self):
        a = cat.create_item(self.db, self.ctx, {"name_en": "Item A 10 tab", "pack_size": 1, "public_price": 10000})
        b = cat.create_item(self.db, self.ctx, {"name_en": "Item B 10 tab", "pack_size": 1, "public_price": 10000})
        with self.db.transaction():
            for it in (a, b):
                bt = inv.get_or_create_batch(self.db, self.ctx, it, "X1", (T + timedelta(days=400)).isoformat())
                inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=5, batch_id=bt, movement_type="purchase",
                                  unit_cost=Decimal("10"))
        self.cd.open(self.ctx, 0)
        sid = self.s.create(self.ctx)
        self.s.add_line(self.ctx, sid, a, units=1, discount_bp=5000)       # 100.00 less 50%: 50.00
        d = self.s.add_line(self.ctx, sid, b, units=1)                     # 100.00
        self.assertEqual(d["total"], 15000)
        real = self.s._plan

        def moved(ctx, s, line, item, used=None):                         # under the lock: A +20, B -10
            plan = real(ctx, s, line, item, used)
            if used is None:
                step = 2000 if line["item_id"] == a else -1000
                for x in plan:
                    x["unit_price"] += step
                    x["pack_price"] = (x["pack_price"] or 0) + step if x.get("pack_price") else x.get("pack_price")
            return plan

        self.s._plan = moved
        f = self.s.safety_check(self.ctx, sid)
        with self.assertRaises(Conflict) as e:                             # the total still says 150.00
            self.s.complete(self.ctx, sid, payments=[{"method": "cash", "amount": 15000}],
                            safety_decisions={i: "ok" for i in range(len(f))})
        self.assertEqual(e.exception.code, "price_changed")
        self.assertEqual(self.db.one("SELECT status FROM sales WHERE id=?", (sid,))["status"], "draft")

    def test_the_header_is_the_sum_of_its_lines(self):
        self.cd.open(self.ctx, 0)
        done = self.sell(units=2)
        lines = self.db.all("SELECT line_total FROM sale_lines WHERE sale_id=?", (done["id"],))
        self.assertEqual(done["subtotal"] - done["discount_total"], sum(r["line_total"] for r in lines))


class TestM2_08ContractChangedUnderThePosting(Base):
    def test_a_new_rate_after_the_check_is_price_changed(self):
        cid = k.save(self.db, self.ctx, {"name": "نقابة تجربة", "discount_local_bp": 1000})[0]["id"]
        cust = self.s.create_customer(self.ctx, name="عميل تعاقد")
        k.link_customer(self.db, self.ctx, cust, cid)
        self.cd.open(self.ctx, 0)
        sid = self.s.create(self.ctx, cust)
        d = self.s.add_line(self.ctx, sid, self.pan, units=2)
        self.assertEqual(d["total"], 3600)                                 # 40.00 less 10%
        real = self.s.safety_check

        def raise_rate(ctx, s):                                            # after the first contract check
            with self.db.transaction():
                self.db.execute("UPDATE contracts SET discount_local_bp=2000 WHERE id=?", (cid,))
            return real(ctx, s)

        self.s.safety_check = raise_rate
        f = real(self.ctx, sid)
        with self.assertRaises(Conflict) as e:
            self.s.complete(self.ctx, sid, payments=[{"method": "cash", "amount": 3600}],
                            safety_decisions={i: "ok" for i in range(len(f))})
        self.assertEqual(e.exception.code, "price_changed")
        self.assertEqual(self.db.one("SELECT status FROM sales WHERE id=?", (sid,))["status"], "draft")


class TestM2_19MalformedMoneyInputs(ApiBase):
    def test_an_empty_list_of_notes_is_not_a_count(self):
        self.cd.open(self.ctx, 30000)
        r = self.post("/api/cash/close", {"denominations": [], "note": "عد الخزينة"}, "m-1")
        self.assertEqual(r.status_code, 422, r.text)
        r = self.post("/api/cash/close", {"note": "عد الخزينة"}, "m-2")          # no count at all
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(self.db.one("SELECT count(*) n FROM cash_shifts WHERE status='open'")["n"], 1)
        r = self.post("/api/cash/close", {"counted_cash": 30000}, "m-3")
        self.assertEqual(r.status_code, 200, r.text)

    def test_true_is_not_an_amount(self):
        self.cd.open(self.ctx, 0)
        sid, total, dec = self.draft()
        with self.assertRaises(ValidationFailed):
            self.s.complete(self.ctx, sid, payments=[{"method": "cash", "amount": total - 1},
                                                     {"method": "cash", "amount": True}],
                            safety_decisions={int(i): v for i, v in dec.items()})
        self.assertEqual(self.drawer(), [])

    def test_true_is_not_a_delivery_fee(self):
        sid = self.s.create(self.ctx)
        self.s.add_line(self.ctx, sid, self.pan, units=1)
        with self.assertRaises(ValidationFailed):
            self.s.set_details(self.ctx, sid, {"kind": "delivery", "delivery": {"phone": "0100", "fee": True}})


class TestM2_20OneBuyerTwoPhoneFormats(Base):
    def test_international_forms_become_the_local_number(self):
        for raw in ("+201001234567", "00201001234567", "201001234567", "01001234567", "+20 100 123 4567"):
            self.assertEqual(cu.clean_phone(raw), "01001234567", raw)
        self.assertEqual(cu.clean_phone("+9665012345678"), "+9665012345678")   # not Egyptian: kept as typed
        self.assertEqual(cu.clean_phone("0482221234"), "0482221234")             # a landline stays

    def test_the_same_mobile_cannot_open_a_second_credit_account(self):
        self.s.create_customer(self.ctx, name="عميل أول", phone="01001234567", credit_limit=10000)
        with self.assertRaises(Conflict):
            self.s.create_customer(self.ctx, name="عميل ثان", phone="+20 100 123 4567", credit_limit=10000)

    def test_a_number_saved_before_in_the_international_form_is_found(self):
        old = self.s.create_customer(self.ctx, name="عميل قديم")
        with self.db.transaction():                                         # saved before this fix
            self.db.execute("UPDATE customers SET phone='+201001234567' WHERE id=?", (old,))
        with self.assertRaises(Conflict):
            self.s.create_customer(self.ctx, name="عميل جديد", phone="01001234567")
        other = self.s.create_customer(self.ctx, name="عميل آخر")
        with self.assertRaises(Conflict):
            cu.update(self.db, self.ctx, other, {"phone2": "00201001234567"})

    def test_existing_doubles_are_listed_for_review(self):
        a = self.s.create_customer(self.ctx, name="عميل أ", phone="01001234567")
        b = self.s.create_customer(self.ctx, name="عميل ب")
        with self.db.transaction():
            self.db.execute("UPDATE customers SET phone2='201001234567' WHERE id=?", (b,))
        rows = cu.phone_doubles(self.db, self.ctx)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["phone"], "01001234567")
        self.assertEqual({c["id"] for c in rows[0]["customers"]}, {a, b})


class TestM2_26OpenerWithoutTheSummary(ApiBase):
    def test_the_open_answers_with_what_the_opener_may_see(self):
        people.add_user(self.db, self.ctx, username="seller", full_name="S", password=PW, role_code="assistant")
        uid = self.db.one("SELECT id FROM users WHERE username='seller'")["id"]
        people.set_exception(self.db, self.ctx, uid, "sales.read", "deny")
        self.login_as("seller", PW)
        r = self.post("/api/cash/open", {"opening_cash": 10000}, "o-1")
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual((r.json()["status"], r.json()["opening_cash"]), ("open", 10000))
        self.assertNotIn("cash_by_kind", r.json())
        self.login_as("owner", OWNER_PW)
        r = self.post("/api/cash/close", {"counted_cash": 10000}, "o-2")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("cash_by_kind", self.post("/api/cash/open", {"opening_cash": 0}, "o-3").json())



class TestM2_20DoublesRoute(ApiBase):
    def test_the_owner_sees_the_doubles_a_seller_does_not(self):
        a = self.s.create_customer(self.ctx, name="عميل أ", phone="01001234567")
        b = self.s.create_customer(self.ctx, name="عميل ب")
        with self.db.transaction():
            self.db.execute("UPDATE customers SET phone='+201001234567' WHERE id=?", (b,))
        r = self.c.get("/api/customers/phone-doubles", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual({c["id"] for c in r.json()["doubles"][0]["customers"]}, {a, b})
        people.add_user(self.db, self.ctx, username="seller2", full_name="S", password=PW, role_code="assistant")
        self.login_as("seller2", PW)
        self.assertEqual(self.c.get("/api/customers/phone-doubles", headers=H).status_code, 403)
        self.assertIn('api("/api/customers/phone-doubles")', JS)


class TestM2_25ReplayCleanupInTheRoutes(ApiBase):
    """The keyed-POST reservation used to scan the replay table on every request; it now asks the one helper,
    which cleans at most once an hour (the core half came in #364)."""

    def test_the_reservation_cleans_through_the_helper(self):
        from unittest.mock import patch

        from app.core import idempotency
        idempotency._pruned_at.clear()
        with self.db.transaction():
            self.db.execute("INSERT INTO idempotency_keys (org_id, user_id, key, method, path, request_hash, status,"
                            " response, created_at) VALUES (?,?,?,?,?,?,?,?, datetime('now','-30 days'))",
                            (self.ctx.org_id, self.ctx.user_id, "old", "POST", "/x", "h", 201, "{}"))
        real, calls = idempotency.prune, []
        with patch.object(idempotency, "prune", lambda db, *a, **kw: calls.append(1) or real(db, *a, **kw)):
            for key in ("p-1", "p-2"):
                self.assertEqual(self.post("/api/cash/open", {"opening_cash": 0}, key).status_code in (201, 409), True)
        self.assertEqual(len(calls), 2)                                   # asked each time ...
        self.assertIsNone(self.db.one("SELECT 1 FROM idempotency_keys WHERE key='old'"))   # ... cleaned once
        self.assertNotIn("DELETE FROM idempotency_keys WHERE created_at", (Path(__file__).resolve().parents[1]
                         / "app" / "api" / "app.py").read_text(encoding="utf-8"))


class TestCodexReview366(ApiBase):
    """#366 Codex: P1 the minimal open answer reads its shift by org too; P2 a full international number typed in
    the search finds the customer saved under the local form."""

    def test_the_minimal_open_answer_is_scoped_by_org(self):
        at = JS_APP.index("if not ctx.can(\"sales.read\"):       # the shift is open")
        self.assertIn("AND org_id=?", JS_APP[at:at + 300])

    def test_an_international_number_typed_in_the_search_finds_the_customer(self):
        cid = self.s.create_customer(self.ctx, name="عميل بحث", phone="+201001234567")
        for q in ("+201001234567", "00201001234567", "+20 100 123 4567", "01001234567", "1234567", "عميل"):
            with self.subTest(q=q):
                r = self.c.get("/api/customers", params={"q": q}, headers=H).json()
                self.assertIn(cid, [c["id"] for c in r])
                rows = cu.listing(self.db, self.ctx, q)
                self.assertIn(cid, [c["id"] for c in rows])


JS_APP = (Path(__file__).resolve().parents[1] / "app" / "api" / "app.py").read_text(encoding="utf-8")
