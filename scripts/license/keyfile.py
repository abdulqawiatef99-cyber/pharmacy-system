"""Publisher key files for the signing tools (licences, releases): where they may live and how they open.

Rules enforced here, not left to memory (docs/PROTECTION_PLAN.md, "المفاتيح"):
- a private key file is never inside a git repository (so it cannot be committed, pushed or packed
  into an installer by accident);
- it is always protected by a passphrase, asked on the keyboard, or read from an environment variable
  for automation; never from the command line (command lines are visible to other programs and logs);
- an existing key file is never overwritten.
"""
from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.core import signed_envelope as se  # noqa: E402

PASSPHRASE_ENV = "PHARM_SIGNING_PASSPHRASE"


class KeyFileError(RuntimeError):
    pass


def outside_any_repository(path: Path) -> Path:
    """The resolved path, refused when it is inside this repository or any other git work tree."""
    path = Path(path).expanduser().resolve()
    for folder in [path, *path.parents]:
        if folder == ROOT or (folder / ".git").exists():
            raise KeyFileError(f"refused: {path} is inside a git repository ({folder}). "
                               "A private key lives outside every repository, e.g. on an encrypted USB drive.")
    return path


def passphrase(confirm: bool = False) -> str:
    value = os.environ.get(PASSPHRASE_ENV)
    if value is None:
        value = getpass.getpass("Key passphrase: ")
        if confirm and getpass.getpass("Again: ") != value:
            raise KeyFileError("the two passphrases differ")
    if len(value) < se.MIN_PASSPHRASE:
        raise KeyFileError(f"the passphrase needs at least {se.MIN_PASSPHRASE} characters")
    return value


def create(path: Path) -> str:
    """Write a new passphrase-protected private key at `path`; returns the public key (hex)."""
    path = outside_any_repository(path)
    if path.exists():
        raise KeyFileError(f"refused: {path} already exists (a key file is never overwritten)")
    pem, public_hex = se.new_private_key_pem(passphrase(confirm=True))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)      # owner-only where the OS supports it
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    return public_hex


def load(path: Path):
    path = outside_any_repository(path)
    try:
        pem = path.read_bytes()
    except OSError as e:
        raise KeyFileError(f"key file not readable: {path} ({e.strerror or e})") from None
    try:
        return se.load_private_key(pem, passphrase())
    except ValueError as e:
        raise KeyFileError(str(e)) from None
