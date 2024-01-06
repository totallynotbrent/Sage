@echo off
rem Sage - Windows development start script.
rem Creates a virtualenv if absent, installs requirements, then serves the app.
cd /d "%~dp0"

if not exist ".venv" (
    where py >nul 2>nul
    if %errorlevel%==0 (
        py -3.11 -m venv .venv
        if errorlevel 1 py -3 -m venv .venv
    ) else (
        python -m venv .venv
    )
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt

if "%HOST%"=="" set HOST=0.0.0.0
if "%PORT%"=="" set PORT=8000

python -m uvicorn app.main:app --host %HOST% --port %PORT%
