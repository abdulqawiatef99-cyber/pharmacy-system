"""Owner / manager / pharmacist / assistant: defaults, per-person exceptions,
discount limits, PIN, on-screen approval, returns policy, visibility, notifications."""
import json
from dataclasses import replace
from datetime import timedelta

from app.catalog import service as cat
from app.core import accounts, notify, people
from app.core.errors import Conflict, Forbidden, Unauthorized, ValidationFailed
from app.core.permissions import DEFAULT_ROLES, ROLES_VERSION
from app.sales.service import Sales
from tests.test_api import APICase, H
from tests.test_cashier_acceptance import Base, T

PW = "Staff-Pass-2026"


class People(Base):
    def setUp(self):
        super().setUp()
        self.cd.open(self.ctx, 0)
        self.ids = {}
        for code in ("manager", "pharmacist", "assistant"):
            self.ids[code] = people.add_user(self.db, self.ctx, username=code, full_name=code.title(),
                                             password=PW, role_code=code, pin={"manager": "2468",
                                                                                "pharmacist": "1357",
                                                                                "assistant": "8642"}[code])
        self.mgr, self.ph, self.asst = (self.login(c, PW) for c in ("manager", "pharmacist", "assistant"))
        self.ids["owner"] = self.ctx.user_id
        people.set_pin(self.db, self.ctx, self.ctx.user_id, "9753")


class TestDefaults(People):
    def test_four_roles_and_owner_has_everything(self):
        codes = {r["code"] for r in people.roles(self.db, self.ctx)}
        self.assertEqual(codes, {"owner", "manager", "pharmacist", "assistant"})
        self.assertTrue(self.ctx.is_owner and self.ctx.can("costs.read") and self.ctx.discount_limit_bp == 10000)
        for c in (self.mgr, self.ph, self.asst):
            self.assertFalse(c.can("costs.read"))            # cost & profit: owner only
            self.assertFalse(c.can("users.manage"))
            self.assertEqual(c.max_discount_bp, 0)           # no discount by default
        self.assertTrue(self.mgr.can("sales.totals") and self.mgr.can("notifications.read"))
        self.assertFalse(self.ph.can("sales.totals"))
        self.assertTrue(self.asst.can("cash.close") and not self.asst.can("cash.see_expected"))

    def test_exceptions_grant_and_deny(self):
        uid = self.ids["assistant"]
        people.set_exception(self.db, self.ctx, uid, "sales.return_others", "grant")
        people.set_exception(self.db, self.ctx, uid, "cash.close", "deny")
        a = self.login("assistant", PW)
        self.assertTrue(a.can("sales.return_others"))
        self.assertFalse(a.can("cash.close"))
        m = people.access_matrix(self.db, self.ctx, uid)
        ex = {i["code"]: i["exception"] for g in m["groups"] for i in g["items"] if i["exception"]}
        self.assertEqual(ex, {"sales.return_others": "grant", "cash.close": "deny"})
        people.reset_to_role(self.db, self.ctx, uid)
        a = self.login("assistant", PW)
        self.assertFalse(a.can("sales.return_others"))
        self.assertTrue(a.can("cash.close"))

    def test_owner_cannot_be_restricted_and_only_one_owner(self):
        with self.assertRaises(Forbidden):
            people.set_exception(self.db, self.ctx, self.ctx.user_id, "sales.void", "deny")
        with self.assertRaises(Forbidden):
            people.set_role(self.db, self.ctx, self.ids["manager"], "owner")
        with self.assertRaises(Forbidden):
            people.set_exception(self.db, self.ctx, self.ids["manager"], "users.manage", "grant")
        with self.assertRaises(Forbidden):                    # managers do not manage people
            people.set_exception(self.db, self.mgr, self.ids["assistant"], "sales.void", "grant")

    def test_sync_upgrades_old_pharmacies_once(self):
        with self.db.transaction():
            self.db.execute("DELETE FROM org_settings WHERE key='roles_version'")
            self.db.execute("UPDATE roles SET permissions='[\"sales.create\"]' WHERE code='pharmacist'")
        self.assertEqual(accounts.sync_system_roles(self.db), 1)
        perms = json.loads(self.db.one("SELECT permissions FROM roles WHERE code='pharmacist'")["permissions"])
        self.assertEqual(sorted(perms), sorted(DEFAULT_ROLES["pharmacist"][1]))
        self.assertEqual(self.db.one("SELECT value FROM org_settings WHERE key='roles_version'")["value"],
                         ROLES_VERSION)
        self.assertEqual(accounts.sync_system_roles(self.db), 0)


