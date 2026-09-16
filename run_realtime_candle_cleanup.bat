@echo off
REM 장 종료 후 실시간 3분봉 구독 해제 + 봉 히스토리 초기화 (작업 스케줄러용)
REM 권장 시각: 평일 20:00 이후
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" "scripts\realtime_candle_cleanup_batch.py" %*
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\realtime_candle_cleanup_batch.py" %*
) else (
  python "scripts\realtime_candle_cleanup_batch.py" %*
)
exit /b %ERRORLEVEL%
