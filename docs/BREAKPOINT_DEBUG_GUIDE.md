# 🐛 브레이크포인트 디버깅 가이드

VS Code/Cursor에서 브레이크포인트를 찍어가며 코드 실행을 단계별로 추적하는 방법입니다.

## 🎯 디버깅 준비

### 1. 필요한 파일 확인
- ✅ `.vscode/launch.json` - 디버깅 설정 파일
- ✅ `test_buy_executor_debug.py` - 매수 주문 실행기 테스트
- ✅ `test_stop_loss_debug.py` - 손절/익절 모니터링 테스트

### 2. Python 디버거 설치 확인
VS Code/Cursor에서 Python Extension이 설치되어 있어야 합니다.

```bash
# 확장 프로그램에서 "Python" 검색 후 설치
```

## 📍 브레이크포인트 사용 방법

### 기본 조작법

1. **브레이크포인트 설정**: 코드 줄 번호 왼쪽 클릭 (빨간 점 생김)
2. **디버깅 시작**: F5 또는 상단 메뉴 "실행 > 디버깅 시작"
3. **단계별 실행**:
   - **F10** (Step Over): 다음 줄로 이동 (함수 내부로 들어가지 않음)
   - **F11** (Step Into): 함수 내부로 들어가기
   - **Shift+F11** (Step Out): 현재 함수 빠져나오기
   - **F5** (Continue): 다음 브레이크포인트까지 실행

4. **변수 확인**:
   - 왼쪽 사이드바 "변수" 탭에서 모든 변수 확인
   - 마우스 오버로 변수 값 즉시 확인
   - "조사식" 탭에서 표현식 평가

5. **디버그 콘솔**:
   - 하단 "디버그 콘솔"에서 실시간으로 코드 실행
   - 변수 출력, 함수 호출 등 가능

## 🔍 시나리오별 디버깅

### 시나리오 1: 매수 주문 프로세스 추적

#### 1단계: 디버깅 시작

1. **`test_buy_executor_debug.py` 파일 열기**

2. **브레이크포인트 설정 (추천 위치)**:
   ```python
   # 51줄: executor 생성 후
   print(f"   - 인스턴스 생성 완료: {executor}")
   ⬅️ 여기 클릭 (빨간 점)
   
   # 60줄: 설정 로드 후
   if executor.auto_trade_settings:
   ⬅️ 여기 클릭
   
   # 70줄: 신호 조회 후
   print(f"   - 발견된 신호 개수: {len(pending_signals)}")
   ⬅️ 여기 클릭
   
   # 84줄: 검증 결과 확인
   print(f"   - 검증 결과: {validation_result}")
   ⬅️ 여기 클릭
   ```

3. **디버깅 시작**:
   - `F5` 누르기
   - 또는 상단 메뉴: "실행 > 디버깅 시작"
   - 디버깅 구성 선택: **"Python: 매수 주문 실행기 단독 테스트"**

#### 2단계: 단계별 실행 및 확인

**브레이크포인트 1: executor 생성 확인**
```
멈춘 위치: 51줄
확인할 것:
- executor 객체가 제대로 생성되었는지
- executor.kiwoom_api가 초기화되었는지
- executor.is_running = False인지

변수 탭에서 확인:
- executor
  - kiwoom_api
  - is_running
  - max_retry_attempts
```

**브레이크포인트 2: 설정 로드 확인**
```
멈춘 위치: 60줄
확인할 것:
- executor.auto_trade_settings.is_enabled
- executor.auto_trade_settings.max_invest_amount
- executor.auto_trade_settings.stop_loss_rate
- executor.auto_trade_settings.take_profit_rate

디버그 콘솔에서 실행:
>>> executor.auto_trade_settings.max_invest_amount
1000000
```

**브레이크포인트 3: 신호 조회 확인**
```
멈춘 위치: 70줄
확인할 것:
- pending_signals 리스트 길이
- 각 신호의 stock_code, stock_name, status

디버그 콘솔에서 실행:
>>> len(pending_signals)
3
>>> pending_signals[0].stock_name
'삼성전자'
>>> pending_signals[0].status
'PENDING'
```

**브레이크포인트 4: 검증 결과 확인**
```
멈춘 위치: 84줄
확인할 것:
- validation_result['valid'] = True/False
- validation_result['reason']

만약 valid=False라면:
- 어떤 검증에서 실패했는지 확인
- reason을 보고 원인 파악
```

#### 3단계: 함수 내부로 들어가기

특정 함수가 어떻게 동작하는지 보고 싶다면:

