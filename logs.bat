@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul 2>&1

if /i "%~1"=="follow" goto follow
if /i "%~1"=="server" goto server
if /i "%~1"=="caddy" goto caddy
goto app

:app
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\logs.ps1" app %2
exit /b %ERRORLEVEL%

:follow
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\logs.ps1" follow %2
exit /b %ERRORLEVEL%

:server
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\logs.ps1" server %2
exit /b %ERRORLEVEL%

:caddy
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\logs.ps1" caddy %2
exit /b %ERRORLEVEL%
