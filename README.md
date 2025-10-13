# 키움증권 통합 자동매매 시스템

## 프로젝트 개요
키움증권 API를 활용하여 주식 시장을 실시간으로 모니터링하고 자동매매를 실행하는 통합 시스템입니다. 조건식 모니터링, 전략 매매, 스캘핑, 손절/익절까지 완전 자동화된 트레이딩 파이프라인을 제공합니다.

## 주요 기능

### 🔍 조건식 모니터링
- 키움증권 조건식 실시간 감시
- 기준봉 전략을 통한 고급 신호 필터링
- WebSocket 기반 실시간 데이터 수신
- 조건식 종목 자동 관심종목 동기화

### 📊 전략 매매 시스템
- **모멘텀 전략**: 가격 모멘텀 기반 매매
- **이격도 전략**: 이동평균 대비 이격도 매매
- **볼린저밴드 전략**: 밴드 터치 후 반등 매매
- **RSI 전략**: 과매수/과매도 구간 매매
- **스캘핑 전략**: 고빈도 단기 매매

### 🤖 자동매매 실행
- 백그라운드 주문 실행 엔진
- 실패 시 자동 재시도 (최대 3회)
- 손절/익절 자동화
- 포지션 관리 및 리스크 관리

### 📈 차트 및 분석
- 실시간 차트 이미지 생성
- 네이버 주식 토론 크롤링
- 기술적 분석 지표 제공
- 전략별 신호 히스토리 관리

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

### 전략 매매 관리
- `POST /strategy/start` - 전략 매매 시작
- `POST /strategy/stop` - 전략 매매 중지
- `GET /strategy/status` - 전략 매매 상태 조회
- `GET /strategies/` - 전략 목록 조회
- `POST /strategies/{strategy_type}/configure` - 전략 파라미터 설정
- `PUT /strategies/{strategy_id}/toggle` - 전략 활성화/비활성화

### 관심종목 관리
- `GET /watchlist/` - 관심종목 목록 조회
- `POST /watchlist/add` - 관심종목 추가
- `DELETE /watchlist/{stock_code}` - 관심종목 제거
- `PUT /watchlist/{stock_code}/toggle` - 관심종목 활성화/비활성화
- `POST /watchlist/sync/start` - 조건식 동기화 시작
- `POST /watchlist/sync/stop` - 조건식 동기화 중지

### 신호 관리
- `GET /signals/pending` - 대기 중인 매수 신호 조회
- `GET /signals/statistics` - 신호 통계 조회
- `GET /signals/by-strategy/{strategy_id}` - 특정 전략의 신호 조회

### 조건식 관리
- `GET /conditions/` - 조건식 목록 조회
- `GET /conditions/{id}/stocks` - 특정 조건식의 종목 조회
- `POST /conditions/toggle` - 조건식 활성화/비활성화

### 자동매매 설정
- `GET /trading/settings` - 자동매매 설정 조회
- `POST /trading/settings` - 자동매매 설정 업데이트
- `GET /buy-executor/status` - 매수 실행기 상태 조회
- `GET /stop-loss/status` - 손절/익절 상태 조회

### 스캘핑 전략
- `POST /scalping/start` - 스캘핑 전략 시작
- `POST /scalping/stop` - 스캘핑 전략 중지
- `GET /scalping/status` - 스캘핑 상태 조회

