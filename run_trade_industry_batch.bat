@echo off
REM 수출입 업종 지표 월배치 (작업 스케줄러용)
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" "scripts\trade_industry_batch.py" --months 24 --sleep 0.15
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\trade_industry_batch.py" --months 24 --sleep 0.15
) else (
  python "scripts\trade_industry_batch.py" --months 24 --sleep 0.15
)
exit /b %ERRORLEVEL%
