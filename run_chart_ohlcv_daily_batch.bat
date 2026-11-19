@echo off
REM 차트 OHLCV 마트 — 평일 장후 일봉만 (fundamental)
REM 작업 스케줄러: Stocke-ChartOhlcvBatch

cd /d "%~dp0"

if not exist "logs" mkdir "logs"

if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" "scripts\chart_ohlcv_mart_batch.py" --scope fundamental --intervals 1D --workers 1 --sleep 0.3 %*
) else if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "scripts\chart_ohlcv_mart_batch.py" --scope fundamental --intervals 1D --workers 1 --sleep 0.3 %*
) else (
    python "scripts\chart_ohlcv_mart_batch.py" --scope fundamental --intervals 1D --workers 1 --sleep 0.3 %*
)

exit /b %ERRORLEVEL%
