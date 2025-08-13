from datetime import datetime, timedelta
from typing import Optional
import requests
from config import Config

class TokenManager:
    def __init__(self):
        self.access_token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None
        self.refresh_token: Optional[str] = None
    
    def authenticate(self) -> bool:
        """키움증권 API 인증을 수행하고 토큰을 발급받습니다."""
        try:
            response = requests.post(
                f"{Config.KIWOOM_BASE_URL}/oauth2/token",
                json={
                    "grant_type": "client_credentials",
                    "appkey": Config.KIWOOM_APP_KEY,
                    "secretkey": Config.KIWOOM_APP_SECRET
                },
                headers={
                    "Content-Type": "application/json"
                },
                timeout=10
            )
            
            if response.status_code == 200:
                token_data = response.json()
                # 키움증권 API 응답에서 오류 확인
                if token_data.get("return_code") == 0:  # 성공
                    self.access_token = token_data.get("token")  # 키움증권은 'token' 필드 사용
                    # expires_dt 형식: "20250809005645" -> datetime으로 변환
                    expires_dt_str = token_data.get("expires_dt")
                    if expires_dt_str:
                        self.token_expiry = datetime.strptime(expires_dt_str, "%Y%m%d%H%M%S")
                    else:
                        self.token_expiry = datetime.utcnow() + timedelta(hours=24)  # 기본 24시간
                    return True
                else:
                    print(f"키움증권 API 오류: {token_data.get('return_msg', '알 수 없는 오류')}")
                    return False
            else:
                print(f"키움증권 API 인증 실패 - HTTP {response.status_code}")
                return False
            
        except Exception as e:
            print(f"키움증권 API 인증 오류: {type(e).__name__}: {e}")
            return False
    
    def is_token_valid(self) -> bool:
        """토큰이 유효한지 확인합니다."""
        if not self.access_token or not self.token_expiry:
            return False
        
        # 만료 10분 전부터는 토큰을 갱신
        return datetime.utcnow() < (self.token_expiry - timedelta(minutes=10))
    
    def refresh_access_token(self) -> bool:
        """리프레시 토큰을 사용하여 액세스 토큰을 갱신합니다."""
        if not self.refresh_token:
            return self.authenticate()
        
        try:
            response = requests.post(
                f"{Config.KIWOOM_BASE_URL}/oauth2/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token
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
            print(f"토큰 갱신 오류: {e}")
            return False
    
    def get_valid_token(self) -> Optional[str]:
        """유효한 액세스 토큰을 반환합니다."""
        if not self.is_token_valid():
            if not self.refresh_access_token():
                if not self.authenticate():
                    return None
        return self.access_token