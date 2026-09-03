# PRD: 이평선 + 윌리엄스 프랙탈 눌림목 스캘핑

> **상태**: Draft (설계) — 유니버스 **확정: HTS 조건식** (사용자 제공)  
> **작성일**: 2026-08-15  
> **대상 시스템**: stocke 자동매매 (`AutoTradeScanner` → `BuyOrderExecutor` → `StopLossManager`)  
> **관련 코드**: `managers/auto_trade_scanner.py`, `utils/auto_trade_engine.py` (`evaluate_gate_pack`), `managers/stop_loss_manager.py`, `api/kiwoom_api.py` (`get_stock_chart_data` 1M), `core/models.py` (`AutoTradeSettings`, `Position.strategy_key`)  
> **선행 패턴**: `docs/PRD_SANGTTA_BREAKOUT.md` (게이트 패키지 + `strategy_key` 분리)

---

## 0. 한 줄 결론

**접목 가능하다. 단, 기존 게이트에 끼워 넣으면 안 되고, 6번째 전략 프로필로 분리해야 한다.**

진입 로직(1분봉 EMA 정배열 + 프랙탈 + 20EMA 재돌파)과 청산(50EMA 손절, 1:1.5 익절)은 지금 파이프라인의 **스캐너 → 게이트 패키지 → 주문 → 전략별 청산** 골격에 그대로 올라간다.  
다만 **전 종목 1분봉 프랙탈 스캔은 키움 API 한도 때문에 불가능**하므로, 유니버스는 **HTS 조건식 통과분 → 대금 상위 5종 → 1분봉 게이트** 2단으로 짠다.

**확정 (2026-08-15):** 1단 유니버스는 사용자가 제공할 **키움 HTS 조건식**만 사용한다 (`fractal_condition_names`). 거래대금순 스크리너를 이 전략 풀로 쓰지 않는다. 조건식 통과가 5종을 넘으면 서버가 대금 상위 5만 남긴다.

---

## 1. 전략 프로필

| 항목 | 내용 |
|------|------|
| 전략명 | 이평선 + 윌리엄스 프랙탈 눌림목 스캘핑 |
| `strategy_key` | `fractal` (가칭) |
| 게이트 패키지 | `ema_fractal_pullback` |
| 스타일 | 초단타 / 추세 눌림목 재돌파 |
| 방향 | 롱 위주 (상승 정배열). 숏은 설정 안내만, 1차 비범위 |
| 타임프레임 | **1분봉** (확인은 **종가 확정** 후) |
| ON/OFF | `use_fractal` (기존 `use_breakout` / `use_ymgp` 와 동일 패턴) |

### 1.1 핵심 아이디어

강한 상승(정배열) 안에서 가격이 **20EMA 아래로 눌린 뒤**, 윌리엄스 프랙탈 **매수(녹색)** 가 나오고, **20EMA를 종가로 재돌파**하면 산다.  
20EMA만 스치면 사지 않는다. 역배열·횡보면 매매하지 않는다.

### 1.2 차트 세팅

| 지표 | 값 |
|------|-----|
| EMA | 20 / 50 / 100 (1분봉) |
| Williams Fractal | 표준 5봉(고/저 중심봉 ±2). 매수=녹색, 매도=빨강 |
| 진입가 | 재돌파 확정 캔들 **종가** |
| 손절 | **50EMA 바로 아래** |
| 익절 | 손절 폭 × **1.5** (손익비 1:1.5) |

### 1.3 진입 조건 (AND)

1. 정배열: **종가 > 20EMA > 50EMA > 100EMA** (또는 캡쳐 기준 `20 > 50 > 100`, 구현 시 가격 위치도 AND)
2. 눌림: 최근 구간에서 저가/종가가 **20EMA 하회**
3. 신호: 녹색 프랙탈(저점 프랙탈) 발생 — 프랙탈은 **2봉 지연**이 정상
4. 확인: 이후 봉 **종가가 20EMA 상향 돌파**
5. 실행: 그 봉 종가(또는 다음 봉 시가, 설정)로 매수

### 1.4 청산