class TestPin(People):
    def test_weak_and_bad_pins_refused(self):
        for bad in ("12", "abcd", "1111", "1234", "1234567"):
            with self.assertRaises(ValidationFailed):
                people.set_pin(self.db, self.asst, self.ids["assistant"], bad)
        with self.assertRaises(Forbidden):                    # someone else's PIN: owner only
            people.set_pin(self.db, self.asst, self.ids["manager"], "5791")

    def test_lockout_after_five_wrong(self):
        for _ in range(5):
            with self.assertRaises(Unauthorized):
                people.verify_pin(self.db, self.ids["manager"], "0000", org_id=self.ctx.org_id)
        with self.assertRaises(Unauthorized) as e:
            people.verify_pin(self.db, self.ids["manager"], "2468", org_id=self.ctx.org_id)
        self.assertEqual(e.exception.code, "pin_locked")

    def test_pin_login_and_approvers(self):
        tok = people.pin_login(self.db, self.ctx.org_id, self.ids["pharmacist"], "1357")["token"]
        self.assertEqual(accounts.resolve_session(self.db, tok).role_code, "pharmacist")
        names = [a["full_name"] for a in people.approvers(self.db, self.asst)]
        self.assertEqual(names, ["Owner", "Manager"])
        with self.assertRaises(Forbidden):                    # a pharmacist is not an approver
            accounts.with_approval(self.db, self.asst, self.ids["pharmacist"], "1357")
        with self.assertRaises(ValidationFailed):
            accounts.with_approval(self.db, self.mgr, self.ids["manager"], "2468")


class TestDiscount(People):
    def test_limits_approval_and_notification(self):
        sid = self.s.create(self.asst)
        with self.assertRaises(Forbidden) as e:
            self.s.add_line(self.asst, sid, self.pan, units=2, discount_bp=500)
        self.assertEqual(e.exception.code, "approval_required")
        people.set_role_discount(self.db, self.ctx, "assistant", 500)
        a = self.login("assistant", PW)
        self.s.add_line(a, sid, self.pan, units=2, discount_bp=500)
        people.set_role_discount(self.db, self.ctx, "manager", 1500)
        with self.assertRaises(Forbidden):
            self.s.add_line(a, sid, self.pan, units=2, discount_bp=1000)
        approved = accounts.with_approval(self.db, a, self.ids["manager"], "2468")
        d = self.s.add_line(approved, sid, self.pan, units=2, discount_bp=1000)
        line = self.db.one("SELECT * FROM sale_lines WHERE sale_id=? AND discount_bp=1000", (sid,))
        self.assertEqual(line["discount_approved_by"], self.ids["manager"])
        self.s.complete(a, sid, paid_cash=d["total"], safety_decisions={})
        n = notify.listing(self.db, self.mgr)["items"][0]
        self.assertEqual((n["kind"], n["actor"]), ("discount", "Assistant"))

    def test_below_cost_owner_confirms_others_cannot(self):
        # cost 15/unit, price 20/unit: 30% discount = 14 < 15
        people.set_role_discount(self.db, self.ctx, "manager", 5000)
        m = self.login("manager", PW)
        sid = self.s.create(m)
        d = self.s.add_line(m, sid, self.pan, units=2, discount_bp=3000)
        with self.assertRaises(Forbidden):
            self.s.complete(m, sid, paid_cash=d["total"], safety_decisions={})
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=2, discount_bp=3000)
        with self.assertRaises(Conflict) as e:
            self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions={})
        self.assertEqual(e.exception.code, "below_cost_confirm")
        self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions={}, confirm_below_cost=True)
        kinds = [n["kind"] for n in notify.listing(self.db, self.ctx)["items"]]
        self.assertIn("below_cost", kinds)


