@echo off
REM Launch night, step 2 of 2: bring items and opening stock from fresh E-Stock exports (one double-click).
REM Put the E-Stock exports (Excel - Data Only) in C:\estock first (docs/RELEASE_RUNBOOK.md, launch night section):
REM   items list  (name contains "qa'ima" or "dalil", e.g. the catalog report): required
REM   stock by expiry date (name contains "salahiya"): required
REM   pack sizes  (name contains "wahdat", .xlsx or .json, prepared earlier): optional
REM ASCII names catalog.xlsx / stock.xlsx / packs.xlsx work too.
REM Steps: stop the program (only its own python processes), dry run on a COPY of the database
REM (full output: %USERPROFILE%\Documents\launch-night\import-dry-run.txt), then "type YES to apply":
REM the real import takes its own verified backup first, then the stock check (manage.py verify),
REM then the program starts again. Every step is logged PASS/FAIL to Documents\launch-night\estock-import.txt;
REM the first FAIL stops the script.
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
set "SRC_DIR=C:\estock"
set "OUT=%USERPROFILE%\Documents\launch-night"
if not exist "%OUT%" mkdir "%OUT%"
set "LOG=%OUT%\estock-import.txt"
set "DRY=%OUT%\import-dry-run.txt"
set "APPLIED=%OUT%\import-apply.txt"
set "STAMP="
for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss" ^<nul') do set "STAMP=%%t"
set "WORK=%TEMP%\pharm-estock-%STAMP%"
set "STOPPED=0"
echo ==== estock import %DATE% %TIME% ==== >> "%LOG%"
echo program: %INST% >> "%LOG%"
echo data:    %DATA% >> "%LOG%"

if "%STAMP%"=="" (set "MSG=could not read the date and time" & goto fail)
if not exist "%PY%" (set "MSG=program not found: %PY%" & goto fail)
if not exist "%MANAGE%" (set "MSG=program not found: %MANAGE%" & goto fail)
if not exist "%DATA%\pharmacy.db" (set "MSG=no database yet: open the program and finish the setup screen first" & goto fail)
if not exist "%SRC_DIR%\" (set "MSG=folder %SRC_DIR% not found: put the E-Stock exports there" & goto fail)
call :log PASS "program, data and %SRC_DIR% found"

REM pick the exports by their names and copy them to plain names in a new work folder (the originals are only read)
"%PY%" -I -c "import sys,shutil,pathlib as P; src,out=P.Path(sys.argv[1]),P.Path(sys.argv[2]); keys={'catalog':('\u0642\u0627\u0626\u0645\u0629','\u062f\u0644\u064a\u0644','catalog'),'stock':('\u0635\u0644\u0627\u062d\u064a\u0629','stock'),'packs':('\u0648\u062d\u062f\u0627\u062a','packs')}; files=sorted(f for f in src.iterdir() if f.is_file() and f.suffix.lower() in ('.xlsx','.json') and not f.name.startswith('~$')); found={k:[f for f in files if any(w in f.stem.lower() for w in ws)] for k,ws in keys.items()}; [print(k, len(v), [ascii(f.name) for f in v]) for k,v in found.items()]; both=[f for f in files if sum(f in v for v in found.values())>1]; bad=[k for k in ('catalog','stock') if len(found[k])!=1 or found[k][0].suffix.lower()!='.xlsx']+(['packs'] if len(found['packs'])>1 else [])+(['a file matches two kinds'] if both else []); sys.exit('FAIL: '+', '.join(bad)) if bad else None; out.mkdir(parents=True,exist_ok=True); [shutil.copy2(v[0], out/(k+v[0].suffix.lower())) for k,v in found.items() if v]; print('OK')" "%SRC_DIR%" "%WORK%" >> "%LOG%" 2>&1
if errorlevel 1 (set "MSG=the E-Stock files in %SRC_DIR% are missing or ambiguous: exactly one items list and one stock file are needed (see the log)" & goto fail)
set "PACKS="
if exist "%WORK%\packs.xlsx" set "PACKS=--packs "%WORK%\packs.xlsx""
if exist "%WORK%\packs.json" set "PACKS=--packs "%WORK%\packs.json""
call :log PASS "E-Stock files found and copied to %WORK%"

