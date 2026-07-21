# PRD: 과매도 돌파전략 (Oversold Breakout)

> **상태**: Draft — 유니버스 **조건식 확정** (2026-07-17). 나머지 수치·청산은 운영 전 확정  
> **작성일**: 2026-07-17  
> **확정**: 유니버스 = **전용 키움 조건식만** (`breakout_condition_names`). 자체 일봉 RSI 전수/배치 스캔 **비범위** (API 비용)  

> **대상 시스템**: stocke 자동매매 (`AutoTradeScanner` → `BuyOrderExecutor` → `StopLossManager`)  
> **관련 코드**: `utils/auto_trade_engine.py`, `managers/auto_trade_scanner.py`, `managers/stop_loss_manager.py`, `core/models.py` (`AutoTradeSettings`, `Position.strategy_key`)  
> **선행 PRD**: `docs/PRD_SANGTTA_BREAKOUT.md` (멀티게이트·전략 프로필 골격)  
> **HTS 조건식 초안**: `docs/OVERSOLD_BREAKOUT_HTS_CONDITION_EXAMPLE.md`

---

## 0. 한 줄 결론

**세 번째 전략 프로필로 “과매도 후 돌파”를 넣는다.**  
유니버스는 **전용 키움 조건식**으로만 모은다(과매도 이력은 HTS 조건식이 담당). 진입은 **돌파 신호 AND 패키지**, 청산은 **구조 이탈 → 고정손절 → 트레일 → (선택) 당일청산** 순으로 이어지게 한다.  
기존 **거래대금 눌림목(`legacy_momentum`)** · **상따(`sangtta`)** 와 설정·슬롯·시간대를 분리해, 서로 조건을 덮어쓰지 않는다.  
**자체 일봉 RSI 전수 스캔은 하지 않는다** — API rate limit·비용 때문에 유니버스는 조건식만 쓴다(상따와 동일).

---

## 1. 전략 프로필 지도 (현재 → 목표)

| 프로필 | `strategy_key` | 게이트 패키지 | 한 줄 성격 |
|--------|----------------|---------------|------------|
| 거래대금 눌림목 | `legacy` | `legacy_momentum` | 대금 상위·품질 게이트 후 모멘텀/눌림 |
| 상따 | `sangtta` | `sangtta_breakout` | 장초·소형·상한가 근접 · 이탈/급락 청산 |
| **과매도 돌파 (신규)** | **`breakout`** | **`oversold_breakout`** | **과매도 이력 → 저항/고점 돌파 → 구조·트레일 청산** |

```
[유니버스: breakout_condition_names 조건식 통과분만]
        │   ← 과매도 이력은 HTS 조건식이 담당 (서버 RSI 전수 스캔 없음)
        ▼  스캐너 (전략 윈도우 내)
[돌파 게이트 패키지 AND]
  · 조건식 통과(유니버스)?  · 돌파 레벨 돌파?  · 수급?  · 시간·필터?
        │
        ▼
매수 신호 (strategy=breakout) → 주문 → 포지션.strategy_key=breakout
        │
        ▼
청산: 구조 이탈 → 고정손절 → 트레일 → (선택) 장마감
```

상따 PRD와 동일한 **방식 B(게이트 패키지 + 전략 태그)** 를 그대로 확장한다.  
조건만 OR로 늘리는 방식(A)은 비추천.

---

## 2. 왜 기존 두 전략과 안 맞나

### 2.1 거래대금 눌림목 (`legacy`)

- 후보가 **당일 거래대금 상위** 중심 → 이미 강한 종목·대형 회전 위주
- 게이트가 “시가/VWAP/당일위치” 등 **당일 품질 확인**에 가깝다
- **“최근에 눌렸다가(과매도) 다시 깨고 올라가는”** 패턴을 전제로 하지 않음

### 2.2 상따 (`sangtta`)

