# Module Process Overview

이 문서는 현재 자동매매 서버의 핵심 모듈별 실행 흐름을 한눈에 보기 위해 정리한 운영 문서입니다.

## 1) `core/main.py` (앱 부트스트랩 + API 허브)

- FastAPI 앱 생성, CORS/정적 파일 마운트, 로깅 초기화.
- `lifespan`에서 DB 초기화/보정, 키움 API 인증·연결, 자동매매 실행기 상태 반영.
- 자동매매 ON/OFF 시 `apply_auto_trade_state()`로 스캐너/매수/손절 모듈 일괄 제어.
- 운영 API 제공:
  - 설정/상태: `/trading/settings`, `/trading/readiness`, `/trading/activity-log`
  - 신호/포지션: `/signals/pending`, `/positions/`, `/sell-orders/`
  - 손절 모니터링: `/stop-loss/status`, `/stop-loss/start`, `/stop-loss/stop`, `/stop-loss/reconcile`

## 2) `managers/auto_trade_scanner.py` (신호 생성기)

- 주기(기본 2분)로 스캔 루프 실행.
- 사전 게이트:
  - 자동매매 활성화 여부
  - 매매 시간(in_trade_hours)
  - 일일 손익 제한(check_daily_limits)
  - 최대 동시 보유/일일 매수 횟수 제한
- 대상 수집:
  - 수동 watchlist + 거래대금/거래량 스크리너
- 종목별 평가:
  - 가격/등락률 조건 + 진입 게이트(check_entry_gate)
  - 통과 시 `signal_manager.create_signal(..., AUTO_TRADE)` 호출

## 3) `managers/signal_manager.py` (신호 저장/중복 제어)

- 신호 생성 요청을 받아 `pending_buy_signals`에 저장.
- 중복 방지:
  - 메모리 TTL(`processed_signals`)
  - DB 동일 일자(`detected_date`) 기존 신호 확인 후 update 처리
- 신호 상태 변경 API(`update_signal_status`) 제공.

## 4) `managers/buy_order_executor.py` (매수 주문 실행기)

- 주기(기본 60초)로 `PENDING` 신호 조회 후 순차 처리.
- 처리 단계:
  1. 신호 상태 `PROCESSING`
  2. 매수 가능 검증(시간/현금/포지션/게이트/중복)
  3. 현재가 조회
  4. 수량 계산
  5. 키움 매수 주문(재시도 포함)
- 주문 성공 시:
  - 신호 `ORDERED`
  - 포지션 생성 또는 추가매수 반영
  - 매수 알림(`notifications.trade_alert.notify_buy_async`)

## 5) `managers/stop_loss_manager.py` (청산/동기화 실행기)

- 주기(기본 120초) 루프:
  1. 설정 로드
  2. 매도 주문 체결 reconcile(계좌 잔고 기준)
  3. 포지션 가격/손익 동기화
  4. 장마감 청산 윈도우면 전량 청산 시도
  5. 장중 + 자동매매 ON이면 손절/익절/트레일링 판단
- 매도 우선순위:
  - `MARKET_CLOSE` > `TAKE_PROFIT` > `STOP_LOSS` > `PROFIT_LOCK` > `TRAILING` > `MANUAL`
- 중복 주문 방지:
  - 기존 PENDING/ORDERED 존재 시 `_prepare_sell`로 하위 우선순위 주문 정리 후 실행
- 체결 확정 시:
  - `SellOrder` `COMPLETED`
  - `Position` 청산 상태 전환
  - 매도 알림(`notify_sell_filled_async`)

## 6) `api/kiwoom_api.py` + `api/token_manager.py` (외부 API 계층)

- REST/WebSocket 호출 캡슐화.
- 레이트리밋 슬롯(`api_rate_limiter`)과 계좌조회 캐시 적용.
- 토큰 수명/재인증:
  - `TokenManager.get_valid_token()`
  - 계좌조회 `8005 token invalid` 감지 시 토큰 무효화 후 재인증+1회 재시도.

## 7) `notifications/*` (알림 모듈)

- `telegram_notifier.py`: 텔레그램 전송 공통.
- `trade_alert.py`: 매수/매도 체결 메시지 포맷/전송.
- 트레이딩 모듈은 이벤트 발생 시 비동기 알림 태스크로 호출.

## 8) 데이터 흐름(요약)

1. 스캐너가 후보 종목을 평가해 신호 생성  
2. 매수 실행기가 신호를 주문으로 전환하고 포지션 생성  
3. 손절 매니저가 포지션을 동기화/청산하며 sell_orders 관리  
4. 체결 결과가 DB와 대시보드 API에 반영되고 텔레그램으로 통지

