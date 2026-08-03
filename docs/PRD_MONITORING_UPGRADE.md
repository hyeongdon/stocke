# PRD: 모니터링 고도화 (Ops Observability)

> **상태**: Draft — 범위·알림 채널·워치독 임계값 확정 대기  
> **작성일**: 2026-07-24  
> **대상 시스템**: stocke 운영 관측 (`대시보드` · `activity-log` · `readiness` · `tray/telegram` · 배치 스케줄러)  
> **관련 코드**: `core/main.py` (`/health`, `/trading/readiness`, `/trading/activity-log`, `/batch-status`), `static/dashboard.js`, `managers/stop_loss_manager.py`, `managers/auto_trade_scanner.py`, `managers/buy_order_executor.py`, `utils/tray_notify.py`, `notifications/trade_alert.py`, `utils/batch_scheduler_status.py`, `scripts/server_tray.ps1`  
> **관련 문서**: `docs/MODULE_PROCESS_OVERVIEW.md` (수동 운영 체크리스트), `docs/PROCESS_FLOW.md`, `docs/DEBUG_MODE_GUIDE.md`

---

## 0. 한 줄 결론

**“루프가 켜져 있다”만으로는 부족하다.**  
장중에는 **하트비트(최근 사이클 시각) + 이상 징후 자동 알림 + 한 화면 헬스**로, 사람이 `MODULE_PROCESS_OVERVIEW` 체크리스트를 돌리지 않아도 **멈춤·토큰장애·잔고정합·배치실패**를 즉시 알 수 있게 한다.  
체결 알림(텔레그램/트레이)은 이미 있으므로, 1차는 **운영(Ops) 관측·이상 알림**에 집중한다.

---

## 1. 배경 · 문제

### 1.1 지금 있는 것

| 영역 | 현재 | 한계 |
|------|------|------|
| 엔진 ON/OFF | 대시보드 상태행, `/trading/activity-log` `runtime` | **살아 있는지(최근 사이클)** 가 아니라 **세션 활성 여부** 위주 |
| 준비도 | `/trading/readiness` (토큰·계좌·설정·루프 alive) | 대시보드에 **통합 표시·경보**가 약함 |
| 손절 하트비트 | `StopLossManager._last_cycle_at` / 로그 | **API·UI·알림으로 거의 안 나감** |
| 스캐너 | `last_scan_at` | 대시보드 상태행에 **stale 판정 없음** |
| 체결 알림 | 텔레그램 + 트레이 큐 | 매수/매도 중심. **장애 알림은 산발적** |
| 배치 | `/batch-status`, schtasks 조회 | 실패 시 **일관된 알림·재시도 UX** 미흡 |
| 운영 루틴 | `MODULE_PROCESS_OVERVIEW` 장전/장중/장후 체크리스트 | **수동** — 놓치면 장중 무방비 |

### 1.2 왜 아픈가

```
서버 프로세스 살아 있음
  ≠ 스캐너/매수/손절 루프가 실제로 돌고 있음
  ≠ 토큰이 유효함
  ≠ DB HOLDING ↔ 키움 잔고가 맞음
  ≠ PENDING 신호가 쌓이지 않음
  ≠ 야간 배치가 성공했음
```

실무에서 자주 나오는 실패 모드:

1. **조용한 죽음**: 태스크는 `running`인데 예외로 사이클이 안 돎 → 손절/동기화 공백  
2. **토큰/8005**: 인증 끊김 후 주문·조회 실패가 누적되나, 즉시 “운영 경보”로 안 뜸  
3. **잔고 드리프트**: 키움에는 있는데 DB에 없거나 그 반대 → 대시보드 배너 수준, **푸시 알림 약함**  
4. **API 제한(429)**: 배지는 있으나 **지속 시 자동 완화/경보 정책**이 문서화되지 않음  
5. **배치 실패**: 테마/뉴스/펀더멘털/수출입 배치가 깨져도 장중까지 모르는 경우  

→ 제품 목표: **관측 가능한 신호(heartbeat) + 규칙 기반 경보 + 단일 헬스 뷰**.

---

## 2. 제품 목표

### 2.1 Goals

