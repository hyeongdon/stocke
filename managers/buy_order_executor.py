import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from sqlalchemy.orm import Session

from api.kiwoom_api import KiwoomAPI
from core.models import PendingBuySignal, get_db, AutoTradeCondition, AutoTradeSettings, Position
from managers.stop_loss_manager import stop_loss_manager
from core.config import Config
from utils.debug_tracer import debug_tracer
from utils.auto_trade_engine import (
    auto_trade_engines_allowed,
    cap_buy_amount_by_cash,
    cash_reserve_pct,
    check_daily_limits,
    check_entry_gate,
    compute_buy_amount,
    compute_investable_cash,
    compute_quantity,
    count_open_position_slots,
    effective_min_change_rate,
    has_buy_conditions,
    allows_new_buy,
    new_buy_block_reason,
    is_max_concurrent_positions_reached,
    max_concurrent_positions_limit,
    order_params,
    parse_signal_meta,
    passes_buy_price_conditions,
)
from utils.auto_trade_activity_log import log_activity
from notifications.trade_alert import notify_buy_async

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
        
        # 손절/익절 모니터링 (전역 싱글톤)
        self.stop_loss_manager = stop_loss_manager
        
    async def start_processing(self):
        """매수 주문 처리 시작"""
        logger.info("💰 [BUY_EXECUTOR] 매수 주문 처리기 시작")
        log_activity("BUY", "매수 실행기 시작 (60초 주기)", "info")
        self.is_running = True
        
        try:
            while self.is_running:
                # 자동매매 설정 로드
                await self._load_auto_trade_settings()
                
                # 자동매매가 활성화된 경우에만 처리
                if self.auto_trade_settings and self.auto_trade_settings.is_enabled:
                    allowed, off_reason = auto_trade_engines_allowed()
                    if not allowed:
                        logger.debug(f"💰 [BUY_EXECUTOR] {off_reason} — 신호 처리 건너뜀")
                    else:
                        await self._process_pending_signals()
                else:
                    logger.debug("💰 [BUY_EXECUTOR] 자동매매 비활성화 상태 - 신호 처리 건너뜀")
                
                await asyncio.sleep(60)  # 60초마다 확인 (API 제한 고려)
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 처리 중 오류: {e}")
        finally:
            logger.info("💰 [BUY_EXECUTOR] 매수 주문 처리기 종료")
    
    async def stop_processing(self):
        """매수 주문 처리 중지"""
        logger.info("💰 [BUY_EXECUTOR] 매수 주문 처리기 중지 요청")
        log_activity("BUY", "매수 실행기 중지", "warn")
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
    
    @debug_tracer.trace_async(component="BUY_EXECUTOR")
    async def _process_pending_signals(self):
        """대기 중인 매수 신호들 처리"""
        try:
            debug_tracer.log_checkpoint("PENDING 신호 조회 시작", "BUY_EXECUTOR")
            
            # PENDING 상태인 신호들 조회
            pending_signals = await self._get_pending_signals()
            
            debug_tracer.log_checkpoint(f"조회된 신호 개수: {len(pending_signals)}", "BUY_EXECUTOR")
            
            if not pending_signals:
                return
            
            logger.info(f"💰 [BUY_EXECUTOR] 처리할 신호 {len(pending_signals)}개 발견")
            log_activity("BUY", f"대기 신호 {len(pending_signals)}건 처리 시작", "info")
            
            for idx, signal in enumerate(pending_signals, 1):
                try:
                    debug_tracer.log_checkpoint(f"[{idx}/{len(pending_signals)}] 신호 처리 시작: {signal.stock_name}({signal.stock_code})", "BUY_EXECUTOR")
                    await self._process_single_signal(signal)
                except Exception as e:
                    logger.error(f"💰 [BUY_EXECUTOR] 신호 처리 오류 (ID: {signal.id}): {e}")
                    await self._update_signal_status(signal.id, "FAILED", str(e))
                
                # API 제한을 고려한 대기 (키움 제한: 1분당 20회)
                debug_tracer.log_checkpoint(f"[{idx}/{len(pending_signals)}] 신호 처리 완료, 5초 대기", "BUY_EXECUTOR")
                await asyncio.sleep(5)
                
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
    
    @debug_tracer.trace_async(component="BUY_EXECUTOR")
    async def _process_single_signal(self, signal: PendingBuySignal):
        """단일 신호 처리"""
        logger.info(f"💰 [BUY_EXECUTOR] 신호 처리 시작 - {signal.stock_name}({signal.stock_code})")
        
        try:
            # 처리 중 상태로 먼저 변경 (자기 자신을 '대기 주문'으로 인식하는 문제 방지)
            debug_tracer.log_checkpoint("상태 변경: PROCESSING", "BUY_EXECUTOR")
            await self._update_signal_status(signal.id, "PROCESSING")

            # 1. 매수 전 검증
            debug_tracer.log_checkpoint("1단계: 매수 전 검증 시작", "BUY_EXECUTOR")
            validation_result = await self._validate_buy_conditions(signal)
            debug_tracer.log_checkpoint(f"1단계 결과: {validation_result}", "BUY_EXECUTOR")
            
            if not validation_result["valid"]:
                reason = validation_result["reason"]
                logger.warning(f"💰 [BUY_EXECUTOR] 매수 조건 미충족 - {signal.stock_name}: {reason}")
                log_activity("BUY", f"검증 실패 {signal.stock_name}: {reason}", "warn",
                             stock_code=signal.stock_code)
                await self._update_signal_status(signal.id, "FAILED", reason)
                return
            
            # 2. 현재가 조회
            debug_tracer.log_checkpoint("2단계: 현재가 조회 시작", "BUY_EXECUTOR")
            current_price = await self._get_current_price(signal.stock_code)
            debug_tracer.log_checkpoint(f"2단계 결과: 현재가={current_price:,}원" if current_price else "2단계 결과: 실패", "BUY_EXECUTOR")
            
            if not current_price:
                logger.error(f"💰 [BUY_EXECUTOR] 현재가 조회 실패 - {signal.stock_name}")
                await self._update_signal_status(signal.id, "FAILED", "현재가 조회 실패")
                return

            meta = parse_signal_meta(signal)
            is_add_buy = bool(meta.get("is_add_buy"))
            if self.auto_trade_settings and not is_add_buy and self.auto_trade_settings.use_entry_gate:
                gate_ok, gate_reason = await check_entry_gate(
                    self.kiwoom_api,
                    self.auto_trade_settings,
                    signal.stock_code,
                    current_price,
                )
                if not gate_ok:
                    reason = f"진입 게이트: {gate_reason}"
                    logger.warning(f"💰 [BUY_EXECUTOR] 주문 직전 게이트 실패 - {signal.stock_name}: {reason}")
                    log_activity(
                        "BUY",
                        f"게이트 실패 {signal.stock_name}: {gate_reason}",
                        "warn",
                        stock_code=signal.stock_code,
                    )
                    await self._update_signal_status(signal.id, "FAILED", reason)
                    return
            
            # 3. 매수 수량 계산
            debug_tracer.log_checkpoint("3단계: 매수 수량 계산 시작", "BUY_EXECUTOR")
            quantity = await self._calculate_buy_quantity(
                signal.stock_code,
                current_price,
                change_rate=meta.get("change_rate"),
                is_add_buy=bool(meta.get("is_add_buy")),
            )
            debug_tracer.log_checkpoint(f"3단계 결과: 수량={quantity}주, 총액={current_price*quantity:,}원", "BUY_EXECUTOR")
            
            if quantity < 1:
                logger.warning(f"💰 [BUY_EXECUTOR] 매수 수량 부족 - {signal.stock_name}: {quantity}")
                await self._update_signal_status(signal.id, "FAILED", f"매수 수량 부족: {quantity}")
                return
            
            # 4. 매수 주문 실행 (재시도 포함)
            debug_tracer.log_checkpoint(f"4단계: 매수 주문 실행 (가격={current_price:,}원, 수량={quantity}주)", "BUY_EXECUTOR")
            await self._execute_buy_order_with_retry(signal, current_price, quantity)
            debug_tracer.log_checkpoint("4단계 완료: 매수 주문 성공", "BUY_EXECUTOR")
            
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 신호 처리 중 오류 - {signal.stock_name}: {e}")
            await self._update_signal_status(signal.id, "FAILED", str(e))
    
    async def _validate_buy_conditions(self, signal: PendingBuySignal) -> Dict:
        """매수 전 검증"""
        try:
            # 1. 시장 시간·장마감 청산 이후 매수 차단
            now = datetime.now()
            block = new_buy_block_reason(self.auto_trade_settings, now)
            if block:
                return {"valid": False, "reason": block}

            # 1b. 일일 손익 한도
            if self.auto_trade_settings:
                halt = check_daily_limits(self.auto_trade_settings)
                if halt:
                    return {"valid": False, "reason": halt}
            
            # 2. 계좌 잔고 확인
            account_info = await self._get_account_info()
            if not account_info:
                return {"valid": False, "reason": "계좌 정보 조회 실패"}

            meta = parse_signal_meta(signal)
            is_add_buy = bool(meta.get("is_add_buy"))

            # 1c. 최대 동시 보유 (신규 매수만 — 대기 신호 슬롯 포함)
            if self.auto_trade_settings and not is_add_buy:
                limit = max_concurrent_positions_limit(self.auto_trade_settings)
                if limit > 0:
                    for db in get_db():
                        session: Session = db
                        from utils.auto_trade_engine import (
                            count_open_position_slots,
                            prune_stale_buy_slot_reservations,
                        )
                        if prune_stale_buy_slot_reservations(session):
                            session.commit()
                        if is_max_concurrent_positions_reached(
                            self.auto_trade_settings, session, for_new_signal=False,
                        ):
                            slots = count_open_position_slots(session)
                            return {
                                "valid": False,
                                "reason": (
                                    f"최대 동시 보유 {limit}종목 초과 "
                                    f"(슬롯 {slots}: 보유+대기 신호)"
                                ),
                            }
                        break

            investable = account_info.get("investable_cash", 0)
            deposit = account_info.get("deposit", 0)
            reserve = account_info.get("cash_reserve", 0)
            pct = cash_reserve_pct(self.auto_trade_settings) if self.auto_trade_settings else 10.0

            if investable <= 0:
                return {
                    "valid": False,
                    "reason": (
                        f"현금 보유 {pct:.0f}% 유지 — 매수 가능 0원 "
                        f"(예수금 {deposit:,}원, 보유 {reserve:,}원)"
                    ),
                }

            if self.auto_trade_settings:
                planned = compute_buy_amount(
                    self.auto_trade_settings,
                    meta.get("change_rate"),
                    is_add_buy,
                )
                planned = cap_buy_amount_by_cash(planned, investable)
                if planned <= 0:
                    return {
                        "valid": False,
                        "reason": (
                            f"현금 보유 {pct:.0f}% 적용 후 매수 가능 금액 부족 "
                            f"(가능 {investable:,}원, 예수금 {deposit:,}원)"
                        ),
                    }
            
            # 3. 종목 상태 확인 (상장폐지, 거래정지 등)
            stock_status = await self._check_stock_status(signal.stock_code)
            if not stock_status["tradeable"]:
                return {"valid": False, "reason": f"거래 불가 종목: {stock_status['reason']}"}
            
            # 4. 중복 주문 확인
            if await self._has_pending_order(signal.stock_code, exclude_signal_id=signal.id):
                return {"valid": False, "reason": "이미 대기 중인 주문 존재"}

            # 5. 대시보드 매수 조건 (가격/등락률) — 추가매수는 수익률 트리거로 이미 검증됨
            if is_add_buy:
                holding = False
                for db in get_db():
                    holding = db.query(Position).filter(
                        Position.stock_code == signal.stock_code,
                        Position.status == "HOLDING",
                    ).first() is not None
                    break
                if not holding:
                    return {"valid": False, "reason": "추가매수 대상 포지션 없음"}

            if self.auto_trade_settings and not is_add_buy:
                cfg = self.auto_trade_settings
                need_price = bool(cfg.buy_below_price) or effective_min_change_rate(cfg) is not None
                need_gate = bool(cfg.use_entry_gate)
                if need_price or need_gate:
                    current_price = await self._get_current_price(signal.stock_code)
                    if not current_price:
                        return {"valid": False, "reason": "현재가 조회 실패(매수조건 검증)"}
                    change_rate = None
                    if need_price:
                        snap = await self.kiwoom_api.get_stock_snapshot(signal.stock_code)
                        if snap.get("success"):
                            snap_data = snap.get("snapshot") or {}
                            try:
                                change_rate = float(str(snap_data.get("change_rate", "0")).replace(",", ""))
                            except (TypeError, ValueError):
                                change_rate = None
                        if not passes_buy_price_conditions(cfg, current_price, change_rate):
                            return {"valid": False, "reason": "매수 조건 미충족(가격/등락률)"}

                    if need_gate:
                        gate_ok, gate_reason = await check_entry_gate(
                            self.kiwoom_api, cfg, signal.stock_code, current_price,
                        )
                        if not gate_ok:
                            return {"valid": False, "reason": f"진입 게이트: {gate_reason}"}

            return {"valid": True, "reason": "검증 통과"}
            
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 매수 조건 검증 오류: {e}")
            return {"valid": False, "reason": f"검증 오류: {e}"}
    
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

            entr = _to_int(raw.get("entr") or 0)
            d2 = _to_int(raw.get("d2_entra") or 0)
            investable, reserve = compute_investable_cash(entr, self.auto_trade_settings)
            if d2 <= 0:
                investable = 0
            else:
                investable = min(investable, d2)
            return {
                "deposit": entr,
                "d2_entra": d2,
                "available_cash": entr,
                "investable_cash": investable,
                "cash_reserve": reserve,
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
    
    async def _calculate_buy_quantity(
        self,
        stock_code: str,
        current_price: int,
        change_rate: Optional[float] = None,
        is_add_buy: bool = False,
    ) -> int:
        """매수 수량 계산 (FIXED / PYRAMIDING, 추가매수 포함)."""
        try:
            if not self.auto_trade_settings:
                logger.error("💰 [BUY_EXECUTOR] 자동매매 설정이 없습니다.")
                return 0

            amount = compute_buy_amount(self.auto_trade_settings, change_rate, is_add_buy)
            account_info = await self._get_account_info()
            if account_info:
                investable = account_info.get("investable_cash", 0)
                capped = cap_buy_amount_by_cash(amount, investable)
                if capped < amount:
                    pct = cash_reserve_pct(self.auto_trade_settings)
                    logger.info(
                        f"💰 [BUY_EXECUTOR] 현금 보유 {pct:.0f}% 적용 — "
                        f"매수금액 {amount:,}→{capped:,}원 (가능 {investable:,}원)"
                    )
                amount = capped
            quantity = compute_quantity(amount, current_price)

            logger.info(
                f"💰 [BUY_EXECUTOR] 매수 수량: {quantity}주 "
                f"(금액={amount:,}원, add={is_add_buy}, 등락={change_rate})"
            )
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
                order_price, order_type = order_params(
                    self.auto_trade_settings,
                    current_price,
                ) if self.auto_trade_settings else (0, "3")
                result = await self.kiwoom_api.place_buy_order(
                    stock_code=signal.stock_code,
                    quantity=quantity,
                    price=order_price,
                    order_type=order_type,
                )
                
                if result.get("success"):
                    msg = f"매수 주문 성공 {signal.stock_name} {quantity}주 @ {current_price:,}원"
                    logger.info(f"💰 [BUY_EXECUTOR] {msg}")
                    log_activity("BUY", msg, "info", stock_code=signal.stock_code, quantity=quantity)
                    order_id = result.get("order_id", "")
                    await self._update_signal_status(signal.id, "ORDERED", "", order_id)

                    meta = parse_signal_meta(signal)
                    is_add = bool(meta.get("is_add_buy"))
                    asyncio.create_task(notify_buy_async(
                        stock_name=signal.stock_name,
                        stock_code=signal.stock_code,
                        quantity=quantity,
                        price=current_price,
                        is_add_buy=is_add,
                        order_id=order_id,
                    ))
                    
                    # 포지션 생성 또는 추가매수 반영
                    position = None
                    try:
                        if is_add:
                            position = await self._add_to_existing_position(
                                signal.stock_code, current_price, quantity, order_id,
                            )
                        else:
                            position = await self.stop_loss_manager.create_position_from_buy_signal(
                                signal_id=signal.id,
                                buy_price=current_price,
                                buy_quantity=quantity,
                                buy_order_id=order_id,
                            )
                        logger.info(f"💰 [BUY_EXECUTOR] 포지션 {'추가' if is_add else '생성'} — {signal.stock_name}")
                        
                        if position:
                            await self._record_buy_fill(
                                position, signal, current_price, quantity, order_id, is_add, meta,
                            )
                            await self._update_signal_status(signal.id, "FILLED", "")
                            asyncio.create_task(self._update_position_with_actual_price(position.id, signal.stock_code, 5))
                    except Exception as e:
                        logger.error(f"💰 [BUY_EXECUTOR] 포지션 생성 실패 - {signal.stock_name}: {e}")
                        log_activity("BUY", f"포지션 생성 실패 {signal.stock_name}: {e}", "error",
                                     stock_code=signal.stock_code)
                    
                    return
                else:
                    error_msg = result.get("error", "알 수 없는 오류")
                    logger.warning(f"💰 [BUY_EXECUTOR] 매수 주문 실패 (시도 {attempt + 1}): {error_msg}")
                    log_activity("BUY", f"매수 실패 {signal.stock_name}: {error_msg}", "error",
                                 stock_code=signal.stock_code)
                    
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
    
    async def _update_position_with_actual_price(self, position_id: int, stock_code: str, delay_seconds: int = 5):
        """주문 체결 후 실제 체결가로 포지션 업데이트"""
        try:
            # 체결 대기 시간
            await asyncio.sleep(delay_seconds)
            
            logger.info(f"💰 [BUY_EXECUTOR] 실제 체결가 조회 시작 - Position ID: {position_id}, 종목: {stock_code}")
            
            # 키움 API에서 보유종목 정보 조회
            account_number = Config.KIWOOM_MOCK_ACCOUNT_NUMBER if Config.KIWOOM_USE_MOCK_ACCOUNT else Config.KIWOOM_ACCOUNT_NUMBER
            balance_data = await self.kiwoom_api.get_account_balance(account_number)
            
            if not balance_data or 'stk_acnt_evlt_prst' not in balance_data:
                logger.warning(f"💰 [BUY_EXECUTOR] 보유종목 정보 조회 실패 - Position ID: {position_id}")
                return
            
            # 해당 종목 찾기
            from api.kiwoom_api import KiwoomAPI
            norm_code = KiwoomAPI.normalize_stock_code(stock_code)
            holdings = balance_data.get('stk_acnt_evlt_prst', [])
            target_holding = None
            for holding in holdings:
                holding_code = KiwoomAPI.normalize_stock_code(holding.get('stk_cd', ''))
                if holding_code == norm_code:
                    target_holding = holding
                    break
            
            if not target_holding:
                logger.warning(f"💰 [BUY_EXECUTOR] 보유종목에서 찾을 수 없음 - 종목: {stock_code}")
                return

            from utils.position_buy_fills import reconcile_position_buy_with_fills

            for db in get_db():
                session: Session = db
                position = session.query(Position).filter(Position.id == position_id).first()
                if position:
                    old_price = position.buy_price
                    old_amt = position.actual_buy_amount or position.buy_amount
                    reconcile_position_buy_with_fills(session, position, target_holding)
                    session.commit()
                    logger.info(
                        f"💰 [BUY_EXECUTOR] 키움 API 포지션 동기화 — {position.stock_name}: "
                        f"매입가 {old_price:,}→{position.buy_price:,}원, "
                        f"매입금액 {old_amt:,}→{position.buy_amount:,}원"
                    )
                break
                
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 실제 체결가 업데이트 오류 - Position ID: {position_id}, 오류: {e}")
    
    async def _add_to_existing_position(
        self,
        stock_code: str,
        add_price: int,
        add_quantity: int,
        order_id: str = "",
    ) -> Optional[Position]:
        """기존 HOLDING 포지션에 추가매수 반영."""
        for db in get_db():
            session: Session = db
            position = session.query(Position).filter(
                Position.stock_code == stock_code,
                Position.status == "HOLDING",
            ).first()
            if not position:
                return None
            old_qty = position.buy_quantity
            old_amt = position.buy_amount or (position.buy_price * old_qty)
            new_qty = old_qty + add_quantity
            new_amt = old_amt + add_price * add_quantity
            position.buy_quantity = new_qty
            position.buy_price = new_amt // new_qty if new_qty else position.buy_price
            position.buy_amount = new_amt
            if order_id:
                position.buy_order_id = order_id
            session.commit()
            logger.info(
                f"💰 [BUY_EXECUTOR] 추가매수 주문 반영 — {position.stock_name}: "
                f"+{add_quantity}주 @ {add_price:,}원 (키움 API 동기화 대기)"
            )
            asyncio.create_task(self._after_add_buy_followup(position.id, stock_code))
            return position
        return None

    async def _after_add_buy_followup(self, position_id: int, stock_code: str):
        """추가매수 후 키움 평균단가 동기화 → 트레일링 바닥(평균단가×시작%) 상향."""
        await self._update_position_with_actual_price(position_id, stock_code, 5)
        await self.stop_loss_manager.refresh_trailing_floor_for_position(position_id)

    async def _record_buy_fill(
        self,
        position: Position,
        signal: PendingBuySignal,
        price: int,
        quantity: int,
        order_id: str,
        is_add: bool,
        meta: Dict,
    ) -> None:
        """매수 체결 이력 저장 (검증 페이지 타임라인용)."""
        from utils.buy_condition_checks import build_buy_condition_checklist_at_buy
        from utils.position_buy_fills import record_buy_fill
        from utils.trade_verification import _settings_dict

        try:
            settings = self.auto_trade_settings
            sizing = (settings.sizing_method or "FIXED") if settings else "FIXED"
            change_rate = meta.get("change_rate")
            if change_rate is not None:
                change_rate = float(change_rate)
            planned = None
            if settings:
                planned = compute_buy_amount(settings, change_rate, is_add)

            note = None
            if is_add:
                trig = settings.add_buy_trigger if settings else None
                if change_rate is not None and trig is not None:
                    note = f"보유 수익률 {change_rate:.2f}% (추가매수 트리거 +{trig}%)"
                else:
                    note = "피라미딩 추가매수"

            fill_amount = price * quantity
            condition_checks = await build_buy_condition_checklist_at_buy(
                self.kiwoom_api,
                _settings_dict(settings),
                signal,
                meta,
                price,
                change_rate,
                is_add,
                fill_amount,
            )

            for db in get_db():
                session: Session = db
                record_buy_fill(
                    session,
                    position_id=position.id,
                    stock_code=position.stock_code,
                    stock_name=position.stock_name,
                    fill_type="ADD" if is_add else "INITIAL",
                    price=price,
                    quantity=quantity,
                    order_quantity=quantity,
                    signal_id=signal.id,
                    order_id=order_id,
                    planned_amount=planned,
                    change_rate=change_rate,
                    sizing_method=sizing,
                    note=note,
                    condition_checks=condition_checks,
                )
                pos_row = session.query(Position).filter(Position.id == position.id).first()
                if pos_row and not getattr(pos_row, "order_quantity", None):
                    pos_row.order_quantity = quantity
                session.commit()
                logger.info(f"💰 [BUY_EXECUTOR] 매수 체결 이력 저장 — {position.stock_name} {'ADD' if is_add else 'INITIAL'}")
                break
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 매수 체결 이력 저장 오류: {e}")

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
