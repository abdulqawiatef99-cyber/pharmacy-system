"""E-Stock backups on this PC: every .bak file on D:\\ and on the Desktop, newest first. READ ONLY.

Owner's question (19/11/2026): is E-Stock's own hourly backup really running? This lists what is on the disk
and says in one line whether the newest database backup is older than 24 hours.

What it does, and nothing else:
- walks D:\\ and the Desktop folders and reads each .bak file's size, date and first 8 bytes (to tell a
  SQL Server database backup from any other file that happens to be called .bak);
- prints the list and one RESULT line, and shows the same answer in a small Arabic message box.
It never writes, renames or deletes a file, never connects to SQL Server or to E-Stock, and sends nothing
anywhere. Standard library only, so it runs with the program's own Python from the tools folder.

Staying on this PC (Codex review of #369):
- a place on the network (a Desktop redirected to a server share, a mapped drive) is not searched at all, not
  even asked whether it exists: that question already goes over the network;
- a file that lives in the cloud (OneDrive "online only") is listed from its name, size and date but never
  opened: opening it would download it and write it to this disk;
- a link is never followed, file or folder (a shortcut-like link on the Desktop can point at a server): a
  linked .bak is listed as a link, with the link's own date, and a linked folder is not entered.

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
# Windows file attributes of a file whose content is not on this disk: OFFLINE, RECALL_ON_OPEN,
# RECALL_ON_DATA_ACCESS (OneDrive Files On-Demand and the like). Reading such a file fetches it.
CLOUD_ATTRIBUTES = 0x1000 | 0x40000 | 0x400000
DRIVE_REMOTE = 4
UNCHECKED = ("unread", "cloud", "link")      # listed, never looked into, never counted
# a native SQL Server backup starts with a tape-format block ("TAPE"); a compressed one with "MSSQLBAK"
SQL_MARKS = (b"TAPE", b"MSSQLBAK")


def _drive_type(root: str) -> int:
    """Windows' own answer for a drive root such as "D:\\" (4 = a network drive). It reads the local drive
    table and does not touch the drive. 0 where it cannot be asked."""
    if os.name != "nt":
        return 0
    try:
        import ctypes
        return int(ctypes.windll.kernel32.GetDriveTypeW(root))
    except Exception:
        return 0


def is_remote(path: str, drive_type=_drive_type) -> bool:
    """A path on another computer: \\\\server\\share..., or a drive letter mapped to one. Decided from the text
    of the path and the local drive table only."""
    p = str(path)
    if p.startswith(("\\\\", "//")):
        return True
    return len(p) >= 2 and p[0].isalpha() and p[1] == ":" and drive_type(p[:2] + "\\") == DRIVE_REMOTE


def is_link(path: str) -> bool:
    """The path itself is a symbolic link or a junction (asked of the path, not of where it points)."""
    try:
        return os.path.islink(path) or getattr(os.path, "isjunction", lambda p: False)(path)
    except OSError:
        return True


def off_limits(path: str, remote=is_remote) -> bool:
    """A place this tool does not look into: on the network, or a link (which may lead there)."""
    return remote(path) or is_link(path)


def desktops(env=None) -> list[str]:
    """The Desktop folders a backup may have been saved to: the user's (it may be redirected, e.g. into
    OneDrive) and the shared one. A folder on the network is returned as it is (the scan reports it as not
    searched); of the local ones, only those that exist."""
    real = env is None                    # a made-up environment (tests) must not pick up this PC's own Desktop
    env = os.environ if real else env
    found = []
    if real and os.name == "nt":
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
        key = os.path.normcase(p if is_remote(p) else os.path.abspath(p))
        if key not in seen and (off_limits(p) or os.path.isdir(p)):     # a network path or a link is never probed
            seen.add(key)
            out.append(p)
    return out


def default_roots() -> list[str]:
    return ["D:\\"] + desktops()


def kind(path: str, attributes: int = 0) -> str:
    """'sql' = a SQL Server database backup, 'other' = some other file named .bak, 'unread' = could not
    look, 'cloud' = its content is not on this disk, so it was not opened."""
    if attributes & CLOUD_ATTRIBUTES:
        return "cloud"
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return "unread"
    return "sql" if head.startswith(SQL_MARKS) else "other"


def _is_link(entry) -> bool:
    """A symbolic link or a Windows junction. Answered from the folder listing itself, never from the target."""
    try:
        return entry.is_symlink() or getattr(entry, "is_junction", lambda: False)()
    except OSError:
        return True                                  # cannot tell: treat it as one, so it is not followed


def _entry_stat(entry):
    """Size, date and Windows attributes of the entry itself (for a link: of the link, not of its target)."""
    return entry.stat(follow_symlinks=False)


def _bak_entries(root: str):
    """Every entry named *.bak under root, as (entry, is a link). Links are never followed: a linked folder is
    not entered and nothing is asked about a link's target. A folder that cannot be listed is passed over."""
    todo = [root]
    while todo:
        folder = todo.pop()
        try:
            with os.scandir(folder) as listing:
                entries = sorted(listing, key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            link = _is_link(e)
            if e.name.lower().endswith(".bak"):
                try:
                    is_folder = (not link) and e.is_dir(follow_symlinks=False)
                except OSError:
                    is_folder = False
                if not is_folder:
                    yield e, link
                    continue
            if link:
                continue
            try:
                if e.is_dir(follow_symlinks=False) and e.name.lower() not in SKIP_DIRS:
                    todo.append(e.path)
            except OSError:
                continue


def scan(roots, progress=None, remote=is_remote) -> tuple[list[dict], list[str], list[str]]:
    """Every .bak file under the roots, as {path, mtime, size, kind}; the roots that do not exist; and the
    roots on the network or reached through a link, which were not searched. A folder that cannot be opened is passed over: the list is
    what this user can see."""
    files, missing, skipped, seen = [], [], [], set()
    for root in roots:
        if off_limits(root, remote):                # before any look at it: even "does it exist" is a network call
            skipped.append(root)
            continue
        if not os.path.isdir(root):
            missing.append(root)
            continue
        if progress:
            progress(root)
        for entry, link in _bak_entries(root):
            path = entry.path
            key = os.path.normcase(os.path.abspath(path))
            if key in seen:                         # the Desktop may itself be on D:\
                continue
            try:
                st = _entry_stat(entry)
            except OSError:
                continue
            seen.add(key)
            files.append({"path": path, "mtime": st.st_mtime, "size": st.st_size,
                          "kind": "link" if link else kind(path, getattr(st, "st_file_attributes", 0))})
    files.sort(key=lambda f: (-f["mtime"], f["path"]))
    return files, missing, skipped


def verdict(files, now: float) -> dict:
    """code 'ok' / 'old' / 'none'. Judged on the files recognised as SQL Server backups (basis 'sql'); when
    none was recognised, on every .bak file found (basis 'any'), and the report says the type is not certain.

    `unchecked`: files newer than the one judged that could not be looked into (locked, refused, in the
    cloud, or a link). They never change the answer: a file nobody could read proves nothing, neither that the backup
    runs (it may be any program's .bak) nor that it does not (SQL Server locks the backup it is writing right
    now). The report names them and says to run again."""
    sql = [f for f in files if f["kind"] == "sql"]
    basis = sql or files
    if not basis:
        return {"code": "none", "basis": "none", "newest": None, "age_hours": None, "unchecked": 0}
    newest = max(basis, key=lambda f: f["mtime"])
    age = max(0.0, (now - newest["mtime"]) / 3600)
    unchecked = sum(1 for f in files if f["kind"] in UNCHECKED and f["mtime"] > newest["mtime"]) if sql else 0
    return {"code": "ok" if age <= MAX_AGE_HOURS else "old", "basis": "sql" if sql else "any",
            "newest": newest, "age_hours": age, "unchecked": unchecked}


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


def report(files, missing, roots, now: float, skipped=()) -> list[str]:
    v = verdict(files, now)
    lines = [f"E-Stock backups on this PC (read only)  {fmt_date(now)}",
             "searched: " + ", ".join(r for r in roots if r not in missing and r not in skipped)
             + ("" if not missing else "   not found: " + ", ".join(missing))]
    if skipped:
        lines.append("not searched (on the network, or a link; this tool stays on this PC): " + ", ".join(skipped))
    lines += [f".bak files found: {len(files)} (SQL Server backups: {sum(f['kind'] == 'sql' for f in files)})", ""]
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
                     " or it saves somewhere else." + (" (A network place was not searched, see above.)" if skipped else ""))
        return lines
    what = "SQL Server backup" if v["basis"] == "sql" else ".bak file (none looks like a SQL Server backup: type not certain)"
    where = f"{fmt_date(v['newest']['mtime'])}, {fmt_age(v['age_hours'])} old: {v['newest']['path']}"
    if v["code"] == "ok":
        lines.append(f"RESULT: OK - the newest {what} is {where}")
    else:
        lines.append(f"RESULT: WARNING - the newest {what} is OLDER THAN {MAX_AGE_HOURS} HOURS ({where})."
                     " E-Stock's hourly backup is probably not running.")
    if v["unchecked"]:
        lines.append(f"NOTE: {v['unchecked']} newer .bak file(s) above could not be looked into (type 'unread' ="
                     " locked or refused, 'cloud' = online only, 'link' = a link, not followed) and were not counted. If E-Stock is writing a"
                     " backup right now, run this again in a few minutes.")
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
    if v.get("unchecked"):
        unsure += (f"\n\n(فيه {v['unchecked']} ملف أحدث ماقدرتش أفتحه عشان أتأكد منه، فماتحسبش."
                   " لو إي ستوك بيعمل نسخة دلوقتي، شغّل الأداة تاني بعد كام دقيقة.)")
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
    files, missing, skipped = scan(roots, progress=lambda r: print(
        f"searching {r} ... (a big disk can take a few minutes)", file=out, flush=True))
    print("", file=out)
    for line in report(files, missing, roots, now, skipped):
        print(line, file=out)
    v = verdict(files, now)
    if box:
        message_box(arabic_message(v), warning=v["code"] != "ok")
    return {"ok": 0, "old": 1, "none": 2}[v["code"]]


if __name__ == "__main__":
    sys.exit(main())
