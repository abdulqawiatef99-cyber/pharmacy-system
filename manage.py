#!/usr/bin/env python3
"""Command line for running and administering a pharmacy node.

  python manage.py init --pharmacy "صيدلية ..." --username owner     (asks for password)
  python manage.py serve [--host 127.0.0.1] [--port 8000]
  python manage.py backup [--dest backups/]
  python manage.py verify                      (ledger vs balances consistency)
  python manage.py feature ORG_ID FEATURE [--monthly N] [--disable]   (platform operator)
"""
from __future__ import annotations

import argparse
import getpass
import sys
from datetime import datetime
from pathlib import Path

from app import config
from app.core import accounts
from app.db import Database
from app.inventory import service as inventory


def _db(s: config.Settings) -> Database:
    s.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(s.db_path)
    applied = db.migrate()
    if applied:
        print("migrations applied:", ", ".join(applied))
    return db


def cmd_init(a, s):
    db = _db(s)
    pw = a.password or getpass.getpass("كلمة مرور المالك (10 حروف على الأقل): ")
    org = accounts.create_organization(db, name=a.pharmacy, owner_username=a.username,
                                       owner_full_name=a.full_name or a.username,
                                       owner_password=pw)
    print("تم إنشاء الصيدلية:", org["org_id"])


def cmd_serve(a, s):
    import uvicorn
    from app.api.app import create_app
    if a.host not in ("127.0.0.1", "localhost") and not s.cookie_secure:
        print("تحذير: التشغيل على الشبكة بدون HTTPS. فعّل PHARM_COOKIE_SECURE=1 خلف HTTPS.",
              file=sys.stderr)
    uvicorn.run(create_app(_db(s), s), host=a.host, port=a.port, proxy_headers=False,
                server_header=False)


def cmd_backup(a, s):
    db = _db(s)
    dest = Path(a.dest)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"pharmacy-{datetime.now():%Y%m%d-%H%M%S}.db"
    db.backup_to(out)
    # prove the backup opens and is consistent
    check = Database(out)
    n = check.one("SELECT count(*) c FROM schema_migrations")["c"]
    check.close()
    print(f"backup written: {out} ({out.stat().st_size} bytes, {n} migrations)")


def verify_db(db: Database) -> list[str]:
    """Integrity checks used by `verify` and `restore`. Returns problems."""
    from app.core import audit
    problems = []
    r = db.one("PRAGMA integrity_check")[0]
    if r != "ok":
        problems.append(f"sqlite integrity_check: {r}")
    fk = db.all("PRAGMA foreign_key_check")
    if fk:
        problems.append(f"foreign key violations: {len(fk)}")
    for org in db.all("SELECT id, name FROM organizations"):
        diffs = inventory.verify_balances(db, org["id"])
        if diffs:
            problems.append(f"{org['name']}: {len(diffs)} stock balance differences")
    chain = audit.verify_chain(db)
    if chain:
        problems.append(f"audit chain broken at seq {chain[0]['seq']} ({chain[0]['problem']})")
    return problems


def cmd_verify(a, s):
    db = _db(s)
    problems = verify_db(db)
    for p in problems:
        print("PROBLEM:", p)
    print("OK" if not problems else f"{len(problems)} problem(s)")
    sys.exit(1 if problems else 0)


def cmd_feature(a, s):
    db = _db(s)
    limits = {"monthly": a.monthly} if a.monthly else {}
    accounts.set_entitlement(db, a.org_id, a.feature, enabled=not a.disable, limits=limits,
                             operator="cli")
    print("ok")


def main(argv=None):
    p = argparse.ArgumentParser(description="Pharmacy platform node")
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init")
    i.add_argument("--pharmacy", required=True)
    i.add_argument("--username", required=True)
    i.add_argument("--full-name")
    i.add_argument("--password", help="for automation only; prefer the prompt")
    sv = sub.add_parser("serve")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    b = sub.add_parser("backup")
    b.add_argument("--dest", default="backups")
    sub.add_parser("verify")
    f = sub.add_parser("feature")
    f.add_argument("org_id")
    f.add_argument("feature")
    f.add_argument("--monthly", type=int)
    f.add_argument("--disable", action="store_true")
    a = p.parse_args(argv)
    s = config.load()
    {"init": cmd_init, "serve": cmd_serve, "backup": cmd_backup, "verify": cmd_verify,
     "feature": cmd_feature}[a.cmd](a, s)


if __name__ == "__main__":
    main()