1. **엔진 헬스**: 스캐너·매수실행기·손절 각각에 `last_cycle_at` / `stale` / `expected_interval` 을 노출한다.  
2. **통합 헬스 API**: `/ops/health`(가칭) 한 번에 readiness + 엔진 stale + API limit + 잔고 드리프트 요약 + 배치 요약.  
3. **이상 알림**: 심각도별(info/warn/critical)로 **텔레그램 + 트레이**에 Ops 알림. 중복 억제(쿨다운) 포함.  
4. **대시보드**: 상태행을 ON/OFF가 아니라 **정상 / 지연 / 중지 / 장애** 4단으로 보이게 한다.  
5. **장전 원클릭 점검**: readiness + 드리프트 + 스케줄러 작업 존재 여부를 한 카드로 통과/실패 표시.

### 2.2 Non-goals (1차)

- Prometheus/Grafana/Datadog 등 외부 APM 도입  
- 로그 전량 중앙 수집(ELK)  
- 틱/호가 실시간 마켓 모니터링 UI  
- 자동매매 전략 성과 대시보드(승률·MDD) — 별도 PRD  
- 장애 시 자동 서버 재기동(워치독이 알림만; 재기동은 기존 `ensure_server_running` 유지)  
- 모바일 전용 앱

### 2.3 성공 지표 (초안)

| 지표 | 1차 목표 |
|------|----------|
| 장중 엔진 stale(임계 초과) → 알림 | ≤ 임계+1분 이내 1회 이상 도달 |
| 동일 경보 스팸 | 동일 `alert_key` 쿨다운 내 재발송 0 |
| 장전 체크리스트 수동 항목 | 핵심 5개 이상 대시보드/API로 대체 |
| 잔고 드리프트 감지 | 기존 배너 + Ops 알림(옵션) |
| 기존 체결 알림 회귀 | 매수/매도 텔레그램·트레이 동작 동일 |

---

## 3. 현재 → 목표 그림

```
[현재]
대시보드 ──여러 API──► ON/OFF 상태
로그/체크리스트 ─────► 사람이 주기적으로 확인
체결 이벤트 ─────────► 텔레그램 / 트레이

[목표]
각 엔진 루프 ──heartbeat──┐
readiness / rate-limit ───┤
잔고 정합 / PENDING 적체 ─┼──► OpsHealthAggregator
배치 last_run / schtasks ─┘         │
                                    ▼
                         /ops/health + 대시보드 헬스카드
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
            OpsAlertDispatcher              (기존) trade_alert
            (쿨다운·심각도)                 매수/매도 체결
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
   텔레그램 Ops 채널        tray_notify (warning/error)
```

---

## 4. 제품 스펙

### 4.1 엔진 하트비트 계약

각 장중 루프는 사이클 종료 시 공통 필드를 갱신한다.

| 필드 | 의미 |
|------|------|
| `component` | `scanner` / `buy_executor` / `stop_loss` |
| `last_cycle_at` | KST ISO — 마지막 정상 사이클 |
| `last_mode` | 예: `스캔`, `손절점검`, `장마감청산`, `동기화` |
| `expected_interval_sec` | 설정·모드 기준 기대 주기 |
| `stale` | `now - last_cycle_at > expected * factor` |
| `session_active` | 기존 “세션 창 안·활성” |
| `loop_alive` | asyncio Task 생존 여부 |

**stale factor (초안)**: `2.0` (예: 손절 120초 주기 → 240초 초과 시 stale).  
장외·세션 OFF·자동매매 OFF 시에는 stale 경보 **억제**(의도된 정지와 구분).

기존:
- `StopLossManager._last_cycle_at` → API 필드로 승격  
- `AutoTradeScanner.last_scan_at` → stale 판정에 사용  
- 매수 실행기: 사이클 타임스탬프가 없으면 **추가**

### 4.2 `/ops/health` (가칭)

응답 스케치:

```json
{
  "ok": false,
  "level": "critical",
  "summary": "stop_loss stale 4m · token ok · drift 1",
  "engine": {
    "scanner": { "session_active": true, "stale": false, "last_cycle_at": "..." },
    "buy_executor": { "...": "..." },
    "stop_loss": { "stale": true, "last_cycle_at": "...", "last_mode": "손절점검" }
  },
  "readiness": { "ready": true, "checks": {} },
  "api_rate_limit": { "status": "WARNING", "wait_sec": 12 },
  "positions": {
    "holding_count": 2,
    "account_drift": { "missing_in_db": ["005930"], "missing_in_account": [] }
  },
  "signals": { "pending_old_count": 0, "pending_oldest_sec": null },
  "batches": { "failed_today": ["stock_news"], "running": [] },
  "timestamp": "..."
}
```

- `ok`: critical 이슈 0건  
- `level`: `ok` | `warn` | `critical`  
- 대시보드·트레이·장전 스크립트가 **이 한 엔드포인트**만 봐도 되게.

