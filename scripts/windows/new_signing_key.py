"""A new update signing key pair, written only to the folder the owner picks (flash drive F:\\ by default).
Part 3 item 0, outcome (b): update-1's private half is lost, the spare (update-2) becomes the signing key,
and this makes the NEW spare (update-3). Never prints the private key; never overwrites an existing one.

  new-signing-key.bat              -> F:\\pharmacy-update-keys
  new-signing-key.bat G:\\keys      -> there

Writes update-signing-PRIVATE.txt (stays on the flash drive, offline) and update-3.pub (an agent commits it
to release_keys/). Exit code: 0 = written, 1 = a private key file is already there (nothing changed), 3 = failed.
"""
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

DEFAULT = Path("F:\\pharmacy-update-keys")
PRIVATE, PUBLIC = "update-signing-PRIVATE.txt", "update-3.pub"


def generate() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    priv = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(priv).decode("ascii"), base64.b64encode(pub).decode("ascii")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    out = Path(argv[0]) if argv else DEFAULT
    if not argv and not Path(out.anchor).exists():
        print(f"RESULT: FAIL - الفلاشة مش ظاهرة ({out.anchor}). حطها وجرّب تاني.")
        return 3
    out.mkdir(parents=True, exist_ok=True)
    priv_file, pub_file = out / PRIVATE, out / PUBLIC
    for f in (priv_file, pub_file):          # either one already there: an earlier pair, never replaced
        if f.exists():
            print(f"RESULT: فيه مفتاح موجود بالفعل هنا، ماغيرتش حاجة:\n  {f}")
            return 1
    priv, pub = generate()
    fd = os.open(pub_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(pub + "\n")
    fd = os.open(priv_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(priv + "\n")
    print("RESULT: اتعمل مفتاح احتياطي جديد.")
    print("المفتاح الخاص (يفضل على الفلاشة بس، ماتبعتوش لحد):")
    print(f"  {priv_file}")
    print("المفتاح العام (ابعته للوكيل عشان يتحط في البرنامج):")
    print(f"  {out / PUBLIC}")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):     # Arabic output even without -X utf8 (a cp1252 console)
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"RESULT: FAIL - الأداة وقفت ({type(exc).__name__})")
        sys.exit(3)
