"""Stable stdlib-only entry point for staged code releases (protocol 1).

Never imports app or opens the pharmacy database. The installed launcher stays
at the original location; updates select a complete, verified directory.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import sys
import tempfile

PROTOCOL = 1        # format of active-release.json and release_protocol.json (installed launchers read it)
BOOTSTRAP = 2       # stage-0 capability: journal-driven database swap + undo (docs/SMART_UPDATE.md)


class ReleaseError(RuntimeError):
    pass


def code_home(data_dir: Path) -> Path:
    return Path(data_dir) / "code-releases"


def sync_dir(path: Path) -> None:
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
        sync_dir(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def stage0_lock(home: Path):
    """One launcher at a time owns the journal, the swap and the first start (Codex P1 on #386). Held for the
    launcher's whole life; a second launcher gets ReleaseError and only opens the browser."""
    with locked(home, name="stage0.lock"):
        yield


@contextmanager
def locked(home: Path, name: str = "release.lock"):
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    with open(home / name, "a+b") as f:
        if os.name == "nt":
            import msvcrt
            if f.seek(0, 2) == 0:
                f.write(b"0")
                f.flush()
            f.seek(0)
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ReleaseError("Another release operation is running") from exc
        else:
            import fcntl
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ReleaseError("Another release operation is running") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)


def read_state(home: Path) -> dict:
    p = home / "active-release.json"
    if not p.exists():
        return {}
    try:
        state = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("protocol") != PROTOCOL:
            raise ValueError("unsupported state")
        for name in ("active", "previous"):
            entry = state.get(name)
            if entry is not None and (not isinstance(entry, dict)
                    or not re.fullmatch(r"[0-9a-f]{64}", entry.get("id", ""))):
                raise ValueError("invalid release id")
        return state
    except (OSError, ValueError, TypeError) as exc:
        raise ReleaseError("Release state is invalid; use the verified installed launcher") from exc


