# PRD - SWING_PULLBACK (공포 구간 계획 분할매수 · 중장기 스윙)

| 항목 | 내용 |
|------|------|
| 문서 ID | PRD-STRATEGY-SWING-PULLBACK |
| 버전 | 0.3.0 |
| 상태 | Draft (검토용 — §15 미결 항목 확정 전 개발 착수 금지) |
| 작성일 | 2026-10-03 |
| 대상 | `stocke` (`AutoTradeScanner` 일봉 배치 + 장중 계획가 폴링 → `BuyOrderExecutor` → `StopLossManager`) |
| 원본 | 사용자 차트 캡처 4종 검토 (DL이앤씨·대한해운·무림P&P·샌디스크) + 사용자 컨셉 "떨리는 자리가 기회의 자리" (2026-10-03) |
| 관련 | [`PRD_MA1592.md`](./PRD_MA1592.md) (분할매수·반익절·오버나잇 패턴), [`PRD_STRATEGY_HAMBURGER.md`](./PRD_STRATEGY_HAMBURGER.md) (L0~L4 문서 구조), [`PRD_TURTLE_DONCHIAN.md`](./PRD_TURTLE_DONCHIAN.md), [`BACKTEST_PLAN.md`](./BACKTEST_PLAN.md), [`SIGNAL_LIFECYCLE_GUIDE.md`](./SIGNAL_LIFECYCLE_GUIDE.md) |

**남들이 공포에 패대기칠 때, 미리 짜 둔 계획대로 줍는다.**
다만 아무 종목이나 줍지 않는다. **급등을 지켜낸 종목**(바닥 → 첫 급등 → 눌림의 3단계)이 지지 자리까지 공포로 밀릴 때만 줍는다.
계획(가격·비중·손절)은 공포가 오기 **전에** 장 마감 후 확정해 두고, 장중에는 기계가 실행한다. 2~12주 보유, 10일선·20일선이 깨지면 판다.

기존 전략(햄버거·MA1592·스캘핑·세운 6종)은 전부 분봉·당일~수일 단위이고, 오르는 힘(돌파·대금)을 산다. 본 전략은 **일봉 단위로, 떨어지는 공포를 산다.** 해석 단위·보유 기간·매수 심리가 모두 다르므로 별도 타입이다.

---

## 0. 컨셉

### 0.1 떨리는 자리가 기회의 자리

> 지나가면 쉬운데, 실제로는 분할매수 계획을 잡고 사도 떨리는 건 매번 같다.
> 결국 떨리는 자리가 기회의 자리다.

이 문장을 시스템 요구사항으로 바꾸면 세 가지다.

| 컨셉 | 시스템 요구사항 |
|---|---|
| 공포에 판 사람의 물량을 줍는다 | **공포 점수**(§3.3)가 높을 때 계획가에서 매수. 반전 확인을 기다리지 않는다 |
| 떨려도 계획대로 산다 | 계획서는 **장 마감 후 미리** 확정, 장중 실행은 기계가 한다. 체결 후 계획 변경 금지 |
| 지나가면 쉬운 자리 | 반등 확인 후 사면 늦다. 확인 매수(L3)는 마지막 40%만 |

### 0.2 공포 매수가 "떨어지는 칼"이 되지 않으려면

공포는 좋은 종목에도, 망가진 종목에도 온다. 무림P&P도 8년 내내 공포였다.
**가격 자리는 공포로 고르고, 종목은 구조로 고른다.**

| 질문 | 거르는 장치 | 샘플 |
|---|---|---|
| 큰 추세가 살아 있나 | G1 추세 필터 (120선 우상향·미래 기울기·240선·주봉) | 무림P&P 탈락 |
| 급등을 지켜냈나 | G3 반납 < 80% | 대한해운 탈락 |
| 공포가 지지 자리에서 왔나 | G4 되돌림 38.2~78.6% + G5 confluence | DL이앤씨·샌디스크 통과 |
| 틀리면 어디서 끝내나 | 손절가를 계획서에 **먼저** 고정 | — |
| 한 번에 다 물리지 않나 | 30/30/40 분할, 하루 1차수 | — |

### 0.3 원칙 (샘플 4종에서 도출)

1. **구조로 거르고, 공포로 산다.** 눌림 깊이는 종목 변동성마다 다르다(샌디스크 −55%, DL이앤씨 −16%). 고점 대비 고정 %가 아니라 **직전 상승 구간의 되돌림 비율**로 판단한다.
2. **급등을 80% 이상 반납하면 추세가 아니다.** 1단계(바닥)로 강등한다.
3. **120일선은 미래 기울기까지 본다.** 120거래일 전 종가가 현재가보다 높으면 120선이 곧 꺾인다(대한해운).
4. **상위 TF가 하락이면 일봉 공포는 무시한다** (무림P&P 월봉 역배열).
5. **환호에는 사지 않는다.** 장대양봉·RSI 과열·상단 밴드 이탈 날은 매수 금지(샌디스크 10/02). 공포의 반대편이다.

### 0.4 단계 정의

| 단계 | 정의 | 처리 | 샘플 |
|---|---|---|---|
| S1 바닥 탐색 | 장기선 아래 또는 하락, 저점이 계속 낮아짐 | 제외 | 무림P&P |
| S2 첫 급등 | 직전 상승 구간 ≥ `min_rally_pct`, 아직 눌림 미확정 | 관찰만 | — |
| S3 지켜낸 눌림 | S2 이후 하락 구간 진행 중, 되돌림 38.2~78.6% 구간 접근 | **매수 계획서 대상** | DL이앤씨, 샌디스크(7월) |
| DEMOTED | 직전 큰 급등의 되돌림 ≥ `major_retrace_max` 또는 구조 붕괴 | S1로 강등 | 대한해운 |

---

## 1. 파이프라인

```text
[장 마감 후 15:40 — 계획]
L0 후보 (technical_snapshots 1D 프리필터 ∪ HTS 조건식 swing_condition_names ∪ 관심종목)
       │   ka10081 일봉 ≥ 320봉 (종목당 1회/일, 캐시)
       ▼
L0 추세 필터 (G1)  ──fail──▶ skip (reason 기록)
       ▼
L1 지그재그 파동 분류 S1/S2/S3/DEMOTED (G2·G3)
       ▼
L2 swing_universe: S3 ∩ 되돌림 구간 접근 ∩ confluence ≥ N (G4·G5)
       │   매수 계획서 생성: plan_l1 / plan_l2 / stop / 수량
       │   텔레그램 "매수 계획서" 발송
       ▼
[장중 — 줍줍]
L3a 계획가 폴링 (스캐너 주기, 장부 WAIT_ZONE·MANAGE_L1 종목만 현재가 조회)
       │   현재가 ≤ plan_l1 (또는 plan_l2) AND 공포 점수 ≥ min_fear_score AND 현재가 > stop
       ▼
      BUY FEAR_L1 / FEAR_L2 (하루 1차수)
       │
[장 마감 후 — 확인]
L3b 확인 매수: 종가 > MA20 + 모멘텀 회복 → 익일 시가 BUY CONFIRM_MA20
       ▼
BuyOrderExecutor (suggested_stop / suggested_qty 스냅샷)
       ▼
StopLossManager (strategy_key=swing_pullback 분기)
   STOP_HARD / STOP_STRUCT / EXIT_MA20 / EXIT_MA10 / TIME_STOP / MAX_HOLD
```

기존 전략과 분리: `strategy_type=SWING_PULLBACK`(DB 표시값), `strategy_key=swing_pullback`(코드 분기, 소문자), 표시명 `스윙 눌림`, 시드 `is_enabled=false`.

신호 계약 (기존과 동일, 분할은 `qty_frac`):

