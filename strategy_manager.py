"""
전략 매매 관리자
관심종목 기반으로 모멘텀, 이격도, 볼린저밴드, RSI 전략을 실행하고 신호를 생성합니다.
"""

import asyncio
import logging
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Any
import pandas as pd
import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from models import get_db, WatchlistStock, TradingStrategy, StrategySignal, PendingBuySignal
from kiwoom_api import KiwoomAPI
from signal_manager import SignalManager, SignalType, SignalStatus
from config import Config

logger = logging.getLogger(__name__)


class StrategyManager:
    """전략 매매 관리자"""
    
    def __init__(self):
        self.running = False
        self.monitoring_task = None
        self.kiwoom_api = KiwoomAPI()
        self.signal_manager = SignalManager()
        
        # 차트 데이터 캐싱 (중복 호출 방지)
        self.chart_cache = {}
        self.cache_duration = 600  # 10분 캐시 (API 호출 감소) 유지
        
        # 전략별 파라미터 기본값
        self.default_strategies = {
            "MOMENTUM": {
                "momentum_period": 10,
                "trend_confirmation_days": 3
            },
            "DISPARITY": {
                "ma_period": 20,
                "buy_threshold": 95.0,
                "sell_threshold": 105.0
            },
            "BOLLINGER": {
                "ma_period": 20,
                "std_multiplier": 2.0,
                "confirmation_days": 3
            },
            "RSI": {
                "rsi_period": 14,
                "oversold_threshold": 30.0,
                "overbought_threshold": 70.0
            }
        }

    def _to_native_json(self, value: Any) -> Any:
        """NumPy/pandas/Datetime 등을 JSON 직렬화 가능한 기본 파이썬 타입으로 변환"""
        # 딕셔너리
        if isinstance(value, dict):
            return {k: self._to_native_json(v) for k, v in value.items()}
        # 리스트/튜플/시퀀스
        if isinstance(value, (list, tuple)):
            return [self._to_native_json(v) for v in value]
        # NumPy 스칼라 타입
        if isinstance(value, (np.integer,)):
            return int(value)
        if isinstance(value, (np.floating,)):
            return float(value)
        if isinstance(value, (np.bool_,)):
            return bool(value)
        # pandas Timestamp/NaT 처리
        try:
            import pandas as _pd  # 이미 상단에 임포트되어 있지만, 방어적 참조
            if isinstance(value, _pd.Timestamp):
                return value.isoformat()
        except Exception:
            pass
        # datetime/date
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        # 그 외 기본 타입은 그대로 반환
        return value
    
    async def start_strategy_monitoring(self):
        """전략 모니터링 시작"""
        if self.running:
            logger.warning("🎯 [STRATEGY_MANAGER] 전략 모니터링이 이미 실행 중입니다")
            return
        
        logger.info("🎯 [STRATEGY_MANAGER] 전략 모니터링 시작")
        self.running = True
        
        # 키움 API 연결
        await self.kiwoom_api.connect()
        
        # 모니터링 태스크 시작
        self.monitoring_task = asyncio.create_task(self._monitoring_loop())
    
    async def stop_strategy_monitoring(self):
        """전략 모니터링 중지"""
        if not self.running:
            logger.warning("🎯 [STRATEGY_MANAGER] 전략 모니터링이 실행 중이 아닙니다")
            return
        
        logger.info("🎯 [STRATEGY_MANAGER] 전략 모니터링 중지")
        self.running = False
        
        # 모니터링 태스크 취소
        if self.monitoring_task:
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass
        
        # 키움 API 연결 종료
        await self.kiwoom_api.disconnect()
    
    async def _monitoring_loop(self):
        """전략 모니터링 루프 (1분 주기)"""
        while self.running:
            try:
                logger.info("🎯 [STRATEGY_MANAGER] 전략 모니터링 실행")
                
                # 활성화된 전략들 조회
                strategies = await self._get_active_strategies()
                if not strategies:
                    logger.info("🎯 [STRATEGY_MANAGER] 활성화된 전략이 없습니다")
                    await asyncio.sleep(60)  # 1분 대기
                    continue
                
                # 관심종목 조회
                watchlist = await self._get_active_watchlist()
                if not watchlist:
                    logger.info("🎯 [STRATEGY_MANAGER] 활성화된 관심종목이 없습니다")
                    await asyncio.sleep(60)  # 1분 대기
                    continue
                
                # 각 전략별로 관심종목 스캔 (순차 실행으로 API 제한 방지)
                for i, strategy in enumerate(strategies):
                    logger.info(f"🎯 [STRATEGY_MANAGER] 전략 {i+1}/{len(strategies)} 실행: {strategy.strategy_name}")
                    await self._scan_strategy_signals(strategy, watchlist)
                    
                    # 전략 간 대기 (마지막 전략 제외)
                    if i < len(strategies) - 1:
                        logger.debug(f"🎯 [STRATEGY_MANAGER] 다음 전략 실행 전 3초 대기...")
                        await asyncio.sleep(3)
                
                logger.info(f"🎯 [STRATEGY_MANAGER] 전략 모니터링 완료 - {len(strategies)}개 전략, {len(watchlist)}개 종목")
                
            except Exception as e:
                logger.error(f"🎯 [STRATEGY_MANAGER] 모니터링 루프 오류: {e}")
            
            # 1분 대기
            await asyncio.sleep(60)
    
    async def _get_active_strategies(self) -> List[TradingStrategy]:
        """활성화된 전략들 조회"""
        try:
            for db in get_db():
                session: Session = db
                strategies = session.query(TradingStrategy).filter(
                    TradingStrategy.is_enabled == True
                ).all()
                return strategies
        except Exception as e:
            logger.error(f"🎯 [STRATEGY_MANAGER] 전략 조회 오류: {e}")
            return []
    
    async def _get_active_watchlist(self) -> List[WatchlistStock]:
        """활성화된 관심종목 조회"""
        try:
            for db in get_db():
                session: Session = db
                watchlist = session.query(WatchlistStock).filter(
                    WatchlistStock.is_active == True
                ).all()
                return watchlist
        except Exception as e:
            logger.error(f"🎯 [STRATEGY_MANAGER] 관심종목 조회 오류: {e}")
            return []
    
    async def _scan_strategy_signals(self, strategy: TradingStrategy, watchlist: List[WatchlistStock]):
        """특정 전략으로 관심종목 스캔"""
        try:
            logger.info(f"🎯 [STRATEGY_MANAGER] {strategy.strategy_name} 전략 스캔 시작 - 대상 종목: {len(watchlist)}개")
            
            signal_count = 0
            for i, stock in enumerate(watchlist, 1):
                try:
                    logger.debug(f"🔍 [SCAN_DEBUG] {i}/{len(watchlist)} - {stock.stock_name}({stock.stock_code}) 스캔 중...")
                    
                    # 종목별 신호 계산
                    signal_result = await self._calculate_strategy_signal(strategy, stock)
                    
                    if signal_result:
                        # 신호 생성
                        await self._create_strategy_signal(strategy, stock, signal_result)
                        signal_count += 1
                        logger.info(f"✅ [SCAN_RESULT] {stock.stock_name} - {signal_result['signal_type']} 신호 감지!")
                    else:
                        logger.debug(f"❌ [SCAN_RESULT] {stock.stock_name} - 신호 없음")
                        
                except Exception as e:
                    logger.error(f"🎯 [STRATEGY_MANAGER] {stock.stock_name}({stock.stock_code}) 신호 계산 오류: {e}")
                    continue
                
                # API 제한 고려하여 충분한 대기 (최소 1.5초 간격 보장)
                await asyncio.sleep(1.8)
            
            logger.info(f"🎯 [STRATEGY_MANAGER] {strategy.strategy_name} 전략 스캔 완료 - 신호 발생: {signal_count}개")
            
        except Exception as e:
            logger.error(f"🎯 [STRATEGY_MANAGER] 전략 스캔 오류: {e}")
    
    async def _get_cached_chart_data(self, stock_code: str) -> Optional[List]:
        """캐시된 차트 데이터 조회 또는 새로 조회"""
        try:
            current_time = datetime.now()
            
            # 캐시 확인
            if stock_code in self.chart_cache:
                cached_data, cache_time = self.chart_cache[stock_code]
                cache_age = (current_time - cache_time).total_seconds()
                if cache_age < self.cache_duration:
                    logger.debug(f"🎯 [CHART_DEBUG] 캐시된 차트 데이터 사용: {stock_code} (캐시나이: {cache_age:.1f}초)")
                    logger.debug(f"🎯 [CHART_DEBUG] 캐시데이터 개수: {len(cached_data) if cached_data else 0}개")
                    if cached_data and len(cached_data) > 0:
                        logger.debug(f"🎯 [CHART_DEBUG] 캐시데이터 샘플: {cached_data[0] if cached_data else 'None'}")
                    return cached_data
                else:
                    logger.debug(f"🎯 [CHART_DEBUG] 캐시 만료: {stock_code} (캐시나이: {cache_age:.1f}초 > {self.cache_duration}초)")
            
            # API 제한 확인
            from api_rate_limiter import api_rate_limiter
            if not api_rate_limiter.is_api_available():
                logger.warning(f"🎯 [CHART_DEBUG] API 제한 상태로 차트 조회 건너뜀: {stock_code}")
                # 제한 상태에서는 캐시된 데이터라도 반환 (빈 데이터가 아닌 경우)
                if stock_code in self.chart_cache:
                    cached_data, _ = self.chart_cache[stock_code]
                    if cached_data and len(cached_data) > 0:
                        logger.info(f"🎯 [CHART_DEBUG] API 제한 중 - 캐시된 데이터 사용: {stock_code}")
                        return cached_data
                return None
            
            # 새로 조회
            logger.info(f"🎯 [CHART_DEBUG] 차트 데이터 새로 조회 시작: {stock_code}")
            # 데이트레이딩용 5분봉 요청
            chart_data = await self.kiwoom_api.get_stock_chart_data(stock_code, period="5M")
            
            # 디버깅: 조회 결과 상세 분석
            logger.debug(f"🎯 [CHART_DEBUG] API 조회 결과: {stock_code}")
            logger.debug(f"🎯 [CHART_DEBUG] - 데이터 타입: {type(chart_data)}")
            logger.debug(f"🎯 [CHART_DEBUG] - 데이터 개수: {len(chart_data) if chart_data else 0}개")
            logger.debug(f"🎯 [CHART_DEBUG] - 데이터가 None인가: {chart_data is None}")
            logger.debug(f"🎯 [CHART_DEBUG] - 데이터가 빈 리스트인가: {chart_data == []}")
            
            if chart_data:
                logger.debug(f"🎯 [CHART_DEBUG] - 첫 번째 데이터: {chart_data[0] if len(chart_data) > 0 else 'None'}")
                logger.debug(f"🎯 [CHART_DEBUG] - 마지막 데이터: {chart_data[-1] if len(chart_data) > 0 else 'None'}")
                
                # 데이터 구조 검증
                if len(chart_data) > 0:
                    first_item = chart_data[0]
                    logger.debug(f"🎯 [CHART_DEBUG] - 첫 데이터 키들: {list(first_item.keys()) if isinstance(first_item, dict) else 'Not a dict'}")
                    if isinstance(first_item, dict):
                        for key in ['timestamp', 'open', 'high', 'low', 'close', 'volume']:
                            value = first_item.get(key, 'MISSING')
                            logger.debug(f"🎯 [CHART_DEBUG] - {key}: {value}")
            else:
                logger.warning(f"🎯 [CHART_DEBUG] ⚠️ {stock_code} 차트 데이터가 비어있음!")
            
            # 캐시에 저장
            if chart_data:
                self.chart_cache[stock_code] = (chart_data, current_time)
                logger.debug(f"🎯 [CHART_DEBUG] 캐시에 저장 완료: {stock_code}")
            else:
                logger.warning(f"🎯 [CHART_DEBUG] ⚠️ {stock_code} 빈 데이터로 인해 캐시 저장 안함")
            
            return chart_data
            
        except Exception as e:
            logger.error(f"🎯 [CHART_DEBUG] 차트 데이터 조회 오류: {stock_code} - {e}")
            import traceback
            logger.error(f"🎯 [CHART_DEBUG] 스택 트레이스: {traceback.format_exc()}")
            return None

    async def _calculate_strategy_signal(self, strategy: TradingStrategy, stock: WatchlistStock) -> Optional[Dict]:
        """전략별 신호 계산"""
        try:
            # 캐시된 차트 데이터 조회
            chart_data = await self._get_cached_chart_data(stock.stock_code)
            
            # 디버깅: 차트 데이터 부족 원인 분석
            logger.debug(f"🎯 [CHART_DEBUG] {stock.stock_name}({stock.stock_code}) 차트 데이터 검증:")
            logger.debug(f"🎯 [CHART_DEBUG] - chart_data is None: {chart_data is None}")
            logger.debug(f"🎯 [CHART_DEBUG] - chart_data == []: {chart_data == []}")
            logger.debug(f"🎯 [CHART_DEBUG] - len(chart_data): {len(chart_data) if chart_data else 0}")
            logger.debug(f"🎯 [CHART_DEBUG] - 최소 필요 개수: 20개")
            
            if not chart_data:
                logger.warning(f"🎯 [CHART_DEBUG] ⚠️ {stock.stock_name} 차트 데이터가 None 또는 빈 리스트")
                logger.warning(f"🎯 [STRATEGY_MANAGER] {stock.stock_name} 차트 데이터 부족 (데이터 없음)")
                return None
            
            if len(chart_data) < 20:
                logger.warning(f"🎯 [CHART_DEBUG] ⚠️ {stock.stock_name} 차트 데이터 개수 부족: {len(chart_data)}개 < 20개")
                logger.warning(f"🎯 [STRATEGY_MANAGER] {stock.stock_name} 차트 데이터 부족 (개수 부족)")
                # 데이터가 부족해도 디버깅 정보는 출력
                self._log_strategy_debug_info(strategy, stock, chart_data, "데이터 부족")
                return None
            
            logger.debug(f"🎯 [CHART_DEBUG] ✅ {stock.stock_name} 차트 데이터 충분: {len(chart_data)}개")
            
            # DataFrame 생성
            df = pd.DataFrame(chart_data)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df.set_index('timestamp', inplace=True)
            df = df.sort_index()
            
            # 컬럼명 변경 (키움 API 형식에 맞춤)
            df = df.rename(columns={
                'open': 'Open',
                'high': 'High', 
                'low': 'Low',
                'close': 'Close',
                'volume': 'Volume'
            })
            
            # 데이터 타입 변환
            df['Open'] = pd.to_numeric(df['Open'])
            df['High'] = pd.to_numeric(df['High'])
            df['Low'] = pd.to_numeric(df['Low'])
            df['Close'] = pd.to_numeric(df['Close'])
            df['Volume'] = pd.to_numeric(df['Volume'])
            
            # 전략별 신호 계산
            # JSON 파라미터 파싱 (이미 dict인 경우와 문자열인 경우 모두 처리)
            import json
            try:
                if isinstance(strategy.parameters, dict):
                    parameters = strategy.parameters
                elif isinstance(strategy.parameters, str):
                    parameters = json.loads(strategy.parameters) if strategy.parameters else {}
                else:
                    parameters = {}
            except (json.JSONDecodeError, TypeError):
                parameters = {}
            
            # 전략별 신호 계산 전 디버깅 정보 출력
            self._log_strategy_debug_info(strategy, stock, chart_data, "정상")
            
            if strategy.strategy_type == "MOMENTUM":
                return await self._calculate_momentum_signal(df, parameters)
            elif strategy.strategy_type == "DISPARITY":
                return await self._calculate_disparity_signal(df, parameters)
            elif strategy.strategy_type == "BOLLINGER":
                return await self._calculate_bollinger_signal(df, parameters)
            elif strategy.strategy_type == "RSI":
                return await self._calculate_rsi_signal(df, parameters)
            
        except Exception as e:
            logger.error(f"🎯 [STRATEGY_MANAGER] {stock.stock_name} 신호 계산 오류: {e}")
            return None
    
    def _log_strategy_debug_info(self, strategy, stock, chart_data, status):
        """전략별 디버깅 정보 출력"""
        try:
            logger.info(f"📊 [STRATEGY_DEBUG] ===== {strategy.strategy_name} 전략 디버깅 =====")
            logger.info(f"📊 [STRATEGY_DEBUG] 종목: {stock.stock_name}({stock.stock_code})")
            logger.info(f"📊 [STRATEGY_DEBUG] 상태: {status}")
            logger.info(f"📊 [STRATEGY_DEBUG] 전략 타입: {strategy.strategy_type}")
            logger.info(f"📊 [STRATEGY_DEBUG] 전략 파라미터: {strategy.parameters}")
            logger.info(f"📊 [STRATEGY_DEBUG] 차트 데이터 개수: {len(chart_data) if chart_data else 0}")
            
            if chart_data and len(chart_data) > 0:
                # 최신 데이터 정보
                latest_data = chart_data[-1]
                logger.info(f"📊 [STRATEGY_DEBUG] 최신 데이터: {latest_data}")
                
                # 가격 정보
                if 'close' in latest_data:
                    current_price = latest_data['close']
                    logger.info(f"📊 [STRATEGY_DEBUG] 현재가: {current_price}")
                    
                    # 전략별 기준값 계산 및 출력
                    if strategy.strategy_type == "MOMENTUM":
                        self._log_momentum_debug(strategy, chart_data, current_price)
                    elif strategy.strategy_type == "DISPARITY":
                        self._log_disparity_debug(strategy, chart_data, current_price)
                    elif strategy.strategy_type == "BOLLINGER":
                        self._log_bollinger_debug(strategy, chart_data, current_price)
                    elif strategy.strategy_type == "RSI":
                        self._log_rsi_debug(strategy, chart_data, current_price)
            
            logger.info(f"📊 [STRATEGY_DEBUG] ===== {strategy.strategy_name} 디버깅 완료 =====")
            
        except Exception as e:
            logger.error(f"📊 [STRATEGY_DEBUG] 디버깅 정보 출력 오류: {e}")
    
    def _log_momentum_debug(self, strategy, chart_data, current_price):
        """모멘텀 전략 디버깅 정보"""
        try:
            import json
            # strategy.parameters가 이미 dict인 경우와 문자열인 경우 모두 처리
            if isinstance(strategy.parameters, dict):
                parameters = strategy.parameters
            elif isinstance(strategy.parameters, str):
                parameters = json.loads(strategy.parameters) if strategy.parameters else {}
            else:
                parameters = {}
            momentum_period = parameters.get("momentum_period", 10)
            
            if len(chart_data) >= momentum_period + 1:
                prev_price = chart_data[-momentum_period-1]['close']
                momentum = current_price - prev_price
                
                logger.info(f"📊 [MOMENTUM_DEBUG] 모멘텀 기간: {momentum_period}일")
                logger.info(f"📊 [MOMENTUM_DEBUG] 현재가: {current_price}")
                logger.info(f"📊 [MOMENTUM_DEBUG] {momentum_period}일전가: {prev_price}")
                logger.info(f"📊 [MOMENTUM_DEBUG] 모멘텀 값: {momentum:.2f}")
                logger.info(f"📊 [MOMENTUM_DEBUG] 0선 돌파 조건: {momentum > 0}")
                logger.info(f"📊 [MOMENTUM_DEBUG] 매수 조건: 0선 상향 돌파 (현재 모멘텀 > 0 이고 이전 모멘텀 <= 0)")
                logger.info(f"📊 [MOMENTUM_DEBUG] 매도 조건: 0선 하향 돌파 (현재 모멘텀 < 0 이고 이전 모멘텀 >= 0)")
            else:
                logger.info(f"📊 [MOMENTUM_DEBUG] 데이터 부족으로 모멘텀 계산 불가")
                
        except Exception as e:
            logger.error(f"📊 [MOMENTUM_DEBUG] 모멘텀 디버깅 오류: {e}")
    
    def _log_disparity_debug(self, strategy, chart_data, current_price):
        """이격도 전략 디버깅 정보"""
        try:
            import json
            # strategy.parameters가 이미 dict인 경우와 문자열인 경우 모두 처리
            if isinstance(strategy.parameters, dict):
                parameters = strategy.parameters
            elif isinstance(strategy.parameters, str):
                parameters = json.loads(strategy.parameters) if strategy.parameters else {}
            else:
                parameters = {}
            ma_period = parameters.get("ma_period", 20)
            buy_threshold = parameters.get("buy_threshold", 95.0)
            sell_threshold = parameters.get("sell_threshold", 105.0)
            
            if len(chart_data) >= ma_period:
                # 이동평균 계산
                recent_prices = [data['close'] for data in chart_data[-ma_period:]]
                ma_value = sum(recent_prices) / len(recent_prices)
                disparity = (current_price / ma_value) * 100
                
                logger.info(f"📊 [DISPARITY_DEBUG] 이동평균 기간: {ma_period}일")
                logger.info(f"📊 [DISPARITY_DEBUG] 현재가: {current_price}")
                logger.info(f"📊 [DISPARITY_DEBUG] {ma_period}일 이동평균: {ma_value:.2f}")
                logger.info(f"📊 [DISPARITY_DEBUG] 이격도: {disparity:.2f}%")
                logger.info(f"📊 [DISPARITY_DEBUG] 매수 임계값: {buy_threshold}%")
                logger.info(f"📊 [DISPARITY_DEBUG] 매도 임계값: {sell_threshold}%")
                logger.info(f"📊 [DISPARITY_DEBUG] 매수 조건: {disparity < buy_threshold}")
                logger.info(f"📊 [DISPARITY_DEBUG] 매도 조건: {disparity > sell_threshold}")
            else:
                logger.info(f"📊 [DISPARITY_DEBUG] 데이터 부족으로 이격도 계산 불가")
                
        except Exception as e:
            logger.error(f"📊 [DISPARITY_DEBUG] 이격도 디버깅 오류: {e}")
    
    def _log_bollinger_debug(self, strategy, chart_data, current_price):
        """볼린저밴드 전략 디버깅 정보"""
        try:
            import json
            import statistics
            # strategy.parameters가 이미 dict인 경우와 문자열인 경우 모두 처리
            if isinstance(strategy.parameters, dict):
                parameters = strategy.parameters
            elif isinstance(strategy.parameters, str):
                parameters = json.loads(strategy.parameters) if strategy.parameters else {}
            else:
                parameters = {}
            ma_period = parameters.get("ma_period", 20)
            std_multiplier = parameters.get("std_multiplier", 2.0)
            
            if len(chart_data) >= ma_period:
                # 이동평균과 표준편차 계산
                recent_prices = [data['close'] for data in chart_data[-ma_period:]]
                ma_value = sum(recent_prices) / len(recent_prices)
                std_value = statistics.stdev(recent_prices)
                
                upper_band = ma_value + (std_value * std_multiplier)
                lower_band = ma_value - (std_value * std_multiplier)
                
                logger.info(f"📊 [BOLLINGER_DEBUG] 이동평균 기간: {ma_period}일")
                logger.info(f"📊 [BOLLINGER_DEBUG] 표준편차 배수: {std_multiplier}")
                logger.info(f"📊 [BOLLINGER_DEBUG] 현재가: {current_price}")
                logger.info(f"📊 [BOLLINGER_DEBUG] {ma_period}일 이동평균: {ma_value:.2f}")
                logger.info(f"📊 [BOLLINGER_DEBUG] 표준편차: {std_value:.2f}")
                logger.info(f"📊 [BOLLINGER_DEBUG] 상단밴드: {upper_band:.2f}")
                logger.info(f"📊 [BOLLINGER_DEBUG] 하단밴드: {lower_band:.2f}")
                logger.info(f"📊 [BOLLINGER_DEBUG] 매수 조건 (하단밴드 터치): {current_price <= lower_band}")
                logger.info(f"📊 [BOLLINGER_DEBUG] 매도 조건 (상단밴드 터치): {current_price >= upper_band}")
            else:
                logger.info(f"📊 [BOLLINGER_DEBUG] 데이터 부족으로 볼린저밴드 계산 불가")
                
        except Exception as e:
            logger.error(f"📊 [BOLLINGER_DEBUG] 볼린저밴드 디버깅 오류: {e}")
    
    def _log_rsi_debug(self, strategy, chart_data, current_price):
        """RSI 전략 디버깅 정보"""
        try:
            import json
            # strategy.parameters가 이미 dict인 경우와 문자열인 경우 모두 처리
            if isinstance(strategy.parameters, dict):
                parameters = strategy.parameters
            elif isinstance(strategy.parameters, str):
                parameters = json.loads(strategy.parameters) if strategy.parameters else {}
            else:
                parameters = {}
            rsi_period = parameters.get("rsi_period", 14)
            oversold_threshold = parameters.get("oversold_threshold", 30.0)
            overbought_threshold = parameters.get("overbought_threshold", 70.0)
            
            if len(chart_data) >= rsi_period + 1:
                # RSI 계산
                prices = [data['close'] for data in chart_data[-rsi_period-1:]]
                gains = []
                losses = []
                
                for i in range(1, len(prices)):
                    change = prices[i] - prices[i-1]
                    if change > 0:
                        gains.append(change)
                        losses.append(0)
                    else:
                        gains.append(0)
                        losses.append(-change)
                
                avg_gain = sum(gains) / len(gains) if gains else 0
                avg_loss = sum(losses) / len(losses) if losses else 0
                
                if avg_loss != 0:
                    rs = avg_gain / avg_loss
                    rsi = 100 - (100 / (1 + rs))
                else:
                    rsi = 100
                
                logger.info(f"📊 [RSI_DEBUG] RSI 기간: {rsi_period}일")
                logger.info(f"📊 [RSI_DEBUG] 현재가: {current_price}")
                logger.info(f"📊 [RSI_DEBUG] RSI 값: {rsi:.2f}")
                logger.info(f"📊 [RSI_DEBUG] 과매도 임계값: {oversold_threshold}")
                logger.info(f"📊 [RSI_DEBUG] 과매수 임계값: {overbought_threshold}")
                logger.info(f"📊 [RSI_DEBUG] 매수 조건 (과매도): {rsi < oversold_threshold}")
                logger.info(f"📊 [RSI_DEBUG] 매도 조건 (과매수): {rsi > overbought_threshold}")
            else:
                logger.info(f"📊 [RSI_DEBUG] 데이터 부족으로 RSI 계산 불가")
                
        except Exception as e:
            logger.error(f"📊 [RSI_DEBUG] RSI 디버깅 오류: {e}")
    
    async def _calculate_momentum_signal(self, df: pd.DataFrame, params: Dict) -> Optional[Dict]:
        """모멘텀 전략 신호 계산"""
        try:
            momentum_period = params.get("momentum_period", 10)
            trend_confirmation_days = params.get("trend_confirmation_days", 3)
            
            if len(df) < momentum_period + trend_confirmation_days:
                return None
            
            # 모멘텀 계산: 당일 종가 - n기간 전 종가 (분봉에도 동일 적용)
            df['momentum'] = df['Close'] - df['Close'].shift(momentum_period)
            
            # 최근 데이터
            current_momentum = df['momentum'].iloc[-1]
            prev_momentum = df['momentum'].iloc[-2]
            current_price = df['Close'].iloc[-1]
            prev_price = df['Close'].iloc[-momentum_period-1]
            
            # 디버깅 로그: 현재값과 기준값 비교
            logger.info(f"📊 [MOMENTUM_DEBUG] 현재가: {current_price:.2f}, {momentum_period}일전가: {prev_price:.2f}")
            logger.info(f"📊 [MOMENTUM_DEBUG] 현재모멘텀: {current_momentum:.2f}, 이전모멘텀: {prev_momentum:.2f}")
            logger.info(f"📊 [MOMENTUM_DEBUG] 0선돌파조건 - 현재>0: {current_momentum > 0}, 이전<=0: {prev_momentum <= 0}")
            logger.info(f"📊 [MOMENTUM_DEBUG] 0선하향조건 - 현재<0: {current_momentum < 0}, 이전>=0: {prev_momentum >= 0}")
            
            # 신호 판단
            signal_type = None
            buy_condition = current_momentum > 0 and prev_momentum <= 0
            sell_condition = current_momentum < 0 and prev_momentum >= 0
            
            logger.info(f"📊 [MOMENTUM_DEBUG] 매수 조건 (0선 상향 돌파): {buy_condition}")
            logger.info(f"📊 [MOMENTUM_DEBUG] 매도 조건 (0선 하향 돌파): {sell_condition}")
            
            if buy_condition:
                # 0선 상향 돌파
                signal_type = "BUY"
                logger.info(f"🚀 [MOMENTUM_SIGNAL] BUY 신호 발생! 모멘텀: {current_momentum:.2f} (이전: {prev_momentum:.2f})")
            elif sell_condition:
                # 0선 하향 돌파
                signal_type = "SELL"
                logger.info(f"📉 [MOMENTUM_SIGNAL] SELL 신호 발생! 모멘텀: {current_momentum:.2f} (이전: {prev_momentum:.2f})")
            else:
                logger.info(f"📊 [MOMENTUM_DEBUG] 신호 없음 - 매수/매도 조건 미충족")
            
            if signal_type:
                return {
                    "signal_type": signal_type,
                    "signal_value": current_momentum,
                    "additional_data": {
                        "momentum_period": momentum_period,
                        "current_price": current_price,
                        "prev_price": prev_price
                    }
                }
            
            return None
            
        except Exception as e:
            logger.error(f"🎯 [STRATEGY_MANAGER] 모멘텀 신호 계산 오류: {e}")
            return None
    
    async def _calculate_disparity_signal(self, df: pd.DataFrame, params: Dict) -> Optional[Dict]:
        """이격도 전략 신호 계산"""
        try:
            ma_period = params.get("ma_period", 20)
            buy_threshold = params.get("buy_threshold", 95.0)
            sell_threshold = params.get("sell_threshold", 105.0)
            
            if len(df) < ma_period:
                return None
            
            # 이동평균 계산
            df['ma'] = df['Close'].rolling(window=ma_period).mean()
            
            # 이격도 계산: (현재가 / 이동평균) * 100
            df['disparity'] = (df['Close'] / df['ma']) * 100
            
            # 최근 데이터
            current_disparity = df['disparity'].iloc[-1]
            prev_disparity = df['disparity'].iloc[-2]
            current_price = df['Close'].iloc[-1]
            ma_value = df['ma'].iloc[-1]
            
            # 디버깅 로그: 현재값과 기준값 비교
            logger.debug(f"📊 [DISPARITY_DEBUG] 현재가: {current_price:.2f}, {ma_period}일이동평균: {ma_value:.2f}")
            logger.debug(f"📊 [DISPARITY_DEBUG] 현재이격도: {current_disparity:.2f}%, 이전이격도: {prev_disparity:.2f}%")
            logger.debug(f"📊 [DISPARITY_DEBUG] 매수임계값: {buy_threshold}%, 매도임계값: {sell_threshold}%")
            logger.debug(f"📊 [DISPARITY_DEBUG] 매수조건 - 현재<임계값: {current_disparity < buy_threshold}, 이전>=임계값: {prev_disparity >= buy_threshold}")
            logger.debug(f"📊 [DISPARITY_DEBUG] 매도조건 - 현재>임계값: {current_disparity > sell_threshold}, 이전<=임계값: {prev_disparity <= sell_threshold}")
            
            # 신호 판단
            signal_type = None
            if current_disparity < buy_threshold and prev_disparity >= buy_threshold:
                # 매수 임계값 하향 돌파
                signal_type = "BUY"
                logger.info(f"🚀 [DISPARITY_SIGNAL] BUY 신호 발생! 이격도: {current_disparity:.2f}% (임계값: {buy_threshold}%)")
            elif current_disparity > sell_threshold and prev_disparity <= sell_threshold:
                # 매도 임계값 상향 돌파
                signal_type = "SELL"
                logger.info(f"📉 [DISPARITY_SIGNAL] SELL 신호 발생! 이격도: {current_disparity:.2f}% (임계값: {sell_threshold}%)")
            
            if signal_type:
                return {
                    "signal_type": signal_type,
                    "signal_value": current_disparity,
                    "additional_data": {
                        "ma_period": ma_period,
                        "current_price": current_price,
                        "ma_value": ma_value,
                        "buy_threshold": buy_threshold,
                        "sell_threshold": sell_threshold
                    }
                }
            
            return None
            
        except Exception as e:
            logger.error(f"🎯 [STRATEGY_MANAGER] 이격도 신호 계산 오류: {e}")
            return None
    
    async def _calculate_bollinger_signal(self, df: pd.DataFrame, params: Dict) -> Optional[Dict]:
        """볼린저밴드 전략 신호 계산"""
        try:
            ma_period = params.get("ma_period", 20)
            std_multiplier = params.get("std_multiplier", 2.0)
            confirmation_days = params.get("confirmation_days", 3)
            
            if len(df) < ma_period + confirmation_days:
                return None
            
            # 이동평균과 표준편차 계산
            df['ma'] = df['Close'].rolling(window=ma_period).mean()
            df['std'] = df['Close'].rolling(window=ma_period).std()
            
            # 볼린저밴드 계산
            df['upper_band'] = df['ma'] + (df['std'] * std_multiplier)
            df['lower_band'] = df['ma'] - (df['std'] * std_multiplier)
            
            # 최근 데이터
            current_price = df['Close'].iloc[-1]
            upper_band = df['upper_band'].iloc[-1]
            lower_band = df['lower_band'].iloc[-1]
            ma_value = df['ma'].iloc[-1]
            std_value = df['std'].iloc[-1]
            
            # 디버깅 로그: 현재값과 기준값 비교
            logger.debug(f"📊 [BOLLINGER_DEBUG] 현재가: {current_price:.2f}, {ma_period}일이동평균: {ma_value:.2f}")
            logger.debug(f"📊 [BOLLINGER_DEBUG] 표준편차: {std_value:.2f}, 배수: {std_multiplier}")
            logger.debug(f"📊 [BOLLINGER_DEBUG] 상단밴드: {upper_band:.2f}, 하단밴드: {lower_band:.2f}")
            logger.debug(f"📊 [BOLLINGER_DEBUG] 매수조건 - 현재가<=하단밴드: {current_price <= lower_band}")
            logger.debug(f"📊 [BOLLINGER_DEBUG] 매도조건 - 현재가>=상단밴드: {current_price >= upper_band}")
            
            # 신호 판단
            signal_type = None
            if current_price <= lower_band:
                # 하단밴드 터치 - 매수 신호
                signal_type = "BUY"
                logger.info(f"🚀 [BOLLINGER_SIGNAL] BUY 신호 발생! 현재가: {current_price:.2f} (하단밴드: {lower_band:.2f})")
            elif current_price >= upper_band:
                # 상단밴드 터치 - 매도 신호
                signal_type = "SELL"
                logger.info(f"📉 [BOLLINGER_SIGNAL] SELL 신호 발생! 현재가: {current_price:.2f} (상단밴드: {upper_band:.2f})")
            
            if signal_type:
                return {
                    "signal_type": signal_type,
                    "signal_value": current_price,
                    "additional_data": {
                        "ma_period": ma_period,
                        "std_multiplier": std_multiplier,
                        "current_price": current_price,
                        "upper_band": upper_band,
                        "lower_band": lower_band,
                        "ma_value": ma_value
                    }
                }
            
            return None
            
        except Exception as e:
            logger.error(f"🎯 [STRATEGY_MANAGER] 볼린저밴드 신호 계산 오류: {e}")
            return None
    
    async def _calculate_rsi_signal(self, df: pd.DataFrame, params: Dict) -> Optional[Dict]:
        """RSI 전략 신호 계산"""
        try:
            rsi_period = params.get("rsi_period", 14)
            oversold_threshold = params.get("oversold_threshold", 30.0)
            overbought_threshold = params.get("overbought_threshold", 70.0)
            
            if len(df) < rsi_period + 1:
                return None
            
            # RSI 계산
            delta = df['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=rsi_period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=rsi_period).mean()
            rs = gain / loss
            df['rsi'] = 100 - (100 / (1 + rs))
            
            # 최근 데이터
            current_rsi = df['rsi'].iloc[-1]
            prev_rsi = df['rsi'].iloc[-2]
            current_price = df['Close'].iloc[-1]
            
            # 디버깅 로그: 현재값과 기준값 비교
            logger.debug(f"📊 [RSI_DEBUG] 현재가: {current_price:.2f}, RSI기간: {rsi_period}일")
            logger.debug(f"📊 [RSI_DEBUG] 현재RSI: {current_rsi:.2f}, 이전RSI: {prev_rsi:.2f}")
            logger.debug(f"📊 [RSI_DEBUG] 과매도임계값: {oversold_threshold}, 과매수임계값: {overbought_threshold}")
            logger.debug(f"📊 [RSI_DEBUG] 매수조건 - 현재<과매도: {current_rsi < oversold_threshold}, 이전>=과매도: {prev_rsi >= oversold_threshold}")
            logger.debug(f"📊 [RSI_DEBUG] 매도조건 - 현재>과매수: {current_rsi > overbought_threshold}, 이전<=과매수: {prev_rsi <= overbought_threshold}")
            
            # 신호 판단
            signal_type = None
            if current_rsi < oversold_threshold and prev_rsi >= oversold_threshold:
                # 과매도 구간 진입 - 매수 신호
                signal_type = "BUY"
                logger.info(f"🚀 [RSI_SIGNAL] BUY 신호 발생! RSI: {current_rsi:.2f} (과매도임계값: {oversold_threshold})")
            elif current_rsi > overbought_threshold and prev_rsi <= overbought_threshold:
                # 과매수 구간 진입 - 매도 신호
                signal_type = "SELL"
                logger.info(f"📉 [RSI_SIGNAL] SELL 신호 발생! RSI: {current_rsi:.2f} (과매수임계값: {overbought_threshold})")
            
            if signal_type:
                return {
                    "signal_type": signal_type,
                    "signal_value": current_rsi,
                    "additional_data": {
                        "rsi_period": rsi_period,
                        "current_price": current_price,
                        "oversold_threshold": oversold_threshold,
                        "overbought_threshold": overbought_threshold
                    }
                }
            
            return None
            
        except Exception as e:
            logger.error(f"🎯 [STRATEGY_MANAGER] RSI 신호 계산 오류: {e}")
            return None
    
    async def _create_strategy_signal(self, strategy: TradingStrategy, stock: WatchlistStock, signal_result: Dict):
        """전략 신호 생성 및 저장"""
        try:
            # StrategySignal 테이블에 저장
            for db in get_db():
                session: Session = db
                
                # NumPy/판다스 타입을 기본 파이썬 타입으로 변환
                raw_value = signal_result.get("signal_value")
                signal_value = None
                if raw_value is not None:
                    # np.float64 등 처리
                    try:
                        signal_value = float(raw_value)
                    except Exception:
                        signal_value = self._to_native_json(raw_value)

                additional_data_native = self._to_native_json(signal_result.get("additional_data", {}))

                signal = StrategySignal(
                    strategy_id=strategy.id,
                    stock_code=stock.stock_code,
                    stock_name=stock.stock_name,
                    signal_type=signal_result["signal_type"],
                    signal_value=signal_value,
                    detected_date=date.today(),
                    additional_data=additional_data_native
                )
                
                session.add(signal)
                session.commit()
                
                logger.info(f"🎯 [STRATEGY_MANAGER] 전략 신호 저장 완료 - {strategy.strategy_name}, {stock.stock_name}, {signal_result['signal_type']}")
            
            # PendingBuySignal에도 저장 (매수 신호인 경우)
            if signal_result["signal_type"] == "BUY":
                await self.signal_manager.create_signal(
                    condition_id=strategy.id,  # 전략 ID를 condition_id로 사용
                    stock_code=stock.stock_code,
                    stock_name=stock.stock_name,
                    signal_type=SignalType.STRATEGY,
                    additional_data=signal_result.get("additional_data", {})
                )
                
        except Exception as e:
            logger.error(f"🎯 [STRATEGY_MANAGER] 전략 신호 생성 오류: {e}")


# 전역 인스턴스
strategy_manager = StrategyManager()