1. **함수 호출 줄에 브레이크포인트 설정**:
   ```python
   validation_result = await executor._validate_buy_conditions(test_signal)
   ⬅️ 여기서 멈춤
   ```

2. **F11 (Step Into) 눌러서 함수 내부로 진입**

3. **`buy_order_executor.py` 파일의 `_validate_buy_conditions` 함수로 이동**

4. **F10으로 한 줄씩 실행하며 로직 확인**:
   ```python
   # 시장 시간 확인
   now = datetime.now()
   ⬅️ F10: now 변수에 현재 시간 저장됨
   
   if not self._is_market_open(now):
   ⬅️ F10: 조건 평가, 변수 탭에서 결과 확인
   
   # 계좌 잔고 확인
   account_info = await self._get_account_info()
   ⬅️ F11: _get_account_info() 내부로 진입 (원한다면)
   ⬅️ F10: 결과만 받고 다음으로
   ```

### 시나리오 2: 손절/익절 판단 과정 추적

#### 1단계: 디버깅 시작

1. **`test_stop_loss_debug.py` 파일 열기**

2. **브레이크포인트 설정 (추천 위치)**:
   ```python
   # 46줄: 포지션 조회 후
   print(f"   - 발견된 포지션 개수: {len(positions)}")
   ⬅️ 여기 클릭
   
   # 76줄: 손익 계산 후
   print(f"   - 손익금액: {profit_loss:+,}원")
   ⬅️ 여기 클릭
   
   # 91줄: 판단 결과
   if profit_loss_rate <= -stop_loss_rate:
   ⬅️ 여기 클릭
   ```

3. **디버깅 시작**: F5 → **"Python: 손절 매니저 단독 테스트"** 선택

#### 2단계: 손익 계산 과정 추적

**브레이크포인트: 76줄 (손익 계산 후)**
```
확인할 변수:
- test_position.buy_price: 매수가
- current_price: 현재가
- profit_loss: 손익금액 = (현재가 - 매수가) × 수량
- profit_loss_rate: 손익률 = (현재가 - 매수가) / 매수가 × 100

디버그 콘솔에서 계산 확인:
>>> test_position.buy_price
70000
>>> current_price
75000
>>> profit_loss
5000 * test_position.buy_quantity
>>> profit_loss_rate
7.14
```

**브레이크포인트: 91줄 (판단 조건)**
```
조건식 평가 확인:
>>> profit_loss_rate
7.14
>>> stop_loss_rate
5.0
>>> take_profit_rate
10.0
>>> profit_loss_rate <= -stop_loss_rate
False  # 손절 아님
>>> profit_loss_rate >= take_profit_rate
False  # 익절 아님

결론: 보유 유지
```

### 시나리오 3: FastAPI 서버 디버깅

실제 서버를 디버깅 모드로 실행하여 API 호출 시 동작 확인

#### 1단계: 서버 디버깅 시작

1. **`main.py` 파일 열기**

2. **API 엔드포인트에 브레이크포인트 설정**:
   ```python
   @app.get("/signals/pending")
   async def get_pending_signals(limit: int = 100, status: str = "PENDING"):
       """매수대기(PENDING) 신호 목록 조회"""
       try:
           logger.info(f"[PENDING_API] request: limit={limit} status={status}")
           ⬅️ 여기 브레이크포인트
           items = []
   ```

3. **디버깅 시작**: F5 → **"Python: FastAPI 서버 디버깅"** 선택

4. **서버 시작 대기** (콘솔에 "Uvicorn running on..." 표시됨)

#### 2단계: API 요청하여 브레이크포인트 트리거

브라우저나 터미널에서 API 요청:

```bash
# 브라우저에서
http://localhost:8000/signals/pending

# 또는 터미널에서
curl http://localhost:8000/signals/pending
```

→ 브레이크포인트에서 멈춤!

#### 3단계: 요청 처리 과정 추적

```
F10으로 한 줄씩 실행하며 확인:

1. 파라미터 확인
   - limit: 100
   - status: "PENDING"

2. DB 쿼리 실행
   - q = session.query(PendingBuySignal)
   - rows = q.filter(...).all()
   ⬅️ rows 변수에서 조회 결과 확인

3. 각 row 처리
   - for i, r in enumerate(rows):
   ⬅️ r 변수에서 신호 데이터 확인

4. 응답 생성
   - payload = {"items": items, ...}
   ⬅️ payload 변수에서 최종 응답 확인
```

## 🎓 고급 기능

### 조건부 브레이크포인트

특정 조건에서만 멈추고 싶을 때:

