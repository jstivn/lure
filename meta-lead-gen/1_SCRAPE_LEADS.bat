@echo off
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo ================================================================================
echo            MODULE 1: SCRAPING & QUALIFYING (META AD LIBRARY)
echo ================================================================================
echo.

if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe main.py scrape
) else (
    python main.py scrape
)

echo.
echo ================================================================================
pause