- **등락 +15~19%**, 시총 ≤3000억, 장초 윈도우
- 청산이 **상한가 이탈 / 급락 HARD·SOFT**
- 과매도 반등·중기 돌파와 **타이밍·리스크 프로파일이 반대에 가깝다**

→ 돌파전략을 legacy 게이트에 끼워 넣거나 상따 청산을 공유하면  
**미진입 / 과매수 / 잘못된 손절**이 동시에 난다.  
→ **세 번째 패키지 + `strategy_key=breakout`** 가 필요.

---

## 3. 제품 목표

### 3.1 Goals

1. **전용 조건식 통과 종목**만 후보로 두고, **돌파가 확정될 때** 소수·적정 금액으로 진입한다.
2. 진입부터 청산까지 **한 전략 프로필로 end-to-end** 동작한다 (검증 페이지에 `strategy=breakout` + 청산 체크).
3. legacy / sangtta **회귀 없음** (각자 ON/OFF·쿼터·시간대).
4. 진입·미진입·청산 사유를 **체크리스트**로 남긴다.

### 3.2 Non-goals (1차)

- 자체 일봉 RSI 전수/배치로 유니버스 구성 (API 비용 — **조건식만 사용**)
- 완벽한 바닥 예측 / 테마·뉴스 NLP
- 틱·호가 단위 HFT
- 완전 자동 백테스트 엔진 (별도 `BACKTEST_PLAN`)
- 상따식 상한가 추격·이탈룰을 이 전략에 강제 적용

### 3.3 성공 지표 (초안)

| 지표 | 1차 목표 |
|------|----------|
| 후보 중 “과매도 이력 유효” 비율 | 로그로 100% 판정 가능 |
| 돌파 신호 → 주문 → 청산 라운드트립 | `strategy_key=breakout` 일관 |
| 동시 보유 | ≤ `breakout_max_slots` |
| 기존 전략 회귀 | breakout OFF 시 legacy/sangtta 동일 |
| 평균 보유·승률 | Phase 0 관측 후 목표치 설정 |

---

## 4. 과매도 돌파 정의 (제품 스펙)

직관: **“한번 많이 눌린 뒤, 의미 있는 저항을 거래량과 함께 뚫으면 산다. 깨지면 나온다.”**

### 4.1 유니버스 (확정) — 전용 조건식만

**확정 (2026-07-17):** 돌파전략 후보는 **`breakout_condition_names`에 등록한 키움 조건식 통과 종목만** 사용한다.

| 포함 | 제외 (비범위) |
|------|----------------|
| `breakout_condition_names` 조건식 결과 | 당일 거래대금 TopN과 합치기 |
| (선택) 관심종목 — **수동 보조만**, 기본 OFF | **자체 일봉 RSI lookback 전수/배치 스캔** |
| | legacy `screener_condition_names` / 상따 `sangtta_condition_names`와 풀 공유 |

**이유:** REST API 호출 예산·rate limit. 과매도 이력(RSI ≤30 lookback 등)은 **HTS 조건식에서 정의**하고, 서버는 조건식 결과만 받는다.  
상따 PRD의 `sangtta_condition_names` 분리 패턴과 동일.

조건식 설계 방향 (HTS — 과매도 이력, 초안):

| # | 방향 | 비고 |
|---|------|------|
| O1 | RSI(14) ≤ 30을 **최근 N일 내** 1회 이상 (또는 HTS 동등 표현) | “지금 과매도”가 아니라 **이력** |
| O2 | (선택) 급락 후 반등·당일 회복 중 | 바닥 추격 완화 |
| O3 | 관리·투자주의·우선주·ETF/ETN·스팩 제외 | 공통 |
| O4 | (선택) 시총·유동성 하한 | 상따(소형)와 차별 |

→ 프로그램은 **조건식 통과 = 과매도 유니버스 자격**으로 신뢰한다.  
서버에서 RSI를 다시 전수 계산하지 않는다 (API 비용). 게이트의 “과매도 재확인”은 **비범위**이거나, 돌파 판정용 **소수 후보 차트만** 조회.

