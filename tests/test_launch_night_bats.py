"""Launch-night one-click scripts: launch-fresh-start.bat (clean start) and estock-import.bat (#113).

Windows cmd cannot run here; these tests pin the safety properties statically and run the embedded
Python file picker for real."""
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRESH = ROOT / "scripts" / "windows" / "launch-fresh-start.bat"
IMPORT = ROOT / "scripts" / "windows" / "estock-import.bat"
OWN_FILTER = "$d=('%INST%\\python\\').ToLower()"


def code_lines(text):
    return [l.strip() for l in text.splitlines() if l.strip() and not l.strip().upper().startswith("REM")]


def first(lines, needle):
    return next(i for i, l in enumerate(lines) if needle in l)


class Common:
    BAT = None

    def setUp(self):
        self.raw = self.BAT.read_bytes()
        self.text = self.raw.decode("ascii")       # cmd reads the file in the console code page: ASCII only
        self.lines = code_lines(self.text)

    def test_crlf_line_ends(self):
        # cmd.exe mis-reads labels and goto in LF-only batch files
        self.assertNotIn(b"\n", self.raw.replace(b"\r\n", b""))
        self.assertTrue(self.raw.endswith(b"\r\n"))

    def test_no_delayed_expansion(self):
        # the embedded Python uses "!=": delayed expansion would eat the "!"
        self.assertNotIn("EnableDelayedExpansion", self.text)

    def test_stops_only_its_own_python_processes(self):
        for bad in ("taskkill", "Stop-Process -Name", "Get-Process", "wmic", "tskill"):
            self.assertNotIn(bad.lower(), self.text.lower(), bad)
        kills = [l for l in self.lines if "Stop-Process" in l]
        self.assertEqual(len(kills), 1)
        k = kills[0]
        self.assertIn(OWN_FILTER, k)
        self.assertIn("$_.ExecutablePath.ToLower().StartsWith($d)", k)
        self.assertIn("$_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe'", k)
        self.assertIn("Stop-Process -Id $p.ProcessId", k)
        # the one stop routine is a subroutine; nothing else kills
        self.assertIn(":stopprog", self.lines)

    def test_every_failure_goes_to_fail_and_is_logged(self):
        main = self.lines.index(":main") if ":main" in self.lines else 0
        for l in self.lines[main:self.lines.index(":fail")]:
            if "errorlevel" in l.lower() and l.lower().startswith("if ") and "goto" in l:
                self.assertRegex(l, r'set "MSG=.+" & goto fail\)$')
        self.assertIn('call :log FAIL "%MSG%"', self.lines)
        self.assertIn('>> "%LOG%" echo %DATE% %TIME% %~1 - %~2', self.lines)

    def test_manage_commands_and_flags_exist(self):
        src = (ROOT / "manage.py").read_text(encoding="utf-8")
        for cmd in re.findall(r'"%MANAGE%" ([a-z-]+)', self.text):
            self.assertIn(f'sub.add_parser("{cmd}"', src)
        for flag in set(re.findall(r" (--[a-z-]+)", " ".join(l for l in self.lines if "%MANAGE%" in l))):
            self.assertIn(f'"{flag}"', src, flag)

    def test_program_started_with_the_installed_launcher(self):
        self.assertIn('set "INST=%LOCALAPPDATA%\\Programs\\PharmacyPlatform"', self.lines)
        self.assertIn('start "" /d "%INST%\\program" "%PYW%" "%INST%\\program\\launcher.py"', self.text)
        self.assertTrue((ROOT / "launcher.py").is_file())


