"""One lost or changed upload file must not stop every automatic backup.

The database is what the pharmacy cannot lose: when it is good and only registered attachments are
missing or differ, the backup is kept (named "-auto-incomplete"), the owner is warned with the file
names, and the newest complete backup (the only copy of the lost file) is never pruned. Restore still
refuses such an archive unless it is told to go on without the files."""
import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest import mock

from app import config
from app.catalog import service as cat
from app.core import autobackup, backup, notify
from app.core.autobackup import AutoBackup
from app.db import Database
from app.inventory import service as inv
from app.purchasing.service import Purchasing
from tests.fixtures.make_invoices import excel_invoice
from tests.helpers import DBTestCase

ROOT = Path(__file__).resolve().parent.parent


class Base(DBTestCase):
    def setUp(self):
        super().setUp()
        self.up = self.tmp / "uploads"
        p = Purchasing(self.db, replace(config.load(), upload_dir=self.up))
        cat.create_supplier(self.db, self.ctx, name="ابن سينا فارما")
        self.pan = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab", "pack_size": 2})
        p.import_file(self.ctx, excel_invoice(
            [("PANADOL 500 MG 24 TAB", 10, 0, 40, 20, 32, 320, "P1", "05/2030", None)]), "invoice-march.xlsx")
        self.bdir = self.tmp / "backups"
        self.ab = AutoBackup(self.db, self.up, self.bdir, self.tmp / "state")

    def lose_the_file(self):
        f = next(p for p in self.up.rglob("*") if p.is_file())
        f.unlink()
        self.change()

    def change(self):
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=1, movement_type="opening",
                              unit_cost=Decimal("1"))

    def warnings(self):
        return [n for n in notify.listing(self.db, self.ctx)["items"]
                if n["kind"] == "backup" and n["severity"] == "warning"]


class TestAutomaticBackupWithAMissingFile(Base):
    def test_backup_is_kept_and_the_owner_is_warned_with_the_file_name(self):
        clean = self.ab.run()
        self.assertTrue(clean["last_ok"])
        self.assertIsNone(clean.get("last_warning"))
        self.lose_the_file()
        with self.assertLogs("pharm.backup", level="WARNING"):
            st = self.ab.run()
        self.assertTrue(st["last_ok"], st)                             # the run is done, not failed
        self.assertIsNone(st["last_error"])
        self.assertTrue(st["last_file"].endswith("-auto-incomplete.zip"))
        self.assertTrue((self.bdir / st["last_file"]).exists())
        self.assertIn("invoice-march.xlsx", st["last_warning"])
        self.assertEqual(st["last_warning_files"], ["invoice-march.xlsx"])
        w = self.warnings()
        self.assertEqual(len(w), 1)
        self.assertIn("invoice-march.xlsx", w[0]["body"])
        # the database inside is complete and current
        problems = backup.verify_archive(self.bdir / st["last_file"])["problems"]
        self.assertEqual(len(problems), 1)
        self.assertIn("missing original attachment", problems[0])
        with self.assertLogs("pharm.backup", level="WARNING"):
            self.ab.run()                                              # the warning itself was a new record
        self.assertEqual(self.ab.run().get("skipped"), "no_changes")    # then it settles: no loop
        self.change()
        with self.assertLogs("pharm.backup", level="WARNING"):
            self.assertTrue(self.ab.run()["last_warning"])
        self.assertEqual(len(self.warnings()), 1)                      # told once a day, not every hour

    def test_a_changed_file_is_a_warning_too(self):
        self.ab.run()
        f = next(p for p in self.up.rglob("*") if p.is_file())
        f.write_bytes(b"X" * f.stat().st_size)
        self.change()
        with self.assertLogs("pharm.backup", level="WARNING"):
            st = self.ab.run()
        self.assertTrue(st["last_ok"])
        self.assertEqual(st["last_warning_files"], ["invoice-march.xlsx"])

    def test_a_damaged_database_part_still_fails_and_is_deleted(self):
        with mock.patch("app.core.backup.quick_check", return_value=[
                "database checksum does not match the manifest", "missing original attachment for source x"]), \
                self.assertLogs("pharm.backup", level="ERROR"):
            st = self.ab.run()
        self.assertFalse(st["last_ok"])
        self.assertEqual(list(self.bdir.glob("*.zip")), [])

    def test_a_clean_run_clears_the_warning(self):
        self.ab.run()
        f = next(p for p in self.up.rglob("*") if p.is_file())
        data = f.read_bytes()
        self.lose_the_file()
        with self.assertLogs("pharm.backup", level="WARNING"):
            self.assertTrue(self.ab.run()["last_warning"])
        f.write_bytes(data)                                            # the owner put the file back
        self.change()
        st = self.ab.run()
        self.assertIsNone(st["last_warning"])
        self.assertEqual(st["last_warning_files"], [])
        self.assertTrue(st["last_file"].endswith("-auto.zip"))


