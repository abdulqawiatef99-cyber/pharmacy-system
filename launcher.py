"""Desktop launcher (no black window): run with pythonw.exe from the installed folder.

- Data lives outside the program folder: %LOCALAPPDATA%\\PharmacyPlatform (data\\, backups\\, logs\\),
  so installing a new version or updating can never touch it.
- One instance only: if the program is already running, just open the browser.
- The server runs as a child process; exit code 75 means "restart me" (after an in-app update).
"""
from __future__ import annotations

import os
import json
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_bootstrap as releases

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PHARM_PORT", "8000"))
URL = f"http://127.0.0.1:{PORT}/"
RESTART = 75


def home() -> Path:
    base = os.environ.get("PHARM_HOME")
    if base:
        return Path(base)
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "PharmacyPlatform"
    return Path.home() / ".local" / "share" / "pharmacy-platform"


def lan_enabled(h: Path) -> bool:
    """The owner turns on "mobiles on the pharmacy Wi-Fi" from the program page (data\\network.json)."""
    import json
    try:
        return bool(json.loads((h / "data" / "network.json").read_text(encoding="utf-8")).get("lan"))
    except (OSError, ValueError):
        return False


def running() -> bool:
    try:
        with urllib.request.urlopen(URL + "api/health", timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def open_when_ready(timeout: float = 60) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if running():
            webbrowser.open(URL)
            return
        time.sleep(0.5)


def healthy_boot(boot_id: str) -> bool:
    try:
        with urllib.request.urlopen(URL + "api/health", timeout=2) as response:
            value = json.loads(response.read())
            return response.status == 200 and value.get("ok") is True and value.get("boot_id") == boot_id
    except (OSError, ValueError, AttributeError):
        return False


def candidate_ready(process, boot_id: str, timeout: float = 60) -> bool:
    """Startup smoke check only, not clinical or financial acceptance."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if process.poll() is not None:
            return False
        if healthy_boot(boot_id):
            return process.poll() is None
        time.sleep(0.25)
    return False


def main() -> int:
    if running():
        webbrowser.open(URL)
        return 0
    h = home()
    for d in ("data", "backups", "logs"):
        (h / d).mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PHARM_DATA_DIR": str(h / "data"), "PYTHONUTF8": "1", "PHARM_LAUNCHER": "1",
           "PYTHONPATH": str(ROOT)}
    flags = 0x08000000 if os.name == "nt" else 0          # CREATE_NO_WINDOW
    first = True
    code_home = releases.code_home(h / "data")
    build = releases.adopt_installer(ROOT, code_home)        # a new PharmacySetup.exe was just installed
    if build:
        (h / "data" / "installed_version.json").write_text(json.dumps({**build, "source": "installer"}),
                                                           encoding="utf-8")
    releases.write_capability(code_home)                    # stage 1 may now offer database updates
    while True:
        # Smart update (docs/SMART_UPDATE.md): finish or undo a swap left by a power cut, then run a prepared
        # apply once if the program asked for it, then swap the checked copy in. Nothing here migrates.
        try:
            releases.resume_journal(code_home)
            if (code_home / "apply-go.json").exists():
                releases.run_apply(code_home, releases.current_root(ROOT, code_home), env, h / "logs", flags)
                releases.resume_journal(code_home)
        except (releases.ReleaseError, OSError) as exc:
            with open(h / "logs" / "update.log", "a", encoding="utf-8") as log:
                log.write(f"Update journal could not be completed: {exc}\n")
            (code_home / "apply-go.json").unlink(missing_ok=True)   # then start what is selected (logged above)
        state = releases.read_state(code_home)
        selected_id = (state.get("active") or {}).get("id")
        try:
            program = releases.current_root(ROOT, code_home)
        except releases.ReleaseError:
            if not state.get("pending"):
                raise
            releases.recover_failed_start(code_home, selected_id)
            continue
        pending = h / "data" / "pending_restore.zip"
        if pending.exists():                     # restore asked from the first-run screen
            with open(h / "logs" / "restore.log", "a", encoding="utf-8") as log:
                subprocess.run([sys.executable, str(program / "manage.py"), "restore", str(pending), "--yes",
                                "--pre-dir", str(h / "backups" / "pre-restore")], cwd=h, env=env,
                               stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
            pending.replace(h / "backups" / f"restored-{time.strftime('%Y%m%d-%H%M%S')}.zip")
        with open(h / "logs" / "server.log", "a", encoding="utf-8") as log:
            host = "0.0.0.0" if lan_enabled(h) else "127.0.0.1"
            boot_id = secrets.token_hex(16)
            p = subprocess.Popen([sys.executable, str(program / "manage.py"), "serve", "--port", str(PORT), "--host", host],
                                 cwd=h, env={**env, "PHARM_BOOT_ID": boot_id}, stdout=log,
                                 stderr=subprocess.STDOUT, creationflags=flags)
            if state.get("pending"):
                if not candidate_ready(p, boot_id):
                    if p.poll() is None:
                        p.terminate()
                        try:
                            p.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            p.kill()
                            p.wait(timeout=10)
                    releases.recover_failed_start(code_home, selected_id)
                    log.write("Candidate startup failed; previous complete code (and database) selected.\n")
                    continue
                releases.confirm(code_home, selected_id)
                releases.finish_journal(code_home, selected_id)
            if first:
                threading.Thread(target=open_when_ready, daemon=True).start()
                first = False
            code = p.wait()
        if code != RESTART:
            return code


if __name__ == "__main__":
    sys.exit(main())
