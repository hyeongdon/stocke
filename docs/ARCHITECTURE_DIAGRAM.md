# 키움증권 자동매매 시스템 아키텍처 다이어그램

## 1. 시스템 전체 아키텍처

```mermaid
graph TB
    subgraph "Frontend Layer"
        UI[Web UI<br/>static/index.html<br/>app.js]
        UI_MODULES[UI Modules<br/>- account-manager.js<br/>- chart-manager.js<br/>- condition-manager.js<br/>- strategy-manager.js<br/>- ui-utils.js]
        UI --> UI_MODULES
    end

    subgraph "API Layer"
        API[FastAPI Server<br/>main.py]
        API_ROUTES[API Endpoints<br/>- /monitoring/*<br/>- /strategy/*<br/>- /scalping/*<br/>- /watchlist/*<br/>- /trading/*<br/>- /chart/*]
        API --> API_ROUTES
    end

    subgraph "Business Logic Layer"
        COND_MON[ConditionMonitor<br/>condition_monitor.py<br/>조건식 모니터링<br/>10분 주기]
        STRAT_MGR[StrategyManager<br/>strategy_manager.py<br/>전략 매매<br/>1분 주기]
        SCALP_MGR[ScalpingStrategy<br/>scalping_strategy.py<br/>스캘핑 전략<br/>30초 주기]
        STOP_LOSS[StopLossManager<br/>stop_loss_manager.py<br/>손절/익절<br/>30초 주기]
        WATCH_SYNC[WatchlistSync<br/>watchlist_sync_manager.py<br/>관심종목 동기화<br/>5분 주기]
        SIG_MGR[SignalManager<br/>signal_manager.py<br/>신호 관리]
        BUY_EXEC[BuyOrderExecutor<br/>buy_order_executor.py<br/>매수 주문 실행]
        CLEANUP[CleanupScheduler<br/>cleanup_scheduler.py<br/>자정 정리]
    end

    subgraph "Data Layer"
        DB[(SQLite Database<br/>stock_pipeline.db)]
        TABLES[Tables<br/>- pending_buy_signals<br/>- watchlist_stocks<br/>- trading_strategies<br/>- strategy_signals<br/>- positions<br/>- sell_orders<br/>- auto_trade_conditions<br/>- auto_trade_settings]
        DB --> TABLES
    end

    subgraph "External Services"
        KIWOOM[Kiwoom API<br/>REST + WebSocket]
        NAVER[Naver API<br/>뉴스 검색]
        NAVER_CRAWL[Naver Crawler<br/>토론 크롤링]
    end

    subgraph "Infrastructure"
        TOKEN_MGR[TokenManager<br/>token_manager.py<br/>인증 토큰 관리]
        RATE_LIMIT[APIRateLimiter<br/>api_rate_limiter.py<br/>API 제한 관리]
        CONFIG[Config<br/>config.py<br/>환경 설정]
    end

    UI -->|HTTP/HTTPS| API
    API --> COND_MON
    API --> STRAT_MGR
    API --> SCALP_MGR
    API --> STOP_LOSS
    API --> WATCH_SYNC
    API --> SIG_MGR
    API --> BUY_EXEC
    API --> CLEANUP

    COND_MON --> SIG_MGR
    STRAT_MGR --> SIG_MGR
    SCALP_MGR --> SIG_MGR
    SIG_MGR --> DB
    BUY_EXEC --> DB
    STOP_LOSS --> DB
    WATCH_SYNC --> DB

    COND_MON -->|조건식 조회| KIWOOM
    STRAT_MGR -->|차트 데이터| KIWOOM
    SCALP_MGR -->|현재가 조회| KIWOOM
    STOP_LOSS -->|현재가/매도| KIWOOM
    WATCH_SYNC -->|조건식 종목| KIWOOM
    BUY_EXEC -->|매수 주문| KIWOOM
    API -->|뉴스 검색| NAVER
    API -->|토론 크롤링| NAVER_CRAWL

    KIWOOM --> TOKEN_MGR
    KIWOOM --> RATE_LIMIT
    TOKEN_MGR --> CONFIG
    RATE_LIMIT --> CONFIG
```

## 2. 데이터베이스 ER 다이어그램

