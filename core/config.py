import os
from pathlib import Path
from typing import Callable, Iterable, List, Optional, TypeVar, Union, overload

from dotenv import load_dotenv

load_dotenv()


T = TypeVar("T")


@overload
def _env(key: str, default: str) -> str:
    ...


@overload
def _env(key: str, default: str, *, cast: Callable[[str], T]) -> T:
    ...


def _env(key: str, default: str, *, cast: Optional[Callable[[str], T]] = None) -> Union[T, str]:
    """환경변수를 안전하게 읽어 변환한다."""
    raw_value = os.getenv(key)
    value = raw_value.strip() if raw_value is not None else ""
    if not value:
        return cast(default) if cast is not None else default
    if cast is None:
        return value
    return cast(value)


def _env_bool(key: str, default: bool) -> bool:
    return _env(key, "true" if default else "false").lower() == "true"


def _env_int(key: str, default: int) -> int:
    return _env(key, str(default), cast=int)


def _env_float(key: str, default: float) -> float:
    return _env(key, str(default), cast=float)


def _env_list(key: str, default: Union[Iterable[str], str] = ()) -> List[str]:
    default_value = default if isinstance(default, str) else ",".join(default)
    items = _env(key, default_value).split(",")
    return [item.strip() for item in items if item and item.strip()]


def get_account_mode() -> str:
    return "mock" if Config.KIWOOM_USE_MOCK_ACCOUNT else "real"


