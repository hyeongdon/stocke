@echo off
REM 기술적분석 마트 배치 (일봉 지표 + 주봉 RSI) - 작업 스케줄러용
REM 기본분석 배치(유니버스) 이후 실행

cd /d "%~dp0"

if not exist "logs" mkdir "logs"

REM 로그는 Python 이 logs\technical_mart_batch.log 에 직접 기록

if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" "scripts\technical_mart_batch.py" %*
) else if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "scripts\technical_mart_batch.py" %*
) else (
    python "scripts\technical_mart_batch.py" %*
)

exit /b %ERRORLEVEL%
