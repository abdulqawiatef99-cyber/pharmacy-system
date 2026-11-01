#!/usr/bin/env python3
"""Command line for running and administering a pharmacy node.

  python manage.py init --pharmacy "صيدلية ..." --username owner     (asks for password)
  python manage.py serve [--host 127.0.0.1] [--port 8000]
  python manage.py backup [--dest DIR]        (default: backups/ next to the data folder)
  python manage.py verify                      (ledger vs balances consistency)
  (maintenance commands never create a database; only init and serve do — #80)
  python manage.py preflight backup.zip        (read-only: can this backup move to another PC?)
  python manage.py import-estock --catalog A.xlsx --stock B.xlsx --packs C.xlsx --username owner [--apply]
  python manage.py update [--check] [--from-zip file.zip]     (keeps data/)
  python manage.py feature ORG_ID FEATURE [--monthly N] [--disable]   (platform operator)
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import datetime
from pathlib import Path

# Embedded Windows Python ignores PYTHONPATH; choose code before importing app.
sys.path.insert(0, str(Path(__file__).resolve().parent))
if __name__ == "__main__":
    from release_bootstrap import run_selected
    run_selected(Path(__file__).resolve().parent)

from app import config
from app.core import accounts
from app.db import Database
from app.inventory import service as inventory


def _db(s: config.Settings, create: bool = False) -> Database:
    """Open (and migrate) the pharmacy database. Only init and serve may create a new one (#80): a maintenance
    command run from the wrong folder or without PHARM_DATA_DIR must stop, not back up an empty database."""
    if not create and not s.db_path.exists():
        sys.exit(f"لا توجد قاعدة بيانات في: {s.db_path}\n"
                 "حدد مكان البيانات: set PHARM_DATA_DIR=%LOCALAPPDATA%\\PharmacyPlatform\\data ثم أعد الأمر. "
                 "(no database here; nothing was created)")
    s.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(s.db_path)
    applied = db.migrate()
    if applied:
        print("migrations applied:", ", ".join(applied))
    accounts.sync_system_roles(db)
    from app.catalog.search import backfill_search_keys
    backfill_search_keys(db)
    from app.catalog.card import ensure_default_categories
    ensure_default_categories(db)
    from app.sales.channels import ensure_defaults as _ch
    from app.finance.expenses import ensure_defaults as _ex
    _ch(db)
    _ex(db)
    return db


def _backups(chosen: str | None, s: config.Settings, sub: str = "") -> Path:
    """Where a command writes its backup: the folder given, else the backups folder next to the data
    (installed: %LOCALAPPDATA%\\PharmacyPlatform\\backups), never the current folder (#80)."""
    return Path(chosen) if chosen else (s.backup_path / sub if sub else s.backup_path)


def cmd_init(a, s):
    db = _db(s, create=True)
    pw = a.password or getpass.getpass("كلمة مرور المالك (10 حروف على الأقل): ")
    org = accounts.create_organization(db, name=a.pharmacy, owner_username=a.username,
                                       owner_full_name=a.full_name or a.username,
                                       owner_password=pw)
    print("تم إنشاء الصيدلية:", org["org_id"])


def cmd_serve(a, s):
    import uvicorn
    from app.api.app import create_app
    if a.host not in ("127.0.0.1", "localhost") and not s.cookie_secure:
        print("تنبيه: البرنامج مفتوح على شبكة الصيدلية (واي فاي) من غير HTTPS — استخدمه على شبكة الصيدلية المقفولة بباسورد بس.",
              file=sys.stderr)
    os.environ["PHARM_HOST"] = a.host
    uvicorn.run(create_app(_db(s, create=True), s, auto_backup=True), host=a.host, port=a.port, proxy_headers=False,
                server_header=False)


def cmd_backup(a, s):
    from app.core import backup
    db = _db(s)
    if not db.one("SELECT count(*) n FROM organizations")["n"]:
        sys.exit(f"قاعدة البيانات في {s.db_path} فاضية (لا توجد صيدلية): لم تُعمل نسخة. تأكد من PHARM_DATA_DIR.")
    out = backup.create(db, s.upload_dir, _backups(a.dest, s))
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


def cmd_preflight(a, s):
    """Read-only: reads the backup ZIP only, never the live data (R5, before moving data)."""
    import json
    from app.core import backup_preflight
    r = backup_preflight.check(Path(a.archive))
    print(json.dumps(r, ensure_ascii=False, indent=1) if a.json else "\n".join(backup_preflight.render(r)))
    sys.exit(0 if r["can_restore"] else 1)


def cmd_restore(a, s):
    from app.core import backup
    if not a.yes:
        print("أوقف البرنامج أولًا (أغلق نافذة serve)، ثم أعد الأمر مع --yes")
        sys.exit(2)
    r = backup.restore(Path(a.archive), Path(s.db_path), s.upload_dir,
                       _backups(a.pre_dir, s, "pre-restore"))
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
    def shaping():
        if escpos.shaping_available():
            return None
        # without raqm the receipt is printed from the browser (handled, not a failure)
        # — unless direct printing was configured, which then cannot work
        if os.environ.get("PHARM_PRINTER", "none") != "none":
            raise RuntimeError("raqm missing: direct printing of Arabic receipts is not possible")
        return "WARN: raqm missing -> receipts are printed from the browser"
    check("arabic_shaping(raqm)", shaping)
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
        # Windows keeps SQLite files locked a moment after close: never fail the check on cleanup
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            os.environ["PHARM_DATA_DIR"] = tmp
            suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent / "tests"),
                                                        top_level_dir=str(Path(__file__).parent))
            r = unittest.TextTestRunner(stream=open(os.devnull, "w", encoding="utf-8"), verbosity=0).run(suite)
            if not r.wasSuccessful():
                raise RuntimeError(f"{len(r.failures)} failures, {len(r.errors)} errors out of {r.testsRun}: "
                                   + "; ".join(str(t[0]) for t in (r.failures + r.errors)[:5]))
            skipped = f" ({len(r.skipped)} skipped: " + "; ".join(sorted({why for _, why in r.skipped})) + ")" \
                if r.skipped else ""
            return f"{r.testsRun - len(r.skipped)} tests passed{skipped}"
    if not a.quick:
        check("automated_tests", tests)
    for k, v in report.items():
        print(f"{k:24} {v}")
    print("SELFTEST", "PASSED" if ok else "FAILED")
    sys.exit(0 if ok else 1)


