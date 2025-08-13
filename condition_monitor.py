import logging
from datetime import datetime
from typing import Dict, Set
from sqlalchemy.orm import Session
from models import StockSignal, ConditionLog, get_db
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
            # 조건식 결과 조회
            results = self.kiwoom_api.get_condition_result(condition_id, condition_name)
            
            if results:
                # DB 연결
                db = next(get_db())
                try:
                    # 조건 만족 종목들에 대해 신호 생성
                    for stock_data in results:
                        await self._create_signal(db, condition_id, stock_data)
                    
                    logger.info(f"조건식 {condition_id} 모니터링 결과: {len(results)}개 종목 감지")
                    return True
                    
                finally:
                    db.close()
            
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
    
    async def _create_signal(self, db: Session, condition_id: int, stock_data: Dict):
        """신호 생성"""
        try:
            # 중복 신호 확인
            if not self.is_duplicate_signal(condition_id, stock_data["stock_code"]):
                # 신호 생성
                signal = StockSignal(
                    condition_id=condition_id,
                    stock_code=stock_data["stock_code"],
                    stock_name=stock_data["stock_name"],
                    signal_type="CONDITION",
                    created_at=datetime.now()
                )
                
                db.add(signal)
                db.commit()
                
                # 로그 기록
                self._log_condition(db, condition_id, 
                                  f"조건 만족: {stock_data['stock_name']}({stock_data['stock_code']})")
                
                logger.info(f"신호 생성: {stock_data['stock_name']}({stock_data['stock_code']})")
            
        except Exception as e:
            logger.error(f"신호 생성 중 오류: {e}")
            db.rollback()
    
    def _log_condition(self, db: Session, condition_id: int, message: str, log_type: str = "INFO"):
        """조건식 로그 기록"""
        try:
            log = ConditionLog(
                condition_id=condition_id,
                message=message,
                log_type=log_type,
                created_at=datetime.now()
            )
            
            db.add(log)
            db.commit()
            
        except Exception as e:
            logger.error(f"로그 기록 중 오류: {e}")
            db.rollback()
    
    async def start_all_monitoring(self):
        """모든 조건식 모니터링 시작"""
        self.is_running = True
        logger.info("모든 조건식 모니터링 시작")
    
    async def stop_all_monitoring(self):
        """모든 조건식 모니터링 중지"""
        self.is_running = False
        logger.info("모든 조건식 모니터링 중지")
    
    def get_monitoring_status(self) -> Dict:
        """모니터링 상태 조회"""
        return {
            "is_running": self.is_running,
            "processed_signals": len(self.processed_signals)
        }

# 전역 인스턴스
condition_monitor = ConditionMonitor()