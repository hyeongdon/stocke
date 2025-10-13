import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from sqlalchemy.orm import Session

from kiwoom_api import KiwoomAPI
from models import Position, SellOrder, AutoTradeSettings, get_db
from config import Config

logger = logging.getLogger(__name__)

class StopLossManager:
    """손절/익절 모니터링 매니저"""
    
    def __init__(self):
        self.kiwoom_api = KiwoomAPI()
        self.is_running = False
        self.monitoring_interval = 30  # 30초마다 모니터링
        self.auto_trade_settings = None
        
    async def start_monitoring(self):
        """손절/익절 모니터링 시작"""
        logger.info("🛡️ [STOP_LOSS] 손절/익절 모니터링 시작")
        self.is_running = True
        
        try:
            while self.is_running:
                # 자동매매 설정 로드
                await self._load_auto_trade_settings()
                
                # 자동매매가 활성화된 경우에만 모니터링
                if self.auto_trade_settings and self.auto_trade_settings.is_enabled:
                    await self._monitor_positions()
                else:
                    logger.debug("🛡️ [STOP_LOSS] 자동매매 비활성화 상태 - 모니터링 건너뜀")
                
                await asyncio.sleep(self.monitoring_interval)
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 모니터링 중 오류: {e}")
        finally:
            logger.info("🛡️ [STOP_LOSS] 손절/익절 모니터링 종료")
    
    async def stop_monitoring(self):
        """손절/익절 모니터링 중지"""
        logger.info("🛡️ [STOP_LOSS] 손절/익절 모니터링 중지 요청")
        self.is_running = False
    
    async def _load_auto_trade_settings(self):
        """자동매매 설정 로드"""
        try:
            for db in get_db():
                session: Session = db
                settings = session.query(AutoTradeSettings).first()
                if settings:
                    self.auto_trade_settings = settings
                    logger.debug(f"🛡️ [STOP_LOSS] 자동매매 설정 로드: 활성화={settings.is_enabled}, 손절={settings.stop_loss_rate}%, 익절={settings.take_profit_rate}%")
                else:
                    logger.warning("🛡️ [STOP_LOSS] 자동매매 설정이 없습니다.")
                break
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 자동매매 설정 로드 오류: {e}")
    
    async def _monitor_positions(self):
        """포지션 모니터링"""
        try:
            # HOLDING 상태인 포지션들 조회
            positions = await self._get_active_positions()
            
            if not positions:
                logger.debug("🛡️ [STOP_LOSS] 모니터링할 포지션이 없습니다.")
                return
            
            logger.info(f"🛡️ [STOP_LOSS] {len(positions)}개 포지션 모니터링 중...")
            
            for position in positions:
                try:
                    await self._check_position_stop_loss(position)
                except Exception as e:
                    logger.error(f"🛡️ [STOP_LOSS] 포지션 모니터링 오류 (ID: {position.id}): {e}")
                
                # API 제한을 고려한 대기
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 포지션 모니터링 중 오류: {e}")
    
    async def _get_active_positions(self) -> List[Position]:
        """활성 포지션 조회 (실제 보유 종목과 대조)"""
        positions = []
        for db in get_db():
            try:
                session: Session = db
                db_positions = session.query(Position).filter(
                    Position.status == "HOLDING"
                ).all()
                
                # 실제 계좌 보유 종목 조회
                account_number = Config.KIWOOM_MOCK_ACCOUNT_NUMBER if Config.KIWOOM_USE_MOCK_ACCOUNT else Config.KIWOOM_ACCOUNT_NUMBER
                account_balance = await self.kiwoom_api.get_account_balance(account_number)
                
                # 실제 보유 종목 코드 목록
                actual_holdings = set()
                if account_balance and 'stk_acnt_evlt_prst' in account_balance:
                    for holding in account_balance['stk_acnt_evlt_prst']:
                        actual_holdings.add(holding.get('stk_cd', ''))
                    logger.debug(f"🛡️ [STOP_LOSS] 실제 보유 종목: {len(actual_holdings)}개 - {actual_holdings}")
                else:
                    logger.warning(f"🛡️ [STOP_LOSS] 실제 계좌 조회 실패 또는 보유 종목 없음")
                
                # DB 포지션 중 실제로 보유한 종목만 필터링
                verified_positions = []
                for pos in db_positions:
                    if pos.stock_code in actual_holdings:
                        verified_positions.append(pos)
                        logger.debug(f"🛡️ [STOP_LOSS] 포지션 검증 완료: {pos.stock_name}({pos.stock_code})")
                    else:
                        logger.warning(f"🛡️ [STOP_LOSS] ⚠️ 실제 보유하지 않은 포지션 발견: {pos.stock_name}({pos.stock_code}) - 모니터링 제외")
                
                positions = verified_positions
                break
            except Exception as e:
                logger.error(f"🛡️ [STOP_LOSS] 포지션 조회 오류: {e}")
                import traceback
                logger.error(f"🛡️ [STOP_LOSS] 스택 트레이스: {traceback.format_exc()}")
                continue
        
        return positions
    
    async def _check_position_stop_loss(self, position: Position):
        """개별 포지션 손절/익절 확인"""
        try:
            # 현재가 조회
            current_price = await self._get_current_price(position.stock_code)
            if not current_price:
                logger.warning(f"🛡️ [STOP_LOSS] 현재가 조회 실패 - {position.stock_name}")
                return
            
            # 손익 계산
            profit_loss = (current_price - position.buy_price) * position.buy_quantity
            profit_loss_rate = (current_price - position.buy_price) / position.buy_price * 100
            
            # 포지션 정보 업데이트
            await self._update_position_price(position.id, current_price, profit_loss, profit_loss_rate)
            
            # 손절/익절 확인
            should_sell = False
            sell_reason = ""
            sell_reason_detail = ""
            
            # 손절 확인
            if profit_loss_rate <= -self.auto_trade_settings.stop_loss_rate:
                should_sell = True
                sell_reason = "STOP_LOSS"
                sell_reason_detail = f"손절: {profit_loss_rate:.2f}% (기준: -{self.auto_trade_settings.stop_loss_rate}%)"
                logger.warning(f"🛡️ [STOP_LOSS] 손절 신호 - {position.stock_name}: {profit_loss_rate:.2f}%")
            
            # 익절 확인
            elif profit_loss_rate >= self.auto_trade_settings.take_profit_rate:
                should_sell = True
                sell_reason = "TAKE_PROFIT"
                sell_reason_detail = f"익절: {profit_loss_rate:.2f}% (기준: {self.auto_trade_settings.take_profit_rate}%)"
                logger.info(f"🛡️ [STOP_LOSS] 익절 신호 - {position.stock_name}: {profit_loss_rate:.2f}%")
            
            # 매도 실행
            if should_sell:
                await self._execute_sell_order(position, current_price, sell_reason, sell_reason_detail)
            
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 포지션 확인 오류 - {position.stock_name}: {e}")
    
    async def _get_current_price(self, stock_code: str) -> Optional[int]:
        """현재가 조회"""
        try:
            logger.debug(f"🛡️ [STOP_LOSS] 현재가 조회 시도: {stock_code}")
            current_price = await self.kiwoom_api.get_current_price(stock_code)
            if current_price:
                logger.debug(f"🛡️ [STOP_LOSS] 현재가 조회 성공: {stock_code} = {current_price:,}원")
            else:
                logger.warning(f"🛡️ [STOP_LOSS] 현재가 조회 반환값 None: {stock_code} (API 제한 또는 토큰 만료 가능성)")
            return current_price
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 현재가 조회 예외 발생: {stock_code} - {e}")
            import traceback
            logger.error(f"🛡️ [STOP_LOSS] 스택 트레이스: {traceback.format_exc()}")
            return None
    
    async def _update_position_price(self, position_id: int, current_price: int, profit_loss: int, profit_loss_rate: float):
        """포지션 현재가 및 손익 업데이트"""
        try:
            for db in get_db():
                session: Session = db
                position = session.query(Position).filter(Position.id == position_id).first()
                if position:
                    position.current_price = current_price
                    position.current_profit_loss = profit_loss
                    position.current_profit_loss_rate = profit_loss_rate
                    position.last_monitored = datetime.utcnow()
                    session.commit()
                    logger.debug(f"🛡️ [STOP_LOSS] 포지션 업데이트 - {position.stock_name}: {profit_loss_rate:.2f}%")
                break
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 포지션 업데이트 오류: {e}")
    
    async def _execute_sell_order(self, position: Position, sell_price: int, sell_reason: str, sell_reason_detail: str):
        """매도 주문 실행"""
        try:
            logger.info(f"🛡️ [STOP_LOSS] 매도 주문 실행 - {position.stock_name}: {sell_reason}")
            
            # 매도 주문 생성
            sell_order = await self._create_sell_order(position, sell_price, sell_reason, sell_reason_detail)
            
            # 키움 API로 매도 주문
            result = await self.kiwoom_api.place_sell_order(
                stock_code=position.stock_code,
                quantity=position.buy_quantity,
                price=0,  # 시장가
                order_type="3"  # 시장가
            )
            
            if result.get("success"):
                logger.info(f"🛡️ [STOP_LOSS] 매도 주문 성공 - {position.stock_name}: {position.buy_quantity}주")
                
                # 매도 주문 상태 업데이트
                await self._update_sell_order_status(sell_order.id, "ORDERED", result.get("order_id", ""))
                
                # 포지션 상태 업데이트
                await self._update_position_status(position.id, sell_reason, sell_price)
                
            else:
                error_msg = result.get("error", "알 수 없는 오류")
                logger.error(f"🛡️ [STOP_LOSS] 매도 주문 실패 - {position.stock_name}: {error_msg}")
                await self._update_sell_order_status(sell_order.id, "FAILED", error_msg)
                
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 매도 주문 실행 오류 - {position.stock_name}: {e}")
    
    async def _create_sell_order(self, position: Position, sell_price: int, sell_reason: str, sell_reason_detail: str) -> SellOrder:
        """매도 주문 생성"""
        try:
            sell_order = None
            for db in get_db():
                session: Session = db
                sell_order = SellOrder(
                    position_id=position.id,
                    stock_code=position.stock_code,
                    stock_name=position.stock_name,
                    sell_price=sell_price,
                    sell_quantity=position.buy_quantity,
                    sell_amount=sell_price * position.buy_quantity,
                    sell_reason=sell_reason,
                    sell_reason_detail=sell_reason_detail,
                    profit_loss=(sell_price - position.buy_price) * position.buy_quantity,
                    profit_loss_rate=(sell_price - position.buy_price) / position.buy_price * 100,
                    status="PENDING"
                )
                session.add(sell_order)
                session.commit()
                break
            
            return sell_order
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 매도 주문 생성 오류: {e}")
            raise
    
    async def _update_sell_order_status(self, sell_order_id: int, status: str, order_id: str = ""):
        """매도 주문 상태 업데이트"""
        try:
            for db in get_db():
                session: Session = db
                sell_order = session.query(SellOrder).filter(SellOrder.id == sell_order_id).first()
                if sell_order:
                    sell_order.status = status
                    if order_id:
                        sell_order.sell_order_id = order_id
                    if status == "ORDERED":
                        sell_order.ordered_at = datetime.utcnow()
                    elif status == "COMPLETED":
                        sell_order.completed_at = datetime.utcnow()
                    session.commit()
                break
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 매도 주문 상태 업데이트 오류: {e}")
    
    async def _update_position_status(self, position_id: int, status: str, sell_price: int):
        """포지션 상태 업데이트"""
        try:
            for db in get_db():
                session: Session = db
                position = session.query(Position).filter(Position.id == position_id).first()
                if position:
                    position.status = status
                    position.sell_time = datetime.utcnow()
                    session.commit()
                    logger.info(f"🛡️ [STOP_LOSS] 포지션 상태 업데이트 - {position.stock_name}: {status}")
                break
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 포지션 상태 업데이트 오류: {e}")
    
    async def create_position_from_buy_signal(self, signal_id: int, buy_price: int, buy_quantity: int, buy_order_id: str = ""):
        """매수 신호로부터 포지션 생성"""
        try:
            # 매수 신호 정보 조회
            signal = None
            for db in get_db():
                session: Session = db
                from models import PendingBuySignal
                signal = session.query(PendingBuySignal).filter(PendingBuySignal.id == signal_id).first()
                if signal:
                    # 포지션 생성
                    position = Position(
                        stock_code=signal.stock_code,
                        stock_name=signal.stock_name,
                        buy_price=buy_price,
                        buy_quantity=buy_quantity,
                        buy_amount=buy_price * buy_quantity,
                        buy_order_id=buy_order_id,
                        stop_loss_rate=self.auto_trade_settings.stop_loss_rate if self.auto_trade_settings else 5.0,
                        take_profit_rate=self.auto_trade_settings.take_profit_rate if self.auto_trade_settings else 10.0,
                        condition_id=signal.condition_id,
                        signal_id=signal.id,
                        status="HOLDING"
                    )
                    session.add(position)
                    session.commit()
                    
                    logger.info(f"🛡️ [STOP_LOSS] 포지션 생성 - {signal.stock_name}: {buy_quantity}주 @ {buy_price:,}원")
                    break
            
            return position
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 포지션 생성 오류: {e}")
            raise
    
    async def get_monitoring_status(self) -> Dict:
        """모니터링 상태 조회"""
        try:
            # 활성 포지션 수 조회
            active_positions = await self._get_active_positions()
            
            # 최근 매도 주문 조회
            recent_sell_orders = []
            for db in get_db():
                session: Session = db
                recent_sell_orders = session.query(SellOrder).order_by(
                    SellOrder.created_at.desc()
                ).limit(10).all()
                break
            
            status = {
                "is_running": self.is_running,
                "monitoring_interval": self.monitoring_interval,
                "auto_trade_settings_loaded": self.auto_trade_settings is not None,
                "auto_trade_enabled": self.auto_trade_settings.is_enabled if self.auto_trade_settings else False,
                "stop_loss_rate": self.auto_trade_settings.stop_loss_rate if self.auto_trade_settings else 0,
                "take_profit_rate": self.auto_trade_settings.take_profit_rate if self.auto_trade_settings else 0,
                "active_positions_count": len(active_positions),
                "recent_sell_orders": [
                    {
                        "id": order.id,
                        "stock_name": order.stock_name,
                        "sell_reason": order.sell_reason,
                        "profit_loss_rate": order.profit_loss_rate,
                        "created_at": order.created_at.isoformat() if order.created_at else None,
                        "status": order.status
                    }
                    for order in recent_sell_orders
                ]
            }
            
            return status
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 모니터링 상태 조회 오류: {e}")
            return {"error": str(e)}