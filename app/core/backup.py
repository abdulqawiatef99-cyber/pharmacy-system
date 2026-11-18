"""Backup and restore of a pharmacy node.

A backup is ONE zip file containing:
  pharmacy.db.xz consistent snapshot made with SQLite's online backup API, xz-compressed
                (format 2; format 1 backups stored pharmacy.db and are still readable)
                (safe while the program runs — never copy the .db file by hand:
                recent changes may still sit in the -wal file)
  uploads/...   the original invoice files (needed for audits)
  estock-archive.sqlite.xz  the read-only E-Stock history next to the database, when there is one (F91)
  manifest.json counts + sha256 of the snapshot, for verification

Restore refuses a backup that fails verification, always keeps the current
database first (a pre-restore backup, or a raw copy of the file when the current
database is itself damaged), and verifies the result afterwards.
The server must be stopped during restore.
"""
from __future__ import annotations

import hashlib
import json
import lzma
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

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
ESTOCK_ARCHIVE = "estock-archive.sqlite"     # owner data next to the database: it goes into every backup
ARCHIVE_MEMBER = ESTOCK_ARCHIVE + ".xz"


def _estock_archive_of(db_path) -> Path | None:
    if str(db_path) == ":memory:":
        return None
    p = Path(db_path).parent / ESTOCK_ARCHIVE
    return p if p.is_file() else None


def _archive_xz(archive: Path, tmp: str) -> tuple[Path, str, int]:
    """The compressed archive for a backup. It never changes after the move, so the .xz is made once and
    kept next to it (checked by the archive's sha256 each time), not recompressed every hour."""
    sha = _sha256(archive)
    cache, mark = archive.with_name(ARCHIVE_MEMBER), archive.with_name(ARCHIVE_MEMBER + ".sha256")
    try:
        if cache.is_file() and mark.is_file() and mark.read_text(encoding="ascii").strip() == sha:
            return cache, sha, archive.stat().st_size
    except OSError:
        pass
    out = Path(tmp) / ARCHIVE_MEMBER
    with open(archive, "rb") as src, lzma.open(out, "wb", preset=XZ_PRESET) as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    try:                                   # the cache is only a speed-up: a read-only folder does not stop a backup
        shutil.copy2(out, cache.with_suffix(".part"))
        cache.with_suffix(".part").replace(cache)
        mark.write_text(sha, encoding="ascii")
    except OSError:
        pass
    return out, sha, archive.stat().st_size


def _extract_archive(z: zipfile.ZipFile, tmp: str) -> Path:
    target = Path(tmp) / ESTOCK_ARCHIVE
    with z.open(ARCHIVE_MEMBER) as member, lzma.open(member) as src, open(target, "wb") as out:
        n = 0
        while chunk := src.read(1 << 20):
            n += len(chunk)
            if n > MAX_DB_BYTES:
                raise RuntimeError("E-Stock archive larger than the limit; not extracted")
            out.write(chunk)
    return target


def _archive_problems(z: zipfile.ZipFile, manifest: dict, tmp: str) -> list[str]:
    want = manifest.get("estock_archive")
    if not want:
        return []
    if ARCHIVE_MEMBER not in z.namelist():
        return ["the E-Stock archive is missing from the backup"]
    try:
        got = _extract_archive(z, tmp)
    except (lzma.LZMAError, RuntimeError) as e:
        return [f"E-Stock archive unreadable: {e}"]
    if _sha256(got) != want.get("sha256"):
        return ["E-Stock archive checksum does not match the manifest"]
    return []


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


MAX_DB_BYTES = 8 * 1024 ** 3     # far above one pharmacy's database; a bigger expansion is refused (#324 X18)


def _extract_db(z: zipfile.ZipFile, tmp: str) -> Path:
    """Write the database of a backup (either format) to tmp/pharmacy.db, never more than MAX_DB_BYTES
    (a small archive could otherwise expand until the disk is full)."""
    names = set(z.namelist())
    target = Path(tmp) / "pharmacy.db"
    with (z.open("pharmacy.db.xz") if "pharmacy.db.xz" in names else z.open("pharmacy.db")) as member, \
            (lzma.open(member) if "pharmacy.db.xz" in names else member) as src, open(target, "wb") as out:
        n = 0
        while chunk := src.read(1 << 20):
            n += len(chunk)
            if n > MAX_DB_BYTES:
                raise RuntimeError("backup database larger than the limit; not extracted")
            out.write(chunk)
    return target


