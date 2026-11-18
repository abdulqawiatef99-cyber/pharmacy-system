# Read the whole E-Stock database on this PC, READ ONLY (owner request 18/11/2026, F91 / F98).
# - Never writes to E-Stock: SELECT only, WITH (NOLOCK) so the live cashier is never blocked.
# - Real data stays on this PC: full tables go to Documents\estock-db-read\<stamp>\tables\*.csv.
# - summary.txt has the structure only (tables, columns, types, row counts, filled counts,
#   min/max of dates and numbers, never any text value), so it can be read to compare features.
# - Connection: Windows sign-in to every local SQL Server instance first. If that fails, connection
#   strings found in E-Stock's own folder are tried locally; a password is never written anywhere.
$ErrorActionPreference = 'Stop'
# CSV dates and numbers in one fixed format (not the PC's Arabic culture: AM/PM marks, decimal comma)
[Threading.Thread]::CurrentThread.CurrentCulture = [Globalization.CultureInfo]::InvariantCulture
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$out = Join-Path ([Environment]::GetFolderPath('MyDocuments')) "estock-db-read\$stamp"
$tables = Join-Path $out 'tables'
New-Item -ItemType Directory -Force -Path $tables | Out-Null
$summary = Join-Path $out 'summary.txt'
$log = New-Object System.Collections.Generic.List[string]
function Say([string]$s) { $log.Add($s); Write-Host $s }
function Save { [IO.File]::WriteAllLines($summary, $log, (New-Object Text.UTF8Encoding $true)) }

Say "E-Stock database read (read only) $stamp"
Say "computer: $env:COMPUTERNAME  user: $env:USERNAME"

