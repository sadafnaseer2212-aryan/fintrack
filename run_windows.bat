@echo off
REM FinTrack - one-click start for Windows.
REM First run: creates .venv and installs requirements (needs internet). Later runs start immediately.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -m venv .venv 2>nul || python -m venv .venv
  if not exist ".venv\Scripts\python.exe" (
    echo Could not create the virtual environment. Install Python 3.10-3.13 from python.org and tick "Add python.exe to PATH".
    pause
    exit /b 1
  )
  echo Installing requirements, this can take a few minutes...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Installing requirements failed. Check your internet connection and Python version.
    pause
    exit /b 1
  )
)
start "" http://localhost:5000
".venv\Scripts\python.exe" app.py
pause
