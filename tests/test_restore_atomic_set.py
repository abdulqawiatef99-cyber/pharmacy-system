"""A restore that fails part-way leaves ONE usable set: the original database with its own E-Stock archive
and attachments, or the whole restored set. Never the live database beside the backup's archive (#461)."""
import os
import shutil
import stat
from decimal import Decimal
from pathlib import Path
from unittest import mock

from app.catalog import service as cat
from app.core import backup
from app.db import Database
from app.inventory import service as inv
from tests.helpers import DBTestCase

OLD_ARCH = b"synthetic E-Stock history, old " * 400
LIVE_ARCH = b"synthetic E-Stock history, LIVE " * 500


class Base(DBTestCase):
    backup_archive = OLD_ARCH
    live_archive = LIVE_ARCH

    def setUp(self):
        super().setUp()
        self.up = self.tmp / "uploads"
        self.db_path = Path(self.db.path)
        self.arch = self.db_path.parent / backup.ESTOCK_ARCHIVE
        self.add_item("Panadol 500mg 24 tab")
        self.put_archive(self.backup_archive)
        self.bk = backup.create(self.db, self.up, self.tmp / "bk")          # one item, the old archive
        self.add_item("Brufen 400mg 30 tab")                                 # live: two items, the live archive
        self.put_archive(self.live_archive)
        self.db.close()

    def tearDown(self):
        if self.arch.exists():
            self.arch.chmod(0o644)
        super().tearDown()

    def add_item(self, name):
        item = cat.create_item(self.db, self.ctx, {"name_en": name, "pack_size": 2})
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=item, qty_delta=20, movement_type="opening",
                              unit_cost=Decimal("1"))

    def put_archive(self, data):
        if self.arch.exists():
            self.arch.chmod(0o644)
            self.arch.unlink()
        for f in (self.arch.with_name(backup.ARCHIVE_MEMBER), self.arch.with_name(backup.ARCHIVE_MEMBER + ".sha256")):
            f.unlink(missing_ok=True)
        if data is not None:
            self.arch.write_bytes(data)
            self.arch.chmod(0o444)

    def restore(self):
        return backup.restore(self.bk, self.db_path, self.up, self.tmp / "pre")

    def items(self):
        self.db = Database(self.db_path)
        self.assertEqual(backup.integrity_problems(self.db), [])
        return self.db.one("SELECT count(*) c FROM items")["c"]

    def assert_original_set(self):
        self.assertEqual(self.items(), 2)
        if self.live_archive is None:
            self.assertFalse(self.arch.exists())
        else:
            self.assertTrue(self.arch.exists(), "no archive left at the active path")
            self.assertEqual(self.arch.read_bytes(), self.live_archive)
            self.assertFalse(os.stat(self.arch).st_mode & stat.S_IWUSR)
        self.assertEqual(list(self.db_path.parent.glob("estock-archive-replaced-*")), [])
        self.assertEqual(list(self.db_path.parent.glob("*.restoring*")), [])
        self.assertEqual(list(self.db_path.parent.glob("*.part")), [])
        self.assertFalse((self.db_path.parent / backup.RESTORE_JOURNAL).exists())

    def assert_restored_set(self):
        self.assertEqual(self.items(), 1)
        if self.backup_archive is None:
            self.assertFalse(self.arch.exists())
        else:
            self.assertEqual(self.arch.read_bytes(), self.backup_archive)
        self.assertFalse((self.db_path.parent / backup.RESTORE_JOURNAL).exists())

    def failing_replace(self, match):
        """Path.replace that fails (full disk, locked file) for the move whose source matches."""
        real = Path.replace

        def replace(src, target):
            if match(Path(src), Path(target)):
                raise OSError(28, "No space left on device (injected)")
            return real(src, target)
        return mock.patch.object(Path, "replace", replace)


class TestFailedRestoreKeepsTheOriginalSet(Base):
    def test_the_restore_works_when_nothing_fails(self):
        self.restore()
        self.assert_restored_set()

    def test_final_database_replacement_fails(self):
        with self.failing_replace(lambda s, t: t == self.db_path):
            with self.assertRaises(OSError):
                self.restore()
        self.assert_original_set()

    def test_archive_copy_fails(self):
        real = shutil.copy2

        def copy2(src, dst, **kw):
            if Path(src).name == backup.ESTOCK_ARCHIVE:
                Path(dst).write_bytes(b"half")
                raise OSError(28, "No space left on device (injected)")
            return real(src, dst, **kw)
        with mock.patch.object(shutil, "copy2", copy2):
            with self.assertRaises(OSError):
                self.restore()
        self.assert_original_set()

    def test_moving_the_live_archive_aside_fails(self):
        with self.failing_replace(lambda s, t: s == self.arch):
            with self.assertRaises(OSError):
                self.restore()
        self.assert_original_set()

    def test_moving_the_restored_archive_in_fails(self):
        with self.failing_replace(lambda s, t: t == self.arch and s.suffix == ".part"):
            with self.assertRaises(OSError):
                self.restore()
        self.assert_original_set()

    def test_a_retry_after_the_failure_restores_the_whole_set(self):
        with self.failing_replace(lambda s, t: t == self.db_path):
            with self.assertRaises(OSError):
                self.restore()
        self.restore()
        self.assert_restored_set()
        asides = list(self.db_path.parent.glob("estock-archive-replaced-*"))
        self.assertEqual(len(asides), 1)                       # the replaced history is kept, never deleted
        self.assertEqual(asides[0].read_bytes(), LIVE_ARCH)


