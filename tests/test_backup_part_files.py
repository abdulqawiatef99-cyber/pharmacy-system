"""A backup that fails while its ZIP is being written leaves no ".zip.part" file behind, and old
leftovers (from a crash or a power cut) are removed with the routine pruning."""
import os
import time
import zipfile
from unittest import mock

from app.core import autobackup, backup
from app.core.autobackup import AutoBackup
from tests.helpers import DBTestCase


class TestPartFiles(DBTestCase):
    def setUp(self):
        super().setUp()
        self.up = self.tmp / "uploads"
        (self.up / "x").mkdir(parents=True)
        (self.up / "x" / "scan.png").write_bytes(b"picture")
        self.bdir = self.tmp / "backups"

    def failing_write(self):
        real = zipfile.ZipFile.write

        def write(z, filename, arcname=None, *a, **k):
            if str(arcname).startswith("uploads/"):
                raise PermissionError(13, "file is locked")
            return real(z, filename, arcname, *a, **k)
        return mock.patch.object(zipfile.ZipFile, "write", write)

    def test_failed_backup_leaves_no_part_file(self):
        with self.failing_write(), self.assertRaises(PermissionError):
            backup.create(self.db, self.up, self.bdir)
        self.assertEqual(list(self.bdir.iterdir()), [])

    def test_failed_automatic_backups_do_not_pile_up(self):
        ab = AutoBackup(self.db, self.up, self.bdir, self.tmp / "state")
        with self.failing_write(), self.assertLogs("pharm.backup", level="ERROR"):
            for _ in range(3):
                self.assertFalse(ab.run(force=True)["last_ok"])
        self.assertEqual(list(self.bdir.iterdir()), [])

    def test_prune_removes_stale_part_files_only(self):
        self.bdir.mkdir()
        stale = self.bdir / "pharmacy-backup-20200101-000000-auto.zip.part"
        usb = self.bdir / "pharmacy-backup-20200102-000000-auto.zip.part"
        fresh = self.bdir / "pharmacy-backup-20200103-000000.zip.part"
        other = self.bdir / "notes.part"
        for f in (stale, usb, fresh, other):
            f.write_bytes(b"half a backup")
        old = time.time() - 3 * 3600
        os.utime(stale, (old, old))
        os.utime(usb, (old, old))
        os.utime(other, (old, old))
        removed = autobackup.prune(self.bdir, dict(autobackup.DEFAULTS))
        self.assertFalse(stale.exists())
        self.assertFalse(usb.exists())
        self.assertTrue(fresh.exists())                                # may be a backup being written right now
        self.assertTrue(other.exists())                                # not ours
        self.assertEqual(sorted(removed), sorted([stale.name, usb.name]))

    def test_a_good_backup_is_untouched(self):
        out = backup.create(self.db, self.up, self.bdir)
        self.assertEqual([p.name for p in self.bdir.iterdir()], [out.name])
        self.assertEqual(backup.verify_archive(out)["problems"], [])
