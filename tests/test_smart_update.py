"""Smart update, stage 1, end to end on this program's own code and a synthetic pharmacy database:
signed bundle -> prepare (rehearsal on a snapshot) -> apply on a copy -> stage-0 swap -> undo.
No network: the bundle is read from a folder, exactly as from a flash drive."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from app import config
from app.core import smart_update as su
from app.core import update_sign
from app.core.updater import UpdateError
import release_bootstrap as releases

REPO = Path(__file__).resolve().parents[1]
PROGRAM_FILES = ["manage.py", "launcher.py", "release_bootstrap.py", "release_protocol.json", "requirements.txt"]
SHA_OLD, SHA_NEW = "a" * 40, "b" * 40


def copy_program(dest: Path) -> None:
    for name in PROGRAM_FILES:
        shutil.copy2(REPO / name, dest / name)
    shutil.copytree(REPO / "app", dest / "app", ignore=shutil.ignore_patterns("__pycache__"))
    (dest / "docs").mkdir()
    (dest / "docs" / "CHANGES_AR.md").write_text("# التغييرات\n- تغيير قديم\n", encoding="utf-8")


def zip_tree(src: Path, out: Path, prefix: str = "program/") -> None:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                z.write(p, prefix + p.relative_to(src).as_posix())


class SmartUpdateBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.priv, cls.pub = update_sign.generate()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.legacy, self.new, self.bundle = base / "installed", base / "new", base / "bundle"
        self.app_home = base / "home"
        self.data = self.app_home / "data"
        for d in (self.legacy, self.new, self.bundle, self.data):
            d.mkdir(parents=True)
        copy_program(self.legacy)
        (self.legacy / "release_keys").mkdir()
        (self.legacy / "release_keys" / "test.pub").write_text(self.pub, encoding="ascii")
        copy_program(self.new)
        shutil.copytree(self.legacy / "release_keys", self.new / "release_keys")
        (self.new / "docs" / "CHANGES_AR.md").write_text(
            "# التغييرات\n- تغيير قديم\n- البحث بقى أسرع\n- عمود جديد للتجربة\n", encoding="utf-8")
        env = {**os.environ, "PHARM_DATA_DIR": str(self.data), "PYTHONUTF8": "1"}
        for k in ("PHARM_DB_PATH", "PHARM_UPLOAD_DIR", "PHARM_BACKUP_DIR", "PHARM_DEPS_DIR"):
            env.pop(k, None)
        subprocess.run([sys.executable, str(self.legacy / "manage.py"), "init", "--pharmacy", "صيدلية تجربة",
                        "--username", "owner", "--password", "Smoke-Pass-2026"], env=env, check=True,
                       capture_output=True, cwd=str(self.app_home))
        (self.data / "installed_version.json").write_text(json.dumps({"sha": SHA_OLD, "ref": "main"}))
        (self.data / "network.json").write_text('{"lan": false}')         # a setting in the data folder
        self.env = patch.dict(os.environ, {"PHARM_DATA_DIR": str(self.data)})
        self.env.start()
        self.addCleanup(self.env.stop)
        for k in ("PHARM_DB_PATH", "PHARM_UPLOAD_DIR", "PHARM_BACKUP_DIR", "PHARM_DEPS_DIR"):
            os.environ.pop(k, None)
        self.s = config.load()
        self.home = su.home_of(self.data)
        releases.write_capability(self.home)
        self.backups = self.app_home / "backups"

    def add_migration(self, sql="CREATE TABLE smart_update_probe (id INTEGER PRIMARY KEY);\n"):
        (self.new / "app" / "migrations" / "0999_smart_update_probe.sql").write_text(sql, encoding="utf-8")

    def build(self, *, deps_files=None, tamper_code=False, key=None, channel="main"):
        code = self.bundle / "code.zip"
        zip_tree(self.new, code)
        req = (self.new / "requirements.txt").read_bytes()
        ident = su.deps_id(req, su.python_tag(), su.platform_tag())
        deps_zip = None
        if deps_files is not None:
            deps_zip = self.bundle / "deps.zip"
            with zipfile.ZipFile(deps_zip, "w") as z:
                for name, body in deps_files.items():
                    z.writestr(name, body)
        raw = su.make_manifest(channel=channel, sha=SHA_NEW, commit_time=2000, version="0.5.0",
                               python=su.python_tag(), platform=su.platform_tag(), code_zip=code,
                               deps_zip=deps_zip, deps=ident,
                               migrations=[p.stem for p in (self.new / "app" / "migrations").glob("*.sql")])
        m = json.loads(raw)
        os.replace(code, self.bundle / m["code"]["name"])
        if deps_zip:
            os.replace(deps_zip, self.bundle / m["deps"]["name"])
        if tamper_code:
            with open(self.bundle / m["code"]["name"], "ab") as f:
                f.write(b"x")
        (self.bundle / su.MANIFEST).write_bytes(raw)
        (self.bundle / su.SIGNATURE).write_text(update_sign.sign(raw, key or self.priv), encoding="ascii")
        return m

    def prepare(self):
        return su.prepare(self.s, su.FolderSource(self.bundle), legacy=self.legacy)

    def tables(self):
        con = sqlite3.connect(self.s.db_path)
        try:
            return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()

    def live_bytes(self):
        return self.s.db_path.read_bytes()


class TestPrepare(SmartUpdateBase):
    def test_rehearsal_finds_the_migration_without_touching_live(self):
        self.add_migration()
        self.build()
        before = self.live_bytes()
        p = self.prepare()
        self.assertTrue(p["needs_db"])
        self.assertEqual(p["migrations"], ["0999_smart_update_probe"])
        self.assertEqual(p["notes"], ["البحث بقى أسرع", "عمود جديد للتجربة"])      # one batch, plain Arabic
        self.assertEqual(self.live_bytes(), before)
        self.assertNotIn("smart_update_probe", self.tables())
        self.assertEqual(releases.read_state(self.home), {})                       # nothing selected

    def test_code_only_batch_needs_no_database_step(self):
        self.build()
        p = self.prepare()
        self.assertFalse(p["needs_db"])

    def test_bad_signature_is_refused_before_any_download(self):
        self.build(key=update_sign.generate()[0])
        with self.assertRaisesRegex(UpdateError, "موقّع"):
            self.prepare()
        self.assertFalse((self.home / "downloads").exists())

    def test_tampered_code_is_refused(self):
        self.build(tamper_code=True)
        with self.assertRaisesRegex(UpdateError, "مطابق"):
            self.prepare()
        self.assertFalse((self.home / "prepared.json").exists())

    def test_other_channel_is_refused(self):
        self.build(channel="develop")
        with self.assertRaisesRegex(UpdateError, "قناة"):
            self.prepare()

    def test_broken_migration_fails_the_rehearsal_and_changes_nothing(self):
        self.add_migration("CREATE TABLE items (id INTEGER);\n")                    # already exists: fails
        self.build()
        before = self.live_bytes()
        with self.assertRaisesRegex(UpdateError, "فشلت|فشل"):
            self.prepare()
        self.assertEqual(self.live_bytes(), before)
        self.assertFalse((self.home / "prepared.json").exists())
        self.assertEqual(releases.read_state(self.home), {})

    def test_requirements_change_without_a_package_set_is_refused(self):
        with open(self.new / "requirements.txt", "a", encoding="utf-8") as f:
            f.write("# smart update probe\n")
        self.build()
        with self.assertRaisesRegex(UpdateError, "حزمة مكتبات"):
            self.prepare()

    def test_package_set_is_verified_and_used_by_the_candidate(self):
        with open(self.new / "requirements.txt", "a", encoding="utf-8") as f:
            f.write("# smart update probe\n")
        (self.new / "app" / "smartprobe_check.py").write_text("import smartprobe\nVALUE = smartprobe.VALUE\n")
        m = self.build(deps_files={"smartprobe/__init__.py": "VALUE = 42\n"})
        p = self.prepare()
        self.assertEqual(p["deps"], m["deps"]["id"])
        folder = su.deps_dir(self.home, p["deps"])
        su.verify_deps(folder)
        out = subprocess.run([sys.executable, "-c", "import app.smartprobe_check as c; print(c.VALUE)"],
                             cwd=str(releases.release_path(self.home, p["release"])), capture_output=True,
                             text=True, env={**os.environ, "PYTHONPATH": str(folder)})
        self.assertEqual(out.stdout.strip(), "42")
        (folder / "smartprobe" / "__init__.py").write_text("VALUE = 0\n")
        with self.assertRaises(UpdateError):
            su.verify_deps(folder)

    def test_unsafe_package_member_is_refused(self):
        with open(self.new / "requirements.txt", "a", encoding="utf-8") as f:
            f.write("# smart update probe\n")
        self.build(deps_files={"../evil.py": "x"})
        with self.assertRaisesRegex(UpdateError, "غير آمن"):
            self.prepare()

    def test_database_batch_needs_the_new_launcher(self):
        (self.home / "bootstrap.json").unlink()                                     # old installed launcher
        self.add_migration()
        self.build()
        with self.assertRaisesRegex(UpdateError, "المثبّت"):
            self.prepare()


class TestApply(SmartUpdateBase):
    def test_apply_migrates_a_copy_then_stage0_swaps_it_in_and_can_undo(self):
        self.add_migration()
        self.build()
        p = self.prepare()
        before = self.live_bytes()
        out = su.apply_prepared(self.s, self.legacy, p["release"], self.backups)
        self.assertEqual(self.live_bytes(), before)                                 # never migrated in place
        self.assertTrue(Path(out["backup"]).exists())
        st = releases.read_state(self.home)
        self.assertEqual(st["active"]["id"], p["release"])
        self.assertTrue(st["pending"])
        self.assertEqual(releases.resume_journal(self.home), "swapped")
        self.assertIn("smart_update_probe", self.tables())
        self.assertEqual((self.data / "network.json").read_text(), '{"lan": false}')
        releases.recover_failed_start(self.home, p["release"])                      # the new version did not start
        self.assertNotIn("smart_update_probe", self.tables())
        self.assertEqual(self.live_bytes(), before)
        self.assertIsNone(releases.read_state(self.home)["active"])

    def test_write_after_prepare_is_kept(self):
        """The apply copies the database as it is when the program stops, not the rehearsal snapshot."""
        self.add_migration()
        self.build()
        p = self.prepare()
        con = sqlite3.connect(self.s.db_path)
        con.execute("UPDATE organizations SET name_en='after prepare'")
        con.commit()
        con.close()
        su.apply_prepared(self.s, self.legacy, p["release"], self.backups)
        releases.resume_journal(self.home)
        con = sqlite3.connect(self.s.db_path)
        self.assertEqual(con.execute("SELECT name_en FROM organizations").fetchone()[0], "after prepare")
        con.close()

    def test_failed_check_on_the_copy_selects_nothing(self):
        self.add_migration()
        self.build()
        p = self.prepare()
        before = self.live_bytes()
        with patch.object(su, "check_copy", side_effect=UpdateError("فشل")):
            with self.assertRaises(UpdateError):
                su.apply_prepared(self.s, self.legacy, p["release"], self.backups)
        self.assertEqual(self.live_bytes(), before)
        self.assertEqual(releases.read_state(self.home), {})
        self.assertFalse((self.data / "update-candidate" / "pharmacy.db").exists())
        self.assertNotEqual(releases.read_journal(self.home).get("state"), "ready")

    def test_events_reach_the_audit_log(self):
        from app.core import audit
        from app.db import Database
        self.build()
        p = self.prepare()
        su.request(self.data, "after_close", "owner")
        db = Database(self.s.db_path)
        try:
            self.assertGreaterEqual(su.import_log(db, self.data), 2)
            actions = [r["action"] for r in db.all("SELECT action FROM audit_log ORDER BY seq")]
            self.assertIn("system.update.prepare", actions)
            self.assertIn("system.update.request", actions)
            self.assertEqual(su.import_log(db, self.data), 0)                       # each line once
            self.assertEqual(audit.verify_chain(db), [])
        finally:
            db.close()
        self.assertEqual(p["release"], su.pending_request(self.data)["release"])

    def test_manual_code_rollback_refused_after_a_database_update(self):
        self.add_migration()
        self.build()
        p = self.prepare()
        su.apply_prepared(self.s, self.legacy, p["release"], self.backups)
        releases.resume_journal(self.home)
        releases.confirm(self.home, p["release"])
        releases.finish_journal(self.home, p["release"])
        env = {**os.environ, "PYTHONUTF8": "1"}
        out = subprocess.run([sys.executable, str(releases.release_path(self.home, p["release"]) / "manage.py"),
                              "update", "--rollback"], env=env, capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("النسخة الاحتياطية", out.stderr)
        self.assertEqual(releases.read_state(self.home)["active"]["id"], p["release"])


class TestNeverDuringASale(SmartUpdateBase):
    def db(self):
        from app.db import Database
        d = Database(self.s.db_path)
        self.addCleanup(d.close)
        return d

    def test_quiet_program_may_update(self):
        from datetime import datetime, timedelta, timezone
        later = datetime.now(timezone.utc) + timedelta(minutes=5)
        self.assertIsNone(su.busy_reason(self.db(), "now", later))

    def test_recent_write_waits(self):
        self.assertIsNotNone(su.busy_reason(self.db(), "now"))                     # init just wrote

    def test_open_shift_waits_for_closing_time(self):
        from datetime import datetime, timedelta, timezone
        db = self.db()
        org = db.one("SELECT id FROM organizations")["id"]
        branch = db.one("SELECT id FROM branches WHERE org_id=?", (org,))["id"]
        with db.transaction():
            db.execute("INSERT INTO cash_shifts (id, org_id, branch_id, status, opening_cash, opened_by, opened_at)"
                       " VALUES ('s1', ?, ?, 'open', 0, 'u', '2026-11-20T10:00:00Z')", (org, branch))
        later = datetime.now(timezone.utc) + timedelta(minutes=5)
        self.assertEqual(su.busy_reason(db, "after_close", later), "مستني قفل الوردية")
        self.assertIsNone(su.busy_reason(db, "now", later))


if __name__ == "__main__":
    unittest.main()


from tests.helpers import DBTestCase  # noqa: E402


class TestSaleInProgress(DBTestCase):
    def test_draft_sale_with_lines_blocks_until_it_is_finished(self):
        from datetime import datetime, timedelta, timezone
        from decimal import Decimal
        from app.catalog import service as cat
        from app.inventory import service as inv
        from app.sales.cash import CashDrawer
        from app.sales.service import Sales
        CashDrawer(self.db).open(self.ctx, 50000)
        item = cat.create_item(self.db, self.ctx, {"name_en": "Probe tab", "public_price": 1000})
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, item, "B1",
                                        (datetime.now() + timedelta(days=400)).date().isoformat())
            inv.post_movement(self.db, self.ctx, item_id=item, qty_delta=5, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("5"))
        s = Sales(self.db)
        sid = s.create(self.ctx)
        later = datetime.now(timezone.utc) + timedelta(minutes=5)          # past the quiet minute
        self.assertIsNone(su.busy_reason(self.db, "now", later))              # empty draft: no sale yet
        s.add_line(self.ctx, sid, item, units=1)
        self.assertEqual(su.busy_reason(self.db, "now", later), "فاتورة بيع مفتوحة")
        self.assertIsNone(su.busy_reason(self.db, "now", later + timedelta(minutes=su.SALE_MINUTES)))
