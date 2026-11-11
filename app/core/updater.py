"""Code update from the private GitHub repository, without touching pharmacy data.

Flow (manage.py update): read-only token -> latest commit of the branch ->
verified database backup -> snapshot of the current code (for rollback) ->
download + check the archive -> copy the new files over the program folder,
never into data/, backups/ or the virtual environment -> record the installed
commit. Installing packages and migrating the database happen afterwards in a
fresh process (the new code), see manage.py.

The token is a GitHub *fine-grained, read-only* token stored on the pharmacy PC
outside the program folder (never in the repository, never in backups).
"""
from __future__ import annotations

import io
import json
import os
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import hashlib
import release_bootstrap as releases

DEFAULT_REPO = "abdulqawiatef99-cyber/pharmacy-system"
PROTECTED = {"data", "backups", ".venv", "venv", ".git", "__pycache__", ".code-releases",
             ".release-source.json"}
API = "https://api.github.com"


class UpdateError(RuntimeError):
    pass


def token_path() -> Path:
    return Path(os.environ.get("PHARM_UPDATE_TOKEN_FILE",
                               Path.home() / ".pharmacy" / "github_token.txt"))


def read_token() -> str:
    t = os.environ.get("PHARM_GITHUB_TOKEN")
    if t:
        return t.strip()
    p = token_path()
    if not p.exists():
        raise UpdateError(f"مفتاح GitHub غير موجود. احفظه في الملف: {p}")
    return p.read_text(encoding="utf-8").strip()


def _get(url: str, token: str, accept: str) -> bytes:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": accept,
                                               "User-Agent": "pharmacy-updater"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            raise UpdateError("GitHub رُفض الطلب: المفتاح خاطئ أو منتهٍ أو ليس له صلاحية على المستودع") from e
        raise UpdateError(f"خطأ من GitHub: {e.code}") from e
    except OSError as e:
        raise UpdateError(f"لا يوجد اتصال بـ GitHub: {e}") from e


def latest_sha(repo: str, ref: str, token: str) -> str:
    sha = _get(f"{API}/repos/{repo}/commits/{ref}", token, "application/vnd.github.sha").decode().strip()
    if len(sha) != 40:
        raise UpdateError("رد غير متوقع من GitHub")
    return sha


def download(repo: str, sha: str, token: str) -> bytes:
    return _get(f"{API}/repos/{repo}/zipball/{sha}", token, "application/vnd.github+json")


CHANNELS = ("main", "develop")      # #107: main = the pharmacy; develop = test installs


def channel(info: dict) -> str | None:
    """The branch this install updates from (#107): the ref it was installed or last updated from, main when
    unknown. None for any other branch (a review installer of a work branch): no update offered."""
    ref = info.get("ref") or "main"
    return ref if ref in CHANNELS else None


