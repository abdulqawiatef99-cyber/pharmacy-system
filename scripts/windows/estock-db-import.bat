@echo off
REM Launch night: move the whole E-Stock database into the program (F91), one double-click.
REM First run estock-db-read.bat (it exports every E-Stock table to %LOCALAPPDATA%\PharmacyPlatform\estock-db-read\<time>).
REM This script takes the NEWEST full read there; if that read missed tables (PARTIAL), it reads only those again
REM first (what estock-db-read-one.bat does), so one double-click is enough. Everything it writes stays in %LOCALAPPDATA%\PharmacyPlatform\launch-night
REM (real customer and supplier names: never in Documents, which OneDrive may upload).
REM Optional files in that folder, used when present:
REM   openings-confirmed.xlsx  the owner's filled copy of openings-to-confirm.xlsx (confirmed balances / credit limits)
REM   packs.json or packs.xlsx pack sizes a person decided, for items whose E-Stock unit conversions are broken
REM   stock-source.txt         one line such as Product_Amount:3, only when the dry run says the live stock table is unclear
REM Steps: stop the program (only its own python processes), dry run on a COPY of the database
REM (reports in the reports folder below), then "type YES to apply": the real move takes its own verified backup
REM first, then the stock check (manage.py verify), then the program starts again. Every step is logged PASS/FAIL
REM to launch-night\estock-db-import.txt; the first FAIL stops the script.
chcp 65001 >nul
set PYTHONUTF8=1
setlocal
cd /d "%TEMP%"
set "INST=%LOCALAPPDATA%\Programs\PharmacyPlatform"
if exist "%~dp0..\..\..\python\pythonw.exe" for %%i in ("%~dp0..\..\..") do set "INST=%%~fi"
set "PY=%INST%\python\python.exe"
set "PYW=%INST%\python\pythonw.exe"
set "MANAGE=%INST%\program\manage.py"
set "DATA=%LOCALAPPDATA%\PharmacyPlatform\data"
set "READS=%LOCALAPPDATA%\PharmacyPlatform\estock-db-read"
set "OUT=%LOCALAPPDATA%\PharmacyPlatform\launch-night"
if not exist "%OUT%" mkdir "%OUT%"
set "LOG=%OUT%\estock-db-import.txt"
set "PORT=%PHARM_PORT%"
if not defined PHARM_PORT set "PORT=8000"
set "DRY=%OUT%\estock-db-dry-run.txt"
set "APPLIED=%OUT%\estock-db-apply.txt"
set "STAMP="
for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss" ^<nul') do set "STAMP=%%t"
set "REP=%OUT%\reports-%STAMP%"
REM never show an earlier run's owner report as this one's
if exist "%OUT%\owner-report.txt" del /q "%OUT%\owner-report.txt"
set "OWNERSHOWN="
set "SHOW="
set "STOPPED=0"
echo ==== estock db import %DATE% %TIME% ==== >> "%LOG%"
echo program: %INST% >> "%LOG%"
echo data:    %DATA% >> "%LOG%"

if "%STAMP%"=="" (set "MSG=could not read the date and time" & goto fail)
if not exist "%PY%" (set "MSG=program not found: %PY%" & goto fail)
if not exist "%MANAGE%" (set "MSG=program not found: %MANAGE%" & goto fail)
if not exist "%DATA%\pharmacy.db" (set "MSG=no database yet: open the program and finish the setup screen first" & goto fail)
call :log PASS "program and data found"

REM the newest full E-Stock read (+ the retries made for it); exit 3 = some tables were not read
if not exist "%READS%\" (set "MSG=no E-Stock read in %READS%: run estock-db-read.bat first" & goto fail)
"%PY%" "%MANAGE%" import-estock-db --dir "%READS%" --status >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" if not "%RC%"=="3" (set "MSG=no usable E-Stock read in %READS%: run estock-db-read.bat first (see the log)" & goto fail)
if "%RC%"=="3" call :reread
"%PY%" "%MANAGE%" import-estock-db --dir "%READS%" --status >> "%LOG%" 2>&1
if errorlevel 1 (set "MSG=some E-Stock tables still could not be read (see the log): nothing was moved" & goto fail)
call :log PASS "E-Stock read complete: %READS% (newest full read + its retries)"

