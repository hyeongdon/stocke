# PRD: 19/85 돌파 (MA1985)

> **상태**: Draft  
> **작성일**: 2026-09-05  
> **대상 시스템**: stocke 자동매매 (`AutoTradeScanner` → `BuyOrderExecutor` → `StopLossManager`)  
> **관련 코드(신규)**: `utils/ma1985.py`, `managers/ma1985_universe_scheduler.py`, `managers/auto_trade_scanner.py`, `utils/auto_trade_engine.py`, `core/models.py`, `core/config.py`, `static/modules/strategy-manager.js`  
> **선행 패턴**: `docs/PRD_MA1592.md` (MA1592 — 완전히 별도 전략으로 코드 복제, 익절/손절 로직 공유)

---

## 0. 한 줄 결론

**유니버스는 HTS `1985매매` 조건식(관찰).** 지표는 5분봉 **WMA19 / WMA85**(가중이동평균). MA1592(교차→홀드→분할매수)와 달리 **돌파(breakout) 전략**: WMA19>WMA85 정배열 상태에서 최근 N봉 고점을 상향 돌파하면 **전량 1회 매수**. 익절/손절은 **MA1592와 완전히 동일한 로직**(전고 50% 반익절 → impulse 판정 → 잔량은 급락+구조선 이탈로만 청산)을 재사용한다.

MA1592와 병렬로 공존하는 **완전히 별도 전략**(`use_ma1985`, 전용 조건식, 전용 장부 파일, 전용 게이트팩). 매매 로직(엔트리) 자체는 다르지만 청산 파라미터·상태코드·우선순위는 1592와 동일 값을 기본값으로 가져온다.

---

## 1. 전략 프로필

| 항목 | 내용 |
|------|------|
| 전략명 | 19/85 돌파 (WMA 브레이크아웃) |
| `strategy_key` / `strategy_type` | `ma1985` / `MA1985` |
| 게이트 패키지 | `ma1985_breakout` |
| 스타일 | **돌파 매매** — WMA19/85 정배열 + 최근 고점 상향 돌파 |
| 방향 | 롱만 |
| 실행 TF | **5분봉** (`exec_tf=5M`, 설정 가능) |
| MA 종류 | **WMA(가중이동평균)** — MA1592는 EMA, 본 전략은 WMA |
| MA 기간 | fast=19, slow=85 |
| ON/OFF | `use_ma1985` — **기본 OFF** |
| Seed | OFF / paper·mock 우선 |
| 유니버스 | HTS 조건식 `1985매매` (`ma1985_condition_names`) |
| 익절/손절 | **MA1592와 동일 로직·동일 기본값** (§6) |

### 1.1 L0~L4 레이어

| 레이어 | 질문 | 주기 | 주문 |
|--------|------|------|------|
| **L1 유니버스** | HTS 조건식 `1985매매` 편입인가? | 스캔·실시간 편입 (스티키) | 없음 |
| **L2 장부** | 관찰 중인가? | 편입=IN · **WMA19 종가 완전 이탈=OUT** | 없음 |
| **L3 스캔** | WMA19/85 정배열 + 최근 고점 돌파했나? | **5분 루프** · L2만 | **BUY (전량 1회)** |
| **기존 스캐너** | 오늘 살 만한가? | ~2분 · 관심종목 | BUY |

MA1592와 동일하게 **조건식 이탈로는 장부를 빼지 않는다** (돌파형 조건식은 편입 직후 이탈이 흔함). 관찰 중 **WMA19 종가 완전 이탈**(`break_before_entry_pct`) 시에만 장부 제거.

### 1.2 타임라인 예시

| 시각 | 사건 | 상태 |
|------|------|------|
| 월 10:20 | 조건식 `1985매매` 편입 → L2 장부 | `WATCH` · 매수 없음 |
| 월 10:25 | 조건식에서 사라짐 | **장부 유지** (스티키) |
| 월 10:35 | 확정 5분봉 종가가 `breakout_lookback_bars` 최고가 상향 돌파 + WMA19>WMA85 | **BUY(전량)** → `MANAGE_FULL` |
| (관찰 중, 매수 전) | WMA19 종가 완전 이탈 | 장부 제거 · `MA19_BREAK_PRE` |
| 화 | 전고 터치 → **50% 시장가** | `MANAGE_HALF` · `TP1_HIGH` |

