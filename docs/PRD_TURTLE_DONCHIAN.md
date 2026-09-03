# PRD: 터틀(데니스) Highest/Lowest 돌파 전략

> **상태**: Draft (설계) — 미확정 항목은 §12  
> **작성일**: 2026-08-26  
> **참고 영상**: [리처드 데니스 터틀 / Highest·Lowest 매매](https://www.youtube.com/watch?v=58rGRodGTi8)  
> **대상 시스템**: stocke 자동매매 (`AutoTradeScanner` → `BuyOrderExecutor` → `StopLossManager`)  
> **관련 코드(예정)**: `managers/auto_trade_scanner.py`, `utils/auto_trade_engine.py` (`evaluate_gate_pack`), `managers/stop_loss_manager.py`, `api/kiwoom_api.py` (`get_stock_chart_data` 일봉), `core/models.py` (`AutoTradeSettings`, `Position.strategy_key`)  
> **선행 패턴**: `docs/PRD_SANGTTA_BREAKOUT.md`, `docs/PRD_Williams_Fractal_EMA_Scalping.md` (게이트 패키지 + `strategy_key` 분리)

---

## 0. 한 줄 결론

**접목 가능하다. 기존 게이트에 OR로 끼우지 말고, 전용 전략 프로필(`turtle`)로 분리한다.**

영상·고전 터틀의 핵심은 **예측이 아니라 돌파 확정 후 추종**, **한 거래 리스크 상한**, **2N(ATR) 손절 + 채널 트레일로 이익을 길게**이다.  
stocke에는 **일봉 Highest High / Lowest Low(돈치안) + ATR(20)×2 + (선택) 200일선 필터**를 게이트·청산으로 올리고, 유니버스는 **HTS 조건식(유동성·잡주 제외)** 으로만 좁힌다.  
한국 현물 롱만 1차 범위. 숏·피라미딩·System 2(55/20)는 2차.

---

## 1. 전략 프로필

| 항목 | 내용 |
|------|------|
| 전략명 | 터틀 Highest/Lowest 돌파 (돈치안) |
| `strategy_key` | `turtle` |
| 게이트 패키지 | `turtle_donchian` |
| 스타일 | 추세추종 / 채널 돌파 |
| 방향 | **롱만** (1차). 숏은 Non-goal |
| 타임프레임 | **일봉** (진입·청산 모두 **종가 확정**). 분봉 스캘핑 아님 |
| ON/OFF | `use_turtle` (기존 `use_breakout` / `use_fractal` 과 동일 패턴) |

### 1.1 핵심 아이디어 (영상 요약)

| # | 원칙 | 제품 반영 |
|---|------|-----------|
| 1 | 한 거래 리스크 ≤ 계좌의 **2%** | 수량 = `(equity × risk%) ÷ (entry − stop)` |
| 2 | 손실은 빠르고 단호하게 | 진입 직후 **2N 손절** 고정. 미루지 않음 |
| 3 | 수익은 길게 | **10일 최저가(채널)** 이탈까지 보유. 채널이 2N보다 유리하면 트레일 |
| 4 | 예측하지 말고 추세를 따른다 | **종가가 20일 최고가를 상향 돌파**한 뒤에만 매수. 돌파 전 선진입 금지 |

승률은 낮아도 된다(영상: 여러 번 작은 손절 + 한 번의 큰 추세).  
제품 성공 지표는 **승률보다 손익비·평균 R·최대연속손실 후 잔고 회복**에 둔다.

### 1.2 차트·파라미터 (영상 기본값 = System 1)

TradingView `Highest High / Lowest Low` + ATR + 200MA 세팅을 서버 규칙으로 옮긴다.

| 지표 | 값 | 역할 |
|------|-----|------|
| Entry channel | **20**봉 최고가 (`HH20`) | 종가 > 직전 확정 `HH20` → 롱 진입 후보 |
| Exit channel | **10**봉 최저가 (`LL10`) | 종가 < 직전 확정 `LL10` → 청산(익절/트레일) |
| ATR (N) | 기간 **20** | 변동성. 손절 폭 = **2N** |
| 추세 필터 | **SMA 200** (일봉) | 종가 ≥ SMA200 일 때만 신규 롱 (권장 ON) |
| 진입 확정 | **종가** | 장중 터치만으로 진입하지 않음 |
| 손절 | `entry − 2 × ATR(20)` | 진입 시점 N 스냅샷. 노이즈에 안 털리게 |
| 트레일 | `max(초기 2N 손절, LL10)` 개념 | LL10이 손절가 위로 올라오면 청산선을 LL10에 맞춤 |

**고전 터틀 System 2 (2차, 기본 OFF)**

| 구분 | 진입 | 청산 |
|------|------|------|
| System 2 | 55일 최고가 돌파 | 20일 최저가 이탈 |

1차는 System 1만. System 2는 설정 토글로 나중에.

### 1.3 진입 조건 (AND, 롱)

1. 유니버스: `turtle_condition_names` 조건식 통과(또는 WATCHING 스티키 — §3)
2. (권장) **종가 ≥ SMA200**
3. **종가 > 직전 봉 기준 확정 HH20** (당일 봉의 high가 아니라, lookback에 **당일 미포함** 또는 전일까지 20봉 — 구현 시 하나로 고정)
4. 슬롯·쿨다운·시장 리스크 게이트·매수 금액 가드 통과
5. 수량: 리스크% 기반 (§1.5). 호가단위·최소수량 내림

**금지**

- 채널 위에서 종가 확정 전 예측 진입
- SMA200 아래에서의 “반등 예상” 롱 (필터 ON일 때)
- 같은 종목에 다른 전략과 이중 진입

### 1.4 청산 (우선순위)

보유 중 **매 스캔/봉 확정** 시:

1. **하드 손절**: 현재가(또는 종가 모드) ≤ `stop_price` (= 진입 시 `entry − 2N`, 이후 트레일로만 상향)  
2. **채널 청산**: 종가 < 직전 확정 `LL10` → 전량 청산 (추세 종료)  
3. (선택 2차) 당일 청산 / 오버나이트 금지 — 터틀 철학상 **Overnight 허용이 기본**. `overnight_keep` 패턴과 맞출지 §12에서 확정  
4. 고정 % 익절은 **쓰지 않음** (짧게 자르면 전략 붕괴)

트레일 갱신:

```
stop_price := max(stop_price, LL10_confirmed)   # 롱만, 손절가 하향 금지
```

영상 표현: “빨간 하단선이 초기 손절선 위로 올라오면 청산가를 하단선에 맞춘다.”

### 1.5 리스크·사이징

| 항목 | 1차 기본 |
|------|----------|
| 1회 리스크 | 계좌의 **1%** (영상 상한 2%. 보수적으로 1%부터) |
| 수량 | `(equity × risk_pct) ÷ (entry − stop)` |
| 유닛 상한 | 종목당 **1유닛** (피라미딩 없음) |
| 슬롯 | `turtle_max_slots` (예: 2~3). 추세 전략이라 동시 소수 |
| 쿨다운 | 동일 종목 손절 후 N일 (예: 3거래일) |

고전 터틀의 **½N마다 추가매수(최대 4유닛)** 는 2차. 1차는 단일 유닛 + 2N 손절만.

**체크리스트 (로그/검증 페이지)**

- [ ] 종가 > HH20 (확정)
- [ ] (ON) 종가 ≥ SMA200
- [ ] stop = entry − 2×ATR20, 수량 = 리스크÷손절폭
- [ ] 청산: 2N 또는 LL10 종가 이탈
- [ ] `strategy_key=turtle` 일관

---

## 2. 지금 파이프라인에 붙는가?

### 2.1 결론: **구조는 맞고, 일봉 게이트·ATR 사이징·채널 청산이 신규**

```
관심/조건식 (turtle_condition_names)
        │
        ▼  AutoTradeScanner
가격·유니버스 필터 → evaluate_gate_pack(turtle_donchian)
        │  일봉 HH20 / LL10 / ATR20 / SMA200
        ▼
PendingBuySignal → BuyOrderExecutor (리스크% 수량) → Position.strategy_key=turtle
        │
        ▼
StopLossManager: exit_levels.stop_price 트레일 + LL10 종가 청산
```

| 레이어 | 재사용 | 신규 |
|--------|--------|------|
| `_collect_targets` | 조건식 소스 패턴 | `source=turtle` 풀 |
| `evaluate_gate_pack` | 분기 | `turtle_donchian` |
| `get_stock_chart_data(..., "1D")` | **이미 있음** (ATR·일봉 캐시 TTL 12h) | HH/LL/SMA200 계산 유틸 |
| `BuyOrderExecutor` | 주문 골격 | **리스크% ÷ (entry−stop)** 수량 (fractal PRD와 동일 계열) |
| `StopLossManager` | 루프 | `%손절이 아닌` `stop_price` + LL10 채널 청산 |
| 설정 | `use_*` / 슬롯 | `use_turtle`, `turtle_*` |

**기존 프로필에 끼워 넣으면 안 되는 이유**

| 기존 | 충돌 |
|------|------|
| `legacy` | 당일 대금·당일 품질. 20일 채널 추세와 무관 |
| `sangtta` | 장초·상한가. 보유·청산 프로파일 반대 |
| `breakout` | 분봉·조건식 돌파·구조/트레일. 파라미터·철학 다름 |
| `fractal` | 1분 EMA·프랙탈 스캘핑. TF·청산 전면 불일치 |
| `jongga` | 종가배팅 특화 |

→ **`strategy_key=turtle` + 전용 유니버스 + 전용 청산.**  
우선순위 제안(같은 종목 이중진입 금지): 기존 순위 유지 후 turtle은 **중·후순위** (예: … > `fractal` > `turtle` > `legacy`). 확정은 §12.

### 2.2 막히는 지점

1. **일봉 종가 확정 타이밍**  
   정규장 종가(15:30) 전에 “오늘 종가 > HH20”을 확정할 수 없다.  
   → **장중**: 돌파 “임박/감시”만. **매수 실행**은 (A) 종가 근처 시장가/지정가, (B) 다음날 시가, (C) 15:20 이후 종가 추정 — 중 하나. **권장 1차: (B) 익일 시가** 또는 종가배팅과 비슷한 **종가 동시호가 창**.  
   잘못된 선택: 장중 고가 터치만으로 매수 → 영상·터틀 규칙 위반 + 가짜돌파 급증.

2. **API·캐시**  
   일봉은 상대적으로 싸다. 그래도 전 시장 일봉 전수는 하지 않는다 → 조건식 유니버스.  
   `turtle` 경로 일봉 캐시는 장중 당일 봉 갱신이 필요하면 TTL을 짧게(예: 5~15분) 또는 당일 봉만 시세 TR로 보정.

3. **손절이 % 전역**  
   `StopLossManager` 기본 %와 충돌. `exit_levels`에 `stop_price`, `n_atr`, `ll10`, `system` 스냅샷 필수.

4. **승률 착시**  
   연속 손절이 정상이다. 대시보드에 “승률만” 노출하면 사용자가 전략을 끈다.  
   → 검증/저널에 **평균 R, 기대값, 최대 DD**를 같이 표시.

5. **200일선 필터의 기회비용**  
   영상도 인정: SMA200 아래 강한 반등·위에서 하락 시작을 놓친다.  
   → 필터는 설정 ON/OFF. 기본 ON(보수).

---

## 3. 대상 종목(스크리너)

### 3.1 원칙

**전 종목 일봉 HH/ATR을 REST로 돌리지 않는다.**  
HTS가 유동성·잡주를 거르고, 서버는 소수 후보에만 일봉 게이트를 건다.

```
[1단] turtle_condition_names (HTS)
        │  cap: turtle_candidate_limit (예: 20)
        ▼
[2단] 일봉 HH20 / SMA200 / ATR → 진입 자격
        │
        ▼
신호 strategy=turtle
```

### 3.2 HTS 조건식 초안 (유니버스만 — 진입 순간을 넣지 말 것)

| # | 필터 | 목적 |
|---|------|------|
| F1 | 거래대금 ≥ N억 (예: 50억+) | 체결 |
| F2 | 관리/주의/우선주/ETF/ETN/스팩 제외 | 공통 |
| F3 | (선택) 주가 ≥ 절대가 (유동성) | 저가주 노이즈 |
| F4 | (선택) 20일 신고가 근접 — **돌파 확정은 넣지 않음** | 후보만 좁힘 |
| F5 | (선택) 종가 > 이평 200 — HTS에 넣을지 서버에 둘지 택1 | 중복 방지 |

**넣지 말 것:** “오늘 종가 돌파” 자체를 조건식 필수로 걸면 편입 창이 너무 짧아 스캐너가 놓친다.  
돌파 확정은 **서버 2단 게이트** 담당. (fractal/breakout PRD의 “ARMED를 조건식에 넣지 말 것”과 동일)

스티키: 조건식에 한 번 잡히면 `WATCHING`으로 수일 유지할지 — 일봉 전략은 편입 주기가 느리므로 **스티키 우선순위는 fractal보다 낮음**. 1차는 **당일 조건식 스냅샷 + 소수 cap**으로 시작 가능.

### 3.3 2단 게이트 (일봉)

종목당 `get_stock_chart_data(code, "1D", max_bars≥220)` (SMA200 + ATR20 + HH20):

1. SMA200 필터(ON이면)
2. `hh20 = max(high[-21:-1])` 형태(당일 제외 확정)
3. 오늘(또는 확정 기준봉) 종가 > hh20
4. `atr20` → `stop = close − 2*atr` (또는 익일 시가 진입 시 그 시점 가격 기준 재산출 규칙 고정)
5. 수량·슬롯

---

## 4. 제품 목표

### 4.1 Goals

1. 데니스/영상 규칙의 **HH20 종가 돌파 롱 + 2N 손절 + LL10 트레일 청산**을 end-to-end 자동화한다.
2. `strategy_key=turtle`로 진입·청산·검증·저널이 일관된다.
3. 한 거래 리스크%·슬롯으로 연속 손절에도 계좌가 버틴다.
4. 다른 전략 ON/OFF·회귀 없음.

### 4.2 Non-goals (1차)

- 숏(공매도/인버스 ETF 자동)
- 피라미딩(½N 추가)
- System 2(55/20) 필수화
- TradingView 커스텀 “한 장 지표” 복제(파란 중심선 익절 등 영상 후반 업그레이드판)
- 틱/호가 HFT
- 전 시장 일봉 자체 스크리너
- 승률 최적화용 과다 필터 더미

### 4.3 성공 지표 (초안)

| 지표 | 1차 |
|------|-----|
| 신호 → 주문 → 청산 | `turtle` 태그 100% |
| 1회 리스크 | 설정값 ± 슬리피지 허용 |
| 동시 보유 | ≤ `turtle_max_slots` |
| 청산 사유 분포 | `TURTLE_2N` / `TURTLE_LL10` 로그 |
| 회귀 | `use_turtle=false` 시 타 전략 동일 |
| 기대값 | Phase 0 관측 후 목표 |

---

## 5. 설정 키 (초안)

| 키 | 기본 | 설명 |
|----|------|------|
| `use_turtle` | false | 전략 ON |
| `turtle_condition_names` | [] | HTS 조건식 이름 |
| `turtle_max_slots` | 2 | 동시 포지션 |
| `turtle_candidate_limit` | 20 | 스캔 상한 |
| `turtle_entry_lookback` | 20 | HH 기간 |
| `turtle_exit_lookback` | 10 | LL 기간 |
| `turtle_atr_period` | 20 | N |
| `turtle_stop_atr_mult` | 2.0 | 2N |
| `turtle_risk_pct` | 1.0 | 계좌 대비 % |
| `turtle_use_sma200` | true | 추세 필터 |
| `turtle_entry_mode` | `next_open` | `next_open` \| `close_auction` \| `intraday_close_confirm` |
| `turtle_allow_overnight` | true | 기본 보유 허용 |
| `turtle_system2` | false | 55/20 (2차) |

---

## 6. 청산·알림 라벨 (초안)

| 코드 | 의미 |
|------|------|
| `TURTLE_2N` | 초기/트레일 손절가 터치 |
| `TURTLE_LL10` | 10일 최저 종가 이탈 |
| `TURTLE_LL20` | System2 청산 (2차) |
| `TURTLE_MANUAL` | 수동 |
| `TURTLE_FORCE` | 가드/장운영 강제 |

텔레그램: 진입 시 HH20·ATR·stop·수량·리스크원 표시. 청산 시 R배수.

---

## 7. 구현 페이즈

### Phase 0 — 관측 (매수 OFF 가능)

- 일봉 HH/LL/ATR/SMA200 유틸 + 단위 테스트
- 조건식 유니버스 → 게이트 합격/불합격 로그만
- 가상 손절·LL10 청산 시점 리플레이(선택)

### Phase 1 — 실매수 최소

- `use_turtle`, 게이트, 리스크 수량, `stop_price` 스냅샷
- 청산: 2N + LL10만
- SMA200 필터 ON
- 대시보드 토글·슬롯

### Phase 2 — 고도화

- System 2, 피라미딩, entry_mode 다양화
- 영상 후반형 “중심선 익절”은 **별도 실험 플래그** (고전 터틀과 섞지 말 것)

---

## 8. 테스트 계획

| 테스트 | 내용 |
|--------|------|
| `test_turtle_donchian_levels` | HH20/LL10/ATR/2N 수치 |
| `test_turtle_entry_close_only` | 고가 터치만으로는 신호 없음 |
| `test_turtle_sma200_filter` | 필터 ON/OFF |
| `test_turtle_position_size` | 리스크% → 수량 |
| `test_turtle_trail_ll10` | stop 하향 금지, LL10 상향만 |
| `test_turtle_no_regression` | OFF 시 타 전략 |

---

## 9. 영상 vs 고전 터틀 vs 본 PRD

| 항목 | 영상 | 고전 터틀 | 본 PRD 1차 |
|------|------|-----------|------------|
| 진입 | HH20 종가 돌파 | S1: 20 / S2: 55 | S1만 |
| 청산 | LL10 종가 | S1: 10 / S2: 20 | LL10 |
| 손절 | 2×ATR20 | 2N | 동일 |
| 추세필터 | 200일선 | 원전은 채널 위주 | SMA200 옵션(기본 ON) |
| 사이징 | 2% 상한 강조 | N·유닛·피라미딩 | 리스크%·1유닛 |
| 숏 | 설명함 | 선물 롱숏 | **비범위** |
| 업그레이드 지표 | 중심선 익절 등 | 없음 | **Non-goal** |

참고 자료(배경): [TrendSpider Turtle rules](https://trendspider.com/learning-center/richard-dennis-turtle-trading-strategy/), [돈치안·20/55 요약](https://ideal-life.co.kr/503).

---

## 10. 리스크·운영 주의

- **가짜 돌파·횡보 구간**에서 손절이 반복된다. 슬롯·리스크%를 키우면 계좌가 먼저 죽는다.
- 한국 주식은 **갭·단일가·VI**로 2N을 넘길 수 있다 → 손절은 최선노력, 사이징에 슬리피지 버퍼(선택).
- 종가 미확정 매수는 전략 정체성을 깨뜨린다. `entry_mode`를 문서·UI에 명시.
- 다른 전략과 **같은 종목 동시 보유 금지**.

---

## 11. 관련 파일 (구현 시)

- `utils/turtle_donchian.py` (신규) — HH/LL/ATR/SMA·게이트 순수함수
- `utils/auto_trade_engine.py` — `turtle_donchian` 분기
- `managers/auto_trade_scanner.py` — 유니버스
- `managers/buy_order_executor.py` — 리스크 수량
- `managers/stop_loss_manager.py` — 채널·2N 청산
- `core/models.py` — 설정 컬럼
- `static/modules/strategy-manager.js` / dashboard — 토글
- `tests/test_turtle_donchian.py`

---

## 12. 미확정 (사용자 결정 필요)

| # | 질문 | 옵션 | 제안 |
|---|------|------|------|
| Q1 | 진입 시점 | 익일 시가 / 종가 동시호가 / 장중 종가확정 후 | **익일 시가** 또는 **종가 창** |
| Q2 | SMA200 | 기본 ON/OFF | **ON** |
| Q3 | 리스크% | 1% vs 2% | **1%** 시작 |
| Q4 | 오버나이트 | 허용 vs 금지 | **허용** (터틀 본령) |
| Q5 | System 2 | 1차에 넣을지 | **2차** |
| Q6 | 유니버스 | 조건식만 vs 관심종목 병합 | **조건식만** (관심은 보조 OFF) |
| Q7 | 분봉 터틀 | 60분/일봉 혼용 | **1차는 일봉만** (영상 데모가 일·장기 추세 중심) |

---

## 13. 변경 이력

| 날짜 | 내용 |
|------|------|
| 2026-08-26 | 초안. 영상(Highest/Lowest·ATR2N·200일선) + stocke 멀티전략 패턴 반영 |
