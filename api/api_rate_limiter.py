import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Optional
from enum import Enum

from core.config import Config

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
        self.max_calls_per_window = Config.API_MAX_CALLS_PER_MIN
        self.min_call_interval = Config.API_MIN_CALL_INTERVAL
        
        # 제한 복구 설정
        self.limit_duration_minutes = Config.API_LIMIT_DURATION_MIN
        self.recovery_check_interval = 300  # 복구 확인 간격 (초)

    def seconds_until_available(self) -> float:
        """다음 API 호출 가능까지 대기 시간(초). 0이면 즉시 가능."""
        current_time = datetime.now()
        if self.status == APILimitStatus.LIMITED and self.limit_until and current_time < self.limit_until:
            return (self.limit_until - current_time).total_seconds()
        if self.call_history:
            last_call_time = self.call_history[-1]["timestamp"]
            elapsed = (current_time - last_call_time).total_seconds()
            if elapsed < self.min_call_interval:
                return self.min_call_interval - elapsed
        return 0.0
        
    def is_api_available(self) -> bool:
        """API 사용 가능 여부 확인"""
        try:
            current_time = datetime.now()
            
            # 제한 상태 확인
            if self.status == APILimitStatus.LIMITED:
                if self.limit_until and current_time < self.limit_until:
                    remaining_time = (self.limit_until - current_time).total_seconds()
                    logger.debug(f"🚫 [API_LIMITER] API 제한 중 - {remaining_time:.1f}초 남음")
                    return False
                else:
                    self.status = APILimitStatus.RECOVERING
                    self.limit_until = None
                    logger.info("🔄 [API_LIMITER] API 제한 해제 - 복구 모드로 전환")
            
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
            
            if self.call_history:
                last_call_time = self.call_history[-1]["timestamp"]
                time_since_last_call = (current_time - last_call_time).total_seconds()
                if time_since_last_call < self.min_call_interval:
                    return False
            
            self.call_history.append({
                "api_name": api_name,
                "timestamp": current_time
            })
            
            if len(self.call_history) > self.max_history_size:
                self.call_history = self.call_history[-self.max_history_size:]
            
            window_start = current_time - timedelta(seconds=self.rate_limit_window)
            recent_calls = [
                call for call in self.call_history
                if call["timestamp"] >= window_start
            ]
            
            remaining_calls = self.max_calls_per_window - len(recent_calls)
            usage_percent = (len(recent_calls) / self.max_calls_per_window) * 100
            
            logger.debug(
                f"📊 [API_LIMITER] {api_name}: {len(recent_calls)}/{self.max_calls_per_window} "
                f"({usage_percent:.0f}%), 남은 {remaining_calls}"
            )
            
            if len(recent_calls) > self.max_calls_per_window:
                logger.warning(f"🚫 [API_LIMITER] 호출 한도 초과 - {len(recent_calls)}/{self.max_calls_per_window}")
                self._trigger_rate_limit()
                return False
            
            if len(recent_calls) > self.max_calls_per_window * 0.85:
                if self.status == APILimitStatus.NORMAL:
                    self.status = APILimitStatus.WARNING
                    logger.warning(f"⚠️ [API_LIMITER] 호출 빈도 높음 ({usage_percent:.0f}%)")
            
            return True
            
        except Exception as e:
            logger.error(f"🚫 [API_LIMITER] API 호출 기록 오류: {e}")
            return True
    
    def handle_api_error(self, error: Exception) -> bool:
        """API 오류 처리 및 제한 상태 업데이트"""
        try:
            error_str = str(error).lower()
            
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
            
            logger.warning(f"⚠️ [API_LIMITER] API 오류: {error}")
            self._increment_warning_count()
            
            return True
            
        except Exception as e:
            logger.error(f"🚫 [API_LIMITER] API 오류 처리 중 오류: {e}")
            return True
    
    def _trigger_rate_limit(self):
        """API 제한 트리거"""
        try:
            current_time = datetime.now()
            self.status = APILimitStatus.LIMITED
            self.limit_until = current_time + timedelta(minutes=self.limit_duration_minutes)
            self.warning_count = 0
            
            logger.warning(
                f"🚫 [API_LIMITER] {self.limit_duration_minutes}분간 API 호출 일시 중지 "
                f"(해제: {self.limit_until.strftime('%H:%M:%S')})"
            )
            
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
            
            window_start = current_time - timedelta(seconds=self.rate_limit_window)
            recent_calls = [
                call for call in self.call_history
                if call["timestamp"] >= window_start
            ]
            
            remaining_calls = self.max_calls_per_window - len(recent_calls)
            usage_percent = (len(recent_calls) / self.max_calls_per_window) * 100
            
            status_info = {
                "status": self.status.value,
                "limit_until": self.limit_until.isoformat() if self.limit_until else None,
                "warning_count": self.warning_count,
                "recent_calls": len(recent_calls),
                "max_calls_per_window": self.max_calls_per_window,
                "min_call_interval": self.min_call_interval,
                "remaining_calls": remaining_calls,
                "usage_percent": usage_percent,
                "is_available": self.is_api_available(),
                "seconds_until_available": self.seconds_until_available(),
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
    
    def log_current_status(self):
        """현재 상태를 로그에 출력"""
        try:
            status_info = self.get_status_info()
            
            logger.info("=" * 50)
            logger.info("📊 [API_LIMITER] 현재 API 제한 상태")
            logger.info(f"   상태: {status_info['status'].upper()}")
            logger.info(f"   사용량: {status_info['recent_calls']}/{status_info['max_calls_per_window']} ({status_info['usage_percent']:.1f}%)")
            logger.info(f"   최소 간격: {status_info.get('min_call_interval')}초")
            logger.info(f"   남은 횟수: {status_info['remaining_calls']}")
            logger.info(f"   사용 가능: {'✅ 예' if status_info['is_available'] else '❌ 아니오'}")
            
            if status_info['limit_until']:
                limit_until = datetime.fromisoformat(status_info['limit_until'])
                remaining_time = limit_until - datetime.now()
                logger.info(f"   제한 해제까지: {remaining_time}")
            
            logger.info("=" * 50)
            
        except Exception as e:
            logger.error(f"🚫 [API_LIMITER] 상태 로깅 오류: {e}")
    
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
        """제한 상태라면 대기 (동기 — 이벤트 루프에서 사용 금지)"""
        try:
            if not self.is_api_available():
                if self.limit_until:
                    wait_seconds = (self.limit_until - datetime.now()).total_seconds()
                    if wait_seconds > 0:
                        logger.info(f"⏳ [API_LIMITER] API 제한 해제까지 {wait_seconds:.0f}초 대기")
                        time.sleep(min(wait_seconds, 300))
                        return True
            return False
            
        except Exception as e:
            logger.error(f"🚫 [API_LIMITER] 제한 대기 오류: {e}")
            return False

# 전역 인스턴스
api_rate_limiter = APIRateLimiter()
