# Read the whole E-Stock database on this PC, READ ONLY (owner request 18/11/2026, F91 / F98).
# - Never writes to E-Stock: SELECT only, WITH (NOLOCK) so the live cashier is never blocked.
# - Real data stays on this PC: full tables go to %LOCALAPPDATA%\PharmacyPlatform\estock-db-read\<stamp>\tables\
#   (LOCALAPPDATA is never synced by OneDrive, unlike Documents).
# - summary.txt has the structure only (tables, columns, types, row counts, filled counts,
#   min/max of dates and numbers, never any text value), so it can be read to compare features.
# - Connection: Windows sign-in to every local SQL Server instance first. If that fails, connection
#   strings found in E-Stock's own folder are tried locally; a password is never written anywhere.
$ErrorActionPreference = 'Stop'
# CSV dates and numbers in one fixed format (not the PC's Arabic culture: AM/PM marks, decimal comma)
[Threading.Thread]::CurrentThread.CurrentCulture = [Globalization.CultureInfo]::InvariantCulture
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$out = Join-Path $env:LOCALAPPDATA "PharmacyPlatform\estock-db-read\$stamp"
$tables = Join-Path $out 'tables'
New-Item -ItemType Directory -Force -Path $tables | Out-Null
$summary = Join-Path $out 'summary.txt'
$log = New-Object System.Collections.Generic.List[string]
function Say([string]$s) { $log.Add($s); Write-Host $s }
# manifest.csv: one row per table written, with the exact original names; the file and folder names carry a
# running index first (01_, 001_), so two names that sanitize alike can never overwrite each other.
$manifest = New-Object System.Collections.Generic.List[object]
function Safe([string]$n) { $x = $n -replace '[\\/:*?"<>|()]', '_'; if ($x.Length -gt 60) { $x = $x.Substring(0, 60) }; $x }
function Save {
    [IO.File]::WriteAllLines($summary, $log, (New-Object Text.UTF8Encoding $true))
    if ($manifest.Count) { $manifest | Export-Csv -Path (Join-Path $tables 'manifest.csv') -NoTypeInformation -Encoding UTF8 }
}
# any error not handled below still leaves summary.txt (and the manifest so far) behind
trap { Say ("RESULT: FAIL - " + $_.Exception.Message + " (nothing in E-Stock was changed)"); try { Save } catch { }; exit 3 }

Say "E-Stock database read (read only) $stamp"
Say "computer: $env:COMPUTERNAME  user: $env:USERNAME"

