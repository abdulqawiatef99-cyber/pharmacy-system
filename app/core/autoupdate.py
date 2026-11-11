"""Automatic updates (#96): check, then apply only when it is safe, with no button press.

- Checks the chosen channel at startup and every few hours (default 4). Channel "main" is the pharmacy
  (default); "develop" is for the owner's test PC only (an installer built from develop, or the marker
  file ~/.pharmacy/test_pc). An installer from any other branch is a review build: it is never replaced
  unless the owner picks a channel.
- Applies only when no sale and no cash shift is open (a draft invoice with lines started in the last hour,
  any sales write in the last 5 minutes, or an open shift), and only outside working hours: the quiet
  hours (default 03:00-06:00). The first minutes after startup and a shift close count too only when the
  owner turned that on (setting "on_events", off by default). Any other time it waits for the owner's
  "update now" (his confirmation). While the program restarts, sales writes are refused for a moment.
- A new version is announced once in Arabic: when it will install by itself, or that it waits for the
  owner's button.
- Code update: `manage.py update --ref <channel> --sha <commit>` (the downloaded archive must be the
  requested commit, then verified backup, candidate selftest on throw-away data, atomic selection); the launcher then runs selftest on the selected code and checks its
  startup, and rolls back by itself when either fails (launcher.py).
- Installer update (requirements, migrations, launcher, bootstrap or the embedded Python changed): the
  channel's published installer is downloaded with the same read-only token, its sha256 is checked against
  the release manifest, a verified backup is taken, and PharmacySetup.exe runs silently; it starts the
  program again by itself (/RELAUNCH=1). A failed install cannot be rolled back automatically (migrations
  may have run); the owner is told, and the backup taken before it is named in the notification.
- After the restart, one notification tells the owner the version and what changed, or why it failed.
  A failed commit is not retried automatically; a newer commit, or the owner's button, tries again.
Settings and state are small JSON files in the data folder (a restore never rolls them back).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import config
from ..db import Database
from . import audit, backup, notify, updater
from .errors import ValidationFailed
from .permissions import Context

log = logging.getLogger("pharm.update")

DEFAULTS = {"auto": True, "channel": "", "check_hours": 4, "quiet_start": 3, "quiet_end": 6, "on_events": False}
CHECK_HOURS = (1, 2, 4, 6, 12, 24)
START_DELAY_S = 60               # let the program finish starting before the first check
STARTUP_WINDOW_S = 15 * 60       # "apply at startup" = within the first minutes after a start
OPEN_SALE_MIN = 60               # a draft invoice with lines started this recently is an open sale
SALE_WRITE_S = 5 * 60            # any sales write this recently means someone is selling
WAITING_RECHECK_S = 30 * 60
SHIFT_CLOSE_KEEP_S = 30 * 60     # a shift close stays an allowed moment this long while a sale cools down      # installer not published yet: look again sooner
STATUS_CACHE_S = 600
INSTALLER_TIMEOUT_S = 15 * 60    # the installer stops the program within seconds; else give the tills back
RESTART = 75                     # launcher.py starts the program again on this exit code

_instance: "AutoUpdater | None" = None


def trigger(reason: str) -> None:
    """Ask for an update attempt soon (after a shift close). Never blocks or raises."""
    inst = _instance
    if inst is not None:
        inst.pending_reason, inst.pending_since = reason, time.monotonic()
        inst.wake.set()


def test_pc_marker() -> Path:
    return Path(os.environ.get("PHARM_TEST_PC_FILE", Path.home() / ".pharmacy" / "test_pc"))


def in_quiet_hours(now: datetime, start: int, end: int) -> bool:
    h = now.hour
    return start <= h < end if start < end else (h >= start or h < end)


class AutoUpdater:
    def __init__(self, db: Database, s: config.Settings, *, root: Path | None = None,
                 repo: str = updater.DEFAULT_REPO, clock=datetime.now, run=None,
                 installer=None, exit_fn=os._exit):
        self.db, self.s = db, s
        self.root = root or Path(__file__).resolve().parents[2]
        self.repo, self.clock, self._exit = repo, clock, exit_fn
        self._run = run or (lambda *a, **k: subprocess.run(*a, **k))
        self._installer = installer or (lambda exe, log_path: updater.run_installer(exe, log_path))
        d = s.data_dir
        self.cfg_path, self.state_path = d / "update_settings.json", d / "update_state.json"
        self.attempt_path, self.outcome_path = d / "update_attempt.json", d / "update_outcome.json"
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stop_event = threading.Event()
        self.pending_reason: str | None = None
        self.pending_since = float("-inf")
        self.restart_due = False
        self.restart_confirmed = False   # the deferred restart came from the owner's button
        self.restarting = False          # about to restart for an update: new sales writes wait a moment
        self.last_sale_write = float("-inf")
        self.checked_once = False
        self.started = time.monotonic()
        self.thread: threading.Thread | None = None

    # ----------------------------------------------------------------- files
    def _read(self, path: Path, default: dict | None = None) -> dict:
        try:
            v = json.loads(path.read_text(encoding="utf-8"))
            return {**(default or {}), **v} if isinstance(v, dict) else dict(default or {})
        except (OSError, ValueError):
            return dict(default or {})

    def _write(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)

    def settings(self) -> dict:
        return self._read(self.cfg_path, DEFAULTS)

    def state(self) -> dict:
        return self._read(self.state_path)

    def _save(self, **changes) -> dict:
        st = {**self.state(), **changes}
        self._write(self.state_path, st)
        return st

    # ----------------------------------------------------------------- channel
    def _build_ref(self) -> str | None:
        """Branch of the running code: the in-app release's channel, else the installer's branch."""
        info = updater.installed(self.s.data_dir)
        if info.get("ref"):
            return info["ref"]
        return self._read(self.s.data_dir / "installed_version.json").get("ref")

    def develop_allowed(self) -> bool:
        return (self._build_ref() == "develop" or self.settings().get("channel") == "develop"
                or test_pc_marker().exists())

    def channel(self) -> str | None:
        """The channel to follow, or None for a review build from another branch (never replaced)."""
        chosen = self.settings().get("channel")
        if chosen in updater.CHANNELS:
            return chosen
        return updater.channel({"ref": self._build_ref()})     # #107 rule: main, develop, or None

    def update_settings(self, ctx: Context, changes: dict) -> dict:
        ctx.require("settings.manage")
        cfg = self.settings()
        new = dict(cfg)
        for k in ("auto", "on_events"):
            if k in changes:
                new[k] = bool(changes[k])
        if "channel" in changes:
            ch = changes["channel"] or ""
            if ch not in ("", *updater.CHANNELS):
                raise ValidationFailed("قناة تحديث غير معروفة")
            if ch == "develop" and not self.develop_allowed():
                raise ValidationFailed("قناة التجربة (develop) لجهاز التجربة فقط، مش لجهاز الصيدلية",
                                       code="test_pc_only")
            new["channel"] = ch
        if "check_hours" in changes:
            if changes["check_hours"] not in CHECK_HOURS:
                raise ValidationFailed("اختر كل 1 أو 2 أو 4 أو 6 أو 12 أو 24 ساعة")
            new["check_hours"] = changes["check_hours"]
        for k in ("quiet_start", "quiet_end"):
            if k in changes:
                v = changes[k]
                if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 23:
                    raise ValidationFailed("الساعة من 0 إلى 23")
                new[k] = v
        if new["quiet_start"] == new["quiet_end"]:
            raise ValidationFailed("بداية وقت الهدوء ونهايته لازم يختلفوا")
        with self.db.transaction():
            self._write(self.cfg_path, new)
            audit.record(self.db, ctx, "update.settings", "system", None,
                         before={k: cfg.get(k) for k in new}, after=new)
        self._save(checked_at=None)           # a new channel is checked right away
        self.wake.set()
        return new

    # ----------------------------------------------------------------- safety
    def touch_sale(self) -> None:
        self.last_sale_write = time.monotonic()

    def busy(self) -> str | None:
        """Why updating now would interrupt a sale, or None."""
        if time.monotonic() - self.last_sale_write < SALE_WRITE_S:
            return "فيه بيع شغال دلوقتي"
        since = (datetime.now(timezone.utc) - timedelta(minutes=OPEN_SALE_MIN)).strftime("%Y-%m-%dT%H:%M:%S")
        orgs = self.db.all("SELECT id FROM organizations WHERE status='active'")
        for o in orgs:
            r = self.db.one("SELECT count(*) n FROM sales s WHERE s.org_id=? AND s.status='draft'"
                            " AND s.created_at>=? AND EXISTS (SELECT 1 FROM sale_lines l"
                            " WHERE l.sale_id=s.id AND l.org_id=?)", (o["id"], since, o["id"]))
            if r["n"]:
                return "فيه فاتورة بيع مفتوحة"
        for o in orgs:
            if self.db.one("SELECT count(*) n FROM cash_shifts WHERE org_id=? AND status='open'", (o["id"],))["n"]:
                return "فيه وردية مفتوحة"
        return None

    def _window(self) -> str | None:
        """Is this a moment the owner allowed for updating? Returns the reason."""
        cfg = self.settings()
        if in_quiet_hours(self.clock(), int(cfg["quiet_start"]), int(cfg["quiet_end"])):
            return "quiet_hours"
        if not cfg.get("on_events"):          # working hours: only the owner's button (his confirmation)
            return None
        if self.pending_reason == "shift_close" and time.monotonic() - self.pending_since < SHIFT_CLOSE_KEEP_S:
            return "shift_close"
        if time.monotonic() - self.started < STARTUP_WINDOW_S:
            return "startup"
        return None

    # ----------------------------------------------------------------- check
    def check(self) -> dict:
        """Ask GitHub what the channel has. Network only; changes nothing but the state file."""
        info = updater.installed(self.s.data_dir)
        cur = info.get("sha")
        ch = self.channel()
        st = self.state()
        plan = {"channel": ch, "installed": cur, "latest": None, "available": False, "kind": None,
                "summary": "", "error": None, "candidate": None, "waiting": None,
                "checked_at": time.time()}
        if ch is None:
            plan["candidate"] = self._build_ref()
            return self._save(**plan)
        try:
            token = updater.read_token()
            latest = updater.latest_sha(self.repo, ch, token)
            plan["latest"] = latest
            plan["available"] = latest != cur
            if plan["available"]:
                diff = updater.compare(self.repo, cur, latest, token)
                plan["summary"] = diff["summary"]
                plan["kind"] = "installer" if updater.needs_installer(diff["files"]) else "code"
                if st.get("latest") == latest and st.get("kind") == "installer":
                    plan["kind"] = "installer"        # `update` already said only the installer can ship it
        except updater.UpdateError as e:
            plan["error"] = str(e)
        return self._save(**plan)

    def _fresh(self, max_age: float) -> dict:
        st = self.state()
        if st.get("checked_at") and time.time() - st["checked_at"] < max_age:
            return st
        return self.check()

    # ----------------------------------------------------------------- apply
    def apply(self, reason: str, ctx: Context | None = None) -> dict:
        """Apply the channel's newest commit if it is safe now. ctx = the owner pressing the button."""
        if not self.lock.acquire(blocking=False):
            return {"ok": False, "message": "فيه تحديث شغال بالفعل"}
        try:
            why = self.busy()
            if why:
                return {"ok": False, "blocked": why, "message": f"التحديث مستني: {why}"}
            if self.restart_due and ctx is not None:     # staged code waiting for its restart: the owner's go
                return self._restart(confirmed=True)
            plan = self.check() if ctx is not None else self._fresh(300)
            if plan.get("error"):
                return {"ok": False, "message": plan["error"]}
            if not plan["available"]:
                return {"ok": True, "updated": False, "message": "البرنامج محدّث"}
            if ctx is None and plan["latest"] == self.state().get("failed_sha"):
                return {"ok": False, "message": "التحديث ده فشل قبل كده؛ مستني نسخة أحدث أو زرار التحديث"}
            if plan["kind"] == "installer":
                return self._apply_installer(plan, reason, ctx)
            return self._apply_code(plan, reason, ctx)
        except updater.UpdateError as e:
            self._save(error=str(e))
            return {"ok": False, "message": str(e)}
        finally:
            self.lock.release()

    def _audit(self, ctx: Context | None, after: dict) -> None:
        with self.db.transaction():
            if ctx is not None:
                audit.record(self.db, ctx, "system.update", "system", None, after=after)
                return
            for o in self.db.all("SELECT id FROM organizations WHERE status='active'"):
                audit.record(self.db, Context(user_id="system", org_id=o["id"], branch_id=None),
                             "system.update", "system", None, after=after)

    def _attempt(self, plan: dict, kind: str, target: str, reason: str, **extra) -> None:
        self._write(self.attempt_path, {"target": target, "kind": kind, "channel": plan["channel"],
                                        "from": plan["installed"], "summary": plan.get("summary", ""),
                                        "reason": reason, "ts": time.time(), **extra})

    def _apply_code(self, plan: dict, reason: str, ctx: Context | None) -> dict:
        proc = self._run([sys.executable, str(self.root / "manage.py"), "update", "--ref", plan["channel"],
                          "--sha", plan["latest"]], cwd=os.getcwd(), capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=900, env={**os.environ, "PYTHONUTF8": "1"})
        output = ((proc.stdout or "") + (proc.stderr or ""))[-3000:]
        if proc.returncode == updater.NEEDS_INSTALLER:
            self._save(kind="installer")
            return self._apply_installer({**plan, "kind": "installer"}, reason, ctx)
        ok = proc.returncode == 0
        self._audit(ctx, {"ok": ok, "kind": "code", "channel": plan["channel"], "target": plan["latest"], "reason": reason,
                          "tail": output[-500:]})
        if not ok:
            self._fail(plan["latest"], "فحص النسخة الجديدة أو النسخة الاحتياطية فشل؛ البرنامج زي ما هو",
                       output[-200:])
            return {"ok": False, "message": "التحديث فشل؛ البرنامج زي ما هو", "output": output}
        self._attempt(plan, "code", plan["latest"], reason)
        return {**self._restart(confirmed=ctx is not None), "output": output}

    def _apply_installer(self, plan: dict, reason: str, ctx: Context | None) -> dict:
        if os.environ.get("PHARM_LAUNCHER") != "1":
            raise updater.UpdateError("التحديث ده محتاج المثبّت، وده بيشتغل في النسخة المثبّتة بس")
        token = updater.read_token()
        rel = updater.installer_release(self.repo, plan["channel"], token)
        target = rel["sha"]
        if target != plan["latest"]:
            # The newest commit has no installer yet. An older installer still helps when everything after
            # it is code only (e.g. a docs merge): install it now, the rest follows as a code update.
            later = updater.compare(self.repo, target, plan["latest"], token)
            if target == plan["installed"] or not later["complete"] or updater.needs_installer(later["files"]):
                self._save(waiting="installer")
                return {"ok": False, "message": "المثبّت الجديد لسه بيتبني على GitHub؛ هيتحدث لوحده لما يخلص"}
        updates = self.s.data_dir / "updates"
        exe = updates / f"PharmacySetup-{target[:12]}.exe"
        if not (exe.is_file() and backup._sha256(exe) == rel["sha256"]):
            got = updater.download_asset(rel["url"], token, exe)
            if got != rel["sha256"]:
                exe.unlink(missing_ok=True)
                raise updater.UpdateError("بصمة المثبّت (sha256) لا تطابق المنشور؛ لم يُثبَّت شيء")
        for old in updates.glob("PharmacySetup-*.exe"):
            if old != exe:
                old.unlink(missing_ok=True)
        if ctx is None and not self._window():           # the download ran past the allowed moment
            return {"ok": False, "blocked": "وقت الشغل بدأ", "message": "التحديث مستني: وقت الشغل بدأ"}
        out = backup.create(self.db, self.s.upload_dir, self.s.backup_path)
        if backup.verify_archive(out)["problems"]:
            raise updater.UpdateError("النسخة الاحتياطية فشلت في التحقق؛ لم يُثبَّت شيء")
        self._audit(ctx, {"ok": True, "kind": "installer", "channel": plan["channel"], "target": target, "reason": reason,
                          "sha256": rel["sha256"], "backup": out.name})
        self.restarting = True
        why = self.busy() or (None if ctx is not None or self._window() else "وقت الشغل بدأ")
        if why:
            self.restarting = False
            return {"ok": False, "blocked": why, "message": f"التحديث مستني: {why}"}
        self._attempt(plan, "installer", target, reason, backup=out.name,
                      log=str(updates / f"install-{target[:12]}.log"))
        try:
            self._installer(exe, updates / f"install-{target[:12]}.log")
        except Exception as e:
            self.restarting = False
            self.attempt_path.unlink(missing_ok=True)
            raise updater.UpdateError(f"تعذّر تشغيل المثبّت: {e}") from e
        t = threading.Timer(INSTALLER_TIMEOUT_S, self._installer_timeout)
        t.daemon = True
        t.start()
        return {"ok": True, "restarting": True, "installer": True,
                "message": "المثبّت شغال؛ البرنامج هيقفل ويفتح لوحده خلال دقيقة أو اتنين"}

    def _installer_timeout(self) -> None:
        """Still running long after the installer was started: it did not install. Give the tills back."""
        self.restarting = False
        self.finish_attempt()

    def _restart(self, confirmed: bool = False) -> dict:
        if os.environ.get("PHARM_LAUNCHER") != "1":
            return {"ok": True, "restarting": False, "message": "اكتمل. أغلق البرنامج وافتحه مرة أخرى"}
        self.restarting = True
        why = self.busy()
        if not why and not confirmed and not self._window():
            why = "وقت الشغل بدأ"            # the check/backup ran past the allowed moment (#99 review)
        if why:                               # a sale started meanwhile: the new code waits for it
            self.restarting = False
            self.restart_due = True
            self.restart_confirmed = self.restart_confirmed or confirmed
            return {"ok": True, "restarting": False, "message": f"النسخة جاهزة ومستنية: {why}"}
        self.restart_due = self.restart_confirmed = False
        t = threading.Timer(1.5, lambda: self._exit(RESTART))
        t.daemon = True
        t.start()
        return {"ok": True, "restarting": True, "message": "يعيد البرنامج تشغيل نفسه…"}

    # ----------------------------------------------------------------- result after the restart
    def _fail(self, target: str, title_reason: str, detail: str = "") -> None:
        self._save(failed_sha=target, last_result={"ok": False, "text": title_reason, "at": time.time()})
        self._notify("error", "التحديث التلقائي لم يكتمل", f"{title_reason} {detail}".strip())

    def _notify(self, severity: str, title: str, body: str) -> None:
        try:
            with self.db.transaction():
                for o in self.db.all("SELECT id FROM organizations WHERE status='active'"):
                    notify.emit(self.db, Context(user_id="system", org_id=o["id"], branch_id=None),
                                "update", severity, title, body, "system", None)
        except Exception:
            log.exception("could not record update notification")

    def finish_attempt(self) -> bool:
        """After the restart: one notification with the version and what changed, or why it failed.
        Returns False while the launcher is still checking a newly selected release."""
        att = self._read(self.attempt_path)
        if not att.get("target"):
            return True
        import release_bootstrap as releases
        try:
            if releases.read_state(releases.code_home(self.s.data_dir)).get("pending"):
                return False
        except releases.ReleaseError:
            pass
        outcome = self._read(self.outcome_path)
        installed = updater.installed(self.s.data_dir).get("sha")
        failed = outcome.get("ok") is False and float(outcome.get("ts") or 0) >= float(att.get("ts") or 0)
        target = att["target"]
        if installed == target and not failed:
            from ..version import __version__
            title = f"تم تحديث البرنامج تلقائيًا: نسخة {__version__} ({target[:7]})"
            body = att.get("summary") or "تحسينات وإصلاحات"
            self._save(last_result={"ok": True, "text": f"{title} — {body}", "at": time.time()}, failed_sha=None)
            self._notify("info", title, body)
        elif att.get("kind") == "installer":
            why = outcome.get("reason") if failed else "المثبّت لم يكمل"
            self._fail(target, f"{why}. البيانات محفوظة، ونسخة احتياطية قبل التحديث: {att.get('backup', '')}",
                       f"السجل: {att.get('log', '')}")
        else:
            why = outcome.get("reason") or "النسخة الجديدة لم تبدأ"
            self._fail(target, f"{why}؛ رجع البرنامج للنسخة السابقة تلقائيًا. البيانات محفوظة")
        self.attempt_path.unlink(missing_ok=True)
        return True

    # ----------------------------------------------------------------- owner view
    def _status(self) -> dict:
        st = self._fresh(STATUS_CACHE_S)
        cfg = self.settings()
        from ..version import __version__
        return {"version": __version__,
                "installed": st.get("installed"), "latest": st.get("latest"),
                "available": bool(st.get("available")), "kind": st.get("kind"), "summary": st.get("summary"),
                "channel": st.get("channel"), "candidate": st.get("candidate"), "error": st.get("error"),
                "waiting": st.get("waiting"), "failed": bool(st.get("latest")) and st.get("latest") == st.get("failed_sha"),
                "last_result": st.get("last_result"), "settings": cfg, "develop_allowed": self.develop_allowed(),
                "busy": self.busy(), "restart_due": self.restart_due,
                "restarts_itself": os.environ.get("PHARM_LAUNCHER") == "1"}

    def status(self) -> dict:
        out = self._status()
        if not out.get("candidate"):
            out.pop("candidate", None)       # #107 API: "candidate" only for a review build with no channel
        return out

    def _announce(self, st: dict, cfg: dict) -> None:
        """Tell the owner once per new version, in Arabic, what will happen (no technical words)."""
        latest = st.get("latest")
        if not (st.get("available") and latest and st.get("channel")) or st.get("announced") == latest \
                or latest == st.get("failed_sha"):
            return
        start, end = f"{int(cfg['quiet_start']):02d}:00", f"{int(cfg['quiet_end']):02d}:00"
        if cfg.get("auto"):
            when = (f"هيتركب لوحده بين الساعة {start} و{end} لو الجهاز شغال ومفيش بيع ولا وردية مفتوحة"
                    + ("، أو بعد قفل الوردية أو أول ما البرنامج يفتح" if cfg.get("on_events") else "")
                    + "، أو دوس «حدّث الآن» من الإعدادات ← البرنامج.")
        else:
            when = "التحديث التلقائي مقفول؛ دوس «حدّث الآن» من الإعدادات ← البرنامج لما تحب."
        body = f"{st.get('summary') or 'تحسينات وإصلاحات'}. {when} بياناتك مش بتتلمس، وبتتاخد نسخة احتياطية الأول."
        self._notify("info", "فيه تحديث جديد للبرنامج", body)
        self._save(announced=latest)

    # ----------------------------------------------------------------- scheduler
    def _check_due(self) -> bool:
        st, cfg = self.state(), self.settings()
        last = st.get("checked_at")
        if not last:
            return True
        every = WAITING_RECHECK_S if st.get("waiting") else int(cfg["check_hours"]) * 3600
        return time.time() - last >= every

    def tick(self) -> dict | None:
        """One scheduler step (the loop calls it every minute)."""
        if self.restart_due:                  # staged code waits for a sale; the launcher can only confirm it
            if self.busy() or not (self.restart_confirmed or self._window()):    # after this restart, so
                return None                                                      # it comes first (#99 review)
            return self._restart(confirmed=self.restart_confirmed)
        if not self.finish_attempt():
            return None
        if self._check_due() or not self.checked_once:        # always once at startup
            self.check()
            self.checked_once = True
        st, cfg = self.state(), self.settings()
        reason = self._window()
        if (cfg.get("auto") and st.get("available") and reason and not st.get("error")
                and not st.get("waiting") and st.get("latest") != st.get("failed_sha")
                and os.environ.get("PHARM_LAUNCHER") == "1"):
            r = self.apply(reason)
            if r.get("blocked"):              # a sale or shift: keep the shift-close moment for a while
                self._announce(self.state(), cfg)
            else:
                self.pending_reason = None
            return r
        self.pending_reason = None
        self._announce(st, cfg)               # not now: tell the owner once when it will happen
        return None

    def _loop(self) -> None:
        self.stop_event.wait(START_DELAY_S)
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:                       # never let the updater take the program down
                log.exception("automatic update step failed")
            self.wake.wait(60)
            self.wake.clear()

    def start(self) -> None:
        global _instance
        _instance = self
        self.thread = threading.Thread(target=self._loop, name="auto-update", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        global _instance
        self.stop_event.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=5)
        _instance = None
