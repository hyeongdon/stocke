## 시스템 데이터/프로세스 흐름 개요

- **주요 컴포넌트**
  - `main.py`: FastAPI 서버 및 엔드포인트, 앱 수명주기, 정적 리소스 제공
  - `condition_monitor.py`: 조건식 주기 스캔, 기준봉 전략, 신호 생성
  - `signal_manager.py`: 신호 중복 방지, DB 저장/통계, 상태 업데이트
  - `buy_order_executor.py`: PENDING 신호 처리, 검증/주문/재시도, 상태 갱신
  - `strategy_manager.py`: 관심종목 기반 전략 매매 관리 (모멘텀, 이격도, 볼린저밴드, RSI)
    - 차트 데이터 캐싱(10분), API 제한 감지 시 스킵, 중복 호출 방지
  - `scalping_strategy.py`: 고빈도 단기 매매 전략 관리
  - `stop_loss_manager.py`: 손절/익절 모니터링 및 자동 매도 실행
  - `watchlist_sync_manager.py`: 조건식 종목을 관심종목으로 자동 동기화
  - `models.py`: SQLite/SQLAlchemy 모델 및 세션, 테이블 초기화
  - `kiwoom_api.py`: 키움 API 인증/차트/시세/주문 호출 (REST/WebSocket)

---

## 상위 수준 시퀀스 (통합 자동매매 시스템)

```mermaid
sequenceDiagram
    autonumber
    participant UI as Web UI
    participant API as FastAPI (main.py)
    participant Mon as ConditionMonitor
    participant Sig as SignalManager
    participant Exec as BuyOrderExecutor
    participant Str as StrategyManager
    participant Scalp as ScalpingStrategy
    participant Stop as StopLossManager
    participant Sync as WatchlistSync
    participant DB as SQLite (models.py)
    participant KI as Kiwoom API

    Note over UI,KI: 조건식 모니터링
    UI->>API: POST /monitoring/start
    API->>Mon: start_periodic_monitoring()
    loop 10분 주기
        Mon->>KI: get_condition_list_websocket()
        Mon->>KI: search_condition_stocks(condition_id, name)
        alt 결과 존재
            Mon->>Mon: _apply_condition_reference_strategy()
            Mon->>Sig: create_signal(condition_id, stock, type)
            Sig->>DB: INSERT pending_buy_signals (status=PENDING)
        end
    end

    Note over UI,KI: 전략 매매 시스템
    UI->>API: POST /strategy/start
    API->>Str: start_strategy_monitoring()
    loop 1분 주기
        Str->>DB: SELECT 관심종목 목록
        loop 각 관심종목
            Str->>KI: get_stock_chart_data(stock_code, 5분봉)
            Str->>Str: 전략별 신호 계산 (모멘텀/이격도/볼린저밴드/RSI)
            alt 매수/매도 신호 발생
                Str->>Sig: create_signal(strategy_id, stock, signal_type)
                Sig->>DB: INSERT pending_buy_signals (signal_type=strategy)
            end
        end
    end

    Note over UI,KI: 스캘핑 전략
    UI->>API: POST /scalping/start
    API->>Scalp: start_scalping_monitoring()
    loop 30초 주기
        Scalp->>DB: SELECT 활성 종목
        Scalp->>KI: get_current_price(stock)
        Scalp->>Scalp: 스캘핑 신호 계산
        alt 스캘핑 신호 발생
            Scalp->>Sig: create_signal(scalping_id, stock, signal_type)
            Sig->>DB: INSERT pending_buy_signals (signal_type=scalping)
        end
    end

    Note over UI,KI: 손절/익절 모니터링
    UI->>API: POST /stop-loss/start
    API->>Stop: start_monitoring()
    loop 30초 주기
        Stop->>DB: SELECT 보유 종목
        Stop->>KI: get_current_price(stock)
        Stop->>Stop: 손절/익절 조건 확인
        alt 손절/익절 조건 만족
            Stop->>KI: place_sell_order(...)
            Stop->>DB: UPDATE 포지션 상태
        end
    end

    Note over UI,KI: 관심종목 동기화
    UI->>API: POST /watchlist/sync/start
    API->>Sync: start_sync_monitoring()
    loop 5분 주기
        Sync->>KI: get_condition_stocks(condition_name)
        Sync->>DB: INSERT/UPDATE 관심종목
        Sync->>KI: add_to_watchlist(stock_code)
    end

    Note over Exec: 통합 주문 실행
    Exec->>DB: SELECT PENDING signals (조건식 + 전략 + 스캘핑)
    alt 처리할 신호 있음
        Exec->>KI: get_current_price(stock)
        Exec->>Exec: 수량 계산(max_invest_amount)
        Exec->>KI: place_buy_order(...)
        alt 주문 성공
            Exec->>DB: UPDATE status=ORDERED
            Exec->>DB: INSERT positions
        else 실패(재시도 최대 3회)
            Exec->>DB: UPDATE status=FAILED
        end
    end
```