def cmd_import_estock(a, s):
    """Dry run on a copy of the database by default; --apply writes, after a verified backup."""
    import json
    import shutil
    import tempfile
    from app.core import backup
    from app.dataimport import estock_job
    db = _db(s)
    pw = a.password or getpass.getpass(f"كلمة مرور {a.username}: ")
    tok = accounts.login(db, a.username, pw)["token"]
    files = {k: Path(getattr(a, k)) for k in ("catalog", "stock")}
    packs = Path(a.packs) if a.packs else None
    for f in [*files.values(), *([packs] if packs else [])]:
        if not f.exists():
            sys.exit(f"الملف مش موجود: {f}")
    tmp = None
    target = db
    if not a.apply:
        tmp = Path(tempfile.mkdtemp(prefix="estock-dry-"))
        db.backup_to(tmp / "copy.db")
        target = Database(tmp / "copy.db")
        print("== تجربة على نسخة من قاعدة البيانات (مفيش أي حاجة اتكتبت في الأصلية) ==")
    else:
        out = backup.create(db, s.upload_dir, _backups(a.backup_dir, s))
        if backup.verify_archive(out)["problems"]:
            sys.exit("النسخة الاحتياطية فشلت؛ مفيش استيراد")
        print("نسخة احتياطية قبل الاستيراد:", out)
    try:
        ctx = accounts.resolve_session(target, tok)
        rep = estock_job.run(target, ctx, catalog_file=files["catalog"], stock_file=files["stock"],
                             packs_file=packs, stores=a.store or ["الصيدلية"],
                             accept_zero_cost=a.accept_zero_cost, max_unit_error=a.max_unit_error)
    finally:
        if tmp:
            target.close()
            shutil.rmtree(tmp, ignore_errors=True)
    rdir = s.data_dir / "reports"
    rdir.mkdir(parents=True, exist_ok=True)
    rpath = rdir / f"estock-import-{datetime.now():%Y%m%d-%H%M%S}{'' if a.apply else '-dry'}.json"
    rpath.write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    st = rep["stock"]
    print(f"الأصناف: {rep['catalog']['created']} جديد، {rep['catalog']['updated']} اتحدث، "
          f"{rep['catalog']['kept_duplicates']} بنفس الاسم اتسابوا منفصلين")
    print(f"عدد الوحدات: {rep['packs']['applied']} صنف")
    print(f"الأرصدة: {st['applied']['stock_rows']} تشغيلة اتنقلت — المطابقة: "
          f"{'OK' if st['reconciliation']['ok'] else 'FAILED'}")
    for k, label in (("rounded", "اتقربت لأقرب وحدة"), ("posted_zero_cost", "دخلت بتكلفة صفر"),
                     ("skipped_unknown_pack", "اتسابت: عدد الوحدات مش معروف"),
                     ("skipped_zero_cost", "اتسابت: تكلفة صفر"), ("skipped_review", "اتسابت: محتاجة مراجعة"),
                     ("errors", "أخطاء")):
        if st.get(k):
            print(f"  {label}: {len(st[k])}")
    print("التقرير الكامل:", rpath)