---

## 2. 파이프라인 접목

```
L1 (HTS 조건식 1985매매) ──편입(스티키)──▶ L2 ma1985_universe
                                              │
                          WMA19 종가 완전 이탈 ──┘  관찰 장부 제거
                                              │
                                              ▼  5분 루프 (L2만)
                                     정배열 + 고점돌파 게이트 → BUY(전량)
```

| 레이어 | 재사용 | 신규 |
|--------|--------|------|
| L1 후보 | `fetch_condition_target_items` (조건식명만 `1985매매`로 교체) | `ma1985_condition_names` |
| L2 장부 | — (MA1592 장부 구조 패턴만 재사용, 파일은 별도) | `_ma1985_universe.json` |
| 게이트 | `evaluate_gate_pack` 프레임 재사용 | `ma1985_breakout` |
| 5분 | `get_stock_chart_data` | WMA19/85 캐시 |
| 수량 | MA1592 사이징 공식 재사용(분할 없음) | 전량 1회 |
| 청산 | **MA1592 청산 함수 공유(리팩터 후 공통 모듈)** | 없음 — 100% 재사용 |

**공통화 방침:** MA1592의 청산(전고 반익절/impulse/crash/%손절/MAX_HOLD/EOD) 로직은 `utils/ma1592.py`에서 구조선(EMA92)만 파라미터로 받는 공통 함수로 분리해 `utils/ma_exit_common.py`로 옮기고, `ma1592.py`·`ma1985.py` 양쪽이 이를 호출한다. (엔트리 로직만 전략별로 다름.)

**금지:** 조건 미편입 종목 자동 L2 편입, 미확정(진행) 봉으로 돌파 판정, 청산 로직 MA1592와 분기(값은 설정으로 다를 수 있으나 계산식은 동일 함수 사용).

---

## 3. 데이터

### 3.1 `ma1985_universe` (P0 인메모리+JSON, P1 테이블)

MA1592의 `ma1592_universe`(`UniverseRow`)와 동일 스키마를 재사용하되 별도 저장소로 분리:

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `strategy_id` | FK | 전략 |
| `stock_code` | str PK부 | 종목 |
| `in_at` | datetime | 조건식 편입 시각 |
| `wma19` / `wma85` | float | 최근 스냅샷 |
| `prev_high` | int | 진입 시 고정(전고, TP1 기준) |
| `breakout_high` | int | 돌파 판정 기준 고점(진입 전) |
| `state` | str | `WATCH` \| `MANAGE_FULL` \| `MANAGE_HALF` \| `DONE` |
| `impulse_seen` | bool | MA1592와 동일 정의 |
| `tp1_filled` | bool | 전고 반익절 완료 |
| `expire_date` | date | `in_date + setup_expire_days` |
| `wma19_broke` | bool | 매수 전 19선 이탈 |

- Unique: `(strategy_id, stock_code, in_at)`.
- 저장 파일: `logs/_ma1985_universe.json` (MA1592의 `_ma1592_universe.json`과 별도).

### 3.2 구멍 점검 (MA1592 H-표 재사용 + 돌파 전용 추가)

| ID | 구멍 | 막기 |
|----|------|------|
| H1 | L3가 전 관심종목 스캔 | L3 입력 = L2만 (MA1592 동일) |
| H2 | 돌파 후 조건식에서 바로 사라짐 | 편입 스티키. **WMA19 종가 완전 이탈** 시에만 OUT |
| H3 | 프로세스 재시작 시 L2 소실 | P1 테이블 / P0 JSON 복구 |
| H4 | 장중 진행봉으로 돌파 오판 | 확정봉만 판정, 진행봉 insert/판정 금지 |
| H6 | Executor가 신호 시점 시장가 매수 | `entry_fill=next_open` (MA1592 동일) |
| H7 | 타전략 보유 중복매수 | `ALREADY_IN_POSITION` 스킵 |
| H9 | 글로벌 trailing이 전고 반익절 덮어씀 | `take_profit_price=prev_high`, 트레일 OFF, qty 50% (MA1592 동일) |
| H15 | 돌파 직후 눌림에 바로 재돌파 추격매수 | 셋업당 **1회만** BUY, 매수 후 장부는 `MANAGE_*`로 전환(재평가 대상 아님) |
| H16 | 돌파 기준 고점 갱신 없이 계속 낮은 고점 돌파 인정 | 확정봉마다 `breakout_high` 롤링 재계산(§5.1) |

