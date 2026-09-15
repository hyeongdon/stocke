@echo off
REM 기본분석 마트 배치 (네이버 DB) - 업무 스케줄러용
REM 장 마감 후 1회 실행 권장

cd /d "%~dp0"

if not exist "logs" mkdir "logs"

REM Python FileHandler 가 직접 logs\fundamental_mart_batch.log 를 관리한다.
REM bat 에서 동일 파일로 리다이렉션하면 Windows 파일 잠금 충돌이 발생하므로 제거.

if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" "scripts\fundamental_mart_batch.py"
) else if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "scripts\fundamental_mart_batch.py"
) else (
    python "scripts\fundamental_mart_batch.py"
)

exit /b %ERRORLEVEL%
