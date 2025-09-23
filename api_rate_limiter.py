import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Optional
from enum import Enum

logger = logging.getLogger(__name__)

class APILimitStatus(Enum):
    """API 제한 상태"""
    NORMAL = "normal"           # 정상
    WARNING = "warning"         # 경고 (빈번한 요청)
    LIMITED = "limited"         # 제한됨
    RECOVERING = "recovering"   # 복구 중

class APIRateLimiter:
    """API 제한 관리 시스템 - 전역 API 제한 상태 관리"""
    
    def __init__(self):
        self.status = APILimitStatus.NORMAL
        self.limit_until = None
        self.warning_count = 0
        self.max_warnings = 5  # 최대 경고 횟수
        self.warning_reset_hours = 1  # 경고 리셋 시간 (시간)
        self.last_warning_reset = datetime.now()
        
        # API 호출 기록
        self.call_history = []
        self.max_history_size = 100
        self.rate_limit_window = 60  # 1분 윈도우
        self.max_calls_per_window = 20  # 1분당 최대 호출 수 (보수적 설정)
        
        # 제한 복구 설정
        self.limit_duration_minutes = 10  # 제한 지속 시간 (분)
        self.recovery_check_interval = 300  # 복구 확인 간격 (초)
        
    def is_api_available(self) -> bool:
        """API 사용 가능 여부 확인"""
        try:
            # 제한 상태 확인
            if self.status == APILimitStatus.LIMITED:
                if self.limit_until and datetime.now() < self.limit_until:
                    logger.debug(f"🚫 [API_LIMITER] API 제한 중 - {self.limit_until}까지 대기")
                    return False
                else:
                    # 제한 시간 만료 - 복구 상태로 변경
                    self.status = APILimitStatus.RECOVERING
                    self.limit_until = None
                    logger.info("🔄 [API_LIMITER] API 제한 해제 - 복구 모드로 전환")
            
            # 경고 상태 확인
            if self.status == APILimitStatus.WARNING:
                self._check_warning_reset()
            
            return True
            
        except Exception as e:
            logger.error(f"🚫 [API_LIMITER] API 가용성 확인 오류: {e}")
            return False
    
    def record_api_call(self, api_name: str = "unknown") -> bool:
        """API 호출 기록 및 제한 확인"""
        try:
            current_time = datetime.now()
            
            # 호출 기록 추가
            self.call_history.append({
                "api_name": api_name,
                "timestamp": current_time
            })
            
            # 기록 크기 제한
            if len(self.call_history) > self.max_history_size:
                self.call_history = self.call_history[-self.max_history_size:]
            
            # 윈도우 내 호출 수 확인
            window_start = current_time - timedelta(seconds=self.rate_limit_window)
            recent_calls = [
                call for call in self.call_history
                if call["timestamp"] >= window_start
            ]
            
            if len(recent_calls) > self.max_calls_per_window:
                logger.warning(f"🚫 [API_LIMITER] API 호출 한도 초과 - {len(recent_calls)}/{self.max_calls_per_window}")
                self._trigger_rate_limit()
                return False
            
            # 경고 상태 업데이트
            if len(recent_calls) > self.max_calls_per_window * 0.8:  # 80% 이상
                if self.status == APILimitStatus.NORMAL:
                    self.status = APILimitStatus.WARNING
                    logger.warning("⚠️ [API_LIMITER] API 호출 빈도 높음 - 경고 상태")
            
            return True
            
        except Exception as e:
            logger.error(f"🚫 [API_LIMITER] API 호출 기록 오류: {e}")
            return True  # 오류 시에도 호출 허용
    
    def handle_api_error(self, error: Exception) -> bool:
        """API 오류 처리 및 제한 상태 업데이트"""
        try:
            error_str = str(error).lower()
            
            # API 제한 관련 오류 감지
            if any(keyword in error_str for keyword in [
                "허용된 요청 개수를 초과",
                "429",
                "rate limit",
                "too many requests",
                "api 제한",
                "요청 한도 초과"
            ]):
                logger.warning(f"🚫 [API_LIMITER] API 제한 오류 감지: {error}")
                self._trigger_rate_limit()
                return False
            
            # 기타 오류는 경고만
            logger.warning(f"⚠️ [API_LIMITER] API 오류: {error}")
            self._increment_warning_count()
            
            return True
            
        except Exception as e:
            logger.error(f"🚫 [API_LIMITER] API 오류 처리 중 오류: {e}")
            return True
    
    def _trigger_rate_limit(self):
        """API 제한 트리거"""
        try:
            self.status = APILimitStatus.LIMITED
            self.limit_until = datetime.now() + timedelta(minutes=self.limit_duration_minutes)
            self.warning_count = 0
            
            logger.warning(f"🚫 [API_LIMITER] API 제한 활성화 - {self.limit_until}까지 제한")
            
        except Exception as e:
            logger.error(f"🚫 [API_LIMITER] API 제한 트리거 오류: {e}")
    
    def _increment_warning_count(self):
        """경고 카운트 증가"""
        try:
            self.warning_count += 1
            
            if self.warning_count >= self.max_warnings:
                logger.warning(f"🚫 [API_LIMITER] 경고 횟수 초과 ({self.warning_count}/{self.max_warnings}) - 제한 활성화")
                self._trigger_rate_limit()
            else:
                logger.warning(f"⚠️ [API_LIMITER] 경고 횟수: {self.warning_count}/{self.max_warnings}")
                
        except Exception as e:
            logger.error(f"🚫 [API_LIMITER] 경고 카운트 증가 오류: {e}")
    
    def _check_warning_reset(self):
        """경고 상태 리셋 확인"""
        try:
            current_time = datetime.now()
            
            # 경고 리셋 시간 확인
            if current_time - self.last_warning_reset >= timedelta(hours=self.warning_reset_hours):
                self.warning_count = 0
                self.last_warning_reset = current_time
                self.status = APILimitStatus.NORMAL
                logger.info("✅ [API_LIMITER] 경고 상태 리셋 - 정상 상태로 복구")
                
        except Exception as e:
            logger.error(f"🚫 [API_LIMITER] 경고 리셋 확인 오류: {e}")
    
    def get_status_info(self) -> Dict:
        """현재 상태 정보 반환"""
        try:
            current_time = datetime.now()
            
            # 윈도우 내 호출 수 계산
            window_start = current_time - timedelta(seconds=self.rate_limit_window)
            recent_calls = [
                call for call in self.call_history
                if call["timestamp"] >= window_start
            ]
            
            status_info = {
                "status": self.status.value,
                "limit_until": self.limit_until.isoformat() if self.limit_until else None,
                "warning_count": self.warning_count,
                "recent_calls": len(recent_calls),
                "max_calls_per_window": self.max_calls_per_window,
                "is_available": self.is_api_available(),
                "last_warning_reset": self.last_warning_reset.isoformat()
            }
            
            return status_info
            
        except Exception as e:
            logger.error(f"🚫 [API_LIMITER] 상태 정보 조회 오류: {e}")
            return {
                "status": "error",
                "error": str(e),
                "is_available": False
            }
    
    def reset_limits(self):
        """제한 상태 초기화 (수동 리셋)"""
        try:
            self.status = APILimitStatus.NORMAL
            self.limit_until = None
            self.warning_count = 0
            self.last_warning_reset = datetime.now()
            self.call_history.clear()
            
            logger.info("🔄 [API_LIMITER] 제한 상태 수동 초기화 완료")
            
        except Exception as e:
            logger.error(f"🚫 [API_LIMITER] 제한 상태 초기화 오류: {e}")
    
    def wait_if_limited(self) -> bool:
        """제한 상태라면 대기"""
        try:
            if not self.is_api_available():
                if self.limit_until:
                    wait_seconds = (self.limit_until - datetime.now()).total_seconds()
                    if wait_seconds > 0:
                        logger.info(f"⏳ [API_LIMITER] API 제한 해제까지 {wait_seconds:.0f}초 대기")
                        time.sleep(min(wait_seconds, 300))  # 최대 5분 대기
                        return True
            return False
            
        except Exception as e:
            logger.error(f"🚫 [API_LIMITER] 제한 대기 오류: {e}")
            return False

# 전역 인스턴스
api_rate_limiter = APIRateLimiter()
