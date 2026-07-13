# 세운전략 백테스트 개발기획서

> 작성일: 2026-07-11  
> 대상: stocke 자동매매 시스템  
> 목적: 현재 운용 중인 매매 규칙(세운전략)을 과거 데이터로 재현·검증할 수 있는 백테스트 엔진 구축

---

## 1. 배경 및 목적

### 1.1 배경
- stocke는 **실시간 자동매매**(스캐너 → 신호 → 매수실행기 → 손절모니터)가 동작 중이나, **백테스트 모듈은 없음**.
- 전략 파라미터(손절·익절·트레일링·스크리너 조건 등)를 바꿀 때 **과거 성과를 사전 검증**할 수단이 필요함.
- 코드베이스에 `세운`이라는 이름의 전략 클래스는 없으며, **키움 조건식명** 또는 **대시보드 `AutoTradeSettings` 조합**으로 운용 중인 것으로 추정됨.

### 1.2 목적
| 목표 | 설명 |
|------|------|
| **재현성** | 동일 규칙으로 과거 구간 매매를 시뮬레이션 |
| **비교** | 파라미터·조건식·유니버스 변경 전후 성과 비교 |
| **리스크 가시화** | MDD, 승률, 손익비, 동시 보유, 일별 손실 한도 도달 빈도 |
| **운영 연계** | 백테스트에 쓴 설정을 그대로 실매매 설정으로보내기 |

### 1.3 성공 기준 (MVP)
- 사용자가 **기간·초기자금·AutoTradeSettings 스냅샷**을 지정해 백테스트 실행
- **일봉 기준** 진입/청산 시뮬레이션 및 요약 리포트(수익률, MDD, 거래 수, 승률)
- 대시보드 또는 분석 페이지에서 결과 확인

---

## 2. 현황 분석 (As-Is)

### 2.1 “세운전략”에 해당하는 운영 로직 (코드 기준)

실제 매매는 아래 파이프라인의 **조합**으로 이루어짐:

```
[유니버스]                    [진입]                         [청산]
관심종목 watchlist_codes  →  AutoTradeScanner          →  StopLossManager
거래대금 상위 N종목          buy_condition_checks         stop_loss_rate
키움 조건식 names            entry_gate                   take_profit_rate
PER 필터 (fundamental)       sizing (FIXED/PYRAMIDING)    trailing_stop_pct
                             max_concurrent_positions     profit_lock / ATR
                             daily_loss_limit             장마감 청산
```

| 구성요소 | 파일 | 백테스트 재사용 가능성 |
|----------|------|------------------------|
| 매수 조건 엔진 | `utils/auto_trade_engine.py` | ★★★ 높음 (순수 함수화 가능) |
| 진입 게이트 | `utils/buy_condition_checks.py` | ★★☆ 일봉 근사 필요 (VWAP/시가 등) |
| 스캐너 유니버스 | `managers/auto_trade_scanner.py` | ★★☆ 거래대금 순위는 일봉으로 근사 |
| 손절/익절/트레일링 | `managers/stop_loss_manager.py` | ★★★ 일봉 high/low로 근사 가능 |
| 6종 기술전략 | `managers/strategy_manager.py` | ★☆☆ **5분봉 API 의존** — Phase 2 |
| 키움 조건식 | `managers/condition_monitor.py` | ★☆☆ **과거 조건식 이력 없음** — 별도 처리 |

### 2.2 보유 데이터

| 데이터 | 테이블/소스 | 기간 | 백테스트 활용 |
|--------|-------------|------|----------------|
| 일봉 OHLCV + 지표 | `technical_snapshots` (1D) | 배치 적재분 | **진입·청산 1차 근거** |
| 기본적 지표 | `fundamental_snapshots` | 일별 | PER 필터, 유니버스 |
| 테마/키워드 | `theme_score_daily` 등 | 일별 | 스크리너 확장 (Phase 2+) |
| 실매매 이력 | `positions`, `sell_orders` | 운용 이후 | **워크포워드 검증**용 |
| 5분봉 | Kiwoom API (실시간만) | 없음 | Phase 2에서 적재 필요 |

### 2.3 갭 (Gap)

