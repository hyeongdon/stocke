import logging
from datetime import datetime
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
        self.processed_signals: Set[str] = set()  # 중복 감지 방지
    
    async def start_monitoring(self, condition_id: int, condition_name: str) -> bool:
        """조건식 모니터링 시작"""
        try:
            # 조건식으로 종목 검색
            results = await self.kiwoom_api.search_condition_stocks(str(condition_id), condition_name)
            
            if results:
                # 조건 만족 종목들에 대해 신호 처리 (DB 없이)
                for stock_data in results:
                    await self._process_signal(condition_id, stock_data)
                
                logger.info(f"조건식 {condition_id} 모니터링 결과: {len(results)}개 종목 감지")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"조건식 {condition_id} 모니터링 시작 실패: {e}")
            return False
    
    def is_duplicate_signal(self, condition_id: int, stock_code: str) -> bool:
        """중복 신호 확인"""
        signal_key = f"{condition_id}_{stock_code}"
        
        if signal_key in self.processed_signals:
            return True
        
        self.processed_signals.add(signal_key)
        return False
    
    async def _process_signal(self, condition_id: int, stock_data: Dict):
        """신호 처리 (DB 없이)"""
        try:
            # 중복 신호 확인
            if not self.is_duplicate_signal(condition_id, stock_data["stock_code"]):
                # 신호 처리 (로깅만)
                logger.info(f"조건 만족 신호: {stock_data['stock_name']}({stock_data['stock_code']})")
                
                # 여기에 추가적인 신호 처리 로직을 구현할 수 있습니다
                # 예: 알림 전송, 웹소켓으로 실시간 전송 등
                
        except Exception as e:
            logger.error(f"신호 처리 중 오류: {e}")
    
    async def start_all_monitoring(self):
        """모든 조건식 모니터링 시작"""
        self.is_running = True
        logger.info("모든 조건식 모니터링 시작")
    
    async def stop_all_monitoring(self):
        """모든 조건식 모니터링 중지"""
        self.is_running = False
        # WebSocket 연결 종료 추가
        await self.kiwoom_api.disconnect()
        logger.info("모든 조건식 모니터링 중지 및 WebSocket 연결 종료")
    
    def get_monitoring_status(self) -> Dict:
        """모니터링 상태 조회"""
        return {
            "is_running": self.is_running,
            "processed_signals": len(self.processed_signals)
        }

# 전역 인스턴스
condition_monitor = ConditionMonitor()