class TestReturns(People):
    def _line(self, sale):
        return {sale["lines"][0]["id"]: 1}

    def test_only_seller_returns_unless_approved(self):
        sale = self.sell(ctx=self.ph)
        with self.assertRaises(Forbidden) as e:
            self.s.return_items(self.asst, sale["id"], self._line(sale), refund_method="cash", reason="مش محتاجه")
        self.assertEqual(e.exception.details["permission"], "sales.return_others")
        approved = accounts.with_approval(self.db, self.asst, self.ids["manager"], "2468")
        with self.assertRaises(Forbidden):               # managers can't by default either
            self.s.return_items(approved, sale["id"], self._line(sale), refund_method="cash", reason="مش محتاجه")
        approved = accounts.with_approval(self.db, self.asst, self.ids["owner"], "9753")
        self.s.return_items(approved, sale["id"], self._line(sale), refund_method="cash", reason="مش محتاجه")
        audit_row = self.db.one("SELECT after_json FROM audit_log WHERE action='sale.return'")
        self.assertEqual(json.loads(audit_row["after_json"])["approved_by"], self.ids["owner"])
        self.s.return_items(self.ph, sale["id"], self._line(sale), refund_method="cash", reason="مش محتاجه")

    def test_window_14_days_by_default(self):
        """Owner 21/11 10:17: «مدة المرتجع 14 يوم» (part 3 had made it 7); Settings still changes it."""
        sale = self.sell(ctx=self.ph)
        later = Sales(self.db, today=T + timedelta(days=15))
        with self.assertRaises(Forbidden) as e:
            later.return_items(self.ph, sale["id"], self._line(sale), refund_method="cash", reason="متأخر")
        self.assertEqual(e.exception.details["permission"], "sales.return_late")
        Sales(self.db, today=T + timedelta(days=14)).return_items(
            self.ph, sale["id"], self._line(sale), refund_method="cash", reason="في المدة")
        later.return_items(self.ctx, sale["id"], self._line(sale), refund_method="cash", reason="المالك")


class TestVisibility(People):
    def test_employee_sees_only_his_invoices_today(self):
        mine, other = self.sell(ctx=self.asst), self.sell(ctx=self.ph)
        self.assertEqual([r["id"] for r in self.s.list_sales(self.asst)], [mine["id"]])
        self.assertEqual(len(self.s.list_sales(self.mgr)), 2)
        with self.assertRaises(Forbidden):
            self.s.list_sales(self.asst, day=(T - timedelta(days=1)).isoformat())
        with self.assertRaises(Forbidden):
            self.s.day_summary(self.asst)
        with self.assertRaises(Forbidden):
            self.s.get(self.asst, other["id"])
        self.assertEqual(self.s.list_sales(self.asst, sale_no=other["sale_no"]), [])
        self.assertIn("total", self.s.day_summary(self.mgr))

    def test_price_change_needs_permission_and_notifies(self):
        with self.assertRaises(Forbidden):
            cat.update_item(self.db, self.ph, self.pan, {"public_price": 4500})
        cat.update_item(self.db, self.ph, self.pan, {"location": "A1"})      # other edits are fine
        cat.update_item(self.db, self.mgr, self.pan, {"public_price": 4500})
        n = notify.listing(self.db, self.ctx)["items"][0]
        self.assertEqual(n["kind"], "price_change")
        self.assertEqual(notify.unread_count(self.db, self.ctx), 1)
        notify.mark_read(self.db, self.ctx)
        self.assertEqual(notify.unread_count(self.db, self.ctx), 0)
        self.assertEqual(notify.unread_count(self.db, self.mgr), 1)          # read state is per person
        with self.assertRaises(Forbidden):
            notify.listing(self.db, self.ph)


