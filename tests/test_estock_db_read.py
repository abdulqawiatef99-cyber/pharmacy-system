"""scripts/windows/estock-db-read.{bat,ps1}: read the E-Stock database, read only (owner 18/11/2026).

PowerShell cannot run here; these tests pin the properties that matter statically."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PS1 = ROOT / "scripts" / "windows" / "estock-db-read.ps1"
BAT = ROOT / "scripts" / "windows" / "estock-db-read.bat"


class TestEstockDbRead(unittest.TestCase):
    def test_ps1_is_utf8_with_bom_and_crlf(self):
        raw = PS1.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "PowerShell 5.1 reads a file without BOM as ANSI")
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        raw.decode("utf-8-sig")

    def test_bat_is_crlf_ascii_and_calls_the_ps1_next_to_it(self):
        raw = BAT.read_bytes()
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        raw.decode("ascii")
        self.assertIn('-File "%~dp0estock-db-read.ps1"', raw.decode())

    def test_sql_is_select_only_with_nolock(self):
        text = PS1.read_text(encoding="utf-8-sig")
        sql = " ".join(re.findall(r'"(SELECT [^"]*)"', text))
        code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
        for word in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "EXEC", "MERGE", "CREATE", "GRANT"):
            self.assertIsNone(re.search(rf"\b{word}\b", code), word)
        self.assertEqual(text.count("FROM $full WITH (NOLOCK)"), 2)    # the stats and the full read
        self.assertIn("SELECT", sql)

    def test_password_never_printed_and_csv_stays_under_documents(self):
        text = PS1.read_text(encoding="utf-8-sig")
        self.assertNotRegex(text, r"Say[^\n]*\$cs\b")                  # a connection string is never logged
        self.assertIn("GetFolderPath('MyDocuments')", text)
        self.assertIn('"estock-db-read\\$stamp"', text)
        self.assertIn("InvariantCulture", text)                       # CSV dates/numbers not in the PC's culture

    def test_codex_333_partial_fallback_and_folder_per_instance(self):
        text = PS1.read_text(encoding="utf-8-sig")
        # (1) a failed table is listed, the run is PARTIAL with a nonzero exit, and a table counts only after its CSV
        self.assertIn("RESULT: PARTIAL", text)
        self.assertIn("if ($failed.Count -gt 0) { exit 4 }", text)
        csv, counted = text.index("Export-Csv -Path"), text.index("$totalTables++")
        self.assertLess(csv, counted)
        self.assertEqual(text.count("$totalTables++"), 1)
        # (2) a Windows sign-in that opens no user database does not stop the fallback
        self.assertIn("if ($u -gt 0)", text)
        self.assertIn("(Usable $c) -gt 0", text)
        self.assertLess(text.index("function Usable"), text.index("$u = Usable $c"))   # defined before use
        # (3) tables\<instance>\<db>\
        self.assertIn("Join-Path (Join-Path $tables ($srv -replace", text)

    def test_in_app_update_ships_the_ps1(self):
        import io
        import zipfile
        from app.core import updater
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for name in ("manage.py", "app/__init__.py", "scripts/windows/estock-db-read.bat",
                         "scripts/windows/estock-db-read.ps1"):
                z.writestr(f"repo-abc/{name}", "x")
        with zipfile.ZipFile(buf) as z:
            _, members = updater._members(z)
        self.assertIn("repo-abc/scripts/windows/estock-db-read.ps1", [m.filename for m in members])


if __name__ == "__main__":
    unittest.main()
