@echo off
REM 조건식 실시간 편입 → 텔레그램 (장중 상시 실행용)
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
echo ===== %DATE% %TIME% realtime alert start ===== >> "logs\scheduler_run.log"
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" "scripts\condition_telegram_alert.py" --realtime >> "logs\condition_telegram_alert.log" 2>&1
) else (
    python "scripts\condition_telegram_alert.py" --realtime >> "logs\condition_telegram_alert.log" 2>&1
)
echo ===== %DATE% %TIME% realtime alert end (exit=%ERRORLEVEL%) ===== >> "logs\scheduler_run.log"
