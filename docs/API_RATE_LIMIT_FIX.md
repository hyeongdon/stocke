# 429 API Rate Limit 에러 해결 방법 (최종본)

## 🚨 문제 상황
```
2026-01-15 22:34:06,057 - kiwoom_api - ERROR - 현재가 조회 API 호출 실패: 429
2026-01-15 22:39:06,289 - kiwoom_api - ERROR - 응답 본문: {"return_msg":"허용된 요청 개수를 초과하였습니다[1700:허용된 요청 개수를 초과하였습니다. API ID=ka10081]","return_code":5}
```

## 📋 원인 분석

### 429 에러란?
- **HTTP 429 Too Many Requests**: API 호출 횟수 제한 초과
- 키움 OpenAPI의 **매우 엄격한** Rate Limit:
  - **1초당 1회 호출**
  - **1분당 20회 호출** (API ID별)

### 근본 원인
여러 백그라운드 작업이 **동시에** `get_current_price()` 호출:
1. `main.py` - `/signals/pending` API (시그널 조회)
2. `buy_order_executor.py` - **10초마다** 매수 주문 처리
3. `stop_loss_manager.py` - **30초마다** 손절/익절 체크
4. 대시보드 - **30초마다** 자동 새로고침

**예시 계산:**
- 시그널 10개 × 10초마다 = 분당 60회
- 포지션 5개 × 30초마다 = 분당 10회
- 대시보드 = 분당 2회
- **총 분당 72회** → 키움 제한(20회) **3.6배 초과!**

## ✅ 적용된 해결책 (완전 개편)

### 1. 현재가 캐싱 대폭 강화 (kiwoom_api.py)
```python
# 현재가 캐시 TTL 대폭 증가
self._price_cache = {}
self._price_cache_ttl = 30  # 3초 → 30초 (10배 증가)

# 캐시 확인 로직
if stock_code in self._price_cache:
    price, timestamp = self._price_cache[stock_code]
    age = datetime.now().timestamp() - timestamp
    if age < self._price_cache_ttl:
        # 캐시 사용 - API 호출 없이 즉시 반환
        return price
```

**효과**: 같은 종목은 30초 이내 API 호출 **완전 제거**

### 2. 백그라운드 작업 주기 대폭 증가
```python
# buy_order_executor.py
await asyncio.sleep(60)  # 10초 → 60초 (6배 증가)
await asyncio.sleep(5)   # 각 항목 간 대기: 1초 → 5초

# stop_loss_manager.py  
self.monitoring_interval = 120  # 30초 → 120초 (4배 증가)
await asyncio.sleep(5)   # 각 항목 간 대기: 1초 → 5초
```

**효과**: 백그라운드 API 호출 **1/6 ~ 1/4로 감소**

### 3. 대시보드 API 호출 최적화
```python
# main.py - /signals/pending
# DB 저장 가격 우선 사용, skip_price=true 기본값
current_price = getattr(r, "target_price", 0) or 0

# static/modules/signal-lifecycle.js
setInterval(() => {}, 60000);  // 30초 → 60초 (2배 증가)
```

**효과**: 대시보드 API 호출 **1/2로 감소**

### 4. API Rate Limiter 강화 (api_rate_limiter.py)
```python
self.max_calls_per_window = 12  # 1분당 12회 (키움 제한의 60%)
self.min_call_interval = 5.0    # 5초 간격 (매우 안전)
```

**효과**: 전역 제한 강화

### 5. 상세 로그 추가
429 에러 발생 시 상세 정보 출력:
- 종목코드, API URL, 응답 헤더
- 키움 에러 메시지
- 해결 방법 안내

## 📊 예상 효과 (최종)

### 이전 (문제 상황)
```
백그라운드 작업:
- buy_order_executor: 10초마다 (시그널 10개 = 분당 60회)
- stop_loss_manager: 30초마다 (포지션 5개 = 분당 10회)
- 대시보드: 30초마다 (분당 2회)
총 분당 API 호출: ~72회 → 키움 제한(20회) 3.6배 초과!

결과: 429 에러 연속 발생
```

### 이후 (수정 후)
```
백그라운드 작업:
- buy_order_executor: 60초마다 (시그널 10개 = 분당 10회)
- stop_loss_manager: 120초마다 (포지션 5개 = 분당 2.5회)
- 대시보드: 60초마다 (분당 1회)
총 분당 API 호출: ~13.5회

+ 캐싱 효과 (30초 TTL): 실제 호출 50% 감소
= 실제 API 호출: 분당 약 6~7회

결과: 키움 제한(20회) 대비 30% 수준 → 매우 안전!
```

