@echo off
REM Daily backup - schedule with Windows Task Scheduler (e.g. every night 23:30)
cd /d "%~dp0\..\.."
python manage.py backup --dest backups
python manage.py verify
