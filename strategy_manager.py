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
        self.cache_duration = 300  # 5분 캐시 유지
        
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
                
                # 각 전략별로 관심종목 스캔
                for strategy in strategies:
                    await self._scan_strategy_signals(strategy, watchlist)
                
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
            logger.info(f"🎯 [STRATEGY_MANAGER] {strategy.strategy_name} 전략 스캔 시작")
            
            for stock in watchlist:
                try:
                    # 종목별 신호 계산
                    signal_result = await self._calculate_strategy_signal(strategy, stock)
                    
                    if signal_result:
                        # 신호 생성
                        await self._create_strategy_signal(strategy, stock, signal_result)
                        
                except Exception as e:
                    logger.error(f"🎯 [STRATEGY_MANAGER] {stock.stock_name}({stock.stock_code}) 신호 계산 오류: {e}")
                    continue
                
                # API 제한 고려하여 잠시 대기 (캐싱으로 인해 대기 시간 단축)
                await asyncio.sleep(0.5)
            
            logger.info(f"🎯 [STRATEGY_MANAGER] {strategy.strategy_name} 전략 스캔 완료")
            
        except Exception as e:
            logger.error(f"🎯 [STRATEGY_MANAGER] 전략 스캔 오류: {e}")
    
    async def _get_cached_chart_data(self, stock_code: str) -> Optional[List]:
        """캐시된 차트 데이터 조회 또는 새로 조회"""
        try:
            current_time = datetime.now()
            
            # 캐시 확인
            if stock_code in self.chart_cache:
                cached_data, cache_time = self.chart_cache[stock_code]
                if (current_time - cache_time).total_seconds() < self.cache_duration:
                    logger.debug(f"🎯 [STRATEGY_MANAGER] 캐시된 차트 데이터 사용: {stock_code}")
                    return cached_data
            
            # API 제한 확인
            from api_rate_limiter import api_rate_limiter
            if not api_rate_limiter.is_api_available():
                logger.warning(f"🎯 [STRATEGY_MANAGER] API 제한 상태로 차트 조회 건너뜀: {stock_code}")
                return None
            
            # 새로 조회
            logger.info(f"🎯 [STRATEGY_MANAGER] 차트 데이터 새로 조회: {stock_code}")
            # 데이트레이딩용 5분봉 요청
            chart_data = await self.kiwoom_api.get_stock_chart_data(stock_code, period="5M")
            
            # 캐시에 저장
            if chart_data:
                self.chart_cache[stock_code] = (chart_data, current_time)
            
            return chart_data
            
        except Exception as e:
            logger.error(f"🎯 [STRATEGY_MANAGER] 차트 데이터 조회 오류: {e}")
            return None

    async def _calculate_strategy_signal(self, strategy: TradingStrategy, stock: WatchlistStock) -> Optional[Dict]:
        """전략별 신호 계산"""
        try:
            # 캐시된 차트 데이터 조회
            chart_data = await self._get_cached_chart_data(stock.stock_code)
            
            if not chart_data or len(chart_data) < 20:
                logger.warning(f"🎯 [STRATEGY_MANAGER] {stock.stock_name} 차트 데이터 부족")
                return None
            
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
            # JSON 파라미터 파싱
            import json
            try:
                parameters = json.loads(strategy.parameters) if strategy.parameters else {}
            except (json.JSONDecodeError, TypeError):
                parameters = {}
            
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
            
            # 신호 판단
            signal_type = None
            if current_momentum > 0 and prev_momentum <= 0:
                # 0선 상향 돌파
                signal_type = "BUY"
            elif current_momentum < 0 and prev_momentum >= 0:
                # 0선 하향 돌파
                signal_type = "SELL"
            
            if signal_type:
                return {
                    "signal_type": signal_type,
                    "signal_value": current_momentum,
                    "additional_data": {
                        "momentum_period": momentum_period,
                        "current_price": df['Close'].iloc[-1],
                        "prev_price": df['Close'].iloc[-momentum_period-1]
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
            
            # 신호 판단
            signal_type = None
            if current_disparity < buy_threshold and prev_disparity >= buy_threshold:
                # 매수 임계값 하향 돌파
                signal_type = "BUY"
            elif current_disparity > sell_threshold and prev_disparity <= sell_threshold:
                # 매도 임계값 상향 돌파
                signal_type = "SELL"
            
            if signal_type:
                return {
                    "signal_type": signal_type,
                    "signal_value": current_disparity,
                    "additional_data": {
                        "ma_period": ma_period,
                        "current_price": df['Close'].iloc[-1],
                        "ma_value": df['ma'].iloc[-1],
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
            
            # 신호 판단
            signal_type = None
            if current_price <= lower_band:
                # 하단밴드 터치 - 매수 신호
                signal_type = "BUY"
            elif current_price >= upper_band:
                # 상단밴드 터치 - 매도 신호
                signal_type = "SELL"
            
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
                        "ma_value": df['ma'].iloc[-1]
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
            
            # 신호 판단
            signal_type = None
            if current_rsi < oversold_threshold and prev_rsi >= oversold_threshold:
                # 과매도 구간 진입 - 매수 신호
                signal_type = "BUY"
            elif current_rsi > overbought_threshold and prev_rsi <= overbought_threshold:
                # 과매수 구간 진입 - 매도 신호
                signal_type = "SELL"
            
            if signal_type:
                return {
                    "signal_type": signal_type,
                    "signal_value": current_rsi,
                    "additional_data": {
                        "rsi_period": rsi_period,
                        "current_price": df['Close'].iloc[-1],
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
                
                signal = StrategySignal(
                    strategy_id=strategy.id,
                    stock_code=stock.stock_code,
                    stock_name=stock.stock_name,
                    signal_type=signal_result["signal_type"],
                    signal_value=signal_result["signal_value"],
                    detected_date=date.today(),
                    additional_data=signal_result.get("additional_data", {})
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
