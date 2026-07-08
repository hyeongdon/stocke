@echo off

setlocal

cd /d "%~dp0"

chcp 65001 >nul 2>&1



if "%~1"=="" goto usage



if /i "%~1"=="start" goto run_elevated

if /i "%~1"=="restart" goto run_elevated

goto run_normal



:run_elevated

powershell -NoProfile -ExecutionPolicy Bypass -Command "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); & '%~dp0scripts\caddy.ps1' %*"

set ERR=%ERRORLEVEL%

if not "%ERR%"=="0" pause

exit /b %ERR%



:run_normal

powershell -NoProfile -ExecutionPolicy Bypass -Command "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); & '%~dp0scripts\caddy.ps1' %*"

set ERR=%ERRORLEVEL%

if not "%ERR%"=="0" pause

exit /b %ERR%



:usage

echo.

echo  Stocke reverse proxy (Caddy 80 -^> 8000)

echo  ------------------------------------------------

echo   proxy.bat install

echo   proxy.bat start    ^<-- 이걸 실행하세요 (UAC 예)

echo   proxy.bat stop

echo   proxy.bat restart

echo   proxy.bat status

echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\caddy.ps1" status

echo.

for /f "tokens=*" %%a in ('powershell -NoProfile -Command "(Get-NetTCPConnection -LocalPort 80 -State Listen -ErrorAction SilentlyContinue).OwningProcess" 2^>nul') do set PORT80=%%a

if not defined PORT80 (

    echo  [중요] 포트 80이 비어 있습니다. proxy.bat start 를 실행하고 UAC에서 [예]를 누르세요.

    echo.

)

pause

exit /b 0

