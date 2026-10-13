@echo off
REM 모의투자 계좌 교체(앱키/시크릿/계좌번호 변경) 감지 시 이전 계좌 데이터 백업 후 초기화 (수동 실행)
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" "scripts\mock_account_reset_batch.py" %*
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\mock_account_reset_batch.py" %*
) else (
  python "scripts\mock_account_reset_batch.py" %*
)
exit /b %ERRORLEVEL%
