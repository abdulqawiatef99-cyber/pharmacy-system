"""Day health at shift close (part 3 item 4, idea 28) and the PC health check (idea 30). Read only.

One list of plain checks, each {key, status ok|warn|bad, title, text}, worst first, so the person closing the
shift sees in one look whether the day is clean: today's backup, the stock check, drafts left open, transfers
without a proof, expenses waiting, returns and voids of the day, errors the program raised today. Money figures
appear only for someone who may see them (`sales.totals`); counts are enough for everyone else.

The PC check: free disk where the data lives, the database file and its write-ahead log, a quick SQLite check,
the clock (never behind the newest record), and Windows 10 64-bit or newer (the embedded Python's minimum).
Nothing here writes, so it can run at any time and from any screen.
"""
from __future__ import annotations

import platform
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from ..core import dbhealth
from ..core.permissions import Context
from ..db import Database

GB = 1024 ** 3
DISK_WARN, DISK_BAD = 5 * GB, 1 * GB
ORDER = {"bad": 0, "warn": 1, "ok": 2}


def _c(key: str, status: str, title: str, text: str, **extra) -> dict:
    return {"key": key, "status": status, "title": title, "text": text, **extra}


def _egp(p: int) -> str:
    return f"{p / 100:,.2f}"


def _bounds(day: date) -> tuple[str, str]:
    from ..sales.service import local_day_bounds
    return local_day_bounds(day)


def day_checks(db: Database, ctx: Context, *, backup: dict | None = None, day: date | None = None) -> list[dict]:
    """The checks of one day (default today). backup: the server's backup health (None when not known)."""
    ctx.require("cash.close")
    from ..sales.service import local_today
    day = day or local_today()
    lo, hi = _bounds(day)
    org = ctx.org_id
    money_ok = ctx.can("sales.totals")
    out: list[dict] = []
    if backup is not None:
        if backup.get("failed"):
            out.append(_c("backup", "bad", "النسخة الاحتياطية", "آخر نسخة احتياطية فشلت — بلّغ المالك قبل ما تقفل الجهاز"))
        elif backup.get("late") or not backup.get("last_good_at"):
            out.append(_c("backup", "warn", "النسخة الاحتياطية", "مفيش نسخة احتياطية جديدة من بدري — خليه يعمل نسخة من «البرنامج والنسخ الاحتياطي»"))
        else:
            out.append(_c("backup", "ok", "النسخة الاحتياطية", f"آخر نسخة سليمة {backup['last_good_at'][:16].replace('T', ' ')}"))
    from ..inventory import integrity
    st = integrity.last_state(db, org)
    if st and st.get("alert"):
        out.append(_c("stock", "bad", "مطابقة المخزون", "فيه فرق في أرصدة المخزون — افتح «صحة المخزون» وبلّغ المالك"))
    else:
        out.append(_c("stock", "ok", "مطابقة المخزون", "أرصدة المخزون مطابقة" if st else "لسه ما اتعملش فحص للمخزون النهارده"))
    drafts = db.one("SELECT count(*) n FROM sales s WHERE s.org_id=? AND s.status='draft'"
                    " AND EXISTS (SELECT 1 FROM sale_lines l WHERE l.sale_id=s.id)", (org,))["n"]
    out.append(_c("drafts", "warn" if drafts else "ok", "فواتير مفتوحة",
                  f"{drafts} فاتورة فيها أصناف ولسه ما اتقفلتش — اتممها أو احذفها" if drafts else "مفيش فواتير مفتوحة"))
    nproof = db.one("SELECT count(*) n FROM sales s WHERE s.org_id=? AND s.status='completed' AND s.paid_transfer>0"
                    " AND s.completed_at>=? AND s.completed_at<? AND NOT EXISTS (SELECT 1 FROM sale_attachments a"
                    " WHERE a.sale_id=s.id)", (org, lo, hi))["n"]
    out.append(_c("transfers", "warn" if nproof else "ok", "صور التحويلات",
                  f"{nproof} فاتورة تحويل النهارده من غير صورة التحويل" if nproof else "كل تحويلات النهارده ليها صورة"))
    exp = db.one("SELECT count(*) n, coalesce(sum(amount),0) t FROM expenses WHERE org_id=? AND status='pending'", (org,))
    out.append(_c("expenses", "warn" if exp["n"] else "ok", "مصروفات مستنية الموافقة",
                  (f"{exp['n']} مصروف مستني موافقة المالك أو المدير" + (f" ({_egp(exp['t'])} ج)" if money_ok else ""))
                  if exp["n"] else "مفيش مصروفات مستنية"))
    ret = db.one("SELECT count(*) n, coalesce(sum(total),0) t FROM sale_returns WHERE org_id=? AND created_at>=?"
                 " AND created_at<?", (org, lo, hi))
    voids = db.one("SELECT count(*) n FROM audit_log WHERE org_id=? AND action='sale.void' AND at>=? AND at<?",
                   (org, lo, hi))["n"]
    text = f"{ret['n']} مرتجع" + (f" بـ {_egp(ret['t'])} ج" if money_ok and ret["n"] else "") + f" و{voids} فاتورة ملغاة النهارده"
    out.append(_c("returns", "warn" if voids else "ok", "المرتجع والإلغاء", text))
    errs = db.one("SELECT count(*) n FROM notifications WHERE org_id=? AND severity='error' AND created_at>=?"
                  " AND created_at<?", (org, lo, hi))["n"]
    out.append(_c("alerts", "warn" if errs else "ok", "تنبيهات مهمة",
                  f"{errs} تنبيه مهم النهارده — شوفهم في «الإشعارات»" if errs else "مفيش تنبيهات مهمة النهارده"))
    return sorted(out, key=lambda c: ORDER[c["status"]])


