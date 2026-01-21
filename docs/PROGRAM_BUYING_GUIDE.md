# 프로그램매수 내역 조회 가이드

## 1. 프로그램매수란?

프로그램매수는 기관투자자나 외국인 등이 알고리즘에 따라 자동으로 대량 매수하는 거래를 의미합니다. 프로그램매수 내역을 확인하면 시장의 수급 동향을 파악할 수 있습니다.

## 2. 필요한 데이터

프로그램매수 내역을 확인하기 위해 필요한 데이터:

### 필수 데이터
- **종목 코드**: 어떤 종목인지
- **날짜/시간**: 매수 발생 시점
- **매수량/매도량**: 프로그램매매 물량
- **순매수량**: 매수량 - 매도량
- **거래대금**: 프로그램매매 금액

### 보조 데이터
- **투자자 구분**: 기관/외국인/개인/프로그램
- **주가 변동**: 시가/종가/고가/저가
- **거래량**: 전체 거래량 대비 비율

## 3. 키움 API 활용 방법

### 방법 1: 계좌 잔고 조회 (자신의 체결 내역)

**API**: `get_account_balance()` (kt00004)

**사용 시나리오**:
- 자신의 계좌에서 체결된 주문 내역 확인
- 보유종목의 매입 정보 확인

**제한사항**:
- 자신의 계좌 내역만 조회 가능
- 시장 전체 프로그램매수는 조회 불가

### 방법 2: 주문 내역 조회 (DB 기반)

**API**: `GET /trading/orders`

**사용 시나리오**:
- 시스템에서 실행한 주문 내역 확인
- PENDING → ORDERED 상태 변경 추적

**제한사항**:
- 시스템 내부 주문만 조회
- 시장 전체 프로그램매수는 조회 불가

### 방법 3: 키움 API 체결 내역 조회 (추가 구현 필요)

키움 OpenAPI에서 체결 내역을 조회하는 API가 있을 수 있습니다:

**예상 API**:
- `kt10001`: 주문 체결 내역 조회
- `kt10002`: 미체결 주문 조회
- `kt10003`: 주문 상세 조회

**구현 필요**:
- 키움 API 문서에서 체결 내역 조회 TR 확인
- 해당 TR을 사용하여 체결 내역 조회 함수 구현

## 4. 시장 전체 프로그램매수 조회

시장 전체의 프로그램매수 내역을 조회하려면:

### 옵션 1: 한국거래소(KRX) 데이터
- KRX 정보데이터시스템에서 투자자별 매매동향 제공
- 프로그램매매 통계 데이터 제공

### 옵션 2: 금융정보 제공업체
- FnGuide, 인포스탁 등에서 프로그램매매 데이터 제공
- 유료 서비스일 수 있음

### 옵션 3: 키움 HTS 조건식
- 키움 HTS에서 프로그램매수 관련 조건식 생성
- 조건식 검색 API로 해당 종목 조회

## 5. 구현 시나리오

### 시나리오 1: 자신의 체결 내역 조회

```python
# 1. 계좌 잔고 조회
balance = await kiwoom_api.get_account_balance()

# 2. 보유종목에서 매입 정보 확인
holdings = balance.get('stk_acnt_evlt_prst', [])
for holding in holdings:
    stock_code = holding.get('stk_cd', '').replace('A', '')
    stock_name = holding.get('stk_nm', '')
    purchase_amount = holding.get('pur_amt', 0)  # 매입금액
    purchase_price = holding.get('avg_prc', 0)    # 평균단가
    quantity = holding.get('rmnd_qty', 0)        # 보유수량
    
    print(f"{stock_name}: {quantity}주, 평균단가 {purchase_price}원")
```

### 시나리오 2: 시스템 주문 내역 조회

```python
# GET /trading/orders
# DB에서 ORDERED 상태인 주문 조회
orders = await get_order_history()

for order in orders:
    print(f"{order['stock_name']}: {order['status']} - {order['detected_at']}")
```

### 시나리오 3: 체결 내역 조회 API 구현 (예시)

```python
async def get_order_executions(self, account_number: str, start_date: str = None, end_date: str = None):
    """주문 체결 내역 조회 (kt10001 예상)"""
    # 키움 API 호출
    # api-id: kt10001 (체결 내역 조회)
    # 파라미터: 계좌번호, 시작일, 종료일
    # 응답: 체결 내역 리스트
    pass
```

## 6. 추천 구현 방법

**현재 가능한 방법**:
1. **자신의 체결 내역**: `get_account_balance()` 사용
2. **시스템 주문 내역**: `GET /trading/orders` 사용

**추가 구현 필요**:
1. 키움 API 문서에서 체결 내역 조회 TR 확인
2. `get_order_executions()` 함수 구현
3. 프로그램매수 필터링 로직 추가

## 7. 프로그램매수 판별 기준

프로그램매수를 판별하기 위한 기준:

### 기본 기준
- 거래량이 평소 대비 급증 (2배 이상)
- 대량 매수 발생 (일정 금액 이상)
- 시간대별 집중 매수 (특정 시간대)

### 고급 기준
- 기관/외국인 순매수 동반
- 가격대별 매수 집중
- 거래 패턴 분석 (알고리즘 거래 특징)




