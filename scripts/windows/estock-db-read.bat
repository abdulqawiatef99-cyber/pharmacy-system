@echo off
REM Read the whole E-Stock database on this PC, READ ONLY (owner 18/11/2026, F91/F98). One double-click.
REM Never changes E-Stock. Full tables stay on this PC: Documents\estock-db-read\<time>\tables\*.csv
REM summary.txt (structure, counts, date/number ranges, no text values) opens in Notepad at the end.
chcp 65001 >nul
cd /d "%TEMP%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0estock-db-read.ps1" <nul
echo.
echo Done (code %ERRORLEVEL%). You can close this window.
pause >nul
