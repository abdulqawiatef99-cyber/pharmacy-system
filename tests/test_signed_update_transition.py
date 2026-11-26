"""Exercise the real pre-hardening update endpoints and launcher against a signed fixture."""
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from app.core import smart_update as su
from tests.test_smart_update import REPO, SmartUpdateBase

LIVE = "b433c31998aafeefa561b7ae882b62144778f7c5"
LAST_INSTALLER = "f2af5bdcd0de565e0d24ff3c39799577370ae060"

DRIVER = r"""
import importlib.util
import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

from app import config
from app.api.app import create_app
from app.core import smart_update as su, updater
from app.db import Database
import release_bootstrap as releases

home, bundle, installed, live_archive = map(Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location("installed_launcher", installed / "launcher.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
settings = config.load()
code_home = releases.code_home(settings.data_dir)
releases.adopt_installer(installed, code_home)
staged = updater.apply_zip(live_archive.read_bytes(), installed, code_home=code_home)
releases.activate(installed, code_home, staged["id"], {
    "sha": os.environ["TEST_LIVE_SHA"], "ref": "main", "commit_time": 1000, "smart": True,
}, expected_active=None, coordinated=True)
releases.confirm(code_home, staged["id"])
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    launcher.PORT = s.getsockname()[1]
base = f"http://127.0.0.1:{launcher.PORT}"
launcher.URL = base + "/"
launcher.open_when_ready = lambda **kwargs: None
db = Database(settings.db_path)
app = create_app(db, settings)
client = TestClient(app)
headers = {"X-Requested-With": "pharm"}
login = {"username": "owner", "password": "Smoke-Pass-2026"}
signed_in = client.post("/api/auth/login", json=login, headers=headers)
assert signed_in.status_code == 200
old_cookie = signed_in.headers["set-cookie"].split(";", 1)[0]


def prepare(*args, **kwargs):
    prepared = su.prepare(settings, su.FolderSource(bundle), legacy=installed)
    return subprocess.CompletedProcess(args, 0, stdout=str(prepared), stderr="")


# Replace the GitHub transport with the local signed fixture. The legacy
# status/prepare/schedule handlers, staging, trigger and launcher stay real.
with (patch.object(su, "GitHubSource", side_effect=lambda *args: su.FolderSource(bundle)),
      patch.object(updater, "latest_sha", return_value=json.loads((bundle / su.MANIFEST).read_bytes())["sha"]),
      patch.object(updater, "read_token", return_value=None)):
    status = client.get("/api/system/update").json()
    assert status["smart"]["enabled"] and status["smart"]["available"], status
with patch("subprocess.run", side_effect=prepare):
    r = client.post("/api/system/update", headers=headers, json={"action": "prepare"})
    assert r.status_code == 200, r.text
r = client.post("/api/system/update", headers=headers, json={"action": "schedule", "when": "now"})
assert r.status_code == 200, r.text
with patch.object(su, "datetime", wraps=datetime) as clock:
    clock.now.return_value = datetime.now(timezone.utc) + timedelta(minutes=5)
    exits = []
    su.trigger(settings, db, in_flight=lambda: 0, exit=exits.append)
    assert exits == [75], exits
db.close()

real_popen = subprocess.Popen
started = []


def popen(cmd, *args, **kwargs):
    child = real_popen(cmd, *args, **kwargs)
    if "serve" in cmd:
        started.append(child)
        if len(started) > 1:
            child.terminate()
            raise AssertionError("Unexpected recovery to previous release")
        wait = child.wait

        def check_then_stop(*args, **kwargs):
            try:
                try:
                    urllib.request.urlopen(urllib.request.Request(
                        base + "/api/me", headers={"Cookie": old_cookie}), timeout=5)
                    raise AssertionError("Legacy session was still active")
                except urllib.error.HTTPError as err:
                    assert err.code == 401
                payload = json.dumps(login).encode()
                with urllib.request.urlopen(urllib.request.Request(
                        base + "/api/auth/login", payload, {"Content-Type": "application/json"}), timeout=5) as r:
                    cookie = r.headers["Set-Cookie"].split(";", 1)[0]
                request = urllib.request.Request(base + "/api/system/update", b'{"action":"classic"}',
                    {"Content-Type": "application/json", "Cookie": cookie, **headers})
                try:
                    urllib.request.urlopen(request, timeout=5)
                    raise AssertionError("Retired update action was accepted")
                except urllib.error.HTTPError as err:
                    assert err.code == 422 and json.load(err)["error"]["code"] == "signed_update_required"
            finally:
                child.terminate()
                wait(timeout=15)
            return 0

        child.wait = check_then_stop
    return child


with patch.object(launcher.subprocess, "Popen", side_effect=popen):
    try:
        assert launcher._main(home) == 0
    finally:
        for child in started:
            if child.poll() is None:
                child.kill()
entry = releases.read_state(releases.code_home(settings.data_dir))
assert not entry["pending"], entry
print("TRANSITION " + entry["active"]["sha"])
"""


