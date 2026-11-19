"""Compiled-build evaluation (docs/PROTECTION_PLAN.md, layer 3): measurement only, never a release.

Builds the program as a standalone compiled folder with one tool, then runs the program's own checks
from that folder on temporary data and writes a report (JSON + Markdown):

  python scripts/ci/compiled_build_eval.py build --tool nuitka|pyinstaller --out DIR
  python scripts/ci/compiled_build_eval.py check --tool nuitka|pyinstaller|python --out DIR [--extra-dll DIR]

`--tool python` runs the same checks with the plain interpreter: the baseline the compiled numbers are
compared with. Nothing here signs, publishes or installs anything, and no secret is read.
Started by .github/workflows/compiled-build-eval.yml (manual only); it also runs on Linux for a dry run.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / "scripts" / "ci" / "compiled_eval_entry.py"
NAME = "pharmacy-eval"
TOOLS = ("nuitka", "pyinstaller")
# what the program reads from its own folder at run time (app/db.py, app/api/app.py, release_bootstrap.py)
DATA_DIRS = ("app/web", "app/migrations")
DATA_FILES = ("release_protocol.json",)
# our own source: in a protected build none of it may be readable next to the program
OWN_SOURCES = ("app", "manage.py", "launcher.py", "release_bootstrap.py")


def exe_name() -> str:
    return NAME + (".exe" if os.name == "nt" else "")


def dist_dir(tool: str, out: Path) -> Path:
    """The folder a customer would get (the program and everything it needs)."""
    if tool == "nuitka":
        return out / "nuitka" / (ENTRY.stem + ".dist")
    if tool == "pyinstaller":
        return out / "pyinstaller" / "dist" / NAME
    raise ValueError(f"unknown tool: {tool}")


def program_cmd(tool: str, out: Path) -> list[str]:
    if tool == "python":
        return [sys.executable, str(ENTRY)]
    return [str(dist_dir(tool, out) / exe_name())]


def build_command(tool: str, out: Path) -> list[str]:
    """The exact build command line; kept as data so the test can pin what a build may and may not do."""
    if tool == "nuitka":
        cmd = [sys.executable, "-m", "nuitka", "--standalone", "--assume-yes-for-downloads",
               f"--output-dir={out / 'nuitka'}", f"--output-filename={exe_name()}",
               "--include-package=app", "--nofollow-import-to=tests",
               "--include-package-data=pdfminer", "--lto=no"]
        cmd += [f"--include-data-dir={ROOT / d}={d}" for d in DATA_DIRS]
        cmd += [f"--include-data-files={ROOT / f}={f}" for f in DATA_FILES]
        if os.name == "nt":
            cmd += ["--msvc=latest", "--windows-console-mode=force"]
        return cmd + [str(ENTRY)]
    if tool == "pyinstaller":
        work = out / "pyinstaller"
        cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console",
               "--name", NAME, "--distpath", str(work / "dist"), "--workpath", str(work / "work"),
               "--specpath", str(work), "--paths", str(ROOT),
               "--collect-submodules", "app", "--collect-submodules", "uvicorn"]
        for d in DATA_DIRS:
            cmd += ["--add-data", f"{ROOT / d}{os.pathsep}{d}"]
        for f in DATA_FILES:
            cmd += ["--add-data", f"{ROOT / f}{os.pathsep}."]
        return cmd + [str(ENTRY)]
    raise ValueError(f"unknown tool: {tool}")


def tree_size(path: Path) -> dict:
    files = [p for p in path.rglob("*") if p.is_file()]
    sizes = sorted(((p.stat().st_size, p.relative_to(path).as_posix()) for p in files), reverse=True)
    return {"files": len(files), "bytes": sum(s for s, _ in sizes),
            "largest": [{"file": n, "bytes": s} for s, n in sizes[:8]]}


def compressed_size(path: Path) -> int:
    """Size as an .xz archive: close to what the installer (Inno Setup, lzma2) would add for this folder."""
    with tempfile.TemporaryDirectory() as tmp:
        return Path(shutil.make_archive(str(Path(tmp) / "dist"), "xztar", root_dir=path)).stat().st_size


def source_exposure(dist: Path) -> dict:
    """How much of our own source a customer could simply open: our .py files shipped as they are, our
    compiled bytecode files (.pyc, which public tools turn back into near-source), and whether a comment
    and a docstring taken from the source can be found as plain text inside any shipped file."""
    def ours(rel: Path) -> bool:
        parts = rel.parts[1:] if rel.parts[:1] == ("_internal",) else rel.parts     # PyInstaller's folder
        return bool(parts) and (parts[0] in OWN_SOURCES or (len(parts) == 1 and parts[0] in OWN_SOURCES))
    rels = [p.relative_to(dist) for p in dist.rglob("*") if p.is_file()]
    comment = b"Embedded Windows Python ignores PYTHONPATH"          # a comment in manage.py
    docstring = b"Command line for running and administering"         # the docstring of manage.py
    found = {"comment": False, "docstring": False}
    for rel in rels:
        try:
            blob = (dist / rel).read_bytes()
        except OSError:
            continue
        found["comment"] = found["comment"] or comment in blob
        found["docstring"] = found["docstring"] or docstring in blob
    return {"own_py_files": sum(1 for r in rels if r.suffix == ".py" and ours(r)),
            "own_pyc_files": sum(1 for r in rels if r.suffix == ".pyc" and ours(r)),
            "comment_text_readable": found["comment"], "docstring_text_readable": found["docstring"]}


def pyinstaller_bytecode(dist: Path) -> dict:
    """PyInstaller keeps Python bytecode in an archive inside the program: count our modules in it
    (each one can be taken out and decompiled with public tools). Best effort: the reader is PyInstaller's own."""
    try:
        from PyInstaller.archive.readers import CArchiveReader
        arch = CArchiveReader(str(dist / exe_name()))
        names: list[str] = []
        for name, entry in arch.toc.items():
            if entry[-1] == "z" or name.lower().endswith(".pyz"):
                names += list(arch.open_embedded_archive(name).toc)
        own = [n for n in names if n == "manage" or n == "release_bootstrap" or n.split(".")[0] == "app"]
        return {"modules_in_archive": len(names), "own_modules_extractable": len(own)}
    except Exception as e:  # noqa: BLE001 - informative only
        return {"error": f"{type(e).__name__}: {e}"}


