@echo off
chcp 65001 > nul
title WhatsApp Outreach - WP 1 (Business)
cd /d "%~dp0"

call venv\Scripts\activate.bat
python main.py send 1
pause
