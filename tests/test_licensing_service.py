"""The licence file on a PC (app/licensing/service.py, clock.py): check, install, and the memory of
the latest time seen. Also pins that the running program does not use any of this yet."""
import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core import signed_envelope as se
from app.licensing import clock, fingerprint as fp, license as L, service

UTC = timezone.utc
T0 = datetime(2026, 12, 1, tzinfo=UTC)
PC_A = fp.from_raw({"board": "4c4c4544-0042-3510-8051-c7c04f4a3432", "system": "guid-a", "disk": "a4f1c2d3"})
PC_B = fp.from_raw({"board": "9f2d1a70-11c3-4e57-a2b8-0d6f5e3c7a91", "system": "guid-b", "disk": "b5e2d3c4"})
ROOT = Path(__file__).resolve().parents[1]


def make(kind="subscription", *, days=30, grace=7, devices=(PC_A,), lid="PH-20261201-ABC123") -> L.License:
    expires = None if kind == "perpetual" else T0 + timedelta(days=days, seconds=-1)
    return L.License(license_id=lid, customer="صيدلية تجربة", kind=kind, issued_at=T0, starts_at=T0,
                     expires_at=expires, grace_days=0 if kind == "perpetual" else grace, updates_until=None,
                     devices=tuple(devices), features=())


class Base(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.trusted = [se.public_key_bytes(self.key)]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name) / "data"
        self.extra = Path(tmp.name) / "outside"
        self.data.mkdir()

    def check(self, now, **kw):
        kw.setdefault("device", PC_A)
        return service.check(self.data, now=now, trusted=self.trusted, extra_clock_dir=self.extra, **kw)

    def install(self, lic, now=T0, key=None, **kw):
        kw.setdefault("device", PC_A)
        return service.install(self.data, L.issue(lic, key or self.key), now=now, trusted=self.trusted,
                               extra_clock_dir=self.extra, **kw)


