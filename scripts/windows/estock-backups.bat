@echo off
REM E-Stock backups on this PC, READ ONLY (owner 19/11/2026). One double-click.
REM Lists every .bak file on D:\ and the Desktop, newest first, with date and size, and says clearly when the
REM newest database backup is older than 24 hours (E-Stock's own hourly backup is then probably not running).
REM It only reads file names, sizes, dates and the first 8 bytes of each .bak file. It writes nothing, changes
REM nothing, never connects to SQL Server or E-Stock, and sends nothing anywhere.
chcp 65001 >nul
setlocal
cd /d "%TEMP%"
set "INST=%LOCALAPPDATA%\Programs\PharmacyPlatform"
if exist "%~dp0..\..\..\python\pythonw.exe" for %%i in ("%~dp0..\..\..") do set "INST=%%~fi"
set "PY=%INST%\python\python.exe"
set "RC=3"
if not exist "%PY%" echo RESULT: FAIL - the program's Python was not found: "%PY%"
if not exist "%PY%" goto done
if not exist "%~dp0estock-backups.py" echo RESULT: FAIL - estock-backups.py is missing next to this file
if not exist "%~dp0estock-backups.py" goto done
"%PY%" -I -X utf8 "%~dp0estock-backups.py"
set "RC=%ERRORLEVEL%"
:done
echo.
echo Done (code %RC%: 0 = fresh backup, 1 = older than 24 hours, 2 = no backup found, 3 = could not run).
echo You can close this window.
pause >nul
exit /b %RC%