set "EXTRA="
REM the confirmed balances are read once, at the start: remember the file as it was (time and size)
set "CONFSTAMP=none"
if exist "%OUT%\openings-confirmed.xlsx" for %%f in ("%OUT%\openings-confirmed.xlsx") do set "CONFSTAMP=%%~tf %%~zf"
if exist "%OUT%\openings-confirmed.xlsx" set "EXTRA=%EXTRA% --confirm "%OUT%\openings-confirmed.xlsx""
if exist "%OUT%\packs.json" set "EXTRA=%EXTRA% --packs "%OUT%\packs.json""
if exist "%OUT%\packs.xlsx" set "EXTRA=%EXTRA% --packs "%OUT%\packs.xlsx""
set "SRC="
if exist "%OUT%\stock-source.txt" set /p SRC=<"%OUT%\stock-source.txt"
if not "%SRC%"=="" set "EXTRA=%EXTRA% --stock-source %SRC%"
echo options:%EXTRA% >> "%LOG%"

echo.
set "OWNER="
REM automated check on synthetic data only (CI): never set on the pharmacy PC
if defined PHARM_E2E_OWNER set "OWNER=%PHARM_E2E_OWNER%"
set "TRIES=0"
:askowner
if defined OWNER goto haveowner
set /a TRIES+=1
if %TRIES% gtr 5 (set "MSG=no user name given" & goto fail)
REM update 1: a stray Enter is not an answer: ask again
set /p "OWNER=Owner user name (the one made on the setup screen): "
goto askowner
:haveowner
set "PWARG="
REM automated check on synthetic data only (CI): never set on the pharmacy PC, where the password is asked below
if defined PHARM_IMPORT_PASSWORD set "PWARG=--password "%PHARM_IMPORT_PASSWORD%""
REM update 1: the password is asked ONCE, here, before anything runs; the prompt goes to this window itself
REM (Python's hidden prompt), and the dry run and the move read it from PHARM_OWNER_PASSWORD, never from the
REM command line. Cleared on every way out.
set "PHARM_OWNER_PASSWORD="
if defined PHARM_IMPORT_PASSWORD goto havepw
for /f "usebackq delims=" %%p in (`""%PY%" -X utf8 "%MANAGE%" ask-password --username "%OWNER%""`) do set "PHARM_OWNER_PASSWORD=%%p"
if not defined PHARM_OWNER_PASSWORD (set "MSG=no password given" & goto fail)
:havepw

set "STOPPED=1"
call :stopprog
if errorlevel 1 (set "MSG=the program could not be stopped (see the log)" & goto fail)
call :log PASS "program stopped (only its own python processes)"
set "PHARM_DATA_DIR=%DATA%"

REM dry run: on a copy of the database, nothing is written to the real one; owner-report.txt (what needs the
REM owner first, how much matched, the rest by kind) is copied next to the balances file and opened
"%PY%" "%MANAGE%" import-estock-db --dir "%READS%" %EXTRA% --out "%REP%-dry" --username "%OWNER%" %PWARG% > "%DRY%" 2>&1
set "RC=%ERRORLEVEL%"
type "%DRY%" >> "%LOG%"
if exist "%REP%-dry\openings-to-confirm.xlsx" copy /y "%REP%-dry\openings-to-confirm.xlsx" "%OUT%\openings-to-confirm.xlsx" >nul
if "%RC%"=="4" goto rehearsal
if not "%RC%"=="0" (set "SHOW=%DRY%" & set "MSG=dry run failed, exit %RC%: nothing was moved (reports in %REP%-dry)" & goto fail)
findstr /c:"RECONCILIATION: OK" "%DRY%" >nul
if errorlevel 1 (set "MSG=dry run: the reconciliation is not OK: nothing was moved (reports in %REP%-dry)" & goto fail)
call :log PASS "dry run on a copy: reconciliation OK (reports %REP%-dry)"
call :owner "%REP%-dry"

echo.
echo Read the owner report first: %OUT%\owner-report.txt (opened in Notepad).
echo The dry run is in %DRY%. Balances to confirm: %OUT%\openings-to-confirm.xlsx
set "ANS="
if defined PHARM_E2E_ANSWER set "ANS=%PHARM_E2E_ANSWER%"
:askyes
REM update 1: a stray Enter is not "no": ask again; anything typed other than YES stops
if defined ANS goto haveyes
set /p "ANS=Type YES to apply the move to the real database (NO to stop): "
goto askyes
:haveyes
if /i not "%ANS%"=="YES" (set "MSG=not confirmed: nothing was moved" & goto fail)
call :log PASS "apply confirmed by the user"
set "NOWSTAMP=none"
if exist "%OUT%\openings-confirmed.xlsx" for %%f in ("%OUT%\openings-confirmed.xlsx") do set "NOWSTAMP=%%~tf %%~zf"
if not "%NOWSTAMP%"=="%CONFSTAMP%" (set "MSG=openings-confirmed.xlsx appeared or changed after the dry run: run this again so the dry run uses it: nothing was moved" & goto fail)

REM the real move: import-estock-db --apply takes its own verified backup first
"%PY%" "%MANAGE%" import-estock-db --dir "%READS%" %EXTRA% --out "%REP%" --username "%OWNER%" %PWARG% --apply > "%APPLIED%" 2>&1
set "RC=%ERRORLEVEL%"
type "%APPLIED%" >> "%LOG%"
if not "%RC%"=="0" (set "SHOW=%APPLIED%" & set "MSG=move failed, exit %RC%: restore the backup named in %APPLIED% before any sale" & goto fail)
findstr /c:"RECONCILIATION: OK" "%APPLIED%" >nul
if errorlevel 1 (set "MSG=move: the reconciliation is not OK: restore the backup named in %APPLIED%" & goto fail)
call :owner "%REP%" quiet
call :log PASS "move applied after its own verified backup (reports %REP%)"

"%PY%" "%MANAGE%" verify >> "%LOG%" 2>&1
if errorlevel 1 (set "MSG=stock check after the move found problems (see the log)" & goto fail)
call :log PASS "stock check after the move: OK"
set "PHARM_DATA_DIR="
start "" /d "%INST%\program" "%PYW%" "%INST%\program\launcher.py"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok=$false; for($i=0; $i -lt 90; $i++){ try { $h=Invoke-RestMethod -TimeoutSec 2 -Uri 'http://127.0.0.1:%PORT%/api/health'; if($h.ok -eq $true){ $ok=$true; break } } catch { }; Start-Sleep -Seconds 1 }; if(-not $ok){ Write-Output 'the program did not answer on port %PORT% within 90 seconds'; exit 1 }; Write-Output ('program answers, version ' + $h.version); exit 0" <nul >> "%LOG%" 2>&1
if errorlevel 1 (set "MSG=the move is done but the program did not answer after it (see the log)" & goto fail)
call :log PASS "program started again and answers on port %PORT%"
set "PHARM_OWNER_PASSWORD="
call :log PASS "ALL DONE - program started again"
echo.
echo DONE.
pause
endlocal
exit /b 0

:rehearsal
REM the program still holds trial data, so the move refuses its database (exit 4). The daytime trial runs on a
REM NEW EMPTY database in a temp folder instead: the program's database is not opened, nothing is written to it,
REM and there is no YES. Tonight: launch-fresh-start.bat first, then this again for the real move.
call :log NOTE "the program's database holds trial data: rehearsal on a new empty database (nothing is written)"
"%PY%" "%MANAGE%" import-estock-db --dir "%READS%" %EXTRA% --out "%REP%-rehearsal" --rehearsal > "%DRY%" 2>&1
set "RC=%ERRORLEVEL%"
type "%DRY%" >> "%LOG%"
findstr /b /c:"RECONCILIATION" "%DRY%"
if exist "%REP%-rehearsal\openings-to-confirm.xlsx" copy /y "%REP%-rehearsal\openings-to-confirm.xlsx" "%OUT%\openings-to-confirm.xlsx" >nul
call :owner "%REP%-rehearsal"
if not "%RC%"=="0" (set "MSG=rehearsal failed, exit %RC%: nothing was moved (reports in %REP%-rehearsal)" & goto fail)
set "PHARM_OWNER_PASSWORD="
call :log PASS "REHEARSAL ONLY: nothing was moved (reports %REP%-rehearsal). Tonight: launch-fresh-start.bat first, then this again"
set "PHARM_DATA_DIR="
if "%STOPPED%"=="1" if exist "%PYW%" start "" /d "%INST%\program" "%PYW%" "%INST%\program\launcher.py"
echo.
echo REHEARSAL DONE. Read %OUT%\owner-report.txt and fill openings-confirmed.xlsx; nothing was moved.
pause
endlocal
exit /b 0

:fail
call :log FAIL "%MSG%"
REM the real move's own report when it ran (a failed apply must never show the trial's 100%), else the trial's
if exist "%REP%\owner-report.txt" call :owner "%REP%"
if not exist "%REP%\owner-report.txt" if not defined OWNERSHOWN call :owner "%REP%-dry"
set "PHARM_OWNER_PASSWORD="
REM update 1: the program's own words (Arabic) in Notepad, which shows them; an old cmd window shows ???
if defined SHOW if exist "%SHOW%" if not defined PHARM_E2E_ANSWER start "" notepad "%SHOW%"
set "PHARM_DATA_DIR="
if "%STOPPED%"=="1" if exist "%PYW%" start "" /d "%INST%\program" "%PYW%" "%INST%\program\launcher.py"
echo.
echo FAILED: %MSG%
echo Details: %LOG%
pause
endlocal
exit /b 1