```mermaid
erDiagram
    AUTO_TRADE_CONDITIONS ||--o{ PENDING_BUY_SIGNALS : "has"
    AUTO_TRADE_SETTINGS ||--o{ PENDING_BUY_SIGNALS : "configures"
    WATCHLIST_STOCKS ||--o{ STRATEGY_SIGNALS : "generates"
    TRADING_STRATEGIES ||--o{ STRATEGY_SIGNALS : "produces"
    PENDING_BUY_SIGNALS ||--o| POSITIONS : "creates"
    POSITIONS ||--o{ SELL_ORDERS : "triggers"
    AUTO_TRADE_CONDITIONS ||--o{ WATCHLIST_STOCKS : "syncs"
    CONDITION_WATCHLIST_SYNC }o--|| AUTO_TRADE_CONDITIONS : "tracks"

    AUTO_TRADE_CONDITIONS {
        int id PK
        string condition_name UK
        string api_condition_id
        boolean is_enabled
        datetime updated_at
    }

    AUTO_TRADE_SETTINGS {
        int id PK
        boolean is_enabled
        int max_invest_amount
        int stop_loss_rate
        int take_profit_rate
        datetime updated_at
    }

    PENDING_BUY_SIGNALS {
        int id PK
        int condition_id FK
        string stock_code
        string stock_name
        datetime detected_at
        date detected_date
        string status
        string signal_type
        int reference_candle_high
        datetime reference_candle_date
        int target_price
    }

    WATCHLIST_STOCKS {
        int id PK
        string stock_code UK
        string stock_name
        datetime added_at
        boolean is_active
        string source_type
        int condition_id FK
        string condition_name
    }

    TRADING_STRATEGIES {
        int id PK
        string strategy_name UK
        string strategy_type
        boolean is_enabled
        json parameters
        datetime updated_at
    }

    STRATEGY_SIGNALS {
        int id PK
        int strategy_id FK
        string stock_code
        string stock_name
        string signal_type
        float signal_value
        datetime detected_at
        date detected_date
        string status
        json additional_data
    }

    POSITIONS {
        int id PK
        string stock_code
        string stock_name
        int buy_price
        int buy_quantity
        int buy_amount
        float stop_loss_rate
        float take_profit_rate
        int stop_loss_price
        int take_profit_price
        string status
        int current_price
        int current_profit_loss
        float current_profit_loss_rate
        datetime buy_time
        datetime sell_time
        datetime last_monitored
    }

    SELL_ORDERS {
        int id PK
        int position_id FK
        string stock_code
        string stock_name
        int sell_price
        int sell_quantity
        int sell_amount
        string sell_reason
        string sell_reason_detail
        int profit_loss
        float profit_loss_rate
        string status
        datetime created_at
        datetime ordered_at
        datetime completed_at
    }

    CONDITION_WATCHLIST_SYNC {
        int id PK
        int condition_id FK
        string condition_name
        string stock_code
        string stock_name
        string sync_status
        datetime last_sync_at
        boolean added_to_watchlist
    }
```

## 3. 프로세스 플로우 다이어그램