- 손절: 진입 시점 50EMA 아래 틱 (동적 추적은 2차)
- 익절: `entry + 1.5 × (entry − stop)`
- 필터 청산: 가격이 **100EMA 이탈**하면 신규 금지. 보유분은 손절/익절 우선, (선택) 정배열 붕괴 시 즉시 청산

### 1.5 리스크 프로필 (실전)

- 1회 리스크: 계좌의 **0.5% ~ 1%**
- 수량: `(계좌 × 리스크%) ÷ (진입가 − 손절가)` → 키움 호가단위·최소수량으로 내림
- 금지: 역배열, 횡보, 20EMA 터치만으로 진입
- 슬롯: `fractal_max_slots` (초단타라도 동시 보유 소수, 예: 1~2)

**체크리스트**

- [ ] 20EMA > 50EMA > 100EMA
- [ ] 가격이 20EMA 하회(눌림)
- [ ] 녹색 프랙탈
- [ ] 20EMA 재돌파 **종가** 확인
- [ ] 손절 = 50EMA 아래 / 익절 = 손절폭 × 1.5

---

## 2. 지금 파이프라인에 붙는가?

### 2.1 결론: **구조는 맞고, 타임프레임·청산·사이징은 새로 짜야 함**

기존 전략은 이미 **유니버스 소스 → `strategy_key` → 게이트 패키지 → 주문 → 전략별 청산** 으로 분리되어 있다.

```
관심종목 + 거래대금순(legacy)
상따(ka10027) / 돌파(조건식) / 역매공파(조건식) / 종가배팅
        │
        ▼  AutoTradeScanner (약 1~2분 주기)
가격 필터 → evaluate_gate_pack(전략별)
        │
        ▼
PendingBuySignal → BuyOrderExecutor → Position.strategy_key
        │
        ▼
StopLossManager (breakout / ymgp / jongga 는 분기 청산)
```

이 전략도 같은 방식 B를 쓴다.

| 레이어 | 재사용 | 신규 |
|--------|--------|------|
| 후보 수집 `_collect_targets` | 패턴 동일 | `source=fractal` 풀 |
| `evaluate_gate_pack` | 분기 추가 | `ema_fractal_pullback` |
| 1분봉 `get_stock_chart_data(..., "1M")` | **이미 있음** | EMA·프랙탈 계산, 캐시 TTL 단축 |
| `BuyOrderExecutor` | 그대로 | 리스크% 기반 수량 (기존은 금액 한도 위주) |
| `StopLossManager` | 루프 재사용 | **가격%가 아닌 50EMA / R:R 1.5** |
| 설정 `use_*` / 슬롯 / 시간창 | 패턴 동일 | `use_fractal`, `fractal_*` |

**기존 게이트에 OR로 끼워 넣으면 안 되는 이유**

| 기존 프로필 | 왜 안 맞나 |
|-------------|------------|
| `legacy` 거래대금 눌림목 | 당일 대금·품질 게이트. 1분 EMA·프랙탈과 무관 |
| `sangtta` | 장초·급등·상한가. 눌림 재돌파와 반대 |
| `breakout` | 조건식 유니버스 + 레벨 돌파. 청산이 구조/트레일 |
| `ymgp` / `jongga` | 수급·종가 특화 |

→ **`strategy_key=fractal` + 전용 유니버스 + 전용 청산.**  
우선순위 제안: `sangtta` > `breakout` > `ymgp` > `fractal` > `legacy` (같은 종목 이중진입 금지).

### 2.2 막히는 지점 (반드시 설계에 넣을 것)

1. **API 한도**  
   `Config.API_MAX_CALLS_PER_MIN`(기본 18) · `API_MIN_CALL_INTERVAL`(기본 3초). 스캐너가 후보 50종목에 매번 1분봉을 받으면 다른 전략까지 굶긴다.  
   → 스크리너 후보 **5종목 고정** (`FRACTAL_CANDIDATE_LIMIT=5`). 1분봉은 이 5종만.
2. **스캐너 주기 vs 스캘핑**  
   지금 스캔은 약 1분. 프랙탈은 2봉 확정 + 재돌파 종가 대기라 **“틱 스캘핑”은 불가**.  
   → 제품 정의는 **1분봉 종가 확정 스캘핑**. 틱/호가 HFT는 Non-goal.