---

## 컴포넌트 간 데이터 흐름 (C4/컨텍스트 관점)

```mermaid
flowchart LR
    UI[Web UI / static] -->|HTTP| API[FastAPI app\nmain.py]
    API -->|조건식 모니터링| Mon[ConditionMonitor\ncondition_monitor.py]
    API -->|전략 매매 관리| Str[StrategyManager\nstrategy_manager.py]
    API -->|스캘핑 전략| Scalp[ScalpingStrategy\nscalping_strategy.py]
    API -->|손절/익절| Stop[StopLossManager\nstop_loss_manager.py]
    API -->|관심종목 동기화| Sync[WatchlistSync\nwatchlist_sync_manager.py]
    API --> Exec[BuyOrderExecutor\nbuy_order_executor.py]
    API --> Sig[SignalManager\nsignal_manager.py]

    Mon -->|조건목록/검색| KI[Kiwoom API]
    Mon -->|신호 생성| Sig
    Str -->|관심종목/전략신호| Sig
    Scalp -->|스캘핑 신호| Sig
    Stop -->|보유종목/매도| KI
    Sync -->|조건식 종목| KI
    Sync -->|관심종목 추가| KI
    Sig -->|INSERT/SELECT/UPDATE| DB[(SQLite\nstock_pipeline.db)]

    Exec -->|조회 PENDING| DB
    Exec -->|현재가/주문| KI
    Exec -->|상태 갱신| DB
    Stop -->|포지션 조회| DB
```

---

## 데이터 모델 스냅샷

### 기존 테이블 (유지)
- `pending_buy_signals`
  - id, condition_id, stock_code, stock_name, detected_at, status(PENDING/ORDERED/FAILED/...), signal_type(condition/reference/strategy)
  - reference_candle_high, reference_candle_date, target_price
  - Unique: (detected_date, condition_id, stock_code)

- `auto_trade_conditions`
  - id, condition_name(UNIQUE), api_condition_id, is_enabled, updated_at

- `auto_trade_settings`
  - id, is_enabled, max_invest_amount, stop_loss_rate, take_profit_rate, updated_at

### 새로운 테이블
- `watchlist_stocks` (관심종목)
  - id, stock_code, stock_name, added_at, is_active, notes
  - Unique: (stock_code)

- `trading_strategies` (매매 전략 설정)
  - id, strategy_name, strategy_type(MOMENTUM/DISPARITY/BOLLINGER/RSI/SCALPING), is_enabled, parameters(JSON), updated_at
  - Unique: (strategy_name)

- `strategy_signals` (전략별 신호 히스토리)
  - id, strategy_id, stock_code, signal_type(BUY/SELL), signal_value, detected_at, status(ACTIVE/EXPIRED/EXECUTED)
  - Unique: (strategy_id, stock_code, detected_at)

- `positions` (보유 종목)
  - id, stock_code, stock_name, quantity, avg_price, current_price, profit_loss, profit_loss_rate, created_at, updated_at
  - Unique: (stock_code)

- `sell_orders` (매도 주문)
  - id, position_id, stock_code, quantity, order_price, order_type(STOP_LOSS/TAKE_PROFIT/MANUAL), status(PENDING/EXECUTED/CANCELLED), created_at, executed_at
  - Foreign Key: position_id -> positions.id

- `condition_watchlist_sync` (조건식-관심종목 동기화 로그)
  - id, condition_name, stock_code, action(ADD/REMOVE), sync_time, status(SUCCESS/FAILED)
  - Unique: (condition_name, stock_code, sync_time)

---

## 주요 엔드포인트와 내부 처리

### 기존 모니터링 관리 (유지)
- **POST /monitoring/start**: `ConditionMonitor.start_periodic_monitoring` 시작 → 10분 간격 스캔 → 신호 생성
- **POST /monitoring/stop**: 모니터링 중지 및 WebSocket 종료
- **GET /monitoring/status**: 통합 상태 조회 (모니터링/신호/API제한/실행기 상태)

