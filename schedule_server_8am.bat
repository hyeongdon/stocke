@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  Stocke 매일 08:00 자동 서버 기동 등록
echo  ------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_daily_server_task.ps1" -Time "08:00"
echo.
pause
