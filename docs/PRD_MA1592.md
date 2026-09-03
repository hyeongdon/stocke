# PRD: 15/92 홀드 (MA1590)

> **상태**: Draft → Dev Ready (v0.2.0)  
> **작성일**: 2026-08-26  
> **대상 시스템**: stocke 자동매매 (`AutoTradeScanner` → `BuyOrderExecutor` → `StopLossManager`)  
> **관련 코드**: `utils/ma1590.py`, `managers/auto_trade_scanner.py`, `utils/auto_trade_engine.py`, `managers/stop_loss_manager.py`, `managers/buy_order_executor.py`, `core/models.py`, `static/modules/strategy-manager.js`  
> **선행 패턴**: `docs/PRD_Williams_Fractal_EMA_Scalping.md` (전략 분리 + 게이트 패키지)

---

## 0. 한 줄 결론

**유니버스는 HTS `1592매매` 조건식(관찰).** 조건식명은 임진왜란(1592)과 이순신 「죽고자 하면 산다」의 집중 매매 의미. 지표는 3분봉 GC(EMA15>EMA92) 후 의도 비중을 **15% → 35% → 50%** 로 분할 매수. 전고점 50% 익절 후 잔량은 시세(impulse) 뒤 급락+큰이탈로만 청산.

기존 스캐너와 분리. L3 입력은 조건식 **편입 시 스티키 장부**. 조건식 이탈로는 빼지 않고, **EMA15 종가 완전 이탈** 시에만 관찰 장부 제거.

### 분할매수 (기본 `hold_mode=scale_in_gc`)

| 차수 | 비중 | 트리거 |
|------|------|--------|
| T1 | 15% | 기본 **`entry_trigger=price_lead`**: 확정 5분봉 종가 > EMA15·EMA92 이고 EMA 이격 ≤ `price_lead_near_pct`(1%). (`gc_above`면 EMA15>EMA92만) |
| T2 | 35% | **15분봉** `(종가 − EMA15) / EMA15 ≥ scale_gap_pct`(기본 1%) |
| T3 | 50% | 2차 후 **15분봉** `scale_leg3_mode=pullback`(기본): **EMA92 유지** + **EMA15 터치 반등**(양봉). (`hold`면 N봉 유지 레거시) |

조건식 편입 = **관찰 유니버스**일 뿐, 매수 시각이 아님. 데드크로스여도 근접 구간은 장부 유지(`price_lead`). 이격 > `price_lead_far_pct`(3%)면 폐기.

레거시 터치 반등 전량매수: `hold_mode=no_break_then_touch`.

---

## 1. 전략 프로필

| 항목 | 내용 |
|------|------|
| 전략명 | 15/92 홀드 (교차→15선) |
| `strategy_key` / `strategy_type` | `ma1590` / `MA1590` |
| 게이트 패키지 | `ma1590_hold` |
| 스타일 | 추세 눌림 / **5분 EMA15·90 교차** + EMA15 지지 |
| 방향 | 롱만 |
| 실행 TF | **5분봉** (`exec_tf=5M`) |
| MA 소스(기본) | **`bar` + `ema`** — 5분봉 EMA15 / EMA92 |
| ON/OFF | `use_ma1590` — **기본 OFF** |
| Seed | OFF / paper·mock 우선 |
| 유니버스 | HTS 조건식 `1592매매` (`ma1590_condition_names`) |

### 1.1 L0~L4 레이어

| 레이어 | 질문 | 주기 | 주문 |
|--------|------|------|------|
| **L1 유니버스** | HTS 조건식 `1592매매` 편입인가? | 스캔·실시간 편입 (스티키) | 없음 |
| **L2 장부** | 관찰 중인가? | 편입=IN · **EMA15 완전 이탈=OUT** | 없음 |
| **L3 스캔** | 5분 EMA15에서 받쳤나? | **5분 루프** · L2만 | **BUY** |
| **MA38 L3** (별전략) | 급등장부 종목의 일봉 3·8선 지지? | 일 1회 | BUY (본 PRD 범위 외) |
| **기존 스캐너** | 오늘 살 만한가? | ~2분 · 관심종목 | BUY |

