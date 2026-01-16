# 🔍 디버그 모드 사용 가이드

시스템 모니터링 프로세스를 상세하게 추적하고 분석할 수 있는 디버그 모드입니다.

## 🎯 주요 기능

1. **함수 호출 추적**: 실행되는 모든 함수의 호출 순서 기록
2. **실행 시간 측정**: 각 함수의 소요 시간 자동 측정
3. **체크포인트 로깅**: 함수 내부의 주요 단계별 상태 기록
4. **통계 분석**: 실행 횟수, 평균 시간, 가장 느린 호출 등

## 📝 디버그 모드 활성화

### 방법 1: API 호출

```bash
# 디버그 모드 활성화
curl -X POST http://localhost:5000/debug/enable

# 응답
{
  "message": "디버그 모드가 활성화되었습니다",
  "debug_enabled": true,
  "description": "이제 모든 함수 호출이 상세하게 로깅됩니다"
}
```

### 방법 2: 브라우저에서

```
http://localhost:5000/debug/enable
```

## 🔎 로그 확인

디버그 모드가 활성화되면 다음과 같은 형식의 로그가 출력됩니다:

```
┌─ 🔍 [BUY_EXECUTOR._process_pending_signals] 시작
│  ⏱️  [BUY_EXECUTOR] PENDING 신호 조회 시작 (시각: 22:45:30.123)
  ┌─ 🔍 [BUY_EXECUTOR._get_pending_signals] 시작
  └─ ✅ [BUY_EXECUTOR._get_pending_signals] 완료 (소요시간: 0.052초)
│  ⏱️  [BUY_EXECUTOR] 조회된 신호 개수: 3 (시각: 22:45:30.175)
│  ⏱️  [BUY_EXECUTOR] [1/3] 신호 처리 시작: 삼성전자(005930) (시각: 22:45:30.176)
  ┌─ 🔍 [BUY_EXECUTOR._process_single_signal] 시작
  │  ⏱️  [BUY_EXECUTOR] 상태 변경: PROCESSING (시각: 22:45:30.177)
  │  ⏱️  [BUY_EXECUTOR] 1단계: 매수 전 검증 시작 (시각: 22:45:30.180)
    ┌─ 🔍 [BUY_EXECUTOR._validate_buy_conditions] 시작
    └─ ✅ [BUY_EXECUTOR._validate_buy_conditions] 완료 (소요시간: 0.125초)
  │  ⏱️  [BUY_EXECUTOR] 1단계 결과: {'valid': True, 'reason': '검증 통과'} (시각: 22:45:30.305)
  │  ⏱️  [BUY_EXECUTOR] 2단계: 현재가 조회 시작 (시각: 22:45:30.306)
  │  ⏱️  [BUY_EXECUTOR] 2단계 결과: 현재가=70,000원 (시각: 22:45:30.450)
  │  ⏱️  [BUY_EXECUTOR] 3단계: 매수 수량 계산 시작 (시각: 22:45:30.451)
  │  ⏱️  [BUY_EXECUTOR] 3단계 결과: 수량=14주, 총액=980,000원 (시각: 22:45:30.455)
  │  ⏱️  [BUY_EXECUTOR] 4단계: 매수 주문 실행 (가격=70,000원, 수량=14주) (시각: 22:45:30.456)
  │  ⏱️  [BUY_EXECUTOR] 4단계 완료: 매수 주문 성공 (시각: 22:45:31.200)
  └─ ✅ [BUY_EXECUTOR._process_single_signal] 완료 (소요시간: 1.024초)
│  ⏱️  [BUY_EXECUTOR] [1/3] 신호 처리 완료, 5초 대기 (시각: 22:45:31.201)
...
└─ ✅ [BUY_EXECUTOR._process_pending_signals] 완료 (소요시간: 16.523초)
```

## 📊 통계 확인

### 실시간 통계 조회

```bash
curl http://localhost:5000/debug/status

# 응답
{
  "debug_enabled": true,
  "call_count": 15,
  "tracked_functions": [
    "BUY_EXECUTOR._process_pending_signals",
    "BUY_EXECUTOR._process_single_signal",
    "BUY_EXECUTOR._validate_buy_conditions",
    ...
  ],
  "execution_times": {
    "BUY_EXECUTOR._process_single_signal": {
      "avg": 1.024,
      "total": 3.072,
      "count": 3
    },
    ...
  }
}
```

### 통계 로그 출력

```bash
curl -X POST http://localhost:5000/debug/statistics
```