1. **조건식 백테스트**: 키움 “세운” 조건식의 과거 편입 종목 이력이 DB에 없음 → 당일 스냅샷 export 또는 규칙 재구현 필요.
2. **장중 진입 게이트**: VWAP·당일 위치는 일봉만으로는 부정확 → MVP는 **종가 매매** 또는 **다음날 시가 매매** 모드로 단순화.
3. **체결 가정**: 슬리피지·수수료·세금 모델 없음 → 기획 단계에서 명시적 가정 필요.
4. **전략 SELL 신호**: `StrategyManager`의 SELL은 실제 주문에 연결되지 않음 → 백테스트 청산은 **StopLoss 규칙만** 반영.

---

## 3. 백테스트 범위 정의

### 3.1 Phase 1 — MVP (일봉·세운 운영규칙)

**시뮬레이션 대상 = `AutoTradeSettings` 기반 자동매매 규칙**

| 항목 | 포함 | 비고 |
|------|------|------|
| 유니버스: 관심종목 고정 | ✅ | `watchlist_codes` |
| 유니버스: 거래대금 상위 | ✅ | `technical_snapshots.trading_value` 일별 순위 |
| 매수: 등락률·가격 상한 | ✅ | `min_change_rate_buy`, `buy_below_price` |
| 매수: 동시 보유·일일 매수 한도 | ✅ | |
| 매수: PER 필터 | ✅ | `fundamental_snapshots` |
| 진입 게이트 (VWAP 등) | ⚠️ 옵션 OFF 기본 | 일봉 근사 시 부정확 |
| 청산: 손절/익절 % | ✅ | 일봉 low/high 터치 가정 |
| 청산: 트레일링 (고점 대비) | ✅ | `position_peak_since_buy` 로직 동형 |
| 청산: 장마감 전량 청산 | ✅ | |
| 키움 조건식 편입 | ❌ Phase 1 제외 | 이력 없음 |
| 5분봉 기술전략 6종 | ❌ Phase 2 | |

**체결 모드 (MVP 2종)**

| 모드 | 설명 |
|------|------|
| `close` | 신호 발생일 **종가** 매수/매도 |
| `next_open` | 신호 익일 **시가** 체결 (룩어헤드 방지에 유리) |

**비용 가정 (기본값, 설정 가능)**

| 항목 | 기본값 |
|------|--------|
| 매수 수수료 | 0.015% |
| 매도 수수료 | 0.015% |
| 매도 세금 | 0.20% |
| 슬리피지 | 0.05% (단방향) |

### 3.2 Phase 2 — 정밀화

- 5분봉 마트 적재 (`technical_snapshots` timeframe=`5M`)
- 진입 게이트(VWAP, 당일 위치) 장중 재현
- `StrategyManager` 6종 전략 백테스트 어댑터
- 조건식 이력 테이블 + 일별 스냅샷 배치

### 3.3 Phase 3 — 운영 통합

- 워크포워드 / 파라미터 그리드 서치
- 실매매 vs 백테스트 괴리 리포트
- 백테스트 설정 → `AutoTradeSettings` 원클릭 적용

---

## 4. 목표 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│  UI: /backtest (또는 analysis 탭)                            │
│  - 기간, 초기자금, settings 프리셋, 유니버스 모드              │
└──────────────────────────┬──────────────────────────────────┘
                           │ POST /backtest/run
