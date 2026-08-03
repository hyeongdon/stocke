@echo off
REM 장마감 역매공파 단계·박스권 → 텔레그램 (작업 스케줄러용)
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" "scripts\ymgp_eod_batch.py"
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\ymgp_eod_batch.py"
) else (
  python "scripts\ymgp_eod_batch.py"
)
exit /b %ERRORLEVEL%