```mermaid
flowchart TD
    START([시스템 시작]) --> INIT[애플리케이션 초기화]
    INIT --> AUTH[키움 API 인증]
    AUTH --> WS_CONN{WebSocket<br/>연결}
    WS_CONN -->|성공| WS_OK[WebSocket 연결 성공]
    WS_CONN -->|실패| REST_ONLY[REST API만 사용]
    WS_OK --> START_SERVICES
    REST_ONLY --> START_SERVICES

    START_SERVICES[백그라운드 서비스 시작] --> START_BUY[매수 주문 실행기 시작]
    START_BUY --> START_STOP[손절/익절 모니터링 시작]
    START_STOP --> START_CLEANUP[자정 정리 스케줄러 시작]
    START_CLEANUP --> READY[시스템 준비 완료]

    READY --> MONITORING{모니터링<br/>활성화?}
    
    MONITORING -->|조건식 모니터링| COND_LOOP[10분 주기 루프]
    COND_LOOP --> COND_CHECK[조건식 목록 조회]
    COND_CHECK --> COND_ENABLED{활성화된<br/>조건식?}
    COND_ENABLED -->|있음| COND_SEARCH[조건식 종목 검색]
    COND_SEARCH --> COND_STRATEGY[기준봉 전략 적용]
    COND_STRATEGY --> COND_SIGNAL{신호<br/>생성?}
    COND_SIGNAL -->|생성| SAVE_SIGNAL[신호 저장<br/>PENDING 상태]
    COND_SIGNAL -->|없음| COND_LOOP
    SAVE_SIGNAL --> COND_LOOP
    COND_ENABLED -->|없음| COND_LOOP

    MONITORING -->|전략 매매| STRAT_LOOP[1분 주기 루프]
    STRAT_LOOP --> STRAT_WATCH[관심종목 조회]
    STRAT_WATCH --> STRAT_CHART[차트 데이터 조회]
    STRAT_CHART --> STRAT_CALC[전략 계산<br/>모멘텀/이격도/볼린저/RSI]
    STRAT_CALC --> STRAT_SIGNAL{매수/매도<br/>신호?}
    STRAT_SIGNAL -->|있음| SAVE_SIGNAL
    STRAT_SIGNAL -->|없음| STRAT_LOOP

    MONITORING -->|스캘핑 전략| SCALP_LOOP[30초 주기 루프]
    SCALP_LOOP --> SCALP_CHECK[활성 종목 확인]
    SCALP_CHECK --> SCALP_PRICE[현재가 조회]
    SCALP_PRICE --> SCALP_SIGNAL{스캘핑<br/>신호?}
    SCALP_SIGNAL -->|있음| SAVE_SIGNAL
    SCALP_SIGNAL -->|없음| SCALP_LOOP

    MONITORING -->|손절/익절| STOP_LOOP[30초 주기 루프]
    STOP_LOOP --> STOP_POS[보유 종목 조회]
    STOP_POS --> STOP_PRICE[현재가 조회]
    STOP_PRICE --> STOP_CHECK{손절/익절<br/>조건?}
    STOP_CHECK -->|만족| STOP_SELL[매도 주문 실행]
    STOP_CHECK -->|불만족| STOP_LOOP
    STOP_SELL --> STOP_UPDATE[포지션 상태 업데이트]
    STOP_UPDATE --> STOP_LOOP

    MONITORING -->|관심종목 동기화| SYNC_LOOP[5분 주기 루프]
    SYNC_LOOP --> SYNC_COND[조건식 종목 조회]
    SYNC_COND --> SYNC_UPDATE[관심종목 DB 업데이트]
    SYNC_UPDATE --> SYNC_API[키움 관심종목 추가]
    SYNC_API --> SYNC_LOOP

    SAVE_SIGNAL --> BUY_PROCESS[매수 주문 처리]
    BUY_PROCESS --> BUY_CHECK[PENDING 신호 조회]
    BUY_CHECK --> BUY_VALID{신호<br/>유효?}
    BUY_VALID -->|유효| BUY_PRICE[현재가 조회]
    BUY_PRICE --> BUY_CALC[수량 계산]
    BUY_CALC --> BUY_ORDER[매수 주문 실행]
    BUY_ORDER --> BUY_RESULT{주문<br/>성공?}
    BUY_RESULT -->|성공| BUY_UPDATE[상태: ORDERED<br/>포지션 생성]
    BUY_RESULT -->|실패| BUY_RETRY{재시도<br/>가능?}
    BUY_RETRY -->|가능| BUY_WAIT[30초 대기]
    BUY_WAIT --> BUY_ORDER
    BUY_RETRY -->|불가능| BUY_FAIL[상태: FAILED]
    BUY_VALID -->|무효| BUY_PROCESS
    BUY_UPDATE --> BUY_PROCESS
    BUY_FAIL --> BUY_PROCESS
```

## 4. 전략별 매매 로직 플로우