:log
REM redirection first: a message ending in a digit must not become a handle number (" 1>>")
>> "%LOG%" echo %DATE% %TIME% %~1 - %~2
echo %~1 - %~2
exit /b 0

:owner
REM owner-report.txt (what needs the owner first, how much matched, the rest by kind): next to the balances
REM file, opened in Notepad unless "quiet" or an automated run
if not exist "%~1\owner-report.txt" exit /b 0
copy /y "%~1\owner-report.txt" "%OUT%\owner-report.txt" >nul
if "%~2"=="" if not defined PHARM_E2E_ANSWER start "" notepad "%OUT%\owner-report.txt"
set "OWNERSHOWN=1"
exit /b 0

:reread
REM the last read missed tables: read only those again (read only, E-Stock keeps selling), once
call :log NOTE "the last E-Stock read missed some tables: reading only those again now"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0estock-db-read.ps1" -RetryFailed <nul >> "%LOG%" 2>&1
exit /b 0

:stopprog
REM Only python.exe / pythonw.exe whose executable is inside this program's python folder (same rule as the installer).
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=('%INST%\python\').ToLower(); $own={ @(Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and $_.ExecutablePath -and $_.ExecutablePath.ToLower().StartsWith($d) }) }; for($i=0; $i -lt 20; $i++){ $l=& $own; if($l.Count -eq 0){ break }; foreach($p in $l){ Write-Output ('stopping pid ' + $p.ProcessId + ' ' + $p.ExecutablePath); Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }; Start-Sleep -Milliseconds 500 }; if((& $own).Count -gt 0){ Write-Output 'the program is still running'; exit 1 }; $c=New-Object System.Net.Sockets.TcpClient; try { $c.Connect('127.0.0.1', %PORT%) } catch { exit 0 }; $c.Close(); Write-Output 'another program still listens on port %PORT% (not stopped: not ours)'; exit 2" <nul >> "%LOG%" 2>&1
exit /b
