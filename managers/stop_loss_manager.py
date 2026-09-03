import logging
import asyncio
import time
from datetime import datetime, timedelta, date, timezone
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session

from api.kiwoom_api import KiwoomAPI, _parse_kiwoom_int
from core.models import Position, PositionBuyFill, PendingBuySignal, SellOrder, AutoTradeSettings, get_db
from core.config import Config
from utils.debug_tracer import debug_tracer
from utils.auto_trade_activity_log import log_activity
from utils.market_hours import (
    in_linked_trading_session,
    is_krx_session,
    is_krx_trading_day,
    is_stop_loss_monitoring_session,
    linked_trading_session_window_str,
    seconds_until_stop_loss_monitoring,
    stop_loss_monitoring_window_str,
)
from utils.auto_trade_engine import get_auto_trade_settings_sync
from utils.datetime_kst import as_kst, kst_today, now_kst, utc_now_naive, KST
from utils.position_peak_since_buy import (
    buy_time_utc_naive_to_kst,
    max_high_full_holding_days,
    max_high_since_buy_from_intraday_bars,
    resolve_position_peak_price,
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


def is_sell_qty_shortage_error(msg: Optional[str]) -> bool:
    """키움 모의/실전: 매도가능수량 부족 (이미 체결·중복 주문 포함)."""
    text = str(msg or "")
    return "800033" in text or "매도가능수량이 부족" in text


def effective_sellable_qty(
    acct_qty: int,
    sellable_field: Optional[int] = None,
    locked_qty: int = 0,
) -> int:
    """실제 매도 가능 수량. 필드가 있으면 min(보유, 필드), 없으면 보유-미체결잠금."""
    acct = max(0, int(acct_qty or 0))
    locked = max(0, int(locked_qty or 0))
    if sellable_field is not None:
        return max(0, min(acct, int(sellable_field)))
    return max(0, acct - locked)


def is_unfilled_sell_side(item: Optional[dict]) -> bool:
    """ka10075 한 건이 매도 미체결인지."""
    if not item:
        return False
    io = str(item.get("io_tp_nm") or "")
    trde = str(item.get("trde_tp") or "")
    if "매수" in io or trde in ("2", "매수"):
        return False
    if "매도" in io or trde in ("1", "매도"):
        return True
    return True


def classify_breakout_structure(
    current_price: int,
    level_price: int,
    soft_pct: float,
    hard_pct: float,
) -> str:
    """돌파 레벨 이탈 강도: HARD | SOFT | NONE."""
    if current_price <= 0 or level_price <= 0:
        return "NONE"
    hard_line = level_price * (1 - abs(float(hard_pct)) / 100.0)
    soft_line = level_price * (1 - abs(float(soft_pct)) / 100.0)
    if current_price <= hard_line:
        return "HARD"
    if current_price <= soft_line:
        return "SOFT"
    return "NONE"


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
        getattr(Config, "BUY_SETTLE_GRACE_SECONDS", 300) or 300
    )
    if grace <= 0:
        return False
    age = _buy_age_seconds(pos, now=now)
    if age is None:
        return False
    return age < grace