class FreshStartBat(Common, unittest.TestCase):
    BAT = FRESH

    def test_runs_from_a_temp_copy_outside_the_data_folder(self):
        # an update puts this file inside data\code-releases, which the script renames
        at_copy = first(self.lines, 'copy /y "%~f0" "%TEMP%\\pharm-launch-fresh-start.bat"')
        at_main = self.lines.index(":main")
        self.assertLess(at_copy, at_main)
        self.assertEqual(self.lines[self.lines.index(":main") + 1], 'cd /d "%TEMP%"')

    def test_step_order_stop_backup_verified_then_rename(self):
        L = self.lines
        stop = first(L, "call :stopprog")
        backup = first(L, '"%MANAGE%" backup --dest')
        verify = first(L, '"%MANAGE%" verify "%ZIP%"')
        preflight = first(L, '"%MANAGE%" preflight "%ZIP%"')
        dcopy = first(L, 'copy /y "%ZIP%" "D:\\PharmacyPlatform-backups\\"')
        dverify = first(L, '"%MANAGE%" verify "%DZIP%"')
        rename = first(L, 'ren "%DATA%" "%TRIAL%"')
        start = first(L, 'start "" /d')
        self.assertLess(first(L, 'set /p "ANS=Type YES'), stop)
        self.assertTrue(stop < backup < verify < preflight < dcopy < dverify < rename < start)
        for at in (backup, verify, preflight, dverify):
            self.assertIn("errorlevel 1", L[at + 1].lower())
        self.assertIn('set "RENAMED=1"', L[rename:rename + 5])

    def test_rename_never_delete(self):
        low = self.text.lower()
        for bad in ("rmdir", "rd /s", "del ", "erase ", "remove-item", "move /y", "/mir", "/purge", "/move"):
            self.assertNotIn(bad, low, bad)
        self.assertEqual(len([l for l in self.lines if l.startswith('ren "%DATA%"')]), 2)  # rename + aside on fail
        self.assertIn('set "TRIAL=data-trial-%STAMP%"', self.lines)
        # a failure after the rename puts the old folder back, also by renaming
        fail = self.lines.index(":fail")
        self.assertIn('ren "%HOMEDIR%\\%TRIAL%" data', self.lines[fail:])
        self.assertIn('if not "%RENAMED%"=="1" goto failstart', self.lines[fail:])
        # the new folder may not exist (mkdir failed): the old one is put back anyway (Codex P2 on #317)
        aside = self.lines.index('ren "%DATA%" "data-failed-%STAMP%"')
        self.assertEqual(self.lines[aside - 1], 'if not exist "%DATA%" goto putback')
        self.assertLess(aside, self.lines.index(":putback"))
        self.assertLess(self.lines.index(":putback"), self.lines.index('ren "%HOMEDIR%\\%TRIAL%" data'))

    def test_same_program_version_kept_and_setup_screen_checked(self):
        L = self.lines
        rename = first(L, 'ren "%DATA%" "%TRIAL%"')
        self.assertLess(first(L, 'pharm-code-before.txt'), rename)
        self.assertLess(rename, first(L, 'robocopy "%HOMEDIR%\\%TRIAL%\\code-releases" "%DATA%\\code-releases" /E'))
        self.assertLess(rename, first(L, 'fc "%TEMP%\\pharm-code-before.txt" "%TEMP%\\pharm-code-after.txt"'))
        self.assertIn("http://127.0.0.1:8000/api/setup", self.text)
        self.assertIn("$s.needed -ne $true", self.text)
        api = (ROOT / "app" / "api" / "app.py").read_text(encoding="utf-8")
        self.assertIn('Route("/api/setup", setup_status)', api)
        self.assertIn('return J({"needed": _setup_needed()})', api)
        # the pharmacy database itself is not copied into the new folder
        copied = [l for l in L[rename:] if l.startswith(("copy ", "for %%n", "robocopy"))]
        self.assertFalse([l for l in copied if "pharmacy.db" in l or "uploads" in l])

    def test_release_check_snippet_runs(self):
        snippet = re.search(r'"%PY%" -I -c "([^"]+)" "%INST%\\program" "%DATA%\\code-releases" > "%TEMP%\\pharm-code-before',
                            self.text).group(1)
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run([sys.executable, "-I", "-c", snippet, str(ROOT), str(Path(tmp) / "code-releases")],
                               capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), ROOT.name)    # no release selected: the installed program

    def test_log_file(self):
        self.assertIn('set "OUT=%USERPROFILE%\\Documents\\launch-night"', self.lines)
        self.assertIn('set "LOG=%OUT%\\fresh-start.txt"', self.lines)


