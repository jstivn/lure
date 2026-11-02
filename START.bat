@echo off
rem Lure launcher: first run creates a Python environment and installs dependencies, then opens the app.
cd /d "%~dp0"

set "VENV=meta-lead-gen\venv"
if not exist "%VENV%\Scripts\python.exe" (
    echo First run: creating Python environment, this takes a few minutes...
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo Python 3.10+ was not found. Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
        pause
        exit /b 1
    )
    "%VENV%\Scripts\python.exe" -m pip install --upgrade pip
    "%VENV%\Scripts\python.exe" -m pip install -r control-hub\requirements.txt -r meta-lead-gen\requirements.txt -r whatsapp-outreach\requirements.txt
    if errorlevel 1 (
        echo Dependency install failed. Check your internet connection and run START.bat again.
        pause
        exit /b 1
    )
)

start "" "%VENV%\Scripts\pythonw.exe" control-hub\app.py