def _required_missing_confirms() -> int:
    return max(1, int(getattr(Config, "BUY_SETTLE_MISSING_CONFIRMS", 3) or 3))


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
        # code -> (peak, buy_iso, monotonic_ts) — TTL 없으면 장중 고점 갱신이 멈춤
        self._since_buy_peak_cache: Dict[str, Tuple[int, str, float]] = {}
        self._since_buy_peak_cache_ttl_sec = 20.0
        self._last_cycle_at: Optional[datetime] = None
        self._last_heartbeat_msg: Optional[str] = None
        self._loop_active = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._off_hours_logged = False
        # 상따(전략별) 소프트 카운터 — 메모리 캐시 (프로세스 재시작 시 초기화됨)
        self._sangtta_soft_counters: Dict[int, int] = {}
        self._breakout_soft_counters: Dict[int, int] = {}
        self._legacy_ema_state: Dict[int, Dict[str, Any]] = {}
        # 앱 매도 없는 '계좌 미보유' 청산 — 연속 미확인 횟수 (position_id → count)
        self._account_missing_strikes: Dict[int, int] = {}
        # 800033 직후에는 키움 미체결 잔량이 해제될 시간을 확보한다.
        self._sell_qty_shortage_cooldown: Dict[int, float] = {}
        self._sell_qty_shortage_cooldown_sec = 90.0
        # 대시보드·모니터 동시 동기화로 잔고 API가 중첩되지 않게
        self._holdings_sync_lock = asyncio.Lock()

    def _settings_for_session(self) -> Optional[AutoTradeSettings]:
        return get_auto_trade_settings_sync()

    def invalidate_settings_cache(self) -> None:
        """설정 저장 후 인메모리 캐시 제거."""
        self.auto_trade_settings = None

    def is_monitoring_active(self) -> bool:
        """손절 모니터 세션 여부 — 거래일 08:00~19:30 (매수 창과 무관)."""
        return is_stop_loss_monitoring_session(self._settings_for_session())

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
                        window = stop_loss_monitoring_window_str()
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

                # 매도 체결 확인 → 잔고·현재가 동기화 (대시보드 live와 동일 락)
                await self.sync_holdings_from_api(force=True)

                # 장마감 전량청산 — 자동매매 ON/OFF 무관 (설정만 켜져 있으면 실행)
                if self._is_in_liquidation_window():
                    await self._run_market_close_liquidation()
                    await self._log_cycle_heartbeat(mode="장마감청산")
                    await asyncio.sleep(30)
                    continue
                elif self.auto_trade_settings:
                    # 보유 청산 판단: 매수 OFF·전략 매수창과 무관, 08:00~19:30(NXT 포함)
                    await self._monitor_positions()
                    await self._log_cycle_heartbeat(mode="손절점검")
                else:
                    logger.debug("🛡️ [STOP_LOSS] 설정 없음 — 손절·익절 판단 건너뜀")
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
    def strategy_stop_loss_rate(
        settings: Optional["AutoTradeSettings"],
        strategy_key: Optional[str],
    ) -> Optional[float]:
        """전략에 실제 적용되는 고정 손절률을 반환한다."""
        if not settings:
            return None
        sk = (strategy_key or "").strip().lower()
        field = {
            "breakout": "breakout_stop_loss_pct",
            "ymgp": "ymgp_stop_loss_pct",
            "jongga": "jongga_stop_loss_pct",
            "ma1592": "ma1592_stop_pct",
        }.get(sk, "stop_loss_rate")
        rate = StopLossManager._num(getattr(settings, field, None))
        if rate is None and field != "stop_loss_rate":
            rate = StopLossManager._num(getattr(settings, "stop_loss_rate", None))
        return rate

    @staticmethod
    def overlay_global_exit_settings(
        ex: Optional[dict],
        buy_price: int,
        settings: Optional["AutoTradeSettings"],
        strategy_key: Optional[str] = None,
    ) -> dict:
        """exit_levels에 전략별 설정 손절율·%손절가를 명시한다."""
        out = dict(ex or {})
        if not settings or not buy_price:
            return out
        sl = StopLossManager.strategy_stop_loss_rate(settings, strategy_key)
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
            if str(getattr(pos, "strategy_key", None) or "").strip().lower() == "fractal":
                continue
            sl = StopLossManager.strategy_stop_loss_rate(settings, pos.strategy_key)
            if sl is not None:
                pos.stop_loss_rate = sl
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
            session_txt = (
                "손절세션" if is_stop_loss_monitoring_session() else "모니터외"
            )
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
                
                if idx < len(positions):
                    from api.api_rate_limiter import api_rate_limiter
                    gap = float(getattr(api_rate_limiter, "min_call_interval", 0.25) or 0.25)
                    await asyncio.sleep(max(0.2, min(gap, 1.0)))
                
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 포지션 모니터링 중 오류: {e}")

    async def _run_market_close_liquidation(self):
        """장마감 슬롯 정리 — 당일 종가배팅은 유지, 익일 플러스·이틀 초과 종가배팅은 청산."""
        s = self.auto_trade_settings
        liq_time = getattr(s, "liquidate_time", "15:10") if s else "15:10"
        try:
            holdings_map, _ = await self._fetch_balance_holdings()
            if not holdings_map:
                logger.debug("🛡️ [STOP_LOSS] 장마감 청산 — 보유 종목 없음")
                return

            from utils.overnight_keep import (
                OvernightCandidate,
                is_today_jongga,
                jongga_close_force_reason,
                jongga_force_liquidate_at_close,
                select_overnight_keep,
            )

            today = kst_today()
            keep_slots = 3
            per_strat = 1
            try:
                keep_slots = int(getattr(s, "overnight_keep_slots", None) or 3)
            except (TypeError, ValueError):
                keep_slots = 3
            try:
                per_strat = int(getattr(s, "overnight_max_per_strategy", None) or 1)
            except (TypeError, ValueError):
                per_strat = 1
            fractal_force = bool(getattr(s, "fractal_liquidate_before_close", True)) if s else True

            targets: List[Tuple[int, dict]] = []
            candidates: List[OvernightCandidate] = []
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
                    pid = int(pos.id)
                    sk = getattr(pos, "strategy_key", None)
                    buy_t = getattr(pos, "buy_time", None)
                    rate = float(getattr(pos, "current_profit_loss_rate", None) or 0)
                    sk_norm = (sk or "").strip().lower()
                    frac_force = fractal_force and sk_norm == "fractal"
                    jg_force = jongga_force_liquidate_at_close(sk, buy_t, today, rate)
                    force_reason = ""
                    if jg_force:
                        force_reason = jongga_close_force_reason(buy_t, today, rate)
                    elif frac_force:
                        force_reason = "프랙탈 당일청산"
                    candidates.append(
                        OvernightCandidate(
                            position_id=pid,
                            strategy_key=sk or "legacy",
                            pnl_rate=rate,
                            is_today_jongga=is_today_jongga(sk, buy_t, today),
                            force_liquidate=frac_force or jg_force,
                            force_reason=force_reason,
                            stock_code=str(pos.stock_code or code),
                            stock_name=str(pos.stock_name or holding.get("stk_nm") or code),
                        )
                    )
                    targets.append((pid, holding))
                session.commit()
                break

            if not targets:
                return

            keep_ids, kept_rows, liq_rows = select_overnight_keep(
                candidates, keep_slots=keep_slots, max_per_strategy=per_strat,
            )
            liquidate_ids = {r.position_id for r in liq_rows}
            force_reason_by_id = {
                r.position_id: (r.force_reason or "")
                for r in liq_rows
                if r.force_reason
            }

            logger.warning(
                f"🛡️ [STOP_LOSS] 장마감 청산 시작 ({liq_time}) — "
                f"보유 {len(targets)}종목 · 오버나잇 유지 {len(keep_ids)} "
                f"(당일종가배팅 제외 슬롯 {keep_slots}) · 정리 {len(liquidate_ids)}"
            )
            for row in kept_rows:
                tag = "당일종가배팅" if row.is_today_jongga else row.strategy_key
                log_activity(
                    "SELL",
                    f"장마감 오버나잇 유지 — {row.stock_name} ({tag} {row.pnl_rate:+.2f}%)",
                    "info",
                    stock_code=row.stock_code,
                    reason="MARKET_CLOSE",
                )
            log_activity(
                "SELL",
                f"장마감 슬롯 정리 시작 — {len(liquidate_ids)}종목 ({liq_time})",
                "warn",
            )

            sell_targets = [(pid, h) for pid, h in targets if pid in liquidate_ids]
            for idx, (position_id, holding) in enumerate(sell_targets, 1):
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

                    # 이미 접수된 매도가 있으면 키움 미체결이 수량을 잠근다.
                    # DB만 취소하고 재주문하면 800033이 난다.
                    if await self._has_any_pending_sell_order(position.id):
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
                    close_tag = force_reason_by_id.get(position_id) or f"장마감 슬롯 정리 ({liq_time})"
                    detail = (
                        f"{close_tag} | "
                        f"{position.buy_quantity}주 | 손익 {rate:+.2f}%"
                    )
                    logger.warning(
                        f"🛡️ [STOP_LOSS] 장마감 청산 — {position.stock_name}: {rate:+.2f}%"
                    )
                    await self._execute_sell_order(position, int(cur), "MARKET_CLOSE", detail)
                    if idx < len(sell_targets):
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

        force는 하위 호환용 — 잔고(kt00004) 동기화는 장외에도 수행.
        NXT 연장(손절 모니터 세션·KRX 외)에는 종목별 통합/NXT 시세로 현재가를 보정.
        동시 호출은 락으로 직렬화(대시보드 live 갱신 + 모니터 중첩 방지).
        """
        if self._holdings_sync_lock.locked() and not force:
            logger.debug("🛡️ [STOP_LOSS] 포지션 동기화 진행 중 — 스킵")
            return
        async with self._holdings_sync_lock:
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
                from api.api_rate_limiter import api_rate_limiter

                for idx, position in enumerate(positions, 1):
                    try:
                        code = KiwoomAPI.normalize_stock_code(position.stock_code)
                        holding = holdings_map.get(code)
                        did_quote_api = False

                        if holding:
                            apply_holding_to_position(position, holding)
                            position.last_monitored = utc_now_naive()
                            # KRX 마감 후(NXT 연장): 잔고 cur_pr가 정규장 종가에 남을 수 있어
                            # 통합/NXT 시세로 현재가·손익을 한 번 더 맞춤
                            if (
                                is_stop_loss_monitoring_session()
                                and not is_krx_session()
                            ):
                                live_px = await self._get_current_price(position.stock_code)
                                did_quote_api = True
                                if live_px and live_px > 0:
                                    pl, rate = calc_profit_for_position(position, live_px)
                                    position.current_price = live_px
                                    position.current_profit_loss = pl
                                    position.current_profit_loss_rate = rate
                            logger.debug(
                                f"🛡️ [STOP_LOSS] API 동기화 — {position.stock_name}: "
                                f"{position.current_profit_loss:+,}원 ({position.current_profit_loss_rate:+.2f}%)"
                            )
                        elif code not in account_codes:
                            logger.debug(
                                f"🛡️ [STOP_LOSS] 계좌 미보유 — DB 값 유지 ({position.stock_name}, {code})"
                            )
                        elif not is_stop_loss_monitoring_session():
                            logger.debug(
                                f"🛡️ [STOP_LOSS] 모니터외 — DB 값 유지 ({position.stock_name})"
                            )
                        else:
                            current_price = await self._get_current_price(position.stock_code)
                            did_quote_api = True
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

                        # 종목별 시세 API를 친 경우에만 간격 — 고정 5초는 대시보드 타임아웃 유발
                        if did_quote_api and idx < len(positions):
                            gap = float(getattr(api_rate_limiter, "min_call_interval", 0.25) or 0.25)
                            await asyncio.sleep(max(0.15, min(gap, 1.0)))
                    except Exception as e:
                        logger.error(f"🛡️ [STOP_LOSS] 포지션 동기화 오류 (ID: {position.id}): {e}")

                session.commit()
                logger.info(f"🛡️ [STOP_LOSS] {len(positions)}개 포지션 API 동기화 완료")
                break
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 포지션 동기화 중 오류: {e}")
            import traceback
            logger.error(f"🛡️ [STOP_LOSS] 스택 트레이스: {traceback.format_exc()}")
    
    async def compute_exit_levels(
        self,
        position: Position,
        live: bool = False,
        holdings_map: Optional[Dict[str, dict]] = None,
    ) -> Dict:
        """청산 레벨 스냅샷. live=False: DB값만(빠름), live=True: API로 현재가·ATR.

        holdings_map을 넘기면 종목마다 잔고 API를 다시 치지 않는다 (대시보드 N+1 방지).
        """
        await self._load_auto_trade_settings()
        s = self.auto_trade_settings
        if not s:
            return {}

        api_live = live and is_stop_loss_monitoring_session()
        if live and not api_live:
            logger.debug(f"손절 모니터 시간 외 — live=false 처리 ({position.stock_name})")

        current_price = position.current_price or position.buy_price
        holding = None
        fetched_live = None
        if api_live:
            try:
                if holdings_map is None:
                    holdings_map, _ = await self._fetch_balance_holdings()
                holding = holdings_map.get(KiwoomAPI.normalize_stock_code(position.stock_code))
                if holding:
                    from utils.eval_pnl import apply_holding_to_position
                    apply_holding_to_position(position, holding)
                    current_price = position.current_price or current_price
                # KRX 외(NXT) 또는 잔고 미반영: 잔고 cur_pr는 종가에 남을 수 있어 통합시세 우선
                if (not holding) or (not is_krx_session()):
                    fetched_live = await asyncio.wait_for(
                        self._get_current_price(position.stock_code), timeout=6.0,
                    )
                    if fetched_live:
                        current_price = fetched_live
                        position.current_price = fetched_live
            except asyncio.TimeoutError:
                logger.warning(f"현재가 조회 타임아웃 — {position.stock_name}")

        buy_price = position.buy_price or current_price
        # live 요청에서만 일봉 고가 API — 대시보드 DB 폴링은 저장된 peak 사용
        peak = await self._resolve_position_peak(
            position, int(current_price), allow_api=api_live,
        )
        # 통합시세로 보정했으면 잔고 lspft(종가 기준)를 쓰지 않음
        holding_for_pl = None if fetched_live else holding
        profit_loss, profit_loss_rate = self._calc_profit(
            position, int(current_price), holding_for_pl,
        )
        position.current_profit_loss = profit_loss
        position.current_profit_loss_rate = profit_loss_rate

        if api_live and position.id and (fetched_live or holding):
            # 잔고 수량·매입 동기화 후, live 현재가·손익을 마지막에 저장 (cur_pr 덮어쓰기 방지)
            if holding:
                await self._sync_position_from_api(position.id, holding)
            await self._update_position_price(
                position.id, int(current_price), profit_loss, profit_loss_rate,
            )

        if position.id and getattr(position, "status", None) == "HOLDING":
            stored_peak = int(getattr(position, "peak_price", None) or buy_price)
            if peak != stored_peak:
                await self._update_position_tracking(position.id, peak, None)
                position.peak_price = peak

        is_breakout = getattr(position, "strategy_key", None) == "breakout"
        is_ymgp = getattr(position, "strategy_key", None) == "ymgp"
        is_jongga = getattr(position, "strategy_key", None) == "jongga"
        if is_breakout:
            tp = self._num(getattr(s, "breakout_trailing_start_pct", None))
        elif is_ymgp:
            tp = self._num(getattr(s, "ymgp_trailing_start_pct", None))
        elif is_jongga:
            tp = self._num(getattr(s, "jongga_trailing_start_pct", None))
        else:
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
            strategy_key=getattr(position, "strategy_key", None),
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

        trailing_stop_pct = self._num(
            getattr(s, "breakout_trailing_pct", None)
            if is_breakout
            else (
                getattr(s, "jongga_trailing_pct", None)
                if is_jongga
                else (
                    getattr(s, "ymgp_trailing_pct", None)
                    if is_ymgp
                    else s.trailing_stop_pct
                )
            )
        )
        level_rows = []
        for reason, price, method in candidates:
            level_rows.append({
                "reason": reason,
                "price": int(price),
                "method": method,
            })

        if is_breakout:
            sl_rate = self._num(getattr(s, "breakout_stop_loss_pct", None))
        elif is_jongga:
            sl_rate = self._num(getattr(s, "jongga_stop_loss_pct", None))
        elif is_ymgp:
            sl_rate = self._num(getattr(s, "ymgp_stop_loss_pct", None))
        else:
            sl_rate = self._num(s.stop_loss_rate)
        stop_loss_price_pct = (
            int(buy_price * (1 - abs(sl_rate) / 100.0)) if sl_rate and buy_price else None
        )

        soft_snap = self._soft_confirm_snapshot(position, s)
        if api_live and self._uses_legacy_ema_exit(position) and current_price:
            await self._eval_legacy_ema_exit(position, int(current_price), s)
        ema_snap = self._legacy_ema_snapshot_payload(position)

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
            "breakout_level_kind": getattr(position, "breakout_level_kind", None),
            "breakout_level_price": getattr(position, "breakout_level_price", None),
            **soft_snap,
            **ema_snap,
            "levels": level_rows,
            "liquidate_time": getattr(s, "liquidate_time", None) if getattr(s, "liquidate_before_close", False) else None,
            "levels_live": api_live,
        }

    def _soft_confirm_snapshot(
        self,
        position: Position,
        settings: Optional["AutoTradeSettings"],
    ) -> Dict[str, Any]:
        """상따·돌파 포지션의 SOFT 연속 확인 횟수 (손절 루프 메모리 카운터)."""
        strat = (getattr(position, "strategy_key", None) or "").strip().lower()
        if strat not in ("sangtta", "breakout"):
            return {}
        polls = max(1, int(getattr(settings, "soft_confirm_polls", 3) or 3)) if settings else 3
        pid = getattr(position, "id", None)
        if strat == "sangtta":
            count = int(self._sangtta_soft_counters.get(pid, 0) or 0) if pid else 0
            label = "상한가 이탈·급락"
        else:
            count = int(self._breakout_soft_counters.get(pid, 0) or 0) if pid else 0
            label = "구조 이탈"
        return {
            "soft_confirm_count": count,
            "soft_confirm_polls": polls,
            "soft_confirm_label": label,
        }

    @staticmethod
    def _is_legacy_position(position: Position) -> bool:
        from utils.market_risk_gate import normalize_strategy_key
        return normalize_strategy_key(getattr(position, "strategy_key", None)) == "legacy"

    @staticmethod
    def _uses_legacy_ema_exit(position: Position) -> bool:
        """5분 EMA 이탈 SOFT — 거래대금(레거시)·수급 돌파·상따 공통."""
        from utils.market_risk_gate import normalize_strategy_key
        key = normalize_strategy_key(getattr(position, "strategy_key", None))
        return key in ("legacy", "breakout", "sangtta")

    def _legacy_ema_snapshot_payload(self, position: Position) -> Dict[str, Any]:
        pid = getattr(position, "id", None)
        snap = self._legacy_ema_state.get(pid) if pid else None
        if not snap or snap.get("ema") is None:
            return {}
        return {
            "legacy_ema": snap.get("ema"),
            "legacy_ema_period": snap.get("period"),
            "legacy_ema_below": bool(snap.get("below")),
            "legacy_ema_consecutive": int(snap.get("consecutive") or 0),
            "legacy_ema_soft_min": snap.get("soft_minutes"),
            "legacy_ema_label": (
                f"EMA{int(snap.get('period') or 90)} 이탈"
            ),
        }

    async def _eval_legacy_ema_exit(
        self,
        position: Position,
        current_price: int,
        settings: Optional[AutoTradeSettings],
    ) -> Optional[Dict[str, Any]]:
        """5분 EMA 이탈 SOFT(레거시·수급돌파·상따). 차트 없거나 비활성이면 None."""
        from utils.legacy_ema_exit import evaluate_legacy_ema_soft_exit, legacy_ema_exit_params
        from utils.ema_fractal import drop_forming_minute_bar

        enabled, period, soft_min, band_pct = legacy_ema_exit_params(settings)
        if not enabled:
            pid = getattr(position, "id", None)
            if pid:
                self._legacy_ema_state.pop(pid, None)
            return None
        try:
            code = KiwoomAPI.normalize_stock_code(position.stock_code or "")
            # 키움 영웅문 EMA와 시드 오차를 최소화하도록 충분한 과거 봉을 예열한다.
            # 5분봉 900개는 API 1회 응답 범위이며 캐시(25초)를 종목별로 재사용한다.
            need = max(900, int(period) + int(soft_min) + 40)
            raw = await self.kiwoom_api.get_stock_chart_data(
                code, "5M", max_bars=need, cache_ttl_sec=25,
            )
            bars = drop_forming_minute_bar(raw or [], interval_minutes=5)
            result = evaluate_legacy_ema_soft_exit(
                bars,
                float(current_price or 0),
                now=now_kst(),
                period=period,
                soft_minutes=soft_min,
                band_pct=band_pct,
                buy_time=buy_time_utc_naive_to_kst(getattr(position, "buy_time", None)),
            )
        except Exception as e:
            logger.debug(f"🛡️ [STOP_LOSS] EMA 판정 오류 {position.stock_name}: {e}")
            return None
        pid = getattr(position, "id", None)
        if pid:
            self._legacy_ema_state[pid] = result
        return result

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
        now_mono = time.monotonic()
        if (
            cached
            and cached[1] == buy_iso
            and (now_mono - cached[2]) < self._since_buy_peak_cache_ttl_sec
        ):
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

        self._since_buy_peak_cache[code] = (peak, buy_iso, now_mono)
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
        """시작% 도달 시 armed+floor. 한 번 잠긴 바닥은 고점 보정으로 해제하지 않음."""
        return self._resolve_trailing_state(
            position, buy_price, peak, trail_start_val,
        )

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
        """패턴 B: 시작% 도달 시 armed + floor 잠금. armed 후 바닥 유지(추가매수 시에만 상향)."""
        stored_armed = bool(getattr(position, "trailing_armed", False))
        stored_floor = getattr(position, "trailing_floor_price", None)

        if trail_start_rate is None or trail_start_rate <= 0:
            return True, None

        peak_rate = self._peak_rate_pct(buy_price, peak)
        if stored_armed:
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
        try:
            for db in get_db():
                session: Session = db
                p = session.query(Position).filter(Position.id == position_id).first()
                if not p or p.status not in ("HOLDING", "TRAILING"):
                    return
                sk = (getattr(p, "strategy_key", None) or "").strip().lower()
                if sk == "breakout":
                    trail_start = self._num(getattr(s, "breakout_trailing_start_pct", None))
                elif sk == "ymgp":
                    trail_start = self._num(getattr(s, "ymgp_trailing_start_pct", None))
                elif sk == "jongga":
                    trail_start = self._num(getattr(s, "jongga_trailing_start_pct", None))
                else:
                    trail_start = self._num(s.take_profit_rate)
                if not trail_start or trail_start <= 0:
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
        strategy_key: Optional[str] = None,
    ) -> List[Tuple[str, float, str]]:
        """손절·트레일·수익잠금 후보. %·ATR을 모두 포함하고 유효선은 최고가(가장 타이트)."""
        candidates: List[Tuple[str, float, str]] = []
        floor = int(trailing_floor_price) if trailing_floor_price else None

        def _apply_trail_floor(raw: float) -> float:
            if floor is not None:
                return max(raw, float(floor))
            return raw

        is_breakout = (strategy_key or "").strip().lower() == "breakout"
        is_ymgp = (strategy_key or "").strip().lower() == "ymgp"
        is_jongga = (strategy_key or "").strip().lower() == "jongga"
        is_fractal = (strategy_key or "").strip().lower() == "fractal"
        if is_fractal:
            return []
        if is_breakout:
            sl = self._num(getattr(settings, "breakout_stop_loss_pct", None))
        elif is_ymgp:
            sl = self._num(getattr(settings, "ymgp_stop_loss_pct", None))
        elif is_jongga:
            sl = self._num(getattr(settings, "jongga_stop_loss_pct", None))
        else:
            sl = self._num(settings.stop_loss_rate)
        if sl:
            candidates.append(("STOP_LOSS", buy_price * (1 - abs(sl) / 100.0), "PCT"))

        atr_stop_mult = self._num(settings.atr_mult_stop)
        if not is_breakout and not is_ymgp and not is_jongga and atr and atr_stop_mult:
            candidates.append(("STOP_LOSS", buy_price - atr * atr_stop_mult, "ATR"))

        lock_trigger = self._num(settings.profit_lock_trigger)
        if lock_trigger:
            peak_rate = self._peak_rate_pct(buy_price, peak)
            if peak_rate >= lock_trigger:
                lock_floor = self._num(settings.profit_lock_floor)
                lock_floor = 0.0 if lock_floor is None else lock_floor
                candidates.append(("PROFIT_LOCK", buy_price * (1 + lock_floor / 100.0), "PCT"))

        if trailing_armed:
            if is_breakout:
                tr = self._num(getattr(settings, "breakout_trailing_pct", None))
            elif is_ymgp:
                tr = self._num(getattr(settings, "ymgp_trailing_pct", None))
            elif is_jongga:
                tr = self._num(getattr(settings, "jongga_trailing_pct", None))
            else:
                tr = self._num(settings.trailing_stop_pct)
            if tr:
                raw = peak * (1 - tr / 100.0)
                candidates.append(("TRAILING", _apply_trail_floor(raw), "PCT"))

            atr_trail_mult = self._num(settings.atr_mult_trail)
            if not is_breakout and not is_jongga and atr and atr_trail_mult:
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

    def _jongga_leg2_filled(self, position_id: int) -> bool:
        from utils.jongga_engine import is_jongga_leg2_fill_note

        try:
            for db in get_db():
                rows = (
                    db.query(PositionBuyFill)
                    .filter(PositionBuyFill.position_id == int(position_id))
                    .all()
                )
                return any(is_jongga_leg2_fill_note(getattr(r, "note", None)) for r in rows)
        except Exception as e:
            logger.debug(f"🛡️ [STOP_LOSS] 종가배팅 2차 체결 조회 오류: {e}")
        return False

    async def _eval_jongga_ma_dc_exit(self, position: Position, settings) -> Optional[str]:
        """종가배팅 2차 물타기 후 EMA15≤92 + 이격 확대 시 청산."""
        if not self._jongga_leg2_filled(int(position.id)):
            return None
        from utils.jongga_engine import evaluate_ma_dc_exit_after_avg_down
        from utils.ma1592 import chart_tf_interval_minutes, compute_bar_ma, normalize_chart_tf
        from utils.ema_fractal import drop_forming_minute_bar

        try:
            far_pct = float(getattr(settings, "ma1592_price_lead_far_pct", None) or 3.0)
        except (TypeError, ValueError):
            far_pct = 3.0

        ma15 = 0.0
        ma92 = 0.0
        exec_tf = normalize_chart_tf("3M")
        interval_min = chart_tf_interval_minutes(exec_tf)
        try:
            raw = await self.kiwoom_api.get_stock_chart_data(
                position.stock_code, exec_tf, max_bars=150, cache_ttl_sec=60,
            )
            bars = drop_forming_minute_bar(raw or [], interval_minutes=interval_min)
            closes = []
            for b in bars:
                try:
                    c = float(b.get("close") or 0)
                except (TypeError, ValueError):
                    c = 0
                if c > 0:
                    closes.append(c)
            f, s, _, _ = compute_bar_ma(closes, fast=15, slow=92, ma_type="ema")
            if f is not None:
                ma15 = float(f)
            if s is not None:
                ma92 = float(s)
        except Exception as e:
            logger.debug(f"🛡️ [STOP_LOSS] 종가배팅 EMA 조회 실패: {e}")
            return None

        return evaluate_ma_dc_exit_after_avg_down(
            ma15, ma92, avg_down_done=True, far_pct=far_pct,
        )

    def _jongga_open_avg_buy_inflight(self, stock_code: str) -> bool:
        from utils.auto_trade_engine import parse_signal_meta

        try:
            for db in get_db():
                rows = (
                    db.query(PendingBuySignal)
                    .filter(
                        PendingBuySignal.stock_code == stock_code,
                        PendingBuySignal.status.in_(["PENDING", "PROCESSING", "ORDERED"]),
                    )
                    .all()
                )
                for sig in rows:
                    meta = parse_signal_meta(sig)
                    if meta.get("open_avg_down") or meta.get("source") == "jongga_open_avg_down":
                        return True
                    if meta.get("is_add_buy") and str(meta.get("strategy") or "") == "jongga":
                        try:
                            if int(meta.get("entry_leg") or meta.get("jongga_entry_leg") or 0) == 2:
                                return True
                        except (TypeError, ValueError):
                            pass
                return False
        except Exception as e:
            logger.debug(f"🛡️ [STOP_LOSS] 시초 물타기 신호 조회 오류: {e}")
        return False

    async def _should_defer_jongga_open_avg_down(
        self,
        position: Position,
        current_price: int,
        sell_reason: str,
        settings: AutoTradeSettings,
    ) -> bool:
        if sell_reason != "STOP_LOSS":
            return False
        from utils.jongga_engine import (
            DEFAULT_STOP_LOSS_PCT,
            at_or_below_stop,
            in_open_avg_down_window,
            is_jongga_open_avg_down_day,
            jongga_pct_stop_price,
            open_avg_down_done_in_state,
            pig_split_enabled,
            prev_session_state,
            should_defer_jongga_stop_for_open_avg_down,
        )

        buy_px = int(getattr(position, "buy_price", None) or 0)
        stored_stop = int(getattr(position, "stop_loss_price", None) or 0)
        sl_pct = getattr(settings, "jongga_stop_loss_pct", None)
        if sl_pct is None:
            sl_pct = DEFAULT_STOP_LOSS_PCT
        calc_stop = jongga_pct_stop_price(buy_px, sl_pct) or 0
        stop_px = stored_stop if stored_stop > 0 else calc_stop
        prev = prev_session_state()
        return should_defer_jongga_stop_for_open_avg_down(
            pig_split=pig_split_enabled(settings),
            first_exit_day=is_jongga_open_avg_down_day(getattr(position, "buy_time", None)),
            in_open_window=in_open_avg_down_window(),
            leg2_filled=self._jongga_leg2_filled(int(position.id)),
            open_avg_already_done=open_avg_down_done_in_state(prev),
            price_at_or_below_stop=at_or_below_stop(current_price, stop_px),
            pending_open_avg_buy=self._jongga_open_avg_buy_inflight(position.stock_code or ""),
        )

    @debug_tracer.trace_async(component="STOP_LOSS")
    async def _check_position_stop_loss(self, position: Position, holding: Optional[dict] = None):
        """개별 포지션 청산 판단.
        패턴 B: 시작% 도달 → trailing_armed + floor 잠금(이후 고점 보정으로 해제하지 않음),
        고점 따라 트레일링(바닥 이하로 선 하락 없음).
        """
        try:
            if holding:
                from utils.eval_pnl import apply_holding_to_position
                apply_holding_to_position(position, holding)
                current_price = position.current_price
            else:
                current_price = None

            # NXT 등 KRX 외: 잔고 cur_pr(종가)보다 통합시세 우선 — 손절 판단·표시 일치
            if (not current_price) or (
                is_stop_loss_monitoring_session() and not is_krx_session()
            ):
                live_px = await self._get_current_price(position.stock_code)
                if live_px:
                    current_price = live_px
                    position.current_price = live_px

            if not current_price:
                logger.warning(f"🛡️ [STOP_LOSS] 현재가 조회 실패 - {position.stock_name}")
                return

            s = self.auto_trade_settings
            buy_price = position.buy_price or current_price

            # 손익 — live 시세면 잔고 lspft(종가) 대신 매입×현재가
            holding_for_pl = (
                None
                if (
                    is_stop_loss_monitoring_session()
                    and not is_krx_session()
                )
                else holding
            )
            profit_loss, profit_loss_rate = self._calc_profit(
                position, current_price, holding_for_pl,
            )
            if holding:
                await self._sync_position_from_api(position.id, holding)
            await self._update_position_price(
                position.id, current_price, profit_loss, profit_loss_rate,
            )

            # 고점 갱신 (매수 시각 이후 고가만)
            peak = await self._resolve_position_peak(position, int(current_price), allow_api=True)

            is_breakout = getattr(position, "strategy_key", None) == "breakout"
            is_ymgp = getattr(position, "strategy_key", None) == "ymgp"
            is_jongga = getattr(position, "strategy_key", None) == "jongga"
            is_fractal = getattr(position, "strategy_key", None) == "fractal"

            if is_fractal:
                stop_px = int(getattr(position, "stop_loss_price", None) or 0)
                tp_px = int(getattr(position, "take_profit_price", None) or 0)
                sell_reason = None
                detail = ""
                if stop_px > 0 and int(current_price) <= stop_px:
                    sell_reason = "STOP_LOSS"
                    detail = (
                        f"프랙탈 손절: 현재 {int(current_price):,} ≤ {stop_px:,} "
                        f"(진입시 50EMA 스냅샷)"
                    )
                elif tp_px > 0 and int(current_price) >= tp_px:
                    sell_reason = "TAKE_PROFIT"
                    detail = (
                        f"프랙탈 익절: 현재 {int(current_price):,} ≥ {tp_px:,} (R:R)"
                    )
                else:
                    liq_on = bool(getattr(s, "fractal_liquidate_before_close", True))
                    if liq_on:
                        t = getattr(s, "fractal_liquidate_time", None) or "15:10"
                        try:
                            lh, lm = map(int, str(t).split(":"))
                            kst = as_kst()
                            if (kst.hour, kst.minute) >= (lh, lm) and kst.hour < 16:
                                sell_reason = "MARKET_CLOSE"
                                detail = f"프랙탈 당일청산 ({t})"
                        except Exception:
                            pass
                await self._update_position_tracking(position.id, peak, stop_px or None)
                if sell_reason:
                    from utils.sell_reason_labels import classify_exit_reason
                    classified = classify_exit_reason(
                        sell_reason,
                        profit_loss=profit_loss,
                        profit_loss_rate=profit_loss_rate,
                    )
                    if classified != sell_reason:
                        detail = (
                            f"{sell_reason}→{classified} | {detail}"
                            if detail
                            else f"{sell_reason}→{classified}"
                        )
                        sell_reason = classified
                    if await self._has_any_pending_sell_order(position.id):
                        await self._prepare_sell(position.id, sell_reason)
                    if await self._has_pending_sell_order(position.id, for_reason=sell_reason):
                        return
                    await self._execute_sell_order(
                        position, current_price, sell_reason, detail,
                    )
                return

            is_ma1592 = getattr(position, "strategy_key", None) == "ma1592"
            if is_ma1592:
                await self._monitor_ma1592_exit(
                    position, current_price, peak, buy_price,
                    profit_loss, profit_loss_rate, s,
                )
                return

            # 종가배팅: 매수 당일은 청산 모니터 스킵, 익일부터 고정손절·트레일
            if is_jongga:
                from utils.jongga_engine import is_exit_management_day
                if not is_exit_management_day(getattr(position, "buy_time", None)):
                    await self._update_position_tracking(position.id, peak, None)
                    return

            if is_breakout:
                trail_start = self._num(getattr(s, "breakout_trailing_start_pct", None))
            elif is_ymgp:
                trail_start = self._num(getattr(s, "ymgp_trailing_start_pct", None))
            elif is_jongga:
                trail_start = self._num(getattr(s, "jongga_trailing_start_pct", None))
            else:
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
            ymgp_sell_qty = None

            # Phase3: 상따 전용 soft/hard 임계 (상한가 이탈, 급락룰)
            try:
                if getattr(position, "strategy_key", None) == "sangtta":
                    lim_soft = float(getattr(s, "limit_break_soft_pct", 2.0) or 2.0)
                    lim_hard = float(getattr(s, "limit_break_hard_pct", 3.0) or 3.0)
                    drop_soft = float(getattr(s, "sharp_drop_soft_pct", 3.0) or 3.0)
                    drop_hard = float(getattr(s, "sharp_drop_hard_pct", 5.0) or 5.0)
                    soft_required = int(getattr(s, "soft_confirm_polls", 3) or 3)

                    # 상한가(approx): 전일 종가 기준 30% 상한가 근사치
                    ul_price = None
                    try:
                        code = KiwoomAPI.normalize_stock_code(position.stock_code or "")
                        daily = await self.kiwoom_api.get_stock_chart_data(code, "1D")
                        if daily and len(daily) >= 2:
                            prev = daily[-2]
                            prev_close = int(prev.get("close") or 0)
                            if prev_close > 0:
                                ul_price = int(prev_close * 1.3)
                    except Exception:
                        ul_price = None

                    # 상한가 터치 여부(매수 이후 고점이 상한가 근처였는지)
                    touched_upper = False
                    try:
                        if ul_price and peak and peak >= int(ul_price * 0.999):
                            touched_upper = True
                    except Exception:
                        touched_upper = False

                    # 상한가 이탈 판단 (HARD 즉시, SOFT 연속 확인)
                    if ul_price and touched_upper:
                        soft_px = int(ul_price * (1 - lim_soft / 100.0))
                        hard_px = int(ul_price * (1 - lim_hard / 100.0))
                        if current_price <= hard_px:
                            sell_reason = "STOP_LOSS"
                            detail = f"상한가 이탈(HARD): 현재 {current_price:,} ≤ {hard_px:,} (상한가 {ul_price:,})"
                        elif current_price <= soft_px:
                            cnt = self._sangtta_soft_counters.get(position.id, 0) + 1
                            self._sangtta_soft_counters[position.id] = cnt
                            if cnt >= soft_required:
                                sell_reason = "STOP_LOSS"
                                detail = f"상한가 이탈(SOFT≧{soft_required}회): 현재 {current_price:,} ≤ {soft_px:,} (상한가 {ul_price:,})"
                            else:
                                log_activity("SELL", f"상따 SOFT 경고 {position.stock_name}: {cnt}/{soft_required} (현재 {current_price:,} ≤ {soft_px:,})", "warn", stock_code=position.stock_code)
                        else:
                            if self._sangtta_soft_counters.get(position.id):
                                self._sangtta_soft_counters[position.id] = 0

                    # 급락룰 — 당일고 대비 SOFT/HARD
                    if not sell_reason and peak and peak > 0:
                        soft_px2 = int(peak * (1 - drop_soft / 100.0))
                        hard_px2 = int(peak * (1 - drop_hard / 100.0))
                        if current_price <= hard_px2:
                            sell_reason = "STOP_LOSS"
                            detail = f"급락(HARD): 현재 {current_price:,} ≤ {hard_px2:,} (고점 {peak:,})"
                        elif current_price <= soft_px2:
                            cnt2 = self._sangtta_soft_counters.get(position.id, 0) + 1
                            self._sangtta_soft_counters[position.id] = cnt2
                            if cnt2 >= soft_required:
                                sell_reason = "STOP_LOSS"
                                detail = f"급락(SOFT≧{soft_required}회): 현재 {current_price:,} ≤ {soft_px2:,} (고점 {peak:,})"
                            else:
                                log_activity("SELL", f"상따 급락 SOFT 경고 {position.stock_name}: {cnt2}/{soft_required} (현재 {current_price:,} ≤ {soft_px2:,})", "warn", stock_code=position.stock_code)
                        else:
                            if self._sangtta_soft_counters.get(position.id):
                                self._sangtta_soft_counters[position.id] = 0
            except Exception as e:
                logger.debug(f"🛡️ [STOP_LOSS] 상따 전용 임계 적용 오류: {e}")

            # 수급 돌파 전용 구조 이탈 — 고정손절·트레일보다 먼저 평가
            try:
                if not sell_reason and getattr(position, "strategy_key", None) == "breakout":
                    level = int(getattr(position, "breakout_level_price", None) or 0)
                    soft_pct = float(getattr(s, "struct_break_soft_pct", 1.0) or 1.0)
                    hard_pct = float(getattr(s, "struct_break_hard_pct", 2.0) or 2.0)
                    soft_required = max(1, int(getattr(s, "soft_confirm_polls", 3) or 3))
                    state = classify_breakout_structure(
                        int(current_price), level, soft_pct, hard_pct,
                    )
                    if state == "HARD":
                        self._breakout_soft_counters[position.id] = 0
                        sell_reason = "STOP_LOSS"
                        detail = (
                            f"구조 이탈(HARD): 현재 {current_price:,} ≤ "
                            f"{int(level * (1 - hard_pct / 100)):,} (돌파레벨 {level:,})"
                        )
                    elif state == "SOFT":
                        count = self._breakout_soft_counters.get(position.id, 0) + 1
                        self._breakout_soft_counters[position.id] = count
                        if count >= soft_required:
                            sell_reason = "STOP_LOSS"
                            detail = (
                                f"구조 이탈(SOFT≧{soft_required}회): 현재 {current_price:,} ≤ "
                                f"{int(level * (1 - soft_pct / 100)):,} (돌파레벨 {level:,})"
                            )
                        else:
                            log_activity(
                                "SELL",
                                f"돌파 구조 SOFT 경고 {position.stock_name}: "
                                f"{count}/{soft_required} (레벨 {level:,})",
                                "warn",
                                stock_code=position.stock_code,
                            )
                    else:
                        self._breakout_soft_counters[position.id] = 0
                    if level <= 0:
                        logger.warning(
                            f"🛡️ [STOP_LOSS] 돌파 레벨 없음 — 고정손절로 폴백: "
                            f"{position.stock_name} #{position.id}"
                        )
            except Exception as e:
                logger.debug(f"🛡️ [STOP_LOSS] 돌파 구조 이탈 적용 오류: {e}")

            # 역매공파: 기준봉/MA 손절 · 분할 익절 (전량·부분)
            try:
                if not sell_reason and getattr(position, "strategy_key", None) == "ymgp":
                    from utils.ymgp_engine import (
                        compute_mas,
                        mark_stopped,
                        partial_sell_qty,
                        stop_invalidated,
                        take_profit_target,
                    )
                    ref = {
                        "high": getattr(position, "ymgp_ref_high", None),
                        "low": getattr(position, "ymgp_ref_low", None)
                            or getattr(position, "breakout_level_price", None),
                        "open": getattr(position, "ymgp_ref_open", None),
                    }
                    bars = await self._ymgp_daily_bars(position.stock_code)
                    mas = compute_mas(bars or [], s) if bars else {}
                    inv, inv_detail = stop_invalidated(
                        int(current_price), ref, mas, s, use_close_vs_ma=True,
                    )
                    if inv:
                        sell_reason = "STOP_LOSS"
                        detail = f"역매공파 무효화: {inv_detail}"
                        mark_stopped(position.stock_code, s)
                    elif getattr(s, "ymgp_enable_partial_tp", True):
                        tp_stage = int(getattr(position, "ymgp_tp_stage", None) or 0)
                        box = None
                        if bars:
                            from utils.ymgp_engine import _box_stats, _as_int
                            box = _box_stats(bars, _as_int(s, "ymgp_box_days", 15))
                        target, tlabel = take_profit_target(tp_stage, box, mas)
                        if target and current_price >= float(target):
                            qty = int(position.buy_quantity or 0)
                            sell_n = partial_sell_qty(qty, tp_stage, s)
                            if sell_n >= qty:
                                sell_reason = "TAKE_PROFIT"
                                detail = f"역매공파 {tlabel} 전량 ({current_price:,} ≥ {float(target):,.0f})"
                                ymgp_sell_qty = qty
                            elif sell_n > 0:
                                sell_reason = "TAKE_PROFIT"
                                detail = (
                                    f"역매공파 {tlabel} 분할 {sell_n}/{qty}주 "
                                    f"({current_price:,} ≥ {float(target):,.0f})"
                                )
                                ymgp_sell_qty = sell_n
                                await self._bump_ymgp_tp_stage(position.id, tp_stage + 1)
            except Exception as e:
                logger.debug(f"🛡️ [STOP_LOSS] 역매공파 청산 적용 오류: {e}")

            # 레거시·수급돌파·상따: 5분 EMA 이탈 SOFT (고정손절·트레일보다 먼저)
            # 상따 이탈/급락·돌파 구조 이탈 미발동 시에만 여기로 옴
            try:
                if (
                    not sell_reason
                    and self._uses_legacy_ema_exit(position)
                ):
                    ema_res = await self._eval_legacy_ema_exit(
                        position, int(current_price), s,
                    )
                    sk = (getattr(position, "strategy_key", None) or "").strip().lower()
                    strat_tag = (
                        "상따" if sk == "sangtta"
                        else ("돌파" if sk == "breakout" else "레거시")
                    )
                    if ema_res and ema_res.get("triggered"):
                        sell_reason = "STOP_LOSS"
                        detail = str(ema_res.get("detail") or "EMA 이탈 SOFT 청산")
                        logger.warning(
                            f"🛡️ [STOP_LOSS] {strat_tag} EMA SOFT - {position.stock_name}: "
                            f"{ema_res.get('consecutive')}/{ema_res.get('required_bars')}개 확정 5분봉"
                        )
                    elif ema_res and ema_res.get("below"):
                        log_activity(
                            "SELL",
                            f"{strat_tag} EMA SOFT 경고 {position.stock_name}: "
                            f"{ema_res.get('consecutive')}/{ema_res.get('required_bars')}개 확정 5분봉 "
                            f"(현재 {int(current_price):,} ≤ 선, EMA {float(ema_res.get('ema') or 0):,.0f}"
                            f" 이격>{float(ema_res.get('band_pct') if ema_res.get('band_pct') is not None else 1):g}%)",
                            "warn",
                            stock_code=position.stock_code,
                        )
            except Exception as e:
                logger.debug(f"🛡️ [STOP_LOSS] EMA 이탈 적용 오류: {e}")

            # 종가배팅: 2차 물타기 후 EMA15≤92 + 이격 확대 시 즉시 청산
            try:
                if not sell_reason and is_jongga and s:
                    dc_detail = await self._eval_jongga_ma_dc_exit(position, s)
                    if dc_detail:
                        sell_reason = "STOP_LOSS"
                        detail = f"종가배팅 DC+이격 확대: {dc_detail}"
                        logger.warning(
                            f"🛡️ [STOP_LOSS] 종가배팅 DC+이격 - {position.stock_name}: {dc_detail}"
                        )
            except Exception as e:
                logger.debug(f"🛡️ [STOP_LOSS] 종가배팅 DC+이격 적용 오류: {e}")

            # 장마감 청산은 구조·손절 규칙이 미발동일 때만 적용
            # breakout·ymgp·jongga는 오버나잇 허용이므로 MARKET_CLOSE 제외
            sk = (getattr(position, "strategy_key", None) or "").strip().lower()
            if (
                not sell_reason
                and sk not in ("breakout", "ymgp", "jongga", "fractal")
                and self._is_past_liquidation_time()
            ):
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
                    strategy_key=getattr(position, "strategy_key", None),
                )

                if candidates:
                    reason_eff, eff_stop, _ = max(candidates, key=lambda x: x[1])
                    if current_price <= eff_stop:
                        from utils.sell_reason_labels import classify_exit_reason

                        # 트레일·수익잠금은 익절 바닥/보호선 — 수익(+)이면 익절로 분류
                        sell_reason = classify_exit_reason(
                            reason_eff,
                            profit_loss=profit_loss,
                            profit_loss_rate=profit_loss_rate,
                        )
                        line_name = (
                            "익절선"
                            if sell_reason == "TAKE_PROFIT"
                            else ("매도선" if reason_eff in ("TRAILING", "PROFIT_LOCK") else "손절선")
                        )
                        detail = (
                            f"{reason_eff} 청산: 현재가 {current_price:,} ≤ {line_name} {eff_stop:,.0f} "
                            f"(고점 {peak:,}, 손익 {profit_loss_rate:+.2f}%)"
                        )
                        if reason_eff != sell_reason:
                            detail = f"{reason_eff}→{sell_reason} | {detail}"
                        logger.warning(
                            f"🛡️ [STOP_LOSS] {reason_eff}->{sell_reason} - "
                            f"{position.stock_name}: {profit_loss_rate:+.2f}%"
                        )

            # 고점/손절선 저장 (매도 안 해도 추적 유지)
            await self._update_position_tracking(position.id, peak, int(eff_stop) if eff_stop else None)

            if sell_reason and is_jongga and s and await self._should_defer_jongga_open_avg_down(
                position, int(current_price), sell_reason, s,
            ):
                msg = (
                    f"종가배팅 시초 물타기 대기 — 손절 보류 {position.stock_name} "
                    f"(현재 {int(current_price):,})"
                )
                logger.info(f"🛡️ [STOP_LOSS] {msg}")
                log_activity("SELL", msg, "info", stock_code=position.stock_code)
                return

            # 매도 실행
            if sell_reason:
                # 상따 HARD/SOFT·구조 이탈 등은 메커니즘상 STOP_LOSS로 잡히지만
                # 실현 손익이 +이면 결과 분류는 익절(TAKE_PROFIT)로 맞춘다.
                from utils.sell_reason_labels import classify_exit_reason

                classified = classify_exit_reason(
                    sell_reason,
                    profit_loss=profit_loss,
                    profit_loss_rate=profit_loss_rate,
                )
                if classified != sell_reason:
                    detail = (
                        f"{sell_reason}→{classified} | {detail}"
                        if detail
                        else f"{sell_reason}→{classified}"
                    )
                    logger.info(
                        f"🛡️ [STOP_LOSS] 사유 재분류 {position.stock_name}: "
                        f"{sell_reason}→{classified} (손익 {profit_loss_rate:+.2f}%)"
                    )
                    sell_reason = classified

                # PENDING/ORDERED 매도 주문이 이미 있으면,
                # 현재 sell_reason의 우선순위가 더 높더라도 먼저 '하위 사유 주문'을 취소해
                # 중복/불일치 sell_orders가 쌓이지 않게 합니다.
                if await self._has_any_pending_sell_order(position.id):
                    await self._prepare_sell(position.id, sell_reason)

                if await self._has_pending_sell_order(position.id, for_reason=sell_reason):
                    logger.debug(f"🛡️ [STOP_LOSS] 매도 대기 중 — {position.stock_name}, 추가 주문 생략")
                    return
                await self._execute_sell_order(
                    position, current_price, sell_reason, detail,
                    quantity=ymgp_sell_qty,
                )

        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 포지션 확인 오류 - {position.stock_name}: {e}")
    
    async def _get_current_price(self, stock_code: str) -> Optional[int]:
        """현재가 조회"""
        try:
            if not is_stop_loss_monitoring_session():
                logger.debug(f"🛡️ [STOP_LOSS] 모니터외 — 현재가 조회 생략: {stock_code}")
                return None
            logger.debug(f"🛡️ [STOP_LOSS] 현재가 조회 시도: {stock_code}")
            from utils.api_traffic_guard import APIPriority
            current_price = await self.kiwoom_api.get_current_price(
                stock_code,
                priority=APIPriority.CRITICAL,
                allow_off_hours=not is_krx_session(),
            )
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

    @staticmethod
    def _holdings_sellable_map(balance: Optional[dict]) -> Dict[str, Optional[int]]:
        """계좌 잔고 → {코드: 매도가능수량 또는 None(필드 없음)}."""
        out: Dict[str, Optional[int]] = {}
        if not balance or balance.get("_error"):
            return out
        for h in balance.get("stk_acnt_evlt_prst") or []:
            code = KiwoomAPI.normalize_stock_code(h.get("stk_cd", ""))
            if not code:
                continue
            if "sellable_qty" in h and h.get("sellable_qty") not in (None, ""):
                out[code] = _parse_kiwoom_int(h.get("sellable_qty"))
            else:
                out[code] = None
        return out

    @staticmethod
    def _open_sell_locked_qty(stock_code: str, unfilled_items: Optional[List[dict]]) -> int:
        code = KiwoomAPI.normalize_stock_code(stock_code or "")
        if not code:
            return 0
        total = 0
        for item in unfilled_items or []:
            if KiwoomAPI.normalize_stock_code(item.get("stk_cd") or "") != code:
                continue
            if not is_unfilled_sell_side(item):
                continue
            total += max(0, int(item.get("oso_qty") or 0))
        return total

    async def _fetch_unfilled_sells(self, *, force: bool = False) -> Tuple[List[dict], bool]:
        """매도 미체결 목록. (items, ok) — 조회 실패 시 ok=False."""
        now = time.monotonic()
        cached = getattr(self, "_unfilled_sells_cache", None)
        if not force and cached and now - cached[0] < 8.0:
            return cached[1], cached[2]
        try:
            result = await self.kiwoom_api.get_unfilled_orders(trde_tp="1")
        except Exception as e:
            logger.debug(f"🛡️ [STOP_LOSS] 미체결 조회 실패: {e}")
            return [], False
        ok = bool(result.get("success"))
        items = list(result.get("items") or []) if ok else []
        self._unfilled_sells_cache = (now, items, ok)
        return items, ok

    async def _broker_cancel_sell(self, sell: SellOrder) -> bool:
        """키움 원주문 취소. 성공 시에만 True."""
        oid = str(sell.sell_order_id or "").strip()
        if not oid:
            return False
        try:
            result = await self.kiwoom_api.cancel_order(
                stock_code=sell.stock_code,
                order_no=oid,
                quantity=int(sell.sell_quantity or 0),
            )
            return bool(result.get("success"))
        except Exception as e:
            logger.warning(f"🛡️ [STOP_LOSS] 키움 주문취소 실패 — {sell.stock_name} #{sell.id}: {e}")
            return False

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
            # ORDERED는 키움 원주문이 살아 있을 수 있어 DB만 취소하지 않는다.
            for sell in sells[:-1]:
                if sell.status == "PENDING" and not str(sell.sell_order_id or "").strip():
                    sell.status = "CANCELLED"
                    changed += 1
                    logger.info(f"🛡️ [RECONCILE] 중복 PENDING 취소 — {sell.stock_name} #{sell.id}")

        for sell in session.query(SellOrder).filter(
            SellOrder.status.in_(("PENDING", "ORDERED")),
        ).all():
            age = self._sell_order_age_minutes(sell)

            # 오래된 PENDING (미접수)만 DB 취소. ORDERED stale은 키움 취소 없이 건드리지 않음.
            if sell.status == "PENDING" and age >= STALE_SELL_ORDER_MINUTES:
                sell.status = "CANCELLED"
                changed += 1
                logger.warning(f"🛡️ [RECONCILE] 만료 PENDING 취소 — {sell.stock_name} #{sell.id}")
                continue
        return changed

    async def _cancel_inferior_sell_orders(self, session: Session, position_id: int, new_reason: str) -> int:
        """새 청산 사유가 더 긴급하면 기존 하위 주문 취소. 키움 취소 성공 후에만 DB CANCELLED."""
        new_rank = _sell_reason_rank(new_reason)
        n = 0
        for sell in session.query(SellOrder).filter(
            SellOrder.position_id == position_id,
            SellOrder.status.in_(("PENDING", "ORDERED")),
        ).all():
            if _sell_reason_rank(sell.sell_reason) > new_rank:
                if sell.status == "PENDING" and not str(sell.sell_order_id or "").strip():
                    sell.status = "CANCELLED"
                    n += 1
                    logger.info(
                        f"🛡️ [STOP_LOSS] 하위 매도 취소 — {sell.stock_name} "
                        f"{sell.sell_reason} → {new_reason}"
                    )
                    continue
                ok = await self._broker_cancel_sell(sell)
                if ok:
                    sell.status = "CANCELLED"
                    n += 1
                    logger.info(
                        f"🛡️ [STOP_LOSS] 하위 매도 키움취소 — {sell.stock_name} "
                        f"{sell.sell_reason} → {new_reason}"
                    )
                else:
                    logger.warning(
                        f"🛡️ [STOP_LOSS] 하위 매도 키움취소 실패 — {sell.stock_name} "
                        f"#{sell.id} ORDERED 유지 (재주문 금지)"
                    )
        return n

    async def _cancel_all_open_sell_orders(self, session: Session, position_id: int) -> int:
        """포지션의 미완료 매도: 키움 취소 성공 또는 미접수 PENDING만 DB 취소."""
        n = 0
        for sell in session.query(SellOrder).filter(
            SellOrder.position_id == position_id,
            SellOrder.status.in_(("PENDING", "ORDERED")),
        ).all():
            if sell.status == "PENDING" and not str(sell.sell_order_id or "").strip():
                sell.status = "CANCELLED"
                n += 1
                logger.info(
                    f"🛡️ [STOP_LOSS] 매도 취소(수동청산) — {sell.stock_name} "
                    f"#{sell.id} ({sell.sell_reason})"
                )
                continue
            ok = await self._broker_cancel_sell(sell)
            if ok:
                sell.status = "CANCELLED"
                n += 1
                logger.info(
                    f"🛡️ [STOP_LOSS] 매도 키움취소(수동청산) — {sell.stock_name} "
                    f"#{sell.id} ({sell.sell_reason})"
                )
            else:
                logger.warning(
                    f"🛡️ [STOP_LOSS] 매도 키움취소 실패 — {sell.stock_name} "
                    f"#{sell.id} 유지"
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
            cancelled = await self._cancel_all_open_sell_orders(session, position_id)
            # commit(expire_on_commit) 후 세션 종료 시 DetachedInstanceError 방지
            _ = (
                position.id,
                position.stock_code,
                position.stock_name,
                position.buy_quantity,
                position.buy_price,
                position.current_price,
            )
            session.expunge(position)
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
            await self._cancel_inferior_sell_orders(session, position_id, sell_reason)
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

            unfilled_items, unfilled_ok = await self._fetch_unfilled_sells(force=True)

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
                    locked = self._open_sell_locked_qty(code, unfilled_items) if unfilled_ok else 0

                    if acct_qty <= 0:
                        # 미체결이 남아 있거나 조회 실패면 전량 확정 보류
                        if not unfilled_ok or locked > 0:
                            logger.info(
                                f"🛡️ [RECONCILE] 전량확정 보류 — {pos.stock_name} "
                                f"(잔고0, 미체결잠금={locked}, 조회ok={unfilled_ok})"
                            )
                            if pos.status != "HOLDING":
                                pos.status = "HOLDING"
                                pos.sell_time = None
                            continue
                        # 기본 매도 확정 처리
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

                        # 추가 방어: 계좌 잔고가 0인데도 기존 COMPLETED 매도 합계가
                        # 포지션의 매수 수량보다 작을 경우, 누락된 체결을 보정합니다.
                        try:
                            from utils.position_sell_backfill import _infer_sell_price
                            # 이미 확정된 COMPLETED 매도 합계
                            completed_rows = session.query(SellOrder).filter(
                                SellOrder.position_id == pos.id,
                                SellOrder.status == "COMPLETED",
                            ).all()
                            completed_total = sum(int(r.sell_quantity or 0) for r in completed_rows)
                            orig_qty = int(pos.buy_quantity or 0)
                            missing = orig_qty - completed_total
                            if missing > 0:
                                # 기존 COMPLETED 레코드의 수량을 보정(증가) — 새 레코드 추가 대신 업데이트
                                # 최신 COMPLETED 레코드를 선택하여 수량을 증가시킵니다.
                                latest_done = None
                                if completed_rows:
                                    # completed_rows may not be ordered; pick most recent by completed_at
                                    try:
                                        latest_done = sorted(
                                            completed_rows,
                                            key=lambda r: (r.completed_at or utc_now_naive()),
                                            reverse=True,
                                        )[0]
                                    except Exception:
                                        latest_done = completed_rows[-1]
                                if latest_done:
                                    price = int(latest_done.sell_price or _infer_sell_price(pos) or 0)
                                    old_qty = int(latest_done.sell_quantity or 0)
                                    new_qty = old_qty + missing
                                    latest_done.sell_quantity = new_qty
                                    latest_done.sell_amount = price * new_qty
                                    # 손익 재계산
                                    if pos.buy_price:
                                        latest_done.profit_loss = (price - pos.buy_price) * new_qty
                                        try:
                                            latest_done.profit_loss_rate = (price - pos.buy_price) / pos.buy_price * 100
                                        except Exception:
                                            latest_done.profit_loss_rate = None
                                    # 포지션 손익 갱신(추정)
                                    try:
                                        pos.current_profit_loss = int((pos.current_profit_loss or 0) + ((price - (pos.buy_price or 0)) * missing)) if pos.buy_price else pos.current_profit_loss
                                    except Exception:
                                        pass
                                    # 상세에 보정 이력 추가(최대 200자)
                                    note = (latest_done.sell_reason_detail or "") + " · 자동 보정: COMPLETED 레코드 수량 증가"
                                    latest_done.sell_reason_detail = note[:200]
                                    session.commit()
                                    logger.info(
                                        f"🛡️ [RECONCILE] 기존 COMPLETED 레코드 수량 보정 — {pos.stock_name} +{missing}주 (새 합계 {new_qty}주 @ {price})"
                                    )
                                    snap2 = sell_fill_snapshot(latest_done, pos)
                                    asyncio.create_task(notify_sell_filled_async(snap2, remaining_qty=None))
                                else:
                                    # 안전망: 만약 completed_rows가 비어있다면 기존 방식으로 새 레코드 생성
                                    price = int(_infer_sell_price(pos) or 0)
                                    backfill = SellOrder(
                                        position_id=pos.id,
                                        stock_code=pos.stock_code,
                                        stock_name=pos.stock_name,
                                        sell_price=price,
                                        sell_quantity=missing,
                                        sell_amount=price * missing,
                                        sell_reason=sell.sell_reason or "MANUAL",
                                        sell_reason_detail=(
                                            (sell.sell_reason_detail or "") + " · 자동 보정(대체): 계좌 잔고 0으로 누락분 생성"
                                        )[:200],
                                        profit_loss=(price - (pos.buy_price or 0)) * missing if pos.buy_price else None,
                                        profit_loss_rate=(
                                            ((price - (pos.buy_price or 0)) / pos.buy_price * 100) if pos.buy_price else None
                                        ),
                                        status="COMPLETED",
                                        created_at=utc_now_naive(),
                                        ordered_at=utc_now_naive(),
                                        completed_at=utc_now_naive(),
                                    )
                                    session.add(backfill)
                                    if backfill.profit_loss is not None:
                                        pos.current_profit_loss = int((pos.current_profit_loss or 0) + backfill.profit_loss)
                                    session.commit()
                                    logger.info(
                                        f"🛡️ [RECONCILE] 누락된 COMPLETED 보정(대체) 생성 — {pos.stock_name} {missing}주 @ {price}"
                                    )
                                    snap2 = sell_fill_snapshot(backfill, pos)
                                    asyncio.create_task(notify_sell_filled_async(snap2, remaining_qty=None))
                        except Exception as e:
                            logger.error(f"🛡️ [RECONCILE] COMPLETED 보정 실패: {e}")
                    elif acct_qty < pos.buy_quantity:
                        sold_qty = int(pos.buy_quantity) - int(acct_qty)
                        age_min = self._sell_order_age_minutes(sell)
                        # 키움 미체결이 남아 있으면 qty 감소만으로 부분체결 확정하지 않음
                        if not unfilled_ok or locked > 0:
                            logger.info(
                                f"🛡️ [RECONCILE] 부분체결 보류 — {pos.stock_name} "
                                f"차이 {sold_qty}주 · 미체결잠금 {locked}주 "
                                f"(조회ok={unfilled_ok})"
                            )
                            if int(pos.buy_quantity or 0) != acct_qty:
                                pos.buy_quantity = acct_qty
                            if pos.status != "HOLDING":
                                pos.status = "HOLDING"
                                pos.sell_time = None
                            continue
                        # 접수 직후 잔고 미반영·오차로 극소량만 줄어든 경우 부분체결로 확정하지 않음
                        # (에스씨디: 전량매도 접수 후 5주만 감소 → 5주 부분확정 → 잔량 재매도 800033)
                        min_wait_min = 0.75  # 45초
                        tiny = sold_qty < max(10, int((pos.buy_quantity or 0) * 0.02))
                        if age_min < min_wait_min and tiny:
                            logger.info(
                                f"🛡️ [RECONCILE] 부분체결 보류 — {pos.stock_name} "
                                f"차이 {sold_qty}주 · 경과 {age_min*60:.0f}초 (잔고 반영 대기)"
                            )
                            if pos.status != "HOLDING":
                                pos.status = "HOLDING"
                                pos.sell_time = None
                            continue
                        sell.sell_quantity = sold_qty
                        sell.sell_amount = int((sell.sell_price or pos.current_price or pos.buy_price) * sold_qty)
                        if sell.profit_loss is not None:
                            # 수량 정정 시 손익 재계산
                            sell.profit_loss = None
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
                    locked = self._open_sell_locked_qty(code, unfilled_items) if unfilled_ok else 0
                    target = None
                    if pending_for_code:
                        target = session.query(Position).filter(
                            Position.id == pending_for_code[-1].position_id,
                        ).first()
                    else:
                        # HOLDING 없고 계좌 잔량 있음 — 잘못된 MARKET_CLOSE 등 복구
                        candidates = [
                            p for p in session.query(Position).order_by(Position.id.desc()).all()
                            if KiwoomAPI.normalize_stock_code(p.stock_code) == code
                            and p.status != "HOLDING"
                        ]
                        target = candidates[0] if candidates else None

                    if not target or target.status == "HOLDING":
                        continue

                    target.status = "HOLDING"
                    target.sell_time = None
                    h = holdings_map.get(code)
                    if h:
                        apply_holding_to_position(target, h)
                    else:
                        target.buy_quantity = acct_qty
                    reason_note = (
                        "ORDERED 매도 미체결" if pending_for_code
                        else ("키움 미체결 잠금" if locked > 0 else "계좌 잔량 재확인")
                    )
                    log_activity(
                        "SELL",
                        f"매도 대기 포지션 복구 — {target.stock_name} {acct_qty}주 ({reason_note})",
                        "warn",
                        stock_code=target.stock_code,
                    )
                    logger.warning(
                        f"🛡️ [RECONCILE] 매도 대기 포지션 복구 — {target.stock_name} ({code}) "
                        f"{acct_qty}주 ({reason_note})"
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
        needed = _required_missing_confirms()
        for pos in session.query(Position).filter(Position.status == "HOLDING").all():
            code = KiwoomAPI.normalize_stock_code(pos.stock_code)
            if holdings.get(code, 0) > 0:
                self._account_missing_strikes.pop(pos.id, None)
                continue

            open_sells = session.query(SellOrder).filter(
                SellOrder.position_id == pos.id,
                SellOrder.status.in_(("PENDING", "ORDERED")),
            ).order_by(SellOrder.created_at.asc()).all()

            # 매수 직후 잔고 API 미반영 → 앱 매도 없이 MANUAL_SELL 오판 방지.
            # ORDERED 매도가 있으면 실제 청산 확정이므로 유예·연속확인을 건너뛴다.
            has_ordered_sell = any(s.status == "ORDERED" for s in open_sells)
            if not has_ordered_sell and _within_buy_settle_grace(pos):
                age = _buy_age_seconds(pos)
                grace = int(getattr(Config, "BUY_SETTLE_GRACE_SECONDS", 300) or 300)
                self._account_missing_strikes.pop(pos.id, None)
                logger.info(
                    f"🛡️ [RECONCILE] 매수 직후 유예 — {pos.stock_name} "
                    f"({age:.0f}s < {grace}s, 잔고 미반영 가능)"
                )
                continue

            if not has_ordered_sell:
                strikes = self._account_missing_strikes.get(pos.id, 0) + 1
                self._account_missing_strikes[pos.id] = strikes
                if strikes < needed:
                    logger.info(
                        f"🛡️ [RECONCILE] 계좌 미보유 확인 대기 — {pos.stock_name} "
                        f"({strikes}/{needed}회, 앱 매도 없음)"
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
                self._account_missing_strikes.pop(pos.id, None)
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
                detail = (
                    f"계좌 청산 확인 — {pos.stock_name} "
                    f"(앱 매도 기록 없음, 연속 {needed}회 미보유)"
                )
                from utils.position_sell_backfill import ensure_completed_sell_order
                ensure_completed_sell_order(
                    session,
                    pos,
                    sell_reason="MANUAL",
                    sell_reason_detail=(
                        f"계좌 미보유 동기화 — 키움 잔고 기준 청산 "
                        f"(연속 {needed}회 확인)"
                    ),
                    completed_at=pos.sell_time,
                )

            self._account_missing_strikes.pop(pos.id, None)
            log_activity("SELL", detail, "info", stock_code=pos.stock_code)
            logger.info(f"🛡️ [RECONCILE] {detail}")
            try:
                from utils.ma1592 import release_ma1592_ledger_if_flat
                release_ma1592_ledger_if_flat(session, pos.stock_code)
            except Exception:
                pass
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
        try:
            from utils.ma1592 import release_ma1592_ledger_if_flat
            release_ma1592_ledger_if_flat(session, pos.stock_code)
        except Exception:
            pass

    async def _execute_sell_order(
        self,
        position: Position,
        sell_price: int,
        sell_reason: str,
        sell_reason_detail: str,
        quantity: Optional[int] = None,
    ):
        """매도 주문 실행 (체결 확정은 _reconcile_sell_orders_and_holdings에서 처리)."""
        try:
            cooldown_until = self._sell_qty_shortage_cooldown.get(position.id, 0.0)
            now_mono = time.monotonic()
            if cooldown_until > now_mono:
                logger.info(
                    f"🛡️ [STOP_LOSS] 매도 주문 생략 — {position.stock_name}: "
                    f"매도가능수량 부족 재확인 대기 ({cooldown_until - now_mono:.0f}초)"
                )
                return
            self._sell_qty_shortage_cooldown.pop(position.id, None)

            if await self._has_any_pending_sell_order(position.id):
                logger.info(
                    f"🛡️ [STOP_LOSS] 매도 주문 생략 — {position.stock_name}: "
                    f"미체결/대기 매도 있음"
                )
                log_activity(
                    "SELL",
                    f"매도 생략 — {position.stock_name} ({sell_reason}): 동일/상위 주문 대기 중",
                    "info",
                    stock_code=position.stock_code,
                    reason=sell_reason,
                )
                return

            qty = int(quantity) if quantity and int(quantity) > 0 else int(position.buy_quantity or 0)
            if qty <= 0:
                logger.warning(f"🛡️ [STOP_LOSS] 매도 수량 0 — {position.stock_name}")
                return
            if qty > int(position.buy_quantity or 0):
                qty = int(position.buy_quantity or 0)

            # 매도 직전에는 캐시를 쓰지 않는다. 부분체결 직후의 15초 캐시가
            # 예전 보유수량으로 과다 주문을 만드는 것을 방지한다.
            account_number = (
                Config.KIWOOM_MOCK_ACCOUNT_NUMBER
                if Config.KIWOOM_USE_MOCK_ACCOUNT
                else Config.KIWOOM_ACCOUNT_NUMBER
            )
            balance = await self.kiwoom_api.get_account_balance(
                account_number, force_refresh=True,
            )
            acct_qty = None
            sellable_field = None
            if balance and not balance.get("_error") and not balance.get("_stale"):
                qty_map = self._holdings_qty_map(balance)
                sell_map = self._holdings_sellable_map(balance)
                code = KiwoomAPI.normalize_stock_code(position.stock_code or "")
                acct_qty = int(qty_map.get(code, 0) or 0)
                sellable_field = sell_map.get(code)

            unfilled_items, unfilled_ok = await self._fetch_unfilled_sells(force=True)
            locked = self._open_sell_locked_qty(position.stock_code, unfilled_items) if unfilled_ok else 0
            if unfilled_ok and locked > 0:
                logger.info(
                    f"🛡️ [STOP_LOSS] 매도 주문 생략 — {position.stock_name}: "
                    f"키움 미체결 매도 {locked}주"
                )
                log_activity(
                    "SELL",
                    f"매도 생략 — {position.stock_name}: 미체결 매도 {locked}주 (잠금)",
                    "warn",
                    stock_code=position.stock_code,
                    reason=sell_reason,
                )
                return

            if acct_qty is not None:
                if acct_qty <= 0:
                    logger.warning(
                        f"🛡️ [STOP_LOSS] 계좌 잔량 0 — 매도 생략 {position.stock_name} "
                        f"(DB {position.buy_quantity}주)"
                    )
                    log_activity(
                        "SELL",
                        f"매도 생략 — {position.stock_name}: 계좌 잔량 0 (이미 청산)",
                        "warn",
                        stock_code=position.stock_code,
                        reason=sell_reason,
                    )
                    return
                sellable = effective_sellable_qty(
                    acct_qty,
                    sellable_field if unfilled_ok or sellable_field is not None else None,
                    locked,
                )
                if sellable <= 0:
                    logger.info(
                        f"🛡️ [STOP_LOSS] 매도 주문 생략 — {position.stock_name}: "
                        f"매도가능 0 (보유 {acct_qty}주, 잠금 {locked}주)"
                    )
                    log_activity(
                        "SELL",
                        f"매도 생략 — {position.stock_name}: 매도가능수량 0 (미체결 잠금)",
                        "warn",
                        stock_code=position.stock_code,
                        reason=sell_reason,
                    )
                    return
                if qty > sellable:
                    logger.info(
                        f"🛡️ [STOP_LOSS] 매도수량 조정 {qty}→{sellable}주 "
                        f"— {position.stock_name} (매도가능)"
                    )
                    qty = sellable
                if int(position.buy_quantity or 0) != acct_qty:
                    position.buy_quantity = acct_qty

            logger.info(f"🛡️ [STOP_LOSS] 매도 주문 실행 - {position.stock_name}: {sell_reason} {qty}주")
            
            # 매도 주문 생성
            sell_order_id = await self._create_sell_order(
                position, sell_price, sell_reason, sell_reason_detail, quantity=qty,
            )
            if not sell_order_id:
                return
            
            # KRX 정규장: 시장가. NXT 연장(08:00전·15:30후): NXT는 시장가 미지원 → SOR+지정가
            if is_krx_session():
                stex_tp = "KRX"
                order_price = 0
                order_type = "3"
                order_label = "시장가"
            else:
                stex_tp = "SOR"
                order_price = int(sell_price or 0)
                order_type = "0"
                order_label = f"SOR지정가@{order_price:,}"
                if order_price <= 0:
                    logger.warning(
                        f"🛡️ [STOP_LOSS] NXT/연장 매도 단가 없음 — 생략 {position.stock_name}"
                    )
                    await self._update_sell_order_status(sell_order_id, "FAILED", "")
                    return

            # 키움 API로 매도 주문
            result = await self.kiwoom_api.place_sell_order(
                stock_code=position.stock_code,
                quantity=qty,
                price=order_price,
                order_type=order_type,
                dmst_stex_tp=stex_tp,
            )
            
            if result.get("success"):
                msg = (
                    f"매도 주문 {sell_reason} — {position.stock_name} {qty}주 "
                    f"@ {sell_price:,}원 ({order_label})"
                )
                logger.info(f"🛡️ [STOP_LOSS] 매도 주문 성공 - {position.stock_name}: {qty}주 ({order_label})")
                log_activity("SELL", msg, "info", stock_code=position.stock_code, reason=sell_reason)
                
                # 매도 주문 접수 — 포지션 청산은 계좌 체결 확인 후 reconcile에서 처리
                await self._update_sell_order_status(sell_order_id, "ORDERED", result.get("order_id", ""))
                if sell_reason == "STOP_LOSS" and getattr(position, "strategy_key", None) == "ymgp":
                    try:
                        from utils.ymgp_engine import mark_stopped
                        mark_stopped(position.stock_code, self.auto_trade_settings)
                    except Exception:
                        pass
                
            else:
                error_msg = result.get("error", "알 수 없는 오류")
                # 이미 체결된 뒤 중복 매도 → 800033. FAILED로 두면 매 사이클 재시도 스팸.
                if is_sell_qty_shortage_error(error_msg):
                    recovered = await self._recover_sell_after_qty_shortage(
                        position, sell_order_id, sell_reason, error_msg,
                        requested_qty=qty, sell_price=sell_price,
                    )
                    if recovered:
                        return
                logger.error(f"🛡️ [STOP_LOSS] 매도 주문 실패 - {position.stock_name}: {error_msg}")
                log_activity("SELL", f"매도 실패 {position.stock_name}: {error_msg}", "warn", stock_code=position.stock_code, reason=sell_reason)
                await self._update_sell_order_status(sell_order_id, "FAILED", error_msg)
                
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 매도 주문 실행 오류 - {position.stock_name}: {e}")

    async def _fetch_account_qty(
        self, stock_code: str, *, force_refresh: bool = False,
    ) -> Optional[int]:
        """계좌 보유 수량. 조회 실패 시 None."""
        try:
            account_number = (
                Config.KIWOOM_MOCK_ACCOUNT_NUMBER
                if Config.KIWOOM_USE_MOCK_ACCOUNT
                else Config.KIWOOM_ACCOUNT_NUMBER
            )
            balance = await self.kiwoom_api.get_account_balance(
                account_number, force_refresh=force_refresh,
            )
            # 실제 조회 실패 후 반환된 stale 잔고는 주문 수량 판단에 사용하지 않는다.
            if (
                not balance
                or balance.get("_error")
                or (force_refresh and balance.get("_stale"))
            ):
                return None
            code = KiwoomAPI.normalize_stock_code(stock_code or "")
            return int(self._holdings_qty_map(balance).get(code, 0) or 0)
        except Exception as e:
            logger.debug(f"🛡️ [STOP_LOSS] 계좌 수량 조회 실패: {e}")
            return None

    async def _ymgp_daily_bars(self, stock_code: str):
        """역매공파용 일봉 캐시 (프로세스 메모리, 코드당 1회/세션)."""
        if not hasattr(self, "_ymgp_bars_cache"):
            self._ymgp_bars_cache = {}
        code = str(stock_code or "").replace("A", "")
        cached = self._ymgp_bars_cache.get(code)
        if cached is not None:
            return cached
        try:
            bars = await self.kiwoom_api.get_stock_chart_data(code, "1D", max_bars=520)
            self._ymgp_bars_cache[code] = bars or []
            return bars or []
        except Exception as e:
            logger.debug(f"🛡️ [STOP_LOSS] ymgp 일봉 조회 실패 {code}: {e}")
            return []

    async def _monitor_ma1592_exit(
        self,
        position: Position,
        current_price: int,
        peak: int,
        buy_price: int,
        profit_loss,
        profit_loss_rate,
        settings,
    ) -> None:
        """MA1592: 전고 반익절 + impulse 분기 청산. 글로벌 트레일 미사용(H9)."""
        from utils.ma1592 import (
            chart_tf_interval_minutes,
            compute_bar_ma,
            evaluate_exit,
            normalize_chart_tf,
            params_from_settings,
        )
        from utils.ema_fractal import drop_forming_minute_bar
        from utils.sell_reason_labels import classify_exit_reason

        p = params_from_settings(settings)
        tp1_filled = int(getattr(position, "ymgp_tp_stage", None) or 0) >= 1
        # trailing_armed 재사용: impulse_seen
        impulse_seen = bool(getattr(position, "trailing_armed", False)) or tp1_filled
        tp1_px = int(getattr(position, "take_profit_price", None) or 0)
        entry = int(buy_price or 0)
        state = "MANAGE_HALF" if tp1_filled else "MANAGE_FULL"

        hold_days = 0
        try:
            bt = getattr(position, "buy_time", None)
            if bt:
                hold_days = max(0, (as_kst().date() - as_kst(bt).date()).days)
        except Exception:
            hold_days = 0

        ma15 = 0.0
        ma92 = 0.0
        bar_open_3m = 0.0
        bar_close_3m = 0.0
        entry_leg = 1
        exec_tf = normalize_chart_tf(p.get("exec_tf") or "3M")
        interval_min = chart_tf_interval_minutes(exec_tf)
        try:
            from utils.ma1592 import get_universe_store

            row = get_universe_store().get(
                KiwoomAPI.normalize_stock_code(position.stock_code or "")
            )
            if row:
                entry_leg = max(1, int(row.entry_leg or 1))
        except Exception:
            entry_leg = 1
        try:
            raw = await self.kiwoom_api.get_stock_chart_data(
                position.stock_code, exec_tf, max_bars=150, cache_ttl_sec=60,
            )
            bars = drop_forming_minute_bar(raw or [], interval_minutes=interval_min)
            if bars:
                latest_3m = bars[-1]
                bar_open_3m = float(latest_3m.get("open") or 0)
                bar_close_3m = float(latest_3m.get("close") or 0)
            closes = []
            for b in bars:
                try:
                    c = float(b.get("close") or 0)
                except (TypeError, ValueError):
                    c = 0
                if c > 0:
                    closes.append(c)
            f, s, _, _ = compute_bar_ma(
                closes,
                fast=int(p.get("ma_fast") or 15),
                slow=int(p.get("ma_slow") or 92),
                ma_type=str(p.get("ma_type") or "ema"),
            )
            if f is not None:
                ma15 = float(f)
            if s is not None:
                ma92 = float(s)
        except Exception as e:
            logger.debug(f"🛡️ [STOP_LOSS] MA1592 EMA 조회 실패: {e}")

        peak_i = max(int(peak or 0), int(current_price or 0), entry)
        bars_since_peak = 0
        if peak_i > int(current_price or 0):
            bars_since_peak = 1  # 폴링 단위 근사; 정밀은 P2 5분 리플레이

        session_end = False
        if p.get("flatten_eod"):
            kst = as_kst()
            session_end = (kst.hour, kst.minute) >= (15, 20)

        ex = evaluate_exit(
            state=state,
            entry=entry,
            last=float(current_price),
            close=float(current_price),
            open_=float(current_price),
            high=float(current_price),
            ma15=ma15,
            ma92=ma92,
            tp1_price_val=tp1_px,
            tp1_filled=tp1_filled,
            impulse_seen=impulse_seen,
            peak=peak_i,
            bars_since_peak=bars_since_peak,
            hold_days=hold_days,
            session_end=session_end,
            entry_leg=entry_leg,
            params=p,
            bar_open_3m=bar_open_3m,
            bar_close_3m=bar_close_3m,
        )
        await self._update_position_tracking(position.id, peak_i, None)
        if ex and ex.get("impulse_seen") and not impulse_seen:
            # impulse sticky → trailing_armed
            try:
                for db in get_db():
                    pos = db.query(Position).filter(Position.id == position.id).first()
                    if pos:
                        pos.trailing_armed = True
                        db.commit()
                    break
            except Exception:
                pass

        if not ex:
            return

        reason = str(ex.get("reason") or "STOP_LOSS")
        qty_frac = float(ex.get("qty_frac") or 1.0)
        qty = int(position.buy_quantity or 0)
        sell_n = qty
        if qty_frac < 1.0 and qty >= 2:
            sell_n = max(1, int(qty * qty_frac))
            sell_n = min(sell_n, qty - 1)

        detail = f"MA1592 {reason} · frac={qty_frac} · {sell_n}/{qty}주"
        mapped = reason
        if reason.startswith("TP1"):
            mapped = "TAKE_PROFIT"
        elif reason in ("STOP_MA_DC_WIDEN", "STOP_MA_DC_CRASH", "STOP_MA_CRASH", "STOP_PCT", "STOP_3M_BEARISH_BELOW_MA15"):
            mapped = "STOP_LOSS"
        elif reason in ("MAX_HOLD", "EOD"):
            mapped = "MARKET_CLOSE"

        classified = classify_exit_reason(
            mapped, profit_loss=profit_loss, profit_loss_rate=profit_loss_rate,
        )
        if classified != mapped:
            detail = f"{reason}→{classified} | {detail}"
            mapped = classified
        else:
            detail = f"{reason} | {detail}"

        if await self._has_any_pending_sell_order(position.id):
            await self._prepare_sell(position.id, mapped)
        if await self._has_pending_sell_order(position.id, for_reason=mapped):
            return

        if qty_frac < 1.0 and sell_n < qty:
            await self._bump_ymgp_tp_stage(position.id, 1)
            try:
                for db in get_db():
                    pos = db.query(Position).filter(Position.id == position.id).first()
                    if pos:
                        pos.trailing_armed = True
                        db.commit()
                    break
            except Exception:
                pass
            try:
                from utils.ma1592 import get_universe_store
                get_universe_store().set_state(
                    KiwoomAPI.normalize_stock_code(position.stock_code or ""),
                    "MANAGE_HALF",
                )
            except Exception:
                pass
        else:
            try:
                from utils.ma1592 import get_universe_store
                get_universe_store().set_state(
                    KiwoomAPI.normalize_stock_code(position.stock_code or ""),
                    "DONE",
                )
            except Exception:
                pass

        await self._execute_sell_order(
            position, current_price, mapped, detail, quantity=sell_n,
        )

    async def _bump_ymgp_tp_stage(self, position_id: int, stage: int) -> None:
        try:
            for db in get_db():
                session: Session = db
                pos = session.query(Position).filter(Position.id == position_id).first()
                if pos:
                    pos.ymgp_tp_stage = int(stage)
                    session.commit()
                break
        except Exception as e:
            logger.debug(f"🛡️ [STOP_LOSS] ymgp_tp_stage 갱신 실패: {e}")

    async def _recover_sell_after_qty_shortage(
        self,
        position: Position,
        sell_order_id: int,
        sell_reason: str,
        error_msg: str,
        *,
        requested_qty: Optional[int] = None,
        sell_price: Optional[int] = None,
    ) -> bool:
        """800033 등 매도가능수량 부족 — 잔고 0이면 청산 확정, 잔량 있으면 잔량으로 1회 재주문."""
        try:
            self._sell_qty_shortage_cooldown[position.id] = (
                time.monotonic() + self._sell_qty_shortage_cooldown_sec
            )
            account_number = (
                Config.KIWOOM_MOCK_ACCOUNT_NUMBER
                if Config.KIWOOM_USE_MOCK_ACCOUNT
                else Config.KIWOOM_ACCOUNT_NUMBER
            )
            balance = await self.kiwoom_api.get_account_balance(
                account_number, force_refresh=True,
            )
            # 800033 직후 실제 조회가 실패하면 stale 잔고로 재주문하지 않는다.
            if not balance or balance.get("_error") or balance.get("_stale"):
                await self._update_sell_order_status(sell_order_id, "FAILED", error_msg)
                return True
            holdings = self._holdings_qty_map(balance)
            sellable_map = self._holdings_sellable_map(balance)
            code = KiwoomAPI.normalize_stock_code(position.stock_code or "")
            acct_qty = int(holdings.get(code, 0) or 0)
            sellable_field = sellable_map.get(code)
            unfilled_items, unfilled_ok = await self._fetch_unfilled_sells(force=True)
            locked = self._open_sell_locked_qty(code, unfilled_items) if unfilled_ok else 0
            sellable = effective_sellable_qty(acct_qty, sellable_field, locked)

            if acct_qty > 0:
                req = int(requested_qty or 0) or int(position.buy_quantity or 0)
                if (unfilled_ok and locked > 0) or sellable <= 0:
                    logger.warning(
                        f"🛡️ [STOP_LOSS] 매도가능수량 부족 — 재주문 생략 "
                        f"{position.stock_name} (보유 {acct_qty}·가능 {sellable}·잠금 {locked})"
                    )
                    try:
                        for db in get_db():
                            session: Session = db
                            pos = session.query(Position).filter(Position.id == position.id).first()
                            if pos and int(pos.buy_quantity or 0) != acct_qty:
                                pos.buy_quantity = acct_qty
                            session.commit()
                            break
                    except Exception as e:
                        logger.debug(f"🛡️ [STOP_LOSS] 800033 수량 동기화 실패: {e}")
                    log_activity(
                        "SELL",
                        f"매도 실패 {position.stock_name}: {error_msg} "
                        f"(보유 {acct_qty}·매도가능 {sellable}·미체결잠금 {locked})",
                        "warn",
                        stock_code=position.stock_code,
                        reason=sell_reason,
                    )
                    await self._update_sell_order_status(sell_order_id, "FAILED", error_msg)
                    position.buy_quantity = acct_qty
                    return True

                retry_qty = min(sellable, req) if req > 0 else sellable
                try:
                    for db in get_db():
                        session: Session = db
                        pos = session.query(Position).filter(Position.id == position.id).first()
                        sell = session.query(SellOrder).filter(SellOrder.id == sell_order_id).first()
                        if pos and int(pos.buy_quantity or 0) != acct_qty:
                            pos.buy_quantity = acct_qty
                        if sell and retry_qty > 0:
                            sell.sell_quantity = retry_qty
                            px = int(sell_price or sell.sell_price or pos.current_price or pos.buy_price or 0)
                            sell.sell_amount = px * retry_qty
                        session.commit()
                        break
                except Exception as e:
                    logger.debug(f"🛡️ [STOP_LOSS] 800033 수량 동기화 실패: {e}")

                if retry_qty > 0 and retry_qty < req:
                    logger.warning(
                        f"🛡️ [STOP_LOSS] 매도가능수량 부족 → 매도가능 {retry_qty}주로 재주문 "
                        f"— {position.stock_name} (요청 {req}주)"
                    )
                    if is_krx_session():
                        retry_price, retry_type, retry_stex = 0, "3", "KRX"
                    else:
                        retry_price = int(sell_price or 0)
                        retry_type, retry_stex = "0", "SOR"
                        if retry_price <= 0:
                            await self._update_sell_order_status(
                                sell_order_id, "FAILED", error_msg,
                            )
                            return True
                    result = await self.kiwoom_api.place_sell_order(
                        stock_code=position.stock_code,
                        quantity=retry_qty,
                        price=retry_price,
                        order_type=retry_type,
                        dmst_stex_tp=retry_stex,
                    )
                    if result.get("success"):
                        await self._update_sell_order_status(
                            sell_order_id, "ORDERED", result.get("order_id", ""),
                        )
                        log_activity(
                            "SELL",
                            f"매도 재주문(잔량) — {position.stock_name} {retry_qty}주",
                            "info",
                            stock_code=position.stock_code,
                            reason=sell_reason,
                        )
                        position.buy_quantity = acct_qty
                        return True

                logger.warning(
                    f"🛡️ [STOP_LOSS] 매도가능수량 부족이지만 계좌 잔량 {acct_qty}주 "
                    f"— {position.stock_name} (DB {position.buy_quantity}주)"
                )
                log_activity(
                    "SELL",
                    f"매도 실패 {position.stock_name}: {error_msg} (계좌 잔량 {acct_qty}주)",
                    "warn",
                    stock_code=position.stock_code,
                    reason=sell_reason,
                )
                await self._update_sell_order_status(sell_order_id, "FAILED", error_msg)
                position.buy_quantity = acct_qty
                return True

            if unfilled_ok and locked > 0:
                logger.warning(
                    f"🛡️ [STOP_LOSS] 잔고0이지만 미체결 매도 {locked}주 — 청산 보류 "
                    f"{position.stock_name}"
                )
                await self._update_sell_order_status(sell_order_id, "FAILED", error_msg)
                return True
            if not unfilled_ok:
                logger.warning(
                    f"🛡️ [STOP_LOSS] 잔고0·미체결 미확인 — 청산 보류 {position.stock_name}"
                )
                await self._update_sell_order_status(sell_order_id, "FAILED", error_msg)
                return True

            for db in get_db():
                session: Session = db
                sell = session.query(SellOrder).filter(SellOrder.id == sell_order_id).first()
                pos = session.query(Position).filter(Position.id == position.id).first()
                if not sell or not pos:
                    break
                note = f"이미 청산됨(잔고0) · {error_msg}"
                if sell.sell_reason_detail:
                    sell.sell_reason_detail = f"{sell.sell_reason_detail} · {note}"[:200]
                else:
                    sell.sell_reason_detail = note[:200]
                self._finalize_sell_in_session(session, sell, pos)
                session.commit()
                self._sangtta_soft_counters.pop(pos.id, None)
                self._breakout_soft_counters.pop(pos.id, None)
                self._legacy_ema_state.pop(pos.id, None)
                self._account_missing_strikes.pop(pos.id, None)
                log_activity(
                    "SELL",
                    f"매도 확정(중복주문·잔고0) — {pos.stock_name} {sell.sell_quantity}주 ({sell_reason})",
                    "info",
                    stock_code=pos.stock_code,
                    reason=sell_reason,
                )
                logger.info(
                    f"🛡️ [STOP_LOSS] 800033 복구 — 이미 청산으로 확정: {pos.stock_name}"
                )
                snap = sell_fill_snapshot(sell, pos)
                asyncio.create_task(notify_sell_filled_async(snap, remaining_qty=None))
                break
            return True
        except Exception as e:
            logger.error(f"🛡️ [STOP_LOSS] 매도가능수량 부족 복구 오류: {e}")
            return False

    async def _create_sell_order(
        self,
        position: Position,
        sell_price: int,
        sell_reason: str,
        sell_reason_detail: str,
        quantity: Optional[int] = None,
    ) -> Optional[int]:
        """매도 주문 생성 — DB id 반환 (세션 분리 안전)."""
        try:
            qty = int(quantity) if quantity and int(quantity) > 0 else int(position.buy_quantity or 0)
            sell_order_id = None
            for db in get_db():
                session: Session = db
                sell_order = SellOrder(
                    position_id=position.id,
                    stock_code=position.stock_code,
                    stock_name=position.stock_name,
                    sell_price=sell_price,
                    sell_quantity=qty,
                    sell_amount=sell_price * qty,
                    sell_reason=sell_reason,
                    sell_reason_detail=sell_reason_detail,
                    profit_loss=(sell_price - position.buy_price) * qty if position.buy_price else 0,
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
            signal_meta = (
                signal.additional_data
                if isinstance(getattr(signal, "additional_data", None), dict)
                else {}
            )
            strategy_key = str(signal_meta.get("strategy") or "").strip().lower() or None
            strategy_stop_loss_rate = self.strategy_stop_loss_rate(s, strategy_key)
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
                    if (signal_meta.get("strategy") or "").strip().lower() == "ymgp":
                        existing.strategy_key = "ymgp"
                        leg = int(signal_meta.get("entry_leg") or signal_meta.get("ymgp_entry_leg") or 2)
                        existing.ymgp_entry_leg = max(int(getattr(existing, "ymgp_entry_leg", None) or 1), leg)
                        for attr, key in (
                            ("ymgp_ref_high", "ymgp_ref_high"),
                            ("ymgp_ref_low", "ymgp_ref_low"),
                            ("ymgp_ref_open", "ymgp_ref_open"),
                        ):
                            val = signal_meta.get(key)
                            if val and not getattr(existing, attr, None):
                                try:
                                    setattr(existing, attr, int(val))
                                except (TypeError, ValueError):
                                    pass
                    session.commit()
                    session.refresh(existing)
                    logger.info(
                        f"🛡️ [STOP_LOSS] 기존 HOLDING에 매수 반영 — {signal.stock_name}: "
                        f"+{buy_quantity}주 @ {buy_price:,}원 (포지션 #{existing.id})"
                    )
                    return existing

                ref = signal_meta.get("ymgp_ref") if isinstance(signal_meta.get("ymgp_ref"), dict) else {}
                position = Position(
                    stock_code=code,
                    stock_name=signal.stock_name,
                    buy_price=buy_price,
                    buy_quantity=buy_quantity,
                    order_quantity=buy_quantity,
                    buy_amount=buy_price * buy_quantity,
                    buy_order_id=buy_order_id,
                    stop_loss_rate=(
                        strategy_stop_loss_rate
                        if strategy_stop_loss_rate is not None
                        else 5.0
                    ),
                    take_profit_rate=s.take_profit_rate if s else 10.0,
                    condition_id=signal.condition_id,
                    signal_id=signal.id,
                    status="HOLDING",
                    # 전략 키를 신호의 additional_data.strategy에서 복사 (예: "sangtta")
                    strategy_key=strategy_key,
                    breakout_level_kind=signal_meta.get("level_kind"),
                    breakout_level_price=(
                        int(signal_meta.get("breakout_level_price") or signal_meta.get("level_price") or 0)
                        or None
                    ),
                    ymgp_ref_high=(
                        int(signal_meta.get("ymgp_ref_high") or ref.get("high") or 0) or None
                    ),
                    ymgp_ref_low=(
                        int(signal_meta.get("ymgp_ref_low") or ref.get("low") or 0) or None
                    ),
                    ymgp_ref_open=(
                        int(signal_meta.get("ymgp_ref_open") or ref.get("open") or 0) or None
                    ),
                    ymgp_entry_leg=int(signal_meta.get("entry_leg") or signal_meta.get("ymgp_entry_leg") or 1),
                    ymgp_tp_stage=0,
                    peak_price=buy_price,
                    buy_atr=buy_atr,
                    buy_atr_period=buy_atr_period,
                )
                if str(signal_meta.get("strategy") or "").strip().lower() == "fractal":
                    stop_px = int(signal_meta.get("stop_price") or 0) or None
                    tp_px = int(signal_meta.get("take_profit_price") or 0) or None
                    position.stop_loss_price = stop_px
                    position.take_profit_price = tp_px
                    if stop_px and buy_price:
                        position.stop_loss_rate = round(
                            abs(buy_price - stop_px) / buy_price * 100.0, 4
                        )
                    if tp_px and buy_price:
                        position.take_profit_rate = round(
                            abs(tp_px - buy_price) / buy_price * 100.0, 4
                        )
                if str(signal_meta.get("strategy") or "").strip().lower() == "ma1592":
                    stop_px = int(signal_meta.get("stop_price") or signal_meta.get("suggested_stop") or 0) or None
                    tp_px = int(
                        signal_meta.get("tp1_price")
                        or signal_meta.get("take_profit_price")
                        or 0
                    ) or None
                    prev_h = int(signal_meta.get("prev_high") or 0) or None
                    position.stop_loss_price = stop_px
                    position.take_profit_price = tp_px
                    position.breakout_level_price = prev_h
                    position.ymgp_tp_stage = 0
                    position.trailing_armed = False
                    if stop_px and buy_price:
                        position.stop_loss_rate = round(
                            abs(buy_price - stop_px) / buy_price * 100.0, 4
                        )
                    if tp_px and buy_price:
                        position.take_profit_rate = round(
                            abs(tp_px - buy_price) / buy_price * 100.0, 4
                        )
                    try:
                        from utils.ma1592 import get_universe_store
                        from utils.datetime_kst import now_kst
                        leg = int(
                            signal_meta.get("entry_leg")
                            or signal_meta.get("ma1592_entry_leg")
                            or 1
                        )
                        planned = int(signal_meta.get("planned_qty") or 0)
                        fields = {
                            "entry_price": int(buy_price or 0),
                            "tp1_price": int(tp_px or 0),
                            "prev_high": int(prev_h or 0),
                            "entry_leg": max(leg, 1),
                        }
                        if planned > 0:
                            fields["planned_qty"] = planned
                        if leg >= 2:
                            fields["leg2_at"] = now_kst().isoformat(timespec="seconds")
                        get_universe_store().set_state(
                            code,
                            "MANAGE_FULL",
                            **fields,
                        )
                    except Exception as e:
                        logger.debug(f"🛡️ [STOP_LOSS] MA1592 장부 MANAGE 전환 실패: {e}")
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
            buy_window = (
                linked_trading_session_window_str(settings) if settings else None
            )
            sl_window = stop_loss_monitoring_window_str()
            status = {
                "is_running": self.is_running,
                "monitoring_active": self.is_monitoring_active(),
                "monitoring_loop_alive": self.monitoring_task_running(),
                "monitoring_interval": self.monitoring_interval,
                "linked_session_window": buy_window,
                "session_window": sl_window,
                "stop_loss_session_window": sl_window,
                "trade_start_time": settings.trade_start_time if settings else None,
                "trade_end_time": settings.trade_end_time if settings else None,
                "in_linked_session": in_linked_trading_session(settings),
                "in_stop_loss_session": is_stop_loss_monitoring_session(settings),
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