### 4.2 돌파 신호 (Entry — 게이트 패키지 `oversold_breakout`)

과매도 이력이 **유효한 후보**에 대해서만 AND 평가.

| # | 조건 | 초안 정의 | 데이터 |
|---|------|-----------|--------|
| B1 | 매매 시간대 | **`breakout_trade_start` ~ `breakout_trade_end`** (예: **09:30~14:30**) | 시계 |
| B2 | 유니버스 자격 | **조건식 통과** (과매도 이력은 HTS가 담보) — 서버 RSI 재계산 없음 | 조건식 |
| B3 | 돌파 레벨 | 아래 **하나 이상(설정으로 선택)** 상향 돌파 | 일/분봉 |
| B4 | 돌파 확인 | 직전 봉 종가 또는 현재가가 레벨 **위** + (선택) 레벨 위 **유지 N분** | 1·5분봉 |
| B5 | 수급 | 당일 거래량 ≥ 전일×`vol_mult` **또는** 돌파 분봉 거래량 급증 | 일/분봉 |
| B6 | 과열 컷 | 등락 ≥ `breakout_max_change_pct` 이면 진입 금지 (이미 과도 진행) | 현재가 |
| B7 | (선택) VWAP | 현재가 ≥ VWAP (되돌림 후 재돌파 품질) | 5분 VWAP |
| B8 | 슬롯·한도 | `breakout_max_slots` · 일일매수 · 현금예비 · 총 동시보유 | 설정 |

**돌파 레벨 모드 (확정) — 2종만, 설정에서 1개 선택**

`breakout_level_mode` 로 **하나**만 고른다. 두 모드를 동시에 OR로 돌리지 않는다.  
어떤 레벨로 진입했는지는 신호·포지션에 **태그로 구분**해 청산·검증에서 그대로 쓴다.

| `breakout_level_mode` | 레벨 | 기준값 | 신호 태그 (`level_kind`) | 비고 |
|-----------------------|------|--------|--------------------------|------|
| `n_day_high` (대안) | 최근 N일 고가 | `breakout_n_day`(기본 10) 일봉 high | `n_day_high` | 저항 돌파 |

- 신호 `additional_data`: `{"strategy":"breakout", "level_kind": <위 태그>, "level_price": <레벨가>}`
- 청산 구조 이탈은 이 `level_price` 기준으로 판정 (§4.4)
- 검증 페이지에서 `level_kind` 뱃지로 “전일고 돌파 / N일고 돌파” 구분 표시

**의사코드**

```
breakout_universe = fetch_condition_results(breakout_condition_names)  # 유니버스 = 조건식만
for stock in breakout_universe:
  if not in breakout_time_window: continue
  # 과매도 이력: 조건식 통과로 간주 (서버 RSI lookback 스캔 없음)
  level_price = resolve_level(breakout_level_mode, stock)  # prev_high | n_day_high 중 택1
  if not breaks(level_price) with volume_ok: continue      # 소수 후보만 시세·분봉 조회
  if overheated(change_pct): continue
  if not allows_strategy_new_buy("breakout"): continue
  emit signal(strategy="breakout", gate_pack="oversold_breakout",
              level_kind=breakout_level_mode, level_price=level_price)
```

**동시 통과 시 우선순위 (제안)**

```
sangtta > breakout > legacy
```

같은 종목 이중 진입 금지. 이미 HOLDING/슬롯 예약이면 스킵.

### 4.3 사이징 · 슬롯 (초안)

| 항목 | 제안 | 비고 |
|------|------|------|
| 방법 | FIXED | 1차 단순 |
| 금액 | `breakout_buy_amount` (예: 100~200만, 상따보다 큼·legacy보다 작거나 비슷) | UI 노출 |
| 전략 쿼터 | `breakout_max_slots = 1` | 총 `max_concurrent_positions` 안에서 |
| 총 슬롯 예시 | 총 5 = 상따≤2 + 돌파≤1 + legacy 나머지 | 운영값 튜닝 |

