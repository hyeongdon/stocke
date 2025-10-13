import logging
import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, Set, List, Optional
# pandas 제거됨 - 기준봉 전략에서만 사용
# DB 관련 import
from kiwoom_api import KiwoomAPI
from models import PendingBuySignal, get_db, AutoTradeCondition
from sqlalchemy.orm import Session

# 개선된 모듈들 import
from signal_manager import signal_manager, SignalType, SignalStatus
from api_rate_limiter import api_rate_limiter
from buy_order_executor import buy_order_executor
from watchlist_sync_manager import watchlist_sync_manager

logger = logging.getLogger(__name__)

class ConditionMonitor:
    """조건식 모니터링 시스템"""
    
    def __init__(self):
        self.kiwoom_api = KiwoomAPI()
        self.is_running = False
        self.loop_sleep_seconds = 600  # 10분 주기
        self._monitor_task: Optional[asyncio.Task] = None
        self.start_time: Optional[datetime] = None  # 모니터링 시작 시간
        
        # 기준봉 전략 제거됨 - 현재 매매전략에 집중
    
    async def start_monitoring(self, condition_id: int, condition_name: str) -> bool:
        """조건식 모니터링 시작 (신호 생성 제거)"""
        logger.info(f"🔍 [CONDITION_MONITOR] 조건식 모니터링 시작 요청 - ID: {condition_id}, 이름: {condition_name}")
        try:
            # API 제한 확인
            if not api_rate_limiter.is_api_available():
                logger.warning(f"🔍 [CONDITION_MONITOR] API 제한 상태 - 조건식 {condition_id} 모니터링 건너뜀")
                return False
            
            # 조건식으로 종목 검색 (신호 생성 없이)
            logger.debug(f"🔍 [CONDITION_MONITOR] 키움 API로 종목 검색 시작 - 조건식 ID: {condition_id}")
            results = await self.kiwoom_api.search_condition_stocks(str(condition_id), condition_name)
            
            # API 호출 기록
            api_rate_limiter.record_api_call(f"search_condition_stocks_{condition_id}")
            
            if results:
                logger.info(f"🔍 [CONDITION_MONITOR] 종목 검색 완료 - {len(results)}개 종목 발견 (신호 생성 없음)")
                
                # 기준봉 전략 제거됨 - 조건식 검색만 수행
                
                logger.info(f"🔍 [CONDITION_MONITOR] 조건식 {condition_id} 모니터링 완료 - {len(results)}개 종목 확인됨 (신호 생성 안함)")
                return True
            else:
                logger.info(f"🔍 [CONDITION_MONITOR] 조건식 {condition_name} (API ID: {condition_id})에 해당하는 종목이 없음")
                return False
            
        except Exception as e:
            logger.error(f"🔍 [CONDITION_MONITOR] 조건식 {condition_id} 모니터링 시작 실패: {e}")
            # API 오류 처리
            api_rate_limiter.handle_api_error(e)
            import traceback
            logger.error(f"🔍 [CONDITION_MONITOR] 스택 트레이스: {traceback.format_exc()}")
            return False
    
    
    async def _process_signal(self, condition_id: int, stock_data: Dict):
        """신호 처리 (비활성화됨)"""
        # 신호 생성 기능이 제거되어 비활성화됨
        logger.debug(f"🔍 [CONDITION_MONITOR] 신호 처리 비활성화됨 - {stock_data.get('stock_name', 'Unknown')}({stock_data.get('stock_code', 'Unknown')})")
        return
    
    async def _scan_once(self):
        """활성 조건식에 대해 한 번 스캔 수행"""
        # WebSocket 연결 보장
        if not self.kiwoom_api.running or self.kiwoom_api.websocket is None:
            logger.info("🔍 [CONDITION_MONITOR] WebSocket 미연결 상태 감지 - 재연결 시도")
            try:
                connected = await self.kiwoom_api.connect()
                logger.info(f"🔍 [CONDITION_MONITOR] WebSocket 재연결 결과: {connected}")
            except Exception as conn_err:
                logger.error(f"🔍 [CONDITION_MONITOR] WebSocket 재연결 실패: {conn_err}")
                pass

        # 조건식 목록 조회
        logger.debug("🔍 [CONDITION_MONITOR] 조건식 목록 조회 시작")
        conditions = await self.kiwoom_api.get_condition_list_websocket()
        logger.info(f"🔍 [CONDITION_MONITOR] 키움 API에서 받은 조건식: {len(conditions)}개")
        for i, cond in enumerate(conditions):
            logger.info(f"🔍 [CONDITION_MONITOR]   {i+1}. {cond.get('condition_name')} (API ID: {cond.get('condition_id')})")

        # 자동매매 대상만 필터링
        enabled_set = set()
        for db in get_db():
            session: Session = db
            rows = session.query(AutoTradeCondition).filter(AutoTradeCondition.is_enabled == True).all()
            enabled_set = {row.condition_name for row in rows}

        if not conditions:
            logger.warning("🔍 [CONDITION_MONITOR] 조건식 목록이 비어있습니다.")
            return

        # 자동매매 활성 조건이 하나도 없으면 스캔하지 않음
        if not enabled_set:
            logger.info("🔍 [CONDITION_MONITOR] 활성화된 자동매매 조건이 없음 - 스캔 건너뜀")
            return

        logger.info(f"🔍 [CONDITION_MONITOR] 조건식 {len(conditions)}개 발견 - 순차 검색 시작")

        # 각 조건식에 대해 즉시 한 번 검색 실행
        for idx, cond in enumerate(conditions):
            condition_name = cond.get("condition_name", f"조건식_{idx+1}")
            condition_api_id = cond.get("condition_id", str(idx))
            if condition_name not in enabled_set:
                logger.info(f"🔍 [CONDITION_MONITOR] 비활성 조건식 스킵: {condition_name} (API ID: {condition_api_id})")
                continue
            logger.info(f"🔍 [CONDITION_MONITOR] 조건식 실행: {condition_name} (API ID: {condition_api_id})")
            # 키움에서 제공한 실제 조건식 ID로 조회
            await self.start_monitoring(condition_id=condition_api_id, condition_name=condition_name)

        logger.info("🔍 [CONDITION_MONITOR] 모든 조건식 1회 모니터링 완료")
        
        # 기준봉 전략 제거됨 - 현재 매매전략에 집중

    async def start_periodic_monitoring(self):
        """모든 조건식을 주기적으로 모니터링 (백그라운드 태스크로 실행)"""
        logger.info("🔍 [CONDITION_MONITOR] 주기적 모니터링 시작 요청")
        if self.is_running:
            logger.info("🔍 [CONDITION_MONITOR] 이미 실행 중입니다")
            return
        self.is_running = True
        self.start_time = datetime.now()  # 모니터링 시작 시간 기록
        logger.info("🔍 [CONDITION_MONITOR] 모니터링 상태: RUNNING")
        
        # 관심종목 동기화는 독립적으로 제어 (별도 토글로 시작/중지)
        # await watchlist_sync_manager.start_auto_sync()
        
        # 백그라운드 태스크로 루프 실행
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("🔍 [CONDITION_MONITOR] 모니터링 루프가 백그라운드에서 시작되었습니다")

    async def _monitor_loop(self):
        try:
            while self.is_running:
                logger.info("🔁 [CONDITION_MONITOR] 주기 스캔 시작")
                try:
                    await self._scan_once()
                except Exception as e:
                    logger.error(f"🔍 [CONDITION_MONITOR] 스캔 중 오류: {e}")
                    import traceback
                    logger.error(f"🔍 [CONDITION_MONITOR] 스택 트레이스: {traceback.format_exc()}")
                logger.info(f"⏳ [CONDITION_MONITOR] 다음 스캔까지 대기 {self.loop_sleep_seconds}초")
                if not self.is_running:
                    break
                await asyncio.sleep(self.loop_sleep_seconds)
        finally:
            logger.info("🛑 [CONDITION_MONITOR] 주기적 모니터링 루프 종료")
    
    async def stop_all_monitoring(self):
        """모든 조건식 모니터링 중지"""
        logger.info("🔍 [CONDITION_MONITOR] 모든 조건식 모니터링 중지 요청")
        self.is_running = False
        self.start_time = None  # 시작 시간 초기화
        logger.info("🔍 [CONDITION_MONITOR] 모니터링 상태: STOPPED")
        
        # 관심종목 동기화는 독립적으로 유지 (별도 토글로 제어)
        # await watchlist_sync_manager.stop_auto_sync()
        
        # 백그라운드 태스크가 있다면 안전하게 종료 대기/취소
        if self._monitor_task is not None:
            try:
                await asyncio.wait_for(self._monitor_task, timeout=1.0)
            except asyncio.TimeoutError:
                self._monitor_task.cancel()
                try:
                    await self._monitor_task
                except asyncio.CancelledError:
                    pass
            finally:
                self._monitor_task = None
        # WebSocket 연결 종료 추가 (타임아웃 내 비차단)
        try:
            await asyncio.wait_for(self.kiwoom_api.disconnect(), timeout=3.0)
        except asyncio.TimeoutError:
            logger.warning("🔍 [CONDITION_MONITOR] disconnect 타임아웃 - 강제 종료 진행")
        logger.info("🔍 [CONDITION_MONITOR] 모든 조건식 모니터링 중지 및 WebSocket 연결 종료")
    
    async def get_monitoring_status(self) -> Dict:
        """모니터링 상태 조회 (개선된 상태 정보 포함)"""
        logger.debug("🔍 [CONDITION_MONITOR] 모니터링 상태 조회 요청")
        
        # 신호 통계 조회
        signal_stats = await signal_manager.get_signal_statistics()
        
        # API 제한 상태 조회
        api_status = api_rate_limiter.get_status_info()
        
        # 관심종목 동기화 상태 조회
        watchlist_sync_status = await watchlist_sync_manager.get_sync_status()
        
        # 실행시간 계산
        running_time_minutes = 0
        if self.is_running and self.start_time:
            running_time = datetime.now() - self.start_time
            running_time_minutes = int(running_time.total_seconds() / 60)
        
        status = {
            "is_running": self.is_running,
            "running_time_minutes": running_time_minutes,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "loop_sleep_seconds": self.loop_sleep_seconds,
            "signal_statistics": signal_stats,
            "api_status": api_status,
            "reference_candles_count": 0,  # 기준봉 전략 제거됨
            "active_strategies": 0,  # 기준봉 전략 제거됨
            "watchlist_sync": watchlist_sync_status
        }
        
        logger.debug(f"🔍 [CONDITION_MONITOR] 모니터링 상태: {status}")
        return status



    # 기준봉 전략 제거됨 - 현재 매매전략에 집중



        return

# 전역 인스턴스
condition_monitor = ConditionMonitor()