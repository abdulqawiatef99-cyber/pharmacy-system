@echo off
REM Test PC only (Heikal PC, synthetic data). One double-click runs:
REM   1) restore drill: the newest backup is restored into a NEW temporary folder and verified
REM      (the program's live data folder is never written to);
REM   2) scale test small then medium, 1 year of synthetic activity each (#253), timings only.
REM Results: %USERPROFILE%\Documents\heikal-tests\ (restore-drill.txt, scale-small.txt, scale-medium.txt, DONE.txt)
REM Usage: heikal-tests.bat            (restore drill + small + medium)
REM        heikal-tests.bat restore    (restore drill only)
REM        heikal-tests.bat giant      (restore drill + giant size, many hours: leave it overnight)
chcp 65001 >nul
set PYTHONUTF8=1
setlocal EnableDelayedExpansion
cd /d "%~dp0\..\.."
set "PY=%~dp0..\..\..\python\python.exe"
if not exist "%PY%" set "PY=python"
set "OUT=%USERPROFILE%\Documents\heikal-tests"
if not exist "%OUT%" mkdir "%OUT%"
if exist "%OUT%\DONE.txt" del "%OUT%\DONE.txt"
set "BK=%LOCALAPPDATA%\PharmacyPlatform\backups"
set "DRILL=%TEMP%\pharm-restore-drill"
set "LOG=%OUT%\restore-drill.txt"

echo ==== restore drill %DATE% %TIME% ==== > "%LOG%"
set "ZIP="
for /f "delims=" %%f in ('dir /b /o-d "%BK%\pharmacy-backup-*.zip" 2^>nul') do if not defined ZIP set "ZIP=%BK%\%%f"
if not defined ZIP (
  echo RESULT: FAIL - no backup found in %BK% >> "%LOG%"
  goto scale
)
echo backup: !ZIP! >> "%LOG%"
"%PY%" manage.py verify "!ZIP!" >> "%LOG%" 2>&1
"%PY%" manage.py preflight "!ZIP!" >> "%LOG%" 2>&1
if exist "%DRILL%" rmdir /s /q "%DRILL%"
mkdir "%DRILL%"
REM only this window sees PHARM_DATA_DIR: the restore and the check run on the temporary copy
set "PHARM_DATA_DIR=%DRILL%\data"
set "T0=%TIME%"
"%PY%" manage.py restore "!ZIP!" --yes --pre-dir "%DRILL%\pre" >> "%LOG%" 2>&1
set "RC1=!ERRORLEVEL!"
"%PY%" manage.py verify >> "%LOG%" 2>&1
set "RC2=!ERRORLEVEL!"
set "PHARM_DATA_DIR="
echo started %T0% finished %TIME% >> "%LOG%"
if "!RC1!!RC2!"=="00" (echo RESULT: PASS - restored into a new folder and verified >> "%LOG%") else (echo RESULT: FAIL - restore exit !RC1!, verify exit !RC2! >> "%LOG%")
rmdir /s /q "%DRILL%"

:scale
if /i "%~1"=="restore" goto done
set "WORK=%TEMP%\pharm-scale"
REM scale.py makes its temporary folder inside --work, which must already exist
if not exist "%WORK%" mkdir "%WORK%"
if /i "%~1"=="giant" (
  "%PY%" scripts\perf\scale.py --profile giant --years 1 --out-txt "%OUT%\scale-giant.txt" --work "%WORK%" > "%OUT%\scale-giant-log.txt" 2>&1
  goto done
)
"%PY%" scripts\perf\scale.py --profile small --years 1 --out-txt "%OUT%\scale-small.txt" --work "%WORK%" > "%OUT%\scale-small-log.txt" 2>&1
"%PY%" scripts\perf\scale.py --profile medium --years 1 --out-txt "%OUT%\scale-medium.txt" --work "%WORK%" > "%OUT%\scale-medium-log.txt" 2>&1

:done
echo finished %DATE% %TIME% > "%OUT%\DONE.txt"
endlocal