### 4.4 청산 (End-to-end — 돌파 전용 우선순위)

상따의 “상한가 이탈/급락”과 **다른 구조**를 쓴다.  
돌파 포지션(`strategy_key=breakout`)만 아래 순서를 적용.

```
1) 구조 이탈 (돌파 레벨 / 피봇 하향 이탈)   ← 테제 파괴
2) 고정 손절 · 일일손실한도                 ← 계좌·포지션 한도
3) 트레일링 스탑 (고점 대비)                 ← 이익 보호
4) (선택) 장마감 청산                        ← 당일만 할지 여부는 운영 결정
```

| 규칙 | 정의 (초안) | 비고 |
|------|-------------|------|
| 구조 이탈 HARD | 현재가 ≤ 돌파레벨 × (1 − `struct_hard_pct`) | 즉시 매도 |
| 구조 이탈 SOFT | soft~hard 구간, `soft_confirm_polls` 연속 | 상따와 동일 패턴 재사용 |
| 고정손절 | 매수가 − `breakout_stop_loss_pct` (또는 포지션 stop) | 백업 |
| 트레일 | 고점 − `breakout_trailing_pct` (또는 공통 trailing 후 Phase2 분리) | 1차는 공통 trailing 가능 |
| 장마감 | `liquidate_before_close` | **당일청산 여부 명시적 토글** 권장 |

**구조 이탈 기준가**

- 진입 시 신호에 `breakout_level_price` 저장 → 청산 시 그 가격(또는 max(레벨, 당일 진입 후 피봇)) 사용
- 없으면 fallback: 매수가 × (1 − 고정손절%) 만 사용 (관측 Phase에서 경고)

**HARD/SOFT (노이즈 제거 — 상따와 동일 메커니즘 재사용)**

| 구분 | 구조 이탈 예시 | 동작 |
|------|----------------|------|
| HARD | 레벨 대비 ≤ −2~3% | 즉시 |
| SOFT | −1~2% 구간 | 연속 `soft_confirm_polls`(기본 2) |
| NONE | 레벨 위 | 카운트 리셋 |

---

## 5. 설정 키 (신규 제안)

`AutoTradeSettings` 싱글톤에 컬럼 추가 (상따와 동일 패턴).

| 키 | 타입 | 기본 제안 | 의미 |
|----|------|-----------|------|
| `use_breakout` | bool | false | 돌파 전략 ON |
| `breakout_condition_names` | text | "" | 전용 조건식명(쉼표) |
| `breakout_max_slots` | int | 2 | 전략 쿼터 |
| `breakout_buy_amount` | int | 1_000_000 | 1회 매수 금액 |
| `breakout_trade_start_time` | str | "09:30" | 신규매수 시작 |
| `breakout_trade_end_time` | str | "14:30" | 신규매수 종료 |
| `oversold_rsi` | float | 30 | (문서/HTS 동기용·선택) 서버 재스캔용 아님 |
| `oversold_lookback_days` | int | 7 | (문서/HTS 동기용·선택) 서버 재스캔용 아님 |
| `breakout_level_mode` | str | "prev_high" | **prev_high | n_day_high 중 1개** (동시 사용 X) |
| `breakout_n_day` | int | 10 | n_day_high일 때 N |
| `breakout_vol_mult` | float | 1.5 | 거래량 배수 |
| `breakout_max_change_pct` | float | 12 | 과열 컷(등락 상한) |
| `breakout_stop_loss_pct` | float | 3 | 고정손절 % |
| `breakout_trailing_start_pct` | float | 10 | 돌파전략 트레일링 활성화 고점 수익률 % |
| `breakout_trailing_pct` | float | (공통 또는 4) | 전략별 트레일(2차) |
| `struct_break_soft_pct` | float | 1.0 | 구조 이탈 SOFT |
| `struct_break_hard_pct` | float | 2.0 | 구조 이탈 HARD |

