from datetime import datetime, timedelta
from typing import Optional
import requests
import logging
import threading
from core.config import Config
import urllib3

# SSL 검증 비활성화 경고 억제 (모의투자 서버 연결 문제 해결)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

_shared_token_manager: Optional["TokenManager"] = None
_shared_token_lock = threading.Lock()


def get_token_manager() -> "TokenManager":
    """프로세스 전역 단일 TokenManager (중복 발급·토큰 무효화 방지)."""
    global _shared_token_manager
    if _shared_token_manager is None:
        with _shared_token_lock:
            if _shared_token_manager is None:
                _shared_token_manager = TokenManager()
    return _shared_token_manager


class TokenManager:
    def __init__(self):
        self.access_token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None
        self.refresh_token: Optional[str] = None
        self.last_429_error_time: Optional[datetime] = None  # 마지막 429 에러 발생 시간
        self.rate_limit_cooldown = 90  # 429 에러 후 대기 시간 (초)
        self._auth_lock = threading.Lock()
    
    def authenticate(self) -> bool:
        """키움증권 API 인증을 수행하고 토큰을 발급받습니다."""
        with self._auth_lock:
            return self._authenticate_unlocked()

    def _authenticate_unlocked(self) -> bool:
        # 429 에러 후 쿨다운 기간 확인
        if self.last_429_error_time:
            elapsed = (datetime.utcnow() - self.last_429_error_time).total_seconds()
            if elapsed < self.rate_limit_cooldown:
                remaining = int(self.rate_limit_cooldown - elapsed)
                logger.warning(f"🔑 [TOKEN] API 제한으로 인증 대기 중 (남은 시간: {remaining}초)")
                return False
        
        try:
            # 투자구분 설정 (모의투자/실전투자)
            investment_type = "1" if Config.KIWOOM_USE_MOCK_ACCOUNT else "0"  # 1: 모의투자, 0: 실전투자
            account_type = "모의투자" if Config.KIWOOM_USE_MOCK_ACCOUNT else "실전투자"
            
            # 계좌 타입에 따른 App Key 선택
            if Config.KIWOOM_USE_MOCK_ACCOUNT:
                app_key = Config.KIWOOM_MOCK_APP_KEY
                app_secret = Config.KIWOOM_MOCK_APP_SECRET
            else:
                app_key = Config.KIWOOM_APP_KEY
                app_secret = Config.KIWOOM_APP_SECRET
            
            logger.debug(f"🔑 [TOKEN_DEBUG] 키움 API 토큰 발급 요청 - 투자구분: {account_type} (코드: {investment_type})")
            logger.debug(f"🔑 [TOKEN_DEBUG] 사용할 App Key: {app_key[:10]}...")
            
            # 엔드포인트 도메인 분기 (실전/모의)
            base_host = Config.KIWOOM_MOCK_API_URL if Config.KIWOOM_USE_MOCK_ACCOUNT else Config.KIWOOM_REAL_API_URL
            auth_url = f"{base_host}/oauth2/token"
            
            logger.debug(f"🔑 [TOKEN_DEBUG] 인증 URL: {auth_url}")
            
            response = requests.post(
                auth_url,
                json={
                    "grant_type": "client_credentials",
                    "appkey": app_key,
                    "secretkey": app_secret,
                    "investment_type": investment_type  # 투자구분 추가
                },
                headers={
                    "Content-Type": "application/json"
                },
                timeout=30,  # 타임아웃 증가
                verify=False  # SSL 검증 비활성화 (모의투자 서버 연결 문제 해결)
            )
            
            logger.debug(f"🔑 [TOKEN_DEBUG] HTTP 응답 상태: {response.status_code}")
            
            if response.status_code == 200:
                token_data = response.json()
                logger.debug(f"🔑 [TOKEN_DEBUG] API 응답 데이터: {token_data}")
                
                # 키움증권 API 응답에서 오류 확인
                if token_data.get("return_code") == 0:  # 성공
                    # 키움 응답 키는 버전에 따라 달라질 수 있어 둘 다 허용
                    self.access_token = token_data.get("access_token") or token_data.get("token")
                    self.refresh_token = token_data.get("refresh_token") or token_data.get("refreshToken") or self.refresh_token
                    logger.info(f"🔑 [TOKEN_DEBUG] ✅ 토큰 발급 성공: {self.access_token[:20]}...")
                    
                    # expires_dt 형식: "20250809005645" -> datetime으로 변환
                    expires_dt_str = token_data.get("expires_dt")
                    now = datetime.utcnow()
                    expires_in = token_data.get("expires_in")
                    if expires_dt_str:
                        self.token_expiry = datetime.strptime(expires_dt_str, "%Y%m%d%H%M%S")
                        logger.debug(f"🔑 [TOKEN_DEBUG] 토큰 만료 시간(expires_dt): {self.token_expiry}")
                    elif expires_in is not None:
                        # expires_in: 초 단위로 내려오는 경우
                        self.token_expiry = now + timedelta(seconds=int(expires_in))
                        logger.debug(f"🔑 [TOKEN_DEBUG] 토큰 만료 시간(expires_in): {self.token_expiry}")
                    else:
                        # 만료 정보를 못 받는 경우를 대비해 보수적으로 짧게 잡음
                        # (너무 길게 잡으면 서버가 먼저 만료 처리해 8005가 발생할 수 있음)
                        self.token_expiry = now + timedelta(minutes=55)
                        logger.debug(f"🔑 [TOKEN_DEBUG] 기본 토큰 만료 시간(보수 설정): {self.token_expiry}")
                    return True
                else:
                    logger.error(f"🔑 [TOKEN_DEBUG] ❌ 키움증권 API 오류: {token_data.get('return_msg', '알 수 없는 오류')}")
                    return False
            else:
                # 429 에러 (API 제한) 처리
                if response.status_code == 429:
                    self.last_429_error_time = datetime.utcnow()
                    logger.error(f"🔑 [TOKEN_DEBUG] ❌ API 호출 제한 초과 (HTTP 429) - {self.rate_limit_cooldown}초 동안 재인증 중지")
                    logger.error(f"🔑 [TOKEN_DEBUG] 응답 내용: {response.text}")
                else:
                    logger.error(f"🔑 [TOKEN_DEBUG] ❌ 키움증권 API 인증 실패 - HTTP {response.status_code}")
                    logger.error(f"🔑 [TOKEN_DEBUG] 응답 내용: {response.text}")
                return False
            
        except Exception as e:
            logger.error(f"키움증권 API 인증 오류: {type(e).__name__}: {e}")
            return False
    
    def is_token_valid(self) -> bool:
        """토큰이 유효한지 확인합니다."""
        current_time = datetime.utcnow()
        
        logger.debug(f"🔑 [TOKEN_DEBUG] 토큰 유효성 확인:")
        logger.debug(f"🔑 [TOKEN_DEBUG] - access_token 존재: {self.access_token is not None}")
        logger.debug(f"🔑 [TOKEN_DEBUG] - token_expiry 존재: {self.token_expiry is not None}")
        
        if not self.access_token or not self.token_expiry:
            logger.debug(f"🔑 [TOKEN_DEBUG] ❌ 토큰 또는 만료시간이 없음")
            return False
        
        # 만료 10분 전부터는 토큰을 갱신
        valid_until = self.token_expiry - timedelta(minutes=10)
        is_valid = current_time < valid_until
        
        logger.debug(f"🔑 [TOKEN_DEBUG] - 현재 시간: {current_time}")
        logger.debug(f"🔑 [TOKEN_DEBUG] - 토큰 만료 시간: {self.token_expiry}")
        logger.debug(f"🔑 [TOKEN_DEBUG] - 유효 기준 시간: {valid_until}")
        logger.debug(f"🔑 [TOKEN_DEBUG] - 토큰 유효: {is_valid}")
        
        return is_valid
    
    def refresh_access_token(self) -> bool:
        """리프레시 토큰을 사용하여 액세스 토큰을 갱신합니다."""
        with self._auth_lock:
            return self._refresh_access_token_unlocked()

    def _refresh_access_token_unlocked(self) -> bool:
        # 429 에러 후 쿨다운 기간 확인
        if self.last_429_error_time:
            elapsed = (datetime.utcnow() - self.last_429_error_time).total_seconds()
            if elapsed < self.rate_limit_cooldown:
                remaining = int(self.rate_limit_cooldown - elapsed)
                logger.warning(f"🔑 [TOKEN] API 제한으로 토큰 갱신 대기 중 (남은 시간: {remaining}초)")
                return False
        
        if not self.refresh_token:
            return self._authenticate_unlocked()
        
        try:
            # 투자구분 설정 (모의투자/실전투자)
            investment_type = "1" if Config.KIWOOM_USE_MOCK_ACCOUNT else "0"  # 1: 모의투자, 0: 실전투자
            
            # 계좌 타입에 따른 App Key 선택
            if Config.KIWOOM_USE_MOCK_ACCOUNT:
                app_key = Config.KIWOOM_MOCK_APP_KEY
                app_secret = Config.KIWOOM_MOCK_APP_SECRET
            else:
                app_key = Config.KIWOOM_APP_KEY
                app_secret = Config.KIWOOM_APP_SECRET
            
            base_host = Config.KIWOOM_MOCK_API_URL if Config.KIWOOM_USE_MOCK_ACCOUNT else Config.KIWOOM_REAL_API_URL
            response = requests.post(
                f"{base_host}/oauth2/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                    "appkey": app_key,
                    "secretkey": app_secret,
                    "investment_type": investment_type  # 투자구분 추가
                },
                timeout=30,
                verify=False  # SSL 검증 비활성화
            )
            
            if response.status_code == 200:
                token_data = response.json()
                # 키움 응답 키는 버전에 따라 달라질 수 있어 둘 다 허용
                self.access_token = token_data.get("access_token") or token_data.get("token")
                expires_in = token_data.get("expires_in", 7200)
                self.token_expiry = datetime.utcnow() + timedelta(seconds=int(expires_in))
                return True
            elif response.status_code == 429:
                # API 제한 에러 처리
                self.last_429_error_time = datetime.utcnow()
                logger.error(f"🔑 [TOKEN] ❌ API 호출 제한 초과 (HTTP 429) - {self.rate_limit_cooldown}초 동안 토큰 갱신 중지")
                logger.error(f"🔑 [TOKEN] 응답 내용: {response.text}")
                return False
            else:
                logger.error(f"🔑 [TOKEN] ❌ 토큰 갱신 실패 - HTTP {response.status_code}")
                logger.error(f"🔑 [TOKEN] 응답 내용: {response.text}")
                return False
            
        except Exception as e:
            logger.error(f"토큰 갱신 오류: {e}")
            return False
    
    def get_valid_token(self) -> Optional[str]:
        """유효한 액세스 토큰을 반환합니다."""
        logger.debug(f"🔑 [TOKEN_DEBUG] 유효한 토큰 요청")
        
        if self.is_token_valid():
            logger.debug(f"🔑 [TOKEN_DEBUG] ✅ 기존 토큰 유효")
            return self.access_token

        had_token = bool(self.access_token)
        expiry = self.token_expiry

        logger.debug(f"🔑 [TOKEN_DEBUG] 토큰이 유효하지 않음 - 갱신 시도")
        if not self.refresh_access_token():
            logger.debug(f"🔑 [TOKEN_DEBUG] 토큰 갱신 실패 - 재인증 시도")
            if not self.authenticate():
                if had_token and expiry and datetime.utcnow() < expiry:
                    logger.warning("🔑 [TOKEN] 갱신/재인증 실패 — 만료 전 기존 토큰 유지")
                    return self.access_token
                logger.error(f"🔑 [TOKEN_DEBUG] ❌ 재인증 실패 - 토큰 없음")
                return None
            logger.info(f"🔑 [TOKEN_DEBUG] ✅ 재인증 성공")
        else:
            logger.info(f"🔑 [TOKEN_DEBUG] ✅ 토큰 갱신 성공")
        
        logger.debug(f"🔑 [TOKEN_DEBUG] 반환할 토큰: {self.access_token[:20] if self.access_token else 'None'}...")
        return self.access_token