```python
{
  "signal_type": "BUY" | "SELL",
  "signal_value": float,
  "additional_data": {
    "reason": "FEAR_L1" | "FEAR_L2" | "CONFIRM_MA20" | "ZONE_REVERSAL",
    "leg": 1 | 2 | 3,
    "qty_frac": 0.3,
    "fear_score": 3,
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
| IN-02 | 계산기 `utils/swing_pullback.py` — 일봉 지표, 지그재그, 단계 분류, 공포 점수, 계획서, 게이트, 청산 판정 (순수 함수) |
| IN-03 | 장 마감 후 일봉 배치: L0 → L2 + 매수 계획서 + 텔레그램 발송 |
| IN-04 | 장중 계획가 폴링 → `FEAR_L1` / `FEAR_L2` 매수 |
| IN-05 | 장 마감 후 확인 매수 `CONFIRM_MA20` (익일 시가) |
| IN-06 | 시장 리스크 게이트·급락장 연동 차단에서 본 전략 **예외 처리** (§3.6 H16) |
| IN-07 | 청산: 구조 손절·장중 하드 손절·10선 이탈 절반·20선 이탈 전량·시간 손절·만기 |
| IN-08 | 오버나잇 보유 필수 (`flatten_eod=false`), 글로벌 트레일 무장 금지 |
| IN-09 | 단위 테스트 + 샘플 회귀 픽스처(§12) |

**제외 (MVP)**

- 해외주식 (샌디스크는 규칙 설계 참고용. 키움 국내 전용 시스템)
- 숏, 신용·미수, 피라미딩(수익 중 추가 매수)
- 거래소 지정가 예약 주문(장 시작 전 미리 걸어 두기) — P1 (§15 G-7)
- 외부 공포 지표(VKOSPI, 신용 반대매매, 커뮤니티 심리) — P2
- 실적 발표·이벤트 캘린더 회피 — P2

### 2.1 Phase 정의

| Phase | 정의 | 목표 | 실거래 여부 |
|-------|------|------|------------|
| **P0** | MVP. 인메모리/JSON 장부, 계획서 + 장중 폴링 매수 + 확인 매수 + 청산 7종 | §12 수락 기준 + 일봉 백테스트 | Paper/mock만 |
| **P1** | `swing_universe` 테이블, 거래소 지정가 예약 주문, 월봉 필터, 반전봉 모드(`entry_mode=reversal`) 비교 | 소액 실거래로 재현성 검증 | 소액 실거래 (수동 ON) |
| **P2** | 외부 공포 지표, 실적 캘린더, 업종 상대강도, 수평 매물대 자동 인식, 차트 마커 | 실익 검증 | 신규 옵션 기본 OFF |
| **P3** | 범위 밖: 해외주식, 숏, 신용 | — | — |

**결정값 (초안)**

| 항목 | 값 |
|------|------|
| 실행 TF | `1D` 판정 + 장중 현재가 폴링. 상위 TF 필터는 일봉을 주봉으로 리샘플 |
| 계획 | 장 마감 후 확정봉으로만 생성. 첫 체결 후 가격·손절 고정 |
| 1·2차 진입 | **공포 매수** — 계획가 터치 + 공포 점수 ≥ 2. 반전 확인 안 기다림 |
| 3차 진입 | 확인 매수 — 종가 MA20 회복 + 모멘텀, 익일 시가 |
| 비중 | 30/30/40. L2 미체결이면 L3가 70%. 하루 1차수 |
| 손절 | 상승 구간 78.6% 되돌림 **종가** 이탈 (장중 꼬리는 무시, 하드 손절만 장중) |
| 매도 | **10일선·20일선.** 20선·10선 위 종가로 무장 후, 종가 10선 이탈 → 50%, 종가 20선 이탈 → 잔량 전량. 10선 규칙은 정배열일 때만 |
| 보유 | 만기 60거래일. L1 후 15거래일 내 무장 못 하면 시간 손절 |
| 사이징 | 리스크 기반 (`risk_per_trade_pct=1.0`) |

---

## 3. 유니버스 · 데이터 · 공포 점수

### 3.0 데이터 소스

| 데이터 | 소스 | 비고 |
|---|---|---|
| 일봉 OHLCV | `get_stock_chart_data(period="1D", allow_off_hours=True)` → `ka10081` | 수정주가. ≥ 320봉 필요 (240선 + 120일 전 종가 + 주봉 60주) |
| 장중 현재가·누적 거래량 | 기존 현재가 조회 경로 (스캐너 주기 캐시) | 장부 종목만 |
| L0 프리필터 | `technical_snapshots` (1D) — `ma120`, `close_price`, `avg_trading_value_20d`, `rsi14` | 240선·MACD·BB·기준선 컬럼 없음 → 계산기에서 직접 계산 |
| 지수 등락 | `utils/market_risk_gate.py` `evaluate_market_risk` / `_crash_index_pct` | 공포 점수 F6 (차단이 아니라 가점) |
| 종목 제외 | 기존 `_is_screener_stock` (ETF·우선주·관리·위험) | 재사용 |

**API 예산:** L0 후보를 프리필터로 ≤ `max_l0_candidates`(기본 150)로 줄인 뒤에만 `ka10081` 호출. 장중 폴링은 장부 종목(3~15개) 현재가만.

### 3.1 지표 (계산기에서 산출)

```text
MA5/20/60/120/240        = SMA(close, n)
BB(20,2)                 = MA20 ± 2σ
ENV(20,10)               = MA20 × (1 ± 0.10)
KIJUN(26)                = (max(high,26) + min(low,26)) / 2
MACD(12,26,9)            = EMA12 − EMA26, signal = EMA9(MACD), hist = MACD − signal
RSI(14)                  = Wilder
VOL_MA20                 = SMA(volume, 20)
ATR14, ATR14_PCT
WEEKLY_MA60              = 주봉 리샘플 후 SMA(close_w, 60)
```

캡처에 걸린 480/960/1920선, ENV 20% 밴드는 판단에 쓰지 않는다(데이터 부족·중복).

### 3.2 파동 인식 (지그재그)

"최근 N일 최고/최저"로 잡으면 DL이앤씨처럼 **4월 고점 기준으로 잘못 계산**된다. 확정 피벗 기반 지그재그로 **가장 최근에 완성된 상승 구간**과 **현재 진행 중인 하락 구간**을 잡는다.

```text
zz_th   = max(zigzag_min_pct, zigzag_atr_mult × ATR14_PCT)      # 기본 max(8%, 3×ATR%)
pivots  = zigzag(high/low, zz_th)                                # 반대 방향 zz_th 이상 움직여야 피벗 확정

현재가 하락 구간 진행 중일 때:
  H = 마지막 확정 고점 피벗
  L = H 직전 저점 피벗                    # 최근 상승 구간 L→H
  P = H 이후 최저가 (진행 중 눌림 저점)
  L_prev, H_prev = 그 이전 피벗 (큰 급등 반납 판정용)