**1차 확정 (2026-08-26):** L1은 **텔레그램 조건알림과 동일 조건식(`1592매매`)**.  
편입 → 장부(`GC_WATCH`) **스티키**. 돌파형 조건은 편입 직후 이탈이 흔하므로 **조건식 이탈로는 장부를 빼지 않음**.  
관찰 중 **EMA15 종가 완전 이탈**(`break_before_entry_pct`) 시에만 장부 제거. 보유(`MANAGE_*`)는 청산 규칙만 적용.

### 1.2 타임라인 예시

| 시각 | 사건 | 상태 |
|------|------|------|
| 월 10:20 | 조건식 `1592매매` 편입 → L2 장부 | `GC_WATCH` · **매수 없음** |
| 월 10:25 | 조건식에서 사라짐 | **장부 유지** (스티키) |
| 월 10:20~10:50 | EMA15 위 6봉 유지 (`hold_bars=6`) | `WAIT_HOLD` · 매수 없음 |
| 월 11:05 | 양봉이 EMA15 터치·반등 → **다음 5분 시가** 매수 | `MANAGE_FULL` |
| (관찰 중) | EMA15 종가 완전 이탈 | 장부 제거 · `MA15_BREAK_PRE` |
| 화 | 전고 터치 → **50% 시장가** | `MANAGE_HALF` · `TP1_HIGH` |

**H2:** 조건식 이탈 ≠ 장부 OUT. **EMA15 완전 이탈**이 OUT 트리거.

---

## 2. 파이프라인 접목

```
L1 (HTS 조건식 1592매매) ──편입(스티키)──▶ L2 ma1590_universe
                                              │
                         EMA15 종가 완전 이탈 ──┘  관찰 장부 제거
                                              │
                                              ▼  5분 루프 (L2만)
                                       홀드·터치 게이트 → BUY
```

| 레이어 | 재사용 | 신규 |
|--------|--------|------|
| L1 후보 | `fetch_condition_target_items` · 텔레그램 `1592매매` | `ma1590_condition_names` |
| L2 장부 | — | 편입 스티키 · **EMA15 이탈 시에만 OUT** |
| 게이트 | `evaluate_gate_pack` | `ma1590_hold` |
| 5분 | `get_stock_chart_data` | EMA15/92 캐시 |
| 수량 | 리스크% | `suggested_qty` 우선 (H8) |
| 청산 | StopLoss 루프 | 전고 반익절 + impulse 분기 |

**금지:** 전 `WatchlistStock` L3 스캔(H1), 조건 미편입 종목 자동 L2(H10), 전고 반익절에 글로벌 트레일(H9).  
**조건식 이탈:** 장부 유지. **EMA15 완전 이탈:** 관찰 장부 제거.
---

## 3. 데이터

### 3.1 `ma1590_universe` (P0 인메모리 가능, P1 테이블)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `strategy_id` | FK | 전략 |
| `stock_code` | str PK부 | 종목 |
| `gc_at` | datetime | GC 확정 시각 |
| `gc_date` | date | TTL 기준 |
| `gc_price` | int | GC 시점 가격 |
| `ma15` / `ma90` | float | 스냅샷 |
| `prev_high` | int | 진입 시 고정 |
| `source` | str | `condition` / `volume_rank` / `watchlist` |
| `state` | str | `GC_WATCH` \| `WAIT_HOLD` \| `MANAGE_FULL` \| `MANAGE_HALF` \| `DONE` |
| `impulse_seen` | bool | §6.2 · 한번 true면 유지 |
| `tp1_filled` | bool | 전고 반익절 완료 |
| `expire_date` | date | `gc_date + setup_expire_days` |
| `ma15_broke` | bool | 매수 전 15선 이탈 |

