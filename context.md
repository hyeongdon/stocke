최근에 메소드가 추가되고 삭제가 많이 됫는데 
현재 프로젝트 기준으로 엔드포인트별 프로세스 흐름도 다이어그램으로 그려줘

DB)  
PendingBuySignal
├── id (PK)
├── condition_id
├── stock_code
├── stock_name
├── detected_at
├── status (PENDING/ORDERED/FAILED)
├── reference_candle_high (대량거래용)
├── reference_candle_date (대량거래용)
└── target_price (대량거래용)

AutoTradeCondition
├── id (PK)
├── condition_name (UNIQUE)
├── api_condition_id
├── is_enabled
└── updated_at

AutoTradeSettings
├── id (PK)
├── is_enabled
├── max_invest_amount
├── stop_loss_rate
├── take_profit_rate
└── updated_at

## DB 접속 및 테이블별 쿼리 (복사해서 바로 사용)

아래 예시는 로컬 SQLite 파일(`stock_pipeline.db`)을 직접 조회하는 방법입니다. DB 파일 경로는 `config.py`의 `Config.DATABASE_URL` 기준으로 `프로젝트루트/stock_pipeline.db` 입니다.

### 1) PowerShell에서 sqlite3로 접속 (권장)
sqlite3가 설치되어 있다면 다음을 그대로 실행하세요.
```powershell
cd C:\Users\A87719\project\stocke_new\stocke
sqlite3 .\stock_pipeline.db
```

접속 후 유용한 명령:
```sql
.tables              -- 테이블 목록
.schema              -- 전체 스키마
.schema pending_buy_signals  -- 특정 테이블 스키마
.headers on          -- 컬럼명 표시
.mode column         -- 컬럼 형태 출력
```

종료:
```sql
.quit
```  

### 테이블: pending_buy_signals
기본 조회
```sql
SELECT *
FROM pending_buy_signals
ORDER BY detected_at DESC
LIMIT 50;
```

상태별 집계
```sql
SELECT status, COUNT(*) AS cnt
FROM pending_buy_signals
GROUP BY status
ORDER BY cnt DESC;
```

특정 종목코드/조건식 필터
```sql
-- 종목코드 예: 005930
SELECT *
FROM pending_buy_signals
WHERE stock_code = '005930'
ORDER BY detected_at DESC
LIMIT 50;

-- 조건식 ID 예: 1
SELECT *
FROM pending_buy_signals
WHERE condition_id = 1
ORDER BY detected_at DESC
LIMIT 50;
```

대량거래 전략 컬럼 확인용
```sql
SELECT id, stock_code, stock_name, detected_at,
       reference_candle_high, reference_candle_date, target_price,
       status, signal_type
FROM pending_buy_signals
ORDER BY detected_at DESC
LIMIT 50;
```

최근 1일 데이터
```sql
SELECT *
FROM pending_buy_signals
WHERE detected_at >= datetime('now', '-1 day')
ORDER BY detected_at DESC;
```

중복 방지 제약 확인(유니크 키)
```sql
.schema pending_buy_signals
-- UniqueConstraint("condition_id", "stock_code", "status", name="uq_pending_unique")
```

---

### 테이블: auto_trade_conditions
전체 조회
```sql
SELECT *
FROM auto_trade_conditions
ORDER BY updated_at DESC;
```

활성화된 조건만
```sql
SELECT *
FROM auto_trade_conditions
WHERE is_enabled = 1
ORDER BY updated_at DESC;
```

조건명으로 검색
```sql
SELECT *
FROM auto_trade_conditions
WHERE condition_name LIKE '%검색어%'
ORDER BY updated_at DESC;
```

유니크 제약 확인
```sql
.schema auto_trade_conditions
-- condition_name UNIQUE (uq_autotrade_condition_name)
```

---

### 테이블: auto_trade_settings
싱글톤 설정 확인
```sql
SELECT *
FROM auto_trade_settings
ORDER BY updated_at DESC
LIMIT 1;
```

개별 컬럼만 확인
```sql
SELECT is_enabled, max_invest_amount, stop_loss_rate, take_profit_rate, updated_at
FROM auto_trade_settings
ORDER BY updated_at DESC
LIMIT 1;
```

유니크 제약(싱글톤)
```sql
.schema auto_trade_settings
-- UniqueConstraint("id", name="uq_autotrade_settings_singleton")
```

---

### 추가 팁
- 날짜/시간 비교는 SQLite의 `datetime` 함수 사용. 예: 최근 7일
```sql
SELECT COUNT(*)
FROM pending_buy_signals
WHERE detected_at >= datetime('now', '-7 day');
```

- 결과를 파일로 저장 (sqlite3 내부에서 실행)
```sql
.mode csv
.once pending_buy_signals.csv
SELECT * FROM pending_buy_signals;
```