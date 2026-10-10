# PRD - RWM_BOWL (RWM 밥그릇 매매 · 주봉+일봉 RSI 과매도 → Williams %R 반전 → 20/60 골든크로스 자리 → 익일 3분봉 눌림 매수 · 20일선 매도)

| 항목 | 내용 |
|------|------|
| 문서 ID | PRD-STRATEGY-RWM-BOWL |
| 버전 | 0.3.2 |
| 상태 | Draft (검토용 — §15 미결 항목 확정 전 개발 착수 금지) |
| 작성일 | 2026-10-08 |
| 대상 | `stocke` (HTS 조건식 `RWM밥그릇` 편입 종목 → 장 마감 후 일봉 판정 → 익일 장중 실시간 3분봉 눌림 감시 → `BuyOrderExecutor` → `StopLossManager`) |
| 원본 | 사용자 요구 (2026-10-08) ① "매수는 RSI 과매도 + Williams 매수신호, RSI 과매도는 주봉·일봉 둘 다 본다, 매도는 20일선 기준" ② "매수 자리는 20일선이 60일선을 돌파하는 자리(GC), 또는 돌파는 안 했지만 이격이 가까워졌고 주가는 위로 올라와 있는 경우" ③ 의사결정 응답 (§13) + 참고 영상 [YouTube 4-WSO2vPoOY](https://www.youtube.com/watch?v=4-WSO2vPoOY) (영상과 다른 규칙 없음 확인) |
| 관련 | [`PRD_SWING_PULLBACK.md`](./PRD_SWING_PULLBACK.md) (일봉 배치·이평 매도 패턴), [`PRD_MA1592.md`](./PRD_MA1592.md) (분봉 이평 터치 반등 `is_scale_pullback_bar`, `suggested_qty` 경로), [`BACKTEST_PLAN.md`](./BACKTEST_PLAN.md), [`SIGNAL_LIFECYCLE_GUIDE.md`](./SIGNAL_LIFECYCLE_GUIDE.md) |

**R**SI 과매도(주봉이 먼저, 일봉이 뒤따라)로 그릇 바닥을 찍고, **W**illiams %R로 바닥에서 고개를 든 종목이, **M**A20이 MA60을 뚫거나 바짝 붙고 주가가 그 위에 올라선 날을 신호로 잡는다. 다음 날 3분봉에서 눌림이 오면 산다. 20일선이 깨지면 판다.

이름 해석: **R** = RSI, **W** = Williams %R, **M** = Moving Average (매수는 20·60일선 관계, 매도는 20일선).
**밥그릇** = 20일선이 60일선 아래로 내려가 바닥을 만들고, 다시 둥글게 올라와 60일선에 닿는 모양. RSI·Williams는 그릇 바닥이 진짜 바닥이었는지 확인하고, 20/60 교차는 그릇 입구(매수 자리)를 알려 준다.

기존 전략과 다른 점: 햄버거·MA1592·스캘핑은 분봉 돌파·모멘텀이다. 스윙 눌림은 급등을 지켜낸 종목의 피보나치 눌림을 공포 점수로 산다(바닥 매수). 본 전략은 **바닥을 확인만 하고, 바닥에서 올라와 중기 추세가 돌아서는 자리에서 산다**(추세 전환 매수). 종목 선정은 일봉·주봉, 매수 타이밍만 3분봉이다.

---

## 0. 컨셉

### 0.1 밥그릇 3단계 + 진입

```text
                                                         ┌ M1: 20선이 60선 상향 돌파 (GC)
  60일선 ──────────────────────────────────────────── ●  ┤
     \                                              ╱    └ M2: 20선이 60선 2% 이내 + 상승 중
      \  20일선                                   ╱          + 주가가 60선 위
       \__                                    __╱         (공통: 60선이 평평하거나 상승)
          \___                          ____╱              = 신호일 (그릇 입구)
              \________ 그릇 바닥 ______╱                   → 다음 날 3분봉 눌림에서 매수
  RSI 주봉       ≤ 30  ─┐ 5거래일 이내
  RSI 일봉       ≤ 30  ─┘                          ← R: 주봉·일봉 모두 과매도였나
  %R                ≤ −80 ... ↗ −80 상향 돌파        ← W: 바닥에서 돌아섰나
```

| 단계 | 정의 | 시스템 요구사항 | 역할 |
|---|---|---|---|
| ① 그릇 바닥 | 주봉 RSI 과매도 + 5거래일 이내 일봉 RSI 과매도 | 최근 60거래일 안에 확정 주봉 RSI(14) ≤ 30 인 주가 있고, 그 주 마지막 거래일 기준 ±5거래일 안에 일봉 RSI(14) ≤ 30 | **R** — 큰 바닥(주봉)이면서 단기 투매(일봉)까지 나왔나 |
| ② 오른쪽 벽 | Williams %R 반전 | ①의 일봉 과매도 이후 %R(14)이 −80 아래 2봉 이상 머문 뒤 −80 상향 돌파 | **W** — 바닥에서 돌아섰나 |
| ③ 그릇 입구 | 20/60 골든크로스 자리 | M1 또는 M2 성립 + 60선 평평/상승 (§3.3) | **M** — 신호일 (트리거) |
| ④ 진입 | 익일 3분봉 눌림 | 3분봉 20이평 터치 후 반등 양봉 확정 (§3.5) | 매수 타이밍 |

①②는 **이력 조건**(그릇 안에서 있었으면 됨), ③이 **신호 트리거**(장 마감 후 확정), ④가 **체결 타이밍**(다음 날 장중)이다.

### 0.2 왜 세 조건을 같은 날 요구하지 않나

| 조합 | 같은 날 성립 가능성 | 이유 |
|---|---|---|
| RSI ≤ 30 + 20/60 GC | 거의 없음 | RSI 30 은 급락 직후, GC 는 수주간 회복 뒤. 시간 차가 3~8주 |
| Williams −80 돌파 + GC | 드묾 | GC 무렵 주가는 14일 고점 근처라 %R 이 −20 부근 (과매수 쪽) |

그래서 R·W 는 "그릇 바닥에서 있었는가"로 보고, M 이 성립하는 날 신호를 낸다 (사용자 확정, §13 D5).

### 0.3 RSI — 주봉이 1차, 일봉이 확인

사용자 결정: **주봉 과매도를 1차로 보고, 주봉 기준 5거래일 이내에 일봉도 과매도가 나온 종목만** 대상으로 한다.

```text
주봉 과매도 주 w*   : 확정 주봉 RSI_W(14) ≤ 30,  w* 는 최근 bowl_lookback(60거래일) 안
기준일 E            : w* 의 마지막 거래일
일봉 과매도일 d*    : RSI_D(14) ≤ 30  AND  |d* − E| ≤ rsi_pair_days(5) 거래일
                      → 같은 주 안 또는 그 다음 주 안에 일봉도 30 아래
r_date             : 조건을 만족하는 d* 중 가장 이른 날 (W 순서 판정 기준)
```

| 예 | 주봉 RSI | 일봉 RSI ≤ 30 날짜 | 판정 |
|---|---|---|---|
| 8/29(금) 주 확정 RSI_W 28 | ✓ | 8/27(수) | 통과 (E − 2) |
| 〃 | ✓ | 9/04(목) | 통과 (E + 4) |
| 〃 | ✓ | 9/10(수) | 탈락 (E + 8) |
| 주봉 RSI_W 최저 32 | ✗ | 아무 날 | 탈락 — 주봉이 1차 조건 |

**주봉·일봉 모두 30 은 엄격한 조건이다.** 대형주는 주봉 RSI 30 이 몇 년에 한 번이다. 신호가 연간 몇 건 수준일 수 있으며, 백테스트로 신호 수를 먼저 확인한다 (§10, §15 Q12).

### 0.4 M 조건 두 가지

| 코드 | 이름 | 조건 | 의미 |
|---|---|---|---|
| **M1** | 골든크로스 (GC) | 최근 3거래일 안에 MA20 이 MA60 을 상향 돌파, 현재 MA20 > MA60, 종가 > MA20 | 중기 추세 전환 확정 |
| **M2** | GC 임박 | MA20 < MA60 이지만 이격 `(MA60 − MA20)/MA60 ≤ 2%`, 5일 전보다 이격 축소, MA20 상승 중, **종가 > MA60** | 주가가 먼저 60선 위로 올라섬. 20선은 따라오는 중 |
| 공통 | 60선 기울기 | `MA60[t] / MA60[t−5] − 1 ≥ −0.2%` (평평하거나 상승) | 60선이 아직 내려가는 중이면 약한 GC — 사지 않음 (사용자 결정) |

M2 는 GC 보다 하루~수일 먼저 사는 자리다. 그릇 바닥에서 올라오는 중이면 20일 전 가격이 지금보다 낮으므로, 주가가 이 높이에 머무는 동안 20선은 대개 계속 올라 60선을 뚫는다.

> 60선 기울기 조건은 신호를 많이 줄인다. 주봉 과매도 바닥 직후의 GC 는 60선이 아직 내려가는 경우가 많다. 60선이 평평해질 때까지 기다린 자리만 산다는 뜻이다.

### 0.5 원칙

1. **바닥은 확인만 하고 바닥에서 사지 않는다.** R·W 는 "이 그릇이 진짜 바닥에서 시작됐나"의 증거.
2. **주가가 먼저, 이평이 나중.** M2 는 종가 > MA60 일 때만.
3. **종목은 확정봉으로 고르고, 타이밍은 3분봉으로 잡는다.** 신호는 장 마감 후 확정 일봉. 다음 날 3분봉 눌림에서만 산다. 눌림이 안 오면 안 산다(추격 금지).
4. **매수 시점에 이미 20선 위다.** 20선 종가 이탈 매도가 매수 다음 날부터 바로 작동한다. 20선이 곧 손절선이다.
5. **한 그릇에 한 번만 산다.** M2 로 산 뒤 GC 가 나와도 추가 매수 없음.

---

## 1. 파이프라인

```text
[D일 장 마감 후 15:40 — 신호]
L0 후보 = HTS 조건식 rwm_condition_names (기본 "RWM밥그릇": 20/60 근접·GC + 종가 > 60선 + 유동성·제외)
         ∪ 수동 추가 종목 rwm_manual_codes ∪ 보유 종목     ← fetch_condition_target_items (WS 조건검색)
       │   ka10081 일봉 ≥ 300봉 (후보 ≤ 200, 종목당 1회/일, 캐시)
       ▼
지표: MA20·MA60 · 일봉 RSI14 · 주봉 RSI14(일봉 리샘플, 확정 주봉) · Williams %R14
       ▼
M 트리거 (M1 / M2 + 60선 기울기) → R 이력 (주봉 → 5거래일 내 일봉) → W 이력 → 게이트
       │   통과 → 감시 목록(watch) 등록 (유효: D+1 장중), 텔레그램 "내일 눌림 대기" 발송
       ▼
[D+1일 장중 09:06~14:30 — 3분봉 눌림 감시]
watch 종목 실시간 3분봉 구독 (realtime_candle_manager, WebSocket — API 호출 없음)
   09:00 1회 ka10080 3분봉으로 전일 봉 받아 EMA20 시드
   시가 < 신호일 MA20 → 당일 감시 취소 (OPEN_BELOW_MA20)
   상승 흐름 → 3분봉 EMA20 터치 → 반등 양봉 확정 → 가격 상한 확인
       ▼
BUY PULLBACK_3M → BuyOrderExecutor (suggested_qty / suggested_stop)
   14:30 까지 눌림 없음 → NO_PULLBACK, 감시 종료 (추격 매수 없음)
       ▼
[매일 장 마감 후]  EXIT_MA20 (종가 < MA20) / MAX_HOLD → 익일 시가 전량
[장중]            STOP_HARD (현재가 ≤ 전일 MA20 × 0.97) → 즉시 전량
```

기존 전략과 분리: `strategy_type=RWM_BOWL`(DB 표시값), `strategy_key=rwm_bowl`(코드 분기, 소문자), 표시명 `RWM 밥그릇`, 시드 `is_enabled=false`.

신호 계약 (기존과 동일):

```python
{
  "signal_type": "BUY" | "SELL",
  "signal_value": float,
  "additional_data": {
    "reason": "PULLBACK_3M",
    "m_type": "M1_GC" | "M2_NEAR",
    "signal_date": "...",
    ...
  }
}
```

---

## 2. 범위

**MVP (P0)**

| ID | 내용 |
|----|------|
| IN-01 | 시드 + UI 폼. 기본 OFF |
| IN-02 | 계산기 `utils/rwm_bowl.py` — MA20/60, 일봉·주봉 RSI, Williams %R, M1/M2, 60선 기울기, R·W 이력, 3분봉 눌림 판정, 게이트, 청산 판정 (순수 함수) |
| IN-03 | 장 마감 후 일봉 배치: HTS 조건식 조회 → 지표 → 신호 → 감시 목록 + 텔레그램 |
| IN-04 | 익일 장중 3분봉 눌림 감시 → 매수 |
| IN-05 | 청산: 20일선 종가 이탈, 장중 하드 손절(20선 −3%), 만기 |
| IN-06 | 오버나잇 보유 필수 (`flatten_eod=false`), 글로벌 트레일·`take_profit_rate` 미적용 |
| IN-07 | 단위 테스트 + 지표 HTS 대조 픽스처 (§12) |

**제외 (MVP)**

- 분할 매수·피라미딩 (M2 로 산 뒤 GC 추가 매수 없음)
- 눌림 미발생 시 종가 근처 대체 매수 — 옵션만, 기본 OFF (사용자 결정: 추격 금지)
- 감시 기간 2~3일 연장 — 옵션만, 기본 1일 (사용자 결정: 다음 날만)
- M2 실패(20선이 60선 못 뚫고 꺾임) 전용 조기 청산 — P1
- 3분봉 눌림의 거래량 감소 조건 — P1
- 해외주식, 숏, 신용·미수

### 2.1 Phase 정의

| Phase | 정의 | 목표 | 실거래 여부 |
|-------|------|------|------------|
| **P0** | MVP. 일봉 배치 신호 + 익일 3분봉 눌림 매수 + 청산 3종 | §12 수락 기준 + 일봉 백테스트(진입 근사) | Paper/mock만 |
| **P1** | 3분봉 데이터 축적 후 분봉 백테스트, M2 실패 청산, 눌림 거래량 조건, 감시 연장 | 소액 실거래로 재현성 검증 | 소액 실거래 (수동 ON) |
| **P2** | GC 거래량 확인, RSI 다이버전스 가점, 차트 마커 | 실익 검증 | 신규 옵션 기본 OFF |

**결정값**

| 항목 | 값 | 상태 |
|------|------|------|
| 실행 TF | 신호 `1D` (주봉은 일봉 리샘플), 진입 `3M` | 확정 |
| 바닥 확인 (R) | 60거래일 안 확정 주봉 RSI ≤ 30 + 그 주 기준 ±5거래일 안 일봉 RSI ≤ 30 | **확정** |
| 반전 확인 (W) | 일봉 과매도 이후 %R(14) −80 상향 돌파 (바닥 체류 ≥ 2봉), 그릇 안 이력 | 확정 |
| 트리거 (M) | M1 = 3거래일 내 GC + 종가 > MA20 / M2 = 이격 ≤ 2% + 축소 + MA20 상승 + 종가 > MA60 | **확정** |
| 60선 기울기 | 5일 변화율 ≥ −0.2% (평평하거나 상승) | **확정** |
| 추격 방지 | 신호일 종가 ≤ MA20 × 1.08, 신호일 상승률 < 7% | 기본 |
| 진입 | **신호 다음 날 3분봉 EMA20 눌림 반등**, 감시는 다음 날만, 눌림 없으면 안 삼. 가격 상한 신호 종가 +5%, 14:30 까지 | **확정** (수치는 기본값) |
| 매도 | **종가 < MA20 1일 → 익일 시가 전량** | **확정** |
| 손절 | 20일선 종가 이탈이 곧 손절. 장중은 전일 MA20 −3% 하드 손절 | 기본 |
| 보유 | 만기 60거래일 (0 = 끔) | 기본 |
| 사이징 | 리스크 1%/거래, 최대 5종목, 종목당 20% | **확정** |

---

## 3. 데이터 · 지표 · 판정

### 3.0 데이터 소스

| 데이터 | 소스 | 비고 |
|---|---|---|
| 일봉 OHLCV | `get_stock_chart_data(period="1D", allow_off_hours=True)` → `ka10081` | 수정주가. ≥ 300봉 (주봉 RSI14 안정화 ≥ 60주) |
| 주봉 | 일봉 리샘플 (ISO 주, 주 마지막 거래일 종가) | `kiwoom_api.py`에 주봉 TR 미구현 |
| **3분봉 (장중)** | `managers/realtime_candle_manager.py` — WebSocket 체결 → 메모리 3분봉 집계, `get_candles(code, count, include_current=False)` | 이미 있음 (MA1592 장부 종목이 사용 중). API 호출 제한 없음, 구독 한도 95종목. 당일 09:00 이후 봉만 보관 |
| **3분봉 (EMA 시드)** | `get_stock_chart_data(period="3M")` → `ka10080` `tic_scope=3` | 감시 시작 시 1회. 전일 3분봉으로 EMA20 초기값 계산 (당일 봉만으로는 09:57 까지 20봉 미만) |
| 장중 현재가 | 기존 현재가 조회 경로 | STOP_HARD 용, 보유 종목만 |
| **대상 종목 (L0)** | HTS 조건식 `rwm_condition_names` → `utils/screener_targets.py` `fetch_condition_target_items` (WS 조건검색 세션) | 기존 breakout·fractal·MA1592 와 같은 경로. 조건식 정의는 §3.6 |
| ~~`technical_snapshots`~~ | 사용 안 함 | 최신 2026-07-06, 171종목뿐 (정기 갱신 없음, §15 G-1) |
| 종목 제외 | 기존 `_is_screener_stock` (ETF·우선주·관리·위험) | 재사용 |
| RSI 구현 | `utils/auto_trade_engine.py` `compute_rsi_series` (Wilder) | 재사용. 주봉도 같은 함수 |
| 분봉 터치 반등 | `utils/ma1592.py` `is_touch_bounce` / `is_scale_pullback_bar` 패턴 | 3분봉 EMA20 터치 반등에 재사용 |

### 3.1 지표

```text
[일봉]
MA20, MA60      = SMA(close, n)
gap_pct         = (MA60 − MA20) / MA60 × 100          # 양수 = 20선이 아래, 음수 = GC 이후
ma60_slope_pct  = (MA60[t] / MA60[t − 5] − 1) × 100
RSI_D(14)       = Wilder RSI(일봉 close)
WR(14)          = (HH14 − close) / (HH14 − LL14) × (−100), HH14 == LL14 이면 −50

[주봉]
RSI_W(14)       = Wilder RSI(주봉 close)               # 확정 주봉만

[3분봉 — D+1 장중]
EMA20_3m        = EMA(close_3m, 20)                   # 전일 3분봉 포함해 연속 계산
DAY_HIGH        = 당일 시가 이후 최고가 (확정 3분봉 기준)
```

**확정 주봉:** 배치일이 그 주 마지막 거래일이면 그 주까지, 아니면 직전 주까지. 진행 중 주봉은 판정에 쓰지 않는다.

### 3.2 R · W 이력 판정 (그릇 안)

```text
window       = [t − bowl_lookback + 1 .. t]                     # 60거래일

# R — 주봉 1차
os_weeks     = { 확정 주봉 w : w 의 마지막 거래일 ∈ window AND RSI_W[w] ≤ rsi_os_weekly(30) }
if os_weeks 없음 → NO_WEEKLY_OVERSOLD

# R — 일봉 확인 (주봉 기준 5거래일 이내)
pairs        = { (w, d) : w ∈ os_weeks, d ∈ window,
                 RSI_D[d] ≤ rsi_os_daily(30),
                 |trading_days_between(d, E(w))| ≤ rsi_pair_days(5) }
if pairs 없음 → NO_DAILY_OVERSOLD_PAIR
r_date       = min(d for (w, d) in pairs)
r_week       = 해당 w

# W — 일봉 과매도 이후 바닥에서 돌아섰나
w_cross(i)   = WR[i−1] ≤ w_os(−80) < WR[i]
               AND WR 이 i−1 부터 거꾸로 연속 ≤ w_os 인 봉 수 ≥ w_min_bottom_bars(2)
w_ok         = ∃ i ∈ window, i ≥ r_date, w_cross(i)
w_date       = 그런 i 중 가장 최근

# 그릇
bowl_ok      = window 안에 MA20 < MA60 인 날이 있었음
bowl_low     = min(low[window])          # 참고 표시용
bowl_date    = argmin(MA20[window])      # 그릇 바닥 = 20선 최저점. 사이클 키
```

### 3.3 M 트리거 판정 (확정 일봉 t)

```text
# 공통
ma20_up      = MA20[t] > MA20[t−1]
ma60_ok      = ma60_slope_pct[t] ≥ ma60_slope_min_pct(−0.2)        # 평평하거나 상승

# M1 — 골든크로스
gc_day       = 최근 gc_fresh_days(3) 안의 i 중 MA20[i−1] ≤ MA60[i−1] AND MA20[i] > MA60[i]
m1           = gc_day 존재 AND MA20[t] > MA60[t] AND close[t] > MA20[t] AND ma20_up

# M2 — GC 임박, 주가 위
m2           = MA20[t] < MA60[t]
               AND gap_pct[t] ≤ near_gap_pct (2.0)
               AND gap_pct[t] < gap_pct[t − near_shrink_days(5)]
               AND ma20_up
               AND close[t] > MA60[t]
               AND close[t] > MA20[t]

m_type       = M1_GC if m1 else (M2_NEAR if m2 else None)
신호 조건     = m_type AND ma60_ok

# 신선도 — M 상태가 처음 성립한 날부터 m_fresh_days(5) 안에서만 신호
m_first_date = 이번 사이클(bowl_date)에서 m1 or m2 가 처음 참이 된 날
fresh        = (t − m_first_date) < m_fresh_days
```

M 상태가 처음 성립한 날 R·W 가 맞으면 그날 신호. 첫날 W 이력이 없었으나 며칠 뒤 %R 반전이 나오면 `m_fresh_days` 안에서는 그날 신호. 신호가 났는데 다음 날 눌림이 안 와서 못 샀으면, `m_fresh_days` 안에서 M 상태가 유지되는 동안 매일 다시 신호(감시 재등록)를 낸다.

### 3.4 신호일 추격 방지

| 조건 | 기본 | 실패 reason |
|---|---|---|
| 신호일 종가 ≤ MA20 × (1 + `max_above_ma20_pct`/100) | 8% | `TOO_FAR_ABOVE_MA20` |
| 신호일 등락률 < `chase_day_pct` | 7% | `CHASE_DAY` |

### 3.5 익일 3분봉 눌림 진입

사용자 결정: **신호 다음 날, 3분봉 기준으로 눌림이 올 때 매수.**

```text
감시 시간      : pb_start(09:06) ~ pb_end(14:30), 확정 3분봉 기준
기준선         : EMA20_3m
일봉 바닥선    : MA20_D = 신호일 일봉 MA20 (= 손절 기준선)
가격 상한      : cap = signal_close × (1 + max_entry_above_pct(5)/100)

[당일 취소]
  시가 < MA20_D                               → OPEN_BELOW_MA20 (그릇 입구가 이미 깨짐)
  장중 3분봉 종가 < MA20_D                     → BELOW_DAILY_MA20 (당일 감시 종료)

[1. 상승 흐름]  (눌림의 전제 — 오른 뒤에 쉬어야 눌림)
  당일 확정 3분봉 중 종가 > EMA20_3m 인 봉이 있었고
  DAY_HIGH ≥ max(시가, 전일 종가) × (1 + pb_min_rise_pct(1.0)/100)

[2. 눌림]
  확정 3분봉 low ≤ EMA20_3m × (1 + pb_touch_buf_pct(0.2)/100)       # EMA20 터치
  AND (DAY_HIGH − low) / DAY_HIGH × 100 ≥ pb_min_depth_pct(1.0)      # 고점 대비 최소 1% 쉼
  AND (DAY_HIGH − low) / DAY_HIGH × 100 ≤ pb_max_depth_pct(5.0)      # 5% 넘게 빠지면 눌림이 아니라 꺾임

[3. 반등 확정]  (눌림 봉 또는 이후 pb_confirm_bars(2)봉 안)
  양봉 AND 종가 > EMA20_3m AND 종가 > 직전 3분봉 고가
  AND 종가 ≤ cap
  → BUY PULLBACK_3M (다음 3분봉 시작 시 시장가, 또는 반등 봉 종가 지정가 — §15 G-4)

[종료]
  pb_end 까지 반등 확정 없음                    → NO_PULLBACK (추격 매수 없음)
  반등 확정했으나 종가 > cap                    → ENTRY_ABOVE_CAP (계속 감시, 다음 눌림 대기)
```

| 상황 | 처리 |
|---|---|
| 갭 상승 +6% 시작 → 눌림 → 반등 종가가 cap 아래 | 매수 (갭 자체로는 취소 안 함, 가격 상한만 봄) |
| 갭 상승 후 눌림 없이 계속 상승 | `NO_PULLBACK`, 안 삼 |
| 시가부터 계속 하락, EMA20 아래에서만 움직임 | 상승 흐름 조건 불충족 → `NO_PULLBACK` |
| 눌림이 5% 초과 (고점 대비) | 그 봉은 눌림으로 안 봄. 이후 EMA20 회복·재눌림 시 다시 판정 |
| 장중 종가가 신호일 MA20 아래 | `BELOW_DAILY_MA20` 당일 감시 종료 |

### 3.6 대상 종목 (유니버스) — HTS 조건식 + 수동 추가

사용자 결정: **대상 종목은 키움 HTS 조건식으로 가져오고, 수동으로 추가할 수도 있다.** 전종목 일봉 조회·DB 마트는 쓰지 않는다.

```text
[1단계 — 후보 모으기]
  ① rwm_condition_names (기본 "RWM밥그릇") → fetch_condition_target_items   # 키움 서버가 전종목 계산
  ② rwm_manual_codes (수동 추가, 최대 max_manual_codes=50)                  # §3.6.1
  ③ 본 전략 보유 종목 (청산 판정용, 조건식 이탈과 무관)
  − 제외 종목 (_is_screener_stock — 조건식에서 못 거른 것 재확인)
  → ① 은 20일선·60일선 이격 작은 순 max_l0_candidates(200) trim. ②·③ 은 trim 면제

[2단계 — 후보 ≤ 200, ka10081 300봉 — 프로그램이 정밀 판정]
  M 트리거 + 60선 기울기 → R 이력 (주봉 → 일봉) → W 이력 → 게이트
```

**역할 분리 (breakout 조건식과 같은 원칙):** 조건식은 **넓게** 잡고(20/60 근처에 있는 종목), 정확한 판정은 프로그램이 300봉으로 한다. HTS 와 서버의 이평·RSI 값이 미세하게 달라도 조건식이 넓으면 누락이 없다.

#### 조건식 `RWM밥그릇` (초안 — HTS 에서 직접 구성)

| 기호 | 조건 (HTS 화면 기준, 항목명은 버전에 따라 다를 수 있음) | 역할 | 프로그램 정밀 판정 |
|---|---|---|---|
| **A** | `[일] 0봉전 이동평균 20 / 60 골든크로스` — **3봉 이내** 발생 | M1 후보 | §3.3 M1 (GC 3거래일, 종가 > MA20, MA20 상승) |
| **B** | `[일] 0봉전 이동평균 이격: 20이평이 60이평의 96% 이상 100% 이하` | M2 후보 (이격 4% 이내로 넓게) | §3.3 M2 (이격 ≤ 2%, 축소, MA20 상승) |
| **C** | `[일] 0봉전 종가 > 60이평` | "주가는 위로 올라와 있는" | 같음 |
| **D** | `[일] 0봉전 주가 이평 이격도(20) 100% 이상 108% 이하` | 종가 > MA20, 추격 방지 | §3.4 |
| **E** | (선택) `[주] RSI(14) 30 이하` — **12봉 이내** 발생 | 주봉 과매도 1차 (HTS 가 "N봉 이내" 지원 시) | §3.2 R (주봉 → 5거래일 내 일봉 짝) |
| **G** | 관리·정리매매·거래정지·투자위험·ETF·ETN·스팩·우선주 제외 | 제외 | `_is_screener_stock` 재확인 |
| **J** | `[일] 거래대금 20봉 평균 30억 이상` (없으면 `[일] 0봉전 거래대금 30억 이상`) · `주가 1,000원 이상` | 유동성 | `min_trading_value_20d` 재확인 |

```text
논리식: (A or B) and C and D and G and J [and E]
```

- **E 는 HTS 가 주봉 RSI "N봉 이내 발생"을 지원할 때만 넣는다.** 넣으면 후보가 크게 줄어 2단계 API 호출이 줄고, 안 넣어도 프로그램이 판정하므로 결과는 같다.
- 60선 기울기·이격 축소·MA20 상승·일봉 RSI 짝·Williams 반전은 HTS 로 표현하기 어렵거나 기준이 엄격해 **프로그램 전담**.
- 조건식 이름은 `rwm_condition_names`에 등록. 다른 전략 조건식(`breakout`·`ma1592` 등)과 **합치지 않는다.**

#### 3.6.1 수동 추가 종목

차트를 보다가 "이건 밥그릇이 될 것 같다" 싶은 종목을 조건식과 별개로 넣어 두는 목록이다.

| 항목 | 규칙 |
|---|---|
| 저장 | `AutoTradeSettings.rwm_manual_codes` (Text, 쉼표·줄바꿈 구분 종목코드). 기존 `watchlist_codes` 와 같은 형식·저장 경로 |
| 전용 목록인 이유 | 기존 `watchlist_codes` 는 장중 스캐너(`_collect_targets`)가 모든 단타 전략 대상으로 쓰는 공용 관심종목이다. 여기에 넣으면 단타 전략도 그 종목을 스캔한다. 그래서 RWM 전용 목록을 따로 둔다 (`include_global_watchlist=false` 기본) |
| 입력 경로 | (P0) 전략 설정 폼 textarea · 대시보드 `RWM 밥그릇` 탭의 종목코드 입력 + 추가/삭제 버튼 (같은 필드 수정) / (P1) 텔레그램 명령 `/rwm add 005930`, `/rwm del 005930`, `/rwm list` |
| 추가 시 검증 | 종목코드 정규화(`normalize_stock_code`), 종목명 조회 표시, 제외 종목(관리·ETF·스팩·우선주 등)이면 경고 후 거부, 상한 50 초과 시 거부 |
| 판정 | **조건식 종목과 똑같이 2단계 정밀 판정(R·W·M·게이트 전부)을 통과해야 신호.** 수동으로 넣었다고 조건을 건너뛰지 않는다 |
| 유지 | 직접 삭제할 때까지 유지 (스티키). 그릇은 몇 주에 걸쳐 만들어지므로 자동 만료 없음. 매수·청산 후에도 남아 다음 그릇을 기다림 |
| 판정 시점 | 다음 장 마감 배치(15:40)부터. 장중에 추가해도 즉시 신호를 내지 않음 |
| 출처 표시 | 신호·포지션 `additional_data.universe_source` = `condition` \| `manual` \| `both` |

**수동 종목 판정 현황 리포트.** 수동으로 넣은 종목은 "왜 아직 신호가 안 나오는지"가 궁금하다. 장 마감 배치 후 수동 종목마다 단계별 통과 여부와 막힌 이유를 남긴다.

```text
[RWM 수동 종목 현황 · 10/08]
005930 삼성전자   R 주봉 ✓ 일봉 ✓ · W ✓ · M ✗ 이격 3.1% (2% 이하 대기) · 60선 −0.1% ✓
000660 SK하이닉스 R 주봉 ✗ (최저 34.2) → 대상 아님
035720 카카오     R ✓ · W ✓ · M2 ✓ · 60선 −0.6% ✗ (MA60_DOWN)
```

- 대시보드 `RWM 밥그릇` 탭: 수동 종목 목록 + 위 현황을 표로 표시
- 텔레그램: `manual_daily_report=true`(기본)면 장후 1회 발송. 변화가 있는 종목만 보내는 옵션(`manual_report_changes_only`)

#### 조회 시점

| 시점 | 용도 |
|---|---|
| **D일 15:40** (장 마감 후) | 신호 판정. 일봉이 확정된 뒤 조건검색 1회 → 2단계 |
| D+1 장중 | 조회 안 함. 감시 목록(watch)만 사용 |

장 마감 후 조건검색이 확정 종가 기준으로 결과를 주는지 확인 필요 (§15 G-11). 안 되면 15:20 장중 조회 결과를 후보로 쓰고, 2단계 판정은 15:40 확정 일봉으로 한다.

#### 운영 눈금

| 조건식 편입 수 | 조치 |
|---|---|
| **30~150** | 적정 |
| 200 초과 | B 이격 96% → 97%, J 거래대금 상향, 또는 E 추가 |
| 10 미만 | B 이격 96% → 95%, D 상한 108% → 110% |

예상 규모: 조건식 30~150, M 정밀 판정 통과 10~40, 60선 기울기 통과 5~20, R(주봉+일봉 30)·W 까지 통과 **하루 0~2건, 0건인 날이 대부분**일 가능성. 백테스트로 연간 신호 수를 먼저 확인한다.

### 3.7 구멍 점검

| # | 구멍 | 막기 |
|---|------|------|
| H1 | 전종목 `ka10081` 호출 | HTS 조건식 편입 종목만, ≤ 200종목 |
| H1-1 | HTS 조건식과 서버 계산 값 차이로 누락 | 조건식은 넓게(이격 4%, 이격도 108%), 정밀 판정은 서버 |
| H1-2 | 조건식 조회 실패 (WS 끊김·조건식 이름 불일치) | `fetch_condition_target_items` 단건 폴백 + 실패 시 텔레그램 경고. 수동 추가·보유 종목만으로 진행 |
| H1-3 | 수동 추가 종목이 공용 관심종목에 섞여 단타 전략까지 스캔 | RWM 전용 `rwm_manual_codes` 사용, 공용 `watchlist_codes` 미사용 |
| H1-4 | 수동 종목은 조건 완화로 오해 → 무리한 매수 | 수동 종목도 R·W·M·게이트 전부 동일 적용. 현황 리포트로 막힌 이유 표시 |
| H2 | 장중 진행 봉으로 GC 판정 | 신호는 15:40 이후 확정 일봉만 |
| H3 | 진행 중 주봉으로 주봉 RSI 판정 | 확정 주봉만 |
| H4 | R·W·M 같은 날 요구 → 신호 실종 | R·W 는 그릇 안 이력, M 만 트리거 |
| H5 | 바닥 확인 없는 GC (횡보 중 이평 꼬임) | 주봉+일봉 과매도 이력 + W 반전 이력 + 그릇 필수 |
| H6 | 60선 하락 중인 약한 GC | `ma60_slope_pct ≥ −0.2%` (사용자 결정) |
| H7 | 횡보장 20/60 반복 교차 → 잦은 신호 | 사이클 키 `bowl_date` 당 BUY 1회 |
| H8 | M2 로 샀는데 20선이 60선 못 뚫고 꺾임 | 20선 종가 이탈 매도가 처리 (P1 `M2_FAIL`) |
| H9 | GC 이후 한참 지난 자리 추격 | `gc_fresh_days`·`m_fresh_days` + `max_above_ma20_pct` |
| H10 | 익일 갭 상승 후 눌림 없이 급등 → 추격 | 3분봉 눌림 없으면 `NO_PULLBACK`, 반등가 > cap 이면 `ENTRY_ABOVE_CAP` |
| H11 | 익일 시가·장중이 일봉 20선 아래 → 이미 깨짐 | `OPEN_BELOW_MA20` / `BELOW_DAILY_MA20` 당일 취소 |
| H12 | 3분봉 "눌림"이 실제로는 하락 시작 | 상승 흐름 선행 + 고점 대비 5% 이내 + 반등 양봉 확정(종가 > 직전 고가) 후에만 매수 |
| H13 | 장 초반 변동성 (09:00~09:05) | 감시 시작 09:06. 첫 2봉은 상승 흐름 판정에만 사용 |
| H14 | 3분봉 조회 API 부하 | 실시간 구독(`realtime_candle_manager`)으로 API 호출 없음. `ka10080` 은 감시 시작 시 EMA 시드용 1회 |
| H14-1 | 서버 재시작으로 실시간 3분봉 유실 | 재시작 시 `ka10080` 으로 당일 3분봉 재적재 후 실시간 이어 붙임 |
| H15 | 진입가가 일봉 MA20 에 너무 붙어 손절 거리 ≈ 0 → 수량 폭증 | 사이징 손절 거리 최소 `min_risk_pct`(3%) |
| H16 | 매수 직후 20선 언저리 휩쏘 | 종가 1일 이탈 확정(사용자 결정). 휩쏘율은 백테스트로 관찰 |
| H17 | 글로벌 트레일·`take_profit_rate`·EOD 청산 | 본 전략 포지션 예외 |
| H18 | 다른 전략과 같은 종목 이중 보유 | HOLDING/PENDING 이면 `ALREADY_IN_POSITION` |
| H19 | 봉 부족 (신규 상장) | 주봉 RSI 가 1차 조건이므로 일봉 < 300 이면 `INSUFFICIENT_BARS` skip |
| H20 | 시장 리스크 게이트 | 회복 구간 매수라 급락장 매수가 아님 → 기존 게이트 적용. `normalize_strategy_key`에 `rwm_bowl` 등록만 |

---

## 4. 프로필

**시드:** `strategy_name=RWM 밥그릇`, `strategy_type=RWM_BOWL`, `strategy_key=rwm_bowl`, `is_enabled=false`

```json
{
  "exec_tf": "1D",
  "entry_tf": "3M",
  "min_bars": 300,
  "max_l0_candidates": 200,
  "min_trading_value_20d": 3000000000,
  "min_price": 1000,
  "rwm_condition_names": "RWM밥그릇",
  "rwm_manual_codes": "",
  "max_manual_codes": 50,
  "include_global_watchlist": false,
  "manual_daily_report": true,
  "manual_report_changes_only": false,

  "ma_fast": 20,
  "ma_slow": 60,
  "gc_fresh_days": 3,
  "near_gap_pct": 2.0,
  "near_shrink_days": 5,
  "m2_require_close_above_ma60": true,
  "require_ma20_up": true,
  "require_ma60_not_down": true,
  "ma60_slope_days": 5,
  "ma60_slope_min_pct": -0.2,
  "m_fresh_days": 5,

  "bowl_lookback": 60,
  "rsi_period": 14,
  "rsi_os_weekly": 30.0,
  "rsi_os_daily": 30.0,
  "rsi_pair_days": 5,

  "w_period": 14,
  "w_os": -80.0,
  "w_min_bottom_bars": 2,

  "max_above_ma20_pct": 8.0,
  "chase_day_pct": 7.0,
  "max_new_per_day": 3,

  "pb_start": "09:06",
  "pb_end": "14:30",
  "pb_ema": 20,
  "pb_min_rise_pct": 1.0,
  "pb_touch_buf_pct": 0.2,
  "pb_min_depth_pct": 1.0,
  "pb_max_depth_pct": 5.0,
  "pb_confirm_bars": 2,
  "max_entry_above_pct": 5.0,
  "pb_valid_days": 1,
  "pb_fallback": "none",

  "exit_ma": 20,
  "ma_exit_close_days": 1,
  "ma_exit_buffer_pct": 0.0,
  "intraday_hard_buf_pct": 3.0,
  "exit_fill": "next_open",
  "max_hold_days": 60,

  "flatten_eod": false,
  "exempt_market_risk_gate": false,
  "risk_per_trade_pct": 1.0,
  "min_risk_pct": 3.0,
  "max_positions": 5,
  "max_position_pct": 20.0
}
```

| 키 | 기본 | UI | 의미 |
|----|------|----|----|
| `rwm_condition_names` | RWM밥그릇 | Y | 대상 종목 HTS 조건식 이름 (쉼표로 여러 개) |
| `max_l0_candidates` | 200 | Y | 조건식 편입 중 2단계 판정 최대 종목 수 (수동·보유 종목은 별도) |
| `rwm_manual_codes` | 빈 값 | Y | 수동 추가 종목코드 (쉼표·줄바꿈). 조건식과 합집합, 판정은 동일 |
| `max_manual_codes` | 50 | N | 수동 추가 상한 |
| `include_global_watchlist` | false | N | 공용 `watchlist_codes` 도 대상에 포함할지 |
| `manual_daily_report` / `manual_report_changes_only` | true / false | Y | 수동 종목 장후 판정 현황 텔레그램 / 변화 있는 종목만 |
| `gc_fresh_days` | 3 | Y | GC 후 며칠까지 M1 으로 인정 |
| `near_gap_pct` | 2.0 | Y | M2 이격 한도 |
| `near_shrink_days` | 5 | Y | 이격이 N일 전보다 줄었는지 비교 |
| `require_ma60_not_down` | true | Y | 60선 평평/상승일 때만 |
| `ma60_slope_min_pct` | −0.2 | Y | 60선 5일 변화율 하한 (이 이상이면 "평평") |
| `m_fresh_days` | 5 | Y | M 상태 첫 성립 후 신호 허용 기간 |
| `bowl_lookback` | 60 | Y | R·W 이력을 찾는 그릇 기간(거래일) |
| `rsi_os_weekly` / `rsi_os_daily` | 30 / 30 | Y | 과매도 기준 |
| `rsi_pair_days` | 5 | Y | 주봉 과매도 주 마지막 거래일 기준 일봉 과매도 허용 거리(거래일) |
| `w_os` / `w_min_bottom_bars` | −80 / 2 | Y | %R 과매도선·바닥 체류 봉 수 |
| `max_above_ma20_pct` | 8 | Y | 신호일 20선 대비 최대 이격 |
| `chase_day_pct` | 7 | Y | 신호일 급등 제외 |
| `max_new_per_day` | 3 | Y | 하루 신규 감시·진입 상한 |
| `pb_start` / `pb_end` | 09:06 / 14:30 | Y | 3분봉 눌림 감시 시간 |
| `pb_min_rise_pct` | 1.0 | Y | 눌림 전 상승 흐름: 당일 고가 ≥ 기준가 +1% |
| `pb_touch_buf_pct` | 0.2 | Y | 3분봉 EMA20 터치 허용 폭 |
| `pb_min_depth_pct` / `pb_max_depth_pct` | 1.0 / 5.0 | Y | 당일 고점 대비 눌림 깊이 범위 |
| `pb_confirm_bars` | 2 | Y | 눌림 봉 이후 반등 확정까지 허용 봉 수 |
| `max_entry_above_pct` | 5 | Y | 매수가 상한 = 신호 종가 +5% |
| `pb_valid_days` | 1 | Y | 눌림 감시 일수 (1 = 신호 다음 날만) |
| `pb_fallback` | none | Y | 눌림 없을 때 `none` \| `close_1520`(15:20 조건 유지 시 종가 근처 매수) |
| `ma_exit_close_days` | 1 | Y | 20선 종가 이탈 확인 일수 (사용자 결정 1일) |
| `intraday_hard_buf_pct` | 3 | Y | 장중 하드 손절 = 전일 MA20 × (1 − 3%) |
| `max_hold_days` | 60 | Y | 만기 (0 = 끔) |
| `min_risk_pct` | 3 | N | 사이징용 최소 손절 거리 |
| `risk_per_trade_pct` | 1.0 | Y | 손절 시 계좌 손실 % |
| `max_positions` / `max_position_pct` | 5 / 20 | Y | 동시 보유 종목 수 / 종목당 비중 상한 |

---

## 5. 상태머신 · 게이트

```text
IDLE → SIGNAL(D일 장후, 감시 등록) → WATCH(D+1 장중 3분봉 감시) → HOLD → DONE
WATCH → EXPIRED (NO_PULLBACK / OPEN_BELOW_MA20 / BELOW_DAILY_MA20)
        → M 상태·신선도 유지 시 다음 장후 재신호 가능 (사이클당 BUY 1회는 유지)
HOLD  → DONE (STOP_HARD / EXIT_MA20 / MAX_HOLD)
```

그릇·R·W 상태는 저장하지 않는다. 매일 300봉으로 재계산하면 결정적으로 같은 결과가 나온다. 저장하는 것은 감시 목록(`rwm_watch`: 종목, 신호일, 신호 종가, 신호일 MA20, cap, 수량)과 사이클 중복 방지용 `bowl_date` 뿐이다.

**신호 게이트 (장 마감 후)**

| 게이트 | 조건 | 실패 reason |
|--------|------|-------------|
| G0 데이터 | 일봉 ≥ `min_bars` | `INSUFFICIENT_BARS` |
| G1 M 트리거 | M1 또는 M2 | `NO_MA_SETUP` |
| G2 60선 기울기 | `ma60_slope_pct ≥ ma60_slope_min_pct` | `MA60_DOWN` |
| G3 신선도 | M 첫 성립 후 `m_fresh_days` 이내, M1 은 GC 후 `gc_fresh_days` 이내 | `MA_SETUP_STALE` |
| G4 R 주봉 | 그릇 안 확정 주봉 RSI ≤ 30 | `NO_WEEKLY_OVERSOLD` |
| G5 R 일봉 | 주봉 과매도 주 기준 ±5거래일 안 일봉 RSI ≤ 30 | `NO_DAILY_OVERSOLD_PAIR` |
| G6 W 이력 | 일봉 과매도 이후 %R −80 상향 돌파 | `NO_W_SIGNAL` |
| G7 그릇 | 그릇 기간 안에 MA20 < MA60 구간 존재 | `NO_BOWL` |
| G8 추격 | §3.4 | `TOO_FAR_ABOVE_MA20` / `CHASE_DAY` |
| G9 사이클 | `(stock_code, bowl_date)` 첫 BUY | `CYCLE_DONE` |
| G10 중복·슬롯 | HOLDING/PENDING 아님, 보유+감시 < `max_positions`, 당일 신규 < `max_new_per_day` | `ALREADY_IN_POSITION` / `SLOT_FULL` / `DAILY_NEW_LIMIT` |

하루 신호가 `max_new_per_day` 를 넘으면 우선순위: M1_GC > M2_NEAR, 같으면 주봉 RSI 최저값 낮은 순, 같으면 MA20 이격 작은 순.

**진입 게이트 (D+1 장중, §3.5)**

| 게이트 | 조건 | 실패 reason |
|--------|------|-------------|
| E-G1 시가 | 시가 ≥ 신호일 MA20 | `OPEN_BELOW_MA20` (당일 종료) |
| E-G2 일봉 바닥 | 3분봉 종가 ≥ 신호일 MA20 | `BELOW_DAILY_MA20` (당일 종료) |
| E-G3 눌림 | 상승 흐름 → EMA20 터치 → 반등 확정 | `NO_PULLBACK` (14:30 종료) |
| E-G4 가격 상한 | 반등 봉 종가 ≤ cap | `ENTRY_ABOVE_CAP` (계속 감시) |
| E-G5 시장 게이트 | 기존 `check_market_risk_buy_allowed` | 기존 reason |

### 5.1 의사코드

```text
rwm_signal(ind, t, p):                            # D일 장후
  m_type = ma_trigger(ind, t, p)                  # M1_GC | M2_NEAR | None
  if m_type is None: skip NO_MA_SETUP
  if p.require_ma60_not_down and ind.ma60_slope_pct[t] < p.ma60_slope_min_pct: skip MA60_DOWN
  if not fresh(ind, t, p): skip MA_SETUP_STALE

  os_weeks = weekly_oversold(ind, t, p)
  if not os_weeks: skip NO_WEEKLY_OVERSOLD
  r_date = daily_pair(ind, os_weeks, p)           # 주봉 기준 ±5거래일 일봉 과매도
  if r_date is None: skip NO_DAILY_OVERSOLD_PAIR

  w_date = williams_turn(ind, t, p, since=r_date)
  if w_date is None: skip NO_W_SIGNAL

  if not bowl_exists(ind, t, p): skip NO_BOWL
  if close[t] > MA20[t] × (1 + p.max_above_ma20_pct/100): skip TOO_FAR_ABOVE_MA20
  if day_return[t] ≥ p.chase_day_pct: skip CHASE_DAY
  if cycle_bought(code, bowl_date): skip CYCLE_DONE

  watch.add(code, signal_close=close[t], ma20_d=MA20[t],
            cap=close[t] × (1 + p.max_entry_above_pct/100), qty=size(...))
  notify WATCH

on_3m_bar_close(w, bars_3m, p):                   # D+1 장중, 확정 3분봉마다
  if first_bar and bars_3m.open < w.ma20_d: expire OPEN_BELOW_MA20
  if bar.close < w.ma20_d: expire BELOW_DAILY_MA20
  if now < p.pb_start: return
  st = pullback_state(bars_3m, ema20_3m, p)       # rise → touch → confirm
  if st.confirmed:
    if bar.close > w.cap: log ENTRY_ABOVE_CAP; return
    emit BUY PULLBACK_3M (qty=w.qty 재계산, suggested_stop=w.ma20_d × 0.97)
  if now ≥ p.pb_end: expire NO_PULLBACK
```

---

## 6. 진입 · 청산 · 사이징

### 진입

- D일 장후 신호 → D+1 장중 3분봉 눌림 반등 확정 시 매수 (§3.5). 100% 1회.
- 눌림이 안 오면 안 산다. M 상태와 신선도가 유지되면 D+1 장후 재신호 → D+2 다시 감시.
- 같은 사이클(`bowl_date`)에 BUY 1회. M2 로 산 뒤 GC 가 와도 추가 매수 없음.

### 매도 — 20일선 (사용자 확정)

매수 시점에 이미 일봉 20선 위이므로, **매수 다음 날부터 20선 매도가 작동한다.** 별도 무장 단계 없음.

```text
EXIT_MA20 : 일봉 종가 < MA20 (1일)          → 익일 시가 전량
STOP_HARD : 장중 현재가 ≤ round(MA20[전일] × (1 − intraday_hard_buf_pct/100))
            → 즉시 전량 (갭 하락·장중 급락 방어. 20선을 따라 매일 올라감)
            매수 당일은 신호일 MA20 기준
MAX_HOLD  : 진입일 이후 max_hold_days(60) 거래일 → 전량 (0 = 끔)
```

| 규칙 | 의미 |
|---|---|
| 20선 종가 이탈 → 전량 | 추세 종료. 손절과 익절을 겸함 |
| 장중은 하드 손절만 | 장중 20선 살짝 깨는 꼬리는 무시, 종가로 판단 |
| 판 뒤 재매수 없음 | 새 그릇이 와야 다시 산다 |

### 우선순위 (같은 날 복수 조건)

| 우선 | reason | 수량 |
|---|---|---|
| 1 | `STOP_HARD` | 전량 (장중) |
| 2 | `EXIT_MA20` | 전량 |
| 3 | `MAX_HOLD` | 전량 |

### 사이징 (사용자 확정: 1% / 5종목 / 20%)

```text
stop_ref    = 신호일 MA20 × (1 − intraday_hard_buf_pct/100)
risk_amount = equity × risk_per_trade_pct / 100

[신호 시 — 예상 수량, 텔레그램 표시용]
  qty_est   = floor(risk_amount / max(signal_close − stop_ref, signal_close × min_risk_pct/100))

[체결 시 — 실제 수량]
  entry     = 반등 확정 봉 종가
  risk_dist = max(entry − stop_ref, entry × min_risk_pct/100)
  qty       = min(floor(risk_amount / risk_dist),
                  floor(equity × max_position_pct / 100 / entry),
                  floor(max_invest_amount / entry))
```

체결 시 수량을 `additional_data.suggested_qty` 로 넘긴다 (MA1592 와 동일 경로, `BuyOrderExecutor`).

---

## 7. `additional_data` (BUY)

```json
{
  "strategy": "rwm_bowl",
  "universe_source": "condition",
  "reason": "PULLBACK_3M",
  "m_type": "M1_GC",
  "gc_date": "",
  "signal_date": "", "signal_close": 0,
  "ma20": 0, "ma60": 0, "gap_pct": 0, "ma60_slope_pct": 0,
  "r_week": "", "rsi_w_min": 0,
  "r_date": "", "rsi_d_min": 0,
  "w_date": "", "w_bottom_bars": 0,
  "bowl_date": "", "bowl_low": 0,
  "pb_day_high": 0, "pb_low": 0, "pb_depth_pct": 0,
  "pb_ema20_3m": 0, "pb_confirm_time": "",
  "entry_cap": 0,
  "suggested_stop": 0,
  "suggested_qty": 0,
  "max_hold_days": 60
}
```

SELL: `reason` = `STOP_HARD` \| `EXIT_MA20` \| `MAX_HOLD`

---

## 8. 이벤트 / reason_code

신호: `INSUFFICIENT_BARS`, `NO_MA_SETUP`, `MA60_DOWN`, `MA_SETUP_STALE`, `NO_WEEKLY_OVERSOLD`, `NO_DAILY_OVERSOLD_PAIR`, `NO_W_SIGNAL`, `NO_BOWL`, `TOO_FAR_ABOVE_MA20`, `CHASE_DAY`, `CYCLE_DONE`, `ALREADY_IN_POSITION`, `SLOT_FULL`, `DAILY_NEW_LIMIT`, `WATCH_ADDED`

진입: `OPEN_BELOW_MA20`, `BELOW_DAILY_MA20`, `NO_PULLBACK`, `ENTRY_ABOVE_CAP`, `PULLBACK_3M`

청산: `STOP_HARD`, `EXIT_MA20`, `MAX_HOLD`

---

## 9. UI · 알림 (최소)

- 타입 `RWM_BOWL`, 표시명 `RWM 밥그릇`
- 폼: 유동성, M1 신선도, M2 이격·축소 비교일, 60선 기울기, 그릇 기간, 주봉·일봉 RSI 기준·짝 거리, Williams 기준·바닥 체류, 신호일 추격 방지, 3분봉 눌림(감시 시간·상승·터치·깊이·확정 봉 수·가격 상한·감시 일수·대체 매수), 하루 신규 상한, 하드 손절 폭, 만기, 리스크%, 최대 종목 수
- 배지: 감시(눌림 대기) / 보유 / 종료
- 대시보드 후보 탭 `RWM 밥그릇` — 당일 감시 + **M 대기 목록**(R·W 통과, 이격 2~4% 접근 중 또는 60선 기울기만 미달) + 보유 + **수동 종목**(추가/삭제 입력, 단계별 판정 현황 §3.6.1). 종목마다 출처 배지(조건식 / 수동 / 둘 다)
- **텔레그램** (`notifications/telegram_notifier.py`):
  - D일 장후: 감시 등록 ("내일 3분봉 눌림 대기")
  - D+1: 매수 체결 / 감시 종료 사유
  - 보유 중 종가가 MA20 1% 이내 접근 시 15:00 경고
  - 청산 체결

**텔레그램 감시 등록 예:**

```text
[RWM 밥그릇 · 내일 눌림 대기] 종목명 (000000) — M2 GC 임박
20선 14,050 / 60선 14,300 (이격 1.7%, 5일 전 3.9%) · 60선 5일 +0.1% · 종가 14,600
R 바닥: 주봉 RSI 28.6 (08/29 주) → 일봉 RSI 26.8 (08/27, 주봉 기준 −2일)
W 반전: %R −88 → −71 (09/03, 바닥 체류 3일)
매수: 내일 09:06~14:30 3분봉 EMA20 눌림 반등 시 · 상한 15,330 (+5%) · 시가/장중 14,050 아래면 취소
예상 수량 150주 · 매도 종가 20선 이탈 전량 · 장중 하드 손절 13,630
```

---

## 10. 비기능 · 백테스트

| ID | 기준 |
|----|------|
| NFR-1 | 동일 일봉 + params → 동일 신호 (결정적). 동일 3분봉 → 동일 진입 판정 |
| NFR-2 | 장 마감 배치 ≤ 15분 (조건검색 1회 + 후보 200종목 일봉, 캐시 히트 시 ≤ 3분) |
| NFR-3 | 3분봉 감시: 실시간 봉 완성 후 10초 안에 판정 (API 호출 없음) |
| NFR-4 | skip/exit reason 구조화 로그 |
| NFR-5 | 시드 OFF, Paper/mock 기본 |
| NFR-6 | 기존 전략 로직·시장 리스크 게이트 동작 불변 |
| NFR-7 | 글로벌 트레일·`take_profit_rate`·EOD 청산이 본 전략 포지션에 미적용 |

백테스트: 신호·청산은 일봉이라 [`BACKTEST_PLAN.md`](./BACKTEST_PLAN.md) MVP(일봉) 범위. **3분봉 진입은 과거 분봉 데이터가 부족**(`ka10080` 조회 깊이 제한)하므로 단계적으로 한다.

| 단계 | 진입 체결 모델 | 용도 |
|---|---|---|
| P0 일봉 근사 | D+1 일봉으로 근사: 저가 < 신호일 MA20 → 미체결. 고가 ≥ 기준가 × 1.01 이고 (고가 − 저가)/고가 ≥ 1% → 체결가 = max(저가, (시가 + 저가)/2), 단 > cap 이면 미체결. 그 외 미체결 | 신호 수·청산 규칙·대략 성과 |
| P1 분봉 | 실운영 중 축적한 3분봉(또는 `realtime_candle` 1분봉 → 3분 합성)으로 §3.5 그대로 재현 | 눌림 진입 실효성 검증 |

- STOP_HARD: 일봉 low ≤ 전일 MA20 × 0.97 → 그 가격 체결 (시가가 더 낮으면 시가)
- EXIT_MA20: 종가 판정 → 익일 시가
- **먼저 볼 것: 연간 신호 수.** 주봉·일봉 모두 RSI 30 + 60선 기울기 조건에서 신호가 너무 적으면 §15 Q12 로 완화 검토
- 비교 축: M1 만 vs M2 만 vs 둘 다 / `near_gap_pct` 1·2·3 / `ma60_slope_min_pct` −0.5·−0.2·0 / `rsi_os_weekly` 30·35 / `rsi_pair_days` 5·10 / `bowl_lookback` 40·60·90 / R·W 끈 대조군(M 단독)
- 지표: 연간 신호 수, 체결률(감시 대비), 승률, 평균 손익비, 평균 보유일, MDD, 휩쏘율(5일 내 EXIT_MA20 비율)
- **R·W 조건이 성과를 올리는지 대조군(M 단독)과 반드시 비교한다**
- 합격 기준은 수익 수치가 아니라 **§12 규칙 재현 + HTS 지표 대조 일치**

---

## 11. 구현

| Phase | 내용 | 파일 |
|-------|------|------|
| P0 | 계산기 (MA20/60·기울기, RSI 일/주, 주봉 리샘플, %R, M1/M2, R 주봉→일봉 짝, W 이력, 3분봉 눌림 상태, 게이트, 청산) + 테스트 | `utils/rwm_bowl.py`, `tests/test_rwm_bowl.py` |
| P0 | 시드 OFF + `use_rwm_bowl` / `rwm_condition_names` | `core/models.py` |
| P0 | 장 마감 후 배치 (조건식 조회 → 신호 → 감시 목록 → 텔레그램) + 보유 종목 청산 판정 | `scripts/rwm_bowl_batch.py` + `run_rwm_bowl_batch.bat` + `scripts/install_rwm_bowl_batch_task.ps1`, 또는 스캐너 15:40 훅 (§15 G-2) |
| P0 | 대상 종목: 조건식 조회 | `utils/screener_targets.py` `fetch_condition_target_items` + `parse_condition_names` 재사용 (MA1592 `_collect_ma1592_targets` 패턴) |
| P0 | 수동 추가: `rwm_manual_codes` 컬럼 + 설정 저장 경로 + 대시보드 추가/삭제 + 판정 현황 | `core/models.py` (마이그레이션 컬럼 목록에 추가), `core/main.py` (설정 필드 목록 — `watchlist_codes` 와 같은 곳), `static/modules/strategy-manager.js` |
| P0 | 3분봉 눌림 감시: watch 종목 실시간 구독 + 봉 완성 콜백에서 판정 | `managers/realtime_candle_manager.py` `subscribe` / `get_candles` (MA1592 장부 구독 패턴). EMA20 시드는 `get_stock_chart_data(period="3M")` 1회. 터치 반등은 `utils/ma1592.py` `is_touch_bounce` 재사용 |
| P0 | `normalize_strategy_key`에 `rwm_bowl` 등록 (게이트는 기존 동작) | `utils/market_risk_gate.py` |
| P0 | 매수 + `suggested_qty` | `managers/buy_order_executor.py` (`strategy_key == "rwm_bowl"` 분기) |
| P0 | 청산 3종 + 글로벌 트레일 제외 | `managers/stop_loss_manager.py` |
| P0 | 텔레그램 | `notifications/telegram_notifier.py` |
| P0 | UI | `static/modules/strategy-manager.js` |
| P1 | 3분봉 축적·분봉 백테스트, `M2_FAIL`, 눌림 거래량 조건, 감시 연장 | |
| P2 | GC 거래량 확인, RSI 다이버전스, 차트 마커 | |

---

## 12. 수락 기준

**지표**
- [ ] 일봉 MA20·MA60·RSI(14)·Williams %R(14) 가 키움 HTS 값과 일치 (허용 오차 0.5)
- [ ] 주봉 RSI(14)(일봉 리샘플) 가 HTS 주봉 RSI 와 일치 (허용 오차 1.0), 연휴 주 포함
- [ ] 3분봉 EMA20 이 HTS 3분봉 EMA20 과 일치 (허용 오차 0.3%)
- [ ] 진행 중 주봉은 판정에 쓰이지 않음

**대상 종목**
- [ ] 조건식 `RWM밥그릇` 편입 종목 + `rwm_manual_codes` + 보유 종목이 2단계 판정 대상
- [ ] 수동 종목은 조건식에 없어도 판정되고, 200 trim 에서 빠지지 않음
- [ ] 수동 종목도 R·W·M·게이트 하나라도 실패하면 신호 없음, 막힌 이유가 현황 리포트에 표시
- [ ] 제외 종목(관리·ETF 등) 수동 추가 시 거부, 51번째 추가 시 거부
- [ ] 수동 종목은 매수·청산 후에도 목록에 남음
- [ ] 수동 종목이 장중 단타 스캐너 대상(`watchlist_codes`)에 들어가지 않음
- [ ] 조건식 조회 실패 시 수동·보유 종목만으로 판정 진행 + 텔레그램 경고

**신호 (D일 장후)**
- [ ] **M1:** 어제 MA20 ≤ MA60, 오늘 MA20 > MA60, 종가 > MA20, 60선 5일 +0.1%, 7주 전 주봉 RSI 28, 그 주 마지막 거래일 2일 전 일봉 RSI 27, 이후 %R −85→−70 (바닥 2봉) → 감시 등록 (`M1_GC`)
- [ ] **M2:** MA20 < MA60, 이격 1.8% (5일 전 4%), MA20 상승, 종가 > MA60, 60선 평평, R·W 충족 → 감시 등록 (`M2_NEAR`)
- [ ] 60선 5일 −0.5% → `MA60_DOWN`
- [ ] M2 조건 중 종가 < MA60 (MA20 위) → `NO_MA_SETUP`
- [ ] 이격 2.5%, 또는 이격 1.8% 이나 5일 전보다 커짐 → `NO_MA_SETUP`
- [ ] GC 4거래일 경과 → `MA_SETUP_STALE`
- [ ] 일봉 RSI 26 이 있으나 주봉 RSI 최저 32 → `NO_WEEKLY_OVERSOLD`
- [ ] 주봉 RSI 28 (E), 일봉 RSI ≤ 30 이 E + 8거래일에만 → `NO_DAILY_OVERSOLD_PAIR`
- [ ] 주봉 RSI 28 (E), 일봉 RSI ≤ 30 이 E + 4거래일 → 통과
- [ ] %R 반전이 일봉 과매도 **이전에만** 있음 → `NO_W_SIGNAL`
- [ ] 신호일 종가 MA20 +9% → `TOO_FAR_ABOVE_MA20`. 신호일 +8% → `CHASE_DAY`

**진입 (D+1 장중 3분봉)**
- [ ] 시가 < 신호일 MA20 → `OPEN_BELOW_MA20`, 매수 없음
- [ ] 장중 3분봉 종가 < 신호일 MA20 → `BELOW_DAILY_MA20`, 당일 감시 종료
- [ ] 고가 +1.5% 상승 → 3분봉 EMA20 터치 (고점 대비 −2%) → 다음 봉 양봉·종가 > EMA20·종가 > 직전 고가 → `PULLBACK_3M` 매수 1회
- [ ] 상승 흐름 없이(고가 < 기준가 +1%) EMA20 터치·반등 → 매수 없음
- [ ] 고점 대비 −6% 눌림 → 그 봉은 눌림 아님
- [ ] 반등 확정 봉 종가 > cap → `ENTRY_ABOVE_CAP`, 감시 계속
- [ ] 14:30 까지 반등 확정 없음 → `NO_PULLBACK`, 추격 매수 없음
- [ ] `NO_PULLBACK` 후 M 상태·신선도 유지 → 다음 장후 재신호, 사이클당 BUY 1회 유지
- [ ] M2 로 매수 후 GC 발생 → 추가 매수 없음 (`CYCLE_DONE`)

**청산**
- [ ] 매수 다음 날 종가 < MA20 → 익일 시가 전량 `EXIT_MA20`
- [ ] 장중 MA20 살짝 하회 후 종가 회복 → 매도 없음
- [ ] 장중 현재가 ≤ 전일 MA20 × 0.97 → 즉시 `STOP_HARD` (매수 당일은 신호일 MA20 기준)
- [ ] 60거래일 → `MAX_HOLD`

**공통**
- [ ] `RWM_BOWL` 프로필 + UI 저장, 기본 OFF
- [ ] 기존 전략 로직·시장 리스크 게이트 동작 불변
- [ ] 하루 신호 > 3 → 우선순위(M1 > M2 > 주봉 RSI > 이격) 3건만
- [ ] 글로벌 트레일·EOD 청산 미적용
- [ ] 동일 데이터 재실행 시 신호·진입 판정 동일
- [ ] mock/Paper 에서 실주문 어댑터 미호출

실제 종목 픽스처(그릇 바닥·반전일·신호일·눌림 체결·청산일)는 `ka10081`·`ka10080` 데이터로 과거 사례를 뽑아 개발 착수 시 추가한다.

---

## 13. 의사결정

| ID | 상태 | 결정 |
|----|------|------|
| D1 | **확정** | 바닥 확인 = RSI 과매도 + Williams %R 매수신호 |
| D2 | **확정** | RSI = **주봉 1차**(확정 주봉 ≤ 30) + **주봉 기준 5거래일 이내 일봉 과매도**(≤ 30) |
| D3 | **확정** | 매수 자리 = 20선이 60선 돌파(GC, 3거래일 이내), 또는 GC 전 이격 ≤ 2% + 이격 축소 + 종가 > 60선 |
| D4 | **확정** | 매도 = 20일선 종가 1일 이탈 → 익일 시가 전량. 무장 단계 없음 |
| D5 | **확정** | R·W 는 그릇 안 이력 조건(60거래일), M 이 신호 트리거 |
| D6 | **확정** | M2 "주가가 위로" = 종가 > MA60 |
| D7 | **확정** | 60선이 평평하거나 오를 때만 (5일 변화율 ≥ −0.2%) |
| D8 | **확정** | 진입 = 신호 다음 날 3분봉 눌림. 눌림 없으면 안 삼 |
| D9 | **확정** | 3분봉 눌림 기준선 = 3분봉 EMA20. 감시는 신호 다음 날만(못 사면 M 유지 시 장후 재신호). 눌림 없이 오르면 안 삼 |
| D9-1 | 기본 | 3분봉 눌림 수치 = 상승 1% 선행, 깊이 1~5%, 상한 +5%, 09:06~14:30, 반등 확정 = 양봉 + 종가 > EMA20 + 종가 > 직전 고가 (Q16) |
| D10 | 기본 | 장중 하드 손절 = 전일 MA20 −3% |
| D11 | **확정** | 자금 = 리스크 1%/거래, 최대 5종목, 종목당 20% |
| D12 | 기본 | 시장 리스크 게이트는 기존대로 적용 |
| D13 | **확정** | 영상과 다른 규칙 없음. 지표 기본값 RSI 14 / %R 14 / −80 |
| D14 | **확정** | `strategy_key="rwm_bowl"`, `strategy_type=RWM_BOWL` |
| D15 | **확정** | 대상 종목 = HTS 조건식 (`RWM밥그릇`). 조건식은 넓게, 정밀 판정은 서버. `technical_snapshots` 미사용 |
| D16 | 기본 | 장중 3분봉 = 실시간 구독(`realtime_candle_manager`), EMA20 시드만 `ka10080` |
| D17 | **확정** | 수동 추가 지원 (사용자 요구). RWM 전용 `rwm_manual_codes`, 판정은 조건식 종목과 동일, 삭제 전까지 유지 |

### D2가 묻는 것

주봉 RSI 30 은 몇 달짜리 하락의 끝이고, 일봉 RSI 30 은 며칠짜리 투매다. 둘이 5거래일 안에 겹친다는 것은 **긴 하락의 마지막에 투매까지 나온 자리**, 즉 항복(capitulation) 바닥이다.
대신 조건이 엄격해서 신호가 드물다. 백테스트에서 연간 신호 수가 너무 적으면 주봉 기준을 35 로 올리거나 짝 거리를 10거래일로 늘리는 것을 검토한다 (Q12).

### D8이 묻는 것

일봉 신호 다음 날 시가에 바로 사면, 신호일 상승분이 이어지는 갭 상승을 그대로 떠안는다. 3분봉 눌림을 기다리면 평균 단가가 낮아지고 손절선(20선)과의 거리가 줄어 수량을 더 실을 수 있다.
대가는 미체결이다. 눌림 없이 오르는 가장 강한 종목은 놓친다. 사용자 결정은 "추격 금지, 감시는 다음 날만"이다. 체결률이 너무 낮게 나오면 `pb_valid_days`·`pb_fallback` 옵션(기본 OFF)으로 재검토한다.

---

## 14. 변경 이력

| 버전 | 일자 | 내용 |
|------|------|------|
| 0.1.0 | 2026-10-08 | 초안. RSI 과매도(주봉·일봉) + Williams %R −80 반전 시 익일 시가 매수, 20선 무장 후 이탈 매도, 그릇 바닥 손절 |
| 0.2.0 | 2026-10-08 | 매수 자리를 20/60 골든크로스(M1) · GC 임박 + 주가 60선 위(M2)로 전환. RSI·Williams 는 그릇 안 이력 조건. 매도는 20선 종가 이탈(무장 없음) + 장중 하드 손절로 단순화 |
| 0.3.0 | 2026-10-08 | 사용자 의사결정 반영. **RSI 를 주봉 1차(≤ 30) + 주봉 기준 5거래일 이내 일봉(≤ 30) 짝 조건으로 변경**(either/both 등급 삭제). **60선 평평/상승 필터 필수**(5일 −0.2% 이상). **진입을 익일 시가 → 익일 3분봉 EMA20 눌림 반등으로 변경**, 갭 가드를 가격 상한(+5%)·일봉 20선 하한으로 대체. 3분봉 기준선 EMA20·감시 다음 날만·추격 금지 확정. 그릇 60거래일·GC 3거래일·20선 종가 1일 이탈·자금 1%/5종목/20% 확정. 백테스트에 3분봉 진입 일봉 근사 모델 추가 |
| 0.3.1 | 2026-10-08 | **대상 종목을 HTS 조건식(`RWM밥그릇`)으로 확정**(사용자 결정). `technical_snapshots` 프리필터 삭제 — DB 확인 결과 최신 2026-07-06·171종목뿐. 조건식 초안(§3.6)·조회 시점·운영 눈금 추가. 장중 3분봉을 `ka10080` 폴링 → `realtime_candle_manager` 실시간 구독으로 변경 (EMA20 시드만 `ka10080`) |
| 0.3.2 | 2026-10-08 | **수동 추가 종목**(사용자 요구, §3.6.1). RWM 전용 `rwm_manual_codes` (공용 `watchlist_codes` 미사용), 설정 폼·대시보드 추가/삭제, 판정 동일·스티키·trim 면제, 수동 종목 장후 판정 현황 리포트. (P1) 텔레그램 `/rwm add·del·list` |

---

## 15. 미결 항목 (검토 요청)

### 15.1 전략 판단

확정된 항목(RSI 결합·기준값·그릇 기간·GC 기간·진입 시점·3분봉 기준선·감시 기간·추격 금지·20선 이탈 확인·60선 기울기·자금·영상)은 §13 으로 옮겼다. 남은 항목은 기본값으로 개발하고 백테스트·실운영으로 조정한다:

| # | 질문 | 선택지 | 초안 |
|---|------|--------|------|
| Q7 | M2 실패 처리 | (a) 20선 이탈 매도로 충분 (b) 20선이 3일 연속 하락하면 조기 청산 | (a), (b)는 P1 |
| Q12 | 신호가 너무 적을 때 완화 순서 | (a) 주봉 RSI 30 → 35 (b) 짝 거리 5 → 10거래일 (c) 60선 기울기 −0.2% → −0.5% (d) 완화 안 함 | 백테스트 신호 수 확인 후 결정. 연 10건 미만이면 (a)부터 |
| Q16 | 3분봉 반등 확정 | (a) 양봉 + 종가 > EMA20 + 종가 > 직전 고가 (b) 양봉 + 종가 > EMA20 만 (더 빠름) | (a) |

### 15.2 코드베이스 갭 (개발 착수 전 확인)

| # | 미진한 부분 | 왜 문제인가 | 제안 |
|---|------------|------------|------|
| G-1 | `technical_snapshots` 정기 갱신 없음 | DB 확인(2026-10-08): 최신 `as_of_date` 2026-07-06, 171종목, 날짜 1개뿐. `scripts/technical_mart_batch.py` 의 `.bat`·스케줄러 등록 없음 | **본 전략은 HTS 조건식으로 대체해 해당 없음.** 스윙 눌림 PRD 는 여전히 영향 받음 |
| G-2 | 장 마감 후 배치 위치 | `AutoTradeScanner` 는 장중 루프 중심 | 독립 스크립트 + 작업 스케줄러 vs 스캐너 15:40 훅. 스윙 눌림과 통일 |
| G-3 | 감시 목록 저장 | 장후 등록 → 익일 장중 사용. 서버 재시작에도 유지 필요 | P0 JSON 파일(`data/rwm_watch.json`) 또는 `PendingBuySignal` 에 `watch` 상태 추가. `valid_until = D+1 14:30` |
| G-4 | 3분봉 반등 확정 후 주문 방식 | 시장가는 슬리피지, 지정가는 미체결 | 반등 봉 종가 지정가 → 다음 봉 안에 미체결이면 시장가 전환 (cap 이하일 때만) |
| G-5 | 3분봉 EMA20 연속 계산 | `realtime_candle_manager` 는 당일 09:00 이후 봉만 보관 → 09:57 까지 20봉 미만 | 감시 시작 시 `ka10080` 3분봉 1회로 전일 봉 EMA20 시드 → 실시간 봉으로 이어서 갱신 |
| G-6 | `ka10081` 응답 봉 수 | 300봉 미만이면 주봉 RSI 수렴 부족 | 실제 길이 확인, 부족 시 `next-key` 연속 조회 |
| G-7 | 주봉 리샘플 휴장 처리 | 연휴 주 거래일 2~3일 | ISO 주 그룹핑, HTS 주봉 대조 테스트. 짝 거리(±5거래일)도 거래일 기준 |
| G-8 | 장중 하드 손절 가격 갱신 | 전일 MA20 기준으로 매일 바뀜 | 장후 배치에서 `hard_price` 를 포지션에 저장, `StopLossManager` 는 저장값만 비교 |
| G-9 | 3분봉 과거 데이터 | 분봉 백테스트 불가 | P1: 감시 종목 3분봉을 매일 저장, 또는 `realtime_candle` 1분봉에서 3분봉 합성 |
| G-10 | 스윙 눌림과 공통 코드 | 일봉 배치·주봉 리샘플·이평 매도 겹침 | `utils/daily_swing_common.py` 로 추출 검토 |
| G-11 | 장 마감 후 조건검색 | 기존 조건식 사용처(breakout·MA1592)는 모두 장중. 15:40 에 WS 조건검색이 확정 종가 기준 결과를 주는지 미확인 | 개발 첫 단계에서 15:40·16:00 조회 실측. 안 되면 15:20 장중 조회를 후보로 쓰고 판정은 15:40 확정 일봉 |
| G-12 | HTS 조건식 항목 지원 범위 | "골든크로스 N봉 이내", "주봉 RSI N봉 이내", "거래대금 20봉 평균" 항목이 HTS 에 있는지 미확인 | HTS 에서 구성 후 실제 논리식·항목을 §3.6 표에 기입 (breakout 조건식 문서처럼 동기화) |
| G-13 | 실시간 구독 한도 | 구독 한도 95종목을 MA1592 장부 등과 공유 | 감시 종목 ≤ 5 라 여유 있음. 구독 실패 시 `ka10080` 3분 폴링으로 폴백 |