로그에 다음과 같은 통계가 출력됩니다:

```
================================================================================
📊 디버그 모드 - 실행 통계
================================================================================

🔢 함수 호출 횟수:
  - BUY_EXECUTOR._process_single_signal: 3회
  - BUY_EXECUTOR._validate_buy_conditions: 3회
  - STOP_LOSS._check_position_stop_loss: 5회
  ...

⏱️  평균 실행 시간:
  - BUY_EXECUTOR._process_single_signal: 평균 1.024초, 총 3.072초 (3회)
  - BUY_EXECUTOR._validate_buy_conditions: 평균 0.125초, 총 0.375초 (3회)
  - STOP_LOSS._check_position_stop_loss: 평균 0.350초, 총 1.750초 (5회)
  ...

🐌 가장 느린 실행:
  - BUY_EXECUTOR._process_single_signal: 1.200초 (2번째 호출)
  - BUY_EXECUTOR._execute_buy_order: 0.850초 (1번째 호출)
  ...
================================================================================
```

## 🛑 디버그 모드 비활성화

```bash
# 디버그 모드 비활성화 (자동으로 통계 출력)
curl -X POST http://localhost:5000/debug/disable

# 응답
{
  "message": "디버그 모드가 비활성화되었습니다",
  "debug_enabled": false,
  "description": "로그 레벨이 INFO로 변경되었습니다"
}
```

## 🔍 모니터링 프로세스 흐름 파악하기

### 1. 매수 주문 실행기 (BUY_EXECUTOR)

**실행 주기**: 60초마다

**처리 흐름**:
```
1. _load_auto_trade_settings()      # 자동매매 설정 로드
   └─ 설정 확인: 활성화 여부, 최대 투자금액, 손절/익절률

2. _process_pending_signals()        # PENDING 신호 처리
   └─ _get_pending_signals()         # DB에서 PENDING 상태 신호 조회
   
   각 신호에 대해:
   3. _process_single_signal()       # 개별 신호 처리
      ├─ 상태 변경: PROCESSING
      │
      ├─ 1단계: _validate_buy_conditions()  # 매수 전 검증
      │   ├─ 시장 시간 확인
      │   ├─ 계좌 잔고 확인
      │   ├─ 종목 상태 확인 (상장폐지, 거래정지)
      │   └─ 중복 주문 확인
      │
      ├─ 2단계: _get_current_price()        # 현재가 조회
      │   └─ 키움 API 호출 (캐싱 적용)
      │
      ├─ 3단계: _calculate_buy_quantity()   # 매수 수량 계산
      │   └─ 수량 = 최대투자금액 / 현재가
      │
      └─ 4단계: _execute_buy_order_with_retry()  # 매수 주문 실행
          └─ _execute_buy_order()
              ├─ 키움 API 매수 주문
              ├─ Position 생성 (DB 저장)
              └─ 상태 변경: ORDERED
   
   4. asyncio.sleep(5)               # 5초 대기 (API 제한)
```

### 2. 손절/익절 모니터링 (STOP_LOSS)

**실행 주기**: 120초(2분)마다

**처리 흐름**:
```
1. _load_auto_trade_settings()       # 자동매매 설정 로드

2. _monitor_positions()              # 포지션 모니터링
   └─ _get_active_positions()        # HOLDING 상태 포지션 조회
      ├─ DB에서 조회
      └─ 실제 계좌와 대조 (검증)
   
   각 포지션에 대해:
   3. _check_position_stop_loss()    # 손절/익절 확인
      ├─ _get_current_price()        # 현재가 조회
      │
      ├─ 손익 계산
      │   ├─ 손익금액 = (현재가 - 매수가) × 수량
      │   └─ 손익률 = (현재가 - 매수가) / 매수가 × 100
      │
      ├─ _update_position_price()    # 포지션 정보 업데이트
      │
      ├─ 손절/익절 판단
      │   ├─ 손익률 <= -손절률 → 손절
      │   └─ 손익률 >= 익절률 → 익절
      │
      └─ _execute_sell_order()       # (조건 충족 시) 매도 주문
          ├─ 키움 API 매도 주문
          ├─ SellOrder 생성 (DB 저장)
          └─ 포지션 상태 변경: SOLD
   
   4. asyncio.sleep(5)               # 5초 대기 (API 제한)
```

## 📌 체크포인트 설명

### BUY_EXECUTOR 체크포인트

