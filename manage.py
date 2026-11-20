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
  python manage.py reset-owner-password [--username owner]   (forgotten owner password; on this PC only)
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


def _use_packages() -> None:
    """Smart update (docs/SMART_UPDATE.md): a release that brought its own package set (verified when it was
    staged) puts it before the installed one. No pip on the pharmacy PC."""
    import site
    here = Path(__file__).resolve().parent
    folder = os.environ.get("PHARM_DEPS_DIR")
    if not folder:
        try:
            import release_bootstrap as rb
            home = rb.code_home(rb.data_dir())
            active = rb.read_state(home).get("active") or {}
            if active.get("id") == here.name and active.get("deps"):
                folder = str(home / "deps" / active["deps"])
        except Exception:                       # no state / old bootstrap: the installed packages
            return
    if not folder:
        return
    if not (Path(folder) / ".deps-files.json").is_file():
        sys.exit(f"package set missing: {folder}")
    before = list(sys.path)
    site.addsitedir(folder)
    added = [x for x in sys.path if x not in before]
    for x in added:
        sys.path.remove(x)
    sys.path[1:1] = added


_use_packages()

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
    existed = s.db_path.exists()
    db = Database(s.db_path)
    from app.db import MigrationError
    try:
        if existed:
            _backup_before_migration(db, s)
        applied = db.migrate()
    except MigrationError as e:
        db.close()
        sys.exit("البرنامج لم يفتح البيانات: " + ("البيانات دي مكتوبة بنسخة أحدث من البرنامج؛ ثبّت آخر مثبّت. "
                                                   if "newer version" in str(e) else "")
                 + f"البيانات كما هي لم تتغير. أرسل هذه الرسالة للمطوّر.\n({e})")
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