```mermaid
flowchart TD
    START([전략 모니터링 시작]) --> LOAD[관심종목 로드]
    LOAD --> LOOP[각 종목 순회]
    LOOP --> CHART[차트 데이터 조회<br/>5분봉]
    CHART --> CACHE{캐시<br/>유효?}
    CACHE -->|유효| USE_CACHE[캐시 사용]
    CACHE -->|무효| FETCH[API 호출]
    FETCH --> USE_CACHE

    USE_CACHE --> STRATEGY{전략<br/>선택}
    
    STRATEGY -->|모멘텀| MOMENTUM[모멘텀 계산<br/>종가 - n일전 종가]
    MOMENTUM --> MOM_CHECK{모멘텀 > 0<br/>AND 상승 추세?}
    MOM_CHECK -->|예| MOM_BUY[매수 신호]
    MOM_CHECK -->|아니오| NEXT

    STRATEGY -->|이격도| DISPARITY[이격도 계산<br/>현재가 / 이동평균 * 100]
    DISPARITY --> DISP_CHECK{이격도 < 95%<br/>매수 OR > 105% 매도?}
    DISP_CHECK -->|매수| DISP_BUY[매수 신호]
    DISP_CHECK -->|매도| DISP_SELL[매도 신호]
    DISP_CHECK -->|없음| NEXT

    STRATEGY -->|볼린저밴드| BOLLINGER[볼린저밴드 계산<br/>MA ± 2σ]
    BOLLINGER --> BOL_CHECK{하단밴드 터치<br/>후 반등?}
    BOL_CHECK -->|예| BOL_BUY[매수 신호]
    BOL_CHECK -->|아니오| NEXT

    STRATEGY -->|RSI| RSI[RSI 계산<br/>14일 기준]
    RSI --> RSI_VOL[거래량 확인<br/>가중평균 * 1.5배]
    RSI_VOL --> RSI_CHECK{RSI < 30<br/>AND 상승 전환<br/>AND 거래량 증가?}
    RSI_CHECK -->|예| RSI_BUY[매수 신호]
    RSI_CHECK -->|아니오| RSI_CHECK2{RSI > 70<br/>AND 하락 전환<br/>AND 거래량 증가?}
    RSI_CHECK2 -->|예| RSI_SELL[매도 신호]
    RSI_CHECK2 -->|아니오| NEXT

    STRATEGY -->|차이킨| CHAIKIN[차이킨 오실레이터<br/>계산]
    CHAIKIN --> CHK_CHECK{오실레이터<br/>0선 상향 돌파?}
    CHK_CHECK -->|예| CHK_BUY[매수 신호]
    CHK_CHECK -->|아니오| NEXT

    MOM_BUY --> SAVE
    DISP_BUY --> SAVE
    DISP_SELL --> SAVE_SELL
    BOL_BUY --> SAVE
    RSI_BUY --> SAVE
    RSI_SELL --> SAVE_SELL
    CHK_BUY --> SAVE

    SAVE[신호 저장<br/>SignalManager] --> NEXT[다음 종목]
    SAVE_SELL[매도 신호 저장] --> NEXT
    NEXT --> MORE{더 많은<br/>종목?}
    MORE -->|예| LOOP
    MORE -->|아니오| WAIT[1분 대기]
    WAIT --> LOAD
```

## 5. API 엔드포인트 구조

```mermaid
graph LR
    API[FastAPI Server] --> MON[모니터링 API]
    API --> STRAT[전략 API]
    API --> SCALP[스캘핑 API]
    API --> WATCH[관심종목 API]
    API --> TRADE[매매 API]
    API --> CHART[차트 API]
    API --> ACCOUNT[계좌 API]
    API --> SIGNAL[신호 API]

    MON --> MON1[POST /monitoring/start]
    MON --> MON2[POST /monitoring/stop]
    MON --> MON3[GET /monitoring/status]

    STRAT --> STRAT1[POST /strategy/start]
    STRAT --> STRAT2[POST /strategy/stop]
    STRAT --> STRAT3[GET /strategy/status]
    STRAT --> STRAT4[GET /strategies/]
    STRAT --> STRAT5[POST /strategies/{type}/configure]

    SCALP --> SCALP1[POST /scalping/start]
    SCALP --> SCALP2[POST /scalping/stop]
    SCALP --> SCALP3[GET /scalping/status]

    WATCH --> WATCH1[GET /watchlist/]
    WATCH --> WATCH2[POST /watchlist/add]
    WATCH --> WATCH3[DELETE /watchlist/{code}]
    WATCH --> WATCH4[POST /watchlist/sync/start]

    TRADE --> TRADE1[POST /trading/buy]
    TRADE --> TRADE2[GET /trading/settings]
    TRADE --> TRADE3[POST /trading/settings]
    TRADE --> TRADE4[GET /positions/]

    CHART --> CHART1[GET /chart/image/{code}]
    CHART --> CHART2[GET /chart/strategy/{code}/{type}]
    CHART --> CHART3[GET /stocks/{code}/info]

    ACCOUNT --> ACC1[GET /account/balance]
    ACCOUNT --> ACC2[GET /account/holdings]
    ACCOUNT --> ACC3[GET /account/profit]

    SIGNAL --> SIG1[GET /signals/pending]
    SIGNAL --> SIG2[GET /signals/statistics]
    SIGNAL --> SIG3[GET /signals/by-strategy/{id}]
```