공통: `soft_confirm_polls`, `max_concurrent_positions`, `cash_reserve_pct`, `max_daily_buys` 등은 **포트폴리오 공유**.

---

## 6. 개발 로드맵

### Phase 0 — 관측 (매수 OFF)

- **`breakout_condition_names` 통과분**만 후보로 로그·대시보드 표시 (돌파 근접도)
- `strategy=breakout_candidate`
- **완료**: 실장 2~3일, “사고 싶은 종목”이 조건식 결과에 들어오는지 육안 검증
- **금지**: 전종목·대금 TopN RSI 자체 스캔으로 유니버스 확장

### Phase 1 — 게이트 + 소액 실매수

1. `evaluate_gate_pack("oversold_breakout")` 추가  
2. 스캐너·실행기에 `strategy=breakout` / `strategy_key` 저장  
3. 슬롯·금액·시간 윈도우 준수  
4. `buy_condition_checks`에 과매도·돌파 항목  
5. 청산은 **1차: 고정손절 + 공통 트레일 + 장마감** (구조 이탈은 Phase 2)  
6. **회귀**: `use_breakout=False` 시 기존과 동일

### Phase 2 — 구조 이탈 청산 + 검증 UI

- 진입 시 `breakout_level_price` 저장  
- `StopLossManager`에서 breakout 전용: 구조 HARD/SOFT → 손절 → 트레일  
- 검증 페이지: `strategy=breakout` 뱃지 + 구조 이탈 체크 (상따 이탈/급락 체크와 대칭)

### Phase 3 — 레벨·청산 고도화 (유니버스는 조건식 유지)

- n_day_high / 오프닝레인지 옵션 (후보 = 여전히 조건식 통과분만)  
- (선택) 분봉 거래량 급증 스코어 — **소수 후보만**  
- 전략별 trailing % UI  
- 자체 RSI 전수 스캔으로 유니버스 확장 **하지 않음** (API 비용 확정)

### Phase 4 — 운영

- 대시보드 토글·금액·조건식 피커 (상따 UI 패턴 복제)  
- 당일 리플레이·실패 사유 집계

---

## 7. 기존 구현과의 접점 · 제약

### 7.1 재사용

- 멀티게이트 `evaluate_gate_pack` / `allows_strategy_new_buy`  
- `Position.strategy_key` + 검증·체크리스트 패턴 (상따에서 이미 깔림)  
- HARD/SOFT + `soft_confirm_polls` 카운터 패턴 (`StopLossManager`)  
- 조건식 피커 UI (`sangtta_condition_names` 복제 → `breakout_condition_names`)  
- `strategy_manager` RSI 계산 로직 (참고; 자동매매 스캐너 경로와 통합은 명확히 분리)

### 7.2 제약

| 제약 | 영향 | 대응 |
|------|------|------|
| 설정 싱글톤 | 키 증가 | 상따와 같이 컬럼 추가 |
| REST rate limit / API 비용 | 전종목 RSI 불가 | **유니버스=조건식만 (확정)** |
| 스캔 ~60초 | 돌파 순간 놓침 | 분봉 확인은 조건식 통과 소수만 |
| 공통 trailing | 1차 타협 | Phase 2~3 전략별 % |
| RSI 정의 HTS vs 코드 | 서버 재계산 시 불일치·API 비용 | **조건식만 신뢰 (확정)** — 서버 RSI lookback 비범위 |

### 7.3 상따·legacy와의 시간 충돌

| 전략 | 신규매수 윈도우 (제안) |
|------|------------------------|
| sangtta | 09:05 ~ 11:00 |
| breakout | 11:00 ~ 14:30 |
| legacy | 기존 `trade_start` ~ `trade_end` |

오전 겹침 구간에서는 **우선순위 + 종목 단위 단일 전략**으로 충돌 해소.

