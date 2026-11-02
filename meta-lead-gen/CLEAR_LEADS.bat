@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ========================================================
echo   ОЧИСТКА БАЗЫ ДАННЫХ ЛИДОВ (leads.db и leads.csv)
echo ========================================================
echo.
echo Внимание! Все сохраненные лиды будут полностью удалены.
echo.
set /p CONFIRM="Вы уверены, что хотите удалить ВСЕХ лидов? (y/n / д/н): "

if /i "%CONFIRM%"=="y" goto do_clear
if /i "%CONFIRM%"=="yes" goto do_clear
if /i "%CONFIRM%"=="д" goto do_clear
if /i "%CONFIRM%"=="да" goto do_clear

echo.
echo Очистка отменена.
goto done

:do_clear
echo.
echo Очищаю базу...
if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe -c "from core.database import DatabaseManager; db = DatabaseManager('data/leads.db'); db.clear_all_leads(); print('[OK] База leads.db и файл leads.csv успешно очищены!')"
) else (
    python -c "from core.database import DatabaseManager; db = DatabaseManager('data/leads.db'); db.clear_all_leads(); print('[OK] База leads.db и файл leads.csv успешно очищены!')"
)

:done
echo.
pause
