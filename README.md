# 키움증권 조건식 모니터링 시스템

## 프로젝트 개요
키움증권 API를 활용하여 주식 시장을 실시간으로 모니터링하고 자동매매 신호를 생성하는 통합 시스템입니다. 조건식 기반 종목 감시, 신호 생성, 자동 주문 실행까지 완전 자동화된 트레이딩 파이프라인을 제공합니다.

## 주요 기능

### 🔍 조건식 모니터링
- 키움증권 조건식 실시간 감시
- 기준봉 전략을 통한 고급 신호 필터링
- WebSocket 기반 실시간 데이터 수신

### 📊 신호 관리
- 중복 신호 방지 및 상태 추적
- 신호 통계 및 히스토리 관리
- 실시간 신호 대시보드

### 🤖 자동매매 실행
- 백그라운드 주문 실행 엔진
- 실패 시 자동 재시도 (최대 3회)
- 손절/익절 자동화

### 📈 차트 및 분석
- 실시간 차트 이미지 생성
- 네이버 주식 토론 크롤링
- 기술적 분석 지표 제공

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

### 방법 1: 직접 실행
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 방법 2: 런처 사용 (Windows)
```bash
python launcher.py
```

### 방법 3: 설치 스크립트 사용 (Windows)
```bash
install.bat
```

서버 실행 후 브라우저에서 `http://localhost:8000`으로 접속하여 웹 대시보드를 사용할 수 있습니다.

## 주요 API 엔드포인트

### 모니터링 관리
- `POST /monitoring/start` - 조건식 모니터링 시작
- `POST /monitoring/stop` - 모니터링 중지
- `GET /monitoring/status` - 모니터링 상태 조회

### 신호 관리
- `GET /signals/pending` - 대기 중인 매수 신호 조회
- `GET /signals/statistics` - 신호 통계 조회

### 조건식 관리
- `GET /conditions/` - 조건식 목록 조회
- `GET /conditions/{id}/stocks` - 특정 조건식의 종목 조회
- `POST /conditions/toggle` - 조건식 활성화/비활성화

### 자동매매 설정
- `GET /trading/settings` - 자동매매 설정 조회
- `POST /trading/settings` - 자동매매 설정 업데이트
- `GET /buy-executor/status` - 매수 실행기 상태 조회

### 차트 및 분석
- `GET /chart/image/{stock_code}` - 종목 차트 이미지 생성
- `GET /kiwoom/account` - 계좌 정보 조회
- `GET /kiwoom/balance` - 잔고 조회

## API 문서
서버 실행 후 다음 URL에서 상세한 API 문서를 확인할 수 있습니다:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## 프로젝트 구조
```
stocke/
├── main.py                      # FastAPI 애플리케이션 및 엔드포인트
├── models.py                    # SQLAlchemy 데이터베이스 모델
├── config.py                    # 설정 및 환경 변수 관리
├── kiwoom_api.py               # 키움증권 API 연동 (REST/WebSocket)
├── condition_monitor.py        # 조건식 모니터링 및 신호 생성
├── signal_manager.py           # 신호 중복 방지 및 상태 관리
├── buy_order_executor.py       # 자동매매 주문 실행 엔진
├── api_rate_limiter.py         # API 호출 제한 관리
├── token_manager.py            # 키움 API 토큰 관리
├── naver_discussion_crawler.py # 네이버 주식 토론 크롤러
├── launcher.py                 # 애플리케이션 런처
├── static/                     # 웹 UI 정적 파일
│   ├── index.html             # 메인 대시보드
│   ├── app.js                 # 프론트엔드 로직
│   ├── style.css              # 스타일시트
│   └── modules/               # UI 모듈들
├── requirements.txt            # Python 의존성
├── install.bat                # Windows 설치 스크립트
├── env_example.txt            # 환경 변수 예시
├── stock_pipeline.db          # SQLite 데이터베이스
├── README.md                  # 프로젝트 문서
├── PROCESS_FLOW.md            # 시스템 프로세스 흐름도
└── context.md                 # 개발 컨텍스트 및 DB 쿼리
```

## 주의사항
1. API 키는 절대 공개하지 마세요.
2. 실제 거래에 사용하기 전에 충분한 테스트를 진행하세요.
3. 중요한 데이터는 정기적으로 백업하세요.