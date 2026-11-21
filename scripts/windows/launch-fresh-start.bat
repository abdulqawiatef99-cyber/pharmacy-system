@echo off
REM Launch night, step 1 of 2: start the real program from a CLEAN database (one double-click).
REM   1) stop the running program: only python.exe / pythonw.exe from this program's own install folder
REM      (E-Stock and every other program are never touched);
REM   2) take a backup of the current data and verify it (also copied to D:\PharmacyPlatform-backups when D: exists);
REM   3) RENAME (never delete) %LOCALAPPDATA%\PharmacyPlatform\data to data-trial-DATE-TIME;
REM      the program code chosen by the update button (data\code-releases) and the small settings files are
REM      copied into the new empty data folder (installed_version, network, backup_settings: the backup
REM      folder and schedule chosen by the owner), so the same program version keeps running;
REM   4) start the program again: it shows the first-run setup screen.
REM Every step is logged PASS/FAIL to %USERPROFILE%\Documents\launch-night\fresh-start.txt.
REM The first FAIL stops the script; nothing after it runs. A FAIL before step 3 leaves the data folder untouched;
REM a FAIL after the rename puts the old data folder back under its own name.
REM Next: finish the setup screen, then double-click estock-import.bat.
chcp 65001 >nul
set PYTHONUTF8=1
setlocal
REM This file may live inside the data folder (an update puts it in data\code-releases), and that folder is renamed
REM below: cmd reads a batch file line by line, so run from a copy in TEMP, with the current folder outside the data.
if /i "%~1"=="--from-temp" goto main
copy /y "%~f0" "%TEMP%\pharm-launch-fresh-start.bat" >nul
if errorlevel 1 goto nocopy
cd /d "%TEMP%"
"%TEMP%\pharm-launch-fresh-start.bat" --from-temp "%~dp0."
:nocopy
echo FAIL - could not copy this script to %TEMP%. Nothing was changed.
pause
exit /b 1

:main
cd /d "%TEMP%"
set "SRC=%~2"
set "INST=%LOCALAPPDATA%\Programs\PharmacyPlatform"
if exist "%SRC%\..\..\..\python\pythonw.exe" for %%i in ("%SRC%\..\..\..") do set "INST=%%~fi"
set "PY=%INST%\python\python.exe"
set "PYW=%INST%\python\pythonw.exe"
set "MANAGE=%INST%\program\manage.py"
set "HOMEDIR=%LOCALAPPDATA%\PharmacyPlatform"
set "DATA=%HOMEDIR%\data"
set "OUT=%USERPROFILE%\Documents\launch-night"
if not exist "%OUT%" mkdir "%OUT%"
set "LOG=%OUT%\fresh-start.txt"
REM the port the launcher serves on (launcher.py reads PHARM_PORT, default 8000)
set "PORT=%PHARM_PORT%"
if not defined PHARM_PORT set "PORT=8000"
set "STAMP="
for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss" ^<nul') do set "STAMP=%%t"
set "TRIAL=data-trial-%STAMP%"
set "BKDIR=%HOMEDIR%\backups\fresh-start-%STAMP%"
set "RENAMED=0"
set "STOPPED=0"
echo ==== fresh start %DATE% %TIME% ==== >> "%LOG%"
echo program: %INST% >> "%LOG%"
echo data:    %DATA% >> "%LOG%"

if "%STAMP%"=="" (set "MSG=could not read the date and time" & goto fail)
if not exist "%PY%" (set "MSG=program not found: %PY%" & goto fail)
if not exist "%MANAGE%" (set "MSG=program not found: %MANAGE%" & goto fail)
if not exist "%DATA%\pharmacy.db" (set "MSG=no database in %DATA%: nothing to start fresh from" & goto fail)
if exist "%HOMEDIR%\%TRIAL%" (set "MSG=%TRIAL% already exists" & goto fail)
call :log PASS "program and data found"

