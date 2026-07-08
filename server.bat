@echo off
setlocal
cd /d "%~dp0"

rem 콘솔 UTF-8 (한글 메시지 깨짐 방지)
chcp 65001 >nul 2>&1

if "%~1"=="" goto usage

powershell -NoProfile -ExecutionPolicy Bypass -Command "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); [Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false); & '%~dp0scripts\server.ps1' %*"
set ERR=%ERRORLEVEL%
if not "%ERR%"=="0" pause
exit /b %ERR%

:usage
echo.
echo  Stocke server
echo  -------------
echo   server.bat start
echo   server.bat stop
echo   server.bat restart
echo   server.bat status
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\server.ps1" status
echo.
pause
exit /b 0