3. **차트 캐시 TTL 5분** (`kiwoom_api._chart_cache_ttl` = 300초, 분봉 공통)  
   1분 전략이면 캐시가 신호를 늦춘다. `fractal` 경로만 TTL 30~60초 또는 봉 마감 무효화.  
   `managers/scalping_strategy.py`는 1분봉을 쓰지만 대시보드 자동매매(`apply_auto_trade_state`)에 연결되어 있지 않고, RSI/BB 실험용이다. **재사용하지 말고** breakout 프로필 패턴을 복사한다.
4. **손절이 % 전역 설정**  
   `StopLossManager` 기본은 `stop_loss_rate` / `take_profit_rate`.  
   이 전략은 **EMA 거리 기반**. `exit_levels`에 `stop_price` / `take_profit_price` 스냅샷 필수.
5. **프랙탈 리페인트**  
   표준 윌리엄스 프랙탈은 좌우 2봉이 있어야 확정. 미확정 프랙탈로 들어가면 가짜 신호가 난다.

---

## 3. 대상 종목(스크리너) — 어떻게 짜야 하나

### 3.1 원칙

**1분 지표로 전 시장을 훑지 않는다.**  
스크리너는 “추세가 살아 있고 유동성이 있는 종목”만 고르고, 프랙탈·재돌파는 **그 소수에만** 계산한다.  
돌파전략과 같은 이유: REST 비용·rate limit (`PRD_OVERSOLD_BREAKOUT.md` 조건식 유니버스와 동일 철학).

```
[1단 유니버스: 값싼 API / HTS 조건식]     ← 전수에 가까운 필터
        │  5종목 cap
        ▼
[2단 게이트: 1분봉 EMA + 프랙탈 + 재돌파]  ← 비싼 차트 조회
        │
        ▼
신호 (strategy=fractal)
```

### 3.2 1단 유니버스 — **확정: HTS 조건식** (`fractal_condition_names`)

사용자가 키움 HTS에서 조건식을 만들어 이름을 등록한다. 상따/돌파/역매공파와 동일: HTS가 전 시장 필터, 서버는 통과 종목 리스트만 받는다. **거래대금순(안 B)은 이 전략 유니버스로 사용하지 않는다.**

**타임프레임 역할 (헷갈리기 쉬운 지점)**

| 단계 | 어디서 | 봉 | 하는 일 |
|------|--------|----|---------|
| 1단 유니버스 | **키움 HTS 조건식** | 아래 F3 선택 | 전 시장을 우리가 아닌 HTS가 훑고, 통과 종목 리스트만 서버에 줌 |
| 2단 진입 | **우리 서버** | **항상 1분봉** | 정확한 20/50/100 정배열 + 눌림 + 프랙탈 + 20EMA 재돌파 |

진입·손절·익절 계산은 **전부 1분봉**이다. 1단을 일봉으로 적었던 이유는 “스캘핑을 일봉으로 한다”가 아니라, **우리 REST로 전 종목 1분봉을 돌리지 않기 위해** HTS에 맡긴 거친 체일 뿐이었다.

HTS 조건식은 키움 쪽에서 돌아가므로 **1분봉 정배열을 조건식에 넣어도 API 18회와 무관**하다. 스캘핑이면 1단도 1분이 맞다.

HTS 조건식에 넣을 것 (초안):

| # | 필터 | 봉 | 목적 |
|---|------|----|------|
| F1 | 당일 거래대금 ≥ N억 (예: 50~100억) | 일/당일 | 체결·슬리피지 |
| F2 | 관리/투자주의/우선주/ETF/ETN/스팩 제외 | — | 공통 |
| F3 | **1분 EMA 스택만** `EMA20 > EMA50 > EMA100` (**종가>EMA20 넣지 않음**) | **1분** | 눌림 중에도 편입 유지 |
| F3b | (선택 AND) 60분 또는 일봉 정배열 — 기준은 아래 | 60분/일 | 상위 추세 필터. 넣으면 후보가 줄고, **1분만 살아있는 종목은 놓침** |

**F3b 60분봉 정배열 기준 (EMA 기간은 전략과 동일 20/50/100)**

