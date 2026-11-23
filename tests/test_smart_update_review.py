"""Second review of #386 and the owner's decision of 20/11 (the pharmacy PC follows the official release, main):
- a review installer of a work branch follows main once main contains its commit, never an older main;
- an update never moves a PC back to an older build of its channel (also when no commit time was recorded);
- only a launcher that can apply a batch is ever asked to (a bootstrap.json left by a newer launcher is not
  enough once an older installer is back: exit 75 would only restart the program again and again);
- the bundle build names the committed key that verified its signature, and the owner's keys stay committed.
Synthetic data only; GitHub is never called (every request is a mock)."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from app.core import smart_update as su
from app.core import update_sign, updater
from app.core.updater import UpdateError
import launcher
import release_bootstrap as releases
from tests.test_api import APICase, H
from tests.test_smart_update import SHA_NEW, SHA_OLD, SmartUpdateBase
from tests.test_updater_edges import _Resp

REPO = Path(__file__).resolve().parents[1]
REVIEW = {"sha": "a" * 40, "source": "installer", "ref": "claude/launch-candidate"}


class TestFollowTheOfficialRelease(unittest.TestCase):
    def setUp(self):
        updater._contains_seen.clear()
        self.addCleanup(updater._contains_seen.clear)

    def compare(self, status):
        return mock.patch("urllib.request.urlopen", return_value=_Resp(json.dumps({"status": status}).encode()))

    def test_channel_installs_never_ask_github(self):
        with mock.patch.object(updater, "contains") as has:
            self.assertEqual(updater.follow({"sha": "a" * 40, "ref": "develop"}, "o/r", None), "develop")
            self.assertEqual(updater.follow({"sha": "a" * 40, "ref": "main"}, "o/r", None), "main")
            self.assertEqual(updater.follow({}, "o/r", None), "main")
        has.assert_not_called()

    def test_review_install_follows_main_only_once_main_contains_it(self):
        for status, expected in (("ahead", "main"), ("identical", "main"), ("behind", None), ("diverged", None)):
            updater._contains_seen.clear()
            with self.compare(status) as get:
                self.assertEqual(updater.follow(REVIEW, "o/r", "t"), expected, status)
            self.assertIn("/repos/o/r/compare/" + "a" * 40 + "...main", get.call_args[0][0].full_url)

    def test_offline_or_unknown_commit_is_no_channel_not_an_error(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
            self.assertIsNone(updater.follow(REVIEW, "o/r", None))
        with mock.patch("urllib.request.urlopen") as get:
            self.assertIsNone(updater.follow({**REVIEW, "sha": "local"}, "o/r", None))     # a zip install
        get.assert_not_called()

    def test_contained_stays_contained_without_asking_again(self):
        with self.compare("ahead") as get:
            self.assertTrue(updater.contains("o/r", "a" * 40, "main", None))
            self.assertTrue(updater.contains("o/r", "a" * 40, "main", None))
        self.assertEqual(get.call_count, 1)                    # main only moves forward
        with self.compare("behind") as get:
            self.assertFalse(updater.contains("o/r", "b" * 40, "main", None))
            self.assertFalse(updater.contains("o/r", "b" * 40, "main", None))
        self.assertEqual(get.call_count, 2)                    # not there yet: asked again at the next check


@mock.patch.dict("os.environ", {"PHARM_TEST_OFFLINE_UPDATE": "0"})
class TestReviewInstallOnTheApi(APICase):
    def test_status_follows_main_once_the_official_release_has_it(self):
        self.http_login()
        signed = {"sha": "b" * 40, "channel": "main", "commit_time": 5, "python": su.python_tag(),
                  "migrations": [], "notes": []}
        with mock.patch.object(updater, "installed", return_value=REVIEW), \
                mock.patch.object(updater, "read_token", return_value="t"), \
                mock.patch.object(updater, "contains", return_value=True) as has, \
                mock.patch.object(update_sign, "trusted_keys", return_value=[b"k" * 32]), \
                mock.patch.object(su, "bootstrap_level", return_value=releases.BOOTSTRAP), \
                mock.patch.object(su, "read_manifest", return_value=signed) as manifest:
            st = self.c.get("/api/system/update").json()
        has.assert_any_call(updater.DEFAULT_REPO, "a" * 40, "main", "t")
        self.assertEqual(manifest.call_args.args[0].channel, "main")
        self.assertEqual((st["channel"], st["latest"], st["available"]), ("main", "b" * 40, True))
        self.assertNotIn("candidate", st)

    def test_button_starts_signed_preparation(self):
        self.http_login()
        done = mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch("subprocess.run", return_value=done) as run:
            r = self.c.post("/api/system/update", headers=H, json={"action": "prepare"})
        self.assertEqual(r.status_code, 200, r.text)
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[cmd.index("update"):], ["update", "--prepare"])

    def test_smart_status_never_offers_an_older_bundle(self):
        self.http_login()
        m = {"sha": "c" * 40, "channel": "main", "commit_time": 5, "python": su.python_tag(), "migrations": [], "notes": []}
        with mock.patch.object(updater, "installed", return_value={"sha": "a" * 40, "ref": "main"}), \
                mock.patch.object(updater, "read_token", return_value=None), \
                mock.patch.object(update_sign, "trusted_keys", return_value=[b"k" * 32]), \
                mock.patch.object(su, "bootstrap_level", return_value=releases.BOOTSTRAP), \
                mock.patch.object(su, "read_manifest", return_value=m), \
                mock.patch.object(updater, "contains", return_value=False) as has:
            st = self.c.get("/api/system/update").json()
        has.assert_called_once_with(updater.DEFAULT_REPO, "a" * 40, "c" * 40, None)
        self.assertTrue(st["smart"]["enabled"])
        self.assertFalse(st["smart"]["available"])             # the bundle lags behind a code-only update


class TestCommandLineFollowsMain(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pharm-follow-")).resolve()
        env = {k: v for k, v in os.environ.items() if not k.startswith("PHARM_")}
        self.env = mock.patch.dict(os.environ, {**env, "PHARM_DATA_DIR": str(self.tmp / "data")}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        import manage
        self.manage = manage

    def test_update_without_ref_requires_a_signed_official_channel(self):
        with mock.patch.object(updater, "read_token", return_value=None), \
                mock.patch.object(updater, "installed", return_value=REVIEW), \
                mock.patch.object(su, "prepare", return_value={"channel": "main"}) as prepare:
            with mock.patch.object(updater, "contains", return_value=False):
                with self.assertRaisesRegex(SystemExit, "النسخة الرسمية لسه ما وصلتلهاش"):
                    self.manage.main(["update", "--prepare"])
            prepare.assert_not_called()
            with mock.patch.object(updater, "contains", return_value=True):
                self.manage.main(["update", "--prepare"])
        self.assertEqual(prepare.call_args.args[1].channel, "main")


class TestNeverBackward(unittest.TestCase):
    M = {"sha": "b" * 40, "commit_time": 2000}

    def test_known_commit_time_decides(self):
        self.assertTrue(su.is_forward(self.M, {"sha": "a" * 40, "commit_time": 1000}))
        self.assertFalse(su.is_forward(self.M, {"sha": "a" * 40, "commit_time": 3000}))
        self.assertFalse(su.is_forward(self.M, {"sha": "b" * 40, "commit_time": 1000}))   # the installed one

    def test_unknown_time_asks_github_whether_the_bundle_contains_the_install(self):
        with mock.patch.object(updater, "contains", return_value=False) as has:
            self.assertFalse(su.is_forward(self.M, {"sha": "a" * 40}, "o/r", "t"))
        has.assert_called_once_with("o/r", "a" * 40, "b" * 40, "t")
        with mock.patch.object(updater, "contains", return_value=True):
            self.assertTrue(su.is_forward(self.M, {"sha": "a" * 40}, "o/r", "t"))

    def test_offline_bundle_with_nothing_to_compare_is_the_owners_choice(self):
        with mock.patch.object(updater, "contains") as has:
            self.assertTrue(su.is_forward(self.M, {"sha": "a" * 40}))                    # flash drive
            self.assertTrue(su.is_forward(self.M, {"sha": "local"}, "o/r"))              # zip install
        has.assert_not_called()


    def test_offline_release_must_respect_the_confirmed_channel_floor(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            releases.atomic_json(home / "version-floor.json", {"main": {"sha": "c" * 40, "commit_time": 3000}})
            info = {"sha": "a" * 40, "commit_time": 1000}
            self.assertFalse(su.meets_version_floor(home, {**self.M, "channel": "main"}, info))
            self.assertTrue(su.meets_version_floor(home, {**self.M, "channel": "develop"}, info))


class _GitHubLike(su.FolderSource):
    """The bundle folder read as if it came from the GitHub release (it has a repository to ask)."""
    repo, token = "o/r", None


class TestPrepareNeverGoesBack(SmartUpdateBase):
    def test_bundle_behind_the_installed_commit_is_refused_before_any_download(self):
        self.build()                     # installed_version.json has no commit time (an installer or code-only update)
        with mock.patch.object(updater, "contains", return_value=False) as has:
            with self.assertRaisesRegex(UpdateError, "أقدم"):
                su.prepare(self.s, _GitHubLike(self.bundle), legacy=self.legacy)
        has.assert_called_once_with("o/r", SHA_OLD, SHA_NEW, None)
        self.assertFalse((self.home / "downloads").exists())
        self.assertEqual(su.prepared(self.data), {})


class TestOnlyALauncherThatCanApply(SmartUpdateBase):
    def db(self):
        from app.db import Database
        d = Database(self.s.db_path)
        self.addCleanup(d.close)
        return d

    def test_older_launcher_never_gets_a_batch_even_with_a_stale_capability_file(self):
        self.assertTrue((self.home / "bootstrap.json").exists())       # left by a newer launcher (setUp)
        leave = mock.Mock()
        with mock.patch.dict(os.environ, {"PHARM_LAUNCHER": "1"}):
            os.environ.pop("PHARM_BOOTSTRAP", None)                    # what an older launcher passes
            self.assertEqual(su.bootstrap_level(self.home), 1)
            with mock.patch.object(su, "due", return_value={"release": "c" * 64, "when": "now", "needs_db": True}):
                self.assertFalse(su.trigger(self.s, self.db(), in_flight=lambda: 0, exit=leave))
            self.build()
            with self.assertRaisesRegex(UpdateError, "المثبّت"):
                self.prepare()
        leave.assert_not_called()                                      # no exit 75, so no restart loop
        self.assertFalse((self.home / "apply-go.json").exists())
        self.assertFalse((self.home / "downloads").exists())

    def test_the_new_launcher_and_a_command_window(self):
        with mock.patch.dict(os.environ, {"PHARM_LAUNCHER": "1", "PHARM_BOOTSTRAP": str(releases.BOOTSTRAP)}):
            self.assertEqual(su.bootstrap_level(self.home), releases.BOOTSTRAP)
        with mock.patch.dict(os.environ, {"PHARM_LAUNCHER": "1", "PHARM_BOOTSTRAP": "x"}):
            self.assertEqual(su.bootstrap_level(self.home), 1)
        os.environ.pop("PHARM_LAUNCHER", None)
        self.assertEqual(su.bootstrap_level(self.home), releases.BOOTSTRAP)   # no launcher: the file


class TestLauncherTellsItsCapability(unittest.TestCase):
    def test_server_env_carries_the_launcher_capability(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root, app_home = base / "program", base / "home"
            root.mkdir()
            proc = mock.Mock()
            proc.wait.return_value = 0
            with mock.patch.object(launcher, "ROOT", root), mock.patch.object(launcher, "home", return_value=app_home), \
                    mock.patch.object(launcher, "running", return_value=False), \
                    mock.patch.object(launcher.threading, "Thread"), \
                    mock.patch.object(launcher.subprocess, "Popen", return_value=proc) as popen:
                self.assertEqual(launcher.main(), 0)
        env = popen.call_args.kwargs["env"]
        self.assertEqual((env["PHARM_LAUNCHER"], env["PHARM_BOOTSTRAP"]), ("1", str(releases.BOOTSTRAP)))


class TestSigningKeys(unittest.TestCase):
    def test_owner_keys_stay_committed_and_load(self):
        names = {p.name for p in (REPO / "release_keys").glob("*.pub")}
        # owner 21/11 13:16, outcome (b): update-1's private half is not found, so it is no longer trusted; the
        # former spare signs (update-2.pub, same blob d61835b); the new spare update-3.pub (owner 21/11 14:41) is
        # committed before any main merge, so the PCs always trust two keys
        self.assertIn("update-2.pub", names)
        self.assertIn("update-3.pub", names)
        self.assertEqual(len(set(update_sign.trusted_keys(REPO))), 2)
        self.assertNotIn("update-1.pub", names)
        self.assertNotIn("update-2-spare.pub", names)
        self.assertTrue(all(len(k) == 32 for k in update_sign.trusted_keys(REPO)))

    def test_bundle_build_names_the_committed_key_that_verified(self):
        from scripts.release import build_bundle
        a_priv, a_pub = update_sign.generate()
        b_priv, b_pub = update_sign.generate()
        raw = b'{"format": 1}'
        with tempfile.TemporaryDirectory() as tmp:
            keys = Path(tmp) / "release_keys"
            keys.mkdir()
            (keys / "update-1.pub").write_text(a_pub + "\n", encoding="ascii")
            (keys / "update-2-spare.pub").write_text(b_pub + "\n", encoding="ascii")
            with mock.patch.object(build_bundle, "REPO", Path(tmp)):
                self.assertEqual(build_bundle.signing_key_file(raw, update_sign.sign(raw, a_priv)), "update-1.pub")
                self.assertEqual(build_bundle.signing_key_file(raw, update_sign.sign(raw, b_priv)),
                                 "update-2-spare.pub")
                self.assertEqual(build_bundle.signing_key_file(raw, update_sign.sign(raw, update_sign.generate()[0])),
                                 "?")


if __name__ == "__main__":
    unittest.main()


from tests.test_smart_update_stage0 import Stage0Base  # noqa: E402


class TestAbandonedJournalPutsTheDatabaseBack(Stage0Base):
    """Codex P1 on 005db1b: a power cut mid-swap, then a new installer clears the selected release before the
    journal is resumed. Abandoning the journal must first put back any live database it already moved."""

    def installer_clears_the_pointer(self):
        st = releases.read_state(self.home)
        releases.atomic_json(self.home / "active-release.json", {**st, "active": None, "previous": None,
                                                                  "pending": False})

    def test_first_move_done_then_installer(self):
        rid = self.stage_and_select()
        cand, prev = self.prepare_journal(rid)
        prev.parent.mkdir(parents=True, exist_ok=True)
        os.replace(self.live, prev)                                # power cut after the first move
        self.installer_clears_the_pointer()
        self.assertEqual(releases.resume_journal(self.home), "abandoned")
        self.assertEqual(self.live.read_bytes(), b"OLD DATABASE")
        self.assertTrue(releases.settled(self.home))

    def test_both_moves_done_then_installer(self):
        rid = self.stage_and_select()
        cand, prev = self.prepare_journal(rid)
        prev.parent.mkdir(parents=True, exist_ok=True)
        os.replace(self.live, prev)
        os.replace(cand, self.live)                                # journal write to "swapped" lost
        self.installer_clears_the_pointer()
        self.assertEqual(releases.resume_journal(self.home), "abandoned")
        self.assertEqual(self.live.read_bytes(), b"OLD DATABASE")    # the old code gets the old database

    def test_undo_that_cannot_finish_keeps_the_journal_and_starts_nothing(self):
        rid = self.stage_and_select()
        cand, prev = self.prepare_journal(rid)
        prev.parent.mkdir(parents=True, exist_ok=True)
        os.replace(self.live, prev)
        self.installer_clears_the_pointer()
        with mock.patch.object(releases, "_undo_one", side_effect=OSError("locked")):
            with self.assertRaises(releases.ReleaseError):
                releases.resume_journal(self.home)
        self.assertEqual(releases.read_journal(self.home)["state"], "ready")
        self.assertFalse(releases.settled(self.home))
        self.assertTrue(cand.exists())
        self.assertEqual(releases.resume_journal(self.home), "abandoned")   # next start finishes it
        self.assertEqual(self.live.read_bytes(), b"OLD DATABASE")
