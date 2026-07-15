import logging
import asyncio
from datetime import datetime, timedelta, date, timezone
from typing import Dict, List, Optional, Tuple
from sqlalchemy.orm import Session

from api.kiwoom_api import KiwoomAPI, _parse_kiwoom_int
from core.models import Position, SellOrder, AutoTradeSettings, get_db
from core.config import Config
from utils.debug_tracer import debug_tracer
from utils.auto_trade_activity_log import log_activity
from utils.market_hours import (
    in_linked_trading_session,
    is_krx_session,
    is_krx_trading_day,
    linked_trading_session_window_str,
    seconds_until_stop_loss_monitoring,
)
from utils.auto_trade_engine import get_auto_trade_settings_sync
from utils.datetime_kst import as_kst, kst_today, now_kst, utc_now_naive, KST
from utils.position_peak_since_buy import (
    buy_time_utc_naive_to_kst,
    max_high_full_holding_days,
    max_high_since_buy_from_intraday_bars,
    resolve_position_peak_price,
    should_disarm_trailing,
)
from notifications.trade_alert import notify_sell_filled_async, sell_fill_snapshot

logger = logging.getLogger(__name__)

# 청산 사유 우선순위 (낮을수록 긴급). TAKE_PROFIT > TRAILING 등 하위 주문 덮어쓰기용.
SELL_REASON_PRIORITY = {
    "MARKET_CLOSE": 0,
    "TAKE_PROFIT": 1,
    "STOP_LOSS": 2,
    "PROFIT_LOCK": 3,
    "TRAILING": 4,
    "MANUAL": 5,
}
STALE_SELL_ORDER_MINUTES = 15


def _buy_age_seconds(pos: Position, *, now: Optional[datetime] = None) -> Optional[float]:
    """매수 후 경과 초(UTC naive buy_time 기준). buy_time 없으면 None."""
    if not pos.buy_time:
        return None
    bt = pos.buy_time
    if bt.tzinfo is not None:
        bt = bt.astimezone(timezone.utc).replace(tzinfo=None)
    ref = now or utc_now_naive()
    if ref.tzinfo is not None:
        ref = ref.astimezone(timezone.utc).replace(tzinfo=None)
    return (ref - bt).total_seconds()


def _within_buy_settle_grace(
    pos: Position,
    *,
    grace_seconds: Optional[int] = None,
    now: Optional[datetime] = None,
) -> bool:
    """매수 직후 잔고 반영 유예 구간이면 True (가짜 계좌청산 방지)."""
    grace = grace_seconds if grace_seconds is not None else int(
        getattr(Config, "BUY_SETTLE_GRACE_SECONDS", 90) or 90
    )
    if grace <= 0:
        return False
    age = _buy_age_seconds(pos, now=now)
    if age is None:
        return False
    return age < grace


def _holding_rows_for_code(session: Session, stock_code: str) -> List[Position]:
    code = KiwoomAPI.normalize_stock_code(stock_code)
    if not code:
        return []
    return [
        p for p in session.query(Position).filter(Position.status == "HOLDING").all()
        if KiwoomAPI.normalize_stock_code(p.stock_code) == code
    ]


def _collapse_duplicate_holdings(session: Session) -> int:
    """같은 종목 HOLDING 중복 — 최신 buy_time 1건만 유지, 나머지는 청산 상태로 정리."""
    from collections import defaultdict

    groups: Dict[str, List[Position]] = defaultdict(list)
    for pos in session.query(Position).filter(Position.status == "HOLDING").all():
        code = KiwoomAPI.normalize_stock_code(pos.stock_code)
        if code:
            groups[code].append(pos)

    collapsed = 0
    for code, rows in groups.items():
        if len(rows) <= 1:
            continue
        rows.sort(key=lambda p: p.buy_time or datetime.min, reverse=True)
        keep = rows[0]
        for dup in rows[1:]:
            last_done = session.query(SellOrder).filter(
                SellOrder.position_id == dup.id,
                SellOrder.status == "COMPLETED",
            ).order_by(SellOrder.completed_at.desc()).first()
            dup.status = (last_done.sell_reason if last_done else "DUPLICATE_HOLDING")
            dup.sell_time = (last_done.completed_at if last_done else utc_now_naive())
            logger.warning(
                f"🛡️ [RECONCILE] 중복 HOLDING 정리 — {dup.stock_name} "
                f"#{dup.id} → {dup.status} (유지 #{keep.id})"
            )
            collapsed += 1
    if collapsed:
        session.flush()
    return collapsed


def _sell_reason_rank(reason: Optional[str]) -> int:
    return SELL_REASON_PRIORITY.get(reason or "", 9)