### 차트 및 분석
- `GET /chart/image/{stock_code}` - 종목 차트 이미지 생성
- `GET /chart/strategy/{stock_code}/{strategy_type}` - 특정 전략 지표 차트
- `GET /chart/strategy/{stock_code}` - 모든 전략 지표 종합 차트
- `GET /kiwoom/account` - 계좌 정보 조회
- `GET /kiwoom/balance` - 잔고 조회
- `GET /stocks/{stock_code}/info` - 종목 정보 및 토론 크롤링

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
├── strategy_manager.py         # 전략 매매 관리자 (모멘텀, 이격도, 볼린저밴드, RSI)
├── scalping_strategy.py        # 스캘핑 전략 관리자
├── stop_loss_manager.py        # 손절/익절 모니터링 관리자
├── watchlist_sync_manager.py   # 조건식-관심종목 동기화 관리자
├── api_rate_limiter.py         # API 호출 제한 관리
├── token_manager.py            # 키움 API 토큰 관리
├── naver_discussion_crawler.py # 네이버 주식 토론 크롤러
├── launcher.py                 # 애플리케이션 런처
├── static/                     # 웹 UI 정적 파일
│   ├── index.html             # 메인 대시보드
│   ├── app.js                 # 프론트엔드 로직
│   ├── style.css              # 스타일시트
│   └── modules/               # UI 모듈들
│       ├── account-manager.js # 계좌 관리 모듈
│       ├── chart-manager.js   # 차트 관리 모듈
│       ├── condition-manager.js # 조건식 관리 모듈
│       ├── strategy-manager.js # 전략 관리 모듈
│       └── ui-utils.js       # UI 유틸리티
├── requirements.txt            # Python 의존성
├── install.bat                # Windows 설치 스크립트
├── env_example.txt            # 환경 변수 예시
├── stock_pipeline.db          # SQLite 데이터베이스
├── README.md                  # 프로젝트 문서
└── PROCESS_FLOW.md            # 시스템 프로세스 흐름도
```

## 전략별 상세 설명

### 모멘텀 전략 (MOMENTUM)
- **개념**: 가격의 움직임 추세를 활용한 매매
- **매수 조건**: 모멘텀 > 0 (0선 상향 돌파) AND 모멘텀 상승 추세
- **매도 조건**: 모멘텀 < 0 (0선 하향 돌파) AND 모멘텀 하락 추세
- **파라미터**: 모멘텀 계산 기간(24봉), 추세 확인 기간(3일)

### 이격도 전략 (DISPARITY)
- **개념**: 현재가와 이동평균선의 이격도를 활용한 매매
- **매수 조건**: 이격도 < 95% (현재가가 이동평균 대비 5% 이상 낮음)
- **매도 조건**: 이격도 > 105% (현재가가 이동평균 대비 5% 이상 높음)
- **파라미터**: 이동평균 기간(20), 매수 임계값(95%), 매도 임계값(105%)

### 볼린저밴드 전략 (BOLLINGER)
- **개념**: 가격의 변동성을 활용한 매매
- **매수 조건**: 현재가가 하단밴드 터치 후 반등
- **매도 조건**: 현재가가 상단밴드 터치 후 하락
- **파라미터**: 이동평균 기간(20), 표준편차 배수(2), 반등 확인 기간(3일)

### RSI 전략 (RSI)
- **개념**: 과매수/과매도 구간을 활용한 매매
- **매수 조건**: RSI < 30 (과매도) AND RSI 상승 전환 AND 거래량 증가
- **매도 조건**: RSI > 70 (과매수) AND RSI 하락 전환 AND 거래량 증가
- **파라미터**: RSI 기간(14), 과매도 임계값(30), 과매수 임계값(70)

### 스캘핑 전략 (SCALPING)
- **개념**: 고빈도 단기 매매를 통한 소폭 수익 실현
- **특징**: 빠른 진입/청산, 작은 수익률, 높은 거래 빈도
- **리스크 관리**: 엄격한 손절, 포지션 크기 제한

## 시스템 특징

### 실시간 모니터링
- 조건식 모니터링: 10분 주기 스캔
- 전략 매매: 1분 주기 스캔
- 스캘핑: 30초 주기 스캔
- 손절/익절: 30초 주기 모니터링

### API 제한 관리
- 호출 한도: 1분 60회
- 최소 간격: 1.0초
- 제한 감지 시 자동 대기 및 재시도
- 상태: NORMAL/WARNING/LIMITED/RECOVERING

### 데이터베이스 구조
- `pending_buy_signals`: 매수 신호 관리
- `watchlist_stocks`: 관심종목 관리
- `trading_strategies`: 전략 설정 관리
- `strategy_signals`: 전략별 신호 히스토리
- `positions`: 보유 종목 관리
- `sell_orders`: 매도 주문 관리

## 주의사항
1. API 키는 절대 공개하지 마세요.
2. 실제 거래에 사용하기 전에 충분한 테스트를 진행하세요.
3. 중요한 데이터는 정기적으로 백업하세요.
4. 전략별 파라미터는 시장 상황에 따라 조정이 필요할 수 있습니다.
5. 스캘핑 전략은 높은 리스크를 수반하므로 신중하게 사용하세요.