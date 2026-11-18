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

    def test_password_never_printed_and_csv_stays_off_the_cloud(self):
        text = PS1.read_text(encoding="utf-8-sig")
        self.assertNotRegex(text, r"Say[^\n]*\$cs\b")                  # a connection string is never logged
        self.assertIn('Join-Path $env:LOCALAPPDATA "PharmacyPlatform\\estock-db-read\\$stamp"', text)
        self.assertNotIn("MyDocuments", text)                         # Documents can be synced by OneDrive
        self.assertNotIn("Documents", BAT.read_text())
        self.assertIn("InvariantCulture", text)                       # CSV dates/numbers not in the PC's culture

    def test_codex_333_partial_fallback_and_folder_per_instance(self):
        text = PS1.read_text(encoding="utf-8-sig")
        # (1) a failed table is listed, the run is PARTIAL with a nonzero exit, and a table counts only after its CSV
        self.assertIn("RESULT: PARTIAL", text)
        self.assertIn("if ($failed.Count -gt 0) { exit 4 }", text)
        csv, counted = text.index("Export-Csv -Path ($csv"), text.index("$totalTables++")
        self.assertLess(csv, counted)
        self.assertEqual(text.count("$totalTables++"), 1)
        # (2) a Windows sign-in that opens no user database does not stop the fallback
        self.assertIn("if ($u -gt 0)", text)
        self.assertIn("(Usable $c) -gt 0", text)
        self.assertLess(text.index("function Usable"), text.index("$u = Usable $c"))   # defined before use
        # (3) one folder per instance: see test_paths_collision_free_by_construction
        self.assertIn("$srvDir = Join-Path $tables", text)

    def test_one_cell_formatter_for_every_table(self):
        text = PS1.read_text(encoding="utf-8-sig")
        self.assertEqual(text.count("Export-Csv -Path ($csv"), 1)       # one export path, every table
        self.assertIn("$o[$n] = Cell $_[$n]", text)
        self.assertNotIn("Select-Object -Property $names", text)
        body = text[text.index("function Cell($v)"):text.index("function Try-Open")]
        for rule in ("$v -is [DBNull]) { return '' }",
                     "[byte[]]) { return 'b64:' + [Convert]::ToBase64String($v) }",
                     "[DateTime]) { return $v.ToString('o', $inv) }",
                     "[DateTimeOffset]) { return $v.ToString('o', $inv) }",
                     "[TimeSpan]) { return $v.ToString('c', $inv) }",
                     "[double]) { return $v.ToString('G17', $inv) }",
                     "[single]) { return $v.ToString('G9', $inv) }",
                     "[decimal]) { return $v.ToString($inv) }",
                     "if ($v) { return '1' } else { return '0' }",
                     "[Guid]) { return $v.ToString() }",
                     "[IFormattable]) { return $v.ToString($null, $inv) }"):
            self.assertIn(rule, body)
        self.assertLess(body.index("[DateTime]"), body.index("[IFormattable]"))    # specific types first
        self.assertLess(text.index("function Cell"), text.index("Cell $_[$n]"))

    def test_no_silent_loss_and_no_wrong_pass(self):
        text = PS1.read_text(encoding="utf-8-sig")
        # an instance running but not reached (E-Stock's own, when another instance accepted Windows sign-in)
        self.assertIn("if ($conns.Count -eq 0 -or (& $missing) -gt 0)", text)
        self.assertIn('$failed.Add($want[$k] + " (could not sign in)")', text)
        # a database not ONLINE and a server whose read stopped are failures, not skips
        self.assertIn('$failed.Add("$srv / " + $db.name + " (" + $db.state_desc + ")")', text)
        self.assertIn('$failed.Add("$srv (server read stopped)")', text)
        # totals are the rows actually exported; empty tables keep a header; case-twin columns refuse
        self.assertIn("$totalRows += $got; $totalTables++", text)
        self.assertIn("if ($data.Rows.Count -eq 0)", text)
        self.assertIn("Sort-Object -Unique", text)

    def test_paths_collision_free_by_construction(self):
        """Codex on 4589236: [dbo].[Sales/Archive] and [dbo].[Sales_Archive] (or [a.b].[c] and [a].[b.c])
        sanitized to the same file. Every level now starts with its running index; manifest.csv keeps the
        exact names."""
        text = PS1.read_text(encoding="utf-8-sig")
        self.assertIn("$srvDir = Join-Path $tables (('{0:D2}_' -f $si) + (Safe $srv))", text)
        self.assertIn("$dbDir = Join-Path $srvDir (('{0:D2}_' -f $di) + (Safe $dbn))", text)
        self.assertIn("$csv = Join-Path $dbDir (('{0:D3}_' -f $ti) + (Safe ($t.sc + '.' + $t.nm)))", text)
        loop = text[text.index("foreach ($t in $tl.Rows) {"):].splitlines()
        self.assertEqual(loop[1].strip(), "$ti++")                    # the index advances for every table
        self.assertIn("$dbn = $db.name; $di++; $ti = 0", text)
        self.assertIn("$si++; $di = 0", text)
        for col in ("index =", "instance = $srv", "database = $dbn", "schema = $t.sc", "table = $t.nm",
                    "rows = $got", "file = "):
            self.assertIn(col, text)
        self.assertIn("Join-Path $tables 'manifest.csv'", text)

    def test_summary_written_on_every_exit(self):
        text = PS1.read_text(encoding="utf-8-sig")
        self.assertIn("try { Save } catch { }; exit 3 }", text)          # trap: any unhandled error
        self.assertLess(text.index("trap {"), text.index("Say \"E-Stock database read"))
        code = [l for l in text.splitlines() if not l.lstrip().startswith("#")]
        exits = [i for i, l in enumerate(code) if re.search(r"\bexit [0-9]", l)]
        self.assertGreaterEqual(len(exits), 4)
        for i in exits:                                                # every exit has a Save right before it
            self.assertTrue(any("Save" in x for x in code[max(0, i - 3):i + 1]), code[i])

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
