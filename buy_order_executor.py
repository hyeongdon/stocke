import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from enum import Enum
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from kiwoom_api import KiwoomAPI
from models import PendingBuySignal, get_db, AutoTradeCondition

logger = logging.getLogger(__name__)

class SignalType(Enum):
    """신호 타입 정의"""
    CONDITION_SIGNAL = "condition"  # 조건식 신호
    REFERENCE_CANDLE = "reference"  # 기준봉 신호

class BuyOrderExecutor:
    """매수 주문 실행기 - 별도 프로세스에서 매수 주문 처리"""
    
    def __init__(self):
        self.kiwoom_api = KiwoomAPI()
        self.is_running = False
        self.max_invest_amount = 100000  # 기본 최대 투자 금액 (10만원)
        self.max_retry_attempts = 3  # 최대 재시도 횟수
        self.retry_delay_seconds = 30  # 재시도 간격 (초)
        
    async def start_processing(self):
        """매수 주문 처리 시작"""
        logger.info("💰 [BUY_EXECUTOR] 매수 주문 처리기 시작")
        self.is_running = True
        
        try:
            while self.is_running:
                await self._process_pending_signals()
                await asyncio.sleep(10)  # 10초마다 확인
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 처리 중 오류: {e}")
        finally:
            logger.info("💰 [BUY_EXECUTOR] 매수 주문 처리기 종료")
    
    async def stop_processing(self):
        """매수 주문 처리 중지"""
        logger.info("💰 [BUY_EXECUTOR] 매수 주문 처리기 중지 요청")
        self.is_running = False
    
    async def _process_pending_signals(self):
        """대기 중인 매수 신호들 처리"""
        try:
            # PENDING 상태인 신호들 조회
            pending_signals = await self._get_pending_signals()
            
            if not pending_signals:
                return
            
            logger.info(f"💰 [BUY_EXECUTOR] 처리할 신호 {len(pending_signals)}개 발견")
            
            for signal in pending_signals:
                try:
                    await self._process_single_signal(signal)
                except Exception as e:
                    logger.error(f"💰 [BUY_EXECUTOR] 신호 처리 오류 (ID: {signal.id}): {e}")
                    await self._update_signal_status(signal.id, "FAILED", str(e))
                
                # API 제한을 고려한 대기
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 대기 신호 처리 중 오류: {e}")
    
    async def _get_pending_signals(self) -> List[PendingBuySignal]:
        """PENDING 상태인 신호들 조회"""
        signals = []
        for db in get_db():
            try:
                session: Session = db
                signals = session.query(PendingBuySignal).filter(
                    PendingBuySignal.status == "PENDING"
                ).order_by(PendingBuySignal.detected_at.asc()).all()
                break
            except Exception as e:
                logger.error(f"💰 [BUY_EXECUTOR] 신호 조회 오류: {e}")
                continue
        
        return signals
    
    async def _process_single_signal(self, signal: PendingBuySignal):
        """단일 신호 처리"""
        logger.info(f"💰 [BUY_EXECUTOR] 신호 처리 시작 - {signal.stock_name}({signal.stock_code})")
        
        try:
            # 1. 매수 전 검증
            validation_result = await self._validate_buy_conditions(signal)
            if not validation_result["valid"]:
                logger.warning(f"💰 [BUY_EXECUTOR] 매수 조건 미충족 - {signal.stock_name}: {validation_result['reason']}")
                await self._update_signal_status(signal.id, "FAILED", validation_result["reason"])
                return
            
            # 2. 현재가 조회
            current_price = await self._get_current_price(signal.stock_code)
            if not current_price:
                logger.error(f"💰 [BUY_EXECUTOR] 현재가 조회 실패 - {signal.stock_name}")
                await self._update_signal_status(signal.id, "FAILED", "현재가 조회 실패")
                return
            
            # 3. 매수 수량 계산
            quantity = await self._calculate_buy_quantity(signal.stock_code, current_price)
            if quantity < 1:
                logger.warning(f"💰 [BUY_EXECUTOR] 매수 수량 부족 - {signal.stock_name}: {quantity}")
                await self._update_signal_status(signal.id, "FAILED", f"매수 수량 부족: {quantity}")
                return
            
            # 4. 매수 주문 실행 (재시도 포함)
            await self._execute_buy_order_with_retry(signal, current_price, quantity)
            
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 신호 처리 중 오류 - {signal.stock_name}: {e}")
            await self._update_signal_status(signal.id, "FAILED", str(e))
    
    async def _validate_buy_conditions(self, signal: PendingBuySignal) -> Dict:
        """매수 전 검증"""
        try:
            # 1. 시장 시간 확인
            now = datetime.now()
            if not self._is_market_open(now):
                return {"valid": False, "reason": "시장 시간이 아님"}
            
            # 2. 계좌 잔고 확인
            account_info = await self._get_account_info()
            if not account_info:
                return {"valid": False, "reason": "계좌 정보 조회 실패"}
            
            available_cash = account_info.get("available_cash", 0)
            if available_cash < self.max_invest_amount:
                return {"valid": False, "reason": f"잔고 부족: {available_cash:,}원"}
            
            # 3. 종목 상태 확인 (상장폐지, 거래정지 등)
            stock_status = await self._check_stock_status(signal.stock_code)
            if not stock_status["tradeable"]:
                return {"valid": False, "reason": f"거래 불가 종목: {stock_status['reason']}"}
            
            # 4. 중복 주문 확인
            if await self._has_pending_order(signal.stock_code):
                return {"valid": False, "reason": "이미 대기 중인 주문 존재"}
            
            return {"valid": True, "reason": "검증 통과"}
            
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 매수 조건 검증 오류: {e}")
            return {"valid": False, "reason": f"검증 오류: {e}"}
    
    def _is_market_open(self, now: datetime) -> bool:
        """시장 시간 확인 (평일 09:00-15:30)"""
        if now.weekday() >= 5:  # 주말
            return False
        
        market_start = now.replace(hour=9, minute=0, second=0, microsecond=0)
        market_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
        
        return market_start <= now <= market_end
    
    async def _get_account_info(self) -> Optional[Dict]:
        """계좌 정보 조회"""
        try:
            # 키움 API로 계좌 정보 조회
            account_info = await self.kiwoom_api.get_account_balance()
            return account_info
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 계좌 정보 조회 오류: {e}")
            return None
    
    async def _check_stock_status(self, stock_code: str) -> Dict:
        """종목 상태 확인"""
        try:
            # 키움 API로 종목 상태 조회
            stock_info = await self.kiwoom_api.get_stock_info(stock_code)
            
            if not stock_info:
                return {"tradeable": False, "reason": "종목 정보 없음"}
            
            # 거래정지, 상장폐지 등 확인
            if stock_info.get("status") == "SUSPENDED":
                return {"tradeable": False, "reason": "거래정지"}
            
            return {"tradeable": True, "reason": "정상"}
            
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 종목 상태 확인 오류: {e}")
            return {"tradeable": False, "reason": f"확인 오류: {e}"}
    
    async def _has_pending_order(self, stock_code: str) -> bool:
        """대기 중인 주문 확인"""
        try:
            for db in get_db():
                session: Session = db
                pending_order = session.query(PendingBuySignal).filter(
                    PendingBuySignal.stock_code == stock_code,
                    PendingBuySignal.status.in_(["PENDING", "ORDERED"])
                ).first()
                
                if pending_order:
                    return True
                break
            
            return False
            
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 대기 주문 확인 오류: {e}")
            return False
    
    async def _get_current_price(self, stock_code: str) -> Optional[int]:
        """현재가 조회"""
        try:
            # 키움 API로 현재가 조회
            current_price = await self.kiwoom_api.get_current_price(stock_code)
            return current_price
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 현재가 조회 오류: {e}")
            return None
    
    async def _calculate_buy_quantity(self, stock_code: str, current_price: int) -> int:
        """매수 수량 계산"""
        try:
            # 최대 투자 금액 내에서 수량 계산
            quantity = self.max_invest_amount // current_price
            
            # 최소 수량 확인 (1주 이상)
            if quantity < 1:
                return 0
            
            # 최대 수량 제한 (1000주)
            if quantity > 1000:
                quantity = 1000
            
            return quantity
            
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 수량 계산 오류: {e}")
            return 0
    
    async def _execute_buy_order_with_retry(self, signal: PendingBuySignal, current_price: int, quantity: int):
        """재시도 포함 매수 주문 실행"""
        for attempt in range(self.max_retry_attempts):
            try:
                logger.info(f"💰 [BUY_EXECUTOR] 매수 주문 시도 {attempt + 1}/{self.max_retry_attempts} - {signal.stock_name}")
                
                # 키움 API로 매수 주문
                result = await self.kiwoom_api.place_buy_order(
                    stock_code=signal.stock_code,
                    quantity=quantity,
                    price=0,  # 시장가
                    order_type="3"  # 시장가
                )
                
                if result.get("success"):
                    logger.info(f"💰 [BUY_EXECUTOR] 매수 주문 성공 - {signal.stock_name}: {quantity}주")
                    await self._update_signal_status(signal.id, "ORDERED", result.get("order_id", ""))
                    return
                else:
                    error_msg = result.get("error", "알 수 없는 오류")
                    logger.warning(f"💰 [BUY_EXECUTOR] 매수 주문 실패 (시도 {attempt + 1}): {error_msg}")
                    
                    if attempt < self.max_retry_attempts - 1:
                        logger.info(f"💰 [BUY_EXECUTOR] {self.retry_delay_seconds}초 후 재시도")
                        await asyncio.sleep(self.retry_delay_seconds)
                    else:
                        await self._update_signal_status(signal.id, "FAILED", error_msg)
                        
            except Exception as e:
                logger.error(f"💰 [BUY_EXECUTOR] 매수 주문 실행 오류 (시도 {attempt + 1}): {e}")
                
                if attempt < self.max_retry_attempts - 1:
                    await asyncio.sleep(self.retry_delay_seconds)
                else:
                    await self._update_signal_status(signal.id, "FAILED", str(e))
    
    async def _update_signal_status(self, signal_id: int, status: str, reason: str = "", order_id: str = ""):
        """신호 상태 업데이트 (실패 사유 포함)"""
        try:
            for db in get_db():
                session: Session = db
                signal = session.query(PendingBuySignal).filter(PendingBuySignal.id == signal_id).first()
                if signal:
                    signal.status = status
                    if reason and status == "FAILED":
                        signal.failure_reason = reason[:255]
                    if order_id:
                        # 주문 ID 저장 (필드가 있다면)
                        pass
                    session.commit()
                    if reason:
                        logger.info(f"💰 [BUY_EXECUTOR] 신호 상태 변경: ID {signal_id} -> {status}, reason={reason}")
                    else:
                        logger.info(f"💰 [BUY_EXECUTOR] 신호 상태 변경: ID {signal_id} -> {status}")
                break
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 신호 상태 업데이트 오류: {e}")

# 전역 인스턴스
buy_order_executor = BuyOrderExecutor()