def run(cmd: list[str], env: dict, timeout: int = 600, cwd: Path | None = None) -> dict:
    start = time.perf_counter()
    try:
        p = subprocess.run(cmd, env=env, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        code, text = p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as e:
        code, text = -1, f"TIMEOUT after {timeout}s\n{e.stdout or ''}"
    except OSError as e:
        code, text = -1, f"{type(e).__name__}: {e}"
    return {"exit": code, "seconds": round(time.perf_counter() - start, 2), "output": text[-4000:]}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve_smoke(cmd: list[str], env: dict, cwd: Path, timeout: float = 120) -> dict:
    """Start the real server, wait for /api/health, fetch the start page, stop it."""
    port = free_port()
    log = cwd / "serve.log"
    start = time.perf_counter()
    with open(log, "w", encoding="utf-8") as out:
        p = subprocess.Popen(cmd + ["serve", "--port", str(port)], env=env, cwd=cwd, stdout=out,
                             stderr=subprocess.STDOUT)
        result = {"healthy": False, "seconds_to_healthy": None, "start_page": False}
        try:
            while time.perf_counter() - start < timeout and p.poll() is None:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
                        if r.status == 200 and json.loads(r.read()).get("ok") is True:
                            result.update(healthy=True, seconds_to_healthy=round(time.perf_counter() - start, 2))
                            break
                except (OSError, ValueError):
                    time.sleep(0.2)
            if result["healthy"]:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as r:
                    result["start_page"] = r.status == 200 and b"<html" in r.read().lower()
        except OSError as e:
            result["error"] = f"{type(e).__name__}: {e}"
        finally:
            p.terminate()
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=15)
    if not (result["healthy"] and result["start_page"]):
        result["log_tail"] = log.read_text(encoding="utf-8", errors="replace")[-3000:]
    return result