- Unique: `(strategy_id, stock_code, gc_at)`. 동일 종목 신규 GC는 이전 사이클 `DONE` 후만.
- 일봉 MA: `technical_snapshots` 1D 우선, 없으면 `ka10081` 90+1봉. **전종목 API 풀스캔 금지.**

### 3.2 구멍 점검 (H1–H14)

| ID | 구멍 | 막기 |
|----|------|------|
| H1 | L3가 전 관심종목 스캔 | L3 입력 = L2 ∩ (활성 포지션 제외) |
| H2 | 돌파 후 조건식에서 바로 사라짐 | **편입 스티키**. 조건 이탈로 장부 안 뺌. **EMA15 종가 완전 이탈** 시에만 OUT |
| H3 | 프로세스 재시작 시 L2 소실 | P1 테이블 / P0 JSON 복구 |
| H4 | 장중 15>90이나 일봉 미확정 | §5.1 오버레이 규칙. **진행봉 L2 insert 금지** |
| H5 | 장후 검색 0건 | 장중 L1 큐 또는 Daily Bar Mart 백필(P2) |
| H6 | Executor가 신호 시점 시장가 매수 | `entry_fill=next_open` |
| H7 | 세운/MA38 등 타전략 보유 중복매수 | `ALREADY_IN_POSITION` 스킵 |
| H8 | `max_invest`가 suggested_qty 무시 | `additional_data.suggested_qty` 우선 |
| H9 | 글로벌 trailing이 전고 반익절 덮어씀 | `take_profit_price=prev_high`, 트레일 OFF, qty 50% |
| H10 | 미교차 관심주 L2 편입 | L2 = L1 ∩ **GC만** |
| H11 | 전종목 5분 API로 GC | GC는 L1만. overlay = 일봉90 + 현재가 |
| H12 | 반익절 후 StopLoss가 원수량 취급 | `remain_qty` / `tp1_filled` 스냅샷 |
| H13 | 15/92 봉 vs 일봉 혼동 | 기본 `daily_overlay`, `bar`는 별 프로필 |
| H14 | 교차봉에서 매수 | G1 성공 = 장부만. **BUY는 G3만** |

---

## 4. 프로필 (기본값)

전략명: `15/92 홀드` · type `MA1590` · `is_enabled=false`

```json
{
  "ma_fast": 15,
  "ma_slow": 92,
  "ma_type": "ema",
  "ma_source": "bar",
  "exec_tf": "5M",
  "gc_confirm": "cross_close",
  "require_ma_slope_up": true,
  "min_trading_value": 5000000000,
  "hold_mode": "scale_in_gc",
  "hold_bars": 6,
  "touch_mode": "wick",
  "touch_buffer_pct": 0.15,
  "entry_confirm": "bounce_candle",
  "require_bullish_candle": true,
  "break_before_entry_pct": 0.4,
  "leg1_pct": 15.0,
  "leg2_pct": 35.0,
  "leg3_pct": 50.0,
  "scale_tf": "15M",
  "scale_gap_pct": 1.0,
  "scale_leg3_mode": "pullback",
  "scale_hold_bars": 2,
  "prev_high_mode": "swing_lookback",
  "prev_high_lookback_bars": 90,
  "prev_high_lookback_days": 20,
  "tp1_frac": 0.5,
  "take_profit_mode": "prev_high_half",
  "take_profit_pct": 4.0,
  "tp_trigger": "last",
  "tp_fill": "market",
  "tp_same_bar_priority": "tp",
  "tp_fallback": "hard_pct",
  "stop_mode": "ma_or_pct",
  "stop_pct": 4.0,
  "hard_break_pct": 1.0,
  "large_break_pct": 0.7,
  "impulse_min_pct": 2.0,
  "crash_pct": 1.8,
  "crash_bars": 3,
  "setup_expire_days": 8,
  "setup_expire_bars": 0,
  "max_hold_days": 10,
  "flatten_eod": true,
  "entry_fill": "next_open",
  "risk_per_trade_pct": 2.0,
  "max_invest_amount_cap": true
}
```

