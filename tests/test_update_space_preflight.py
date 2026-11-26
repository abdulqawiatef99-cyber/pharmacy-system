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

    def test_temp_counts_the_sort_files_of_the_verification(self):
        # Codex P2: verifying keeps the unpacked database and history in TEMP while SQLite's balance check
        # spills its sort there too (234 MB measured on two million movements): one more database of room
        need = {n["volume"]: n["bytes"] for n in su.space_needs(self.s, self.backups, needs_db=False)}
        self.assertEqual(need["temp"], 2 * self.DB + self.ARCH)
        sparse(self.data / backup.ARCHIVE_MEMBER, 60 * MB)
        (self.data / (backup.ARCHIVE_MEMBER + ".sha256")).write_text(
            backup._sha256(self.data / backup.ESTOCK_ARCHIVE), encoding="ascii")
        need = {n["volume"]: n["bytes"] for n in su.space_needs(self.s, self.backups, needs_db=False)}
        self.assertEqual(need["temp"], 2 * self.DB + self.ARCH)       # a ready .xz does not shrink the unpacking

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
        unmarked = {n["volume"]: n["bytes"] for n in su.space_needs(self.s, self.backups, needs_db=False)}
        self.assertEqual(unmarked, before)                # no matching mark: the backup would compress again
        mark = self.data / (backup.ARCHIVE_MEMBER + ".sha256")
        mark.write_text("0" * 64, encoding="ascii")
        self.assertEqual({n["volume"]: n["bytes"] for n in su.space_needs(self.s, self.backups, needs_db=False)}, before)
        mark.write_text(backup._sha256(self.data / backup.ESTOCK_ARCHIVE), encoding="ascii")
        after = {n["volume"]: n["bytes"] for n in su.space_needs(self.s, self.backups, needs_db=False)}
        self.assertEqual(before["backup"] - after["backup"], self.ARCH - 60 * MB)
        self.assertLess(after["data"], before["data"])                # nothing new is compressed next to the data


class TestAMissingDiskIsARefusalNotACrash(Base):
    def test_backup_folder_on_a_drive_that_is_gone(self):
        def gone(path):
            if self.volume(path) == "backup":
                raise FileNotFoundError(2, "The system cannot find the path specified (injected)")
            return self.volume(path)
        with mock.patch.object(su, "_volume", gone):
            msg = self.refused()
        self.assertIn("لم يتغير شيء", msg)
        self.assertIn(str(self.backups), msg)


class TestDatabaseOnAnotherDiskThanTheDataFolder(Base):
    """Codex P2: with PHARM_DB_PATH elsewhere, the archive and its .xz cache live beside the database."""

    def setUp(self):
        super().setUp()
        self.dbdisk = self.data.parent.parent / "dbdisk"
        self.dbdisk.mkdir()
        for name in ("pharmacy.db", backup.ESTOCK_ARCHIVE):
            (self.data / name).replace(self.dbdisk / name)
        self.s = replace(self.s, db_path=self.dbdisk / "pharmacy.db")
        self.free["db"] = 10 ** 12

    def volume(self, path) -> str:
        p = Path(path).resolve()
        if p == self.dbdisk or self.dbdisk in p.parents:
            return "db"
        return super().volume(path)

    def test_the_cache_is_budgeted_where_it_is_written(self):
        need = {n["volume"]: n["bytes"] for n in su.space_needs(self.s, self.backups, needs_db=True)}
        self.assertEqual(need["db"], self.ARCH)                # the .xz cache beside the archive
        self.assertEqual(need["data"], 3 * self.DB)            # the migrated copy stays in the data folder
        self.free["db"] = self.ARCH                            # no margin left on the database's disk
        self.assertIn(str(self.dbdisk), self.refused())


class TestSchedulingNeverBlocksTheTill(unittest.TestCase):
    def test_the_space_check_runs_off_the_event_loop(self):
        # Codex P2: the check hashes the whole E-Stock archive; inside the async handler it would hold every
        # cashier request for that long
        src = (Path(__file__).resolve().parents[1] / "app" / "api" / "app.py").read_text(encoding="utf-8")
        at = src.index('if action == "schedule":')
        body = src[at:src.index('if action == "cancel":', at)]
        self.assertIn("await run_in_threadpool(su.request, s.data_dir, when, ctx.user_id, settings=s)", body)
        self.assertNotIn("= su.request(", body)


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

    def test_a_missing_backup_drive_drops_the_request_with_the_reason(self):
        su.request(self.data, "now", "owner", settings=self.s)

        def gone(path):
            raise FileNotFoundError(2, "The system cannot find the path specified (injected)")
        left = []
        with mock.patch.object(su, "busy_reason", lambda *a, **k: None), mock.patch.object(su, "_volume", gone):
            self.assertFalse(su.trigger(self.s, mock.MagicMock(), in_flight=lambda: 0, exit=left.append))
        self.assertEqual((left, su.pending_request(self.data)), ([], {}))
        self.assertTrue(su.last_refusal(self.data)["reason"])

    def test_a_selected_update_clears_an_old_refusal(self):
        releases.atomic_json(self.home / "apply-refused.json", {"release": "r0", "reason": "old"})
        with mock.patch.object(releases, "activate"), mock.patch.object(releases, "log_event"):
            su._select(self.data, self.home, {"release": "r1", "sha": "a" * 40, "channel": "main", "commit_time": 1})
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


class TestScheduleRoute(__import__("tests.test_api", fromlist=["APICase"]).APICase):
    """The schedule action end to end: refused with the reason on screen, accepted when there is room."""

    def test_schedule_is_refused_for_space_then_accepted(self):
        from tests.test_api import H
        self.http_login()
        home = su.home_of(self.s.data_dir)
        releases.atomic_json(home / "prepared.json", {"release": "r1", "needs_db": True})
        with mock.patch.object(su, "_free", lambda p: 10 * MB):
            r = self.c.post("/api/system/update", json={"action": "schedule", "when": "now"}, headers=H)
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("المساحة", r.json()["error"]["message"] if isinstance(r.json().get("error"), dict) else r.text)
        self.assertEqual(su.pending_request(self.s.data_dir), {})
        with mock.patch.object(su, "_free", lambda p: 10 ** 12):
            r = self.c.post("/api/system/update", json={"action": "schedule", "when": "now"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(su.pending_request(self.s.data_dir)["release"], "r1")