class TestPruneKeepsTheLastCompleteBackup(DBTestCase):
    def test_incomplete_backups_never_push_out_the_newest_complete_one(self):
        d = self.tmp / "b"
        d.mkdir()
        t0 = datetime.now() - timedelta(days=200)
        old_clean = d / f"pharmacy-backup-{t0:%Y%m%d-%H%M%S}-auto.zip"
        older_clean = d / f"pharmacy-backup-{t0 - timedelta(days=1):%Y%m%d-%H%M%S}-auto.zip"
        for f in (old_clean, older_clean):
            f.write_bytes(b"x")
        newer = [d / f"pharmacy-backup-{t0 + timedelta(days=1, minutes=i):%Y%m%d-%H%M%S}-auto-incomplete.zip"
                 for i in range(15)]
        for f in newer:
            f.write_bytes(b"x")
        removed = autobackup.prune(d, dict(autobackup.DEFAULTS))
        self.assertTrue(old_clean.exists())                            # the only copy of the lost file
        self.assertFalse(older_clean.exists())                         # ordinary retention otherwise
        self.assertEqual(sum(f.exists() for f in newer), 10)           # incomplete ones are pruned as usual
        self.assertIn(older_clean.name, removed)


class TestRestoreWithMissingAttachments(Base):
    def incomplete(self):
        self.lose_the_file()
        out = backup.create(self.db, self.up, self.tmp / "manual")
        self.assertTrue(backup.verify_archive(out)["problems"])
        return out

    def test_refused_by_default(self):
        arch = self.incomplete()
        target = self.tmp / "r" / "pharmacy.db"
        with self.assertRaises(RuntimeError) as e:
            backup.restore(arch, target, self.tmp / "r" / "up", self.tmp / "pre")
        self.assertIn("missing original attachment", str(e.exception))
        self.assertFalse(target.exists())

    def test_explicit_switch_restores_the_database_and_reports_the_files(self):
        arch = self.incomplete()
        target = self.tmp / "r" / "pharmacy.db"
        r = backup.restore(arch, target, self.tmp / "r" / "up", self.tmp / "pre", allow_missing_attachments=True)
        self.assertEqual([m["filename"] for m in r["missing_attachments"]], ["invoice-march.xlsx"])
        self.assertIn("missing original attachment", r["missing_attachments"][0]["problem"])
        db2 = Database(target)
        try:
            self.assertEqual(backup.integrity_problems(db2), [])
            self.assertEqual(db2.one("SELECT count(*) c FROM items")["c"], 1)
        finally:
            db2.close()

    def test_the_switch_never_lets_a_damaged_database_through(self):
        arch = self.incomplete()
        with mock.patch("app.core.backup.verify_archive", return_value={"problems": [
                "missing original attachment for source x", "database checksum does not match the manifest"]}):
            with self.assertRaises(RuntimeError):
                backup.restore(arch, self.tmp / "r" / "pharmacy.db", self.tmp / "r" / "up", self.tmp / "pre",
                               allow_missing_attachments=True)
        self.assertFalse((self.tmp / "r" / "pharmacy.db").exists())

    def test_cli_switch(self):
        arch = self.incomplete()
        env = {**os.environ, "PHARM_DATA_DIR": str(self.tmp / "cli"), "PYTHONPATH": str(ROOT)}
        run = lambda *a: subprocess.run([sys.executable, "manage.py", *a], cwd=ROOT, env=env,  # noqa: E731
                                        capture_output=True, text=True, encoding="utf-8")
        r = run("restore", str(arch), "--yes")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("--allow-missing-attachments", r.stdout + r.stderr)
        self.assertFalse((self.tmp / "cli" / "pharmacy.db").exists())
        r = run("restore", str(arch), "--yes", "--allow-missing-attachments")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("invoice-march.xlsx", r.stdout)
        self.assertTrue((self.tmp / "cli" / "pharmacy.db").exists())