class TestPeopleAPI(APICase):
    def setUp(self):
        super().setUp()
        people.set_pin(self.db, self.ctx, self.ctx.user_id, "9753")

    def test_users_screen_and_approval_headers(self):
        self.http_login()
        r = self.c.post("/api/users", json={"username": "asst", "full_name": "أحمد", "password": PW,
                                            "role": "assistant", "pin": "8642"}, headers=H)
        self.assertEqual(r.status_code, 201, r.text)
        uid = r.json()["user"]["id"]
        r = self.c.post(f"/api/users/{uid}/permission", json={"permission": "sales.return_others",
                                                              "mode": "grant"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(self.c.get("/api/users").json()["users"]), 2)
        # the assistant logs in with his PIN on the pharmacy PC
        from starlette.testclient import TestClient
        a = TestClient(self.app)
        users = a.get("/api/auth/quick-users").json()["users"]
        self.assertEqual({u["full_name"] for u in users}, {"Owner", "أحمد"})
        r = a.post("/api/auth/pin", json={"user_id": uid, "pin": "8642"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(a.get("/api/users").status_code, 403)
        r = a.get("/api/sales/summary")
        self.assertEqual(r.status_code, 403)
        # wrong approver PIN is refused, right one passes
        hdr = {**H, "X-Approver": self.ctx.user_id, "X-Approver-Pin": "1111"}
        self.assertEqual(a.post("/api/approvals/check", json={"permission": "sales.void"},
                                headers=hdr).status_code, 401)
        hdr["X-Approver-Pin"] = "9753"
        r = a.post("/api/approvals/check", json={"permission": "sales.void"}, headers=hdr)
        self.assertEqual(r.json()["ok"], True)
        me = self.c.get("/api/me").json()
        self.assertEqual((me["role"], me["role_name"]), ("owner", "المالك"))
        r = self.c.put("/api/settings/sales", json={"return_window_days": 7}, headers=H)
        self.assertEqual(r.json()["return_window_days"], 7)


class TestRoleChange(People):
    """Changing someone's role (coverage 12/11: never run by a test)."""

    def test_a_new_role_starts_clean_and_is_audited(self):
        uid = self.ids["assistant"]
        people.set_exception(self.db, self.ctx, uid, "sales.return_others", "grant")
        people.set_discount(self.db, self.ctx, uid, 500)
        m = people.set_role(self.db, self.ctx, uid, "pharmacist")
        self.assertEqual(m["user"]["role"], "pharmacist")
        self.assertEqual([i["code"] for g in m["groups"] for i in g["items"] if i["exception"]], [])
        self.assertIsNone(m["max_discount_bp"])                              # back to the role's limit
        row = self.db.one("SELECT before_json, after_json FROM audit_log WHERE action='user.role' AND entity_id=?", (uid,))
        self.assertEqual((json.loads(row["before_json"]), json.loads(row["after_json"])),
                         ({"role": "assistant"}, {"role": "pharmacist"}))
        p = self.login("assistant", PW)
        self.assertTrue(p.can("items.write") and not p.can("sales.return_others"))

    def test_an_open_session_follows_the_new_role_at_once(self):
        tok = accounts.login(self.db, "manager", PW)["token"]
        self.assertTrue(accounts.resolve_session(self.db, tok).can("sales.totals"))
        people.set_role(self.db, self.ctx, self.ids["manager"], "assistant")
        after = accounts.resolve_session(self.db, tok)
        self.assertFalse(after.can("sales.totals") or after.can("audit.read"))   # no stale manager rights

    def test_refusals(self):
        with self.assertRaises(Forbidden):                                   # the owner's own role is locked
            people.set_role(self.db, self.ctx, self.ids["owner"], "manager")
        with self.assertRaises(Forbidden):                                   # only the owner manages people
            people.set_role(self.db, self.mgr, self.ids["assistant"], "manager")
        from app.core.errors import NotFound
        with self.assertRaises(NotFound):
            people.set_role(self.db, self.ctx, self.ids["assistant"], "director")
        _, other = self.second_tenant()
        with self.assertRaises((NotFound, Forbidden)):                       # another pharmacy's owner
            people.set_role(self.db, other, self.ids["assistant"], "manager")
        self.assertEqual(people.access_matrix(self.db, self.ctx, self.ids["assistant"])["user"]["role"], "assistant")

    def test_role_discount_limit(self):
        people.set_role_discount(self.db, self.ctx, "pharmacist", 300)
        self.assertEqual(self.login("pharmacist", PW).max_discount_bp, 300)
        for code, v in (("owner", 100), ("director", 100), ("pharmacist", None), ("pharmacist", True),
                        ("pharmacist", 10001), ("pharmacist", -1), ("pharmacist", 2.5)):
            with self.subTest(code=code, v=v), self.assertRaises(ValidationFailed):
                people.set_role_discount(self.db, self.ctx, code, v)
        with self.assertRaises(Forbidden):
            people.set_role_discount(self.db, self.mgr, "assistant", 100)
        self.assertEqual(self.login("pharmacist", PW).max_discount_bp, 300)


class TestReturnWindowSetting(People):
    """The 14-day default stays a default: the owner's saved value wins, both ways (owner 21/11 10:17)."""

    def test_saved_window_wins_over_the_default(self):
        from app.sales.service import DEFAULT_RETURN_DAYS, return_window_days
        self.assertEqual((DEFAULT_RETURN_DAYS, return_window_days(self.db, self.ctx.org_id)), (14, 14))
        for days in (7, 30):
            with self.db.transaction():
                self.db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?, 'return_window_days', ?)"
                                " ON CONFLICT(org_id, key) DO UPDATE SET value=excluded.value", (self.ctx.org_id, str(days)))
            self.assertEqual(return_window_days(self.db, self.ctx.org_id), days)
