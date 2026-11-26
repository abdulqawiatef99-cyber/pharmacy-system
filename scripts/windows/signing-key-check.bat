@echo off
REM Which update signing key is in which file (READ ONLY). Never prints or copies a key.
REM Part 3 item 0 (owner 20/11/2026). One double-click; a folder or file can be dropped on it.
chcp 65001 >nul
setlocal
cd /d "%TEMP%"
set "INST=%LOCALAPPDATA%\Programs\PharmacyPlatform"
if exist "%~dp0..\..\..\python\pythonw.exe" for %%i in ("%~dp0..\..\..") do set "INST=%%~fi"
set "PY=%INST%\python\python.exe"
set "RC=3"
if not exist "%PY%" echo RESULT: FAIL - the program's Python was not found: "%PY%"
if not exist "%PY%" goto done
if not exist "%~dp0signing_key_check.py" echo RESULT: FAIL - signing_key_check.py is missing next to this file
if not exist "%~dp0signing_key_check.py" goto done
"%PY%" -I -X utf8 "%~dp0signing_key_check.py" %*
set "RC=%ERRORLEVEL%"
:done
echo.
echo Done (code %RC%). You can close this window.
pause >nul
exit /b %RC%
