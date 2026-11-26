import sqlite3
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from app.core import accounts, audit, security
from app.core.errors import Conflict, Forbidden, Unauthorized, ValidationFailed
from app.db import Database, MigrationError, new_id
from tests.helpers import DBTestCase, OWNER_PW


class TestIds(unittest.TestCase):
    def test_uuid7_unique_and_ordered(self):
        ids = [new_id() for _ in range(2000)]
        self.assertEqual(len(set(ids)), 2000)
        self.assertTrue(all(i[14] == "7" for i in ids))  # version nibble
        # time-ordered prefix (48-bit ms) never decreases
        prefixes = [i.replace("-", "")[:12] for i in ids]
        self.assertEqual(prefixes, sorted(prefixes))


class TestPasswords(unittest.TestCase):
    def test_hash_verify(self):
        h = security.hash_password("correct horse 9")
        self.assertTrue(security.verify_password("correct horse 9", h))
        self.assertFalse(security.verify_password("correct horse 8", h))
        self.assertFalse(security.verify_password("x", "garbage"))
        self.assertNotEqual(h, security.hash_password("correct horse 9"))  # salted

    def test_policy(self):
        self.assertTrue(security.check_password_policy("short"))
        self.assertTrue(security.check_password_policy("aaaaaaaaaaaa"))
        self.assertEqual(security.check_password_policy("Good-Pass-2026"), [])


class TestMigrations(DBTestCase):
    def test_idempotent(self):
        self.assertEqual(self.db.migrate(), [])

    def test_edited_migration_is_rejected(self):
        d = self.tmp / "mig"
        d.mkdir()
        (d / "0001_x.sql").write_text("CREATE TABLE t (a INTEGER);")
        db2 = Database(self.tmp / "m.db")
        db2.migrate(d)
        (d / "0001_x.sql").write_text("CREATE TABLE t (a INTEGER, b INTEGER);")
        with self.assertRaises(MigrationError):
            db2.migrate(d)
        db2.close()

    def test_writes_require_transaction(self):
        with self.assertRaises(RuntimeError):
            self.db.execute("UPDATE users SET full_name='x'")

    def test_backup(self):
        dest = self.tmp / "backup.db"
        self.db.backup_to(dest)
        c = sqlite3.connect(dest)
        self.assertEqual(c.execute("SELECT count(*) FROM organizations").fetchone()[0], 1)
        c.close()