### 4.3 Ops 알림 규칙 (1차)

| `alert_key` | 조건 | 심각도 | 채널 | 쿨다운 |
|-------------|------|--------|------|--------|
| `engine.stale.{component}` | 세션 활성인데 stale | critical | TG + tray | 10분 |
| `engine.dead.{component}` | 세션 활성인데 loop_alive=false | critical | TG + tray | 5분 |
| `auth.token` | readiness `api_authenticated=false` (장중) | critical | TG + tray | 15분 |
| `api.rate_limited` | LIMITED 지속 N분 | warn | TG | 20분 |
| `position.drift` | 계좌↔DB 불일치 | warn | TG (+ tray 옵션) | 30분 |
| `signal.pending_backlog` | PENDING 이 T분 초과 N건 | warn | TG | 30분 |
| `batch.failed.{id}` | 스케줄 배치 실패/미실행(당일 필수 배치) | warn | TG | 1일 1회 |
| `ops.daily_halt` | 일일 손익 한도 도달 | info | TG | 당일 1회 |

구현 메모:
- `utils/ops_alert.py`(가칭) — `alert_key` + 쿨다운 상태(메모리 또는 `logs/_ops_alert_state.json`)  
- 기존 `enqueue_tray_notify(kind=warning|error)` / `TelegramNotifier` 재사용  
- **체결 알림과 문구·이모지 구분** (예: `🛠 Stocke · Ops`)

### 4.4 대시보드 UX

상태 카드 개선:

| 표시 | 조건 |
|------|------|
| 정상(녹) | session_active && !stale |
| 지연(황) | session_active && stale 직전(예: 1.5×~2×) 또는 API WARNING |
| 중지(회) | 의도적 OFF / 세션 밖 |
| 장애(적) | stale 확정 · loop dead · token fail |

추가 UI(1차):
1. **헬스 요약 한 줄**: `/ops/health.summary`  
2. **장전 점검 패널**: readiness 체크 리스트를 통과/실패 아이콘으로  
3. (선택) 최근 Ops 알림 3건 — activity-log와 분리된 “경보” 탭/섹션

기존 체결 activity / 포지션 / 배치 카드는 유지. **새 페이지 강제 분기 없음** — 대시보드 상단 강화가 기본.

### 4.5 장전/장중 자동화와의 관계

| 시점 | 동작 |
|------|------|
| 장전 (`MorningServerWatch` 등) | 서버 up 후 `/ops/health` 호출 → critical 있으면 텔레그램 |
| 장중 | 서버 내부 워치독 태스크(60~120초)가 규칙 평가 |
| 장후 | 배치 실패·드리프트 요약 1회(옵션) |

워치독은 `lifespan`에 가벼운 asyncio 루프로 둔다. 외부 cron만 의존하지 않음.

---

## 5. 단계(Phase)

### Phase 0 — 관측만 (알림 OFF)

- 엔진 3종 `last_cycle_at` API 노출  
- `/ops/health` 읽기 전용  
- 대시보드 상태행 4단 표시  
- 성공 기준: 장중 수동으로 stale를 재현·화면에서 확인

### Phase 1 — Ops 알림

- `ops_alert` + 쿨다운  
- stale / dead / token / drift / batch.failed  
- 트레이 `warning|error` 연동  
- 성공 기준: 의도적 손절 루프 정지 시 10분 내 알림 1회

### Phase 2 — 장전 원클릭 · 배치 헬스 강화

- 대시보드 장전 점검 카드  
- `KNOWN_BATCHES` 실패·미실행 → 당일 알림  
- (선택) PENDING backlog 규칙

### Phase 3 — (백로그)

- 일일 Ops 리포트(체결 수·실패 사유·API wait 합)  
- 알림 채널 분리(체결 vs Ops 텔레그램 챗)  
- 임계값 UI 설정(`AutoTradeSettings` 또는 env)

---

## 6. 설정 · 환경변수 (초안)

| 키 | 기본 | 설명 |
|----|------|------|
| `OPS_WATCHDOG_ENABLED` | `true` | 서버 내 워치독 루프 |
| `OPS_WATCHDOG_INTERVAL_SEC` | `90` | 평가 주기 |
| `OPS_STALE_FACTOR` | `2.0` | 기대주기 배수 |
| `OPS_ALERT_TELEGRAM` | `true` | Ops → 텔레그램 |
| `OPS_ALERT_TRAY` | `true` | Ops → 트레이 |
| `OPS_ALERT_COOLDOWN_SEC` | 규칙별 기본값 | 전역 하한(선택) |