def cmd_update(a, s):
    """Stage immutable code and atomically select it; never migrate the live DB here."""
    import re
    import subprocess
    import tempfile
    import zipfile
    from app.core import backup, updater
    import release_bootstrap as releases
    root = Path(__file__).resolve().parent
    home = releases.code_home(s.data_dir)
    try:
        if a.rollback:
            if a.from_zip or a.check:
                raise updater.UpdateError("--rollback cannot be combined with --from-zip or --check")
            releases.rollback(home)
            print("Previous code selected. Restart the program; no database restore was performed.")
            return
        if a.sha and not re.fullmatch(r"[0-9a-f]{40}", a.sha):
            raise updater.UpdateError("--sha must be a full commit id")
        state = releases.read_state(home)
        expected_active = (state.get("active") or {}).get("id")
        if a.from_zip:
            data, sha, source = Path(a.from_zip).read_bytes(), "local", str(a.from_zip)
        else:
            token = updater.read_token()
            sha = a.sha or updater.latest_sha(a.repo, a.ref, token)
            cur = updater.installed(s.data_dir).get("sha")
            if cur == sha and not a.force:
                print("البرنامج محدّث بالفعل:", sha[:7])
                return
            print(f"فيه تحديث: {(cur or 'غير معروف')[:7]} -> {sha[:7]}")
            if a.check:
                return
            data, source = updater.download(a.repo, sha, token), f"github:{a.repo}@{sha}"
        staged = updater.apply_zip(data, root, code_home=home)
        candidate = Path(staged["release"])
        current = releases.current_root(root, home)
        try:
            releases.check_compatible(current, candidate)
        except releases.ReleaseError as exc:
            print("يحتاج المثبّت (installer):", exc)
            sys.exit(updater.NEEDS_INSTALLER)
        reasons = updater.installer_reasons(current, candidate)
        if reasons:      # the installed launcher/bootstrap/runtime would not change with a code-only release
            print("يحتاج المثبّت (installer):", ", ".join(reasons[:10]))
            sys.exit(updater.NEEDS_INSTALLER)
        # A candidate check must not open/migrate the real pharmacy database.
        with tempfile.TemporaryDirectory(prefix="pharm-release-check-") as tmp:
            isolated = Path(tmp)
            env = {**os.environ, "PHARM_RELEASE_CANDIDATE": "1", "PHARM_DATA_DIR": str(isolated),
                   "PHARM_DB_PATH": str(isolated / "test.db"), "PHARM_UPLOAD_DIR": str(isolated / "uploads"),
                   "PHARM_BACKUP_DIR": str(isolated / "backups"), "PHARM_PRINTER": "none",
                   "PYTHONUTF8": "1"}
            check = subprocess.run([sys.executable, str(candidate / "manage.py"), "selftest", "--quick"],
                                   cwd=candidate, env=env, timeout=120)
            if check.returncode:
                raise updater.UpdateError("Candidate selftest failed; the selected code is unchanged")
        if s.db_path.exists():
            db = Database(s.db_path)  # read/snapshot only; do NOT call _db or migrate
            try:
                out = backup.create(db, s.upload_dir, _backups(a.backup_dir, s))
                if backup.verify_archive(out)["problems"]:
                    raise updater.UpdateError("Backup verification failed; the selected code is unchanged")
            finally:
                db.close()
            print("نسخة احتياطية للبيانات:", out)
        releases.activate(root, home, staged["id"],
                          {"sha": sha, "source": source, "ref": None if a.from_zip else a.ref,
                           "at": datetime.now().isoformat()},
                          expected_active=expected_active)
        print(f"Prepared {staged['files']} files; restart to check the selected release.")
        print("Previous code is retained. No pip install or database migration was run.")
    except (updater.UpdateError, releases.ReleaseError, OSError, zipfile.BadZipFile,
            subprocess.TimeoutExpired) as exc:
        sys.exit(f"التحديث فشل: {exc}")


