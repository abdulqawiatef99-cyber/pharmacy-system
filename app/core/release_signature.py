"""Publisher-signed releases: an update is accepted only if the publisher signed exactly these files.

NOT used by the updater yet (docs/PROTECTION_PLAN.md, layer 2). Today an update is trusted because it
came over HTTPS from the project's GitHub account, and `release_bootstrap.verify_release` checks the
staged files against a list that arrives with the release itself (damage, not forgery). With this
module the list is signed: whoever controls the download channel, a stolen read token, or a mirror
cannot ship code, only the holder of the release key can. Wiring it into `app/core/updater.py` and
`manage.py update` is a later change after launch.

The signed manifest (envelope purpose "release", app/core/signed_envelope.py):

    {"format": 1, "product": "pharmacy-platform",
     "channel": "main",                 the update channel this release is for; never accepted on another
     "version": "0.4.0", "commit": "<40 hex>",
     "sequence": 1234,                  grows with every release of a channel: an older release is refused
     "released_at": "2026-11-19T12:00:00Z",
     "protocol": 1,                     release_protocol.json of this release
     "archive_sha256": "<64 hex>" | null,   the exact download, when the channel serves one fixed file
     "files": {"manage.py": "<sha256>", "app/db.py": "<sha256>", ...}}    every file, nothing else

`files` has the same shape as the `files` of `.release-source.json` that the updater writes for a
staged release, so the later wiring compares the two with `check_files`.

What this does NOT do: it does not hide the code, and it cannot stop someone who already controls the
PC from editing the installed files. It protects the road between the publisher and the PC.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from app.core import signed_envelope as se

PURPOSE = "release"
FORMAT = 1
PRODUCT = "pharmacy-platform"
CHANNELS = ("main", "develop")
MAX_FILES = 15000                          # same ceiling as the updater's archive check
# top-level names the updater never takes from an archive (app/core/updater.py PROTECTED): a signed
# release must not contain them, so "exactly the signed files" stays true after staging
RESERVED_TOP = ("data", "backups", ".venv", "venv", ".git", "__pycache__", ".code-releases", ".release-source.json")
STAGING_FILE = ".release-source.json"      # written by the updater into a staged release, not part of it
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_VERSION = re.compile(r"[0-9]+(\.[0-9]+){1,3}([a-z0-9.+-]{0,20})")
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_FIELDS = {"format", "product", "channel", "version", "commit", "sequence", "released_at", "protocol",
           "archive_sha256", "files"}

# Release public keys built into the program (Ed25519, 64 hex characters each; public keys are not
# secret). EMPTY until the owner's key ceremony: with no key every signed release is refused, which is
# safe today because the updater does not ask for a signature yet. Keep two from the first day: the key
# in use and an offline spare (see app/licensing/keys.py for the same rule and why).
RELEASE_KEYS: tuple[str, ...] = ()


class ReleaseSignatureError(ValueError):
    """Refused. `code`: malformed | wrong_purpose | no_trusted_key | untrusted_key | bad_signature |
    unsupported_format | wrong_product | wrong_channel | wrong_protocol | downgrade | archive_mismatch |
    files_mismatch."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def safe_path(name) -> bool:
    """A release file name: relative, forward slashes, no '..', nothing Windows would rename or refuse."""
    if not isinstance(name, str) or not name or len(name) > 400 or "\\" in name or ":" in name or "\x00" in name:
        return False
    parts = PurePosixPath(name).parts
    return (PurePosixPath(name).as_posix() == name and not name.startswith("/") and ".." not in parts
            and all(p.rstrip(" .") == p and p != "." for p in parts)
            and parts[0].casefold() not in RESERVED_TOP)


@dataclass(frozen=True)
class Manifest:
    channel: str
    version: str
    commit: str
    sequence: int
    released_at: datetime
    protocol: int
    archive_sha256: str | None
    files: dict

    def payload(self) -> dict:
        return {"format": FORMAT, "product": PRODUCT, "channel": self.channel, "version": self.version,
                "commit": self.commit, "sequence": self.sequence,
                "released_at": self.released_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "protocol": self.protocol, "archive_sha256": self.archive_sha256,
                "files": dict(sorted(self.files.items()))}