def _member_of(row: dict) -> str | None:
    """The archive member of a registered original (the layout _attachment_checks accepts), or None."""
    org = str(row["org_id"])
    stored = PurePosixPath(str(row["storage_path"]).replace("\\", "/"))
    if not org or org in (".", "..") or "/" in org or "\\" in org or stored.name in ("", ".", ".."):
        return None
    if stored.parent.name == org:
        return f"uploads/{org}/{stored.name}"
    if stored.parent.name == "transfers" and stored.parent.parent.name == org:
        return f"uploads/{org}/transfers/{stored.name}"
    return None


def _attachment_checks(db: Database, z: zipfile.ZipFile) -> list[tuple[dict, str | None]]:
    """(source row, problem or None) for every registered original, legacy ZIPs included.

    Stored paths are metadata only: never open an old machine's absolute path.
    Both writers use org/name or org/transfers/name beneath the upload root.
    """
    out = []
    for row in db.all("SELECT id, org_id, filename, storage_path, size_bytes, sha256 FROM source_files"):
        row = dict(row)
        org = str(row["org_id"])
        stored = PurePosixPath(str(row["storage_path"]).replace("\\", "/"))
        parent = stored.parent
        if (not org or org in (".", "..") or "/" in org or "\\" in org
                or stored.name in ("", ".", "..")):
            out.append((row, f"invalid attachment path for source {row['id']}"))
            continue
        if parent.name == org:
            member = f"uploads/{org}/{stored.name}"
        elif parent.name == "transfers" and parent.parent.name == org:
            member = f"uploads/{org}/transfers/{stored.name}"
        else:
            out.append((row, f"unknown attachment layout for source {row['id']}"))
            continue
        try:
            info = z.getinfo(member)
        except KeyError:
            out.append((row, f"missing original attachment for source {row['id']}"))
            continue
        if info.is_dir() or info.file_size != row["size_bytes"]:
            out.append((row, f"attachment size mismatch for source {row['id']}"))
            continue
        digest = hashlib.sha256()
        with z.open(info) as src:
            while chunk := src.read(1 << 20):
                digest.update(chunk)
        out.append((row, None if digest.hexdigest() == row["sha256"]
                    else f"attachment checksum mismatch for source {row['id']}"))
    return out


# The per-file messages of _attachment_checks. Only these are "attachment problems": the database part of
# the archive is good and one registered original is missing or differs. Everything else is a hard problem.
ATTACHMENT_PROBLEM = re.compile(r"^(?:invalid attachment path|unknown attachment layout|missing original attachment"
                                r"|attachment size mismatch|attachment checksum mismatch) for source (\S+)$")


def attachment_source(problem: str) -> str | None:
    """The source_files id an attachment problem is about, or None for any other problem."""
    m = ATTACHMENT_PROBLEM.match(problem)
    return m.group(1) if m else None


def split_problems(problems: list[str]) -> tuple[list[str], list[str]]:
    """(hard problems, attachment problems)."""
    return ([p for p in problems if not attachment_source(p)], [p for p in problems if attachment_source(p)])


def _attachment_problems(db: Database, z: zipfile.ZipFile) -> list[str]:
    names = z.namelist()
    if len(names) != len(set(names)):
        return ["duplicate members in backup archive"]
    if len(names) != len({n.casefold() for n in names}):
        # on Windows both names are one file: the checked copy could be overwritten by the other (#324 X15)
        return ["duplicate members (differing only in letter case) in backup archive"]
    return [p for _, p in _attachment_checks(db, z) if p]


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
        arch = _estock_archive_of(db.path)
        arch_xz = None
        if arch:
            arch_xz, arch_sha, arch_bytes = _archive_xz(arch, tmp)
            manifest["estock_archive"] = {"sha256": arch_sha, "bytes": arch_bytes}
        xz = Path(tmp) / "pharmacy.db.xz"
        with open(snap, "rb") as src, lzma.open(xz, "wb", preset=XZ_PRESET) as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        part = out.with_suffix(".zip.part")
        try:
            with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(xz, "pharmacy.db.xz", compress_type=zipfile.ZIP_STORED)
                if arch_xz:
                    z.write(arch_xz, ARCHIVE_MEMBER, compress_type=zipfile.ZIP_STORED)
                if upload_dir.exists():
                    for f in sorted(upload_dir.rglob("*")):
                        if f.is_file() and not f.name.endswith(".part"):
                            z.write(f, "uploads/" + f.relative_to(upload_dir).as_posix())
                z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            part.replace(out)
        except BaseException:                       # disk full, locked file, Ctrl+C: no half file left behind
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass
            raise
    return out