---

## 4. 프로필 (기본값)

전략명: `19/85 돌파` · type `MA1985` · `is_enabled=false`

```json
{
  "ma_fast": 19,
  "ma_slow": 85,
  "ma_type": "wma",
  "ma_source": "bar",
  "exec_tf": "5M",
  "require_wma_align": true,
  "min_trading_value": 5000000000,
  "breakout_lookback_bars": 20,
  "require_bullish_candle": true,
  "break_before_entry_pct": 0.4,
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

`tp1_frac`부터 `max_invest_amount_cap`까지는 **MA1592 기본값과 동일 값**으로 고정(요구사항: "1592랑 같은 익절 손절 추종").

| Key | 기본 | 의미 |
|-----|------|------|
| `ma_type` | **wma** | MA1592는 ema, 본 전략은 **가중이동평균** |
| `require_wma_align` | true | WMA19 > WMA85 정배열 유지 필수 |
| `breakout_lookback_bars` | 20 | 돌파 판정용 최근 고점 창 (5분봉 기준 ≈100분) |
| `break_before_entry_pct` | 0.4 | 매수 전 WMA19 종가 이탈 → 장부 폐기 |
| 이하 청산 관련 키 | — | §6, MA1592와 동일 |

### 4.1 MA 계산 (5분봉 WMA)

```
WMA(period) = Σ(close[i] * weight_i) / Σ(weight_i),  weight_i = 1..period (최근봉일수록 가중치 큼)
WMA19_5m, WMA85_5m 확정봉 기준 계산
정배열: WMA19[t] > WMA85[t]
```

SMA/EMA와 달리 최근 종가에 더 큰 가중치 → 돌파 초입 반응 속도가 EMA보다 빠름.

---

## 5. 칼날 게이트 (상태머신)

```
IDLE → WATCH → MANAGE_FULL → MANAGE_HALF → DONE
```

미충족 시 BUY 없이 `signal.skip` + `reason_code`.

| Gate | 조건 | 실패 코드 | 성공 |
|------|------|-----------|------|
| G0 Universe | L2 장부 소속 | (스캔 제외) | — |
| G1 정배열 | WMA19>WMA85 (require_wma_align) | `NO_ALIGN` | 관찰 유지 |
| G2 관찰 | 매수 전 WMA19 hard break 없음 | `WMA19_BREAK_PRE` → DONE | 관찰 유지 |
| G3 돌파매수 | §5.1 + (양봉) + 거래대금 | `NO_BREAKOUT` `LOW_VALUE` | **BUY(전량)** → `MANAGE_FULL` |
| G4 중복 | 타전략 HOLDING/PENDING | `ALREADY_IN_POSITION` | — |
| G5 만료 | expire_date | `SETUP_EXPIRED` → DONE | — |
| G6 전고 | prev_high 계산 (TP1용) | — | — |

### 5.1 돌파 판정 (확정봉 기준)

1. `WMA19[t] > WMA85[t]` (정배열, `require_wma_align`)
2. `breakout_high = max(high, over last breakout_lookback_bars, 진입봉 제외)`
3. `close[t] > breakout_high` (상향 돌파, 확정봉 종가 기준)
4. `min_trading_value` 이상
5. `require_bullish_candle` → `close[t] > open[t]`

**진행봉 판정 금지.** 위 5조건 모두 충족한 확정봉의 **다음 5분 시가**(`entry_fill=next_open`)에 전량 매수.

**추격 금지:** 이미 돌파 확정봉을 놓쳤고 이후 봉이 추가 급등 중이면 `breakout_high`가 갱신되지 않은 한 재진입 없음(셋업당 1회).

### 5.2 prev_high (TP1 기준 — MA1592와 동일 산식)

- `max(high_5m)` over `[진입시각 - prev_high_lookback_bars, 진입시각)`.
- 돌파 판정용 `breakout_high`와는 **별개 값**(창 길이가 다를 수 있음: 진입 트리거용 20봉 vs TP1 기준 90봉).

---

## 6. 진입 · 청산 · 사이징 (청산은 MA1592와 완전 동일)

### 진입 (MA1592와 다른 부분)

- G3 통과 시 **전량 1회 매수** (분할 없음 — leg1/2/3 개념 없음).
- 체결가 = 다음 5분 **시가** (`entry_fill=next_open`).

```
risk_amount = equity * (risk_per_trade_pct / 100)
stop_price  = min(entry*(1-stop_pct/100), wma85*(1-hard_break_pct/100))
qty_full    = floor(risk_amount / (entry - stop_price))
qty_full    = min(qty_full, floor(max_invest_amount / entry))
qty_tp1     = max(1, floor(qty_full * tp1_frac))
```

`qty < 2` → `TP1_SKIP_QTY`, 전량 잔량 규칙 (MA1592 동일).

### 6.1 TP1 (전고 반익절) — MA1592와 동일 로직

```
if prev_high <= entry:
  tp1_price = round(entry * (1 + take_profit_pct/100))  # TP1_FALLBACK
