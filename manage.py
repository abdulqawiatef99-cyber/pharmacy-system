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
import os
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
    from app.core import backup
    db = _db(s)
    out = backup.create(db, s.upload_dir, Path(a.dest))
    info = backup.verify_archive(out)
    if info["problems"]:
        print("BACKUP FAILED VERIFICATION:", info["problems"])
        sys.exit(1)
    print(f"backup written and verified: {out} ({out.stat().st_size} bytes, {info['files']} files)")


def cmd_verify(a, s):
    from app.core import backup
    if a.archive:
        info = backup.verify_archive(Path(a.archive))
        problems = info["problems"]
    else:
        problems = backup.integrity_problems(_db(s))
    for p in problems:
        print("PROBLEM:", p)
    print("OK" if not problems else f"{len(problems)} problem(s)")
    sys.exit(1 if problems else 0)


def cmd_restore(a, s):
    from app.core import backup
    if not a.yes:
        print("أوقف البرنامج أولًا (أغلق نافذة serve)، ثم أعد الأمر مع --yes")
        sys.exit(2)
    r = backup.restore(Path(a.archive), Path(s.db_path), s.upload_dir, Path(a.pre_dir))
    print("restored and verified. previous data saved in:", r["pre_restore_backup"])


def cmd_selftest(a, s):
    """Run on the pharmacy PC (Windows) and send the output to the developer.
    Uses a throw-away database; never touches the real data."""
    import platform
    import tempfile
    import unittest
    from app.printing import escpos
    from app.version import __version__
    report = {"version": __version__, "python": sys.version.split()[0], "os": platform.platform()}
    ok = True
    def check(name, fn):
        nonlocal ok
        try:
            report[name] = fn() or "OK"
        except Exception as e:  # report every failure, keep going
            ok = False
            report[name] = f"FAIL: {type(e).__name__}: {e}"
    check("python>=3.11", lambda: None if sys.version_info >= (3, 11) else (_ for _ in ()).throw(RuntimeError("old python")))
    def libs():
        import openpyxl, pdfplumber, PIL, starlette, uvicorn, multipart  # noqa: F401
    check("libraries", libs)
    check("arabic_shaping(raqm)", lambda: None if escpos.shaping_available() else (_ for _ in ()).throw(RuntimeError("raqm missing: Arabic receipts cannot be printed as image")))
    check("receipt_font", escpos.find_font)
    def data_dir():
        s.data_dir.mkdir(parents=True, exist_ok=True)
        t = s.data_dir / ".write_test"
        t.write_text("x")
        t.unlink()
    check("data_dir_writable", data_dir)
    def printer():
        target = os.environ.get("PHARM_PRINTER", "none")
        if target == "none":
            return "not configured (browser printing will be used)"
        if a.print_test:
            img = escpos.render_receipt([("title", "اختبار الطابعة", ""), ("row", "سطر عربي", "12.50"),
                                         ("small", "إذا ظهر النص مقروءًا فالطباعة سليمة", "")])
            escpos.send(target, escpos.receipt_job(img, open_drawer=a.drawer_test))
            return f"sent test receipt to {target}" + (" + drawer pulse" if a.drawer_test else "")
        return f"configured: {target} (add --print-test to print)"
    check("printer", printer)
    def tests():
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["PHARM_DATA_DIR"] = tmp
            suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent / "tests"),
                                                        top_level_dir=str(Path(__file__).parent))
            r = unittest.TextTestRunner(stream=open(os.devnull, "w"), verbosity=0).run(suite)
            if not r.wasSuccessful():
                raise RuntimeError(f"{len(r.failures)} failures, {len(r.errors)} errors out of {r.testsRun}: "
                                   + "; ".join(str(t[0]) for t in (r.failures + r.errors)[:5]))
            return f"{r.testsRun} tests passed"
    if not a.quick:
        check("automated_tests", tests)
    for k, v in report.items():
        print(f"{k:24} {v}")
    print("SELFTEST", "PASSED" if ok else "FAILED")
    sys.exit(0 if ok else 1)


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
    v = sub.add_parser("verify")
    v.add_argument("archive", nargs="?", help="verify a backup .zip instead of the live database")
    st = sub.add_parser("selftest")
    st.add_argument("--quick", action="store_true", help="skip the automated test suite")
    st.add_argument("--print-test", action="store_true", help="print a test receipt on PHARM_PRINTER")
    st.add_argument("--drawer-test", action="store_true", help="also pulse the cash drawer")
    rs = sub.add_parser("restore")
    rs.add_argument("archive")
    rs.add_argument("--yes", action="store_true")
    rs.add_argument("--pre-dir", default="backups/pre-restore")
    f = sub.add_parser("feature")
    f.add_argument("org_id")
    f.add_argument("feature")
    f.add_argument("--monthly", type=int)
    f.add_argument("--disable", action="store_true")
    a = p.parse_args(argv)
    s = config.load()
    {"init": cmd_init, "serve": cmd_serve, "backup": cmd_backup, "verify": cmd_verify,
     "feature": cmd_feature, "restore": cmd_restore, "selftest": cmd_selftest}[a.cmd](a, s)


if __name__ == "__main__":
    main()