rally_pct     = H / L − 1
retrace       = (H − P) / (H − L)
fib(r)        = H − r × (H − L)
major_retrace = (H_prev − L) / (H_prev − L_prev)   # H_prev > H 일 때만
```

| 샘플 | L_prev → H_prev | L → H | P (현재) | 판정 |
|---|---|---|---|---|
| DL이앤씨 | 41k → 106.6k | 58k → 89k (+53%) | ≈74.8k (retrace ≈46%) | major 74% ✓ → S3 |
| 대한해운 | 1,597 → 3,385 | 1,786 → ≈2,200 (+23%) | ≈2,050 | major 89% ✗ → DEMOTED |
| 무림P&P | — | — | — | G1 추세 실패 → S1 |
| 샌디스크(7월) | — | 517 → 2,354 (+355%) | ≈1,050 (retrace 71%) | S3 |
| 아가방컴퍼니(8월 말) | — | 3,115 → ≈5,150 (+65%) | ≈4,150 (retrace ≈49%, 60선 지지) | S3 (단 계획 리스크 ≈20% → `RISK_TOO_WIDE`, §15 Q3) |
| 아가방컴퍼니(10/02) | 3,115 → ≈5,150 | ≈4,150 → 5,240 (+26%) | 신고가 부근 | 다음 눌림은 `RALLY_WEAK` (§15 Q9) |

수치는 캡처 육안값. 픽스처는 실제 `ka10081` 데이터로 재확정한다(§12).

### 3.3 공포 점수 (Fear Score)

"떨리는 자리"를 숫자로 잰다. 항목당 1점, 0~6점.
장중에는 현재가·누적 거래량으로 **잠정 계산**, 장 마감 후 확정값을 기록한다.

| 코드 | 항목 | 조건 | 의미 |
|---|---|---|---|
| F1 | RSI 과매도 | RSI14 ≤ `fear_rsi` (35) | 단기 매도 과잉 |
| F2 | 밴드 이탈 | 가격 < BB 하한 | 통계적으로 드문 하락 |
| F3 | 이격 과대 | 가격 ≤ MA20 × (1 − `fear_ma20_gap_pct`/100) (ENV 하한, 10%) | 20일 평균 대비 과도한 하락 |
| F4 | 연속 하락 | 연속 음봉 ≥ 3일 OR 5일 수익률 ≤ −`fear_5d_pct` (10%) | 쉬지 않고 밀림 |
| F5 | 투매 거래량 | 음봉 AND 거래량 ≥ VOL_MA20 × `fear_vol_mult` (2.0). 장중은 시간 비례 환산 | 공포에 던지는 물량 |
| F6 | 시장 동반 급락 | 지수 당일 ≤ `fear_index_pct` (−1.5%) OR 5일 ≤ −5% | "남들이 다 패대기" |

```text
fear_score = F1 + F2 + F3 + F4 + F5 + F6
FEAR_L1/L2 조건: fear_score ≥ min_fear_score (기본 2)
```

**공포 점수는 매수 허가증이지 매수 이유가 아니다.** 구조 필터(G1~G5)를 통과한 종목에만 적용한다. 구조가 망가진 종목의 공포(무림P&P)는 점수가 높아도 사지 않는다.

**공포 없이 조용히 계획가에 닿으면?** 기본은 매수하지 않고 기다린다(`quiet_touch=wait`). 다음 날 이후 공포가 붙거나, 반등해 L3 확인 매수로 넘어간다. `quiet_touch=buy_half`면 L1을 절반만 산다(§15 Q10).

### 3.4 매수 계획서

장 마감 후 S3 ∩ G1~G5 통과 종목에 대해 생성한다. **공포가 오기 전에 가격·수량·손절을 정해 둔다.**

```text
plan_l1 = fib 0.382~0.5 구간에서 confluence가 가장 많이 겹친 가격 (없으면 fib 0.5)
plan_l2 = fib 0.618~0.70 구간에서 confluence가 가장 많이 겹친 가격 (없으면 fib 0.618)
stop    = round(fib(stop_retrace) × (1 − stop_buffer_pct/100))     # 기본 0.786, 1%
prev_high = H                                                      # 참고 표시용. 매도 기준 아님
avg_ref = plan_l1 × leg1 + plan_l2 × leg2 + MA20 × leg3 (가중 평균, 사이징용)
qty     = §6 사이징
```

- 첫 체결 전에는 장 마감마다 재계산할 수 있다(H·L은 그대로, confluence 가격만 갱신).
- **첫 체결(L1) 이후 plan_l2·stop은 고정.** 장중 공포에 계획을 바꾸지 않는다.
- 현재가가 이미 plan_l1 아래면(장 마감 후 생성 시점) 익일 장중 첫 폴링에서 F 조건 충족 시 매수.

**텔레그램 계획서 예 (DL이앤씨, 육안값):**

```text
[스윙 눌림 계획] DL이앤씨 (375500) — S3, 되돌림 46%
상승 구간 58,000 → 89,000 (+53%) / 지지: MA120 · FIB_500 · 수평 73k
1차 73,500 (30%) · 2차 69,800 (30%) · 확인 MA20 회복 (40%)
손절 64,000 종가 이탈 (1·2차 평균 대비 약 −11%, 3차 포함 평균 대비 약 −15%)
매도 20선·10선 위 종가로 무장 후 → 10선 종가 이탈 50% · 20선 종가 이탈 전량 (전고 89,000은 참고)
공포 점수 매수 조건 ≥ 2 / 현재 1 (RSI 41, BB 하한 근접)
```

### 3.5 L0 프리필터 · 유니버스

**탐색 범위는 전종목(코스피+코스닥)이다.** 다만 비싼 분류(320봉 조회·지그재그)는 전종목에 돌리지 않고, 2단계 깔때기로 줄인다.

```text
[1단계 — 전종목, API 호출 없음, DB 조회만]
  전종목 ≈ 2,500 (fundamental_snapshots 최신일 = 네이버 시가총액 전 페이지)
    → technical_snapshots 최신 1D 로 프리필터
         ma120 > ma120(20거래일 전 스냅샷)                   # 120선 우상향
         AND close ≥ ma120 × (1 − prefilter_ma120_gap_pct/100) # 기본 15%: 공포로 120선을 깬 종목도 포함
         AND avg_trading_value_20d ≥ min_trading_value_20d
         AND close ≥ min_price
    ∪ HTS 조건식 swing_condition_names 편입 종목 (옵션)
    ∪ watchlist_codes
    ∪ 기존 장부(swing_universe) 종목                          # WAIT_ZONE·MANAGE 는 프리필터 면제
    − 제외 종목 (_is_screener_stock)
    → |close / ma120 − 1| 작은 순(120선에 가까운 순)으로 max_l0_candidates(150) trim

[2단계 — 후보 ≤ 150, ka10081 320봉]
  G1 추세 → 지그재그 → S1/S2/S3/DEMOTED 분류 → 계획서
