"""Smart update, stage 0 (installed launcher + release_bootstrap): the journal-driven database swap,
its undo on a failed first start, and resuming after a power cut at any step. Synthetic files only."""
import hashlib
import io
import json
import os
from pathlib import Path
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


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class Stage0Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / "program"
        self.app_home = self.base / "home"
        self.data = self.app_home / "data"
        self.home = releases.code_home(self.data)
        (self.root / "app" / "migrations").mkdir(parents=True)
        (self.root / "manage.py").write_text("old")
        (self.root / "requirements.txt").write_text("same")
        (self.root / "app" / "migrations" / "0001.sql").write_text("one")
        self.data.mkdir(parents=True)
        self.live = self.data / "pharmacy.db"
        self.live.write_bytes(b"OLD DATABASE")
        self.files = {"manage.py": "new", "app/x.py": "new", "release_bootstrap.py": "",
                      "release_protocol.json": '{"protocol":1}', "requirements.txt": "same",
                      "app/migrations/0001.sql": "one", "app/migrations/0002.sql": "two"}

    def stage_and_select(self):
        staged = updater.apply_zip(package(self.files), self.root, code_home=self.home)
        releases.activate(self.root, self.home, staged["id"], {"sha": "test"}, expected_active=None,
                          coordinated=True)
        return staged["id"]

    def prepare_journal(self, release_id, *, candidate=b"NEW DATABASE"):
        cand = self.data / "update-candidate" / "pharmacy.db"
        cand.parent.mkdir(parents=True, exist_ok=True)
        cand.write_bytes(candidate)
        prev = self.data / "update-previous" / "pharmacy-before.db"
        releases.write_journal(self.home, {
            "state": "ready", "release": release_id,
            "swaps": [{"live": str(self.live), "candidate": str(cand), "previous": str(prev),
                       "live_sha256": sha(self.live), "candidate_sha256": sha(cand)}]})
        return cand, prev


