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
- Watchdog (the backup must never stop silently): a second thread, independent of the scheduler, checks
  every minute. It restarts a scheduler thread that died, and tells the owner once a day while the backup
  is stale (no good backup or "nothing changed" check for two intervals, or the scheduler dead or stuck).
  Reading the summary (health()) restarts dead threads too, so the screen is a third watcher. Free disk
  space is measured before every run. Each of these events is written in the audit log.
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
from . import audit, backup, notify, responsive
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
WATCH_SECONDS = 60               # how often the watchdog looks
STALE_INTERVALS = 2              # stale = no good backup or check for this many intervals
STALE_NOTIFY_HOURS = 24          # one notification a day while it stays stale
STUCK_SECONDS = 45 * 60          # a single run longer than this is reported as stuck
MIN_FREE_BYTES = 1024 ** 3       # warn below 1 GiB free, or below twice (database + last backup)
# reasons that are a warning (the data is still being backed up); every other reason is an error
WARNINGS = ("low_disk", "offsite_stale", "incomplete")
# one short line per reason for the screen (F114), worst first; the same wording as the notifications
LINES = {"failed": "آخر نسخة احتياطية تلقائية فشلت",
         "never": "لم تُعمل أي نسخة احتياطية منذ تشغيل البرنامج",
         "scheduler_dead": "النسخ الاحتياطي التلقائي متوقف",
         "scheduler_stuck": "نسخة احتياطية بدأت ولم تنتهِ",
         "file_missing": "ملف آخر نسخة احتياطية غير موجود على الجهاز",
         "stale": "النسخة الاحتياطية التلقائية متأخرة",
         "low_disk": "مساحة القرص قليلة للنسخ الاحتياطي",
         "offsite_stale": "النسخة الموجودة خارج الجهاز قديمة",
         "incomplete": "النسخة الاحتياطية ناقصها ملفات فواتير"}

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
        # watchdog: its own thread, stop signal, lock and state file, so nothing it does waits for a backup
        self.watch_path = state_dir / "backup_watchdog.json"
        self.watch_thread: threading.Thread | None = None
        self.watch_stop = threading.Event()
        self.watch_lock = threading.Lock()
        self._started = False
        self.started_at: datetime | None = None
        self.run_started: float | None = None        # time.monotonic() while a run is in progress
        self.restarts = 0
        self._own = {"a": 0, "n": 0}                 # audit rows / notifications written by the current run

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

    def _after_own_writes(self, fp: str) -> str:
        """The fingerprint as it will read once this run's own notifications and audit rows are counted:
        they are not changes to back up. Without this every warning forced one more backup, and a record
        per event would force one for ever."""
        parts = fp.split("|")
        try:
            parts[0], parts[-1] = str(int(parts[0]) + self._own["a"]), str(int(parts[-1]) + self._own["n"])
        except (ValueError, IndexError):
            return fp
        return "|".join(parts)

    def _disk(self, st: dict) -> tuple[int | None, bool]:
        """(free bytes where the backups are written, is that low?). Low = under MIN_FREE_BYTES, or under
        twice what one backup needs (a copy of the database plus the archive)."""
        try:
            folder = self.backup_dir if self.backup_dir.exists() else self.backup_dir.parent
            free = int(shutil.disk_usage(folder).free)
        except (OSError, ValueError):
            return None, False               # cannot be measured: a failed backup is reported by itself
        try:
            size = Path(self.db.path).stat().st_size
        except OSError:
            size = 0
        last = st.get("last_bytes")
        need = size + (last if isinstance(last, int) and last > 0 else 0)
        return free, free < max(MIN_FREE_BYTES, 2 * need)

    def _measure_disk(self, st: dict) -> None:
        """Free space into the state; when low, one warning a day (with its audit row)."""
        st["free_bytes"], st["low_disk"] = self._disk(st)
        if st["low_disk"]:
            mb = st["free_bytes"] // (1024 * 1024)
            self._notify_once(st, "disk_notified", "warning", "مساحة القرص قليلة للنسخ الاحتياطي",
                              f"المساحة الفاضية على قرص النسخ {mb:,} ميجا فقط. فضّي مساحة أو غيّر مكان النسخ قبل ما"
                              " النسخ يفشل.", hours=24, audit_action="backup.low_disk",
                              audit_after={"free_bytes": st["free_bytes"], "folder": str(self.backup_dir)})
        else:
            st["disk_notified"] = None

    def run(self, reason: str = "schedule", *, force: bool = False) -> dict:
        with self.lock:
            self.run_started = time.monotonic()       # the watchdog reports a run that never ends
            try:
                st, ran = self._run(reason, force)
            finally:
                self.run_started = None
        if ran:
            # #202: recompute every stock balance from the ledger after each backup run (also when it failed:
            # a stock mismatch is one reason a backup is refused). Alerts and audits only; never repairs.
            self._check_stock()
        return st

    def _run(self, reason: str, force: bool) -> tuple[dict, bool]:
        """(state, was a backup attempted). Called with the backup lock held."""
        st = self.state()
        fp = self._fingerprint()
        self._own = {"a": 0, "n": 0}
        # nothing changed since the last good backup, and that backup is still on the disk: only then is there
        # nothing to do. A newest file deleted by hand or lost with its disk is made again now (F114); before,
        # an unchanged database was never backed up again.
        if not force and st.get("last_ok") and st.get("fingerprint") == fp and not self._newest_missing(st):
            st["last_check_at"] = datetime.now().isoformat(timespec="seconds")
            # the disk is measured here too: the warning must clear when space is freed, and appear when it
            # runs low, without waiting for the next change to the data (Codex P2 on #350)
            self._measure_disk(st)
            st["fingerprint"] = self._after_own_writes(fp)
            self._offsite_catch_up(self.settings(), st)
            self._write(self.state_path, st)
            # #202: a change made outside the program (a hand edit, a damaged file) does not change the
            # fingerprint, so the stock check runs on the "no changes" path too (Codex P2 on #213)
            self._check_stock()
            return {"skipped": "no_changes", **st}, False
        started = time.monotonic()
        failed_before = st.get("last_ok") is False
        self._measure_disk(st)
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
            if failed_before:
                self._record("backup.recovered", {"file": out.name, "reason": reason})
            st["fingerprint"] = self._after_own_writes(fp)
        except Exception as e:                   # never let a failed backup take the program down
            log.exception("automatic backup failed")
            st.update(last_ok=False, last_error=str(e)[:300], last_fail_at=datetime.now().isoformat(timespec="seconds"))
            self._notify_once(st, "fail_notified", "error", "فشلت النسخة الاحتياطية التلقائية",
                              str(e)[:200], hours=6, audit_action="backup.failed",
                              audit_after={"error": str(e)[:300], "reason": reason})
        self._write(self.state_path, st)
        return st, True

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

    def _offsite_catch_up(self, cfg: dict, st: dict) -> None:
        """Nothing changed, so no new backup: but if the newest one never reached the off-device folder (the
        drive was unplugged when it was made), copy it now that the folder is there. The "no changes" path
        used to return before the copy, so a reconnected drive stayed without the latest backup until the
        next change (#324 X27)."""
        d, name = cfg.get("offsite_dir"), st.get("last_file")
        if not d or not name:
            return
        try:
            newest, folder = self.backup_dir / str(name), Path(d)
            if newest.is_file() and folder.is_dir() and not (folder / newest.name).exists():
                self._offsite(newest, cfg, st)
        except Exception:                             # a bad path in the settings: the next real run reports it
            log.exception("off-device catch-up copy failed")

    def _notify_once(self, st: dict, key: str, severity: str, title: str, body, *, hours: int,
                     audit_action: str | None = None, audit_after: dict | None = None) -> bool:
        """`body`: one text for every pharmacy, or {org_id: text} to tell each pharmacy only about its own files.
        With `audit_action`, the event is also written in each pharmacy's audit log, in the same transaction.
        True when it was told now (False: already told within `hours`)."""
        last = st.get(key)
        try:
            if last and datetime.fromisoformat(last) > datetime.now() - timedelta(hours=hours):
                return False
        except (TypeError, ValueError):               # a state file edited by hand: tell again
            pass
        try:
            with self.db.transaction():
                for o in self.db.all("SELECT id FROM organizations WHERE status='active'"):
                    text = body.get(o["id"]) if isinstance(body, dict) else body
                    if text:
                        notify.emit(self.db, Context(user_id="system", org_id=o["id"], branch_id=None),
                                    "backup", severity, title, text, "system", None)
                        self._own["n"] += 1
                    if audit_action:
                        audit.record(self.db, None, audit_action, "system", None, after=audit_after, org_id=o["id"])
                        self._own["a"] += 1
            st[key] = datetime.now().isoformat(timespec="seconds")
            return True
        except Exception:
            log.exception("could not record backup notification")
            return False

    def _record(self, action: str, after: dict) -> None:
        """An audit row in every pharmacy for a backup event that needs no notification."""
        try:
            with self.db.transaction():
                for o in self.db.all("SELECT id FROM organizations WHERE status='active'"):
                    audit.record(self.db, None, action, "system", None, after=after, org_id=o["id"])
                    self._own["a"] += 1
        except Exception:
            log.exception("could not record %s", action)

    # ----------------------------------------------------------------- watchdog
    @staticmethod
    def _when(v) -> datetime | None:
        """A time read from a state file, or None: not a time, or in the future (the PC clock moved back,
        so it says nothing about when the backup really was)."""
        try:
            t = datetime.fromisoformat(v)
        except (TypeError, ValueError):
            return None
        return t if t <= datetime.now() else None

    def _newest_missing(self, st: dict) -> bool:
        """The backup file the state names as the newest is not on the disk, or is not the size it was written
        with: the summary would say "fine" about a backup nobody can restore."""
        name = st.get("last_file")
        if not name:
            return False
        try:
            size = (self.backup_dir / str(name)).stat().st_size
        except OSError:
            return True
        want = st.get("last_bytes")
        return isinstance(want, int) and want > 0 and size != want

    def health(self, *, detail: bool = False) -> dict:
        """One summary of "is the pharmacy's data being backed up?", cheap enough to poll: two small files
        and one look at the newest backup file, no database query. Safe for every signed-in user
        (`detail=False` leaves the error text out). Calling it restarts a scheduler or watchdog thread that died."""
        self._ensure_threads()
        cfg, st = self.settings(), self.state()
        now = datetime.now()
        interval = cfg.get("interval_min")
        if interval not in INTERVALS:
            interval = DEFAULTS["interval_min"]
        limit = STALE_INTERVALS * interval
        good = self._when(st.get("last_at"))
        checked = self._when(st.get("last_check_at")) if st.get("last_ok") else None   # a check after a failure proves nothing
        fresh = max((t for t in (good, checked) if t), default=None)
        age = int((now - fresh).total_seconds() // 60) if fresh else None
        reasons = []
        if st.get("last_ok") is False:
            reasons.append("failed")
        if fresh:
            # to the second, as the screen's "late" always was: `age` is whole minutes, and comparing that with
            # the limit said "fine" for the minute after the limit (Codex P2 on #370)
            if now - fresh > timedelta(minutes=limit):
                reasons.append("stale")
        elif self.started_at and (now - self.started_at) > timedelta(minutes=limit):
            reasons.append("never")
        if not self._started or self.stop_event.is_set():
            scheduler = "stopped"
        elif self.thread is None or not self.thread.is_alive():
            scheduler = "dead"
            reasons.append("scheduler_dead")
        elif self.run_started is not None and time.monotonic() - self.run_started > STUCK_SECONDS:
            scheduler = "stuck"
            reasons.append("scheduler_stuck")
        else:
            scheduler = "running"
        if self._newest_missing(st):
            reasons.append("file_missing")
        stale = any(r in ("stale", "never", "scheduler_dead", "scheduler_stuck", "file_missing") for r in reasons)
        if st.get("low_disk"):
            reasons.append("low_disk")
        off = self._when(st.get("offsite_at"))
        if cfg.get("offsite_dir") and good and (off is None or now - off > timedelta(days=OFFSITE_STALE_DAYS)):
            reasons.append("offsite_stale")          # a folder is set, there is a backup, and no recent copy of it
        if st.get("last_warning"):
            reasons.append("incomplete")
        level = "ok" if not reasons else "warning" if all(r in WARNINGS for r in reasons) else "error"
        out = {"ok": not reasons, "level": level,
               "stale": stale, "last_good_at": good.isoformat(timespec="seconds") if good else None,
               "last_fresh_at": fresh.isoformat(timespec="seconds") if fresh else None,
               "last_fail_at": st.get("last_fail_at"), "age_min": age, "limit_min": limit, "scheduler": scheduler,
               "scheduler_restarts": self.restarts, "low_disk": bool(st.get("low_disk")),
               "free_bytes": st.get("free_bytes"), "reasons": reasons}
        if detail:
            out["last_error"] = st.get("last_error")
        return out

    def screen(self, *, detail: bool = False) -> dict:
        """The one answer for the screen (F114): is there anything about the backups a person must see, and the
        line to show. `detail=False` is safe for every signed-in user (no error text, no folder); `detail=True`
        is for who may manage the settings, and its line says where to go.

        It carries the keys the screen already reads with their old meaning (`warn`, `failed`, `late`,
        `last_good_at` = the last backup or "nothing changed" check, `ok` = how the last run ended,
        `interval_min`, `last_fail_at`; with detail `last_error` and `offsite_error`), so the route can return
        this as it is. New: `healthy`, `level` (ok / warning / error), `reasons`, `text`, `last_backup_at`.
        `warn` is now true for every reason, not only "failed" and "late"."""
        h = self.health(detail=detail)
        st = self.state()
        failed = st.get("last_ok") is False
        fresh = self._when(h["last_fresh_at"])
        late = fresh is not None and datetime.now() - fresh > timedelta(minutes=h["limit_min"])
        text = None
        if h["reasons"]:
            said = "، ".join(LINES[r] for r in h["reasons"] if r in LINES)
            if h["level"] == "error" and h["last_fresh_at"]:
                said += f" — آخر نسخة سليمة: {h['last_fresh_at'][:16].replace('T', ' ')}"
            do = "افتح «البرنامج والنسخ الاحتياطي»." if detail else "بلّغ صاحب الصيدلية."
            text = f"{'✕' if h['level'] == 'error' else '⚠'} {said}. {do}"
        out = {"warn": bool(h["reasons"]), "healthy": h["ok"], "level": h["level"], "reasons": h["reasons"],
               "text": text, "failed": failed, "late": late, "ok": st.get("last_ok"),
               "last_good_at": h["last_fresh_at"], "last_backup_at": h["last_good_at"],
               "interval_min": h["limit_min"] // STALE_INTERVALS, "last_fail_at": st.get("last_fail_at")}
        if detail:
            out["last_error"] = st.get("last_error") if failed else None
            out["offsite_error"] = st.get("offsite_error")
        return out

    def _ensure_threads(self) -> None:
        """Start again whichever of the two threads is not running. Called by the watchdog, and by every
        read of the summary: no single thread's death can stop the backups unnoticed."""
        if not self._started or self.stop_event.is_set():
            return
        restarted = False
        with self.watch_lock:
            if not self._started or self.stop_event.is_set():
                return
            if self.thread is None or not self.thread.is_alive():
                self._spawn_scheduler()
                self.restarts += 1
                restarted = True
            if self.watch_thread is None or not self.watch_thread.is_alive():
                self._spawn_watchdog()
        if restarted:
            log.error("the automatic backup scheduler thread had stopped; restarted (%d)", self.restarts)
            w = self._read(self.watch_path, {})
            self._notify_once(w, "restart_notified", "warning", "توقف جدول النسخ الاحتياطي وأُعيد تشغيله",
                              "جدول النسخ الاحتياطي التلقائي توقف وأعاده البرنامج للعمل. لو اتكررت الرسالة بلّغ الدعم.",
                              hours=STALE_NOTIFY_HOURS, audit_action="backup.scheduler_restart",
                              audit_after={"restarts": self.restarts})
            self._save_watch(w)

    def _spawn_scheduler(self) -> None:
        self.thread = threading.Thread(target=self._loop, name="auto-backup", daemon=True)
        self.thread.start()

    def _spawn_watchdog(self) -> None:
        self.watch_thread = threading.Thread(target=self._watch, name="backup-watchdog", daemon=True)
        self.watch_thread.start()

    def _save_watch(self, w: dict) -> None:
        try:
            self._write(self.watch_path, w)
        except OSError:
            log.exception("could not save the backup watchdog state")

    def watch_once(self) -> dict:
        """One look by the watchdog. Never takes the backup lock: a run that hangs (a dead USB drive, a
        network folder) must not silence the alarm about it."""
        h = self.health(detail=True)
        w = self._read(self.watch_path, {})
        before = dict(w)
        if h["stale"]:
            w.setdefault("stale_since", datetime.now().isoformat(timespec="seconds"))
            last = h["last_good_at"]
            if last and h["age_min"] is not None:
                hours = h["age_min"] // 60
                since = f"آخر نسخة سليمة: {last[:16].replace('T', ' ')} (من {hours} ساعة)." if hours else \
                    f"آخر نسخة سليمة: {last[:16].replace('T', ' ')}."
            else:
                since = "لا توجد نسخة سليمة حتى الآن."
            why = {"scheduler_dead": "جدول النسخ توقف.", "scheduler_stuck": "نسخة بدأت ولم تنتهِ.",
                   "file_missing": "ملف آخر نسخة احتياطية غير موجود على الجهاز.",
                   "failed": f"آخر محاولة فشلت: {(h.get('last_error') or '')[:120]}"}
            body = " ".join([since] + [why[r] for r in h["reasons"] if r in why]
                            + ["افتح الإعدادات ← النسخ الاحتياطي واضغط «اعمل نسخة الآن»، ولو فشلت بلّغ الدعم."])
            self._notify_once(w, "stale_notified", "error", "النسخ الاحتياطي متوقف", body, hours=STALE_NOTIFY_HOURS,
                              audit_action="backup.stale",
                              audit_after={"reasons": h["reasons"], "last_good_at": last, "age_min": h["age_min"]})
        elif w.get("stale_since"):
            self._record("backup.recovered", {"stale_since": w["stale_since"], "last_good_at": h["last_good_at"]})
            w.pop("stale_since", None)
            w.pop("stale_notified", None)
        if w != before:
            self._save_watch(w)
        return h

    def _watch(self) -> None:
        while not self.watch_stop.wait(WATCH_SECONDS):
            try:
                self.watch_once()
            except Exception:                          # the watchdog must outlive any one bad look
                log.exception("backup watchdog: check failed")

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
        return {"settings": cfg, "state": shown, "health": self.health(detail=True),
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
        self.stop_event.clear()
        self.watch_stop.clear()
        self.started_at = datetime.now()
        responsive.apply()          # the backup's own checks must not make a cashier's request wait
        with self.watch_lock:
            self.restarts = 0
            self._spawn_scheduler()
            self._spawn_watchdog()
            self._started = True

    def stop(self) -> None:
        """On a normal shutdown: one last backup if something changed."""
        global _instance
        self._started = False
        self.stop_event.set()
        self.watch_stop.set()
        self.wake.set()
        for t in (self.thread, self.watch_thread):
            if t:
                t.join(timeout=5)
        _instance = None
        self.run("shutdown")