```

> `close > ma120`으로 거르면 **공포 구간 종목이 빠진다.** DL이앤씨(10/02 74.8k, 120선 ≈ 76~77k)와 샌디스크 7월 바닥(120선 아래)이 모두 탈락한다. 그래서 120선 아래 15%까지 허용하고, 정밀 판정은 2단계 G1(240선·120일 전 종가·주봉)에 맡긴다.
>
> trim 기준을 거래대금 상위로 하면 거래가 말라 가는 눌림 종목이 밀려난다. 120선 근접 순으로 자른다.

예상 규모: L0 ≤ 150, L1 S3 10~40, L2 장부(계획서) 3~15. **장부 30개 초과 시 confluence 조건이 느슨한 것.**

### 3.6 `swing_universe` (L2, P0 인메모리+JSON / P1 테이블)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `strategy_id` | FK | |
| `stock_code` | str | PK 일부 |
| `stage` | str | `S1` \| `S2` \| `S3` \| `DEMOTED` |
| `state` | str | `WAIT_ZONE` \| `MANAGE_L1` \| `MANAGE_L2` \| `MANAGE_FULL` \| `MANAGE_HALF` \| `DONE` |
| `armed` | bool | 종가 > MA20·MA10 성립 후 true (이평 매도 활성) |
| `armed_date` | date | 무장일 |
| `ma10_sold` | bool | 10선 절반 매도 완료 |
| `pivot_l` / `pivot_h` / `pivot_p` | float | 지그재그 L, H, 눌림 저점 P |
| `pivot_h_date` | date | H 확정일 |
| `rally_pct` / `retrace` / `major_retrace` | float | |
| `confluence` | json | 겹친 지지 목록 (`["MA120","FIB_500"]`) |
| `plan_l1` / `plan_l2` | int | 계획가 |
| `plan_locked` | bool | 첫 체결 후 true |
| `stop_price` | int | 구조 손절가 |
| `prev_high` | int | 전고 H (참고 표시용) |
| `fear_score_last` | int | 최근 확정 공포 점수 |
| `entry_leg` | int | 0~3 |
| `last_leg_date` | date | 하루 1차수 제한 |
| `l1_date` | date | 시간 손절 기준 |
| `setup_date` / `expire_date` | date | `setup_date + setup_expire_days` |

유니크: `(strategy_id, stock_code, pivot_h_date)`. 같은 H로 사이클 1회. 새 H가 확정되면 새 사이클.

### 3.7 구멍 점검

| # | 구멍 | 막기 |
|---|------|------|
| H1 | 전종목 `ka10081` 호출 → 한도·시간 초과 | `technical_snapshots` 프리필터 후 ≤ 150종목만 |
| H2 | 진행 중 일봉으로 계획 생성 | 계획·확인 매수는 15:40 이후 확정봉만. 장중은 계획가 터치 + 공포 점수 잠정값만 |
| H3 | "최근 N일 최고"로 파동 오인 (DL 4월 고점) | 지그재그 확정 피벗 (§3.2) |
| H4 | 고점 대비 고정 %로 눌림 판단 → 고변동 종목 누락 (샌디스크) | 되돌림 비율 38.2~78.6% |
| H5 | 120선 지금은 상승이나 곧 꺾임 (대한해운) | `close > close[120]` 필수 |
| H6 | 일봉만 예쁜 장기 하락 종목 (무림P&P) | 주봉 60주선, (P1) 월봉 20개월선 |
| H7 | 환호 구간 추격 (샌디스크 10/02) | `CHASE_BLOCK` 3종 (L3 확인 매수에 적용) |
| H8 | 공포 매수가 떨어지는 칼 | 구조 필터 통과 종목만 + 손절가 사전 고정 + 1차 30% + 하루 1차수 |
| H9 | 장중 공포에 계획 수정 (손절가 내리기·추가 차수 신설) | `plan_locked` 후 plan_l2·stop 변경 금지. 차수는 3개 고정 |
| H10 | 글로벌 트레일링이 스윙 포지션을 며칠 만에 자름 | 본 전략 포지션은 글로벌 트레일·`take_profit_rate` 무시 |
| H11 | `flatten_eod`/당일 청산 로직에 걸림 | `flatten_eod=false` 고정, `max_hold_days=40` |
| H12 | 2차가 사실상 물타기 | 2차 가격·손절가는 1차 **전에** 계획서에 확정. 손절가 위에서만, 1회 한정 |
| H13 | 단타 전략과 같은 종목 이중 보유 | HOLDING/PENDING이면 skip (`ALREADY_IN_POSITION`) |
| H14 | 신규 상장 등 데이터 < 320봉 | `INSUFFICIENT_BARS` skip |
| H15 | 장중 꼬리(투매 윅)에 손절 | 구조 손절은 **종가** 기준. 장중은 하드 손절(stop −3%)만 |
| H16 | **기존 시장 리스크 게이트가 급락장 매수를 막음** | `strategy_limited_when_bad`는 미등록 전략을 `market_risk_block_legacy`(기본 True)로 묶어 하락장 2회 제한. `check_crash_sync_pullback`은 지수 연동 눌림을 차단. **본 전략은 둘 다 예외** — 시장 급락은 차단 사유가 아니라 공포 점수 F6 가점 |
| H17 | 급락일 하루에 1·2차가 모두 체결 | `one_leg_per_day=true`. 2차는 다음 거래일 이후 |
| H18 | 시가 갭 하락으로 계획가보다 훨씬 아래에서 시작 | 시가 ≤ plan가면 시가 근처 체결(더 유리). 시가 ≤ stop이면 `GAP_BELOW_STOP` → 계획 폐기 |

---

## 4. 프로필

**시드:** `strategy_name=스윙 눌림`, `strategy_type=SWING_PULLBACK`, `strategy_key=swing_pullback`, `is_enabled=false`

```json
{
  "exec_tf": "1D",
  "min_bars": 320,
  "max_l0_candidates": 150,
  "min_trading_value_20d": 3000000000,
  "min_price": 1000,
  "prefilter_ma120_gap_pct": 15.0,

  "require_ma120_up": true,
  "ma120_slope_days": 20,
  "require_above_ma240": true,
  "require_above_close_120ago": true,
  "require_weekly_ma60": true,
  "require_monthly_ma20": false,

  "zigzag_min_pct": 8.0,
  "zigzag_atr_mult": 3.0,
  "min_rally_pct": 30.0,
  "impulse_vol_ratio": 1.3,
  "major_retrace_max": 0.80,
  "min_pullback_bars": 3,

  "zone_retrace_min": 0.382,
  "zone_retrace_max": 0.786,
  "plan_l1_band": [0.382, 0.5],
  "plan_l2_band": [0.618, 0.70],
  "min_confluence": 2,
  "confluence_tol_pct": 2.5,

  "entry_mode": "fear_scale",
  "min_fear_score": 2,
  "fear_rsi": 35.0,
  "fear_ma20_gap_pct": 10.0,
  "fear_5d_pct": 10.0,
  "fear_vol_mult": 2.0,
  "fear_index_pct": -1.5,
  "quiet_touch": "wait",
  "one_leg_per_day": true,
  "exempt_market_risk_gate": true,

  "confirm_rsi_min": 50.0,
  "confirm_macd": "golden_or_hist_up",
  "reversal_vol_mult": 1.5,

  "chase_day_pct": 7.0,
  "chase_rsi": 65.0,
  "chase_env_upper": true,
  "max_gap_up_pct": 4.0,

  "leg1_pct": 30.0,
  "leg2_pct": 30.0,
  "leg3_pct": 40.0,

  "stop_retrace": 0.786,
  "stop_buffer_pct": 1.0,
  "intraday_hard_buf_pct": 3.0,
  "max_stop_pct": 15.0,

  "exit_mode": "ma_trail",
  "exit_ma_fast": 10,
  "exit_ma_slow": 20,
  "ma10_exit_frac": 0.5,
  "ma10_exit_requires_aligned": true,
  "ma_exit_close_days": 1,
  "ma_exit_buffer_pct": 0.0,
  "breakeven_after_ma10": true,
  "exit_fill": "next_open",

  "time_stop_days": 15,
  "max_hold_days": 60,
  "setup_expire_days": 20,

  "confirm_entry_fill": "next_open",
  "flatten_eod": false,
  "risk_per_trade_pct": 1.0,
  "max_positions": 5,
  "max_position_pct": 20.0
}
```

| 키 | 기본 | UI | 의미 |
|----|------|----|----|
| `min_trading_value_20d` | 30억 | Y | 유동성 하한 |
| `prefilter_ma120_gap_pct` | 15 | Y | 1단계 프리필터에서 120선 아래 허용 폭 (공포 구간 종목 포함용) |
| `require_above_close_120ago` | true | Y | 120선 미래 기울기 (대한해운 차단) |
| `require_weekly_ma60` | true | Y | 상위 TF 상승 (무림P&P 차단) |
| `min_rally_pct` | 30 | Y | 직전 상승 구간 최소 폭 |
| `major_retrace_max` | 0.80 | Y | 큰 급등 반납 한도. 넘으면 DEMOTED |
| `plan_l1_band` / `plan_l2_band` | 0.382~0.5 / 0.618~0.70 | Y | 1·2차 계획가를 찾는 되돌림 구간 |
| `min_confluence` | 2 | Y | 겹쳐야 하는 지지 수 |
| `entry_mode` | fear_scale | Y | `fear_scale`(공포 매수) \| `reversal`(반전봉 확인, P1 비교용) |
| `min_fear_score` | 2 | Y | 1·2차 매수 허가 공포 점수 |
| `quiet_touch` | wait | Y | 공포 없이 계획가 도달 시 `wait` \| `buy_half` |
| `one_leg_per_day` | true | Y | 하루 1차수 (시간 분할) |
| `exempt_market_risk_gate` | true | N | 시장 리스크 게이트·급락 연동 차단 예외 |
| `chase_day_pct` / `chase_rsi` | 7 / 65 | Y | 확인 매수 추격 방지 |
| `leg1/2/3_pct` | 30/30/40 | Y | 분할 비중 |
| `stop_retrace` | 0.786 | Y | 구조 손절 되돌림 |
| `max_stop_pct` | 15 | Y | 평균 단가 대비 손절 거리 상한. 넘으면 계획 생성 안 함 |
| `exit_mode` | ma_trail | Y | `ma_trail`(10·20선 매도) \| `targets`(전고·확장, 비교용) |
| `exit_ma_fast` / `exit_ma_slow` | 10 / 20 | Y | 매도 기준 이평 |
| `ma10_exit_frac` | 0.5 | Y | 10선 이탈 시 매도 비율 |
| `ma10_exit_requires_aligned` | true | Y | 10선 매도는 MA10 > MA20 일 때만 |
| `ma_exit_close_days` | 1 | Y | 종가 이탈 확인 일수 (2 = 이틀 연속) |
| `ma_exit_buffer_pct` | 0 | Y | 이평 아래 허용 폭 (살짝 깨는 것 무시) |
| `breakeven_after_ma10` | true | Y | 10선 절반 매도 후 손절가 본전 이동 |
| `time_stop_days` | 15 | Y | L1 후 무장(20선·10선 위 종가) 못 하면 정리 |
| `max_hold_days` | 60 | Y | 만기 (0 = 끔) |
| `risk_per_trade_pct` | 1.0 | Y | 손절 시 계좌 손실 % |
| `max_positions` | 5 | Y | 동시 스윙 종목 수 |

---

## 5. 상태머신 · 칼날 게이트

```text
IDLE → S2(관찰) → WAIT_ZONE(계획서) → MANAGE_L1 → MANAGE_L2 → MANAGE_FULL
     → (armed) → MANAGE_HALF(10선 절반 매도) → DONE(20선 전량)