## 6. 컴포넌트 상호작용 시퀀스

```mermaid
sequenceDiagram
    autonumber
    participant User as 사용자
    participant UI as Web UI
    participant API as FastAPI
    participant CondMon as ConditionMonitor
    participant StratMgr as StrategyManager
    participant SigMgr as SignalManager
    participant BuyExec as BuyOrderExecutor
    participant StopLoss as StopLossManager
    participant DB as Database
    participant Kiwoom as Kiwoom API

    User->>UI: 모니터링 시작 클릭
    UI->>API: POST /monitoring/start
    API->>CondMon: start_periodic_monitoring()
    CondMon->>CondMon: 10분 주기 루프 시작

    loop 10분마다
        CondMon->>Kiwoom: 조건식 목록 조회
        Kiwoom-->>CondMon: 조건식 목록 반환
        CondMon->>Kiwoom: 조건식 종목 검색
        Kiwoom-->>CondMon: 종목 목록 반환
        CondMon->>CondMon: 기준봉 전략 적용
        CondMon->>SigMgr: create_signal()
        SigMgr->>DB: INSERT pending_buy_signals
    end

    User->>UI: 전략 매매 시작 클릭
    UI->>API: POST /strategy/start
    API->>StratMgr: start_strategy_monitoring()
    StratMgr->>StratMgr: 1분 주기 루프 시작

    loop 1분마다
        StratMgr->>DB: SELECT 관심종목
        DB-->>StratMgr: 관심종목 목록
        loop 각 종목
            StratMgr->>Kiwoom: 차트 데이터 조회
            Kiwoom-->>StratMgr: 차트 데이터
            StratMgr->>StratMgr: 전략 계산
            alt 신호 발생
                StratMgr->>SigMgr: create_signal()
                SigMgr->>DB: INSERT pending_buy_signals
            end
        end
    end

    BuyExec->>BuyExec: 백그라운드 주문 처리 루프
    loop 지속적으로
        BuyExec->>DB: SELECT PENDING 신호
        DB-->>BuyExec: PENDING 신호 목록
        BuyExec->>Kiwoom: 현재가 조회
        Kiwoom-->>BuyExec: 현재가
        BuyExec->>BuyExec: 수량 계산
        BuyExec->>Kiwoom: 매수 주문
        alt 주문 성공
            Kiwoom-->>BuyExec: 주문 성공
            BuyExec->>DB: UPDATE status=ORDERED
            BuyExec->>DB: INSERT positions
        else 주문 실패
            BuyExec->>DB: UPDATE status=FAILED
        end
    end

    StopLoss->>StopLoss: 30초 주기 모니터링 루프
    loop 30초마다
        StopLoss->>DB: SELECT 보유 종목
        DB-->>StopLoss: 포지션 목록
        loop 각 포지션
            StopLoss->>Kiwoom: 현재가 조회
            Kiwoom-->>StopLoss: 현재가
            StopLoss->>StopLoss: 손절/익절 조건 확인
            alt 조건 만족
                StopLoss->>Kiwoom: 매도 주문
                Kiwoom-->>StopLoss: 주문 성공
                StopLoss->>DB: UPDATE 포지션 상태
                StopLoss->>DB: INSERT sell_orders
            end
        end
    end
```

## 7. 데이터 흐름도

