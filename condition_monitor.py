import logging
from datetime import datetime, timedelta
from typing import Dict, Set
# DB 관련 import는 나중에 필요시 추가
# from sqlalchemy.orm import Session
# from models import StockSignal, ConditionLog, get_db
from kiwoom_api import KiwoomAPI

logger = logging.getLogger(__name__)

class ConditionMonitor:
    """조건식 모니터링 시스템"""
    
    def __init__(self):
        self.kiwoom_api = KiwoomAPI()
        self.is_running = False
        self.processed_signals: Dict[str, datetime] = {}  # 중복 감지 방지 (신호키: 타임스탬프)
        self.signal_ttl_minutes = 5  # 신호 중복 방지 TTL (분)
    
    async def start_monitoring(self, condition_id: int, condition_name: str) -> bool:
        """조건식 모니터링 시작"""
        logger.info(f"🔍 [CONDITION_MONITOR] 조건식 모니터링 시작 요청 - ID: {condition_id}, 이름: {condition_name}")
        try:
            # 조건식으로 종목 검색
            logger.debug(f"🔍 [CONDITION_MONITOR] 키움 API로 종목 검색 시작 - 조건식 ID: {condition_id}")
            results = await self.kiwoom_api.search_condition_stocks(str(condition_id), condition_name)
            
            if results:
                logger.info(f"🔍 [CONDITION_MONITOR] 종목 검색 완료 - {len(results)}개 종목 발견")
                # 조건 만족 종목들에 대해 신호 처리 (DB 없이)
                for i, stock_data in enumerate(results, 1):
                    logger.debug(f"🔍 [CONDITION_MONITOR] 신호 처리 중 ({i}/{len(results)}) - {stock_data.get('stock_name', 'Unknown')}")
                    await self._process_signal(condition_id, stock_data)
                
                logger.info(f"🔍 [CONDITION_MONITOR] 조건식 {condition_id} 모니터링 완료 - {len(results)}개 종목 처리됨")
                return True
            else:
                logger.info(f"🔍 [CONDITION_MONITOR] 조건식 {condition_id}에 해당하는 종목이 없음")
                return False
            
        except Exception as e:
            logger.error(f"🔍 [CONDITION_MONITOR] 조건식 {condition_id} 모니터링 시작 실패: {e}")
            import traceback
            logger.error(f"🔍 [CONDITION_MONITOR] 스택 트레이스: {traceback.format_exc()}")
            return False
    
    def _cleanup_expired_signals(self):
        """만료된 신호 정리"""
        current_time = datetime.now()
        expired_keys = [
            key for key, timestamp in self.processed_signals.items()
            if current_time - timestamp > timedelta(minutes=self.signal_ttl_minutes)
        ]
        
        for key in expired_keys:
            del self.processed_signals[key]
        
        if expired_keys:
            logger.debug(f"만료된 신호 {len(expired_keys)}개 정리 완료")
    
    def is_duplicate_signal(self, condition_id: int, stock_code: str) -> bool:
        """중복 신호 확인 (TTL 기반)"""
        signal_key = f"{condition_id}_{stock_code}"
        current_time = datetime.now()
        
        logger.debug(f"🔍 [CONDITION_MONITOR] 중복 신호 확인 - 신호키: {signal_key}")
        
        # 만료된 신호 정리
        self._cleanup_expired_signals()
        
        if signal_key in self.processed_signals:
            # TTL 내의 신호인지 확인
            signal_time = self.processed_signals[signal_key]
            time_diff = current_time - signal_time
            if time_diff <= timedelta(minutes=self.signal_ttl_minutes):
                logger.debug(f"🔍 [CONDITION_MONITOR] 중복 신호 감지 - {signal_key} (TTL 내: {time_diff.total_seconds():.1f}초 전)")
                return True
            else:
                # 만료된 신호는 제거하고 새로 등록
                logger.debug(f"🔍 [CONDITION_MONITOR] 만료된 신호 제거 - {signal_key} (TTL 초과: {time_diff.total_seconds():.1f}초 전)")
                del self.processed_signals[signal_key]
        
        # 새 신호 등록
        self.processed_signals[signal_key] = current_time
        logger.debug(f"🔍 [CONDITION_MONITOR] 새 신호 등록 - {signal_key}")
        return False
    
    async def _process_signal(self, condition_id: int, stock_data: Dict):
        """신호 처리 (DB 없이)"""
        stock_code = stock_data.get("stock_code", "Unknown")
        stock_name = stock_data.get("stock_name", "Unknown")
        
        logger.debug(f"🔍 [CONDITION_MONITOR] 신호 처리 시작 - {stock_name}({stock_code})")
        
        try:
            # 중복 신호 확인
            if not self.is_duplicate_signal(condition_id, stock_code):
                # 신호 처리 (로깅만)
                logger.info(f"🔍 [CONDITION_MONITOR] 조건 만족 신호 감지: {stock_name}({stock_code}) - 조건식 ID: {condition_id}")
                
                # 여기에 추가적인 신호 처리 로직을 구현할 수 있습니다
                # 예: 알림 전송, 웹소켓으로 실시간 전송 등
                logger.debug(f"🔍 [CONDITION_MONITOR] 신호 처리 완료 - {stock_name}({stock_code})")
            else:
                logger.debug(f"🔍 [CONDITION_MONITOR] 중복 신호로 인해 처리 건너뜀 - {stock_name}({stock_code})")
                
        except Exception as e:
            logger.error(f"🔍 [CONDITION_MONITOR] 신호 처리 중 오류 - {stock_name}({stock_code}): {e}")
            import traceback
            logger.error(f"🔍 [CONDITION_MONITOR] 스택 트레이스: {traceback.format_exc()}")
    
    async def start_all_monitoring(self):
        """모든 조건식 모니터링 시작"""
        logger.info("🔍 [CONDITION_MONITOR] 모든 조건식 모니터링 시작 요청")
        self.is_running = True
        logger.info("🔍 [CONDITION_MONITOR] 모니터링 상태: RUNNING")
        logger.info("🔍 [CONDITION_MONITOR] 현재 처리된 신호 수: {len(self.processed_signals)}")
    
    async def stop_all_monitoring(self):
        """모든 조건식 모니터링 중지"""
        logger.info("🔍 [CONDITION_MONITOR] 모든 조건식 모니터링 중지 요청")
        self.is_running = False
        logger.info("🔍 [CONDITION_MONITOR] 모니터링 상태: STOPPED")
        # WebSocket 연결 종료 추가
        await self.kiwoom_api.disconnect()
        logger.info("🔍 [CONDITION_MONITOR] 모든 조건식 모니터링 중지 및 WebSocket 연결 종료")
    
    def get_monitoring_status(self) -> Dict:
        """모니터링 상태 조회"""
        logger.debug("🔍 [CONDITION_MONITOR] 모니터링 상태 조회 요청")
        # 만료된 신호 정리
        self._cleanup_expired_signals()
        
        status = {
            "is_running": self.is_running,
            "processed_signals": len(self.processed_signals),
            "signal_ttl_minutes": self.signal_ttl_minutes
        }
        
        logger.debug(f"🔍 [CONDITION_MONITOR] 모니터링 상태: {status}")
        return status

# 전역 인스턴스
condition_monitor = ConditionMonitor()