| Key | 기본 | UI | 의미 |
|-----|------|----|------|
| `ma_source` | **bar** | Y | bar(5분) \| daily_overlay(예비) |
| `ma_type` | **ema** | Y | ema \| sma |
| `require_ma_slope_up` | true | Y | GC 시 slope90 ≥ 0 |
| `min_trading_value` | 50억 | Y | L2 편입 대금 |
| `hold_bars` | 6 | Y | 30분 최소 관찰 |
| `break_before_entry_pct` | 0.4 | Y | 매수 전 15선 이탈 → 폐기 |
| `prev_high_lookback_days` | 20 | Y | 전고 창 |
| `tp1_frac` | 0.5 | Y | 전고 매도 비율 |
| `hard_break_pct` | 1.0 | Y | 시세 전 전량 손절 (EMA15 이탈%) |
| `large_break_pct` | 0.7 | Y | impulse 후 EMA92 대비 종가 이탈% (`STOP_MA_CRASH`) |
| `impulse_min_pct` | 2.0 | Y | MFE ≥ 이값 → impulse |
| `crash_pct` / `crash_bars` | 1.8 / 3 | Y | 고점 대비 급락% (`STOP_MA_DC_CRASH`·`STOP_MA_CRASH` 공통) |
| `setup_expire_days` | 8 | Y | 장부 TTL |
| `flatten_eod` | false | Y | overlay 기본 오버나잇 |

### 4.1 MA 계산 (기본: 5분봉 EMA)

```
EMA15_5m = EMA(close_5m, 15)
EMA92_5m = EMA(close_5m, 90)
GC: EMA15[t-1] <= EMA92[t-1] AND EMA15[t] > EMA92[t]   # 확정봉 종가
홀드/청산 기준선: EMA15_5m
```

**daily_overlay (예비 프로필):** 키움형 일봉 SMA 오버레이 — 본 전략 기본 아님.

---

## 5. 칼날 게이트 (상태머신)

```
IDLE → GC_WATCH → WAIT_HOLD → MANAGE_FULL → MANAGE_HALF → DONE
```

미충족 시 BUY 없이 `signal.skip` + `reason_code`.

| Gate | 조건 | 실패 코드 | 성공 |
|------|------|-----------|------|
| G0 Universe | L3 장부 소속 | (스캔 제외) | — |
| G1 GC | §5.1 + slope + value | `NO_GC` `SLOPE_DOWN` `LOW_VALUE` | → `GC_WATCH` (장부만) |
| G2 관찰 | hold 중 hard break 없음 | `MA15_BREAK_PRE` → DONE | → `WAIT_HOLD` |
| G3 홀드매수 | §5.2 + (양봉) | `NO_BOUNCE` | **BUY** → `MANAGE_FULL` |
| G4 중복 | 타전략 HOLDING/PENDING | `ALREADY_IN_POSITION` | — |
| G5 만료 | expire_date / bars | `SETUP_EXPIRED` → DONE | — |
| G6 전고 | prev_high 계산 | (스킵 아님, TP1용) | — |

### 5.1 GC (5분봉 EMA — 기본)

1. `EMA15[t-1] <= EMA92[t-1]`
2. `AND EMA15[t] > EMA92[t]`
3. `AND (require_ma_slope_up → EMA92[t] >= EMA92[t-1])`

**확정봉 종가만.** 진행봉 GC 판정 금지.  
유니버스는 HTS `1592매매` 편입; 서버 L3는 EMA15>EMA92 유지와 EMA15 홀드·터치를 재확인.  
**당봉 매수 금지** (교차봉=장부만, BUY는 G3만).

### 5.2 진입 (`hold_mode=no_break_then_touch`)

1. GC 후 `hold_bars` 동안 확정봉: `close >= EMA15 * (1 - break_before_entry_pct/100)`
2. 이후(또는 중) 첫 터치 반등:
   - `low <= EMA15 * (1 + touch_buffer_pct/100)`
   - `AND close > EMA15`
   - `AND (require_bullish_candle → close > open)`