class TestCheck(Base):
    def test_no_file_is_missing_not_an_error(self):
        s = self.check(T0)
        self.assertEqual((s.state, s.usable, s.license), ("missing", False, None))

    def test_a_good_licence_is_valid_on_its_pc_and_refused_on_another(self):
        self.install(make())
        self.assertEqual(self.check(T0 + timedelta(days=1)).state, "valid")
        self.assertEqual(self.check(T0 + timedelta(days=1), device=PC_B).state, "wrong_device")

    def test_a_damaged_or_foreign_file_is_invalid_with_the_reason(self):
        path = service.license_path(self.data)
        for content, reason in ((b"garbage", "malformed"),
                                (L.issue(make(), Ed25519PrivateKey.generate()), "untrusted_key"),
                                (b"x" * (L.MAX_LICENSE_BYTES + 1), "malformed")):
            path.write_bytes(content)
            s = self.check(T0)
            self.assertEqual((s.state, s.reason, s.usable), ("invalid", reason, False))
        good = bytearray(L.issue(make(), self.key))
        good[len(good) // 2] ^= 1
        path.write_bytes(bytes(good))
        self.assertEqual(self.check(T0).state, "invalid")

    def test_with_the_real_built_in_key_list_everything_is_refused_today(self):
        service.license_path(self.data).write_bytes(L.issue(make(), self.key))
        s = service.check(self.data, now=T0, device=PC_A)                 # trusted=None: keys.PUBLISHER_KEYS
        self.assertEqual((s.state, s.reason), ("invalid", "no_trusted_key"))

    def test_the_clock_cannot_be_turned_back_past_what_the_program_saw(self):
        self.install(make(days=30, grace=7))
        after_the_end = T0 + timedelta(days=60)
        self.assertEqual(self.check(after_the_end).state, "expired")
        s = self.check(T0 + timedelta(days=5))                           # the owner of the PC sets the date back
        self.assertEqual((s.state, s.usable, s.clock_back), ("expired", False, True))

    def test_deleting_one_copy_of_the_mark_changes_nothing(self):
        self.install(make(days=30, grace=7))
        self.check(T0 + timedelta(days=60))
        (self.data / clock.FILE_NAME).unlink()
        self.assertEqual(self.check(T0 + timedelta(days=5)).state, "expired")
        self.assertTrue((self.data / clock.FILE_NAME).exists())          # and the copy is written again

    def test_the_pharmacys_own_records_are_a_witness_too(self):
        self.install(make(days=30, grace=7))
        for p in service.clock_paths(self.data, self.extra):             # every copy of the mark deleted
            p.unlink()
        last_sale = T0 + timedelta(days=60)
        s = self.check(T0 + timedelta(days=5), evidence=last_sale)
        self.assertEqual((s.state, s.clock_back), ("expired", True))

    def test_a_read_only_look_does_not_move_the_mark(self):
        self.install(make())
        before = clock.read(service.clock_paths(self.data, self.extra))
        self.check(T0 + timedelta(days=20), remember=False)
        self.assertEqual(clock.read(service.clock_paths(self.data, self.extra)), before)

    def test_revoked_licence(self):
        self.install(make("perpetual"))
        self.assertEqual(self.check(T0, revoked={"PH-20261201-ABC123"}).state, "revoked")


class TestInstall(Base):
    def test_install_writes_the_file_and_reports_the_state(self):
        s = self.install(make())
        self.assertEqual(s.state, "valid")
        self.assertEqual(L.verify(service.license_path(self.data).read_bytes(), self.trusted).license_id,
                         "PH-20261201-ABC123")

    def test_bad_files_are_refused_and_change_nothing(self):
        self.install(make())
        path = service.license_path(self.data)
        before = path.read_bytes()
        attempts = {"untrusted_key": lambda: self.install(make(lid="PH-20261201-PIRATE"), key=Ed25519PrivateKey.generate()),
                    "wrong_device": lambda: self.install(make(devices=(PC_B,), lid="PH-20261201-OTHER1")),
                    "revoked": lambda: self.install(make(lid="PH-20261201-GONE01"), revoked={"PH-20261201-GONE01"}),
                    "malformed": lambda: service.install(self.data, b"junk", now=T0, device=PC_A, trusted=self.trusted)}
        for reason, attempt in attempts.items():
            with self.assertRaises(service.InstallRefused) as cm:
                attempt()
            self.assertEqual(cm.exception.status.reason, reason)
            self.assertEqual(path.read_bytes(), before, reason)
        self.assertFalse((self.data / service.PREVIOUS_FILE).exists())

    def test_an_already_ended_licence_is_not_installed(self):
        with self.assertRaises(service.InstallRefused) as cm:
            self.install(make(days=30, grace=7), now=T0 + timedelta(days=90))
        self.assertEqual(cm.exception.status.state, "expired")
        self.assertFalse(service.license_path(self.data).exists())

    def test_renewal_replaces_the_old_licence_and_keeps_it_aside(self):
        self.install(make(days=30))
        old = service.license_path(self.data).read_bytes()
        renewal = L.License(**{**make().__dict__, "license_id": "PH-20261225-REN001",
                               "expires_at": T0 + timedelta(days=395)})
        s = self.install(renewal, now=T0 + timedelta(days=33))            # installed during the grace period
        self.assertEqual((s.state, s.license.license_id), ("valid", "PH-20261225-REN001"))
        self.assertEqual((self.data / service.PREVIOUS_FILE).read_bytes(), old)
        self.assertEqual(self.check(T0 + timedelta(days=100)).state, "valid")

    def test_a_working_licence_is_never_traded_for_one_that_has_not_started(self):
        self.install(make("perpetual"))
        later = L.License(**{**make().__dict__, "license_id": "PH-20270101-LATER1",
                             "starts_at": T0 + timedelta(days=30), "expires_at": T0 + timedelta(days=60)})
        with self.assertRaises(service.InstallRefused):
            self.install(later)
        self.assertEqual(self.check(T0).license.kind, "perpetual")

    def test_a_licence_that_starts_later_is_accepted_on_an_empty_pc(self):
        later = L.License(**{**make().__dict__, "starts_at": T0 + timedelta(days=30),
                             "expires_at": T0 + timedelta(days=60)})
        self.assertEqual(self.install(later).state, "not_yet_valid")
        self.assertEqual(self.check(T0 + timedelta(days=31)).state, "valid")

    def test_installing_the_same_file_twice_is_harmless(self):
        data = L.issue(make(), self.key)
        for _ in range(2):
            service.install(self.data, data, now=T0, device=PC_A, trusted=self.trusted)
        self.assertFalse((self.data / service.PREVIOUS_FILE).exists())

    def test_no_temporary_files_are_left(self):
        self.install(make())
        self.assertEqual(sorted(p.name for p in self.data.iterdir()), [clock.FILE_NAME, service.LICENSE_FILE])

    def test_activation_request_for_a_given_pc(self):
        self.assertEqual(fp.decode_request(service.activation_request(PC_A, "PC-1")), (PC_A, "PC-1"))


class TestClock(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.paths = [Path(tmp.name) / "a" / clock.FILE_NAME, Path(tmp.name) / "b" / clock.FILE_NAME]

    def test_nothing_stored_is_none(self):
        self.assertIsNone(clock.read(self.paths))
        self.assertIsNone(clock.high_water(self.paths))
        self.assertEqual(clock.high_water(self.paths, None, T0), T0)

    def test_the_mark_only_goes_up(self):
        self.assertEqual(clock.remember(self.paths, T0), T0)
        self.assertEqual(clock.remember(self.paths, T0 - timedelta(days=400)), T0)      # clock turned back
        self.assertEqual(clock.remember(self.paths, T0 + timedelta(days=1)), T0 + timedelta(days=1))
        self.assertEqual(clock.read(self.paths), T0 + timedelta(days=1))
        for p in self.paths:
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"seen": "2026-12-02T00:00:00Z"})

    def test_evidence_raises_the_mark(self):
        self.assertEqual(clock.remember(self.paths, T0, T0 + timedelta(days=9)), T0 + timedelta(days=9))

    def test_the_latest_copy_wins_and_a_damaged_copy_is_skipped(self):
        clock.remember(self.paths, T0)
        self.paths[0].write_text(json.dumps({"seen": "2027-06-01T00:00:00Z"}), encoding="utf-8")
        self.assertEqual(clock.read(self.paths), datetime(2027, 6, 1, tzinfo=UTC))
        for junk in ("", "not json", "[]", '{"seen": 5}', '{"seen": "yesterday"}', '{"other": 1}'):
            self.paths[0].write_text(junk, encoding="utf-8")
            self.assertEqual(clock.read(self.paths), T0, junk)

    def test_a_folder_that_cannot_be_written_does_not_stop_the_program(self):
        blocker = self.paths[0].parent
        blocker.parent.mkdir(parents=True, exist_ok=True)
        blocker.write_text("a file where the folder should be", encoding="utf-8")
        self.assertEqual(clock.remember(self.paths, T0), T0)
        self.assertEqual(clock.read(self.paths), T0)                     # the other copy holds it

    def test_naive_times_are_refused_not_read_as_local_time(self):
        with self.assertRaises(ValueError):
            clock.remember(self.paths, datetime(2026, 12, 1))
        with self.assertRaises(ValueError):
            clock.remember(self.paths, T0, datetime(2027, 1, 1))
        with self.assertRaises(ValueError):
            clock.high_water(self.paths, datetime(2027, 1, 1))
        self.assertIsNone(clock.read(self.paths))                        # and nothing was written


