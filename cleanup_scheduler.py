"""
매수대기 목록 정리 스케줄러
오래된 매수대기 신호들을 정리하고 관리합니다.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import List
from sqlalchemy.orm import Session

from models import get_db, PendingBuySignal

logger = logging.getLogger(__name__)


class CleanupScheduler:
    """매수대기 목록 정리 스케줄러"""
    
    def __init__(self):
        self.is_running = False
        self.cleanup_task = None
        
        # 정리 설정
        self.max_age_hours = 24  # 24시간 이상 된 신호 정리
        self.cleanup_interval_minutes = 60  # 1시간마다 정리 실행
        
    async def start_scheduler(self):
        """정리 스케줄러 시작"""
        if self.is_running:
            logger.warning("정리 스케줄러가 이미 실행 중입니다")
            return
            
        self.is_running = True
        self.cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("🧹 [CLEANUP_SCHEDULER] 정리 스케줄러 시작")
    
    async def stop_scheduler(self):
        """정리 스케줄러 중지"""
        self.is_running = False
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
        logger.info("🧹 [CLEANUP_SCHEDULER] 정리 스케줄러 중지")
    
    async def _cleanup_loop(self):
        """정리 메인 루프"""
        while self.is_running:
            try:
                await self._cleanup_old_signals()
                await asyncio.sleep(self.cleanup_interval_minutes * 60)  # 분을 초로 변환
            except Exception as e:
                logger.error(f"🧹 [CLEANUP_SCHEDULER] 정리 루프 오류: {e}")
                await asyncio.sleep(300)  # 오류 시 5분 대기
    
    async def _cleanup_old_signals(self):
        """오래된 신호들 정리"""
        try:
            cutoff_time = datetime.now() - timedelta(hours=self.max_age_hours)
            
            for db in get_db():
                session: Session = db
                try:
                    # 오래된 PENDING 신호들 조회
                    old_signals = session.query(PendingBuySignal).filter(
                        PendingBuySignal.status == "PENDING",
                        PendingBuySignal.detected_at < cutoff_time
                    ).all()
                    
                    if old_signals:
                        # 상태를 EXPIRED로 변경
                        for signal in old_signals:
                            signal.status = "EXPIRED"
                            signal.failure_reason = f"정리됨 (생성 후 {self.max_age_hours}시간 경과)"
                        
                        session.commit()
                        logger.info(f"🧹 [CLEANUP_SCHEDULER] {len(old_signals)}개 오래된 신호 정리 완료")
                    else:
                        logger.debug("🧹 [CLEANUP_SCHEDULER] 정리할 오래된 신호 없음")
                    
                    break
                    
                except Exception as e:
                    logger.error(f"🧹 [CLEANUP_SCHEDULER] DB 정리 오류: {e}")
                    session.rollback()
                    continue
                    
        except Exception as e:
            logger.error(f"🧹 [CLEANUP_SCHEDULER] 신호 정리 오류: {e}")
    
    async def manual_cleanup(self) -> dict:
        """수동 정리 실행"""
        try:
            cutoff_time = datetime.now() - timedelta(hours=self.max_age_hours)
            cleaned_count = 0
            
            for db in get_db():
                session: Session = db
                try:
                    # 모든 PENDING 신호들 조회
                    pending_signals = session.query(PendingBuySignal).filter(
                        PendingBuySignal.status == "PENDING"
                    ).all()
                    
                    for signal in pending_signals:
                        # 오래된 신호이거나 특정 조건에 해당하는 경우 정리
                        if (signal.detected_at < cutoff_time or 
                            self._should_cleanup_signal(signal)):
                            signal.status = "EXPIRED"
                            signal.failure_reason = "수동 정리"
                            cleaned_count += 1
                    
                    session.commit()
                    break
                    
                except Exception as e:
                    logger.error(f"🧹 [CLEANUP_SCHEDULER] 수동 정리 DB 오류: {e}")
                    session.rollback()
                    continue
            
            logger.info(f"🧹 [CLEANUP_SCHEDULER] 수동 정리 완료: {cleaned_count}개 신호")
            return {
                "success": True,
                "cleaned_count": cleaned_count,
                "message": f"{cleaned_count}개의 매수대기 신호를 정리했습니다."
            }
            
        except Exception as e:
            logger.error(f"🧹 [CLEANUP_SCHEDULER] 수동 정리 오류: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": "정리 중 오류가 발생했습니다."
            }
    
    def _should_cleanup_signal(self, signal: PendingBuySignal) -> bool:
        """신호 정리 여부 판단"""
        try:
            # 추가 정리 조건들을 여기에 구현
            # 예: 특정 종목, 특정 전략, 특정 조건 등
            
            # 현재는 기본적으로 False 반환 (시간 기반 정리만 사용)
            return False
            
        except Exception as e:
            logger.error(f"🧹 [CLEANUP_SCHEDULER] 신호 정리 판단 오류: {e}")
            return False
    
    async def get_cleanup_status(self) -> dict:
        """정리 상태 조회"""
        try:
            total_pending = 0
            old_pending = 0
            
            for db in get_db():
                session: Session = db
                try:
                    # 전체 PENDING 신호 수
                    total_pending = session.query(PendingBuySignal).filter(
                        PendingBuySignal.status == "PENDING"
                    ).count()
                    
                    # 오래된 PENDING 신호 수
                    cutoff_time = datetime.now() - timedelta(hours=self.max_age_hours)
                    old_pending = session.query(PendingBuySignal).filter(
                        PendingBuySignal.status == "PENDING",
                        PendingBuySignal.detected_at < cutoff_time
                    ).count()
                    
                    break
                    
                except Exception as e:
                    logger.error(f"🧹 [CLEANUP_SCHEDULER] 상태 조회 DB 오류: {e}")
                    continue
            
            return {
                "is_running": self.is_running,
                "total_pending": total_pending,
                "old_pending": old_pending,
                "max_age_hours": self.max_age_hours,
                "cleanup_interval_minutes": self.cleanup_interval_minutes
            }
            
        except Exception as e:
            logger.error(f"🧹 [CLEANUP_SCHEDULER] 상태 조회 오류: {e}")
            return {
                "is_running": False,
                "error": str(e)
            }


# 전역 인스턴스
cleanup_scheduler = CleanupScheduler()
