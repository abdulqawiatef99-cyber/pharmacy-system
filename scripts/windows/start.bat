@echo off
REM Start the pharmacy platform and open the browser
cd /d "%~dp0\..\.."
start "" http://127.0.0.1:8000
python manage.py serve
