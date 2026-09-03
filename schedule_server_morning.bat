@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  Stocke 평일 07:55~08:20 서버 자동 기동 등록
echo  (5분마다 확인, 꺼져 있을 때만 start — 손절 모니터 08:00 전 기동)
echo  ------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_morning_server_task.ps1" -WindowStart "07:55" -WindowEnd "08:20" -IntervalMinutes 5
echo.
pause