class TestJournalSwap(Stage0Base):
    def test_ready_journal_swaps_the_checked_copy_in(self):
        rid = self.stage_and_select()
        cand, prev = self.prepare_journal(rid)
        self.assertEqual(releases.resume_journal(self.home), "swapped")
        self.assertEqual(self.live.read_bytes(), b"NEW DATABASE")
        self.assertEqual(prev.read_bytes(), b"OLD DATABASE")
        self.assertFalse(cand.exists())
        self.assertEqual(releases.read_journal(self.home)["state"], "swapped")
        self.assertTrue(releases.read_state(self.home)["pending"])

    def test_live_changed_after_the_copy_aborts_and_keeps_old_code(self):
        rid = self.stage_and_select()
        cand, prev = self.prepare_journal(rid)
        self.live.write_bytes(b"OLD DATABASE + A SALE")          # a write after the copy
        self.assertEqual(releases.resume_journal(self.home), "aborted")
        self.assertEqual(self.live.read_bytes(), b"OLD DATABASE + A SALE")
        self.assertFalse(prev.exists())
        self.assertFalse(cand.exists())
        self.assertIsNone(releases.read_state(self.home)["active"])

    def test_tampered_candidate_aborts(self):
        rid = self.stage_and_select()
        cand, _ = self.prepare_journal(rid)
        cand.write_bytes(b"SOMETHING ELSE")
        self.assertEqual(releases.resume_journal(self.home), "aborted")
        self.assertEqual(self.live.read_bytes(), b"OLD DATABASE")
        self.assertIsNone(releases.read_state(self.home)["active"])

    def test_live_with_unmerged_wal_is_not_swapped(self):
        rid = self.stage_and_select()
        self.prepare_journal(rid)
        Path(str(self.live) + "-wal").write_bytes(b"frames")
        self.assertEqual(releases.resume_journal(self.home), "aborted")
        self.assertEqual(self.live.read_bytes(), b"OLD DATABASE")
        self.assertEqual(Path(str(self.live) + "-wal").read_bytes(), b"frames")

    def test_journal_for_another_release_is_abandoned(self):
        self.stage_and_select()
        cand, _ = self.prepare_journal("f" * 64)
        self.assertEqual(releases.resume_journal(self.home), "abandoned")
        self.assertEqual(self.live.read_bytes(), b"OLD DATABASE")
        self.assertFalse(cand.exists())

    def test_power_cut_between_the_two_moves_is_completed(self):
        rid = self.stage_and_select()
        cand, prev = self.prepare_journal(rid)
        prev.parent.mkdir(parents=True, exist_ok=True)
        os.replace(self.live, prev)                               # first move done, then the power went
        self.assertEqual(releases.resume_journal(self.home), "swapped")
        self.assertEqual(self.live.read_bytes(), b"NEW DATABASE")
        self.assertEqual(prev.read_bytes(), b"OLD DATABASE")

    def test_failed_first_start_puts_the_old_database_and_code_back(self):
        rid = self.stage_and_select()
        _, prev = self.prepare_journal(rid)
        releases.resume_journal(self.home)
        Path(str(self.live) + "-wal").write_bytes(b"new frames")  # the failed version wrote a WAL
        releases.recover_failed_start(self.home, rid)
        self.assertEqual(self.live.read_bytes(), b"OLD DATABASE")
        self.assertFalse(Path(str(self.live) + "-wal").exists())   # never replayed on the old file
        self.assertFalse(prev.exists())
        failed = list((self.data / "update-previous").glob("*.failed"))
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].read_bytes(), b"NEW DATABASE")
        self.assertIsNone(releases.read_state(self.home)["active"])
        self.assertEqual(releases.read_journal(self.home)["state"], "rolled_back")

    def test_power_cut_during_undo_is_completed(self):
        rid = self.stage_and_select()
        _, prev = self.prepare_journal(rid)
        releases.resume_journal(self.home)
        j = releases.read_journal(self.home)
        releases.write_journal(self.home, {**j, "state": "undoing"})
        failed = Path(str(prev) + ".failed")
        os.replace(self.live, failed)                             # first undo move done, then the power went
        self.assertEqual(releases.resume_journal(self.home), "rolled_back")
        self.assertEqual(self.live.read_bytes(), b"OLD DATABASE")
        self.assertIsNone(releases.read_state(self.home)["active"])

    def test_confirmed_start_finishes_the_journal(self):
        rid = self.stage_and_select()
        self.prepare_journal(rid)
        releases.resume_journal(self.home)
        releases.confirm(self.home, rid)
        releases.finish_journal(self.home, rid)
        self.assertEqual(releases.read_journal(self.home)["state"], "done")
        self.assertEqual(releases.resume_journal(self.home), "done")      # nothing more to do
        self.assertEqual(self.live.read_bytes(), b"NEW DATABASE")

    def test_maintenance_commands_refused_while_a_swap_is_ready(self):
        rid = self.stage_and_select()
        self.prepare_journal(rid)
        with patch.dict(os.environ, {"PHARM_DATA_DIR": str(self.data), "PHARM_RELEASE_CANDIDATE": ""}), \
                patch.object(sys, "argv", ["manage.py", "backup"]):
            with self.assertRaisesRegex(releases.ReleaseError, "update"):
                releases.run_selected(self.root)
        self.assertEqual(self.live.read_bytes(), b"OLD DATABASE")

    def test_events_are_logged(self):
        rid = self.stage_and_select()
        self.prepare_journal(rid)
        releases.resume_journal(self.home)
        lines = (self.home / "update-log.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(lines[-1])["step"], "swap")


class TestCodexFindings386(Stage0Base):
    """Codex P1s on #386: never start a program over a journal that is not settled."""

    def test_failed_restore_keeps_recovery_pending(self):
        rid = self.stage_and_select()
        cand, prev = self.prepare_journal(rid)
        real = releases._replace
        def flaky(src, dst):
            if Path(src) == cand or Path(src) == prev:       # candidate in, then the restore: both locked
                raise PermissionError("locked")
            real(src, dst)
        with patch.object(releases, "_replace", side_effect=flaky):
            with self.assertRaises(releases.ReleaseError):
                releases.resume_journal(self.home)
        self.assertFalse(self.live.exists())                  # the moment Codex describes
        self.assertEqual(releases.read_journal(self.home)["state"], "ready")   # not "aborted"
        self.assertTrue(cand.exists())                        # not deleted
        self.assertTrue(releases.read_state(self.home)["pending"])
        self.assertEqual(releases.resume_journal(self.home), "swapped")        # next start finishes it
        self.assertEqual(self.live.read_bytes(), b"NEW DATABASE")

    def test_sync_error_after_both_moves_restores_the_old_database(self):
        """Codex P1 on ec061d9: a directory-sync error after both renames must not leave the migrated file
        live with the old code selected."""
        rid = self.stage_and_select()
        cand, prev = self.prepare_journal(rid)
        real = releases.sync_dir
        calls = {"n": 0}
        def failing(path):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("sync")
            real(path)
        with patch.object(releases, "sync_dir", side_effect=failing):
            self.assertEqual(releases.resume_journal(self.home), "aborted")
        self.assertEqual(self.live.read_bytes(), b"OLD DATABASE")
        self.assertIsNone(releases.read_state(self.home)["active"])
        self.assertTrue(releases.settled(self.home))

    def test_files_swapped_but_journal_not_updated_is_still_undone(self):
        rid = self.stage_and_select()
        cand, prev = self.prepare_journal(rid)
        prev.parent.mkdir(parents=True, exist_ok=True)
        os.replace(self.live, prev)
        os.replace(cand, self.live)                           # both moves done, journal still "ready"
        releases.recover_failed_start(self.home, rid)
        self.assertEqual(self.live.read_bytes(), b"OLD DATABASE")
        self.assertIsNone(releases.read_state(self.home)["active"])

    def launch(self, journal_error):
        rid = self.stage_and_select()
        self.prepare_journal(rid)
        real = releases.write_journal
        calls = {"n": 0}
        def failing(home, value):
            if value.get("state") == "swapped":
                calls["n"] += 1
                if journal_error == "always" or calls["n"] == 1:
                    raise OSError("disk")
            real(home, value)
        proc = Mock()
        proc.wait.return_value = 0
        with patch.object(launcher, "ROOT", self.root), patch.object(launcher, "home", return_value=self.app_home), \
                patch.object(launcher, "running", return_value=False), \
                patch.object(launcher, "candidate_ready", return_value=True), \
                patch.object(launcher.threading, "Thread"), patch.object(launcher.time, "sleep"), \
                patch.object(releases, "write_journal", side_effect=failing), \
                patch.object(launcher.subprocess, "Popen", return_value=proc) as spawn:
            code = launcher.main()
        return code, spawn

    def test_second_launcher_never_touches_the_swap(self):
        """Codex P1 on 6f3d1f1: the icon opened twice must not run two swaps at once."""
        rid = self.stage_and_select()
        self.prepare_journal(rid)
        with releases.stage0_lock(self.home):                 # the first launcher is still working
            with patch.object(launcher, "ROOT", self.root), \
                    patch.object(launcher, "home", return_value=self.app_home), \
                    patch.object(launcher, "running", return_value=False), \
                    patch.object(launcher, "open_when_ready") as wait, \
                    patch.object(launcher.subprocess, "Popen") as spawn, \
                    patch.object(releases, "resume_journal") as resume:
                self.assertEqual(launcher.main(), 0)
        spawn.assert_not_called()
        resume.assert_not_called()
        wait.assert_called_once()
        self.assertEqual(releases.read_journal(self.home)["state"], "ready")
        self.assertEqual(self.live.read_bytes(), b"OLD DATABASE")

    def test_unsettled_journal_never_starts_a_program(self):
        code, spawn = self.launch("always")
        self.assertNotEqual(code, 0)
        spawn.assert_not_called()
        self.assertIn("could not be completed", (self.app_home / "logs" / "update.log").read_text(encoding="utf-8"))

    def test_transient_journal_error_is_retried(self):
        code, spawn = self.launch("once")
        self.assertEqual(code, 0)
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(self.live.read_bytes(), b"NEW DATABASE")


class TestLauncherApply(Stage0Base):
    def run_launcher(self, *, healthy, apply_writes_journal=True):
        rid_box = {}

        def fake_apply(cmd, **kwargs):       # stage 1 `update --apply-prepared`, as run by stage 0
            self.assertIn("--apply-prepared", cmd)
            if apply_writes_journal:
                rid_box["id"] = self.stage_and_select()
                self.prepare_journal(rid_box["id"])
            return Mock(returncode=0 if apply_writes_journal else 1)

        releases.atomic_json(self.home / "apply-go.json", {"release": "x"})
        first, second = Mock(), Mock()
        first.wait.return_value = 0
        second.wait.return_value = 0
        first.poll.return_value = None if healthy else 1
        with patch.object(launcher, "ROOT", self.root), patch.object(launcher, "home", return_value=self.app_home), \
                patch.object(launcher, "running", return_value=False), \
                patch.object(launcher, "candidate_ready", return_value=healthy), \
                patch.object(launcher.threading, "Thread"), \
                patch.object(launcher.subprocess, "run", side_effect=fake_apply) as run, \
                patch.object(launcher.subprocess, "Popen", side_effect=[first, second]):
            self.assertEqual(launcher.main(), 0)
        return rid_box.get("id"), run

    def test_capability_is_announced(self):
        self.run_launcher(healthy=True, apply_writes_journal=False)
        self.assertEqual(json.loads((self.home / "bootstrap.json").read_text())["bootstrap"], releases.BOOTSTRAP)

    def test_apply_then_healthy_start_keeps_new_database(self):
        rid, run = self.run_launcher(healthy=True)
        self.assertEqual(run.call_count, 1)
        self.assertFalse((self.home / "apply-go.json").exists())       # one shot: no restart loop
        self.assertEqual(self.live.read_bytes(), b"NEW DATABASE")
        self.assertEqual(releases.read_state(self.home)["active"]["id"], rid)
        self.assertFalse(releases.read_state(self.home)["pending"])
        self.assertEqual(releases.read_journal(self.home)["state"], "done")

    def test_apply_then_failed_start_restores_old_database_and_code(self):
        self.run_launcher(healthy=False)
        self.assertEqual(self.live.read_bytes(), b"OLD DATABASE")
        self.assertIsNone(releases.read_state(self.home)["active"])
        self.assertEqual(releases.read_journal(self.home)["state"], "rolled_back")

    def test_failed_apply_changes_nothing(self):
        self.run_launcher(healthy=True, apply_writes_journal=False)
        self.assertEqual(self.live.read_bytes(), b"OLD DATABASE")
        self.assertEqual(releases.read_state(self.home), {})


if __name__ == "__main__":
    unittest.main()
