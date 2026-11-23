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

    def build(self, *, deps_files=None, tamper_code=False, key=None, channel="main", commit_time=2000, startup_proof=False):
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
        raw = su.make_manifest(channel=channel, sha=SHA_NEW, commit_time=commit_time, version="0.5.0",
                               python=su.python_tag(), platform=su.platform_tag(), code_zip=code,
                               deps_zip=deps_zip, deps=ident,
                               migrations=[p.stem for p in (self.new / "app" / "migrations").glob("*.sql")])
        m = json.loads(raw)
        if startup_proof:
            su.add_startup_proof(code, m, self.priv)
            m["code"].update(size=code.stat().st_size, sha256=releases.file_sha256(code))
            raw = json.dumps(m).encode("utf-8")
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
        release = releases.release_path(self.home, p["release"])
        probe = ("import runpy, sys; runpy.run_path(sys.argv[1], run_name='probe'); "
                 "import smartprobe; print(smartprobe.VALUE)")
        def run_probe(env):
            return subprocess.run([sys.executable, "-c", probe, str(release / "manage.py")], cwd=str(release),
                                  capture_output=True, text=True, env=env)
        base_env = {k: v for k, v in os.environ.items() if k != "PHARM_DEPS_DIR"}
        self.assertNotEqual(run_probe(base_env).returncode, 0)                    # not selected: installed set only
        self.assertEqual(run_probe({**base_env, "PHARM_DEPS_DIR": str(folder)}).stdout.strip(), "42")
        su.select_code_only(self.s, self.legacy, self.backups)                     # selected: from its state entry
        self.assertEqual(run_probe(base_env).stdout.strip(), "42")
        (folder / "smartprobe" / "__init__.py").write_text("VALUE = 0\n")
        with self.assertRaises(UpdateError):
            su.verify_deps(folder)

    def test_unsafe_package_member_is_refused(self):
        with open(self.new / "requirements.txt", "a", encoding="utf-8") as f:
            f.write("# smart update probe\n")
        self.build(deps_files={"../evil.py": "x"})
        with self.assertRaisesRegex(UpdateError, "غير آمن"):
            self.prepare()

    def test_every_smart_batch_needs_the_new_launcher(self):
        (self.home / "bootstrap.json").unlink()                                     # old installed launcher
        self.build()                                                                # code only, too
        with self.assertRaisesRegex(UpdateError, "المشغّل"):
            self.prepare()
        self.assertFalse((self.home / "downloads").exists())