# 1. Candidate servers: the default instance and every named instance found in the services.
$servers = New-Object System.Collections.Generic.List[string]
$failed = New-Object System.Collections.Generic.List[string]
$want = @{}      # running instances that must be read: machine\instance (upper case) -> label
foreach ($svc in Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Name -eq 'MSSQLSERVER' -or $_.Name -like 'MSSQL$*' }) {
    Say ("service " + $svc.Name + " : " + $svc.Status)
    if ($svc.Name -eq 'MSSQLSERVER') { $servers.Add('(local)'); $id = $env:COMPUTERNAME }
    else { $servers.Add('.\' + $svc.Name.Substring(6)); $id = $env:COMPUTERNAME + '\' + $svc.Name.Substring(6) }
    if ($svc.Status -eq 'Running') { $want[$id.ToUpper()] = $svc.Name }
}
if ($servers.Count -eq 0) { $servers.Add('(local)'); $servers.Add('.\SQLEXPRESS') }

function Q($conn, [string]$sql) {
    $cmd = $conn.CreateCommand(); $cmd.CommandText = $sql; $cmd.CommandTimeout = 600
    $t = New-Object System.Data.DataTable
    (New-Object System.Data.SqlClient.SqlDataAdapter $cmd).Fill($t) | Out-Null
    return ,$t
}
function Qn([string]$n) { '[' + $n.Replace(']', ']]') + ']' }
# How many user databases this connection can really open (a sign-in may connect and still see none).
function Usable($c) {
    $n = 0
    try { $d = Q $c "SELECT name FROM sys.databases WHERE database_id > 4 AND state_desc = 'ONLINE'" } catch { return 0 }
    foreach ($r in $d.Rows) { try { $c.ChangeDatabase($r.name); $n++ } catch { } }
    try { $c.ChangeDatabase('master') } catch { }
    return $n
}

# Which instance a connection reached, the same way for every connection string: MACHINE or MACHINE\INSTANCE.
function Who($c) {
    $r = Q $c "SELECT CAST(SERVERPROPERTY('MachineName') AS nvarchar(128)) + ISNULL(N'\' + CAST(SERVERPROPERTY('InstanceName') AS nvarchar(128)), N'') n"
    return ([string]$r.Rows[0].n).ToUpper()
}

# One text form per value type, so the CSV keeps every value exactly (dates to the 100 ns tick, numbers
# round trip), whatever the PC's regional settings.
$inv = [Globalization.CultureInfo]::InvariantCulture
function Cell($v) {
    if ($null -eq $v -or $v -is [DBNull]) { return '' }
    if ($v -is [byte[]]) { return 'b64:' + [Convert]::ToBase64String($v) }
    if ($v -is [DateTime]) { return $v.ToString('o', $inv) }
    if ($v -is [DateTimeOffset]) { return $v.ToString('o', $inv) }
    if ($v -is [TimeSpan]) { return $v.ToString('c', $inv) }
    if ($v -is [double]) { return $v.ToString('G17', $inv) }
    if ($v -is [single]) { return $v.ToString('G9', $inv) }
    if ($v -is [decimal]) { return $v.ToString($inv) }
    if ($v -is [bool]) { if ($v) { return '1' } else { return '0' } }
    if ($v -is [Guid]) { return $v.ToString() }
    if ($v -is [IFormattable]) { return $v.ToString($null, $inv) }
    return [string]$v
}

function Try-Open([string]$cs) {
    try {
        $c = New-Object System.Data.SqlClient.SqlConnection $cs
        $c.Open(); return $c
    } catch { return $null }
}

$conns = @()
$have = @{}      # instances already connected (by Who)
foreach ($s in $servers) {
    $c = Try-Open "Server=$s;Integrated Security=True;Connect Timeout=8;Application Name=estock-db-read"
    if (-not $c) { Say "no Windows sign-in to: $s"; continue }
    $u = Usable $c
    if ($u -gt 0) { Say "connected (Windows sign-in): $s, $u user database(s) open"; $conns += ,@($s, $c); try { $have[(Who $c)] = 1 } catch { } }
    else { Say "connected (Windows sign-in): $s, but no user database opens with it"; $c.Close() }
}

# 2. Fallback for every running instance not reached yet (no Windows sign-in, or one that opens no user
#    database): connection strings in E-Stock's own folder (used here only, never printed).
$missing = { @($want.Keys | Where-Object { -not $have.ContainsKey($_) }).Count }
if ($conns.Count -eq 0 -or (& $missing) -gt 0) {
    $roots = @('D:\Modern Soft for Programming', 'C:\Program Files (x86)\Modern Soft for Programming', 'C:\Program Files\Modern Soft for Programming')
    foreach ($r in $roots) {
        if (-not (Test-Path $r)) { continue }
        foreach ($f in Get-ChildItem -Path $r -Recurse -File -Include *.config, *.ini, *.xml, *.udl, *.txt, *.dat -ErrorAction SilentlyContinue | Where-Object { $_.Length -lt 200KB }) {
            $text = ''
            try { $text = [IO.File]::ReadAllText($f.FullName) } catch { continue }
            foreach ($m in [regex]::Matches($text, '(?i)(Data Source|Server)\s*=[^"<>\r\n]+')) {
                $cs = $m.Value -replace '(?i)Provider\s*=[^;]*;?', ''
                if ($cs -notmatch '(?i)(Connect|Connection)\s*Timeout') { $cs = $cs.TrimEnd(';') + ';Connect Timeout=8' }
                $c = Try-Open $cs
                if (-not $c) { continue }
                $id = $null; try { $id = Who $c } catch { }
                if ($id -and -not $have.ContainsKey($id) -and (Usable $c) -gt 0) {
                    Say ("connected with a connection string from: " + $f.FullName); $conns += ,@($c.DataSource, $c); $have[$id] = 1
                } else { $c.Close() }
            }
            if ($conns.Count -gt 0 -and (& $missing) -eq 0) { break }
        }
        if ($conns.Count -gt 0 -and (& $missing) -eq 0) { break }
    }
}
foreach ($k in $want.Keys) { if (-not $have.ContainsKey($k)) { Say ("instance " + $want[$k] + ": could not sign in"); $failed.Add($want[$k] + " (could not sign in)") } }
if ($conns.Count -eq 0) {
    Say 'RESULT: FAIL - could not open any database on a local SQL Server. Nothing was read or changed.'
    Save; exit 2
}


$numTypes = 'int','bigint','smallint','tinyint','decimal','numeric','money','smallmoney','float','real','bit'
$private = '(?i)phone|tel|mob|hatef|card|visa|nat|ssn|pass|pwd|هاتف|تليفون|موبايل|بطاق|رقم.?قومي|كلمة'
$dateTypes = 'date','datetime','datetime2','smalldatetime','datetimeoffset','time'
$totalRows = 0; $totalTables = 0
try {

$si = 0
foreach ($pair in $conns) {
    $srv = $pair[0]; $conn = $pair[1]; $si++; $di = 0
    $srvDir = Join-Path $tables (('{0:D2}_' -f $si) + (Safe $srv))
  try {
    $v = Q $conn "SELECT CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(50)) v, CAST(SERVERPROPERTY('Edition') AS nvarchar(100)) e"
    Say ("server $srv  version " + $v.Rows[0].v + "  " + $v.Rows[0].e)
    $dbs = Q $conn "SELECT name, state_desc FROM sys.databases WHERE database_id > 4 ORDER BY name"
    foreach ($db in $dbs.Rows) {
        if ($db.state_desc -ne 'ONLINE') { Say ("database " + $db.name + " : " + $db.state_desc + " (not read)"); $failed.Add("$srv / " + $db.name + " (" + $db.state_desc + ")"); continue }
        $dbn = $db.name; $di++; $ti = 0
        try { $conn.ChangeDatabase($dbn) } catch { Say ("database $dbn : no access (" + $_.Exception.Message + ")"); $failed.Add("$srv / $dbn (no access)"); continue }
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
        # one folder per instance, then per database: two instances never overwrite each other
        $dbDir = Join-Path $srvDir (('{0:D2}_' -f $di) + (Safe $dbn))
        New-Item -ItemType Directory -Force -Path $dbDir | Out-Null
        foreach ($t in $tl.Rows) {
          $ti++
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
            $rows = [int64]$st['__rows']
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
            $csv = Join-Path $dbDir (('{0:D3}_' -f $ti) + (Safe ($t.sc + '.' + $t.nm)))
            $names = [string[]]($data.Columns | ForEach-Object { $_.ColumnName })
            if (@($names | Sort-Object -Unique).Count -ne $names.Count) { throw "two columns differ only in letter case" }
            if ($data.Rows.Count -eq 0) {                   # an empty table still gets its file and header
                $head = ($names | ForEach-Object { '"' + $_.Replace('"', '""') + '"' }) -join ','
                [IO.File]::WriteAllLines($csv + '.csv', [string[]]@($head), (New-Object Text.UTF8Encoding $true))
            } else {
                $data.Rows | ForEach-Object {
                    $o = [ordered]@{}
                    foreach ($n in $names) { $o[$n] = Cell $_[$n] }
                    [pscustomobject]$o
                } | Export-Csv -Path ($csv + '.csv') -NoTypeInformation -Encoding UTF8
            }
            $got = $data.Rows.Count
            if ($got -ne $rows) { Say ("   note: $got rows exported (count was $rows; the cashier changed it meanwhile)") }
            $totalRows += $got; $totalTables++               # counted only once its CSV is written
            $manifest.Add([pscustomobject][ordered]@{ index = ('{0:D2}/{1:D2}/{2:D3}' -f $si, $di, $ti); instance = $srv
                database = $dbn; schema = $t.sc; table = $t.nm; rows = $got; file = $csv.Substring($tables.Length + 1) + '.csv' })
          } catch {
            Say ("   ERROR reading " + $t.sc + "." + $t.nm + ": " + $_.Exception.Message)
            $failed.Add("$srv / $dbn.$($t.sc).$($t.nm)")
          }
        }
    }
  } catch { Say ("server $srv : ERROR " + $_.Exception.Message); $failed.Add("$srv (server read stopped)") }
    $conn.Close()
}
} catch { Say ("RESULT: FAIL - " + $_.Exception.Message + " (nothing in E-Stock was changed)"); Save; exit 3 }
Say ''
if ($failed.Count -gt 0) {
    Say ("RESULT: PARTIAL - $totalTables tables, $totalRows rows read; " + $failed.Count + " not read:")
    foreach ($x in $failed) { Say ("   " + $x) }
    Say "Nothing in E-Stock was changed."
} else {
    Say "RESULT: PASS - $totalTables tables, $totalRows rows read. Nothing in E-Stock was changed."
}
Say "full tables (stay on this PC): $tables  (exact names: manifest.csv)"
Say "note: read WITH (NOLOCK) while the cashier works; a row changed during the read can be missing or twice."
Save
Start-Process notepad.exe -ArgumentList "`"$summary`""
if ($failed.Count -gt 0) { exit 4 }
exit 0