echo.
echo This keeps today's data in a folder named %TRIAL% and starts the program EMPTY.
echo E-Stock is not touched.
REM the pharmacy name must be typed, not just YES (#324 R2); once the database has sales after the E-Stock import
REM (the live data after launch) Python lists them (dates, totals; also in Notepad) and goes on only on YES
REM (update 1, owner 21/11). Python asks: set /p cannot read Arabic typing
set "PHARM_DATA_DIR=%DATA%"
"%PY%" "%MANAGE%" fresh-start-check
if errorlevel 1 (set "MSG=not confirmed (the pharmacy name, or YES for the sales listed): nothing was changed" & goto fail)
set "PHARM_DATA_DIR="
call :log PASS "confirmed by the user (pharmacy name typed, no sale after an import)"

REM 1) stop the program
set "STOPPED=1"
call :stopprog
if errorlevel 1 (set "MSG=the program could not be stopped (see the log)" & goto fail)
call :log PASS "program stopped (only its own python processes)"

REM 2) backup + verify (+ copy to D:)
set "PHARM_DATA_DIR=%DATA%"
mkdir "%BKDIR%"
"%PY%" "%MANAGE%" backup --dest "%BKDIR%" >> "%LOG%" 2>&1
if errorlevel 1 (set "MSG=backup failed" & goto fail)
set "ZIP="
for /f "delims=" %%f in ('dir /b /o-d "%BKDIR%\*.zip" 2^>nul') do if not defined ZIP set "ZIP=%BKDIR%\%%f"
if not defined ZIP (set "MSG=backup file not found in %BKDIR%" & goto fail)
call :log PASS "backup written: %ZIP%"
"%PY%" "%MANAGE%" verify "%ZIP%" >> "%LOG%" 2>&1
if errorlevel 1 (set "MSG=backup did not pass verify" & goto fail)
"%PY%" "%MANAGE%" preflight "%ZIP%" >> "%LOG%" 2>&1
if errorlevel 1 (set "MSG=backup did not pass preflight (it could not be restored)" & goto fail)
call :log PASS "backup verified (verify + preflight)"
if not exist D:\ goto nod
if not exist "D:\PharmacyPlatform-backups" mkdir "D:\PharmacyPlatform-backups"
copy /y "%ZIP%" "D:\PharmacyPlatform-backups\" >> "%LOG%" 2>&1
if errorlevel 1 (set "MSG=copy of the backup to D: failed" & goto fail)
for %%z in ("%ZIP%") do set "DZIP=D:\PharmacyPlatform-backups\%%~nxz"
"%PY%" "%MANAGE%" verify "%DZIP%" >> "%LOG%" 2>&1
if errorlevel 1 (set "MSG=the copy on D: did not pass verify" & goto fail)
call :log PASS "backup copied to D: and verified: %DZIP%"
goto afterd
:nod
call :log PASS "no drive D: on this PC, backup kept in %BKDIR% only"
:afterd

REM which program code is selected now (the same must be selected after the rename)
"%PY%" -I -c "import sys,pathlib as p; sys.path.insert(0, sys.argv[1]); import release_bootstrap as r; print(r.current_root(p.Path(sys.argv[1]), p.Path(sys.argv[2])).name)" "%INST%\program" "%DATA%\code-releases" > "%TEMP%\pharm-code-before.txt" 2>> "%LOG%"
if errorlevel 1 (set "MSG=could not read which program version is selected" & goto fail)
set "PHARM_DATA_DIR="

REM 3) rename the data folder (never delete)
ren "%DATA%" "%TRIAL%"
if errorlevel 1 (set "MSG=rename failed: close every window or file from the data folder and try again" & goto fail)
if exist "%DATA%" (set "MSG=the data folder is still there after the rename" & goto fail)
if not exist "%HOMEDIR%\%TRIAL%\pharmacy.db" (set "MSG=renamed folder not found" & goto fail)
set "RENAMED=1"
call :log PASS "data folder renamed to %HOMEDIR%\%TRIAL% (nothing deleted)"