class TestNotWiredYet(unittest.TestCase):
    """This batch must not change what the program does (not wired yet). When the licence is wired
    into the launcher and the screens, this test is the one to change, on purpose."""

    def test_nothing_the_program_runs_imports_licensing_or_the_envelope(self):
        program = [p for p in ROOT.glob("app/**/*.py")
                   if "licensing" not in p.relative_to(ROOT).parts
                   # the protection libraries themselves; release_signature.py is not wired either
                   # (tests/test_release_signature.py pins that nothing imports it)
                   and p.name not in ("signed_envelope.py", "release_signature.py")]
        program += [ROOT / "manage.py", ROOT / "launcher.py", ROOT / "release_bootstrap.py"]
        self.assertGreater(len(program), 50)
        uses = re.compile(r"^\s*(from|import)\s+[\w., ]*\b(licensing|signed_envelope)\b", re.M)
        for p in program:
            found = uses.search(p.read_text(encoding="utf-8"))
            self.assertIsNone(found, f"{p.relative_to(ROOT)}: {found.group(0).strip() if found else ''}")

    def test_the_import_check_itself_catches_every_way_of_importing(self):
        uses = re.compile(r"^\s*(from|import)\s+[\w., ]*\b(licensing|signed_envelope)\b", re.M)
        for line in ("from app.licensing import service", "import app.licensing.service as lic",
                     "from app import licensing", "    from app.core import audit, signed_envelope",
                     "from app.core.signed_envelope import sign", "from . import licensing"):
            self.assertIsNotNone(uses.search(line), line)
        for line in ("# drug licensing rules of the authority", 'text = "licensing"', "licensing_note = 1"):
            self.assertIsNone(uses.search(line), line)

    def test_the_library_touches_no_database_network_or_screen(self):
        for p in [*(ROOT / "app" / "licensing").glob("*.py"), ROOT / "app" / "core" / "signed_envelope.py"]:
            text = p.read_text(encoding="utf-8")
            for forbidden in ("sqlite3", "app.db", "urllib", "requests", "httpx", "starlette", "subprocess"):
                self.assertNotIn(f"import {forbidden}", text, f"{p.name}: {forbidden}")
                self.assertNotIn(f"from {forbidden}", text, f"{p.name}: {forbidden}")


if __name__ == "__main__":
    unittest.main()