def _backup_before_migration(db: Database, s: config.Settings) -> None:
    """A new installer brings new migrations and the first start applies them to the live data: take a
    verified backup first, and stop without migrating when it cannot be made. Only when the database
    holds a pharmacy and something is pending, so new, empty and up-to-date databases start as fast as before."""
    pending = db.pending_migrations()
    if not pending or db.unknown_migrations():      # data from a newer program: migrate() refuses it unchanged
        return
    if not db.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='organizations'") or \
            not db.one("SELECT count(*) n FROM organizations")["n"]:
        return
    if os.environ.get("PHARM_MIGRATE_WITHOUT_BACKUP") == "1":      # developer's decision, after a copy by hand
        print("WARNING: migrating WITHOUT a backup (PHARM_MIGRATE_WITHOUT_BACKUP=1)", file=sys.stderr)
        return
    from app.core import backup
    try:
        out = backup.create(db, s.upload_dir, s.backup_path, tag="pre-migrate")
        hard, soft = backup.split_problems(backup.quick_check(out))
        if hard:
            out.unlink(missing_ok=True)
            raise RuntimeError("; ".join(hard))
    except Exception as e:  # noqa: BLE001 - a live database that fails its own check must still get its upgrade:
        # keep a plain copy of the file instead (SQLite's online copy); only when that fails too does the start stop
        from datetime import datetime
        try:
            s.backup_path.mkdir(parents=True, exist_ok=True)
            stamp, n = datetime.now().strftime('%Y%m%d-%H%M%S'), 1
            raw = s.backup_path / f"pre-migrate-UNVERIFIED-{stamp}.db"
            while raw.exists():                      # a retry in the same second never overwrites the first copy
                n += 1
                raw = s.backup_path / f"pre-migrate-UNVERIFIED-{stamp}-{n}.db"
            db.backup_to(raw)
        except Exception as e2:  # noqa: BLE001
            db.close()
            sys.exit("لم يتم تحديث قاعدة البيانات: تعذّر عمل نسخة احتياطية قبل التحديث، فالبرنامج توقف والبيانات كما هي"
                     " لم تتغير. أرسل هذه الرسالة للمطوّر.\n"
                     f"(pre-migration backup failed, nothing was migrated; pending: {', '.join(pending)})\n"
                     f"السبب / reason: {e}; plain copy: {e2}")
        print(f"WARNING: the verified backup before the database update failed ({e}); a plain copy was kept: {raw}",
              file=sys.stderr)
        return
    print(f"backup before database update: {out}")
    if soft:
        print(f"WARNING: {len(soft)} attachment file(s) are missing or changed; the backup holds the data without them:",
              "; ".join(soft), file=sys.stderr)


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
    from app.core.accesslog import uvicorn_log_config
    uvicorn.run(create_app(_db(s, create=True), s, auto_backup=True), host=a.host, port=a.port, proxy_headers=False,
                server_header=False, log_config=uvicorn_log_config())


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
    try:
        r = backup.restore(Path(a.archive), Path(s.db_path), s.upload_dir,
                           _backups(a.pre_dir, s, "pre-restore"),
                           allow_missing_attachments=a.allow_missing_attachments)
    except RuntimeError as e:
        print("RESTORE REFUSED:", e)
        if not a.allow_missing_attachments and "attachment" in str(e):
            print("If only invoice/transfer files are missing and you accept losing them, repeat with"
                  " --allow-missing-attachments: the database is restored and the missing files are listed.")
        sys.exit(1)
    for m in r["missing_attachments"]:
        print(f"MISSING FILE (not restored): {m['filename']} — {m['problem']}")
    if r["missing_attachments"]:
        print(f"WARNING: {len(r['missing_attachments'])} attachment file(s) were not in the backup; the data itself is restored.")
    if r["pre_restore_kind"] == "raw_copy":
        print("WARNING: the previous database could not be backed up normally (" + str(r["pre_restore_error"])[:300] + ").")
        print("A RAW copy of its file(s) was kept instead (not a verified backup) in:", r["pre_restore_backup"])
        print("restored and verified.")
    else:
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
    import subprocess
    import tempfile
    import zipfile
    from app.core import backup, updater
    import release_bootstrap as releases
    root = Path(__file__).resolve().parent
    home = releases.code_home(s.data_dir)
    try:
        if a.candidate_check or a.self_check or a.prepare or a.apply_prepared or a.select_prepared:
            return _smart_update(a, s, root)
        if a.rollback:
            if a.from_zip or a.check:
                raise updater.UpdateError("--rollback cannot be combined with --from-zip or --check")
            state = releases.read_state(home)
            journal = releases.read_journal(home)
            if (state.get("active") or {}).get("needs_db") and journal.get("release") == state["active"]["id"]:
                raise updater.UpdateError(
                    "النسخة دي غيّرت قاعدة البيانات؛ الرجوع للكود القديم وحده مش آمن. استرجع النسخة الاحتياطية "
                    f"اللي اتعملت قبل التحديث: {journal.get('backup', '')}")
            releases.rollback(home)
            print("Previous code selected. Restart the program; no database restore was performed.")
            return
        state = releases.read_state(home)
        expected_active = (state.get("active") or {}).get("id")
        if a.from_zip:
            data, sha, source = Path(a.from_zip).read_bytes(), "local", str(a.from_zip)
        else:
            if a.ref is None:   # #107: no --ref follows the install's own channel (a develop test PC stays on develop)
                a.ref = updater.channel(updater.installed(s.data_dir)) or "main"
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
        staged = updater.apply_zip(data, root, code_home=home)
        candidate = Path(staged["release"])
        releases.check_compatible(releases.current_root(root, home), candidate)
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
        # #107: remember the channel, so the next check follows the same branch (a zip keeps the current one)
        ref = updater.installed(s.data_dir).get("ref") if a.from_zip else a.ref
        releases.activate(root, home, staged["id"],
                          {"sha": sha, "source": source, "ref": ref, "at": datetime.now().isoformat()},
                          expected_active=expected_active)
        print(f"Prepared {staged['files']} files; restart to check the selected release.")
        print("Previous code is retained. No pip install or database migration was run.")
    except (updater.UpdateError, releases.ReleaseError, OSError, zipfile.BadZipFile,
            subprocess.TimeoutExpired) as exc:
        sys.exit(f"التحديث فشل: {exc}")


def _smart_update(a, s, root: Path):
    """Stage-1 steps of the smart update (docs/SMART_UPDATE.md); every failure keeps code and data as they are."""
    import json
    from app.core import smart_update as su, update_sign, updater
    if a.candidate_check:                       # runs inside the NEW code, on a COPY of the database
        report = su.candidate_check(s, _db)
        print("CANDIDATE-CHECK " + json.dumps(report, ensure_ascii=False))
        sys.exit(0 if report["ok"] else 1)
    if a.self_check:
        update_sign.self_check(root)
        su.deps_id(b"x", su.python_tag(), su.platform_tag())
        print("SELF-CHECK OK")
        return
    legacy = Path(releases_legacy_root(s))
    backups = _backups(a.backup_dir, s)
    if a.prepare:
        if a.from_bundle:
            source = su.FolderSource(Path(a.from_bundle))
        else:
            chan = updater.channel(updater.installed(s.data_dir)) or "main"
            source = su.GitHubSource(a.repo, chan, updater.read_token())
        out = su.prepare(s, source, legacy=legacy)
        print("SMART-PREPARE " + json.dumps(out, ensure_ascii=False))
        return
    if a.select_prepared:
        out = su.select_code_only(s, legacy, backups)
        print(f"Selected {out['sha'][:7]}; restart to check it.")
        return
    out = su.apply_prepared(s, legacy, a.apply_prepared, backups)
    print("SMART-APPLY " + json.dumps({k: out.get(k) for k in ("release", "sha", "migrated", "backup")},
                                      ensure_ascii=False))