def release_path(home: Path, release_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", release_id):
        raise ReleaseError("Invalid release id")
    return home / "releases" / release_id


def verify_release(path: Path) -> None:
    try:
        manifest = json.loads((path / ".release-source.json").read_text(encoding="utf-8"))
        if manifest["id"] != path.name or not isinstance(manifest["files"], dict):
            raise ValueError("invalid manifest")
        for name, expected in manifest["files"].items():
            rel = Path(name)
            if rel.is_absolute() or ".." in rel.parts or "\\" in name or ":" in name:
                raise ValueError("unsafe source path")
            p = path / rel
            if p.is_symlink() or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != expected:
                raise ValueError("source file changed")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ReleaseError("Release is missing or failed source verification") from exc


def current_root(legacy: Path, home: Path) -> Path:
    state = read_state(home)
    entry = state.get("active")
    if not entry:
        return Path(state.get("legacy_root", str(legacy)))
    path = release_path(home, entry["id"])
    verify_release(path)
    return path


def _text(p: Path) -> bytes:
    """File bytes with Windows line ends as Unix ones. The installer's program files came from `git archive`
    on a Windows runner (CRLF) while GitHub's update archive has LF: byte for byte every update looked like a
    dependency/migration change and the update button refused all of them on the pharmacy PC (#18 B13).
    The database's own migration checksum (db.migrate) already reads the files in text mode, ignoring them."""
    return p.read_bytes().replace(b"\r\n", b"\n")


def _contract(root: Path) -> tuple[bytes, dict]:
    req = root / "requirements.txt"
    return (_text(req) if req.exists() else b"",
            {p.name: hashlib.sha256(_text(p)).hexdigest()
             for p in sorted((root / "app" / "migrations").glob("*.sql"))})


def check_compatible(current: Path, candidate: Path, *, coordinated: bool = False) -> None:
    """coordinated=True: a signed smart-update bundle whose migrations ran on a copy of the database and whose
    packages ship with it (stage 1 checked both); only the bootstrap marker is required then."""
    try:
        marker = json.loads((candidate / "release_protocol.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReleaseError("This update needs the protocol-1 installer first") from exc
    if marker != {"protocol": PROTOCOL} or not (candidate / "release_bootstrap.py").is_file():
        raise ReleaseError("Unsupported release bootstrap")
    if not coordinated and _contract(current) != _contract(candidate):
        raise ReleaseError("Dependencies or database migrations changed; a coordinated upgrade is required")


def activate(legacy: Path, home: Path, release_id: str, metadata: dict,
             expected_active: str | None, *, coordinated: bool = False) -> None:
    with locked(home):
        state = read_state(home)
        active = state.get("active")
        if active and active["id"] == release_id:
            raise ReleaseError("Release is already selected")
        if (active or {}).get("id") != expected_active or state.get("pending"):
            raise ReleaseError("Release changed or is awaiting its first healthy startup")
        candidate = release_path(home, release_id)
        verify_release(candidate)
        check_compatible(current_root(legacy, home), candidate, coordinated=coordinated)
        atomic_json(home / "active-release.json",
                    {"protocol": PROTOCOL, "legacy_root": state.get("legacy_root", str(legacy.resolve())),
                     "active": {**metadata, "id": release_id}, "previous": active, "pending": True})


def confirm(home: Path, expected_id: str) -> None:
    with locked(home):
        state = read_state(home)
        if (state.get("active") or {}).get("id") != expected_id:
            raise ReleaseError("Release changed before health confirmation")
        state["pending"] = False
        atomic_json(home / "active-release.json", state)


def rollback(home: Path, expected_id: str | None = None) -> None:
    with locked(home):
        state = read_state(home)
        if not state or not state.get("active"):
            raise ReleaseError("No selected release to roll back")
        if expected_id is not None and state["active"]["id"] != expected_id:
            raise ReleaseError("Release changed before rollback")
        previous = state.get("previous")
        if previous:
            verify_release(release_path(home, previous["id"]))
        atomic_json(home / "active-release.json",
                    {**state, "active": previous, "previous": None, "pending": False})


def adopt_installer(legacy: Path, home: Path) -> dict | None:
    """A newly installed program (build_info.json, written by the installer build) runs instead of a
    release selected by an older in-app update. Once per installer build: a later in-app update stays
    selected. Returns the build info when it was adopted now, else None."""
    try:
        build = json.loads((legacy / "build_info.json").read_text(encoding="utf-8"))
        sha = build["sha"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        return None
    marker = home / "installer.json"
    try:
        if json.loads(marker.read_text(encoding="utf-8")).get("sha") == sha:
            return None
    except (OSError, ValueError, AttributeError):
        pass
    with locked(home):
        try:
            state = read_state(home)
        except ReleaseError:                 # the installer is the recovery path for a damaged state
            state = {"damaged": True}
        if state:
            atomic_json(home / "active-release.json",
                        {"protocol": PROTOCOL, "legacy_root": str(legacy.resolve()),
                         "active": None, "previous": None, "pending": False})
        atomic_json(marker, {"sha": sha, "ref": build.get("ref")})
    return {"sha": sha, "ref": build.get("ref")}


def data_dir() -> Path:
    """Same default as app.config.default_data_dir (this module runs before any app code is chosen, so it
    cannot import it; tests keep the two equal): PHARM_DATA_DIR, else the installed folder on Windows."""
    if os.environ.get("PHARM_DATA_DIR"):
        return Path(os.environ["PHARM_DATA_DIR"]).resolve()
    local = os.environ.get("LOCALAPPDATA")
    if os.name == "nt" and local:
        return (Path(local) / "PharmacyPlatform" / "data").resolve()
    return Path("data").resolve()


def run_selected(root: Path) -> None:
    """Direct `python manage.py …` on the installed program: run the selected release instead."""
    # Explicit candidate checks must not redirect back to the currently active code.
    # Recovery must remain callable even if the selected release was damaged.
    if os.environ.get("PHARM_RELEASE_CANDIDATE") == "1" or (
            len(sys.argv) > 1 and sys.argv[1] == "update" and "--rollback" in sys.argv):
        return
    home = code_home(data_dir())        # the installed program's release state too (Codex on #91)
    if read_journal(home).get("state") in ("ready", "undoing"):
        # the new code would open (and migrate) the live database before the checked copy is swapped in
        raise ReleaseError("A program update is being applied; start the program from its icon to finish it")
    if (len(sys.argv) > 1 and sys.argv[1] == "serve"
            and read_state(home).get("pending")
            and os.environ.get("PHARM_LAUNCHER") != "1"):
        raise ReleaseError("Pending release requires launcher health supervision; use launcher or update --rollback")
    selected = current_root(root, home)
    if selected.resolve() != root.resolve():
        sys.path.insert(0, str(selected))
        runpy.run_path(str(selected / "manage.py"), run_name="__main__")
        raise SystemExit(0)


# --------------------------------------------------------------------------- smart update, stage 0
# The journal (written by stage 1 `update --apply-prepared`) lists file swaps: a checked, migrated copy of the
# database replaces the live file. Every step is idempotent, decided from the files and their sha256, so a
# power cut at any point is finished (or undone) on the next start. Stage 0 never migrates anything.

def journal_path(home: Path) -> Path:
    return Path(home) / "update-journal.json"


def read_journal(home: Path) -> dict:
    p = journal_path(home)
    try:
        value = json.loads(p.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_journal(home: Path, value: dict) -> None:
    atomic_json(journal_path(home), value)


def log_event(home: Path, **fields) -> None:
    """Append-only update log; the next start of the program copies it into the audit log."""
    from datetime import datetime, timezone
    Path(home).mkdir(parents=True, exist_ok=True)
    line = json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **fields},
                      ensure_ascii=False)
    with open(Path(home) / "update-log.jsonl", "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


def write_capability(home: Path) -> None:
    try:
        if json.loads((Path(home) / "bootstrap.json").read_text(encoding="utf-8")).get("bootstrap") == BOOTSTRAP:
            return
    except (OSError, ValueError, AttributeError):
        pass
    atomic_json(Path(home) / "bootstrap.json", {"bootstrap": BOOTSTRAP})


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _replace(src: Path, dst: Path) -> None:
    """os.replace with a few retries: on Windows an antivirus scan can hold a just-closed file for a moment."""
    import time
    for attempt in range(6):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.5 * (attempt + 1))


def _sidecars(db: Path) -> list[Path]:
    return [Path(str(db) + "-wal"), Path(str(db) + "-shm")]


def _discard(path: Path) -> None:
    for p in [path, *_sidecars(path)]:
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def _swap_one(s: dict) -> None:
    live, cand, prev = Path(s["live"]), Path(s["candidate"]), Path(s["previous"])
    if not cand.exists():
        if live.exists() and file_sha256(live) == s["candidate_sha256"]:
            return                                          # already swapped
        raise ReleaseError("Update copy is missing")
    if file_sha256(cand) != s["candidate_sha256"] or any(p.exists() for p in _sidecars(cand)):
        raise ReleaseError("Update copy changed after its checks")
    if live.exists():
        wal = _sidecars(live)[0]
        if wal.exists() and wal.stat().st_size:
            raise ReleaseError("Live database has unmerged changes (-wal); not swapped")
        if file_sha256(live) != s["live_sha256"]:
            raise ReleaseError("Live database changed after the copy; not swapped")
        for side in _sidecars(live):
            side.unlink(missing_ok=True)                    # empty WAL / stale shared memory, no open connection
        prev.parent.mkdir(parents=True, exist_ok=True)
        _replace(live, prev)
    elif not (prev.exists() and file_sha256(prev) == s["live_sha256"]):
        raise ReleaseError("Live database is missing")
    _replace(cand, live)
    sync_dir(live.parent)


def _undo_one(s: dict) -> None:
    live, prev = Path(s["live"]), Path(s["previous"])
    if not prev.exists():
        if live.exists() and file_sha256(live) == s["live_sha256"]:
            return                                          # already undone
        raise ReleaseError("Previous database is missing; restore the pre-update backup")
    if file_sha256(prev) != s["live_sha256"]:
        raise ReleaseError("Previous database changed; restore the pre-update backup")
    failed = Path(str(prev) + ".failed")
    if live.exists():
        _discard(failed)
        _replace(live, failed)
    for side, dest in zip(_sidecars(live), _sidecars(failed)):
        if side.exists():                                   # the failed version's WAL belongs to the new file
            _replace(side, dest)
    _replace(prev, live)
    sync_dir(live.parent)


def _abort(home: Path, j: dict, state: str, reason: str) -> str:
    for s in j.get("swaps", []):
        cand = Path(s.get("candidate", ""))
        if cand.name and cand.exists():
            _discard(cand)
    with locked(home):
        st = read_state(home)
        if (st.get("active") or {}).get("id") == j.get("release") and st.get("pending"):
            atomic_json(home / "active-release.json",
                        {**st, "active": st.get("previous"), "previous": None, "pending": False})
    write_journal(home, {**j, "state": state, "reason": reason})
    log_event(home, step="swap", ok=False, state=state, release=j.get("release"), reason=reason)
    return state


def resume_journal(home: Path) -> str:
    """Bring the journal to a stable state before any program starts. Returns the state."""
    home = Path(home)
    j = read_journal(home)
    state = j.get("state")
    if not state:
        return "none"
    if state == "ready":
        st = read_state(home)
        if (st.get("active") or {}).get("id") != j.get("release") or not st.get("pending"):
            # e.g. a power cut mid-swap, then a new installer cleared the pointer: any live database this
            # journal already moved goes back first; if that fails the journal stays "ready" and no program
            # starts (an empty database must never be created over a missing one; Codex P1 on 005db1b)
            try:
                for s in j.get("swaps", []):
                    if Path(s["previous"]).exists():
                        _undo_one(s)
            except (ReleaseError, OSError, KeyError, TypeError) as exc:
                log_event(home, step="swap", ok=False, release=j.get("release"), reason=f"abandon: {exc}")
                raise ReleaseError(f"Live database could not be put back yet: {exc}") from exc
            return _abort(home, j, "abandoned", "the update was not selected")
        try:
            for s in j["swaps"]:
                _swap_one(s)
        except (ReleaseError, OSError, KeyError, TypeError) as exc:
            # Every swap that moved the live file (first move only, or both) is put back first. If that fails too, the journal stays
            # "ready" with the copy kept, and the caller must not start a program (Codex P1 on #386): the
            # next start retries. Only with every live file in place is the update dropped.
            try:
                for s in j.get("swaps", []):        # any swap that moved a file (half or whole) goes back
                    if Path(s["previous"]).exists():
                        _undo_one(s)
            except (ReleaseError, OSError, KeyError) as undo_exc:
                log_event(home, step="swap", ok=False, release=j.get("release"), reason=f"{exc}; {undo_exc}")
                raise ReleaseError(f"Live database could not be put back yet: {undo_exc}") from exc
            return _abort(home, j, "aborted", str(exc))
        write_journal(home, {**j, "state": "swapped"})
        log_event(home, step="swap", ok=True, release=j.get("release"))
        return "swapped"
    if state == "swapped":
        st = read_state(home)
        active = (st.get("active") or {}).get("id")
        if active == j.get("release") and not st.get("pending"):
            finish_journal(home, active)                    # confirmed, then the power went
            return "done"
        if active != j.get("release"):                      # pointer rolled back, files not yet
            recover_failed_start(home, j.get("release"))
            return "rolled_back"
        return "swapped"                                    # first start still to be supervised
    if state == "undoing":
        recover_failed_start(home, j.get("release"))
        return "rolled_back"
    return state


def _swapped_files(j: dict) -> bool:
    try:
        return any(Path(s["previous"]).exists() or (Path(s["live"]).exists() and not Path(s["candidate"]).exists()
                                                    and file_sha256(Path(s["live"])) == s["candidate_sha256"])
                   for s in j.get("swaps", []))
    except (OSError, KeyError, TypeError):
        return True                                         # unsure: undo (it checks every hash itself)


def settled(home: Path) -> bool:
    """True when no program may be harmed by starting now: no swap half-done or awaiting its undo."""
    j = read_journal(home)
    if j.get("state") in ("ready", "undoing"):
        return False
    return all(Path(s.get("live", "")).exists() for s in j.get("swaps", [])) if j.get("state") == "swapped" else True


def finish_journal(home: Path, release_id: str) -> None:
    j = read_journal(home)
    if j.get("state") == "swapped" and j.get("release") == release_id:
        write_journal(home, {**j, "state": "done"})
        log_event(home, step="confirm", ok=True, release=release_id)


def recover_failed_start(home: Path, release_id: str | None) -> None:
    """The selected release did not start: put the previous database back (if this release swapped one),
    then the previous code. Idempotent."""
    home = Path(home)
    j = read_journal(home)
    ours = j.get("release") == release_id and (j.get("state") in ("swapped", "undoing") or (
        j.get("state") == "ready" and _swapped_files(j)))      # files moved, journal write lost
    if ours:
        write_journal(home, {**j, "state": "undoing"})
        for s in j["swaps"]:
            _undo_one(s)
    with locked(home):
        st = read_state(home)
        if st.get("active") and st["active"]["id"] == release_id:
            previous = st.get("previous")
            if previous:
                verify_release(release_path(home, previous["id"]))
            atomic_json(home / "active-release.json",
                        {**st, "active": previous, "previous": None, "pending": False})
    if ours:
        write_journal(home, {**j, "state": "rolled_back"})
    log_event(home, step="rollback", ok=True, release=release_id, database=ours)


def run_apply(home: Path, program: Path, env: dict, logs: Path, creationflags: int = 0) -> None:
    """Run stage 1's prepared apply once (requested by the running program through apply-go.json)."""
    import subprocess
    go = Path(home) / "apply-go.json"
    if not go.exists():
        return
    try:
        release = json.loads(go.read_text(encoding="utf-8")).get("release", "")
    except (OSError, ValueError, AttributeError):
        release = ""
    go.unlink(missing_ok=True)                              # one shot: a crash here never loops
    rc = None
    try:
        logs.mkdir(parents=True, exist_ok=True)
        with open(logs / "update.log", "a", encoding="utf-8") as log:
            rc = subprocess.run([sys.executable, str(program / "manage.py"), "update", "--apply-prepared",
                                 str(release)], cwd=str(Path(home).parent.parent), env=env, stdout=log,
                                stderr=subprocess.STDOUT, timeout=1800, creationflags=creationflags).returncode
    except (OSError, subprocess.SubprocessError) as exc:
        log_event(home, step="apply", ok=False, release=release, reason=f"{type(exc).__name__}: {exc}")
        return
    log_event(home, step="apply", ok=rc == 0, release=release, rc=rc)