def _windows_ok(system: str, release: str, machine: str, bits64: bool) -> tuple[bool, str]:
    if system != "Windows":
        return True, f"{system} {release}"
    try:
        major = int(release.split(".")[0])
    except ValueError:
        major = 0
    ok = major >= 10 and bits64
    return ok, f"Windows {release} {'64-bit' if bits64 else '32-bit'}"


def pc_checks(db: Database, data_dir: Path, *, now: datetime | None = None, disk=shutil.disk_usage,
              system: tuple[str, str, str] | None = None) -> list[dict]:
    """The PC the program runs on. disk / system are injectable for tests."""
    out: list[dict] = []
    try:
        free = disk(str(data_dir)).free
        st = "bad" if free < DISK_BAD else "warn" if free < DISK_WARN else "ok"
        out.append(_c("disk", st, "مساحة الهارد", f"فاضي {free / GB:.1f} جيجا مكان البيانات"
                      + (" — قليل، فضّي مساحة" if st != "ok" else ""), free_bytes=free))
    except OSError:
        out.append(_c("disk", "warn", "مساحة الهارد", "مش قادر أعرف المساحة الفاضية"))
    rep = dbhealth.report(db)
    wal_big = rep["wal_bytes"] > dbhealth.WAL_LIMIT_BYTES
    out.append(_c("db", "warn" if wal_big else "ok", "ملف البيانات",
                  f"حجمه {rep['db_bytes'] / 1024 ** 2:.1f} ميجا" + (" — ملف السجل كبر، البرنامج بيصغره لوحده بالليل" if wal_big else ""),
                  db_bytes=rep["db_bytes"], wal_bytes=rep["wal_bytes"]))
    qc = db.conn.execute("PRAGMA quick_check(1)").fetchone()[0]
    out.append(_c("sqlite", "ok" if qc == "ok" else "bad", "سلامة قاعدة البيانات",
                  "سليمة" if qc == "ok" else "فيها مشكلة — متقفلش البرنامج وبلّغ المالك فورًا"))
    now = now or datetime.now(timezone.utc)
    last = db.one("SELECT max(at) a FROM audit_log")["a"]
    behind = False
    if last:
        try:
            behind = datetime.fromisoformat(last.replace("Z", "+00:00")) > now
        except ValueError:
            behind = False
    out.append(_c("clock", "bad" if behind else "ok", "ساعة الجهاز",
                  "ساعة الجهاز متأخرة عن آخر عملية متسجلة — ظبط التاريخ والساعة" if behind else "مظبوطة"))
    sysname, release, machine = system or (platform.system(), platform.release(), platform.machine())
    ok, label = _windows_ok(sysname, release, machine, sys.maxsize > 2 ** 32 if system is None else machine.endswith("64"))
    out.append(_c("windows", "ok" if ok else "bad", "نسخة الويندوز",
                  label + ("" if ok else " — البرنامج محتاج Windows 10 64-bit أو أحدث")))
    return sorted(out, key=lambda c: ORDER[c["status"]])
