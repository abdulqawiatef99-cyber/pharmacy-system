"""Compiled-build evaluation (docs/PROTECTION_PLAN.md, layer 3): the workflow and its script are a
measurement only. These tests pin what they may never do (run by themselves, publish, read a secret,
write into the repository) and check the script's own arithmetic on small made-up folders.
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "compiled-build-eval.yml"

sys.path.insert(0, str(ROOT / "scripts" / "ci"))
import compiled_build_eval as ev  # noqa: E402
import compiled_eval_entry as entry  # noqa: E402


class TestWorkflow(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.code = "\n".join(line for line in self.text.splitlines() if not line.lstrip().startswith("#"))

    def test_it_starts_by_hand_only(self):
        triggers = self.code[self.code.index("\non:"):self.code.index("\npermissions:")]
        self.assertIn("workflow_dispatch:", triggers)
        for event in ("push", "pull_request", "schedule", "workflow_run", "release", "issue_comment"):
            self.assertNotRegex(triggers, rf"(?m)^\s*{event}\b", event)

    def test_it_cannot_write_publish_or_read_a_secret(self):
        self.assertRegex(self.code, r"(?m)^permissions:\n  contents: read\n")
        self.assertEqual(self.code.count("permissions:"), 1)          # no job widens it
        self.assertNotIn("secrets.", self.code)
        self.assertNotIn("GH_TOKEN", self.code)
        self.assertNotIn("gh release", self.code)
        self.assertNotIn("ISCC", self.code)                           # never builds an installer
        self.assertIn("persist-credentials: false", self.code)

    def test_only_the_report_is_uploaded_never_the_program(self):
        upload = self.code[self.code.index("actions/upload-artifact"):]
        paths = re.findall(r"(?m)^\s+(\$\{\{ runner\.temp \}\}\S+)$", upload)
        self.assertEqual(len(paths), 2)
        for p in paths:
            self.assertRegex(p, r"report-\*\.(json|md)$")
        self.assertIn("retention-days: 14", upload)

    def test_runner_and_tools_are_pinned_and_requirements_are_not_changed(self):
        self.assertIn("runs-on: windows-2025", self.code)
        self.assertIn("timeout-minutes:", self.code)
        self.assertRegex(self.code, r"pip install nuitka==\d+(\.\d+)+")
        self.assertRegex(self.code, r"pip install pyinstaller==\d+(\.\d+)+ pyinstaller-hooks-contrib==\d+\.\d+")
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        for tool in ("nuitka", "pyinstaller"):
            self.assertNotIn(tool, requirements)                      # build tools never ship to the pharmacy

    def test_the_baseline_and_the_report_step_always_have_something_to_say(self):
        self.assertIn("check --tool python", self.code)
        check = self.code[self.code.index("Checks from the compiled folder"):]
        self.assertIn("if: ${{ !cancelled() }}", check.split("shell:")[0])


class TestBuildCommands(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.gettempdir()) / "eval-out"

    def test_both_tools_build_a_folder_from_the_evaluation_entry_with_the_data_the_program_reads(self):
        for tool in ev.TOOLS:
            cmd = ev.build_command(tool, self.out)
            joined = " ".join(cmd)
            self.assertEqual(cmd[-1], str(ev.ENTRY), tool)
            self.assertEqual(cmd[0], sys.executable, tool)            # the pinned tool of this Python, as a module
            for data in ("app/web", "app/migrations", "release_protocol.json"):
                self.assertIn(data, joined.replace("\\", "/"), f"{tool}: {data}")
            self.assertIn(str(self.out), joined, tool)                # output goes outside the repository
            self.assertNotIn("onefile", joined, tool)                 # one-file programs unpack on every start
        self.assertIn("--standalone", ev.build_command("nuitka", self.out))
        self.assertIn("--onedir", ev.build_command("pyinstaller", self.out))

    def test_the_data_the_program_reads_exists(self):
        for d in ev.DATA_DIRS:
            self.assertTrue((ROOT / d).is_dir(), d)
        for f in ev.DATA_FILES:
            self.assertTrue((ROOT / f).is_file(), f)

    def test_unknown_tool_is_refused(self):
        with self.assertRaises(ValueError):
            ev.build_command("py2exe", self.out)
        with self.assertRaises(ValueError):
            ev.dist_dir("python", self.out)

    def test_output_inside_the_repository_is_refused(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            ev.main(["build", "--tool", "nuitka", "--out", str(ROOT / "build-eval")])
        self.assertFalse((ROOT / "build-eval").exists())


class TestMeasurements(unittest.TestCase):
    def test_tree_size_counts_files_and_bytes_and_lists_the_largest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "sub").mkdir()
            (d / "a.bin").write_bytes(b"x" * 10)
            (d / "sub" / "b.bin").write_bytes(b"x" * 30)
            size = ev.tree_size(d)
            self.assertEqual((size["files"], size["bytes"]), (2, 40))
            self.assertEqual(size["largest"][0], {"file": "sub/b.bin", "bytes": 30})
            self.assertGreater(ev.compressed_size(d), 0)

    def test_source_exposure_sees_our_files_and_our_text_but_not_library_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "app" / "sales").mkdir(parents=True)
            (d / "_internal" / "app").mkdir(parents=True)
            (d / "openpyxl").mkdir()
            (d / "app" / "sales" / "service.py").write_text("x = 1\n", encoding="utf-8")
            (d / "_internal" / "app" / "db.pyc").write_bytes(b"\x00")
            (d / "_internal" / "manage.py").write_text(
                "# Embedded Windows Python ignores PYTHONPATH; choose code before importing app.\n", encoding="utf-8")
            (d / "openpyxl" / "cell.py").write_text("y = 2\n", encoding="utf-8")          # a library: not ours
            e = ev.source_exposure(d)
            self.assertEqual((e["own_py_files"], e["own_pyc_files"]), (2, 1))
            self.assertTrue(e["comment_text_readable"])
            self.assertFalse(e["docstring_text_readable"])

    def test_the_text_markers_really_are_in_the_source(self):
        """If manage.py is reworded the markers must follow, or "not readable" would mean nothing."""
        src = (ROOT / "manage.py").read_bytes()
        self.assertIn(b"Embedded Windows Python ignores PYTHONPATH", src)
        self.assertIn(b"Command line for running and administering", src)

    def test_clean_folder_has_no_exposure(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "pharmacy-eval.bin").write_bytes(b"\x7fELF...")
            e = ev.source_exposure(d)
            self.assertEqual(e, {"own_py_files": 0, "own_pyc_files": 0, "comment_text_readable": False,
                                 "docstring_text_readable": False})


def _good_report() -> dict:
    return {"tool": "nuitka", "platform": "win32", "python": "3.13.9",
            "build": {"ok": True, "seconds": 900.0},
            "size": {"files": 120, "bytes": 90 * 1024 * 1024, "compressed_bytes": 30 * 1024 * 1024, "largest": []},
            "probe": {"sqlite": "OK 3.50", "arabic_receipt(pillow)": "WARN: raqm/fribidi missing"},
            "start": {"first_seconds": 1.2, "best_seconds": 0.4, "ok": True},
            "commands": {"selftest": {"ok": True, "seconds": 2.0}, "init": {"ok": True, "seconds": 1.0}},
            "serve": {"healthy": True, "seconds_to_healthy": 1.5, "start_page": True},
            "source_exposure": {"own_py_files": 0, "own_pyc_files": 0, "comment_text_readable": False,
                                "docstring_text_readable": True},
            "antivirus": {"ran": True, "exit": 0, "clean": True}}


class TestReport(unittest.TestCase):
    def test_a_complete_run_passes_and_a_warning_is_not_a_failure(self):
        report = _good_report()
        self.assertTrue(ev.passed(report))
        md = ev.markdown(report)
        self.assertIn("**Result: PASS**", md)
        self.assertIn("90.0 MB in 120 files; about 30.0 MB compressed", md)
        self.assertIn("Microsoft Defender scan: clean", md)

    def test_each_kind_of_failure_fails_the_run(self):
        breakers = {
            "library": lambda r: r["probe"].update({"pdf(pdfplumber)": "FAIL: ImportError: x"}),
            "command": lambda r: r["commands"]["selftest"].update(ok=False, output="SELFTEST FAILED"),
            "start": lambda r: r["start"].update(ok=False),
            "server": lambda r: r["serve"].update(healthy=False),
            "start page": lambda r: r["serve"].update(start_page=False),
            "no build": lambda r: r.update(error="no compiled program: the build failed"),
            "no probe": lambda r: r.pop("probe"),
            "no commands": lambda r: r.pop("commands"),
        }
        for name, breaker in breakers.items():
            report = _good_report()
            breaker(report)
            self.assertFalse(ev.passed(report), name)
            self.assertIn("**Result: FAIL**", ev.markdown(report), name)

    def test_finish_writes_both_files_and_returns_the_result(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as out:
            env = {k: os.environ.pop(k) for k in ("GITHUB_STEP_SUMMARY", "GITHUB_ACTIONS") if k in os.environ}
            try:
                os.environ["GITHUB_ACTIONS"] = "true"
                os.environ["GITHUB_STEP_SUMMARY"] = str(Path(tmp) / "summary.md")
                self.assertEqual(ev.finish(_good_report(), Path(tmp)), 0)
                bad = _good_report()
                bad["serve"]["healthy"] = False
                self.assertEqual(ev.finish(bad, Path(tmp)), 1)
            finally:
                os.environ.pop("GITHUB_ACTIONS", None)
                os.environ.pop("GITHUB_STEP_SUMMARY", None)
                os.environ.update(env)
            self.assertEqual(json.loads((Path(tmp) / "report-nuitka.json").read_text(encoding="utf-8"))["tool"], "nuitka")
            self.assertIn("Result: FAIL", (Path(tmp) / "report-nuitka.md").read_text(encoding="utf-8"))
            self.assertIn("Result: PASS", (Path(tmp) / "summary.md").read_text(encoding="utf-8"))
            self.assertIn("::notice title=compiled-build-eval nuitka::", out.getvalue())
            self.assertIn("::error title=compiled-build-eval nuitka::", out.getvalue())


class TestEntry(unittest.TestCase):
    def test_the_hand_written_pdf_is_read_back(self):
        import pdfplumber
        with pdfplumber.open(io.BytesIO(entry._tiny_pdf("PROBE 12345"))) as doc:
            self.assertIn("PROBE 12345", doc.pages[0].extract_text())

    def test_probe_reports_every_library_and_never_raises(self):
        result = entry.probe()
        for name in ("sqlite", "web_files", "migrations", "excel(openpyxl)", "pdf(pdfplumber)", "pdf(pypdfium2)",
                     "arabic_receipt(pillow)", "signature(cryptography)", "tls(certifi)", "server(uvicorn)",
                     "windows_printing(pywin32)"):
            self.assertIn(name, result)
            self.assertFalse(result[name].startswith("FAIL"), f"{name}: {result[name]}")

    def test_other_command_lines_go_to_the_program_unchanged(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = subprocess.run([sys.executable, str(ev.ENTRY), "selftest", "--quick"], capture_output=True, text=True,
                               encoding="utf-8", cwd=tmp,
                               env={**os.environ, "PHARM_DATA_DIR": str(Path(tmp) / "data"), "PYTHONUTF8": "1",
                                    "PYTHONPATH": str(ROOT), "PHARM_PRINTER": "none"})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("SELFTEST PASSED", r.stdout)

    def test_the_entry_is_not_part_of_the_program(self):
        """Nothing under app/ or the launcher may import the evaluation files."""
        for p in [*ROOT.glob("app/**/*.py"), ROOT / "manage.py", ROOT / "launcher.py", ROOT / "release_bootstrap.py"]:
            self.assertNotIn("compiled_eval", p.read_text(encoding="utf-8"), p.name)


if __name__ == "__main__":
    unittest.main()