기존 `TELEGRAM_*` 재사용. 챗 분리은 Phase 3.

---

## 7. 테스트 계획

| # | 시나리오 | 기대 |
|---|----------|------|
| 1 | 손절 모니터링 stop 후 세션 활성 유지(테스트 훅) | `stop_loss.stale=true`, Phase1이면 알림 |
| 2 | 동일 stale 연속 | 쿨다운 동안 알림 1회만 |
| 3 | 장외 / `is_enabled=false` | stale 경보 없음 |
| 4 | 토큰 무효 시뮬레이션 | `auth.token` critical |
| 5 | DB에만 있는 HOLDING | drift warn + 기존 배너 |
| 6 | `/ops/health` 부하 | 워치독 주기 호출에 API rate limit 악화 없음(캐시·경량 조회) |
| 7 | 회귀 | 매수/매도 체결 텔레그램·트레이 문구·호출 경로 불변 |

단위: `tests/test_ops_health.py`, `tests/test_ops_alert_cooldown.py` (가칭).

---

## 8. 리스크 · 가드레일

| 리스크 | 대응 |
|--------|------|
| 알림 스팸 | `alert_key` 쿨다운 필수 |
| 워치독이 API 호출 폭주 | health는 메모리 heartbeat 우선, 잔고 조회는 TTL 캐시(예: 60~120초) |
| “중지” vs “장애” 혼동 | 세션/설정 OFF면 회색 중지, 활성+stale만 적색 |
| 트레이 미기동 | JSONL 큐는 기존처럼 적재; 알림 누락 방지는 텔레그램이 primary |
| readiness와 health 중복 | readiness는 하위 블록으로 포함, 대시보드는 health만 폴링해도 되게 |

---

## 9. 결정 필요 사항

| # | 항목 | 옵션 | 권장 |
|---|------|------|------|
| 1 | Ops 텔레그램 챗 | (A) 체결와 동일 (B) 별도 챗 | **A** (1차), B는 Phase 3 |
| 2 | 잔고 드리프트 알림 | (A) warn TG (B) 대시보드만 | **A** |
| 3 | stale factor | 1.5 / 2.0 / 3.0 | **2.0** |
| 4 | 워치독 위치 | 서버 lifespan / 외부 schtasks | **서버 lifespan** + 장전 스크립트 보조 |
| 5 | `/ops/health` 경로명 | `/ops/health` vs `/trading/health` | **`/ops/health`** (트레이딩 외 배치 포함) |
| 6 | Phase 0만 먼저? | 관측만 / 알림까지 | 구현 착수 전 확정 |

---

## 10. 구현 시 건드릴 파일 (예상)

| 파일 | 변경 |
|------|------|
| `managers/stop_loss_manager.py` | heartbeat → status API 필드 |
| `managers/auto_trade_scanner.py` | stale용 interval·노출 정리 |
| `managers/buy_order_executor.py` | `last_cycle_at` 추가 |
| `core/main.py` | `/ops/health`, (선택) 워치독 시작 |
| `utils/ops_health.py` | 집계 |
| `utils/ops_alert.py` | 쿨다운 디스패치 |
| `utils/tray_notify.py` | Ops용 헬퍼(선택) |
| `static/dashboard.js` / `dashboard.css` | 4단 상태 · 헬스 요약 · 장전 카드 |
| `core/config.py` / `env_example.txt` | Ops 설정 |
| `tests/test_ops_*.py` | 단위 테스트 |

---

## 11. 열린 질문

1. Ops 알림을 **장중만** 보낼지, 장전 배치 실패는 장외에도 보낼지?  
2. `pending_backlog` 임계(건수·분) 운영 선호값은?  
3. 모의투자(`KIWOOM_USE_MOCK`)에서도 critical 알림을 동일하게 켤지?  
4. 대시보드 폴링 주기(현재 상태 갱신)를 health 기준으로 바꿀지, 기존 다중 fetch 유지할지?

---

## 12. 다음 액션

1. 위 **§9 결정표** 확정  
2. Phase 0 PR: heartbeat 노출 + `/ops/health` + 대시보드 4단  
3. Phase 1: `ops_alert` + stale/token/drift  
4. `MODULE_PROCESS_OVERVIEW` 장전/장중 항목을 “자동 커버됨 / 여전히 수동”으로 갱신