### 개선율
- API 호출 빈도: **72회 → 7회 (90% 감소!)**
- 캐시 적용률: 약 50% (30초 내 재조회)
- 429 에러: **완전 제거 예상**

## 🧪 테스트 방법

### 1. 서버 재시작
```bash
# 현재 서버 종료 (Ctrl+C)
# 서버 재시작
venv\Scripts\python.exe main.py
```

### 2. 로그 확인
```bash
# 캐시 적용 확인
💾 [CACHE_HIT] 005930 캐시 사용 (나이: 2.1초)
💾 현재가 조회 성공 (캐시 저장): 005930 = 70,000원

# API 호출 간격 확인
🚫 [API_LIMITER_DEBUG] ⚠️ 호출 간격 부족 - 4.2초 대기 필요 (최소 간격: 5.0초)
```

### 3. 대시보드 확인
- http://localhost:5000/static/signal-lifecycle.html
- 시그널 데이터가 정상적으로 로드되는지 확인
- 429 에러가 사라졌는지 확인

## 📌 추가 권장 사항

### 1. 불필요한 조회 최소화
- 대시보드 자동 새로고침 간격 늘리기 (30초 → 60초)
- 여러 탭을 동시에 열지 않기

### 2. 피크 타임 주의
- 장 시작/마감 시간대 API 호출 집중
- 이 시간대에는 더 조심스럽게 사용

### 3. 모니터링
- 로그에서 `429 ERROR` 키워드 검색
- 여전히 발생하면 `min_call_interval`을 더 늘리기 (6초, 7초...)

## 🔍 문제 지속 시 추가 조치

만약 429 에러가 계속 발생하면:

1. **API 호출 간격 더 늘리기**
   ```python
   # api_rate_limiter.py
   self.min_call_interval = 7.0  # 7초로 증가
   ```

2. **캐시 TTL 늘리기**
   ```python
   # kiwoom_api.py
   self._price_cache_ttl = 5  # 5초로 증가
   ```

3. **1분당 호출 수 더 줄이기**
   ```python
   # api_rate_limiter.py
   self.max_calls_per_window = 8  # 8회로 감소
   ```

## 📚 키움 API 제한 정책

- **공식 제한**: 1초당 1회, 1분당 20회
- **안전 마진**: 실제로는 더 보수적으로 설정 필요
- **권장 설정**: 1분당 10~12회 (50~60% 수준)

## ✅ 체크리스트

- [x] 현재가 캐싱 강화 (3초 → 30초)
- [x] 백그라운드 작업 주기 증가 (10초 → 60초, 30초 → 120초)
- [x] API 호출 간격 5초로 증가
- [x] 1분당 호출 수 12회로 제한
- [x] 대시보드 새로고침 주기 증가 (30초 → 60초)
- [x] /signals/pending API 최적화 (DB 가격 우선 사용)
- [x] 429 에러 상세 로그 추가
- [ ] **서버 재시작 후 테스트**
- [ ] 로그에서 캐시 적용 확인
- [ ] 429 에러 완전 제거 확인

---

## 📝 변경사항 요약

| 파일 | 변경 내용 | 효과 |
|------|-----------|------|
| `kiwoom_api.py` | 캐시 TTL: 3초 → 30초 | API 호출 50% 감소 |
| `buy_order_executor.py` | 주기: 10초 → 60초, 대기: 1초 → 5초 | API 호출 83% 감소 |
| `stop_loss_manager.py` | 주기: 30초 → 120초, 대기: 1초 → 5초 | API 호출 75% 감소 |
| `api_rate_limiter.py` | 제한: 40회/분 → 12회/분, 간격: 1.5초 → 5초 | 전역 제한 강화 |
| `main.py` | DB 가격 우선 사용 | API 호출 최소화 |
| `static/modules/signal-lifecycle.js` | 새로고침: 30초 → 60초 | API 호출 50% 감소 |

**전체 효과**: API 호출 **90% 감소** (72회/분 → 7회/분)

---

**최종 수정일**: 2026-01-15  
**테스트 필요**: 서버 재시작 후 최소 5분간 모니터링  
**예상 결과**: 429 에러 완전 제거

