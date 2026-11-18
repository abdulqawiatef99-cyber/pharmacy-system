"""Read-only archive of the E-Stock history (F91): every exported table, as text, in its own SQLite file
next to the database, plus the maps from E-Stock ids to the program's ids. The main schema needs no
migration; screens read it later (a customer's past items, demand, contract tickets).

Written once, on --apply: built in a temp file, row counts checked against the export, then moved into
place and marked read-only. The same export again keeps the file; another export is refused (the
history must not be mixed)."""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
import stat
from datetime import datetime
from pathlib import Path

from ..core.backup import ESTOCK_ARCHIVE as NAME   # the backup carries it (one name for both)
INDEX_COLS = ("product_id", "customer_id", "vendor_id", "sales_id", "back_sales_id", "purchase_id", "store_id")


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def source_sha(path: Path) -> str | None:
    if not path.exists():
        return None
    with closing(open_ro(path)) as con:
        r = con.execute("SELECT value FROM meta WHERE key='source_sha'").fetchone()
        return r[0] if r else None


def open_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)


def build(ex, path: Path, maps: dict[str, dict[str, str]], *, keep: bool = True) -> dict[str, int]:
    """Writes the archive at `path` (or checks the one there). Returns rows per table as stored."""
    sha = ex.source_sha()
    if path.exists():
        old = source_sha(path)
        if old != sha:
            raise RuntimeError(f"فيه أرشيف إي ستوك من تصدير تاني: {path}. مفيش أي حاجة اتكتبت")
        with closing(open_ro(path)) as con:
            return {r[0]: r[1] for r in con.execute("SELECT name, rows FROM tables")}
    tmp = path.with_name(path.name + ".part")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    counts: dict[str, int] = {}
    try:
        con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        con.execute("CREATE TABLE tables (name TEXT PRIMARY KEY, schema_name TEXT, rows INTEGER, manifest_rows INTEGER)")
        for t in sorted(ex.tables.values(), key=lambda t: t["name"].lower()):
            head, rows = ex.raw(t["name"])
            cols = []
            for h in head:
                c = h.strip() or "col"
                while c.lower() in {x.lower() for x in cols}:
                    c += "_"
                cols.append(c)
            tn = "e_" + t["name"]
            con.execute(f"CREATE TABLE {_q(tn)} (" + ", ".join(f"{_q(c)} TEXT" for c in cols or ["col"]) + ")")
            if cols:
                ph = ",".join("?" * len(cols))
                width = len(cols)
                con.executemany(f"INSERT INTO {_q(tn)} VALUES ({ph})",
                                ([v if v != "" else None for v in (r + [""] * width)[:width]] for r in rows))
                for c in cols:
                    if c.lower() in INDEX_COLS:
                        con.execute(f"CREATE INDEX {_q('ix_' + tn + '_' + c)} ON {_q(tn)}({_q(c)})")
            n = con.execute(f"SELECT count(*) FROM {_q(tn)}").fetchone()[0]
            counts[t["name"]] = n
            con.execute("INSERT INTO tables VALUES (?,?,?,?)", (t["name"], t["schema"], n, t["rows"]))
        for kind, m in maps.items():
            con.execute(f"CREATE TABLE {_q('map_' + kind)} (estock_id TEXT PRIMARY KEY, program_id TEXT NOT NULL)")
            con.executemany(f"INSERT INTO {_q('map_' + kind)} VALUES (?,?)", sorted(m.items()))
        con.executemany("INSERT INTO meta VALUES (?,?)", [
            ("source_sha", sha), ("instance", ex.instance), ("database", ex.database),
            ("built_at", datetime.now().isoformat(timespec="seconds")), ("format", "1")])
        con.commit()
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("archive integrity check failed")
    finally:
        con.close()
    if not keep:
        tmp.unlink(missing_ok=True)
        return counts
    os.replace(tmp, path)
    os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
    return counts
