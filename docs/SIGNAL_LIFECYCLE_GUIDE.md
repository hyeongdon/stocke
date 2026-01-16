# 시그널 라이프사이클 추적 가이드

## 🎯 기능 소개

시그널부터 매수 완료까지의 전체 과정을 **실시간**으로 한 화면에서 추적할 수 있습니다.

### 추적 가능한 단계

```
① 시그널 포착 (PENDING)
   ↓
② 현재가 조회
   ↓
③ 매수 수량 계산
   ↓
④ 주문 실행 (PROCESSING)
   ↓
⑤ 주문 완료 (ORDERED)
   ↓
⑥ 포지션 생성 (HOLDING)
```

### 막힌 구간 확인

각 단계에서 **실패 이유**가 표시됩니다:
- ❌ 현재가 조회 실패
- ❌ 예수금 부족 (수량 계산 단계)
- ❌ API 오류 (주문 실행 단계)
- ❌ 포지션 생성 실패

---

## 🚀 사용 방법

### 1. 접속

#### 방법 A: 메인 화면에서
```
1. http://localhost:8000 접속
2. 헤더의 "라이프사이클" 버튼 클릭
```

#### 방법 B: 직접 접속
```
http://localhost:8000/static/signal-lifecycle.html
```

### 2. 화면 구성

#### 상단 컨트롤
- **필터**: 전체 / 대기중 / 처리중 / 완료 / 실패
- **자동 새로고침**: 10초마다 자동 업데이트
- **새로고침 버튼**: 수동 새로고침

#### 시그널 카드
각 시그널마다 다음 정보를 표시:
- 종목명 / 종목코드
- 현재 상태 (대기중/처리중/완료/실패)
- 생성 시간
- 라이프사이클 타임라인
- 상세 정보 (매수가, 수량, 수익률 등)

---

## 📊 타임라인 표시

### 아이콘 의미

| 아이콘 | 단계 | 설명 |
|--------|------|------|
| 🔍 | 시그널 포착 | 조건식 또는 전략에서 신호 발생 |
| 💲 | 현재가 조회 | 실시간 현재가 조회 |
| 🧮 | 수량 계산 | 매수 가능 수량 계산 |
| ✈️ | 주문 실행 | 키움 API로 매수 주문 |
| ✅ | 주문 완료 | 주문이 체결됨 |
| 💼 | 포지션 생성 | 손절/익절 모니터링 시작 |

### 상태 표시

- **회색 원**: 아직 진행 안됨
- **파란색 원 (펄스 애니메이션)**: 현재 진행중
- **초록색 원**: 완료
- **빨간색 원**: 실패

### 진행률 바

타임라인 하단의 초록색 바가 전체 진행률을 표시합니다.

---

## 🔍 실패 원인 파악

### 실패 메시지 예시

```
❌ 현재가 조회 실패
   → API 제한 또는 종목코드 오류
   → 잠시 후 재시도됩니다

❌ 예수금 부족 (필요: 1,000,000원, 보유: 50,000원)
   → 계좌 입금 필요
   → 또는 종목 수 줄이기

❌ 주문 실행 실패: 장 마감 시간입니다
   → 장 운영시간 확인 (09:00 - 15:30)

❌ API 오류: rate limit exceeded
   → API 요청 제한 초과
   → 자동 재시도 대기중
```

---

## ⚙️ 설정

### 자동 새로고침 간격

현재 설정: **30초 간격** (API 호출 부담을 줄이기 위해)

JavaScript 파일에서 수정 가능:

```javascript
// signal-lifecycle.js 파일
this.autoRefreshInterval = setInterval(() => {
    this.loadSignals();
}, 30000); // 30초 (권장) → 필요시 변경
```

**주의:** 간격을 너무 짧게 하면 API 제한에 걸릴 수 있습니다!

### 필터 기본값

```javascript
// signal-lifecycle.js 파일
this.currentFilter = 'all'; // 'pending', 'processing', 'ordered', 'failed'
```

---

## 📱 모바일 지원

반응형 디자인으로 모바일에서도 사용 가능합니다:
- 타임라인이 세로로 표시
- 터치 제스처 지원
- 자동 새로고침 최적화

---

## 🔧 문제 해결

### 데이터가 안 보여요

```bash
# 1. 서버 실행 확인
python main.py

# 2. API 엔드포인트 확인
curl http://localhost:8000/signals/pending
curl http://localhost:8000/positions/

# 3. 브라우저 콘솔 확인 (F12)
# 에러 메시지 확인
```

### 실시간 업데이트가 안 돼요

```
1. 자동 새로고침 토글 확인
2. 브라우저 콘솔에서 네트워크 탭 확인
3. 서버 로그 확인 (stock_pipeline.log)
```

### 타임라인이 이상해요

```
1. 브라우저 캐시 삭제 (Ctrl + F5)
2. 다른 브라우저에서 테스트
3. CSS 파일 로드 확인
```

---

## 💡 활용 팁

### 1. 실패 패턴 분석
- 특정 단계에서 자주 실패하는 패턴 파악
- 예수금 부족이 반복되면 투자 금액 조정
- API 제한이 자주 걸리면 모니터링 간격 조정

### 2. 성능 모니터링
- 시그널부터 매수까지 걸리는 시간 확인
- 병목 구간 파악
- 최적화 포인트 발견

### 3. 디버깅
- 실패한 주문의 정확한 원인 파악
- 재시도 로직 확인
- 로그와 함께 분석

---

## 📊 데이터 흐름

```
[조건식/전략] → PendingBuySignal (PENDING)
       ↓
[BuyOrderExecutor] → 현재가 조회
       ↓
[BuyOrderExecutor] → 수량 계산
       ↓
[BuyOrderExecutor] → PendingBuySignal (PROCESSING)
       ↓
[KiwoomAPI] → 매수 주문
       ↓
[BuyOrderExecutor] → PendingBuySignal (ORDERED)
       ↓
[StopLossManager] → Position (HOLDING)
```

---

## 🎨 커스터마이징

### 색상 변경

`signal-lifecycle.html`의 CSS에서:

```css
/* 성공 색상 */
.step-icon.completed {
    background: #4CAF50; /* 초록색 */
}

/* 진행중 색상 */
.step-icon.active {
    background: #2196F3; /* 파란색 */
}

/* 실패 색상 */
.step-icon.failed {
    background: #f44336; /* 빨간색 */
}
```

### 타임라인 단계 추가/수정

`signal-lifecycle.js`의 `calculateLifecycle()` 함수에서:

```javascript
const stages = {
    detected: { ... },
    // 여기에 새로운 단계 추가
    newStage: {
        status: 'unknown',
        time: null,
        label: '새 단계',
        icon: 'fa-star'
    },
    ...
};
```

---

## 📚 관련 문서

- [전체 아키텍처](ARCHITECTURE_DIAGRAM.md)
- [프로세스 플로우](PROCESS_FLOW.md)
- [테스트 가이드](TESTING_GUIDE.md)

---

생성일: 2026-01-15
버전: 1.0

