import logging
import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, Set, List, Optional
# pandas 제거됨 - 기준봉 전략에서만 사용
# DB 관련 import
from api.kiwoom_api import KiwoomAPI
from core.models import PendingBuySignal, get_db, AutoTradeCondition
from sqlalchemy.orm import Session
from core.config import Config

# 개선된 모듈들 import
from managers.signal_manager import signal_manager, SignalType, SignalStatus
from api.api_rate_limiter import api_rate_limiter
from managers.buy_order_executor import buy_order_executor
from managers.watchlist_sync_manager import watchlist_sync_manager

logger = logging.getLogger(__name__)

class ConditionMonitor:
    """조건식 모니터링 시스템"""
    
    def __init__(self):
        self.kiwoom_api = KiwoomAPI()
        self.is_running = False
        self.loop_sleep_seconds = max(60, int(getattr(Config, "CONDITION_MONITOR_INTERVAL", 600) or 600))
        self._monitor_task: Optional[asyncio.Task] = None
        self.start_time: Optional[datetime] = None  # 모니터링 시작 시간
        
        # 기준봉 전략 제거됨 - 현재 매매전략에 집중
    
    async def start_monitoring(self, condition_id: int, condition_name: str) -> bool:
        """조건식 모니터링 시작 (조건식 결과 -> PendingBuySignal 신호 생성)"""
        logger.info(f"🔍 [CONDITION_MONITOR] 조건식 모니터링 시작 요청 - ID: {condition_id}, 이름: {condition_name}")
        try:
            # API 제한 확인
            if not api_rate_limiter.is_api_available():
                logger.warning(f"🔍 [CONDITION_MONITOR] API 제한 상태 - 조건식 {condition_id} 모니터링 건너뜀")
                return False
            
            # 조건식으로 종목 검색
            logger.debug(f"🔍 [CONDITION_MONITOR] 키움 API로 종목 검색 시작 - 조건식 ID: {condition_id}")
            results = await self.kiwoom_api.search_condition_stocks(str(condition_id), condition_name)
            
            # API 호출 기록
            api_rate_limiter.record_api_call(f"search_condition_stocks_{condition_id}")
            
            if results:
                logger.info(f"🔍 [CONDITION_MONITOR] 종목 검색 완료 - {len(results)}개 종목 발견")

                # 너무 많은 종목이 한 번에 신호로 들어가 주문이 폭주하는 것을 방지
                max_signals = int(getattr(Config, "MAX_SIGNALS_PER_CONDITION_SCAN", 1))
                created = 0

                # condition_id는 PendingBuySignal에서 int 필드이므로 안전하게 캐스팅
                try:
                    condition_id_int = int(condition_id)
                except Exception:
                    condition_id_int = abs(hash(str(condition_id))) % 1000000

                for stock in results[:max_signals]:
                    stock_code = stock.get("stock_code") or ""
                    stock_name = stock.get("stock_name") or stock_code
                    if not stock_code:
                        continue

                    ok = await signal_manager.create_signal(
                        condition_id=condition_id_int,
                        stock_code=stock_code,
                        stock_name=stock_name,
                        signal_type=SignalType.CONDITION_SIGNAL,
                        additional_data={
                            # PendingBuySignal 모델에는 없어서 저장되진 않지만, 로깅/확장 대비
                            "current_price": stock.get("current_price"),
                            "change_rate": stock.get("change_rate"),
                            "volume": stock.get("volume"),
                        },
                    )
                    if ok:
                        created += 1

                logger.info(f"🔍 [CONDITION_MONITOR] 조건식 {condition_name} 신호 생성: {created}/{min(len(results), max_signals)}")
                logger.info(f"🔍 [CONDITION_MONITOR] 조건식 {condition_id} 모니터링 완료")
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

        # DB에 저장된 활성 조건식만 사용 (CNSRLST 목록 조회 생략 — API 부하 절감)
        enabled_rows: List[AutoTradeCondition] = []
        for db in get_db():
            session: Session = db
            enabled_rows = session.query(AutoTradeCondition).filter(
                AutoTradeCondition.is_enabled == True
            ).all()
            break

        if not enabled_rows:
            logger.info("🔍 [CONDITION_MONITOR] 활성화된 자동매매 조건이 없음 - 스캔 건너뜀")
            return

        logger.info(f"🔍 [CONDITION_MONITOR] 활성 조건식 {len(enabled_rows)}개 — 순차 검색 시작")

        for row in enabled_rows:
            condition_name = row.condition_name
            condition_api_id = row.api_condition_id
            if not condition_api_id:
                logger.warning(
                    f"🔍 [CONDITION_MONITOR] API ID 없음 — 스킵: {condition_name} "
                    "(대시보드 조건식 '새로고침'으로 목록을 한 번 불러오세요)"
                )
                continue
            logger.info(f"🔍 [CONDITION_MONITOR] 조건식 실행: {condition_name} (API ID: {condition_api_id})")
            await self.start_monitoring(condition_id=condition_api_id, condition_name=condition_name)

        logger.info("🔍 [CONDITION_MONITOR] 모든 조건식 1회 모니터링 완료")
        
        # 기준봉 전략 제거됨 - 현재 매매전략에 집중

    async def start_periodic_monitoring(self, force: bool = False):
        """활성 조건식을 주기적으로 검색 (백그라운드).
        force=False(기본): Config.CONDITION_MONITOR_AUTO_ENABLED=true 일 때만 시작.
        force=True: API /monitoring/start 등 수동 호출 시 강제 시작.
        """
        if not force and not Config.CONDITION_MONITOR_AUTO_ENABLED:
            logger.info(
                "🔍 [CONDITION_MONITOR] 조건식 주기 검색 비활성(기본) — "
                "수동 POST /monitoring/start 또는 CONDITION_MONITOR_AUTO_ENABLED=true 필요"
            )
            return
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
        
        # 프랙탈(Auto-trade scanner에서 'strategy': 'fractal'로 표시된 신호 통계)
        try:
            fractal_info = {"enabled": False, "watching": 0, "pending": 0}
            # AutoTradeSettings에서 사용 여부 조회
            from core.models import AutoTradeSettings, get_db, PendingBuySignal
            for db in get_db():
                session = db
                settings = session.query(AutoTradeSettings).first()
                if settings:
                    fractal_info["enabled"] = bool(getattr(settings, "use_fractal", False))
                # additional_data에 JSON 문자열 안에 "strategy":"fractal" 가 포함된 레코드 수 집계
                try:
                    fractal_info["watching"] = session.query(PendingBuySignal).filter(
                        PendingBuySignal.signal_type == "auto_trade",
                        PendingBuySignal.status == "WATCHING",
                        PendingBuySignal.additional_data.like('%"strategy":"fractal"%')
                    ).count()
                    fractal_info["pending"] = session.query(PendingBuySignal).filter(
                        PendingBuySignal.signal_type == "auto_trade",
                        PendingBuySignal.status == "PENDING",
                        PendingBuySignal.additional_data.like('%"strategy":"fractal"%')
                    ).count()
                except Exception:
                    # SQLite/DB 형식 차이로 like()가 실패하면 0으로 무시
                    pass
                break
            status["fractal"] = fractal_info
        except Exception:
            # 프랙탈 통계 집계가 실패해도 모니터링 전체에는 영향 없음
            pass
        
        logger.debug(f"🔍 [CONDITION_MONITOR] 모니터링 상태: {status}")
        return status



    # 기준봉 전략 제거됨 - 현재 매매전략에 집중



        return

# 전역 인스턴스
condition_monitor = ConditionMonitor()