## 9) 운영 점검 체크리스트

- `/trading/activity-log`의 `runtime`:
  - `scanner_running`, `buy_executor_running`, `stop_loss_running`
- `/stop-loss/status`:
  - `active_positions_count`, `recent_sell_orders`
- `/positions/?status=HOLDING`:
  - `items`/`positions`/`total` 값 일치 여부
- 토큰/인증 이슈:
  - 로그에서 `8005` 빈도 확인, 재인증 후 정상 회복 여부 확인

## 10) 시간대별 운영 점검 순서 (실무)

아래 순서는 "장전 → 장중 → 장마감 → 장후" 루틴으로 바로 사용할 수 있는 운영 체크리스트입니다.

### A. 장전 (08:40~08:59)

- **서버 상태 확인**
  - `server.bat status` 또는 `/trading/activity-log` 호출
  - `runtime.stop_loss_running`이 `true`인지 확인 (동기화 루프 필수)
- **인증/계좌 준비 확인**
  - `/trading/readiness`에서 `api_authenticated`, `account_configured` 확인
  - 직전 로그에 `8005` 연속 발생 흔적이 있으면 장 시작 전 재기동
- **자동매매 설정 확인**
  - `/trading/settings`에서 `is_enabled`, `trade_start_time`, `trade_end_time`,
    `liquidate_before_close`, `liquidate_time` 점검
  - 장마감 청산 구간은 운영 정책상 `15:05~15:20` 권장
- **보유 포지션/주문 정합성 확인**
  - `/positions/?status=HOLDING&limit=100`의 `items`=`positions`=`total` 일치 확인
  - `/sell-orders/?status=ORDERED` 잔존 건이 과도한지 확인

### B. 장중 (10:00~15:04, 매수 시작)

- **매수는 `trade_start_time`(기본 10:00) 이후에만** scanner·buy_executor가 동작
- 엔진 자동 기동(`auto_start_time`, 기본 08:00)과 매수 시작 시각은 별도

- **루프 정상 동작 감시 (5~10분 간격)**
  - `/trading/activity-log?limit=80`
  - `runtime.scanner_running`, `buy_executor_running`, `stop_loss_running` 모두 `true`
- **신호/주문 처리 지연 체크**
  - `/signals/pending?status=ALL&skip_price=true`에서 오래된 `PENDING` 누적 여부 확인
  - 필요 시 `/stop-loss/reconcile`로 매도 체결 상태 동기화
- **리스크 제한 체크**
  - 일일 손익 한도 도달 시 scanner가 자동 중지되므로 activity-log 경고 확인
- **에러 핫스팟 체크**
  - 로그에서 `429`, `8005`, `timeout` 패턴 빈도 확인
  - 급증 시 API 호출 주기/대상수 조정 또는 일시 중지 검토

### C. 장마감 직전/장마감 (15:05~15:20)

- **청산 윈도우 진입 확인**
  - `stop_loss_manager` heartbeat가 `장마감청산` 모드로 찍히는지 확인
- **청산 시도 확인**
  - activity-log에서 `"장마감 전량청산 시작"` 이벤트 확인
  - `sell_orders`에 `MARKET_CLOSE` 주문이 생성되는지 확인
- **중복 주문/불일치 방지 확인**
  - 동일 포지션의 `PENDING/ORDERED` 다중 누적이 없는지 확인
  - 현재 로직은 하위 우선순위 주문을 선취소 후 `MARKET_CLOSE` 실행
- **유의사항**
  - 15:20 이후는 장종료 리스크가 급증하므로 신규 청산 시도 지양
  - 모의투자 환경에서는 `RC4058(장종료)` 실패 코드가 자주 발생할 수 있음

### D. 장후 (15:21~종료)

- **결과 정리**
  - `/stop-loss/status`의 `recent_sell_orders`에서 `COMPLETED/FAILED` 사유 정리
  - 실패 건은 `sell_reason_detail` 기준 분류 (장종료/인증/API 제한)
- **데이터 정합성 점검**
  - HOLDING 수량과 계좌 보유 수량(kt00004)이 일치하는지 확인
  - 필요 시 `/positions/update-prices` 또는 `/stop-loss/reconcile` 실행
- **다음 거래일 준비**
  - 과도한 `FAILED` 누적 원인 분석(토큰/시간대/유동성)
  - 설정값(`liquidate_time`, 리스크 한도, 대상 필터) 재점검
  - 필요 시 서버 재기동으로 토큰/연결 상태 초기화
