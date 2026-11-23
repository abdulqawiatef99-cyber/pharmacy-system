"""Part 3 item 1 (owner 20/11 23:18: this must be the LAST installer): end-to-end rehearsal of the «تحديث»
button on a test channel (a folder bundle, never the pharmacy PC). One signed batch carries all three kinds of
change an installer used to be needed for: a database migration, a package change and a change to the
updater itself. It goes the whole way as on the PC: prepare (rehearsal on a snapshot) -> «حدّث دلوقتي» ->
trigger (exit 75) -> the launcher's settle() runs the real `manage.py update --apply-prepared` with the
program stopped -> journal swap -> supervised first start. A second batch whose first start fails must
bring the old database and the old code back on its own. Runs on Windows CI too (Windows file semantics)."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from unittest.mock import patch

import launcher
import release_bootstrap as releases
from app.core import smart_update as su
from tests.test_smart_update import SHA_NEW, SmartUpdateBase

MARK = "\nREHEARSAL_UPDATER = 2   # the updater itself changed in this batch\n"


class SmartUpdateLauncher(SmartUpdateBase):
    def press_update_now(self):
        """The owner presses «حدّث دلوقتي»; the program leaves for the launcher when quiet (exit 75 mocked)."""
        from app.db import Database
        su.request(self.data, "now", "owner")
        db = Database(self.s.db_path)
        left = []
        try:
            later = datetime.now(timezone.utc) + timedelta(minutes=5)
            with patch.object(su, "datetime", wraps=datetime) as fake:
                fake.now.return_value = later
                self.assertTrue(su.trigger(self.s, db, in_flight=lambda: 0, exit=left.append) or left)
        finally:
            db.close()
        self.assertEqual(left, [75])
        self.assertTrue((self.home / "apply-go.json").exists())

    def launcher_settles(self):
        """What the installed launcher does before it starts any program (stage 0)."""
        env = {**os.environ, "PHARM_DATA_DIR": str(self.data), "PYTHONUTF8": "1", "PHARM_LAUNCHER": "1",
               "PHARM_BOOTSTRAP": str(releases.BOOTSTRAP)}
        for k in ("PHARM_DB_PATH", "PHARM_UPLOAD_DIR", "PHARM_BACKUP_DIR", "PHARM_DEPS_DIR"):
            env.pop(k, None)
        with patch.object(launcher, "ROOT", self.legacy):
            self.assertTrue(launcher.settle(self.home, self.app_home, env, 0))
        self.assertFalse((self.home / "apply-go.json").exists())            # one shot

    def launcher_starts(self, *, healthy: bool):
        """The installed launcher's main(): it applies (the real `--apply-prepared`), swaps, starts the program
        and supervises its first start; on a failed start it rolls back and starts the previous version
        itself (Codex P2 on #392). Only the server process is faked: healthy or not."""
        from unittest.mock import Mock
        procs = []
        real_popen = subprocess.Popen
        def popen(cmd, *a, **kw):
            if "serve" not in cmd:                 # the real apply (`--apply-prepared`) and its checks run for real
                return real_popen(cmd, *a, **kw)
            p = Mock()
            p.wait.return_value = 0
            p.poll.return_value = None if healthy or procs else 1
            procs.append((cmd, kw.get("env", {})))
            return p
        states = []
        def ready(p, boot_id, timeout=60):
            states.append(releases.read_state(self.home).get("active", {}) or {})
            return healthy
        with patch.object(launcher, "ROOT", self.legacy), patch.object(launcher, "home", return_value=self.app_home), \
                patch.object(launcher, "running", return_value=False), \
                patch.object(launcher, "candidate_ready", side_effect=ready), \
                patch.object(launcher.threading, "Thread"), \
                patch.object(launcher.subprocess, "Popen", side_effect=popen):
            self.assertEqual(launcher.main(), 0)
        self.assertFalse((self.home / "apply-go.json").exists())            # one shot
        return procs

    def tables(self):
        con = sqlite3.connect(self.s.db_path)
        try:
            return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()


class TestLastInstallerRehearsal(SmartUpdateLauncher):
    def test_migration_package_and_updater_change_through_the_button_then_auto_rollback(self):
        (self.app_home / "logs").mkdir(exist_ok=True)
        # --- batch 1: a migration, a package change and a change to the updater
        self.add_migration()
        with open(self.new / "requirements.txt", "a", encoding="utf-8") as f:
            f.write("# rehearsal package\n")
        (self.new / "app" / "rehearsal_pkg_check.py").write_text("import rehearsalpkg\nVALUE = rehearsalpkg.VALUE\n")
        with open(self.new / "app" / "core" / "smart_update.py", "a", encoding="utf-8") as f:
            f.write(MARK)
        m = self.build(deps_files={"rehearsalpkg/__init__.py": "VALUE = 7\n"})
        before = self.s.db_path.read_bytes()
        p = su.prepare(self.s, su.FolderSource(self.bundle), legacy=self.legacy)
        self.assertTrue(p["needs_db"])
        self.assertEqual(p["deps"], m["deps"]["id"])
        self.assertEqual(self.s.db_path.read_bytes(), before)               # prepare only reads the live file
        self.press_update_now()
        procs = self.launcher_starts(healthy=True)
        self.assertEqual(len(procs), 1)                                     # the new version, supervised
        self.assertEqual(procs[0][1].get("PHARM_BOOTSTRAP"), str(releases.BOOTSTRAP))
        st = releases.read_state(self.home)
        self.assertEqual((st["active"]["id"], st["pending"], st["active"]["deps"]),
                         (p["release"], False, m["deps"]["id"]))                 # confirmed by the launcher
        self.assertIn("smart_update_probe", self.tables())                  # the migrated copy is live
        self.assertEqual(releases.read_journal(self.home)["state"], "done")
        release = releases.release_path(self.home, p["release"])
        self.assertIn("REHEARSAL_UPDATER = 2", (release / "app" / "core" / "smart_update.py").read_text())
        probe = ("import runpy, sys; runpy.run_path(sys.argv[1], run_name='probe'); "
                 "from app import rehearsal_pkg_check as c; print(c.VALUE)")
        env = {k: v for k, v in os.environ.items() if k != "PHARM_DEPS_DIR"}
        r = subprocess.run([sys.executable, "-c", probe, str(release / "manage.py")], cwd=str(release),
                           capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.stdout.strip(), "7", r.stderr)                    # the new package set is used
        r = subprocess.run([sys.executable, str(release / "manage.py"), "update", "--self-check"], cwd=str(release),
                           capture_output=True, text=True, env=env, timeout=120)
        self.assertIn("SELF-CHECK OK", r.stdout, r.stderr)                   # the new updater works
        events = [json.loads(x) for x in (self.home / "update-log.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertIn(("apply", True), {(e.get("step"), e.get("ok")) for e in events})   # the real apply ran
        self.assertIn(("swap", True), {(e.get("step"), e.get("ok")) for e in events})
        self.assertTrue(list(self.backups.glob("*pre-update*.zip")))       # verified backup first
        self.assertIn("SMART-APPLY", (self.app_home / "logs" / "update.log").read_text(encoding="utf-8"))
        good = self.s.db_path.read_bytes()

        # --- batch 2: a later batch whose first start fails -> old database and code come back by themselves
        (self.new / "app" / "migrations" / "0998_rehearsal_two.sql").write_text(
            "CREATE TABLE rehearsal_two (id INTEGER PRIMARY KEY);\n", encoding="utf-8")
        for f in self.bundle.iterdir():
            f.unlink()
        with patch("tests.test_smart_update.SHA_NEW", "c" * 40):            # a later commit of the channel
            self.build(deps_files={"rehearsalpkg/__init__.py": "VALUE = 7\n"}, commit_time=3000)
        raw = json.loads((self.bundle / su.MANIFEST).read_bytes())
        self.assertNotEqual(raw["sha"], SHA_NEW)
        p2 = su.prepare(self.s, su.FolderSource(self.bundle), legacy=self.legacy)
        self.assertTrue(p2["needs_db"])
        self.press_update_now()
        procs = self.launcher_starts(healthy=False)                          # its first start fails
        self.assertEqual(len(procs), 2)                                      # the launcher started batch 1 again
        self.assertNotIn("rehearsal_two", self.tables())
        self.assertEqual(self.s.db_path.read_bytes(), good)
        st = releases.read_state(self.home)
        self.assertEqual((st["active"]["id"], st["pending"]), (p["release"], False))   # batch 1's code again
        self.assertEqual(releases.read_journal(self.home)["state"], "rolled_back")


class TestPurchaseVATWalletMigration(SmartUpdateLauncher):
    legacy_pending = ("0045_purchase_vat_wallet_returns",)

    def historical_rows(self):
        con = sqlite3.connect(self.s.db_path)
        try:
            return (
                con.execute("SELECT id, public_price FROM batches ORDER BY id").fetchall(),
                con.execute("SELECT id, total, refund_method FROM sale_returns ORDER BY id").fetchall(),
                con.execute("SELECT id, batch_id, qty_base, returned_qty FROM sale_allocations ORDER BY id").fetchall(),
                con.execute("SELECT id, line_total, invoice_share FROM sale_lines ORDER BY id").fetchall(),
                con.execute("SELECT item_id, batch_key, qty FROM stock_balances ORDER BY item_id, batch_key").fetchall(),
            )
        finally:
            con.close()

    def test_existing_database_takes_the_QA_migration_through_the_update_button(self):
        from decimal import Decimal
        from app.catalog import service as catalog
        from app.core import accounts
        from app.db import Database
        from app.inventory import service as inventory

        db = Database(self.s.db_path)
        try:
            token = accounts.login(db, "owner", "Smoke-Pass-2026")["token"]
            ctx = accounts.resolve_session(db, token)
            item = catalog.create_item(db, ctx, {"name_en": "QA synthetic box", "public_price": 10000})
            with db.transaction():
                exp = (datetime.now(timezone.utc).date() + timedelta(days=700)).isoformat()
                batch = inventory.get_or_create_batch(db, ctx, item, "QA-LOT", exp)
                db.execute("UPDATE batches SET public_price=10000 WHERE id=? AND org_id=?", (batch, ctx.org_id))
                db.execute("INSERT INTO sales (id, org_id, branch_id, sale_no, status, subtotal, discount_total,"
                           " total, paid_card, returned_total, created_by, completed_at)"
                           " VALUES ('qa-sale',?,?,1,'completed',20000,2000,18000,18000,9000,?,"
                           " strftime('%Y-%m-%dT%H:%M:%fZ','now'))", (ctx.org_id, ctx.branch_id, ctx.user_id))
                db.execute("INSERT INTO sale_lines (id, sale_id, org_id, line_no, item_id, qty_base, line_total, invoice_share)"
                           " VALUES ('qa-line','qa-sale',?,1,?,2,18000,2000)", (ctx.org_id, item))
                db.execute("INSERT INTO sale_allocations (id, sale_line_id, batch_id, qty_base,"
                           " unit_price, returned_qty) VALUES ('qa-alloc','qa-line',?,2,10000,1)", (batch,))
                db.execute("INSERT INTO sale_returns (id, org_id, sale_id, total, refund_method, reason, created_by)"
                           " VALUES ('qa-return',?,'qa-sale',9000,'card','QA synthetic refund',?)",
                           (ctx.org_id, ctx.user_id))
                for quantity, kind in ((6, "opening"), (-2, "sale"), (1, "sale_return")):
                    inventory.post_movement(db, ctx, item_id=item, batch_id=batch, qty_delta=quantity,
                                            movement_type=kind, unit_cost=Decimal("20"), ref_id="qa-sale",
                                            ref_line_id="qa-line")
            self.assertFalse(db.one("SELECT version FROM schema_migrations WHERE version=?", self.legacy_pending))
        finally:
            db.close()

        before = self.historical_rows()
        before_bytes = self.live_bytes()
        (self.app_home / "logs").mkdir(exist_ok=True)
        manifest = self.build()
        self.assertIn(self.legacy_pending[0], manifest["migrations"])
        prepared = self.prepare()
        self.assertEqual(prepared["migrations"], list(self.legacy_pending))
        self.assertTrue(prepared["needs_db"])
        self.assertEqual(self.live_bytes(), before_bytes)

        self.press_update_now()
        self.assertEqual(len(self.launcher_starts(healthy=True)), 1)
        self.assertEqual(releases.read_state(self.home)["active"]["id"], prepared["release"])
        self.assertEqual(releases.read_journal(self.home)["state"], "done")
        self.assertTrue(list(self.backups.glob("*pre-update*.zip")))
        self.assertEqual(self.historical_rows(), before)
        db = Database(self.s.db_path)
        try:
            self.assertEqual(db.pending_migrations(), [])
            self.assertEqual(db.all("PRAGMA foreign_key_check"), [])
            self.assertIsNone(db.one("SELECT channel_id FROM sale_returns WHERE id='qa-return'")[0])
            self.assertIn("tax_included", {r["name"] for r in db.all("PRAGMA table_info(purchase_invoice_lines)")})
        finally:
            db.close()


class TestCleanInstallThenUpdate(TestLastInstallerRehearsal):
    """Owner 20/11 23:54 (D): a friend installs the same installer as a model customer, with no data at all.
    The installed program starts on an empty data folder, the first-run screen creates a new pharmacy, and the
    button then delivers a batch with a migration to that fresh pharmacy the same way."""

    def test_migration_package_and_updater_change_through_the_button_then_auto_rollback(self):
        pass                                     # the parent's scenario; not repeated here

    def serve_fresh(self):
        import socket
        import time
        import urllib.request
        for f in list(self.data.iterdir()):      # nothing from setUp's init: a PC that never had the program
            if f.is_file():
                f.unlink()
            else:
                import shutil
                shutil.rmtree(f)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        env = {**os.environ, "PHARM_DATA_DIR": str(self.data), "PYTHONUTF8": "1"}
        for k in ("PHARM_DB_PATH", "PHARM_UPLOAD_DIR", "PHARM_BACKUP_DIR", "PHARM_DEPS_DIR", "PHARM_LAUNCHER"):
            env.pop(k, None)
        proc = subprocess.Popen([sys.executable, str(self.legacy / "manage.py"), "serve", "--port", str(port),
                                 "--host", "127.0.0.1"], cwd=str(self.app_home), env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (proc.poll() is None and proc.terminate(), proc.wait(timeout=30)))
        base = f"http://127.0.0.1:{port}"
        for _ in range(240):
            try:
                with urllib.request.urlopen(base + "/api/health", timeout=2) as r:
                    if r.status == 200:
                        break
            except OSError:
                time.sleep(0.25)
        else:
            self.fail("the fresh install did not start")
        return proc, base

    def test_empty_install_creates_a_pharmacy_then_takes_a_database_update(self):
        import http.cookiejar
        import urllib.request
        proc, base = self.serve_fresh()
        jar = http.cookiejar.CookieJar()
        web = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        def call(method, path, body=None):
            req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body else None,
                                         headers={"Content-Type": "application/json", "X-Requested-With": "pharm"})
            with web.open(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"{}")
        self.assertTrue(call("GET", "/api/setup")[1]["needed"])                     # first run screen
        status, _ = call("POST", "/api/setup", {"pharmacy": "صيدلية العميل النموذجي", "username": "owner",
                                                "password": "Model-Customer-2026"})
        self.assertEqual(status, 201)
        self.assertEqual(call("GET", "/api/me")[0], 200)                             # signed in
        self.assertFalse(call("GET", "/api/setup")[1]["needed"])
        proc.terminate()
        proc.wait(timeout=30)
        (self.app_home / "logs").mkdir(exist_ok=True)
        releases.write_capability(self.home)                 # what the installed launcher writes at each start
        self.add_migration()
        self.build()
        p = su.prepare(self.s, su.FolderSource(self.bundle), legacy=self.legacy)
        self.assertTrue(p["needs_db"])
        self.press_update_now()
        self.launcher_settles()
        self.assertIn("smart_update_probe", self.tables())
        con = sqlite3.connect(self.s.db_path)
        try:
            self.assertEqual(con.execute("SELECT name FROM organizations").fetchone()[0], "صيدلية العميل النموذجي")
        finally:
            con.close()
