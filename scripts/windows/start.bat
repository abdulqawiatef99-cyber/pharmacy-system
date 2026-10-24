@echo off
chcp 65001 >nul
set PYTHONUTF8=1
REM Start the pharmacy platform and open the browser
cd /d "%~dp0\..\.."
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
start "" http://127.0.0.1:8000
python manage.py serve
