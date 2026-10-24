@echo off
REM Nightly backup - add to Windows Task Scheduler (e.g. 23:30). Keeps the zip in backups\
cd /d "%~dp0\..\.."
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
python manage.py backup --dest backups || echo BACKUP FAILED
