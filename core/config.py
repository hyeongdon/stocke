import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

class Config:
    # 프로젝트 기본 설정 (core/ 상위 = 저장소 루트)
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG")
    LOG_FILE = os.getenv("LOG_FILE", str(PROJECT_ROOT / "logs" / "app.log"))
    
    # 데이터베이스 설정
    DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{PROJECT_ROOT / 'stock_pipeline.db'}")
    
    # 키움증권 API 설정
    # 실전투자용 키
    KIWOOM_APP_KEY = os.getenv("KIWOOM_APP_KEY", "")
    KIWOOM_APP_SECRET = os.getenv("KIWOOM_APP_SECRET", "")
    
    # 모의투자용 키 (신청일: 2026-01-13, 만료일: 2026-03-29)
    KIWOOM_MOCK_APP_KEY = os.getenv("KIWOOM_MOCK_APP_KEY", "Y-5kFGhoiKXPt5aQ-qnDoprjkuR4xh0biY9-hPkGBPI")
    KIWOOM_MOCK_APP_SECRET = os.getenv("KIWOOM_MOCK_APP_SECRET", "mUstvGMXN0HDp_qVWNeeqZydX2lMkVpaeQSB0Fy9HlQ")
    
    KIWOOM_BASE_URL = os.getenv("KIWOOM_BASE_URL", "https://openapi.kiwoom.com/v1")
    # WebSocket URL (실전/모의 분리)
    KIWOOM_WS_URL = os.getenv("KIWOOM_WS_URL", "wss://api.kiwoom.com:10000")  # 실전 기본값(호환)
    KIWOOM_MOCK_WS_URL = os.getenv("KIWOOM_MOCK_WS_URL", "wss://mockapi.kiwoom.com:10000")  # 모의투자 기본값
    KIWOOM_ACCOUNT_NUMBER = os.getenv("KIWOOM_ACCOUNT_NUMBER", "")  # 추가
    KIWOOM_MOCK_ACCOUNT_NUMBER = os.getenv("KIWOOM_MOCK_ACCOUNT_NUMBER", "81109058")  # 모의투자 계좌
    KIWOOM_USE_MOCK_ACCOUNT = os.getenv("KIWOOM_USE_MOCK_ACCOUNT", "true").lower() == "true"  # 모의투자 사용 여부
    # 일부 주문 API에서 계좌 비밀번호(4자리 등)를 요구할 수 있어 옵션으로 지원
    KIWOOM_ACCOUNT_PASSWORD = os.getenv("KIWOOM_ACCOUNT_PASSWORD", "")
    KIWOOM_MOCK_ACCOUNT_PASSWORD = os.getenv("KIWOOM_MOCK_ACCOUNT_PASSWORD", "")
    KIWOOM_WS_RECONNECT_INTERVAL = int(os.getenv("KIWOOM_WS_RECONNECT_INTERVAL", 5))  # 초 단위
    KIWOOM_WS_PING_INTERVAL = int(os.getenv("KIWOOM_WS_PING_INTERVAL", 30))  # 초 단위
    
    # 키움증권 API 도메인 설정
    KIWOOM_REAL_API_URL = "https://api.kiwoom.com"  # 운영 도메인2
    KIWOOM_MOCK_API_URL = "https://mockapi.kiwoom.com"  # 모의투자 도메인
    
    # 모니터링 설정
    CONDITION_CHECK_INTERVAL = int(os.getenv("CONDITION_CHECK_INTERVAL", 60))  # 초 단위
    SIGNAL_DEDUPLICATION_WINDOW = int(os.getenv("SIGNAL_DEDUPLICATION_WINDOW", 300))  # 초 단위 (5분)
    # 조건식 실시간(주기) 검색 — 기본 OFF. ON이면 /monitoring/start 또는 자동매매 ON 시 주기 스캔.
    # API 호출 절감을 위해 기본 false. 수동 조회(/conditions/{id}/stocks, 스크리너 조회)는 항상 가능.
    CONDITION_MONITOR_AUTO_ENABLED = os.getenv("CONDITION_MONITOR_AUTO_ENABLED", "false").lower() == "true"
    CONDITION_MONITOR_INTERVAL = int(os.getenv("CONDITION_MONITOR_INTERVAL", "600"))  # 주기 검색 간격(초), 기본 10분

    # ===== 자동매매 안전장치 / 테스트 옵션 =====
    # 조건식 스캔 1회당 조건식별 신호 생성 상한(폭주 방지). 기본 1개만 생성.
    MAX_SIGNALS_PER_CONDITION_SCAN = int(os.getenv("MAX_SIGNALS_PER_CONDITION_SCAN", 1))
    # 장시간 체크 우회(테스트용). 실계좌에서는 기본 False 권장.
    ALLOW_OUT_OF_MARKET_TRADING = os.getenv("ALLOW_OUT_OF_MARKET_TRADING", "false").lower() == "true"
    # 매수 직후 키움 잔고 미반영으로 HOLDING→MANUAL_SELL 오판 방지(초). ORDERED 매도 확정은 유예 제외.
    # 90초는 잔고 반영 지연에 부족한 경우가 있어 기본 5분.
    BUY_SETTLE_GRACE_SECONDS = int(os.getenv("BUY_SETTLE_GRACE_SECONDS", "300"))
    # 앱 매도 없이 '계좌 미보유'로 청산하려면, 유예 이후 연속 N회 잔고 미확인이 필요.
    BUY_SETTLE_MISSING_CONFIRMS = int(os.getenv("BUY_SETTLE_MISSING_CONFIRMS", "3"))

    # ===== 관심종목 동기화 설정 =====
    # 예: WATCHLIST_SYNC_TARGET_CONDITION_NAMES=돌파,120일선돌파
    WATCHLIST_SYNC_TARGET_CONDITION_NAMES = [
        s.strip() for s in os.getenv("WATCHLIST_SYNC_TARGET_CONDITION_NAMES", "").split(",") if s.strip()
    ]
    WATCHLIST_SYNC_ONLY_TARGET_CONDITIONS = os.getenv("WATCHLIST_SYNC_ONLY_TARGET_CONDITIONS", "false").lower() == "true"
    WATCHLIST_SYNC_REMOVE_EXPIRED_STOCKS = os.getenv("WATCHLIST_SYNC_REMOVE_EXPIRED_STOCKS", "true").lower() == "true"
    WATCHLIST_SYNC_EXPIRED_THRESHOLD_HOURS = int(os.getenv("WATCHLIST_SYNC_EXPIRED_THRESHOLD_HOURS", 6))
    
    # ===== 키움 REST API 레이트 리미터 (내부 전역 제한) =====
    # 키움 공식: 분당 20회. 기본값은 18회/3초 간격 — 여유는 두되 과도하게 조이지 않음.
    API_MIN_CALL_INTERVAL = float(os.getenv("API_MIN_CALL_INTERVAL", "3.0"))
    API_MAX_CALLS_PER_MIN = int(os.getenv("API_MAX_CALLS_PER_MIN", "18"))
    API_LIMIT_DURATION_MIN = int(os.getenv("API_LIMIT_DURATION_MIN", "3"))

    # 자동매매 스크리너 — 거래대금 상위 후보 종목 수
    SCREENER_CANDIDATE_LIMIT = int(os.getenv("SCREENER_CANDIDATE_LIMIT", "70"))

    # 서버 설정
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", 8000))
    
    # 네이버 뉴스 검색 API 설정
    NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "")
    NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")
    NAVER_NEWS_API_URL = "https://openapi.naver.com/v1/search/news.json"

    # ===== 텔레그램 알림 설정 =====
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
    # 알림 대상 조건식 이름 필터(쉼표 구분). 비우면 모든 조건식 대상.
    # 예: TELEGRAM_ALERT_CONDITION_NAMES=돌파,120일선돌파
    TELEGRAM_ALERT_CONDITION_NAMES = [
        s.strip() for s in os.getenv("TELEGRAM_ALERT_CONDITION_NAMES", "").split(",") if s.strip()
    ]
    # 반복 실행 시 조회 주기(초). 단발 실행에서는 사용되지 않음.
    TELEGRAM_ALERT_INTERVAL = int(os.getenv("TELEGRAM_ALERT_INTERVAL", 600))
    # 조건식별 메시지에 표시할 최대 종목 수
    TELEGRAM_ALERT_MAX_STOCKS = int(os.getenv("TELEGRAM_ALERT_MAX_STOCKS", 20))
    # true: 조건식·정기 알림은 거래일 장중(09:00~15:30)만 / 매매 체결 알림도 동일
    TELEGRAM_ALERT_MARKET_HOURS_ONLY = os.getenv("TELEGRAM_ALERT_MARKET_HOURS_ONLY", "true").lower() == "true"

    # ===== CPU 과부하 알림 (트레이 풍선 + 텔레그램) =====
    CPU_ALERT_THRESHOLD = float(os.getenv("CPU_ALERT_THRESHOLD", "90"))
    CPU_ALERT_SUSTAIN_SEC = int(os.getenv("CPU_ALERT_SUSTAIN_SEC", "60"))
    CPU_ALERT_COOLDOWN_SEC = int(os.getenv("CPU_ALERT_COOLDOWN_SEC", "900"))
    CPU_ALERT_TELEGRAM = os.getenv("CPU_ALERT_TELEGRAM", "true").lower() == "true"

    # ===== 뉴스 키워드 추출 (KeyBERT) =====
    KEYWORD_USE_KEYBERT = os.getenv("KEYWORD_USE_KEYBERT", "true").lower() == "true"
    KEYBERT_MODEL = os.getenv("KEYBERT_MODEL", "jhgan/ko-sroberta-multitask")
    KEYBERT_USE_MMR = os.getenv("KEYBERT_USE_MMR", "true").lower() == "true"
    KEYBERT_DIVERSITY = float(os.getenv("KEYBERT_DIVERSITY", "0.5"))
    KEYBERT_USE_KIWI = os.getenv("KEYBERT_USE_KIWI", "true").lower() == "true"

    # ===== 종목 뉴스 배치 (미니PC 친화 기본값, 목표 ~30분) =====
    # theme=테마 편입 종목만 / all=전종목(수시간·고CPU)
    STOCK_NEWS_UNIVERSE = (os.getenv("STOCK_NEWS_UNIVERSE", "theme") or "theme").strip().lower()
    # KeyBERT CPU 기준 종목당 ~10~15초 → 120종 ≈ 20~30분
    STOCK_NEWS_MAX_STOCKS_PER_DAY = int(os.getenv("STOCK_NEWS_MAX_STOCKS_PER_DAY", "120"))
    STOCK_NEWS_CHUNK_SIZE = int(os.getenv("STOCK_NEWS_CHUNK_SIZE", "40"))
    
    # 로그 디렉토리 생성
    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)