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


class TestLastInstallerRehearsal(SmartUpdateBase):
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

    def tables(self):
        con = sqlite3.connect(self.s.db_path)
        try:
            return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()

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
        self.launcher_settles()
        st = releases.read_state(self.home)
        self.assertEqual((st["active"]["id"], st["pending"], st["active"]["deps"]),
                         (p["release"], True, m["deps"]["id"]))
        self.assertIn("smart_update_probe", self.tables())                  # the migrated copy is live
        self.assertEqual(releases.read_journal(self.home)["state"], "swapped")
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
        releases.confirm(self.home, p["release"])                            # healthy first start
        releases.finish_journal(self.home, p["release"])
        self.assertEqual(releases.read_journal(self.home)["state"], "done")
        good = self.s.db_path.read_bytes()

        # --- batch 2: a later batch whose first start fails -> old database and code come back by themselves
        (self.new / "app" / "migrations" / "0998_rehearsal_two.sql").write_text(
            "CREATE TABLE rehearsal_two (id INTEGER PRIMARY KEY);\n", encoding="utf-8")
        for f in self.bundle.iterdir():
            f.unlink()
        with patch("tests.test_smart_update.SHA_NEW", "c" * 40):            # a later commit of the channel
            self.build(deps_files={"rehearsalpkg/__init__.py": "VALUE = 7\n"})   # CI ships the set every time
        raw = json.loads((self.bundle / su.MANIFEST).read_bytes())
        self.assertNotEqual(raw["sha"], SHA_NEW)
        p2 = su.prepare(self.s, su.FolderSource(self.bundle), legacy=self.legacy)
        self.assertTrue(p2["needs_db"])
        self.press_update_now()
        self.launcher_settles()
        self.assertIn("rehearsal_two", self.tables())
        releases.recover_failed_start(self.home, p2["release"])              # the new version did not start
        self.assertNotIn("rehearsal_two", self.tables())
        self.assertEqual(self.s.db_path.read_bytes(), good)
        st = releases.read_state(self.home)
        self.assertEqual((st["active"]["id"], st["pending"]), (p["release"], False))   # batch 1's code again
        self.assertEqual(releases.read_journal(self.home)["state"], "rolled_back")