else:
  tp1_price = prev_high  # TP1_HIGH
```

- `current >= tp1` → 시장가 `qty_tp1`. 진입 직후 이미 위면 `TP1_GAP`.
- 동일봉 TP/SL 동시 충족 시 **TP 반익절 우선**.
- 체결 후: `tp1_filled=true`, `MANAGE_HALF`, 재진입 금지. 글로벌 트레일 OFF.

### 6.2 impulse_seen (MA1592와 동일 정의)

```
impulse_seen = tp1_filled OR MFE_pct >= impulse_min_pct
```

한번 true → 영구. 시세 전 실패 → **전량** 손절. 시세 후 → 잔량만 crash+large_break.

### 6.3 시세 후 잔량 청산 — 구조선만 WMA85로 교체, 로직 동일

```
crash = (peak-close)/peak*100 >= crash_pct AND bars_since_peak <= crash_bars
large_break = close < WMA85 * (1 - large_break_pct/100)   # 종가 기준, structural_stop_ma=WMA85
if crash AND large_break → STOP_MA_CRASH (잔량)
```

`flatten_eod` → 15:20 `EOD`.

### 6.4 우선순위 (MA1592와 완전 동일)

1. TP1 미체결 → 반익절 (`TP1_*`)
2. 동일봉 잔량 조건 → 잔량 청산
3. `impulse_seen==false`: `STOP_MA_DC_CRASH`(급락+WMA19≤WMA85 역배열) 또는 `STOP_PCT` **전량**
4. `impulse_seen==true`: §6.3 + `STOP_PCT` 잔량 + `MAX_HOLD` / `EOD`

| 유형 | 규칙 | 수량 | reason |
|------|------|------|--------|
| 1차 익절 | 전고 | 50% | `TP1_HIGH`/`GAP`/`FALLBACK` |
| 실패 손절 | 급락 + WMA19≤WMA85(역배열) | 100% | `STOP_MA_DC_CRASH` |
| % 손절 | last ≤ entry×(1−stop%) | 잔량전부 | `STOP_PCT` |
| 추세종료 | §6.3 | 잔량 | `STOP_MA_CRASH` |
| 만기 | hold_days ≥ max | 잔량 | `MAX_HOLD` |
| 장종료 | flatten_eod | 잔량 | `EOD` |

### BUY `additional_data` 예시

```json
{
  "strategy": "ma1985",
  "setup_state": "ENTRY",
  "wma19": 0, "wma85": 0,
  "ma_source": "bar",
  "breakout_at": "2026-09-05T10:35:00",
  "breakout_price": 0,
  "prev_high": 0,
  "tp1_price": 0,
  "tp1_frac": 0.5,
  "tp_mode": "prev_high_half",
  "suggested_stop": 0,
  "suggested_qty": 0,
  "qty_tp1": 0,
  "max_hold_days": 10,
  "reason": "BREAKOUT",
  "entry_fill": "next_open"
}
```

---

## 7. 이벤트 · reason_code

| 이벤트 | 결과 |
|--------|------|
| `setup.watch` | 장부 등록 |
| `signal.entry_long` | StrategySignal BUY · PendingBuy |
| `signal.exit` | SELL · `qty_frac` 0.5\|1.0 |
| `signal.skip` | 구조화 로그만 |

코드: `NO_ALIGN` `LOW_VALUE` `WMA19_BREAK_PRE` `NO_BREAKOUT` `SETUP_EXPIRED` `ALREADY_IN_POSITION` `RISK_LIMIT` `TP1_HIGH` `TP1_GAP` `TP1_FALLBACK` `TP1_SKIP_QTY` `STOP_MA_DC_CRASH` `STOP_MA_CRASH` `STOP_PCT` `MAX_HOLD` `EOD`

---

## 8. UI (최소)

- type `MA1985`, 표시명 `19/85 돌파 (WMA 브레이크아웃)`
- 필드: 대금, `breakout_lookback_bars`, 이탈%(`break_before_entry_pct`), 전고 창, tp1, %, 급락%, 큰이탈%, 손절%, TTL, flatten_eod
- 익절 표시: **전고 50%** (MA1592와 동일 문구)
- 배지: Idle / 관찰 / 전량보유 / 반익절 / 종료
- 대시보드 후보 탭 **1985매매** — `/ma1985/candidates` · 장부(`ma1985_universe`)만
- MA1592와 동시 ON 허용(서로 다른 조건식·장부이므로 중복매수는 `ALREADY_IN_POSITION`으로 방지)

---

## 9. NFR

동일: NFR-1~NFR-6은 `docs/PRD_MA1592.md` §9와 동일 요구사항 적용 (재현성, L1 심볼만 조회, skip 로깅, Seed OFF 기본, 캐시, 타전략과 스캔 분리).

---

## 10. 구현 로드맵

### P0

- [ ] PRD (본 문서)
- [ ] `utils/ma_exit_common.py` — MA1592 청산 로직(전고 반익절/impulse/crash/%손절/MAX_HOLD/EOD) 공통 함수로 분리, `ma1592.py` 리팩터하여 재사용
- [ ] `utils/ma1985.py` — WMA 계산(`wma_series`), 정배열·돌파 판정, 장부(`Ma1985UniverseStore`), 사이징 — 청산은 `ma_exit_common` 호출
- [ ] `tests/test_ma1985.py`
- [ ] `core/models.py`: `use_ma1985`, `ma1985_condition_names`, `ma1985_*` 파라미터 컬럼 + SQLite 마이그레이션 블록 (MA1592 컬럼 목록 패턴 그대로 복제, 접두사만 교체 + `breakout_lookback_bars` 등 신규 필드 추가)
- [ ] `core/config.py`: `MA1985_DEFAULT_CONDITION_NAME = "1985매매"`, `MA1985_CHART_CACHE_TTL`
- [ ] `managers/ma1985_universe_scheduler.py` (MA1592 스케줄러 복제, 파일/스토어만 교체)
- [ ] `managers/auto_trade_scanner.py`: `_collect_ma1985_targets` (조건식 `1985매매` 조회 → 장부 sync)
- [ ] `utils/auto_trade_engine.py`: `evaluate_gate_pack` 분기에 `ma1985_breakout` 추가, `_eval_ma1985_breakout` 구현
- [ ] `static/modules/strategy-manager.js` / dashboard: `use_ma1985` 토글, 기본 OFF

### P1

- `ma1985_universe` 테이블 + 마이그레이션 (P0은 JSON)
- 대시보드 후보 탭 `/ma1985/candidates`

### P2

- 백테스트 (`BACKTEST_PLAN`) — WMA 돌파 엔트리 vs MA1592 EMA GC-hold 엔트리 성과 비교

---

## 11. 수락 기준

1. MA1985 프로필·UI 저장, 기본 OFF
2. L1(`1985매매` 조건식 편입) → L2 장부는 스티키, 조건식 이탈로 빠지지 않음
3. WMA19>WMA85 정배열 + 확정봉 종가가 `breakout_lookback_bars` 고점 상향 돌파 → **전량 1회 매수**, 진행봉으로는 매수 금지
4. 매수 전 WMA19 종가 완전 이탈 → `WMA19_BREAK_PRE`로 장부 제거, 매수 없음
5. 전고 터치 → 50% 매도 → `MANAGE_HALF` (MA1592와 동일 산식)
6. 시세 전 급락+역배열(WMA19≤WMA85) → 전량 `STOP_MA_DC_CRASH` (MA1592와 동일 산식)
7. MA1592와 동시 활성화 시에도 서로 독립 장부·독립 조건식으로 충돌 없음 (중복 보유는 `ALREADY_IN_POSITION`)
