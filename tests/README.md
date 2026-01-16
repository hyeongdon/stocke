# 테스트 파일 구조

주식 자동매매 시스템의 테스트 파일들을 기능별로 분류했습니다.

## 📁 폴더 구조

```
tests/
├── buy_order/          # 매수 주문 관련 테스트
├── signal/             # 신호 생성 및 관리 테스트
├── stop_loss/          # 손절/익절 관리 테스트
└── api/                # API 연동 및 외부 서비스 테스트
```

---

## 🛒 buy_order/ - 매수 주문 테스트

### test_buy_order.py
**용도**: 매수 주문 프로세스 전체 테스트
```bash
# DRY-RUN 모드 (주문 실행 안함)
python tests/buy_order/test_buy_order.py

# 실제 주문 실행
python tests/buy_order/test_buy_order.py --execute

# 특정 신호로 주문
python tests/buy_order/test_buy_order.py --signal-id 123 --execute
```

### test_buy_debug.py
**용도**: 매수 주문 각 단계별 상세 디버깅
```bash
python tests/buy_order/test_buy_debug.py
```
- 자동매매 설정 확인
- 신호 검증 단계별 확인
- 현재가 조회 및 수량 계산 확인

### test_buy_executor_debug.py
**용도**: BuyOrderExecutor 내부 로직 디버깅
```bash
python tests/buy_order/test_buy_executor_debug.py
```

---

## 📡 signal/ - 신호 생성 및 관리 테스트

### test_signal_manager.py
**용도**: 매수 신호 생성 및 중복 방지 테스트
```bash
# 신호 생성
python tests/signal/test_signal_manager.py --stock-code 005930 --stock-name "삼성전자"

# 특정 조건식으로 신호 생성
python tests/signal/test_signal_manager.py --stock-code 005930 --stock-name "삼성전자" --condition-id 1

# 신호 타입 지정
python tests/signal/test_signal_manager.py --stock-code 005930 --stock-name "삼성전자" --signal-type reference
```

### test_signal_creation_debug.py
**용도**: 신호 생성 프로세스 디버깅

### test_condition_monitor.py
**용도**: 조건식 모니터링 테스트
```bash
python tests/signal/test_condition_monitor.py
```

---

## 🛑 stop_loss/ - 손절/익절 관리 테스트

### test_stop_loss.py
**용도**: 손절/익절 매니저 기능 테스트
```bash
python tests/stop_loss/test_stop_loss.py
```

### test_stop_loss_debug.py
**용도**: 손절/익절 로직 상세 디버깅
```bash
python tests/stop_loss/test_stop_loss_debug.py
```

---

## 🔌 api/ - API 연동 테스트

### test_token.py
**용도**: 키움증권 API 토큰 발급 테스트
```bash
python tests/api/test_token.py
```

### test_account_balance.py
**용도**: 계좌 잔고 조회 테스트
```bash
python tests/api/test_account_balance.py
```

### test_naver_crawler.py
**용도**: 네이버 뉴스 크롤링 테스트
```bash
python tests/api/test_naver_crawler.py
```

### test_watchlist_sync.py
**용도**: 관심종목 동기화 테스트
```bash
python tests/api/test_watchlist_sync.py
```

### test_debug_mode.py
**용도**: 전반적인 디버그 모드 테스트

---

## 🚀 빠른 시작

### 1. 신호 생성 후 매수 주문 (DRY-RUN)
```bash
# 1단계: 신호 생성
python tests/signal/test_signal_manager.py --stock-code 005930 --stock-name "Samsung"

# 2단계: 매수 주문 (DRY-RUN)
python tests/buy_order/test_buy_order.py
```

### 2. 실제 주문 실행
```bash
# ⚠️ 주의: 실제 모의투자 주문이 발생합니다!
python tests/buy_order/test_buy_order.py --execute
```

### 3. 문제 발생 시 디버깅
```bash
# 매수 프로세스 단계별 확인
python tests/buy_order/test_buy_debug.py

# API 토큰 확인
python tests/api/test_token.py

# 계좌 잔고 확인
python tests/api/test_account_balance.py
```

---

## ⚙️ 자동매매 설정

테스트 실행 전 자동매매 설정이 필요합니다:

```python
from models import get_db, AutoTradeSettings

db = next(get_db())
settings = AutoTradeSettings(
    is_enabled=True,
    max_invest_amount=500000,  # 최대 투자금액 (원)
    stop_loss_rate=5.0,        # 손절률 (%)
    take_profit_rate=10.0      # 익절률 (%)
)
db.add(settings)
db.commit()
```

---

## 📝 테스트 시나리오

### 기본 매수 테스트 시나리오
1. ✅ API 토큰 발급 확인: `tests/api/test_token.py`
2. ✅ 계좌 잔고 확인: `tests/api/test_account_balance.py`
3. ✅ 신호 생성: `tests/signal/test_signal_manager.py`
4. ✅ 매수 주문 DRY-RUN: `tests/buy_order/test_buy_order.py`
5. ✅ 매수 주문 실행: `tests/buy_order/test_buy_order.py --execute`

### 손절/익절 테스트 시나리오
1. 포지션 보유 중인 종목 확인
2. 손절/익절 로직 테스트: `tests/stop_loss/test_stop_loss.py`
3. 손절/익절 상세 디버깅: `tests/stop_loss/test_stop_loss_debug.py`

---

## 🐛 문제 해결

### 문제: "자동매매 설정이 없습니다"
**해결**: 자동매매 설정 생성 (위의 자동매매 설정 참조)

### 문제: "토큰 발급 실패"
**해결**: 
1. `.env` 파일에서 `KIWOOM_MOCK_APP_KEY` 확인
2. `tests/api/test_token.py` 실행하여 토큰 테스트

### 문제: "현재가 조회 실패"
**해결**:
1. API 호출 간격 확인 (최소 5초)
2. 네트워크 연결 확인
3. SSL 설정 확인 (이미 수정됨)

---

## 📚 참고 문서

- [프로젝트 메인 README](../README.md)
- [프로세스 흐름도](../PROCESS_FLOW.md)