한국 장 09:00~15:30이면 60분봉은 하루 약 7개. EMA20≈3거래일, EMA50≈7일, EMA100≈14일 흐름이다. **진입용 1분 정배열과 식을 같게** 가져간다.

| 등급 | 조건 (60분봉, 확정 종가) | 언제 쓰나 |
|------|--------------------------|-----------|
| **엄격 (권장)** | `종가 > EMA20 > EMA50 > EMA100` | 스캘핑이어도 상위 추세가 산 종목만 |
| 보통 | `EMA20 > EMA50 > EMA100` (종가는 20 아래 눌림 허용) | 60분에서도 눌림목 중인 종목을 유니버스에 남김 |
| 느슨 (구 ‘근사’) | `EMA20 > EMA50` 또는 `종가 > EMA20` | 후보가 너무 없을 때만. 죽은 종목이 많이 섞임 |

일봉을 쓸 때도 같은 식: 엄격이면 `종가 > 일봉 EMA20 > 50 > 100`.  
F3(1분)과 F3b(60분)를 **둘 다 AND** 하면 “큰 추세 + 1분 추세”만 남는다. 1차 권장은 **F3만** 넣고 F3b는 신호가 지저분할 때 켠다.
| F4 | 당일 등락 과열 컷 (예: +12% 이상 제외) | 당일 | 장대양봉 추격 금지 |
| F5 | (선택) 고점 대비 소폭 눌림. **프랙탈·재돌파는 HTS에 넣지 않음** | 1분 | 넣으면 리페인트·누락. 2단 게이트 담당 |

서버는 조건식 통과를 **관심 풀 진입**으로만 본다. 매수 자격은 2단 게이트. 편입 이탈은 §3.2.1 스티키로 흡수한다.
### 3.2.1 조건식에서 1분 만에 사라지는 문제 (돌파에서 이미 겪음) — **높음**

돌파에서 조건식이 짧을수록 “HTS에는 보였는데 스캐너·주문까지 못 감”이 났다. 이 전략은 봉이 **1분**이라 그 위험이 더 크다.

**왜 파이프라인에 못 붙나**

```
HTS 편입 (이번 1분봉 조건 참)
    → 스캐너가 조건식 결과를 가져옴 (최대 ~1분 지연)
    → 1분봉 조회·게이트 (API 대기 수 초~수십 초)
    → 프랙탈 확정은 저점 이후 +2봉, 재돌파는 그 다음 종가
    → PendingBuySignal → BuyOrderExecutor (또 한 사이클)
```

편입이 **그 1분 동안만** 유지되면, 재돌파를 기다리다가 다음 스캔 때 명단에서 빠진다. 게이트는 지금 돌파처럼 **조건식 재조회를 하지 않지만**, `_collect_targets`가 실시간 편입만 보기 때문에 **다음 스캔에서 평가 자체가 안 된다.**

더 치명적인 충돌: 이 전략의 진입은 **20EMA 아래 눌림**이다.  
HTS에 `종가 > 1분 EMA20` 또는 “재돌파 순간”을 넣으면, **눌리는 동안 조건식에서 탈락**한다. 사야 할 타이밍에 유니버스가 비는 구조다.

| 원인 | 결과 |
|------|------|
| 조건식 = 진입 신호 (정배열+눌림+재돌파) | 편입 창이 1봉. 주문 루프까지 생존 못 함 |
| 조건식에 `종가 > EMA20` | 눌림목이면 명단에서 사라짐 |
| 대금 상위 5 cap이 매분 재정렬 | 4위↔6위 진동으로 차트 대상에서 퇴출 |
| 스캔 주기 1분 + 프랙탈 2봉 지연 | 필요 관측 시간 3~5분 vs HTS 체류 1분 |

**대책 (확정 방향)**

