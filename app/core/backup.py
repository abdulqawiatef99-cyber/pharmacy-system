"""Backup and restore of a pharmacy node.

A backup is ONE zip file containing:
  pharmacy.db.xz consistent snapshot made with SQLite's online backup API, xz-compressed
                (format 2; format 1 backups stored pharmacy.db and are still readable)
                (safe while the program runs — never copy the .db file by hand:
                recent changes may still sit in the -wal file)
  uploads/...   the original invoice files (needed for audits)
  manifest.json counts + sha256 of the snapshot, for verification

Restore refuses a backup that fails verification, always snapshots the current
database first (pre-restore backup), and verifies the result afterwards.
The server must be stopped during restore.
"""
from __future__ import annotations

import hashlib
import json
import lzma
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from ..db import Database


def integrity_problems(db: Database) -> list[str]:
    from ..inventory import service as inventory
    from . import audit
    problems = []
    r = db.one("PRAGMA integrity_check")[0]
    if r != "ok":
        problems.append(f"sqlite integrity_check: {r}")
    if db.all("PRAGMA foreign_key_check"):
        problems.append("foreign key violations")
    for org in db.all("SELECT id, name FROM organizations"):
        diffs = inventory.verify_balances(db, org["id"])
        if diffs:
            problems.append(f"{org['name']}: {len(diffs)} stock balance differences")
    chain = audit.verify_chain(db)
    if chain:
        problems.append(f"audit chain broken at seq {chain[0]['seq']} ({chain[0]['problem']})")
    return problems


def _counts(db: Database) -> dict:
    tables = ["organizations", "items", "stock_movements", "purchase_invoices", "sales", "audit_log",
              "customers", "suppliers", "source_files"]
    return {t: db.one(f"SELECT count(*) c FROM {t}")["c"] for t in tables}


XZ_PRESET = 1          # measured on a real pharmacy: 102 MB -> ~14 MB in ~4 s


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _extract_db(z: zipfile.ZipFile, tmp: str) -> Path:
    """Write the database of a backup (either format) to tmp/pharmacy.db."""
    names = set(z.namelist())
    target = Path(tmp) / "pharmacy.db"
    if "pharmacy.db.xz" in names:
        with z.open("pharmacy.db.xz") as src, lzma.open(src) as xz, open(target, "wb") as out:
            shutil.copyfileobj(xz, out, 1 << 20)
    else:
        z.extract("pharmacy.db", tmp)
    return target


def create(db: Database, upload_dir: Path, dest_dir: Path, *, tag: str = "") -> Path:
    """tag 'auto' marks scheduled backups: only those are ever deleted by retention."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = dest_dir / f"pharmacy-backup-{stamp}{'-' + tag if tag else ''}.zip"
    n = 1
    while out.exists():
        out = dest_dir / f"pharmacy-backup-{stamp}-{n}{'-' + tag if tag else ''}.zip"
        n += 1
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "pharmacy.db"
        db.backup_to(snap)
        check = Database(snap)
        try:
            problems = integrity_problems(check)
            counts = _counts(check)
        finally:
            check.close()
        if problems:
            raise RuntimeError("the live database failed verification; backup NOT written: " + "; ".join(problems))
        manifest = {"created_at": datetime.now().isoformat(timespec="seconds"), "counts": counts,
                    "db_sha256": _sha256(snap), "db_bytes": snap.stat().st_size, "format": 2}
        xz = Path(tmp) / "pharmacy.db.xz"
        with open(snap, "rb") as src, lzma.open(xz, "wb", preset=XZ_PRESET) as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        part = out.with_suffix(".zip.part")
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(xz, "pharmacy.db.xz", compress_type=zipfile.ZIP_STORED)
            if upload_dir.exists():
                for f in sorted(upload_dir.rglob("*")):
                    if f.is_file() and not f.name.endswith(".part"):
                        z.write(f, "uploads/" + f.relative_to(upload_dir).as_posix())
            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        part.replace(out)
    return out


def verify_archive(archive: Path) -> dict:
    """Open a backup in a temp folder and check everything. Returns the manifest
    plus 'problems' (empty = good)."""
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(archive) as z:
        bad = z.testzip()
        if bad:
            return {"problems": [f"corrupt member in zip: {bad}"]}
        names = set(z.namelist())
        if "manifest.json" not in names or not names & {"pharmacy.db", "pharmacy.db.xz"}:
            return {"problems": ["not a pharmacy backup (missing pharmacy.db or manifest.json)"]}
        manifest = json.loads(z.read("manifest.json"))
        try:
            snap = _extract_db(z, tmp)
        except lzma.LZMAError as e:
            return {"problems": [f"database unreadable: {e}"]}
        problems = []
        if _sha256(snap) != manifest.get("db_sha256"):
            problems.append("database checksum does not match the manifest")
        try:
            db = Database(snap)
            try:
                problems += integrity_problems(db)
                if _counts(db) != manifest.get("counts"):
                    problems.append("record counts differ from the manifest")
            finally:
                db.close()
        except sqlite3.DatabaseError as e:
            problems.append(f"database unreadable: {e}")
        manifest["problems"] = problems
        manifest["files"] = len([n for n in names if n.startswith("uploads/")])
        return manifest


def restore(archive: Path, db_path: Path, upload_dir: Path, pre_restore_dir: Path) -> dict:
    """Server must be stopped. Returns {'pre_restore_backup', 'manifest'}."""
    info = verify_archive(archive)
    if info["problems"]:
        raise RuntimeError("backup failed verification, nothing restored: " + "; ".join(info["problems"]))
    pre = None
    if db_path.exists():
        live = Database(db_path)
        try:
            pre = create(live, upload_dir, pre_restore_dir)
        finally:
            live.close()
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(archive) as z:
        for name in z.namelist():
            if name.startswith("uploads/") and ".." not in name.split("/"):
                z.extract(name, tmp)
        src = Database(_extract_db(z, tmp))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(str(db_path) + suffix).unlink(missing_ok=True)
        tmp_target = db_path.with_suffix(".restoring")
        tmp_target.unlink(missing_ok=True)
        try:
            src.backup_to(tmp_target)
        finally:
            src.close()
        tmp_target.replace(db_path)
        up_src = Path(tmp) / "uploads"
        if up_src.exists():
            for f in up_src.rglob("*"):
                if f.is_file():
                    target = upload_dir / f.relative_to(up_src)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, target)
    restored = Database(db_path)
    try:
        problems = integrity_problems(restored)
    finally:
        restored.close()
    if problems:
        raise RuntimeError("restored database failed verification: " + "; ".join(problems)
                           + f" — previous data kept in {pre}")
    return {"pre_restore_backup": str(pre) if pre else None, "manifest": info}


def quick_check(archive: Path) -> list[str]:
    """Cheap check right after writing: zip CRCs + the database matches its checksum."""
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(archive) as z:
        bad = z.testzip()
        if bad:
            return [f"corrupt member in zip: {bad}"]
        manifest = json.loads(z.read("manifest.json"))
        if _sha256(_extract_db(z, tmp)) != manifest.get("db_sha256"):
            return ["database checksum does not match the manifest"]
    return []
