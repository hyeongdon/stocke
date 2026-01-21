# 급등 종목 조회 가이드

## 1. 필요한 데이터

급등 종목을 판별하기 위해 다음 데이터가 필요합니다:

### 필수 데이터
- **현재가 (current_price)**: 실시간 주가
- **전일 종가 (prev_close)**: 전일 대비 계산 기준
- **등락률 (change_rate)**: 전일 대비 가격 변동률 (%)
- **거래량 (volume)**: 당일 거래량
- **전일 거래량 (prev_volume)**: 거래량 급증 판단용

### 보조 데이터
- **시가 (open_price)**: 시가 대비 상승률 계산
- **고가 (high_price)**: 당일 최고가
- **52주 최고가 (52w_high)**: 장기 돌파 여부 확인
- **시가총액 (market_cap)**: 페니주식 필터링용
- **이동평균선 (MA)**: 20일, 50일, 200일선

## 2. 키움 API 활용 방법

### 방법 1: 조건식 검색 활용 (권장)

키움증권 조건식 검색 API를 활용하여 급등 종목 조건식을 만들어 사용합니다.

**API**: `search_condition_stocks()` (WebSocket CNSRREQ)

**조건식 예시**:
```
급등 종목 조건:
- 등락률 >= 5%
- 거래량 >= 전일 거래량의 2배
- 현재가 >= 1000원 (페니주식 제외)
- 시가총액 >= 100억원
```

**장점**:
- 키움증권 HTS에서 조건식을 미리 만들어 사용 가능
- API 호출 1회로 여러 종목 조회
- 실시간 데이터 제공

**단점**:
- 조건식을 미리 만들어야 함
- 조건식 개수 제한 (보통 200개)

### 방법 2: 차트 데이터 활용

개별 종목의 차트 데이터를 조회하여 급등 여부 판단

**API**: `get_stock_chart_data()` (ka10080, ka10081)

**사용 시나리오**:
1. 관심 종목 리스트 준비
2. 각 종목의 최근 일봉 데이터 조회
3. 전일 대비 등락률 계산
4. 거래량 증가율 계산
5. 필터링 및 정렬

**장점**:
- 유연한 필터링 조건 설정 가능
- 추가 기술적 지표 계산 가능

**단점**:
- API 호출 횟수가 많음 (종목 수만큼)
- API 제한에 걸릴 수 있음

### 방법 3: 현재가 조회 활용

개별 종목의 현재가를 조회하여 급등 여부 판단

**API**: `get_current_price()` (ka10081)

**사용 시나리오**:
1. 관심 종목 리스트 준비
2. 각 종목의 현재가 조회
3. 전일 종가와 비교하여 등락률 계산
4. 필터링 및 정렬

**장점**:
- 가장 정확한 실시간 가격
- 캐싱으로 API 호출 최소화

**단점**:
- 전일 종가 정보가 별도로 필요
- API 호출 횟수가 많음

## 3. 구현 시나리오

### 시나리오 1: 조건식 기반 급등 종목 조회 (권장)

```python
# 1. 급등 종목 조건식 생성 (키움 HTS에서)
# 조건식 ID: 예) "001" (급등종목 조건식)

# 2. API 호출
stocks = await kiwoom_api.search_condition_stocks(
    condition_id="001",
    condition_name="급등종목"
)

# 3. 추가 필터링 (필요시)
surge_stocks = [
    stock for stock in stocks
    if float(stock['change_rate']) >= 5.0  # 5% 이상 상승
    and int(stock['volume']) > 1000000  # 거래량 100만주 이상
]

# 4. 정렬 (등락률 기준 내림차순)
surge_stocks.sort(key=lambda x: float(x['change_rate']), reverse=True)
```

### 시나리오 2: 차트 데이터 기반 급등 종목 조회

```python
# 1. 관심 종목 리스트 준비
watchlist = ["005930", "000660", "035420", ...]  # 삼성전자, SK하이닉스, NAVER 등

# 2. 각 종목의 최근 일봉 데이터 조회
surge_stocks = []
for stock_code in watchlist:
    chart_data = await kiwoom_api.get_stock_chart_data(stock_code, "1D")
    
    if not chart_data or len(chart_data) < 2:
        continue
    
    # 최근 2일 데이터
    today = chart_data[-1]
    yesterday = chart_data[-2]
    
    # 등락률 계산
    change_rate = ((today['close'] - yesterday['close']) / yesterday['close']) * 100
    
    # 거래량 증가율 계산
    volume_ratio = today['volume'] / yesterday['volume'] if yesterday['volume'] > 0 else 0
    
    # 급등 조건 확인
    if change_rate >= 5.0 and volume_ratio >= 2.0:
        surge_stocks.append({
            'stock_code': stock_code,
            'change_rate': change_rate,
            'volume_ratio': volume_ratio,
            'current_price': today['close'],
            'volume': today['volume']
        })

# 3. 정렬
surge_stocks.sort(key=lambda x: x['change_rate'], reverse=True)
```

### 시나리오 3: 실시간 모니터링 기반 급등 종목 탐지

```python
# 1. 주기적으로 관심 종목 모니터링 (예: 1분마다)
async def monitor_surge_stocks():
    while True:
        # 2. 관심 종목 리스트에서 현재가 조회
        watchlist = get_watchlist()  # 관심종목 목록
        
        for stock_code in watchlist:
            # 3. 현재가 조회
            current_price = await kiwoom_api.get_current_price(stock_code)
            
            # 4. 전일 종가 조회 (DB 또는 캐시에서)
            prev_close = get_prev_close(stock_code)
            
            if not current_price or not prev_close:
                continue
            
            # 5. 등락률 계산
            change_rate = ((current_price - prev_close) / prev_close) * 100
            
            # 6. 급등 조건 확인
            if change_rate >= 5.0:
                # 7. 알림 발송 또는 DB 저장
                notify_surge_stock(stock_code, change_rate, current_price)
        
        # 8. 1분 대기
        await asyncio.sleep(60)
```

## 4. 추천 구현 방법

**가장 효율적인 방법**: **조건식 기반 조회**

이유:
1. API 호출 횟수 최소화 (1회 호출로 여러 종목 조회)
2. 키움증권 HTS에서 조건식을 미리 테스트 가능
3. 실시간 데이터 제공
4. API 제한에 걸릴 가능성 낮음

**구현 단계**:
1. 키움증권 HTS에서 급등 종목 조건식 생성
2. 조건식 ID 확인
3. `search_condition_stocks()` API 호출
4. 결과 필터링 및 정렬
5. 프론트엔드에 표시

## 5. 급등 종목 판별 기준 예시

### 기본 기준
- 등락률 >= 5% (상승)
- 거래량 >= 전일 대비 2배
- 현재가 >= 1,000원 (페니주식 제외)

### 고급 기준
- 등락률 >= 10% (급등)
- 거래량 >= 전일 대비 5배 (거래량 급증)
- 시가 대비 상승률 >= 3% (시가 돌파)
- 52주 최고가 근접 (90% 이상)
- 이동평균선 상향 돌파

## 6. API 엔드포인트 제안

```python
@app.get("/stocks/surge")
async def get_surge_stocks(
    min_change_rate: float = 5.0,  # 최소 등락률 (%)
    min_volume_ratio: float = 2.0,  # 최소 거래량 비율
    limit: int = 50,  # 최대 조회 개수
    condition_id: Optional[str] = None  # 조건식 ID (선택)
):
    """급등 종목 조회"""
    # 구현...
```




