@echo off
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe launcher.py
) else if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" launcher.py
) else (
    python launcher.py
)

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Failed to start launcher.
    pause
)
