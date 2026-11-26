"""Smart update, stage 1 (docs/SMART_UPDATE.md): signed bundles, packages, migrations on a copy.

Phases: check -> prepare (program running; nothing selected) -> request (now / after closing) ->
trigger (never during a sale) -> apply (program stopped, run once by stage 0) -> swap + first start (stage 0).
Any failure before the swap leaves the selected code and the live database exactly as they were.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

import release_bootstrap as releases

from . import update_sign
from .updater import UpdateError

FORMAT = 1
MANIFEST, SIGNATURE = "manifest.json", "manifest.sig"
STARTUP_PROOF = ".startup-proof.json"
NOTES = "docs/CHANGES_AR.md"
QUIET_SECONDS = 60           # no write in the last minute
SALE_MINUTES = 30            # a draft sale with lines from the last 30 min is a sale in progress
WHEN = ("now", "after_close")


# --------------------------------------------------------------------------- paths and small helpers
def home_of(data_dir: Path) -> Path:
    return releases.code_home(Path(data_dir))


def python_tag() -> str:
    return f"{sys.version_info[0]}.{sys.version_info[1]}"


def platform_tag() -> str:
    return sysconfig.get_platform().replace("-", "_").replace(".", "_")


def deps_id(requirements: bytes, python: str, platform: str) -> str:
    text = requirements.replace(b"\r\n", b"\n")
    return hashlib.sha256(f"{python} {platform}\n".encode() + text).hexdigest()


def _json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def bootstrap_level(home: Path) -> int:
    """What the launcher that started this program can do. Under a launcher, only its own environment counts
    (PHARM_BOOTSTRAP): a bootstrap.json left by a newer launcher outlives it when an older installer is put
    back, and that launcher never applies a batch, so exit 75 would only restart the program again and again
    (second review of #386). In a command window (no launcher), the file the last launcher wrote."""
    if os.environ.get("PHARM_LAUNCHER") == "1":
        try:
            return int(os.environ.get("PHARM_BOOTSTRAP") or 1)
        except ValueError:
            return 1
    v = _json(Path(home) / "bootstrap.json").get("bootstrap")
    return v if isinstance(v, int) else 1


def is_forward(m: dict, info: dict, repo: str | None = None, token: str | None = None) -> bool:
    """The bundle moves this PC forward, never back to an older build of its channel. With the installed
    commit time known (a smart update recorded it), the times decide. An installer or a code-only update
    records none: then GitHub must show the bundle's commit containing the installed one (a bundle that lags
    behind a code-only update is no update). An offline bundle with no time to compare is the owner's own
    choice; the copy check still refuses a database this code is too old for."""
    cur = info.get("sha")
    if m["sha"] == cur:
        return False
    known = int(info.get("commit_time") or 0)
    if known:
        return m["commit_time"] > known
    if repo and re.fullmatch(r"[0-9a-f]{40}", cur or ""):
        from . import updater

        return updater.contains(repo, cur, m["sha"], token)
    return True


def meets_version_floor(home: Path, m: dict, info: dict, repo: str | None = None,
                        token: str | None = None) -> bool:
    floor = releases.version_floor(home, m["channel"])
    if floor and m["sha"] != floor["sha"] and not is_forward(m, floor, repo, token):
        return False
    return is_forward(m, info, repo, token)


def notes_of(root: Path) -> list[str]:
    """Bullet lines of docs/CHANGES_AR.md (one line per change, plain Arabic)."""
    try:
        text = (Path(root) / NOTES).read_text(encoding="utf-8")
    except OSError:
        return []
    return [ln[2:].strip() for ln in text.splitlines() if ln.startswith("- ") and ln[2:].strip()]


def new_notes(active_root: Path, candidate_root: Path) -> list[str]:
    seen = set(notes_of(active_root))
    return [n for n in notes_of(candidate_root) if n not in seen]


# --------------------------------------------------------------------------- manifest
def make_manifest(*, channel: str, sha: str, commit_time: int, version: str, python: str, platform: str,
                  code_zip: Path, deps_zip: Path | None, deps: str, migrations: list[str],
                  notes: list[str] | None = None) -> bytes:
    def asset(p: Path, name: str) -> dict:
        return {"name": name, "sha256": releases.file_sha256(p), "size": p.stat().st_size}
    value = {"format": FORMAT, "channel": channel, "sha": sha, "commit_time": int(commit_time),
             "version": version, "python": python, "platform": platform,
             "code": asset(code_zip, f"code-{sha}.zip"),
             "deps": {"id": deps, **(asset(deps_zip, f"deps-{deps}.zip") if deps_zip else {})},
             "migrations": sorted(migrations), "notes": list(notes or [])[:500]}
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8")


def parse_manifest(raw: bytes, signature: str, keys: list[bytes]) -> dict:
    try:
        update_sign.verify(raw, signature, keys)
    except update_sign.SignatureError as exc:
        raise UpdateError(f"التحديث غير موقّع توقيعًا صحيحًا؛ لم يتغير شيء ({exc})") from exc
    try:
        m = json.loads(raw.decode("utf-8"))
        assert m["format"] == FORMAT
        assert re.fullmatch(r"[0-9a-f]{40}", m["sha"]) and isinstance(m["commit_time"], int)
        for key in ("code",):
            assert re.fullmatch(r"[0-9a-f]{64}", m[key]["sha256"]) and isinstance(m[key]["size"], int)
            assert re.fullmatch(r"[A-Za-z0-9_.-]+", m[key]["name"])
        assert re.fullmatch(r"[0-9a-f]{64}", m["deps"]["id"])
        if "sha256" in m["deps"]:
            assert re.fullmatch(r"[0-9a-f]{64}", m["deps"]["sha256"]) and isinstance(m["deps"]["size"], int)
            assert re.fullmatch(r"[A-Za-z0-9_.-]+", m["deps"]["name"])
        assert isinstance(m["migrations"], list) and isinstance(m["channel"], str)
        assert all(isinstance(n, str) for n in m.setdefault("notes", []))
    except (ValueError, KeyError, TypeError, AssertionError, UnicodeDecodeError) as exc:
        raise UpdateError("ملف وصف التحديث غير صالح") from exc
    return m


# --------------------------------------------------------------------------- sources (GitHub or a folder)
class _NoAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """GitHub sends asset downloads to a signed storage URL; the token must not follow it there."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.request.urlparse(newurl).netloc != urllib.request.urlparse(req.full_url).netloc:
            new.remove_header("Authorization")
        return new


class GitHubSource:
    """Release `update-<channel>` of the private repository, read with the read-only token."""
    def __init__(self, repo: str, channel: str, token: str | None):
        self.repo, self.channel, self.token = repo, channel, token
        self._assets: dict | None = None
        self._open = urllib.request.build_opener(_NoAuthOnRedirect()).open

    def _request(self, url: str, accept: str, extra: dict | None = None):
        headers = {"Accept": accept, "User-Agent": "pharmacy-updater", **(extra or {})}
        if self.token:                                   # no key file: anonymous (the repository is public)
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            return self._open(urllib.request.Request(url, headers=headers), timeout=120)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise UpdateError("مفيش حزمة تحديث موقّعة على القناة دي لسه") from e
            from .updater import _rate_limited
            if _rate_limited(e):
                raise UpdateError("GitHub وصل لحد الطلبات المسموح بيه مؤقتًا. انتظر ساعة ثم اضغط «تحديث» مرة أخرى") from e
            if e.code in (401, 403):
                raise UpdateError("GitHub رفض الطلب: مفتاح القراءة خاطئ أو منتهٍ") from e
            raise UpdateError(f"خطأ من GitHub: {e.code}") from e
        except OSError as e:
            raise UpdateError("مفيش إنترنت دلوقتي؛ البرنامج شغال عادي، جرّب بعدين") from e

    def assets(self) -> dict:
        if self._assets is None:
            with self._request(f"https://api.github.com/repos/{self.repo}/releases/tags/update-{self.channel}",
                               "application/vnd.github+json") as r:
                rel = json.loads(r.read())
            self._assets = {a["name"]: a["url"] for a in rel.get("assets", [])}
        return self._assets

    def read(self, name: str, limit: int = 1 << 20) -> bytes:
        url = self.assets().get(name)
        if not url:
            raise UpdateError(f"حزمة التحديث ناقصة ({name})")
        with self._request(url, "application/octet-stream") as r:
            data = r.read(limit + 1)
        if len(data) > limit:
            raise UpdateError("ملف وصف التحديث أكبر من المسموح")
        return data

    def fetch(self, name: str, dest: Path, size: int) -> None:
        """Download with resume: a cut connection continues from the bytes already saved."""
        url = self.assets().get(name)
        if not url:
            raise UpdateError(f"حزمة التحديث ناقصة ({name})")
        part = dest.with_name(dest.name + ".part")
        have = part.stat().st_size if part.exists() else 0
        if have > size:
            part.unlink()
            have = 0
        extra = {"Range": f"bytes={have}-"} if have else {}
        with self._request(url, "application/octet-stream", extra) as r:
            mode = "ab" if have and getattr(r, "status", 200) == 206 else "wb"
            with open(part, mode) as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
                    if f.tell() > size:
                        break
                f.flush()
                os.fsync(f.fileno())
        os.replace(part, dest)


class FolderSource:
    """An update bundle copied to a folder (flash drive, no internet): same signature and hash checks."""
    def __init__(self, folder: Path):
        self.folder = Path(folder)

    def read(self, name: str, limit: int = 1 << 20) -> bytes:
        p = self.folder / name
        if not p.is_file() or p.stat().st_size > limit:
            raise UpdateError(f"حزمة التحديث ناقصة ({name})")
        return p.read_bytes()

    def fetch(self, name: str, dest: Path, size: int) -> None:
        p = self.folder / name
        if not p.is_file():
            raise UpdateError(f"حزمة التحديث ناقصة ({name})")
        shutil.copyfile(p, dest)


def _manifest_proof(source) -> dict:
    try:
        manifest = source.read(MANIFEST).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UpdateError("ملف وصف التحديث غير صالح") from exc
    return {"manifest": manifest, "signature": source.read(SIGNATURE, limit=4096).decode("ascii", "replace")}


def _verify_proof(proof: dict, keys: list[bytes]) -> dict:
    if not isinstance(proof.get("manifest"), str) or not isinstance(proof.get("signature"), str):
        raise UpdateError("التحديث محتاج ملف إصدار موقّع")
    return parse_manifest(proof["manifest"].encode("utf-8"), proof["signature"], keys)


def read_manifest(source, keys: list[bytes]) -> dict:
    return _verify_proof(_manifest_proof(source), keys)


def download(source, home: Path, asset: dict) -> Path:
    """Verified local copy (cached by sha256: pressing the button again reuses it)."""
    folder = Path(home) / "downloads"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{asset['sha256']}.zip"
    if dest.exists() and dest.stat().st_size == asset["size"] and releases.file_sha256(dest) == asset["sha256"]:
        return dest
    source.fetch(asset["name"], dest, asset["size"])
    if dest.stat().st_size != asset["size"] or releases.file_sha256(dest) != asset["sha256"]:
        dest.unlink(missing_ok=True)
        raise UpdateError("الملف اللي اتنزل مش مطابق للتوقيع؛ اتمسح ومتغيرش حاجة")
    return dest


# --------------------------------------------------------------------------- packages (no pip on the PC)
def deps_dir(home: Path, ident: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", ident or ""):
        raise UpdateError("Invalid package set id")
    return Path(home) / "deps" / ident


def _safe_member(name: str) -> str:
    parts = PurePosixPath(name).parts
    if (not name or name.startswith("/") or "\\" in name or ":" in name or ".." in parts or "\x00" in name
            or PurePosixPath(name).as_posix() != name.rstrip("/")):
        raise UpdateError(f"مسار غير آمن في حزمة المكتبات: {name}")
    return name


def stage_deps(home: Path, ident: str, archive: Path) -> Path:
    """Extract a verified package set into deps/<id>/ (complete or not at all) with its own file hashes."""
    dest = deps_dir(home, ident)
    if dest.exists():
        verify_deps(dest)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent, prefix="staging-", ignore_cleanup_errors=True) as tmp:
        staged = Path(tmp) / ident
        staged.mkdir()
        hashes = {}
        try:
            z = zipfile.ZipFile(archive)
        except zipfile.BadZipFile as exc:
            raise UpdateError("حزمة المكتبات تالفة") from exc
        with z:
            if len(z.infolist()) > 60000 or sum(i.file_size for i in z.infolist()) > 1024 * 1024 * 1024:
                raise UpdateError("Package archive exceeds the safety limit")
            for info in z.infolist():
                name = _safe_member(info.filename)
                if info.is_dir():
                    continue
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise UpdateError("رابط غير مسموح في حزمة المكتبات")
                target = staged / name
                target.parent.mkdir(parents=True, exist_ok=True)
                h = hashlib.sha256()
                with z.open(info) as src, open(target, "wb") as out:
                    while chunk := src.read(1 << 20):
                        h.update(chunk)
                        out.write(chunk)
                hashes[name] = h.hexdigest()
        releases.atomic_json(staged / ".deps-files.json", {"id": ident, "files": hashes})
        os.replace(staged, dest)
    verify_deps(dest)
    return dest


def verify_deps(path: Path) -> None:
    try:
        m = json.loads((Path(path) / ".deps-files.json").read_text(encoding="utf-8"))
        if m["id"] != Path(path).name:
            raise ValueError("id")
        for name, expected in m["files"].items():
            p = Path(path) / _safe_member(name)
            if p.is_symlink() or not p.is_file() or releases.file_sha256(p) != expected:
                raise ValueError(name)
    except (OSError, ValueError, KeyError, TypeError, UpdateError) as exc:
        raise UpdateError("حزمة المكتبات ناقصة أو اتغيرت") from exc


# --------------------------------------------------------------------------- prepare
def _candidate_env(isolated: Path, deps: Path | None, db: Path | None = None) -> dict:
    env = {**os.environ, "PHARM_RELEASE_CANDIDATE": "1", "PHARM_DATA_DIR": str(isolated),
           "PHARM_DB_PATH": str(db or isolated / "test.db"), "PHARM_UPLOAD_DIR": str(isolated / "uploads"),
           "PHARM_BACKUP_DIR": str(isolated / "backups"), "PHARM_PRINTER": "none", "PYTHONUTF8": "1",
           "PHARM_MIGRATE_WITHOUT_BACKUP": "1"}       # only ever a copy; the verified backup is taken first
    env.pop("PHARM_DEPS_DIR", None)
    if deps is not None:
        env["PHARM_DEPS_DIR"] = str(deps)
    env.pop("PHARM_LAUNCHER", None)
    return env


def _run_candidate(candidate: Path, args: list[str], env: dict, timeout: int, run=subprocess.run) -> str:
    proc = run([sys.executable, str(candidate / "manage.py"), *args], cwd=str(candidate), env=env,
               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode:
        tail = ((proc.stdout or "") + (proc.stderr or ""))[-600:]
        raise UpdateError(f"النسخة الجديدة فشلت في الفحص ({' '.join(args[:2])}); لم يتغير شيء\n{tail}")
    return proc.stdout or ""


def check_copy(candidate: Path, db_copy: Path, deps: Path | None, run=subprocess.run) -> dict:
    """The candidate code migrates a COPY and checks it (integrity, foreign keys, stock, audit chain, row
    counts, app start + /api/health). Returns its JSON report."""
    with tempfile.TemporaryDirectory(prefix="pharm-update-check-") as tmp:
        out = _run_candidate(candidate, ["update", "--candidate-check", str(db_copy)],
                             _candidate_env(Path(tmp), deps, db_copy), 1800, run)
    for line in reversed(out.splitlines()):
        if line.startswith("CANDIDATE-CHECK "):
            report = json.loads(line[len("CANDIDATE-CHECK "):])
            if not report.get("ok"):
                raise UpdateError("فحص قاعدة البيانات بعد التحديث فشل؛ لم يتغير شيء: "
                                  + "؛ ".join(report.get("problems", []))[:500])
            return report
    raise UpdateError("النسخة الجديدة لم ترجع نتيجة الفحص؛ لم يتغير شيء")


def _asset_path(home: Path, asset: dict) -> Path:
    path = home / "downloads" / f"{asset['sha256']}.zip"
    if (not path.is_file() or path.stat().st_size != asset["size"]
            or releases.file_sha256(path) != asset["sha256"]):
        raise UpdateError("ملف التحديث مش مطابق للتوقيع")
    return path


def _archive_files(path: Path, *, packages: bool) -> dict:
    from .updater import _members

    with zipfile.ZipFile(path) as z:
        if packages:
            members = (("", info) for info in z.infolist() if not info.is_dir())
        else:
            top, code_members = _members(z)
            members = ((top + "/", info) for info in code_members)
        files = {}
        for prefix, info in members:
            name = _safe_member(info.filename.removeprefix(prefix))
            with z.open(info) as src:
                files[name] = hashlib.file_digest(src, "sha256").hexdigest()
        return files


def _verify_tree(root: Path, expected: dict, *metadata: str) -> None:
    if not root.is_dir() or root.is_symlink() or root.is_junction():
        raise UpdateError("مجلد التحديث غير صالح")
    found = set()
    for path in root.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise UpdateError("ملفات التحديث لا تطابق التوقيع")
        name = path.relative_to(root).as_posix()
        if path.is_dir() or name in metadata:
            continue
        if name not in expected or releases.file_sha256(path) != expected[name]:
            raise UpdateError("ملفات التحديث لا تطابق التوقيع")
        found.add(name)
    if found != expected.keys():
        raise UpdateError("ملفات التحديث لا تطابق التوقيع")


def add_startup_proof(code: Path, manifest: dict, signing_key: str) -> None:
    """Embed a signed tree attestation for launchers that do not retain the outer manifest."""
    from .updater import _members

    files = _archive_files(code, packages=False)
    if STARTUP_PROOF in files:
        raise UpdateError("Reserved startup proof member in release source")
    payload = json.dumps({"purpose": "pharmacy-startup-v1", "release": manifest, "files": files},
                         ensure_ascii=False, sort_keys=True)
    proof = {"payload": payload, "signature": update_sign.sign(payload.encode("utf-8"), signing_key)}
    with zipfile.ZipFile(code, "a", zipfile.ZIP_DEFLATED) as bundle:
        top, _ = _members(bundle)
        bundle.writestr(f"{top}/{STARTUP_PROOF}", json.dumps(proof, ensure_ascii=False))


def _startup_proof(candidate: Path, keys: list[bytes]) -> tuple[dict, dict]:
    proof = _json(candidate / STARTUP_PROOF)
    try:
        raw = proof["payload"].encode("utf-8")
        update_sign.verify(raw, proof["signature"], keys)
        payload = json.loads(raw)
        if payload["purpose"] != "pharmacy-startup-v1" or not isinstance(payload["files"], dict):
            raise ValueError("startup proof format")
        return payload["release"], payload["files"]
    except (AttributeError, KeyError, TypeError, ValueError, update_sign.SignatureError) as exc:
        raise UpdateError("إثبات توقيع ملفات التشغيل ناقص أو غير صحيح") from exc


def _verify_signed_deps(home: Path, legacy: Path, m: dict) -> str | None:
    requirements = legacy / "requirements.txt"
    base = deps_id(requirements.read_bytes() if requirements.exists() else b"", m["python"], m["platform"])
    deps = m["deps"]["id"]
    if deps == base:
        return None
    if "sha256" not in m["deps"]:
        raise UpdateError("التحديث بيغيّر المكتبات من غير حزمة مكتبات")
    packages = _asset_path(home, m["deps"])
    _verify_tree(deps_dir(home, deps), _archive_files(packages, packages=True), ".deps-files.json")
    return deps


def _verify_signed_files(home: Path, legacy: Path, m: dict) -> tuple[Path, str | None]:
    candidate = releases.release_path(home, m["code"]["sha256"])
    code = _asset_path(home, m["code"])
    _verify_tree(candidate, _archive_files(code, packages=False), ".release-source.json")
    return candidate, _verify_signed_deps(home, legacy, m)


def verify_selected_release(home: Path, legacy: Path, entry: dict) -> Path:
    release_id = entry["id"]
    candidate = releases.release_path(home, release_id)
    proof_path = home / "proofs" / f"{release_id}.json"
    keys = update_sign.trusted_keys(legacy)
    if proof_path.exists():
        m = _verify_proof(_json(proof_path), keys)
        if m["code"]["sha256"] != release_id:
            raise UpdateError("بيانات التحديث مش مطابقة للتوقيع")
        _, deps = _verify_signed_files(home, legacy, m)
    else:
        # The deployed updater already checked the signed outer bundle before
        # staging this release. Its selected tree carries its own signed proof.
        m, files = _startup_proof(candidate, keys)
        _verify_tree(candidate, files, ".release-source.json", STARTUP_PROOF)
        deps = _verify_signed_deps(home, legacy, m)
    if (m["sha"] != entry.get("sha") or m["commit_time"] != entry.get("commit_time")
            or m["channel"] != entry.get("ref") or m["python"] != python_tag()
            or m["platform"] != platform_tag() or deps != entry.get("deps")):
        raise UpdateError("بيانات التحديث مش مطابقة للتوقيع")
    return candidate


def prepare(settings, source, *, legacy: Path, run=subprocess.run) -> dict:
    """Download, verify, stage and rehearse. Selects nothing; the live database is only read."""
    from . import updater
    from ..db import Database
    home = home_of(settings.data_dir)
    if bootstrap_level(home) < releases.BOOTSTRAP:     # every smart batch is applied by the new launcher
        raise UpdateError("التحديث الموقّع محتاج إصدار المشغّل اللي بيدعم تطبيق الحزم؛ لم يتغير شيء")
    active_root = releases.current_root(legacy, home)
    proof = _manifest_proof(source)
    m = _verify_proof(proof, update_sign.trusted_keys(legacy))
    info = updater.installed(settings.data_dir)
    chan = updater.channel(info) or "main"
    if m["channel"] != chan:
        raise UpdateError("حزمة التحديث لقناة تانية؛ لم يتغير شيء")
    if m["sha"] == info.get("sha"):
        return {"up_to_date": True, "sha": m["sha"]}
    if not meets_version_floor(home, m, info, getattr(source, "repo", None), getattr(source, "token", None)):
        raise UpdateError("حزمة التحديث أقدم من النسخة المثبتة؛ لم يتغير شيء")
    if m["python"] != python_tag() or m["platform"] != platform_tag():
        raise UpdateError("التحديث ده محتاج نسخة بايثون تانية؛ ينزل بالمثبّت")
    code = download(source, home, m["code"])
    code_bytes = code.read_bytes()
    if hashlib.sha256(code_bytes).hexdigest() != m["code"]["sha256"]:
        raise UpdateError("ملف التحديث مش مطابق للتوقيع")
    staged = updater.apply_zip(code_bytes, legacy, code_home=home)
    candidate = Path(staged["release"])
    releases.check_compatible(active_root, candidate, coordinated=True)
    req = candidate / "requirements.txt"
    if deps_id(req.read_bytes() if req.exists() else b"", m["python"], m["platform"]) != m["deps"]["id"]:
        raise UpdateError("قائمة المكتبات في التحديث مش مطابقة لحزمة المكتبات؛ لم يتغير شيء")
    base = deps_id((legacy / "requirements.txt").read_bytes() if (legacy / "requirements.txt").exists() else b"",
                   m["python"], m["platform"])
    deps = None
    if m["deps"]["id"] != base:
        if "sha256" not in m["deps"]:
            raise UpdateError("التحديث بيغيّر المكتبات من غير حزمة مكتبات؛ لم يتغير شيء")
        deps = stage_deps(home, m["deps"]["id"], download(source, home, m["deps"]))
    _verify_signed_files(home, legacy, m)
    with tempfile.TemporaryDirectory(prefix="pharm-release-check-") as tmp:
        env = _candidate_env(Path(tmp), deps)
        _run_candidate(candidate, ["selftest", "--quick"], env, 300, run)
        _run_candidate(candidate, ["update", "--self-check"], env, 120, run)
    report = {"migrated": []}
    if settings.db_path.exists():                       # rehearsal on an online snapshot (read only)
        with tempfile.TemporaryDirectory(prefix="pharm-rehearsal-", dir=_scratch(settings)) as tmp:
            snap = Path(tmp) / "pharmacy.db"
            db = Database(settings.db_path)
            try:
                db.backup_to(snap)
            finally:
                db.close()
            report = check_copy(candidate, snap, deps, run)
    needs_db = bool(report.get("migrated"))
    prepared = {"release": staged["id"], "sha": m["sha"], "commit_time": m["commit_time"], "version": m["version"],
                "channel": m["channel"], "deps": m["deps"]["id"] if deps else None, "needs_db": needs_db,
                "migrations": report.get("migrated", []), "notes": new_notes(active_root, candidate),
                "active": (releases.read_state(home).get("active") or {}).get("id"),
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    releases.atomic_json(home / "proofs" / f"{staged['id']}.json", proof)
    releases.atomic_json(home / "prepared.json", prepared)
    releases.log_event(home, step="prepare", ok=True, release=staged["id"], sha=m["sha"], needs_db=needs_db)
    return prepared


def _scratch(settings) -> Path:
    """Temporary copies live next to the data (same disk), never in the system temp of another drive."""
    p = Path(settings.data_dir) / "update-candidate"
    p.mkdir(parents=True, exist_ok=True)
    return p


def prepared(data_dir: Path) -> dict:
    return _json(home_of(data_dir) / "prepared.json")


# --------------------------------------------------------------------------- request and trigger
def request(data_dir: Path, when: str, by: str, settings=None) -> dict:
    if when not in WHEN:
        raise UpdateError("اختار: دلوقتي أو بعد قفل الوردية")
    p = prepared(data_dir)
    if not p.get("release"):
        raise UpdateError("التحديث لسه ما اتجهزش")
    if settings is not None:                    # refused here, with the reason on screen, not after the stop
        check_space(settings, needs_db=bool(p.get("needs_db")))
    (home_of(data_dir) / "apply-refused.json").unlink(missing_ok=True)
    value = {"release": p["release"], "when": when, "by": by,
             "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    releases.atomic_json(home_of(data_dir) / "apply-request.json", value)
    releases.log_event(home_of(data_dir), step="request", ok=True, release=p["release"], when=when)
    return value


def pending_request(data_dir: Path) -> dict:
    return _json(home_of(data_dir) / "apply-request.json")


def cancel(data_dir: Path) -> None:
    (home_of(data_dir) / "apply-request.json").unlink(missing_ok=True)
    releases.log_event(home_of(data_dir), step="request", ok=True, cancelled=True)


def busy_reason(db, when: str, now: datetime | None = None) -> str | None:
    """Why the update must wait (None = it may run now). Never during a sale."""
    now = now or datetime.now(timezone.utc)
    last = db.one("SELECT at, action FROM audit_log WHERE seq IS NOT NULL ORDER BY seq DESC LIMIT 1")
    if last and not str(last["action"]).startswith("system.update"):
        try:
            at = datetime.fromisoformat(str(last["at"]).replace("Z", "+00:00"))
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            if now - at < timedelta(seconds=QUIET_SECONDS):
                return "شغل جاري على البرنامج"
        except ValueError:
            pass
    since = (now - timedelta(minutes=SALE_MINUTES)).strftime("%Y-%m-%dT%H:%M:%S")
    for org in db.all("SELECT id FROM organizations"):
        if db.one("SELECT 1 FROM sales s WHERE s.org_id=? AND s.status='draft' AND s.created_at >= ?"
                  " AND EXISTS (SELECT 1 FROM sale_lines l WHERE l.sale_id=s.id) LIMIT 1", (org["id"], since)):
            return "فاتورة بيع مفتوحة"
        if when == "after_close" and db.one(
                "SELECT 1 FROM cash_shifts WHERE org_id=? AND status='open' LIMIT 1", (org["id"],)):
            return "مستني قفل الوردية"
    return None


def due(settings, db) -> dict | None:
    """The request to run now, or None. Called by the running program every 30 s."""
    req = pending_request(settings.data_dir)
    if not req:
        return None
    p = prepared(settings.data_dir)
    if p.get("release") != req.get("release"):
        return None
    if busy_reason(db, req.get("when", "now")):
        return None
    return {**req, "needs_db": bool(p.get("needs_db"))}


class InFlight:
    """ASGI middleware: how many HTTP requests are being handled right now (the update never cuts one)."""
    count = 0
    _lock = __import__("threading").Lock()

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        with InFlight._lock:
            InFlight.count += 1
        try:
            return await self.app(scope, receive, send)
        finally:
            with InFlight._lock:
                InFlight.count -= 1


def trigger(settings, db, in_flight=lambda: InFlight.count, exit=os._exit) -> bool:
    """Leave for the launcher (exit 75), which applies the batch with the program stopped. The gate is checked
    again while holding the database write lock: no transaction can start or be half-written, no request is
    being handled, no sale is in progress (Codex P1 on #386)."""
    req = due(settings, db)
    home = home_of(settings.data_dir)
    if not req or bootstrap_level(home) < releases.BOOTSTRAP:
        return False
    try:                                        # the disk may have filled since the update was scheduled
        check_space(settings, needs_db=bool(req.get("needs_db")))
    except UpdateError as e:
        # not retried every 30 s: the request is dropped and the reason kept for the update screen
        (home / "apply-request.json").unlink(missing_ok=True)
        releases.atomic_json(home / "apply-refused.json", {"release": req["release"], "reason": str(e),
                                                           "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        releases.log_event(home, step="trigger", ok=False, release=req["release"], reason=str(e)[:500])
        return False
    with db._write_lock:
        if busy_reason(db, req.get("when", "now")) or in_flight() > 0:
            return False
        releases.atomic_json(home / "apply-go.json", {"release": req["release"]})
        releases.log_event(home, step="trigger", ok=True, release=req["release"], when=req.get("when"))
        exit(75)
    return True


# --------------------------------------------------------------------------- apply
def _verified_backup(db, settings, backup_dir: Path) -> Path:
    from . import backup
    out = backup.create(db, settings.upload_dir, backup_dir, tag="pre-update")
    if backup.verify_archive(out)["problems"]:
        raise UpdateError("النسخة الاحتياطية قبل التحديث فشلت في التحقق؛ لم يتغير شيء")
    return out


SPACE_MARGIN = 200 * 1024 * 1024     # kept free on every disk the update writes to


def _existing(path) -> Path:
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    return p


def _volume(path):
    """Which disk a folder is on (the nearest existing parent decides)."""
    return os.stat(_existing(path)).st_dev


def _free(path) -> int:
    return shutil.disk_usage(_existing(path)).free


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _tree_size(folder: Path) -> int:
    total = 0
    if folder.is_dir():
        for f in folder.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                pass
    return total


def space_needs(settings, backup_dir: Path, *, needs_db: bool) -> list[dict]:
    """What the update writes, per disk: [{'volume', 'label', 'path', 'bytes'}]. Worst case on purpose
    (nothing is assumed to compress): a refusal here costs nothing, running out with the cashier stopped does.

    - backups folder: the verified backup = database + E-Stock history (its ready .xz when there is one) +
      every attachment;
    - system TEMP: the backup is built there (snapshot + its .xz, and the history's .xz when none is ready),
      then unpacked there again to be verified (database + history): the larger of the two;
    - data folder: the history's .xz kept next to it when none is ready; with a database change also the
      migrated copy and its growth (3 x the database, the rule from before #461)."""
    from . import backup
    db = _file_size(settings.db_path) + _file_size(Path(str(settings.db_path) + "-wal"))
    arch_file = Path(settings.db_path).parent / backup.ESTOCK_ARCHIVE
    arch = _file_size(arch_file)
    cache = _file_size(arch_file.with_name(backup.ARCHIVE_MEMBER)) if arch else 0
    arch_xz = cache or arch
    uploads = _tree_size(Path(settings.upload_dir))
    wants = [("مجلد النسخ الاحتياطية", Path(backup_dir), db + arch_xz + uploads),
             ("المجلد المؤقت للويندوز", Path(tempfile.gettempdir()), max(2 * db + (0 if cache else arch), db + arch)),
             ("مجلد البيانات", Path(settings.data_dir), (3 * db if needs_db else 0) + (0 if cache else arch))]
    out: dict = {}
    for label, path, need in wants:
        vol = _volume(path)
        if vol in out:                              # the same disk: the needs add up
            out[vol]["bytes"] += need
            out[vol]["label"] += " + " + label
        else:
            out[vol] = {"volume": vol, "label": label, "path": str(path), "bytes": need}
    return list(out.values())


def check_space(settings, backup_dir: Path | None = None, *, needs_db: bool = True) -> None:
    """Refuse, saying which disk and how much, when the verified backup or the migrated copy cannot fit.
    Called when the update is scheduled and again just before the program stops, so the cashier is never
    stopped for an update that then fails for lack of room (#461)."""
    backup_dir = Path(backup_dir) if backup_dir else settings.backup_path
    mb = lambda n: f"{-(-n // (1024 * 1024)):,}"
    for n in space_needs(settings, backup_dir, needs_db=needs_db):
        try:
            free = _free(n["path"])
        except OSError:
            raise UpdateError(f"مش قادر أقرأ المساحة الفاضية في {n['label']} ({n['path']})؛ لم يتغير شيء")
        need = n["bytes"] + SPACE_MARGIN
        if free < need:
            raise UpdateError(f"المساحة الفاضية مش كفاية للتحديث في {n['label']} ({n['path']}): محتاج {mb(need)} ميجا"
                              f" والفاضي {mb(free)} ميجا. فضّي مساحة وجرّب تاني؛ لم يتغير شيء")


def last_refusal(data_dir: Path) -> dict:
    """Why the last scheduled update did not start ({} when it did, or was never refused)."""
    return _json(home_of(data_dir) / "apply-refused.json")


def select_code_only(settings, legacy: Path, backup_dir: Path) -> dict:
    """A batch without database changes: verified backup, then select it (first start supervised)."""
    from ..db import Database
    home = home_of(settings.data_dir)
    p = prepared(settings.data_dir)
    if not p.get("release") or p.get("needs_db"):
        raise UpdateError("التحديث ده محتاج يتطبق والبرنامج مقفول")
    _verify_prepared(settings, legacy, home, p)
    if settings.db_path.exists():
        check_space(settings, backup_dir, needs_db=False)      # a code-only update takes a verified backup too
        db = Database(settings.db_path)
        try:
            _verified_backup(db, settings, backup_dir)
        finally:
            db.close()
    _select(legacy, home, p)
    return p


def _verify_prepared(settings, legacy: Path, home: Path, p: dict) -> Path:
    from . import updater

    if (releases.read_state(home).get("active") or {}).get("id") != p.get("active"):
        raise UpdateError("النسخة الشغالة اتغيرت من وقت التجهيز؛ جهّز التحديث تاني")
    if not (home / "proofs" / f"{p['release']}.json").is_file():
        raise UpdateError("التحديث المجهّز محتاج إثبات توقيع الحزمة")
    candidate = verify_selected_release(home, legacy, {**p, "id": p["release"], "ref": p.get("channel")})
    info = updater.installed(settings.data_dir)
    if p["channel"] != (updater.channel(info) or "main") or not meets_version_floor(home, p, info):
        raise UpdateError("حزمة التحديث لقناة تانية أو أقدم من النسخة المثبّتة")
    return candidate


def _select(legacy: Path, home: Path, p: dict) -> None:
    releases.activate(legacy, home, p["release"],
                      {"sha": p["sha"], "source": f"bundle:{p['channel']}@{p['sha']}", "ref": p["channel"],
                       "commit_time": p["commit_time"], "deps": p.get("deps"), "smart": True,
                       "needs_db": bool(p.get("needs_db")), "at": datetime.now().isoformat()},
                      expected_active=p.get("active"), coordinated=True)
    (home / "prepared.json").unlink(missing_ok=True)
    (home / "apply-request.json").unlink(missing_ok=True)
    releases.log_event(home, step="select", ok=True, release=p["release"], needs_db=bool(p.get("needs_db")))


def apply_prepared(settings, legacy: Path, release_id: str, backup_dir: Path, run=subprocess.run) -> dict:
    """Program stopped (run once by stage 0). Backup, copy, migrate the COPY with the new code, check it,
    write the journal, select the code. The live file is only read; stage 0 swaps."""
    from ..db import Database
    home = home_of(settings.data_dir)
    p = prepared(settings.data_dir)
    cand_dir = _scratch(settings)
    copy = cand_dir / "pharmacy.db"
    try:
        if p.get("release") != release_id:
            raise UpdateError("التحديث المطلوب مش هو اللي اتجهز؛ لم يتغير شيء")
        candidate = _verify_prepared(settings, legacy, home, p)
        if not p.get("needs_db"):
            return select_code_only(settings, legacy, backup_dir)
        if releases.read_journal(home).get("state") in ("ready", "swapped", "undoing"):
            raise UpdateError("فيه تحديث سابق لسه ما خلصش")
        check_space(settings, backup_dir, needs_db=True)
        for f in [copy, Path(str(copy) + "-wal"), Path(str(copy) + "-shm")]:
            f.unlink(missing_ok=True)
        db = Database(settings.db_path)
        try:
            backup_file = _verified_backup(db, settings, backup_dir)
            db.backup_to(copy)
        finally:
            db.close()                                  # last connection: WAL merged and removed
        wal = Path(str(settings.db_path) + "-wal")
        if wal.exists() and wal.stat().st_size:
            raise UpdateError("قاعدة البيانات لسه مفتوحة من برنامج تاني؛ لم يتغير شيء")
        live_sha = releases.file_sha256(settings.db_path)
        deps = deps_dir(home, p["deps"]) if p.get("deps") else None
        report = check_copy(candidate, copy, deps, run)
        for side in (Path(str(copy) + "-wal"), Path(str(copy) + "-shm")):
            if side.exists() and side.stat().st_size:
                raise UpdateError("النسخة المجهزة لم تُقفل سليمة؛ لم يتغير شيء")
            side.unlink(missing_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        previous = Path(settings.data_dir) / "update-previous" / f"pharmacy-before-{stamp}.db"
        for old in sorted((previous.parent).glob("pharmacy-before-*")) if previous.parent.exists() else []:
            old.unlink(missing_ok=True)                 # keep one: the verified backup is the long-term copy
        releases.write_journal(home, {
            "state": "ready", "release": p["release"], "backup": str(backup_file),
            "swaps": [{"live": str(settings.db_path), "candidate": str(copy), "previous": str(previous),
                       "live_sha256": live_sha, "candidate_sha256": releases.file_sha256(copy)}]})
        _select(legacy, home, p)
        releases.log_event(home, step="apply", ok=True, release=p["release"], migrated=report.get("migrated"),
                           backup=backup_file.name)
        return {**p, "backup": str(backup_file), "migrated": report.get("migrated")}
    except BaseException as exc:
        j = releases.read_journal(home)
        st = releases.read_state(home)
        selected = (st.get("active") or {}).get("id") == release_id and st.get("pending")
        ours = j.get("release") == release_id and j.get("state") == "ready"
        if not (ours and selected):                     # nothing for stage 0 to swap: drop the copy
            if ours:
                releases.write_journal(home, {**j, "state": "abandoned", "reason": str(exc)[:300]})
            for f in [copy, Path(str(copy) + "-wal"), Path(str(copy) + "-shm")]:
                f.unlink(missing_ok=True)
        (home / "apply-request.json").unlink(missing_ok=True)   # no retry loop; the owner presses again
        releases.log_event(home, step="apply", ok=False, release=release_id, reason=str(exc)[:500])
        raise


# --------------------------------------------------------------------------- inside the candidate
def candidate_check(settings, open_db) -> dict:
    """Runs in the NEW code with PHARM_DB_PATH = the copy: migrate + start-up steps (open_db = manage._db, as
    serve runs them), then integrity, foreign keys, stock balances, audit chain, row counts, /api/health."""
    from . import backup
    from ..db import Database
    tables = ["organizations", "items", "stock_movements", "purchase_invoices", "sales", "audit_log",
              "customers", "suppliers"]
    def counts(db):
        have = {r["name"] for r in db.all("SELECT name FROM sqlite_master WHERE type='table'")}
        return {t: db.one(f"SELECT count(*) c FROM {t}")["c"] for t in tables if t in have}
    raw = Database(settings.db_path)
    try:
        pending = raw.pending_migrations()
        before = counts(raw)
    finally:
        raw.close()
    db = open_db(settings)
    problems = []
    try:
        problems += backup.integrity_problems(db)
        after = counts(db)
        problems += [f"{t}: {before[t]} -> {after.get(t)} rows" for t in before if after.get(t, -1) < before[t]]
        from starlette.testclient import TestClient
        from ..api.app import create_app
        with TestClient(create_app(db, settings)) as c:
            r = c.get("/api/health")
            if r.status_code != 200 or r.json().get("ok") is not True:
                problems.append(f"health {r.status_code}")
    finally:
        db.close()
    return {"ok": not problems, "problems": problems, "migrated": list(pending)}


# --------------------------------------------------------------------------- audit and status
def import_log(db, data_dir: Path) -> int:
    """Copy new lines of update-log.jsonl (written by stage 0/1 outside the database) into the audit log.
    The position read so far is kept IN the database (org_settings), in the same transaction as the audit
    rows: a database put back by an undo also puts the position back, so no event is ever skipped
    (Codex P1 on #386)."""
    from . import audit
    log = home_of(data_dir) / "update-log.jsonl"
    if not log.exists() or not db.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='org_settings'"):
        return 0
    org = db.one("SELECT id FROM organizations ORDER BY created_at LIMIT 1")
    if not org:
        return 0
    data = log.read_bytes()
    n = 0
    with db.transaction():
        row = db.one("SELECT value FROM org_settings WHERE org_id=? AND key='update_log_offset'", (org["id"],))
        try:
            done = int(row["value"]) if row else 0
        except ValueError:
            done = 0
        if done > len(data):
            done = 0
        chunk = data[done:]
        end = chunk.rfind(b"\n") + 1
        if not end:
            return 0
        for line in chunk[:end].decode("utf-8", "replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                event = {"raw": line[:300]}
            audit.record(db, None, f"system.update.{event.get('step', 'event')}", "system", None, after=event,
                         org_id=org["id"], user_id="system")
            n += 1
        db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?, 'update_log_offset', ?)"
                   " ON CONFLICT(org_id, key) DO UPDATE SET value=excluded.value", (org["id"], str(done + end)))
    return n


def last_result(data_dir: Path) -> dict:
    j = releases.read_journal(home_of(data_dir))
    return {k: j.get(k) for k in ("state", "release", "reason")} if j else {}