UNREADABLE = "not a readable backup file (cut off while copying, or not a ZIP)"


def verify_archive(archive: Path) -> dict:
    """Open a backup in a temp folder and check everything. Returns the manifest
    plus 'problems' (empty = good). A file that is not a ZIP at all (a copy cut off on a USB stick) is a
    problem like any other, never an exception."""
    try:
        return _verify_archive(archive)
    except (zipfile.BadZipFile, EOFError, json.JSONDecodeError, KeyError) as e:
        return {"problems": [f"{UNREADABLE}: {type(e).__name__}"]}


def _verify_archive(archive: Path) -> dict:
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
        problems += _archive_problems(z, manifest, tmp)
        try:
            db = Database(snap)
            try:
                newer = db.unknown_migrations()
                if newer:
                    problems.append("this backup was written by a newer version of the program (unknown database"
                                    " update: " + ", ".join(newer) + "); install the newer program to restore it")
                problems += integrity_problems(db)
                problems += _attachment_problems(db, z)
                if _counts(db) != manifest.get("counts"):
                    problems.append("record counts differ from the manifest")
            finally:
                db.close()
        except sqlite3.DatabaseError as e:
            problems.append(f"database unreadable: {e}")
        manifest["problems"] = problems
        manifest["files"] = len([n for n in names if n.startswith("uploads/")])
        return manifest


