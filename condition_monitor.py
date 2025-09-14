import logging
from datetime import datetime, timedelta
from typing import Dict, Set
# DB 관련 import
from kiwoom_api import KiwoomAPI
from models import PendingBuySignal, get_db, AutoTradeCondition
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)

class ConditionMonitor:
    """조건식 모니터링 시스템"""
    
    def __init__(self):
        self.kiwoom_api = KiwoomAPI()
        self.is_running = False
        self.loop_sleep_seconds = 600  # 10분 주기
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
                
                # 매수대기 테이블에 적재
                for db in get_db():
                    try:
                        pending = PendingBuySignal(
                            condition_id=condition_id,
                            stock_code=stock_code,
                            stock_name=stock_name,
                            status="PENDING",
                        )
                        db.add(pending)
                        db.commit()
                        logger.info(f"📝 [PENDING] 저장 완료 - {stock_name}({stock_code}), 조건식 {condition_id}")
                    except IntegrityError:
                        db.rollback()
                        logger.debug(f"🛑 [PENDING] 중복으로 저장 생략 - {stock_name}({stock_code}), 조건식 {condition_id}")
                    except Exception as ex:
                        db.rollback()
                        logger.error(f"❌ [PENDING] 저장 실패 - {stock_name}({stock_code}): {ex}")
                    finally:
                        pass
                
                # 여기에 추가적인 신호 처리 로직 (알림/웹소켓 등) 가능
                logger.debug(f"🔍 [CONDITION_MONITOR] 신호 처리 완료 - {stock_name}({stock_code})")
            else:
                logger.debug(f"🔍 [CONDITION_MONITOR] 중복 신호로 인해 처리 건너뜀 - {stock_name}({stock_code})")
                
        except Exception as e:
            logger.error(f"🔍 [CONDITION_MONITOR] 신호 처리 중 오류 - {stock_name}({stock_code}): {e}")
            import traceback
            logger.error(f"🔍 [CONDITION_MONITOR] 스택 트레이스: {traceback.format_exc()}")
    
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
                logger.info(f"🔍 [CONDITION_MONITOR] 비활성 조건식 스킵: {condition_name}")
                continue
            logger.info(f"🔍 [CONDITION_MONITOR] 조건식 실행: {condition_name} (API ID: {condition_api_id})")
            # 키움에서 제공한 실제 조건식 ID로 조회
            await self.start_monitoring(condition_id=condition_api_id, condition_name=condition_name)

        logger.info("🔍 [CONDITION_MONITOR] 모든 조건식 1회 모니터링 완료")

    async def start_periodic_monitoring(self):
        """모든 조건식을 주기적으로 모니터링 (10분 간격)"""
        logger.info("🔍 [CONDITION_MONITOR] 주기적 모니터링 시작 요청")
        if self.is_running:
            logger.info("🔍 [CONDITION_MONITOR] 이미 실행 중입니다")
            return
        self.is_running = True
        logger.info("🔍 [CONDITION_MONITOR] 모니터링 상태: RUNNING")
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
                import asyncio
                await asyncio.sleep(self.loop_sleep_seconds)
        finally:
            logger.info("🛑 [CONDITION_MONITOR] 주기적 모니터링 루프 종료")
    
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