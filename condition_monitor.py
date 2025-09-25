import logging
import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, Set, List, Optional
import pandas as pd
# DB 관련 import
from kiwoom_api import KiwoomAPI
from models import PendingBuySignal, get_db, AutoTradeCondition
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

# 개선된 모듈들 import
from signal_manager import signal_manager, SignalType, SignalStatus
from api_rate_limiter import api_rate_limiter
from buy_order_executor import buy_order_executor

logger = logging.getLogger(__name__)

class ConditionMonitor:
    """조건식 모니터링 시스템"""
    
    def __init__(self):
        self.kiwoom_api = KiwoomAPI()
        self.is_running = False
        self.loop_sleep_seconds = 600  # 10분 주기
        self._monitor_task: Optional[asyncio.Task] = None
        
        # 조건식별 기준봉 전략 관련 속성
        self.condition_reference_candles: Dict[int, Dict[str, Dict]] = {}  # {condition_id: {stock_code: candle_data}}
        self.condition_strategies: Dict[int, Dict] = {}  # 조건식별 전략 설정
        self._last_condition_candle_check: Dict[int, float] = {}  # 조건식별 마지막 기준봉 확인 시간
    
    async def start_monitoring(self, condition_id: int, condition_name: str) -> bool:
        """조건식 모니터링 시작"""
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
                
                # 조건식별 기준봉 전략 적용
                await self._apply_condition_reference_strategy(condition_id, condition_name, results)
                
                # 조건 만족 종목들에 대해 신호 처리
                for i, stock_data in enumerate(results, 1):
                    logger.debug(f"🔍 [CONDITION_MONITOR] 신호 처리 중 ({i}/{len(results)}) - {stock_data.get('stock_name', 'Unknown')}")
                    await self._process_signal(condition_id, stock_data)
                
                logger.info(f"🔍 [CONDITION_MONITOR] 조건식 {condition_id} 모니터링 완료 - {len(results)}개 종목 처리됨")
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
        """신호 처리 (개선된 신호 관리 시스템 사용)"""
        stock_code = stock_data.get("stock_code", "Unknown")
        stock_name = stock_data.get("stock_name", "Unknown")
        
        logger.debug(f"🔍 [CONDITION_MONITOR] 신호 처리 시작 - {stock_name}({stock_code})")
        
        try:
            # 개선된 신호 관리 시스템을 사용하여 신호 생성
            success = await signal_manager.create_signal(
                condition_id=condition_id,
                stock_code=stock_code,
                stock_name=stock_name,
                signal_type=SignalType.CONDITION_SIGNAL,
                additional_data={
                    "detected_price": stock_data.get("current_price", 0),
                    "detected_volume": stock_data.get("volume", 0)
                }
            )
            
            if success:
                logger.info(f"🔍 [CONDITION_MONITOR] 조건 만족 신호 생성 완료: {stock_name}({stock_code}) - 조건식 ID: {condition_id}")
            else:
                logger.debug(f"🔍 [CONDITION_MONITOR] 신호 생성 건너뜀 (중복 또는 기타 이유): {stock_name}({stock_code})")
                
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
        
        # 조건식별 기준봉 하락 확인 (5분마다 실행)
        import time
        current_time = time.time()
        last_condition_check = getattr(self, '_last_condition_check', 0)
        
        if current_time - last_condition_check > 300:  # 5분 (300초)
            for condition_id in self.condition_reference_candles.keys():
                await self._check_condition_reference_drops(condition_id)
            self._last_condition_check = current_time
        else:
            logger.debug("🔍 [CONDITION_REF] API 제한을 고려하여 조건식 기준봉 확인 건너뜀")

    async def start_periodic_monitoring(self):
        """모든 조건식을 주기적으로 모니터링 (백그라운드 태스크로 실행)"""
        logger.info("🔍 [CONDITION_MONITOR] 주기적 모니터링 시작 요청")
        if self.is_running:
            logger.info("🔍 [CONDITION_MONITOR] 이미 실행 중입니다")
            return
        self.is_running = True
        logger.info("🔍 [CONDITION_MONITOR] 모니터링 상태: RUNNING")
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
        logger.info("🔍 [CONDITION_MONITOR] 모니터링 상태: STOPPED")
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
        
        status = {
            "is_running": self.is_running,
            "loop_sleep_seconds": self.loop_sleep_seconds,
            "signal_statistics": signal_stats,
            "api_status": api_status,
            "reference_candles_count": sum(len(candles) for candles in self.condition_reference_candles.values()),
            "active_strategies": len(self.condition_strategies)
        }
        
        logger.debug(f"🔍 [CONDITION_MONITOR] 모니터링 상태: {status}")
        return status



    async def _apply_condition_reference_strategy(self, condition_id: int, condition_name: str, stocks: List[Dict]):
        """조건식별 기준봉 전략 적용"""
        try:
            logger.info(f"🔍 [CONDITION_REF] 조건식 {condition_name} 기준봉 전략 시작 - {len(stocks)}개 종목")
            
            # 조건식별 전략 설정 초기화 (없는 경우)
            if condition_id not in self.condition_strategies:
                self.condition_strategies[condition_id] = {
                    "volume_threshold": 2.0,  # 평균 거래량의 2배 이상
                    "gain_threshold": 3.0,    # 3% 이상 상승
                    "target_drop_rate": 0.3,  # 30% 하락 시 매수
                    "lookback_days": 15,      # 15일 평균 거래량
                    "max_candle_age_days": 20 # 기준봉 최대 유효 기간
                }
            
            # 조건식별 기준봉 저장소 초기화
            if condition_id not in self.condition_reference_candles:
                self.condition_reference_candles[condition_id] = {}
            
            # 각 종목에 대해 기준봉 찾기 (API 제한 고려하지 않고 진행)
            for i, stock in enumerate(stocks):
                try:
                    stock_code = stock.get('stock_code', '')
                    stock_name = stock.get('stock_name', '')
                    
                    if not stock_code:
                        continue
                    
                    logger.debug(f"🔍 [CONDITION_REF] 종목 기준봉 분석: {stock_name}({stock_code})")
                    await self._find_condition_reference_candle(condition_id, stock_code, stock_name)
                    
                    # API 호출 간격 조절 (0.5초 대기)
                    if i < len(stocks) - 1:
                        import asyncio
                        await asyncio.sleep(0.5)
                        
                except Exception as stock_error:
                    logger.error(f"🔍 [CONDITION_REF] 종목 {stock.get('stock_code', '')} 처리 중 오류: {stock_error}")
                    continue
            
            logger.info(f"🔍 [CONDITION_REF] 조건식 {condition_name} 기준봉 전략 완료")
            
        except Exception as e:
            logger.error(f"🔍 [CONDITION_REF] 조건식 기준봉 전략 오류: {e}")
            import traceback
            logger.error(f"🔍 [CONDITION_REF] 스택 트레이스: {traceback.format_exc()}")

    async def _find_condition_reference_candle(self, condition_id: int, stock_code: str, stock_name: str):
        """조건식별 기준봉 찾기"""
        try:
            # 차트 데이터 조회
            chart_data = await self.kiwoom_api.get_stock_chart_data(stock_code, "1D")
            
            if not chart_data or len(chart_data) < 15:
                logger.debug(f"🔍 [CONDITION_REF] 차트 데이터 부족: {stock_name}({stock_code})")
                return
            
            # DataFrame으로 변환
            df = pd.DataFrame(chart_data)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df.sort_values('timestamp')
            
            strategy = self.condition_strategies[condition_id]
            lookback_days = strategy['lookback_days']
            volume_threshold = strategy['volume_threshold']
            gain_threshold = strategy['gain_threshold']
            
            # 최근 데이터에서 기준봉 찾기
            for i in range(len(df) - 1, max(0, len(df) - 30), -1):  # 최근 30일 내에서만
                row = df.iloc[i]
                
                # 1. 거래량 스파이크 확인
                recent_volume = df.iloc[max(0, i-lookback_days):i+1]['volume']
                avg_volume = recent_volume.mean()
                volume_ratio = row['volume'] / avg_volume if avg_volume > 0 else 0
                
                # 2. 상승률 확인
                prev_close = df.iloc[i-1]['close'] if i > 0 else row['open']
                change_rate = ((row['close'] - prev_close) / prev_close) * 100 if prev_close > 0 else 0
                
                # 3. 조건 확인
                is_volume_spike = volume_ratio >= volume_threshold
                is_high_gain = change_rate >= gain_threshold
                
                if is_volume_spike and is_high_gain:
                    # 기준봉 발견
                    candle_data = {
                        "stock_code": stock_code,
                        "stock_name": stock_name,
                        "timestamp": row['timestamp'],
                        "open_price": int(row['open']),
                        "high_price": int(row['high']),
                        "low_price": int(row['low']),
                        "close_price": int(row['close']),
                        "volume": int(row['volume']),
                        "change_rate": change_rate,
                        "volume_ratio": volume_ratio,
                        "is_volume_spike": is_volume_spike,
                        "is_high_gain": is_high_gain,
                        "strategy": strategy
                    }
                    
                    # 조건식별 기준봉 저장
                    self.condition_reference_candles[condition_id][stock_code] = candle_data
                    
                    logger.info(f"🔍 [CONDITION_REF] 기준봉 발견: {stock_name}({stock_code}) - "
                              f"{row['timestamp'].strftime('%Y-%m-%d')} "
                              f"거래량비율: {volume_ratio:.2f}, 상승률: {change_rate:.2f}%")
                    break
            
        except Exception as e:
            logger.error(f"🔍 [CONDITION_REF] 기준봉 찾기 오류 {stock_code}: {e}")
            # API 제한 오류인 경우 더 긴 대기 시간 설정
            if "허용된 요청 개수를 초과" in str(e) or "429" in str(e) or "API 제한" in str(e):
                logger.warning(f"🔍 [CONDITION_REF] API 제한 감지 - 조건식 기준봉 전략 일시 중단")
                self._last_condition_check = time.time() + 1800  # 30분 후 재시도

    async def _check_condition_reference_drops(self, condition_id: int):
        """조건식별 기준봉 하락 확인 및 매수 신호 생성"""
        try:
            if condition_id not in self.condition_reference_candles:
                return
            
            strategy = self.condition_strategies.get(condition_id, {})
            target_drop_rate = strategy.get('target_drop_rate', 0.3)
            max_candle_age_days = strategy.get('max_candle_age_days', 20)
            
            current_time = datetime.now()
            candles_to_remove = []
            
            for stock_code, candle in self.condition_reference_candles[condition_id].items():
                try:
                    # 기준봉이 너무 오래된 경우 제거
                    if (current_time - candle['timestamp']).days > max_candle_age_days:
                        candles_to_remove.append(stock_code)
                        logger.info(f"🔍 [CONDITION_REF] 오래된 기준봉 제거: {candle['stock_name']}({stock_code})")
                        continue
                    
                    # 현재가 조회
                    chart_data = await self.kiwoom_api.get_stock_chart_data(stock_code, "1D")
                    if not chart_data or len(chart_data) == 0:
                        continue
                    
                    current_price = int(chart_data[-1].get('close', 0))
                    target_price = int(candle['close_price'] * (1 - target_drop_rate))
                    
                    if current_price <= target_price:
                        # 목표 하락 달성 - 매수 신호 생성
                        await self._create_condition_reference_buy_signal(
                            condition_id, stock_code, candle['stock_name'], 
                            current_price, target_price, candle
                        )
                        candles_to_remove.append(stock_code)
                    else:
                        logger.debug(f"🔍 [CONDITION_REF] 아직 하락 미달성: {candle['stock_name']}({stock_code}) - "
                                   f"현재가: {current_price}, 목표가: {target_price}")
                
                except Exception as stock_error:
                    logger.error(f"🔍 [CONDITION_REF] 종목 {stock_code} 확인 중 오류: {stock_error}")
                    continue
            
            # 처리된 기준봉들 제거
            for stock_code in candles_to_remove:
                if stock_code in self.condition_reference_candles[condition_id]:
                    del self.condition_reference_candles[condition_id][stock_code]
            
        except Exception as e:
            logger.error(f"🔍 [CONDITION_REF] 기준봉 하락 확인 오류: {e}")
            # API 제한 오류인 경우 더 긴 대기 시간 설정
            if "허용된 요청 개수를 초과" in str(e) or "429" in str(e) or "API 제한" in str(e):
                logger.warning(f"🔍 [CONDITION_REF] API 제한 감지 - 조건식 기준봉 하락 확인 일시 중단")
                self._last_condition_check = time.time() + 1800  # 30분 후 재시도

    async def _create_condition_reference_buy_signal(self, condition_id: int, stock_code: str, stock_name: str, 
                                                   current_price: int, target_price: int, candle: Dict):
        """조건식 기준봉 전략 매수 신호 생성 (개선된 신호 관리 시스템 사용)"""
        try:
            # 개선된 신호 관리 시스템을 사용하여 신호 생성
            success = await signal_manager.create_signal(
                condition_id=condition_id,
                stock_code=stock_code,
                stock_name=stock_name,
                signal_type=SignalType.REFERENCE_CANDLE,
                additional_data={
                    "detected_price": current_price,
                    "target_price": target_price,
                    "reference_candle_high": candle['high_price'],
                    "reference_candle_date": candle['timestamp'],
                    "reference_candle_close": candle['close_price'],
                    "strategy": "reference_candle_drop"
                }
            )
            
            if success:
                logger.info(f"🔍 [CONDITION_REF] 기준봉 매수 신호 생성 완료: {stock_name}({stock_code}) - "
                          f"현재가: {current_price}, 목표가: {target_price}, "
                          f"기준봉: {candle['timestamp'].strftime('%Y-%m-%d')} "
                          f"({candle['close_price']}원)")
            else:
                logger.debug(f"🔍 [CONDITION_REF] 기준봉 신호 생성 건너뜀 (중복 또는 기타 이유): {stock_code}")
                
        except Exception as e:
            logger.error(f"🔍 [CONDITION_REF] 기준봉 매수 신호 생성 오류 {stock_code}: {e}")

# 전역 인스턴스
condition_monitor = ConditionMonitor()