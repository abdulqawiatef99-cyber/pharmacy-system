"""Automatic backups.

- Every 15 / 30 / 60 (default) / 360 minutes, plus when a shift is closed and when the
  program stops normally. A backup is skipped when nothing changed since the last one.
- Retention: the last N (default 10) automatic backups, plus the newest one of each of the
  last 7 days and of each of the last 4 weeks, so a mistake found days later still has a
  copy from before it. Only files tagged "-auto" are ever deleted; manual, pre-update and
  pre-restore backups are never touched.
- A registered attachment (invoice file, transfer screenshot) that is missing or changed on disk does
  not stop the backups: the database is backed up, the file is named "-auto-incomplete", the owner is
  warned with the file names, and the newest complete backup is never pruned.
- Off-site copy: the newest backup is also copied to a folder the owner picks (USB disk,
  external drive, a Google Drive folder synced on the PC), with the same retention.
  A warning appears when that copy is older than 3 days.
Settings and state live in small JSON files in the data folder (not in the database, so
a restore never rolls them back).
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from ..db import Database
from . import audit, backup, notify
from .errors import ValidationFailed
from .numbers import whole_number
from .permissions import Context

log = logging.getLogger("pharm.backup")

INTERVALS = (15, 30, 60, 360)
DEFAULTS = {"interval_min": 60, "keep_last": 10, "keep_daily": 7, "keep_weekly": 4,
            "offsite_dir": "", "on_shift_close": True}
OFFSITE_STALE_DAYS = 3
# "-auto-incomplete": the database inside is good but a registered attachment was missing or changed
NAME = re.compile(r"^pharmacy-backup-(\d{8})-(\d{6})(?:-\d+)?-auto(-incomplete)?\.zip$")
INCOMPLETE = "-auto-incomplete.zip"
PART_STALE_SECONDS = 3600

_instance: "AutoBackup | None" = None


def trigger(reason: str) -> None:
    """Ask for a backup soon (e.g. after closing a shift). Never blocks or raises."""
    inst = _instance
    if inst is not None and (reason != "shift_close" or inst.settings().get("on_shift_close", True)):
        inst.pending_reason = reason                  # before waking the thread, so it sees the reason
        inst.wake.set()


def _stamp(p: Path) -> datetime | None:
    m = NAME.match(p.name)
    return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S") if m else None


def plan_retention(files: list[Path], keep_last: int, keep_daily: int, keep_weekly: int,
                   today: date | None = None) -> set[Path]:
    """Which automatic backups to keep."""
    today = today or date.today()
    dated = sorted(((t, f) for f in files if (t := _stamp(f))), reverse=True)
    keep = {f for _, f in dated[:max(1, keep_last)]}
    days: dict[date, Path] = {}
    weeks: dict[tuple, Path] = {}
    for t, f in dated:                                  # newest first: first seen = newest of its day/week
        d = t.date()
        if (today - d).days < keep_daily and d not in days:
            days[d] = f
        wk = d.isocalendar()[:2]
        if (today - d).days < keep_weekly * 7 and wk not in weeks:
            weeks[wk] = f
    return keep | set(days.values()) | set(weeks.values())


def prune(folder: Path, cfg: dict) -> list[str]:
    files = [f for pattern in ("pharmacy-backup-*-auto.zip", "pharmacy-backup-*" + INCOMPLETE)
             for f in folder.glob(pattern) if f.is_file()]
    keep = plan_retention(files, cfg["keep_last"], cfg["keep_daily"], cfg["keep_weekly"])
    # the newest complete backup may hold the only copy of a file that later went missing: incomplete
    # backups, however many, never push it out
    complete = sorted(((t, f) for f in files if (t := _stamp(f)) and not f.name.endswith(INCOMPLETE)), reverse=True)
    if complete:
        keep.add(complete[0][1])
    removed = []
    for f in files:
        if f not in keep:
            try:
                f.unlink()
                removed.append(f.name)
            except OSError as e:
                log.warning("could not delete old backup %s: %s", f, e)
    # half-written backups left by a crash or a power cut. Only old ones: a fresh one may be a backup
    # (manual, or a copy to this folder) being written right now.
    now = time.time()
    for f in folder.glob("pharmacy-backup-*.zip.part"):
        try:
            if f.is_file() and abs(now - f.stat().st_mtime) > PART_STALE_SECONDS:
                f.unlink()
                removed.append(f.name)
        except OSError as e:
            log.warning("could not delete unfinished backup %s: %s", f, e)
    return removed


class AutoBackup:
    def __init__(self, db: Database, upload_dir: Path, backup_dir: Path, state_dir: Path):
        self.db, self.upload_dir, self.backup_dir = db, upload_dir, backup_dir
        self.cfg_path, self.state_path = state_dir / "backup_settings.json", state_dir / "backup_state.json"
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stop_event = threading.Event()
        self.pending_reason = None
        self.thread: threading.Thread | None = None

    # ----------------------------------------------------------------- settings / state
    def _read(self, path: Path, default: dict) -> dict:
        try:
            return {**default, **json.loads(path.read_text(encoding="utf-8"))}
        except (OSError, ValueError):
            return dict(default)

    def _write(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)

    def settings(self) -> dict:
        return self._read(self.cfg_path, DEFAULTS)

    def state(self) -> dict:
        return self._read(self.state_path, {})

    def update_settings(self, ctx: Context, changes: dict) -> dict:
        ctx.require("settings.manage")
        cfg = self.settings()
        new = dict(cfg)
        if "interval_min" in changes:
            if changes["interval_min"] not in INTERVALS:
                raise ValidationFailed("اختر ربع ساعة أو نصف ساعة أو ساعة أو 6 ساعات")
            new["interval_min"] = changes["interval_min"]
        for k, lo, hi in (("keep_last", 1, 100), ("keep_daily", 0, 60), ("keep_weekly", 0, 52)):
            if k in changes:
                try:
                    v = whole_number(changes[k])     # 2.5 or true is refused, never truncated
                except (TypeError, ValueError):
                    raise ValidationFailed("رقم غير صحيح")
                if not lo <= v <= hi:
                    raise ValidationFailed(f"يجب أن يكون الرقم من {lo} إلى {hi}")
                new[k] = v
        if "on_shift_close" in changes:
            new["on_shift_close"] = bool(changes["on_shift_close"])
        if "offsite_dir" in changes:
            d = str(changes["offsite_dir"] or "").strip()
            if d:
                p = Path(d)
                if not p.is_absolute():
                    raise ValidationFailed("اكتب المسار كاملًا، مثلًا E:\\نسخ الصيدلية")
                if p.resolve() == self.backup_dir.resolve():
                    raise ValidationFailed("هذا هو مجلد النسخ نفسه على الجهاز؛ اختر ذاكرة فلاش أو قرصًا آخر")
            new["offsite_dir"] = d
        with self.db.transaction():
            self._write(self.cfg_path, new)
            audit.record(self.db, ctx, "backup.settings", "system", None,
                         before={k: cfg.get(k) for k in new}, after=new)
        self.wake.set()
        return new

    # ----------------------------------------------------------------- one backup
    def _fingerprint(self) -> str:
        """Changes since last backup: total row writes are not tracked by SQLite, so use the
        audit chain head + newest stock movement + counts of the busiest tables."""
        r = self.db.one("SELECT (SELECT coalesce(max(seq),0) FROM audit_log) a,"
                        " (SELECT count(*) FROM stock_movements) m, (SELECT count(*) FROM sales) s,"
                        " (SELECT count(*) FROM sale_lines) l, (SELECT count(*) FROM purchase_invoice_lines) p,"
                        " (SELECT count(*) FROM cash_movements) c, (SELECT count(*) FROM notifications) n")
        return "|".join(str(r[k]) for k in r.keys())

    def run(self, reason: str = "schedule", *, force: bool = False) -> dict:
        with self.lock:
            st = self.state()
            fp = self._fingerprint()
            if not force and st.get("last_ok") and st.get("fingerprint") == fp:
                st["last_check_at"] = datetime.now().isoformat(timespec="seconds")
                self._write(self.state_path, st)
                # #202: a change made outside the program (a hand edit, a damaged file) does not change the
                # fingerprint, so the stock check runs on the "no changes" path too (Codex P2 on #213)
                self._check_stock()
                return {"skipped": "no_changes", **st}
            started = time.monotonic()
            try:
                out = backup.create(self.db, self.upload_dir, self.backup_dir, tag="auto")
                hard, soft = backup.split_problems(backup.quick_check(out))
                if hard:
                    out.unlink(missing_ok=True)
                    raise RuntimeError("; ".join(hard + soft))
                warning, lost = None, {}
                if soft:
                    # the database part is good: one lost upload must not stop every backup. Keep the
                    # file under a name that says so, and tell the owner which files are affected.
                    stem = out.name[:-len("-auto.zip")]
                    parts = stem.split("-")          # pharmacy-backup-<date>-<time>[-<n>]
                    base, n = "-".join(parts[:4]), int(parts[4]) if len(parts) > 4 else 0
                    marked = out.with_name(stem + INCOMPLETE)
                    while marked.exists():           # two incomplete backups in one second: never overwrite
                        n += 1
                        marked = out.with_name(f"{base}-{n}{INCOMPLETE}")
                    out.replace(marked)
                    out = marked
                    lost = self._file_names(soft)    # per pharmacy: a file name is that pharmacy's own data
                    warning = (f"{len(soft)} attachment file(s) missing or changed; the backup holds the data"
                               " without them")
                    log.warning("automatic backup kept as incomplete (%s): %s", out.name, "; ".join(soft))
                cfg = self.settings()
                removed = prune(self.backup_dir, cfg)
                st.update(last_at=datetime.now().isoformat(timespec="seconds"), last_file=out.name,
                          last_bytes=out.stat().st_size, last_seconds=round(time.monotonic() - started, 1),
                          last_ok=True, last_error=None, last_reason=reason, fingerprint=fp, fail_notified=None,
                          last_warning=warning, last_warning_files=lost or {})
                if soft:
                    self._notify_once(st, "warn_notified", "warning", "النسخة الاحتياطية ناقصها ملفات فواتير",
                                      {o: ("البيانات اتحفظت، لكن الملفات دي ناقصة أو متغيّرة على الجهاز: "
                                           + "، ".join(names))[:400] for o, names in lost.items()}, hours=24)
                else:
                    st["warn_notified"] = None
                if removed:
                    log.info("removed old backups: %s", removed)
                self._offsite(out, cfg, st)
            except Exception as e:                   # never let a failed backup take the program down
                log.exception("automatic backup failed")
                st.update(last_ok=False, last_error=str(e)[:300], last_fail_at=datetime.now().isoformat(timespec="seconds"))
                self._notify_once(st, "fail_notified", "error", "فشلت النسخة الاحتياطية التلقائية",
                                  str(e)[:200], hours=6)
            self._write(self.state_path, st)
        # #202: recompute every stock balance from the ledger after each backup run (also when it failed: a
        # stock mismatch is one reason a backup is refused). Alerts and audits only; never repairs.
        self._check_stock()
        return st

    def _file_names(self, problems: list[str]) -> dict[str, list[str]]:
        """Per pharmacy, the names its owner knows (as uploaded) for the attachments these problems are about."""
        names: dict[str, list[str]] = {}
        for p in problems:
            sid = backup.attachment_source(p)
            row = self.db.one("SELECT org_id, filename FROM source_files WHERE id=?", (sid,))
            if row:
                names.setdefault(row["org_id"], []).append(row["filename"] or str(sid))
        return names

    def _check_stock(self) -> None:
        from ..inventory import integrity
        integrity.check_all(self.db, "backup")

    def _offsite(self, newest: Path, cfg: dict, st: dict) -> None:
        d = cfg.get("offsite_dir")
        if not d:
            return
        folder = Path(d)
        try:
            if not folder.exists():
                raise FileNotFoundError("المجلد غير موجود — هل ذاكرة الفلاش أو القرص غير متصل؟")
            part = folder / (newest.name + ".part")
            try:
                shutil.copyfile(newest, part)
                # read the copy back: a failing USB stick must not keep a broken "backup"
                if backup._sha256(part) != backup._sha256(newest):
                    raise OSError("النسخة على ذاكرة الفلاش أو القرص لا تطابق الأصل — جرّب ذاكرة أخرى")
                part.replace(folder / newest.name)
            finally:
                part.unlink(missing_ok=True)
            prune(folder, cfg)
            st.update(offsite_at=datetime.now().isoformat(timespec="seconds"), offsite_error=None,
                      offsite_notified=None)
        except Exception as e:
            st["offsite_error"] = str(e)[:300]
            last = st.get("offsite_at")
            if not last or datetime.fromisoformat(last) < datetime.now() - timedelta(days=OFFSITE_STALE_DAYS):
                self._notify_once(st, "offsite_notified", "warning", "النسخة الموجودة خارج الجهاز قديمة",
                                  f"آخر نسخة خارج الجهاز: {last[:10] if last else 'لا يوجد'} — وصّل ذاكرة الفلاش أو القرص",
                                  hours=24)

    def _notify_once(self, st: dict, key: str, severity: str, title: str, body, *, hours: int) -> None:
        """`body`: one text for every pharmacy, or {org_id: text} to tell each pharmacy only about its own files."""
        last = st.get(key)
        if last and datetime.fromisoformat(last) > datetime.now() - timedelta(hours=hours):
            return
        try:
            with self.db.transaction():
                for o in self.db.all("SELECT id FROM organizations WHERE status='active'"):
                    text = body.get(o["id"]) if isinstance(body, dict) else body
                    if text:
                        notify.emit(self.db, Context(user_id="system", org_id=o["id"], branch_id=None),
                                    "backup", severity, title, text, "system", None)
            st[key] = datetime.now().isoformat(timespec="seconds")
        except Exception:
            log.exception("could not record backup notification")

    # ----------------------------------------------------------------- status for the owner
    def status(self, ctx: Context) -> dict:
        ctx.require("settings.manage")
        cfg, st = self.settings(), self.state()
        files = sorted(self.backup_dir.glob("pharmacy-backup-*.zip"), key=lambda f: f.stat().st_mtime,
                       reverse=True) if self.backup_dir.exists() else []
        last = st.get("last_at")
        due = (datetime.fromisoformat(last) + timedelta(minutes=cfg["interval_min"])) if last else None
        off = st.get("offsite_at")
        shown = {k: v for k, v in st.items() if k != "fingerprint"}
        by_org = shown.get("last_warning_files")
        shown["last_warning_files"] = (by_org or {}).get(ctx.org_id, []) if isinstance(by_org, dict) else []
        return {"settings": cfg, "state": shown,
                "folder": str(self.backup_dir), "next_at": due.isoformat(timespec="minutes") if due else None,
                "offsite_stale": bool(cfg.get("offsite_dir")) and (
                    not off or datetime.fromisoformat(off) < datetime.now() - timedelta(days=OFFSITE_STALE_DAYS)),
                "total_bytes": sum(f.stat().st_size for f in files),
                "files": [{"name": f.name, "bytes": f.stat().st_size, "auto": bool(NAME.match(f.name)),
                           "at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="minutes")}
                          for f in files[:60]]}

    # ----------------------------------------------------------------- scheduler
    def _due(self) -> bool:
        st, cfg = self.state(), self.settings()
        now = datetime.now()
        try:
            if not st.get("last_ok"):                  # retry a failed backup after one interval too
                times = [datetime.fromisoformat(st["last_fail_at"])] if st.get("last_fail_at") else []
            else:
                times = [datetime.fromisoformat(t) for t in (st.get("last_check_at"), st.get("last_at")) if t]
            # a time in the future means the PC clock moved backwards: it says nothing about when the
            # last backup really was, so it never postpones the next one
            times = [t for t in times if t <= now]
            if not times:
                return True
            return max(times) + timedelta(minutes=cfg["interval_min"]) <= now
        except (TypeError, ValueError):               # a state or settings file edited by hand: back up now
            return True

    def _loop(self) -> None:
        self.stop_event.wait(20)                      # let the program finish starting
        while not self.stop_event.is_set():
            woke = self.wake.wait(30)
            if self.stop_event.is_set():
                break
            if woke:
                self.wake.clear()
            try:
                reason = self.pending_reason
                if reason or self._due():
                    self.pending_reason = None
                    self.run(reason or "schedule")
            except Exception:                          # the scheduler thread must outlive any one bad check
                log.exception("automatic backup scheduler: check failed")

    def start(self) -> None:
        global _instance
        _instance = self
        self.thread = threading.Thread(target=self._loop, name="auto-backup", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        """On a normal shutdown: one last backup if something changed."""
        global _instance
        self.stop_event.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=5)
        _instance = None
        self.run("shutdown")