def releases_legacy_root(s) -> str:
    """The installed program folder (stage 0's root), as recorded in the release state; else this folder."""
    import release_bootstrap as releases
    state = releases.read_state(releases.code_home(s.data_dir))
    return state.get("legacy_root") or str(Path(__file__).resolve().parent)


def cmd_reset_owner_password(a, s):
    """#151: the owner forgot the password. Only someone at this PC, with the data folder, can set a new one:
    there is no network route and no default password. The new password follows the usual policy, every
    session of that owner is signed out (failed logins and lock-out cleared), and the audit log records it."""
    db = _db(s)
    from app.core import audit, security
    from app.core.permissions import Context
    rows = db.all("SELECT u.id, u.username, u.full_name, m.org_id FROM memberships m JOIN roles r ON r.id=m.role_id"
                  " JOIN users u ON u.id=m.user_id WHERE r.code='owner'" + (" AND u.username=?" if a.username else "")
                  + " ORDER BY u.username", ((a.username,) if a.username else ()))
    if not rows:
        sys.exit("مفيش حساب مالك بالاسم ده. (no owner account found)" if a.username else
                 "مفيش حساب مالك في قاعدة البيانات دي. (no owner account found)")
    if len({r["id"] for r in rows}) > 1:
        sys.exit("فيه أكتر من مالك: اكتب الاسم بـ --username: " + "، ".join(sorted({r["username"] for r in rows})))
    owner = rows[0]
    print(f"تغيير كلمة سر المالك: {owner['username']} ({owner['full_name'] or ''})")
    pw = getpass.getpass("كلمة السر الجديدة (10 حروف على الأقل): ")
    if getpass.getpass("اكتبها تاني للتأكيد: ") != pw:
        sys.exit("الكلمتين مش زي بعض؛ متغيّرش حاجة. (passwords differ; nothing changed)")
    problems = security.check_password_policy(pw)
    if problems:
        sys.exit("؛ ".join(problems) + " (nothing changed)")
    now = accounts.iso(accounts.utcnow())
    with db.transaction():
        db.execute("UPDATE users SET password_hash=?, password_changed_at=?, failed_logins=0, locked_until=NULL"
                   " WHERE id=?", (security.hash_password(pw), now, owner["id"]))
        db.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (now, owner["id"]))
        for org_id in sorted({r["org_id"] for r in rows}):
            audit.record(db, Context(user_id="system", org_id=org_id, branch_id=None), "user.password_reset",
                         "user", owner["id"], after={"via": "local_cli", "reason": "forgotten owner password"})
    print("تم. ادخل البرنامج بكلمة السر الجديدة. (done; all sessions of this owner were signed out)")


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
    rs.add_argument("--allow-missing-attachments", action="store_true",
                    help="restore the database even when invoice/transfer files are missing from the backup")
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
    up.add_argument("--ref", default=None, help="branch to update from; default: the install's channel (main or develop)")
    up.add_argument("--force", action="store_true")
    up.add_argument("--backup-dir", default=None)
    up.add_argument("--prepare", action="store_true", help="smart update: download, verify and rehearse (no change)")
    up.add_argument("--from-bundle", help="smart update from a folder (flash drive): same signature checks")
    up.add_argument("--select-prepared", action="store_true", help="smart update without database change")
    up.add_argument("--apply-prepared", metavar="RELEASE_ID", help="(run by the launcher, program stopped)")
    up.add_argument("--candidate-check", metavar="DB_COPY", help="(inside a candidate) migrate and check a copy")
    up.add_argument("--self-check", action="store_true", help="(inside a candidate) the updater's own check")
    rp = sub.add_parser("reset-owner-password", help="forgotten owner password: set a new one on this PC (#151)")
    rp.add_argument("--username", help="needed only when there is more than one owner")
    a = p.parse_args(argv)
    s = config.load()
    {"init": cmd_init, "serve": cmd_serve, "backup": cmd_backup, "verify": cmd_verify,
     "preflight": cmd_preflight,
     "feature": cmd_feature, "restore": cmd_restore, "selftest": cmd_selftest,
     "import-estock": cmd_import_estock, "update": cmd_update,
     "reset-owner-password": cmd_reset_owner_password}[a.cmd](a, s)


if __name__ == "__main__":
    main()
