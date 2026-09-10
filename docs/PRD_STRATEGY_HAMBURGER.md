# PRD - HAMBURGER (종목별 햄버거거래대금 → 고점 돌파/눌림)

| 항목 | 내용 |
|------|------|
| 문서 ID | PRD-STRATEGY-HAMBURGER |
| 버전 | 0.1.0 |
| 상태 | Draft |
| 작성일 | 2026-09-08 |
| 대상 | `stocke` (`TradingStrategy` + 분봉 스캔 루프) |
| 원본 | [진짜 중요한 애매 포인트! 불개미님 햄버거 기법!](https://www.youtube.com/watch?v=AHSUB-eAxxA) (부자회사원, SPG 3분봉 해설). 보조: [한 달 800% 수익 불개미님 햄버거 기법](https://www.youtube.com/watch?v=9EVBKc25TTI), [불개미 핵심 기법](https://www.youtube.com/watch?v=SRq9Sku_gT0). 불개미 본인 통설은 **1분봉 대금 = 힘** |
| 관련 | [`PRD_STRATEGY_MA38.md`](./PRD_STRATEGY_MA38.md), [`PRD_STRATEGY_SECTOR_RS.md`](./PRD_STRATEGY_SECTOR_RS.md), [`PRD_STRATEGY_ICHIMOKU_KIJUN.md`](./PRD_STRATEGY_ICHIMOKU_KIJUN.md), [`THEME_STOCK_PIPELINE.md`](./THEME_STOCK_PIPELINE.md), [`PRD_IMPROVEMENT_2026.md`](./PRD_IMPROVEMENT_2026.md), [`BACKTEST_PLAN.md`](./BACKTEST_PLAN.md), [`SIGNAL_LIFECYCLE_GUIDE.md`](./SIGNAL_LIFECYCLE_GUIDE.md) |

분봉 고정. **종목마다 "이만한 대금이 터지면 움직이기 시작한다"는 햄버거 문턱을 먼저 잰다.** 오늘 그 문턱을 넘는 캔들(패티)이 나오면 셋업이다. **패티 봉의 확정 상태만 신뢰한다.** 그 고점을 돌파하거나, 눌린 뒤 고점/단기선을 받아 주면 산다. 당일 청산.

기존 차트 6종·MA38(일봉 급등 눌림)·`ScalpingStrategyManager`(거래량 배수 스캘핑)와 해석이 다르므로 **별도 타입**이다.

> **주의(리뷰):** 아래 §15는 이번 캡처본을 코드베이스와 대조한 후 추가한 **미확정 항목/갭 체크리스트**다. 개발 착수 전 결정값 확정이 필요하다.

---

## 0. 한 줄 로직 (기존 매수·스캘핑과 어디가 다른가)

기존 자동매수는 **오늘 리스트 → 오늘 산다**.
MA38은 **오늘 급등한 수첩에 적고, 산 것은 며칠 뒤 3/8 눌림**.
기존 `VOLUME_SCALP`는 **평균 거래량의 N배**만 산다. 종목별 문턱도, 패티 기준 손익도 없다.
본 전략은 **종목별 햄버거거래대금을 재고, 그 패티가 나온 뒤에만 돌파·눌림으로 산다.**

```text
기존 AutoTradeScanner (2분)
 관심 + 거래대금 + 조건식 —오늘 게이트— PendingBuy —▶ 지금 현재가 매수

기존 StrategyManager (1분)
 WatchlistStock 전체 —5분봉 지표— PendingBuy —▶ 지금 현재가 매수

기존 VOLUME_SCALP
 3분 거래량 ≥ 평균×N —▶ 바로 BUY
```

```text
HAMBURGER
 L0 종목별 문턱 캘리브레이션 (과거 장대양봉일의 분봉 대금)   ← 주문 없음
 L1 관심·대금상위·당일 테마 줍기                              ← 주문 없음
 L2 그중 오늘 패티(문턱 이상 분봉)만 수첩                      ← 주문 없음
 L3 수첩에 펴서 패티 고점 돌파 또는 눌림 반등                  ← 여기만 BUY
 보유 중 패티 저점 이탈 / 2차 패티 고점 / 장 마감              ← SELL
```

| 묻는 질문 | 언제 | 사나 / 파나 |
|---|---|---|
| L0 | 이 종목의 햄버거 문턱은 얼마인가 | 장전·장후 1회 | 아니오 |
| L1 | 오늘 볼 종목인가 (대장·테마) | 장중 | 아니오 |
| L2 | 오늘 패티가 터졌나 | **분봉 확정** | 아니오 |
| L3 | 패티 고점을 뚫었나 / 받아 줬나 | 분봉 1회 | 그 봉만 산다 |
| 보유 | 패티 구조가 깨지나 / 2차 패티인가 | 분봉 1회 | 깨지면 판다 |
| 기존 스캘핑 | 거래량이 평균보다 큰가 | 분봉마다 | 크면 산다 |

영상에서 "고요하게 흐르다가 20~50억이 터지면 상승이 시작된다"고 한 그 금액이 L0 문턱이다.
당일 100억이 연속으로 나온 것은 L2 패티가 **문턱을 한참 넘긴 강한 날**이지, 문턱을 100억으로 올리는 것이 아니다.

## 1. 파이프라인

```text
L0 hamburger_threshold (종목별 문턱, TTL=threshold_ttl_days)
       │
       ▼
L1 발견 (watchlist ∪ 거래대금상위 ∪ 당일 테마 3종↑)
       │
       ▼
L2 hamburger_universe (오늘 패티 확정만, TTL=당일)
       │
       ▼
L3 1분/3분 스캔 (장부 ∪ WAIT_BREAK / WAIT_PULL ∪ 보유)
       │  ≠ 기존 5분봉 StrategyManager 루프, ≠ ScalpingStrategyManager
       ▼
      BUY

StrategySignal + PendingBuySignal (signal_type=strategy)
       │
       ▼
BuyOrderExecutor → Position
  (suggested_stop = patty_low - buffer, take_profit = 2nd patty / %)
       │
       ▼
StopLossManager
   STOP_PATTY / STOP_PCT / TP_SECOND / TP_STALL / EOD / MAX_HOLD_MIN
```

기존 차트 6종·MA38과 분리: `strategy_type=HAMBURGER`(시드/DB 표시값), `strategy_key=hamburger`(코드 분기·`Position.strategy_key` 값, 소문자 확정), 시드 `is_enabled=false`.

신호 계약 (기존과 동일, 2차 분할은 수량 비율):

```python
{
  "signal_type": "BUY" | "SELL",
  "signal_value": float,
  "additional_data": {
    "reason": "BREAK_PATTY" | "BOUNCE_PATTY",
    "level": "L1" | "L2",
    "qty_frac": 1.0,
    ...
  }
}
```

기존 실행기가 전량만 받으면 70% 익절은 P1에서 확장한다. P0는 전량 청산으로 동일 규칙을 단순화한다.

---

## 2. 범위

**MVP**

| ID | 내용 |
|----|------|
| IN-01 | 시드 + 분봉 분기 + UI 폼. 기본 OFF |
| IN-02 | 분봉 거래대금. `utils/hamburger.py`. 기본 TF=`3M`(영상), `1M` 파라미터 예약 |
| IN-03 | L0 종목별 문턱 캘리브레이션 (`hist_impulse` 또는 `relative`) |
| IN-04 | L2 패티 확정 (`PATTY`) + L3 돌파 BUY (`BREAK_PATTY`) |
| IN-05 | 보유 중 패티 저점 이탈 SELL (`STOP_PATTY`) |
| IN-06 | 당일 강제 청산 (`EOD`). 오버나잇 없음 |
| IN-07 | 종목당 신호 **같은 분봉 1회**, 같은 종목 당일 동시 1포지션 |
| IN-08 | 단위 테스트: 문턱/패티/미돌파/돌파/저점손절/EOD/무포지션무매도 |

**제외 (MVP)**

- 숏, 미수·신용, 29% 대장 재량 진입
- 뉴스/LLM, 호가창 "돼지물량"
- NXT 시간외 실주문 경로 연결 (문턱·테마 관측만 P1)
- 전종목 1분봉 API 스캔
- `ScalpingStrategyManager` 재사용·개조

### 2.1 Phase 정의 (P0~P3)

이 문서에서 P0~P3는 다음을 의미한다. 순서대로 진행하며, 이전 Phase의 §12 수락 기준을 통과해야 다음 Phase로 넘어간다.

| Phase | 정의 | 목표 | 실거래 여부 |
|-------|------|------|------------|
| **P0** | MVP. 문턱·패티·돌파·손절(`STOP_PATTY`/`STOP_PCT`)·EOD만 구현. 전용 DB 테이블 없이 인메모리/세션 상태로 L0~L2 관리 | §12 수락 기준 통과. 시드는 `is_enabled=false` 유지 | Paper/mock만. 실거래 금지 |
| **P1** | 구조 강화. 전용 테이블(`hamburger_threshold`, `hamburger_universe`) 도입, 테마 게이트(`min_theme_peers`), 2차 패티 분할 익절(`tp_second_frac`), 트레일 무장 금지 실장 | 소액 실거래로 규칙 재현성 검증 | 소액 실거래 가능 (수동 ON) |
| **P2** | 확장·관측. 1분봉 모드, NXT 시간외 관측, 눌림(`bounce`) 2차 진입, 차트 마커 UI, 시총 구간별 문턱 배율 | 페이퍼/실거래 데이터로 확장 기능의 실익 검증 | 기존 실거래 규모 유지, 신규 옵션은 기본 OFF |
| **P3** | 이 PRD 범위 밖. 숏, 미수·신용, 대장주 재량 진입, 뉴스/LLM 결합 등 — 별도 PRD로 분리해 재검토 | (계획 없음) | 해당 없음 |

P0/P1 경계는 "전용 DB 테이블 유무", P1/P2 경계는 "MVP 규칙 대비 실험적 확장 여부"로 가른다.

**결정값**

| 항목 | 값 |
|------|------|
| 실행 TF | 기본 `3M`. 불개미 원본은 `1M`. 5분 루프와 혼용 금지 |
| 대금 | `typical_price * volume`. 거래량 막대만 쓰지 않음 |
| 패티 | 확정봉 `value >= threshold` (+ 양봉 옵션) |
| 진입 | **패티 고점 돌파**. 눌림도 P0 옵션 OFF |
| BT 체결 | 확정 분봉 종가 |
| 실거래 체결 | `entry_fill=next_open` (다음 분봉 시가) |
| 유니버스 | P0=관심+대금상위. P1=`hamburger_universe` 당일 장부 |
| 청산 | 당일. `EOD` 기본 15:19 전량 |

---

## 3. 유니버스

관심종목 전체를 1분마다 사는 것이 아니다. 본체는 **오늘 패티 장부**.
1분/3분 API는 비싸다. L3에 넣는 종목만 분봉을 받는다.

```text
L0 문턱: hamburger_condition_names(TBD) 조건식 편입 종목. 장전 1회 배치. expire = calc_date + threshold_ttl_days
       ※ 조건식 이름/조건 자체는 원본 영상 재검토 후 확정 (D13). MA1592의 ma1592_condition_names +
         fetch_condition_target_items 패턴 재사용 — 별도 인프라 신규 개발 아님
L0 제외: ETF·우선주·관리/위험·저가 — 기존 _is_screener_stock / _is_screener_per_eligible
L1 발견: watchlist_codes ∪ get_volume_rank(sort_tp=3) ∪ (P1) 당일 테마 3종 이상 동반 상승
L1 필터: 시가 갭 > gap_skip_pct 이면 skip. open_skip_minutes 이전 매수 금지
L2 장부: L1 ∩ 오늘 PATTY 만 insert, expire = 당일 장 종료
L3 스캔: 장부(미만료) ∪ WAIT_* / MANAGE_* ∪ 해당 전략 포지션
L4 진입: §5 게이트 통과 시 시간 내 BUY
```

P0는 L2 테이블 없이 **관심+대금상위 + 당일 분봉 게이트**로 동일 규칙 적용된다.
워치리스트는 L1 입구일 뿐, 패티가 없는 종목은 BUY가 없다.

예상 규모: 동시 분봉 추적 8~20. **40개 초과 시 L1 필터가 느슨하거나 문턱이 낮게 잡힌 것.**
키움 1분 20회 한도 때문에 L3를 전종목으로 열면 전략이 죽는다.

### 3.0 날짜 예 (영상 SPG)

```text
사전 L0  과거 장대양봉일(예: 2025-12-19, +20%)의 3분봉을 본다.
     고요 → 20~50억에서 상승 시작. threshold = 20억 (하한).
     100억은 "강한 날"이지 문턱이 아니다.

당일 대체거래·정규 초반.
     3분 1·2봉에 100억 연속.  문턱 20억의 5배.  L2 PATTY ×2 (STRENGTH)
     같은 시간 로봇 섹터 다수 상승 (TXR, CMS, 엔젤 등).  L1 테마 OK
     아직 고점 돌파 전 → BUY 없음

이후 패티 고점 돌파 확정봉                    BUY BREAK_PATTY → 다음 3분 시가
     또는 3분 5선/패티 눌림                   BUY BOUNCE_PATTY (옵션)

보유 패티 저점 종가 이탈                       SELL STOP_PATTY
     두 번째 패티 고점                          SELL TP_SECOND (P1: 70%)
     15:19                                     SELL EOD
```

패티만 터지고 고점을 못 돌파하면 사지 않는다. 그게 L2와 L3를 나눈 이유다.

### 3.1 `hamburger_threshold` (L0)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `stock_code` | str | PK |
| `bar_tf` | str | `1M` \| `3M` |
| `threshold` | float | 원. 햄버거거래대금 |
| `method` | str | `hist_impulse` \| `relative` |
| `impulse_date` | date \| null | 캘리브에 쓴 장대양봉일 |
| `impulse_bar_value` | float \| null | 그 날 시동 패티 대금 |
| `calc_date` | date | |
| `expire_date` | date | |

유니크: `(stock_code, bar_tf)`.

### 3.2 `hamburger_universe` (L2, P1)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `strategy_id` | FK | |
| `stock_code` | str | PK 일부 |
| `trade_date` | date | 당일 |
| `patty_time` | datetime | 첫 패티 확정 시각 |
| `patty_high` | float | 기준 캔들 고가 |
| `patty_low` | float | 기준 캔들 저가 |
| `patty_value` | float | 그 봉 대금 |
| `patty_mult` | float | `patty_value / threshold` |
| `patty_count` | int | 당일 연속·누적 패티 수 |
| `state` | str | `WAIT_BREAK` \| `WAIT_PULL` \| `MANAGE` \| `DONE` |
| `expire_date` | datetime | 당일 장마감 |

유니크: `(strategy_id, stock_code, trade_date)`.

### 3.3 구멍 점검

| # | 구멍 | 막기 |
|---|------|------|
| H1 | 전종목 1분봉 스캔 → API 한도 | L3 = L2 ∪ 포지션만. L1은 일·틱 랭킹 |
| H2 | 미확정 분봉으로 패티/돌파 | **확정봉만**. 현재 진행봉 금지 |
| H3 | 패티 봉에서 바로 시장가 | L2는 주문 없음. L3 돌파·눌림만 BUY |
| H4 | 거래량 배수만 보고 삼전·대형 노이즈 | 대금(`price*vol`) + 종목별 문턱 |
| H5 | 문턱을 당일 최대 대금으로 갱신 | 문턱은 L0. 당일 100억은 `patty_mult`일 뿐 |
| H6 | 5분 루프에 1/3분 규칙을 얹음 | 전용 분봉 루프. `StrategyManager` 5분과 분리 |
| H7 | 기존 스캘핑·세운전략과 같은 종목 이중매수 | HOLDING/PENDING이면 skip |
| H8 | 장후 신호 → 당일 현재가 | `entry_fill=next_open` (다음 분봉) |
| H9 | 전역 트레일 %가 패티 손절보다 먼저 잘림 | 본 전략 포지션은 트레일 무장 금지(P1). P0는 전역 익절% 넉넉히 |
| H10 | 갭 +6% 시초 추격 | `gap_skip_pct` |
| H11 | 후발주·테마 없는 단독 패티 | P0는 필터 OFF 가능. P1 `min_theme_peers=3` |
| H12 | 오버나잇 | `EOD` 필수. `allow_overnight=false` |

---

## 4. 프로필

**시드:** `strategy_name=햄버거 대금`, `strategy_type=HAMBURGER`, `strategy_key=hamburger`, `is_enabled=false`

```json
{
  "bar_tf": "3M",
  "threshold_mode": "hist_impulse",
  "impulse_lookback_days": 60,
  "impulse_pct": 15.0,
  "impulse_start_quantile": 0.7,
  "relative_lookback": 20,
  "relative_mult": 4.0,
  "min_patty_value": 2000000000,
  "max_patty_value": 0,
  "threshold_ttl_days": 20,
  "require_bullish_patty": true,
  "patty_confirm": "close_above_open",
  "open_skip_minutes": 10,
  "gap_skip_pct": 6.0,
  "min_theme_peers": 0,
  "require_daily_ma20": true,
  "require_daily_ma240": false,
  "require_leader": false,
  "enable_break_entry": true,
  "enable_pull_entry": false,
  "pull_ma": 5,
  "touch_buf_pct": 0.15,
  "require_bullish_entry": true,
  "l2_qty_frac": 0.0,
  "stop_buffer_pct": 0.15,
  "stop_pct": 2.0,
  "enable_second_patty_tp": true,
  "tp_second_frac": 1.0,
  "tp_stall_bars": 4,
  "max_hold_minutes": 90,
  "eod_hhmm": "1519",
  "allow_overnight": false,
  "entry_fill": "next_open",
  "max_positions": 1
}
```

```text
typical = (high + low + open + close) / 4
value   = typical * volume                    # 봉 거래대금(원)

# L0 hist_impulse
impulse_days = 일봉 change_pct >= impulse_pct  (lookback 내, 최근 N일)
시동봉      = 그 날 분봉 중 value가 처음으로
              (당일 분봉 value 분포의 impulse_start_quantile) 이상인 양봉
threshold   = max(min_patty_value, median(시동봉 value들))
              # 영상 SPG: 시동 20~50억 → 하한 20억

# L0 relative (캘리 실패·신규 종목)
threshold   = max(min_patty_value, median(value[-relative_lookback:]) * relative_mult)

# L2 패티 (확정)
is_patty    = value >= threshold
              and (not require_bullish_patty or close > open)
patty_mult  = value / threshold                # 1.0=문턱, 5.0=영상 당일 100억/20억

# L3 돌파
break       = high >= patty_high * (1 + touch_buf_pct/100)
              and close > patty_high
              and (not require_bullish_entry or close > open)

# L3 눌림 (옵션)
bounce      = low <= max(patty_high, ma(pull_ma)) * (1 + touch_buf_pct/100)
              and close > max(patty_high, ma(pull_ma))
              and close > open
```

| 키 | 기본 | UI | 의미 |
|----|------|----|----|
| `bar_tf` | 3M | Y | 실행 분봉. 1M은 API 부하↑ |
| `threshold_mode` | hist_impulse | Y | 과거 급등일 시동대금 / 상대배수 |
| `impulse_pct` | 15 | Y | 캘리브에 쓸 일봉 급등 하한 |
| `min_patty_value` | 20억 | Y | 문턱 최소 하한 (영상 SPG) |
| `relative_mult` | 4 | Y | 최근 봉 중앙값 대비 |
| `require_bullish_patty` | true | Y | 음봉 패티 제외 |
| `open_skip_minutes` | 10 | Y | 시초 참여. 이 시간 전 BUY 없음 |
| `gap_skip_pct` | 6 | Y | 시가 갭 과대 제외 (불개미 원칙) |
| `min_theme_peers` | 0 | Y | P0=0. P1에서 3 권장 |
| `require_daily_ma20` | true | Y | 종가 > MA20 |
| `enable_break_entry` | true | Y | 패티 고점 돌파 |
| `enable_pull_entry` | false | Y | 패티 고점·분봉5선 눌림 |
| `stop_pct` | 2.0 | Y | 전역 % 손절 백업 |
| `max_hold_minutes` | 90 | Y | 분 만기 |
| `eod_hhmm` | 1519 | Y | 당일 강제 청산 |
| `max_positions` | 1 | Y | 한 종목 집중 (영상·통설) |

`max_patty_value=0`은 상한 없음. 대형주 노이즈가 크면 P1에서 켠다.

## 5. 상태머신 · 칼날 게이트

```text
IDLE → WAIT_BREAK → (옵션 WAIT_PULL) → MANAGE → DONE
```

스킵은 BUY를 내지 않고 reason만 남긴다. 장 마감 시 전부 DONE.

| 게이트 | 조건 (실패 → reason) | 전이 |
|--------|----------------------|------|
| G0 유니버스 | L0 문턱 없음 / L1 아님 | 스캔 안 함 |
| G1 일봉 자리 | `close > MA20` (옵션 MA240) | `DAILY_WEAK` |
| G2 갭 | 시가 갭 ≤ `gap_skip_pct` | `GAP_TOO_WIDE` |
| G3 시초 | 장 시작 후 `open_skip_minutes` 경과 | `TOO_EARLY` |
| G4 테마 | `min_theme_peers=0`이거나 동테마 상승 ≥ N | `NO_THEME` |
| G5 패티 확정 | `is_patty` | `NO_PATTY` |
| G6 돌파 이후 | `break` | `NO_BREAK` |
| G7 눌림 (옵션) | `bounce` | `NO_BOUNCE` |
| G8 중복 | HOLDING/PENDING 또는 당일 동일 신호 | `ALREADY_IN_POSITION` / `DUP_BAR` |
| G9 집중 | 이미 다른 종목 MANAGE 이고 `max_positions=1` | `SLOT_FULL` |

**청산 (MANAGE, 보유 중만)**

| 우선 | 조건 | reason |
|------|------|--------|
| 1 | `close < patty_low * (1 - stop_buffer_pct/100)` | `STOP_PATTY` |
| 2 | `close <= entry * (1 - stop_pct/100)` | `STOP_PCT` |
| 3 | (옵션) 두 번째 패티 확정 후 그 고가 터치 | `TP_SECOND` |
| 4 | 고점 갱신 없이 `tp_stall_bars` 봉 | `TP_STALL` |
| 5 | 보유 분 ≥ `max_hold_minutes` | `MAX_HOLD` |
| 6 | 시각 ≥ `eod_hhmm` | `EOD` |

같은 봉에 패티 이탈과 2차 패티가 같이 나면 **손절(STOP_PATTY) 우선**이다.
"돈이 흐르던 구간이 끝났다"가 익절 재량보다 앞선다.

---

## 6. 진입 · 청산 · 사이징

**진입**

- `WAIT_BREAK` G1-G6 통과 → BUY `reason=BREAK_PATTY`, `level=L1`
- `enable_pull_entry`이고 돌파 실패 후 눌림만 통과 → BUY `reason=BOUNCE_PATTY`, `level=L2`
- 동일 종목 당일 1포지션. 불타기·물타기 없음. P0 `l2_qty_frac=0`
- 체결가 `entry` = 실거래 `next_open` (다음 분봉 시가)

**익절 정의 (D1 확정 = 구조 익절, 고점 % 아님)**

기존 `AutoTradeSettings.take_profit_rate` 는 트레일 시작 %이다.
본 전략의 본전 익절은 **두 번째 패티가 만든 고점**이다. P0는 전량, P1은 `tp_second_frac`(통설 0.7).

```text
second_patty = 첫 패티 이후 value >= threshold 인 다음 확정 양봉
TP_SECOND    = bar.high >= second_patty_high     # P0 전략
```

가격 % 목표는 백업이 아니다. 정체(`TP_STALL`)와 만기·EOD가 백업이다.

**손절**

```text
suggested_stop = round(patty_low * (1 - stop_buffer_pct/100))
stop_pct_price = entry * (1 - stop_pct/100)
# 실거래 백업은 둘 중 가까운 쪽을 StopLoss에 스냅샷 (P1)
```

패티 저점이 곧 "돈이 들어왔던 바닥"이다. 종가로 깨지면 그 힘은 끝난 것이다.

**사이징**

기존 전략 매수와 동일: `max_invest_amount` / 현재가.
`max_positions=1` 이므로 한 슬롯에 몰아 넣는다. MVP는 MA38형 risk_mult 없음.

---

## 7. `additional_data` (BUY)

```json
{
  "reason": "BREAK_PATTY",
  "level": "L1",
  "bar_tf": "3M",
  "threshold": 0,
  "patty_value": 0,
  "patty_mult": 0,
  "patty_high": 0,
  "patty_low": 0,
  "patty_time": "",
  "patty_count": 1,
  "suggested_stop": 0,
  "entry_fill": "next_open",
  "theme_peers": 0
}
```

SELL: `reason` = `STOP_PATTY` \| `STOP_PCT` \| `TP_SECOND` \| `TP_STALL` \| `MAX_HOLD` \| `EOD`

---

## 8. 이벤트 / reason_code

`DAILY_WEAK`, `GAP_TOO_WIDE`, `TOO_EARLY`, `NO_THEME`, `NO_PATTY`, `NO_BREAK`, `NO_BOUNCE`, `ALREADY_IN_POSITION`, `DUP_BAR`, `SLOT_FULL`, `BREAK_PATTY`,
`BOUNCE_PATTY`, `STOP_PATTY`, `STOP_PCT`, `TP_SECOND`, `TP_STALL`, `MAX_HOLD`, `EOD`

---

## 9. UI (최소)

- 타입 `HAMBURGER`, 표시명 `햄버거 대금`
- 폼: TF(3M/1M), 문턱모드, 급등%, 최소대금, 상대배수, 양봉패티, 시초대기분, 갭%, 일봉MA20, 돌파진입, 눌림진입, 손절%, 만기(분), EOD
- 기존 세운전략·스캘핑과 동시 ON 경고 (같은 종목 이중매수)
- 익절 표시: `2차 패티 고점` — % 목표가 아님
- 차트: 문턱 금액 수평(대금축) + 패티 화살표. P2
- MVP는 신호 테이블 + 분봉 차트 버튼

---

## 10. 비기능 · 백테스트

| ID | 기준 |
|----|------|
| NFR-1 | 동일 분봉+params → 동일 시그널 |
| NFR-2 | L3 종목당 분봉 캐시 히트 시 ≤ 1s |
| NFR-3 | skip/exit reason 구조화 |
| NFR-4 | 시드 OFF, Paper/mock 기본 |
| NFR-5 | §12 테스트 |
| NFR-6 | 기존 5분 전략·`ScalpingStrategyManager`를 덮어쓰지 않음 |
| NFR-7 | L3 동시 추적 기본 ≤ 20. 초과 시 L1 trim 로그 |

백테스트: [`BACKTEST_PLAN.md`](./BACKTEST_PLAN.md)에 **분봉 Phase**가 필요하다. 일봉 1만으로는 재현 불가.
합격은 규칙 재현이며 수익 수치 아님. 영상 SPG 날짜는 회귀 픽스처로 쓴다.

---

## 11. 구현

| Phase | 내용 | 파일 |
|-------|------|------|
| P0 | 계산기 + 테스트 | `utils/hamburger.py`, `tests/test_hamburger.py` |
| P0 | 시드 OFF | `core/models.py` 누락 전략 INSERT |
| P0 | 3분 스캔 + 확정봉 1회 (MA1592 패턴 재사용) | `managers/auto_trade_scanner.py` (`_collect_hamburger_targets` 신규, `_collect_ma1592_targets` 참고) |
| P0 | UI | `static/modules/strategy-manager.js` |
| P1 | `hamburger_threshold` / `hamburger_universe` 장부 | `core/models.py` |
| P1 | 테마 3종 게이트 (`min_theme_peers`) | `THEME_STOCK_PIPELINE.md` 읽기 전용 |
| P1 | `STOP_PATTY` / `TP_SECOND` / `EOD` 구현 | `StopLossManager` + ST-01 |
| P1 | BUY 시 `stop_loss_price=suggested_stop`, 트레일 무장 금지 | `BuyOrderExecutor` |
| P2 | 1M 모드, NXT 관측, 눌림 2차, 차트 마커 | |

```text
on_bar_close(bar, hist, daily, has_position, p, book):
  value = typical(bar) * bar.volume
  th = book.threshold or calibrate(hist, daily, p)

  if has_position:
    if close < book.patty_low * (1 - p.stop_buffer_pct/100): emit SELL STOP_PATTY
    elif close <= entry * (1 - p.stop_pct/100): emit SELL STOP_PCT
    elif p.enable_second_patty_tp and second_patty_high_touch: emit SELL TP_SECOND
    elif stall(p.tp_stall_bars): emit SELL TP_STALL
    elif now >= p.eod_hhmm: emit SELL EOD
    else: hold
    return

  if p.require_daily_ma20 and daily.close <= daily.ma20: skip DAILY_WEAK
  if gap(daily) > p.gap_skip_pct: skip GAP_TOO_WIDE
  if minutes_since_open < p.open_skip_minutes: skip TOO_EARLY
  if not book.has_patty:
    if is_patty(value, th, bar, p): book.set_patty(bar); state=WAIT_BREAK
    else: skip NO_PATTY
    return
  if p.enable_break_entry and break_patty(bar, book, p):
    emit BUY BREAK_PATTY
  elif p.enable_pull_entry and bounce_patty(bar, hist, book, p):
    emit BUY BOUNCE_PATTY
  else: skip NO_BREAK
```

기존 `managers/scalping_strategy.py` 의 `VOLUME_SCALP`(평균 거래량 배수)는 **이 파일에 조건을 덧붙이지 않는다.**

---

## 12. 수락 기준

- [ ] 3분봉 대금 = typical × volume 로그
- [ ] `HAMBURGER` 프로필 + UI 저장, 기본 OFF
- [ ] 기존 5분 전략·`VOLUME_SCALP` 로직 불변
- [ ] 과거 급등일 시동대금으로 문턱이 잡힘 (SPG형: 20억 하한)
- [ ] 문턱 미만 분봉은 패티 아님 → BUY 없음
- [ ] 패티 이후 고점 돌파 양봉 → BUY 1회, 다음 분봉 시가
- [ ] 미보유 종목은 패티 저점 이탈이어도 SELL 없음
- [ ] 보유 중 종가 < 패티 저점 → `STOP_PATTY`
- [ ] `eod_hhmm` 이후 전량 → `EOD`
- [ ] 같은 데이터 재실행 시 시그널 동일
- [ ] 같은 종목 같은 봉 동일 신호 재생성 없음
- [ ] L3 추적 종목이 한도를 넘기면 trim (전종목 분봉 호출 없음)
- [ ] mock/Paper에서 실주문 어댑터 미호출(기존과 동일 경로)

---

## 13. 의사결정

| ID | 상태 | 결정 |
|----|------|------|
| D1 | **확정** | 익절 = 2차 패티 고점(`TP_SECOND`) + 정체/만기/EOD. 고점 % 목표가 아님 |
| D2 | **확정** | 진입 = 패티 **이후** 고점 돌파. 패티 봉 자체는 셋업 |
| D3 | **확정** | 문턱 = 종목별 과거 급등일 시동 대금. 당일 최대 대금으로 문턱을 올리지 않음 |
| D4 | **확정** | TF = 분봉. 기본 3M(영상), 1M은 파라미터. 일봉 전략과 분리 |
| D5 | **확정** | 당일 청산. 오버나잇 없음 |
| D6 | 기본 | 눌림 진입 OFF. 돌파만 |
| D7 | 기본 | 테마 3종 게이트 OFF (P1에서 켬) |
| D8 | 기본 | 시드 OFF |
| D9 | 기본 | P0 관심+대금상위 스캔, P1 당일 장부 |
| D10 | 기본 | 한 종목 (`max_positions=1`) |
| D11 | **확정** | `strategy_key="hamburger"` (소문자, `sangtta`/`ma1592`류와 동일 컨벤션). DB 표시값 `strategy_type=HAMBURGER`는 그대로 유지 |
| D12 | **보류** | `min_patty_value` 단일값 유지. 시총 구간별 배율 도입은 실거래 데이터 축적 후 P2에서 재판단 (G-5) |
| D13 | 방향 확정, 세부 미정 | L0 대상 = HTS 조건식(`hamburger_condition_names`) 편입 종목 → 장전 배치. 조건식 이름 자체는 영상 재검토 후 확정 (G-4) |

### D1이 묻는 것

몇 %에 팔지가 아니라 **언제 돈이 들어온 구간이 끝났다고 볼지**.

첫 패티는 수급이 켜진 자리다. 두 번째 패티는 그 힘을 한 번 더 밀어 올린 자리다.
그 고점에서 나눠 나오거나(P1), P0는 전량 나온다.
패티 저점이 종가로 깨지면 그 힘은 끝난 것이다. 익절이 아니라 손절.

### D2가 묻는 것

대금이 터진 그 봉을 살지, 그 봉이 **가격으로 확인된 뒤**에 살지.

- **패티 봉 매수**: 빠르지만 그 봉이 고점이 되면 바로 물린다. 영상이 경고하는 자리.
- **고점 돌파(기본)**: 한 박자 늦다. 패티가 바닥이 아니라 천장인 날을 걸러 낸다.

"확인하고 비싸게 사서 더 비싸게 판다"는 불개미 단타 문장과 같다.

### D3이 묻는 것

모든 종목에 40억을 쓸지, 종목이 움직이기 시작했던 금액을 쓸지.

SPG는 3분 20억이면 시동이 걸렸다. 레인보우 로보틱스는 분봉 480억이 기준이었던 날이 있다.
시총·유동성이 다르므로 **절대 한 값**은 대형주 노이즈이거나 중소형 미탐지다.
당일 100억이 나왔다는 것은 `patty_mult=5`이지, 내일 문턱이 100억이 되는 것이 아니다.

### D4가 묻는 것

1분봉(불개미)과 3분봉(해설 영상) 중 무엇을 기본값으로 둘지.

- **3M 기본**: 영상 재현, API 부하↓, 노이즈↓
- **1M**: 더 빠르고, 키움 한도에 더 잘 걸린다

같은 문턱을 1분에 쓰면 금액이 작아진다. TF를 바꾸면 L0를 다시 잰다.

### 유튜브와의 대응

검색·트랜스크립트에서 **반복되는 규칙만** 코드로 고정했다.
특정 채널 한 편의 재량(호가, 재료 해석, 29% 추격, 미수 풀베팅)은 넣지 않았다.

| 통설 / 영상 | 본 전략 |
|------------|--------|
| 고요하다가 특정 대금이 터지면 상승 시작 | L0 문턱 + L2 패티 |
| 종목마다 그 금액이 다르다 (SPG 20억, 로보 480억) | `hist_impulse` |
| 당일 문턱의 수배가 연속이면 강한 날 | `patty_mult`, `patty_count` |
| 주도 섹터에서 햄버거 한다. 아니면 안 한다 | G4 `min_theme_peers` (P1) |
| 일봉 자리가 우선, 분봉만 예쁜 종목은 제외 | G1 |
| 시초는 탐색, 갭 과대는 제외 | G2·G3 |
| 고점 돌파 + 눌림 분할 | D2 돌파 기본, 눌림 OFF |
| 손절은 복. 패티 저점 | `STOP_PATTY` |
| 한 종목만 본다 | `max_positions=1` |
| 보조지표 거의 안 봄. 캔들·대금 | MA는 일봉 필터·눌림 옵션만 |

---

## 14. 변경 이력

| 버전 | 일자 | 내용 |
|------|------|------|
| 0.1.0 | 2026-09-08 | 초안. 영상 AHSUB-eAxxA + 불개미 통설. 종목별 문턱, 패티 후 돌파, 당일 청산. 기존 스캘핑과 분리 |

---

## 15. 개발 착수 전 점검 (Gap Check)

코드베이스(`core/models.py`, `managers/buy_order_executor.py`, `managers/stop_loss_manager.py`, 기존 전략 프로필 패턴)와 대조한 결과, 착수 전 확정이 필요한 항목:

| # | 미진한 부분 | 왜 문제인가 | 제안 |
|---|------------|------------|------|
| G-1 | ~~`strategy_key` 값 미정~~ **확정 (D11)** | 기존 전략은 `legacy/sangtta/breakout/fractal/jongga/ymgp/ma1592` 같은 소문자 문자열로 구분되고 `BuyOrderExecutor`/`StopLossManager`가 이 값으로 분기한다 | `strategy_key="hamburger"`. DB `strategy_type=HAMBURGER`(표시/시드용)와 코드 분기용 `strategy_key`(소문자)를 이중 관리 — §1·§4 반영 완료 |
| G-2 | ~~분봉(1M/3M) 실시간 수집 인프라 부재~~ **확인 완료 — 기존 인프라 재사용** | 재확인 결과 `utils/ma1592.py`가 실제로 `exec_tf="3M"` **3분봉**을 쓰고(`DEFAULT_PARAMS`), `managers/auto_trade_scanner.py`의 `_collect_ma1592_targets`가 조건식 편입(L1) → `maintain_ma1592_universe` 장부(L2) → 확정봉 스캔(L3)을 이미 스캐너 주기(기본 60초, `scan_interval_sec`)로 구현 중이다. (`managers/strategy_manager.py`는 별개 1분 루프이며 `get_stock_chart_data(period="5M")`를 하드코딩 — MA1592/햄버거와 무관) | 신규 루프를 만들지 말고 `AutoTradeScanner`에 `_collect_hamburger_targets`를 MA1592 패턴 그대로 추가. `fetch_condition_target_items`/`get_universe_store`/`maintain_*_universe` 재사용, `MA1592_CHART_CACHE_TTL`처럼 `HAMBURGER_CHART_CACHE_TTL`(기본 60s) 도입 — §11 P0 반영 완료 |
| G-3 | 키움 API 분당 20회 한도와 L3 동시추적 8~20종목 충돌 가능성 | 각 종목마다 1~3분마다 분봉 조회 시, 8~20종목 × 리프레시 주기로 한도 초과 위험. 본문 H1/NFR-7에서 "20 초과 시 trim"만 언급, 배치 조회(`ka10080` 등 복수 종목 조회 API) 활용 여부 불명 | 실시간 체결/틱 데이터로 대금 누적하는 방식(WebSocket 실시간 체결가+거래량 구독)과 REST 분봉 폴링 중 택1을 P0에 명시 |
| G-4 | `hist_impulse` 캘리브레이션의 실행 시점/비용 미정 | 방향 확정: **HTS 조건식 편입 종목만 대상으로 장전 배치** (MA1592의 `ma1592_condition_names` + `fetch_condition_target_items` 패턴 재사용). 다만 **L0 대상으로 쓸 조건식 자체는 아직 미정 — 원본 영상 재검토 필요** | 신규 설정값 `hamburger_condition_names` 예약(조건식 예: 거래대금상위·관심종목류). 배치는 `schedule_server_8am.bat`류에 연결하고, 조건식 결과 종목만 과거 `impulse_lookback_days`(60일) 일봉 스캔 → `technical_snapshots` 우선 조회, 없으면 `ka10081` fallback. **TODO: 조건식 후보 확정 후 재설계** |
| G-5 | `min_patty_value` 등 절대값 파라미터의 종목군 다양성 미검증 | D3에서 "절대 한 값은 노이즈/미탐지"라 스스로 지적 | **보류 — P0/P1은 단일값(`min_patty_value=20억`) 유지.** 실거래·페이퍼 데이터를 쌓은 뒤 시총 구간별 배율 도입 여부를 P2에서 재판단 (D12) |
| G-6 | 테마 판정 로직(`min_theme_peers`)이 THEME_STOCK_PIPELINE 미완성에 의존 | 탐색 결과 THEME_STOCK_PIPELINE은 Phase 1(수집만)이며 스크리너 게이트로 아직 안 씀. G4 게이트를 P1에서 켜려면 선행 파이프라인 완성이 전제 | G4를 P1로 미루는 현재 결정(D7)을 유지하되, THEME_STOCK_PIPELINE Phase 완료 조건을 이 PRD의 선행조건으로 §11에 명시 |
| G-7 | 손절가 스냅샷과 전역 트레일링(`AutoTradeSettings.take_profit_rate`/트레일 스탑) 충돌 | H9에서 "트레일 무장 금지(P1)"라 했지만 P0는 어떻게 전역 트레일을 무력화할지 미정. `BuyOrderExecutor`/`StopLossManager`가 전략별 트레일 on/off 플래그를 이미 지원하는지 확인 필요 | 기존 `Position.strategy_key` 분기에서 트레일 스킵 조건을 P0에도 최소 형태로 넣거나, "전역 익절%를 충분히 넉넉하게" 설정값을 프로필에 명시적 필드로 추가 |
| G-8 | 확정봉(`close`) 판정과 실거래 체결(`next_open`) 사이의 지연 처리 | 분봉 확정 시점과 실제 매수 주문 실행 시점 사이에 API 호출·주문 지연이 있을 때 "다음 분봉 시가" 기준을 어떻게 근사할지(시장가 매수 시 체결가 ≠ next_open) 불명 | 실거래에서는 확정봉 감지 즉시 시장가 매수하고 `entry_fill=next_open`은 백테스트 전용 가정임을 명확히 구분 |
| G-9 | 2차 패티(`TP_SECOND`) 판정 시 "다음 패티가 없는 날"의 청산 경로 | 첫 패티만 나오고 장중 내내 2차 패티가 안 나오면 `TP_STALL`/`MAX_HOLD`/`EOD`로만 빠지는데, 이 세 조건의 우선순위·중복 발동 시 로그 처리가 §5에 명시 없음 | §5 청산 표의 순서(1~6)를 "동시 만족 시 낮은 번호 우선"으로 코드 주석/테스트에 고정 |
| G-10 | 테스트 파일 경로 불일치 | PRD는 `tests/strategy/test_hamburger.py`를 언급하지만 기존 전략 테스트는 `tests/test_*.py` 평면 구조(`tests/test_oversold_breakout.py` 등) | 리포지토리 컨벤션에 맞춰 `tests/test_hamburger.py`로 변경 (또는 `tests/strategy/` 하위 구조를 신설할지 결정) |
