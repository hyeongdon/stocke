@echo off
chcp 65001 >nul
echo ========================================
echo 키움증권 조건식 모니터링 시스템 설치
echo ========================================
echo.

REM Python 설치 확인
python --version >nul 2>&1
if errorlevel 1 (
    echo Python이 설치되어 있지 않습니다.
    echo https://www.python.org/downloads/ 에서 Python을 다운로드하여 설치해주세요.
    pause
    exit /b 1
)

echo Python 설치 확인 완료
echo.

REM 가상환경 생성
echo 가상환경을 생성합니다...
python -m venv venv
if errorlevel 1 (
    echo 가상환경 생성에 실패했습니다.
    pause
    exit /b 1
)

echo 가상환경 생성 완료
echo.

REM 가상환경 활성화
echo 가상환경을 활성화합니다...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo 가상환경 활성화에 실패했습니다.
    pause
    exit /b 1
)

echo 가상환경 활성화 완료
echo.

REM pip 업그레이드
echo pip를 최신 버전으로 업그레이드합니다...
python -m pip install --upgrade pip
if errorlevel 1 (
    echo pip 업그레이드에 실패했습니다.
    pause
    exit /b 1
)

echo pip 업그레이드 완료
echo.

REM 의존성 설치
echo 필요한 패키지들을 설치합니다...
pip install -r requirements.txt
if errorlevel 1 (
    echo 패키지 설치에 실패했습니다.
    pause
    exit /b 1
)

echo 패키지 설치 완료
echo.

REM 환경설정 파일 생성
echo 환경설정 파일을 생성합니다...
if not exist .env (
    copy env_example.txt .env
    echo .env 파일이 생성되었습니다.
    echo 키움증권 API 키를 .env 파일에 설정해주세요.
) else (
    echo .env 파일이 이미 존재합니다.
)

echo.
echo ========================================
echo 설치가 완료되었습니다!
echo ========================================
echo.
echo 다음 단계:
echo 1. .env 파일에서 키움증권 API 키를 설정하세요
echo 2. python main.py 명령으로 서버를 실행하세요
echo 3. http://localhost:8000 에서 API 문서를 확인하세요
echo.
echo 가상환경을 비활성화하려면: deactivate
echo.
pause 