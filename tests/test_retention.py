"""Cleanup keeps the books, removes only old notifications and sessions."""
import sqlite3

from app.core import notify, retention
from tests.helpers import DBTestCase
from tests.test_api import APICase


class TestRetention(DBTestCase):
    def test_old_notifications_go_recent_stay_protected(self):
        with self.db.transaction():
            notify.emit(self.db, self.ctx, "x", "info", "جديد")
            old = notify.emit(self.db, self.ctx, "x", "info", "قديم")
        # make one old (bypassing the no-update trigger, as only a test can)
        self.db.conn.execute("DROP TRIGGER notifications_no_update")
        with self.db.transaction():
            self.db.execute("UPDATE notifications SET created_at='2020-01-01T00:00:00.000000Z' WHERE id=?", (old,))
        r = retention.clean(self.db)
        self.assertEqual(r["notifications"], 1)
        self.assertEqual([n["title"] for n in notify.listing(self.db, self.ctx)["items"]], ["جديد"])
        with self.assertRaises(sqlite3.DatabaseError):                     # recent: still append-only
            with self.db.transaction():
                self.db.execute("DELETE FROM notifications")

    def test_sessions_and_sizes(self):
        with self.db.transaction():
            self.db.execute("UPDATE sessions SET expires_at='2020-01-01T00:00:00Z'")
        self.assertGreaterEqual(retention.clean(self.db)["sessions"], 1)
        z = retention.sizes(self.db, self.tmp / "pharmacy.db", self.tmp / "up", self.tmp / "bk")
        self.assertIn("sales", z["rows"])


class TestStorageAPI(APICase):
    def test_owner_sees_storage(self):
        self.http_login()
        r = self.c.get("/api/system/storage")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertGreater(r.json()["db"], 0)


class TestAuditList(DBTestCase):
    def test_owner_reads_log_with_labels(self):
        from app.core import audit
        from app.sales.service import local_today
        from app.catalog import service as cat
        cat.create_item(self.db, self.ctx, {"name_en": "X 1mg", "public_price": 100})
        t = local_today().isoformat()
        r = audit.listing(self.db, self.ctx, t, t, group="item")
        self.assertEqual(r["rows"][0]["label"], "صنف جديد")
        with self.assertRaises(Exception):
            audit.listing(self.db, self.ctx, t, t, group="nope")