| 체크포인트 | 의미 | 확인 사항 |
|-----------|------|----------|
| `PENDING 신호 조회 시작` | DB 쿼리 시작 | 신호가 있는지 확인 |
| `조회된 신호 개수: N` | 처리할 신호 수 | 0이면 대기 상태 |
| `상태 변경: PROCESSING` | 신호 처리 시작 | 중복 처리 방지 |
| `1단계: 매수 전 검증 시작` | 검증 로직 실행 | 장시간, 잔고, 중복 확인 |
| `1단계 결과: {...}` | 검증 통과 여부 | valid=False면 실패 |
| `2단계: 현재가 조회 시작` | API 호출 준비 | 캐시 적용 확인 |
| `2단계 결과: 현재가=N원` | 가격 확인 | 0원이면 실패 |
| `3단계: 매수 수량 계산 시작` | 수량 계산 | |
| `3단계 결과: 수량=N주` | 최종 주문 수량 | 1주 미만이면 실패 |
| `4단계: 매수 주문 실행` | 실제 주문 | 키움 API 호출 |
| `4단계 완료: 매수 주문 성공` | 주문 완료 | Position 생성 |

### STOP_LOSS 체크포인트

| 체크포인트 | 의미 | 확인 사항 |
|-----------|------|----------|
| `포지션 조회 시작` | DB 쿼리 시작 | |
| `조회된 포지션 개수: N` | 모니터링 대상 수 | 0이면 대기 |
| `현재가 조회: 종목코드` | API 호출 시작 | |
| `현재가: N원` | 현재 가격 | 캐시 적용 확인 |
| `손익: +/-N원 (+/-N%)` | 현재 손익 상태 | 손절/익절 기준과 비교 |

## 🎯 사용 예시

### 시나리오 1: 매수 주문 프로세스 추적

```bash
# 1. 디버그 모드 활성화
curl -X POST http://localhost:5000/debug/enable

# 2. 조건식 모니터링 시작 (신호 발생 대기)
curl -X POST http://localhost:5000/monitoring/start

# 3. 로그 모니터링 (터미널에서 실시간 확인)
# - 신호 포착 → PENDING 상태
# - 60초 후 BUY_EXECUTOR 실행
# - 4단계 처리 과정 확인

# 4. 5분 후 통계 확인
curl -X POST http://localhost:5000/debug/statistics

# 5. 디버그 모드 비활성화
curl -X POST http://localhost:5000/debug/disable
```

### 시나리오 2: 손절/익절 모니터링 추적

```bash
# 1. 디버그 모드 활성화
curl -X POST http://localhost:5000/debug/enable

# 2. 포지션이 있는 상태에서 120초 대기
# - STOP_LOSS 매니저 실행
# - 각 포지션의 손익 계산 확인
# - 손절/익절 조건 판단 과정 확인

# 3. 실시간 상태 확인
curl http://localhost:5000/debug/status

# 4. 디버그 모드 비활성화
curl -X POST http://localhost:5000/debug/disable
```

## ⚠️ 주의사항

1. **성능 영향**: 디버그 모드는 로깅으로 인해 약간의 성능 저하가 있을 수 있습니다
2. **로그 크기**: 장시간 사용 시 로그 파일이 커질 수 있습니다
3. **운영 환경**: 운영 환경에서는 필요한 경우에만 사용하고 사용 후 즉시 비활성화하세요
4. **민감정보**: 로그에 주문 정보, 계좌 정보 등이 포함될 수 있으므로 주의하세요

## 🔧 고급 사용법

### Python 코드에서 직접 사용

```python
from debug_tracer import debug_tracer

# 함수 데코레이터 적용
@debug_tracer.trace_async(component="MY_MODULE")
async def my_async_function():
    # 체크포인트 추가
    debug_tracer.log_checkpoint("작업 시작", "MY_MODULE")
    
    # ... 작업 수행 ...
    
    debug_tracer.log_checkpoint("작업 완료", "MY_MODULE")

# 동기 함수
@debug_tracer.trace_sync(component="MY_MODULE")
def my_sync_function():
    pass
```

## 📊 분석 팁

1. **병목 지점 찾기**: 가장 느린 함수를 찾아 최적화
2. **호출 빈도 확인**: 불필요하게 자주 호출되는 함수 파악
3. **실패 지점 추적**: 에러 발생 지점의 체크포인트 확인
4. **API 호출 패턴**: 현재가 조회 등 API 호출 빈도 분석

---

**작성일**: 2026-01-15  
**버전**: 1.0.0