3. 만료·중복 아님 → **BUY**

**추격 금지:** 교차 후 EMA15 안 찍고 떠 있으면 사지 않음.  
**매수 전 이탈 = 셋업 폐기.** 재터치 물타기 금지. 신규 GC 필요.

예약 모드(MVP 외): `hold_only`, `touch_only`.

### 5.3 prev_high

- **기본(bar):** `max(high_5m)` over `[gc_bar - prev_high_lookback_bars, entry_bar)`.
- overlay(예비): `max(daily_high)` over `[gc_date - lookback_days, entry_date)` — 당일고 제외.

---

## 6. 진입 · 청산 · 사이징

### 진입

- G3 통과 시 **GC당 3단 분할매수** (T1→T2→T3). `hold_mode=no_break_then_touch`일 때만 GC당 BUY 1회.
- T1 `entry` = 다음 3분 **시가** (`entry_fill=next_open`). T2/T3는 신호 시점 시장가(실행기 설정).
- **T3 pullback**(기본): 2차 체결 봉 이후 확정 **15분봉** 1개에서 — 종가·저가 ≥ EMA92×(1−`break_before_entry_pct`), 저가 ≤ EMA15×(1+`touch_buffer_pct`), 종가>EMA15, 양봉 → leg3 50%.

### 사이징 (분할 합 = 의도 전량)

```
risk_amount = equity * (risk_per_trade_pct / 100)
stop_price  = min(entry*(1-stop_pct/100), ma15*(1-hard_break_pct/100))
qty_full    = floor(risk_amount / (entry - stop_price))
qty_full    = min(qty_full, floor(max_invest_amount / entry))
qty_leg1/2/3 = allocate(qty_full, leg1_pct/leg2_pct/leg3_pct)  # 기본 15/35/50
qty_tp1     = max(1, floor(qty_full * tp1_frac))
```

`qty < 2` → `TP1_SKIP_QTY`, 전량 잔량 규칙.

### 6.1 TP1 (전고 반익절)

기존 `AutoTradeSettings.take_profit_rate` = **트레일 시작%** (즉시익절 아님).  
MA1590은 BUY 시 `take_profit_price = prev_high`(또는 fallback).

```
if prev_high <= entry:
  tp1_price = round(entry * (1 + take_profit_pct/100))  # TP1_FALLBACK
else:
  tp1_price = prev_high  # TP1_HIGH
```

- 실전(D11): `current >= tp1` → 시장가 `qty_tp1`. 진입 직후 이미 위면 `TP1_GAP`.
- 백테스트(D3): 동일봉 TP/SL 시 **TP 반익절 우선**.
- 후: `tp1_filled=true`, `MANAGE_HALF`, 재진입 금지. 글로벌 트레일 OFF.
- 미도달 만료: `MAX_HOLD` (익절 아님).

### 6.2 impulse_seen

```
impulse_seen = tp1_filled OR MFE_pct >= impulse_min_pct
```

한번 true → 영구.  
시세 전 실패 → **전량** 손절. 시세 후 → 잔량만 crash+large_break.

### 6.3 시세 후 잔량 청산

```
crash = (peak-close)/peak*100 >= crash_pct AND bars_since_peak <= crash_bars
large_break = close < EMA92 * (1 - large_break_pct/100)   # 종가, 윅 무시 (structural_stop_ma=EMA92)
if crash AND large_break → STOP_MA_CRASH (잔량)
```

`flatten_eod` → 15:20 `EOD`.

### 6.4 우선순위 (BUY 시점 스냅샷)

1. TP1 미체결 → 반익절 (`TP1_*`)
2. 동일봉 잔량 조건 → 잔량 청산
3. `impulse_seen==false`: `STOP_MA_DC_CRASH`(급락+DC) 또는 `STOP_PCT` **전량**
4. `impulse_seen==true`: §6.3 + `STOP_PCT` 잔량 + `MAX_HOLD` / `EOD`