```mermaid
flowchart LR
    subgraph "데이터 입력"
        KIWOOM_API[키움 API<br/>조건식/차트/시세]
        NAVER_API[네이버 API<br/>뉴스/토론]
    end

    subgraph "데이터 처리"
        COND_PROC[조건식 처리]
        STRAT_PROC[전략 처리]
        SIGNAL_PROC[신호 처리]
    end

    subgraph "데이터 저장"
        PENDING_TBL[(pending_buy_signals)]
        WATCH_TBL[(watchlist_stocks)]
        STRAT_TBL[(trading_strategies)]
        SIGNAL_TBL[(strategy_signals)]
        POS_TBL[(positions)]
        SELL_TBL[(sell_orders)]
    end

    subgraph "데이터 출력"
        UI_DASH[웹 대시보드]
        CHART_IMG[차트 이미지]
        API_RESP[API 응답]
    end

    KIWOOM_API --> COND_PROC
    KIWOOM_API --> STRAT_PROC
    NAVER_API --> UI_DASH

    COND_PROC --> SIGNAL_PROC
    STRAT_PROC --> SIGNAL_PROC
    SIGNAL_PROC --> PENDING_TBL

    COND_PROC --> WATCH_TBL
    STRAT_PROC --> STRAT_TBL
    STRAT_PROC --> SIGNAL_TBL

    PENDING_TBL --> POS_TBL
    POS_TBL --> SELL_TBL

    PENDING_TBL --> UI_DASH
    WATCH_TBL --> UI_DASH
    STRAT_TBL --> UI_DASH
    SIGNAL_TBL --> UI_DASH
    POS_TBL --> UI_DASH
    SELL_TBL --> UI_DASH

    KIWOOM_API --> CHART_IMG
    CHART_IMG --> UI_DASH

    PENDING_TBL --> API_RESP
    WATCH_TBL --> API_RESP
    STRAT_TBL --> API_RESP
    POS_TBL --> API_RESP
```

## 8. 모니터링 주기 및 타이밍

```mermaid
gantt
    title 시스템 모니터링 주기
    dateFormat X
    axisFormat %s초

    section 조건식 모니터링
    조건식 스캔 (10분 주기)    :0, 600s
    조건식 스캔 (10분 주기)    :600, 600s
    조건식 스캔 (10분 주기)    :1200, 600s

    section 전략 매매
    전략 스캔 (1분 주기)       :0, 60s
    전략 스캔 (1분 주기)       :60, 60s
    전략 스캔 (1분 주기)       :120, 60s

    section 스캘핑 전략
    스캘핑 스캔 (30초 주기)    :0, 30s
    스캘핑 스캔 (30초 주기)    :30, 30s
    스캘핑 스캔 (30초 주기)    :60, 30s

    section 손절/익절
    손절/익절 모니터링 (30초 주기) :0, 30s
    손절/익절 모니터링 (30초 주기) :30, 30s
    손절/익절 모니터링 (30초 주기) :60, 30s

    section 관심종목 동기화
    동기화 (5분 주기)          :0, 300s
    동기화 (5분 주기)          :300, 300s
    동기화 (5분 주기)          :600, 300s
```

## 주요 컴포넌트 설명

### 백엔드 컴포넌트
- **main.py**: FastAPI 서버, 모든 API 엔드포인트 정의
- **condition_monitor.py**: 키움 조건식 모니터링 (10분 주기)
- **strategy_manager.py**: 전략 매매 관리 (모멘텀, 이격도, 볼린저밴드, RSI)
- **scalping_strategy.py**: 스캘핑 전략 관리 (30초 주기)
- **stop_loss_manager.py**: 손절/익절 자동화 (30초 주기)
- **watchlist_sync_manager.py**: 조건식-관심종목 동기화 (5분 주기)
- **signal_manager.py**: 신호 중복 방지 및 관리
- **buy_order_executor.py**: 매수 주문 자동 실행
- **kiwoom_api.py**: 키움 API 연동 (REST + WebSocket)
- **api_rate_limiter.py**: API 호출 제한 관리
- **token_manager.py**: 인증 토큰 관리

### 프론트엔드 컴포넌트
- **index.html**: 메인 대시보드
- **app.js**: 메인 애플리케이션 로직
- **modules/**: UI 모듈들 (계좌, 차트, 조건식, 전략, 유틸리티)

### 데이터베이스
- **SQLite**: 모든 데이터 영구 저장
- **8개 주요 테이블**: 신호, 관심종목, 전략, 포지션, 매도주문 등

