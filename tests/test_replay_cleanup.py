"""Codex audit M2-25 (19/11): the clean-up of old replay records ran on every fresh keyed POST. The table has no
index on `created_at`, so each sale, payment and drawer movement read every stored answer of the last seven days
under the write lock, to delete nothing. Now one helper cleans at most once an hour, and the daily clean-up does
it whatever the hour. These tests pin: no scan per request, old records still go, fresh ones stay and replay.
Synthetic data only."""
from unittest.mock import patch

from app.core import idempotency, retention
from tests.helpers import DBTestCase


class ReplayCleanup(DBTestCase):
    def run_once(self, key, counter=None):
        def action():
            if counter is not None:
                counter.append(key)
            return 200, '{"ok": true}'
        return idempotency.run(self.db, self.ctx, key=key, method="POST", path="/api/synthetic",
                               request_hash="h-" + key, ttl_days=7, action=action)

    def old_record(self, key, days):
        with self.db.transaction():
            self.db.execute("INSERT INTO idempotency_keys (org_id, user_id, key, method, path, request_hash, status,"
                            " response, created_at) VALUES (?,?,?,?,?,?,200,'{}',"
                            " strftime('%Y-%m-%dT%H:%M:%fZ','now',?))",
                            (self.ctx.org_id, self.ctx.user_id, key, "POST", "/api/synthetic", "h-" + key,
                             f"-{days} days"))

    def keys(self):
        return {r["key"] for r in self.db.all("SELECT key FROM idempotency_keys WHERE org_id=?", (self.ctx.org_id,))}

    def deletes_during(self, fn):
        seen = []
        self.db.conn.set_trace_callback(seen.append)
        try:
            fn()
        finally:
            self.db.conn.set_trace_callback(None)
        return [s for s in seen if s.lstrip().upper().startswith("DELETE FROM IDEMPOTENCY_KEYS")]

    def test_fresh_requests_do_not_each_scan_the_table(self):
        deletes = self.deletes_during(lambda: [self.run_once(f"k{n}") for n in range(6)])
        self.assertLessEqual(len(deletes), 1, deletes)                  # was one per request
        self.assertEqual(self.keys(), {f"k{n}" for n in range(6)})

    def test_an_old_record_goes_on_the_first_request_and_a_fresh_one_stays(self):
        self.old_record("old", 8)
        self.old_record("recent", 6)
        self.run_once("now")
        self.assertEqual(self.keys(), {"recent", "now"})

    def test_after_an_hour_the_next_request_cleans_again(self):
        self.run_once("first")                                           # the hour starts here
        self.old_record("old", 8)
        self.run_once("second")
        self.assertIn("old", self.keys())                                # inside the hour: not looked at
        with patch.object(idempotency, "PRUNE_EVERY_SECONDS", 0):
            self.run_once("third")
        self.assertNotIn("old", self.keys())

    def test_the_daily_clean_up_does_it_whatever_the_hour(self):
        self.run_once("first")
        self.old_record("old", 8)
        self.old_record("older", 30)
        out = retention.clean(self.db)
        self.assertEqual(out["retry_cache"], 2)
        self.assertEqual(self.keys(), {"first"})

    def test_a_stored_answer_is_still_replayed_and_the_action_runs_once(self):
        ran = []
        first = self.run_once("same", ran)
        again = self.run_once("same", ran)
        self.assertEqual((first[:2], first[2], again[2], ran), (again[:2], False, True, ["same"]))

    def test_a_rolled_back_request_leaves_the_table_as_it_was(self):
        self.old_record("recent", 6)

        def boom():
            raise RuntimeError("synthetic failure after the posting")
        with self.assertRaises(RuntimeError):
            idempotency.run(self.db, self.ctx, key="lost", method="POST", path="/api/synthetic",
                            request_hash="h", ttl_days=7, action=boom)
        self.assertEqual(self.keys(), {"recent"})

    def test_two_databases_do_not_share_the_hour(self):
        from app.core import accounts
        from app.db import Database
        self.run_once("first")                                           # this database was just cleaned
        other = Database(self.tmp / "other.db")
        try:
            other.migrate()
            accounts.create_organization(other, name="صيدلية أخرى", owner_username="owner",
                                         owner_full_name="Owner", owner_password="Owner-Pass-2026")
            ctx = accounts.resolve_session(other, accounts.login(other, "owner", "Owner-Pass-2026")["token"])
            with other.transaction():
                other.execute("INSERT INTO idempotency_keys (org_id, user_id, key, method, path, request_hash, status,"
                              " response, created_at) VALUES (?,?,'old','POST','/api/synthetic','h',200,'{}',"
                              " strftime('%Y-%m-%dT%H:%M:%fZ','now','-9 days'))", (ctx.org_id, ctx.user_id))
            idempotency.run(other, ctx, key="now", method="POST", path="/api/synthetic", request_hash="h2",
                            ttl_days=7, action=lambda: (200, "{}"))
            self.assertEqual([r["key"] for r in other.all("SELECT key FROM idempotency_keys WHERE org_id=?", (ctx.org_id,))],
                             ["now"])
        finally:
            other.close()