class TestApply(SmartUpdateBase):
    def test_prepared_release_signature_is_checked_before_activation(self):
        self.build()
        prepared = self.prepare()
        proof = self.home / "proofs" / f"{prepared['release']}.json"
        releases.atomic_json(proof, {"manifest": "{}", "signature": "invalid"})
        with self.assertRaises(UpdateError):
            su.select_code_only(self.s, self.legacy, self.backups)
        self.assertFalse(releases.read_state(self.home).get("active"))

    def test_installed_launcher_checks_publisher_proof_and_exact_release_files(self):
        self.build()
        p = self.prepare()
        su.select_code_only(self.s, self.legacy, self.backups)
        candidate = releases.verified_program(self.legacy, self.home)
        self.assertEqual(candidate.name, p["release"])
        (candidate / "extra.txt").write_text("Synthetic unexpected file", encoding="utf-8")
        with self.assertRaises(releases.ReleaseError):
            releases.verified_program(self.legacy, self.home)

    def test_startup_proof_is_required_when_the_old_updater_did_not_save_a_manifest(self):
        self.build(startup_proof=True)
        p = self.prepare()
        su.select_code_only(self.s, self.legacy, self.backups)
        (self.home / "proofs" / f"{p['release']}.json").unlink()
        candidate = releases.verified_program(self.legacy, self.home)
        proof = candidate / su.STARTUP_PROOF
        value = json.loads(proof.read_text(encoding="utf-8"))
        value["signature"] = "invalid"
        proof.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(releases.ReleaseError):
            releases.verified_program(self.legacy, self.home)

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

    def test_code_only_apply_selects_with_a_backup(self):
        self.build()
        p = self.prepare()
        before = self.live_bytes()
        su.apply_prepared(self.s, self.legacy, p["release"], self.backups)
        st = releases.read_state(self.home)
        self.assertEqual((st["active"]["id"], st["pending"]), (p["release"], True))
        self.assertEqual(self.live_bytes(), before)
        self.assertEqual(releases.read_journal(self.home), {})                      # no database step
        self.assertTrue(list(self.backups.glob("*pre-update*.zip")))

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

    def test_audit_import_position_rolls_back_with_the_database(self):
        """Codex P1 on 6f3d1f1: events imported into a database that is then put back are imported again."""
        from app.db import Database
        releases.log_event(self.home, step="apply", ok=True)
        before = self.live_bytes()                                                 # the database put back later
        db = Database(self.s.db_path)
        self.assertEqual(su.import_log(db, self.data), 1)
        db.close()
        self.s.db_path.write_bytes(before)                                         # undo of a failed first start
        releases.log_event(self.home, step="rollback", ok=True)
        db = Database(self.s.db_path)
        try:
            self.assertEqual(su.import_log(db, self.data), 2)                      # apply again, then rollback
            steps = [r["action"] for r in db.all("SELECT action FROM audit_log WHERE action LIKE 'system.update.%'"
                                                   " ORDER BY seq")]
            self.assertEqual(steps, ["system.update.apply", "system.update.rollback"])
        finally:
            db.close()

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

    def test_due_only_for_the_prepared_batch_and_when_quiet(self):
        from datetime import datetime, timedelta, timezone
        db = self.db()
        releases.atomic_json(self.home / "prepared.json", {"release": "c" * 64, "needs_db": True})
        self.assertIsNone(su.due(self.s, db))                                      # nothing requested
        su.request(self.data, "now", "owner")
        self.assertIsNone(su.due(self.s, db))                                      # init just wrote
        later = datetime.now(timezone.utc) + timedelta(minutes=5)
        with patch.object(su, "datetime", wraps=datetime) as fake:
            fake.now.return_value = later
            self.assertEqual(su.due(self.s, db)["needs_db"], True)
        releases.atomic_json(self.home / "prepared.json", {"release": "d" * 64})
        self.assertIsNone(su.due(self.s, db))                                      # request for another batch

    def test_trigger_rechecks_the_gate_and_never_cuts_a_request(self):
        """Codex P1 on #386: the last check happens under the write lock, with no request in flight."""
        from datetime import datetime, timedelta, timezone
        from unittest.mock import Mock
        db = self.db()
        releases.atomic_json(self.home / "prepared.json", {"release": "c" * 64, "needs_db": False})
        su.request(self.data, "now", "owner")
        later = datetime.now(timezone.utc) + timedelta(minutes=5)
        leave = Mock()
        with patch.object(su, "datetime", wraps=datetime) as fake:
            fake.now.return_value = later
            self.assertFalse(su.trigger(self.s, db, in_flight=lambda: 1, exit=leave))     # a request running
            leave.assert_not_called()
            self.assertFalse((self.home / "apply-go.json").exists())
            seen = []
            def locked_check():
                seen.append(db._write_lock._is_owned())
                return 0
            su.trigger(self.s, db, in_flight=locked_check, exit=leave)
        leave.assert_called_once_with(75)
        self.assertEqual(seen, [True])
        self.assertEqual(json.loads((self.home / "apply-go.json").read_text())["release"], "c" * 64)

    def test_code_only_batch_is_also_applied_with_the_program_stopped(self):
        (self.home / "bootstrap.json").unlink()
        with patch.object(su, "due", return_value={"release": "c" * 64, "when": "now", "needs_db": False}):
            self.assertFalse(su.trigger(self.s, self.db(), in_flight=lambda: 0, exit=lambda c: None))

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


from tests.test_api import APICase, H  # noqa: E402


