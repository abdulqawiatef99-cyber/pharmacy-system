@echo off
chcp 65001 >nul
set PYTHONUTF8=1
REM Update the program from GitHub. Your data (data\ and backups\) is never touched.
REM Close the program window (start.bat / serve) before running this.
cd /d "%~dp0\..\.."
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
python manage.py update
pause
