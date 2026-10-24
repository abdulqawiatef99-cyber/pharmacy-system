#!/usr/bin/env python3
"""Command line for running and administering a pharmacy node.

  python manage.py init --pharmacy "صيدلية ..." --username owner     (asks for password)
  python manage.py serve [--host 127.0.0.1] [--port 8000]
  python manage.py backup [--dest backups/]
  python manage.py verify                      (ledger vs balances consistency)
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
    accounts.sync_system_roles(db)
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
        out = backup.create(db, s.upload_dir, Path(a.backup_dir))
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
    """Update the program code from GitHub (or a local zip), keeping data/ untouched."""
    import subprocess
    from app.core import backup, updater
    root = Path(__file__).resolve().parent
    try:
        if a.from_zip:
            data, sha, source = Path(a.from_zip).read_bytes(), "local", str(a.from_zip)
        else:
            token = updater.read_token()
            sha = updater.latest_sha(a.repo, a.ref, token)
            cur = updater.installed(s.data_dir).get("sha")
            if cur == sha and not a.force:
                print("البرنامج محدّث بالفعل:", sha[:7])
                return
            print(f"فيه تحديث: {(cur or 'غير معروف')[:7]} -> {sha[:7]}")
            if a.check:
                return
            data, source = updater.download(a.repo, sha, token), f"github:{a.repo}@{sha}"
        if s.db_path.exists():                 # 1) verified data backup
            db = _db(s)
            out = backup.create(db, s.upload_dir, Path(a.backup_dir))
            if backup.verify_archive(out)["problems"]:
                sys.exit("النسخة الاحتياطية فشلت؛ التحديث اتلغى ومفيش حاجة اتغيرت")
            db.close()
            print("نسخة احتياطية للبيانات:", out)
        req = root / "requirements.txt"
        req_before = req.read_bytes() if req.exists() else b""
        snap = updater.snapshot(root, Path(a.backup_dir))      # 2) code rollback point
        print("نسخة من البرنامج الحالي (للرجوع):", snap)
        res = updater.apply_zip(data, root)                       # 3) new code
        updater.record(s.data_dir, sha, source)
        print(f"اتحدث {res['files']} ملف")
    except updater.UpdateError as e:
        sys.exit(f"التحديث فشل: {e}")
    # 4) packages + migrations + check, in a fresh process running the NEW code
    steps = [[sys.executable, str(root / "manage.py"), "verify"],
             [sys.executable, str(root / "manage.py"), "selftest", "--quick"]]
    if req.read_bytes() != req_before:          # packages only when the list changed
        import importlib.util
        if importlib.util.find_spec("pip") is None:   # installed build (embedded Python, no pip)
            print("تنبيه: التحديث ده محتاج مكتبات جديدة. نزّل ملف التثبيت الجديد وشغّله (بياناتك مش هتتلمس).")
        else:
            steps.insert(0, [sys.executable, "-m", "pip", "install", "-q", "-r", str(req)])
    for cmd in steps:
        if subprocess.run(cmd, cwd=root).returncode != 0:
            sys.exit("خطوة بعد التحديث فشلت. للرجوع للنسخة القديمة:\n"
                     f"  python manage.py update --from-zip \"{snap}\"")
    print("التحديث خلص بنجاح. شغّل البرنامج من جديد.")


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
    ie.add_argument("--backup-dir", default="backups")
    up = sub.add_parser("update", help="update the program from GitHub; data is kept")
    up.add_argument("--check", action="store_true", help="just say if an update exists")
    up.add_argument("--from-zip", help="install from a local zip (offline / rollback)")
    up.add_argument("--repo", default=os.environ.get("PHARM_UPDATE_REPO", "abdulqawiatef99-cyber/pharmacy-system"))
    up.add_argument("--ref", default="main")
    up.add_argument("--force", action="store_true")
    up.add_argument("--backup-dir", default="backups")
    a = p.parse_args(argv)
    s = config.load()
    {"init": cmd_init, "serve": cmd_serve, "backup": cmd_backup, "verify": cmd_verify,
     "feature": cmd_feature, "restore": cmd_restore, "selftest": cmd_selftest,
     "import-estock": cmd_import_estock, "update": cmd_update}[a.cmd](a, s)


if __name__ == "__main__":
    main()
