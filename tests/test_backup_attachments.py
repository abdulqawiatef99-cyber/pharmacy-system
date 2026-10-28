"""Backup attachments: completeness, content integrity and legacy portability.
Synthetic source records and temporary SQLite databases only.
"""
import hashlib
import json
import lzma
from pathlib import Path
import zipfile

from app.core import backup
from app.db import new_id
from tests.helpers import DBTestCase


class TestBackupAttachmentIntegrity(DBTestCase):
    def setUp(self):
        super().setUp()
        self.up = self.tmp / "uploads"
        self.content = b"name,qty\nSynthetic item,1\n"
        self.sha = hashlib.sha256(self.content).hexdigest()
        self.source_id = new_id()
        self.file = self.up / self.ctx.org_id / (self.sha + ".csv")
        self.file.parent.mkdir(parents=True)
        self.file.write_bytes(self.content)
        with self.db.transaction():
            self.db.execute(
                "INSERT INTO source_files (id,org_id,sha256,filename,mime_type,size_bytes,storage_path,uploaded_by)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (self.source_id, self.ctx.org_id, self.sha, "synthetic.csv", "text/csv",
                 len(self.content), str(self.file), self.ctx.user_id))
        self.archive = backup.create(self.db, self.up, self.tmp / "backups")
        self.member = "uploads/" + self.file.relative_to(self.up).as_posix()

    def rewrite(self, *, missing=False, changed=False, other_org=False, duplicate=False, legacy=False):
        out = self.tmp / "modified.zip"
        with zipfile.ZipFile(self.archive) as src, zipfile.ZipFile(out, "w") as dst:
            for name in src.namelist():
                data = src.read(name)
                if name == self.member:
                    if missing:
                        continue
                    if changed:
                        data = b"X" * len(data) if changed == "same_size" else b"changed original source"
                    if other_org:
                        name = name.replace(self.ctx.org_id, "foreign-organization")
                if legacy and name == "pharmacy.db.xz":
                    name, data = "pharmacy.db", lzma.decompress(data)
                if legacy and name == "manifest.json":
                    manifest = json.loads(data)
                    manifest["format"] = 1
                    data = json.dumps(manifest).encode()
                dst.writestr(name, data)
            if duplicate:
                dst.writestr(self.member, self.content)
        return out

    def test_valid_format_two_keeps_original_attachment(self):
        self.assertEqual(backup.verify_archive(self.archive)["problems"], [])

    def test_missing_registered_attachment_is_rejected(self):
        self.assertTrue(backup.verify_archive(self.rewrite(missing=True))["problems"])

    def test_changed_attachment_with_valid_zip_crc_is_rejected(self):
        changed = self.rewrite(changed=True)
        with zipfile.ZipFile(changed) as z:
            self.assertIsNone(z.testzip())
        self.assertTrue(backup.verify_archive(changed)["problems"])

    def test_same_content_in_another_tenant_folder_cannot_replace_attachment(self):
        self.assertTrue(backup.verify_archive(self.rewrite(other_org=True))["problems"])

    def test_same_length_modified_original_is_rejected_by_checksum(self):
        self.assertTrue(backup.verify_archive(self.rewrite(changed="same_size"))["problems"])

    def test_duplicate_zip_member_is_rejected(self):
        self.assertTrue(backup.verify_archive(self.rewrite(duplicate=True))["problems"])

    def test_legacy_format_one_is_supported(self):
        self.assertEqual(backup.verify_archive(self.rewrite(legacy=True))["problems"], [])

    def test_legacy_format_one_still_checks_attachments(self):
        self.assertTrue(backup.verify_archive(self.rewrite(legacy=True, missing=True))["problems"])

    def test_windows_storage_path_is_portable_without_reading_that_path(self):
        with self.db.transaction():
            self.db.execute("UPDATE source_files SET storage_path=? WHERE id=?",
                            ("C:\\OldPharmacy\\uploads\\" + self.ctx.org_id + "\\" + self.file.name,
                             self.source_id))
        archive = backup.create(self.db, self.up, self.tmp / "windows-path")
        self.assertEqual(backup.verify_archive(archive)["problems"], [])

    def test_transfer_subfolder_is_supported(self):
        transfer = self.file.parent / "transfers" / self.file.name
        transfer.parent.mkdir()
        self.file.replace(transfer)
        with self.db.transaction():
            self.db.execute("UPDATE source_files SET storage_path=? WHERE id=?",
                            (str(transfer), self.source_id))
        archive = backup.create(self.db, self.up, self.tmp / "transfer")
        self.assertEqual(backup.verify_archive(archive)["problems"], [])

    def test_quick_check_rejects_missing_original(self):
        self.assertTrue(backup.quick_check(self.rewrite(missing=True)))

    def test_quick_check_rejects_changed_original(self):
        self.assertTrue(backup.quick_check(self.rewrite(changed="same_size")))

    def test_restore_rejects_missing_original_before_touching_live_data(self):
        broken = self.rewrite(missing=True)
        before = [dict(r) for r in self.db.all("SELECT * FROM source_files")]
        pre = self.tmp / "pre-restore"
        with self.assertRaises(RuntimeError):
            backup.restore(broken, Path(self.db.path), self.up, pre)
        self.assertEqual([dict(r) for r in self.db.all("SELECT * FROM source_files")], before)
        self.assertEqual(self.file.read_bytes(), self.content)
        self.assertFalse(pre.exists())
