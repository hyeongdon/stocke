# PRD: 종가배팅 자동매매 (거래대금 · 최강테마 · 돼지물량 반응형)

> **상태**: Implementing  
> **작성일**: 2026-07-30 · **개정**: 2026-07-31 (돼지물량 3분할)  
> **대상 시스템**: stocke 자동매매 (`AutoTradeScanner` → `BuyOrderExecutor` → `StopLossManager`)  
> **관련 코드**: `utils/jongga_engine.py`, `managers/auto_trade_scanner.py`, `managers/buy_order_executor.py`, `api/kiwoom_api.py`, `core/models.py`, `static/dashboard.js`  
> **선행 패턴**: `docs/PRD_YEOKMAEGONGPA.md` (방식 B · 전략 프로필 분리)

---

## 0. 한 줄 결론

**당일 14:30 전후, 거래대금순 상위 종목을 테마로 묶어 가장 강한 테마에서 1종만 고른다.**  
선택/자동 확정 후 **돼지물량 반응형**으로 총액을 **20% → 30% → 50%** 분할 매수한다.  
청산은 **익일**부터 고정손절 + 트레일.

---

## 1. 제품 규칙

| 항목 | 값 |
|------|-----|
| `strategy_key` | `jongga` |
| 게이트 pack | `jongga_closing` |
| 유니버스 | ka10030 거래대금순 (`sort_tp=3`) |
| 테마 | `theme_map_store.get_latest_map_by_codes` · 미매핑=`미분류` |
| 최강 테마 | 테마별 당일 거래대금 합산 최대 |
| 선택 창 | `jongga_trade_start_time`~`jongga_pick_end_time` (기본 14:30~14:40) |
| 슬롯 | 당일 1종 (`jongga_max_slots=1`) |
| 눌림 | 당일 고가 대비 하락률(%) |
| 자동스코어 | min-max(눌림, 대금, 등락) 가중합 |
| UX | 대시보드 후보 + `POST /jongga/pick` |
| 알림 | 후보 구축 시 텔레그램 1회 (`notifications/jongga_candidates_notify.py`) |
| 청산 | 매수 당일 스킵 · 익일 고정손절 + 트레일 · 장마감 전량청산 제외(오버나잇) |

---

## 2. 돼지물량 반응형 분할 (`jongga_pig_split`)

총 예산 = `jongga_buy_amount` / `jongga_buy_deposit_pct`.  
OFF면 기존처럼 1회 전량.

| 차수 | 비중(기본) | 시점 | 조건 | 미충족 시 |
|------|------------|------|------|-----------|
| 1차 | 20% | pick/자동확정 직후 | 유니버스·게이트 통과 (씨드) | — |
| 2차 | 30% | `≥ jongga_leg2_start_time` (14:50) | 분봉 **저점 지지** + **프로그램 순매수** | 3차 창 진입 시 스킵 마킹 |
| 3차 | 50% | `jongga_leg3_start`~`end` (15:20~15:28) | 호가 **매수벽(돼지)** | 매도벽 → 스킵 · 중립 → 대기 후 창 종료 시 스킵 |

### 2.1 수급 (한 종목) — 프로그램 매수세

장중 외인·기관 일별 순매수는 미집계(0/공란)인 경우가 많아 **프로그램 매매**로 판정한다.

- Primary: **ka90013** `POST /api/dostk/mrkcond` — `stk_daly_prm_trde_trnsn[].prm_netprps_qty`
- Fallback: **ka90008** 시간별 최신 봉 — `prm_netprps_qty`
- 통과: 프로그램 순매수수량 **> 0**

### 2.2 저점 지지

- 1분봉으로 당일 저가 산출
- 현재가 ≥ day_low(근사) · 최근 N봉 저가가 직전 구간 대비 붕괴하지 않음

### 2.3 돼지(호가) 판정

- **ka10004** `POST /api/dostk/mrkcond` 10호가 스냅샷 (`sel_fpr_*` / `buy_*th_pre_*`)
- 상위 `jongga_pig_levels`(기본 5)호가 `매수잔량합 / 매도잔량합`
- ≥ `jongga_pig_bid_ask_ratio`(기본 1.5) → **buy** (3차 매수)
- ≤ 1/ratio → **sell** (3차 스킵)
- 그 외 → **neutral** (대기)

---

## 3. 상태 머신

```
idle → (14:30 스캔) awaiting_pick
     → (대시보드 pick | 14:40 auto) leg1 신호(20%)
     → (1차 체결 HOLDING)
     → (≥14:50 · 저점·프로그램순매수) leg2 신호(30%) | skip
     → (15:20~15:28 · 돼지매수) leg3 신호(50%) | skip
```

상태 파일: `logs/_jongga_state.json` (일자별 · `legs.1|2|3`)

---

## 4. 설정 키 (`AutoTradeSettings`)

- `use_jongga`
- `jongga_max_slots` (1)
- `jongga_buy_amount` / `jongga_buy_deposit_pct` (총액)
- `jongga_trade_start_time` / `jongga_pick_end_time` / `jongga_trade_end_time`
- `jongga_rank_limit`
- `jongga_stop_loss_pct` / `jongga_trailing_start_pct` / `jongga_trailing_pct`
- `jongga_w_pullback` / `jongga_w_amount` / `jongga_w_change`
- `jongga_pig_split` (기본 ON)
- `jongga_leg1_pct` / `jongga_leg2_pct` / `jongga_leg3_pct` (20/30/50)
- `jongga_leg2_start_time` / `jongga_leg3_start_time` / `jongga_leg3_end_time`
- `jongga_pig_bid_ask_ratio` / `jongga_pig_levels`
- `market_risk_block_jongga`

---

## 5. 비범위

- 텔레그램 선택 버튼
- 조건식 유니버스
- 수출입 `trade_industry_*` 연동
- 매수 당일 손절/트레일
- 틱 단위 HFT 호가 추적 (폴링 스냅샷만)
