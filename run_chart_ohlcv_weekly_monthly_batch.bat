@echo off
REM 차트 OHLCV 마트 — 토요일 주봉 → 월봉 순차 (fundamental)
REM 작업 스케줄러: Stocke-ChartOhlcvWeeklyMonthly

cd /d "%~dp0"

if not exist "logs" mkdir "logs"

set "PY=python"
if exist "venv\Scripts\python.exe" set "PY=venv\Scripts\python.exe"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

echo ===== chart OHLCV weekly start =====
"%PY%" "scripts\chart_ohlcv_mart_batch.py" --scope fundamental --intervals 1W --workers 1 --sleep 0.3
set "WEEKLY_RC=%ERRORLEVEL%"

echo ===== chart OHLCV monthly start =====
"%PY%" "scripts\chart_ohlcv_mart_batch.py" --scope fundamental --intervals 1MO --workers 1 --sleep 0.3
set "MONTHLY_RC=%ERRORLEVEL%"

if not "%WEEKLY_RC%"=="0" exit /b %WEEKLY_RC%
exit /b %MONTHLY_RC%
