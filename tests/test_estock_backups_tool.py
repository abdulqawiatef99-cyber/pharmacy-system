"""«estock-backups»: the owner's read-only look at E-Stock's own backups (manager batch 19/11, item 1).

Windows cmd cannot run here, so the launcher is pinned statically; the Python it runs is exercised for real on
a made-up folder tree. The properties that matter: it only reads, the list is newest first with date and size,
and a newest backup older than 24 hours is said clearly. Synthetic files only."""
import ast
import importlib.util
import io
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.core import tools_folder as tf

ROOT = Path(__file__).resolve().parents[1]
BAT = ROOT / "scripts" / "windows" / "estock-backups.bat"
PY = ROOT / "scripts" / "windows" / "estock-backups.py"
NOW = 1_800_000_000.0                      # a fixed "now" for every age below
HOUR = 3600.0


def load():
    spec = importlib.util.spec_from_file_location("estock_backups_tool", PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestLauncher(unittest.TestCase):
    def setUp(self):
        self.raw = BAT.read_bytes()
        self.text = self.raw.decode("ascii")                    # cmd reads the console code page: ASCII only
        self.code = [l.strip() for l in self.text.splitlines()
                     if l.strip() and not l.strip().upper().startswith("REM")]

    def test_crlf_line_ends(self):
        self.assertNotIn(b"\n", self.raw.replace(b"\r\n", b""))
        self.assertTrue(self.raw.endswith(b"\r\n"))

    def test_it_writes_nothing_and_stops_nothing(self):
        for line in self.code:
            for m in re.finditer(r">>?\s*(\S+)", line):
                self.assertEqual(m.group(1).lower(), "nul", line)         # the only redirection is to nul
        words = set(re.findall(r"[a-z\-]+", " ".join(self.code).lower()))
        for bad in ("del", "erase", "copy", "xcopy", "robocopy", "move", "ren", "rename", "mkdir", "md", "rd", "rmdir",
                    "taskkill", "stop-process", "powershell", "sqlcmd", "osql", "bcp", "reg", "sc", "net", "attrib",
                    "format", "start", "curl", "certutil"):
            self.assertNotIn(bad, words, bad)

    def test_it_runs_the_programs_own_python_on_its_sibling_isolated(self):
        self.assertIn(r'set "INST=%LOCALAPPDATA%\Programs\PharmacyPlatform"', self.code)
        self.assertIn(r'set "PY=%INST%\python\python.exe"', self.code)
        runs = [l for l in self.code if l.startswith('"%PY%"')]
        # -I: nothing beside the script or in the environment is imported; -X utf8: Arabic file names print
        self.assertEqual(runs, ['"%PY%" -I -X utf8 "%~dp0estock-backups.py"'])

    def test_no_delayed_expansion_and_no_block_that_a_path_could_break(self):
        self.assertNotIn("EnableDelayedExpansion", self.text)
        for line in self.code:                                   # a ")" in a user name would end a ( ... ) block
            if "%PY%" in line or "%INST%" in line:
                self.assertFalse(line.lower().startswith("if") and "(" in line.split('"')[-1], line)

    def test_every_path_is_expanded_inside_quotes(self):
        """A folder name with "&" in it would otherwise be run as a command (Codex P1 on #369)."""
        for line in self.code:
            outside = re.sub(r'"[^"]*"', "", line)
            for var in ("%PY%", "%INST%", "%~dp0", "%LOCALAPPDATA%", "%TEMP%", "%%~fi"):
                self.assertNotIn(var, outside, line)

    def test_the_window_stays_open_and_the_code_is_explained(self):
        self.assertIn("pause >nul", self.code)
        self.assertTrue(any("0 = fresh backup" in l and "1 = older than 24 hours" in l for l in self.code))

    def test_it_is_in_the_tools_folder_with_its_script_and_a_line_in_the_read_me(self):
        self.assertIn("estock-backups.bat", tf.TOOLS)
        self.assertIn("estock-backups.py", tf.TOOLS)
        self.assertIn("estock-backups.bat", tf.README_TEXT)


class TestScriptOnlyReads(unittest.TestCase):
    def setUp(self):
        self.src = PY.read_text(encoding="utf-8")
        self.tree = ast.parse(self.src)

    def test_standard_library_only(self):
        mods = set()
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                mods.add((n.module or "").split(".")[0])
        self.assertLessEqual(mods, {"__future__", "os", "sys", "time", "datetime", "winreg", "ctypes"}, mods)

    def test_no_call_that_writes_runs_or_connects(self):
        calls = set()
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Call):
                f = n.func
                calls.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
        for bad in ("remove", "unlink", "rename", "replace", "rmdir", "mkdir", "makedirs", "write", "write_text",
                    "write_bytes", "truncate", "utime", "chmod", "system", "popen", "Popen", "run", "startfile",
                    "connect", "urlopen", "socket", "SetValueEx", "CreateKey", "DeleteKey"):
            self.assertNotIn(bad, calls, bad)
        opens = [n for n in ast.walk(self.tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "open"]
        self.assertEqual(len(opens), 1)                           # the 8-byte look at each file
        self.assertEqual(ast.literal_eval(opens[0].args[1]), "rb")


class TestListing(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.tmp = Path(tempfile.mkdtemp(prefix="pharm-bak-"))
        self.d, self.desk = self.tmp / "D", self.tmp / "Desktop"
        self.d.mkdir()
        self.desk.mkdir()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def bak(self, where: Path, name: str, age_hours: float, size: int = 2048, head: bytes = b"TAPE\x00\x00\x03\x00"):
        where.mkdir(parents=True, exist_ok=True)
        f = where / name
        f.write_bytes(head + b"\x00" * max(0, size - len(head)))
        t = NOW - age_hours * HOUR
        os.utime(f, (t, t))
        return str(f)

    def run_tool(self, *roots):
        out = io.StringIO()
        code = self.m.main(["--no-box", *[x for r in (roots or (self.d, self.desk)) for x in ("--root", str(r))]],
                           now=NOW, out=out)
        return code, out.getvalue()

    def state(self):
        return sorted((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in self.tmp.rglob("*"))

    def test_newest_first_with_date_size_and_a_fresh_newest_is_ok(self):
        old = self.bak(self.d / "Backup", "stock_monday.bak", 50, size=3 * 1024 * 1024)
        new = self.bak(self.d / "Backup", "stock_today.BAK", 2, size=5 * 1024 * 1024)       # upper-case extension
        mid = self.bak(self.desk, "copy.bak", 30)
        (self.d / "Backup" / "notes.txt").write_text("not a backup")
        code, text = self.run_tool()
        self.assertEqual(code, 0)
        rows = [l for l in text.splitlines() if l.rstrip().endswith((".bak", ".BAK")) and l[:4].isdigit()]
        self.assertEqual([r.split("  ")[-1].strip() for r in rows], [new, mid, old])
        self.assertIn("5.0 MB", rows[0])
        self.assertIn("2.0 h", rows[0])
        self.assertRegex(rows[0], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}  ")
        self.assertIn(".bak files found: 3 (SQL Server backups: 3)", text)
        result = [l for l in text.splitlines() if l.startswith("RESULT:")]
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].startswith("RESULT: OK - the newest SQL Server backup is"), result[0])
        self.assertIn(new, result[0])

    def test_a_newest_backup_older_than_24_hours_is_said_clearly(self):
        self.bak(self.d, "stock.bak", 24.5)
        self.bak(self.d, "older.bak", 200)
        code, text = self.run_tool()
        self.assertEqual(code, 1)
        result = next(l for l in text.splitlines() if l.startswith("RESULT:"))
        self.assertIn("WARNING", result)
        self.assertIn("OLDER THAN 24 HOURS", result)
        self.assertIn("hourly backup is probably not running", result)
        v = self.m.verdict(self.m.scan([str(self.d)])[0], NOW)
        box = self.m.arabic_message(v)
        self.assertIn("تحذير", box)
        self.assertIn("24 ساعة", box)

    def test_exactly_24_hours_is_still_fresh_and_a_minute_more_is_not(self):
        self.bak(self.d, "edge.bak", 24)
        self.assertEqual(self.run_tool()[0], 0)
        self.bak(self.d, "edge.bak", 24 + 1 / 60)
        self.assertEqual(self.run_tool()[0], 1)

    def test_no_backup_at_all_is_its_own_answer(self):
        (self.d / "report.xlsx").write_bytes(b"x")
        code, text = self.run_tool()
        self.assertEqual(code, 2)
        self.assertIn("RESULT: NONE - no .bak file", text)
        self.assertIn("ما لقيتش", self.m.arabic_message(self.m.verdict([], NOW)))

    def test_a_fresh_file_that_is_not_a_database_backup_does_not_hide_a_stale_one(self):
        self.bak(self.d, "stock.bak", 100)                                         # the real backup: four days old
        self.bak(self.desk, "drawing.bak", 1, head=b"AC1027\x00\x00")              # some other program's .bak
        self.bak(self.desk, "compressed.bak", 90, head=b"MSSQLBAK")                # a compressed SQL Server backup
        code, text = self.run_tool()
        self.assertEqual(code, 1)
        self.assertIn("(SQL Server backups: 2)", text)
        result = next(l for l in text.splitlines() if l.startswith("RESULT:"))
        self.assertIn("compressed.bak", result)                                    # the newest of the SQL ones
        self.assertIn(" other ", next(l for l in text.splitlines() if "drawing.bak" in l))

    def test_when_no_file_looks_like_a_database_backup_the_answer_says_so(self):
        self.bak(self.d, "unknown.bak", 3, head=b"PK\x03\x04zipp")
        code, text = self.run_tool()
        self.assertEqual(code, 0)
        self.assertIn("type not certain", next(l for l in text.splitlines() if l.startswith("RESULT:")))
        self.assertIn("مش مؤكد", self.m.arabic_message(self.m.verdict(self.m.scan([str(self.d)])[0], NOW)))

    def locked(self, path):
        """As if this one file refused to be opened."""
        real = self.m.kind
        return mock.patch.object(self.m, "kind", lambda p, attributes=0: "unread" if p == path else real(p, attributes))

    def test_a_file_that_cannot_be_opened_never_changes_the_answer_and_is_named(self):
        """Codex P2 on #369. A fresh .bak nobody could read proves nothing: it may be any program's file, or the
        backup SQL Server is writing right now (it locks it). The stale database backup is still reported, and
        the note says to run again."""
        self.bak(self.d, "stock_old.bak", 100)
        fresh = self.bak(self.desk, "who_knows.bak", 1)
        with self.locked(fresh):
            code, text = self.run_tool()
            v = self.m.verdict(self.m.scan([str(self.d), str(self.desk)])[0], NOW)
        self.assertEqual(code, 1)
        result = next(l for l in text.splitlines() if l.startswith("RESULT:"))
        self.assertIn("WARNING", result)
        self.assertIn("stock_old.bak", result)
        self.assertIn(" unread ", next(l for l in text.splitlines() if "who_knows.bak" in l and l[:4].isdigit()))
        note = next(l for l in text.splitlines() if l.startswith("NOTE:"))
        self.assertIn("1 newer .bak file(s)", note)
        self.assertIn("run this again in a few minutes", note)
        self.assertEqual((v["code"], v["basis"], v["unchecked"]), ("old", "sql", 1))
        self.assertIn("شغّل الأداة تاني", self.m.arabic_message(v))

    def test_an_unreadable_file_older_than_the_backup_judged_is_not_worth_a_note(self):
        old = self.bak(self.d, "old_locked.bak", 300)
        self.bak(self.d, "stock.bak", 2)
        with self.locked(old):
            code, text = self.run_tool()
        self.assertEqual(code, 0)
        self.assertNotIn("NOTE:", text)

    def test_a_file_in_the_cloud_is_listed_but_never_opened(self):
        """Codex P1 on #369: opening a OneDrive "online only" file downloads it and writes it to the disk."""
        self.bak(self.d, "stock.bak", 3)
        cloud = self.bak(self.desk, "onedrive_copy.bak", 1)
        real_stat, real_open, opened = self.m._entry_stat, open, []

        def stat(entry):
            st = real_stat(entry)
            if entry.path == cloud:                                                # Windows marks it "recall on data access"
                return SimpleNamespace(st_mtime=st.st_mtime, st_size=st.st_size, st_file_attributes=0x400000)
            return st

        def spy(path, *a, **k):
            opened.append(str(path))
            return real_open(path, *a, **k)
        with mock.patch.object(self.m, "_entry_stat", stat), mock.patch("builtins.open", spy):
            code, text = self.run_tool()
        self.assertNotIn(cloud, opened)
        self.assertEqual(len(opened), 1)                                           # the local one's 8 bytes
        self.assertIn(" cloud ", next(l for l in text.splitlines() if "onedrive_copy.bak" in l and l[:4].isdigit()))
        self.assertEqual(code, 0)
        self.assertIn("stock.bak", next(l for l in text.splitlines() if l.startswith("RESULT:")))
        self.assertIn("NOTE: 1 newer", text)
        for flag in (0x1000, 0x40000, 0x400000):                                   # offline, recall on open, on data access
            self.assertEqual(self.m.kind("/no/such/file.bak", flag), "cloud")

    def test_a_place_on_the_network_is_not_even_asked_whether_it_exists(self):
        """Codex P1 on #369: a Desktop redirected to a server share. `isdir` on it is already a network call."""
        self.bak(self.d, "stock.bak", 3)
        share = r"\\server\redirected\Desktop"
        probed = []
        real_isdir, real_scandir = os.path.isdir, os.scandir
        with mock.patch.object(self.m.os.path, "isdir", lambda p: probed.append(str(p)) or real_isdir(p)), \
                mock.patch.object(self.m.os, "scandir", lambda p: probed.append(str(p)) or real_scandir(p)):
            code, text = self.run_tool(self.d, share)
        self.assertIn(str(self.d), probed)                                         # the spy does see what is searched
        self.assertNotIn(share, probed)
        self.assertIn("not searched (on the network, or a link; this tool stays on this PC): " + share, text)
        self.assertNotIn(share, next(l for l in text.splitlines() if l.startswith("searched:")))
        self.assertEqual(code, 0)

    def test_a_link_is_never_followed_file_or_folder(self):
        """Codex P1 on #369 (second round): a .bak link on the Desktop, or a linked folder, can point at a server
        share. Nothing is asked about a link's target: it is listed as a link, with the link's own date."""
        self.bak(self.d, "stock.bak", 3)
        outside = self.tmp / "elsewhere"                                           # stands for \\server\share
        target = self.bak(outside, "remote_copy.bak", 1)
        self.bak(outside / "folder", "inside_linked_folder.bak", 1)
        try:
            os.symlink(target, self.desk / "shortcut.bak")
            os.symlink(outside / "folder", self.desk / "linked-folder", target_is_directory=True)
            os.symlink(outside / "folder", self.desk / "folder-called.bak", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("links cannot be made here")
        real_open, opened = open, []

        def spy(path, *a, **k):
            opened.append(str(path))
            return real_open(path, *a, **k)
        stats = []
        real_stat = self.m._entry_stat
        with mock.patch("builtins.open", spy), \
                mock.patch.object(self.m, "_entry_stat", lambda e: stats.append((e.path, e.is_symlink())) or real_stat(e)):
            files, _, _ = self.m.scan([str(self.d), str(self.desk)])
        self.assertEqual(opened, [str(self.d / "stock.bak")])                      # the one real local file
        by_name = {os.path.basename(f["path"]): f for f in files}
        self.assertEqual(sorted(by_name), ["folder-called.bak", "shortcut.bak", "stock.bak"])
        self.assertEqual((by_name["shortcut.bak"]["kind"], by_name["folder-called.bak"]["kind"]), ("link", "link"))
        self.assertNotIn("inside_linked_folder.bak", by_name)                      # the linked folder was not entered
        link_stat = os.lstat(self.desk / "shortcut.bak")
        self.assertEqual((by_name["shortcut.bak"]["size"], by_name["shortcut.bak"]["mtime"]),
                         (link_stat.st_size, link_stat.st_mtime))                  # the link's own, not the target's
        code, text = self.run_tool()
        self.assertEqual(code, 0)
        self.assertIn("stock.bak", next(l for l in text.splitlines() if l.startswith("RESULT:")))
        self.assertIn(" link ", next(l for l in text.splitlines() if "shortcut.bak" in l and l[:4].isdigit()))

    def test_a_searched_place_that_is_itself_a_link_is_left_alone(self):
        """A Desktop folder that is a link (to a server, say) is not entered and not asked about."""
        real = self.tmp / "elsewhere" / "Desktop"
        self.bak(real, "behind_the_link.bak", 1)
        linked = self.tmp / "LinkedDesktop"
        try:
            os.symlink(real, linked, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("links cannot be made here")
        probed = []
        real_isdir, real_scandir = os.path.isdir, os.scandir
        with mock.patch.object(self.m.os.path, "isdir", lambda p: probed.append(str(p)) or real_isdir(p)), \
                mock.patch.object(self.m.os, "scandir", lambda p: probed.append(str(p)) or real_scandir(p)):
            code, text = self.run_tool(self.d, linked)
            home = self.tmp / "home"
            home.mkdir()
            os.symlink(real, home / "Desktop", target_is_directory=True)
            found = self.m.desktops({"USERPROFILE": str(home)})
        self.assertNotIn(str(linked), probed)
        self.assertNotIn(str(home / "Desktop"), probed)
        self.assertEqual(found, [str(home / "Desktop")])                           # kept, to be reported as not searched
        self.assertNotIn("behind_the_link.bak", text)
        self.assertIn("this tool stays on this PC): " + str(linked), text)
        self.assertEqual(code, 2)

    def test_a_link_alone_never_answers(self):
        self.bak(self.d, "stock_old.bak", 100)
        target = self.bak(self.tmp / "elsewhere", "fresh.bak", 1)
        try:
            os.symlink(target, self.desk / "fresh_link.bak")
            t = NOW - HOUR                                                         # the link itself was made an hour ago
            os.utime(self.desk / "fresh_link.bak", (t, t), follow_symlinks=False)
        except (OSError, NotImplementedError):
            self.skipTest("links cannot be made or dated here")
        code, text = self.run_tool()
        self.assertEqual(code, 1)                                                  # the stale real backup is the answer
        self.assertIn("stock_old.bak", next(l for l in text.splitlines() if l.startswith("RESULT:")))
        self.assertIn("NOTE: 1 newer", text)

    def test_what_counts_as_the_network(self):
        m = self.m
        mapped = lambda root: m.DRIVE_REMOTE if root == "Z:\\" else 3            # Z: is a mapped drive, the rest fixed
        self.assertTrue(m.is_remote(r"\\server\share\Desktop", mapped))
        self.assertTrue(m.is_remote("//server/share/Desktop", mapped))
        self.assertTrue(m.is_remote(r"Z:\Backups", mapped))
        self.assertFalse(m.is_remote("D:\\", mapped))
        self.assertFalse(m.is_remote(r"C:\Users\someone\Desktop", mapped))
        self.assertFalse(m.is_remote(str(self.d), mapped))

    def test_a_network_desktop_from_the_settings_is_kept_to_be_reported_not_probed(self):
        share = r"\\server\users\someone"
        probed = []
        real_isdir = os.path.isdir
        with mock.patch.object(self.m.os.path, "isdir", lambda p: probed.append(str(p)) or real_isdir(p)):
            got = self.m.desktops({"USERPROFILE": share})
        self.assertEqual(got, [share + os.sep + "Desktop", os.path.join(share, "OneDrive", "Desktop")])
        self.assertEqual(probed, [])

    def test_nothing_found_says_when_a_network_place_was_left_out(self):
        code, text = self.run_tool(self.d, r"\\server\share")
        self.assertEqual(code, 2)
        self.assertIn("A network place was not searched", text)

    def test_deleted_files_and_missing_places_are_passed_over(self):
        self.bak(self.d / "$RECYCLE.BIN" / "S-1-5", "deleted.bak", 1)
        self.bak(self.d / "System Volume Information", "shadow.bak", 1)
        self.bak(self.d / "deep" / "er" / "est", "kept.bak", 5)
        code, text = self.run_tool(self.d, self.tmp / "no-such-drive")
        self.assertEqual(code, 0)
        self.assertIn(".bak files found: 1 ", text)
        self.assertIn("kept.bak", text)
        self.assertNotIn("deleted.bak", text)
        self.assertIn("not found: " + str(self.tmp / "no-such-drive"), text)

    def test_the_same_file_reached_twice_is_listed_once(self):
        self.bak(self.d / "Desktop", "one.bak", 4)
        code, text = self.run_tool(self.d, self.d / "Desktop")                     # a Desktop that lives on D:\
        self.assertIn(".bak files found: 1 ", text)

    def test_a_long_list_is_cut_and_counted(self):
        for n in range(self.m.SHOW + 7):
            self.bak(self.d, f"b{n:03d}.bak", 1 + n)
        code, text = self.run_tool()
        self.assertIn(f".bak files found: {self.m.SHOW + 7} ", text)
        self.assertIn("... and 7 older file(s) not shown", text)
        self.assertEqual(len([l for l in text.splitlines() if l.rstrip().endswith(".bak") and l[:4].isdigit()]), self.m.SHOW)

    def test_a_clock_set_back_does_not_give_a_negative_age(self):
        self.bak(self.d, "future.bak", -5)
        code, text = self.run_tool()
        self.assertEqual(code, 0)
        self.assertIn("0 min", text)

    def test_nothing_on_the_disk_is_changed_by_a_run(self):
        self.bak(self.d / "Backup", "stock.bak", 30)
        self.bak(self.desk, "copy.bak", 300, head=b"other!!!")
        before = self.state()
        self.run_tool()
        self.assertEqual(self.state(), before)                                     # same files, sizes and dates
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["D", "Desktop"])

    def test_sizes_and_ages_read_well(self):
        m = self.m
        self.assertEqual([m.fmt_size(n) for n in (0, 500, 1024 ** 2, 3 * 1024 ** 3 // 2)],
                         ["0 KB", "1 KB", "1.0 MB", "1.50 GB"])
        self.assertEqual([m.fmt_age(h) for h in (0.5, 3, 47.9, 72)], ["30 min", "3.0 h", "47.9 h", "3.0 days"])

    def test_the_desktop_folders_are_the_users_and_the_shared_one(self):
        home, public = self.tmp / "home", self.tmp / "public"
        for p in (home / "Desktop", home / "OneDrive" / "Desktop", public / "Desktop"):
            p.mkdir(parents=True)
        got = self.m.desktops({"USERPROFILE": str(home), "PUBLIC": str(public)})
        self.assertEqual(got, [str(home / "Desktop"), str(home / "OneDrive" / "Desktop"), str(public / "Desktop")])
        self.assertEqual(self.m.default_roots()[0], "D:\\")