class TestSmartUpdateAPI(APICase):
    def post(self, body):
        return self.c.post("/api/system/update", headers=H, json=body)

    def test_schedule_needs_a_prepared_batch(self):
        self.http_login()
        r = self.post({"action": "schedule", "when": "now"})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("ما اتجهزش", r.text)

    def test_prepare_failure_is_audited_and_changes_nothing(self):
        from unittest import mock
        self.http_login()
        failed = mock.Mock(returncode=1, stdout="", stderr="التحديث فشل: مفيش إنترنت دلوقتي")
        with mock.patch("subprocess.run", return_value=failed) as run:
            r = self.post({"action": "prepare"})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("مفيش إنترنت", r.text)
        self.assertIn("--prepare", run.call_args[0][0])
        self.assertEqual(self.db.one("SELECT count(*) FROM audit_log WHERE action='system.update.prepare'")[0], 1)

    def test_schedule_and_cancel_are_audited(self):
        import release_bootstrap as rb
        self.http_login()
        home = su.home_of(self.s.data_dir)
        rb.atomic_json(home / "prepared.json", {"release": "c" * 64, "needs_db": True})
        r = self.post({"action": "schedule", "when": "after_close"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(su.pending_request(self.s.data_dir)["when"], "after_close")
        self.assertEqual(self.post({"action": "schedule", "when": "midnight"}).status_code, 422)
        self.assertEqual(self.post({"action": "cancel"}).status_code, 200)
        self.assertEqual(su.pending_request(self.s.data_dir), {})
        actions = {r["action"] for r in self.db.all("SELECT action FROM audit_log")}
        self.assertTrue({"system.update.schedule", "system.update.cancel"} <= actions)

    def test_status_reports_smart_state_without_a_key(self):
        self.http_login()
        st = self.c.get("/api/system/update").json()
        self.assertIn("smart", st)
        self.assertFalse(st["smart"]["enabled"])            # no trusted key yet: today's button

    def test_signed_out_cannot_schedule(self):
        self.assertEqual(self.post({"action": "cancel"}).status_code, 401)
        self.assertEqual(self.post({"action": "prepare"}).status_code, 401)


class TestBundleBuilder(unittest.TestCase):
    def test_ci_bundle_is_signed_verifiable_and_fails_closed_without_a_key(self):
        import base64
        from scripts.release import build_bundle
        if subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True).returncode:
            self.skipTest("not a git checkout")
        priv, pub = update_sign.generate()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            with patch.dict(os.environ, {"UPDATE_SIGNING_KEY": ""}):
                self.assertEqual(build_bundle.main(["--channel", "main", "--out", str(out)]), 1)
            self.assertFalse(out.exists())                                         # no key: nothing published
            site = Path(tmp) / "site"
            (site / "pkg").mkdir(parents=True)
            (site / "pkg" / "__init__.py").write_text("X = 1\n")
            with patch.dict(os.environ, {"UPDATE_SIGNING_KEY": priv}), \
                    patch.object(update_sign, "trusted_keys", return_value=[base64.b64decode(pub)]):
                self.assertEqual(build_bundle.main(["--channel", "develop", "--out", str(out),
                                                    "--site-packages", str(site)]), 0)
            m = su.parse_manifest((out / su.MANIFEST).read_bytes(), (out / su.SIGNATURE).read_text(),
                                  [base64.b64decode(pub)])
            self.assertEqual(m["channel"], "develop")
            self.assertIn("0001_core", m["migrations"])
            with zipfile.ZipFile(out / m["code"]["name"]) as z:
                self.assertIn("program/manage.py", z.namelist())
            with zipfile.ZipFile(out / m["deps"]["name"]) as z:
                self.assertEqual(z.namelist(), ["pkg/__init__.py"])
            with patch.dict(os.environ, {"UPDATE_SIGNING_KEY": update_sign.generate()[0]}), \
                    patch.object(update_sign, "trusted_keys", return_value=[base64.b64decode(pub)]):
                with self.assertRaises(update_sign.SignatureError):                # secret != committed key
                    build_bundle.main(["--channel", "main", "--out", str(Path(tmp) / "o2")])


class TestGitHubSource(unittest.TestCase):
    def headers_sent(self, token):
        from unittest.mock import MagicMock
        src = su.GitHubSource("o/r", "main", token)
        seen = {}
        def fake_open(req, timeout=None):
            seen.update({k.lower(): v for k, v in req.header_items()})
            resp = MagicMock()
            resp.__enter__.return_value.read.return_value = b'{"assets": []}'
            return resp
        src._open = fake_open
        src.assets()
        return seen

    def test_no_key_means_anonymous_not_bearer_none(self):
        """Codex P2 on 9b006bc: the repository is public; read_token() returns None without a key file."""
        self.assertNotIn("authorization", self.headers_sent(None))
        self.assertEqual(self.headers_sent("k")["authorization"], "Bearer k")
