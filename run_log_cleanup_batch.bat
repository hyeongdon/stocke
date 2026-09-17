@echo off
REM 앱·배치 로그 7일 보관 정리 (작업 스케줄러용)
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" "scripts\log_cleanup_batch.py" %*
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\log_cleanup_batch.py" %*
) else (
  python "scripts\log_cleanup_batch.py" %*
)
exit /b %ERRORLEVEL%
