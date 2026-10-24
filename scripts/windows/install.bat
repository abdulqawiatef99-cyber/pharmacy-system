@echo off
chcp 65001 >nul
set PYTHONUTF8=1
REM ---- Pharmacy platform: first install on a Windows PC (run once) ----
REM Requires Python 3.11+ from python.org ("Add python.exe to PATH" ticked).
cd /d "%~dp0\..\.."
python --version || (echo Python is not installed & pause & exit /b 1)
python -m venv .venv || (echo could not create virtual environment & pause & exit /b 1)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt || (echo package install failed & pause & exit /b 1)
pip install pywin32
echo.
echo ---- self test (uses a temporary database, real data is not touched) ----
python manage.py selftest
echo.
echo Next: python manage.py init --pharmacy "Pharmacy Name" --username owner
pause
