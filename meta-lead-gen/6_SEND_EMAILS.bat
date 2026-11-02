@echo off
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo ========================================================
echo   EMAIL OUTREACH SENDER (GMAIL SMTP)
echo ========================================================
echo.
echo Texts: email_template.json. Sender: config.json - email.
echo Usually easier from Lure: the email outreach button.
echo Recommended daily limit for safe Gmail sending: 50 emails
echo.
echo [1] Send first 50 emails (Recommended daily batch)
echo [2] Send ALL remaining emails
echo [3] Dry-run (Preview without sending)
echo [0] Cancel
echo.
set /p choice="Enter your choice (1, 2, 3 or 0): "

if "%choice%"=="1" (
    echo.
    echo Sending first 50 emails...
    venv\Scripts\python.exe send_emails.py --send --limit 50
) else if "%choice%"=="2" (
    echo.
    echo Sending all emails...
    venv\Scripts\python.exe send_emails.py --send --limit 999
) else if "%choice%"=="3" (
    echo.
    echo Running preview...
    venv\Scripts\python.exe send_emails.py --limit 10
) else (
    echo Cancelled.
)

echo.
pause
