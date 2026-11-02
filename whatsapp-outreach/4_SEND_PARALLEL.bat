@echo off
chcp 65001 > nul
title WhatsApp Outreach - Parallel (WP 1 + WP 2)
cd /d "%~dp0"

echo ================================================================================
echo       WHATSAPP OUTREACH: ПАРАЛЛЕЛЬНЫЙ АВТОМАТ (WP 1 + WP 2)
echo ================================================================================
echo.
echo Режим: ПОЛНЫЙ АВТОМАТ (без ручного подтверждения Enter).
echo Защита: не менее 5 минут между ЛЮБЫМИ исходящими сообщениями.
echo Порядок: WP 1 начинает сразу, wp2 начнет через 5 минут.
echo.

call venv\Scripts\activate.bat
python main.py send all
pause
