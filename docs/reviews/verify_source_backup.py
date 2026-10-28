"""Verify a source-only backup without executing or extracting its application.
Usage: python verify_source_backup.py path/to/pharmacy-source.zip
This verifies integrity, not trusted authorship or production readiness.
"""
import hashlib
import json
import stat
import sys
import zipfile
from pathlib import PurePosixPath

MAX_FILES = 5000
MAX_BYTES = 100 * 1024 * 1024


def verify(path):
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        names = [i.filename for i in infos]
        if len(infos) > MAX_FILES or len(set(names)) != len(names):
            raise ValueError("Too many entries or duplicate paths")
        if sum(i.file_size for i in infos) > MAX_BYTES:
            raise ValueError("Archive is larger than the source backup limit")
        for info in infos:
            n = info.filename
            p = PurePosixPath(n)
            if (not n or p.is_absolute() or ".." in p.parts or "\\" in n
                    or ":" in n or "\x00" in n or p.as_posix() != n
                    or info.is_dir() or stat.S_ISLNK(info.external_attr >> 16)):
                raise ValueError("Unsafe or non-file archive member")
        metadata = z.getinfo("BACKUP-MANIFEST.json")
        if metadata.file_size > 2 * 1024 * 1024:
            raise ValueError("Manifest is too large")
        manifest = json.loads(z.read(metadata))
        if manifest.get("format") != 1 or manifest.get("kind") != "source-only":
            raise ValueError("Unsupported backup format")
        files = manifest["files"]
        if not isinstance(files, list) or not files:
            raise ValueError("Empty or invalid manifest")
        expected = {"BACKUP-MANIFEST.json"}
        for f in files:
            n = "project/" + f["path"]
            if n in expected:
                raise ValueError("Duplicate manifest entry")
            expected.add(n)
            content = z.read(n)
            if len(content) != f["size"]:
                raise ValueError("Size mismatch: " + f["path"])
            if hashlib.sha256(content).hexdigest() != f["sha256"]:
                raise ValueError("SHA-256 mismatch: " + f["path"])
            blob = b"blob " + str(len(content)).encode() + b"\x00" + content
            if hashlib.sha1(blob).hexdigest() != f["git_blob_sha1"]:
                raise ValueError("Git blob mismatch: " + f["path"])
        if set(names) != expected:
            raise ValueError("Missing or unexpected archive members")
        return {"source_ref": manifest["source_ref"], "files": len(files),
                "bytes": sum(f["size"] for f in files), "verified": True}


def main():
    if len(sys.argv) != 2:
        print("Usage: python verify_source_backup.py backup.zip", file=sys.stderr)
        return 2
    try:
        result = verify(sys.argv[1])
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        print("BACKUP INVALID: " + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
