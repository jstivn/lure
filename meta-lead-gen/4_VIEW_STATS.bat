@echo off
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe main.py stats
) else (
    python main.py stats
)

echo.
pause