REM keep the same program version and its settings in the new empty data folder (no pharmacy data)
mkdir "%DATA%"
if errorlevel 1 (set "MSG=could not create the new data folder" & goto fail)
if exist "%HOMEDIR%\%TRIAL%\code-releases" robocopy "%HOMEDIR%\%TRIAL%\code-releases" "%DATA%\code-releases" /E /R:2 /W:1 /NFL /NDL /NP >> "%LOG%" 2>&1
if errorlevel 8 (set "MSG=copy of the program version folder failed" & goto fail)
REM a settings file that cannot be copied is a FAIL: the program would start with the default backup folder (#324 R13)
for %%n in (installed_version.json network.json backup_settings.json) do if exist "%HOMEDIR%\%TRIAL%\%%n" copy /y "%HOMEDIR%\%TRIAL%\%%n" "%DATA%\%%n" >nul || (set "MSG=could not copy the settings file %%n" & goto fail)
"%PY%" -I -c "import sys,pathlib as p; sys.path.insert(0, sys.argv[1]); import release_bootstrap as r; print(r.current_root(p.Path(sys.argv[1]), p.Path(sys.argv[2])).name)" "%INST%\program" "%DATA%\code-releases" > "%TEMP%\pharm-code-after.txt" 2>> "%LOG%"
if errorlevel 1 (set "MSG=the copied program version did not pass its check" & goto fail)
fc "%TEMP%\pharm-code-before.txt" "%TEMP%\pharm-code-after.txt" >nul
if errorlevel 1 (set "MSG=a different program version would start" & goto fail)
call :log PASS "same program version kept in the new data folder"

REM 4) start the program on the empty data: it must answer and show the first-run setup screen
start "" /d "%INST%\program" "%PYW%" "%INST%\program\launcher.py"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok=$false; for($i=0; $i -lt 90; $i++){ try { $s=Invoke-RestMethod -TimeoutSec 2 -Uri 'http://127.0.0.1:%PORT%/api/setup'; $ok=$true; break } catch { Start-Sleep -Seconds 1 } }; if(-not $ok){ Write-Output 'the program did not answer within 90 seconds'; exit 1 }; Write-Output ('first-run setup needed: ' + $s.needed); if($s.needed -ne $true){ exit 2 }; exit 0" <nul >> "%LOG%" 2>&1
if errorlevel 1 (set "MSG=the program did not start on the empty data with the setup screen" & goto fail)
call :log PASS "program started on an empty database: first-run setup screen"
call :log PASS "ALL DONE - old data kept in %HOMEDIR%\%TRIAL%, backup %ZIP%"
echo.
echo DONE. Finish the setup screen in the browser, then double-click estock-import.bat.
pause
endlocal
exit /b 0

:fail
call :log FAIL "%MSG%"
set "PHARM_DATA_DIR="
if not "%RENAMED%"=="1" goto failstart
REM after the rename: put the old data folder back under its own name (the new folder is renamed, not deleted)
call :stopprog
if not exist "%DATA%" goto putback
ren "%DATA%" "data-failed-%STAMP%"
if errorlevel 1 (call :log FAIL "could not move the new data folder aside: old data stays in %HOMEDIR%\%TRIAL%" & goto failend)
:putback
ren "%HOMEDIR%\%TRIAL%" data
if errorlevel 1 (call :log FAIL "could not rename %TRIAL% back to data: rename it by hand" & goto failend)
call :log PASS "old data folder put back as %DATA%"
:failstart
REM the program runs again on the data it had before
if "%STOPPED%"=="1" if exist "%PYW%" start "" /d "%INST%\program" "%PYW%" "%INST%\program\launcher.py"
:failend
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
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=('%INST%\python\').ToLower(); $own={ @(Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and $_.ExecutablePath -and $_.ExecutablePath.ToLower().StartsWith($d) }) }; for($i=0; $i -lt 20; $i++){ $l=& $own; if($l.Count -eq 0){ break }; foreach($p in $l){ Write-Output ('stopping pid ' + $p.ProcessId + ' ' + $p.ExecutablePath); Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }; Start-Sleep -Milliseconds 500 }; if((& $own).Count -gt 0){ Write-Output 'the program is still running'; exit 1 }; $c=New-Object System.Net.Sockets.TcpClient; try { $c.Connect('127.0.0.1', %PORT%) } catch { exit 0 }; $c.Close(); Write-Output 'another program still listens on port %PORT% (not stopped: not ours)'; exit 2" <nul >> "%LOG%" 2>&1
exit /b
