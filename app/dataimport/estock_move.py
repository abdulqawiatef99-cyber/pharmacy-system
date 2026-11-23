"""Local owner-only E-Stock rehearsal, with a checkpoint at the completed read."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from release_bootstrap import ReleaseError, atomic_json, locked

from ..core.errors import Conflict, DomainError, Forbidden, ValidationFailed
from ..core.permissions import Context
from ..db import Database
from . import estock_archive, estock_db

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "windows" / "estock-db-read.ps1"
WORKING = {"finding", "reading", "rehearsing"}
ERRORS = {
    "ESTOCK: AMBIGUOUS": "اتلاقى أكتر من قاعدة بيانات ممكن تكون إي ستوك. حدّد مع الدعم أنهي قاعدة هي الشغالة.",
    "ESTOCK: NOT FOUND": "مقدرناش نلاقي قاعدة إي ستوك على الجهاز. اتأكد إن خدمة قاعدة البيانات شغالة وإن إي ستوك بيفتح.",
}


def supported() -> bool:
    return sys.platform == "win32"


def read_export(folder: Path, progress) -> None:
    """Run the shipped reader and translate only its progress counters for the browser."""
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    command = [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-File", str(SCRIPT), "-Guided", "-OutputDirectory", str(folder)]
    tables = 0
    error = None
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          creationflags=subprocess.CREATE_NO_WINDOW,
                          encoding="utf-8", errors="replace") as process:
        for raw in process.stdout:
            line = raw.strip()
            if line == "ESTOCK: FOUND":
                progress("reading", tables)
            elif line == "ESTOCK: TABLE":
                tables += 1
                progress("reading", tables)
            elif line in ERRORS:
                error = ERRORS[line]
        code = process.wait()
    # A partial read can still produce the owner's report of missing tables.
    expected = {0: "PASS", 4: "PARTIAL"}.get(code)
    if (not expected or estock_db.read_summary(folder)["result"] != expected
            or not (folder / "tables" / "manifest.csv").is_file()):
        raise ValidationFailed(error or "قراية إي ستوك ما كملتش. جرّب تاني بعد ما تتأكد إن إي ستوك شغال.")


def export_fingerprint(folder: Path) -> str:
    """Detect a missing or torn file before reusing an export after restart."""
    summary = estock_db.read_summary(folder)
    if summary["result"] not in estock_db.FINISHED:
        raise estock_db.ExportError("القراية المحفوظة ناقصة. جرّب تقرا إي ستوك من الأول.")
    digest = hashlib.sha256()
    for path in (folder / "summary.txt", folder / "tables" / "manifest.csv"):
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    digest.update(estock_db.Export(folder).source_sha().encode("ascii"))
    return digest.hexdigest()


class EstockMove:
    def __init__(self, db: Database, data_dir: Path):
        self.db = db
        self.root = data_dir / "estock-move"
        self._guard = threading.Lock()
        self._active: tuple[str, str] | None = None
        self._progress = ("finding", 0)

    def _home(self, ctx: Context) -> Path:
        if not ctx.is_owner:
            raise Forbidden("تجربة نقل إي ستوك للمالك بس.")
        ctx.require("settings.manage")
        return self.root / ctx.org_id

    @staticmethod
    def _state(home: Path) -> dict:
        path = home / "state.json"
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"phase": "idle", "tables": 0}
        except (ValueError, UnicodeError):
            raise ValidationFailed("حالة تجربة إي ستوك مش مقروءة.") from None
        if (not isinstance(state, dict) or not re.fullmatch(r"[0-9a-f]{32}", str(state.get("id")))
                or state.get("phase") not in WORKING | {"done", "failed"}):
            raise ValidationFailed("حالة تجربة إي ستوك مش مقروءة.")
        return state

    @staticmethod
    def _completed_report(home: Path, state: dict) -> str | None:
        if state["phase"] != "done":
            return None
        path = home / state["id"] / "preview" / "owner-report.txt"
        try:
            if path.stat().st_size == state["report_size"]:
                return path.read_text(encoding="utf-8-sig")
        except (FileNotFoundError, UnicodeError):
            pass
        return None

    def status(self, ctx: Context) -> dict:
        home = self._home(ctx)
        with self._guard:
            state = self._state(home)
            phase = state["phase"]
            if self._active == (ctx.org_id, state.get("id")) and phase in WORKING:
                state["phase"], state["tables"] = self._progress
            elif phase in WORKING:
                state["phase"] = "interrupted"
        if state["phase"] == "done":
            # A completed report may have been lost on power failure. The export checkpoint can still be used.
            state["report"] = self._completed_report(home, state)
            if state["report"] is None:
                state["phase"] = "interrupted"
        return {**{k: v for k, v in state.items() if k not in {"fingerprint", "report_size"}}, "supported": supported()}

    def start(self, ctx: Context, *, replace_id: str | None = None) -> None:
        home = self._home(ctx)
        if not supported():
            raise ValidationFailed("تجربة نقل إي ستوك متاحة على ويندوز ١٠ و١١.")
        with self._guard:
            if self._active:
                if self._active[0] == ctx.org_id:
                    return
                raise Conflict("فيه تجربة نقل إي ستوك شغالة على الجهاز. جرّب بعد ما تخلص.")
            lock = locked(self.root, name="move.lock")
            try:
                lock.__enter__()
            except ReleaseError:
                raise Conflict("فيه تجربة نقل إي ستوك شغالة على الجهاز. جرّب بعد ما تخلص.") from None
            try:
                previous = self._state(home)
                complete = self._completed_report(home, previous) is not None
                if complete and replace_id != previous["id"]:
                    return
                why = estock_db.refusal(self.db, ctx, "")
                if why:
                    raise Conflict(why, code="estock_db_refused")
                resume = previous["phase"] in {"rehearsing", "done"} and not complete
                fingerprint = previous.get("fingerprint") if resume else None
                run_id = previous["id"] if fingerprint else uuid4().hex
                phase = "rehearsing" if fingerprint else "finding"
                tables = previous["tables"] if fingerprint else 0
                atomic_json(home / "state.json", {"id": run_id, "phase": phase, "tables": tables,
                                                 "fingerprint": fingerprint})
                self._active = (ctx.org_id, run_id)
                self._progress = (phase, tables)
                try:
                    worker = threading.Thread(target=self._run, args=(ctx, home, run_id, fingerprint, lock),
                                              name="estock-move", daemon=True)
                    worker.start()
                except BaseException:
                    self._active = None
                    raise
                lock = None  # the worker owns the filesystem lock until the job ends
            finally:
                if lock is not None:
                    lock.__exit__(None, None, None)

    def _run(self, ctx, home, run_id, fingerprint, lock) -> None:
        work = home / run_id
        export = work / "export"
        preview = work / "preview"
        scratch = preview / "scratch"

        def progress(phase, tables):
            with self._guard:
                self._progress = (phase, tables)

        def checkpoint(phase, **detail):
            with self._guard:
                tables = self._progress[1]
                atomic_json(home / "state.json", {"id": run_id, "phase": phase, "tables": tables,
                                                 "fingerprint": fingerprint, **detail})
                self._progress = (phase, tables)

        try:
            for old in home.iterdir():
                if old != work and re.fullmatch(r"[0-9a-f]{32}", old.name):
                    shutil.rmtree(old, ignore_errors=True)
            if not fingerprint:
                # Each attempt has its own export directory. A leftover reader after a server restart
                # cannot overwrite the new read or make an interrupted export look complete.
                work.mkdir(parents=True, exist_ok=True)
                read_export(export, progress)
                fingerprint = export_fingerprint(export)
            else:
                try:
                    intact = export_fingerprint(export) == fingerprint
                except (OSError, ValueError, DomainError):
                    intact = False
                if not intact:
                    raise ValidationFailed("القراية المحفوظة ناقصة. اضغط «جرّب تاني» عشان تقرا إي ستوك من الأول.")
                shutil.rmtree(preview, ignore_errors=True)
            checkpoint("rehearsing")
            scratch.mkdir(parents=True)
            copy_path = scratch / "copy.db"
            with closing(Database(self.db.path)) as source:
                source.backup_to(copy_path)
            with closing(Database(copy_path)) as copy:
                result = estock_db.run(copy, ctx, export_dir=export, out_dir=preview,
                                       archive_path=scratch / estock_archive.NAME, write_archive=False,
                                       scratch_dir=scratch, rehearsal=True)
            shutil.rmtree(scratch)
            report_path = Path(result["reports"]["owner"])
            with report_path.open("rb+") as report:
                os.fsync(report.fileno())
            checkpoint("done", reconciled=result["ok"], report_size=report_path.stat().st_size)
        except DomainError as exc:
            checkpoint("failed", message=exc.message)
        except Exception:
            # Exceptions from SQL, disk or the importer can include source data and paths.
            checkpoint("failed", message="التجربة ما كملتش. اتأكد إن فيه مساحة فاضية على الجهاز وجرّب تاني.")
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
            with self._guard:
                try:
                    lock.__exit__(None, None, None)
                finally:
                    self._active = None
