"""Owner-only E-Stock rehearsal. All exports and subprocess output are synthetic."""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from unittest import mock, skipUnless

from starlette.testclient import TestClient

from app import config
from app.api.app import create_app
from app.core import people
from app.dataimport import estock_move
from tests.helpers import DBTestCase, OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_estock_db_import import base_tables, product, write_export

URL = "/api/estock/move"
POST = {"X-Requested-With": "pharm", "Idempotency-Key": "synthetic-retry"}
PW = "Manager-Pass-2026"
POWERSHELL = shutil.which("powershell.exe" if sys.platform == "win32" else "pwsh")


class TestMove(DBTestCase):
    def setUp(self):
        super().setUp()
        self.s = replace(config.load(), data_dir=self.tmp, db_path=Path(self.db.path),
                         upload_dir=self.tmp / "uploads", backup_dir=self.tmp / "backups")
        self.app = create_app(self.db, self.s)
        self.c = TestClient(self.app)
        self.sign_in(self.c, "owner", OWNER_PW)
        supported = mock.patch.object(estock_move, "supported", return_value=True)
        supported.start()
        self.addCleanup(supported.stop)

    def sign_in(self, client, username, password):
        client.cookies.clear()
        response = client.post("/api/auth/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.text)

    def wait_for(self, phase):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            response = self.c.get(URL)
            self.assertEqual(response.status_code, 200, response.text)
            state = response.json()
            if state["phase"] == phase:
                return state
            if state["phase"] in {"done", "failed"}:
                self.fail(state)
            time.sleep(.02)
        self.fail(f"Timed out waiting for {phase}: {state}")

    @staticmethod
    def export(folder, progress):
        tables = base_tables()
        tables["Products"] += [product(99, "EA99", "Synthetic 24 tablets", "تجربة ٢٤ قرص"),
                               product(98, "EA98", "Synthetic 500 mg tablet"),
                               product(97, "EA97", "Synthetic 1 capsule"),
                               product(96, "EA96", "Synthetic Omega 3 capsules"),
                               product(95, "EA95", "Synthetic Age 50+ Tablets")]
        tables["Customer"][0].update(customer_name_ar="عميل الاختبار", customer_name_en="Synthetic Customer")
        tables["Customer"][1].update(customer_name_en=tables["Customer"][1]["customer_name_ar"])
        progress("reading", 2)
        write_export(folder, tables)
        (folder / "summary.txt").write_text("RESULT: PASS - synthetic\n", encoding="utf-8")

    def test_owner_flow_reports_progress_and_leaves_live_database_untouched(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def read(folder, progress):
            progress("reading", 2)
            release.wait(10)
            self.export(folder, progress)

        before = "\n".join(self.db.conn.iterdump())
        with mock.patch.object(estock_move, "read_export", side_effect=read) as reader:
            self.assertEqual(self.c.post(URL, headers=POST).status_code, 202)
            self.assertEqual(self.wait_for("reading")["tables"], 2)
            self.assertEqual(self.c.post(URL, headers=POST).status_code, 202)
            release.set()
            result = self.wait_for("done")
            self.assertEqual(reader.call_count, 1)
        self.assertIn("٢. المطابقة", result["report"])
        self.assertIn("عميل الاختبار", result["report"])
        self.assertIn("Synthetic Customer", result["report"])
        self.assertIn("EA99", result["report"])
        self.assertNotIn("EA98", result["report"])
        self.assertNotIn("EA97", result["report"])
        self.assertNotIn("EA96", result["report"])
        self.assertNotIn("EA95", result["report"])
        self.assertEqual("\n".join(self.db.conn.iterdump()), before)
        reopened = TestClient(create_app(self.db, self.s))
        self.sign_in(reopened, "owner", OWNER_PW)
        self.assertEqual(reopened.get(URL).json()["report"], result["report"])
        self.assertEqual(self.c.post(URL + "/apply", headers=POST).status_code, 404)

    def test_owner_only_local_only_and_tenant_scoped(self):
        with mock.patch.object(estock_move, "read_export", side_effect=self.export):
            self.c.post(URL, headers=POST)
            self.wait_for("done")
        people.add_user(self.db, self.ctx, username="manager1", full_name="Manager", password=PW,
                        role_code="manager")
        self.sign_in(self.c, "manager1", PW)
        self.assertEqual(self.c.get(URL).status_code, 403)
        self.assertEqual(self.c.post(URL, headers=POST).status_code, 403)
        self.c.cookies.clear()
        self.assertEqual(self.c.get(URL).status_code, 401)
        self.second_tenant()
        self.sign_in(self.c, "other", "Other-Pass-2026")
        self.assertEqual(self.c.get(URL).json()["phase"], "idle")
        with mock.patch.object(estock_move, "read_export") as reader:
            self.assertEqual(self.c.post(URL, headers=POST).status_code, 409)
            reader.assert_not_called()
        remote = TestClient(create_app(self.db, self.s), client=("192.0.2.10", 5000))
        self.sign_in(remote, "owner", OWNER_PW)
        self.assertEqual(remote.get(URL).status_code, 403)
        self.assertEqual(remote.post(URL, headers=POST).status_code, 403)

    def test_completed_export_is_reused_after_interruption(self):
        # Simulate a restart during the rehearsal by leaving its durable checkpoint on disk.
        home = self.tmp / "estock-move" / self.ctx.org_id
        run_id = "a" * 32
        self.export(home / run_id / "export", lambda *_: None)
        (home / run_id / "preview").mkdir()
        (home / run_id / "preview" / "copy.db").write_bytes(b"interrupted copy")
        fingerprint = estock_move.export_fingerprint(home / run_id / "export")
        (home / "state.json").write_text(json.dumps({"id": run_id, "phase": "rehearsing", "tables": 2,
                                                    "fingerprint": fingerprint}))
        self.assertEqual(self.c.get(URL).json()["phase"], "interrupted")
        with mock.patch.object(estock_move, "read_export") as reader:
            self.assertEqual(self.c.post(URL, headers=POST).status_code, 202)
            self.assertIn("تقرير نقل إي ستوك", self.wait_for("done")["report"])
            reader.assert_not_called()
        self.assertFalse((home / run_id / "preview" / "copy.db").exists())

    def test_restart_checks_export_fingerprint_before_reusing_it(self):
        home = self.tmp / "estock-move" / self.ctx.org_id
        run_id = "a" * 32
        export = home / run_id / "export"
        self.export(export, lambda *_: None)
        fingerprint = estock_move.export_fingerprint(export)
        (home / "state.json").write_text(json.dumps({"id": run_id, "phase": "rehearsing", "tables": 2,
                                                    "fingerprint": fingerprint}))
        table = next(export.rglob("*Products.csv"))
        table.write_bytes(table.read_bytes()[:-10])
        self.c.post(URL, headers=POST)
        self.assertIn("القراية المحفوظة ناقصة", self.wait_for("failed")["message"])
        with mock.patch.object(estock_move, "read_export", side_effect=self.export):
            self.c.post(URL, headers=POST)
            self.assertNotEqual(self.wait_for("done")["id"], run_id)

    def test_interrupted_read_never_uses_uncheckpointed_export(self):
        home = self.tmp / "estock-move" / self.ctx.org_id
        run_id = "a" * 32
        self.export(home / run_id / "export", lambda *_: None)
        (home / "state.json").write_text(json.dumps({"id": run_id, "phase": "reading", "tables": 2}))
        self.assertEqual(self.c.get(URL).json()["phase"], "interrupted")
        with mock.patch.object(estock_move, "read_export", side_effect=self.export) as reader:
            self.c.post(URL, headers=POST)
            result = self.wait_for("done")
            self.assertNotEqual(result["id"], run_id)
            reader.assert_called_once()

    def test_existing_sales_refuse_before_reading(self):
        with self.db.transaction():
            self.db.execute("INSERT INTO sales(id,org_id,branch_id,status,created_by) VALUES(?,?,?,?,?)",
                            ("synthetic", self.ctx.org_id, self.ctx.branch_id, "completed", self.ctx.user_id))
        with mock.patch.object(estock_move, "read_export") as reader:
            response = self.c.post(URL, headers=POST)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertIn("فواتير بيع", response.json()["error"]["message"])
            reader.assert_not_called()

    def test_windows_requirement_is_reported_without_starting_job(self):
        with mock.patch.object(estock_move, "supported", return_value=False):
            self.assertFalse(self.c.get(URL).json()["supported"])
            self.assertEqual(self.c.post(URL, headers=POST).status_code, 422)

    def test_partial_read_still_shows_the_blocking_owner_report(self):
        def partial(folder, progress):
            self.export(folder, progress)
            (folder / "summary.txt").write_text(
                "RESULT: PARTIAL - synthetic\n   PC / stock.dbo.Optional_table\n", encoding="utf-8")

        with mock.patch.object(estock_move, "read_export", side_effect=partial):
            self.c.post(URL, headers=POST)
            result = self.wait_for("done")
        self.assertFalse(result["reconciled"])
        self.assertIn("optional_table", result["report"])
        self.assertNotIn("estock-db-import.bat", result["report"])

    def test_missing_required_table_requests_retry_from_the_screen(self):
        def partial(folder, progress):
            tables = base_tables()
            del tables["Products"]
            write_export(folder, tables)
            (folder / "summary.txt").write_text(
                "RESULT: PARTIAL - synthetic\n   PC / stock.dbo.Products\n", encoding="utf-8")

        with mock.patch.object(estock_move, "read_export", side_effect=partial):
            self.c.post(URL, headers=POST)
            message = self.wait_for("failed")["message"]
        self.assertIn("جرّب تاني", message)
        self.assertNotIn(".bat", message)

    def test_reader_uses_hidden_noninteractive_subprocess_and_whitelisted_output(self):
        process = mock.MagicMock()
        process.__enter__.return_value = process
        process.stdout = io.StringIO("noise that never reaches the screen\nESTOCK: FOUND\nESTOCK: TABLE\n"
                                     "ESTOCK: TABLE\n")
        process.wait.return_value = 0
        folder = self.tmp / "فولدر اختبار"
        self.export(folder, lambda *_: None)
        with mock.patch.dict(os.environ, {"SystemRoot": str(self.tmp)}), \
             mock.patch.object(estock_move.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True), \
             mock.patch.object(estock_move.subprocess, "Popen", return_value=process) as popen:
            progress = mock.Mock()
            estock_move.read_export(folder, progress)
        args, options = popen.call_args
        self.assertEqual(Path(args[0][0]).name, "powershell.exe")
        self.assertIn("-NonInteractive", args[0])
        self.assertIn("-Guided", args[0])
        self.assertIn(str(folder), args[0])
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertEqual(options["creationflags"], subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0x08000000)
        self.assertEqual(progress.call_args_list, [mock.call("reading", 0), mock.call("reading", 1),
                                                   mock.call("reading", 2)])

    @skipUnless(POWERSHELL, "PowerShell executable unavailable")
    def test_reader_parses_guided_switches_without_contacting_sql(self):
        # Parameter validation occurs before discovery. This exercises the shipped .ps1 under Windows
        # PowerShell with the same hidden command options and without needing a SQL service or real data.
        script = Path(estock_move.__file__).resolve().parents[2] / "scripts/windows/estock-db-read.ps1"
        result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive",
                                 "-ExecutionPolicy", "Bypass", "-File", str(script), "-Guided"],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Guided requires OutputDirectory", result.stdout + result.stderr)

    @skipUnless(POWERSHELL, "PowerShell executable unavailable")
    def test_reader_checks_app_config_even_when_windows_sign_in_finds_a_restored_database(self):
        result = self.synthetic_sql_reader("Live")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("ESTOCK: AMBIGUOUS", result.stdout)
        self.assertNotIn("ESTOCK: FOUND", result.stdout)

    @skipUnless(POWERSHELL, "PowerShell executable unavailable")
    def test_reader_prefers_sql_login_with_select_access_to_the_same_database(self):
        result = self.synthetic_sql_reader("Restored")
        self.assertIn("ESTOCK: FOUND", result.stdout)
        self.assertIn("SYNTHETIC PERMITTED QUERY", result.stdout)

    def synthetic_sql_reader(self, config_db):
        """Exercise discovery through the script with synthetic SQL metadata and no network."""
        source = estock_move.SCRIPT.read_text(encoding="utf-8-sig")
        source = re.sub(r"Get-Service -ErrorAction SilentlyContinue \| Where-Object \{ .*? \}",
                        r"@([pscustomobject]@{Name='MSSQLSERVER'; Status='Running'})", source)
        source = re.sub(r"(?m)^    \$roots = .*", "    $roots = @(Join-Path $env:LOCALAPPDATA 'test-install')", source)
        first, end = source.index("function Q("), source.index("function Qn(")
        source = source[:first] + """
class SyntheticConnection {
    [string]$DataSource = '(local)'
    [string]$Database
    [int]$CanRead
    SyntheticConnection([string]$database, [int]$canRead) { $this.Database = $database; $this.CanRead = $canRead }
    [void] ChangeDatabase([string]$database) {}
    [void] Close() {}
}
function Q($conn, [string]$sql) {
    if ($sql -like '*FROM sys.databases*') {
        return @{Rows=@(@{name=$conn.Database; state_desc='ONLINE'})}
    }
    if ($sql -like '*FROM INFORMATION_SCHEMA.TABLES*') {
        return @{Rows=@('Products','Customer','Vendor','Product_Amount' | ForEach-Object { @{nm=$_; allowed=$conn.CanRead} })}
    }
    if ($conn.CanRead) { [Console]::WriteLine('SYNTHETIC PERMITTED QUERY') }
    throw 'Discovery selected a database and reached the export phase.'
}
""" + source[end:]
        first, end = source.index("function Who("), source.index("# One text form")
        source = source[:first] + 'function Who($c) { return $env:COMPUTERNAME }\n' + source[end:]
        first, end = source.index("function Try-Open("), source.index("$conns = @()")
        source = source[:first] + """
function Try-Open([string]$cs) {
    if ($cs -like '*Initial Catalog=*') { return [SyntheticConnection]::new($env:TEST_CATALOG, 1) }
    return [SyntheticConnection]::new('Restored', 0)
}
""" + source[end:]
        script = self.tmp / "synthetic-reader.ps1"
        script.write_text(source, encoding="utf-8-sig")
        install = self.tmp / "test-install"
        install.mkdir()
        (install / "data.config").write_text(f"Server=(local);Initial Catalog={config_db};Integrated Security=True")
        return subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                 "-File", str(script), "-Guided", "-OutputDirectory", str(self.tmp / "export-test")],
                                env={**os.environ, "LOCALAPPDATA": str(self.tmp), "COMPUTERNAME": "SYNTHETIC",
                                     "TEST_CATALOG": config_db},
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