1. 브레이크포인트 우클릭
2. "브레이크포인트 편집..."
3. 조건 입력 예:
   ```python
   profit_loss_rate <= -5  # 손실률이 -5% 이하일 때만
   len(pending_signals) > 0  # 신호가 있을 때만
   stock_code == "005930"  # 삼성전자일 때만
   ```

### 로그 포인트

코드를 멈추지 않고 로그만 출력:

1. 브레이크포인트 위치에서 우클릭
2. "로그 포인트 추가..."
3. 메시지 입력 예:
   ```
   신호 처리 중: {signal.stock_name}, 가격: {current_price}
   ```

### 호출 스택 확인

함수가 어떤 경로로 호출되었는지 확인:

- 왼쪽 사이드바 "호출 스택" 탭
- 각 프레임 클릭하여 호출 경로 추적
- 예:
  ```
  test_buy_executor [Line 84]
  └─ _validate_buy_conditions [Line 160]
     └─ _is_market_open [Line 195]
        ⬅️ 현재 위치
  ```

## 📊 실전 디버깅 예시

### 예시 1: "왜 매수 주문이 안 되지?"

```python
# test_buy_executor_debug.py 실행
# F5로 시작

# 브레이크포인트 1: 84줄에서 멈춤
validation_result = {'valid': False, 'reason': '시장 시간이 아님'}
⬅️ 아하! 시장 시간이 아니어서 실패

# 해결: Config.ALLOW_OUT_OF_MARKET_TRADING = True 설정
# 또는 장 시간에 다시 테스트
```

### 예시 2: "손익률이 제대로 계산되나?"

```python
# test_stop_loss_debug.py 실행
# 76줄 브레이크포인트

# 디버그 콘솔에서 수동 계산 확인
>>> buy_price = test_position.buy_price
>>> current_price = 75000
>>> (current_price - buy_price) / buy_price * 100
7.142857142857143

>>> profit_loss_rate
7.14  # 올바르게 계산됨!
```

### 예시 3: "API 호출이 너무 많이 발생하는데?"

```python
# kiwoom_api.py의 get_current_price 함수
# 636줄에 브레이크포인트

# 호출될 때마다 멈춤
# 호출 스택에서 어디서 호출했는지 확인:
main.py:281 get_pending_signals
⬅️ /signals/pending API에서 13번 호출!

# 캐시가 작동하는지 확인:
>>> stock_code in self._price_cache
True  # 캐시 있음
>>> age < self._price_cache_ttl
True  # TTL 이내
⬅️ 캐시 사용, API 호출 없음!
```

## 💡 디버깅 팁

### 1. 작은 단위로 테스트
- 전체 서버 대신 단독 테스트 스크립트 사용
- `test_buy_executor_debug.py` - 매수 프로세스만
- `test_stop_loss_debug.py` - 손절 프로세스만

### 2. 변수 감시 (Watch)
- 중요한 변수를 "조사식"에 추가
- 실행 중 계속 값 추적
- 예: `profit_loss_rate`, `current_price`, `validation_result`

### 3. 예외 발생 시 자동 중단
- 디버깅 설정에서 "예외 발생 시 중단" 활성화
- 오류 발생 즉시 해당 위치로 이동
- 변수 상태 확인 가능

### 4. 조건부 브레이크포인트 활용
- 특정 종목 코드만 추적
- 특정 조건(손실률 등)에서만 멈춤
- 반복문에서 특정 인덱스만 확인

### 5. 디버그 콘솔 적극 활용
- 변수 출력: `print(validation_result)`
- 표현식 평가: `len(pending_signals)`
- 함수 호출: `await executor._get_current_price("005930")`

## 🚀 빠른 시작 체크리스트

- [ ] `.vscode/launch.json` 파일 확인
- [ ] Python Extension 설치 확인
- [ ] `test_buy_executor_debug.py` 열기
- [ ] 추천 위치에 브레이크포인트 설정 (51, 60, 70, 84줄)
- [ ] F5 눌러서 디버깅 시작
- [ ] F10으로 한 줄씩 실행하며 변수 확인
- [ ] 함수 내부 보고 싶으면 F11
- [ ] 변수 탭, 호출 스택 탭 확인
- [ ] 디버그 콘솔에서 실험

---

**작성일**: 2026-01-15  
**파일 위치**: 
- `.vscode/launch.json` - 디버깅 설정
- `test_buy_executor_debug.py` - 매수 프로세스 테스트
- `test_stop_loss_debug.py` - 손절 프로세스 테스트

**이제 F5를 눌러서 브레이크포인트 디버깅을 시작하세요!** 🐛


