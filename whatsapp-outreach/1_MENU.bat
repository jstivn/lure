@echo off
chcp 65001 > nul
title WhatsApp Outreach Copilot - Menu
cd /d "%~dp0"

call venv\Scripts\activate.bat
python launcher.py
pause
