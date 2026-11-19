#!/usr/bin/env python3
"""Publisher's tool: describe a release (manifest) and sign it. The private key is read from a path
OUTSIDE the repository and behind a passphrase (scripts/license/keyfile.py); it is a different key
from the licence key.

  keygen   --out E:\\keys\\release-1.key        make a release key; prints the public key to build in
  public   --key E:\\keys\\release-1.key
  manifest --tree DIR | --from-git REF          write the unsigned manifest (plain JSON, to read and compare)
           --channel main --sequence N [--version V] [--commit SHA] [--archive FILE] --out manifest.json
  sign     --key ... [--key ...second] --manifest manifest.json --out release.sig.json
  verify   --public-key HEX --signed release.sig.json [--tree DIR] [--archive FILE]
           [--channel main] [--installed-sequence N]

Two steps on purpose: the machine that builds a release writes the manifest and needs no secret; the
signature is made where the key lives. With `--from-git REF` the manifest describes exactly what
`git archive REF` contains (what GitHub serves as the update archive), and `--sequence` defaults to the
number of commits behind REF, which only grows on a branch.

Nothing here publishes anything, and the running program does not check these signatures yet
(docs/PROTECTION_PLAN.md, layer 2).
"""
from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "license"))
import keyfile  # noqa: E402  (puts the repository root on sys.path)

from app.core import release_signature as rs  # noqa: E402
from app.core import signed_envelope as se  # noqa: E402


class ToolError(RuntimeError):
    pass


def _git(*args: str) -> bytes:
    p = subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=ROOT, capture_output=True)
    if p.returncode != 0:
        raise ToolError(f"git {' '.join(args[:2])} failed: {p.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return p.stdout


def export_git(ref: str, dest: Path) -> str:
    """The files of commit `ref` exactly as `git archive` gives them (LF line ends), into `dest`.
    Returns the full commit id."""
    commit = _git("rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()
    with tarfile.open(fileobj=io.BytesIO(_git("archive", "--format=tar", commit))) as tar:
        tar.extractall(dest, filter="data")
    return commit


def _version(tree: Path) -> str:
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', (tree / "app" / "version.py").read_text(encoding="utf-8"), re.M)
    if not m:
        raise ToolError("app/version.py has no __version__")
    return m.group(1)


def _protocol(tree: Path) -> int:
    return int(json.loads((tree / "release_protocol.json").read_text(encoding="utf-8"))["protocol"])


def cmd_manifest(a) -> int:
    out = Path(a.out).expanduser().resolve()
    with tempfile.TemporaryDirectory() as tmp:
        if a.from_git:
            tree = Path(tmp) / "tree"
            tree.mkdir()
            commit = export_git(a.from_git, tree)
            sequence = a.sequence or int(_git("rev-list", "--count", commit).decode().strip())
        else:
            tree = Path(a.tree).resolve()
            if not tree.is_dir():
                raise ToolError(f"not a folder: {tree}")
            commit, sequence = a.commit, a.sequence
            if not commit or not sequence:
                raise ToolError("--tree needs --commit and --sequence")
        archive = Path(a.archive).read_bytes() if a.archive else None
        manifest = rs.build_manifest(tree, channel=a.channel, version=a.version or _version(tree),
                                     commit=a.commit or commit, sequence=sequence,
                                     released_at=datetime.now(timezone.utc), protocol=_protocol(tree), archive=archive)
    out.write_bytes(se.canonical_json(manifest.payload()))
    print(f"manifest: {out} ({len(manifest.files)} files, channel {manifest.channel}, version {manifest.version}, "
          f"commit {manifest.commit[:12]}, sequence {manifest.sequence})")
    return 0


def cmd_sign(a) -> int:
    out = Path(a.out).expanduser().resolve()
    if out.exists():
        raise ToolError(f"refused: {out} already exists")
    manifest = rs.from_payload(se.strict_json(Path(a.manifest).read_bytes()))     # sign only what would be accepted
    keys = [keyfile.load(Path(k)) for k in a.key]
    out.write_bytes(rs.sign_manifest(manifest, *keys))
    print(f"signed: {out} (channel {manifest.channel}, version {manifest.version}, sequence {manifest.sequence}, "
          f"keys {', '.join(se.key_id(k) for k in keys)})")
    return 0


def cmd_verify(a) -> int:
    manifest = rs.verify_manifest(Path(a.signed).read_bytes(), a.public_key)
    print(f"signature: OK (channel {manifest.channel}, version {manifest.version}, commit {manifest.commit[:12]}, "
          f"sequence {manifest.sequence}, {len(manifest.files)} files)")
    if a.channel:
        rs.check_policy(manifest, channel=a.channel, protocol=manifest.protocol)
        print("channel: OK")
    if a.installed_sequence is not None:            # asked for by itself too: never skipped for want of --channel
        rs.check_policy(manifest, channel=manifest.channel, protocol=manifest.protocol,
                        installed_sequence=a.installed_sequence)
        print("sequence: OK")
    if a.archive:
        rs.check_archive(manifest, Path(a.archive).read_bytes())
        print("archive: OK")
    if a.tree:
        rs.check_tree(manifest, Path(a.tree))
        print("files: OK")
    return 0


def cmd_keygen(a) -> int:
    public_hex = keyfile.create(Path(a.out))
    print(f"private key written: {Path(a.out).expanduser().resolve()}")
    print("KEEP IT OUT of the repository, e-mail and chat. A second copy in another place.")
    print(f"public key (build into the program, app/core/release_signature.py RELEASE_KEYS): {public_hex}")
    print(f"key id: {se.key_id(public_hex)}")
    return 0


def cmd_public(a) -> int:
    key = keyfile.load(Path(a.key))
    print(f"public key: {se.public_key_bytes(key).hex()}")
    print(f"key id: {se.key_id(key)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("--out", required=True)
    pb = sub.add_parser("public")
    pb.add_argument("--key", required=True)
    m = sub.add_parser("manifest")
    src = m.add_mutually_exclusive_group(required=True)
    src.add_argument("--tree", help="folder holding the release exactly as it will be installed")
    src.add_argument("--from-git", metavar="REF", help="a commit of this repository (what `git archive` contains)")
    m.add_argument("--channel", choices=rs.CHANNELS, required=True)
    m.add_argument("--sequence", type=int)
    m.add_argument("--version")
    m.add_argument("--commit")
    m.add_argument("--archive", help="the exact file customers download, when there is one")
    m.add_argument("--out", required=True)
    s = sub.add_parser("sign")
    s.add_argument("--key", action="append", required=True, help="private key file outside the repository (repeatable)")
    s.add_argument("--manifest", required=True)
    s.add_argument("--out", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--public-key", action="append", required=True, help="64 hex characters (repeatable)")
    v.add_argument("--signed", required=True)
    v.add_argument("--tree")
    v.add_argument("--archive")
    v.add_argument("--channel", choices=rs.CHANNELS)
    v.add_argument("--installed-sequence", type=int)
    a = p.parse_args(argv)
    try:
        return {"keygen": cmd_keygen, "public": cmd_public, "manifest": cmd_manifest, "sign": cmd_sign,
                "verify": cmd_verify}[a.cmd](a)
    except (ToolError, keyfile.KeyFileError, rs.ReleaseSignatureError, se.EnvelopeError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