┌──────────────────────────▼──────────────────────────────────┐
│  BacktestEngine (utils/backtest_engine.py)                   │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────┐ │
│  │ DataFeed    │  │ Strategy     │  │ Portfolio / Broker  │ │
│  │ (mart load) │→ │ (rules plug) │→ │ (positions, cash)   │ │
│  └─────────────┘  └──────────────┘  └─────────────────────┘ │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│  BacktestRun / BacktestTrade (DB 또는 JSON 결과)             │
│  - metrics, equity_curve, trade_log                          │
└─────────────────────────────────────────────────────────────┘
```

### 4.1 핵심 모듈 (신규)

| 모듈 | 경로 | 역할 |
|------|------|------|
| 엔진 | `utils/backtest/engine.py` | 일별 루프, 체결, 포지션 관리 |
| 데이터 피드 | `utils/backtest/data_feed.py` | `technical_snapshots` + `fundamental_snapshots` 로드 |
| 전략 어댑터 | `utils/backtest/strategies/auto_trade.py` | 스캐너+매수조건+손절 규칙 |
| 청산 시뮬 | `utils/backtest/exit_simulator.py` | `stop_loss_manager` 로직 이식 (순수 함수) |
| 메트릭 | `utils/backtest/metrics.py` | 수익률, MDD, 샤프(옵션), 승률 |
| 저장 | `utils/backtest_store.py` | 실행 결과 persist |
| 배치 CLI | `scripts/backtest_run.py` | CLI 실행·CI용 |
| API | `core/main.py` | `POST/GET /backtest/*` |

### 4.2 기존 코드 재사용 원칙

- `auto_trade_engine`, `buy_condition_checks`에서 **DB/API 의존 제거한 순수 함수**를 `utils/backtest/adapters/`로 추출.
- `stop_loss_manager`의 `exit_levels` / 트레일링 판정 로직을 **가격 시계열 입력**으로 동작하게 분리.
- 실매매 코드 경로는 건드리지 않고, **공통 코어**를 양쪽에서 import.

---

## 5. 데이터 모델 (신규)

```python
# backtest_runs
id, name, created_at
start_date, end_date
initial_cash, fill_mode  # close | next_open
settings_json            # AutoTradeSettings 스냅샷
universe_mode            # watchlist | top_value | custom
status, error_message
metrics_json             # summary
equity_curve_json        # [{date, equity}, ...]

# backtest_trades
id, run_id
stock_code, stock_name
side, qty, price, amount
trade_date, reason         # BUY_SIGNAL | STOP_LOSS | TRAILING | ...
pnl, pnl_pct, hold_days
meta_json                  # condition_checks, params at entry
```

MVP에서는 SQLite JSON 컬럼으로 충분. 거래 건수 많아지면 trades 테이블 분리.

---

## 6. API / UI 기획

### 6.1 API

| Method | Path | 설명 |
|--------|------|------|
| POST | `/backtest/run` | 백테스트 실행 (async job) |
| GET | `/backtest/runs` | 실행 이력 목록 |
| GET | `/backtest/runs/{id}` | 요약 + equity curve |
| GET | `/backtest/runs/{id}/trades` | 거래 내역 페이지네이션 |
| POST | `/backtest/presets` | 현재 AutoTradeSettings를 프리셋으로 저장 |

**요청 예시**

```json
{
  "start_date": "2025-01-01",
  "end_date": "2026-06-30",
  "initial_cash": 10000000,
  "fill_mode": "next_open",
  "universe_mode": "watchlist",
  "settings": { "stop_loss_rate": 5.5, "take_profit_rate": 10.0, "trailing_stop_pct": 3.6 }
}
```

### 6.2 UI (analysis 또는 신규 /backtest)

| 영역 | 내용 |
|------|------|
| 설정 패널 | 기간, 초기자금, 체결모드, settings 프리셋 불러오기 |
| 실행 | 진행률, 예상 소요(종목 수 × 일수) |
| 결과 요약 카드 | 총수익률, MDD, 승률, 거래수, 평균보유일 |
| 차트 | equity curve, drawdown |
| 거래 테이블 | 종목, 매수/매도일, 사유, 손익 |
| 비교 | 2개 run 오버레이 (Phase 2) |

---

## 7. 백테스트 알고리즘 (일별 루프)

```
for each trading_day D in [start, end]:
    if holiday: continue

    # 1) 청산 먼저 (보유 포지션)
    for position in portfolio.open:
        bar = feed.get_bar(position.code, D)
        exit_price, reason = exit_simulator.check_exit(
            position, bar, settings, peak_since_buy
        )
        if exit_price:
            portfolio.sell(...)

    # 2) 유니버스 구성
    candidates = universe_resolver(D, settings)

    # 3) 매수 신호
    for code in candidates:
        if portfolio.slots_full: break
        if daily_buy_limit_reached: break
        bar = feed.get_bar(code, D)
        fund = feed.get_fundamental(code, D)
        if buy_rules.pass(code, bar, fund, settings):
            portfolio.buy(...)

    # 3) 일별 기록
    equity_curve.append(portfolio.equity(D))
```

**주의**
- `next_open` 모드: D일 신호 → D+1 시가 체결.
- 동시 보유 종목 peak 추적은 실매매 `position_peak_since_buy`와 동일 규칙.
- 일봉 내 손절/익절 동시 터치 시 **보수적으로 손절 우선** (설정 가능).

---

## 8. 개발 단계 및 일정 (추정)

| Phase | 기간 | 산출물 |
|-------|------|--------|
| **0. 사전정비** | 3~5일 | `auto_trade_engine` / exit 로직 순수 함수 추출, 단위 테스트 |
| **1. 엔진 MVP** | 1~2주 | `backtest/engine.py`, watchlist 유니버스, 일봉 체결, metrics |
| **2. API + CLI** | 3~5일 | `/backtest/run`, `scripts/backtest_run.py` |
| **3. UI v1** | 1주 | analysis 탭에 백테스트 섹션, equity + trades 테이블 |
| **4. 유니버스 확장** | 3~5일 | 거래대금 상위, PER 필터, 프리셋 저장 |
| **5. 검증** | 3~5일 | 실매매 구간 워크포워드, 괴리 문서화 |
| **6. Phase 2** | 2~3주 | 5분봉 마트, 조건식 스냅샷, 진입게이트 |

**총 MVP (Phase 0~5): 약 4~6주** (1인 기준)

---

## 9. 검증 계획

| 검증 | 방법 |
|------|------|
| 단위 | exit_simulator, buy_rules — 알려진 시나리오 가격으로 assert |
| 통합 | 단일 종목·단일 규칙 — 수기 계산과 대조 |
| 회귀 | 실매매 `positions`/`sell_orders` 구간을 백테스트에 넣어 방향성 일치 확인 |
| 룩어헤드 | `next_open` 모드에서 당일 종가 미사용 검증 |
| 성능 | 3,900종목 × 1년 일봉 — 60초 이내 목표 (캐시·벡터화) |

---

## 10. 리스크 및 제약

| 리스크 | 영향 | 대응 |
|--------|------|------|
| 일봉만으로 장중 규칙 재현 한계 | 실매매 괴리 | MVP 범위 명시, Phase 2 5분봉 |
| 조건식(세운) 이력 부재 | 핵심 진입원 미반영 | 일별 조건식 스냅샷 배치 추가 |
| 거래대금 순위 과거 부정확 | 유니버스 왜곡 | `technical_snapshots` 백필 강화 |
| SQLite 성능 | 대규모 그리드 서치 느림 | run 단위 병렬 CLI, 결과만 DB |
| 과최적화 | 실전 손실 | 워크포워드·OOS 구간 필수화 |

---

## 11. “세운전략” 매핑 체크리스트

구현 전 아래를 사용자와 확정 필요:

- [ ] **세운** = 키움 조건식명인가? → DB `auto_trade_conditions` / `screener_condition_names` 확인
- [ ] 진입의 주 소스: 스캐너 vs 조건식 vs 관심종목 고정?
- [ ] 청산: `%손절 + 트레일링`만 쓰는지, ATR·profit_lock 포함 여부
- [ ] 백테스트 **최우선 기간** (예: 최근 1년 / 2024~2025)
- [ ] 체결 가정: 종가 vs 익일 시가
- [ ] 유니버스: 관심종목 only vs 거래대금 top N

---

## 12. 즉시 착수 가능한 작업 (Week 1)

1. `docs/BACKTEST_PLAN.md` 리뷰 및 세운전략 체크리스트 확정
2. `stop_loss_manager`에서 exit 판정 함수 추출 → `utils/exit_rules.py`
3. `buy_condition_checks` 일봉용 어댑터 스켈레톤
4. `backtest/engine.py` POC — watchlist 1~3종목, 3개월, 손절/익절만
5. 결과를 JSON으로 stdout — UI 없이 먼저 숫자 검증

---

## 13. 참고 파일

| 구분 | 경로 |
|------|------|
| 자동매매 설정 | `core/models.py` → `AutoTradeSettings` |
| 스캐너 | `managers/auto_trade_scanner.py` |
| 매수 조건 | `utils/auto_trade_engine.py`, `utils/buy_condition_checks.py` |
| 청산 | `managers/stop_loss_manager.py` |
| 일봉 마트 | `utils/technical_mart_store.py`, `scripts/technical_mart_batch.py` |
| 펀더멘털 | `utils/fundamental_mart_store.py` |
| 신호 흐름 문서 | `docs/SIGNAL_LIFECYCLE_GUIDE.md`, `docs/PROCESS_FLOW.md` |

---

*본 문서는 구현 착수 전 설계안이며, 세운전략 정의 확정 후 Phase 1 범위를 조정할 수 있습니다.*
