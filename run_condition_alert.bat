@echo off
REM 조건식 조회 -> 텔레그램 알림 실행 런처 (작업 스케줄러용)
REM 가상환경(venv)의 python을 사용하고, 모든 출력을 logs 폴더에 기록한다.

cd /d "%~dp0"

if not exist "logs" mkdir "logs"

echo ===== %DATE% %TIME% 실행 시작 ===== >> "logs\scheduler_run.log"

REM venv 파이썬으로 실행 (없으면 시스템 python으로 폴백)
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" "scripts\condition_telegram_alert.py" >> "logs\scheduler_run.log" 2>&1
) else (
    python "scripts\condition_telegram_alert.py" >> "logs\scheduler_run.log" 2>&1
)

echo ===== %DATE% %TIME% 실행 종료 (exit=%ERRORLEVEL%) ===== >> "logs\scheduler_run.log"
