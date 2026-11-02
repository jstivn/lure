@echo off
setlocal
cd /d "%~dp0"

echo ================================================================================
echo        ВХОД В АККАУНТЫ FACEBOOK И INSTAGRAM (ИЗОЛИРОВАННЫЕ ПРОФИЛИ)
echo ================================================================================
echo.
echo Для надежной параллельной работы FB и IG имеют раздельные профили и порты:
echo   Facebook:   порт 9333, папка data\profiles\chrome_fb
echo   Instagram:  порт 9334, папка data\profiles\chrome_ig
echo.
echo Выберите действие:
echo   [1] Войти в FACEBOOK (откроется окно Chrome для FB)
echo   [2] Войти в INSTAGRAM (откроется окно Chrome для IG)
echo   [3] Открыть ОБА браузера одновременно
echo.
set "CHROME=%LURE_CHROME_PATH%"
if not defined CHROME if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined CHROME if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not defined CHROME if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if not defined CHROME (
    echo Google Chrome не найден. Установите Chrome или задайте переменную LURE_CHROME_PATH.
    pause
    exit /b 1
)

set /p opt="Ваш выбор (1-3): "

if "%opt%"=="2" goto ig
if "%opt%"=="3" goto both

:fb
start "" "%CHROME%" --remote-debugging-port=9333 --remote-allow-origins=* --user-data-dir="%~dp0data\profiles\chrome_fb" "https://www.facebook.com"
goto done

:ig
start "" "%CHROME%" --remote-debugging-port=9334 --remote-allow-origins=* --user-data-dir="%~dp0data\profiles\chrome_ig" "https://www.instagram.com"
goto done

:both
start "" "%CHROME%" --remote-debugging-port=9333 --remote-allow-origins=* --user-data-dir="%~dp0data\profiles\chrome_fb" "https://www.facebook.com"
start "" "%CHROME%" --remote-debugging-port=9334 --remote-allow-origins=* --user-data-dir="%~dp0data\profiles\chrome_ig" "https://www.instagram.com"
goto done

:done
echo.
echo Окно(а) Chrome запущены! Проверьте вход в аккаунты, затем закройте эту консоль.
echo.
pause