시세 전 DC만·92선 이탈만으로는 팔지 않음(급락 동반 필요).

| 유형 | 규칙 | 수량 | reason |
|------|------|------|--------|
| 1차 익절 | 전고 | 50% | `TP1_HIGH`/`GAP`/`FALLBACK` |
| 실패 손절 | 급락 + EMA15≤EMA92(DC) | 100% | `STOP_MA_DC_CRASH` |
| % 손절 | last ≤ entry×(1−stop%) | 잔량전부 | `STOP_PCT` |
| 추세종료 | §6.3 | 잔량 | `STOP_MA_CRASH` |
| 만기 | hold_days ≥ max | 잔량 | `MAX_HOLD` |
| 장종료 | flatten_eod | 잔량 | `EOD` |

### BUY `additional_data` 예시

```json
{
  "strategy": "ma1590",
  "setup_state": "ENTRY",
  "ma15": 0, "ma92": 0,
  "ma_source": "daily_overlay",
  "gc_at": "2026-08-26T10:15:00",
  "gc_price": 0,
  "prev_high": 0,
  "tp1_price": 0,
  "tp1_frac": 0.5,
  "tp_mode": "prev_high_half",
  "suggested_stop": 0,
  "suggested_qty": 0,
  "qty_tp1": 0,
  "max_hold_days": 10,
  "reason": "HOLD_MA15",
  "entry_fill": "next_open"
}
```

---

## 7. 이벤트 · reason_code

| 이벤트 | 결과 |
|--------|------|
| `setup.gc` / `setup.hold_wait` | 장부 상태 · 선택적 WatchlistStock |
| `signal.entry_long` | StrategySignal BUY · PendingBuy |
| `signal.exit` | SELL · `qty_frac` 0.5\|1.0 |
| `signal.skip` | 구조화 로그만 |

코드: `NO_GC` `SLOPE_DOWN` `LOW_VALUE` `MA15_BREAK_PRE` `NO_BOUNCE` `SETUP_EXPIRED` `ALREADY_IN_POSITION` `RISK_LIMIT` `TP1_HIGH` `TP1_GAP` `TP1_FALLBACK` `TP1_SKIP_QTY` `STOP_MA_DC_CRASH` `STOP_MA_CRASH` `STOP_PCT` `MAX_HOLD` `EOD`

---

## 8. UI (최소)

- type `MA1590`, 표시명 `15/92 홀드 (교차→15선)`
- 필드: ma_source, 대금, hold_bars, 이탈%, 전고 창, tp1, %, 급락%, 큰이탈%, 손절%, TTL, flatten_eod
- 익절 표시: **전고 50%** (퍼센트 목표가 아님)
- 배지: Idle / GC관찰 / 15선대기 / 전량보유 / 반익절 / 종료
- 대시보드 후보 탭 **1592매매** — `/ma1590/candidates` · **장부(`ma1590_universe`)만** (조건식 실시간 목록 아님)
- MA38·이격도 동시 ON 경고
- 차트 마커 P2. MVP: 신호표 + 메모 (`GC 08-26 / 전고 12300`)

---

## 9. NFR

| ID | 요구 |
|----|------|
| NFR-1 | 동일 5분·일봉·파라미터 → 신호 동일 |
| NFR-2 | L1 심볼만. 종목당 지표 ≤1초 (캐시) |
| NFR-3 | skip 구조화 로그는 상태전이 전 |
| NFR-4 | Seed OFF, paper/mock 기본 |
| NFR-5 | 5분 루프 + 일봉 MA 캐시 |
| NFR-6 | MA38 일스캔과 분리 |

---

## 10. 구현 로드맵

### P0 (이번)

