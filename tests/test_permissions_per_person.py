"""Part 3 owner correction A: every permission is switchable per person (roles are only templates); the owner
switches anything; the manager (users.permissions) switches a staff member's permissions within his own."""
from app.core import accounts, people
from app.core.errors import Forbidden
from app.core.permissions import DEFAULT_ROLES, GROUPS, PERMISSIONS
from tests.helpers import DBTestCase

PW = "Staff-Pass-2026"


class Case(DBTestCase):
    def setUp(self):
        super().setUp()
        self.mgr_id = people.add_user(self.db, self.ctx, username="mgr1", full_name="مدير", password=PW, role_code="manager")
        self.mgr2_id = people.add_user(self.db, self.ctx, username="mgr2", full_name="مدير2", password=PW, role_code="manager")
        self.ast_id = people.add_user(self.db, self.ctx, username="asst1", full_name="مساعد", password=PW, role_code="assistant")
        self.mgr = self.login("mgr1", PW)

    def perms(self, uid):
        return accounts.effective_access(self.db, self.ctx.org_id, uid)["permissions"]


class TestEveryPermissionSwitchable(Case):
    def test_the_owner_can_open_and_close_every_permission_for_a_staff_member(self):
        for p in PERMISSIONS:
            if p == "users.manage":
                continue
            people.set_exception(self.db, self.ctx, self.ast_id, p, "grant")
            self.assertIn(p, self.perms(self.ast_id), p)
            people.set_exception(self.db, self.ctx, self.ast_id, p, "deny")
            self.assertNotIn(p, self.perms(self.ast_id), p)

    def test_every_permission_is_in_one_screen_group(self):
        self.assertEqual(sorted(c for _, cs in GROUPS for c in cs), sorted(PERMISSIONS))


class TestManagerSwitches(Case):
    def test_manager_opens_and_closes_within_his_own(self):
        people.set_exception(self.db, self.mgr, self.ast_id, "sales.void", "deny")      # assistant lacks it: no-op
        people.set_exception(self.db, self.mgr, self.ast_id, "customers.manage", "grant")
        self.assertIn("customers.manage", self.perms(self.ast_id))
        people.set_exception(self.db, self.mgr, self.ast_id, "sales.return", "deny")
        self.assertNotIn("sales.return", self.perms(self.ast_id))
        self.assertTrue(people.access_matrix(self.db, self.mgr, self.ast_id)["groups"])
        self.assertTrue(people.list_users(self.db, self.mgr))

    def test_manager_limits(self):
        cases = [(self.mgr_id, "customers.manage", "deny"),            # himself
                 (self.mgr2_id, "sales.totals", "deny"),               # another manager
                 (self.ast_id, "users.permissions", "grant"),          # the people rights
                 (self.ast_id, "costs.read", "grant")]                 # something he does not hold himself
        for uid, perm, mode in cases:
            with self.assertRaises(Forbidden, msg=(uid, perm)):
                people.set_exception(self.db, self.mgr, uid, perm, mode)
        owner = self.db.one("SELECT user_id FROM memberships m JOIN roles r ON r.id=m.role_id WHERE r.code='owner'")[0]
        with self.assertRaises(Forbidden):
            people.set_exception(self.db, self.mgr, owner, "sales.void", "deny")

    def test_closing_the_right_stops_the_manager(self):
        people.set_exception(self.db, self.ctx, self.mgr_id, "users.permissions", "deny")
        with self.assertRaises(Forbidden):
            people.set_exception(self.db, self.login("mgr1", PW), self.ast_id, "customers.manage", "grant")

    def test_manager_still_cannot_manage_users_themselves(self):
        with self.assertRaises(Forbidden):
            people.set_role(self.db, self.mgr, self.ast_id, "pharmacist")
        with self.assertRaises(Forbidden):
            people.add_user(self.db, self.mgr, username="newone", full_name="ن", password=PW, role_code="assistant")

    def test_manager_role_template(self):
        self.assertIn("users.permissions", DEFAULT_ROLES["manager"][1])
        self.assertNotIn("users.permissions", DEFAULT_ROLES["pharmacist"][1])


class TestCodexReview396(Case):
    def test_default_cannot_reopen_what_the_manager_does_not_hold(self):
        people.set_exception(self.db, self.ctx, self.mgr_id, "sales.return", "deny")
        people.set_exception(self.db, self.ctx, self.ast_id, "sales.return", "deny")
        mgr = self.login("mgr1", PW)
        with self.assertRaises(Forbidden):
            people.set_exception(self.db, mgr, self.ast_id, "sales.return", "default")
        self.assertNotIn("sales.return", self.perms(self.ast_id))
