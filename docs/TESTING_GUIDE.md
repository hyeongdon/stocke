# 스텝별 테스트 가이드

각 모듈을 독립적으로 테스트할 수 있는 파일들입니다. `manual_order_test.py`처럼 각 스텝별로 실행하여 빠르게 검증할 수 있습니다.

## 📋 테스트 파일 목록

### 1️⃣ 토큰 발급/갱신 테스트
```bash
# 기본 테스트 (토큰 확인)
python test_token.py

# 강제 토큰 갱신
python test_token.py --renew
```

**확인사항:**
- ✅ 토큰 발급 성공
- ✅ 토큰 만료시간 확인
- ✅ APP_KEY, APP_SECRET 설정 검증

---

### 2️⃣ 계좌 잔고 조회 테스트
```bash
# 기본 테스트 (config의 계좌 사용)
python test_account_balance.py

# 특정 계좌 조회
python test_account_balance.py --account-no 12345678
```

**확인사항:**
- ✅ 계좌 정보 조회
- ✅ 예수금, 총평가금액 확인
- ✅ 보유 종목 목록 및 수익률 확인

---

### 3️⃣ 조건식 모니터링 테스트
```bash
# 조건식 검색 테스트
python test_condition_monitor.py --condition-id 1 --condition-name "상승종목"

# 신호 생성까지 테스트
python test_condition_monitor.py --condition-id 1 --condition-name "상승종목" --create-signal
```

**확인사항:**
- ✅ 조건식으로 종목 검색
- ✅ 검색된 종목 리스트 확인
- ✅ PendingBuySignal 신호 생성 (--create-signal 옵션)

---

### 4️⃣ 시그널 관리 테스트
```bash
# 기본 신호 생성 테스트
python test_signal_manager.py --stock-code 005930 --stock-name "삼성전자"

# 조건식 신호 생성
python test_signal_manager.py --stock-code 005930 --stock-name "삼성전자" --condition-id 1 --signal-type condition

# 전략 신호 생성
python test_signal_manager.py --stock-code 005930 --stock-name "삼성전자" --signal-type strategy
```

**확인사항:**
- ✅ 신호 생성 성공
- ✅ 중복 신호 방지 확인
- ✅ DB에 신호 저장 확인

---

### 5️⃣ 매수 주문 실행 테스트
```bash
# PENDING 신호 조회만 (DRY-RUN)
python test_buy_order.py

# 특정 신호로 주문 실행 (실제 주문 발생!)
python test_buy_order.py --signal-id 123 --execute
```

**확인사항:**
- ✅ PENDING 신호 조회
- ✅ 매수 주문 실행 (--execute 옵션 필요)
- ⚠️ **주의: --execute 사용 시 실제 주문 발생**

---

### 6️⃣ 손절/익절 관리 테스트
```bash
# 보유 포지션 및 설정 확인
python test_stop_loss.py

# 손절/익절 모니터링 1회 실행
python test_stop_loss.py --monitor
```

**확인사항:**
- ✅ 자동매매 설정 확인
- ✅ 보유 포지션 목록
- ✅ 손절/익절 조건 체크

---

### 7️⃣ 네이버 토론방 크롤링 테스트
```bash
# 기본 크롤링 (1페이지)
python test_naver_crawler.py --stock-code 005930

# 여러 페이지 크롤링
python test_naver_crawler.py --stock-code 005930 --pages 3

# 오늘 게시글만 필터링
python test_naver_crawler.py --stock-code 005930 --today-only
```

**확인사항:**
- ✅ 네이버 금융 토론방 접근
- ✅ 게시글 제목, 작성자, 날짜 수집
- ✅ 오늘 날짜 필터링

---

### 8️⃣ 관심종목 동기화 테스트
```bash
# 관심종목 그룹 조회
python test_watchlist_sync.py

# 특정 그룹의 종목 조회
python test_watchlist_sync.py --group-id 1
```

**확인사항:**
- ✅ 관심종목 그룹 목록 조회
- ✅ 그룹별 종목 리스트 확인
- ✅ DB 동기화 성공

---

## 🔄 권장 테스트 순서

실제 자동매매 시스템을 테스트할 때는 다음 순서를 권장합니다:

```bash
# 1. 기본 인증 및 API 연결 확인
python test_token.py
python test_account_balance.py

# 2. 조건식 및 신호 생성 확인
python test_condition_monitor.py --condition-id 1 --condition-name "YOUR_CONDITION"
python test_signal_manager.py --stock-code 005930 --stock-name "삼성전자"

# 3. 매수 프로세스 테스트 (DRY-RUN)
python test_buy_order.py

# 4. 손절/익절 시스템 확인
python test_stop_loss.py --monitor

# 5. 부가 기능 테스트
python test_naver_crawler.py --stock-code 005930
python test_watchlist_sync.py
```

---

## ⚠️ 주의사항

1. **모의투자 권장**: 실제 주문이 발생하는 테스트는 모의투자 계좌를 사용하세요
2. **--execute 플래그**: 주문 관련 테스트는 기본적으로 DRY-RUN이며, `--execute` 플래그를 추가해야 실제 주문이 발생합니다
3. **API 제한**: 키움 API는 요청 제한이 있으므로 테스트 간 충분한 간격을 두세요
4. **장 운영시간**: 일부 기능은 장 운영시간에만 정상 작동합니다

---

## 🔍 로그 확인

테스트 실행 중 상세 로그는 `stock_pipeline.log` 파일에서 확인할 수 있습니다:

```bash
# 실시간 로그 확인 (Windows)
Get-Content stock_pipeline.log -Wait -Tail 50

# 최근 로그 확인
type stock_pipeline.log | more
```

---

## 📊 DB 확인

신호 및 포지션 데이터는 SQLite DB에 저장됩니다:

```bash
# SQLite로 DB 확인
sqlite3 stock_pipeline.db

# 주요 테이블
SELECT * FROM pending_buy_signals ORDER BY created_at DESC LIMIT 10;
SELECT * FROM positions WHERE status = 'HOLDING';
SELECT * FROM auto_trade_settings;
```

---

## 🆘 문제 해결

### 토큰 발급 실패
- `.env` 파일의 APP_KEY, APP_SECRET 확인
- 키움 API 권한 및 계좌 상태 확인

### 조건식 검색 실패
- 조건식 ID와 이름이 정확한지 확인
- HTS에서 조건식 등록 상태 확인

### 주문 실패
- 계좌 잔고 확인
- 주문 가능 시간 확인 (장 운영시간)
- 모의투자/실전투자 설정 확인

---

생성일: 2026-01-15
버전: 1.0

