"""
스캘핑 전략 관리자
고빈도 단기 매매를 위한 전략들을 구현합니다.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import pandas as pd
import numpy as np
from sqlalchemy.orm import Session

from models import get_db, WatchlistStock, TradingStrategy, StrategySignal, PendingBuySignal
from kiwoom_api import KiwoomAPI
from signal_manager import SignalManager, SignalType, SignalStatus
from config import Config

logger = logging.getLogger(__name__)

class ScalpingStrategyManager:
    """스캘핑 전략 관리자"""
    
    def __init__(self):
        self.running = False
        self.monitoring_task = None
        self.kiwoom_api = KiwoomAPI()
        self.signal_manager = SignalManager()
        
        # 스캘핑 전략 파라미터
        self.scalping_strategies = {
            "MOMENTUM_SCALP": {
                "timeframe": "1M",  # 1분봉
                "lookback_period": 5,  # 5개 봉 분석
                "volume_threshold": 3.0,  # 평균 거래량의 3배
                "profit_target": 0.5,  # 0.5% 수익 목표
                "stop_loss": 0.3,  # 0.3% 손절
                "max_hold_minutes": 5,  # 최대 5분 보유
                "min_price_change": 0.1  # 최소 0.1% 변동
            },
            "BOLLINGER_SCALP": {
                "timeframe": "5M",  # 5분봉
                "ma_period": 20,
                "std_multiplier": 2.0,
                "rsi_period": 14,
                "rsi_oversold": 30,
                "rsi_overbought": 70,
                "profit_target": 1.0,  # 1% 수익 목표
                "stop_loss": 0.5,  # 0.5% 손절
                "max_hold_minutes": 15  # 최대 15분 보유
            },
            "VOLUME_SCALP": {
                "timeframe": "3M",  # 3분봉
                "volume_multiplier": 3.0,  # 평균 거래량의 3배
                "price_momentum": 0.2,  # 0.2% 이상 상승
                "profit_target": 0.8,  # 0.8% 수익 목표
                "stop_loss": 0.4,  # 0.4% 손절
                "max_hold_minutes": 10  # 최대 10분 보유
            }
        }
        
        # 활성 포지션 추적
        self.active_positions = {}  # {stock_code: {entry_time, entry_price, strategy, target, stop}}
        
    async def start_scalping_monitoring(self):
        """스캘핑 모니터링 시작"""
        if self.running:
            logger.warning("스캘핑 모니터링이 이미 실행 중입니다")
            return
            
        self.running = True
        self.monitoring_task = asyncio.create_task(self._scalping_loop())
        logger.info("🚀 [SCALPING] 스캘핑 모니터링 시작")
    
    async def stop_scalping_monitoring(self):
        """스캘핑 모니터링 중지"""
        self.running = False
        if self.monitoring_task:
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass
        logger.info("🛑 [SCALPING] 스캘핑 모니터링 중지")
    
    async def _scalping_loop(self):
        """스캘핑 메인 루프"""
        while self.running:
            try:
                # 1. 활성 포지션 관리 (손절/익절)
                await self._manage_active_positions()
                
                # 2. 새로운 스캘핑 기회 탐색
                await self._scan_scalping_opportunities()
                
                # 3. 30초 대기 (고빈도)
                await asyncio.sleep(30)
                
            except Exception as e:
                logger.error(f"🚀 [SCALPING] 루프 오류: {e}")
                await asyncio.sleep(60)  # 오류 시 1분 대기
    
    async def _manage_active_positions(self):
        """활성 포지션 관리 (손절/익절)"""
        current_time = datetime.now()
        
        for stock_code, position in list(self.active_positions.items()):
            try:
                # 현재가 조회
                current_price = await self.kiwoom_api.get_current_price(stock_code)
                if not current_price:
                    continue
                
                entry_price = position['entry_price']
                strategy = position['strategy']
                entry_time = position['entry_time']
                
                # 수익률 계산
                profit_rate = ((current_price - entry_price) / entry_price) * 100
                
                # 보유 시간 계산
                hold_minutes = (current_time - entry_time).total_seconds() / 60
                
                # 전략별 파라미터
                params = self.scalping_strategies[strategy]
                
                # 손절/익절 조건 확인
                should_sell = False
                sell_reason = ""
                
                # 1. 수익 목표 달성
                if profit_rate >= params['profit_target']:
                    should_sell = True
                    sell_reason = f"수익 목표 달성: {profit_rate:.2f}%"
                
                # 2. 손절선 도달
                elif profit_rate <= -params['stop_loss']:
                    should_sell = True
                    sell_reason = f"손절선 도달: {profit_rate:.2f}%"
                
                # 3. 최대 보유 시간 초과
                elif hold_minutes >= params['max_hold_minutes']:
                    should_sell = True
                    sell_reason = f"최대 보유 시간 초과: {hold_minutes:.1f}분"
                
                if should_sell:
                    await self._execute_scalp_sell(stock_code, current_price, sell_reason)
                    del self.active_positions[stock_code]
                    
            except Exception as e:
                logger.error(f"🚀 [SCALPING] 포지션 관리 오류 - {stock_code}: {e}")
    
    async def _scan_scalping_opportunities(self):
        """스캘핑 기회 탐색"""
        try:
            # 관심종목 목록 조회
            watchlist_stocks = await self._get_watchlist_stocks()
            
            for stock in watchlist_stocks:
                if stock.stock_code in self.active_positions:
                    continue  # 이미 포지션이 있으면 스킵
                
                # 각 전략별로 신호 확인
                for strategy_name, params in self.scalping_strategies.items():
                    signal = await self._check_scalping_signal(stock, strategy_name, params)
                    if signal:
                        await self._execute_scalp_buy(stock, strategy_name, signal)
                        break  # 하나의 신호만 실행
                        
        except Exception as e:
            logger.error(f"🚀 [SCALPING] 기회 탐색 오류: {e}")
    
    async def _check_scalping_signal(self, stock: WatchlistStock, strategy_name: str, params: Dict) -> Optional[Dict]:
        """스캘핑 신호 확인"""
        try:
            # 차트 데이터 조회
            chart_data = await self.kiwoom_api.get_stock_chart_data(
                stock.stock_code, 
                params['timeframe'],
                count=params.get('lookback_period', 20) + 10
            )
            
            if not chart_data or len(chart_data) < params.get('lookback_period', 20):
                return None
            
            df = pd.DataFrame(chart_data)
            
            # 전략별 신호 확인
            if strategy_name == "MOMENTUM_SCALP":
                return await self._check_momentum_scalp_signal(df, params)
            elif strategy_name == "BOLLINGER_SCALP":
                return await self._check_bollinger_scalp_signal(df, params)
            elif strategy_name == "VOLUME_SCALP":
                return await self._check_volume_scalp_signal(df, params)
                
        except Exception as e:
            logger.error(f"🚀 [SCALPING] 신호 확인 오류 - {stock.stock_name}: {e}")
        
        return None
    
    async def _check_momentum_scalp_signal(self, df: pd.DataFrame, params: Dict) -> Optional[Dict]:
        """모멘텀 스캘핑 신호 확인"""
        try:
            # 최근 5개 봉 분석
            recent_bars = df.tail(params['lookback_period'])
            
            # 1. 연속 상승 확인
            price_changes = recent_bars['close'].pct_change() * 100
            consecutive_ups = 0
            for change in price_changes.tail(3):
                if change > params['min_price_change']:
                    consecutive_ups += 1
                else:
                    break
            
            if consecutive_ups < 2:  # 최소 2연속 상승
                return None
            
            # 2. 거래량 급증 확인
            avg_volume = recent_bars['volume'].mean()
            current_volume = recent_bars['volume'].iloc[-1]
            
            if current_volume < avg_volume * params['volume_threshold']:
                return None
            
            # 3. 현재가 확인
            current_price = recent_bars['close'].iloc[-1]
            
            return {
                'signal_type': 'BUY',
                'entry_price': current_price,
                'strategy': 'MOMENTUM_SCALP',
                'confidence': min(consecutive_ups * 0.3, 1.0)
            }
            
        except Exception as e:
            logger.error(f"🚀 [SCALPING] 모멘텀 신호 확인 오류: {e}")
            return None
    
    async def _check_bollinger_scalp_signal(self, df: pd.DataFrame, params: Dict) -> Optional[Dict]:
        """볼린저밴드 스캘핑 신호 확인"""
        try:
            # 볼린저밴드 계산
            df['ma'] = df['close'].rolling(window=params['ma_period']).mean()
            df['std'] = df['close'].rolling(window=params['ma_period']).std()
            df['upper'] = df['ma'] + (df['std'] * params['std_multiplier'])
            df['lower'] = df['ma'] - (df['std'] * params['std_multiplier'])
            
            # RSI 계산
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=params['rsi_period']).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=params['rsi_period']).mean()
            rs = gain / loss
            df['rsi'] = 100 - (100 / (1 + rs))
            
            # 최근 데이터
            current = df.iloc[-1]
            prev = df.iloc[-2]
            
            # 하단밴드 터치 후 상승 + RSI 과매도
            if (prev['close'] <= prev['lower'] and 
                current['close'] > prev['close'] and 
                current['rsi'] <= params['rsi_oversold']):
                
                return {
                    'signal_type': 'BUY',
                    'entry_price': current['close'],
                    'strategy': 'BOLLINGER_SCALP',
                    'confidence': 0.8
                }
            
        except Exception as e:
            logger.error(f"🚀 [SCALPING] 볼린저밴드 신호 확인 오류: {e}")
            return None
    
    async def _check_volume_scalp_signal(self, df: pd.DataFrame, params: Dict) -> Optional[Dict]:
        """거래량 스캘핑 신호 확인"""
        try:
            # 최근 데이터
            recent_bars = df.tail(10)
            
            # 평균 거래량
            avg_volume = recent_bars['volume'].mean()
            current_volume = recent_bars['volume'].iloc[-1]
            
            # 거래량 급증 확인
            if current_volume < avg_volume * params['volume_multiplier']:
                return None
            
            # 가격 모멘텀 확인
            price_change = ((recent_bars['close'].iloc[-1] - recent_bars['close'].iloc[-2]) / 
                           recent_bars['close'].iloc[-2]) * 100
            
            if price_change < params['price_momentum']:
                return None
            
            return {
                'signal_type': 'BUY',
                'entry_price': recent_bars['close'].iloc[-1],
                'strategy': 'VOLUME_SCALP',
                'confidence': min(current_volume / avg_volume / 10, 1.0)
            }
            
        except Exception as e:
            logger.error(f"🚀 [SCALPING] 거래량 신호 확인 오류: {e}")
            return None
    
    async def _execute_scalp_buy(self, stock: WatchlistStock, strategy_name: str, signal: Dict):
        """스캘핑 매수 실행"""
        try:
            # 매수 수량 계산 (소액 투자)
            max_invest = 100000  # 10만원
            quantity = int(max_invest / signal['entry_price'])
            
            if quantity < 1:
                return
            
            # 매수 주문
            order_result = await self.kiwoom_api.place_buy_order(
                stock.stock_code,
                quantity,
                signal['entry_price']
            )
            
            if order_result.get('success'):
                # 포지션 등록
                self.active_positions[stock.stock_code] = {
                    'entry_time': datetime.now(),
                    'entry_price': signal['entry_price'],
                    'strategy': strategy_name,
                    'quantity': quantity,
                    'target': signal['entry_price'] * (1 + self.scalping_strategies[strategy_name]['profit_target'] / 100),
                    'stop': signal['entry_price'] * (1 - self.scalping_strategies[strategy_name]['stop_loss'] / 100)
                }
                
                logger.info(f"🚀 [SCALPING] 매수 완료 - {stock.stock_name}({stock.stock_code}) "
                           f"전략: {strategy_name}, 수량: {quantity}, 가격: {signal['entry_price']}")
            else:
                logger.error(f"🚀 [SCALPING] 매수 실패 - {stock.stock_name}: {order_result.get('error')}")
                
        except Exception as e:
            logger.error(f"🚀 [SCALPING] 매수 실행 오류 - {stock.stock_name}: {e}")
    
    async def _execute_scalp_sell(self, stock_code: str, current_price: float, reason: str):
        """스캘핑 매도 실행"""
        try:
            position = self.active_positions[stock_code]
            quantity = position['quantity']
            
            # 매도 주문
            order_result = await self.kiwoom_api.place_sell_order(
                stock_code,
                quantity,
                current_price
            )
            
            if order_result.get('success'):
                profit = (current_price - position['entry_price']) * quantity
                profit_rate = ((current_price - position['entry_price']) / position['entry_price']) * 100
                
                logger.info(f"🚀 [SCALPING] 매도 완료 - {stock_code} "
                           f"수량: {quantity}, 가격: {current_price}, "
                           f"수익: {profit:,.0f}원 ({profit_rate:.2f}%), 사유: {reason}")
            else:
                logger.error(f"🚀 [SCALPING] 매도 실패 - {stock_code}: {order_result.get('error')}")
                
        except Exception as e:
            logger.error(f"🚀 [SCALPING] 매도 실행 오류 - {stock_code}: {e}")
    
    async def _get_watchlist_stocks(self) -> List[WatchlistStock]:
        """관심종목 목록 조회"""
        stocks = []
        for db in get_db():
            session: Session = db
            try:
                stocks = session.query(WatchlistStock).filter(
                    WatchlistStock.is_active == True
                ).all()
                break
            except Exception as e:
                logger.error(f"🚀 [SCALPING] 관심종목 조회 오류: {e}")
                continue
        return stocks
    
    async def get_scalping_status(self) -> Dict:
        """스캘핑 상태 조회"""
        return {
            "is_running": self.running,
            "active_positions": len(self.active_positions),
            "strategies": list(self.scalping_strategies.keys()),
            "positions_detail": self.active_positions
        }

# 전역 인스턴스
scalping_manager = ScalpingStrategyManager()

