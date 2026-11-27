"""Exercise the production migration order using an already attributed register refund."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from app.core import accounts
from app.db import Database, MIGRATIONS_DIR
from tests.helpers import OWNER_PW


class TestRefundUpgrade(TestCase):
    def test_register_refund_survives_the_mixed_returns_rebuild(self):
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            migrations = folder / "through-0050"
            migrations.mkdir()
            for migration in MIGRATIONS_DIR.glob("*.sql"):
                if int(migration.name[:4]) <= 50:
                    (migrations / migration.name).write_bytes(migration.read_bytes())
            db = Database(folder / "upgrade.db")
            self.addCleanup(db.close)
            db.migrate(migrations)
            accounts.create_organization(db, name="Synthetic", owner_username="fixture",
                                         owner_full_name="Fixture", owner_password=OWNER_PW)
            org, branch = tuple(db.one("SELECT org_id,id FROM branches"))
            user = db.one("SELECT id FROM users WHERE username='fixture'")[0]
            with db.transaction():
                db.execute("INSERT INTO registers (org_id,branch_id,id,name) VALUES (?,?,2,'Secondary')", (org, branch))
                db.execute("INSERT INTO cash_shifts(id,org_id,branch_id,opened_by,register_id,opening_cash,opened_at) VALUES ('secondary',?,?,?,2,0,'2026-01-01T00:00:00Z')",
                           (org, branch, user))
                db.execute("INSERT INTO sales(id,org_id,branch_id,status,created_by) VALUES ('sale',?,?,'completed',?)",
                           (org, branch, user))
                db.execute("INSERT INTO sale_returns (rowid,id,org_id,sale_id,total,refund_method,reason,created_by,"
                           " acting_shift_id,register_recorded) VALUES (7,'return',?,'sale',1000,'card','fixture',?,"
                           " 'secondary',1)", (org, user))
            db.migrate()
            row = db.one("SELECT rowid,total,acting_shift_id,register_recorded,part_of FROM sale_returns WHERE id='return'")
            self.assertEqual(tuple(row), (7, 1000, "secondary", 1, None))
            index = db.one("SELECT sql FROM sqlite_master WHERE type='index' AND name='ix_returns_acting_shift'")
            self.assertIn("WHERE acting_shift_id IS NOT NULL", index["sql"])
            self.assertEqual(db.all("PRAGMA foreign_key_check"), [])
            db.close()
