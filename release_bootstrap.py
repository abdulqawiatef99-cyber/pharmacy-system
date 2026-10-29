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

PROTOCOL = 1


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
def locked(home: Path):
    home.mkdir(parents=True, exist_ok=True)
    with open(home / "release.lock", "a+b") as f:
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


def _contract(root: Path) -> tuple[bytes, dict]:
    req = root / "requirements.txt"
    return (req.read_bytes() if req.exists() else b"",
            {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted((root / "app" / "migrations").glob("*.sql"))})


def check_compatible(current: Path, candidate: Path) -> None:
    try:
        marker = json.loads((candidate / "release_protocol.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReleaseError("This update needs the protocol-1 installer first") from exc
    if marker != {"protocol": PROTOCOL} or not (candidate / "release_bootstrap.py").is_file():
        raise ReleaseError("Unsupported release bootstrap")
    if _contract(current) != _contract(candidate):
        raise ReleaseError("Dependencies or database migrations changed; a coordinated upgrade is required")


def activate(legacy: Path, home: Path, release_id: str, metadata: dict,
             expected_active: str | None) -> None:
    with locked(home):
        state = read_state(home)
        active = state.get("active")
        if active and active["id"] == release_id:
            raise ReleaseError("Release is already selected")
        if (active or {}).get("id") != expected_active or state.get("pending"):
            raise ReleaseError("Release changed or is awaiting its first healthy startup")
        candidate = release_path(home, release_id)
        verify_release(candidate)
        check_compatible(current_root(legacy, home), candidate)
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


def run_selected(root: Path) -> None:
    # Explicit candidate checks must not redirect back to the currently active code.
    # Recovery must remain callable even if the selected release was damaged.
    if os.environ.get("PHARM_RELEASE_CANDIDATE") == "1" or (
            len(sys.argv) > 1 and sys.argv[1] == "update" and "--rollback" in sys.argv):
        return
    home = code_home(Path(os.environ.get("PHARM_DATA_DIR", "data")).resolve())
    if (len(sys.argv) > 1 and sys.argv[1] == "serve"
            and read_state(home).get("pending")
            and os.environ.get("PHARM_LAUNCHER") != "1"):
        raise ReleaseError("Pending release requires launcher health supervision; use launcher or update --rollback")
    selected = current_root(root, home)
    if selected.resolve() != root.resolve():
        sys.path.insert(0, str(selected))
        runpy.run_path(str(selected / "manage.py"), run_name="__main__")
        raise SystemExit(0)