def _raw_copy(db_path: Path, dest_dir: Path) -> Path:
    """Safety copy of a live database that cannot be backed up the normal way (it fails verification or
    is not a readable database): the file and its -wal/-shm, byte for byte, in their own folder.
    NOT a verified backup: it is the damaged data as it was, kept for repair by hand."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = dest_dir / f"RAW-damaged-database-{stamp}"
    n = 1
    while folder.exists():
        folder = dest_dir / f"RAW-damaged-database-{stamp}-{n}"
        n += 1
    folder.mkdir(parents=True)
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(db_path) + suffix)
        if src.exists():
            shutil.copy2(src, folder / src.name)
            if src.stat().st_size != (folder / src.name).stat().st_size:
                raise OSError(f"raw copy of {src.name} is incomplete")
    return folder


def restore(archive: Path, db_path: Path, upload_dir: Path, pre_restore_dir: Path, *,
            allow_missing_attachments: bool = False) -> dict:
    """Server must be stopped. Returns {'pre_restore_backup', 'pre_restore_kind', 'pre_restore_error',
    'missing_attachments', 'manifest'}.

    The current data is always kept first. pre_restore_kind says how: 'verified_backup' (a normal backup
    ZIP), or 'raw_copy' when the current database fails verification or cannot be read at all, which is
    exactly when a restore is needed: then pre_restore_backup is a folder with the raw database file(s)
    and pre_restore_error says why. None when there was no database. If no safety copy can be made,
    nothing is restored.

    An archive whose registered attachments are missing or differ is refused, unless
    allow_missing_attachments: then the database and the files that are there are restored and
    missing_attachments lists the rest ({'source_id', 'filename', 'problem'}). Any other problem
    (database, checksum, counts) is always refused."""
    info = verify_archive(archive)
    hard, soft = split_problems(info["problems"])
    if hard or (soft and not allow_missing_attachments):
        raise RuntimeError("backup failed verification, nothing restored: " + "; ".join(info["problems"]))
    pre, pre_kind, pre_error = None, None, None
    if db_path.exists():
        # raw copy first: merely opening a damaged database lets SQLite discard its -wal file
        raw = _raw_copy(db_path, pre_restore_dir)
        try:
            live = Database(db_path)
            try:
                pre = create(live, upload_dir, pre_restore_dir)
            finally:
                live.close()
            pre_kind = "verified_backup"
            shutil.rmtree(raw, ignore_errors=True)      # the verified backup replaces it
        except (RuntimeError, sqlite3.Error) as e:      # damaged live data must not block its own repair
            pre, pre_kind, pre_error = raw, "raw_copy", str(e)
    bad = {attachment_source(p) for p in soft}
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(archive) as z:
        # 1) the database is staged next to the live one and checked there; the live one is untouched so far
        src = Database(_extract_db(z, tmp))
        try:
            skip = {_member_of(dict(r)) for r in src.all("SELECT id, org_id, storage_path FROM source_files")
                    if r["id"] in bad}
        finally:
            src.close()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        staged = db_path.with_suffix(".restoring")
        staged.unlink(missing_ok=True)
        src = Database(Path(tmp) / "pharmacy.db")
        try:
            src.backup_to(staged)
        finally:
            src.close()
        restored = Database(staged)
        try:
            problems = integrity_problems(restored)
            if problems:
                raise RuntimeError("restored database failed verification: " + "; ".join(problems)
                                   + f" — nothing replaced; previous data kept in {pre}")
            # sessions saved in the backup (taken before a logout or a password change) would sign in
            # again: everyone signs in anew after a restore (#324 X17)
            with restored.transaction():
                restored.execute("UPDATE sessions SET revoked_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')"
                                 " WHERE revoked_at IS NULL")
            restored.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")    # into the file that is moved below
            missing = []
            for p in soft:
                row = restored.one("SELECT filename FROM source_files WHERE id=?", (attachment_source(p),))
                missing.append({"source_id": attachment_source(p), "filename": row["filename"] if row else None,
                                "problem": p})
        except BaseException:
            restored.close()
            staged.unlink(missing_ok=True)
            raise
        restored.close()
        for suffix in ("-wal", "-shm"):
            Path(str(staged) + suffix).unlink(missing_ok=True)
        # 2) attachments before the database: a copy that fails (locked file, full disk) leaves the live
        # database as it was, never a restored database without its files (#324 X16). A file that failed
        # its checksum is never installed, even when missing ones are allowed (#324 X15).
        try:
            for name in z.namelist():
                if name.startswith("uploads/") and ".." not in name.split("/") and name not in skip:
                    z.extract(name, tmp)
            up_src = Path(tmp) / "uploads"
            if up_src.exists():
                for f in up_src.rglob("*"):
                    if f.is_file():
                        target = upload_dir / f.relative_to(up_src)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(f, target)
        except BaseException:
            staged.unlink(missing_ok=True)
            raise
        # the E-Stock history goes back with its database; one that the backup does not have is kept aside,
        # renamed, never deleted (it is in the pre-restore backup too)
        live_arch = db_path.parent / ESTOCK_ARCHIVE
        try:
            arch_new = _extract_archive(z, tmp) if info.get("estock_archive") else None
            if arch_new and live_arch.exists() and _sha256(live_arch) == _sha256(arch_new):
                arch_new = None                         # the same history: nothing to move
            elif live_arch.exists():
                live_arch.chmod(0o644)
                aside = live_arch.with_name(f"estock-archive-replaced-{datetime.now():%Y%m%d-%H%M%S}.sqlite")
                live_arch.replace(aside)
            for f in (live_arch.with_name(ARCHIVE_MEMBER), live_arch.with_name(ARCHIVE_MEMBER + ".sha256")):
                f.unlink(missing_ok=True)
            if arch_new:
                shutil.copy2(arch_new, live_arch.with_suffix(".part"))
                live_arch.with_suffix(".part").replace(live_arch)
                live_arch.chmod(0o444)
        except BaseException:
            staged.unlink(missing_ok=True)
            raise
        # 3) only now the database itself
        for suffix in ("-wal", "-shm"):
            Path(str(db_path) + suffix).unlink(missing_ok=True)
        staged.replace(db_path)
    return {"pre_restore_backup": str(pre) if pre else None, "pre_restore_kind": pre_kind,
            "pre_restore_error": pre_error, "missing_attachments": missing, "manifest": info}


def quick_check(archive: Path) -> list[str]:
    """Check ZIP CRCs, snapshot checksum and registered originals after writing."""
    try:
        return _quick_check(archive)
    except (zipfile.BadZipFile, EOFError, json.JSONDecodeError, KeyError, lzma.LZMAError) as e:
        return [f"{UNREADABLE}: {type(e).__name__}"]


def _quick_check(archive: Path) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(archive) as z:
        bad = z.testzip()
        if bad:
            return [f"corrupt member in zip: {bad}"]
        manifest = json.loads(z.read("manifest.json"))
        snap = _extract_db(z, tmp)
        if _sha256(snap) != manifest.get("db_sha256"):
            return ["database checksum does not match the manifest"]
        arch = _archive_problems(z, manifest, tmp)
        if arch:
            return arch
        db = Database(snap)
        try:
            return _attachment_problems(db, z)
        except sqlite3.DatabaseError as e:
            return [f"database unreadable: {e}"]
        finally:
            db.close()
