#!/usr/bin/env python3
"""Build and sign a smart-update bundle for one channel (docs/SMART_UPDATE.md §2). Run by CI.

  python scripts/release/build_bundle.py --channel main --out dist/update [--site-packages DIR]

The signing key comes from the environment variable UPDATE_SIGNING_KEY (a repository secret); without it
nothing is written (fail closed). Output: manifest.json, manifest.sig, code-<sha>.zip, deps-<id>.zip.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from app.core import smart_update as su, update_sign  # noqa: E402

FIXED_TIME = (2020, 1, 1, 0, 0, 0)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True, text=True).stdout.strip()


def zip_dir(src: Path, out: Path) -> None:
    """Deterministic zip of a package folder (sorted names, fixed times)."""
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src.rglob("*")):
            if p.is_file() and not p.is_symlink():
                info = zipfile.ZipInfo(p.relative_to(src).as_posix(), FIXED_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                z.writestr(info, p.read_bytes())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True, choices=("main", "develop"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--site-packages", help="pip --target folder built for the PC (win_amd64)")
    ap.add_argument("--python", default="3.13")
    ap.add_argument("--platform", default="win_amd64")
    a = ap.parse_args(argv)
    key = os.environ.get("UPDATE_SIGNING_KEY", "").strip()
    if not key:
        print("UPDATE_SIGNING_KEY is not set: no bundle is built (the button keeps the code-only path).")
        return 0
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    sha = git("rev-parse", "HEAD")
    code = out / "code.zip"
    subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "archive", "--format=zip",
                    "--prefix=program/", "-o", str(code), "HEAD"], cwd=REPO, check=True)
    ident = su.deps_id((REPO / "requirements.txt").read_bytes(), a.python, a.platform)
    deps = None
    if a.site_packages:
        deps = out / "deps.zip"
        zip_dir(Path(a.site_packages), deps)
    version = {}
    exec((REPO / "app" / "version.py").read_text(encoding="utf-8"), version)
    raw = su.make_manifest(channel=a.channel, sha=sha, commit_time=int(git("show", "-s", "--format=%ct", "HEAD")),
                           version=version["__version__"], python=a.python, platform=a.platform, code_zip=code,
                           deps_zip=deps, deps=ident,
                           migrations=[p.stem for p in (REPO / "app" / "migrations").glob("*.sql")],
                           notes=su.notes_of(REPO))
    import json
    m = json.loads(raw)
    os.replace(code, out / m["code"]["name"])
    if deps:
        os.replace(deps, out / m["deps"]["name"])
    (out / su.MANIFEST).write_bytes(raw)
    sig = update_sign.sign(raw, key)
    update_sign.verify(raw, sig, update_sign.trusted_keys(REPO))     # the PCs must accept it: refuse otherwise
    su.parse_manifest(raw, sig, update_sign.trusted_keys(REPO))      # and read it the way the PC does
    (out / su.SIGNATURE).write_text(sig, encoding="ascii")
    print(f"bundle {a.channel} {sha[:7]}: {m['code']['size']} bytes code, deps {ident[:12]}, "
          f"signed with {KEY_NOTE.format(signing_key_file(raw, sig))}")
    return 0


KEY_NOTE = "release_keys/{} (public file name; the secret is never printed)"


def signing_key_file(raw: bytes, sig: str) -> str:
    """Which committed public key verifies this signature: the owner checks the secret is the key meant for
    signing (update-1), not the spare kept offline for rotation."""
    for p in sorted((REPO / update_sign.KEY_DIR).glob("*.pub")):
        try:
            update_sign.verify(raw, sig, [update_sign.key_of(p)])
            return p.name
        except update_sign.SignatureError:
            continue
    return "?"


if __name__ == "__main__":
    sys.exit(main())
