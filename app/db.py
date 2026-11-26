"""Database access layer.

Local node uses SQLite (see docs/DECISIONS.md ADR-002). All SQL is kept close to
ANSI so a PostgreSQL adapter can be added for the central server later.

Rules enforced here:
- foreign keys ON, WAL journal, busy timeout (concurrent readers + one writer).
- every write goes through `transaction()` which uses BEGIN IMMEDIATE so two
  writers never interleave a read-modify-write.
- migrations are plain .sql files applied in order and recorded in
  schema_migrations with a checksum; an edited, already-applied migration is a
  hard error (never silently diverge), and so is an applied migration this
  program has no file for (data from a newer version of the program).
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
import uuid
import time
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Any

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def new_id() -> str:
    """UUIDv7-style id: time-ordered (good index locality) and globally unique,
    so records created offline on different machines never collide when synced."""
    ms = int(time.time() * 1000)
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76                      # version 7
    value |= (rand >> 68 & 0xFFF) << 64     # rand_a (12 bits)
    value |= 0b10 << 62                     # variant
    value |= rand & ((1 << 62) - 1)         # rand_b (62 bits)
    return str(uuid.UUID(int=value))


class MigrationError(RuntimeError):
    pass


# #104/#274: two additive migrations that exist only on `main` (reference catalog, never in the first
# release). Data written by a `main` build carries them; their tables are unused here and harmless, so
# such data is accepted. Any other applied migration without a file means a newer program: refused.
MAIN_ONLY_MIGRATIONS = frozenset({"0034_reference_catalog", "0035_reference_links_prices"})


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self._all: list[sqlite3.Connection] = []
        self._all_lock = threading.Lock()

    # -- connections ---------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 15000")
            if self.path != ":memory:":
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA synchronous = FULL")  # financial data: durability first
        except sqlite3.Error:
            conn.close()          # e.g. the file is not a database
            raise
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = self._connect()
            self._local.conn = c
            with self._all_lock:
                self._all.append(c)
        return c

    def close(self) -> None:
        """Close every connection opened by any thread (server shutdown / tests)."""
        with self._all_lock:
            for c in self._all:
                try:
                    c.close()
                except sqlite3.Error:
                    pass
            self._all.clear()
        self._local = threading.local()

    # -- queries -------------------------------------------------------------
    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def all(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        if not self.conn.in_transaction:
            raise RuntimeError("writes must run inside db.transaction()")
        return self.conn.execute(sql, params)

    @property
    def in_snapshot(self) -> bool:
        """This thread is inside read_snapshot(): it reads one fixed state and cannot write."""
        return bool(getattr(self._local, "snapshot", False))

    @property
    def write_serial(self) -> int:
        """How many write transactions this thread has begun: tells one transaction from the next (a value
        remembered inside one that was rolled back must not be used in the next)."""
        return getattr(self._local, "writes", 0)

    @contextmanager
    def read_snapshot(self) -> Iterator["Database"]:
        """Read-only block that sees one consistent state of the database (one WAL snapshot), for
        reports that run off the event loop while sales keep writing (#249). Writes are refused
        inside it; nothing to commit, so it always ends with ROLLBACK."""
        conn = self.conn
        if conn.in_transaction:
            yield self
            return
        conn.execute("BEGIN")
        self._local.snapshot = True
        try:
            yield self
        finally:
            self._local.snapshot = False
            if conn.in_transaction:
                conn.execute("ROLLBACK")

    @contextmanager
    def transaction(self) -> Iterator["Database"]:
        """Nested calls join the outer transaction (savepoint-free on purpose:
        a business operation is atomic as a whole)."""
        if getattr(self._local, "snapshot", False):
            raise RuntimeError("no writes inside a read snapshot")
        conn = self.conn
        if conn.in_transaction:
            yield self
            return
        with self._write_lock:
            conn.execute("BEGIN IMMEDIATE")
            self._local.writes = getattr(self._local, "writes", 0) + 1
            try:
                yield self
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    # -- migrations ----------------------------------------------------------
    def pending_migrations(self, directory: Path = MIGRATIONS_DIR) -> list[str]:
        """Migrations migrate() would apply now. Read-only: creates nothing."""
        conn = self.conn
        applied: set[str] = set()
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone():
            applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
        return [f.stem for f in sorted(directory.glob("*.sql")) if f.stem not in applied]

    def unknown_migrations(self, directory: Path = MIGRATIONS_DIR) -> list[str]:
        """Applied migrations this program has no file for: the data was written by a newer version of
        the program. Read-only: creates nothing."""
        conn = self.conn
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone():
            return []
        known = {f.stem for f in directory.glob("*.sql")} | MAIN_ONLY_MIGRATIONS
        return sorted(r["version"] for r in conn.execute("SELECT version FROM schema_migrations")
                      if r["version"] not in known)

    def migrate(self, directory: Path = MIGRATIONS_DIR) -> list[str]:
        conn = self.conn
        newer = self.unknown_migrations(directory)
        if newer:                                   # before anything is created or applied
            raise MigrationError(
                "this data was written by a newer version of the program (unknown database update: "
                + ", ".join(newer) + "); install the newer program, nothing was changed")
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)""")
        applied = {r["version"]: r["checksum"] for r in
                   conn.execute("SELECT version, checksum FROM schema_migrations")}
        done: list[str] = []
        for f in sorted(directory.glob("*.sql")):
            sql = f.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            version = f.stem
            if version in applied:
                if applied[version] != checksum:
                    raise MigrationError(
                        f"migration {version} was edited after being applied; "
                        "add a new migration instead")
                continue
            # Table rebuilds (SQLite cannot ALTER a CHECK constraint) follow the
            # official procedure: foreign keys off, rebuild in one transaction,
            # foreign_key_check must be clean before commit, foreign keys back on.
            fk_off = "-- requires: foreign_keys_off" in sql.splitlines()[0]
            with self._write_lock:
                if fk_off:
                    conn.execute("PRAGMA foreign_keys = OFF")
                conn.execute("BEGIN IMMEDIATE")
                try:
                    for stmt in _split_sql(sql):
                        conn.execute(stmt)
                    if fk_off and conn.execute("PRAGMA foreign_key_check").fetchall():
                        raise MigrationError(f"migration {version} left foreign key violations")
                    conn.execute("INSERT INTO schema_migrations VALUES (?,?,datetime('now'))",
                                 (version, checksum))
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
                finally:
                    if fk_off:
                        conn.execute("PRAGMA foreign_keys = ON")
            done.append(version)
        self.drop_planner_stats()
        return done

    def drop_planner_stats(self) -> bool:
        """Remove SQLite's planner statistics (sqlite_stat1/stat4), if any (#249).

        Every query was written and measured for SQLite's default plans (primary keys and indexes).
        Statistics gathered on a small database and kept after a bulk load (E-Stock import, restore)
        made the planner scan whole tables: the stock report took 118 s on 30k items. Dropping the
        tables changes the schema, so every open connection, in any process, re-plans without them.
        Changes no pharmacy data. Returns True when something was dropped."""
        conn = self.conn
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('sqlite_stat1','sqlite_stat4')")]
        if not names:
            return False
        with self._write_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for n in names:
                    conn.execute(f"DROP TABLE {n}")
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return True

    def backup_to(self, dest: str | Path) -> None:
        """Online, consistent backup (safe while the app is running)."""
        target = sqlite3.connect(str(dest))
        try:
            self.conn.backup(target)
        finally:
            target.close()


def _split_sql(sql: str) -> list[str]:
    """Split a migration into statements; understands BEGIN...END trigger bodies."""
    stmts, buf, depth = [], [], 0
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        buf.append(line)
        upper = stripped.upper()
        if upper.endswith("BEGIN") or " BEGIN" in upper and upper.startswith("CREATE TRIGGER"):
            depth += 1
        if depth and (upper == "END;" or upper == "END"):
            depth -= 1
            if depth == 0:
                stmts.append("\n".join(buf))
                buf = []
            continue
        if depth == 0 and stripped.endswith(";"):
            stmts.append("\n".join(buf))
            buf = []
    if buf and "\n".join(buf).strip():
        stmts.append("\n".join(buf))
    return stmts


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None
