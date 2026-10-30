"""R5 preflight: read-only report on a backup before moving data. Synthetic data only."""
import base64
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from app.core import backup, backup_preflight
from app.db import new_id
from tests.test_cashier_acceptance import Base

ROOT = Path(__file__).resolve().parent.parent
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class TestPreflight(Base):
    def setUp(self):
        super().setUp()
        self.up = self.tmp / "uploads"
        self.cd.open(self.ctx, 10000)
        from app.sales import attachments
        sale = self.sell(units=2)
        attachments.attach(self.db, self.up, self.ctx, sale["id"], PNG, "transfer.png")
        self.approved = self._purchase_file(b"name,qty\nSynthetic A,1\n", "approved", "INV-1")
        self.draft = self._purchase_file(b"name,qty\nSynthetic B,2\n", "draft", "INV-2")
        self.archive = backup.create(self.db, self.up, self.tmp / "backups")

    def _purchase_file(self, data, status, no):
        sha = hashlib.sha256(data).hexdigest()
        path = self.up / self.ctx.org_id / f"{sha}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        fid = new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO source_files (id,org_id,sha256,filename,mime_type,size_bytes,storage_path,uploaded_by)"
                            " VALUES (?,?,?,?,?,?,?,?)", (fid, self.ctx.org_id, sha, f"{no}.csv", "text/csv", len(data),
                                                          str(path), self.ctx.user_id))
            self.db.execute("INSERT INTO purchase_invoices (id, org_id, branch_id, invoice_no, supplier_name_raw, status,"
                            " source_type, source_file_id) VALUES (?,?,?,?,?,?,?,?)",
                            (new_id(), self.ctx.org_id, self.ctx.branch_id, no, "مورد تجريبي", status, "csv", fid))
        return "uploads/" + path.relative_to(self.up).as_posix()

    def without(self, *members):
        out = self.tmp / "partial.zip"
        with zipfile.ZipFile(self.archive) as src, zipfile.ZipFile(out, "w") as dst:
            for name in src.namelist():
                if name not in members:
                    dst.writestr(name, src.read(name))
        return out

    def by_name(self, r):
        return {a["filename"]: a for a in r["attachments"]}

    def test_complete_backup_can_move(self):
        r = backup_preflight.check(self.archive)
        self.assertTrue(r["can_restore"], r["problems"])
        a = self.by_name(r)
        self.assertEqual({k: (v["status"], v["importance"]) for k, v in a.items()},
                         {"transfer.png": ("ok", "financial"), "INV-1.csv": ("ok", "financial"),
                          "INV-2.csv": ("ok", "optional")})
        self.assertIn("النتيجة: النسخة تنفع للنقل", backup_preflight.render(r))

    def test_missing_files_are_classified_and_block_the_move(self):
        r = backup_preflight.check(self.without(self.approved, self.draft))
        self.assertFalse(r["can_restore"])
        a = self.by_name(r)
        self.assertEqual((a["INV-1.csv"]["status"], a["INV-1.csv"]["importance"]), ("missing", "financial"))
        self.assertEqual((a["INV-2.csv"]["status"], a["INV-2.csv"]["importance"]), ("missing", "optional"))
        self.assertEqual(a["transfer.png"]["status"], "ok")
        text = "\n".join(backup_preflight.render(r))
        self.assertIn("فيها مشكلة 2 (مالية 1، اختيارية 1)", text)
        self.assertIn("INV-1", text)
        self.assertIn("لا تنفع للنقل", text)

    def test_verdict_matches_restore(self):
        for archive in (self.archive, self.without(self.draft)):
            r = backup_preflight.check(archive)
            target = self.tmp / f"restore-{archive.stem}"
            try:
                backup.restore(archive, target / "pharmacy.db", target / "uploads", target / "pre")
                restored = True
            except RuntimeError:
                restored = False
            self.assertEqual(r["can_restore"], restored, archive.name)

    def test_read_only(self):
        archive = self.without(self.draft)
        before = (archive.stat().st_mtime_ns, hashlib.sha256(archive.read_bytes()).hexdigest())
        files_before = sorted(p for p in self.tmp.rglob("*"))
        backup_preflight.check(archive)
        self.assertEqual(before, (archive.stat().st_mtime_ns, hashlib.sha256(archive.read_bytes()).hexdigest()))
        self.assertEqual(files_before, sorted(p for p in self.tmp.rglob("*")))

    def test_usage_is_looked_up_in_the_files_own_pharmacy(self):
        _, other = self.second_tenant()
        fid = self.db.one("SELECT source_file_id f FROM purchase_invoices WHERE invoice_no='INV-2'")["f"]
        with self.db.transaction():   # a foreign pharmacy's approved invoice pointing at this file
            self.db.execute("INSERT INTO purchase_invoices (id, org_id, branch_id, invoice_no, status, source_type,"
                            " source_file_id) VALUES (?,?,?,?,?,?,?)",
                            (new_id(), other.org_id, other.branch_id, "FOREIGN-9", "approved", "csv", fid))
        r = backup_preflight.check(backup.create(self.db, self.up, self.tmp / "b2"))
        a = self.by_name(r)["INV-2.csv"]
        self.assertEqual(a["importance"], "optional")
        self.assertNotIn("FOREIGN-9", " ".join(a["used_by"]))

    def test_not_a_backup(self):
        bogus = self.tmp / "x.zip"
        with zipfile.ZipFile(bogus, "w") as z:
            z.writestr("readme.txt", "x")
        r = backup_preflight.check(bogus)
        self.assertFalse(r["can_restore"])
        self.assertIn("لا تنفع للنقل", "\n".join(backup_preflight.render(r)))

    def test_not_a_zip_or_missing_file(self):
        junk = self.tmp / "junk.zip"
        junk.write_text("not a zip")
        for path in (junk, self.tmp / "nowhere.zip"):
            r = backup_preflight.check(path)
            self.assertFalse(r["can_restore"])
            self.assertIn("لا تنفع للنقل", "\n".join(backup_preflight.render(r)))

    def test_command_never_touches_the_data_folder(self):
        data = self.tmp / "data-dir"
        env = {**os.environ, "PHARM_DATA_DIR": str(data), "PYTHONUTF8": "1"}
        r = subprocess.run([sys.executable, str(ROOT / "manage.py"), "preflight", "--json",
                            str(self.without(self.approved))], env=env, capture_output=True, text=True,
                           encoding="utf-8", timeout=120, check=False)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertFalse(json.loads(r.stdout)["can_restore"])
        self.assertFalse(data.exists())