1. **HTS는 넓은 관심 풀.** 매수 순간을 조건식에 넣지 않는다. (역매공파 PRD의 “ARMED를 조건식에 넣지 말 것”과 동일)
2. 1분 조건식을 쓸 때: `EMA20 > EMA50 > EMA100` 정도만. **종가 > EMA20, 프랙탈, 재돌파는 넣지 않음** — 눌림 중에도 편입 유지.
3. 더 안전하게: HTS는 **유동성+잡주제외+(선택) 60분 정배열**처럼 느린 조건. 1분 정배열은 서버 게이트.
4. **스티키 + WATCHING (확정):** HTS에 처음 보이면 `WATCHING`으로 고정. 조건식 명단에서 빠져도 관찰 유지. 5종 cap은 **동시 WATCHING ≤ 5**. 빈 슬롯이 있을 때만 신규 편입.
5. 주문 직전 `evaluate_gate_pack`에서 **조건식 재편입 여부를 보지 않음.** 1분 정배열·프랙탈·재돌파만 재확인.
6. **최종 탈락(FAILED)만** 대상 제외: 정배열 붕괴(100EMA), 관찰 시간 초과(10~15분), 과열·정지. “아직 프랙탈/재돌파 없음”은 탈락이 아님.

이 대책 없이 1분 HTS를 그대로 쓰면, 돌파 때와 같이 **로그에는 편입이 찍히고 매수는 0건**이 될 가능성이 높다.


### 3.3 2단 게이트 (소수만 1분봉)

유니버스 종목(WATCHING 포함)마다:

1. `get_stock_chart_data(code, "1M", max_bars=120)` — EMA100에 최소 ~100봉
2. EMA20/50/100 계산
3. 정배열 아니면 **이번 스캔은 진입 없음** (WATCHING은 유지. 붕괴면 FAILED)
4. 최근 N봉 눌림 + 확정 녹색 프랙탈 + 20EMA 종가 재돌파 → PENDING
5. 슬롯·시간창·쿨다운

**동시 차트 조회 상한**: 스캔당 **WATCHING ≤ 5**. 신규 HTS 편입은 빈 자리만.

### 3.4 스크리너가 하면 안 되는 것

- 코스피/코스닥 전 종목 1분봉 루프
- “20EMA 터치”만으로 후보 등록
- 레거시 50종목 풀에 프랙탈 게이트를 얹기
- 프랙탈 미확정(최신 1~2봉) 신호를 유니버스 조건으로 쓰기

### 3.5 운영 시 후보 수 가늠

| 단계 | 목표 규모 | 비고 |
|------|-----------|------|
| 1단 유니버스 | HTS 편입 | 넓을 수 있음 |
| 동시 WATCHING | **5** | 빈 슬롯에만 신규 |
| 당일 실제 매수 | 0~3 | 슬롯 1 기본 |
| 동시 보유 | **1** | 기본값 |

---

## 4. 제품 목표 / 비목표

### Goals

1. `use_fractal` ON일 때만 전용 유니버스·게이트·청산이 돈다.
2. 진입·미진입·청산 사유가 체크리스트로 로그에 남는다 (`strategy_key=fractal`).
3. legacy / sangtta / breakout / ymgp / jongga **회귀 없음**.
4. 1회 손실이 설정 리스크%(0.5~1%)를 넘지 않게 수량을 자른다.

### Non-goals (1차)

- 숏 / 역배열 매도
- 틱·호가 HFT, 1초 이하 진입
- 전 종목 1분 전수 스캔
- 완벽한 프랙탈 리페인트 제거 이상의 패턴인식
- 완전 자동 최적화(EMA 기간 그리드)

---

## 5. 구현 매핑 (코드)

| 할 일 | 위치 |
|-------|------|
| `source=fractal` + HTS 조건식 | `AutoTradeScanner._collect_targets` (`fractal_condition_names`만) |
| 편입 → WATCHING, 탈락만 FAILED | 스캐너 생성 + `BuyOrderExecutor._process_watching_signals` (돌파 유예와 동일 패턴) |
| `_target_strategy_key` / 요약 라벨 | `auto_trade_scanner.py` `_STRATEGY_SUMMARY_*` |
| 게이트 | `evaluate_gate_pack` → `_eval_ema_fractal_pullback` (대기 vs 최종탈락 구분) |
| EMA·프랙탈 | 신규 `utils/ema_fractal.py` (순수 함수, 테스트 가능) |
| 1분봉 + 짧은 캐시 | `KiwoomAPI.get_stock_chart_data` 호출부 |
| 설정 | `AutoTradeSettings` `fractal_*` 전용 컬럼 (전역 `stop_loss_rate` / `take_profit_rate` 재사용 금지) |
| 수량 | `BuyOrderExecutor` — fractal만 리스크% ÷ (진입−손절). `breakout_buy_amount` 식 고정금액 아님 |
| 청산 | `StopLossManager` — `strategy_key=="fractal"` 이면 **체결 시 스냅샷한 가격**만. 트레일·ATR·부분익절 타지 않음 |
| 대시보드 | 돌파/역매공파처럼 **전용 카드**. 공통 손익률(%) 칸에 묶지 않음 |
| 검증 리플레이 | `utils/stock_exit_replay.py` 에 1분 시뮬 분기 (2차) |