class EstockImportBat(Common, unittest.TestCase):
    BAT = IMPORT

    def picker(self):
        return re.search(r'"%PY%" -I -c "([^"]+)" "%SRC_DIR%" "%WORK%"', self.text).group(1)

    def run_picker(self, names):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp) / "estock", Path(tmp) / "work"
            src.mkdir()
            for n in names:
                (src / n).write_bytes(n.encode("utf-8"))
            r = subprocess.run([sys.executable, "-I", "-c", self.picker(), str(src), str(out)],
                               capture_output=True, text=True)
            got = {p.name: p.read_bytes().decode("utf-8") for p in out.iterdir()} if out.exists() else {}
        return r, got

    def test_picker_finds_the_estock_report_names(self):
        names = ["قائمة الأصناف.xlsx", "كميات الأصناف طبقا لتواريخ الصلاحية.xlsx", "عدد_الوحدات_نهائي.xlsx",
                 "حركة بيع الأصناف.xlsx", "~$قائمة الأصناف.xlsx"]
        r, got = self.run_picker(names)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(got, {"catalog.xlsx": names[0], "stock.xlsx": names[1], "packs.xlsx": names[2]})

    def test_picker_packs_optional_and_ascii_names(self):
        r, got = self.run_picker(["دليل الأصناف.xlsx", "كميات الأصناف طبقًا لتواريخ الصلاحية.xlsx"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(set(got), {"catalog.xlsx", "stock.xlsx"})
        r, got = self.run_picker(["catalog.xlsx", "stock.xlsx", "packs.json"])
        self.assertEqual(r.returncode, 0)
        self.assertEqual(set(got), {"catalog.xlsx", "stock.xlsx", "packs.json"})

    def test_picker_refuses_missing_or_ambiguous(self):
        for names in (["قائمة الأصناف.xlsx"],
                      ["قائمة الأصناف.xlsx", "قائمة الأصناف (2).xlsx", "stock.xlsx"],
                      ["catalog.xlsx", "stock.xlsx", "packs.xlsx", "عدد_الوحدات.json"],
                      ["catalog.json", "stock.xlsx"]):
            r, got = self.run_picker(names)
            self.assertNotEqual(r.returncode, 0, names)
            self.assertEqual(got, {}, names)

    def test_step_order_dry_run_confirm_apply_check(self):
        L = self.lines
        stop = first(L, "call :stopprog")
        runs = [i for i, l in enumerate(L) if '"%MANAGE%" import-estock' in l]
        self.assertEqual(len(runs), 2)
        dry, apply = runs
        self.assertNotIn("--apply", L[dry])
        self.assertIn('> "%DRY%" 2>&1', L[dry])
        self.assertTrue(L[apply].endswith('--apply > "%APPLIED%" 2>&1'))
        confirm = first(L, 'set /p "ANS=Type YES to apply')
        check = first(L, '"%MANAGE%" verify')
        self.assertTrue(stop < dry < confirm < apply < check)
        self.assertIn('if /i not "%ANS%"=="YES"', L[confirm + 1])
        for at in (dry, apply):
            block = L[at:at + 7]
            self.assertIn('if not "%RC%"=="0"', " ".join(block))
            self.assertTrue(any(l.startswith('findstr /c:"FAILED"') for l in block))
        self.assertIn("--accept-zero-cost", L[dry])
        self.assertEqual(L[dry].replace(' > "%DRY%" 2>&1', ""), L[apply].replace(' --apply > "%APPLIED%" 2>&1', ""))

    def test_apply_takes_its_own_verified_backup(self):
        src = (ROOT / "manage.py").read_text(encoding="utf-8")
        body = src.split("def cmd_import_estock", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("backup.create(", body)
        self.assertIn('backup.verify_archive(out)["problems"]', body)
        self.assertIn("'OK' if st['reconciliation']['ok'] else 'FAILED'", body)

    def test_output_files(self):
        self.assertIn('set "SRC_DIR=C:\\estock"', self.lines)
        self.assertIn('set "DRY=%OUT%\\import-dry-run.txt"', self.lines)
        self.assertIn('set "LOG=%OUT%\\estock-import.txt"', self.lines)


if __name__ == "__main__":
    unittest.main()
