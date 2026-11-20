"""Which update signing key is in which file (owner 20/11 23:18, part 3 item 0). READ ONLY.

Looks at small .txt files (a flash drive by default: every drive letter from D: to Z:, three folders deep)
and says, for each one that holds an Ed25519 private key, which committed public key it matches:
update-1 (the signing key), update-2-spare (the spare that must stay offline) or none of ours.
The key itself is never printed, copied or written anywhere; only file paths and public key names.

  signing-key-check.bat                -> every drive D: .. Z:
  signing-key-check.bat F:\\some\\folder  -> only there (a file can also be dropped on the .bat)

Exit code: 0 = update-1 found, 1 = only the spare found, 2 = no key of ours found, 3 = could not run.
"""
from __future__ import annotations

import base64
import binascii
import os
import string
import sys
from pathlib import Path

MAX_BYTES = 4096
DEPTH = 3
SIGNING, SPARE = "update-1.pub", "update-2-spare.pub"


def default_keys_dir(here: Path) -> Path:
    """tools folder: keys\\ next to this file; a downloaded copy of the repository: ..\\..\\release_keys."""
    for p in (here / "keys", here.parent.parent / "release_keys"):
        if p.is_dir() and any(p.glob("*.pub")):
            return p
    return here / "keys"


def load_keys(folder: Path) -> dict[bytes, str]:
    out = {}
    for p in sorted(Path(folder).glob("*.pub")):
        try:
            raw = base64.b64decode(p.read_text(encoding="ascii").strip(), validate=True)
        except (OSError, ValueError, binascii.Error):
            continue
        if len(raw) == 32:
            out[raw] = p.name
    return out


def public_of(text: str) -> bytes | None:
    """The public key of a private key written as one base64 line of 32 bytes; None for anything else."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    try:
        raw = base64.b64decode(text.strip(), validate=True)
    except (ValueError, binascii.Error):
        return None
    if len(raw) != 32:
        return None
    return Ed25519PrivateKey.from_private_bytes(raw).public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _files(root: Path):
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        if depth >= DEPTH:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if not d.startswith(("$", "System Volume Information"))]
        for f in filenames:
            if f.lower().endswith(".txt"):
                yield Path(dirpath) / f


def scan(roots, keys: dict[bytes, str]) -> list[tuple[str, str | None]]:
    """[(file, public key file name or None)] for every small .txt file that holds a private key."""
    found = []
    for root in roots:
        for f in _files(Path(root)):
            try:
                if f.stat().st_size > MAX_BYTES:
                    continue
                text = f.read_text(encoding="utf-8-sig", errors="strict")
            except (OSError, UnicodeDecodeError):
                continue
            pub = public_of(text)
            if pub is not None:
                found.append((str(f), keys.get(pub)))
    return found


def drives() -> list[Path]:
    if os.name != "nt":
        return []
    return [Path(f"{d}:\\") for d in string.ascii_uppercase[3:] if Path(f"{d}:\\").exists()]


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    here = Path(__file__).resolve().parent
    keys_dir = default_keys_dir(here)
    if argv[:1] == ["--keys"]:
        keys_dir, argv = Path(argv[1]), argv[2:]
    keys = load_keys(keys_dir)
    if not keys:
        print(f"RESULT: FAIL - مفيش مفاتيح عامة نقارن بيها في:\n  {keys_dir}")
        return 3
    roots = [Path(a) for a in argv] or drives()
    if not roots:
        print("RESULT: FAIL - مفيش فلاشة أو درايف تاني ظاهر. حط الفلاشة وجرّب تاني، أو اسحب الملف على الأداة.")
        return 3
    print("بدوّر على ملفات المفاتيح في:")
    for r in roots:
        print(f"  {r}")
    found = scan(roots, keys)
    print()
    names = {SIGNING: "مفتاح التوقيع الأساسي", SPARE: "المفتاح الاحتياطي (لازم يفضل برّه الجهاز)"}
    for f, k in found:
        print(f"{names.get(k, 'مفتاح مش من مفاتيح البرنامج') if k else 'مفتاح مش من مفاتيح البرنامج'}:")
        print(f"  {f}")
        if k:
            print(f"  ({k})")
    has = {k for _, k in found}
    print()
    if SIGNING in has:
        print("RESULT: لقيت مفتاح التوقيع الأساسي. ده اللي يتحط في سر جيت هاب، والاحتياطي يفضل على الفلاشة بس.")
        return 0
    if SPARE in has:
        print("RESULT: لقيت الاحتياطي بس، والأساسي مش موجود هنا. لو مش لاقيه في أي مكان: الاحتياطي يبقى مفتاح التوقيع"
              " ونعمل احتياطي جديد بأداة new-signing-key.bat.")
        return 1
    print("RESULT: مالقيتش أي مفتاح من مفاتيح البرنامج في الأماكن دي.")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:          # a broken drive or file never shows a traceback to the owner
        print(f"RESULT: FAIL - الأداة وقفت ({type(exc).__name__})")
        sys.exit(3)
