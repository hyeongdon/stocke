# 키움증권 조건식 모니터링 시스템

## 프로젝트 개요
키움증권 API를 활용하여 주식 시장을 모니터링하고 매매 신호를 생성하는 시스템입니다. 단계별로 구현되는 세 가지 주요 컴포넌트로 구성됩니다.

## 주요 컴포넌트

### 1. 키움증권 조건식 서버
- 키움증권 API 연동 및 인증
- 조건식 목록 조회
- 조건식 실행 결과 처리

### 2. 조건식 조회 API
- 조건식 생성/조회/삭제
- 조건식 모니터링 시작/중지
- 모니터링 상태 조회

### 3. 자동매매 엔진
- 조건식 기반 매매 신호 생성
- 신호 중복 제거
- 모니터링 로그 관리

## 시스템 요구사항
- Python 3.8 이상
- 키움증권 API 키 (APP_KEY, APP_SECRET)

## 설치 방법

1. 가상환경 생성 및 활성화
```bash
python -m venv venv
venv\Scripts\activate    # Windows
source venv/bin/activate  # Linux/Mac
```

2. 의존성 설치
```bash
pip install -r requirements.txt
```

3. 환경 변수 설정
`.env` 파일을 생성하고 다음 내용을 추가:
```env
KIWOOM_APP_KEY=your_app_key
KIWOOM_APP_SECRET=your_app_secret
LOG_LEVEL=INFO
```

## 실행 방법
```bash
uvicorn main:app --reload
```

## API 문서
서버 실행 후 다음 URL에서 API 문서를 확인할 수 있습니다:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## 프로젝트 구조
```
stock-pipeline/
├── main.py              # FastAPI 애플리케이션
├── models.py            # 데이터베이스 모델
├── config.py           # 설정 파일
├── kiwoom_api.py       # 키움증권 API 연동
├── condition_monitor.py # 조건식 모니터링
├── requirements.txt    # 의존성 목록
└── README.md          # 프로젝트 설명
```

## 주의사항
1. API 키는 절대 공개하지 마세요.
2. 실제 거래에 사용하기 전에 충분한 테스트를 진행하세요.
3. 중요한 데이터는 정기적으로 백업하세요.