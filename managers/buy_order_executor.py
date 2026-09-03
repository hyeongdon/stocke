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
    classify_breakout_wait_kind,
    compute_buy_amount,
    compute_investable_cash,
    compute_quantity,
    count_open_position_slots,
    effective_min_change_rate,
    evaluate_gate_pack,
    get_auto_trade_settings_sync,
    has_buy_conditions,
    allows_new_buy,
    allows_strategy_new_buy,
    is_breakout_watching_reason,
    is_max_concurrent_positions_reached,
    max_concurrent_positions_limit,
    order_params,
    parse_signal_meta,
    buy_price_skip_reason,
    passes_buy_price_conditions,
)
from managers.signal_manager import signal_manager
from utils.market_hours import linked_trading_session_window_str
from utils.auto_trade_activity_log import log_activity
from utils.datetime_kst import as_kst
from notifications.trade_alert import notify_buy_async, notify_buy_slot_blocked_async

logger = logging.getLogger(__name__)

# 일시 장애(잔고/시세/슬롯) — 즉시 FAILED 하지 않고 재시도·PENDING 보류
_TRANSIENT_BUY_REASON_MARKERS = (
    "계좌 정보 조회 실패",
    "현재가 조회 실패",
    "API 호출 제한",
    "슬롯 대기",
    "rate_limit",
    "토큰",
    "timeout",
    "타임아웃",
    "MA20 유예",  # 돌파 후 MA20 상회 유예창 대기 — FAILED 아님
    "진입 확인 대기",  # HARD/SOFT/HOLD 미충족 대기
)
_MAX_TRANSIENT_DEFER = 5  # 인프로세스 재시도 소진 후 PENDING 보류 횟수


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

    @staticmethod
    def _is_transient_buy_failure(reason: str, *, retryable_flag: bool = False) -> bool:
        if retryable_flag:
            return True
        if not reason:
            return False
        lower = reason.lower()
        return any(m.lower() in lower for m in _TRANSIENT_BUY_REASON_MARKERS)

    def invalidate_settings_cache(self) -> None:
        self.auto_trade_settings = None

    def is_session_active(self) -> bool:
        if not self.is_running:
            return False
        settings = get_auto_trade_settings_sync()
        if not settings or not settings.is_enabled:
            return False
        allowed, _ = auto_trade_engines_allowed()
        return allowed

    def get_status(self) -> Dict:
        active = self.is_session_active()
        settings = get_auto_trade_settings_sync()
        window = linked_trading_session_window_str(settings) if settings else None
        try:
            poll = int(getattr(settings, "scan_interval_sec", None) or 60)
        except (TypeError, ValueError):
            poll = 60
        poll = max(15, min(600, poll))
        return {
            "is_running": self.is_running,
            "is_active": active,
            "session_window": window,
            "trade_start_time": settings.trade_start_time if settings else None,
            "trade_end_time": settings.trade_end_time if settings else None,
            "scan_interval_sec": poll,
            "max_invest_amount": (
                settings.max_invest_amount if settings else 0
            ),
            "max_retry_attempts": self.max_retry_attempts,
        }
        
    async def start_processing(self):
        """매수 주문 처리 시작"""
        logger.info("💰 [BUY_EXECUTOR] 매수 주문 처리기 시작")
        try:
            poll0 = int(getattr(get_auto_trade_settings_sync(), "scan_interval_sec", None) or 60)
        except (TypeError, ValueError, AttributeError):
            poll0 = 60
        poll0 = max(15, min(600, poll0))
        log_activity("BUY", f"매수 실행기 시작 ({poll0}초 주기)", "info")
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
                        # PENDING 매수를 WATCHING 재평가보다 먼저 — 관측 차트 호출에
                        # API 슬롯이 소진되어 계좌조회/주문이 밀리지 않게 한다.
                        await self._process_pending_signals()
                        await self._process_watching_signals()
                else:
                    logger.debug("💰 [BUY_EXECUTOR] 자동매매 비활성화 상태 - 신호 처리 건너뜀")

                try:
                    poll = int(getattr(self.auto_trade_settings, "scan_interval_sec", None) or 60)
                except (TypeError, ValueError):
                    poll = 60
                await asyncio.sleep(max(15, min(600, poll)))
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
    async def _process_watching_signals(self):
        """관측(WATCHING) 신호 — 유니버스와 무관하게 차트 재평가 후 PENDING 승격 또는 만료."""
        try:
            watching = await self._get_watching_signals()
            if not watching:
                return
            logger.info(f"💰 [BUY_EXECUTOR] 관측 신호 {len(watching)}개 재평가")
            for signal in watching:
                try:
                    await self._process_watching_signal(signal)
                except Exception as e:
                    logger.error(f"💰 [BUY_EXECUTOR] WATCHING 처리 오류 (ID: {signal.id}): {e}")
                await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 관측 신호 처리 중 오류: {e}")

    async def _get_watching_signals(self) -> List[PendingBuySignal]:
        signals: List[PendingBuySignal] = []
        for db in get_db():
            try:
                session: Session = db
                signals = (
                    session.query(PendingBuySignal)
                    .filter(PendingBuySignal.status == "WATCHING")
                    .order_by(PendingBuySignal.detected_at.asc())
                    .all()
                )
                break
            except Exception as e:
                logger.error(f"💰 [BUY_EXECUTOR] WATCHING 조회 오류: {e}")
                continue
        return signals

    async def _refresh_watching_meta(
        self,
        signal_id: int,
        *,
        wait_kind: str,
        wait_reason: str,
        ctx: Dict,
        price: int,
    ) -> None:
        """WATCHING 유지 — transient_defer 카운트 증가 없음."""
        for db in get_db():
            session: Session = db
            signal = session.query(PendingBuySignal).filter(
                PendingBuySignal.id == signal_id
            ).first()
            if not signal or signal.status != "WATCHING":
                return
            meta = parse_signal_meta(signal)
            meta.update({k: v for k, v in (ctx or {}).items() if v is not None})
            meta["wait_kind"] = wait_kind
            meta["wait_reason"] = wait_reason
            meta["order_ready"] = False
            meta["current_price"] = price
            meta.pop("transient_defer_count", None)
            signal.additional_data = meta
            signal.failure_reason = None
            session.commit()
            logger.info(
                f"💰 [BUY_EXECUTOR] WATCHING 유지 ID {signal_id}: {wait_reason}"
            )
            break

    async def _process_watching_signal(self, signal: PendingBuySignal):
        """WATCHING 1건: 게이트 통과 시 PENDING 승격(+즉시 매수 시도), 대기 유지, 그 외 FAILED."""
        meta = parse_signal_meta(signal)
        strategy = str(meta.get("strategy") or "")
        pack = str(meta.get("gate_pack") or "")
        if strategy == "breakout" or pack == "oversold_breakout":
            pack = "oversold_breakout"
        elif strategy == "sangtta" or pack == "sangtta_breakout":
            pack = "sangtta_breakout"
        elif strategy == "ymgp" or pack == "yeokmaegongpa":
            await self._update_signal_status(
                signal.id, "EXPIRED", "역매공파 전략 폐기"
            )
            return
        elif strategy == "jongga" or pack in ("jongga_closing", "jongga"):
            pack = "jongga_closing"
        elif strategy == "fractal" or pack in ("ema_fractal_pullback", "fractal"):
            pack = "ema_fractal_pullback"
        else:
            # 레거시 등은 WATCHING 미사용 — 정리
            await self._update_signal_status(
                signal.id, "EXPIRED", "관측 대상 아님"
            )
            return

        current_price = await self._get_current_price(signal.stock_code)
        if not current_price:
            logger.warning(
                f"💰 [BUY_EXECUTOR] WATCHING 시세 없음 — 유지 {signal.stock_name}"
            )
            return

        change_rate = meta.get("change_rate")
        ctx = dict(meta)
        gate_ok, gate_reason = await evaluate_gate_pack(
            self.kiwoom_api,
            self.auto_trade_settings,
            pack,
            signal.stock_code,
            current_price,
            change_rate=change_rate,
            ctx=ctx,
            skip_time_check=True,
            update_soft_streak=False,
        )

        if gate_ok:
            ok, msg = await signal_manager.promote_watching_to_pending(
                signal.id,
                additional_data={
                    **{k: ctx.get(k) for k in (
                        "level_price", "level_kind", "day_volume", "prev_volume",
                        "volume_ratio", "confirm_close", "confirm_high",
                        "entry_confirm_mode", "ma20",
                        "stop_price", "take_profit_price", "ema50_at_entry",
                        "fractal_rr", "fractal_checks",
                    ) if ctx.get(k) is not None},
                    "current_price": current_price,
                    "change_rate": change_rate,
                    "promoted_from": "WATCHING",
                },
            )
            if not ok:
                logger.warning(
                    f"💰 [BUY_EXECUTOR] WATCHING 승격 실패 ID {signal.id}: {msg}"
                )
                return
            log_activity(
                "BUY",
                f"관측→매수대기 승격 {signal.stock_name}: {gate_reason}",
                "info",
                stock_code=signal.stock_code,
            )
            # 승격 직후 같은 사이클로 매수 시도
            for db in get_db():
                session: Session = db
                promoted = session.query(PendingBuySignal).filter(
                    PendingBuySignal.id == signal.id
                ).first()
                if promoted and promoted.status == "PENDING":
                    await self._process_single_signal(promoted)
                break
            return

        wait_kind = classify_breakout_wait_kind(gate_reason)
        if wait_kind and is_breakout_watching_reason(gate_reason):
            await self._refresh_watching_meta(
                signal.id,
                wait_kind=wait_kind,
                wait_reason=gate_reason,
                ctx=ctx,
                price=current_price,
            )
            return

        from utils.ema_fractal import is_fractal_wait_reason
        if strategy == "fractal" and is_fractal_wait_reason(gate_reason):
            await self._refresh_watching_meta(
                signal.id,
                wait_kind="fractal_setup",
                wait_reason=gate_reason,
                ctx=ctx,
                price=current_price,
            )
            return

        # 유예 만료·레벨 이탈 등 → FAILED (주문 로그에 실패로 남음)
        reason = f"관측 종료: {gate_reason}"
        log_activity(
            "BUY",
            f"관측 종료 {signal.stock_name}: {gate_reason}",
            "warn",
            stock_code=signal.stock_code,
        )
        await self._update_signal_status(signal.id, "FAILED", reason)

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
                    reason = str(e)
                    if self._is_transient_buy_failure(reason):
                        await self._defer_transient_failure(signal.id, reason)
                    else:
                        await self._update_signal_status(signal.id, "FAILED", reason)
                
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

            # 1~2. 검증·현재가 — 일시 장애는 재시도 후 PENDING 보류
            validation_result = None
            current_price = None
            for attempt in range(self.max_retry_attempts):
                debug_tracer.log_checkpoint(
                    f"1단계: 매수 전 검증 시작 ({attempt + 1}/{self.max_retry_attempts})",
                    "BUY_EXECUTOR",
                )
                validation_result = await self._validate_buy_conditions(signal)
                debug_tracer.log_checkpoint(f"1단계 결과: {validation_result}", "BUY_EXECUTOR")

                if not validation_result.get("valid"):
                    reason = validation_result.get("reason") or "매수 조건 미충족"
                    retryable = self._is_transient_buy_failure(
                        reason, retryable_flag=bool(validation_result.get("retryable")),
                    )
                    logger.warning(
                        f"💰 [BUY_EXECUTOR] 매수 조건 미충족 - {signal.stock_name}: {reason}"
                        f" (시도 {attempt + 1}/{self.max_retry_attempts}"
                        f"{', 재시도 예정' if retryable and attempt < self.max_retry_attempts - 1 else ''})"
                    )
                    log_activity(
                        "BUY",
                        f"검증 실패 {signal.stock_name}: {reason}",
                        "warn",
                        stock_code=signal.stock_code,
                    )
                    if retryable and attempt < self.max_retry_attempts - 1:
                        await asyncio.sleep(self.retry_delay_seconds)
                        continue
                    if retryable:
                        await self._defer_transient_failure(signal.id, reason)
                    else:
                        await self._update_signal_status(signal.id, "FAILED", reason)
                    return

                debug_tracer.log_checkpoint("2단계: 현재가 조회 시작", "BUY_EXECUTOR")
                current_price = await self._get_current_price(signal.stock_code)
                debug_tracer.log_checkpoint(
                    f"2단계 결과: 현재가={current_price:,}원" if current_price else "2단계 결과: 실패",
                    "BUY_EXECUTOR",
                )
                if not current_price:
                    reason = "현재가 조회 실패"
                    logger.error(
                        f"💰 [BUY_EXECUTOR] {reason} - {signal.stock_name} "
                        f"(시도 {attempt + 1}/{self.max_retry_attempts})"
                    )
                    log_activity(
                        "BUY",
                        f"{reason} {signal.stock_name}({signal.stock_code})",
                        "warn",
                        stock_code=signal.stock_code,
                    )
                    if attempt < self.max_retry_attempts - 1:
                        await asyncio.sleep(self.retry_delay_seconds)
                        continue
                    await self._defer_transient_failure(signal.id, reason)
                    return
                break

            meta = parse_signal_meta(signal)
            is_add_buy = bool(meta.get("is_add_buy"))
            # 전략별 시간/슬롯/게이트 재검증 (보호)
            strategy = meta.get("strategy")
            # 추가매수(is_add_buy)여도 아래 분기에서 strategy_key를 참조하므로 항상 먼저 할당
            strategy_key = strategy or "legacy"
            label = {
                "legacy": "레거시",
                "sangtta": "상따",
                "breakout": "돌파",
                "ymgp": "역매공파",
                "jongga": "종가배팅",
                "fractal": "프랙탈",
                "ma1592": "15/92홀드",
            }.get(strategy_key, strategy_key)
            if not is_add_buy:
                from utils.market_risk_gate import check_market_risk_buy_allowed
                risk_ok, risk_reason = True, ""
                for db in get_db():
                    risk_ok, risk_reason = check_market_risk_buy_allowed(
                        self.auto_trade_settings,
                        strategy,
                        session=db,
                        stock_code=signal.stock_code,
                    )
                    break
                if not risk_ok:
                    log_activity(
                        "BUY",
                        f"{risk_reason} - {signal.stock_name}",
                        "warn",
                        stock_code=signal.stock_code,
                    )
                    await self._update_signal_status(signal.id, "FAILED", risk_reason)
                    return
            if not is_add_buy:
                allowed, reason = allows_strategy_new_buy(
                    self.auto_trade_settings, strategy_key,
                )
                if not allowed:
                    log_activity(
                        "BUY",
                        f"{label} 시간 외 - {signal.stock_name}: {reason}",
                        "warn",
                        stock_code=signal.stock_code,
                    )
                    await self._update_signal_status(
                        signal.id, "FAILED", reason or f"{label} 시간 외",
                    )
                    return
            if strategy_key == "legacy" and not is_add_buy:
                from utils.auto_trade_engine import (
                    _count_strategy_slots,
                    effective_legacy_max_slots,
                    is_strategy_slot_available,
                )
                for db in get_db():
                    if not is_strategy_slot_available(
                        self.auto_trade_settings, db, "legacy", for_new_signal=False,
                    ):
                        used = _count_strategy_slots(db, "legacy")
                        lim = effective_legacy_max_slots(self.auto_trade_settings)
                        msg = f"레거시 슬롯 포화 ({used}/{lim})"
                        log_activity(
                            "BUY", f"{msg} - {signal.stock_name}", "warn",
                            stock_code=signal.stock_code,
                        )
                        await self._update_signal_status(signal.id, "FAILED", msg)
                        return
                    break
            if strategy == "ymgp" and not is_add_buy:
                await self._update_signal_status(signal.id, "FAILED", "역매공파 전략 폐기")
                return
            from utils.auto_trade_engine import _normalize_strategy_key
            slot_strategy = _normalize_strategy_key(strategy_key)
            if slot_strategy in ("sangtta", "breakout", "jongga", "fractal", "ma1592") and not is_add_buy:
                from utils.auto_trade_engine import (
                    is_strategy_slot_available,
                    _count_strategy_slots,
                    effective_sangtta_max_slots,
                    effective_breakout_max_slots,
                    effective_jongga_max_slots,
                    effective_fractal_max_slots,
                    effective_ma1592_max_slots,
                )
                for db in get_db():
                    if not is_strategy_slot_available(
                        self.auto_trade_settings, db, slot_strategy, for_new_signal=False,
                    ):
                        used = _count_strategy_slots(db, slot_strategy)
                        lim = {
                            "sangtta": effective_sangtta_max_slots,
                            "breakout": effective_breakout_max_slots,
                            "jongga": effective_jongga_max_slots,
                            "fractal": effective_fractal_max_slots,
                            "ma1592": effective_ma1592_max_slots,
                        }[slot_strategy](self.auto_trade_settings)
                        msg = f"{label} 슬롯 포화 ({used}/{lim})"
                        log_activity("BUY", f"{msg} - {signal.stock_name}", "warn", stock_code=signal.stock_code)
                        await self._update_signal_status(signal.id, "FAILED", msg)
                        return
                    break
                # MA1592: 스캐너 L3 통과 신호 신뢰 — 주문 직전 게이트 재평가 생략
                # (터치 반등 봉·장부 상태 재평가 시 매수가 막히는 문제 방지)
                if strategy != "ma1592":
                    pack = {
                        "sangtta": "sangtta_breakout",
                        "breakout": "oversold_breakout",
                        "jongga": "jongga_closing",
                        "fractal": "ema_fractal_pullback",
                    }[strategy]
                    # 상따 전략은 시그널 생성 시점의 등락률(meta)이 아닌
                    # 주문 직전 현재가 기준으로 live 등락률을 재계산해 밴드 이탈을 방지
                    if strategy == "sangtta":
                        _prev_close = int(meta.get("prev_close") or 0)
                        if _prev_close > 0 and current_price > 0:
                            _live_cr = round((current_price - _prev_close) / _prev_close * 100, 2)
                        else:
                            _live_cr = meta.get("change_rate")
                        _gate_change_rate = _live_cr
                    else:
                        _gate_change_rate = meta.get("change_rate")
                    gate_ok, gate_reason = await evaluate_gate_pack(
                        self.kiwoom_api,
                        self.auto_trade_settings,
                        pack,
                        signal.stock_code,
                        current_price,
                        change_rate=_gate_change_rate,
                        ctx=meta,
                        skip_time_check=True,
                    )
                    if not gate_ok:
                        reason = f"{label} 게이트: {gate_reason}"
                        logger.warning(f"💰 [BUY_EXECUTOR] 주문 직전 {label} 게이트 실패 - {signal.stock_name}: {reason}")
                        # MA20 유예·HARD/SOFT/HOLD 대기는 조건 미충족이 아니라 "아직 대기"
                        # → 즉시 FAILED 하지 않고 PENDING 보류 후 다음 사이클 재평가
                        if any(k in (gate_reason or "") for k in ("유예", "진입 확인 대기", "HOLD 대기", "프랙탈 대기")):
                            log_activity(
                                "BUY",
                                f"{label} 게이트 대기 {signal.stock_name}: {gate_reason}",
                                "info",
                                stock_code=signal.stock_code,
                            )
                            await self._defer_transient_failure(signal.id, reason)
                            return
                        log_activity(
                            "BUY",
                            f"{label} 게이트 실패 {signal.stock_name}: {gate_reason}",
                            "warn",
                            stock_code=signal.stock_code,
                        )
                        await self._update_signal_status(signal.id, "FAILED", reason)
                        return
            elif self.auto_trade_settings and not is_add_buy and self.auto_trade_settings.use_entry_gate:
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
            is_add_buy = bool(meta.get("is_add_buy"))
            quantity, buy_amount = await self._calculate_buy_quantity(
                signal.stock_code,
                current_price,
                change_rate=meta.get("change_rate"),
                is_add_buy=is_add_buy,
                strategy_key=meta.get("strategy"),
                entry_leg=int(
                    meta.get("entry_leg")
                    or meta.get("jongga_entry_leg")
                    or meta.get("ymgp_entry_leg")
                    or 1
                ),
                stop_price=int(meta.get("stop_price") or 0) or None,
                suggested_qty=int(meta.get("suggested_qty") or 0) or None,
            )
            debug_tracer.log_checkpoint(f"3단계 결과: 수량={quantity}주, 총액={current_price*quantity:,}원", "BUY_EXECUTOR")
            
            if quantity < 1:
                kind = "추가매수" if is_add_buy else "매수"
                if buy_amount <= 0:
                    reason = f"매수 가능 금액 없음 (예산 {buy_amount:,}원)"
                    # 잔고 조회 실패가 수량 단계에서 다시 나면 일시 장애로 보류
                    acct = await self._get_account_info()
                    if not acct:
                        reason = "계좌 정보 조회 실패"
                        await self._defer_transient_failure(signal.id, reason)
                        return
                elif current_price and current_price > 0:
                    # amount // price == 0 → 주가가 예산을 초과 (고가주)
                    reason = (
                        f"고가주라 1주도 매수 불가 "
                        f"({kind} 예산 {buy_amount:,}원 < 1주 {current_price:,}원)"
                    )
                else:
                    reason = f"현재가 없음 — {kind} 수량 산출 불가"
                logger.warning(f"💰 [BUY_EXECUTOR] {signal.stock_name}: {reason}")
                log_activity(
                    "BUY",
                    f"{signal.stock_name}({signal.stock_code}): {reason}",
                    "warn",
                    stock_code=signal.stock_code,
                )
                if self._is_transient_buy_failure(reason):
                    await self._defer_transient_failure(signal.id, reason)
                else:
                    await self._update_signal_status(signal.id, "FAILED", reason)
                return
            
            # 4. 매수 주문 실행 (재시도 포함)
            debug_tracer.log_checkpoint(f"4단계: 매수 주문 실행 (가격={current_price:,}원, 수량={quantity}주)", "BUY_EXECUTOR")
            await self._execute_buy_order_with_retry(signal, current_price, quantity)
            debug_tracer.log_checkpoint("4단계 완료: 매수 주문 성공", "BUY_EXECUTOR")
            await self._clear_transient_defer_meta(signal.id)
            
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 신호 처리 중 오류 - {signal.stock_name}: {e}")
            reason = str(e)
            if self._is_transient_buy_failure(reason):
                await self._defer_transient_failure(signal.id, reason)
            else:
                await self._update_signal_status(signal.id, "FAILED", reason)
    
    async def _validate_buy_conditions(self, signal: PendingBuySignal) -> Dict:
        """매수 전 검증"""
        try:
            meta = parse_signal_meta(signal)
            is_add_buy = bool(meta.get("is_add_buy"))
            strategy = meta.get("strategy")
            now = as_kst()

            # 1. 시장 시간 — 전략별 전용 윈도우 (레거시 포함)
            strategy_key = strategy or "legacy"
            allowed, reason = allows_strategy_new_buy(
                self.auto_trade_settings, strategy_key, now,
                is_add_buy=is_add_buy,
            )
            if not allowed:
                return {"valid": False, "reason": reason or f"{strategy_key} 시간대 외"}

            # 1b. 일일 손익 한도
            if self.auto_trade_settings:
                halt = check_daily_limits(self.auto_trade_settings)
                if halt:
                    return {"valid": False, "reason": halt}
            
            # 2. 계좌 잔고 확인
            account_info = await self._get_account_info()
            if not account_info:
                return {
                    "valid": False,
                    "reason": "계좌 정보 조회 실패",
                    "retryable": True,
                }

            # meta / is_add_buy already parsed above
            # 1c. 최대 동시 보유 (신규 매수만 — 대기 신호 슬롯 포함)
            # 종가배팅은 jongga_max_slots로 별도 관리 — 전역 한도와 분리
            from utils.auto_trade_engine import _normalize_strategy_key
            skip_global_slot_cap = _normalize_strategy_key(strategy_key) == "jongga"
            if self.auto_trade_settings and not is_add_buy and not skip_global_slot_cap:
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
                    deposit=deposit,
                )
                if not is_add_buy and strategy == "breakout":
                    from utils.auto_trade_engine import effective_breakout_buy_amount
                    planned = effective_breakout_buy_amount(self.auto_trade_settings, deposit=deposit)
                elif not is_add_buy and strategy == "sangtta":
                    from utils.auto_trade_engine import effective_sangtta_buy_amount
                    planned = effective_sangtta_buy_amount(self.auto_trade_settings, deposit=deposit)
                elif not is_add_buy and strategy == "jongga":
                    from utils.auto_trade_engine import effective_jongga_buy_amount
                    planned = effective_jongga_buy_amount(
                        self.auto_trade_settings,
                        deposit=deposit,
                        entry_leg=int(meta.get("entry_leg") or meta.get("jongga_entry_leg") or 1),
                    )
                elif not is_add_buy and strategy == "fractal":
                    from utils.ema_fractal import risk_qty
                    current_price = await self._get_current_price(signal.stock_code)
                    if not current_price:
                        return {
                            "valid": False,
                            "reason": "현재가 조회 실패(프랙탈 위험수량 계산)",
                            "retryable": True,
                        }
                    stop_px = int(meta.get("stop_price") or 0)
                    entry_px = int(current_price or 0)
                    if stop_px > 0 and entry_px > 0 and stop_px >= entry_px:
                        return {
                            "valid": False,
                            "reason": (
                                f"프랙탈 손절가({stop_px:,}원) ≥ 현재가({entry_px:,}원) — "
                                f"현재가가 손절선 아래로 하락, 매수 취소"
                            ),
                        }
                    risk_pct = float(getattr(self.auto_trade_settings, "fractal_risk_pct", None) or 0.5)
                    cap = int(getattr(self.auto_trade_settings, "fractal_qty_cap", None) or 0)
                    qty = risk_qty(deposit, risk_pct, entry_px, stop_px, qty_cap=cap)
                    planned = qty * entry_px if qty > 0 and entry_px > 0 else 0
                elif not is_add_buy and strategy == "ma1592":
                    # H8: suggested_qty 우선
                    suggested = int(meta.get("suggested_qty") or 0)
                    current_price = await self._get_current_price(signal.stock_code)
                    entry_px = int(current_price or meta.get("current_price") or 0)
                    if suggested > 0 and entry_px > 0:
                        planned = suggested * entry_px
                    else:
                        from utils.ma1592 import params_from_settings, size_position
                        p = params_from_settings(self.auto_trade_settings)
                        stop_px = int(meta.get("stop_price") or meta.get("suggested_stop") or 0)
                        ma15 = float(meta.get("ma15") or 0)
                        if entry_px <= 0:
                            return {
                                "valid": False,
                                "reason": "현재가 조회 실패(MA1592 수량)",
                                "retryable": True,
                            }
                        sizing = size_position(
                            deposit,
                            entry_px,
                            ma15 or entry_px * 0.996,
                            risk_per_trade_pct=float(p["risk_per_trade_pct"]),
                            stop_pct=float(p["stop_pct"]),
                            hard_break_pct=float(p["hard_break_pct"]),
                            max_invest_amount=int(
                                getattr(self.auto_trade_settings, "ma1592_max_invest_amount", None) or 0
                            ),
                            tp1_frac=float(p["tp1_frac"]),
                        )
                        qty = int(sizing.get("qty") or 0)
                        planned = qty * entry_px if qty > 0 else 0
                elif is_add_buy and strategy == "ma1592":
                    suggested = int(meta.get("suggested_qty") or 0)
                    current_price = await self._get_current_price(signal.stock_code)
                    entry_px = int(current_price or meta.get("current_price") or 0)
                    if suggested > 0 and entry_px > 0:
                        planned = suggested * entry_px
                    else:
                        from utils.ma1592 import params_from_settings, scale_leg_qty
                        p = params_from_settings(self.auto_trade_settings)
                        planned_qty = int(meta.get("planned_qty") or 0)
                        leg = int(meta.get("entry_leg") or meta.get("ma1592_entry_leg") or 2)
                        qty = scale_leg_qty(planned_qty, leg, p) if planned_qty > 0 else 0
                        planned = qty * entry_px if qty > 0 and entry_px > 0 else 0
                elif is_add_buy and strategy == "jongga":
                    from utils.auto_trade_engine import effective_jongga_buy_amount
                    planned = effective_jongga_buy_amount(
                        self.auto_trade_settings,
                        deposit=deposit,
                        entry_leg=int(meta.get("entry_leg") or meta.get("jongga_entry_leg") or 2),
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
                strategy = meta.get("strategy")
                if strategy in ("sangtta", "breakout", "jongga"):
                    current_price = await self._get_current_price(signal.stock_code)
                    if not current_price:
                        return {
                            "valid": False,
                            "reason": "현재가 조회 실패(매수조건 검증)",
                            "retryable": True,
                        }
                    # 상따 전략: 시그널 생성 시점의 등락률이 아닌 현재가 기준으로 재계산
                    # (시그널 생성 후 가격이 급등해 밴드 상단 15%를 초과해도 매수되는 버그 방지)
                    if strategy == "sangtta":
                        _prev_close = int(meta.get("prev_close") or 0)
                        if _prev_close > 0 and current_price > 0:
                            change_rate = round((current_price - _prev_close) / _prev_close * 100, 2)
                        else:
                            # prev_close 미저장 시그널 대비 스냅샷 폴백
                            snap = await self.kiwoom_api.get_stock_snapshot(signal.stock_code)
                            if snap.get("success"):
                                snap_data = snap.get("snapshot") or {}
                                try:
                                    snap_price = int(snap_data.get("current_price") or 0)
                                except (TypeError, ValueError):
                                    snap_price = 0
                                try:
                                    snap_cr = float(str(snap_data.get("change_rate", "0")).replace(",", "").replace("+", ""))
                                except (TypeError, ValueError):
                                    snap_cr = None
                                if snap_cr is not None and snap_price > 0:
                                    change_rate = snap_cr
                                elif snap_cr is not None and abs(snap_cr) > 0:
                                    change_rate = snap_cr
                                else:
                                    change_rate = None
                            else:
                                change_rate = None
                    else:
                        change_rate = meta.get("change_rate")
                        if change_rate is not None:
                            try:
                                change_rate = float(change_rate)
                            except (TypeError, ValueError):
                                change_rate = None
                        if change_rate is None:
                            snap = await self.kiwoom_api.get_stock_snapshot(signal.stock_code)
                            if snap.get("success"):
                                snap_data = snap.get("snapshot") or {}
                                try:
                                    snap_price = int(snap_data.get("current_price") or 0)
                                except (TypeError, ValueError):
                                    snap_price = 0
                                try:
                                    snap_cr = float(str(snap_data.get("change_rate", "0")).replace(",", "").replace("+", ""))
                                except (TypeError, ValueError):
                                    snap_cr = None
                                if snap_cr is not None and snap_price > 0:
                                    change_rate = snap_cr
                                elif snap_cr is not None and abs(snap_cr) > 0:
                                    change_rate = snap_cr
                    if cfg.buy_below_price and current_price > int(cfg.buy_below_price):
                        return {"valid": False, "reason": f"매수가 상한 초과 ({current_price:,} > {int(cfg.buy_below_price):,})"}
                    pack = {
                        "sangtta": "sangtta_breakout",
                        "breakout": "oversold_breakout",
                        "jongga": "jongga_closing",
                    }[strategy]
                    gate_ok, gate_reason = await evaluate_gate_pack(
                        self.kiwoom_api, cfg,
                        pack,
                        signal.stock_code, current_price,
                        change_rate=change_rate,
                        ctx=meta,
                    )
                    if not gate_ok:
                        label = {
                            "sangtta": "상따",
                            "breakout": "돌파",
                            "ymgp": "역매공파",
                            "jongga": "종가배팅",
                        }[strategy]
                        return {"valid": False, "reason": f"{label} 게이트: {gate_reason}"}
                else:
                    # 프랙탈·MA1592은 자체 게이트만 사용 — 레거시 최소등락률(예: 3.5%) 미적용
                    is_fractal = strategy == "fractal"
                    is_ma1592 = strategy == "ma1592"
                    skip_legacy_momentum = is_fractal or is_ma1592
                    need_min_change = (
                        (not skip_legacy_momentum) and effective_min_change_rate(cfg) is not None
                    )
                    need_price_cap = bool(cfg.buy_below_price)
                    need_price = need_price_cap or need_min_change
                    need_gate = bool(cfg.use_entry_gate) and not is_ma1592
                    if need_price or need_gate:
                        current_price = await self._get_current_price(signal.stock_code)
                        if not current_price:
                            return {"valid": False, "reason": "현재가 조회 실패(매수조건 검증)"}
                        change_rate = None
                        try:
                            if meta.get("change_rate") is not None:
                                change_rate = float(meta.get("change_rate"))
                        except (TypeError, ValueError):
                            change_rate = None
                        if need_price:
                            if need_min_change:
                                snap = await self.kiwoom_api.get_stock_snapshot(signal.stock_code)
                                if snap.get("success"):
                                    snap_data = snap.get("snapshot") or {}
                                    snap_price = 0
                                    snap_cr = None
                                    try:
                                        snap_price = int(snap_data.get("current_price") or 0)
                                    except (TypeError, ValueError):
                                        snap_price = 0
                                    try:
                                        snap_cr = float(str(snap_data.get("change_rate", "0")).replace(",", "").replace("+", ""))
                                    except (TypeError, ValueError):
                                        snap_cr = None
                                    # 스냅샷이 유효(현재가>0)하면 최신 등락 사용, 아니면 신호 메타 유지
                                    # (ka10006 flat 미파싱 시 price=0/rate=0 오판 방어)
                                    if snap_price > 0 and snap_cr is not None:
                                        change_rate = snap_cr
                                    elif change_rate is None and snap_cr is not None:
                                        change_rate = snap_cr
                                if not passes_buy_price_conditions(cfg, current_price, change_rate):
                                    skip = buy_price_skip_reason(cfg, current_price, change_rate) or "매수 조건 미충족(가격/등락률)"
                                    return {"valid": False, "reason": skip}
                            elif need_price_cap and current_price > int(cfg.buy_below_price):
                                return {
                                    "valid": False,
                                    "reason": (
                                        f"매수가 상한 초과 "
                                        f"({current_price:,} > {int(cfg.buy_below_price):,})"
                                    ),
                                }

                        if need_gate:
                            gate_ok, gate_reason = await check_entry_gate(
                                self.kiwoom_api, cfg, signal.stock_code, current_price,
                                skip_volume_ratio=is_fractal,
                                skip_day_position=is_fractal,
                            )
                            if not gate_ok:
                                return {"valid": False, "reason": f"진입 게이트: {gate_reason}"}

            return {"valid": True, "reason": "검증 통과"}
            
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 매수 조건 검증 오류: {e}")
            return {"valid": False, "reason": f"검증 오류: {e}"}
    
    async def _get_account_info(self) -> Optional[Dict]:
        """계좌 정보 조회 — API _error/빈응답은 None (예수금 0원으로 위장하지 않음)."""
        account_number = (
            Config.KIWOOM_MOCK_ACCOUNT_NUMBER
            if Config.KIWOOM_USE_MOCK_ACCOUNT
            else Config.KIWOOM_ACCOUNT_NUMBER
        )
        if not account_number:
            logger.error(
                "💰 [BUY_EXECUTOR] 계좌번호가 설정되지 않았습니다 "
                "(KIWOOM_ACCOUNT_NUMBER / KIWOOM_MOCK_ACCOUNT_NUMBER)"
            )
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

        try:
            from utils.api_traffic_guard import APIPriority

            # 매수 검증용 잔고는 HIGH — 스캐너/관측 LOW·차트에 밀려 rate_limit 나지 않게
            raw = await self.kiwoom_api.get_account_balance(
                account_number,
                priority=APIPriority.HIGH,
                max_wait=25.0,
            )
            if not raw:
                logger.warning("💰 [BUY_EXECUTOR] 잔고 조회 빈 응답")
                return None
            if raw.get("_error"):
                err = raw.get("_error_msg") or raw.get("_error")
                logger.warning(f"💰 [BUY_EXECUTOR] 잔고 조회 실패: {err}")
                return None

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

    async def _defer_transient_failure(self, signal_id: int, reason: str) -> None:
        """일시 장애 — PENDING으로 되돌리거나 보류 한도 초과 시 FAILED."""
        try:
            for db in get_db():
                session: Session = db
                signal = session.query(PendingBuySignal).filter(
                    PendingBuySignal.id == signal_id
                ).first()
                if not signal:
                    break
                meta = parse_signal_meta(signal)
                defer_count = int(meta.get("transient_defer_count") or 0) + 1
                meta["transient_defer_count"] = defer_count
                meta["last_transient_reason"] = (reason or "")[:200]
                signal.additional_data = meta
                if defer_count > _MAX_TRANSIENT_DEFER:
                    signal.status = "FAILED"
                    signal.failure_reason = (reason or "일시 장애 재시도 한도 초과")[:255]
                    session.commit()
                    logger.warning(
                        f"💰 [BUY_EXECUTOR] 일시 장애 보류 한도 초과 → FAILED "
                        f"(ID {signal_id}, {defer_count}회): {reason}"
                    )
                    log_activity(
                        "BUY",
                        f"재시도 한도 초과 FAILED: {reason}",
                        "error",
                        stock_code=getattr(signal, "stock_code", None),
                    )
                else:
                    signal.status = "PENDING"
                    signal.failure_reason = None
                    session.commit()
                    logger.info(
                        f"💰 [BUY_EXECUTOR] 일시 장애 → PENDING 보류 "
                        f"(ID {signal_id}, {defer_count}/{_MAX_TRANSIENT_DEFER}): {reason}"
                    )
                    log_activity(
                        "BUY",
                        f"일시 장애 보류({defer_count}/{_MAX_TRANSIENT_DEFER}): {reason}",
                        "warn",
                        stock_code=getattr(signal, "stock_code", None),
                    )
                break
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 일시 장애 보류 처리 오류: {e}")
            await self._update_signal_status(signal_id, "FAILED", reason)

    async def _clear_transient_defer_meta(self, signal_id: int) -> None:
        """주문 성공 경로에서 일시 장애 카운터 정리."""
        try:
            for db in get_db():
                session: Session = db
                signal = session.query(PendingBuySignal).filter(
                    PendingBuySignal.id == signal_id
                ).first()
                if not signal:
                    break
                meta = parse_signal_meta(signal)
                if "transient_defer_count" not in meta and "last_transient_reason" not in meta:
                    break
                meta.pop("transient_defer_count", None)
                meta.pop("last_transient_reason", None)
                signal.additional_data = meta or None
                session.commit()
                break
        except Exception as e:
            logger.debug(f"💰 [BUY_EXECUTOR] transient meta 정리 스킵: {e}")

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

    async def execute_manual_avg_down(self, position_id: int) -> Dict:
        """사용자가 요청한 포지션당 1회 수동 물타기 주문."""
        from utils.position_buy_fills import (
            MANUAL_AVG_DOWN_NOTE,
            manual_avg_down_state,
            record_buy_fill,
        )

        position = None
        baseline = 0
        settings = get_auto_trade_settings_sync()
        pct = float(getattr(settings, "manual_avg_down_pct", 50.0) or 50.0)
        if pct <= 0 or pct > 1000:
            return {"success": False, "error": "물타기 비율은 0 초과 1000 이하로 설정하세요."}

        for db in get_db():
            session: Session = db
            position = session.query(Position).filter(Position.id == position_id).first()
            if not position or position.status != "HOLDING":
                return {"success": False, "error": "보유 중인 포지션을 찾을 수 없습니다."}
            state = manual_avg_down_state(
                session, position.id, int(position.actual_buy_amount or position.buy_amount or 0),
            )
            if state["done"]:
                return {"success": False, "error": "이 포지션은 이미 수동 물타기를 1회 실행했습니다."}
            baseline = int(state["baseline_amount"] or 0)
            break

        self.auto_trade_settings = settings
        if not position or baseline <= 0:
            return {"success": False, "error": "최초 매수금액을 확인할 수 없습니다."}
        if await self._has_pending_order(position.stock_code):
            return {"success": False, "error": "이 종목에 진행 중인 매수 주문이 있습니다."}

        current_price = await self._get_current_price(position.stock_code)
        if not current_price or current_price <= 0:
            return {"success": False, "error": "현재가를 조회할 수 없습니다."}

        planned_amount = int(baseline * pct / 100.0)
        quantity = compute_quantity(planned_amount, current_price)
        if quantity <= 0:
            return {"success": False, "error": "설정 비율로는 1주를 주문할 수 없습니다."}

        account_info = await self._get_account_info()
        if not account_info:
            return {"success": False, "error": "계좌 정보를 조회할 수 없습니다."}
        investable = int(account_info.get("investable_cash") or 0)
        capped_amount = cap_buy_amount_by_cash(planned_amount, investable)
        quantity = compute_quantity(capped_amount, current_price)
        if quantity <= 0:
            return {"success": False, "error": "주문 가능한 예수금이 부족합니다."}

        order_price, order_type = order_params(settings, current_price) if settings else (0, "3")
        result = await self.kiwoom_api.place_buy_order(
            stock_code=position.stock_code,
            quantity=quantity,
            price=order_price,
            order_type=order_type,
        )
        if not result.get("success"):
            return {"success": False, "error": result.get("error") or "물타기 매수 주문 실패"}

        order_id = result.get("order_id", "")
        updated_position = await self._add_to_existing_position(
            position.stock_code, current_price, quantity, order_id,
        )
        if not updated_position:
            return {"success": False, "error": "물타기 주문 후 포지션을 갱신하지 못했습니다."}

        for db in get_db():
            session: Session = db
            record_buy_fill(
                session,
                position_id=updated_position.id,
                stock_code=updated_position.stock_code,
                stock_name=updated_position.stock_name,
                fill_type="ADD",
                price=current_price,
                quantity=quantity,
                order_quantity=quantity,
                order_id=order_id,
                planned_amount=planned_amount,
                sizing_method=getattr(settings, "sizing_method", None),
                note=MANUAL_AVG_DOWN_NOTE,
            )
            pos_row = session.query(Position).filter(Position.id == updated_position.id).first()
            if pos_row:
                cur_ord = int(getattr(pos_row, "order_quantity", None) or 0)
                pos_row.order_quantity = cur_ord + quantity if cur_ord > 0 else quantity
                session.commit()
            break

        order_kind = "시장가" if order_type == "3" else "지정가"
        message = (
            f"수동 물타기 주문 성공({order_kind}) {position.stock_name} "
            f"{quantity}주 · 최초 매수금 {baseline:,}원의 {pct:g}%"
        )
        log_activity("BUY", message, "info", stock_code=position.stock_code, quantity=quantity)
        asyncio.create_task(notify_buy_async(
            stock_name=position.stock_name,
            stock_code=position.stock_code,
            quantity=quantity,
            price=current_price,
            is_add_buy=True,
            order_id=order_id,
            strategy=getattr(position, "strategy_key", None),
        ))
        return {
            "success": True,
            "message": message,
            "position_id": position_id,
            "quantity": quantity,
            "planned_amount": planned_amount,
            "effective_amount": quantity * current_price,
            "pct": pct,
        }
    
    async def _calculate_buy_quantity(
        self,
        stock_code: str,
        current_price: int,
        change_rate: Optional[float] = None,
        is_add_buy: bool = False,
        strategy_key: Optional[str] = None,
        entry_leg: int = 1,
        stop_price: Optional[int] = None,
        suggested_qty: Optional[int] = None,
    ) -> tuple[int, int]:
        """매수 수량 계산 (FIXED / PYRAMIDING, 추가매수 포함).

        Returns:
            (quantity, effective_buy_amount)
        """
        try:
            if not self.auto_trade_settings:
                logger.error("💰 [BUY_EXECUTOR] 자동매매 설정이 없습니다.")
                return 0, 0

            account_info = await self._get_account_info()
            if not account_info:
                logger.warning("💰 [BUY_EXECUTOR] 수량 계산 — 계좌 정보 없음, 매수 보류")
                return 0, 0
            deposit = int(account_info.get("deposit") or 0)
            amount = compute_buy_amount(
                self.auto_trade_settings, change_rate, is_add_buy, deposit=deposit,
            )
            try:
                from utils.auto_trade_engine import (
                    effective_sangtta_buy_amount,
                    effective_breakout_buy_amount,
                    effective_jongga_buy_amount,
                )
                if strategy_key == "sangtta" and not is_add_buy:
                    amount = effective_sangtta_buy_amount(self.auto_trade_settings, deposit=deposit)
                elif strategy_key == "breakout" and not is_add_buy:
                    amount = effective_breakout_buy_amount(self.auto_trade_settings, deposit=deposit)
                elif strategy_key == "jongga":
                    amount = effective_jongga_buy_amount(
                        self.auto_trade_settings,
                        deposit=deposit,
                        entry_leg=entry_leg if entry_leg else (2 if is_add_buy else 1),
                    )
            except Exception:
                pass
            if strategy_key == "fractal" and not is_add_buy:
                # 프랙탈 전용 리스크 기반 수량 산출 플로우:
                # 1) 계좌 × 리스크%로 리스크 원화를 구함 → 1주당 리스크 = entry - stop
                # 2) risk_qty()가 주수(qty)를 반환(옵션으로 주수 상한(fractal_qty_cap) 적용)
                # 3) qty → 금액(amount_from_risk) 변환(amount_from_risk = qty * current_price)
                # 4) (새로 추가) 설정된 금액 상한(fractal_max_amount)이 있으면 그 금액으로 제한
                # 5) 또한 compute_buy_amount로 미리 계산된 baseline 'amount'와의 교차 제한을 적용(더 작은 쪽 선택)
                # 6) 이후 계좌 현금(investable_cash)으로 캡(capping)하고 최종 수량을 재계산
                from utils.ema_fractal import risk_qty
                risk_pct = float(getattr(self.auto_trade_settings, "fractal_risk_pct", None) or 0.5)
                qty_cap = int(getattr(self.auto_trade_settings, "fractal_qty_cap", None) or 0)
                qty = risk_qty(
                    deposit, risk_pct, int(current_price or 0), int(stop_price or 0), qty_cap=qty_cap,
                )
                # risk 기반으로 계산된 금액
                amount_from_risk = qty * int(current_price or 0) if qty > 0 else 0
                # 설정된 금액 상한(0=미적용)
                fractal_max_amount = int(getattr(self.auto_trade_settings, "fractal_max_amount", None) or 0)
                effective_amount = amount_from_risk
                if fractal_max_amount > 0:
                    effective_amount = min(effective_amount, fractal_max_amount)
                # 기존 compute_buy_amount로 계산된 'amount'는 전략별 기본 금액(예: 고정금액/비율 등)입니다.
                # 프랙탈은 리스크 기반 수량을 우선하지만, 추가로 기본 amount보다 초과하지 않도록 보수적으로 제한합니다.
                try:
                    effective_amount = min(effective_amount, int(amount or 0)) if amount and amount > 0 else effective_amount
                except Exception:
                    pass
                amount = int(effective_amount or 0)
            if strategy_key == "ma1592" and not is_add_buy:
                qty_hint = int(suggested_qty or 0)
                if qty_hint > 0 and current_price:
                    amount = qty_hint * int(current_price)
                else:
                    try:
                        from utils.ma1592 import params_from_settings, size_position
                        p = params_from_settings(self.auto_trade_settings)
                        max_invest = int(getattr(self.auto_trade_settings, "ma1592_max_invest_amount", None) or 0)
                        # stop ≈ entry * (1 - hard_break%) → ma15 근사 = stop / (1 - hard)
                        hard = float(p["hard_break_pct"]) / 100.0
                        ma15_approx = float(current_price or 0)
                        if stop_price and hard < 1.0:
                            ma15_approx = float(stop_price) / max(1e-9, (1.0 - hard))
                        sizing = size_position(
                            deposit,
                            int(current_price or 0),
                            ma15_approx,
                            risk_per_trade_pct=float(p["risk_per_trade_pct"]),
                            stop_pct=float(p["stop_pct"]),
                            hard_break_pct=float(p["hard_break_pct"]),
                            max_invest_amount=max_invest,
                            tp1_frac=float(p["tp1_frac"]),
                        )
                        qty_hint = int(sizing.get("qty") or 0)
                    except Exception:
                        qty_hint = 0
                    if qty_hint > 0 and current_price:
                        amount = qty_hint * int(current_price)
            if strategy_key == "ma1592" and is_add_buy:
                qty_hint = int(suggested_qty or 0)
                if qty_hint > 0 and current_price:
                    amount = qty_hint * int(current_price)
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
            return quantity, int(amount or 0)

        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 매수 수량 계산 오류: {e}")
            return 0, 0
    
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
                order_kind = "시장가" if order_type == "3" else "지정가"
                result = await self.kiwoom_api.place_buy_order(
                    stock_code=signal.stock_code,
                    quantity=quantity,
                    price=order_price,
                    order_type=order_type,
                )
                
                if result.get("success"):
                    msg = (
                        f"매수 주문 성공({order_kind}) {signal.stock_name} "
                        f"{quantity}주 @ {current_price:,}원"
                    )
                    logger.info(f"💰 [BUY_EXECUTOR] {msg}")
                    log_activity("BUY", msg, "info", stock_code=signal.stock_code, quantity=quantity)
                    order_id = result.get("order_id", "")
                    await self._update_signal_status(signal.id, "ORDERED", "", order_id)

                    meta = parse_signal_meta(signal)
                    is_add = bool(meta.get("is_add_buy"))
                    strategy = meta.get("strategy")
                    if not strategy and is_add:
                        for db in get_db():
                            pos = (
                                db.query(Position)
                                .filter(
                                    Position.stock_code == signal.stock_code,
                                    Position.status == "HOLDING",
                                )
                                .first()
                            )
                            if pos is not None:
                                strategy = getattr(pos, "strategy_key", None)
                            break
                    asyncio.create_task(notify_buy_async(
                        stock_name=signal.stock_name,
                        stock_code=signal.stock_code,
                        quantity=quantity,
                        price=current_price,
                        is_add_buy=is_add,
                        order_id=order_id,
                        strategy=strategy,
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
                            # 추가매수는 _add_to_existing_position → _after_add_buy_followup에서 동기화
                            if not is_add:
                                asyncio.create_task(
                                    self._update_position_with_actual_price(position.id, signal.stock_code, 5)
                                )
                    except Exception as e:
                        logger.error(f"💰 [BUY_EXECUTOR] 포지션 생성 실패 - {signal.stock_name}: {e}")
                        log_activity("BUY", f"포지션 생성 실패 {signal.stock_name}: {e}", "error",
                                     stock_code=signal.stock_code)
                    
                    return
                else:
                    error_msg = result.get("error", "알 수 없는 오류")
                    logger.warning(f"💰 [BUY_EXECUTOR] 매수 주문 실패 (시도 {attempt + 1}): {error_msg}")
                    log_activity(
                        "BUY",
                        f"매수 실패({order_kind}) {signal.stock_name}: {error_msg}",
                        "error",
                        stock_code=signal.stock_code,
                    )
                    
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
            
            # 키움 API에서 보유종목 정보 조회 (주문 직후 캐시 스킵)
            account_number = Config.KIWOOM_MOCK_ACCOUNT_NUMBER if Config.KIWOOM_USE_MOCK_ACCOUNT else Config.KIWOOM_ACCOUNT_NUMBER
            from api.kiwoom_api import KiwoomAPI, _parse_kiwoom_int
            from utils.api_traffic_guard import APIPriority
            from utils.position_buy_fills import aggregate_buy_fills_from_rows, reconcile_position_buy_with_fills
            from core.models import PositionBuyFill

            norm_code = KiwoomAPI.normalize_stock_code(stock_code)
            target_holding = None
            expected_qty = 0

            for attempt in range(3):
                balance_data = await self.kiwoom_api.get_account_balance(
                    account_number,
                    priority=APIPriority.HIGH,
                    max_wait=12.0,
                    force_refresh=True,
                )

                if not balance_data or 'stk_acnt_evlt_prst' not in balance_data:
                    logger.warning(f"💰 [BUY_EXECUTOR] 보유종목 정보 조회 실패 - Position ID: {position_id}")
                    if attempt < 2:
                        await asyncio.sleep(3)
                        continue
                    return

                holdings = balance_data.get('stk_acnt_evlt_prst', [])
                target_holding = None
                for holding in holdings:
                    holding_code = KiwoomAPI.normalize_stock_code(holding.get('stk_cd', ''))
                    if holding_code == norm_code:
                        target_holding = dict(holding)
                        if balance_data.get("_cached") or balance_data.get("_stale"):
                            target_holding["_cached"] = True
                            if balance_data.get("_stale"):
                                target_holding["_stale"] = True
                        break

                if not target_holding:
                    logger.warning(
                        f"💰 [BUY_EXECUTOR] 보유종목에서 찾을 수 없음 - 종목: {stock_code} "
                        f"(재시도 {attempt + 1}/3)"
                    )
                    if attempt < 2:
                        await asyncio.sleep(3)
                        continue
                    return

                for db in get_db():
                    session: Session = db
                    rows = (
                        session.query(PositionBuyFill)
                        .filter(PositionBuyFill.position_id == position_id)
                        .all()
                    )
                    agg = aggregate_buy_fills_from_rows(rows)
                    expected_qty = int((agg or {}).get("quantity") or 0)
                    break

                api_qty = _parse_kiwoom_int(target_holding.get("qty"))
                if expected_qty > 0 and api_qty < expected_qty and attempt < 2:
                    logger.info(
                        f"💰 [BUY_EXECUTOR] 잔고 수량 미반영({api_qty}<{expected_qty}) — "
                        f"3초 후 재조회 ({attempt + 1}/3)"
                    )
                    await asyncio.sleep(3)
                    continue
                break

            if not target_holding:
                return

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
            strategy = str(meta.get("strategy") or "")
            try:
                entry_leg = int(
                    meta.get("entry_leg")
                    or meta.get("jongga_entry_leg")
                    or meta.get("ymgp_entry_leg")
                    or meta.get("ma1592_entry_leg")
                    or (2 if is_add else 1)
                )
            except (TypeError, ValueError):
                entry_leg = 2 if is_add else 1
            if strategy == "jongga":
                note = f"종가배팅 {entry_leg}차"
            elif strategy == "ymgp":
                note = f"역매공파 {entry_leg}차"
            elif strategy == "ma1592":
                note = f"15/92홀드 {entry_leg}차"
            elif is_add:
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
                if pos_row:
                    cur_ord = int(getattr(pos_row, "order_quantity", None) or 0)
                    # 추가매수 시 주문수량 누적 (기존은 INITIAL만 세팅되어 누락됨)
                    pos_row.order_quantity = cur_ord + quantity if is_add and cur_ord > 0 else max(cur_ord, quantity)
                session.commit()
                logger.info(f"💰 [BUY_EXECUTOR] 매수 체결 이력 저장 — {position.stock_name} {'ADD' if is_add else 'INITIAL'}")
                if strategy == "ma1592":
                    try:
                        from utils.ma1592 import get_universe_store
                        from utils.datetime_kst import now_kst
                        fields = {"entry_leg": max(1, int(entry_leg))}
                        planned = int(meta.get("planned_qty") or 0)
                        if planned > 0:
                            fields["planned_qty"] = planned
                        if entry_leg >= 2:
                            fields["leg2_at"] = now_kst().isoformat(timespec="seconds")
                        store = get_universe_store()
                        if store.get(position.stock_code):
                            store.set_state(position.stock_code, "MANAGE_FULL", **fields)
                    except Exception as e:
                        logger.debug(f"💰 [BUY_EXECUTOR] MA1592 장부 entry_leg 갱신 실패: {e}")
                break
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 매수 체결 이력 저장 오류: {e}")

    async def _update_signal_status(self, signal_id: int, status: str, reason: str = "", order_id: str = ""):
        """신호 상태 업데이트 (실패 사유 포함)"""
        notify_slot: Optional[dict] = None
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
                    if status == "FAILED" and reason:
                        from notifications.trade_alert import is_buy_slot_capacity_reason
                        if is_buy_slot_capacity_reason(reason):
                            meta = parse_signal_meta(signal)
                            notify_slot = {
                                "stock_name": signal.stock_name or "",
                                "stock_code": signal.stock_code or "",
                                "reason": reason,
                                "strategy": meta.get("strategy") or meta.get("source"),
                            }
                    session.commit()
                    if reason:
                        logger.info(f"💰 [BUY_EXECUTOR] 신호 상태 변경: ID {signal_id} -> {status}, reason={reason}")
                    else:
                        logger.info(f"💰 [BUY_EXECUTOR] 신호 상태 변경: ID {signal_id} -> {status}")
                break
        except Exception as e:
            logger.error(f"💰 [BUY_EXECUTOR] 신호 상태 업데이트 오류: {e}")
            return
        if notify_slot:
            try:
                asyncio.create_task(notify_buy_slot_blocked_async(**notify_slot))
            except Exception as e:
                logger.warning(f"💰 [BUY_EXECUTOR] 슬롯 부족 알림 예약 실패: {e}")

# 전역 인스턴스
buy_order_executor = BuyOrderExecutor()
