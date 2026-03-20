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
# 운영(권장): reload 끔
uvicorn main:app --host 0.0.0.0 --port 8000

# 로컬 개발 시에만 자동 재시작
# uvicorn main:app --reload --host 0.0.0.0 --port 8000
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

## Linux에서 ngrok 백그라운드 실행

리눅스를 사용할 때는 애플리케이션을 먼저 실행한 뒤 ngrok를 붙여야 합니다. ngrok 프로세스만 살아 있고 앱 포트가 비어 있으면 `ERR_NGROK_8012`가 발생합니다.

1. 앱 실행 (예시: 8000 포트)
```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

2. 앱 포트 확인
```bash
ss -ltnp | grep 8000
curl -I http://127.0.0.1:8000
```

3. 기존 ngrok 세션 정리 (무료 플랜 동시 1개 제한)
```bash
pkill -f ngrok
pgrep -af ngrok
```

4. ngrok 백그라운드 실행
```bash
nohup ngrok http 8000 > ~/ngrok.log 2>&1 &
```

5. 로그/상태 확인
```bash
tail -f ~/ngrok.log
curl -s http://127.0.0.1:4040/api/tunnels
```

고정 도메인을 사용하는 경우:
```bash
nohup ngrok http --url=YOUR_STATIC_DOMAIN.ngrok-free.app 8000 > ~/ngrok.log 2>&1 &
```

### ngrok 에러 빠른 진단

- `ERR_NGROK_8012`: 업스트림 연결 실패. 앱 포트 미기동 또는 잘못된 포트 설정.
- `ERR_NGROK_108`: ngrok 에이전트 동시 세션 초과. 기존 ngrok 종료 후 재실행.
- `ERR_NGROK_3200`: endpoint 오프라인. ngrok 프로세스가 내려갔거나 연결이 끊긴 상태.

## n8n (Docker) + ngrok 운영 메모

n8n을 Docker로 띄울 때는 호스트 포트 매핑이 반드시 필요합니다. `docker ps`의 `Ports`가 비어 있으면 `curl http://127.0.0.1:5678`가 실패하고 ngrok에서 `ERR_NGROK_8012`가 발생합니다.

### 1) n8n 컨테이너 실행 (5678 매핑 + 한국 시간)

스케줄 워크플로우(`Schedule Trigger`)는 **인스턴스 기본 타임존**을 따릅니다. UI에 timezone 필드가 없는 n8n 버전에서는 아래 환경변수로 맞춥니다.

- `TZ=Asia/Seoul` — 컨테이너 OS 시각
- `GENERIC_TIMEZONE=Asia/Seoul` — n8n 스케줄·크론 기준 시각

```bash
docker run -d \
  --name n8n \
  -p 5678:5678 \
  -e TZ=Asia/Seoul \
  -e GENERIC_TIMEZONE=Asia/Seoul \
  -e N8N_HOST=0.0.0.0 \
  -e N8N_PORT=5678 \
  -e N8N_PROTOCOL=http \
  -e N8N_RUNNERS_ENABLED=true \
  docker.n8n.io/n8nio/n8n:latest
```

**이미 n8n 컨테이너가 있는 경우** 타임존만 반영하려면 컨테이너를 재생성하는 것이 가장 확실합니다.

```bash
docker stop n8n && docker rm n8n
# 위 docker run ... 명령으로 다시 실행
```

환경변수 반영 후 **워크플로우를 비활성화 → 저장 → 다시 활성화**하면 스케줄이 새 타임존으로 다시 계산됩니다.

### 1-1) 타임존 적용 확인

```bash
docker exec n8n date
# 예: KST로 표시되는지 확인 (또는 +0900)

docker inspect n8n --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E 'TZ|GENERIC_TIMEZONE'
```

### 2) n8n 상태 확인
```bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep n8n
docker logs --tail 100 n8n
curl -I http://127.0.0.1:5678
```

### 3) ngrok 연결
```bash
pkill -f ngrok
nohup ngrok http --url=YOUR_STATIC_DOMAIN.ngrok-free.app 5678 > ~/ngrok.log 2>&1 &
tail -n 50 ~/ngrok.log
```

### 4) 문제 발생 시 빠른 체크
```bash
# ngrok 세션 확인/종료
pgrep -af ngrok
pkill -f ngrok

# n8n 컨테이너 상태
docker inspect -f '{{.State.Status}} {{.State.Restarting}} {{.RestartCount}}' n8n
docker logs --tail 200 n8n
```

- `curl: (7) Failed to connect`: n8n 미기동 또는 포트 매핑 누락
- `curl: (56) Recv failure: Connection reset by peer`: n8n 부팅 중/초기화 중일 수 있음 (잠시 후 재시도)
- `ERR_NGROK_108`: 다른 서버/세션에서 ngrok 실행 중 (대시보드 Agents에서 세션 종료 필요)
- `ERR_NGROK_3200`: ngrok endpoint 오프라인 (ngrok 프로세스 재기동)

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