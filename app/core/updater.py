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
import re
import json
import os
import shutil
import stat
import tempfile
import urllib.error
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


# ---------------------------------------------------------------- automatic updates (#96)
# Channels: "main" is the pharmacy (default); "develop" is for the owner's test PC only. Each channel's
# installer is published by .github/workflows/windows-installer.yml as a release with a manifest.
INSTALLER_TAGS = {"main": "installer-latest", "develop": "installer-develop"}
# A change to any of these cannot ship as a code-only release: the installed launcher and bootstrap run
# from the installed folder, and libraries, migrations and the embedded Python come with the installer.
INSTALLER_FILES = ("requirements.txt", "release_bootstrap.py", "release_protocol.json", "launcher.py",
                   ".github/workflows/windows-installer.yml")
INSTALLER_DIRS = ("app/migrations/", "installer/")
NEEDS_INSTALLER = 3                     # exit code of `manage.py update` when only the installer can ship it
MAX_INSTALLER_BYTES = 400 * 1024 * 1024


def needs_installer(paths) -> list[str]:
    """The changed paths that only an installer can deliver."""
    return sorted(p for p in paths if p in INSTALLER_FILES or p.startswith(INSTALLER_DIRS))


def _tree_digest(root: Path) -> dict:
    out = {}
    for name in INSTALLER_FILES:
        p = root / name
        out[name] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
    for d in INSTALLER_DIRS:
        base = root / d
        for p in sorted(base.rglob("*")) if base.is_dir() else ():
            if p.is_file() and "__pycache__" not in p.parts:
                out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def installer_reasons(current: Path, candidate: Path) -> list[str]:
    """Files that differ between the running code and a candidate and need the installer."""
    a, b = _tree_digest(current), _tree_digest(candidate)
    return needs_installer(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def _json(url: str, token: str):
    try:
        return json.loads(_get(url, token, "application/vnd.github+json"))
    except ValueError as e:
        raise UpdateError("رد غير متوقع من GitHub") from e


def _subject(message: str) -> str:
    lines = [ln.strip() for ln in (message or "").splitlines()]
    first = lines[0] if lines else ""
    if first.startswith("Merge pull request"):           # GitHub puts the PR title after a blank line
        body = [ln for ln in lines[1:] if ln]
        return body[0] if body else ""
    return first


def summarize(commits: list[dict]) -> str:
    """One short line of what changed: PR titles when there are merges, else commit subjects (newest last)."""
    msgs = [(c.get("commit") or {}).get("message", "") for c in commits]
    merges = [_subject(m) for m in msgs if m.startswith("Merge pull request")]
    picked = [s for s in (merges or [_subject(m) for m in msgs]) if s and not s.startswith("Merge ")]
    seen = []
    for s in picked:
        if s not in seen:
            seen.append(s)
    text = " · ".join(seen[-3:])
    return text[:300]


def compare(repo: str, base: str | None, head: str, token: str) -> dict:
    """What changed from the installed commit to the channel head: files, summary, and whether the list is
    complete. An unknown base (manual install) returns complete=False, so callers stay conservative."""
    if not base or not re.fullmatch(r"[0-9a-f]{40}", base):
        return {"files": [], "summary": "", "complete": False}
    try:
        r = _json(f"{API}/repos/{repo}/compare/{base}...{head}", token)
    except UpdateError:
        return {"files": [], "summary": "", "complete": False}
    files = [f.get("filename", "") for f in r.get("files") or []]
    return {"files": files, "summary": summarize(r.get("commits") or []), "status": r.get("status"),
            # GitHub lists at most 300 files; a longer diff is treated as needing the installer
            "complete": len(files) < 300 and r.get("status") in ("ahead", "identical")}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _open(url: str, headers: dict):
    opener = urllib.request.build_opener(_NoRedirect)
    return opener.open(urllib.request.Request(url, headers=headers), timeout=120)


def _asset(asset_url: str, token: str):
    """Open a release asset. The token goes to api.github.com only: the redirect to the storage host is
    followed without it (that URL carries its own signature)."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/octet-stream",
               "User-Agent": "pharmacy-updater"}
    try:
        try:
            return _open(asset_url, headers)
        except urllib.error.HTTPError as e:
            if e.code not in (301, 302, 303, 307, 308) or not e.headers.get("Location"):
                raise
            location = e.headers["Location"]
            if not location.startswith("https://"):
                raise UpdateError("رابط تنزيل غير آمن") from e
            return _open(location, {"User-Agent": "pharmacy-updater", "Accept": "application/octet-stream"})
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            raise UpdateError("GitHub رُفض تنزيل المثبّت: المفتاح خاطئ أو منتهٍ أو ليس له صلاحية") from e
        raise UpdateError(f"خطأ من GitHub: {e.code}") from e
    except OSError as e:
        raise UpdateError(f"لا يوجد اتصال بـ GitHub: {e}") from e


def download_asset(asset_url: str, token: str, dest: Path, *, max_bytes: int = MAX_INSTALLER_BYTES) -> str:
    """Stream a release asset to dest and return its sha256."""
    resp = _asset(asset_url, token)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    h, size = hashlib.sha256(), 0
    try:
        with resp, open(part, "wb") as out:
            while chunk := resp.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise UpdateError("ملف المثبّت أكبر من الحد المسموح")
                h.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        os.replace(part, dest)
    except OSError as e:
        raise UpdateError(f"تعذّر حفظ المثبّت: {e}") from e
    finally:
        part.unlink(missing_ok=True)
    return h.hexdigest()


def installer_release(repo: str, channel: str, token: str) -> dict:
    """The channel's published installer: manifest (commit, sha256, size) and the exe asset."""
    try:
        rel = _json(f"{API}/repos/{repo}/releases/tags/{INSTALLER_TAGS[channel]}", token)
    except UpdateError as e:
        raise UpdateError(f"المثبّت الجديد لم يُنشر بعد ({e})") from e
    assets = {a.get("name"): a for a in rel.get("assets") or []}
    exe, man = assets.get("PharmacySetup.exe"), assets.get("installer.json")
    if not exe or not man:
        raise UpdateError("المثبّت الجديد لم يكتمل نشره بعد")
    try:
        with _asset(man["url"], token) as resp:
            manifest = json.loads(resp.read(65536))
    except (ValueError, OSError) as e:
        raise UpdateError("بيانات المثبّت غير صحيحة") from e
    if not (isinstance(manifest, dict) and re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("sha", "")))
            and re.fullmatch(r"[0-9a-f]{64}", str(manifest.get("sha256", "")))):
        raise UpdateError("بيانات المثبّت غير صحيحة")
    digest = str(exe.get("digest") or "")
    if digest.startswith("sha256:") and digest[7:] != manifest["sha256"]:
        raise UpdateError("بصمة المثبّت لا تطابق المنشور على GitHub")
    return {"sha": manifest["sha"], "ref": manifest.get("ref"), "sha256": manifest["sha256"],
            "url": exe["url"], "size": exe.get("size")}


def installer_args(exe: Path, log: Path) -> str:
    """cmd.exe line that starts the installer as a separate process. `start` makes the installer an
    orphan, outside the server's process tree: the installer stops the program's own Python processes
    with taskkill /T, which would otherwise end the installer too. /RELAUNCH=1 starts the program again
    when setup ends (pharmacy.iss)."""
    for p in (exe, log):
        if any(ch in str(p) for ch in '"%^&|<>'):
            raise UpdateError("مسار المثبّت يحتوي على رموز غير مسموحة")
    return (f'start "" "{exe}" /SILENT /SUPPRESSMSGBOXES /NORESTART /RELAUNCH=1 "/LOG={log}"')


def run_installer(exe: Path, log: Path) -> None:     # pragma: no cover - Windows only, exercised on the PC
    if os.name != "nt":
        raise UpdateError("تحديث المثبّت يعمل على ويندوز فقط")
    import subprocess
    subprocess.Popen(f'cmd.exe /d /c {installer_args(exe, log)}', creationflags=0x08000000,
                     close_fds=True, cwd=str(exe.parent))