# 1. Candidate servers: the default instance and every named instance found in the services.
$servers = New-Object System.Collections.Generic.List[string]
foreach ($svc in Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Name -eq 'MSSQLSERVER' -or $_.Name -like 'MSSQL$*' }) {
    Say ("service " + $svc.Name + " : " + $svc.Status)
    if ($svc.Name -eq 'MSSQLSERVER') { $servers.Add('(local)') } else { $servers.Add('.\' + $svc.Name.Substring(6)) }
}
if ($servers.Count -eq 0) { $servers.Add('(local)'); $servers.Add('.\SQLEXPRESS') }

function Try-Open([string]$cs) {
    try {
        $c = New-Object System.Data.SqlClient.SqlConnection $cs
        $c.Open(); return $c
    } catch { return $null }
}

$conns = @()
foreach ($s in $servers) {
    $c = Try-Open "Server=$s;Integrated Security=True;Connect Timeout=8;Application Name=estock-db-read"
    if ($c) { Say "connected (Windows sign-in): $s"; $conns += ,@($s, $c) } else { Say "no Windows sign-in to: $s" }
}

# 2. Fallback: connection strings in E-Stock's own folder (used here only, never printed).
if ($conns.Count -eq 0) {
    $roots = @('D:\Modern Soft for Programming', 'C:\Program Files (x86)\Modern Soft for Programming', 'C:\Program Files\Modern Soft for Programming')
    foreach ($r in $roots) {
        if (-not (Test-Path $r)) { continue }
        foreach ($f in Get-ChildItem -Path $r -Recurse -File -Include *.config, *.ini, *.xml, *.udl, *.txt, *.dat -ErrorAction SilentlyContinue | Where-Object { $_.Length -lt 200KB }) {
            $text = ''
            try { $text = [IO.File]::ReadAllText($f.FullName) } catch { continue }
            foreach ($m in [regex]::Matches($text, '(?i)(Data Source|Server)\s*=[^"<>\r\n]+')) {
                $cs = $m.Value -replace '(?i)Provider\s*=[^;]*;?', ''
                $c = Try-Open $cs
                if ($c) { Say ("connected with a connection string from: " + $f.FullName); $conns += ,@($c.DataSource, $c); break }
            }
            if ($conns.Count -gt 0) { break }
        }
        if ($conns.Count -gt 0) { break }
    }
}
if ($conns.Count -eq 0) {
    Say 'RESULT: FAIL - could not open any local SQL Server. Nothing was read or changed.'
    Save; exit 2
}

function Q($conn, [string]$sql) {
    $cmd = $conn.CreateCommand(); $cmd.CommandText = $sql; $cmd.CommandTimeout = 600
    $t = New-Object System.Data.DataTable
    (New-Object System.Data.SqlClient.SqlDataAdapter $cmd).Fill($t) | Out-Null
    return ,$t
}
function Qn([string]$n) { '[' + $n.Replace(']', ']]') + ']' }

$numTypes = 'int','bigint','smallint','tinyint','decimal','numeric','money','smallmoney','float','real','bit'
$private = '(?i)phone|tel|mob|hatef|card|visa|nat|ssn|pass|pwd|هاتف|تليفون|موبايل|بطاق|رقم.?قومي|كلمة'
$dateTypes = 'date','datetime','datetime2','smalldatetime','datetimeoffset','time'
$totalRows = 0; $totalTables = 0
try {

foreach ($pair in $conns) {
    $srv = $pair[0]; $conn = $pair[1]
    $v = Q $conn "SELECT CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(50)) v, CAST(SERVERPROPERTY('Edition') AS nvarchar(100)) e"
    Say ("server $srv  version " + $v.Rows[0].v + "  " + $v.Rows[0].e)
    $dbs = Q $conn "SELECT name, state_desc FROM sys.databases WHERE database_id > 4 ORDER BY name"
    foreach ($db in $dbs.Rows) {
        if ($db.state_desc -ne 'ONLINE') { Say ("database " + $db.name + " : " + $db.state_desc + " (skipped)"); continue }
        $dbn = $db.name
        try { $conn.ChangeDatabase($dbn) } catch { Say ("database $dbn : no access (" + $_.Exception.Message + ")"); continue }
        $size = Q $conn "SELECT CAST(SUM(size) * 8.0 / 1024 AS decimal(12,2)) mb FROM sys.database_files"
        $bk = $null
        try { $bk = Q $conn "SELECT MAX(backup_finish_date) d FROM msdb.dbo.backupset WHERE database_name = DB_NAME()" } catch { }
        Say ''
        Say ("==== DATABASE $dbn  size " + $size.Rows[0].mb + " MB  last backup " + $(if ($bk) { $bk.Rows[0].d } else { 'unknown' }))
        $objs = Q $conn "SELECT type_desc, COUNT(*) n FROM sys.objects WHERE is_ms_shipped = 0 GROUP BY type_desc ORDER BY type_desc"
        foreach ($o in $objs.Rows) { Say ("  objects " + $o.type_desc + ": " + $o.n) }
        $views = Q $conn "SELECT s.name sc, v.name nm FROM sys.views v JOIN sys.schemas s ON s.schema_id = v.schema_id ORDER BY 1, 2"
        if ($views.Rows.Count) { Say ("  views: " + (($views.Rows | ForEach-Object { $_.nm }) -join ', ')) }
        $procs = Q $conn "SELECT name FROM sys.procedures ORDER BY name"
        if ($procs.Rows.Count) { Say ("  procedures: " + (($procs.Rows | ForEach-Object { $_.name }) -join ', ')) }
        $tl = Q $conn "SELECT TABLE_SCHEMA sc, TABLE_NAME nm FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE' ORDER BY 1, 2"
        $dbDir = Join-Path $tables ($dbn -replace '[\\/:*?"<>|]', '_')
        New-Item -ItemType Directory -Force -Path $dbDir | Out-Null
        foreach ($t in $tl.Rows) {
          try {
            $full = (Qn $t.sc) + '.' + (Qn $t.nm)
            $cols = Q $conn ("SELECT COLUMN_NAME c, DATA_TYPE d, CHARACTER_MAXIMUM_LENGTH l FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = N'" + $t.sc.Replace("'", "''") + "' AND TABLE_NAME = N'" + $t.nm.Replace("'", "''") + "' ORDER BY ORDINAL_POSITION")
            $parts = @('COUNT_BIG(*) AS [__rows]'); $i = 0
            foreach ($c in $cols.Rows) {
                $q = Qn $c.c
                if ($c.d -in 'text','ntext','image','xml','varbinary','binary','timestamp','geography','geometry','sql_variant','hierarchyid') { $i++; continue }
                $parts += "COUNT_BIG($q) AS [n$i]"
                if ($c.d -in $numTypes -and $c.d -ne 'bit' -and $c.c -notmatch $private) { $parts += "MIN($q) AS [lo$i]"; $parts += "MAX($q) AS [hi$i]" }
                if ($c.d -in $dateTypes) { $parts += "MIN($q) AS [lo$i]"; $parts += "MAX($q) AS [hi$i]" }
                if ($c.d -eq 'bit') { $parts += "SUM(CAST($q AS int)) AS [hi$i]" }
                $i++
            }
            $st = (Q $conn ("SELECT " + ($parts -join ', ') + " FROM $full WITH (NOLOCK)")).Rows[0]
            $rows = [int64]$st['__rows']; $totalRows += $rows; $totalTables++
            Say ''
            Say ("-- TABLE $dbn.$($t.sc).$($t.nm)  rows=$rows  columns=" + $cols.Rows.Count)
            $i = 0
            foreach ($c in $cols.Rows) {
                $line = "   " + $c.c + " : " + $c.d + $(if ($c.l -isnot [DBNull] -and $c.l -gt 0) { "(" + $c.l + ")" } else { '' })
                if ($st.Table.Columns.Contains("n$i")) { $line += "  filled=" + $st["n$i"] }
                if ($st.Table.Columns.Contains("lo$i")) { $line += "  min=" + $st["lo$i"] + "  max=" + $st["hi$i"] }
                elseif ($st.Table.Columns.Contains("hi$i")) { $line += "  true=" + $st["hi$i"] }
                Say $line; $i++
            }
            # Full table to CSV, on this PC only.
            $data = Q $conn "SELECT * FROM $full WITH (NOLOCK)"
            $csv = Join-Path $dbDir (($t.sc + '.' + $t.nm) -replace '[\\/:*?"<>|]', '_')
            $names = [string[]]($data.Columns | ForEach-Object { $_.ColumnName })
            $data.Rows | Select-Object -Property $names | Export-Csv -Path ($csv + '.csv') -NoTypeInformation -Encoding UTF8
          } catch { Say ("   ERROR reading " + $t.sc + "." + $t.nm + ": " + $_.Exception.Message) }
        }
    }
    $conn.Close()
}
} catch { Say ("RESULT: FAIL - " + $_.Exception.Message + " (nothing in E-Stock was changed)"); Save; exit 3 }
Say ''
Say "RESULT: PASS - $totalTables tables, $totalRows rows read. Nothing in E-Stock was changed."
Say "full tables (stay on this PC): $tables"
Save
Start-Process notepad.exe -ArgumentList "`"$summary`""
exit 0
