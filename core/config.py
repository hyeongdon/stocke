import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

class Config:
    # 프로젝트 기본 설정
    PROJECT_ROOT = Path(__file__).parent
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

    # ===== 자동매매 안전장치 / 테스트 옵션 =====
    # 조건식 스캔 1회당 조건식별 신호 생성 상한(폭주 방지). 기본 1개만 생성.
    MAX_SIGNALS_PER_CONDITION_SCAN = int(os.getenv("MAX_SIGNALS_PER_CONDITION_SCAN", 1))
    # 장시간 체크 우회(테스트용). 실계좌에서는 기본 False 권장.
    ALLOW_OUT_OF_MARKET_TRADING = os.getenv("ALLOW_OUT_OF_MARKET_TRADING", "false").lower() == "true"

    # ===== 관심종목 동기화 설정 =====
    # 예: WATCHLIST_SYNC_TARGET_CONDITION_NAMES=돌파,120일선돌파
    WATCHLIST_SYNC_TARGET_CONDITION_NAMES = [
        s.strip() for s in os.getenv("WATCHLIST_SYNC_TARGET_CONDITION_NAMES", "").split(",") if s.strip()
    ]
    WATCHLIST_SYNC_ONLY_TARGET_CONDITIONS = os.getenv("WATCHLIST_SYNC_ONLY_TARGET_CONDITIONS", "false").lower() == "true"
    WATCHLIST_SYNC_REMOVE_EXPIRED_STOCKS = os.getenv("WATCHLIST_SYNC_REMOVE_EXPIRED_STOCKS", "true").lower() == "true"
    WATCHLIST_SYNC_EXPIRED_THRESHOLD_HOURS = int(os.getenv("WATCHLIST_SYNC_EXPIRED_THRESHOLD_HOURS", 6))
    
    # 서버 설정
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", 8000))
    
    # 네이버 뉴스 검색 API 설정
    NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "")
    NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")
    NAVER_NEWS_API_URL = "https://openapi.naver.com/v1/search/news.json"
    
    # 로그 디렉토리 생성
    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)