### 새로운 전략 매매 관리
- **POST /strategy/start**: `StrategyManager.start_strategy_monitoring` 시작 → 1분 간격 스캔 → 전략 신호 생성
- **POST /strategy/stop**: 전략 모니터링 중지
- **GET /strategy/status**: 전략 모니터링 상태 조회

### 스캘핑 전략 관리
- **POST /scalping/start**: `ScalpingStrategyManager.start_scalping_monitoring` 시작 → 30초 간격 스캔
- **POST /scalping/stop**: 스캘핑 모니터링 중지
- **GET /scalping/status**: 스캘핑 상태 조회

### 손절/익절 관리
- **POST /stop-loss/start**: `StopLossManager.start_monitoring` 시작 → 30초 간격 모니터링
- **POST /stop-loss/stop**: 손절/익절 모니터링 중지
- **GET /stop-loss/status**: 손절/익절 상태 조회

### 관심종목 관리
- **GET /watchlist/**: 관심종목 목록 조회
- **POST /watchlist/add**: 관심종목 추가
- **DELETE /watchlist/{stock_code}**: 관심종목 제거
- **PUT /watchlist/{stock_code}/toggle**: 관심종목 활성화/비활성화

### 관심종목 동기화 관리
- **POST /watchlist/sync/start**: `WatchlistSyncManager.start_sync_monitoring` 시작 → 5분 간격 동기화
- **POST /watchlist/sync/stop**: 동기화 중지
- **GET /watchlist/sync/status**: 동기화 상태 조회

### 전략 설정 관리
- **GET /strategies/**: 전략 목록 조회
- **POST /strategies/{strategy_type}/configure**: 전략 파라미터 설정
- **PUT /strategies/{strategy_id}/toggle**: 전략 활성화/비활성화

### 신호 관리 (확장)
- **GET /signals/pending**: DB에서 신호 조회 + 실시간 현재가 보강 (조건식 + 전략)
- **GET /signals/statistics**: 신호 통계 및 히스토리 조회
- **GET /signals/by-strategy/{strategy_id}**: 특정 전략의 신호 조회

### 조건식 관리 (유지)
- **GET /conditions/**: 조건식 목록 조회 (키움 API)
- **GET /conditions/{id}/stocks**: 특정 조건식의 종목 조회
- **POST /conditions/toggle**: 조건식 활성화/비활성화 토글

### 자동매매 설정 (유지)
- **GET /trading/settings**: 자동매매 설정 조회
- **POST /trading/settings**: 자동매매 설정 업데이트
- **GET /buy-executor/status**: 백그라운드 주문 실행기 상태/제어

### 차트 및 분석 (확장)
- **GET /chart/image/{stock_code}**: 종목 차트 이미지 생성 (기존 + 전략 지표)
- **GET /chart/strategy/{stock_code}/{strategy_type}**: 특정 전략 지표 차트
- **GET /chart/strategy/{stock_code}**: 모든 전략 지표 종합 차트
- **GET /kiwoom/account**: 계좌 정보 조회
- **GET /kiwoom/balance**: 잔고 조회
- **GET /stocks/{stock_code}/info**: 종목 정보 및 토론 크롤링

### 포지션 관리
- **GET /positions/**: 보유 종목 목록 조회
- **GET /positions/{stock_code}**: 특정 종목 포지션 조회
- **POST /positions/{stock_code}/sell**: 수동 매도 주문

### 매도 주문 관리
- **GET /sell-orders/**: 매도 주문 목록 조회
- **POST /sell-orders/{order_id}/cancel**: 매도 주문 취소

### 시스템 상태 (유지)
- **GET /api/status**: API 연결 상태 체크
- **GET /api/rate-limit-status**: API 제한 상태 상세 조회

---

## 전략별 매매 로직

### 1. 모멘텀 전략 (MOMENTUM)
- **공식**: `모멘텀 = 당일 종가 - n일 전 종가`
- **매수 조건**: 모멘텀 > 0 (0선 상향 돌파) AND 모멘텀 상승 추세
- **매도 조건**: 모멘텀 < 0 (0선 하향 돌파) AND 모멘텀 하락 추세
- **파라미터**: 모멘텀 계산 기간(24봉), 추세 확인 기간(3일)

### 2. 이격도 전략 (DISPARITY)
- **매수 조건**: 이격도 < 95% (현재가가 이동평균 대비 5% 이상 낮음)
- **매도 조건**: 이격도 > 105% (현재가가 이동평균 대비 5% 이상 높음)
- **파라미터**: 이동평균 기간(20), 매수 임계값(95%), 매도 임계값(105%)

### 3. 볼린저밴드 전략 (BOLLINGER)
- **매수 조건**: 현재가가 하단밴드 터치 후 반등
- **매도 조건**: 현재가가 상단밴드 터치 후 하락
- **파라미터**: 이동평균 기간(20), 표준편차 배수(2), 반등 확인 기간(3일)

### 4. RSI 전략 (RSI)
- **매수 조건**: RSI < 30 (과매도) AND RSI 상승 전환 AND 거래량 >= 가중평균거래량 * 1.5배
- **매도 조건**: RSI > 70 (과매수) AND RSI 하락 전환 AND 거래량 >= 가중평균거래량 * 1.5배
- **파라미터**: RSI 기간(14), 과매도 임계값(30), 과매수 임계값(70), 거래량 기간(20), 거래량 임계값(1.5배), 거래량 필터 사용 여부

### 5. 스캘핑 전략 (SCALPING)
- **개념**: 고빈도 단기 매매를 통한 소폭 수익 실현
- **매수 조건**: 빠른 가격 변동 감지 + 거래량 급증
- **매도 조건**: 목표 수익률 달성 또는 손절선 도달
- **파라미터**: 목표 수익률(0.5%), 손절률(0.3%), 최대 보유 시간(30분)

---

## 에러/레이트리미트 고려사항

- `api_rate_limiter`: API 호출 가용성 확인/기록, 429/제한 시 딜레이 및 일부 작업 스킵
  - 호출 한도: 1분 60회, 최소 간격 1.0초 (전략매매에 맞게 튜닝)
  - 상태: NORMAL/WARNING/LIMITED/RECOVERING, 제한 시 최대 10분 대기
- `ConditionMonitor` 기준봉 전략: 데이터 부족/제한 시 스킵 및 재시도 지연
- `StrategyManager` 전략 계산: 차트 데이터 부족 시 스킵, 계산 오류 시 로그 기록
  - 차트 데이터 10분 캐시, 동일 종목 반복 조회 최소화
  - API 제한 감지 시 조회 건너뜀 후 다음 루프로 복귀
- `ScalpingStrategyManager` 스캘핑: 고빈도 호출로 인한 API 제한 주의
  - 30초 주기로 최적화, 제한 시 자동 대기
- `StopLossManager` 손절/익절: 실시간 가격 조회 최적화
  - 30초 주기 모니터링, 포지션별 배치 처리
- `WatchlistSyncManager` 동기화: 조건식 종목 조회 최적화
  - 5분 주기 동기화, 중복 종목 제거 로직
- `BuyOrderExecutor` 주문: 실패 시 최대 3회 재시도, 간격 30초

---

## 로그/운영 포인트

- 앱 수명주기에서: 인증 → WebSocket 연결 시도 → `BuyOrderExecutor` 시작 → `StrategyManager` 시작 → `StopLossManager` 시작 → 종료 시 우아한 정리
- 주요 로그 태그: [STARTUP], [STATIC], [API], [CONDITION_MONITOR], [STRATEGY_MANAGER], [SCALPING], [STOP_LOSS], [WATCHLIST_SYNC], [SIGNAL_MANAGER], [BUY_EXECUTOR]
- 전략별 로그 태그: [MOMENTUM], [DISPARITY], [BOLLINGER], [RSI], [SCALPING]
- 모니터링 주기별 로그: [CONDITION_10MIN], [STRATEGY_1MIN], [SCALPING_30SEC], [STOP_LOSS_30SEC], [SYNC_5MIN]

---

## 참고 코드 위치

- FastAPI 및 엔드포인트: `main.py`
- 조건식 모니터링 로직: `condition_monitor.py`
- 전략 매매 로직: `strategy_manager.py`
- 스캘핑 전략 로직: `scalping_strategy.py`
- 손절/익절 로직: `stop_loss_manager.py`
- 관심종목 동기화 로직: `watchlist_sync_manager.py`
- 신호 저장/중복 방지: `signal_manager.py`
- 주문 실행기: `buy_order_executor.py`
- 테이블/세션: `models.py`
- 키움 API 연동: `kiwoom_api.py`
- API 제한 관리: `api_rate_limiter.py`