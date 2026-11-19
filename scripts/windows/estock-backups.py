"""E-Stock backups on this PC: every .bak file on D:\\ and on the Desktop, newest first. READ ONLY.

Owner's question (19/11/2026): is E-Stock's own hourly backup really running? This lists what is on the disk
and says in one line whether the newest database backup is older than 24 hours.

What it does, and nothing else:
- walks D:\\ and the Desktop folders and reads each .bak file's size, date and first 8 bytes (to tell a
  SQL Server database backup from any other file that happens to be called .bak);
- prints the list and one RESULT line, and shows the same answer in a small Arabic message box.
It never writes, renames or deletes a file, never connects to SQL Server or to E-Stock, and sends nothing
anywhere. Standard library only, so it runs with the program's own Python from the tools folder.

Exit code: 0 = the newest backup is at most 24 hours old, 1 = older, 2 = no .bak file found.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime

MAX_AGE_HOURS = 24
SHOW = 30                                    # rows printed; the count of all files found is always printed
SKIP_DIRS = {"$recycle.bin", "system volume information"}        # deleted files and Windows' own store
# a native SQL Server backup starts with a tape-format block ("TAPE"); a compressed one with "MSSQLBAK"
SQL_MARKS = (b"TAPE", b"MSSQLBAK")


def desktops(env=None) -> list[str]:
    """The Desktop folders a backup may have been saved to: the user's (it may be redirected, e.g. into
    OneDrive) and the shared one. Only folders that exist."""
    env = os.environ if env is None else env
    found = []
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
                found.append(os.path.expandvars(winreg.QueryValueEx(k, "Desktop")[0]))
        except OSError:
            pass
    home = env.get("USERPROFILE") or os.path.expanduser("~")
    found += [os.path.join(home, "Desktop"), os.path.join(home, "OneDrive", "Desktop")]
    if env.get("PUBLIC"):
        found.append(os.path.join(env["PUBLIC"], "Desktop"))
    out, seen = [], set()
    for p in found:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen and os.path.isdir(p):
            seen.add(key)
            out.append(p)
    return out


def default_roots() -> list[str]:
    return ["D:\\"] + desktops()


def kind(path: str) -> str:
    """'sql' = a SQL Server database backup, 'other' = some other file named .bak, 'unread' = could not look."""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return "unread"
    return "sql" if head.startswith(SQL_MARKS) else "other"


def scan(roots, progress=None) -> tuple[list[dict], list[str]]:
    """Every .bak file under the roots, as {path, mtime, size, kind}; and the roots that do not exist.
    A folder that cannot be opened is passed over: the list is what this user can see."""
    files, missing, seen = [], [], set()
    for root in roots:
        if not os.path.isdir(root):
            missing.append(root)
            continue
        if progress:
            progress(root)
        for folder, dirs, names in os.walk(root, onerror=lambda e: None, followlinks=False):
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS]
            for name in names:
                if not name.lower().endswith(".bak"):
                    continue
                path = os.path.join(folder, name)
                key = os.path.normcase(os.path.abspath(path))
                if key in seen:                     # the Desktop may itself be on D:\
                    continue
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                seen.add(key)
                files.append({"path": path, "mtime": st.st_mtime, "size": st.st_size, "kind": kind(path)})
    files.sort(key=lambda f: (-f["mtime"], f["path"]))
    return files, missing


def verdict(files, now: float) -> dict:
    """code 'ok' / 'old' / 'none'. Judged on the SQL Server backups when any was recognised (basis 'sql');
    otherwise on every .bak file found (basis 'any'), and the report says the type is not certain."""
    # a file that could not be opened counts with the database backups: the one SQL Server is writing right now
    # is locked, and it is the newest
    sql = [f for f in files if f["kind"] in ("sql", "unread")]
    basis = sql or files
    if not basis:
        return {"code": "none", "basis": "none", "newest": None, "age_hours": None}
    newest = max(basis, key=lambda f: f["mtime"])
    age = max(0.0, (now - newest["mtime"]) / 3600)
    return {"code": "ok" if age <= MAX_AGE_HOURS else "old", "basis": "sql" if sql else "any",
            "newest": newest, "age_hours": age}


def fmt_size(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{max(1, round(n / 1024))} KB" if n else "0 KB"


def fmt_age(hours: float) -> str:
    if hours < 1:
        return f"{max(0, round(hours * 60))} min"
    if hours < 48:
        return f"{hours:.1f} h"
    return f"{hours / 24:.1f} days"


def fmt_date(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def report(files, missing, roots, now: float) -> list[str]:
    v = verdict(files, now)
    lines = [f"E-Stock backups on this PC (read only)  {fmt_date(now)}",
             "searched: " + ", ".join(r for r in roots if r not in missing)
             + ("" if not missing else "   not found: " + ", ".join(missing)),
             f".bak files found: {len(files)} (SQL Server backups: {sum(f['kind'] == 'sql' for f in files)})", ""]
    if files:
        lines.append(f"{'date':16}  {'age':>10}  {'size':>10}  {'type':6}  file")
        for f in files[:SHOW]:
            age = fmt_age(max(0.0, (now - f["mtime"]) / 3600))
            lines.append(f"{fmt_date(f['mtime']):16}  {age:>10}  {fmt_size(f['size']):>10}  {f['kind']:6}  {f['path']}")
        if len(files) > SHOW:
            lines.append(f"... and {len(files) - SHOW} older file(s) not shown")
        lines.append("")
    if v["code"] == "none":
        lines.append("RESULT: NONE - no .bak file on D:\\ or the Desktop. Either E-Stock's backup never ran,"
                     " or it saves somewhere else.")
        return lines
    what = "SQL Server backup" if v["basis"] == "sql" else ".bak file (none looks like a SQL Server backup: type not certain)"
    where = f"{fmt_date(v['newest']['mtime'])}, {fmt_age(v['age_hours'])} old: {v['newest']['path']}"
    if v["code"] == "ok":
        lines.append(f"RESULT: OK - the newest {what} is {where}")
    else:
        lines.append(f"RESULT: WARNING - the newest {what} is OLDER THAN {MAX_AGE_HOURS} HOURS ({where})."
                     " E-Stock's hourly backup is probably not running.")
    return lines


def arabic_message(v: dict) -> str:
    """The same answer for the owner, in the message box."""
    if v["code"] == "none":
        return ("ما لقيتش أي ملف نسخة احتياطية (.bak) على D:\\ ولا على سطح المكتب.\n\n"
                "النسخ الاحتياطي بتاع إي ستوك غالبًا عمره ما اشتغل، أو بيتحفظ في مكان تاني.")
    when = f"{fmt_date(v['newest']['mtime'])}"
    hours = v["age_hours"]
    age = f"{round(hours * 60)} دقيقة" if hours < 1 else f"{hours:.1f} ساعة" if hours < 48 else f"{hours / 24:.1f} يوم"
    unsure = "" if v["basis"] == "sql" else "\n\n(ملحوظة: نوع الملفات مش مؤكد إنه نسخ قاعدة بيانات.)"
    if v["code"] == "ok":
        return f"أحدث نسخة احتياطية لإي ستوك: {when} (من {age}).\n\nالنسخ الاحتياطي شغال.{unsure}"
    return (f"تحذير: أحدث نسخة احتياطية لإي ستوك عمرها {age} ({when}).\n\n"
            f"ده أكتر من {MAX_AGE_HOURS} ساعة: النسخ الاحتياطي التلقائي بتاع إي ستوك غالبًا مش شغال.\n"
            f"اعمل نسخة احتياطية يدوي من إي ستوك النهارده.{unsure}")


def message_box(text: str, warning: bool) -> None:
    """A plain Windows message box (it draws Arabic properly; the console does not). Nowhere else: nothing."""
    if os.name != "nt":
        return
    try:
        import ctypes
        flags = (0x30 if warning else 0x40) | 0x80000 | 0x100000 | 0x10000 | 0x40000   # icon, right, RTL, front, top
        ctypes.windll.user32.MessageBoxW(None, text, "نسخ إي ستوك الاحتياطية", flags)
    except Exception:                             # the console already has the answer
        pass


def main(argv=None, now: float | None = None, out=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out = out or sys.stdout
    try:
        out.reconfigure(errors="replace")         # a file name the console cannot show never stops the list
    except (AttributeError, ValueError):
        pass
    box = "--no-box" not in argv
    roots = [argv[i + 1] for i, a in enumerate(argv) if a == "--root" and i + 1 < len(argv)] or default_roots()
    now = time.time() if now is None else now
    files, missing = scan(roots, progress=lambda r: print(f"searching {r} ... (a big disk can take a few minutes)",
                                                           file=out, flush=True))
    print("", file=out)
    for line in report(files, missing, roots, now):
        print(line, file=out)
    v = verdict(files, now)
    if box:
        message_box(arabic_message(v), warning=v["code"] != "ok")
    return {"ok": 0, "old": 1, "none": 2}[v["code"]]


if __name__ == "__main__":
    sys.exit(main())
