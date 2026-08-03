@echo off
REM 장마감 매매 일지 → 텔레그램 (작업 스케줄러용)
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" "scripts\daily_trade_journal_batch.py"
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\daily_trade_journal_batch.py"
) else (
  python "scripts\daily_trade_journal_batch.py"
)
exit /b %ERRORLEVEL%
