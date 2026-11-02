@echo off
chcp 65001 > nul
title WhatsApp Outreach - wp2 (Personal)
cd /d "%~dp0"

call venv\Scripts\activate.bat
python main.py send 2
pause
