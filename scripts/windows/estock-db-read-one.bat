@echo off
REM Re-read ONLY the E-Stock tables the last estock-db-read run listed as not read (owner 19/11/2026).
REM Read only, never changes E-Stock. Results: %%LOCALAPPDATA%%\PharmacyPlatform\estock-db-read\<time>
chcp 65001 >nul
cd /d "%TEMP%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0estock-db-read.ps1" -RetryFailed <nul
echo.
echo Done (code %ERRORLEVEL%). You can close this window.
pause >nul
