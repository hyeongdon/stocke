import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from sqlalchemy.orm import Session

from kiwoom_api import KiwoomAPI
from models import PendingBuySignal, get_db, AutoTradeCondition, AutoTradeSettings
from stop_loss_manager import StopLossManager
from config import Config

logger = logging.getLogger(__name__)

class BuyOrderExecutor:
    """매수 주문 실행기 - 별도 프로세스에서 매수 주문 처리"""
    
    def __init__(self):
        self.kiwoom_api = KiwoomAPI()
        self.is_running = False
        self.max_retry_attempts = 3  # 최대 재시도 횟수
        self.retry_delay_seconds = 30  # 재시도 간격 (초)
        
        # 자동매매 설정 (DB에서 동적으로 로드)
        self.auto_trade_settings = None
        
        # 손절/익절 모니터링 매니저
        self.stop_loss_manager = StopLossManager()
        
    async def start_processing(self):
        """매수 주문 처리 시작"""
        logger.info("💰 [BUY_EXECUTOR] 매수 주문 처리기 시작")
        self.is_running = True
        
        try:
            while self.is_running:
                # 자동매매 설정 로드
                await self._load_auto_trade_settings()
                
                # 자동매매가 활성화된 경우에만 처리
                if self.auto_trade_settings and self.auto_trade_settings.is_enabled:
                    await self._process_pending_signals()
                else:
                    logger.debug("💰 [BUY_EXECUTOR] 자동매매 비활성화 상태 - 신호 처리 건너뜀")
                
                await asyncio.sleep(10)  # 10초마다 확인
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 처리 중 오류: {e}")
        finally:
            logger.info("💰 [BUY_EXECUTOR] 매수 주문 처리기 종료")
    
    async def stop_processing(self):
        """매수 주문 처리 중지"""
        logger.info("💰 [BUY_EXECUTOR] 매수 주문 처리기 중지 요청")
        self.is_running = False
    
    async def _load_auto_trade_settings(self):
        """자동매매 설정 로드"""
        try:
            for db in get_db():
                session: Session = db
                settings = session.query(AutoTradeSettings).first()
                if settings:
                    self.auto_trade_settings = settings
                    logger.debug(f"💰 [BUY_EXECUTOR] 자동매매 설정 로드: 활성화={settings.is_enabled}, 최대투자={settings.max_invest_amount:,}원, 손절={settings.stop_loss_rate}%, 익절={settings.take_profit_rate}%")
                else:
                    logger.warning("💰 [BUY_EXECUTOR] 자동매매 설정이 없습니다.")
                break
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 자동매매 설정 로드 오류: {e}")
    
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
            # 처리 중 상태로 먼저 변경 (자기 자신을 '대기 주문'으로 인식하는 문제 방지)
            await self._update_signal_status(signal.id, "PROCESSING")

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
                # 모의투자(또는 옵션)에서는 테스트 목적상 장시간 체크를 우회 가능하게 함
                allow_out_of_hours = getattr(Config, "ALLOW_OUT_OF_MARKET_TRADING", False) or Config.KIWOOM_USE_MOCK_ACCOUNT
                if not allow_out_of_hours:
                    return {"valid": False, "reason": "시장 시간이 아님"}
                logger.warning("💰 [BUY_EXECUTOR] 시장 시간이 아니지만(모의투자/옵션) 테스트 목적으로 진행합니다")
            
            # 2. 계좌 잔고 확인
            account_info = await self._get_account_info()
            if not account_info:
                return {"valid": False, "reason": "계좌 정보 조회 실패"}
            
            available_cash = account_info.get("available_cash", 0)
            max_invest_amount = self.auto_trade_settings.max_invest_amount if self.auto_trade_settings else 100000
            if available_cash < max_invest_amount:
                return {"valid": False, "reason": f"잔고 부족: {available_cash:,}원 (필요: {max_invest_amount:,}원)"}
            
            # 3. 종목 상태 확인 (상장폐지, 거래정지 등)
            stock_status = await self._check_stock_status(signal.stock_code)
            if not stock_status["tradeable"]:
                return {"valid": False, "reason": f"거래 불가 종목: {stock_status['reason']}"}
            
            # 4. 중복 주문 확인
            if await self._has_pending_order(signal.stock_code, exclude_signal_id=signal.id):
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
            # 키움 API로 계좌 정보 조회 (실전/모의 계좌번호 자동 선택)
            account_number = Config.KIWOOM_MOCK_ACCOUNT_NUMBER if Config.KIWOOM_USE_MOCK_ACCOUNT else Config.KIWOOM_ACCOUNT_NUMBER
            if not account_number:
                logger.error("💰 [BUY_EXECUTOR] 계좌번호가 설정되지 않았습니다 (KIWOOM_ACCOUNT_NUMBER / KIWOOM_MOCK_ACCOUNT_NUMBER)")
                return None

            raw = await self.kiwoom_api.get_account_balance(account_number)
            if not raw:
                return None

            def _to_int(v) -> int:
                try:
                    if v is None:
                        return 0
                    if isinstance(v, (int, float)):
                        return int(v)
                    s = str(v).strip().replace(",", "")
                    if s.startswith("+"):
                        s = s[1:]
                    if s == "":
                        return 0
                    return int(float(s))
                except Exception:
                    return 0

            # KiwoomAPI.get_account_balance 파싱 결과는 entr / d2_entra 등을 포함
            available_cash = _to_int(raw.get("entr") or raw.get("d2_entra") or 0)
            return {
                "available_cash": available_cash,
                "raw": raw,
            }
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 계좌 정보 조회 오류: {e}")
            return None
    
    async def _check_stock_status(self, stock_code: str) -> Dict:
        """종목 상태 확인"""
        try:
            # 기존 구현은 get_stock_info()를 호출했는데 KiwoomAPI에 해당 메서드가 없어 항상 실패했음.
            # 최소 검증으로 현재가 조회 성공 여부로 거래 가능 여부를 판단한다.
            current_price = await self.kiwoom_api.get_current_price(stock_code)
            if not current_price or current_price <= 0:
                return {"tradeable": False, "reason": "현재가 조회 실패/0원"}
            return {"tradeable": True, "reason": "정상(현재가 조회 성공)"}
            
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 종목 상태 확인 오류: {e}")
            # 상태 확인 자체 오류는 거래불가로 만들면 '영원히 매수 안 됨'이 될 수 있어 보수적으로 통과 처리
            return {"tradeable": True, "reason": f"상태 확인 스킵(오류): {e}"}
    
    async def _has_pending_order(self, stock_code: str, exclude_signal_id: Optional[int] = None) -> bool:
        """대기 중인 주문 확인"""
        try:
            for db in get_db():
                session: Session = db
                q = session.query(PendingBuySignal).filter(
                    PendingBuySignal.stock_code == stock_code,
                    PendingBuySignal.status.in_(["PENDING", "ORDERED"])
                )
                if exclude_signal_id is not None:
                    q = q.filter(PendingBuySignal.id != exclude_signal_id)
                pending_order = q.first()
                
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
        """매수 수량 계산 (자동매매 설정 사용)"""
        try:
            if not self.auto_trade_settings:
                logger.error("💰 [BUY_EXECUTOR] 자동매매 설정이 없습니다.")
                return 0
            
            # 자동매매 설정의 최대 투자 금액 사용
            max_invest_amount = self.auto_trade_settings.max_invest_amount
            quantity = max_invest_amount // current_price
            
            # 최소 수량 확인 (1주 이상)
            if quantity < 1:
                return 0
            
            # 최대 수량 제한 (1000주)
            if quantity > 1000:
                quantity = 1000
            
            logger.info(f"💰 [BUY_EXECUTOR] 매수 수량 계산: {quantity}주 (최대투자={max_invest_amount:,}원, 현재가={current_price:,}원)")
            return quantity
            
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 매수 수량 계산 오류: {e}")
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
                    order_type="3"  # 시장가 (kt10000 스펙)
                )
                
                if result.get("success"):
                    logger.info(f"💰 [BUY_EXECUTOR] 매수 주문 성공 - {signal.stock_name}: {quantity}주")
                    order_id = result.get("order_id", "")
                    await self._update_signal_status(signal.id, "ORDERED", "", order_id)
                    
                    # 포지션 생성 (손절/익절 모니터링용)
                    try:
                        await self.stop_loss_manager.create_position_from_buy_signal(
                            signal_id=signal.id,
                            buy_price=current_price,
                            buy_quantity=quantity,
                            buy_order_id=order_id
                        )
                        logger.info(f"💰 [BUY_EXECUTOR] 포지션 생성 완료 - {signal.stock_name}")
                    except Exception as e:
                        logger.error(f"💰 [BUY_EXECUTOR] 포지션 생성 실패 - {signal.stock_name}: {e}")
                    
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
    
    async def cleanup_expired_pending_signals(self):
        """만료된 PENDING 신호들 정리 (자정에 실행)"""
        try:
            logger.info("🧹 [BUY_EXECUTOR] 만료된 PENDING 신호 정리 시작")
            
            # 어제 날짜 계산
            yesterday = datetime.now().date() - timedelta(days=1)
            
            cleanup_count = 0
            for db in get_db():
                session: Session = db
                try:
                    # 어제 이전의 PENDING 신호들 조회
                    expired_signals = session.query(PendingBuySignal).filter(
                        PendingBuySignal.status == "PENDING",
                        PendingBuySignal.detected_date < yesterday
                    ).all()
                    
                    for signal in expired_signals:
                        signal.status = "EXPIRED"
                        signal.failure_reason = "자정 정리 - 장마감 후 미체결"
                        cleanup_count += 1
                        logger.info(f"🧹 [BUY_EXECUTOR] 만료 신호 정리: {signal.stock_name}({signal.stock_code}) - {signal.detected_at}")
                    
                    session.commit()
                    logger.info(f"🧹 [BUY_EXECUTOR] 총 {cleanup_count}개 만료 신호 정리 완료")
                    break
                    
                except Exception as e:
                    logger.error(f"🧹 [BUY_EXECUTOR] 만료 신호 정리 중 오류: {e}")
                    session.rollback()
                    continue
                    
        except Exception as e:
            logger.error(f"🧹 [BUY_EXECUTOR] 만료 신호 정리 중 전체 오류: {e}")
    
    async def manual_cleanup_pending_signals(self):
        """수동으로 PENDING 신호들 정리"""
        try:
            logger.info("🧹 [BUY_EXECUTOR] 수동 PENDING 신호 정리 시작")
            
            cleanup_count = 0
            for db in get_db():
                session: Session = db
                try:
                    # 모든 PENDING 신호들 조회
                    pending_signals = session.query(PendingBuySignal).filter(
                        PendingBuySignal.status == "PENDING"
                    ).all()
                    
                    for signal in pending_signals:
                        signal.status = "MANUAL_CLEANUP"
                        signal.failure_reason = "수동 정리 - 사용자 요청"
                        cleanup_count += 1
                        logger.info(f"🧹 [BUY_EXECUTOR] 수동 정리: {signal.stock_name}({signal.stock_code}) - {signal.detected_at}")
                    
                    session.commit()
                    logger.info(f"🧹 [BUY_EXECUTOR] 총 {cleanup_count}개 PENDING 신호 수동 정리 완료")
                    break
                    
                except Exception as e:
                    logger.error(f"🧹 [BUY_EXECUTOR] 수동 정리 중 오류: {e}")
                    session.rollback()
                    continue
                    
            return cleanup_count
                    
        except Exception as e:
            logger.error(f"🧹 [BUY_EXECUTOR] 수동 정리 중 전체 오류: {e}")
            return 0

# 전역 인스턴스
buy_order_executor = BuyOrderExecutor()
