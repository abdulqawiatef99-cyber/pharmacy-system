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


def app_browser(env=os.environ, exists=os.path.exists) -> str | None:
    """U3-4: Edge (always on Windows 10/11) or Chrome, to open the program in its own window (no tabs, no address
    bar, the program's own icon from its manifest). None = use the default browser as before."""
    if os.name != "nt" or env.get("PHARM_APP_WINDOW") == "0":
        return None
    roots = [env.get(k) for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA") if env.get(k)]
    for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
        for root in roots:
            exe = os.path.join(root, rel)
            if exists(exe):
                return exe
    return None


def app_command(exe: str, url: str, profile: Path) -> list[str]:
    """One window of its own: --app (no tabs: Ctrl+Tab and F11 reach the program, F11 is full screen), a profile of
    its own so a second click goes to the same browser process and the pharmacy's normal browsing is untouched."""
    return [exe, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
            "--disable-features=Translate"]


def open_program(url: str = None) -> None:
    url = url or URL
    exe = app_browser()
    if exe:
        try:
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            subprocess.Popen(app_command(exe, url, home() / "app-window"), close_fds=True, creationflags=flags,
                             cwd=str(home()))       # never the program folder: uninstall can remove it
            return
        except OSError:
            pass                                   # the browser could not start: the default browser instead
    webbrowser.open(url)


def open_when_ready(timeout: float = 60) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if running():
            open_program(URL)
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


def _log_update(h: Path, text: str) -> None:
    with open(h / "logs" / "update.log", "a", encoding="utf-8") as log:
        log.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")


def _selected_program(code_home: Path) -> Path:
    if (ROOT / "build_info.json").is_file():
        return releases.verified_program(ROOT, code_home)
    return releases.current_root(ROOT, code_home)


def settle(code_home: Path, h: Path, env: dict, flags: int, attempts: int = 3) -> bool:
    """Smart update (docs/SMART_UPDATE.md): finish or undo a swap left by a power cut, run a prepared apply once
    if the program asked for it, swap the checked copy in. Nothing here migrates. False = do not start."""
    for attempt in range(attempts):
        try:
            releases.resume_journal(code_home)
            if (code_home / "apply-go.json").exists():
                releases.run_apply(code_home, _selected_program(code_home), env, h / "logs", flags)
                releases.resume_journal(code_home)
            if releases.settled(code_home):
                return True
            error = "journal not settled"
        except (releases.ReleaseError, OSError) as exc:
            error = str(exc)
        (code_home / "apply-go.json").unlink(missing_ok=True)
        _log_update(h, f"Update journal could not be completed (attempt {attempt + 1}): {error}")
        time.sleep(2 * (attempt + 1))        # a Windows file lock (antivirus) usually clears in seconds
    _log_update(h, "Program not started; it retries on the next start. Data and the previous copy are kept.")
    return False


def recover(code_home: Path, h: Path, selected_id, attempts: int = 3) -> bool:
    for attempt in range(attempts):
        try:
            releases.recover_failed_start(code_home, selected_id)
            return True
        except (releases.ReleaseError, OSError) as exc:
            _log_update(h, f"Rollback could not be completed (attempt {attempt + 1}): {exc}")
            time.sleep(2 * (attempt + 1))
    _log_update(h, "Program not started; the rollback is retried on the next start.")
    return False


def main() -> int:
    if running():
        open_program(URL)
        return 0
    h = home()
    lock = releases.stage0_lock(releases.code_home(h / "data"))
    try:
        lock.__enter__()
    except releases.ReleaseError:            # another launcher is starting the program (or updating it)
        open_when_ready(timeout=600)
        return 0
    try:
        return _main(h)
    finally:
        lock.__exit__(None, None, None)


def _main(h: Path) -> int:
    for d in ("data", "backups", "logs"):
        (h / d).mkdir(parents=True, exist_ok=True)
    # PHARM_BOOTSTRAP: what this launcher can apply (docs/SMART_UPDATE.md §1); an older launcher sets none
    env = {**os.environ, "PHARM_DATA_DIR": str(h / "data"), "PYTHONUTF8": "1", "PHARM_LAUNCHER": "1",
           "PHARM_BOOTSTRAP": str(releases.BOOTSTRAP), "PHARM_INSTALL_ROOT": str(ROOT),
           "PYTHONPATH": str(ROOT)}
    env.pop("PHARM_RELEASE_CANDIDATE", None)
    env.pop("PHARM_DEPS_DIR", None)
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
        if not settle(code_home, h, env, flags):
            return 1                         # never start a program over an unsettled swap (Codex P1, #386)
        state = releases.read_state(code_home)
        selected_id = (state.get("active") or {}).get("id")
        try:
            program = _selected_program(code_home)
        except releases.ReleaseError:
            if not state.get("pending"):
                raise
            if not recover(code_home, h, selected_id):
                return 1
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
                    if not recover(code_home, h, selected_id):
                        return 1
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
