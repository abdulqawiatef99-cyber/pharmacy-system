"""Trial #6 (#45): what the installer installed is known and wins once over an older in-app update.

- build_info.json (written by the installer build) → on its first start the launcher selects the
  installed program instead of a release chosen by an older in-app update, and records its SHA.
- Once per installer build: a later in-app update stays selected.
- The update badge does not show for main's own build, nor for a candidate built from another branch.
No real data."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
from unittest.mock import Mock, patch

from app.core import updater
import launcher
import release_bootstrap as releases
from tests.test_api import APICase
from tests.test_atomic_releases import package

SHA = "a" * 40
SHA2 = "b" * 40


class TestAdoptInstaller(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.base, True)
        self.root = self.base / "program"
        self.home = self.base / "home" / "data" / "code-releases"
        (self.root / "app" / "migrations").mkdir(parents=True)
        (self.root / "manage.py").write_text("installed")
        (self.root / "requirements.txt").write_text("same")
        (self.root / "app" / "migrations" / "0001.sql").write_text("same")
        self.files = {"manage.py": "update", "app/x.py": "new", "release_bootstrap.py": "",
                      "release_protocol.json": '{"protocol":1}', "requirements.txt": "same",
                      "app/migrations/0001.sql": "same"}

    def in_app_update(self):
        staged = updater.apply_zip(package(self.files), self.root, code_home=self.home)
        active = (releases.read_state(self.home).get("active") or {}).get("id")
        releases.activate(self.root, self.home, staged["id"], {"sha": "c" * 40}, expected_active=active)
        releases.confirm(self.home, staged["id"])
        return staged

    def install(self, sha, ref="main"):
        (self.root / "build_info.json").write_text(json.dumps({"sha": sha, "ref": ref}))

    def test_new_installer_wins_over_older_in_app_update_once(self):
        staged = self.in_app_update()
        self.assertEqual(releases.current_root(self.root, self.home), Path(staged["release"]))
        self.install(SHA)
        self.assertEqual(releases.adopt_installer(self.root, self.home), {"sha": SHA, "ref": "main"})
        self.assertEqual(releases.current_root(self.root, self.home), self.root)      # the installed program runs
        self.assertIsNone(releases.adopt_installer(self.root, self.home))              # once per build
        later = self.in_app_update()                                                   # a later in-app update…
        self.assertIsNone(releases.adopt_installer(self.root, self.home))
        self.assertEqual(releases.current_root(self.root, self.home), Path(later["release"]))  # …stays selected
        self.install(SHA2)                                                             # the next installer wins again
        self.assertEqual(releases.adopt_installer(self.root, self.home)["sha"], SHA2)
        self.assertEqual(releases.current_root(self.root, self.home), self.root)

    def test_no_build_info_or_bad_sha_changes_nothing(self):
        staged = self.in_app_update()
        self.assertIsNone(releases.adopt_installer(self.root, self.home))
        (self.root / "build_info.json").write_text('{"sha": "../../x"}')
        self.assertIsNone(releases.adopt_installer(self.root, self.home))
        self.assertEqual(releases.current_root(self.root, self.home), Path(staged["release"]))

    def test_damaged_state_is_recovered_by_the_installer(self):
        self.home.mkdir(parents=True)
        (self.home / "active-release.json").write_text("{not json")
        self.install(SHA)
        self.assertIsNotNone(releases.adopt_installer(self.root, self.home))
        self.assertEqual(releases.current_root(self.root, self.home), self.root)

    def test_launcher_records_what_was_installed(self):
        self.in_app_update()
        self.install(SHA, "develop")
        process = Mock()
        process.wait.return_value = 0
        app_home = self.home.parent.parent
        with patch.object(launcher, "ROOT", self.root), patch.object(launcher, "home", return_value=app_home), \
                patch.object(launcher, "running", return_value=False), \
                patch.object(launcher.threading, "Thread"), \
                patch.object(launcher.subprocess, "Popen", return_value=process) as spawn:
            self.assertEqual(launcher.main(), 0)
        self.assertEqual(Path(spawn.call_args.args[0][1]).parent, self.root)
        self.assertEqual(json.loads((app_home / "data" / "installed_version.json").read_text()),
                         {"sha": SHA, "ref": "develop", "source": "installer"})
        self.assertEqual(updater.installed(app_home / "data")["sha"], SHA)


@mock.patch.dict("os.environ", {"PHARM_TEST_OFFLINE_UPDATE": "0"})
class TestUpdateBadge(APICase):
    def status(self, info, latest):
        self.http_login()
        with mock.patch.object(updater, "installed", return_value=info), \
                mock.patch.object(updater, "read_token", return_value="t"), \
                mock.patch.object(updater, "latest_sha", return_value=latest):
            return self.c.get("/api/system/update").json()

    def test_main_build_is_not_an_update_of_itself(self):
        st = self.status({"sha": SHA, "ref": "main", "source": "installer"}, SHA)
        self.assertFalse(st["available"])

    def test_main_moved_on_is_an_update(self):
        self.assertTrue(self.status({"sha": SHA, "ref": "main", "source": "installer"}, SHA2)["available"])

    def test_candidate_from_another_branch_shows_no_update(self):
        # #107: develop is a channel now (below); a review installer of a work branch has none until the
        # official release (main) contains its commit (owner 20/11, tests/test_smart_update_review.py)
        with mock.patch.object(updater, "contains", return_value=False):
            st = self.status({"sha": SHA, "ref": "claude/96-auto-updates", "source": "installer"}, SHA2)
        self.assertFalse(st["available"])
        self.assertEqual(st["candidate"], "claude/96-auto-updates")

    def test_develop_build_follows_develop(self):
        st = self.status({"sha": SHA, "ref": "develop", "source": "installer"}, SHA2)
        self.assertTrue(st["available"])
        self.assertEqual(st["channel"], "develop")


if __name__ == "__main__":
    unittest.main()