의사코드:

```
universe = fetch_condition_results(fractal_condition_names)
for code in sticky_watching:  # HTS에서 빠져도 유지
    universe.add(code)
# 신규는 WATCHING 빈 자리(5-n)만 편입
for stock in active_five:
  bars = get_stock_chart_data(code, "1M", max_bars=120)  # 확정 봉만
  if alignment_broken: fail_watching(); continue
  if not (pullback and fractal and reclaim): keep_watching(); continue
  stop = ema50 - tick
  qty = (equity * risk_pct) / (entry - stop)
  promote PENDING(strategy="fractal", stop=stop, tp=entry+1.5*(entry-stop), qty=qty)
```

### 5.1 설정·DB·UI — 청산 개념이 다름 (필수)

다른 프로필은 대부분 **매수가 대비 %** 로 손절·트레일·부분익절을 한다. 이 전략은 **이평 거리 → 원 가격** 이다. 전역/타 전략 청산 칸을 공유하면 50EMA 손절이 −5% 손절로 덮이거나, `take_profit_rate`가 익절이 아니라 트레일 점화로 동작한다.

#### 기존 프로필과 비교

| | 레거시/상따 | 수급 돌파 | 역매공파 | 종가배팅 | **프랙탈 스캘핑** |
|--|-------------|-----------|----------|----------|-------------------|
| 손절 | `stop_loss_rate` % | `breakout_stop_loss_pct` % + 구조이탈 | MA/박스 % | `jongga_stop_loss_pct` % | **진입 시 50EMA 아래 가격 (고정)** |
| 익절 | `take_profit_rate`는 사실 트레일 시작% | 트레일 % | T1/T2 **수량 분할** | 트레일 % | **손절폭 × 1.5 가격 (전량 1회)** |
| 트레일 | ATR·고점% | 있음 | 있음 | 있음 | **1차 없음** |
| 사이징 | 금액/예수금% | 금액/예수금% | 1·2차 금액 | 금액 | **계좌 × 리스크% ÷ (진입−손절)** |
| 장마감 | `liquidate_before_close` 공통 | 돌파는 오버나잇 가능 | 일봉 성격 | 익일 청산 | **당일 청산 권장** (스캘핑) |

`StopLossManager._check_position_stop_loss`는 이미 `breakout` / `ymgp` / `jongga` 분기가 있다. **`fractal` 분기를 추가**하고, 그 포지션은 `_build_stop_candidates`(전역 %·ATR)를 타지 않는다.

#### 체결 시 스냅샷 (포지션 컬럼)

이미 `Position.take_profit_price` 가 있다. 매수 체결 직후 고정한다. 이후 50EMA가 움직여도 **1차는 숫자를 안 따라감**.

| 필드 | 값 |
|------|-----|
| `strategy_key` | `fractal` |
| 손절가 | 진입봉 기준 50EMA − 1틱 (호가 단위 내림) |
| `take_profit_price` | `진입가 + round(1.5 × (진입가 − 손절가))` |
| (권장) `exit_levels` JSON | `{stop_price, take_profit_price, ema50_at_entry, risk_pct, rr}` |

모니터: 현재가 ≤ 손절가 → 전량 매도 / 현재가 ≥ 익절가 → 전량 매도. 둘 다 아니면 유지.

#### DB — `auto_trade_settings` 에 전용 컬럼 (기존 컬럼 재사용 금지)

`core/models.py` AutoTradeSettings + `_ensure_columns` 마이그레이션. PATCH 화이트리스트(`core/main.py`)에 동일 키 추가.

