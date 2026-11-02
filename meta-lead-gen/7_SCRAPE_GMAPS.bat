@echo off
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo ========================================================
echo   GOOGLE MAPS RENTAL SCRAPER (DUBAI) - Strict Quality Filter
echo ========================================================
echo.

if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe scrape_gmaps_rentals.py --min-reviews 10 --max-per-query 60
) else (
    python scrape_gmaps_rentals.py --min-reviews 10 --max-per-query 60
)

echo.
echo ========================================================
echo   Extracting Emails from Found Websites...
echo ========================================================
echo.

if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe extract_emails.py --input data/gmaps_rentals.csv --output data/leads_emails.csv
) else (
    python extract_emails.py --input data/gmaps_rentals.csv --output data/leads_emails.csv
)

echo.
echo All Done! Results are in data/leads_emails.csv
pause