class StopLossManager:
    """손절/익절 모니터링 매니저"""
    
    def __init__(self):
        self.kiwoom_api = KiwoomAPI()
        self.is_running = False
        self.monitoring_interval = 30  # 30초마다 모니터링 (잔고·차트 캐시로 API 부하 완화)
        self.auto_trade_settings = None
        # 일봉 ATR — 종목당 하루 1회 계산 (장중 변동성만 반영)
        self._atr_daily_cache: Dict[str, Tuple[float, date]] = {}
        self._since_buy_peak_cache: Dict[str, Tuple[int, str]] = {}  # code -> (peak, buy_iso)
        self._last_cycle_at: Optional[datetime] = None
        self._last_heartbeat_msg: Optional[str] = None
        self._loop_active = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._off_hours_logged = False

    def _settings_for_session(self) -> Optional[AutoTradeSettings]:
        return get_auto_trade_settings_sync()

    def invalidate_settings_cache(self) -> None:
        """설정 저장 후 인메모리 캐시 제거."""
        self.auto_trade_settings = None

    def is_monitoring_active(self) -> bool:
        """매매 시간 연동 세션 여부 (UI·API용 — 루프 태스크와 별개)."""
        settings = self._settings_for_session()
        return in_linked_trading_session(settings)

    def monitoring_task_running(self) -> bool:
        return self._loop_active or (
            self._monitor_task is not None and not self._monitor_task.done()
        )

    def schedule_monitoring(self) -> bool:
        """모니터링 루프 1개만 예약 (동기 — create_task 전에 호출)."""
        if self.monitoring_task_running():
            return False
        self.is_running = True
        return True

    def attach_monitor_task(self, task: asyncio.Task) -> None:
        self._monitor_task = task

    async def start_monitoring(self):
        """손절/익절 모니터링 시작"""
        if self._loop_active:
            logger.debug("🛡️ [STOP_LOSS] 모니터링 루프 이미 실행 중 — 중복 시작 무시")
            return
        self._loop_active = True
        if not self.is_running:
            self.is_running = True

        logger.info("🛡️ [STOP_LOSS] 손절/익절 모니터링 시작")
        log_activity("SELL", f"손절/익절 모니터 시작 ({self.monitoring_interval}초 주기)", "info")
        
        try:
            while self.is_running:
                await self._load_auto_trade_settings()

                if not self.is_monitoring_active():
                    if not self._off_hours_logged:
                        settings = self._settings_for_session()
                        nxt = seconds_until_stop_loss_monitoring(settings)
                        window = (
                            linked_trading_session_window_str(settings)
                            if settings
                            else "매매시간"
                        )
                        mins = max(1, nxt // 60)
                        log_activity(
                            "SELL",
                            f"장외 — 손절/익절 모니터 일시 중지 ({window}, 약 {mins}분 후 재개)",
                            "info",
                        )
                        logger.info(
                            "🛡️ [STOP_LOSS] 장외 대기 — 다음 모니터까지 %ds (%s)",
                            nxt,
                            window,
                        )
                        self._off_hours_logged = True
                    settings = self._settings_for_session()
                    await asyncio.sleep(seconds_until_stop_loss_monitoring(settings))
                    continue

                if self._off_hours_logged:
                    log_activity(
                        "SELL",
                        f"손절/익절 모니터 재개 ({self.monitoring_interval}초 주기)",
                        "info",
                    )
                    self._off_hours_logged = False

                # 매도 체결 확인 → DB 동기화 (주문 접수와 청산 확정 분리)
                await self._reconcile_sell_orders_and_holdings()
                
                # kt00004 잔고 기준 평가손익 동기화 (종목별 현재가 API는 장중에만)
                await self._update_all_positions_price()

                # 장마감 전량청산 — 자동매매 ON/OFF 무관 (설정만 켜져 있으면 실행)
                if self._is_in_liquidation_window():
                    await self._run_market_close_liquidation()
                    await self._log_cycle_heartbeat(mode="장마감청산")
                    await asyncio.sleep(30)
                    continue
                elif is_krx_session() and self.auto_trade_settings:
                    # 보유 포지션 청산 판단은 자동매매 ON/OFF·매매종료(15:20)와 무관하게 장중(09:00~15:30) 수행
                    await self._monitor_positions()
                    await self._log_cycle_heartbeat(mode="손절점검")
                else:
                    logger.debug("🛡️ [STOP_LOSS] 장외 — 손절·익절 판단 건너뜀")
                    await self._log_cycle_heartbeat(mode="동기화")
                
                await asyncio.sleep(self.monitoring_interval)
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 모니터링 중 오류: {e}")
        finally:
            self.is_running = False
            self._loop_active = False
            self._monitor_task = None
            logger.info("🛡️ [STOP_LOSS] 손절/익절 모니터링 종료")
    
    async def stop_monitoring(self):
        """손절/익절 모니터링 중지"""
        logger.info("🛡️ [STOP_LOSS] 손절/익절 모니터링 중지 요청")
        log_activity("SELL", "손절/익절 모니터 중지", "warn")
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

    @staticmethod
    def overlay_global_exit_settings(
        ex: Optional[dict],
        buy_price: int,
        settings: Optional["AutoTradeSettings"],
    ) -> dict:
        """exit_levels에 전역 설정 손절율·%손절가를 명시 (포지션 스냅샷과 UI 불일치 방지)."""
        out = dict(ex or {})
        if not settings or not buy_price:
            return out
        sl = StopLossManager._num(settings.stop_loss_rate)
        if sl:
            pct_px = int(buy_price * (1 - abs(sl) / 100.0))
            out["stop_loss_rate"] = sl
            out["stop_loss_price_pct"] = pct_px
            levels = [
                lv for lv in (out.get("levels") or [])
                if not (lv.get("reason") == "STOP_LOSS" and lv.get("method") == "PCT")
            ]
            levels.append({"reason": "STOP_LOSS", "price": pct_px, "method": "PCT"})
            out["levels"] = levels
            if levels:
                best = max(levels, key=lambda lv: int(lv["price"]))
                out["effective_stop_price"] = int(best["price"])
                out["effective_stop_reason"] = best["reason"]
                cur = int(out.get("current_price") or 0)
                if cur > 0:
                    out["stop_distance_pct"] = round(
                        (cur - int(best["price"])) / cur * 100, 2,
                    )
        tp = StopLossManager._num(settings.take_profit_rate)
        if tp is not None:
            out["take_profit_rate_setting"] = tp
        return out

    @staticmethod
    def propagate_exit_settings_to_holdings(session: Session) -> int:
        """설정 저장 시 보유 포지션의 손절/익절 % 스냅샷을 전역 설정과 동기화."""
        from core.models import AutoTradeSettings, Position

        settings = session.query(AutoTradeSettings).first()
        if not settings:
            return 0
        updated = 0
        for pos in session.query(Position).filter(Position.status == "HOLDING").all():
            pos.stop_loss_rate = settings.stop_loss_rate
            pos.take_profit_rate = settings.take_profit_rate
            updated += 1
        return updated

    async def _log_cycle_heartbeat(self, mode: str = "동기화"):
        """모니터링 주기마다 — 보유·손익 요약을 활동 로그에 남김 (대시보드 가시성)."""
        try:
            rows: List[Position] = []
            for db in get_db():
                rows = db.query(Position).filter(Position.status == "HOLDING").all()
                break

            pl_sum = sum(int(p.current_profit_loss or 0) for p in rows)
            auto_on = bool(self.auto_trade_settings and self.auto_trade_settings.is_enabled)
            session_txt = "장중" if is_krx_session() else "장외"
            snippets = [
                f"{p.stock_name} {float(p.current_profit_loss_rate or 0):+.1f}%"
                for p in rows[:4]
            ]
            if len(rows) > 4:
                snippets.append(f"+{len(rows) - 4}종")
            detail = ", ".join(snippets) if snippets else "보유 없음"

            msg = (
                f"{mode} · {session_txt} · 자동매매 {'ON' if auto_on else 'OFF'} · "
                f"HOLDING {len(rows)} · 합산 {pl_sum:+,}원 · {detail}"
            )
            now = now_kst()
            if (
                self._last_heartbeat_msg == msg
                and self._last_cycle_at
                and (now - self._last_cycle_at).total_seconds() < 30
            ):
                return
            log_activity("SYNC", msg, "info")
            self._last_heartbeat_msg = msg
            self._last_cycle_at = now
        except Exception as e:
            logger.debug(f"🛡️ [STOP_LOSS] heartbeat 로그 생략: {e}")
    
    @debug_tracer.trace_async(component="STOP_LOSS")
    async def _monitor_positions(self):
        """포지션 모니터링"""
        try:
            debug_tracer.log_checkpoint("포지션 조회 시작", "STOP_LOSS")
            
            # HOLDING 상태인 포지션들 조회
            positions = await self._get_active_positions()
            
            debug_tracer.log_checkpoint(f"조회된 포지션 개수: {len(positions)}", "STOP_LOSS")
            
            if not positions:
                logger.debug("🛡️ [STOP_LOSS] 모니터링할 포지션이 없습니다.")
                return
            
            logger.info(f"🛡️ [STOP_LOSS] {len(positions)}개 포지션 모니터링 중...")
            holdings_map, _ = await self._fetch_balance_holdings()

            for idx, position in enumerate(positions, 1):
                try:
                    debug_tracer.log_checkpoint(f"[{idx}/{len(positions)}] 포지션 점검: {position.stock_name}({position.stock_code})", "STOP_LOSS")
                    code = KiwoomAPI.normalize_stock_code(position.stock_code)
                    await self._check_position_stop_loss(position, holdings_map.get(code))
                except Exception as e:
                    logger.error(f"🛡️ [STOP_LOSS] 포지션 모니터링 오류 (ID: {position.id}): {e}")
                
                # API 제한을 고려한 대기 (키움 제한: 1분당 20회)
                debug_tracer.log_checkpoint(f"[{idx}/{len(positions)}] 포지션 점검 완료, 5초 대기", "STOP_LOSS")
                await asyncio.sleep(5)
                
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 포지션 모니터링 중 오류: {e}")

    async def _run_market_close_liquidation(self):
        """키움 계좌 보유 전 종목 장마감 전량청산 (자동매매 ON/OFF 무관)."""
        s = self.auto_trade_settings
        liq_time = getattr(s, "liquidate_time", "15:10") if s else "15:10"
        try:
            holdings_map, _ = await self._fetch_balance_holdings()
            if not holdings_map:
                logger.debug("🛡️ [STOP_LOSS] 장마감 청산 — 보유 종목 없음")
                return

            targets: List[Tuple[int, dict]] = []
            for db in get_db():
                session: Session = db
                for code, holding in holdings_map.items():
                    qty = _parse_kiwoom_int(holding.get("qty"))
                    if qty <= 0:
                        continue
                    pos = None
                    for p in _holding_rows_for_code(session, code):
                        pos = p
                        break
                    if not pos:
                        logger.warning(
                            f"🛡️ [STOP_LOSS] 장마감 청산 — DB HOLDING 포지션 없음 ({code}), "
                            f"키움 {holding.get('stk_nm', code)} {qty}주"
                        )
                        continue
                    from utils.eval_pnl import apply_holding_to_position
                    apply_holding_to_position(pos, holding)
                    targets.append((int(pos.id), holding))
                session.commit()
                break

            if not targets:
                return

            logger.warning(
                f"🛡️ [STOP_LOSS] 장마감 전량청산 시작 ({liq_time}) — {len(targets)}종목"
            )
            log_activity(
                "SELL",
                f"장마감 전량청산 시작 — {len(targets)}종목 ({liq_time})",
                "warn",
            )

            for idx, (position_id, holding) in enumerate(targets, 1):
                position = None
                stock_label = str(holding.get("stk_nm") or position_id)
                try:
                    for db in get_db():
                        position = db.query(Position).filter(Position.id == position_id).first()
                        break
                    if not position:
                        log_activity(
                            "SELL",
                            f"장마감 청산 생략 — 포지션 #{position_id} DB 없음",
                            "warn",
                        )
                        continue
                    stock_label = position.stock_name

                    # 장마감 청산은 최우선 사유이므로,
                    # 다른(하위 우선순위) 매도 주문이 있더라도 먼저 취소하고 실행합니다.
                    if await self._has_any_pending_sell_order(position.id):
                        await self._prepare_sell(position.id, "MARKET_CLOSE")

                    if await self._has_pending_sell_order(position.id, for_reason="MARKET_CLOSE"):
                        msg = f"장마감 청산 대기 — {position.stock_name} (매도 주문 진행 중)"
                        logger.info(f"🛡️ [STOP_LOSS] {msg}")
                        log_activity("SELL", msg, "info", stock_code=position.stock_code, reason="MARKET_CLOSE")
                        continue

                    cur = _parse_kiwoom_int(holding.get("cur_pr")) or position.current_price
                    if not cur:
                        cur = await self._get_current_price(position.stock_code)
                    if not cur:
                        msg = f"장마감 청산 생략 — {position.stock_name}: 현재가 없음"
                        logger.warning(f"🛡️ [STOP_LOSS] {msg}")
                        log_activity("SELL", msg, "warn", stock_code=position.stock_code, reason="MARKET_CLOSE")
                        continue

                    pl, rate = self._calc_profit(position, int(cur), holding)
                    detail = (
                        f"장마감 전량청산 ({liq_time}) | "
                        f"{position.buy_quantity}주 | 손익 {rate:+.2f}%"
                    )
                    logger.warning(
                        f"🛡️ [STOP_LOSS] 장마감 청산 — {position.stock_name}: {rate:+.2f}%"
                    )
                    await self._execute_sell_order(position, int(cur), "MARKET_CLOSE", detail)
                    if idx < len(targets):
                        await asyncio.sleep(2)
                except Exception as e:
                    logger.error(f"🛡️ [STOP_LOSS] 장마감 청산 오류 — {stock_label}: {e}")
                    log_activity(
                        "SELL",
                        f"장마감 청산 오류 — {stock_label}: {e}",
                        "warn",
                        reason="MARKET_CLOSE",
                    )
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 장마감 전량청산 중 오류: {e}")
    
    async def _get_active_positions(self) -> List[Position]:
        """활성 포지션 조회 (실제 보유 종목과 대조)"""
        positions = []
        for db in get_db():
            try:
                session: Session = db
                db_positions = session.query(Position).filter(
                    Position.status == "HOLDING"
                ).all()
                
                # 실제 계좌 보유 종목 조회 (선택적 - 실패해도 계속 진행)
                account_number = Config.KIWOOM_MOCK_ACCOUNT_NUMBER if Config.KIWOOM_USE_MOCK_ACCOUNT else Config.KIWOOM_ACCOUNT_NUMBER
                account_balance = None
                actual_holdings = set()
                
                try:
                    account_balance = await self.kiwoom_api.get_account_balance(account_number)
                    
                    # 실제 보유 종목 코드 목록
                    if account_balance and 'stk_acnt_evlt_prst' in account_balance:
                        for holding in account_balance['stk_acnt_evlt_prst']:
                            actual_holdings.add(
                                KiwoomAPI.normalize_stock_code(holding.get('stk_cd', ''))
                            )
                        logger.debug(f"🛡️ [STOP_LOSS] 실제 보유 종목: {len(actual_holdings)}개 - {actual_holdings}")
                    else:
                        logger.debug(f"🛡️ [STOP_LOSS] 계좌 조회 결과 없음 (API 제한 또는 보유 종목 없음)")
                except Exception as e:
                    logger.debug(f"🛡️ [STOP_LOSS] 계좌 조회 실패 (계속 진행): {e}")
                
                # 계좌 조회 성공 시에만 검증, 실패 시에는 DB의 모든 HOLDING Position 사용
                if actual_holdings:
                    # DB 포지션 중 실제로 보유한 종목만 필터링
                    verified_positions = []
                    excluded_count = 0
                    for pos in db_positions:
                        if KiwoomAPI.normalize_stock_code(pos.stock_code) in actual_holdings:
                            verified_positions.append(pos)
                            logger.debug(f"🛡️ [STOP_LOSS] 포지션 검증 완료: {pos.stock_name}({pos.stock_code})")
                        else:
                            excluded_count += 1
                    
                    if excluded_count > 0:
                        logger.debug(f"🛡️ [STOP_LOSS] 실제 보유하지 않은 포지션 {excluded_count}개 제외됨")
                    
                    positions = verified_positions
                else:
                    # 계좌 조회 실패 시 DB의 모든 HOLDING Position 사용 (현재가 업데이트는 계속 수행)
                    # API 제한으로 인한 실패는 정상적인 상황이므로 WARNING 대신 DEBUG로 로그
                    positions = db_positions
                    logger.debug(f"🛡️ [STOP_LOSS] 계좌 조회 실패 (API 제한 가능) - DB의 모든 HOLDING Position 사용 ({len(positions)}개)")
                break
            except Exception as e:
                logger.error(f"🛡️ [STOP_LOSS] 포지션 조회 오류: {e}")
                import traceback
                logger.error(f"🛡️ [STOP_LOSS] 스택 트레이스: {traceback.format_exc()}")
                continue
        
        return positions
    
    async def _fetch_balance_holdings(self) -> Tuple[Dict[str, dict], Optional[dict]]:
        """키움 계좌 잔고 → (종목별 보유 dict, raw balance)."""
        from utils.eval_pnl import holdings_by_code

        account_number = (
            Config.KIWOOM_MOCK_ACCOUNT_NUMBER
            if Config.KIWOOM_USE_MOCK_ACCOUNT
            else Config.KIWOOM_ACCOUNT_NUMBER
        )
        balance = await self.kiwoom_api.get_account_balance(account_number)
        return holdings_by_code(balance), balance

    async def sync_holdings_from_api(self, force: bool = False):
        """키움 잔고 ↔ DB 포지션 동기화 (체결 확인 + API 평가손익).

        force는 하위 호환용 — 잔고(kt00004) 동기화는 장외에도 수행, 종목별 현재가 API는 장중만.
        """
        await self._reconcile_sell_orders_and_holdings()
        await self._update_all_positions_price()

    async def _update_all_positions_price(self):
        """계좌 보유 종목 ↔ DB 포지션 — 키움 kt00004 API(현재가·매입·평가손익) 동기화."""
        try:
            holdings_map, balance = await self._fetch_balance_holdings()
            if balance and balance.get("_error"):
                logger.debug(
                    f"🛡️ [STOP_LOSS] 잔고 API 실패 — 가격 동기화 생략 ({balance.get('_error_msg', '')})"
                )
                return
            account_codes = set(holdings_map.keys())
            for db in get_db():
                session: Session = db
                positions = session.query(Position).filter(Position.status == "HOLDING").all()
                if not positions:
                    logger.debug("🛡️ [STOP_LOSS] 업데이트할 HOLDING 포지션이 없습니다.")
                    return

                logger.info(f"🛡️ [STOP_LOSS] {len(positions)}개 포지션 API 동기화 중...")
                from utils.eval_pnl import apply_holding_to_position, calc_profit_for_position

                for idx, position in enumerate(positions, 1):
                    try:
                        code = KiwoomAPI.normalize_stock_code(position.stock_code)
                        holding = holdings_map.get(code)

                        if holding:
                            apply_holding_to_position(position, holding)
                            position.last_monitored = utc_now_naive()
                            logger.debug(
                                f"🛡️ [STOP_LOSS] API 동기화 — {position.stock_name}: "
                                f"{position.current_profit_loss:+,}원 ({position.current_profit_loss_rate:+.2f}%)"
                            )
                        elif code not in account_codes:
                            logger.debug(
                                f"🛡️ [STOP_LOSS] 계좌 미보유 — DB 값 유지 ({position.stock_name}, {code})"
                            )
                        elif not is_krx_session():
                            logger.debug(
                                f"🛡️ [STOP_LOSS] 장외 — DB 값 유지 ({position.stock_name})"
                            )
                        else:
                            current_price = await self._get_current_price(position.stock_code)
                            if current_price and current_price > 0:
                                pl, rate = calc_profit_for_position(position, current_price)
                                position.current_price = current_price
                                position.current_profit_loss = pl
                                position.current_profit_loss_rate = rate
                                position.last_monitored = utc_now_naive()
                            else:
                                logger.info(
                                    f"🛡️ [STOP_LOSS] 현재가 미확인 — DB 값 유지 ({position.stock_name})"
                                )

                        if (
                            idx < len(positions)
                            and not holding
                            and is_krx_session()
                            and code in account_codes
                        ):
                            await asyncio.sleep(5)
                    except Exception as e:
                        logger.error(f"🛡️ [STOP_LOSS] 포지션 동기화 오류 (ID: {position.id}): {e}")

                session.commit()
                logger.info(f"🛡️ [STOP_LOSS] {len(positions)}개 포지션 API 동기화 완료")
                break
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 포지션 동기화 중 오류: {e}")
            import traceback
            logger.error(f"🛡️ [STOP_LOSS] 스택 트레이스: {traceback.format_exc()}")
    
    async def compute_exit_levels(self, position: Position, live: bool = False) -> Dict:
        """청산 레벨 스냅샷. live=False: DB값만(빠름), live=True: API로 현재가·ATR."""
        await self._load_auto_trade_settings()
        s = self.auto_trade_settings
        if not s:
            return {}

        api_live = live and is_krx_session()
        if live and not api_live:
            logger.debug(f"장외 시간 — live=false 처리 ({position.stock_name})")

        current_price = position.current_price or position.buy_price
        holding = None
        fetched_live = None
        if api_live:
            try:
                holdings_map, _ = await self._fetch_balance_holdings()
                holding = holdings_map.get(KiwoomAPI.normalize_stock_code(position.stock_code))
                if holding:
                    from utils.eval_pnl import apply_holding_to_position
                    apply_holding_to_position(position, holding)
                    current_price = position.current_price or current_price
                else:
                    fetched_live = await asyncio.wait_for(
                        self._get_current_price(position.stock_code), timeout=6.0,
                    )
                    if fetched_live:
                        current_price = fetched_live
            except asyncio.TimeoutError:
                logger.warning(f"현재가 조회 타임아웃 — {position.stock_name}")

        buy_price = position.buy_price or current_price
        # 당일 일봉 고가는 live 여부와 무관하게 조회 (모니터링 주기 사이 급등 고점 누락 방지)
        peak = await self._resolve_position_peak(
            position, int(current_price), allow_api=True,
        )
        profit_loss, profit_loss_rate = self._calc_profit(position, int(current_price), holding)

        if api_live and position.id and (fetched_live or holding):
            await self._update_position_price(
                position.id, int(current_price), profit_loss, profit_loss_rate,
            )
            if holding:
                await self._sync_position_from_api(position.id, holding)

        if position.id and getattr(position, "status", None) == "HOLDING":
            stored_peak = int(getattr(position, "peak_price", None) or buy_price)
            if peak != stored_peak:
                await self._update_position_tracking(position.id, peak, None)
                position.peak_price = peak

        tp = self._num(s.take_profit_rate)
        trail_start = tp if tp and tp > 0 else None
        peak_rate = self._peak_rate_pct(buy_price, peak)
        trailing_armed, trailing_floor = await self._guard_trailing_arm_state(
            position, buy_price, peak, trail_start,
        )
        trailing_start_price = (
            int(buy_price * (1 + trail_start / 100.0)) if trail_start else None
        )

        atr_stop_mult = self._num(s.atr_mult_stop)
        atr_trail_mult = self._num(s.atr_mult_trail)
        atr_period = int(self._num(s.atr_period) or 14)
        atr = None
        if atr_stop_mult or atr_trail_mult:
            atr, atr_period = await self._resolve_position_atr(
                position, s, allow_api=bool(api_live or live),
            )

        candidates = self._build_stop_candidates(
            s, buy_price, peak, atr,
            trailing_armed=trailing_armed,
            trailing_floor_price=trailing_floor,
        )

        eff_reason = None
        eff_stop = None
        if candidates:
            eff_reason, eff_stop, _ = max(candidates, key=lambda x: x[1])

        stored_stop = int(getattr(position, "stop_loss_price", None) or 0) or None
        effective = int(eff_stop) if eff_stop else stored_stop

        dist_pct = None
        if effective and current_price:
            dist_pct = (current_price - effective) / current_price * 100

        peak_drop_amount = None
        peak_drop_pct = None
        if peak and current_price and peak > 0:
            peak_drop_amount = max(0, int(peak) - int(current_price))
            peak_drop_pct = round(peak_drop_amount / peak * 100, 2)

        trailing_stop_pct = self._num(s.trailing_stop_pct)
        level_rows = []
        for reason, price, method in candidates:
            level_rows.append({
                "reason": reason,
                "price": int(price),
                "method": method,
            })

        sl_rate = self._num(s.stop_loss_rate)
        stop_loss_price_pct = (
            int(buy_price * (1 - abs(sl_rate) / 100.0)) if sl_rate and buy_price else None
        )

        return {
            "current_price": int(current_price),
            "peak_price": int(peak),
            "profit_loss": profit_loss,
            "profit_loss_rate": round(profit_loss_rate, 2),
            "atr": round(atr, 1) if atr else None,
            "atr_period": atr_period,
            "atr_mult_stop": atr_stop_mult,
            "atr_mult_trail": atr_trail_mult,
            "trailing_start_rate": trail_start,
            "trailing_start_price": trailing_start_price,
            "trailing_active": trailing_armed,
            "trailing_armed": trailing_armed,
            "trailing_floor_price": trailing_floor,
            "take_profit_price": trailing_start_price,
            "take_profit_rate": tp,
            "effective_stop_price": effective,
            "effective_stop_reason": eff_reason,
            "stop_loss_rate": sl_rate,
            "stop_loss_price_pct": stop_loss_price_pct,
            "stored_stop_loss_price": stored_stop,
            "stop_distance_pct": round(dist_pct, 2) if dist_pct is not None else None,
            "peak_drop_amount": peak_drop_amount,
            "peak_drop_pct": peak_drop_pct,
            "trailing_stop_pct": trailing_stop_pct,
            "levels": level_rows,
            "liquidate_time": getattr(s, "liquidate_time", None) if getattr(s, "liquidate_before_close", False) else None,
            "levels_live": api_live,
        }

    @staticmethod
    def _num(v) -> Optional[float]:
        """설정값을 float로 안전 변환. None/빈문자/변환실패는 None."""
        try:
            if v is None or v == "":
                return None
            return float(v)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _peak_rate_pct(buy_price: int, peak: int) -> float:
        if not buy_price:
            return 0.0
        return (peak - buy_price) / buy_price * 100

    async def _resolve_position_peak(
        self,
        position: Position,
        current_price: int,
        *,
        allow_api: bool = True,
    ) -> int:
        """진입 후 고점 — 저장값·현재가·매수 시각 이후 차트 고가만 반영."""
        buy_price = int(position.buy_price or current_price or 0)
        stored = int(getattr(position, "peak_price", None) or buy_price)
        since_buy_high = 0

        if allow_api:
            code = KiwoomAPI.normalize_stock_code(position.stock_code or "")
            if code:
                since_buy_high = await self._fetch_peak_high_since_buy(position, code)

        peak = resolve_position_peak_price(
            buy_price=buy_price,
            current_price=int(current_price or 0),
            stored_peak=stored,
            since_buy_high=since_buy_high,
            allow_api=allow_api,
        )

        if allow_api and stored > peak:
            logger.info(
                f"🛡️ [STOP_LOSS] 고점 보정 — {position.stock_name}: "
                f"stored {stored:,} → {peak:,} (매수 이후 고점만)"
            )

        return peak

    async def _fetch_peak_high_since_buy(self, position: Position, code: str) -> int:
        """매수 시각(KST) 이후 분봉·중간 일봉 고가."""
        buy_kst = buy_time_utc_naive_to_kst(getattr(position, "buy_time", None))
        if buy_kst is None:
            return 0

        buy_iso = buy_kst.isoformat()
        cached = self._since_buy_peak_cache.get(code)
        if cached and cached[1] == buy_iso:
            return cached[0]

        buy_date = buy_kst.date()
        today = kst_today()
        peak = 0
        session_open = datetime(
            buy_date.year, buy_date.month, buy_date.day, 9, 0, tzinfo=KST,
        )

        try:
            daily_bars = await self.kiwoom_api.get_stock_chart_data(
                code, "1D", allow_off_hours=True,
            )
            peak = max(peak, max_high_full_holding_days(daily_bars, buy_date, today))
        except Exception as e:
            logger.debug(f"🛡️ [STOP_LOSS] 일봉 고가 조회 실패 {position.stock_name}: {e}")

        intraday_dates = []
        if buy_date == today:
            intraday_dates.append(today)
        else:
            intraday_dates.append(buy_date)
            if today > buy_date:
                intraday_dates.append(today)

        for trade_date in intraday_dates:
            try:
                result = await self.kiwoom_api.get_intraday_chart_for_date(
                    code, trade_date.isoformat(), tic_scope="15",
                )
                bars = result.get("bars") or []
                if trade_date == buy_date:
                    cutoff = buy_kst
                else:
                    cutoff = session_open.replace(
                        year=trade_date.year,
                        month=trade_date.month,
                        day=trade_date.day,
                    )
                peak = max(
                    peak,
                    max_high_since_buy_from_intraday_bars(bars, cutoff),
                )
            except Exception as e:
                logger.debug(
                    f"🛡️ [STOP_LOSS] 분봉 고가 조회 실패 {position.stock_name} "
                    f"{trade_date}: {e}"
                )

        self._since_buy_peak_cache[code] = (peak, buy_iso)
        return peak

    async def _disarm_trailing(self, position: Position, *, reason: str) -> None:
        """잘못 활성화된 트레일링 해제."""
        if not getattr(position, "id", None):
            return
        try:
            changed = False
            for db in get_db():
                session: Session = db
                p = session.query(Position).filter(Position.id == position.id).first()
                if not p:
                    break
                if p.trailing_armed or p.trailing_floor_price:
                    p.trailing_armed = False
                    p.trailing_floor_price = None
                    changed = True
                    session.commit()
                break
            if changed:
                position.trailing_armed = False
                position.trailing_floor_price = None
                logger.warning(
                    f"🛡️ [STOP_LOSS] 트레일링 해제 — {position.stock_name}: {reason}"
                )
                log_activity(
                    "SELL",
                    f"트레일링 해제 — {position.stock_name}: {reason}",
                    "warn",
                    stock_code=position.stock_code,
                )
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 트레일링 해제 오류 {position.stock_name}: {e}")

    async def _guard_trailing_arm_state(
        self,
        position: Position,
        buy_price: int,
        peak: int,
        trail_start_val: Optional[float],
    ) -> Tuple[bool, Optional[int]]:
        """고점 수익률 미달 시 오활성화 트레일링 방어."""
        trailing_armed, trailing_floor = self._resolve_trailing_state(
            position, buy_price, peak, trail_start_val,
        )
        if should_disarm_trailing(
            trailing_armed=bool(getattr(position, "trailing_armed", False) or trailing_armed),
            trail_start_rate=trail_start_val,
            buy_price=buy_price,
            peak=peak,
        ):
            await self._disarm_trailing(
                position,
                reason=f"고점 수익률 {StopLossManager._peak_rate_pct(buy_price, peak):.2f}% "
                f"< 시작 {trail_start_val}% (매수 이후 고점 기준)",
            )
            return False, None
        return trailing_armed, trailing_floor

    @staticmethod
    def _trailing_floor_price(buy_price: int, trail_start_rate: float) -> int:
        return int(buy_price * (1 + trail_start_rate / 100.0))

    def _resolve_trailing_state(
        self,
        position: Position,
        buy_price: int,
        peak: int,
        trail_start_rate: Optional[float],
    ) -> Tuple[bool, Optional[int]]:
        """패턴 B: 시작% 도달 시 armed + floor. armed 후 평균단가 상승 시 floor 상향(추가매수)."""
        stored_armed = bool(getattr(position, "trailing_armed", False))
        stored_floor = getattr(position, "trailing_floor_price", None)

        if trail_start_rate is None or trail_start_rate <= 0:
            return True, None

        peak_rate = self._peak_rate_pct(buy_price, peak)
        if stored_armed:
            if peak_rate < trail_start_rate:
                return False, None
            floor = self._trailing_floor_for_buy(
                buy_price, trail_start_rate, stored_floor, peak,
            )
            return True, floor

        if peak_rate >= trail_start_rate:
            return True, self._trailing_floor_price(buy_price, trail_start_rate)

        return False, None

    def _trailing_floor_for_buy(
        self,
        buy_price: int,
        trail_start_rate: float,
        stored_floor: Optional[int],
        peak: int,
    ) -> int:
        """평균단가×시작% 바닥 — 고점이 그 가격 이상일 때만 상향(바닥>현재가 즉시청산 방지)."""
        target = self._trailing_floor_price(buy_price, trail_start_rate)
        old = int(stored_floor or 0)
        if peak < target:
            return old if old > 0 else target
        return max(old, target) if old > 0 else target

    async def _persist_trailing_floor(
        self,
        position_id: int,
        floor_price: int,
        *,
        arm: bool = False,
    ) -> None:
        """트레일링 바닥 저장 — 최초 활성화 또는 평균단가 상승 시 바닥 상향."""
        try:
            for db in get_db():
                session: Session = db
                p = session.query(Position).filter(Position.id == position_id).first()
                if not p:
                    break
                new_floor = int(floor_price)
                old_floor = int(p.trailing_floor_price or 0)
                changed = False
                if arm and not p.trailing_armed:
                    p.trailing_armed = True
                    changed = True
                if new_floor > old_floor:
                    p.trailing_floor_price = new_floor
                    changed = True
                if not changed:
                    break
                session.commit()
                if arm and not old_floor:
                    logger.info(
                        f"🛡️ [STOP_LOSS] 트레일링 활성 — {p.stock_name} "
                        f"바닥 {new_floor:,}원"
                    )
                elif new_floor > old_floor:
                    logger.info(
                        f"🛡️ [STOP_LOSS] 트레일링 바닥 상향 — {p.stock_name} "
                        f"{old_floor:,}→{new_floor:,}원 (평단+시작%, 고점 확인 후)"
                    )
                break
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 트레일링 바닥 저장 오류: {e}")

    async def refresh_trailing_floor_for_position(self, position_id: int) -> None:
        """추가매수 등으로 평균단가가 바뀐 뒤 트레일링 바닥 재계산."""
        await self._load_auto_trade_settings()
        s = self.auto_trade_settings
        if not s:
            return
        trail_start = self._num(s.take_profit_rate)
        if not trail_start or trail_start <= 0:
            return
        try:
            for db in get_db():
                session: Session = db
                p = session.query(Position).filter(Position.id == position_id).first()
                if not p or p.status not in ("HOLDING", "TRAILING"):
                    return
                buy_price = int(p.buy_price or 0)
                if buy_price <= 0:
                    return
                peak = max(
                    int(getattr(p, "peak_price", None) or buy_price),
                    int(p.current_price or buy_price),
                )
                armed, floor = self._resolve_trailing_state(p, buy_price, peak, trail_start)
                if not armed or not floor:
                    return
                await self._persist_trailing_floor(
                    position_id,
                    int(floor),
                    arm=not bool(getattr(p, "trailing_armed", False)),
                )
                return
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 트레일링 바닥 갱신 오류 position_id={position_id}: {e}")

    async def _persist_trailing_armed(self, position_id: int, floor_price: int):
        """트레일링 최초 활성화 (하위 호환)."""
        await self._persist_trailing_floor(position_id, floor_price, arm=True)

    def _build_stop_candidates(
        self,
        settings: AutoTradeSettings,
        buy_price: int,
        peak: int,
        atr: Optional[float],
        *,
        trailing_armed: bool = False,
        trailing_floor_price: Optional[int] = None,
    ) -> List[Tuple[str, float, str]]:
        """손절·트레일·수익잠금 후보. %·ATR을 모두 포함하고 유효선은 최고가(가장 타이트)."""
        candidates: List[Tuple[str, float, str]] = []
        floor = int(trailing_floor_price) if trailing_floor_price else None

        def _apply_trail_floor(raw: float) -> float:
            if floor is not None:
                return max(raw, float(floor))
            return raw

        sl = self._num(settings.stop_loss_rate)
        if sl:
            candidates.append(("STOP_LOSS", buy_price * (1 - abs(sl) / 100.0), "PCT"))

        atr_stop_mult = self._num(settings.atr_mult_stop)
        if atr and atr_stop_mult:
            candidates.append(("STOP_LOSS", buy_price - atr * atr_stop_mult, "ATR"))

        lock_trigger = self._num(settings.profit_lock_trigger)
        if lock_trigger:
            peak_rate = self._peak_rate_pct(buy_price, peak)
            if peak_rate >= lock_trigger:
                lock_floor = self._num(settings.profit_lock_floor)
                lock_floor = 0.0 if lock_floor is None else lock_floor
                candidates.append(("PROFIT_LOCK", buy_price * (1 + lock_floor / 100.0), "PCT"))

        if trailing_armed:
            tr = self._num(settings.trailing_stop_pct)
            if tr:
                raw = peak * (1 - tr / 100.0)
                candidates.append(("TRAILING", _apply_trail_floor(raw), "PCT"))

            atr_trail_mult = self._num(settings.atr_mult_trail)
            if atr and atr_trail_mult:
                raw = peak - atr * atr_trail_mult
                candidates.append(("TRAILING", _apply_trail_floor(raw), "ATR"))

        return candidates

    def _calc_profit(self, position: Position, current_price: int, holding: Optional[dict] = None):
        """키움 API 평가손익(pl_amt) 우선."""
        from utils.eval_pnl import calc_profit_for_position

        return calc_profit_for_position(position, current_price, holding)

    async def _sync_position_from_api(self, position_id: int, holding: dict):
        """키움 잔고 → DB 포지션 (체결 이력 우선, 현재가·손익은 API)."""
        from utils.position_buy_fills import reconcile_position_buy_with_fills

        try:
            for db in get_db():
                session: Session = db
                position = session.query(Position).filter(Position.id == position_id).first()
                if position:
                    reconcile_position_buy_with_fills(session, position, holding)
                    position.last_monitored = utc_now_naive()
                    session.commit()
                break
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] API 포지션 동기화 오류: {e}")

    def _is_in_liquidation_window(self) -> bool:
        """장 마감 전 전량청산 유효 구간 — 평일, liquidate_time(기본 15:10) ~ 15:20."""
        s = self.auto_trade_settings
        kst = as_kst()
        if not is_krx_trading_day(kst):
            return False
        t = getattr(s, "liquidate_time", "") or "15:10" if s else "15:10"
        try:
            lh, lm = map(int, str(t).split(":"))
            start = (lh, lm)
            end = (15, 20)
            now_t = (kst.hour, kst.minute)
            in_time = start <= now_t <= end
        except Exception:
            return False
        if not s or not getattr(s, "liquidate_before_close", False):
            if in_time:
                logger.warning(
                    "🛡️ [STOP_LOSS] 장마감 청산 시각(%s)이나 liquidate_before_close=OFF — 청산 미실행",
                    t,
                )
            return False
        return in_time

    def _is_past_liquidation_time(self) -> bool:
        """청산 트리거 시각 경과 (장마감 윈도우 안에서만 True)."""
        return self._is_in_liquidation_window()

    async def _snapshot_buy_atr(
        self, stock_code: str, settings: Optional[AutoTradeSettings],
    ) -> Tuple[Optional[float], Optional[int]]:
        """매수 시점 ATR — 포지션 생성 시 1회 조회·저장."""
        if not settings:
            return None, None
        if not self._num(settings.atr_mult_stop) and not self._num(settings.atr_mult_trail):
            return None, None
        period = int(self._num(settings.atr_period) or 14)
        code = KiwoomAPI.normalize_stock_code(stock_code)
        atr = await self._get_atr_cached(code, period)
        if atr is None:
            atr = await self._compute_atr(code, period)
        if atr is not None and float(atr) > 0:
            return float(atr), period
        return None, None

    async def _resolve_position_atr(
        self,
        position: Position,
        settings: AutoTradeSettings,
        *,
        allow_api: bool,
    ) -> Tuple[Optional[float], int]:
        """포지션 ATR — 매수 스냅샷 우선, 없으면(구 포지션) API."""
        period = int(self._num(settings.atr_period) or 14)
        stored = getattr(position, "buy_atr", None)
        if stored is not None and float(stored) > 0:
            return float(stored), int(getattr(position, "buy_atr_period", None) or period)
        if not allow_api:
            return None, period
        if not self._num(settings.atr_mult_stop) and not self._num(settings.atr_mult_trail):
            return None, period
        try:
            atr = await asyncio.wait_for(
                self._get_atr_cached(position.stock_code, period), timeout=10.0,
            )
        except asyncio.TimeoutError:
            logger.warning(f"ATR 조회 타임아웃 — {position.stock_name}")
            atr = None
        return (float(atr) if atr and float(atr) > 0 else None), period

    async def _get_atr_cached(self, stock_code: str, period: int = 14) -> Optional[float]:
        """일봉 ATR — 종목·기간당 하루 1회 API 조회, 장외엔 당일 캐시 재사용."""
        code = KiwoomAPI.normalize_stock_code(stock_code)
        key = f"{code}:{period}"
        today = kst_today()
        cached = self._atr_daily_cache.get(key)
        if cached and cached[1] == today:
            return cached[0]
        if not is_krx_session():
            if cached:
                logger.debug(f"🛡️ [STOP_LOSS] 장외 — ATR 캐시 사용 ({code})")
                return cached[0]
            return None
        atr = await self._compute_atr(code, period)
        if atr is not None:
            self._atr_daily_cache[key] = (atr, today)
        return atr

    async def _compute_atr(
        self, stock_code: str, period: int = 14, *, allow_off_hours: bool = False,
    ) -> Optional[float]:
        """일봉 기반 ATR 계산. 실패 시 None(→ 고정% 폴백)."""
        try:
            need = period + 2
            data = await self.kiwoom_api.get_stock_chart_data(
                stock_code, "1D", max_bars=need, allow_off_hours=allow_off_hours,
            )
            if not data or len(data) < period + 1:
                return None
            rows = sorted(data, key=lambda x: str(x.get('timestamp', '')))
            trs, prev_close = [], None
            for r in rows:
                h = abs(float(r.get('high') or 0))
                l = abs(float(r.get('low') or 0))
                c = abs(float(r.get('close') or 0))
                if h <= 0 or l <= 0:
                    if c > 0:
                        prev_close = c
                    continue
                tr = (h - l) if prev_close is None else max(h - l, abs(h - prev_close), abs(l - prev_close))
                trs.append(tr)
                prev_close = c
            if len(trs) < period:
                return None
            atr = sum(trs[-period:]) / period
            return atr if atr > 0 else None
        except Exception as e:
            logger.warning(f"🛡️ [STOP_LOSS] ATR 계산 실패 {stock_code}: {e}")
            return None

    async def _update_position_tracking(self, position_id: int, peak_price: int, stop_line_price: Optional[int]):
        """고점/유효 손절선 저장"""
        try:
            for db in get_db():
                session: Session = db
                p = session.query(Position).filter(Position.id == position_id).first()
                if p:
                    p.peak_price = int(peak_price)
                    if stop_line_price is not None:
                        p.stop_loss_price = int(stop_line_price)
                    session.commit()
                break
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 포지션 추적 업데이트 오류: {e}")

    @debug_tracer.trace_async(component="STOP_LOSS")
    async def _check_position_stop_loss(self, position: Position, holding: Optional[dict] = None):
        """개별 포지션 청산 판단.
        패턴 B: 시작% 도달 → trailing_armed + floor 잠금, 이후 고점 따라 트레일링(바닥 이하로 선 하락 없음).
        """
        try:
            if holding:
                from utils.eval_pnl import apply_holding_to_position
                apply_holding_to_position(position, holding)
                current_price = position.current_price
            else:
                current_price = await self._get_current_price(position.stock_code)
            if not current_price:
                logger.warning(f"🛡️ [STOP_LOSS] 현재가 조회 실패 - {position.stock_name}")
                return

            s = self.auto_trade_settings
            buy_price = position.buy_price or current_price

            # 손익 — 키움 API(lspft_amt) 우선
            profit_loss, profit_loss_rate = self._calc_profit(position, current_price, holding)
            await self._update_position_price(position.id, current_price, profit_loss, profit_loss_rate)
            if holding:
                await self._sync_position_from_api(position.id, holding)

            # 고점 갱신 (매수 시각 이후 고가만)
            peak = await self._resolve_position_peak(position, int(current_price), allow_api=True)

            trail_start = self._num(s.take_profit_rate)
            trail_start_val = trail_start if trail_start and trail_start > 0 else None
            stored_peak = int(getattr(position, "peak_price", None) or buy_price)
            if peak != stored_peak:
                await self._update_position_tracking(position.id, peak, None)
                position.peak_price = peak

            trailing_armed, trailing_floor = await self._guard_trailing_arm_state(
                position, buy_price, peak, trail_start_val,
            )
            if trailing_armed and trailing_floor:
                old_floor = int(getattr(position, "trailing_floor_price", None) or 0)
                need_arm = not getattr(position, "trailing_armed", False)
                need_raise = int(trailing_floor) > old_floor
                if need_arm or need_raise:
                    await self._persist_trailing_floor(
                        position.id, int(trailing_floor), arm=need_arm,
                    )
                    position.trailing_armed = True
                    position.trailing_floor_price = int(trailing_floor)

            sell_reason = None
            detail = ""

            # 0) 장 마감 전 전량청산 (최우선)
            if self._is_past_liquidation_time():
                sell_reason = "MARKET_CLOSE"
                detail = f"장마감 전 전량청산 ({getattr(s, 'liquidate_time', '15:10')}) | 손익 {profit_loss_rate:+.2f}%"
                logger.warning(f"🛡️ [STOP_LOSS] 장마감 청산 - {position.stock_name}: {profit_loss_rate:+.2f}%")

            # 1) 위로만 올라가는 손절선 (손절/수익잠금/트레일링 통합)
            eff_stop = None
            if not sell_reason:
                atr_stop_mult = self._num(s.atr_mult_stop)
                atr_trail_mult = self._num(s.atr_mult_trail)
                atr = None
                if atr_stop_mult or atr_trail_mult:
                    atr, _ = await self._resolve_position_atr(
                        position, s, allow_api=True,
                    )

                candidates = self._build_stop_candidates(
                    s, buy_price, peak, atr,
                    trailing_armed=trailing_armed,
                    trailing_floor_price=trailing_floor,
                )

                if candidates:
                    reason_eff, eff_stop, _ = max(candidates, key=lambda x: x[1])
                    if current_price <= eff_stop:
                        sell_reason = reason_eff
                        detail = (f"{reason_eff} 청산: 현재가 {current_price:,} ≤ 손절선 {eff_stop:,.0f} "
                                  f"(고점 {peak:,}, 손익 {profit_loss_rate:+.2f}%)")
                        logger.warning(f"🛡️ [STOP_LOSS] {reason_eff} - {position.stock_name}: {profit_loss_rate:+.2f}%")

            # 고점/손절선 저장 (매도 안 해도 추적 유지)
            await self._update_position_tracking(position.id, peak, int(eff_stop) if eff_stop else None)

            # 매도 실행
            if sell_reason:
                # PENDING/ORDERED 매도 주문이 이미 있으면,
                # 현재 sell_reason의 우선순위가 더 높더라도 먼저 '하위 사유 주문'을 취소해
                # 중복/불일치 sell_orders가 쌓이지 않게 합니다.
                if await self._has_any_pending_sell_order(position.id):
                    await self._prepare_sell(position.id, sell_reason)

                if await self._has_pending_sell_order(position.id, for_reason=sell_reason):
                    logger.debug(f"🛡️ [STOP_LOSS] 매도 대기 중 — {position.stock_name}, 추가 주문 생략")
                    return
                await self._execute_sell_order(position, current_price, sell_reason, detail)

        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 포지션 확인 오류 - {position.stock_name}: {e}")
    
    async def _get_current_price(self, stock_code: str) -> Optional[int]:
        """현재가 조회"""
        try:
            if not is_krx_session():
                logger.debug(f"🛡️ [STOP_LOSS] 장외 — 현재가 조회 생략: {stock_code}")
                return None
            logger.debug(f"🛡️ [STOP_LOSS] 현재가 조회 시도: {stock_code}")
            current_price = await self.kiwoom_api.get_current_price(stock_code)
            if current_price:
                logger.debug(f"🛡️ [STOP_LOSS] 현재가 조회 성공: {stock_code} = {current_price:,}원")
            else:
                logger.debug(
                    f"🛡️ [STOP_LOSS] 현재가 없음 — {stock_code} (API 제한·장외·토큰)"
                )
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
                    position.last_monitored = utc_now_naive()
                    session.commit()
                    logger.debug(f"🛡️ [STOP_LOSS] 포지션 업데이트 - {position.stock_name}: {profit_loss_rate:.2f}%")
                break
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 포지션 업데이트 오류: {e}")
    
    @staticmethod
    def _holdings_qty_map(balance: Optional[dict]) -> Dict[str, int]:
        """계좌 잔고 → {정규화 종목코드: 수량}."""
        out: Dict[str, int] = {}
        if not balance or balance.get("_error"):
            return out
        for h in balance.get("stk_acnt_evlt_prst") or []:
            code = KiwoomAPI.normalize_stock_code(h.get("stk_cd", ""))
            try:
                qty = int(str(h.get("qty", "0")).replace(",", "") or "0")
            except (TypeError, ValueError):
                qty = 0
            if code and qty > 0:
                out[code] = qty
        return out

    async def _has_pending_sell_order(self, position_id: int, for_reason: Optional[str] = None) -> bool:
        """미체결/체결대기 매도 주문 존재 여부. for_reason보다 높은 우선순위 주문만 차단."""
        block_rank = _sell_reason_rank(for_reason) if for_reason else 9
        for db in get_db():
            session: Session = db
            rows = session.query(SellOrder).filter(
                SellOrder.position_id == position_id,
                SellOrder.status.in_(("PENDING", "ORDERED")),
            ).all()
            for row in rows:
                if _sell_reason_rank(row.sell_reason) <= block_rank:
                    return True
            return False
        return False

    async def _has_any_pending_sell_order(self, position_id: int) -> bool:
        """PENDING/ORDERED 매도 주문이 하나라도 존재하는지(우선순위 무관)."""
        for db in get_db():
            session: Session = db
            row = session.query(SellOrder).filter(
                SellOrder.position_id == position_id,
                SellOrder.status.in_(("PENDING", "ORDERED")),
            ).first()
            return row is not None
        return False

    @staticmethod
    def _sell_order_age_minutes(sell: SellOrder) -> float:
        ref = sell.ordered_at or sell.created_at
        if not ref:
            return 9999.0
        return (utc_now_naive() - ref).total_seconds() / 60.0

    def _reconcile_sell_order_hygiene(self, session: Session, holdings: Dict[str, int]) -> int:
        """중복·만료 매도 주문 정리 → 익절 등 신규 주문 가능하게."""
        changed = 0
        open_sells = session.query(SellOrder).filter(
            SellOrder.status.in_(("PENDING", "ORDERED")),
        ).order_by(SellOrder.created_at.asc()).all()

        by_pos: Dict[int, List[SellOrder]] = {}
        for sell in open_sells:
            by_pos.setdefault(sell.position_id, []).append(sell)
        for sells in by_pos.values():
            if len(sells) <= 1:
                continue
            for sell in sells[:-1]:
                sell.status = "CANCELLED"
                changed += 1
                logger.info(f"🛡️ [RECONCILE] 중복 매도 취소 — {sell.stock_name} #{sell.id}")

        for sell in session.query(SellOrder).filter(
            SellOrder.status.in_(("PENDING", "ORDERED")),
        ).all():
            code = KiwoomAPI.normalize_stock_code(sell.stock_code)
            acct_qty = holdings.get(code, 0)
            age = self._sell_order_age_minutes(sell)

            # 오래된 PENDING (미접수)
            if sell.status == "PENDING" and age >= STALE_SELL_ORDER_MINUTES:
                sell.status = "CANCELLED"
                changed += 1
                logger.warning(f"🛡️ [RECONCILE] 만료 PENDING 취소 — {sell.stock_name} #{sell.id}")
                continue

            # ORDERED인데 계좌에 전량 잔존 + 오래됨 → 미체결로 간주하고 취소
            if (
                sell.status == "ORDERED"
                and acct_qty >= sell.sell_quantity
                and age >= STALE_SELL_ORDER_MINUTES
            ):
                sell.status = "CANCELLED"
                changed += 1
                log_activity(
                    "SELL",
                    f"만료 매도 주문 취소 — {sell.stock_name} ({sell.sell_reason}, {age:.0f}분 경과)",
                    "warn",
                    stock_code=sell.stock_code,
                    reason=sell.sell_reason,
                )
                logger.warning(
                    f"🛡️ [RECONCILE] stale ORDERED 취소 — {sell.stock_name} "
                    f"#{sell.id} ({sell.sell_reason}, {age:.0f}분)"
                )
        return changed

    def _cancel_inferior_sell_orders(self, session: Session, position_id: int, new_reason: str) -> int:
        """새 청산 사유가 더 긴급하면 기존 하위 PENDING/ORDERED 취소."""
        new_rank = _sell_reason_rank(new_reason)
        n = 0
        for sell in session.query(SellOrder).filter(
            SellOrder.position_id == position_id,
            SellOrder.status.in_(("PENDING", "ORDERED")),
        ).all():
            if _sell_reason_rank(sell.sell_reason) > new_rank:
                sell.status = "CANCELLED"
                n += 1
                logger.info(
                    f"🛡️ [STOP_LOSS] 하위 매도 취소 — {sell.stock_name} "
                    f"{sell.sell_reason} → {new_reason}"
                )
        return n

    def _cancel_all_open_sell_orders(self, session: Session, position_id: int) -> int:
        """포지션의 모든 미완료 매도 주문 DB 취소 (수동 강제청산용)."""
        n = 0
        for sell in session.query(SellOrder).filter(
            SellOrder.position_id == position_id,
            SellOrder.status.in_(("PENDING", "ORDERED")),
        ).all():
            sell.status = "CANCELLED"
            n += 1
            logger.info(
                f"🛡️ [STOP_LOSS] 매도 취소(수동청산) — {sell.stock_name} "
                f"#{sell.id} ({sell.sell_reason})"
            )
        return n

    async def execute_manual_liquidation(self, position_id: int) -> Dict:
        """대시보드 수동 청산 — 기존 매도 주문 정리 후 전량 시장가 매도."""
        position = None
        cancelled = 0
        for db in get_db():
            session: Session = db
            position = session.query(Position).filter(Position.id == position_id).first()
            if not position:
                return {"success": False, "error": "포지션을 찾을 수 없습니다."}
            if position.status != "HOLDING":
                return {"success": False, "error": "매도 가능한 포지션이 아닙니다."}
            cancelled = self._cancel_all_open_sell_orders(session, position_id)
            session.commit()
            break

        sell_price = await self._get_current_price(position.stock_code) or position.current_price
        if not sell_price:
            return {"success": False, "error": "현재가 조회 실패"}

        await self._execute_sell_order(
            position,
            int(sell_price),
            "MANUAL",
            "대시보드 수동 청산",
        )

        latest = None
        for db in get_db():
            session: Session = db
            latest = (
                session.query(SellOrder)
                .filter(SellOrder.position_id == position_id)
                .order_by(SellOrder.created_at.desc())
                .first()
            )
            break

        if latest and latest.sell_reason == "MANUAL":
            if latest.status in ("PENDING", "ORDERED"):
                log_activity(
                    "SELL",
                    f"수동 청산 주문 — {position.stock_name} {position.buy_quantity}주 @ 시장가",
                    "warn",
                    stock_code=position.stock_code,
                    reason="MANUAL",
                )
                await self.reconcile_after_manual_sell()
                return {
                    "success": True,
                    "message": f"{position.stock_name} 시장가 청산 주문을 접수했습니다.",
                    "position_id": position_id,
                    "sell_price": int(sell_price),
                    "cancelled_orders": cancelled,
                }
            if latest.status == "FAILED":
                return {"success": False, "error": "매도 주문이 거부되었습니다. 키움 계좌·장 시간을 확인하세요."}

        if await self._has_pending_sell_order(position_id, for_reason="MANUAL"):
            return {"success": False, "error": "매도 주문이 이미 진행 중입니다."}

        return {"success": False, "error": "매도 주문을 접수하지 못했습니다."}

    async def reconcile_after_manual_sell(self) -> None:
        """수동 청산 직후 계좌·포지션·매수슬롯 동기화."""
        try:
            await self._reconcile_sell_orders_and_holdings()
            from utils.auto_trade_engine import prune_stale_buy_slot_reservations
            for db in get_db():
                n = prune_stale_buy_slot_reservations(db)
                if n:
                    db.commit()
                    logger.info(f"🛡️ [STOP_LOSS] 수동청산 후 만료 매수 신호 {n}건 정리")
                break
            await self.sync_holdings_from_api(force=True)
        except Exception as e:
            logger.warning(f"🛡️ [STOP_LOSS] 수동청산 후 동기화 실패: {e}")

    async def _prepare_sell(self, position_id: int, sell_reason: str) -> None:
        """매도 전 hygiene + 하위 주문 취소."""
        for db in get_db():
            session: Session = db
            account_number = (
                Config.KIWOOM_MOCK_ACCOUNT_NUMBER
                if Config.KIWOOM_USE_MOCK_ACCOUNT
                else Config.KIWOOM_ACCOUNT_NUMBER
            )
            balance = await self.kiwoom_api.get_account_balance(account_number)
            holdings = self._holdings_qty_map(balance)
            self._reconcile_sell_order_hygiene(session, holdings)
            self._cancel_inferior_sell_orders(session, position_id, sell_reason)
            session.commit()
            break

    async def _reconcile_sell_orders_and_holdings(self):
        """키움 계좌 잔고 기준으로 매도 체결 확정 및 포지션 DB 동기화."""
        try:
            account_number = (
                Config.KIWOOM_MOCK_ACCOUNT_NUMBER
                if Config.KIWOOM_USE_MOCK_ACCOUNT
                else Config.KIWOOM_ACCOUNT_NUMBER
            )
            balance = await self.kiwoom_api.get_account_balance(account_number)
            from utils.eval_pnl import apply_holding_to_position, holdings_by_code

            holdings_map = holdings_by_code(balance)
            holdings = {
                code: _parse_kiwoom_int(h.get("qty"))
                for code, h in holdings_map.items()
            }
            if balance.get("_error"):
                logger.debug(f"🛡️ [RECONCILE] 계좌 조회 실패 — 동기화 생략 ({balance.get('_error_msg', '')})")
                return

            for db in get_db():
                session: Session = db
                hygiene = self._reconcile_sell_order_hygiene(session, holdings)
                if hygiene:
                    logger.info(f"🛡️ [RECONCILE] 매도 주문 정리 {hygiene}건")

                dup_cleared = _collapse_duplicate_holdings(session)
                if dup_cleared:
                    logger.info(f"🛡️ [RECONCILE] 중복 HOLDING 정리 {dup_cleared}건")

                ordered_sells = session.query(SellOrder).filter(
                    SellOrder.status == "ORDERED",
                ).order_by(SellOrder.ordered_at.asc()).all()

                for sell in ordered_sells:
                    pos = session.query(Position).filter(Position.id == sell.position_id).first()
                    if not pos:
                        continue
                    code = KiwoomAPI.normalize_stock_code(sell.stock_code)
                    acct_qty = holdings.get(code, 0)

                    if acct_qty <= 0:
                        self._finalize_sell_in_session(session, sell, pos)
                        log_activity(
                            "SELL",
                            f"매도 체결 확정 — {pos.stock_name} {sell.sell_quantity}주 ({sell.sell_reason})",
                            "info",
                            stock_code=pos.stock_code,
                            reason=sell.sell_reason,
                        )
                        logger.info(f"🛡️ [RECONCILE] 매도 확정 — {pos.stock_name} ({code})")
                        snap = sell_fill_snapshot(sell, pos)
                        asyncio.create_task(notify_sell_filled_async(snap, remaining_qty=None))
                    elif acct_qty < pos.buy_quantity:
                        sold_qty = pos.buy_quantity - acct_qty
                        sell.sell_quantity = sold_qty
                        sell.sell_amount = int((sell.sell_price or pos.current_price or pos.buy_price) * sold_qty)
                        self._finalize_sell_in_session(session, sell, pos)
                        pos.status = "HOLDING"
                        pos.sell_time = None
                        h = holdings_map.get(code)
                        if h:
                            apply_holding_to_position(pos, h)
                        else:
                            pos.buy_quantity = acct_qty
                        log_activity(
                            "SELL",
                            f"부분 매도 확정 — {pos.stock_name} {sold_qty}주 체결, 잔량 {acct_qty}주",
                            "info",
                            stock_code=pos.stock_code,
                        )
                        logger.info(f"🛡️ [RECONCILE] 부분 매도 — {pos.stock_name} 잔량 {acct_qty}주")
                        snap = sell_fill_snapshot(sell, pos)
                        asyncio.create_task(notify_sell_filled_async(snap, remaining_qty=acct_qty))
                    else:
                        # 계좌에 아직 전량 보유 → 체결 대기, 포지션 HOLDING 유지
                        if pos.status != "HOLDING":
                            pos.status = "HOLDING"
                            pos.sell_time = None
                            logger.info(f"🛡️ [RECONCILE] 매도 대기 — {pos.stock_name} HOLDING 복구 ({acct_qty}주)")

                # 계좌 보유 ↔ DB HOLDING 동기화 (청산된 옛 포지션은 복구하지 않음)
                for code, acct_qty in holdings.items():
                    holding_rows = _holding_rows_for_code(session, code)
                    if holding_rows:
                        h = holdings_map.get(code)
                        for pos in holding_rows:
                            if h:
                                apply_holding_to_position(pos, h)
                            elif pos.buy_quantity != acct_qty:
                                pos.buy_quantity = acct_qty
                        continue

                    pending_for_code = [
                        s for s in session.query(SellOrder).filter(
                            SellOrder.status == "ORDERED",
                        ).all()
                        if KiwoomAPI.normalize_stock_code(s.stock_code) == code
                    ]
                    if not pending_for_code:
                        continue

                    target = session.query(Position).filter(
                        Position.id == pending_for_code[-1].position_id,
                    ).first()
                    if not target or target.status == "HOLDING":
                        continue

                    target.status = "HOLDING"
                    target.sell_time = None
                    h = holdings_map.get(code)
                    if h:
                        apply_holding_to_position(target, h)
                    else:
                        target.buy_quantity = acct_qty
                    log_activity(
                        "SELL",
                        f"매도 대기 포지션 복구 — {target.stock_name} {acct_qty}주 (ORDERED 매도 미체결)",
                        "warn",
                        stock_code=target.stock_code,
                    )
                    logger.warning(
                        f"🛡️ [RECONCILE] 매도 대기 포지션 복구 — {target.stock_name} ({code}) {acct_qty}주"
                    )

                cleared = self._reconcile_account_cleared_holdings(session, holdings)
                if cleared:
                    logger.info(f"🛡️ [RECONCILE] 계좌 미보유 포지션 정리 {cleared}건")

                session.commit()
                break
        except Exception as e:
            logger.error(f"🛡️ [RECONCILE] 매도/잔고 동기화 오류: {e}")

    def _reconcile_account_cleared_holdings(
        self, session: Session, holdings: Dict[str, int],
    ) -> int:
        """DB HOLDING인데 키움 계좌에 없음 → 청산 상태로 정리."""
        closed = 0
        for pos in session.query(Position).filter(Position.status == "HOLDING").all():
            code = KiwoomAPI.normalize_stock_code(pos.stock_code)
            if holdings.get(code, 0) > 0:
                continue

            open_sells = session.query(SellOrder).filter(
                SellOrder.position_id == pos.id,
                SellOrder.status.in_(("PENDING", "ORDERED")),
            ).order_by(SellOrder.created_at.asc()).all()

            # 매수 직후 잔고 API 미반영 → 앱 매도 없이 MANUAL_SELL 오판 방지.
            # ORDERED 매도가 있으면 실제 청산 확정이므로 유예하지 않음.
            has_ordered_sell = any(s.status == "ORDERED" for s in open_sells)
            if not has_ordered_sell and _within_buy_settle_grace(pos):
                age = _buy_age_seconds(pos)
                grace = int(getattr(Config, "BUY_SETTLE_GRACE_SECONDS", 90) or 90)
                logger.info(
                    f"🛡️ [RECONCILE] 매수 직후 유예 — {pos.stock_name} "
                    f"({age:.0f}s < {grace}s, 잔고 미반영 가능)"
                )
                continue

            finalized = False
            for sell in open_sells:
                if sell.status == "ORDERED":
                    self._finalize_sell_in_session(session, sell, pos)
                    log_activity(
                        "SELL",
                        f"매도 체결 확정 — {pos.stock_name} {sell.sell_quantity}주 ({sell.sell_reason})",
                        "info",
                        stock_code=pos.stock_code,
                        reason=sell.sell_reason,
                    )
                    logger.info(f"🛡️ [RECONCILE] 계좌 청산 확정 — {pos.stock_name} ({code})")
                    snap = sell_fill_snapshot(sell, pos)
                    asyncio.create_task(notify_sell_filled_async(snap))
                    closed += 1
                    finalized = True
                    break
                sell.status = "CANCELLED"
                logger.info(f"🛡️ [RECONCILE] 미체결 PENDING 취소 — {pos.stock_name} #{sell.id}")

            if finalized:
                continue

            last_done = session.query(SellOrder).filter(
                SellOrder.position_id == pos.id,
                SellOrder.status == "COMPLETED",
            ).order_by(SellOrder.completed_at.desc()).first()

            if last_done:
                pos.status = last_done.sell_reason or "MANUAL_SELL"
                pos.sell_time = last_done.completed_at or utc_now_naive()
                detail = f"계좌 미보유 — DB 정리 ({pos.stock_name} → {pos.status})"
            else:
                pos.status = "MANUAL_SELL"
                pos.sell_time = utc_now_naive()
                detail = f"계좌 청산 확인 — {pos.stock_name} (앱 매도 기록 없음)"
                from utils.position_sell_backfill import ensure_completed_sell_order
                ensure_completed_sell_order(
                    session,
                    pos,
                    sell_reason="MANUAL",
                    sell_reason_detail="계좌 미보유 동기화 — 키움 잔고 기준 청산",
                    completed_at=pos.sell_time,
                )

            log_activity("SELL", detail, "info", stock_code=pos.stock_code)
            logger.info(f"🛡️ [RECONCILE] {detail}")
            closed += 1

        return closed

    @staticmethod
    def _finalize_sell_in_session(session: Session, sell: SellOrder, pos: Position):
        """매도 체결 확정 — SellOrder COMPLETED, Position 청산 상태."""
        if sell.status != "COMPLETED":
            sell.status = "COMPLETED"
            sell.completed_at = utc_now_naive()
        pos.status = sell.sell_reason or "MANUAL_SELL"
        pos.sell_time = sell.completed_at or utc_now_naive()
        if sell.profit_loss is None and pos.buy_price and sell.sell_price:
            sell.profit_loss = (sell.sell_price - pos.buy_price) * sell.sell_quantity
        session.flush()

    async def _execute_sell_order(self, position: Position, sell_price: int, sell_reason: str, sell_reason_detail: str):
        """매도 주문 실행 (체결 확정은 _reconcile_sell_orders_and_holdings에서 처리)."""
        try:
            if await self._has_pending_sell_order(position.id, for_reason=sell_reason):
                logger.info(f"🛡️ [STOP_LOSS] 매도 주문 생략 — {position.stock_name}: 동일/상위 우선순위 대기 중")
                log_activity(
                    "SELL",
                    f"매도 생략 — {position.stock_name} ({sell_reason}): 동일/상위 주문 대기 중",
                    "info",
                    stock_code=position.stock_code,
                    reason=sell_reason,
                )
                return

            logger.info(f"🛡️ [STOP_LOSS] 매도 주문 실행 - {position.stock_name}: {sell_reason}")
            
            # 매도 주문 생성
            sell_order_id = await self._create_sell_order(position, sell_price, sell_reason, sell_reason_detail)
            if not sell_order_id:
                return
            
            # 키움 API로 매도 주문
            result = await self.kiwoom_api.place_sell_order(
                stock_code=position.stock_code,
                quantity=position.buy_quantity,
                price=0,  # 시장가
                order_type="3"  # 시장가
            )
            
            if result.get("success"):
                msg = f"매도 주문 {sell_reason} — {position.stock_name} {position.buy_quantity}주 @ {sell_price:,}원"
                logger.info(f"🛡️ [STOP_LOSS] 매도 주문 성공 - {position.stock_name}: {position.buy_quantity}주")
                log_activity("SELL", msg, "info", stock_code=position.stock_code, reason=sell_reason)
                
                # 매도 주문 접수 — 포지션 청산은 계좌 체결 확인 후 reconcile에서 처리
                await self._update_sell_order_status(sell_order_id, "ORDERED", result.get("order_id", ""))
                
            else:
                error_msg = result.get("error", "알 수 없는 오류")
                logger.error(f"🛡️ [STOP_LOSS] 매도 주문 실패 - {position.stock_name}: {error_msg}")
                log_activity("SELL", f"매도 실패 {position.stock_name}: {error_msg}", "warn", stock_code=position.stock_code, reason=sell_reason)
                await self._update_sell_order_status(sell_order_id, "FAILED", error_msg)
                
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 매도 주문 실행 오류 - {position.stock_name}: {e}")
    
    async def _create_sell_order(self, position: Position, sell_price: int, sell_reason: str, sell_reason_detail: str) -> Optional[int]:
        """매도 주문 생성 — DB id 반환 (세션 분리 안전)."""
        try:
            sell_order_id = None
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
                    profit_loss_rate=(sell_price - position.buy_price) / position.buy_price * 100 if position.buy_price else 0,
                    status="PENDING"
                )
                session.add(sell_order)
                session.commit()
                sell_order_id = sell_order.id
                break
            
            return sell_order_id
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 매도 주문 생성 오류: {e}")
            raise
    
    async def _update_sell_order_status(self, sell_order_id: int, status: str, order_id_or_error: str = ""):
        """매도 주문 상태 업데이트. FAILED 시 order_id_or_error → sell_reason_detail 저장."""
        try:
            for db in get_db():
                session: Session = db
                sell_order = session.query(SellOrder).filter(SellOrder.id == sell_order_id).first()
                if sell_order:
                    sell_order.status = status
                    if status == "FAILED":
                        if order_id_or_error:
                            sell_order.sell_reason_detail = str(order_id_or_error)[:200]
                    elif status == "ORDERED" and order_id_or_error:
                        sell_order.sell_order_id = order_id_or_error
                        sell_order.ordered_at = utc_now_naive()
                    elif status == "COMPLETED":
                        sell_order.completed_at = utc_now_naive()
                    session.commit()
                break
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 매도 주문 상태 업데이트 오류: {e}")
    
    async def create_position_from_buy_signal(self, signal_id: int, buy_price: int, buy_quantity: int, buy_order_id: str = ""):
        """매수 신호로부터 포지션 생성"""
        from core.models import PendingBuySignal
        from api.kiwoom_api import KiwoomAPI

        position = None
        try:
            await self._load_auto_trade_settings()
            s = self.auto_trade_settings
            signal = None
            for db in get_db():
                session: Session = db
                signal = session.query(PendingBuySignal).filter(PendingBuySignal.id == signal_id).first()
                break
            if not signal:
                logger.error(f"🛡️ [STOP_LOSS] 포지션 생성 — 신호 없음 id={signal_id}")
                return None

            code = KiwoomAPI.normalize_stock_code(signal.stock_code)
            buy_atr, buy_atr_period = await self._snapshot_buy_atr(code, s)

            for db in get_db():
                session: Session = db
                existing = session.query(Position).filter(
                    Position.stock_code == code,
                    Position.status == "HOLDING",
                ).first()
                if existing:
                    old_qty = existing.buy_quantity or 0
                    old_amt = existing.buy_amount or (existing.buy_price * old_qty)
                    new_qty = old_qty + buy_quantity
                    new_amt = old_amt + buy_price * buy_quantity
                    existing.buy_quantity = new_qty
                    existing.buy_price = new_amt // new_qty if new_qty else existing.buy_price
                    existing.buy_amount = new_amt
                    if buy_order_id:
                        existing.buy_order_id = buy_order_id
                    if signal.id and not existing.signal_id:
                        existing.signal_id = signal.id
                    session.commit()
                    session.refresh(existing)
                    logger.info(
                        f"🛡️ [STOP_LOSS] 기존 HOLDING에 매수 반영 — {signal.stock_name}: "
                        f"+{buy_quantity}주 @ {buy_price:,}원 (포지션 #{existing.id})"
                    )
                    return existing

                position = Position(
                    stock_code=code,
                    stock_name=signal.stock_name,
                    buy_price=buy_price,
                    buy_quantity=buy_quantity,
                    order_quantity=buy_quantity,
                    buy_amount=buy_price * buy_quantity,
                    buy_order_id=buy_order_id,
                    stop_loss_rate=s.stop_loss_rate if s else 5.0,
                    take_profit_rate=s.take_profit_rate if s else 10.0,
                    condition_id=signal.condition_id,
                    signal_id=signal.id,
                    status="HOLDING",
                    peak_price=buy_price,
                    buy_atr=buy_atr,
                    buy_atr_period=buy_atr_period,
                )
                session.add(position)
                session.commit()
                session.refresh(position)
                if buy_atr:
                    logger.info(
                        f"🛡️ [STOP_LOSS] 포지션 생성 - {signal.stock_name}: "
                        f"{buy_quantity}주 @ {buy_price:,}원 · ATR {buy_atr:,.0f}({buy_atr_period}일)"
                    )
                else:
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
            
            settings = self._settings_for_session()
            session_window = (
                linked_trading_session_window_str(settings) if settings else None
            )
            status = {
                "is_running": self.is_running,
                "monitoring_active": self.is_monitoring_active(),
                "monitoring_loop_alive": self.monitoring_task_running(),
                "monitoring_interval": self.monitoring_interval,
                "linked_session_window": session_window,
                "session_window": session_window,
                "trade_start_time": settings.trade_start_time if settings else None,
                "trade_end_time": settings.trade_end_time if settings else None,
                "in_linked_session": in_linked_trading_session(settings),
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


stop_loss_manager = StopLossManager()