**켜기·유니버스**

| 컬럼 | 타입 | 기본 | UI |
|------|------|------|-----|
| `use_fractal` | bool | false | 전략 사용 |
| `fractal_condition_names` | text | null | HTS 조건식 이름 (사용자 제공) |
| `fractal_verify_condition_names` | text | null | 검증 전용(주문 없음) |
| `fractal_max_slots` | int | 1 | 동시 보유 슬롯 |
| `fractal_watch_slots` | int | 5 | 동시 WATCHING 상한 |
| `fractal_trade_start_time` | str | `09:20` | 매수 시작 (EMA100 봉 부족 회피) |
| `fractal_trade_end_time` | str | `14:50` | 신규 매수 종료 |
| `market_risk_block_fractal` | bool | true | 장세 악화 시 신규 차단 |

**사이징 (금액 칸이 아님)**

| 컬럼 | 타입 | 기본 | 설명 |
|------|------|------|------|
| `fractal_risk_pct` | float | 0.5 | 1회 리스크, 계좌 대비 % (0.5~1) |
| `fractal_qty_cap` | int | 기존 전역 주식수 상한과 맞춤 | 계산 수량 상한 |
| ~~`fractal_buy_amount`~~ | — | **안 만듦** | 돌파형 고정금액과 혼동 방지 |

**청산 (가격/%가 아니라 R과 이평)**

| 컬럼 | 타입 | 기본 | 설명 |
|------|------|------|------|
| `fractal_rr` | float | 1.5 | 익절 = 손절폭 × 이 값 |
| `fractal_stop_ema` | int | 50 | 손절 기준 EMA 기간 |
| `fractal_stop_tick_buffer` | int | 1 | EMA 아래 N호가 |
| `fractal_watching_timeout_min` | int | 15 | WATCHING 최종 탈락 시간 |
| `fractal_liquidate_before_close` | bool | true | 이 전략만 당일 강제청산 |
| `fractal_liquidate_time` | str | `15:10` | 위가 true일 때. 전역 `liquidate_*` 와 분리 |
| `fractal_invalidation_100ema` | bool | false | 보유 중 100EMA 이탈 시 손절보다 먼저 청산 (2차) |

1차에 **넣지 않는 것:** `fractal_trailing_*`, `fractal_tp1_pct_of_pos`, ATR, 전역 `stop_loss_rate` 오버라이드.

#### 대시보드 UI

돌파·역매공파처럼 **독립 카드** (`static/dashboard.html` / `dashboard.js`). 공통 「손절 % / 트레일링 시작 %」 카드에 프랙탈 손절을 넣지 않는다.

카드 구성 초안:

1. 사용 ON + 조건식 이름 + 검증용 이름  
2. 시간창 09:40~14:50  
3. WATCHING 5 / 보유 슬롯 1 / 관찰 15분  
4. 리스크 0.5% + 손익비 1.5 (미리보기: “진입 100 · 손절 98 → 익절 103”)  
5. 당일 청산 ON + 시각  
6. 안내 문구: *손절은 전역 손익률이 아니라 진입 시 50EMA. 조건식에 재돌파를 넣지 말 것.*

저장 시 `use_fractal` OFF 면 컬럼은 남아도 스캐너가 안 돈다 (다른 `use_*` 와 동일).

#### 코드에서 막을 회귀

- `create_position_from_buy_signal` 이 fractal인데 `stop_loss_rate`만 넣고 `take_profit_price` 비우면 안 됨  
- 설정 저장 시 전역 손절%를 전 포지션에 덮어쓰는 로직(`StopLossManager` 동기화)은 **fractal 포지션 제외**  
- 검증 페이지·일지 뱃지: 손절/익절을 %가 아니라 **원** 으로 표시  
- `has_buy_conditions` / 장세 게이트에 `use_fractal` · `market_risk_block_fractal` 추가  

---

## 6. 개발 로드맵

기존 파이프라인을 전제로 **새 주문 엔진을 만들지 않는다.** 게이트·유니버스·WATCHING·청산만 추가.  
유니버스는 **HTS 조건식만** (거래대금순 대체 없음).

