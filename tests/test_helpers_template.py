"""The test database comes from a migrated template copied per test (tests/helpers.py). These tests pin that
the copy is the database the program itself would build, that every test still gets its own file, pharmacy and
ids, and that the template is never written to by a test."""
import hashlib
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.db import Database
from tests import helpers
from tests.helpers import DBTestCase, fresh_database


def dump(path) -> dict:
    """Schema and every row of every table, as text."""
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        out = {"schema": sorted((r[0] or "", r[1] or "", r[2] or "") for r in
                                c.execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")),
               "user_version": c.execute("PRAGMA user_version").fetchone()[0],
               "journal_mode": c.execute("PRAGMA journal_mode").fetchone()[0]}
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
            cols = [r[1] for r in c.execute(f'PRAGMA table_info("{name}")')]
            # the moment a migration was applied is the only value that differs between two builds
            keep = [k for k in cols if not (name == "schema_migrations" and k == "applied_at")]
            out[name] = sorted(repr(r) for r in c.execute(f'SELECT {", ".join(chr(34) + k + chr(34) for k in keep)} FROM "{name}"'))
        return out
    finally:
        c.close()


class TestTemplate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pharm-test-tpl-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_copy_is_the_database_the_program_builds(self):
        direct = Database(self.tmp / "direct.db")
        direct.migrate()
        direct.close()
        copied = fresh_database(self.tmp / "copied.db")
        copied.close()
        a, b = dump(self.tmp / "direct.db"), dump(self.tmp / "copied.db")
        self.assertEqual(set(a), set(b))
        for key in a:
            self.assertEqual(a[key], b[key], key)
        self.assertIn("schema_migrations", a)
        self.assertGreater(len(a["schema_migrations"]), 30)

    def test_migrate_on_the_copy_applies_nothing(self):
        db = fresh_database(self.tmp / "copied.db")
        try:
            before = db.one("SELECT count(*) n FROM schema_migrations")["n"]
            self.assertEqual(db.migrate(), [])
            self.assertEqual(db.one("SELECT count(*) n FROM schema_migrations")["n"], before)
        finally:
            db.close()

    def test_a_test_never_writes_to_the_template(self):
        template = helpers._migrated_template()
        digest = hashlib.sha256(template.read_bytes()).hexdigest()
        db = fresh_database(self.tmp / "copied.db")
        try:
            with db.transaction():
                db.execute("CREATE TABLE scratch (x)")
                db.execute("INSERT INTO scratch VALUES (1)")
        finally:
            db.close()
        self.assertEqual(hashlib.sha256(template.read_bytes()).hexdigest(), digest)
        self.assertFalse(template.with_name(template.name + "-wal").exists())
        other = fresh_database(self.tmp / "other.db")
        try:
            self.assertIsNone(other.one("SELECT name FROM sqlite_master WHERE name='scratch'"))
        finally:
            other.close()

    def test_the_template_is_built_once(self):
        self.assertEqual(helpers._migrated_template(), helpers._migrated_template())


class TestEachTestIsItsOwn(DBTestCase):
    seen: list = []

    def remember(self):
        self.seen.append((str(self.db.path), self.ctx.org_id, self.ctx.user_id))

    def test_one(self):
        self.remember()
        self.assertEqual(self.db.one("SELECT count(*) n FROM organizations")["n"], 1)
        self.assertEqual(self.db.one("SELECT id FROM organizations")["id"], self.ctx.org_id)

    def test_two(self):
        self.remember()
        self.assertEqual(self.db.one("SELECT count(*) n FROM organizations")["n"], 1)

    def test_zz_the_three_did_not_share_a_file_a_pharmacy_or_a_user(self):
        self.remember()
        self.assertEqual(len(self.seen), 3)
        for k in range(3):
            self.assertEqual(len({s[k] for s in self.seen}), 3, k)