- [x] PRD
- [x] `utils/ma1590.py` — SMA overlay/bar, Gate, 상태, 사이징, 청산 판정
- [x] `tests/test_ma1590.py`
- [x] `use_ma1590` + 파라미터 · AutoTradeSettings 마이그레이션
- [x] `evaluate_gate_pack("ma1590_hold")` + 스캐너 L1→L2→L3 (인메모리+JSON)
- [x] BuyExecutor `suggested_qty` / 리스크 수량
- [x] StopLoss TP1 반익절 + impulse 분기
- [x] UI `strategy-manager.js` / dashboard `use_ma1590` · default OFF

### P1

- `ma1590_universe` 테이블 + 마이그레이션
- Position `tp1_filled` / `remain_qty` 명시 필드
- watchlist `source_type=MA1590`
- Stop MA ST-01 정합

### P2

- 5분+일봉 오버레이 백테스트 (`BACKTEST_PLAN`) — TP1 50% / 잔량 손익 분리
- chart-manager 마커 (MA15/92, 전고, TP1)

---

## 11. 수락 기준

1. MA1590 프로필·UI 저장, 기본 OFF  
2. L1→L2는 GC만 장부. 관심주 단독 스캔 금지  
3. 어제 MA15≤MA90 ∧ 오늘 live 15>90 → 장부, **당봉 매수 금지**  
4. hold 전 15선 hard break → `MA15_BREAK_PRE`, 매수 없음  
5. hold 후 15선 터치 양봉 → BUY 1회, prev_high 기록  
6. 전고 터치 → 50% 매도 → `MANAGE_HALF`  
7. 시세 전 급락+DC → 전량 `STOP_MA_DC_CRASH`  
8. 시세 후 윅만 이탈 → 유지  
9. 시세 후 급락+종가 1% 이탈 → 잔량 `STOP_MA_CRASH`  
10. `prev_high <= entry` → +4% fallback  
11. 동일봉 TP/SL → **반익절 우선**  
12. 세운/MA38 등 보유 종목 스킵  
13. 글로벌 트레일 혼용 금지. 10일 → `MAX_HOLD`  
14. 동일 히스토리 재실행 신호 동일  
15. mock/paper에서 실주문 어댑터 미호출  

---

## 12. 결정 (D1–D12)

| ID | 상태 | 결정 |
|----|------|------|
| D1 | 확정 | 기본 MA = **5분봉 EMA 15/92**. 일봉 overlay는 예비 프로필 |
| D2 | 확정 | 교차 = 장부. **교차봉 매수 금지** |
| D3 | 확정 | 동일봉 TP/SL → **반익절 먼저** |
| D4 | 확정 | 전고 = GC 전 lookback 최고 (당일고 제외) |
| D5 | 확정 | 전고 = **반만** 매도. 잔량 = 시세 후 큰이탈 |
| D6 | 확정 | large = 종가 기준 1.0%. 윅 무시 |
| D7 | 확정 | 시세 = TP1 또는 MFE≥2% |
| D8 | 확정 | 시세 전 = 전량 실패손절. 시세 후 = 급락·큰이탈만 |
| D9 | 확정 | 최소 관찰 후 **15선 터치 반등** 매수. 추격 금지 |
| D10 | 기본 | overlay 오버나잇(`flatten_eod=false`). 만기 10거래일 |
| D11 | 기본 | 장중 현재가 전고 터치 즉시 반익절 |
| D12 | 기본 | P0 메모리 장부, P1 테이블. 반익절은 P1 정합 |

이미 확정: TF 5분, SMA, 칼날 G0–G6, L2 유니버스, Seed OFF, 물타기 없음.

---

## 13. 버전 · 출처

| Ver | 일자 | 내용 |
|-----|------|------|
| 0.1.0 | 2026-08-26 | 초안 (영상·캡쳐) |
| 0.2.0 | 2026-08-26 | Dev 스펙 · L0–L4 · H1–H14 · D1–D12 · TP1+잔량 |

참고: 20/60 이평 매매, 반등봉(가짜이탈), VWAP 잔량 관리, HTS 일봉이평 오버레이.
