#!/usr/bin/env python3
"""One time (owner, on his own PC): create the update signing key (docs/SMART_UPDATE.md §2).

  python scripts/release/make_signing_key.py OUT_FOLDER

Writes OUT_FOLDER/update-signing-PRIVATE.txt (goes ONLY into the GitHub repository secret
UPDATE_SIGNING_KEY, then delete the file) and OUT_FOLDER/update.pub (committed to release_keys/ by a PR).
The private key is never printed, committed, sent in chat or kept in the repository.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.core import update_sign  # noqa: E402


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__)
        return 2
    out = Path(argv[0])
    out.mkdir(parents=True, exist_ok=True)
    priv_file = out / "update-signing-PRIVATE.txt"
    if priv_file.exists():
        print(f"{priv_file} already exists; nothing changed")
        return 1
    priv, pub = update_sign.generate()
    priv_file.write_text(priv + "\n", encoding="ascii")
    (out / "update.pub").write_text(pub + "\n", encoding="ascii")
    print(f"written: {priv_file} (secret) and {out / 'update.pub'} (public)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
