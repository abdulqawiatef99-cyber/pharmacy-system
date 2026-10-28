"""Immutable code releases: failure boundaries and startup recovery. No real data."""
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from app.core import updater
import launcher
import release_bootstrap as releases


def package(files):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        for name, body in files.items():
            z.writestr("release/" + name, body)
    return data.getvalue()


class TestAtomicReleases(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "program"
        self.home = self.base / "home" / "data" / "code-releases"
        (self.root / "app" / "migrations").mkdir(parents=True)
        (self.root / "manage.py").write_text("old")
        (self.root / "requirements.txt").write_text("same")
        (self.root / "app" / "migrations" / "0001.sql").write_text("same")
        (self.root / "data").mkdir()
        self.sentinel = self.root / "data" / "synthetic.db"
        self.sentinel.write_bytes(b"SYNTHETIC DATA")
        self.files = {"manage.py": "new", "app/x.py": "new", "release_bootstrap.py": "",
                      "release_protocol.json": '{"protocol":1}', "requirements.txt": "same",
                      "app/migrations/0001.sql": "same"}

    def stage(self, files=None):
        return updater.apply_zip(package(files or self.files), self.root, code_home=self.home)

    def activate(self, staged):
        releases.activate(self.root, self.home, staged["id"], {"sha": "test"},
                          expected_active=None)

    def unchanged(self):
        self.assertEqual((self.root / "manage.py").read_text(), "old")
        self.assertEqual(self.sentinel.read_bytes(), b"SYNTHETIC DATA")

    def test_pending_release_requires_supervised_serve(self):
        self.activate(self.stage())
        with patch.dict("os.environ", {"PHARM_DATA_DIR": str(self.home.parent),
                                       "PHARM_LAUNCHER": "", "PHARM_RELEASE_CANDIDATE": ""}), \
                patch.object(sys, "argv", ["manage.py", "serve"]):
            with self.assertRaisesRegex(releases.ReleaseError, "supervision"):
                releases.run_selected(self.root)
        self.assertTrue(releases.read_state(self.home)["pending"])

    def test_staging_does_not_touch_running_code(self):
        staged = self.stage()
        self.unchanged()
        self.assertEqual((Path(staged["release"]) / "manage.py").read_text(), "new")
        self.assertEqual(releases.current_root(self.root, self.home), self.root)

    def test_copy_failure_does_not_leave_mixed_code(self):
        copy = shutil.copyfileobj
        calls = 0
        def fail(src, dst, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("Synthetic disk failure")
            return copy(src, dst, *args, **kwargs)
        with patch("app.core.updater.shutil.copyfileobj", side_effect=fail):
            with self.assertRaises(OSError):
                self.stage()
        self.unchanged()
        self.assertEqual(list((self.home / "releases").iterdir()), [])
        self.assertEqual(releases.read_state(self.home), {})

    def test_pointer_replace_failure_keeps_old_selection(self):
        staged = self.stage()
        replace = releases.os.replace
        def fail(src, dst):
            if Path(dst).name == "active-release.json":
                raise OSError("Synthetic pointer failure")
            return replace(src, dst)
        with patch("release_bootstrap.os.replace", side_effect=fail):
            with self.assertRaises(OSError):
                self.activate(staged)
        self.assertEqual(releases.current_root(self.root, self.home), self.root)
        self.unchanged()

    def test_same_archive_reuses_verified_complete_release(self):
        first, second = self.stage(), self.stage()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["release"], second["release"])
        releases.verify_release(Path(second["release"]))

    def test_tampered_release_is_rejected_before_activation(self):
        staged = self.stage()
        (Path(staged["release"]) / "manage.py").write_text("tampered")
        with self.assertRaises(releases.ReleaseError):
            self.activate(staged)
        self.assertEqual(releases.current_root(self.root, self.home), self.root)

    def test_schema_or_dependency_change_cannot_activate(self):
        for name in ("requirements.txt", "app/migrations/0001.sql", "app/migrations/0002.sql"):
            with self.subTest(name=name):
                staged = self.stage({**self.files, name: "changed"})
                with self.assertRaises(releases.ReleaseError):
                    self.activate(staged)
                self.unchanged()
                self.assertEqual(releases.read_state(self.home), {})

    def test_pending_activation_cannot_destroy_rollback_target(self):
        first = self.stage()
        self.activate(first)
        second = self.stage({**self.files, "app/x.py": "newer"})
        with self.assertRaises(releases.ReleaseError):
            releases.activate(self.root, self.home, second["id"], {}, expected_active=first["id"])
        self.assertTrue(releases.read_state(self.home)["pending"])
        self.assertEqual(releases.read_state(self.home)["active"]["id"], first["id"])

    def test_reactivating_same_release_cannot_make_it_its_own_rollback(self):
        staged = self.stage()
        self.activate(staged)
        releases.confirm(self.home, staged["id"])
        before = releases.read_state(self.home)
        with self.assertRaises(releases.ReleaseError):
            releases.activate(self.root, self.home, staged["id"], {}, expected_active=staged["id"])
        self.assertEqual(releases.read_state(self.home), before)

    def test_activation_lease_rejects_changed_current_release(self):
        first = self.stage()
        self.activate(first)
        releases.confirm(self.home, first["id"])
        second = self.stage({**self.files, "app/x.py": "newer"})
        with self.assertRaises(releases.ReleaseError):
            releases.activate(self.root, self.home, second["id"], {}, expected_active=None)
        self.assertEqual(releases.current_root(self.root, self.home), Path(first["release"]))

    def test_manual_rollback_selects_old_code_without_restoring_data(self):
        staged = self.stage()
        self.activate(staged)
        releases.rollback(self.home, expected_id=staged["id"])
        self.assertEqual(releases.current_root(self.root, self.home), self.root)
        self.assertFalse(releases.read_state(self.home)["pending"])
        self.assertTrue(Path(staged["release"]).exists())
        self.unchanged()

    def test_process_exit_during_pointer_publication_has_complete_selection(self):
        staged = self.stage()
        for after in (False, True):
            with self.subTest(after_publication=after):
                state_file = self.home / "active-release.json"
                state_file.unlink(missing_ok=True)
                script = f"""import os
from pathlib import Path
import release_bootstrap as r
original = r.os.replace
def stop(src, dst):
    if Path(dst).name == "active-release.json":
        if {after!r}:
            original(src, dst)
        os._exit(9)
    return original(src, dst)
r.os.replace = stop
r.activate(Path({str(self.root)!r}), Path({str(self.home)!r}), {staged['id']!r}, {{}}, expected_active=None)
"""
                child = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=15)
                self.assertEqual(child.returncode, 9, child.stderr)
                selected = releases.current_root(self.root, self.home)
                self.assertEqual(selected, Path(staged["release"]) if after else self.root)
                self.unchanged()
                # The OS released the crashed process's lock.
                with releases.locked(self.home):
                    pass

    def test_launcher_rolls_back_failed_first_start(self):
        staged = self.stage()
        self.activate(staged)
        bad, good = Mock(), Mock()
        bad.poll.return_value = 1
        good.wait.return_value = 0
        app_home = self.home.parent.parent
        with patch.object(launcher, "ROOT", self.root), patch.object(launcher, "home", return_value=app_home), \
                patch.object(launcher, "running", return_value=False), \
                patch.object(launcher, "candidate_ready", return_value=False), \
                patch.object(launcher.threading, "Thread"), \
                patch.object(launcher.subprocess, "Popen", side_effect=[bad, good]) as spawn:
            self.assertEqual(launcher.main(), 0)
        self.assertEqual(Path(spawn.call_args_list[0].args[0][1]).parent, Path(staged["release"]))
        self.assertEqual(Path(spawn.call_args_list[1].args[0][1]).parent, self.root)
        self.assertIsNone(releases.read_state(self.home)["active"])
        self.unchanged()

    def test_launcher_confirms_healthy_first_start(self):
        staged = self.stage()
        self.activate(staged)
        process = Mock()
        process.wait.return_value = 0
        with patch.object(launcher, "ROOT", self.root), \
                patch.object(launcher, "home", return_value=self.home.parent.parent), \
                patch.object(launcher, "running", return_value=False), \
                patch.object(launcher, "candidate_ready", return_value=True), \
                patch.object(launcher.threading, "Thread"), \
                patch.object(launcher.subprocess, "Popen", return_value=process):
            self.assertEqual(launcher.main(), 0)
        self.assertFalse(releases.read_state(self.home)["pending"])

    def test_windows_unsafe_names_and_case_collisions_are_refused(self):
        for extra in ({"app/../evil.py": "x"}, {"app\\evil.py": "x"}, {"app/x.py:stream": "x"},
                      {"app/NUL.txt": "x"}, {"App/y.py": "x"}, {"app/X.py": "x"}):
            with self.subTest(extra=extra):
                with self.assertRaises(updater.UpdateError):
                    self.stage({**self.files, **extra})
                self.unchanged()

    def test_protected_data_is_not_part_of_release(self):
        staged = self.stage({**self.files, "DATA/synthetic.db": "bad", ".venv/key": "bad"})
        candidate = Path(staged["release"])
        self.assertFalse((candidate / "DATA").exists())
        self.assertFalse((candidate / ".venv").exists())
        self.unchanged()

    def test_health_requires_this_process_boot_id(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.status = 200
        with patch.object(launcher.urllib.request, "urlopen", return_value=response):
            response.read.return_value = b'{"ok":true,"boot_id":"other"}'
            self.assertFalse(launcher.healthy_boot("expected"))
            response.read.return_value = b'{"ok":true,"boot_id":"expected"}'
            self.assertTrue(launcher.healthy_boot("expected"))
            response.read.return_value = b'{"ok":false,"boot_id":"expected"}'
            self.assertFalse(launcher.healthy_boot("expected"))

    def test_dead_candidate_is_never_confirmed_by_an_unrelated_healthy_server(self):
        process = Mock()
        process.poll.return_value = 1
        with patch.object(launcher, "healthy_boot", return_value=True) as healthy:
            self.assertFalse(launcher.candidate_ready(process, "expected"))
        healthy.assert_not_called()


from tests.test_api import APICase


class TestBootHealthAPI(APICase):
    def test_health_echoes_nonauthentication_startup_nonce(self):
        with patch.dict("os.environ", {"PHARM_BOOT_ID": "synthetic-boot-id"}):
            response = self.c.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["boot_id"], "synthetic-boot-id")
