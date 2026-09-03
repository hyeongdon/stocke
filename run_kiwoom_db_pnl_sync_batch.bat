@echo off
REM NXT 마감 후 키움 실현손익·수수료·잔고 ↔ DB 동기화 (작업 스케줄러용, 기본 19:50)
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" "scripts\kiwoom_db_pnl_sync_batch.py" --apply
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\kiwoom_db_pnl_sync_batch.py" --apply
) else (
  python "scripts\kiwoom_db_pnl_sync_batch.py" --apply
)
exit /b %ERRORLEVEL%
