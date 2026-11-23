"""Real run of the launch-night scripts on Windows, on synthetic data in a throw-away user profile (#113).

Runs only on Windows with PHARM_BAT_E2E=1 (the "Launch night e2e" workflow sets it). Never on the pharmacy PC:
the scripts use C:\\estock, D:\\PharmacyPlatform-backups and port 8000, which belong to the real program there.

Layout built like the pharmacy PC after an in-app update: the installed program (python + program) under
LOCALAPPDATA\\Programs\\PharmacyPlatform, the data under LOCALAPPDATA\\PharmacyPlatform\\data, and the active code
in data\\code-releases\\releases\\<id>, which is also where the scripts are double-clicked from.
"""
import hashlib
import http.server
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENABLED = os.name == "nt" and os.environ.get("PHARM_BAT_E2E") == "1"
PASSWORD = "Owner-Pass-2026"
ESTOCK = Path(r"C:\estock")


def wait_http(url, want_ok=True, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200 and want_ok:
                    return True
        except OSError:
            if not want_ok:
                return True
        time.sleep(0.5)
    return False


def count(db, sql):
    con = sqlite3.connect(Path(db).as_uri() + "?mode=ro", uri=True)
    try:
        return con.execute(sql).fetchone()[0]
    finally:
        con.close()


@unittest.skipUnless(ENABLED, "Windows CI only (PHARM_BAT_E2E=1)")
class LaunchNightOnWindows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = Path(tempfile.mkdtemp(prefix="ln-"))
        local, user, temp = cls.t / "L", cls.t / "U", cls.t / "T"
        for d in (local, user / "Documents", temp):
            d.mkdir(parents=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith("PHARM_") and k != "PYTHONPATH"}
        env.update(LOCALAPPDATA=str(local), USERPROFILE=str(user), TEMP=str(temp), TMP=str(temp), PYTHONUTF8="1")
        cls.env = env
        cls.inst = local / "Programs" / "PharmacyPlatform"
        cls.home = local / "PharmacyPlatform"
        cls.data = cls.home / "data"
        cls.out = user / "Documents" / "launch-night"
        # installed program: this Python (with the libraries) and the program files
        ignore = shutil.ignore_patterns("__pycache__", ".git", "node_modules", "_release", ".venv", "data")
        shutil.copytree(sys.base_prefix, cls.inst / "python", ignore=shutil.ignore_patterns("__pycache__"))
        if sys.prefix != sys.base_prefix:
            shutil.copytree(Path(sys.prefix) / "Lib" / "site-packages", cls.inst / "python" / "Lib" / "site-packages",
                            dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT, cls.inst / "program", ignore=ignore)
        cls.py = cls.inst / "python" / "python.exe"
        cls.pyw = cls.inst / "python" / "pythonw.exe"
        # trial data with a pharmacy in it
        cls.manage("init", "--pharmacy", "Trial", "--username", "owner", "--password", PASSWORD)
        # active code chosen by an "update": a verified release inside the data folder
        rid = hashlib.sha256(b"launch-night-e2e").hexdigest()
        home = cls.data / "code-releases"
        rel = home / "releases" / rid
        shutil.copytree(cls.inst / "program", rel)
        files = {p.relative_to(rel).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in rel.rglob("*") if p.is_file()}
        (rel / ".release-source.json").write_text(json.dumps({"id": rid, "files": files}), encoding="utf-8")
        (home / "active-release.json").write_text(json.dumps(
            {"protocol": 1, "legacy_root": str(cls.inst / "program"), "active": {"id": rid, "sha": "e2e"},
             "previous": None, "pending": False}), encoding="utf-8")
        cls.rid = rid
        cls.scripts = rel / "scripts" / "windows"

    @classmethod
    def tearDownClass(cls):
        cls.stop_own()
        shutil.rmtree(cls.t, ignore_errors=True)

    @classmethod
    def manage(cls, *args):
        r = subprocess.run([str(cls.py), str(cls.inst / "program" / "manage.py"), *args], env=cls.env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cls.t)
        assert r.returncode == 0, r.stdout + r.stderr
        return r.stdout

    @classmethod
    def stop_own(cls):
        d = str(cls.inst / "python").lower() + "\\"
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and "
                        f"$_.ExecutablePath.ToLower().StartsWith('{d}') }} | "
                        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"])
        wait_http("http://127.0.0.1:8000/api/health", want_ok=False, timeout=20)

    def run_bat(self, name, answers, extra_env=None, timeout=300):
        # files, not pipes: the program the script starts inherits the handles and would keep a pipe open forever
        env = {**self.env, **(extra_env or {})}
        n = len(list(self.t.glob("bat-*.out")))
        inp, out = self.t / f"bat-{n}.in", self.t / f"bat-{n}.out"
        inp.write_text(answers, encoding="ascii")
        with open(inp, "rb") as i, open(out, "wb") as o:
            try:
                rc = subprocess.run(["cmd.exe", "/d", "/c", str(self.scripts / name)], stdin=i, stdout=o,
                                    stderr=subprocess.STDOUT, env=env, cwd=self.t, timeout=timeout).returncode
            except subprocess.TimeoutExpired:
                rc = "timeout"
        text = out.read_bytes().decode("utf-8", errors="replace")
        return subprocess.CompletedProcess(name, rc, text, "")

    def pharmacy(self):
        """The name fresh-start-check wants typed (#324 R2): the pharmacy in the current database."""
        con = sqlite3.connect((self.data / "pharmacy.db").as_uri() + "?mode=ro", uri=True)
        try:                                       # closed at once: an open file blocks the folder rename
            return con.execute("SELECT name FROM organizations").fetchone()[0]
        finally:
            con.close()

    def log(self, name):
        p = self.out / name
        return p.read_text(encoding="utf-8", errors="replace") if p.exists() else "(no log)"

    def trials(self, prefix):
        return sorted(p for p in self.home.iterdir() if p.name.startswith(prefix))

    def test_1_fresh_start_from_the_release_folder(self):
        # the program runs (launcher + server from the installed python), and an unrelated python too
        launcher = subprocess.Popen([str(self.pyw), str(self.inst / "program" / "launcher.py")], env=self.env,
                                    cwd=self.inst / "program",
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertTrue(wait_http("http://127.0.0.1:8000/api/health"), "program did not start")
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            r = self.run_bat("launch-fresh-start.bat", "", {"PHARM_E2E_PHARMACY": self.pharmacy()})
            text = self.log("fresh-start.txt")
            self.assertEqual(r.returncode, 0, text + r.stdout + r.stderr)
            self.assertIn("PASS - ALL DONE", text)
            self.assertIsNone(other.poll(), "an unrelated python process was stopped")
            self.assertIsNotNone(launcher.poll(), "the old launcher is still running")
        finally:
            other.kill()
        trial = self.trials("data-trial-")
        self.assertEqual(len(trial), 1)
        self.assertEqual(count(trial[0] / "pharmacy.db", "SELECT count(*) FROM organizations"), 1)
        self.assertEqual(count(self.data / "pharmacy.db", "SELECT count(*) FROM organizations"), 0)
        self.assertTrue((self.data / "code-releases" / "releases" / self.rid / "manage.py").is_file())
        # U4-0: the printing / device settings go along for the setup screen to apply
        self.assertIn("PASS - device and printing settings kept", text)
        self.assertTrue((self.data / "device_settings_carry.json").is_file())
        zips = list((self.home / "backups").glob("fresh-start-*/*.zip"))
        self.assertEqual(len(zips), 1)
        if Path("D:\\").exists():
            self.assertTrue((Path(r"D:\PharmacyPlatform-backups") / zips[0].name).is_file())
        with urllib.request.urlopen("http://127.0.0.1:8000/api/setup", timeout=5) as resp:
            self.assertEqual(json.loads(resp.read()), {"needed": True})

    def test_2_estock_import(self):
        self.manage("init", "--pharmacy", "Launch", "--username", "owner", "--password", PASSWORD)
        from tests.test_estock import CATALOG, stock_rows, xlsx
        if ESTOCK.exists():
            shutil.rmtree(ESTOCK)
        ESTOCK.mkdir()
        (ESTOCK / "قائمة الأصناف.xlsx").write_bytes(xlsx(CATALOG))
        (ESTOCK / "كميات الأصناف طبقا لتواريخ الصلاحية.xlsx").write_bytes(xlsx(stock_rows()))
        r = self.run_bat("estock-import.bat", "owner\nYES\n", {"PHARM_IMPORT_PASSWORD": PASSWORD, "PHARM_E2E_OWNER": "owner", "PHARM_E2E_ANSWER": "YES"})
        text = self.log("estock-import.txt")
        self.assertEqual(r.returncode, 0, text + r.stdout + r.stderr)
        self.assertIn("PASS - ALL DONE", text)
        self.assertIn("OK", self.log("import-dry-run.txt"))
        self.assertEqual(count(self.data / "pharmacy.db", "SELECT count(*) FROM items"), 4)
        self.assertTrue(wait_http("http://127.0.0.1:8000/api/health"), "program not started again")

    def test_3_fresh_start_failure_after_rename_puts_the_data_back(self):
        self.stop_own()
        items = count(self.data / "pharmacy.db", "SELECT count(*) FROM items")
        launcher = self.inst / "program" / "launcher.py"
        hidden = launcher.with_name("launcher.py.e2e-hidden")
        launcher.rename(hidden)                                        # the program cannot start after the rename
        try:
            r = self.run_bat("launch-fresh-start.bat", "", {"PHARM_E2E_PHARMACY": self.pharmacy()})
        finally:
            hidden.rename(launcher)
        text = self.log("fresh-start.txt").rsplit("==== fresh start", 1)[-1]
        self.assertEqual(r.returncode, 1, text + r.stdout + r.stderr)
        self.assertIn("FAIL - the program did not start", text)
        self.assertIn("PASS - old data folder put back", text)
        self.assertEqual(count(self.data / "pharmacy.db", "SELECT count(*) FROM items"), items)
        self.assertEqual(len(self.trials("data-failed-")), 1)
        self.assertEqual(len(self.trials("data-trial-")), 1)                 # only the one from test 1

    def test_4_a_foreign_server_on_the_port_stops_fresh_start_before_any_change(self):
        """#324 R14: a server answering 404 on the program's port used to pass the stop check."""
        self.stop_own()

        class NotFound(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(404)
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 8000), NotFound)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            r = self.run_bat("launch-fresh-start.bat", "", {"PHARM_E2E_PHARMACY": self.pharmacy()})
        finally:
            srv.shutdown()
            srv.server_close()
        text = self.log("fresh-start.txt").rsplit("==== fresh start", 1)[-1]
        self.assertEqual(r.returncode, 1, text + r.stdout + r.stderr)
        self.assertIn("FAIL - the program could not be stopped", text)
        self.assertNotIn("data folder renamed", text)
        self.assertEqual(len(self.trials("data-failed-")), 1)                # still only the one from test 3


if __name__ == "__main__":
    unittest.main()