| Phase | 내용 | 산출 | 대략 |
|-------|------|------|------|
| **0. 조건식 연동 확인** | 사용자 제공 조건식 이름으로 편입 조회만. 주문 없음. 1분 뒤 명단 이탈 비율 로그 | 편입 유지 시간 숫자 | 0.5일 |
| **1. 순수 지표** | EMA, Williams Fractal(확정만), 눌림·재돌파 + 단위테스트 | `utils/ema_fractal.py` | 1일 |
| **2. 유니버스 + WATCHING** | `_collect_targets` 조건식 → 첫 편입 시 WATCHING. 동시 5. HTS 이탈해도 유지. 최종탈락만 FAILED | 스캐너 + executor 관측 루프 | 1~2일 |
| **3. 게이트** | `ema_fractal_pullback`: 대기(WATCHING 유지) vs 승격(PENDING) vs 탈락(FAILED). 체크리스트 로그. **실주문 OFF** | 신호만 | 1~2일 |
| **4. 사이징·청산** | 승격 시 리스크% 수량. 체결 스냅샷 손절가·익절가. `StopLossManager` fractal 분기(전역%·트레일 제외) | executor + stop_loss + 테스트 | 1~2일 |
| **5. 설정·대시보드** | `fractal_*` 컬럼 마이그레이션, PATCH, 전용 설정 카드, 전역 손절 UI와 분리 | models + main + dashboard | 1~2일 |
| **6. 페이퍼** | 테스트 계좌, 슬롯 1, 리스크 0.5% | 1~2주 로그 | 운용 |
| **7. (선택) 백테스트** | 1분 히스토리 리플레이 | 별도 | 2~4일 |

**권장 순서:** 1 → 2 → 3 (주문 OFF) → 0은 조건식 받는 대로 2와 병행 → 4 → 5 → 6.  
Phase 0을 15종 대금순 관측으로 시작하지 않는다. 조건식이 없으면 유니버스 테스트가 안 된다.

**의존성**

- Phase 3 게이트는 “미충족 = FAILED”로 짜면 WATCHING이 죽는다. **대기/탈락을 처음부터 구분.**
- `BuyOrderExecutor`의 돌파 WATCHING 재평가를 재사용. 새 체결 엔진 없음.
- Phase 4 전에 3에서 승격 로그만 보면 사이징 버그와 게이트 버그를 섞지 않는다.
- 차트 캐시 TTL은 Phase 2~3에서 `fractal` 경로만 짧게.

**일정 합:** 핵심 연동 **6~9 영업일** + 페이퍼. WATCHING을 빼면 일정은 줄지만 1분 HTS 실종 문제가 재발한다.

---

## 7. 리스크 · 운영

- 장 초반 1분봉 EMA100은 봉 부족 → **09:20 이후** 또는 전일 1분 이어붙이기
- 동시호가·단일가 구간 제외
- 손절이 타이트(50EMA)하므로 갭·호가 공백 종목은 1단에서 탈락
- 프랙탈 확정 지연(2분) + 스캔 주기(1분) → 신호가 차트보다 늦게 나올 수 있음. **종가 다음 봉 시가 진입**을 옵션으로 둔다

---

## 8. 성공 지표 (초안)

| 지표 | 1차 |
|------|-----|
| 유니버스 대비 1분봉 조회 수 | 스캔당 ≤ 5 (WATCHING 포함) |
| HTS 이탈 후에도 WATCHING 재평가 | 로그로 확인 |
| 신호→주문 `strategy_key=fractal` 일관 | 로그 100% |
| 1회 손실 | 설정 리스크% 이내 (슬리피지 별도) |
| 기존 전략 | `use_fractal` OFF 시 동일 |

---

## 9. 부록 — 용어

- **정배열**: 20EMA > 50EMA > 100EMA (상승 스택)
- **Williams Fractal**: 중심봉이 좌우 2봉보다 낮으면 매수 프랙탈(확정은 2봉 후)
- **눌림**: 추세 중 20EMA 하회 후 재탈환
- **방식 B**: 게이트 패키지를 전략마다 분리하고 슬롯만 공유 (상따 PRD)

문서 위치: `docs/PRD_Williams_Fractal_EMA_Scalping.md`