def cmd_feature(a, s):
    db = _db(s)
    limits = {"monthly": a.monthly} if a.monthly else {}
    accounts.set_entitlement(db, a.org_id, a.feature, enabled=not a.disable, limits=limits,
                             operator="cli")
    print("ok")


def main(argv=None):
    # Windows consoles default to a legacy code page; Arabic output must never crash a command
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
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
    b.add_argument("--dest", default=None, help="default: the backups folder next to the data folder")
    v = sub.add_parser("verify")
    v.add_argument("archive", nargs="?", help="verify a backup .zip instead of the live database")
    pf = sub.add_parser("preflight", help="read-only report on a backup before moving data to another PC")
    pf.add_argument("archive")
    pf.add_argument("--json", action="store_true")
    st = sub.add_parser("selftest")
    st.add_argument("--quick", action="store_true", help="skip the automated test suite")
    st.add_argument("--print-test", action="store_true", help="print a test receipt on PHARM_PRINTER")
    st.add_argument("--drawer-test", action="store_true", help="also pulse the cash drawer")
    rs = sub.add_parser("restore")
    rs.add_argument("archive")
    rs.add_argument("--yes", action="store_true")
    rs.add_argument("--pre-dir", default=None)
    f = sub.add_parser("feature")
    f.add_argument("org_id")
    f.add_argument("feature")
    f.add_argument("--monthly", type=int)
    f.add_argument("--disable", action="store_true")
    ie = sub.add_parser("import-estock", help="migrate items + opening stock from E-Stock exports")
    ie.add_argument("--catalog", required=True, help="قائمة الأصناف .xlsx")
    ie.add_argument("--stock", required=True, help="كميات الأصناف طبقًا لتواريخ الصلاحية .xlsx")
    ie.add_argument("--packs", help="ملف عدد الوحدات المراجَع (.xlsx أو .json)")
    ie.add_argument("--store", action="append", help="اسم المخزن (يتكرر)؛ الافتراضي: الصيدلية")
    ie.add_argument("--accept-zero-cost", action="store_true", help="تشغيلات بونص/تبرع تدخل بتكلفة صفر")
    ie.add_argument("--max-unit-error", default="0.15")
    ie.add_argument("--username", required=True)
    ie.add_argument("--password", help="for automation only; prefer the prompt")
    ie.add_argument("--apply", action="store_true", help="اكتب فعلًا (الافتراضي تجربة على نسخة)")
    ie.add_argument("--backup-dir", default=None)
    up = sub.add_parser("update", help="update the program from GitHub; data is kept")
    up.add_argument("--check", action="store_true", help="just say if an update exists")
    up.add_argument("--rollback", action="store_true", help="select previous complete code; does not restore data")
    up.add_argument("--from-zip", help="install from a local zip (offline / rollback)")
    up.add_argument("--repo", default=os.environ.get("PHARM_UPDATE_REPO", "abdulqawiatef99-cyber/pharmacy-system"))
    up.add_argument("--ref", default="main", help="channel: main (pharmacy) or develop (test PC)")
    up.add_argument("--sha", help="apply exactly this commit of --ref (automatic updates)")
    up.add_argument("--force", action="store_true")
    up.add_argument("--backup-dir", default=None)
    a = p.parse_args(argv)
    s = config.load()
    {"init": cmd_init, "serve": cmd_serve, "backup": cmd_backup, "verify": cmd_verify,
     "preflight": cmd_preflight,
     "feature": cmd_feature, "restore": cmd_restore, "selftest": cmd_selftest,
     "import-estock": cmd_import_estock, "update": cmd_update}[a.cmd](a, s)


if __name__ == "__main__":
    main()