class TestAuth(DBTestCase):
    def test_login_and_context(self):
        self.assertEqual(self.ctx.org_id, self.org["org_id"])
        self.assertEqual(self.ctx.branch_id, self.org["branch_id"])
        self.assertTrue(self.ctx.can("purchases.approve"))

    def test_wrong_password_same_message_as_unknown_user(self):
        with self.assertRaises(Unauthorized) as a:
            accounts.login(self.db, "owner", "wrong-password-1")
        with self.assertRaises(Unauthorized) as b:
            accounts.login(self.db, "nobody", "wrong-password-1")
        self.assertEqual(a.exception.message, b.exception.message)

    def test_failed_password_policy_has_no_account_wide_lockout(self):
        for _ in range(accounts.MAX_FAILED_LOGINS):
            with self.assertRaises(Unauthorized) as refused:
                accounts.login(self.db, "owner", "wrong-password-1", ip="127.0.0.1")
            self.assertEqual(refused.exception.code, "invalid_credentials")
        result = accounts.login(self.db, "owner", OWNER_PW, ip="127.0.0.1")
        self.assertEqual(accounts.resolve_session(self.db, result["token"]).user_id, self.ctx.user_id)
        self.assertEqual(self.db.one("SELECT count(*) n FROM audit_log WHERE org_id=?"
                                     " AND action='auth.login_failed'", (self.ctx.org_id,))["n"],
                         accounts.MAX_FAILED_LOGINS)
        self.assertEqual(self.db.one("SELECT count(*) n FROM notifications WHERE org_id=?"
                                     " AND kind='failed_sign_in'", (self.ctx.org_id,))["n"], 1)

    def test_password_changed_during_verification_cannot_mint_a_session(self):
        verify = security.verify_password
        replacement = security.hash_password("Replacement-Pass-2026")

        def changed_during_verification(password, stored):
            result = verify(password, stored)
            with self.db.transaction():
                self.db.execute("UPDATE users SET password_hash=? WHERE id=?", (replacement, self.ctx.user_id))
            return result

        with mock.patch.object(security, "verify_password", side_effect=changed_during_verification):
            with self.assertRaises(Unauthorized):
                accounts.login(self.db, "owner", OWNER_PW)
        self.assertEqual(self.db.one("SELECT count(*) n FROM sessions WHERE user_id=?", (self.ctx.user_id,))["n"], 1)

    def test_logout_revokes(self):
        tok = accounts.login(self.db, "owner", OWNER_PW)["token"]
        accounts.resolve_session(self.db, tok)
        accounts.logout(self.db, tok)
        with self.assertRaises(Unauthorized):
            accounts.resolve_session(self.db, tok)

    def test_expired_and_idle_sessions(self):
        tok = accounts.login(self.db, "owner", OWNER_PW)["token"]
        real = accounts.utcnow()
        with mock.patch.object(accounts, "utcnow", return_value=real + accounts.SESSION_IDLE
                               + timedelta(seconds=1)):
            with self.assertRaises(Unauthorized):
                accounts.resolve_session(self.db, tok)

    def test_token_stored_hashed(self):
        tok = accounts.login(self.db, "owner", OWNER_PW)["token"]
        self.assertIsNone(self.db.one("SELECT 1 FROM sessions WHERE token_hash=?", (tok,)))

    def test_password_change_revokes_sessions(self):
        tok = accounts.login(self.db, "owner", OWNER_PW)["token"]
        ctx = accounts.resolve_session(self.db, tok)
        accounts.change_password(self.db, ctx, OWNER_PW, "New-Owner-Pass-1")
        with self.assertRaises(Unauthorized):
            accounts.resolve_session(self.db, tok)
        self.login("owner", "New-Owner-Pass-1")

    def test_roles_limit_permissions(self):
        accounts.add_user(self.db, self.ctx, username="asst", full_name="Assistant",
                          password="Assist-Pass-2026", role_code="assistant")
        actx = self.login("asst", "Assist-Pass-2026")
        self.assertFalse(actx.can("purchases.approve"))
        with self.assertRaises(Forbidden):
            accounts.add_user(self.db, actx, username="x2", full_name="x",
                              password="Another-Pass-1", role_code="owner")

    def test_duplicate_username(self):
        with self.assertRaises(Conflict):
            accounts.add_user(self.db, self.ctx, username="OWNER", full_name="dup",
                              password="Another-Pass-1", role_code="assistant")

    def test_weak_password_rejected(self):
        with self.assertRaises(ValidationFailed):
            accounts.add_user(self.db, self.ctx, username="weak", full_name="w",
                              password="123", role_code="assistant")

    def test_suspended_org_sessions_die(self):
        tok = accounts.login(self.db, "owner", OWNER_PW)["token"]
        with self.db.transaction():
            self.db.execute("UPDATE organizations SET status='suspended' WHERE id=?",
                            (self.org["org_id"],))
        with self.assertRaises(Unauthorized):
            accounts.resolve_session(self.db, tok)


class TestAudit(DBTestCase):
    def test_append_only(self):
        with self.assertRaises(sqlite3.DatabaseError):
            with self.db.transaction():
                self.db.execute("UPDATE audit_log SET action='x'")
        with self.assertRaises(sqlite3.DatabaseError):
            with self.db.transaction():
                self.db.execute("DELETE FROM audit_log")

    def test_login_is_audited(self):
        n = self.db.one("SELECT count(*) c FROM audit_log WHERE action='auth.login'")["c"]
        self.assertGreaterEqual(n, 1)


class TestEntitlements(DBTestCase):
    def test_default_features_and_limits(self):
        accounts.require_feature(self.db, self.ctx, "purchasing")
        with self.assertRaises(Forbidden):
            accounts.require_feature(self.db, self.ctx, "ai_extraction")
        accounts.set_entitlement(self.db, self.org["org_id"], "ai_extraction",
                                 limits={"monthly": 2})
        accounts.consume_usage(self.db, self.ctx, "ai_extraction")
        accounts.consume_usage(self.db, self.ctx, "ai_extraction")
        with self.assertRaises(Forbidden):
            accounts.consume_usage(self.db, self.ctx, "ai_extraction")

    def test_tenant_cannot_grant_itself(self):
        # The only setter takes an org id, not a tenant context, and is not routed.
        import inspect
        params = inspect.signature(accounts.set_entitlement).parameters
        self.assertNotIn("ctx", params)


if __name__ == "__main__":
    unittest.main()
