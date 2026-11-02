@echo off
setlocal
cd /d "%~dp0"

if exist "data\leads.csv" (
    start "" "data\leads.csv"
) else (
    echo [!] File data\leads.csv not found yet. Run 1_SCRAPE_LEADS.bat first!
    pause
)