class Config:
    # 프로젝트 기본 설정 (core/ 상위 = 저장소 루트)
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    LOG_LEVEL = _env("LOG_LEVEL", "INFO")
    LOG_FILE = _env("LOG_FILE", str(PROJECT_ROOT / "logs" / "app.log"))
    # 현재 파일 + 백업 N개. 기본 50MB × 3 ≈ 최대 200MB
    LOG_MAX_BYTES = _env_int("LOG_MAX_BYTES", 50 * 1024 * 1024)
    LOG_BACKUP_COUNT = _env_int("LOG_BACKUP_COUNT", 3)

    # 데이터베이스 설정
    DATABASE_URL = _env("DATABASE_URL", f"sqlite:///{PROJECT_ROOT / 'stock_pipeline.db'}")

    # 키움증권 API 설정
    # 실전투자용 키
    KIWOOM_APP_KEY = _env("KIWOOM_APP_KEY", "")
    KIWOOM_APP_SECRET = _env("KIWOOM_APP_SECRET", "")

    # 모의투자용 키 (신청일: 2026-10-13)
    KIWOOM_MOCK_APP_KEY = _env(
        "KIWOOM_MOCK_APP_KEY",
        "iJOkLOq0cQG0duY33vUGVq0YN3yw3nJa2Z4BUp74XV0",
    )
    KIWOOM_MOCK_APP_SECRET = _env(
        "KIWOOM_MOCK_APP_SECRET",
        "Xr7Hh272DEabYVmp3Pj8QTIHXxakfi8QffYRWhYbpxA",
    )

    KIWOOM_BASE_URL = _env("KIWOOM_BASE_URL", "https://openapi.kiwoom.com/v1")
    # WebSocket URL (실전/모의 분리)
    KIWOOM_WS_URL = _env("KIWOOM_WS_URL", "wss://api.kiwoom.com:10000")  # 실전 기본값(호환)
    KIWOOM_MOCK_WS_URL = _env("KIWOOM_MOCK_WS_URL", "wss://mockapi.kiwoom.com:10000")  # 모의투자 기본값
    KIWOOM_ACCOUNT_NUMBER = _env("KIWOOM_ACCOUNT_NUMBER", "")  # 추가
    KIWOOM_MOCK_ACCOUNT_NUMBER = _env("KIWOOM_MOCK_ACCOUNT_NUMBER", "81364487")  # 모의투자 계좌
    KIWOOM_USE_MOCK_ACCOUNT = _env_bool("KIWOOM_USE_MOCK_ACCOUNT", True)  # 모의투자 사용 여부
    # 일부 주문 API에서 계좌 비밀번호(4자리 등)를 요구할 수 있어 옵션으로 지원
    KIWOOM_ACCOUNT_PASSWORD = _env("KIWOOM_ACCOUNT_PASSWORD", "")
    KIWOOM_MOCK_ACCOUNT_PASSWORD = _env("KIWOOM_MOCK_ACCOUNT_PASSWORD", "")
    KIWOOM_WS_RECONNECT_INTERVAL = _env_int("KIWOOM_WS_RECONNECT_INTERVAL", 5)  # 초 단위
    KIWOOM_WS_PING_INTERVAL = _env_int("KIWOOM_WS_PING_INTERVAL", 30)  # 초 단위

    # 키움증권 API 도메인 설정
    KIWOOM_REAL_API_URL = "https://api.kiwoom.com"  # 운영 도메인2
    KIWOOM_MOCK_API_URL = "https://mockapi.kiwoom.com"  # 모의투자 도메인

    # 모니터링 설정
    CONDITION_CHECK_INTERVAL = _env_int("CONDITION_CHECK_INTERVAL", 60)  # 초 단위
    SIGNAL_DEDUPLICATION_WINDOW = _env_int("SIGNAL_DEDUPLICATION_WINDOW", 300)  # 초 단위 (5분)
    # 조건식 실시간(주기) 검색 — 기본 OFF. ON이면 /monitoring/start 또는 자동매매 ON 시 주기 스캔.
    # API 호출 절감을 위해 기본 false. 수동 조회(/conditions/{id}/stocks, 스크리너 조회)는 항상 가능.
    CONDITION_MONITOR_AUTO_ENABLED = _env_bool("CONDITION_MONITOR_AUTO_ENABLED", False)
    CONDITION_MONITOR_INTERVAL = _env_int("CONDITION_MONITOR_INTERVAL", 600)  # 주기 검색 간격(초), 기본 10분

    # ===== 자동매매 안전장치 / 테스트 옵션 =====
    # 조건식 스캔 1회당 조건식별 신호 생성 상한(폭주 방지). 기본 1개만 생성.
    MAX_SIGNALS_PER_CONDITION_SCAN = _env_int("MAX_SIGNALS_PER_CONDITION_SCAN", 1)
    # 장시간 체크 우회(테스트용). 실계좌에서는 기본 False 권장.
    ALLOW_OUT_OF_MARKET_TRADING = _env_bool("ALLOW_OUT_OF_MARKET_TRADING", False)
    # 시장가 매수는 주문 접수 시점 가격이 현재가보다 오를 수 있어 수량 계산에 여유를 둔다.
    MARKET_BUY_PRICE_BUFFER_PCT = _env_float("MARKET_BUY_PRICE_BUFFER_PCT", 3.0)
    # 매수 직후 키움 잔고 미반영으로 HOLDING→MANUAL_SELL 오판 방지(초). ORDERED 매도 확정은 유예 제외.
    # 90초는 잔고 반영 지연에 부족한 경우가 있어 기본 5분.
    BUY_SETTLE_GRACE_SECONDS = _env_int("BUY_SETTLE_GRACE_SECONDS", 300)
    # 앱 매도 없이 '계좌 미보유'로 청산하려면, 유예 이후 연속 N회 잔고 미확인이 필요.
    BUY_SETTLE_MISSING_CONFIRMS = _env_int("BUY_SETTLE_MISSING_CONFIRMS", 3)

    # ===== 관심종목 동기화 설정 =====
    # 예: WATCHLIST_SYNC_TARGET_CONDITION_NAMES=돌파,120일선돌파
    WATCHLIST_SYNC_TARGET_CONDITION_NAMES = _env_list("WATCHLIST_SYNC_TARGET_CONDITION_NAMES")
    WATCHLIST_SYNC_ONLY_TARGET_CONDITIONS = _env_bool("WATCHLIST_SYNC_ONLY_TARGET_CONDITIONS", False)
    WATCHLIST_SYNC_REMOVE_EXPIRED_STOCKS = _env_bool("WATCHLIST_SYNC_REMOVE_EXPIRED_STOCKS", True)
    WATCHLIST_SYNC_EXPIRED_THRESHOLD_HOURS = _env_int("WATCHLIST_SYNC_EXPIRED_THRESHOLD_HOURS", 6)
    
    # ===== 키움 REST API 레이트 리미터 (내부 전역 제한) =====
    # 키움 공식: 분당 20회. 기본값은 18회/3초 간격 — 여유는 두되 과도하게 조이지 않음.
    API_MIN_CALL_INTERVAL = _env_float("API_MIN_CALL_INTERVAL", 3.0)
    API_MAX_CALLS_PER_MIN = _env_int("API_MAX_CALLS_PER_MIN", 18)
    API_LIMIT_DURATION_MIN = _env_int("API_LIMIT_DURATION_MIN", 3)
    # 스캔 종목 간 추가 대기(초). API 여유 있으면 이보다 짧게(적응형). 게이트 OFF면 절반.
    SCAN_GATE_PAUSE_SEC = _env_float("SCAN_GATE_PAUSE_SEC", 3.0)

    # 1회 스캔 총 대상 상한. 초과분은 레거시(거래대금 상위)부터 줄인다.
    SCAN_TARGET_TOTAL_LIMIT = _env_int("SCAN_TARGET_TOTAL_LIMIT", 60)
    # 레거시 스크리너 — 거래대금 상위 후보 상한(단독 시). 타 전략 편입 시 잔여 자리에 맞춰 축소.
    SCREENER_CANDIDATE_LIMIT = _env_int("SCREENER_CANDIDATE_LIMIT", 20)
    # 상따 — 등락률상위 풀에서 거래대금순으로 남길 후보 수
    SANGTTA_CANDIDATE_LIMIT = _env_int("SANGTTA_CANDIDATE_LIMIT", 20)
    # 프랙탈 스캘핑 — 동시 1분봉 조회(WATCHING) 상한
    FRACTAL_CANDIDATE_LIMIT = _env_int("FRACTAL_CANDIDATE_LIMIT", 5)
    FRACTAL_CHART_CACHE_TTL = _env_float("FRACTAL_CHART_CACHE_TTL", 45.0)
    # 스크리너 후보 최소 등락률(%). 0이면 기존처럼 플러스(>0)만. 매수 최소등락(예: 3.5)보다 약간 낮게.
    SCREENER_MIN_CHANGE_RATE = _env_float("SCREENER_MIN_CHANGE_RATE", 3.3)
    # 스크리너 후보 과열 컷(%). 이 이상이면 편입 제외. 0이면 상한 미적용.
    SCREENER_MAX_CHANGE_RATE = _env_float("SCREENER_MAX_CHANGE_RATE", 12.0)
    # 스크리너 후보 최소 당일 거래대금(억원). 0이면 하한 미적용.
    SCREENER_MIN_TRADE_AMOUNT_EOK = _env_float("SCREENER_MIN_TRADE_AMOUNT_EOK", 20.0)

    # 공공데이터포털 (관세청 수출입 OpenAPI)
    DATA_GO_KR_SERVICE_KEY = _env("DATA_GO_KR_SERVICE_KEY", "")

    # 서버 설정
    HOST = _env("HOST", "0.0.0.0")
    PORT = _env_int("PORT", 8000)
    # 매일 KST 이 시각 이후 uvicorn 프로세스 자동 종료 (장마감 배치·테마 마트는 별도 스케줄)
    SERVER_AUTO_SHUTDOWN_ENABLED = _env_bool("SERVER_AUTO_SHUTDOWN_ENABLED", True)
    # Default auto-shutdown time moved to 20:00 to match extended stop-loss monitoring.
    SERVER_AUTO_SHUTDOWN_TIME = _env("SERVER_AUTO_SHUTDOWN_TIME", "20:00")

    # ===== 웹 UI 세션 인증 =====
    # AUTH_PASSWORD는 평문으로 env에 두고, 검증 시 PBKDF2 해시로만 비교한다.
    AUTH_ENABLED = _env_bool("AUTH_ENABLED", True)
    AUTH_USERNAME = _env("AUTH_USERNAME", "zi5")
    AUTH_PASSWORD = _env("AUTH_PASSWORD", "zi6")
    AUTH_SESSION_SECRET = _env("AUTH_SESSION_SECRET", "stocke-dev-session-secret-change-me")
    AUTH_SESSION_HOURS = _env_int("AUTH_SESSION_HOURS", 6)

    
    # 네이버 뉴스 검색 API 설정
    NAVER_CLIENT_ID = _env("NAVER_CLIENT_ID", "")
    NAVER_CLIENT_SECRET = _env("NAVER_CLIENT_SECRET", "")
    NAVER_NEWS_API_URL = "https://openapi.naver.com/v1/search/news.json"

    # ===== 텔레그램 알림 설정 =====
    TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID", "")
    # 알림 대상 조건식 이름 필터(쉼표 구분). 비우면 모든 조건식 대상.
    # 예: TELEGRAM_ALERT_CONDITION_NAMES=돌파,120일선돌파
    TELEGRAM_ALERT_CONDITION_NAMES = _env_list("TELEGRAM_ALERT_CONDITION_NAMES")
    # 반복 실행 시 조회 주기(초). 단발 실행에서는 사용되지 않음.
    TELEGRAM_ALERT_INTERVAL = _env_int("TELEGRAM_ALERT_INTERVAL", 600)
    # 조건식별 메시지에 표시할 최대 종목 수
    TELEGRAM_ALERT_MAX_STOCKS = _env_int("TELEGRAM_ALERT_MAX_STOCKS", 20)
    # true: 조건식·정기 알림은 거래일 장중(09:00~15:30)만 / 매매 체결 알림도 동일
    TELEGRAM_ALERT_MARKET_HOURS_ONLY = _env_bool("TELEGRAM_ALERT_MARKET_HOURS_ONLY", True)
    # 실시간 편입 알림 — 동일 조건·종목 재알림 최소 간격(초)
    TELEGRAM_CONDITION_REALTIME_DEDUP_SEC = _env_int("TELEGRAM_CONDITION_REALTIME_DEDUP_SEC", 300)

    # ===== MA1592 (15/92 홀드) =====
    # HTS 조건식 기본명 — 임진왜란(1592) · 이순신 「죽고자 하면 산다」 집중 매매
    MA1592_DEFAULT_CONDITION_NAME = _env("MA1592_DEFAULT_CONDITION_NAME", "1592매매")
    MA1592_CHART_CACHE_TTL = _env_float(
        "MA1592_CHART_CACHE_TTL",
        _env_float("MA1590_CHART_CACHE_TTL", 60.0),
    )

    # ===== CPU 과부하 알림 (트레이 풍선 + 텔레그램) =====
    CPU_ALERT_THRESHOLD = _env_float("CPU_ALERT_THRESHOLD", 90.0)
    CPU_ALERT_SUSTAIN_SEC = _env_int("CPU_ALERT_SUSTAIN_SEC", 60)
    CPU_ALERT_COOLDOWN_SEC = _env_int("CPU_ALERT_COOLDOWN_SEC", 900)
    CPU_ALERT_TELEGRAM = _env_bool("CPU_ALERT_TELEGRAM", True)

    # ===== 뉴스 키워드 추출 (KeyBERT) =====
    KEYWORD_USE_KEYBERT = _env_bool("KEYWORD_USE_KEYBERT", True)
    KEYBERT_MODEL = _env("KEYBERT_MODEL", "jhgan/ko-sroberta-multitask")
    KEYBERT_USE_MMR = _env_bool("KEYBERT_USE_MMR", True)
    KEYBERT_DIVERSITY = _env_float("KEYBERT_DIVERSITY", 0.5)
    KEYBERT_USE_KIWI = _env_bool("KEYBERT_USE_KIWI", True)

    # ===== 종목 뉴스 배치 (미니PC 친화 기본값, 목표 ~30분) =====
    # theme=테마 편입 종목만 / all=전종목(수시간·고CPU)
    STOCK_NEWS_UNIVERSE = _env("STOCK_NEWS_UNIVERSE", "theme").lower()
    # KeyBERT CPU 기준 종목당 ~10~15초 → 120종 ≈ 20~30분
    STOCK_NEWS_MAX_STOCKS_PER_DAY = _env_int("STOCK_NEWS_MAX_STOCKS_PER_DAY", 120)
    STOCK_NEWS_CHUNK_SIZE = _env_int("STOCK_NEWS_CHUNK_SIZE", 40)

    # ===== 테마 거래대금 맵 (거래대금순 종목 → 테마 합산, 테마맵) =====
    THEME_FLOW_STOCK_LIMIT = _env_int("THEME_FLOW_STOCK_LIMIT", 300)
    THEME_FLOW_TOP_N = _env_int("THEME_FLOW_TOP_N", 40)
    THEME_FLOW_CACHE_SEC = _env_int("THEME_FLOW_CACHE_SEC", 900)  # 15분

    # ===== 알파스퀘어 테마 (비공식 내부 API, 일 배치용) =====
    ALPHASQUARE_ENABLED = _env_bool("ALPHASQUARE_ENABLED", True)
    ALPHASQUARE_BASE_URL = _env("ALPHASQUARE_BASE_URL", "https://api.alphasquare.co.kr").rstrip("/")
    ALPHASQUARE_TIMEOUT_SEC = _env_float("ALPHASQUARE_TIMEOUT_SEC", 20.0)
    ALPHASQUARE_SLEEP_SEC = _env_float("ALPHASQUARE_SLEEP_SEC", 0.35)
    # stock→themes 편입사유 보강 (호출 수↑). v1 기본 OFF
    ALPHASQUARE_FETCH_REASONS = _env_bool("ALPHASQUARE_FETCH_REASONS", False)
    ALPHASQUARE_USER_AGENT = _env(
        "ALPHASQUARE_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )

    # 대시보드 상단: 실전 ↔ 모의 사이트 이동
    REAL_SITE_URL = _env("REAL_SITE_URL", "http://144.24.81.83:8001/dashboard")
    MOCK_SITE_URL = _env("MOCK_SITE_URL", "https://hyungdon.store/login?next=/dashboard")
    
    # 로그 디렉토리 생성
    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)