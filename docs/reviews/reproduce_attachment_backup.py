"""Known backup defect: verification must cover original invoice attachments.
Run explicitly: python -m unittest docs.reviews.reproduce_attachment_backup -v
Expected to fail on baseline 69e3c4a. Synthetic invoices and temporary databases only.
"""
import unittest
import zipfile

from app.core import backup
from tests import test_backup_resilience as fixtures


class AttachmentBackupIntegrity(unittest.TestCase):
    def _assert_rejected(self, mutation):
        fixture = fixtures.TestBackupRestore()
        fixture.setUp()
        try:
            original = backup.create(fixture.db, fixture.up, fixture.tmp / "original")
            changed = fixture.tmp / "changed.zip"
            with zipfile.ZipFile(original) as src, zipfile.ZipFile(changed, "w") as dst:
                uploads = [n for n in src.namelist() if n.startswith("uploads/")]
                self.assertTrue(uploads, "The synthetic fixture must contain an original invoice")
                for name in src.namelist():
                    data = src.read(name)
                    if name == uploads[0]:
                        if mutation == "missing":
                            continue
                        data = b"synthetic altered invoice"
                    dst.writestr(name, data)
            checked = backup.verify_archive(changed)
            self.assertTrue(checked["problems"],
                            "Backup verifier accepted " + mutation + " original invoice attachment")
        finally:
            fixture.tearDown()

    def test_missing_invoice_attachment_must_be_rejected(self):
        self._assert_rejected("missing")

    def test_modified_invoice_attachment_must_be_rejected(self):
        self._assert_rejected("modified")
