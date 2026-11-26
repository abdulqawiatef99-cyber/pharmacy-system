"""#461: the smart update refuses BEFORE the cashier is stopped when the verified backup cannot fit: the
space check counts the E-Stock archive and the attachments, and looks at the data, backup and TEMP volumes
separately. Synthetic files only (sparse: no real disk space is used)."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app import config
from app.core import backup, smart_update as su
from app.core.updater import UpdateError
import release_bootstrap as releases

MB = 1024 * 1024


def sparse(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.truncate(size)


class Base(unittest.TestCase):
    DB, ARCH, UPLOADS = 40 * MB, 500 * MB, 30 * MB        # an archive far larger than the main database

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        self.data, self.backups, self.temp = base / "home" / "data", base / "backups", base / "temp"
        for d in (self.data, self.backups, self.temp):
            d.mkdir(parents=True)
        self.s = replace(config.load(), data_dir=self.data, db_path=self.data / "pharmacy.db",
                         upload_dir=self.data / "uploads", backup_dir=self.backups)
        sparse(self.s.db_path, self.DB)
        if self.ARCH:
            sparse(self.data / backup.ESTOCK_ARCHIVE, self.ARCH)
        sparse(self.s.upload_dir / "org" / "a.pdf", self.UPLOADS)
        self.free = {"data": 10 ** 12, "backup": 10 ** 12, "temp": 10 ** 12}      # plenty everywhere
        for name, target in (("_volume", self.volume), ("_free", lambda p: self.free[self.volume(p)])):
            patch = mock.patch.object(su, name, target)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(su.tempfile, "gettempdir", lambda: str(self.temp))
        patch.start()
        self.addCleanup(patch.stop)

    def volume(self, path) -> str:
        """Three separate disks: data, backups and the system TEMP."""
        p = Path(path).resolve()
        for name, root in (("backup", self.backups), ("temp", self.temp)):
            if p == root or root in p.parents:
                return name
        return "data"

    def refused(self, needs_db=True) -> str:
        with self.assertRaises(UpdateError) as e:
            su.check_space(self.s, self.backups, needs_db=needs_db)
        return str(e.exception)


class TestEachVolumeIsCheckedOnItsOwn(Base):
    def test_enough_room_everywhere_passes(self):
        su.check_space(self.s, self.backups, needs_db=True)
        su.check_space(self.s, self.backups, needs_db=False)

    def test_the_old_rule_alone_would_have_passed(self):
        # 3 x the main database + 200 MB: true on every disk here, yet the backup cannot be written
        self.free = {k: 3 * self.DB + 200 * MB + 1 for k in self.free}
        self.assertIn("النسخ الاحتياطية", self.refused())

    def test_backup_volume_too_small_for_the_archive_and_attachments(self):
        self.free["backup"] = self.DB + self.ARCH                     # no room left for the attachments
        msg = self.refused()
        self.assertIn("النسخ الاحتياطية", msg)
        self.assertIn(str(self.backups), msg)
        self.assertIn("لم يتغير شيء", msg)
        self.assertRegex(msg, r"\d+ ميجا")                            # how much is needed and how much is free

    def test_temp_volume_too_small_to_unpack_the_backup_for_verification(self):
        self.free["temp"] = self.DB + self.ARCH - 1
        msg = self.refused()
        self.assertIn("المؤقت", msg)
        self.assertIn(str(self.temp), msg)

    def test_data_volume_too_small_for_the_migrated_copy(self):
        self.free["data"] = 2 * self.DB
        self.assertIn("البيانات", self.refused())

    def test_a_code_only_update_takes_a_backup_too(self):
        self.free["backup"] = 50 * MB
        self.assertIn("النسخ الاحتياطية", self.refused(needs_db=False))
        self.free["backup"] = 10 ** 12
        self.free["data"] = self.ARCH + su.SPACE_MARGIN               # no migrated copy: the data disk needs less
        su.check_space(self.s, self.backups, needs_db=False)

    def test_one_disk_for_everything_needs_the_sum(self):
        with mock.patch.object(su, "_volume", lambda p: "one"), mock.patch.object(su, "_free", lambda p: self.free["data"]):
            need = sum(n["bytes"] for n in su.space_needs(self.s, self.backups, needs_db=True))
            self.free["data"] = need + su.SPACE_MARGIN - 1
            self.assertIn("لم يتغير شيء", self.refused())
            self.free["data"] = need + su.SPACE_MARGIN
            su.check_space(self.s, self.backups, needs_db=True)

    def test_exactly_enough_then_one_byte_less(self):
        need = {n["volume"]: n["bytes"] for n in su.space_needs(self.s, self.backups, needs_db=True)}
        self.assertEqual(set(need), {"data", "backup", "temp"})
        for vol in need:
            with self.subTest(volume=vol):
                self.free = {k: need[k] + su.SPACE_MARGIN for k in need}
                su.check_space(self.s, self.backups, needs_db=True)
                self.free[vol] -= 1
                self.refused()

    def test_a_ready_compressed_archive_is_counted_at_its_real_size(self):
        before = {n["volume"]: n["bytes"] for n in su.space_needs(self.s, self.backups, needs_db=False)}
        sparse(self.data / backup.ARCHIVE_MEMBER, 60 * MB)
        after = {n["volume"]: n["bytes"] for n in su.space_needs(self.s, self.backups, needs_db=False)}
        self.assertEqual(before["backup"] - after["backup"], self.ARCH - 60 * MB)
        self.assertLess(after["data"], before["data"])                # nothing new is compressed next to the data


class TestNoArchive(Base):
    ARCH = 0

    def test_small_pharmacy_needs_little(self):
        self.free = {"data": 3 * self.DB + 200 * MB, "backup": self.DB + self.UPLOADS + 200 * MB,
                     "temp": 2 * self.DB + 200 * MB}
        su.check_space(self.s, self.backups, needs_db=True)


class TestRefusedBeforeTheCashierStops(Base):
    def setUp(self):
        super().setUp()
        self.home = su.home_of(self.data)
        releases.write_capability(self.home)
        releases.atomic_json(self.home / "prepared.json", {"release": "r1", "needs_db": True})

    def test_scheduling_is_refused_with_the_reason(self):
        self.free["backup"] = 100 * MB
        with self.assertRaises(UpdateError) as e:
            su.request(self.data, "now", "owner", settings=self.s)
        self.assertIn("النسخ الاحتياطية", str(e.exception))
        self.assertEqual(su.pending_request(self.data), {})
        self.free["backup"] = 10 ** 12
        self.assertEqual(su.request(self.data, "now", "owner", settings=self.s)["release"], "r1")

    def test_the_trigger_does_not_stop_the_program_when_space_ran_out_since(self):
        su.request(self.data, "now", "owner", settings=self.s)
        self.free["temp"] = 10 * MB
        left = []
        db = mock.MagicMock()
        with mock.patch.object(su, "busy_reason", lambda *a, **k: None):
            self.assertFalse(su.trigger(self.s, db, in_flight=lambda: 0, exit=left.append))
        self.assertEqual(left, [])
        self.assertFalse((self.home / "apply-go.json").exists())
        self.assertEqual(su.pending_request(self.data), {})           # no silent retry every 30 s
        self.assertIn("المؤقت", su.last_refusal(self.data)["reason"])
        self.free["temp"] = 10 ** 12
        su.request(self.data, "now", "owner", settings=self.s)
        with mock.patch.object(su, "busy_reason", lambda *a, **k: None):
            su.trigger(self.s, db, in_flight=lambda: 0, exit=left.append)
        self.assertEqual(left, [75])
        self.assertEqual(su.last_refusal(self.data), {})

    def test_apply_checks_space_before_the_backup_for_both_kinds(self):
        self.free["backup"] = 100 * MB
        for needs_db in (True, False):
            releases.atomic_json(self.home / "prepared.json", {"release": "r1", "needs_db": needs_db})
            with self.subTest(needs_db=needs_db), mock.patch.object(su, "_verify_prepared", lambda *a: self.data), \
                    mock.patch.object(su, "_verified_backup") as made:
                with self.assertRaises(UpdateError) as e:
                    su.apply_prepared(self.s, self.data, "r1", self.backups)
                self.assertIn("النسخ الاحتياطية", str(e.exception))
                made.assert_not_called()