def installed(data_dir: Path) -> dict:
    state = releases.read_state(releases.code_home(data_dir))
    if state.get("active"):
        return {**state["active"], "pending": state.get("pending", False)}
    p = data_dir / "installed_version.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _members(z: zipfile.ZipFile) -> tuple[str, list[zipfile.ZipInfo]]:
    """Single top folder that contains manage.py and app/; no absolute or '..' paths."""
    tops = {n.split("/", 1)[0] for n in z.namelist() if n.strip("/")}
    if len(tops) != 1:
        raise UpdateError("ملف التحديث بصيغة غير صحيحة (أكثر من مجلد رئيسي)")
    top = tops.pop()
    if top in (".", "..") or "\\" in top or ":" in top:
        raise UpdateError("Unsafe archive root")
    names = set(z.namelist())
    if f"{top}/manage.py" not in names or not any(n.startswith(f"{top}/app/") for n in names):
        raise UpdateError("ملف التحديث لا يخص هذا البرنامج")
    if len(z.infolist()) > 15000 or sum(i.file_size for i in z.infolist()) > 256 * 1024 * 1024:
        raise UpdateError("Source archive exceeds the safety limit")
    out, seen, prefixes = [], set(), {}
    devices = {"con", "prn", "aux", "nul"} | {f"{kind}{n}" for kind in ("com", "lpt") for n in range(1, 10)}
    for info in z.infolist():
        # ZipInfo may normalize Windows separators before filename is inspected.
        if "\\" in info.orig_filename or "\x00" in info.orig_filename:
            raise UpdateError("Unsafe original update member name")
        rel = info.filename[len(top) + 1:]
        if not rel or info.is_dir():
            continue
        parts = PurePosixPath(rel).parts
        if (not parts or "\\" in rel or ":" in rel or rel.startswith("/") or ".." in parts
                or PurePosixPath(rel).as_posix() != rel
                or any(p.rstrip(" .") != p for p in parts)
                or any(p.split(".", 1)[0].casefold() in devices for p in parts)
                or stat.S_ISLNK(info.external_attr >> 16)):
            raise UpdateError(f"مسار غير آمن في ملف التحديث: {rel}")
        canonical = rel.casefold()
        if canonical in seen or canonical == ".release-source.json":
            raise UpdateError("Duplicate/reserved update member")
        seen.add(canonical)
        for i in range(1, len(parts)):
            prefix = "/".join(parts[:i])
            if prefixes.setdefault(prefix.casefold(), prefix) != prefix:
                raise UpdateError("Inconsistent directory casing in archive")
        if parts[0].casefold() in PROTECTED:
            continue                      # never overwrite data, backups or the environment
        out.append(info)
    return top, out


def snapshot(root: Path, dest_dir: Path) -> Path:
    """Zip of the current program files (same layout as a GitHub archive) for rollback."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"code-before-update-{datetime.now():%Y%m%d-%H%M%S}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in root.rglob("*"):
            rel = p.relative_to(root)
            if (p.is_file() and not p.is_symlink() and rel.parts[0].casefold() not in PROTECTED
                    and "__pycache__" not in rel.parts):
                z.write(p, f"program/{rel.as_posix()}")
    return out


def apply_zip(data: bytes, root: Path, *, code_home: Path | None = None) -> dict:
    """Stage a complete release; NEVER overwrite the running program or activate it.

    Returns files, release directory and archive digest. Activation is a separate
    checked pointer operation after compatibility checks and candidate selftest.
    """
    home = code_home or root / ".code-releases"
    folder = home / "releases"
    folder.mkdir(parents=True, exist_ok=True)
    release_id = hashlib.sha256(data).hexdigest()
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise UpdateError("ملف التحديث تالف") from e
    with z:
        top, members = _members(z)
        with tempfile.TemporaryDirectory(dir=home, prefix="staging-", ignore_cleanup_errors=True) as tmp:
            staged = Path(tmp)
            hashes = {}
            for info in members:                 # stage first: a broken archive changes nothing
                target = staged / info.filename[len(top) + 1:]
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                    dst.flush()
                    os.fsync(dst.fileno())
                hashes[target.relative_to(staged).as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
            releases.atomic_json(staged / ".release-source.json", {"id": release_id, "files": hashes})
            for directory in sorted((p for p in staged.rglob("*") if p.is_dir()),
                                    key=lambda p: len(p.parts), reverse=True):
                releases.sync_dir(directory)
            releases.sync_dir(staged)
            dest = releases.release_path(home, release_id)
            with releases.locked(home):
                if dest.exists():
                    releases.verify_release(dest)
                    existing = json.loads((dest / ".release-source.json").read_text(encoding="utf-8"))
                    if existing["files"] != hashes:
                        raise UpdateError("Existing release does not match this archive")
                else:
                    os.replace(staged, dest)
                    releases.sync_dir(folder)
    return {"files": len(hashes), "release": str(dest), "id": release_id}


def record(data_dir: Path, sha: str, source: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "installed_version.json").write_text(json.dumps(
        {"sha": sha, "source": source, "at": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