MANAGE_L1 → (L2 없이 확인) → MANAGE_FULL
DEMOTED / STRUCTURE_BROKEN / SETUP_EXPIRED → DONE (새 H 확정 전까지 재진입 없음)
```

스킵은 BUY 없이 `reason`만 남긴다.

**계획 게이트 (장 마감 후)**

| 게이트 | 조건 (실패 → reason) | 전이 |
|--------|----------------------|------|
| G0 데이터 | 봉 수 ≥ `min_bars` | `INSUFFICIENT_BARS` |
| G1 추세 | `MA120[t] > MA120[t-20]` / `close > MA240` / `close > close[t-120]` / 주봉 `close_w > MA60_w` | `MA120_DOWN` / `BELOW_MA240` / `MA120_ROLLOFF` / `WEEKLY_DOWN` |
| G2 급등 | `rally_pct ≥ min_rally_pct` AND 상승 구간 평균 거래량 ≥ L 이전 60일 평균 × `impulse_vol_ratio` | `RALLY_WEAK` / `IMPULSE_NO_VOLUME` |
| G3 반납 | `major_retrace < major_retrace_max` (H_prev 존재 시) | `IMPULSE_GIVEN_BACK` → DEMOTED |
| G4 눌림 구간 | H 이후 ≥ `min_pullback_bars`, `retrace ≤ zone_retrace_max`, 현재가가 plan_l1 의 +5% 이내 | `PULLBACK_SHALLOW` (대기) / `STRUCTURE_BROKEN` → DONE |
| G5 confluence | plan_l1 또는 plan_l2 근처(±`confluence_tol_pct`)에 지지 `min_confluence`개 이상 | `NO_CONFLUENCE` |
| G6 리스크 | `(avg_ref − stop) / avg_ref ≤ max_stop_pct` | `RISK_TOO_WIDE` |
| G7 중복·슬롯 | HOLDING/PENDING 아님, 보유 + 계획 < `max_positions` | `ALREADY_IN_POSITION` / `SLOT_FULL` |

G1~G7 통과 → 계획서 생성, `WAIT_ZONE`.

> G1 추세 판정은 **계획 생성 시점**에만 한다. 이미 WAIT_ZONE·MANAGE 중인 종목이 공포로 MA120·MA240을 잠깐 깨는 것은 정상이다. 그 이후는 손절가(78.6%)만 본다.

**공포 매수 게이트 (장중, WAIT_ZONE / MANAGE_L1)**

| 게이트 | 조건 (실패 → reason) |
|--------|----------------------|
| F-G1 계획가 | 현재가 ≤ plan_l1 (L1) / ≤ plan_l2 (L2) → 아니면 `NOT_AT_PLAN` |
| F-G2 공포 | `fear_score_live ≥ min_fear_score` → 아니면 `NO_FEAR` (`quiet_touch=wait`) |
| F-G3 손절 위 | 현재가 > stop → 아니면 `BELOW_STOP` (매수 없음, 보유분은 청산 규칙) |
| F-G4 하루 1차수 | `last_leg_date ≠ 오늘` → 아니면 `LEG_TODAY_DONE` |
| F-G5 시장 게이트 | `exempt_market_risk_gate=true` → 통과. false면 기존 `check_market_risk_buy_allowed` |

**확인 매수 게이트 (장 마감 후, MANAGE_L1 / MANAGE_L2)**

| 게이트 | 조건 (실패 → reason) |
|--------|----------------------|
| C-G1 확인 | §5.1 confirm → 아니면 `NO_CONFIRM` |
| C-G2 추격 | 당일 등락 < `chase_day_pct` AND RSI < `chase_rsi` AND (close ≤ ENV 상단) → 아니면 `CHASE_BLOCK` |
| C-G3 익일 갭 | `open ≤ signal_close × (1 + max_gap_up_pct/100)` → 아니면 `GAP_CHASE` |

**confluence 후보 (G5)**

| 코드 | 지지 |
|---|---|
| `MA120` | 120일선 |
| `MA60` | 60일선 |
| `BB_LOWER` | 볼린저 하한 (20,2) |
| `ENV_LOWER` | 엔벨로프 하한 (MA20 × 0.9) |
| `KIJUN` | 일목 기준선 |
| `FIB_382` / `FIB_500` / `FIB_618` | 상승 구간 되돌림 |
| `PREV_HIGH` | (P2) 급등 이전 박스 상단 수평선 |

### 5.1 진입 신호

```text
# leg 1 — FEAR_L1 (장중, WAIT_ZONE)
fear_l1  = last ≤ plan_l1
       AND fear_score_live ≥ min_fear_score
       AND last > stop
       → 매수 30%, plan_locked = true, l1_date = 오늘

# leg 2 — FEAR_L2 (장중, MANAGE_L1, 다음 거래일 이후)
fear_l2  = last ≤ plan_l2
       AND fear_score_live ≥ min_fear_score
       AND last > stop
       AND last_leg_date < 오늘
       → 매수 30%

# leg 3 — CONFIRM_MA20 (장 마감 후, MANAGE_L1 또는 MANAGE_L2)
confirm  = close > MA20
       AND close > KIJUN
       AND RSI ≥ confirm_rsi_min
       AND (MACD 골든크로스 최근 3봉 내 OR hist 2봉 연속 증가)
       AND C-G2 통과
       → 익일 시가 40% (L2 미체결이면 70%)
```

**L1 없이 L3 단독 진입 불가.** 공포 자리를 거치지 않고 20선 돌파만 사는 것은 본 전략이 아니다.

`entry_mode=reversal`(P1 비교용): FEAR_L1 대신 "구간 내 반전봉(양봉·종가 > MA5·거래량 ≥ VOL_MA20 × 1.5) 확정 → 익일 시가"로 1차 진입. 백테스트로 공포 매수와 성과를 비교한다.

---

## 6. 진입 · 청산 · 사이징

### 진입

- 1·2차: 장중 계획가 도달 + 공포 점수 → 현재가 매수 (P1: 거래소 지정가 예약).
- 3차: 장 마감 후 확인 → 익일 시가 (`confirm_entry_fill=next_open`).
- 같은 사이클(`pivot_h_date`)에 leg당 BUY 1회. 수익 중 추가 매수(피라미딩) 없음.

### 손절

```text
stop_price  = 계획서 stop (L1 체결 시 고정)
hard_price  = round(stop_price × (1 − intraday_hard_buf_pct/100))

STOP_STRUCT : 일봉 종가 < stop_price           → 익일 시가 전량 (투매 꼬리는 무시)
STOP_HARD   : 장중 현재가 ≤ hard_price         → 즉시 전량 (진짜 붕괴 방어)
EXIT_MA10 체결 후 breakeven_after_ma10 → stop_price = max(stop_price, 평균 단가)
```

### 매도 — 10일선·20일선 (`exit_mode=ma_trail`, 기본)

가격 목표(전고·확장)로 팔지 않는다. **추세가 살아 있는 동안 들고 가고, 이평선이 깨지면 판다.**

공포 매수는 정의상 **20선 아래**에서 산다(F3: MA20 −10%). 그래서 진입 직후부터 이평 매도를 켜면 사자마자 팔게 된다.
이평 매도는 **가격이 이평선 위로 올라온 뒤에만 무장(arm)** 한다.

```text
[무장 전 — 진입 ~ 20선 회복 전]
  이평 매도 없음. STOP_HARD / STOP_STRUCT / TIME_STOP 만 적용

[무장 — ARMED]
  armed = 종가 > MA20 AND 종가 > MA10       # 확인 매수(L3) 조건과 같은 날 성립하는 경우가 많음
  armed 이후 영구 유지 (사이클 종료까지)

[무장 후 매도]
  EXIT_MA10 : MA10 > MA20 (정배열) 이고 종가 < MA10 × (1 − ma_exit_buffer_pct/100)
              → 보유의 ma10_exit_frac (50%) 매도. 사이클당 1회
  EXIT_MA20 : 종가 < MA20 × (1 − ma_exit_buffer_pct/100)
              → 잔량 전량
  (ma_exit_close_days = 1 기본. 2면 이틀 연속 이탈 시)
  체결 = 익일 시가 (exit_fill=next_open)
```

| 규칙 | 의미 |
|---|---|
| 10일선 이탈 → 절반 | 단기 탄력이 꺾임. 수익 일부 확정 |
| 20일선 이탈 → 전량 | 스윙 추세 종료 |
| 10선 규칙은 **정배열(MA10 > MA20)일 때만** | 막 회복하는 구간(MA10 < MA20)에서는 10선이 20선 아래라 노이즈가 크다. 그 구간은 20선만 본다. 확인 매수 직후 10선 흔들림에 절반이 털리는 것 방지 |
| 한 번 판 절반은 다시 사지 않음 | 10선 재돌파 재매수 없음 (피라미딩 금지와 동일 원칙) |
| 무장 후에도 STOP_STRUCT 유지 | 20선이 손절가보다 아래인 초기 구간의 바닥 |

`exit_mode=targets`(비교용, 0.2 설계): 전고 1/3 → 1.272 확장 1/3 → 20선 종가 2일 이탈. 백테스트로 이평 매도와 비교한다.

### 시간·만기

```text
TIME_STOP : l1_date 이후 time_stop_days(15)거래일 동안 무장(armed) 못 함 → 전량
MAX_HOLD  : l1_date 이후 max_hold_days(60)거래일 → 잔량 전량 (0 = 끔. 이평 매도가 주 청산)
```

### 우선순위 (같은 날 복수 조건)

| 우선 | reason | 수량 |
|---|---|---|
| 1 | `STOP_HARD` | 전량 (장중) |
| 2 | `STOP_STRUCT` | 전량 |
| 3 | `EXIT_MA20` | 잔량 전량 |
| 4 | `EXIT_MA10` | 50% |
| 5 | `TIME_STOP` | 전량 |
| 6 | `MAX_HOLD` | 잔량 |

같은 날 종가가 10선·20선을 모두 깨면 `EXIT_MA20` 전량 (절반 매도를 따로 하지 않음).

### 사이징

```text
avg_ref     = 계획서 가중 평균 진입가
risk_amount = equity × risk_per_trade_pct / 100
qty_full    = floor(risk_amount / (avg_ref − stop_price))
qty_full    = min(qty_full, floor(equity × max_position_pct / 100 / avg_ref),
                  floor(max_invest_amount / avg_ref))
