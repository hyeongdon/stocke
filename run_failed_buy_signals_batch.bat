@echo off
REM 장마감 매수 실패 신호 → 텔레그램 (작업 스케줄러용)
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" "scripts\failed_buy_signals_batch.py"
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\failed_buy_signals_batch.py"
) else (
  python "scripts\failed_buy_signals_batch.py"
)
exit /b %ERRORLEVEL%
