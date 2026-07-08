@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  Stocke 평일 08:30~08:50 서버 자동 기동 등록
echo  (5분마다 확인, 꺼져 있을 때만 start)
echo  ------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_morning_server_task.ps1" -WindowStart "08:30" -WindowEnd "08:50" -IntervalMinutes 5
echo.
pause
