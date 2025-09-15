import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Set, List, Optional
import pandas as pd
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
        
        
        # 조건식별 기준봉 전략 관련 속성
        self.condition_reference_candles: Dict[int, Dict[str, Dict]] = {}  # {condition_id: {stock_code: candle_data}}
        self.condition_strategies: Dict[int, Dict] = {}  # 조건식별 전략 설정
        self._last_condition_candle_check: Dict[int, float] = {}  # 조건식별 마지막 기준봉 확인 시간
    
    async def start_monitoring(self, condition_id: int, condition_name: str) -> bool:
        """조건식 모니터링 시작"""
        logger.info(f"🔍 [CONDITION_MONITOR] 조건식 모니터링 시작 요청 - ID: {condition_id}, 이름: {condition_name}")
        try:
            # 조건식으로 종목 검색
            logger.debug(f"🔍 [CONDITION_MONITOR] 키움 API로 종목 검색 시작 - 조건식 ID: {condition_id}")
            results = await self.kiwoom_api.search_condition_stocks(str(condition_id), condition_name)
            
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


    async def _update_signal_status(self, signal_id: int, status: str, order_id: str = ""):
        """매수 신호 상태 업데이트"""
        try:
            for db in get_db():
                session: Session = db
                signal = session.query(PendingBuySignal).filter(PendingBuySignal.id == signal_id).first()
                if signal:
                    signal.status = status
                    if order_id:
                        # 주문 ID를 저장할 필드가 있다면 여기에 추가
                        pass
                    session.commit()
                    logger.info(f"🔍 [SIGNAL_UPDATE] 신호 상태 변경: ID {signal_id} -> {status}")
                break
        except Exception as e:
            logger.error(f"🔍 [SIGNAL_UPDATE] 신호 상태 업데이트 오류: {e}")

    async def _execute_buy_order(self, stock_code: str, stock_name: str, current_price: int, signal_id: int):
        """실제 매수 주문 실행"""
        try:
            # 매수 수량 계산 (예: 10만원 상당)
            max_invest_amount = 100000  # 10만원
            quantity = max_invest_amount // current_price
            
            if quantity < 1:
                logger.warning(f"🔍 [BUY_ORDER] 매수 수량 부족: {stock_name}({stock_code}) - 수량: {quantity}")
                return
            
            logger.info(f"🔍 [BUY_ORDER] 매수 주문 실행: {stock_name}({stock_code}) - 수량: {quantity}, 가격: {current_price}")
            
            # 키움 API로 매수 주문
            result = await self.kiwoom_api.place_buy_order(
                stock_code=stock_code,
                quantity=quantity,
                price=0,  # 시장가
                order_type="3"  # 시장가 (kt10000 스펙)
            )
            
            if result.get("success"):
                logger.info(f"🔍 [BUY_ORDER] 매수 주문 성공: {stock_name}({stock_code}) - 주문ID: {result.get('order_id')}")
                
                # 매수 신호 상태를 ORDERED로 변경
                await self._update_signal_status(signal_id, "ORDERED", result.get("order_id", ""))
            else:
                logger.error(f"🔍 [BUY_ORDER] 매수 주문 실패: {stock_name}({stock_code}) - 오류: {result.get('error')}")
                
                # 매수 신호 상태를 FAILED로 변경
                await self._update_signal_status(signal_id, "FAILED", result.get("error", ""))
                
        except Exception as e:
            logger.error(f"🔍 [BUY_ORDER] 매수 주문 실행 오류 {stock_code}: {e}")
            # 매수 신호 상태를 FAILED로 변경
            await self._update_signal_status(signal_id, "FAILED", str(e))

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
        """조건식 기준봉 전략 매수 신호 생성"""
        try:
            # 매수 신호를 매수대기 테이블에 추가
            for db in get_db():
                session: Session = db
                
                # 중복 신호 확인
                existing = session.query(PendingBuySignal).filter(
                    PendingBuySignal.stock_code == stock_code,
                    PendingBuySignal.status == "PENDING",
                    PendingBuySignal.condition_id == condition_id
                ).first()
                
                if existing:
                    logger.debug(f"🔍 [CONDITION_REF] 이미 대기 중인 매수 신호 존재: {stock_code}")
                    return
                
                # 새 매수 신호 저장
                pending_signal = PendingBuySignal(
                    condition_id=condition_id,
                    stock_code=stock_code,
                    stock_name=stock_name,
                    detected_at=datetime.now(),
                    status="PENDING",
                    reference_candle_high=candle['high_price'],
                    reference_candle_date=candle['timestamp'],
                    target_price=target_price
                )
                
                session.add(pending_signal)
                session.commit()
                
                logger.info(f"🔍 [CONDITION_REF] 매수 신호 생성: {stock_name}({stock_code}) - "
                          f"현재가: {current_price}, 목표가: {target_price}, "
                          f"기준봉: {candle['timestamp'].strftime('%Y-%m-%d')} "
                          f"({candle['close_price']}원)")
                
                # 실제 매수 주문 실행
                await self._execute_buy_order(stock_code, stock_name, current_price, pending_signal.id)
                
                break
                
        except Exception as e:
            logger.error(f"🔍 [CONDITION_REF] 매수 신호 생성 오류 {stock_code}: {e}")

# 전역 인스턴스
condition_monitor = ConditionMonitor()