def from_payload(value) -> Manifest:
    def bad(why: str):
        raise ReleaseSignatureError("malformed", why)
    if not isinstance(value, dict):
        bad("not a release manifest")
    if type(value.get("format")) is not int or value["format"] != FORMAT:
        raise ReleaseSignatureError("unsupported_format", "this release needs a newer program")
    if value.get("product") != PRODUCT:
        raise ReleaseSignatureError("wrong_product", "this release is for another program")
    if set(value) != _FIELDS:
        bad("unexpected or missing fields")
    if value["channel"] not in CHANNELS:
        bad("unknown channel")
    if not isinstance(value["version"], str) or not _VERSION.fullmatch(value["version"]):
        bad("bad version")
    if not isinstance(value["commit"], str) or not _COMMIT.fullmatch(value["commit"]):
        bad("bad commit")
    for name in ("sequence", "protocol"):
        if type(value[name]) is not int or not 1 <= value[name] < 2 ** 53:
            bad(f"bad {name}")
    if not isinstance(value["released_at"], str) or not _TIME.fullmatch(value["released_at"]):
        bad("bad release time")
    try:
        released = datetime.strptime(value["released_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        bad("bad release time")
    digest = value["archive_sha256"]
    if digest is not None and (not isinstance(digest, str) or not _SHA256.fullmatch(digest)):
        bad("bad archive digest")
    files = value["files"]
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES:
        bad("bad file list")
    seen = set()
    for name, sha in files.items():
        if not safe_path(name) or not isinstance(sha, str) or not _SHA256.fullmatch(sha):
            bad(f"bad file entry: {str(name)[:80]}")
        if name.casefold() in seen:                 # Windows would write both names into one file
            bad(f"two files differ only by letter case: {name[:80]}")
        seen.add(name.casefold())
    if "manage.py" not in files or not any(n.startswith("app/") for n in files):
        bad("not a release of this program")
    return Manifest(value["channel"], value["version"], value["commit"], value["sequence"], released,
                    value["protocol"], digest, dict(files))


def verify_manifest(data: bytes, trusted_keys=None) -> Manifest:
    """The manifest out of a signed file, only if a built-in release key signed it."""
    try:
        payload = se.open_envelope(data, PURPOSE, RELEASE_KEYS if trusted_keys is None else trusted_keys)
        value = se.strict_json(payload)
    except se.EnvelopeError as e:
        raise ReleaseSignatureError(e.code, str(e).partition(": ")[2]) from None
    return from_payload(value)


def sign_manifest(manifest: Manifest, *private_keys) -> bytes:
    """Publisher side: the signed file. Refuses a manifest the program itself would refuse."""
    checked = from_payload(manifest.payload())
    return se.sign(se.canonical_json(checked.payload()), PURPOSE, *private_keys)


def check_policy(manifest: Manifest, *, channel: str, protocol: int, installed_sequence: int | None = None) -> None:
    """Is this release meant for this install? Its own channel, the protocol this program speaks, and
    never older than what is installed (a valid but old release with a known hole must not come back).
    The same sequence again is allowed: it is the same release."""
    if manifest.channel != channel:
        raise ReleaseSignatureError("wrong_channel", f"release of {manifest.channel}, this install follows {channel}")
    if manifest.protocol != protocol:
        raise ReleaseSignatureError("wrong_protocol", "this release needs an installer, not the update button")
    if installed_sequence is not None and manifest.sequence < installed_sequence:
        raise ReleaseSignatureError("downgrade", f"release {manifest.sequence} is older than the installed "
                                                 f"{installed_sequence}")


def check_archive(manifest: Manifest, data: bytes) -> None:
    """The downloaded file is the one that was signed (only when the manifest names one)."""
    if manifest.archive_sha256 is None:
        raise ReleaseSignatureError("archive_mismatch", "this manifest does not name an archive; check the files")
    if hashlib.sha256(data).hexdigest() != manifest.archive_sha256:
        raise ReleaseSignatureError("archive_mismatch", "the download is not the signed release")


def check_files(manifest: Manifest, hashes: dict) -> None:
    """`hashes` (name -> sha256 of what is on disk) is exactly the signed list: nothing changed,
    nothing missing, nothing added."""
    signed = manifest.files
    changed = sorted(n for n in signed.keys() & hashes.keys() if signed[n] != hashes[n])
    missing, extra = sorted(signed.keys() - hashes.keys()), sorted(hashes.keys() - signed.keys())
    if changed or missing or extra:
        parts = [f"{label}: {', '.join(names[:5])}{' …' if len(names) > 5 else ''}"
                 for label, names in (("changed", changed), ("missing", missing), ("not signed", extra)) if names]
        raise ReleaseSignatureError("files_mismatch", "; ".join(parts))


def hash_tree(root: Path, *, ignore=(STAGING_FILE,)) -> dict:
    """name -> sha256 for every file under `root` (exact bytes). A link is refused: it could point
    outside the release. Used by the signing tool and by `check_tree`."""
    root = Path(root)
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_symlink():
            raise ReleaseSignatureError("files_mismatch", f"a link is not allowed in a release: {rel}")
        if p.is_dir() or rel in ignore:
            continue
        if not p.is_file():
            raise ReleaseSignatureError("files_mismatch", f"not a regular file: {rel}")
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def check_tree(manifest: Manifest, root: Path) -> None:
    """The folder `root` holds exactly the signed release."""
    check_files(manifest, hash_tree(root))


def build_manifest(root: Path, *, channel: str, version: str, commit: str, sequence: int,
                   released_at: datetime, protocol: int, archive: bytes | None = None) -> Manifest:
    """Publisher side: the manifest of the release in folder `root` (validated like a received one)."""
    if released_at.tzinfo is None:
        raise ValueError("pass an aware datetime")
    files = hash_tree(root, ignore=())
    reserved = sorted({n for n in files if n.split("/")[0].casefold() in RESERVED_TOP})
    if reserved:
        raise ReleaseSignatureError("malformed", "a release may not contain: " + ", ".join(reserved[:5]))
    manifest = Manifest(channel, version, commit, sequence, released_at.replace(microsecond=0), protocol,
                        hashlib.sha256(archive).hexdigest() if archive is not None else None, files)
    return from_payload(manifest.payload())