qty_leg     = allocate(qty_full, leg1_pct / leg2_pct / leg3_pct)
```

수량은 계획서 생성 시 확정, `additional_data.suggested_qty` 우선 (MA1592와 동일 경로, `BuyOrderExecutor`).

---

## 7. `additional_data` (BUY)

```json
{
  "strategy": "swing_pullback",
  "reason": "FEAR_L1",
  "leg": 1,
  "qty_frac": 0.3,
  "stage": "S3",
  "pivot_l": 0, "pivot_h": 0, "pivot_p": 0,
  "pivot_h_date": "",
  "rally_pct": 0, "retrace": 0, "major_retrace": 0,
  "confluence": ["MA120", "FIB_500"],
  "fear_score": 0,
  "fear_items": ["F1_RSI", "F2_BB", "F6_INDEX"],
  "plan_l1": 0, "plan_l2": 0,
  "suggested_stop": 0,
  "hard_stop": 0,
  "prev_high": 0,
  "exit_mode": "ma_trail",
  "suggested_qty": 0,
  "max_hold_days": 40,
  "market_risk_exempt": true
}
```

SELL: `reason` = `STOP_HARD` \| `STOP_STRUCT` \| `EXIT_MA20` \| `EXIT_MA10` \| `TIME_STOP` \| `MAX_HOLD`

---

## 8. 이벤트 / reason_code

계획: `INSUFFICIENT_BARS`, `MA120_DOWN`, `BELOW_MA240`, `MA120_ROLLOFF`, `WEEKLY_DOWN`, `RALLY_WEAK`, `IMPULSE_NO_VOLUME`, `IMPULSE_GIVEN_BACK`, `PULLBACK_SHALLOW`, `STRUCTURE_BROKEN`, `NO_CONFLUENCE`, `RISK_TOO_WIDE`, `ALREADY_IN_POSITION`, `SLOT_FULL`, `SETUP_EXPIRED`, `PLAN_CREATED`

공포 매수: `NOT_AT_PLAN`, `NO_FEAR`, `BELOW_STOP`, `LEG_TODAY_DONE`, `GAP_BELOW_STOP`, `FEAR_L1`, `FEAR_L2`

확인 매수: `NO_CONFIRM`, `CHASE_BLOCK`, `GAP_CHASE`, `CONFIRM_MA20`, (`ZONE_REVERSAL` — reversal 모드)

청산: `ARMED`(이벤트), `STOP_HARD`, `STOP_STRUCT`, `EXIT_MA20`, `EXIT_MA10`, `TIME_STOP`, `MAX_HOLD`

---

## 9. UI · 알림 (최소)

- 타입 `SWING_PULLBACK`, 표시명 `스윙 눌림`
- 폼: 유동성, 추세 필터 토글, 최소 상승%, 반납 한도, 계획가 구간, confluence 수, 공포 점수 하한·항목 임계값, quiet_touch, 추격 3종, 분할 비중, 손절 되돌림, 손절 상한, 매도 이평(10/20)·10선 매도 비율·이탈 확인 일수, 시간 손절일, 만기일, 리스크%, 최대 종목 수
- 배지: 관찰(S2) / 계획대기 / 1차보유 / 2차보유 / 전량보유 / 무장(이평 매도 활성) / 절반매도 / 종료
- 대시보드 후보 탭 `스윙 눌림` — 장부(`swing_universe`)만. 단계·되돌림·confluence·**계획가·손절가·목표가·현재 공포 점수** 표시
- **텔레그램** (기존 `notifications/telegram_notifier.py`):
  - 계획서 생성·갱신 (§3.4 형식)
  - 공포 점수가 하한에 근접(하한 −1)하고 현재가가 plan_l1 +3% 이내 → "떨리는 자리 접근" 사전 알림
  - FEAR_L1/L2 체결 — **계획대로 실행했다**는 메시지와 손절가·다음 차수 함께 표시
  - 청산 체결
- 매도 표시: `10선 이탈 50% · 20선 이탈 전량` + 현재 MA10·MA20 가격과 종가 거리(%) — % 목표 아님
- 텔레그램: 무장(`ARMED`) 알림, 종가가 10선·20선 1% 이내로 접근 시 장 마감 전(15:00) 경고
- 단타 전략과 동시 ON 시 같은 종목 이중 보유 경고
- 차트 마커(L·H·P 피벗, 계획가, 손절선, 목표선)는 P2

---

## 10. 비기능 · 백테스트

| ID | 기준 |
|----|------|
| NFR-1 | 동일 일봉 + params → 동일 계획서·시그널 (결정적) |
| NFR-2 | 일봉 배치 전체 ≤ 15분 (L0 150종목 기준, 캐시 히트 시 ≤ 3분) |
| NFR-3 | 장중 폴링은 장부 종목만, 스캐너 주기 내 ≤ 1s/종목 |
| NFR-4 | skip/exit reason 구조화 로그 |
| NFR-5 | 시드 OFF, Paper/mock 기본 |
| NFR-6 | 기존 분봉 전략 로직 불변. 시장 리스크 게이트 기본값은 **기존 전략에 대해** 불변 |
| NFR-7 | 글로벌 트레일·`take_profit_rate`·EOD 청산이 본 전략 포지션에 적용되지 않음 |

백테스트: 일봉 전략이라 [`BACKTEST_PLAN.md`](./BACKTEST_PLAN.md) **MVP(일봉) 범위에 들어간다.**

- 1·2차 체결: 일봉 low ≤ plan가이고 그날 공포 점수(종가 기준 확정값 또는 전일 값 + 당일 저가 기준 F1~F3) ≥ 하한 → plan가 체결. 시가가 plan가 아래면 시가 체결
- 3차 체결: 신호 익일 시가
- STOP_HARD: 일봉 low ≤ hard_price 이면 hard_price 체결 (시가가 더 낮으면 시가)
- EXIT_MA10 / EXIT_MA20: 종가 판정 → 익일 시가 체결
- 비교: `exit_mode=ma_trail` vs `targets`, `ma_exit_close_days` 1 vs 2, `ma10_exit_requires_aligned` on/off
- STOP_STRUCT: 익일 시가
- 비교: `entry_mode=fear_scale` vs `reversal`, `min_fear_score` 1/2/3, `quiet_touch` wait vs buy_half
- 지표: 승률, 평균 손익비, 평균 보유일, MDD, 연간 거래 수, 계획서 대비 체결률, 시간 손절 비율
- 합격 기준은 수익 수치가 아니라 **§12 규칙 재현 + 샘플 픽스처 판정 일치**

---

## 11. 구현

| Phase | 내용 | 파일 |
|-------|------|------|
| P0 | 계산기 (지표·지그재그·단계·공포 점수·계획서·게이트·청산) + 테스트 | `utils/swing_pullback.py`, `tests/test_swing_pullback.py` |
| P0 | 시드 OFF + `use_swing_pullback` 설정 | `core/models.py` |
| P0 | 장 마감 후 배치 L0→L2 + 계획서 + 확인 매수 | `managers/auto_trade_scanner.py` (`_collect_swing_pullback_targets`, `_collect_ma1592_targets` 패턴 참고) 또는 신규 배치 스크립트 (§15 G-2) |
| P0 | 장중 계획가 폴링 (장부 종목 현재가 + 공포 점수 잠정) | `managers/auto_trade_scanner.py` 스캔 루프에 장부 폴링 추가 |
| P0 | 시장 리스크 예외 | `utils/market_risk_gate.py` — `normalize_strategy_key`에 `swing_pullback` 등록, `strategy_limited_when_bad`/`strategy_limited_when_surge`에 `market_risk_block_swing_pullback`(기본 False), `check_crash_sync_pullback` 호출부에서 본 전략 skip |
| P0 | 매수 + `suggested_qty` | `managers/buy_order_executor.py` (`strategy_key == "swing_pullback"` 분기) |
| P0 | 청산 6종 + 10선 절반 매도 + 글로벌 트레일 무장 금지 | `managers/stop_loss_manager.py` (MA1592 `tp1_frac=0.5` 반익절 경로를 10선 절반 매도에 재사용) |
| P0 | 계획서·체결 텔레그램 | `notifications/telegram_notifier.py` |
| P0 | UI | `static/modules/strategy-manager.js` |
| P1 | `swing_universe` 테이블, 지정가 예약 주문, 월봉 필터, reversal 모드 | `core/models.py`, `api/kiwoom_api.py` |
| P2 | 외부 공포 지표, 실적 캘린더, 업종 RS, 수평 매물대, 차트 마커 | |

```text
on_daily_close(code, bars, p, book):
  if len(bars) < p.min_bars: skip INSUFFICIENT_BARS; return
  ind = compute_indicators(bars)
  row = book.get(code)

  if row and row.state.startswith("MANAGE"):
    if not row.armed and ind.close > ind.ma20 and ind.close > ind.ma10:
      row.armed = True; notify ARMED
    evaluate_exit_close(ind, row, p)                          # STOP_STRUCT / EXIT_MA20 / EXIT_MA10 / TIME / MAX
    if row.entry_leg in (1, 2) and confirm(ind, p) and not chase(ind, p):
      emit BUY CONFIRM_MA20 (next_open)
    return

  if not trend_ok(ind, p): skip <G1 reason>; return
  zz = zigzag_legs(bars, ind, p)
  stage = classify(zz, p)                                     # S1/S2/S3/DEMOTED
  if stage != "S3": skip <reason>; return
  if not near_zone(zz, ind, p): skip PULLBACK_SHALLOW | STRUCTURE_BROKEN; return
  plan = build_plan(zz, ind, p)                               # plan_l1/l2, stop, tp, qty, confluence
  if len(plan.confluence) < p.min_confluence: skip NO_CONFLUENCE; return
  if plan.risk_pct > p.max_stop_pct: skip RISK_TOO_WIDE; return
  book.upsert(code, state="WAIT_ZONE", plan); notify PLAN_CREATED

