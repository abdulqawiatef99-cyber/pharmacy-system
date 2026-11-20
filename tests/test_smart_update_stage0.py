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