SCREEN = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.evaluate(() => { location.hash = '#/system'; });
  await p.getByRole('link', { name: 'نقل من إي ستوك' }).click();
  await p.getByRole('button', { name: 'ابدأ تجربة النقل' }).click();
  await p.getByRole('progressbar').waitFor();
  const report = p.locator('#estockMoveReport');
  await report.waitFor();
  const text = await report.textContent();
  await p.reload();
  await report.waitFor();
  console.log(JSON.stringify({ text, reopened: await report.textContent(), safe: !await p.locator('#synthetic-markup').count() }));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestMoveScreen(DBTestCase):
    def test_owner_opens_the_report_and_source_text_stays_plain(self):
        s = replace(config.load(), data_dir=self.tmp, db_path=Path(self.db.path),
                    upload_dir=self.tmp / "uploads", backup_dir=self.tmp / "backups")
        self.app = create_app(self.db, s)

        def read(folder, progress):
            progress("reading", 0)
            time.sleep(1)
            tables = base_tables()
            tables["Customer"][0]["customer_name_en"] = '<b id="synthetic-markup">Synthetic Customer</b>'
            write_export(folder, tables)
            (folder / "summary.txt").write_text("RESULT: PASS - synthetic\n", encoding="utf-8")

        with mock.patch.object(estock_move, "read_export", side_effect=read), \
             mock.patch.object(estock_move, "supported", return_value=True):
            result = run_node(self, SCREEN, "owner", OWNER_PW)
        self.assertIn("٢. المطابقة", result["text"])
        self.assertIn('<b id="synthetic-markup">Synthetic Customer</b>', result["text"])
        self.assertEqual(result["text"], result["reopened"])
        self.assertTrue(result["safe"])