---

## 8. 대시보드 / 검증 UI (최소)

**설정**

- [ ] 돌파전략 사용  
- 조건식 피커 (`breakout_condition_names`)  
- 매수 금액 / 슬롯 / 시작·종료  
- 돌파 레벨 모드 · 과열 컷 (과매도 정의는 HTS 조건식 문서와 동기)  

- (고급) 구조 soft/hard %

**검증**

- 목록·카드: `strategy=breakout` 뱃지  
- 매수 체크: 과매도 이력 · 돌파 레벨 · 수급  
- 매도 체크: 구조 이탈 HARD/SOFT · 손절 · 트레일  

---

## 9. 테스트 계획 (요약)

| 유형 | 내용 |
|------|------|
| 단위 | 조건식 유니버스만 스캔, 레벨 돌파 판정, 슬롯 쿼터, 구조 soft/hard |
| 회귀 | `use_breakout=False` 스냅샷 = 기존 |
| 통합 | 모의: 과매도+전일고 돌파 → 소액 매수 → strategy 태그 → 구조 이탈 매도 |
| 카나리 | Phase 0 3일 → Phase 1 슬롯 1 · 소액 |
| API | 스캔 1사이클 호출 ≤ 예산 |

---

## 10. 의사결정 체크리스트

| # | 항목 | 결정 | 상태 |
|---|------|------|------|
| 1 | `strategy_key` 이름 | **`breakout`** | 확정 |
| 2 | 유니버스 | **`breakout_condition_names` 전용 조건식만**. 대금순·자체 RSI 스캔·상따/legacy 풀과 **합치지 않음** (API 비용) | **확정** |
| 3 | 과매도 정의 | HTS 조건식에서 정의 (권장: RSI≤30 lookback). 서버 재스캔 없음 | **확정** |
| 4 | 돌파 레벨 | **prev_high(기본)·n_day_high 2종만, 설정에서 1개 선택**. 신호에 `level_kind` 태그 | **확정(범위)** |
| 5 | 매수 시간 | **11:00~14:30** | 확정 |
| 6 | 슬롯/금액 | 슬롯 **1** / 금액 **100만** (튜닝) | 확정 |
| 7 | 청산 순서 | **구조 이탈 → 손절 → 트레일 → (선택) 장마감** | 확정 |
| 8 | 당일청산 강제 | 상따처럼 강제 vs 오버나잇 허용 | **오버나잇 허용** |
| 9 | 우선순위 | sangtta > breakout > legacy | 확정 |
| 10 | Phase 1 청산 | 구조 이탈 없이 공통 손절·트레일만? | **구조이탈 허용(확정)** |

---

## 11. 권장 실행 순서 (한 장)

1. 유니버스 = **`breakout_condition_names`만** (대금순·자체 RSI·타 전략 조건식과 분리) — **확정**  
2. 게이트 패키지 `oversold_breakout` + `strategy_key=breakout`  
3. 슬롯·금액·시간 분리 (상따/legacy와 충돌 방지)  
4. Phase 0 관측 → Phase 1 소액 매수  
5. Phase 2: **구조 이탈 HARD/SOFT** 청산 + 검증 UI  
6. 돌파 레벨·청산 %는 장중 로그로 확정 (과매도 정의는 HTS 조건식)

이 방향이 현재 “전략 프로필 2개” 구조를 **3개로 자연 확장**하면서,  
**과매도(조건식) → 돌파 매수 → 청산** end-to-end를 API 예산 안에서 고정한다.

---

## 12. 다음 액션 (문서 확정 후)

1. `OVERSOLD_BREAKOUT_HTS_CONDITION_EXAMPLE.md` 기준으로 HTS 조건식 생성 → `돌파` 등록  
2. 위 **§10** 나머지(레벨·시간·슬롯·당일청산) 확정  
3. Phase 0 로그 필드 스펙 → 구현 PR (유니버스 fetch = 조건식만)  

