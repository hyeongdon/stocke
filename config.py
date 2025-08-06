import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

class Config:
    # 프로젝트 기본 설정
    PROJECT_ROOT = Path(__file__).parent
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE = os.getenv("LOG_FILE", str(PROJECT_ROOT / "logs" / "app.log"))
    
    # 데이터베이스 설정
    DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{PROJECT_ROOT / 'stock_pipeline.db'}")
    
    # 키움증권 API 설정
    KIWOOM_APP_KEY = os.getenv("KIWOOM_APP_KEY", "")
    KIWOOM_APP_SECRET = os.getenv("KIWOOM_APP_SECRET", "")
    KIWOOM_BASE_URL = os.getenv("KIWOOM_BASE_URL", "https://openapi.kiwoom.com/v1")
    KIWOOM_WS_URL = os.getenv("KIWOOM_WS_URL", "wss://openapi.kiwoom.com/v1/ws")
    KIWOOM_WS_RECONNECT_INTERVAL = int(os.getenv("KIWOOM_WS_RECONNECT_INTERVAL", 5))  # 초 단위
    KIWOOM_WS_PING_INTERVAL = int(os.getenv("KIWOOM_WS_PING_INTERVAL", 30))  # 초 단위
    
    # 모니터링 설정
    CONDITION_CHECK_INTERVAL = int(os.getenv("CONDITION_CHECK_INTERVAL", 60))  # 초 단위
    SIGNAL_DEDUPLICATION_WINDOW = int(os.getenv("SIGNAL_DEDUPLICATION_WINDOW", 300))  # 초 단위 (5분)
    
    # 서버 설정
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", 8000))
    
    # 로그 디렉토리 생성
    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)