def defender_scan(path: Path) -> dict:
    """First signal of an antivirus false alarm: Microsoft Defender's own scan of the folder (Windows only).
    A clean result here does not promise a clean result on a customer PC with another antivirus."""
    exe = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Windows Defender" / "MpCmdRun.exe"
    if os.name != "nt" or not exe.is_file():
        return {"ran": False, "why": "no Microsoft Defender on this machine"}
    r = run([str(exe), "-Scan", "-ScanType", "3", "-File", str(path), "-DisableRemediation"], dict(os.environ), 1800)
    return {"ran": True, "exit": r["exit"], "clean": r["exit"] == 0, "seconds": r["seconds"],
            "output": r["output"][-600:]}


def cmd_build(tool: str, out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    cmd = build_command(tool, out)
    print("build:", " ".join(cmd), flush=True)
    start = time.perf_counter()
    code = subprocess.run(cmd, cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONUTF8": "1"}).returncode
    seconds = round(time.perf_counter() - start, 1)
    ok = code == 0 and (dist_dir(tool, out) / exe_name()).is_file()
    (out / f"build-{tool}.json").write_text(json.dumps({"tool": tool, "ok": ok, "exit": code, "seconds": seconds}),
                                           encoding="utf-8")
    print(f"build {tool}: {'OK' if ok else 'FAILED'} in {seconds}s", flush=True)
    return 0 if ok else 1


def cmd_check(tool: str, out: Path, extra_dll: Path | None) -> int:
    report: dict = {"tool": tool, "platform": sys.platform, "python": sys.version.split()[0]}
    build = out / f"build-{tool}.json"
    if build.is_file():
        report["build"] = json.loads(build.read_text(encoding="utf-8"))
    cmd = program_cmd(tool, out)
    if tool != "python":
        dist = dist_dir(tool, out)
        if not Path(cmd[0]).is_file():
            report["error"] = "no compiled program: the build failed"
            return finish(report, out)
        if extra_dll and extra_dll.is_dir():        # FriBiDi for Arabic shaping, as the installer build ships it
            for dll in extra_dll.glob("*.dll"):
                shutil.copy2(dll, dist)
        report["size"] = {**tree_size(dist), "compressed_bytes": compressed_size(dist)}
        report["source_exposure"] = source_exposure(dist)
        if tool == "pyinstaller":
            report["bytecode_archive"] = pyinstaller_bytecode(dist)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        env = {**os.environ, "PHARM_DATA_DIR": str(work / "data"), "PYTHONUTF8": "1", "PYTHONPATH": str(ROOT),
               "PHARM_PRINTER": "none"}
        env.pop("PHARM_RELEASE_CANDIDATE", None)
        probe = run(cmd + ["--eval-probe"], env, cwd=work)
        try:
            report["probe"] = json.loads(probe["output"].strip().splitlines()[-1])
        except (ValueError, IndexError):
            report["probe"] = {"probe": "FAIL: no answer: " + probe["output"][-800:]}
        report["probe_seconds"] = probe["seconds"]
        starts = [run(cmd + ["--help"], env, cwd=work) for _ in range(3)]
        report["start"] = {"first_seconds": starts[0]["seconds"], "best_seconds": min(s["seconds"] for s in starts),
                           "ok": all(s["exit"] == 0 for s in starts)}
        if not report["start"]["ok"]:
            report["start"]["output"] = starts[0]["output"][-1500:]
        steps = {"selftest": ["selftest", "--quick"],
                 "init": ["init", "--pharmacy", "eval", "--username", "owner",
                          "--password", "Eval-" + secrets.token_urlsafe(12)],
                 "verify": ["verify"], "backup": ["backup"]}
        report["commands"] = {}
        for name, args in steps.items():
            r = run(cmd + args, env, cwd=work)
            ok = r["exit"] == 0 and (name != "selftest" or "SELFTEST PASSED" in r["output"])
            report["commands"][name] = {"ok": ok, "seconds": r["seconds"]}
            if not ok:
                report["commands"][name]["output"] = r["output"][-1500:]
        report["serve"] = serve_smoke(cmd, env, work)
    if tool != "python":
        report["antivirus"] = defender_scan(dist_dir(tool, out))
    return finish(report, out)


def passed(report: dict) -> bool:
    return ("error" not in report
            and not any(str(v).startswith("FAIL") for v in report.get("probe", {"x": "FAIL"}).values())
            and report.get("start", {}).get("ok") is True
            and all(c["ok"] for c in report.get("commands", {"x": {"ok": False}}).values())
            and report.get("serve", {}).get("healthy") is True and report["serve"].get("start_page") is True)


def mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB"


def markdown(report: dict) -> str:
    tool = report["tool"]
    lines = [f"## Compiled build evaluation: {tool} ({report['platform']}, Python {report['python']})", "",
             f"**Result: {'PASS' if passed(report) else 'FAIL'}**", ""]
    if "error" in report:
        lines += [report["error"], ""]
    if "build" in report:
        lines.append(f"- Build: {'OK' if report['build']['ok'] else 'FAILED'} in {report['build']['seconds']} s")
    if "size" in report:
        s = report["size"]
        lines.append(f"- Size: {mb(s['bytes'])} in {s['files']} files; about {mb(s['compressed_bytes'])} compressed")
    if "start" in report:
        lines.append(f"- Start (`--help`): first {report['start']['first_seconds']} s, best {report['start']['best_seconds']} s")
    if "serve" in report:
        sv = report["serve"]
        lines.append(f"- Server: healthy={sv['healthy']} after {sv['seconds_to_healthy']} s, start page={sv['start_page']}")
    for name, c in report.get("commands", {}).items():
        lines.append(f"- `{name}`: {'OK' if c['ok'] else 'FAILED'} in {c['seconds']} s")
    if "source_exposure" in report:
        e = report["source_exposure"]
        lines.append(f"- Our source next to the program: {e['own_py_files']} .py, {e['own_pyc_files']} .pyc; "
                     f"comment text readable={e['comment_text_readable']}, docstring text readable={e['docstring_text_readable']}")
    if "bytecode_archive" in report:
        lines.append(f"- Bytecode archive inside the program: {json.dumps(report['bytecode_archive'])}")
    if "antivirus" in report:
        a = report["antivirus"]
        lines.append("- Microsoft Defender scan: " + (("clean" if a["clean"] else f"NOT clean (exit {a['exit']})")
                                                      if a["ran"] else f"not run ({a['why']})"))
    lines += ["", "| Library check | Result |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in report.get("probe", {}).items()]
    failures = [(n, c.get("output", "")) for n, c in report.get("commands", {}).items() if not c["ok"]]
    if report.get("serve", {}).get("log_tail"):
        failures.append(("serve", report["serve"]["log_tail"]))
    if report.get("start", {}).get("output"):
        failures.append(("start", report["start"]["output"]))
    for name, text in failures:
        lines += ["", f"<details><summary>{name}: output</summary>", "", "```", text, "```", "</details>"]
    return "\n".join(lines) + "\n"


def finish(report: dict, out: Path) -> int:
    ok = passed(report)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"report-{report['tool']}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    md = markdown(report)
    (out / f"report-{report['tool']}.md").write_text(md, encoding="utf-8")
    print(md, flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(md + "\n")
    if os.environ.get("GITHUB_ACTIONS") == "true":      # readable through the API even when the log is not
        short = " | ".join(line.lstrip("- ") for line in md.splitlines() if line.startswith(("- ", "**Result")))
        print(f"::{'notice' if ok else 'error'} title=compiled-build-eval {report['tool']}::"
              + short.replace("%", "%25").replace("\r", "").replace("\n", "%0A"))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--tool", choices=TOOLS, required=True)
    b.add_argument("--out", type=Path, required=True)
    c = sub.add_parser("check")
    c.add_argument("--tool", choices=TOOLS + ("python",), required=True)
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("--extra-dll", type=Path, default=None, help="folder with DLLs to put next to the program")
    a = p.parse_args(argv)
    out = a.out.resolve()
    if ROOT == out or ROOT in out.parents:
        p.error("--out must be outside the repository (build output is never committed)")
    return cmd_build(a.tool, out) if a.cmd == "build" else cmd_check(a.tool, out, a.extra_dll)


if __name__ == "__main__":
    sys.exit(main())
