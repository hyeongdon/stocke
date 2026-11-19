@echo off
REM 차트 OHLCV 마트 배치 (분석화면 일/주/월봉) - 작업 스케줄러용
REM 기본: 분석 즐겨찾기 종목. 전체 유니버스는 --scope fundamental

cd /d "%~dp0"

if not exist "logs" mkdir "logs"

REM 로그는 Python 이 logs\chart_ohlcv_mart_batch.log 에 직접 기록

if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" "scripts\chart_ohlcv_mart_batch.py" %*
) else if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "scripts\chart_ohlcv_mart_batch.py" %*
) else (
    python "scripts\chart_ohlcv_mart_batch.py" %*
)

exit /b %ERRORLEVEL%
