"""The licence on this PC: where the file lives, the one-call check, and installing a new file.

NOT called by the running program yet (docs/PROTECTION_PLAN.md): the launcher, the server and the
screens are wired in a later change that ships with an installer. Until then this is a library with
tests, and `keys.PUBLISHER_KEYS` is empty, so nothing can be locked by mistake.

What the later wiring must keep (owner's rule, in the plan): an ended licence never hides or deletes
the pharmacy's data. Backups, export and reports stay available in every state.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import clock, fingerprint, keys
from .license import MAX_LICENSE_BYTES, License, LicenseError, Status, evaluate, status, verify

LICENSE_FILE = "license.lic"
PREVIOUS_FILE = "license.previous.lic"


def license_path(data_dir: Path) -> Path:
    return Path(data_dir) / LICENSE_FILE


def clock_paths(data_dir: Path, extra_dir: Path | None = None) -> list[Path]:
    """Where the clock mark is kept: the data folder, and a second folder when the caller has one."""
    paths = [Path(data_dir) / clock.FILE_NAME]
    if extra_dir is not None:
        paths.append(Path(extra_dir) / clock.FILE_NAME)
    return paths


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def _judge(data: bytes, *, now, device, trusted, marks, evidence, revoked) -> Status:
    try:
        lic = verify(data, keys.PUBLISHER_KEYS if trusted is None else trusted)
    except LicenseError as e:
        return status("invalid", reason=e.code)
    return evaluate(lic, now=now, device=device if device is not None else fingerprint.current(),
                    high_water=clock.high_water(marks, evidence), revoked=revoked)


def check(data_dir: Path, *, now: datetime | None = None, device: fingerprint.Device | None = None,
          trusted=None, evidence: datetime | None = None, extra_clock_dir: Path | None = None,
          revoked=frozenset(), remember: bool = True) -> Status:
    """State of the licence in `data_dir` for this PC, now.

    `evidence`: the latest dated record in the pharmacy's own data, if the caller has one (clock.py).
    `remember`: raise the clock mark afterwards (the normal case); False for a read-only look."""
    now = _now(now)
    marks = clock_paths(data_dir, extra_clock_dir)
    path = license_path(data_dir)
    try:
        if path.stat().st_size > MAX_LICENSE_BYTES:
            result = status("invalid", reason="malformed")
        else:
            result = _judge(path.read_bytes(), now=now, device=device, trusted=trusted, marks=marks,
                            evidence=evidence, revoked=revoked)
    except FileNotFoundError:
        result = status("missing")
    except OSError:
        result = status("invalid", reason="unreadable")
    if remember:
        clock.remember(marks, now, evidence)
    return result


class InstallRefused(ValueError):
    """The new file was not installed; the licence already on the PC is untouched. `.status` says why."""

    def __init__(self, new: Status):
        super().__init__(new.reason)
        self.status = new


def install(data_dir: Path, data: bytes, *, now: datetime | None = None, device: fingerprint.Device | None = None,
            trusted=None, evidence: datetime | None = None, extra_clock_dir: Path | None = None,
            revoked=frozenset()) -> Status:
    """Put a new licence file in place, only if it is good for this PC.

    Refused (InstallRefused, nothing changed): not signed by the publisher, for another PC, revoked,
    already over, or worse than a licence that is working now. The file in use before is kept next to
    the new one as license.previous.lic."""
    now = _now(now)
    marks = clock_paths(data_dir, extra_clock_dir)
    common = dict(now=now, device=device, trusted=trusted, marks=marks, evidence=evidence, revoked=revoked)
    new = _judge(data, **common)
    if new.state not in ("valid", "grace", "not_yet_valid"):
        raise InstallRefused(new)
    path = license_path(data_dir)
    try:
        old_bytes = path.read_bytes()
    except OSError:
        old_bytes = None
    if old_bytes is not None and old_bytes != data:
        old = _judge(old_bytes, **common)
        if old.usable and not new.usable:
            raise InstallRefused(new)                # never trade a working licence for one that is not
    path.parent.mkdir(parents=True, exist_ok=True)
    if old_bytes is not None and old_bytes != data:
        _atomic_write(path.with_name(PREVIOUS_FILE), old_bytes)
    _atomic_write(path, bytes(data))
    clock.remember(marks, now, evidence)
    return new


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".license-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def activation_request(device: fingerprint.Device | None = None, label: str | None = None) -> str:
    """The line the customer sends to the publisher to get a licence for this PC."""
    return fingerprint.encode_request(device if device is not None else fingerprint.current(), label)


__all__ = ["License", "Status", "InstallRefused", "check", "install", "activation_request", "license_path",
           "clock_paths", "LICENSE_FILE"]