def releases_home_resolved(data):
    from release_bootstrap import code_home
    return code_home(Path(data).resolve())


class TestSignedUpdateTransition(SmartUpdateBase):
    def test_live_main_signed_button_upgrades_with_the_existing_launcher(self):
        shutil.rmtree(self.legacy)
        self.legacy.mkdir()
        installed_archive = subprocess.check_output(["git", "archive", "--format=zip", LAST_INSTALLER], cwd=REPO)
        with zipfile.ZipFile(io.BytesIO(installed_archive)) as z:
            z.extractall(self.legacy)
        folder = Path(self.tmp.name)
        live_archive = folder / "live.zip"
        live_archive.write_bytes(subprocess.check_output(
            ["git", "archive", "--format=zip", "--prefix=program/", LIVE], cwd=REPO))
        with zipfile.ZipFile(live_archive) as z:
            z.extractall(folder)
        with zipfile.ZipFile(live_archive, "a") as z:
            z.writestr("program/release_keys/fixture.pub", self.pub)
        live_program = folder / "program"
        for root in (self.legacy, live_program):
            (root / "release_keys" / "fixture.pub").write_text(self.pub, encoding="ascii")
        (self.legacy / "build_info.json").write_text(
            json.dumps({"sha": LAST_INSTALLER, "ref": "main"}), encoding="ascii")
        self.s.db_path.unlink()
        # inherited PHARM_* settings (the Windows validator sets PHARM_RELEASE_CANDIDATE=1 for the whole suite)
        # would make the selected program skip its own startup checks, so the subprocesses start without them
        inherited = {key: value for key, value in os.environ.items() if not key.startswith("PHARM_")}
        env = {**inherited, "PYTHONPATH": str(self.legacy), "PHARM_DATA_DIR": str(self.data),
               "PHARM_LAUNCHER": "1", "PHARM_BOOTSTRAP": "2", "PHARM_TEST_OFFLINE_UPDATE": "0", "TEST_LIVE_SHA": LIVE}
        subprocess.run([sys.executable, "manage.py", "init", "--pharmacy", "Synthetic transition",
                        "--username", "owner", "--password", "Smoke-Pass-2026"],
                       cwd=live_program, env=env, capture_output=True, check=True, timeout=60)
        self.build(startup_proof=True)
        result = subprocess.run([sys.executable, "-c", DRIVER, str(self.app_home), str(self.bundle),
                                 str(self.legacy), str(live_archive)],
                                cwd=live_program, env=env, text=True, capture_output=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("TRANSITION", result.stdout)
        release = su.prepared(self.data)
        self.assertEqual(release, {})
        from release_bootstrap import read_state, release_path, version_floor

        active = read_state(self.home)["active"]
        candidate = release_path(self.home, active["id"])
        verify = subprocess.run([sys.executable, str(candidate / "manage.py"), "update", "--prepare",
                                 "--from-bundle", str(self.bundle)], cwd=self.app_home, env=env,
                                text=True, capture_output=True, timeout=60)
        self.assertEqual(verify.returncode, 0, verify.stderr + verify.stdout)
        self.assertIn('"up_to_date": true', verify.stdout)
        floor_file = self.home / "version-floor.json"
        self.assertEqual(version_floor(self.home, "main").get("sha"), active["sha"], json.dumps({
            "floor_file": floor_file.read_text(encoding="utf-8") if floor_file.exists() else None,
            "state": read_state(self.home), "candidate": str(candidate),
            "build_info": (candidate / "build_info.json").is_file(),
            "home_files": sorted(f.name for f in self.home.iterdir()),
            "resolved": [str(candidate.resolve()), str(releases_home_resolved(self.data))],
            "verify_stdout": verify.stdout[-1500:], "verify_stderr": verify.stderr[-1500:]}, indent=1))
        original = subprocess.check_output(["git", "show", f"{LAST_INSTALLER}:launcher.py"], cwd=REPO)
        # `git archive` writes CRLF on a Windows checkout and `git show` the stored LF: same file either way
        self.assertEqual((self.legacy / "launcher.py").read_bytes().replace(b"\r\n", b"\n"),
                         original.replace(b"\r\n", b"\n"))
        direct_env = {key: value for key, value in env.items()
                      if key not in {"PYTHONPATH", "PHARM_LAUNCHER", "PHARM_BOOTSTRAP"}}
        maintenance = subprocess.run([sys.executable, "manage.py", "verify"], cwd=self.legacy,
                                     env=direct_env, text=True, capture_output=True, timeout=60)
        self.assertEqual(maintenance.returncode, 0, maintenance.stderr + maintenance.stdout)
