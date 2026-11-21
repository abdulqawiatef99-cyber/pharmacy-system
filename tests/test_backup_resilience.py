import os
import shutil
import sqlite3
import subprocess
import sys
import textwrap
import zipfile
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from app import config
from app.catalog import service as cat
from app.core import backup
from app.db import Database
from app.inventory import service as inv
from app.purchasing.service import Purchasing
from tests.fixtures.make_invoices import excel_invoice
from tests.helpers import DBTestCase

ROOT = Path(__file__).resolve().parent.parent


class TestBackupRestore(DBTestCase):
    def setUp(self):
        super().setUp()
        self.up = self.tmp / "uploads"
        self.p = Purchasing(self.db, replace(config.load(), upload_dir=self.up))
        cat.create_supplier(self.db, self.ctx, name="ابن سينا فارما")
        self.pan = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab", "pack_size": 2})
        inv_id = self.p.import_file(self.ctx, excel_invoice(
            [("PANADOL 500 MG 24 TAB", 10, 0, 40, 20, 32, 320, "P1", "05/2030", None)]), "i.xlsx")
        d = self.p.get(self.ctx, inv_id)
        self.p.approve(self.ctx, inv_id, d["invoice"]["version"])
        self.file_id = d["invoice"]["source_file_id"]

    def test_round_trip_restores_data_and_files(self):
        arch = backup.create(self.db, self.up, self.tmp / "bk")
        self.assertEqual(backup.verify_archive(arch)["problems"], [])
        # damage after the backup: more stock + the invoice file deleted
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=99, movement_type="opening",
                              unit_cost=Decimal("1"))
        shutil.rmtree(self.up)
        db_path = Path(self.db.path)
        self.db.close()
        r = backup.restore(arch, db_path, self.up, self.tmp / "pre")
        self.assertTrue(Path(r["pre_restore_backup"]).exists())          # old state kept
        self.db = Database(db_path)
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.pan)["total_qty"], 20)
        data, mime, _ = Purchasing(self.db, replace(config.load(), upload_dir=self.up)).get_file(self.ctx, self.file_id)
        self.assertTrue(data.startswith(b"PK"))                          # original invoice back
        self.assertEqual(backup.integrity_problems(self.db), [])

    def test_restore_to_another_folder(self):
        arch = backup.create(self.db, self.up, self.tmp / "bk")
        new_db, new_up = self.tmp / "other" / "pharmacy.db", self.tmp / "other" / "uploads"
        backup.restore(arch, new_db, new_up, self.tmp / "pre2")
        db2 = Database(new_db)
        try:
            data, _, _ = Purchasing(db2, replace(config.load(), upload_dir=new_up)).get_file(self.ctx, self.file_id)
            self.assertTrue(data)
        finally:
            db2.close()

    def test_corrupt_or_tampered_backups_refused(self):
        arch = backup.create(self.db, self.up, self.tmp / "bk")
        # 1) flipped bytes
        bad = self.tmp / "bad.zip"
        raw = bytearray(arch.read_bytes())
        raw[len(raw) // 2] ^= 0xFF
        bad.write_bytes(bytes(raw))
        try:
            probs = backup.verify_archive(bad)["problems"]
        except zipfile.BadZipFile:
            probs = ["bad zip"]
        self.assertTrue(probs)
        # 2) database inside replaced by a different one
        other = self.tmp / "o.zip"
        with zipfile.ZipFile(arch) as z, zipfile.ZipFile(other, "w") as w:
            w.writestr("manifest.json", z.read("manifest.json"))
            w.writestr("pharmacy.db", b"not a database")
        self.assertTrue(backup.verify_archive(other)["problems"])
        live_before = Path(self.db.path).read_bytes()
        with self.assertRaises(RuntimeError):
            backup.restore(other, Path(self.db.path), self.up, self.tmp / "pre")
        self.assertEqual(Path(self.db.path).read_bytes(), live_before)   # live data untouched

    def test_backup_refused_when_live_db_is_inconsistent(self):
        c = sqlite3.connect(self.db.path)
        c.execute("UPDATE stock_balances SET qty = qty + 1")             # bypass the app
        c.commit()
        c.close()
        with self.assertRaises(RuntimeError):
            backup.create(self.db, self.up, self.tmp / "bk")

    def test_cli_backup_verify_restore(self):
        # never the runner's own PHARM_DB_PATH & co. (scripts/validate_local_release.py sets them, #307)
        env = {**{k: v for k, v in os.environ.items() if not k.startswith("PHARM_")},
               "PHARM_DATA_DIR": str(self.tmp / "cli"), "PYTHONPATH": str(ROOT)}
        run = lambda *a: subprocess.run([sys.executable, "manage.py", *a], cwd=ROOT, env=env,  # noqa: E731
                                        capture_output=True, text=True, encoding="utf-8")
        r = run("init", "--pharmacy", "x", "--username", "cli", "--password", "Cli-Pass-2026")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = run("backup", "--dest", str(self.tmp / "clibk"))
        self.assertEqual(r.returncode, 0, r.stderr)
        arch = next((self.tmp / "clibk").glob("*.zip"))
        self.assertEqual(run("verify", str(arch)).returncode, 0)
        self.assertEqual(run("restore", str(arch)).returncode, 2)               # needs --yes
        r = run("restore", str(arch), "--yes", "--pre-dir", str(self.tmp / "clipre"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(run("verify").returncode, 0)


class TestPowerLoss(DBTestCase):
    """Simulates the process dying (power cut) in the middle of writing."""

    def _child(self, code: str) -> int:
        script = textwrap.dedent(f"""
            import os, sys
            sys.path.insert(0, {str(ROOT)!r})
            from decimal import Decimal
            from app.db import Database
            from app.core import accounts
            from app.inventory import service as inv
            db = Database({self.db.path!r})
            tok = accounts.login(db, "owner", "Owner-Pass-2026")["token"]
            ctx = accounts.resolve_session(db, tok)
            item = db.one("SELECT id FROM items LIMIT 1")["id"]
        """) + textwrap.dedent(code)
        return subprocess.run([sys.executable, "-c", script], capture_output=True).returncode

    def setUp(self):
        super().setUp()
        self.item = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab"})

    def test_kill_inside_transaction_leaves_nothing(self):
        rc = self._child("""
            with db.transaction():
                inv.post_movement(db, ctx, item_id=item, qty_delta=7, movement_type="opening",
                                  unit_cost=Decimal("1"))
                os._exit(9)          # power cut before COMMIT
        """)
        self.assertEqual(rc, 9)
        self.assertEqual(self.db.one("SELECT count(*) FROM stock_movements")[0], 0)
        self.assertEqual(backup.integrity_problems(self.db), [])

    def test_kill_between_many_commits_stays_consistent(self):
        rc = self._child("""
            for i in range(200):
                with db.transaction():
                    inv.post_movement(db, ctx, item_id=item, qty_delta=1, movement_type="opening",
                                      unit_cost=Decimal("1"))
                if i == 137:
                    os._exit(9)
        """)
        self.assertEqual(rc, 9)
        self.assertEqual(self.db.one("SELECT count(*) FROM stock_movements")[0], 138)
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.item)["total_qty"], 138)
        self.assertEqual(backup.integrity_problems(self.db), [])


class TestTamperedInside(DBTestCase):
    """A backup edited inside and re-sealed (checksum and counts recomputed, so the zip and manifest look
    right) is still refused: the database's own invariants are checked, and live data is never touched."""

    def setUp(self):
        super().setUp()
        self.up = self.tmp / "uploads"
        self.pan = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab", "pack_size": 2})
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.pan, "B1", "2030-05-31")
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=20, batch_id=b, movement_type="opening",
                              unit_cost=Decimal("15"))
        self.arch = backup.create(self.db, self.up, self.tmp / "bk")

    def resealed(self, sql):
        import hashlib
        import json
        import lzma
        work = self.tmp / "work"
        work.mkdir(exist_ok=True)
        with zipfile.ZipFile(self.arch) as z:
            members = {n: z.read(n) for n in z.namelist()}
        raw = work / "pharmacy.db"
        raw.write_bytes(lzma.decompress(members["pharmacy.db.xz"]))
        c = sqlite3.connect(raw)
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
            c.execute(f'DROP TRIGGER "{name}"')                       # whoever edits a copy can drop them
        c.executescript(sql)
        c.commit()
        counts = {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in json.loads(members["manifest.json"])["counts"]}
        c.close()
        data = raw.read_bytes()
        manifest = json.loads(members["manifest.json"])
        manifest["db_sha256"] = hashlib.sha256(data).hexdigest()
        manifest["counts"] = counts
        members["manifest.json"] = json.dumps(manifest).encode()
        members["pharmacy.db.xz"] = lzma.compress(data)
        out = self.tmp / f"resealed-{len(list(self.tmp.glob('resealed-*')))}.zip"
        with zipfile.ZipFile(out, "w") as w:
            for n, b in members.items():
                w.writestr(n, b)
        return out

    def refused(self, archive, expect):
        problems = backup.verify_archive(archive)["problems"]
        self.assertTrue(any(expect in p for p in problems), problems)
        live_before = Path(self.db.path).read_bytes()
        with self.assertRaises(RuntimeError):
            backup.restore(archive, Path(self.db.path), self.up, self.tmp / "pre")
        self.assertEqual(Path(self.db.path).read_bytes(), live_before)

    def test_untampered_reseal_passes(self):
        self.assertEqual(backup.verify_archive(self.resealed("SELECT 1;"))["problems"], [])

    def test_stock_edited_without_a_movement_is_refused(self):
        self.refused(self.resealed("UPDATE stock_balances SET qty = qty + 50;"), "stock balance differences")

    def test_an_edited_audit_row_is_refused(self):
        self.refused(self.resealed("UPDATE audit_log SET after_json = '{}' WHERE seq = (SELECT max(seq) FROM audit_log);"),
                     "audit chain broken")

    def test_a_deleted_audit_row_is_refused(self):
        self.refused(self.resealed("DELETE FROM audit_log WHERE seq = (SELECT min(seq) FROM audit_log);"),
                     "audit chain broken")

    def test_a_zip_that_is_not_a_backup_is_refused(self):
        other = self.tmp / "photos.zip"
        with zipfile.ZipFile(other, "w") as w:
            w.writestr("photo.jpg", b"jpeg")
        self.refused(other, "not a pharmacy backup")
