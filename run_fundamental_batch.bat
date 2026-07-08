@echo off
REM 기본적분석 마트 배치 (네이버 → DB) — 작업 스케줄러용
REM 장 마감 후(예: 18:00) 일 1회 실행 권장

cd /d "%~dp0"

if not exist "logs" mkdir "logs"

echo ===== %DATE% %TIME% fundamental batch 시작 ===== >> "logs\fundamental_mart_batch.log"

if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" "scripts\fundamental_mart_batch.py" >> "logs\fundamental_mart_batch.log" 2>&1
) else (
    python "scripts\fundamental_mart_batch.py" >> "logs\fundamental_mart_batch.log" 2>&1
)

echo ===== %DATE% %TIME% fundamental batch 종료 (exit=%ERRORLEVEL%) ===== >> "logs\fundamental_mart_batch.log"
