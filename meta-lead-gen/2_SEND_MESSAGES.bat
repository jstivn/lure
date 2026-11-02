@echo off
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo ================================================================================
echo       META OUTREACH SENDER: ПОЛНЫЙ АВТОМАТ С МЕЖСЕТЕВОЙ ЗАЩИТОЙ
echo ================================================================================
echo.
echo Режим отправки: ПОЛНЫЙ АВТОМАТ (программа сама печатает и жмет Enter).
echo Межсетевая защита: не менее 5 минут между ЛЮБЫМИ сообщениями в Meta.
echo.
echo Выберите режим:
echo   [1] Только INSTAGRAM Direct
echo   [2] Только FACEBOOK Messenger
echo   [3] ПАРАЛЛЕЛЬНО FB + IG (оба браузера, чередование каждые 5+ мин) [Enter]
echo.
set /p opt="Ваш выбор (1-3, по умолчанию 3): "

set TARGET=
if "%opt%"=="1" set TARGET=ig
if "%opt%"=="2" set TARGET=fb

echo.
if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe main.py send %TARGET%
) else (
    python main.py send %TARGET%
)

echo.
echo ================================================================================
pause