on_intraday_poll(code, last, cum_volume, index_pct, row, p):  # WAIT_ZONE / MANAGE_L1 만
  if row.plan_locked is False and last <= row.stop: skip BELOW_STOP; return
  leg, plan_px = next_fear_leg(row)                           # (1, plan_l1) | (2, plan_l2) | None
  if leg is None or last > plan_px: return                    # NOT_AT_PLAN
  if row.last_leg_date == today: skip LEG_TODAY_DONE; return
  fs = fear_score_live(row.ind_prev, last, cum_volume, index_pct, p)
  if fs < p.min_fear_score: skip NO_FEAR; return              # quiet_touch=wait
  emit BUY FEAR_L{leg} (market_risk_exempt=true)
```

---

## 12. 수락 기준

- [ ] `SWING_PULLBACK` 프로필 + UI 저장, 기본 OFF
- [ ] 기존 분봉 전략 로직·기존 전략의 시장 리스크 게이트 동작 불변
- [ ] 지표 계산이 HTS 값과 일치 (MA·BB·ENV·기준선·MACD·RSI, 허용 오차 0.5%)
- [ ] 지그재그가 DL이앤씨 2026-10-02 기준 L≈58k, H≈89k를 잡음 (4월 106.6k가 아님)
- [ ] **픽스처 DL이앤씨(375500) 2026-10-02:** S3, 계획서 생성 (plan_l1≈73.5k, plan_l2≈69.8k, stop≈64.0k), 체결 없음 (현재가 > plan_l1)
- [ ] **픽스처 대한해운(005880) 2026-10-02:** `IMPULSE_GIVEN_BACK` 또는 `MA120_ROLLOFF` / `RALLY_WEAK`, 계획서 없음
- [ ] **픽스처 무림P&P(009580) 2026-10-02:** G1 실패 (`BELOW_MA240` / `WEEKLY_DOWN`), 공포 점수가 높아도 계획서 없음
- [ ] 계획가 도달 + 공포 점수 ≥ 2 → FEAR_L1 1회, `plan_locked=true`
- [ ] 계획가 도달 + 공포 점수 < 2 → `NO_FEAR`, 매수 없음 (`quiet_touch=wait`)
- [ ] 같은 날 plan_l2까지 급락해도 2차는 다음 거래일 이후
- [ ] L1 체결 후 plan_l2·stop 변경 불가
- [ ] 지수 −2% 급락일에도 본 전략 FEAR 매수가 시장 리스크 게이트·급락 연동 차단에 막히지 않음, F6 가점 반영
- [ ] 종가 MA20 회복 + 모멘텀 → 익일 시가 CONFIRM_MA20 (L2 미체결 시 70%)
- [ ] 확인일 당일 +7% 이상 또는 RSI ≥ 65 → `CHASE_BLOCK`
- [ ] 장중 꼬리로 stop 하회 후 종가 회복 → 손절 없음. 종가 < stop → 익일 시가 `STOP_STRUCT`
- [ ] 장중 hard_price 하회 → 즉시 `STOP_HARD`
- [ ] 무장 전(종가 < MA20)에는 10선·20선 아래 종가여도 이평 매도 없음
- [ ] 종가 > MA20·MA10 → `ARMED`, 이후 영구 유지
- [ ] 무장 후 MA10 > MA20 이고 종가 < MA10 → 익일 시가 50% `EXIT_MA10`, 사이클당 1회, 손절가 본전 이동
- [ ] 무장 후 MA10 < MA20 구간에서 종가 < MA10 (MA20 위) → 매도 없음
- [ ] 무장 후 종가 < MA20 → 익일 시가 잔량 전량 `EXIT_MA20`
- [ ] 같은 날 종가가 10선·20선 모두 이탈 → `EXIT_MA20` 전량만
- [ ] 15거래일 20선 미회복 → `TIME_STOP`, 40거래일 → `MAX_HOLD`
- [ ] 글로벌 트레일·EOD 청산이 본 전략 포지션에 미적용
- [ ] 동일 데이터 재실행 시 계획서·시그널 동일
- [ ] mock/Paper에서 실주문 어댑터 미호출

픽스처 수치는 캡처 육안값 기반 초안이며, 실제 `ka10081` 데이터로 재확인 후 확정한다.
샌디스크는 해외주식이라 픽스처 제외. 규칙 설계 근거로만 §0.3, §3.2에 남긴다.

---

## 13. 의사결정

| ID | 상태 | 결정 |
|----|------|------|
| D1 | **확정** | 일봉 전략. 계획은 장 마감 후 확정봉으로만, 장중은 계획 실행만 |
| D2 | **확정** | 매수 단계 = S3(급등을 지켜낸 눌림)만. S1 제외, S2 관찰 |
| D3 | **확정** | 눌림 깊이 = 상승 구간 되돌림 비율. 고점 대비 고정 % 사용 안 함 |
| D4 | **확정** | 1·2차는 **공포 매수**(계획가 + 공포 점수). 반전 확인을 기다리지 않음. 확인 매수는 3차 40%만 |
| D5 | **확정** | 120선 미래 기울기(`close > close[120]`) 필수 |
| D6 | **확정** | 환호 구간 추격 금지 3종 (확인 매수에 적용) |
| D7 | **확정** | 오버나잇 보유, 글로벌 트레일 미적용 |
| D8 | **확정** | 분할 30/30/40, 2차는 계획서에 미리 확정된 가격만 (사용자 컨셉 "분할매수 계획") |
| D9 | **확정** | 첫 체결 후 계획 잠금. 장중 공포에 손절가·차수 변경 금지 |
| D10 | **확정** | 시장 리스크 게이트·급락 연동 차단 **예외**. 시장 급락은 공포 점수 가점 |
| D11 | **확정** | 구조 손절은 종가 기준. 장중은 하드 손절만 |
| D12 | 기본 | 공포 점수 하한 2, 조용한 도달은 대기 (§15 Q10) |
| D13 | 기본 | 손절 = 78.6% 되돌림, 상한 15% (§15 Q3) |
| D14 | **확정** | 매도 = 10일선·20일선 (사용자 결정). 무장 후 10선 종가 이탈 50%(정배열일 때만), 20선 종가 이탈 전량. 가격 목표 매도 없음 |
| D15 | 기본 | 리스크 1%/거래, 최대 5종목 (§15 Q5) |
| D16 | 보류 | 유니버스 소스 = `technical_snapshots` 프리필터 vs HTS 조건식 (§15 Q6, G-1) |
| D17 | **확정** | `strategy_key="swing_pullback"`, `strategy_type=SWING_PULLBACK` |

### D4가 묻는 것

반등을 확인하고 사면 마음은 편하지만 이미 늦다. "지나가면 쉬운 자리"가 바로 확인 후의 자리다.
그래서 1·2차(60%)는 공포 한가운데서 계획가에 산다. 대신 확인 없이 사는 위험은 세 가지로 막는다.
구조 필터(망가진 종목 제외), 손절가 사전 고정(틀리면 끝낼 자리), 하루 1차수(하루에 다 물리지 않음).
나머지 40%는 확인 후에 산다. 공포 매수가 맞았다면 평균 단가가 낮고, 틀렸다면 40%를 아낀 상태로 손절한다.

### D9가 묻는 것

계획을 세워도 떨린다. 떨릴 때 사람이 하는 일은 두 가지다. 손절가를 내리거나, 계획에 없던 차수를 더 산다.
둘 다 막는다. 첫 체결 순간 계획이 잠기고, 그 이후는 계획서대로만 움직인다.

### D10이 묻는 것

기존 단타 전략에게 시장 급락은 "오늘은 쉬어라"는 신호다. 그래서 하락장 매수 횟수 제한과 급락 연동 눌림 차단이 있다.
본 전략에게 시장 급락은 "남들이 다 패대기치는 날"이다. 같은 게이트를 쓰면 전략의 존재 이유가 사라진다.
그래서 본 전략만 예외로 두고, 기존 전략의 게이트 동작은 그대로 유지한다.

### D3이 묻는 것

샌디스크는 고점 대비 −55%까지 빠졌다가 회복했고, DL이앤씨는 −16%에서 지지를 받고 있다.
고정 %를 쓰면 고변동 종목의 가장 좋은 자리를 놓친다. 되돌림 비율은 종목의 직전 상승 폭에 자동으로 맞춰진다.

### D5가 묻는 것

120일선이 지금 상승 중이어도, 120거래일 전 가격이 현재가보다 높으면 그 고가 날짜가 빠지면서 선이 꺾인다.
대한해운은 4월 급등분(3,000원대)이 10월부터 120선 계산에서 빠진다. 지지선이 사라지는 자리다.

---

## 14. 변경 이력

| 버전 | 일자 | 내용 |
|------|------|------|
| 0.1.0 | 2026-10-03 | 초안. 캡처 4종 검토 결과로 단계 분류·지그재그·되돌림 구간·confluence·추격 방지·3단 분할·구조 손절 정의 |
| 0.2.0 | 2026-10-03 | 사용자 컨셉 "떨리는 자리가 기회의 자리" 반영. 1·2차를 반전봉 확인 → **공포 점수 + 사전 계획가 매수**로 전환. 매수 계획서·계획 잠금·하루 1차수·텔레그램 계획서 추가. 시장 리스크 게이트 예외(H16). 눌림 거래량 감소 조건 삭제(투매 거래량은 공포 점수 가점) |
| 0.2.1 | 2026-10-03 | 탐색 범위 명시(전종목 → 2단계 깔때기). 프리필터 `close > ma120` → `close ≥ ma120 × 0.85`(공포 구간 종목 누락 수정), trim 기준 거래대금 → 120선 근접. G-1 기술 마트 배치 스케줄 부재 확인 |
| 0.3.0 | 2026-10-03 | 매도를 **10일선·20일선 기준**으로 전환(사용자 결정, D14). 가격 목표(전고 1/3·확장 1/3) 삭제 → `exit_mode=targets` 비교용으로만 유지. 이평 매도 무장(arm) 규칙, 10선은 정배열일 때만, 만기 40 → 60일. 아가방컴퍼니 샘플 추가(Q3 손절 폭, Q9 계단식 상승) |

---

## 15. 미결 항목 (검토 요청)

### 15.1 전략 판단 (사용자 결정 필요)

| # | 질문 | 선택지 | 초안 |
|---|------|--------|------|
| Q3 | 손절 기준 | (a) 78.6% 되돌림 (b) 61.8% 되돌림 아래 일정 % (c) 2차 계획가 −X% | (a). DL이앤씨 기준 1·2차 평균 대비 약 −11%지만, 3차(MA20 ≈ 80k)까지 넣은 계획 평균(≈75k) 대비 약 −15%로 `max_stop_pct` 경계. 3차를 사이징 평균에서 뺄지, 상한을 올릴지, 3차 비중을 줄일지 결정 필요. **아가방컴퍼니 8월 말**: L=3,115·H≈5,150 → stop ≈3,515, 계획 평균(1차 ≈4,150 + 3차 ≈4,500) ≈4,400 대비 약 −20% → 계획 생성 안 됨. 저점 대비 상승폭이 큰 종목일수록 0.786 손절이 멀어진다. 10·20선 매도로 바뀐 만큼 손절도 (d) 눌림 저점 P 또는 60선 −X% 같은 가까운 기준을 검토 |
| ~~Q4~~ | ~~익절 방식~~ | **확정 (D14): 10일선·20일선** | — |
| Q4-1 | 이평 이탈 확인 | (a) 종가 1일 이탈 (b) 종가 2일 연속 (c) 1일 + 이탈 폭 ≥ 0.5% | (a). 횡보장 휩쏘가 많으면 (c) |
| Q4-2 | 10선 매도 비율 | 50% / 1/3 / 0%(20선만) | 50% |
| Q4-3 | 이평 매도 체결 | (a) 익일 시가 (b) 15:15 현재가 기준 종가 근처 매도 | (a). 갭 하락 위험이 크면 (b) |
| Q5 | 자금 배분 | 리스크 %/거래, 최대 종목 수, 종목당 비중 상한 | 1% / 5종목 / 20% |
| Q6 | 유니버스 | (a) 전종목 마트 프리필터 (b) HTS 조건식 (c) 관심종목만 | (a)+(b) 합집합 |
| Q8 | 단타 전략과 같은 종목 | 스윙 보유 중 단타 진입 허용할지 | 금지 (`ALREADY_IN_POSITION`) |
| Q9 | 최소 상승폭 | 30%가 대형주(건설·금융 등)에 너무 높은지 | 30%. ATR 정규화(상승폭 ≥ 8×ATR%) 대안 검토. **아가방컴퍼니**: 직전 눌림 저점 4,150 → 5,240은 +26%라 다음 눌림이 탈락. 저점이 높아지는 계단식 상승이면 상승 구간을 첫 저점(3,115)부터 잴지 결정 필요 |
| Q10 | 공포 점수 | (a) 하한 2, 조용한 도달 대기 (b) 하한 1 (c) 하한 없음(계획가만) / 조용한 도달 시 절반 매수 | (a). 백테스트로 1/2/3 비교 후 확정 |
| Q11 | 공포 항목 가중치 | 6항목 동일 1점 vs 투매 거래량·시장 급락에 2점 | 동일 1점 (단순하게 시작) |
| Q12 | 시장 전체 폭락장(예: 지수 −8% 서킷) | 예외 없이 계획대로 vs 지수 폭락 시 하루 쉬기 | 계획대로. 단 `max_positions`·리스크%로 총량 제한 |

### 15.2 코드베이스 갭 (개발 착수 전 확인)

| # | 미진한 부분 | 왜 문제인가 | 제안 |
|---|------------|------------|------|
| G-1 | `technical_snapshots` 갱신 스케줄 없음 | 확인 결과 `scripts/technical_mart_batch.py`는 기본값(`--market all --limit 0`)으로 `fundamental_snapshots` 최신일 **전종목**을 적재한다(네이버 시가총액 전 페이지, 기본적분석 배치는 작업 스케줄러 등록됨). 그러나 **기술 마트 배치는 `.bat`·`install_*_task.ps1`이 없어 정기 실행되지 않는다** → 1단계 프리필터가 오래된 데이터를 볼 수 있음. 종목당 `ka10081` 1회라 전종목 수행 시간도 실측 필요 | `run_technical_mart_batch.bat` + `install_technical_mart_batch_task.ps1` 추가(장 마감 후 15:40, 기본적분석 배치 이후). 프리필터는 `as_of_date`가 최근 영업일이 아니면 경고 후 HTS 조건식·관심종목만으로 진행 |
| G-2 | 장 마감 후 배치를 어디서 돌릴지 | `AutoTradeScanner`는 장중 루프 중심 | (a) 스캐너에 15:40 1회 훅 (b) `scripts/swing_pullback_batch.py` + 작업 스케줄러 (기존 `install_*_task.ps1` 패턴) |
| G-3 | `PendingBuySignal` 만료 정책 | 확인 매수(L3)는 장후 생성 → 익일 시가까지 살아 있어야 함 | 만료 필드·`cleanup_scheduler.py` 확인 후 `valid_until=익일 09:10` 지원 |
| G-4 | 분할 매도 경로 | 10선 50% 1회 + 20선 잔량 → MA1592 `tp1_frac=0.5` 반익절 1회 구조와 같다 | MA1592 반익절·`remain_qty` 경로 재사용. 다회 분할 지원은 불필요 |
| G-5 | `ka10081` 응답 봉 수 | 320봉 미만이면 240선·주봉 60주 계산 불가 | 실제 응답 길이 확인, 부족 시 `next-key` 연속 조회 |
| G-6 | 보유일 계산 | 거래일 기준 `hold_days`가 있는지 | MA1592 `hold_days` 재사용 가능 여부 확인 |
| G-7 | 거래소 지정가 예약 주문 | P0는 폴링 → 현재가 매수라 급락 순간 체결가가 계획가와 다를 수 있음 | P1에서 장 시작 전 plan_l1 지정가 주문 + 미체결 취소 관리. 키움 정정·취소 API 확인 |
| G-8 | 시장 리스크 예외 범위 | `check_crash_sync_pullback`은 `crash_sync_block_enabled` 전역 설정, 호출부가 여러 곳일 수 있음 | 호출부 전수 확인 후 `strategy_key == "swing_pullback"`이면 skip. 기존 전략 회귀 테스트 |
| G-9 | 장중 공포 점수 F5 (거래량 시간 비례) | 장 초반 누적 거래량으로 2배 판정이 왜곡 | `cum_volume / (경과 분 / 390) ≥ VOL_MA20 × 2`, 장 시작 30분 이내는 F5 제외 |