echo.
set "OWNER="
set /p "OWNER=Owner user name (the one made on the setup screen): "
REM automated check on synthetic data only (CI): never set on the pharmacy PC
if defined PHARM_E2E_OWNER set "OWNER=%PHARM_E2E_OWNER%"
if "%OWNER%"=="" (set "MSG=no user name given" & goto fail)
set "PWARG="
REM automated check on synthetic data only (CI): never set on the pharmacy PC, where the program asks
if defined PHARM_IMPORT_PASSWORD set "PWARG=--password "%PHARM_IMPORT_PASSWORD%""
echo The program asks for that user's password twice (dry run, then apply); typing is hidden.

REM stop the program: the import writes the database itself
set "STOPPED=1"
call :stopprog
if errorlevel 1 (set "MSG=the program could not be stopped (see the log)" & goto fail)
call :log PASS "program stopped (only its own python processes)"
set "PHARM_DATA_DIR=%DATA%"

REM dry run: on a copy of the database, nothing is written to the real one
"%PY%" "%MANAGE%" import-estock --catalog "%WORK%\catalog.xlsx" --stock "%WORK%\stock.xlsx" %PACKS% --accept-zero-cost --username "%OWNER%" %PWARG% > "%DRY%" 2>&1
set "RC=%ERRORLEVEL%"
type "%DRY%"
type "%DRY%" >> "%LOG%"
if not "%RC%"=="0" (set "MSG=dry run failed, exit %RC%: nothing was imported" & goto fail)
findstr /c:"FAILED" "%DRY%" >nul
if not errorlevel 1 (set "MSG=dry run: the stock totals do not match: nothing was imported" & goto fail)
call :log PASS "dry run on a copy: totals match (full output %DRY%)"

echo.
echo Read the dry run above (also in %DRY%).
set "ANS="
set /p "ANS=Type YES to apply the import to the real database: "
if defined PHARM_E2E_ANSWER set "ANS=%PHARM_E2E_ANSWER%"
if /i not "%ANS%"=="YES" (set "MSG=not confirmed: nothing was imported" & goto fail)
call :log PASS "apply confirmed by the user"

REM the real import: import-estock --apply takes its own verified backup first
"%PY%" "%MANAGE%" import-estock --catalog "%WORK%\catalog.xlsx" --stock "%WORK%\stock.xlsx" %PACKS% --accept-zero-cost --username "%OWNER%" %PWARG% --apply > "%APPLIED%" 2>&1
set "RC=%ERRORLEVEL%"
type "%APPLIED%"
type "%APPLIED%" >> "%LOG%"
if not "%RC%"=="0" (set "MSG=import failed, exit %RC% (see %APPLIED%)" & goto fail)
findstr /c:"FAILED" "%APPLIED%" >nul
if not errorlevel 1 (set "MSG=import: the stock totals do not match (see %APPLIED%)" & goto fail)
call :log PASS "import applied after its own verified backup (output %APPLIED%)"

REM stock check: database integrity, stock balances = stock ledger, audit chain
"%PY%" "%MANAGE%" verify >> "%LOG%" 2>&1
if errorlevel 1 (set "MSG=stock check after the import found problems (see the log)" & goto fail)
call :log PASS "stock check after the import: OK"
set "PHARM_DATA_DIR="
start "" /d "%INST%\program" "%PYW%" "%INST%\program\launcher.py"
call :log PASS "ALL DONE - program started again"
echo.
echo DONE.
pause
endlocal
exit /b 0

:fail
call :log FAIL "%MSG%"
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

:stopprog
REM Only python.exe / pythonw.exe whose executable is inside this program's python folder (same rule as the installer).
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=('%INST%\python\').ToLower(); $own={ @(Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and $_.ExecutablePath -and $_.ExecutablePath.ToLower().StartsWith($d) }) }; for($i=0; $i -lt 20; $i++){ $l=& $own; if($l.Count -eq 0){ break }; foreach($p in $l){ Write-Output ('stopping pid ' + $p.ProcessId + ' ' + $p.ExecutablePath); Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }; Start-Sleep -Milliseconds 500 }; if((& $own).Count -gt 0){ Write-Output 'the program is still running'; exit 1 }; try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri 'http://127.0.0.1:8000/api/health' | Out-Null; Write-Output 'another program still answers on port 8000 (not stopped: not ours)'; exit 2 } catch { exit 0 }" <nul >> "%LOG%" 2>&1
exit /b
