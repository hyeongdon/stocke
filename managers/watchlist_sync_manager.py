import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Set, Optional
from sqlalchemy.orm import Session

from api.kiwoom_api import KiwoomAPI
from core.models import WatchlistStock, ConditionWatchlistSync, AutoTradeCondition, get_db
from api.api_rate_limiter import api_rate_limiter
from core.config import Config

logger = logging.getLogger(__name__)

class WatchlistSyncManager:
    """조건식 종목을 관심종목으로 동기화하는 관리자"""
    
    def __init__(self):
        self.kiwoom_api = KiwoomAPI()
        self.is_running = False
        self.sync_interval_seconds = 300  # 5분마다 동기화
        self._sync_task: Optional[asyncio.Task] = None
        self.start_time: Optional[datetime] = None  # 동기화 시작 시간
        self.last_sync_time: Optional[datetime] = None  # 마지막 동기화 시간
        
        # 동기화 설정
        self.auto_sync_enabled = True
        self.remove_expired_stocks = Config.WATCHLIST_SYNC_REMOVE_EXPIRED_STOCKS
        self.expired_threshold_hours = Config.WATCHLIST_SYNC_EXPIRED_THRESHOLD_HOURS
        
        # 특정 조건식만 동기화하는 설정
        self.target_condition_names = Config.WATCHLIST_SYNC_TARGET_CONDITION_NAMES  # 동기화할 조건식 이름들
        self.sync_only_target_conditions = Config.WATCHLIST_SYNC_ONLY_TARGET_CONDITIONS  # True면 target_condition_names만 동기화
        
    async def start_auto_sync(self):
        """자동 동기화 시작"""
        logger.info("📋 [WATCHLIST_SYNC] 자동 동기화 시작")
        if self.is_running:
            logger.info("📋 [WATCHLIST_SYNC] 이미 실행 중입니다")
            return
        
        self.is_running = True
        self.start_time = datetime.now()  # 시작 시간 기록
        self._sync_task = asyncio.create_task(self._sync_loop())
        logger.info("📋 [WATCHLIST_SYNC] 자동 동기화 루프 시작")
    
    async def stop_auto_sync(self):
        """자동 동기화 중지"""
        logger.info("📋 [WATCHLIST_SYNC] 자동 동기화 중지 요청")
        self.is_running = False
        self.start_time = None  # 시작 시간 초기화
        
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
            finally:
                self._sync_task = None
        
        logger.info("📋 [WATCHLIST_SYNC] 자동 동기화 중지 완료")
    
    async def _sync_loop(self):
        """동기화 루프"""
        try:
            while self.is_running:
                logger.info("📋 [WATCHLIST_SYNC] 동기화 시작")
                try:
                    await self.sync_all_conditions()
                except Exception as e:
                    logger.error(f"📋 [WATCHLIST_SYNC] 동기화 중 오류: {e}")
                
                logger.info(f"📋 [WATCHLIST_SYNC] 다음 동기화까지 대기 {self.sync_interval_seconds}초")
                await asyncio.sleep(self.sync_interval_seconds)
        finally:
            logger.info("📋 [WATCHLIST_SYNC] 동기화 루프 종료")
    
    async def sync_all_conditions(self):
        """모든 활성 조건식에 대해 동기화 수행"""
        try:
            # 활성화된 조건식 목록 조회
            active_conditions = await self._get_active_conditions()
            if not active_conditions:
                logger.info("📋 [WATCHLIST_SYNC] 활성화된 조건식이 없음")
                return
            
            logger.info(f"📋 [WATCHLIST_SYNC] 활성 조건식 {len(active_conditions)}개 동기화 시작")
            
            for condition in active_conditions:
                try:
                    await self.sync_condition_stocks(condition["condition_id"], condition["condition_name"])
                except Exception as e:
                    logger.error(f"📋 [WATCHLIST_SYNC] 조건식 {condition['condition_name']} 동기화 실패: {e}")
                
                # API 제한을 고려한 대기
                await asyncio.sleep(1)
            
            # 만료된 종목들 정리
            if self.remove_expired_stocks:
                await self._cleanup_expired_stocks()
            
            logger.info("📋 [WATCHLIST_SYNC] 모든 조건식 동기화 완료")
            
            # 마지막 동기화 시간 업데이트
            self.last_sync_time = datetime.now()
            logger.info(f"📋 [WATCHLIST_SYNC] 마지막 동기화 시간 업데이트: {self.last_sync_time}")
            
        except Exception as e:
            logger.error(f"📋 [WATCHLIST_SYNC] 전체 동기화 중 오류: {e}")
    
    async def sync_condition_stocks(self, condition_id: int, condition_name: str):
        """특정 조건식의 종목들을 관심종목으로 동기화"""
        try:
            logger.info(f"📋 [WATCHLIST_SYNC] 조건식 동기화 시작: {condition_name} (ID: {condition_id})")
            
            # API 제한 확인
            if not api_rate_limiter.is_api_available():
                logger.warning(f"📋 [WATCHLIST_SYNC] API 제한 상태 - 조건식 {condition_name} 동기화 건너뜀")
                return
            
            # 조건식으로 종목 검색
            stocks = await self.kiwoom_api.search_condition_stocks(str(condition_id), condition_name)
            api_rate_limiter.record_api_call(f"sync_condition_{condition_id}")
            
            if not stocks:
                logger.info(f"📋 [WATCHLIST_SYNC] 조건식 {condition_name}에 해당하는 종목이 없음")
                await self._mark_condition_stocks_as_removed(condition_id)
                return
            
            logger.info(f"📋 [WATCHLIST_SYNC] 조건식 {condition_name}에서 {len(stocks)}개 종목 발견")
            
            # 현재 조건식의 종목 코드들
            current_stock_codes = {stock["stock_code"] for stock in stocks}
            
            # 기존 동기화 데이터 업데이트/추가
            await self._update_condition_sync_data(condition_id, condition_name, stocks)
            
            # 관심종목에 추가/업데이트
            await self._sync_to_watchlist(condition_id, condition_name, stocks)
            
            # 조건식에서 제거된 종목들 처리
            await self._handle_removed_stocks(condition_id, current_stock_codes)
            
            logger.info(f"📋 [WATCHLIST_SYNC] 조건식 {condition_name} 동기화 완료")
            
        except Exception as e:
            logger.error(f"📋 [WATCHLIST_SYNC] 조건식 {condition_name} 동기화 중 오류: {e}")
    
    async def _get_active_conditions(self) -> List[Dict]:
        """활성화된 조건식 목록 조회"""
        conditions = []
        for db in get_db():
            session: Session = db
            try:
                rows = session.query(AutoTradeCondition).filter(
                    AutoTradeCondition.is_enabled == True
                ).all()
                
                for row in rows:
                    # 특정 조건식만 동기화하는 경우 필터링
                    if self.sync_only_target_conditions:
                        if row.condition_name not in self.target_condition_names:
                            logger.info(f"📋 [WATCHLIST_SYNC] 대상 조건식이 아니므로 스킵: {row.condition_name}")
                            continue

                    api_condition_id = row.api_condition_id
                    if not api_condition_id:
                        logger.warning(
                            f"📋 [WATCHLIST_SYNC] API ID 없음 — 스킵: {row.condition_name} "
                            "(조건식 목록을 수동으로 한 번 불러와 주세요)"
                        )
                        continue

                    try:
                        condition_id = int(api_condition_id)
                    except (TypeError, ValueError):
                        condition_id = api_condition_id

                    conditions.append({
                        "condition_id": condition_id,
                        "condition_name": row.condition_name,
                    })
                    logger.info(f"📋 [WATCHLIST_SYNC] 활성 조건식: {row.condition_name} (API ID: {api_condition_id})")
                
                break
            except Exception as e:
                logger.error(f"📋 [WATCHLIST_SYNC] 활성 조건식 조회 오류: {e}")
                continue
        
        logger.info(f"📋 [WATCHLIST_SYNC] 동기화 대상 조건식: {len(conditions)}개")
        for cond in conditions:
            logger.info(f"📋 [WATCHLIST_SYNC]   - {cond['condition_name']} (API ID: {cond['condition_id']})")
        
        return conditions
    
    async def _update_condition_sync_data(self, condition_id: int, condition_name: str, stocks: List[Dict]):
        """조건식 동기화 데이터 업데이트"""
        try:
            for db in get_db():
                session: Session = db
                try:
                    for stock in stocks:
                        stock_code = stock["stock_code"]
                        stock_name = stock["stock_name"]
                        
                        # 기존 동기화 데이터 확인
                        sync_record = session.query(ConditionWatchlistSync).filter(
                            ConditionWatchlistSync.condition_id == condition_id,
                            ConditionWatchlistSync.stock_code == stock_code
                        ).first()
                        
                        if sync_record:
                            # 기존 데이터 업데이트
                            sync_record.stock_name = stock_name
                            sync_record.sync_status = "ACTIVE"
                            sync_record.last_sync_at = datetime.utcnow()
                            sync_record.current_price = int(stock.get("current_price", 0))
                            sync_record.change_rate = float(stock.get("change_rate", 0))
                            sync_record.volume = int(stock.get("volume", 0))
                        else:
                            # 새 데이터 생성
                            sync_record = ConditionWatchlistSync(
                                condition_id=condition_id,
                                condition_name=condition_name,
                                stock_code=stock_code,
                                stock_name=stock_name,
                                sync_status="ACTIVE",
                                last_sync_at=datetime.utcnow(),
                                current_price=int(stock.get("current_price", 0)),
                                change_rate=float(stock.get("change_rate", 0)),
                                volume=int(stock.get("volume", 0))
                            )
                            session.add(sync_record)
                    
                    session.commit()
                    break
                except Exception as e:
                    logger.error(f"📋 [WATCHLIST_SYNC] 동기화 데이터 업데이트 오류: {e}")
                    session.rollback()
                    continue
        except Exception as e:
            logger.error(f"📋 [WATCHLIST_SYNC] 동기화 데이터 업데이트 중 오류: {e}")
    
    async def _sync_to_watchlist(self, condition_id: int, condition_name: str, stocks: List[Dict]):
        """조건식 종목들을 관심종목에 추가/업데이트"""
        try:
            for db in get_db():
                session: Session = db
                try:
                    for stock in stocks:
                        stock_code = stock["stock_code"]
                        stock_name = stock["stock_name"]
                        
                        # 기존 관심종목 확인
                        watchlist_item = session.query(WatchlistStock).filter(
                            WatchlistStock.stock_code == stock_code
                        ).first()
                        
                        if watchlist_item:
                            # 기존 종목 업데이트 (조건식 종목으로 변경)
                            if watchlist_item.source_type != "CONDITION":
                                logger.info(f"📋 [WATCHLIST_SYNC] 기존 수기등록 종목을 조건식 종목으로 변경: {stock_name}")
                            
                            watchlist_item.source_type = "CONDITION"
                            watchlist_item.condition_id = condition_id
                            watchlist_item.condition_name = condition_name
                            watchlist_item.last_condition_check = datetime.utcnow()
                            watchlist_item.condition_status = "ACTIVE"
                            watchlist_item.is_active = True
                        else:
                            # 새 종목 추가
                            watchlist_item = WatchlistStock(
                                stock_code=stock_code,
                                stock_name=stock_name,
                                source_type="CONDITION",
                                condition_id=condition_id,
                                condition_name=condition_name,
                                last_condition_check=datetime.utcnow(),
                                condition_status="ACTIVE",
                                is_active=True
                            )
                            session.add(watchlist_item)
                            logger.info(f"📋 [WATCHLIST_SYNC] 새 조건식 종목 추가: {stock_name}")
                    
                    session.commit()
                    break
                except Exception as e:
                    logger.error(f"📋 [WATCHLIST_SYNC] 관심종목 동기화 오류: {e}")
                    session.rollback()
                    continue
        except Exception as e:
            logger.error(f"📋 [WATCHLIST_SYNC] 관심종목 동기화 중 오류: {e}")
    
    async def _handle_removed_stocks(self, condition_id: int, current_stock_codes: Set[str]):
        """조건식에서 제거된 종목들 처리"""
        try:
            for db in get_db():
                session: Session = db
                try:
                    # 해당 조건식의 기존 동기화 데이터 조회
                    existing_syncs = session.query(ConditionWatchlistSync).filter(
                        ConditionWatchlistSync.condition_id == condition_id,
                        ConditionWatchlistSync.sync_status == "ACTIVE"
                    ).all()
                    
                    for sync_record in existing_syncs:
                        if sync_record.stock_code not in current_stock_codes:
                            # 조건식에서 제거된 종목
                            sync_record.sync_status = "REMOVED"
                            sync_record.last_sync_at = datetime.utcnow()
                            
                            # 관심종목에서도 제거 (조건식 종목인 경우만)
                            watchlist_item = session.query(WatchlistStock).filter(
                                WatchlistStock.stock_code == sync_record.stock_code,
                                WatchlistStock.source_type == "CONDITION",
                                WatchlistStock.condition_id == condition_id
                            ).first()
                            
                            if watchlist_item:
                                watchlist_item.condition_status = "REMOVED"
                                watchlist_item.is_active = False
                                logger.info(f"📋 [WATCHLIST_SYNC] 조건식에서 제거된 종목 비활성화: {sync_record.stock_name}")
                    
                    session.commit()
                    break
                except Exception as e:
                    logger.error(f"📋 [WATCHLIST_SYNC] 제거된 종목 처리 오류: {e}")
                    session.rollback()
                    continue
        except Exception as e:
            logger.error(f"📋 [WATCHLIST_SYNC] 제거된 종목 처리 중 오류: {e}")
    
    async def _mark_condition_stocks_as_removed(self, condition_id: int):
        """조건식의 모든 종목을 제거됨으로 표시"""
        try:
            for db in get_db():
                session: Session = db
                try:
                    # 동기화 데이터 업데이트
                    session.query(ConditionWatchlistSync).filter(
                        ConditionWatchlistSync.condition_id == condition_id,
                        ConditionWatchlistSync.sync_status == "ACTIVE"
                    ).update({
                        "sync_status": "REMOVED",
                        "last_sync_at": datetime.utcnow()
                    })
                    
                    # 관심종목 업데이트
                    session.query(WatchlistStock).filter(
                        WatchlistStock.condition_id == condition_id,
                        WatchlistStock.source_type == "CONDITION"
                    ).update({
                        "condition_status": "REMOVED",
                        "is_active": False
                    })
                    
                    session.commit()
                    break
                except Exception as e:
                    logger.error(f"📋 [WATCHLIST_SYNC] 조건식 종목 제거 표시 오류: {e}")
                    session.rollback()
                    continue
        except Exception as e:
            logger.error(f"📋 [WATCHLIST_SYNC] 조건식 종목 제거 표시 중 오류: {e}")
    
    async def _cleanup_expired_stocks(self):
        """만료된 종목들 정리 (개선된 로직)"""
        try:
            current_time = datetime.utcnow()
            threshold_time = current_time - timedelta(hours=self.expired_threshold_hours)
            
            # 일일 정리: 자정 이후 1시간이 지나면 이전 날의 종목들 정리
            daily_cleanup_time = current_time.replace(hour=1, minute=0, second=0, microsecond=0)
            if current_time > daily_cleanup_time:
                yesterday_threshold = current_time - timedelta(days=1)
            else:
                yesterday_threshold = current_time - timedelta(days=2)
            
            for db in get_db():
                session: Session = db
                try:
                    removed_count = 0
                    
                    # 1. REMOVED 상태인 오래된 동기화 데이터 정리
                    expired_syncs = session.query(ConditionWatchlistSync).filter(
                        ConditionWatchlistSync.sync_status == "REMOVED",
                        ConditionWatchlistSync.last_sync_at < threshold_time
                    ).all()
                    
                    for sync_record in expired_syncs:
                        # 관심종목에서 완전 제거 (조건식 종목인 경우만)
                        watchlist_item = session.query(WatchlistStock).filter(
                            WatchlistStock.stock_code == sync_record.stock_code,
                            WatchlistStock.source_type == "CONDITION",
                            WatchlistStock.condition_id == sync_record.condition_id
                        ).first()
                        
                        if watchlist_item:
                            session.delete(watchlist_item)
                            removed_count += 1
                            logger.info(f"📋 [WATCHLIST_SYNC] 만료된 조건식 종목 완전 제거: {sync_record.stock_name}")
                    
                    # 2. 일일 정리: 이전 날의 모든 조건식 종목들 정리
                    old_condition_stocks = session.query(WatchlistStock).filter(
                        WatchlistStock.source_type == "CONDITION",
                        WatchlistStock.last_condition_check < yesterday_threshold
                    ).all()
                    
                    for stock in old_condition_stocks:
                        session.delete(stock)
                        removed_count += 1
                        logger.info(f"📋 [WATCHLIST_SYNC] 일일 정리로 제거된 종목: {stock.stock_name}")
                    
                    # 3. 동기화 데이터도 정리
                    session.query(ConditionWatchlistSync).filter(
                        ConditionWatchlistSync.sync_status == "REMOVED",
                        ConditionWatchlistSync.last_sync_at < threshold_time
                    ).delete()
                    
                    # 4. 오래된 동기화 데이터도 정리 (2일 이상 된 데이터)
                    session.query(ConditionWatchlistSync).filter(
                        ConditionWatchlistSync.last_sync_at < yesterday_threshold
                    ).delete()
                    
                    session.commit()
                    
                    if removed_count > 0:
                        logger.info(f"📋 [WATCHLIST_SYNC] 총 {removed_count}개의 만료된 종목 정리 완료")
                    
                    break
                except Exception as e:
                    logger.error(f"📋 [WATCHLIST_SYNC] 만료된 종목 정리 오류: {e}")
                    session.rollback()
                    continue
        except Exception as e:
            logger.error(f"📋 [WATCHLIST_SYNC] 만료된 종목 정리 중 오류: {e}")
    
    async def get_sync_status(self) -> Dict:
        """동기화 상태 조회"""
        try:
            # 실행시간 계산
            running_time_minutes = 0
            if self.is_running and self.start_time:
                # start_time이 datetime 객체인지 확인하고 계산
                if hasattr(self.start_time, 'isoformat'):
                    # datetime 객체인 경우
                    running_time = datetime.now() - self.start_time
                else:
                    # 문자열인 경우 datetime으로 변환
                    try:
                        start_time_obj = datetime.fromisoformat(str(self.start_time).replace('Z', '+00:00'))
                        running_time = datetime.now() - start_time_obj
                    except Exception as e:
                        logger.error(f"📊 [WATCHLIST_SYNC] 시작시간 변환 오류: {e}")
                        running_time = datetime.now() - datetime.now()  # 0초로 설정
                
                running_time_minutes = int(running_time.total_seconds() / 60)
                logger.info(f"📊 [WATCHLIST_SYNC] 실행시간 계산: {running_time_minutes}분 (시작시간: {self.start_time}, 타입: {type(self.start_time)})")
            else:
                logger.info(f"📊 [WATCHLIST_SYNC] 실행시간: 0분 (is_running: {self.is_running}, start_time: {self.start_time})")
            
            stats = {
                "is_running": self.is_running,
                "running_time_minutes": running_time_minutes,
                "start_time": self.start_time.isoformat() if self.start_time else None,
                "last_sync_time": self.last_sync_time.isoformat() if self.last_sync_time else None,
                "sync_interval_seconds": self.sync_interval_seconds,
                "auto_sync_enabled": self.auto_sync_enabled,
                "remove_expired_stocks": self.remove_expired_stocks,
                "expired_threshold_hours": self.expired_threshold_hours,
                "total_watchlist_stocks": 0,
                "manual_stocks": 0,
                "condition_stocks": 0,
                "active_conditions": 0
            }
            
            for db in get_db():
                session: Session = db
                try:
                    # 관심종목 통계
                    total_stocks = session.query(WatchlistStock).filter(
                        WatchlistStock.is_active == True
                    ).count()
                    
                    manual_stocks = session.query(WatchlistStock).filter(
                        WatchlistStock.is_active == True,
                        WatchlistStock.source_type == "MANUAL"
                    ).count()
                    
                    condition_stocks = session.query(WatchlistStock).filter(
                        WatchlistStock.is_active == True,
                        WatchlistStock.source_type == "CONDITION"
                    ).count()
                    
                    # 활성 조건식 수
                    active_conditions = session.query(AutoTradeCondition).filter(
                        AutoTradeCondition.is_enabled == True
                    ).count()
                    
                    stats.update({
                        "total_watchlist_stocks": total_stocks,
                        "manual_stocks": manual_stocks,
                        "condition_stocks": condition_stocks,
                        "active_conditions": active_conditions
                    })
                    break
                except Exception as e:
                    logger.error(f"📋 [WATCHLIST_SYNC] 상태 조회 오류: {e}")
                    continue
            
            return stats
            
        except Exception as e:
            logger.error(f"📋 [WATCHLIST_SYNC] 상태 조회 중 오류: {e}")
            return {"error": str(e)}

# 전역 인스턴스
watchlist_sync_manager = WatchlistSyncManager()