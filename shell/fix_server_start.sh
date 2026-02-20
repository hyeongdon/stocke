#!/bin/bash

# 서버 시작 문제 해결 스크립트
# 사용법: ./fix_server_start.sh

set -e

PROJECT_DIR="/home/ubuntu/project/stocke"
cd "$PROJECT_DIR"

echo "=========================================="
echo "🔧 서버 시작 문제 해결"
echo "=========================================="
echo ""

# 1. 현재 실행 중인 프로세스 종료
echo "1. 기존 서버 프로세스 종료 중..."
pkill -f "uvicorn.*core.main:app" || true
pkill -f "uvicorn.*main:app" || true
sleep 2
echo "✅ 프로세스 종료 완료"
echo ""

# 2. 프로젝트 구조 확인
echo "2. 프로젝트 구조 확인 중..."
if [ ! -f "core/main.py" ]; then
    echo "❌ core/main.py 파일을 찾을 수 없습니다!"
    echo "현재 디렉토리: $(pwd)"
    echo "파일 목록:"
    ls -la
    exit 1
fi
echo "✅ core/main.py 파일 확인됨"
echo ""

# 3. 가상환경 확인
echo "3. 가상환경 확인 중..."
if [ ! -d "venv" ]; then
    echo "⚠️  가상환경이 없습니다. 생성 중..."
    python3 -m venv venv
    echo "✅ 가상환경 생성 완료"
fi

if [ ! -f "venv/bin/activate" ]; then
    echo "❌ 가상환경 활성화 스크립트가 없습니다!"
    exit 1
fi
echo "✅ 가상환경 확인됨"
echo ""

# 4. 가상환경 활성화
echo "4. 가상환경 활성화 중..."
source venv/bin/activate
echo "✅ 가상환경 활성화 완료"
echo "   Python 경로: $(which python)"
echo "   Python 버전: $(python --version)"
echo ""

# 5. 의존성 확인
echo "5. 의존성 확인 중..."
if ! python -c "import fastapi" 2>/dev/null; then
    echo "⚠️  FastAPI가 설치되지 않았습니다. 설치 중..."
    pip install -r requirements.txt
    echo "✅ 의존성 설치 완료"
else
    echo "✅ 의존성 확인됨"
fi
echo ""

# 6. PYTHONPATH 설정
echo "6. PYTHONPATH 설정 중..."
export PYTHONPATH="$PROJECT_DIR:$PYTHONPATH"
echo "✅ PYTHONPATH 설정 완료: $PYTHONPATH"
echo ""

# 7. 모듈 import 테스트
echo "7. 모듈 import 테스트 중..."
if python -c "from core.main import app" 2>/dev/null; then
    echo "✅ 모듈 import 성공"
else
    echo "❌ 모듈 import 실패"
    echo "오류 내용:"
    python -c "from core.main import app" 2>&1 || true
    exit 1
fi
echo ""

# 8. 서버 시작
echo "8. 서버 시작 중..."
nohup uvicorn core.main:app --host 0.0.0.0 --port 8001 --reload > server.log 2>&1 &
SERVER_PID=$!
echo "✅ 서버 시작됨 (PID: $SERVER_PID)"
echo ""

# 9. 서버 시작 확인
echo "9. 서버 시작 확인 중..."
sleep 5

if ps -p $SERVER_PID > /dev/null 2>&1; then
    echo "✅ 서버 프로세스 실행 중"
else
    echo "❌ 서버 프로세스가 종료되었습니다"
    echo "로그 확인:"
    tail -20 server.log
    exit 1
fi

# 10. 서버 응답 확인
echo "10. 서버 응답 확인 중..."
sleep 5
if curl -f http://localhost:8001/docs > /dev/null 2>&1; then
    echo "✅ 서버 응답 정상"
else
    echo "⚠️  서버 응답 확인 실패 (아직 시작 중일 수 있음)"
    echo "로그 확인:"
    tail -20 server.log
fi
echo ""

echo "=========================================="
echo "✅ 서버 시작 문제 해결 완료"
echo "=========================================="
echo ""
echo "💡 다음 명령어로 상태 확인:"
echo "   - 로그 확인: tail -f server.log"
echo "   - 프로세스 확인: ps aux | grep uvicorn"
echo "   - 서버 접속: http://localhost:8001/docs"