class TestBackupWithoutArchive(Base):
    backup_archive = None

    def test_final_database_replacement_fails(self):
        with self.failing_replace(lambda s, t: t == self.db_path):
            with self.assertRaises(OSError):
                self.restore()
        self.assert_original_set()


class TestLiveWithoutArchive(Base):
    live_archive = None

    def test_final_database_replacement_fails(self):
        with self.failing_replace(lambda s, t: t == self.db_path):
            with self.assertRaises(OSError):
                self.restore()
        self.assert_original_set()


class TestCutRestoreIsFinishedAtTheNextStart(Base):
    """Power cut or kill between the moves: no code runs, so the next start settles it from the journal."""

    def cut_at(self, match):
        with self.failing_replace(match), mock.patch.object(backup, "_undo_restore", lambda *a, **k: None):
            with self.assertRaises(OSError):
                self.restore()
        self.assertTrue((self.db_path.parent / backup.RESTORE_JOURNAL).exists())

    def test_cut_before_the_database_moved_goes_back_to_the_original_set(self):
        self.cut_at(lambda s, t: t == self.db_path)
        self.assertEqual(self.arch.read_bytes(), OLD_ARCH)      # the mixed state the cut left
        self.assertEqual(backup.finish_interrupted_restore(self.db_path), "rolled_back")
        self.assert_original_set()

    def test_cut_between_the_two_archive_moves(self):
        self.cut_at(lambda s, t: t == self.arch and s.suffix == ".part")
        self.assertFalse(self.arch.exists())
        self.assertEqual(backup.finish_interrupted_restore(self.db_path), "rolled_back")
        self.assert_original_set()

    def test_cut_after_the_database_moved_keeps_the_restored_set(self):
        real = backup._restore_done

        def cut(*a, **k):
            raise OSError("killed (injected)")
        with mock.patch.object(backup, "_restore_done", cut), mock.patch.object(backup, "_undo_restore", lambda *a, **k: None):
            with self.assertRaises(OSError):
                self.restore()
        self.assertIsNotNone(real)
        self.assertEqual(backup.finish_interrupted_restore(self.db_path), "completed")
        self.assert_restored_set()

    def test_the_program_settles_it_when_it_starts(self):
        from app import config
        from app.api.app import create_app
        self.cut_at(lambda s, t: t == self.db_path)
        from dataclasses import replace
        s = replace(config.load(), data_dir=self.db_path.parent, db_path=self.db_path, upload_dir=self.up)
        create_app(settings=s).state.db.close()
        self.assertEqual(self.arch.read_bytes(), LIVE_ARCH)
        self.assertFalse((self.db_path.parent / backup.RESTORE_JOURNAL).exists())

    def test_nothing_to_finish_is_a_no_op(self):
        self.assertIsNone(backup.finish_interrupted_restore(self.db_path))
        self.assert_original_set()


class TestAttachmentCopyNeverDamagesAGoodLiveFile(DBTestCase):
    def test_a_failed_copy_leaves_the_identical_live_file_whole(self):
        import hashlib
        from app.db import new_id
        up = self.tmp / "uploads"
        data = b"%PDF-1.4 synthetic invoice " * 50
        sha = hashlib.sha256(data).hexdigest()
        f = up / self.ctx.org_id / f"{sha}.pdf"
        f.parent.mkdir(parents=True)
        f.write_bytes(data)
        with self.db.transaction():
            self.db.execute("INSERT INTO source_files (id, org_id, sha256, filename, mime_type, size_bytes, storage_path,"
                            " uploaded_by) VALUES (?,?,?,?,?,?,?,?)",
                            (new_id(), self.ctx.org_id, sha, "inv.pdf", "application/pdf", len(data), str(f), self.ctx.user_id))
        bk = backup.create(self.db, up, self.tmp / "bk")
        db_path = Path(self.db.path)
        self.db.close()
        real = shutil.copy2

        def copy2(src, dst, **kw):
            if Path(dst).parent == f.parent:
                Path(dst).write_bytes(b"half")              # a copy cut by a full disk
                raise OSError(28, "No space left on device (injected)")
            return real(src, dst, **kw)
        with mock.patch.object(shutil, "copy2", copy2):
            try:
                backup.restore(bk, db_path, up, self.tmp / "pre")
            except OSError:
                pass
        self.assertEqual(f.read_bytes(), data)
        self.db = Database(db_path)


class TestManageSettlesACutRestore(Base):
    def test_every_manage_command_settles_it_before_opening_the_database(self):
        from dataclasses import replace
        import manage
        from app import config
        with self.failing_replace(lambda s, t: t == self.db_path), \
                mock.patch.object(backup, "_undo_restore", lambda *a, **k: None):
            with self.assertRaises(OSError):
                self.restore()
        s = replace(config.load(), data_dir=self.db_path.parent, db_path=self.db_path, upload_dir=self.up,
                    backup_dir=self.tmp / "auto")
        manage._db(s).close()
        self.assert_original_set()
