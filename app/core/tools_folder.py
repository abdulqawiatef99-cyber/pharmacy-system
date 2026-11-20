"""One fixed folder for the owner's double-click scripts (manager 18/11): %LOCALAPPDATA%\\PharmacyPlatform\\tools.

An in-app update puts its code in data\\code-releases\\releases\\<hash>\\, where nobody finds the scripts.
Each start of the program (`manage.py serve` under the launcher, i.e. the release that is running now)
copies the owner scripts of that release into the tools folder and keeps a shortcut to it on the Desktop.
The update button ships it like any other code: nothing here needs the installer.

Only scripts that work from any folder are copied: they find the installed program by its standard place
(%LOCALAPPDATA%\\Programs\\PharmacyPlatform) and use %~dp0 only for their own siblings. A file is written
only when its bytes changed, through a temp file, so a script that is running (an import that restarts
the program at its end) is never rewritten under itself by the same release.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

TOOLS = ("estock-db-read.bat", "estock-db-read.ps1", "estock-db-read-one.bat", "estock-db-import.bat",
         "estock-import.bat", "launch-fresh-start.bat", "estock-backups.bat", "estock-backups.py")
SHORTCUT = "أدوات الصيدلية.url"
README = "اقرأني.txt"
README_TEXT = (
    "الفولدر ده بيتحدّث لوحده مع كل تحديث للبرنامج: افتح الملفات من هنا دايمًا.\r\n"
    "\r\n"
    "estock-db-read.bat       قراية قاعدة إي ستوك كلها (قراية بس، إي ستوك مابيتغيرش)\r\n"
    "estock-db-read-one.bat   إعادة قراية الجداول اللي ماتقريتش بس\r\n"
    "estock-db-import.bat     نقل إي ستوك كله للبرنامج (تجربة الأول، وبعدين YES)\r\n"
    "estock-import.bat        النقل القديم من ملفات إكسيل\r\n"
    "launch-fresh-start.bat   بداية من الصفر قبل الإطلاق بس\r\n"
    "estock-backups.bat       عرض نسخ إي ستوك الاحتياطية اللي على الجهاز وعمر أحدث نسخة (قراية بس)\r\n")
log = logging.getLogger("pharm.tools")


def _write_if_changed(target: Path, data: bytes) -> bool:
    try:
        if target.is_file() and target.read_bytes() == data:
            return False
    except OSError:
        pass
    part = target.with_name(target.name + ".part")
    part.write_bytes(data)
    os.replace(part, target)
    return True


def desktop_dir() -> Path | None:
    """The user's Desktop (it may be redirected, e.g. to OneDrive): Windows' own setting, else the usual place."""
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
                p = Path(os.path.expandvars(winreg.QueryValueEx(k, "Desktop")[0]))
                if p.is_dir():
                    return p
        except OSError:
            pass
    p = Path(os.environ.get("USERPROFILE") or Path.home()) / "Desktop"
    return p if p.is_dir() else None


def refresh(release_root: Path, home: Path, desktop: Path | None = None) -> dict:
    """Copy the owner scripts of `release_root` into home\\tools; keep the Desktop shortcut to it.
    Returns what changed. Never raises: the program must start whatever happens here."""
    out = {"tools": None, "written": [], "shortcut": None}
    try:
        src = Path(release_root) / "scripts" / "windows"
        tools = Path(home) / "tools"
        tools.mkdir(parents=True, exist_ok=True)
        out["tools"] = str(tools)
        for name in TOOLS:
            f = src / name
            if f.is_file() and _write_if_changed(tools / name, f.read_bytes()):
                out["written"].append(name)
        if _write_if_changed(tools / README, b"\xef\xbb\xbf" + README_TEXT.encode("utf-8")):
            out["written"].append(README)
        desk = desktop if desktop is not None else desktop_dir()
        if desk:
            # a plain Internet shortcut (text, no script, no COM) that opens the folder in Explorer
            url = tools.resolve().as_uri()          # percent-encoded: an Arabic user name stays valid
            body = f"[InternetShortcut]\r\nURL={url}\r\nIconFile=%SystemRoot%\\System32\\shell32.dll\r\nIconIndex=3\r\n"
            _write_if_changed(Path(desk) / SHORTCUT, body.encode("utf-8"))
            out["shortcut"] = str(Path(desk) / SHORTCUT)
    except Exception:    # noqa: BLE001 - a full disk or a locked file must never stop the cashier
        log.exception("tools folder refresh failed")
    return out
