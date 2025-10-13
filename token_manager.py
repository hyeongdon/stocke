from datetime import datetime, timedelta
from typing import Optional
import requests
import logging
from config import Config

logger = logging.getLogger(__name__)

class TokenManager:
    def __init__(self):
        self.access_token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None
        self.refresh_token: Optional[str] = None
    
    def authenticate(self) -> bool:
        """키움증권 API 인증을 수행하고 토큰을 발급받습니다."""
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
                timeout=10
            )
            
            logger.debug(f"🔑 [TOKEN_DEBUG] HTTP 응답 상태: {response.status_code}")
            
            if response.status_code == 200:
                token_data = response.json()
                logger.debug(f"🔑 [TOKEN_DEBUG] API 응답 데이터: {token_data}")
                
                # 키움증권 API 응답에서 오류 확인
                if token_data.get("return_code") == 0:  # 성공
                    self.access_token = token_data.get("token")  # 키움증권은 'token' 필드 사용
                    logger.info(f"🔑 [TOKEN_DEBUG] ✅ 토큰 발급 성공: {self.access_token[:20]}...")
                    
                    # expires_dt 형식: "20250809005645" -> datetime으로 변환
                    expires_dt_str = token_data.get("expires_dt")
                    if expires_dt_str:
                        self.token_expiry = datetime.strptime(expires_dt_str, "%Y%m%d%H%M%S")
                        logger.debug(f"🔑 [TOKEN_DEBUG] 토큰 만료 시간: {self.token_expiry}")
                    else:
                        self.token_expiry = datetime.utcnow() + timedelta(hours=24)  # 기본 24시간
                        logger.debug(f"🔑 [TOKEN_DEBUG] 기본 토큰 만료 시간 설정: {self.token_expiry}")
                    return True
                else:
                    logger.error(f"🔑 [TOKEN_DEBUG] ❌ 키움증권 API 오류: {token_data.get('return_msg', '알 수 없는 오류')}")
                    return False
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
        if not self.refresh_token:
            return self.authenticate()
        
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
                }
            )
            
            if response.status_code == 200:
                token_data = response.json()
                self.access_token = token_data.get("access_token")
                expires_in = token_data.get("expires_in", 7200)
                self.token_expiry = datetime.utcnow() + timedelta(seconds=expires_in)
                return True
            return False
            
        except Exception as e:
            logger.error(f"토큰 갱신 오류: {e}")
            return False
    
    def get_valid_token(self) -> Optional[str]:
        """유효한 액세스 토큰을 반환합니다."""
        logger.debug(f"🔑 [TOKEN_DEBUG] 유효한 토큰 요청")
        
        if not self.is_token_valid():
            logger.debug(f"🔑 [TOKEN_DEBUG] 토큰이 유효하지 않음 - 갱신 시도")
            if not self.refresh_access_token():
                logger.debug(f"🔑 [TOKEN_DEBUG] 토큰 갱신 실패 - 재인증 시도")
                if not self.authenticate():
                    logger.error(f"🔑 [TOKEN_DEBUG] ❌ 재인증 실패 - 토큰 없음")
                    return None
                else:
                    logger.info(f"🔑 [TOKEN_DEBUG] ✅ 재인증 성공")
            else:
                logger.info(f"🔑 [TOKEN_DEBUG] ✅ 토큰 갱신 성공")
        else:
            logger.debug(f"🔑 [TOKEN_DEBUG] ✅ 기존 토큰 유효")
        
        logger.debug(f"🔑 [TOKEN_DEBUG] 반환할 토큰: {self.access_token[:20] if self.access_token else 'None'}...")
        return self.access_token