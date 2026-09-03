"""대시보드 설정 기반 자동매매 스캐너 (KIS 스타일).

관심종목 + 스크리너(거래량/대금 상위) 후보를 주기적으로 점검하고,
매수 조건을 만족하면 PendingBuySignal을 생성한다.
조건식(CNSRREQ) 주기 검색과는 별개이며, 자동매매 ON일 때만 동작한다.
"""
import asyncio
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, time as dt_time, timedelta
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from api.kiwoom_api import KiwoomAPI
from core.config import Config
from core.models import AutoTradeSettings, PendingBuySignal, Position, PositionBuyFill, get_db
from managers.signal_manager import SignalType, signal_manager
from utils.auto_trade_engine import (
    auto_trade_engines_allowed,
    buy_price_skip_reason,
    check_daily_limits,
    check_entry_gate,
    disable_auto_trade,
    effective_min_change_rate,
    evaluate_gate_pack,
    get_auto_trade_settings_sync,
    has_buy_conditions,
    new_buy_block_reason,
    passes_buy_price_conditions,
)
from utils.auto_trade_activity_log import log_activity
from utils.market_hours import linked_trading_session_window_str
from utils.datetime_kst import kst_day_start_utc_naive, now_kst, utc_now_naive

logger = logging.getLogger(__name__)

AUTO_TRADE_CONDITION_ID = 99999  # 자동매매 스캐너 전용 condition_id


def effective_legacy_scan_limit(
    total_limit: int,
    screener_cap: int,
    reserved_count: int,
) -> int:
    """1회 스캔 총한도에서 비레거시 편입 수를 뺀 뒤 레거시(거래대금 상위) 자리.

    reserved_count: 관심·상따·돌파·프랙탈 등 이미 편입된 종목 수.
    """
    total = max(1, int(total_limit or 60))
    cap = max(0, int(screener_cap or 0))
    reserved = max(0, int(reserved_count or 0))
    return max(0, min(cap, total - reserved))


def compute_scan_throttle_sec(
    *,
    use_entry_gate: bool,
    remaining_calls: Optional[int] = None,
    seconds_until_available: float = 0.0,
    base_pause_sec: Optional[float] = None,
    min_call_interval: Optional[float] = None,
) -> float:
    """게이트/신호 평가 후 종목 간 대기초. API 여유면 짧게, 타이트하면 base.

    실제 키움 호출 간격은 rate limiter가 따로 지키므로, 여기 대기는
    손절·매수와 몫을 나누기 위한 여유다. 잔여 호출이 충분하면 거의 쉼 없이 진행.
    """
    base = float(
        base_pause_sec
        if base_pause_sec is not None
        else (getattr(Config, "SCAN_GATE_PAUSE_SEC", None) or 3.0)
    )
    if not use_entry_gate:
        base = max(0.5, base * 0.5)
    min_iv = float(
        min_call_interval
        if min_call_interval is not None
        else (getattr(Config, "API_MIN_CALL_INTERVAL", None) or 3.0)
    )
    until = max(0.0, float(seconds_until_available or 0.0))
    if until > 0.05:
        # 다음 호출이 막혀 있으면 그 시간만 맞추고 고정 base를 겹치지 않음
        return round(min(max(until, 0.3), max(base, min_iv)), 2)

    rem = remaining_calls
    if rem is None:
        return round(base, 2)
    rem = int(rem)
    if rem >= 5:
        # 분당 여유 충분 — 호출 간격의 절반 정도만 (손절 몫)
        return round(max(0.4, min_iv * 0.4), 2)
    if rem >= 3:
        return round(max(0.8, min(base, min_iv * 0.7)), 2)
    if rem >= 1:
        return round(max(1.5, min(base, min_iv)), 2)
    return round(base, 2)


def _scan_throttle_from_limiter(settings: AutoTradeSettings) -> float:
    """현재 키움 rate limiter 상태로 스캔 대기초 산출."""
    rem = None
    until = 0.0
    min_iv = None
    try:
        from api.api_rate_limiter import api_rate_limiter

        info = api_rate_limiter.get_status_info() or {}
        rem = info.get("remaining_calls")
        until = float(info.get("seconds_until_available") or 0)
        min_iv = info.get("min_call_interval")
    except Exception:
        pass
    return compute_scan_throttle_sec(
        use_entry_gate=bool(getattr(settings, "use_entry_gate", False)),
        remaining_calls=None if rem is None else int(rem),
        seconds_until_available=until,
        min_call_interval=None if min_iv is None else float(min_iv),
    )

_SCAN_STAT_LABELS = {
    "holding": "보유·대기",
    "cooldown": "쿨다운",
    "no_price": "시세없음",
    "price_cond": "등락미달",
    "gate": "게이트",
    "signal_ok": "신호생성",
    "signal_fail": "신호실패",
    "skipped": "미검사",
}

_STRATEGY_SUMMARY_ORDER = ("legacy", "sangtta", "breakout", "fractal", "jongga", "ma1592")
_STRATEGY_SUMMARY_LABELS = {
    "legacy": "거래대금 눌림목",
    "sangtta": "상따",
    "breakout": "수급 돌파",
    "fractal": "프랙탈 스캘핑",
    "jongga": "종가배팅",
    "ma1592": "15/92 홀드",
}

# API 호출·게이트 검사가 있었던 경로만 스캔 간 대기 (등락미달 등은 즉시 스킵)
_THROTTLE_REASONS = frozenset({"gate", "signal_ok", "signal_fail", "no_price"})
_DISPARITY_LOG_CONCURRENCY = 4


def _target_strategy_key(item: Dict) -> str:
    """스캔 후보 source → 전략 프로필 키."""
    src = str(item.get("source") or "scanner")
    if src == "sangtta":
        return "sangtta"
    if src == "breakout":
        return "breakout"
    if src == "jongga":
        return "jongga"
    if src == "fractal":
        return "fractal"
    if src == "ma1592":
        return "ma1592"
    return "legacy"


def _count_targets_by_strategy(targets: List[Dict]) -> Dict[str, int]:
    out: Dict[str, int] = {k: 0 for k in _STRATEGY_SUMMARY_ORDER}
    for item in targets or []:
        key = _target_strategy_key(item)
        out[key] = out.get(key, 0) + 1
    return out


def _format_pool_brief(targets_by: Dict[str, int]) -> str:
    bits = []
    for key in _STRATEGY_SUMMARY_ORDER:
        n = int(targets_by.get(key) or 0)
        if n:
            bits.append(f"{_STRATEGY_SUMMARY_LABELS[key]} {n}")
    return " · ".join(bits)


def _log_ma1592_scan_heartbeat(
    settings: AutoTradeSettings,
    targets_by: Dict[str, int],
    stats_by: Dict[str, Dict[str, int]],
    created_by: Dict[str, int],
) -> None:
    """15/92 장부 스캔 결과 — 활동 로그 필터용 한 줄 요약."""
    if not getattr(settings, "use_ma1592", False):
        return
    total = int(targets_by.get("ma1592") or 0)
    if total <= 0:
        return
    st = stats_by.get("ma1592") or {}
    wait_n = int(st.get("watching") or 0) + int(st.get("gate") or 0)
    signals = int(created_by.get("ma1592") or 0)
    msg = f"[MA1592] 15/92 장부 {total}종 검사 완료 (대기 {wait_n})"
    if signals:
        msg += f" · 신호 {signals}"
    logger.info(f"📈 [AUTO_SCANNER] {msg}")
    log_activity("SCANNER", msg, "info", strategy="ma1592")


def _format_strategy_scan_line(
    strategy_key: str,
    stats: Dict[str, int],
    total: int,
    created: int,
) -> str:
    label = _STRATEGY_SUMMARY_LABELS.get(strategy_key, strategy_key)
    parts = [f"대상 {total}"]
    for key in ("price_cond", "holding", "cooldown", "gate", "no_price", "signal_fail", "skipped"):
        n = int(stats.get(key) or 0)
        if n:
            parts.append(f"{_SCAN_STAT_LABELS[key]} {n}")
    parts.append(f"신호 {created}")
    return f"스캔 요약 · [{label}] " + " · ".join(parts)


def _format_scan_summary(
    stats: Dict[str, int],
    total: int,
    created: int,
    settings: AutoTradeSettings,
    *,
    add_created: int = 0,
    targets_by: Optional[Dict[str, int]] = None,
    stats_by: Optional[Dict[str, Dict[str, int]]] = None,
    created_by: Optional[Dict[str, int]] = None,
) -> List[str]:
    """전략별 스캔 요약 메시지 목록 (전체 1줄 + 프로필별)."""
    lines: List[str] = []
    head_parts = [f"전체 대상 {total}", f"신호 {created}"]
    if add_created:
        head_parts.append(f"(추가매수 {add_created})")
    pool = _format_pool_brief(targets_by or {})
    if pool:
        head_parts.append(pool)
    min_rate = effective_min_change_rate(settings)
    if min_rate is not None:
        head_parts.append(f"최소등락 {min_rate:g}%")
    lines.append("스캔 요약 — " + " · ".join(head_parts))

    if stats_by and created_by is not None and targets_by is not None:
        for key in _STRATEGY_SUMMARY_ORDER:
            t = int(targets_by.get(key) or 0)
            c = int(created_by.get(key) or 0)
            st = stats_by.get(key) or {}
            if t <= 0 and c <= 0:
                continue
            lines.append(_format_strategy_scan_line(key, st, t, c))
    elif total:
        # 하위 호환: 전략 분해 없이 단일 합계만
        parts = [f"대상 {total}"]
        for key in ("price_cond", "holding", "cooldown", "gate", "no_price", "signal_fail", "skipped"):
            n = stats.get(key, 0)
            if n:
                parts.append(f"{_SCAN_STAT_LABELS[key]} {n}")
        parts.append(f"신호 {created}")
        lines.append("스캔 요약 · [합계] " + " · ".join(parts))
    return lines


class AutoTradeScanner:
    def __init__(self):
        self.kiwoom_api = KiwoomAPI()
        self.is_running = False
        self.scan_interval = 60  # 기본 1분 — 설정 scan_interval_sec 로 덮어씀
        self._task: Optional[asyncio.Task] = None
        self.last_scan_at: Optional[datetime] = None
        self.last_scan_created = 0
        self.last_scan_targets = 0
        self.last_scan_by_strategy: Dict[str, Dict] = {}
        # 진행 중 스캔 부하 메트릭 (activity-log / 대시보드)
        self._scan_progress: Optional[Dict] = None
        self._last_scan_duration_sec: Optional[float] = None

    def _effective_scan_interval(self, settings: Optional[AutoTradeSettings] = None) -> int:
        settings = settings or self._load_settings()
        try:
            sec = int(getattr(settings, "scan_interval_sec", None) or self.scan_interval or 60)
        except (TypeError, ValueError):
            sec = 60
        return max(15, min(600, sec))

    async def start(self):
        if self.is_running:
            return
        self.is_running = True
        self._task = asyncio.create_task(self._loop())
        interval = self._effective_scan_interval()
        self.scan_interval = interval
        logger.info(f"📈 [AUTO_SCANNER] 자동매매 스캐너 시작 ({interval}초 주기)")
        log_activity("SCANNER", f"종목 스캐너 시작 ({interval}초 주기)", "info")

    async def stop(self):
        self.is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            finally:
                self._task = None
        logger.info("📈 [AUTO_SCANNER] 자동매매 스캐너 중지")
        log_activity("SCANNER", "종목 스캐너 중지", "warn")

    def is_session_active(self) -> bool:
        """장중 엔진 세션에서 실제 스캔 중인지 (루프 태스크 생존과 별개)."""
        if not self.is_running:
            return False
        settings = self._load_settings()
        if not settings or not settings.is_enabled:
            return False
        allowed, _ = auto_trade_engines_allowed()
        return allowed

    def _begin_scan_progress(self, settings: AutoTradeSettings) -> None:
        gate_pause = _scan_throttle_from_limiter(settings)
        self._scan_progress = {
            "active": True,
            "phase": "start",
            "started_at": now_kst().isoformat(),
            "started_mono": time.monotonic(),
            "targets_total": 0,
            "scanned": 0,
            "created": 0,
            "remaining": 0,
            "current_code": None,
            "current_name": None,
            "targets_by": {},
            "gate_pause_sec": gate_pause,
            "eta_sec": None,
        }

    def _update_scan_progress(self, **kwargs) -> None:
        prog = self._scan_progress
        if not prog:
            return
        prog.update(kwargs)
        total = int(prog.get("targets_total") or 0)
        scanned = int(prog.get("scanned") or 0)
        remaining = max(0, total - scanned)
        prog["remaining"] = remaining
        pause = float(prog.get("gate_pause_sec") or 0)
        # 게이트 스로틀 기준 대략 ETA (API 대기 제외)
        prog["eta_sec"] = round(remaining * pause, 0) if pause > 0 and remaining else 0
        started = prog.get("started_mono")
        if started is not None:
            prog["elapsed_sec"] = round(time.monotonic() - float(started), 1)

    def _end_scan_progress(self) -> None:
        prog = self._scan_progress
        if prog and prog.get("started_mono") is not None:
            self._last_scan_duration_sec = round(
                time.monotonic() - float(prog["started_mono"]), 1
            )
        self._scan_progress = None

    def get_scan_load(self) -> Dict:
        """스캔 부하 스냅샷 — 진행 중이면 실시간, 아니면 직전 스캔."""
        prog = self._scan_progress
        if prog and prog.get("active"):
            self._update_scan_progress()  # elapsed 갱신
            return {
                "in_progress": True,
                "phase": prog.get("phase"),
                "targets_total": int(prog.get("targets_total") or 0),
                "scanned": int(prog.get("scanned") or 0),
                "created": int(prog.get("created") or 0),
                "remaining": int(prog.get("remaining") or 0),
                "current_code": prog.get("current_code"),
                "current_name": prog.get("current_name"),
                "targets_by": dict(prog.get("targets_by") or {}),
                "gate_pause_sec": prog.get("gate_pause_sec"),
                "elapsed_sec": prog.get("elapsed_sec"),
                "eta_sec": prog.get("eta_sec"),
                "started_at": prog.get("started_at"),
                "last_scan_duration_sec": self._last_scan_duration_sec,
                "last_scan_targets": self.last_scan_targets,
            }
        return {
            "in_progress": False,
            "phase": None,
            "targets_total": int(self.last_scan_targets or 0),
            "scanned": int(self.last_scan_targets or 0),
            "created": int(self.last_scan_created or 0),
            "remaining": 0,
            "current_code": None,
            "current_name": None,
            "targets_by": {
                k: int((v or {}).get("targets") or 0)
                for k, v in (self.last_scan_by_strategy or {}).items()
            },
            "gate_pause_sec": None,
            "elapsed_sec": None,
            "eta_sec": 0,
            "started_at": None,
            "last_scan_duration_sec": self._last_scan_duration_sec,
            "last_scan_targets": self.last_scan_targets,
        }

    def get_status(self) -> Dict:
        active = self.is_session_active()
        settings = get_auto_trade_settings_sync()
        window = linked_trading_session_window_str(settings) if settings else None
        interval = self._effective_scan_interval(settings)
        return {
            "is_running": self.is_running,
            "is_active": active,
            "session_window": window,
            "trade_start_time": settings.trade_start_time if settings else None,
            "trade_end_time": settings.trade_end_time if settings else None,
            "last_scan_at": (
                self.last_scan_at if isinstance(self.last_scan_at, str) else self.last_scan_at.isoformat() 
            )
            if self.last_scan_at
            else None, 
            "last_scan_targets": self.last_scan_targets,
            "last_scan_created": self.last_scan_created,
            "last_scan_by_strategy": self.last_scan_by_strategy,
            "scan_interval_sec": interval,
            "scan_load": self.get_scan_load(),
        }

    async def _loop(self):
        try:
            while self.is_running:
                settings = self._load_settings()
                interval = self._effective_scan_interval(settings)
                self.scan_interval = interval
                if settings and settings.is_enabled:
                    allowed, off_reason = auto_trade_engines_allowed()
                    if not allowed:
                        msg = f"{off_reason} — 스캔 건너뜀"
                        logger.info(f"📈 [AUTO_SCANNER] {msg}")
                        log_activity("SCANNER", msg, "warn")
                    else:
                        try:
                            created, targets = await self._scan_once(settings)
                            self.last_scan_at = now_kst()
                            self.last_scan_created = created
                            self.last_scan_targets = targets
                            # last_scan_by_strategy는 _scan_once_inner에서 갱신
                        except Exception as e:
                            logger.error(f"📈 [AUTO_SCANNER] 스캔 오류: {e}")
                else:
                    logger.debug("📈 [AUTO_SCANNER] 자동매매 OFF — 스캔 건너뜀")
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("📈 [AUTO_SCANNER] 스캔 루프 종료")

    def _load_settings(self) -> Optional[AutoTradeSettings]:
        return get_auto_trade_settings_sync()

    async def _scan_once(self, settings: AutoTradeSettings) -> tuple:
        from utils.api_traffic_guard import mark_scan_end, mark_scan_start
        mark_scan_start()
        self._begin_scan_progress(settings)
        try:
            return await self._scan_once_inner(settings)
        finally:
            self._end_scan_progress()
            mark_scan_end()

    async def _scan_once_inner(self, settings: AutoTradeSettings) -> tuple:
        if not has_buy_conditions(settings):
            msg = "매수 조건 미설정 — 스캔 건너뜀"
            logger.info(f"📈 [AUTO_SCANNER] {msg}")
            log_activity("SCANNER", msg, "warn")
            return 0, 0

        open_avg = 0
        try:
            open_avg = await self._scan_jongga_open_avg_down(settings)
        except Exception as e:
            logger.exception(f"📈 [AUTO_SCANNER] 종가배팅 시초 물타기 오류: {e}")

        from utils.jongga_engine import in_open_avg_down_window
        from utils.market_hours import any_strategy_buy_window_open

        if in_open_avg_down_window() and not any_strategy_buy_window_open(settings):
            if open_avg:
                self.last_scan_by_strategy = {
                    "jongga": {
                        "label": _STRATEGY_SUMMARY_LABELS["jongga"],
                        "targets": 1,
                        "created": int(open_avg),
                        "stats": {},
                    }
                }
            return open_avg, 0

        halt = check_daily_limits(settings)
        if halt:
            logger.warning(f"📈 [AUTO_SCANNER] {halt} — 스캔 중지")
            log_activity("SCANNER", halt, "error")
            disable_auto_trade(halt)
            # 스캐너 태스크 자기취소는 하지 않음(is_enabled로 idle).
            # 매수 실행기만 멈추고 손절/동기화 루프는 반드시 유지.
            try:
                from core.main import _schedule_stop_loss_monitoring
                from managers.buy_order_executor import buy_order_executor

                _schedule_stop_loss_monitoring()
                if buy_order_executor.is_running:
                    await buy_order_executor.stop_processing()
                log_activity(
                    "SYSTEM",
                    f"{halt} — 신규매수 중단 · 손절/동기화 루프 유지",
                    "warn",
                )
            except Exception as e:
                logger.warning(f"📈 [AUTO_SCANNER] 일일 한도 중단 후처리 경고: {e}")
            return 0, 0

        block = new_buy_block_reason(settings)
        if block:
            logger.info(f"📈 [AUTO_SCANNER] {block} — 스캔 건너뜀")
            log_activity("SCANNER", f"{block} — 스캔 건너뜀", "warn")
            return 0, 0

        if await self._daily_buy_limit_reached(settings):
            msg = "1일 최대 매수 횟수 도달 — 스캔 건너뜀"
            logger.info(f"📈 [AUTO_SCANNER] {msg}")
            log_activity("SCANNER", msg, "warn")
            return 0, 0

        if await self._max_positions_reached(settings):
            from utils.auto_trade_engine import describe_open_position_slots, max_concurrent_positions_limit
            detail = {"holdings": 0, "reserved": 0, "total": 0}
            limit = max_concurrent_positions_limit(settings)
            for db in get_db():
                detail = describe_open_position_slots(db)
                break
            msg = (
                f"최대 동시 보유 종목 도달 — 일반 스캔 건너뜀 "
                f"({detail['total']}/{limit}: 보유 {detail['holdings']} + 대기 {detail['reserved']})"
            )
            logger.info(f"📈 [AUTO_SCANNER] {msg}")
            log_activity("SCANNER", msg, "warn")
            created = 0
            add_created = 0
            created_by: Dict[str, int] = defaultdict(int)
            # 종가배팅은 전역 슬롯과 별도 — 후보 수집 전에 먼저 시도
            try:
                jongga_n = await self._scan_jongga_session(settings)
                if jongga_n:
                    created += jongga_n
                    created_by["jongga"] += jongga_n
                jongga_legs = await self._scan_jongga_pig_legs(settings)
                if jongga_legs:
                    created += jongga_legs
                    created_by["jongga"] += jongga_legs
                    add_created += jongga_legs
            except Exception as e:
                logger.exception(f"📈 [AUTO_SCANNER] 종가배팅 처리 오류: {e}")
            if open_avg:
                created += open_avg
                created_by["jongga"] += open_avg
                add_created += open_avg
            self.last_scan_by_strategy = {
                key: {
                    "label": _STRATEGY_SUMMARY_LABELS[key],
                    "targets": 0,
                    "created": int(created_by.get(key) or 0),
                    "stats": {},
                }
                for key in _STRATEGY_SUMMARY_ORDER
                if int(created_by.get(key) or 0)
            }
            return created, 0

        created = 0
        add_created = 0
        stats: Dict[str, int] = defaultdict(int)
        stats_by: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        created_by: Dict[str, int] = defaultdict(int)
        scanned = 0
        self._market_risk_eval_cache = {}

        # 종가배팅: 시간창이 짧아 후보 수집·일반 루프보다 먼저
        try:
            self._update_scan_progress(phase="jongga")
            jongga_n = await self._scan_jongga_session(settings)
            if jongga_n:
                created += jongga_n
                created_by["jongga"] += jongga_n
            jongga_legs = await self._scan_jongga_pig_legs(settings)
            if jongga_legs:
                created += jongga_legs
                created_by["jongga"] += jongga_legs
                add_created += jongga_legs
        except Exception as e:
            logger.exception(f"📈 [AUTO_SCANNER] 종가배팅 처리 오류: {e}")
        try:
            ma_legs = await self._scan_ma1592_scale_legs(settings)
            if ma_legs:
                created += ma_legs
                created_by["ma1592"] += ma_legs
                add_created += ma_legs
        except Exception as e:
            logger.exception(f"📈 [AUTO_SCANNER] MA1592 분할 추가매수 오류: {e}")
        if open_avg:
            created += open_avg
            created_by["jongga"] += open_avg
            add_created += open_avg

        self._update_scan_progress(phase="collect", created=created)
        targets = await self._collect_targets(settings)
        if not targets:
            logger.debug("📈 [AUTO_SCANNER] 스캔 대상 없음")
            log_activity("SCANNER", "스캔 대상 0개 (관심종목·스크리너 조건 확인)", "info")
        if targets and os.getenv("SCANNER_DISPARITY_LOG", "").lower() in ("1", "true", "yes"):
            await self._log_disparity_observations(targets)

        targets_by = _count_targets_by_strategy(targets or [])
        gate_pause = _scan_throttle_from_limiter(settings)
        self._update_scan_progress(
            phase="evaluate",
            targets_total=len(targets or []),
            scanned=0,
            created=0,
            targets_by=dict(targets_by),
            gate_pause_sec=gate_pause,
        )
        pool_brief = _format_pool_brief(targets_by)
        start_msg = f"스캔 시작 — 대상 {len(targets or [])}개"
        if pool_brief:
            start_msg = f"{start_msg} ({pool_brief})"
        log_activity(
            "SCANNER",
            start_msg,
            "info",
            targets=len(targets or []),
            targets_by=dict(targets_by),
        )
        try:
            for item in targets or []:
                if await self._daily_buy_limit_reached(settings):
                    left = len(targets or []) - scanned
                    stats["skipped"] += left
                    # 남은 종목을 전략별로 미검사 처리
                    for rest in (targets or [])[scanned:]:
                        stats_by[_target_strategy_key(rest)]["skipped"] += 1
                    break
                if await self._max_positions_reached(settings):
                    left = len(targets or []) - scanned
                    stats["skipped"] += left
                    for rest in (targets or [])[scanned:]:
                        stats_by[_target_strategy_key(rest)]["skipped"] += 1
                    break
                sk = _target_strategy_key(item)
                self._update_scan_progress(
                    phase="evaluate",
                    current_code=item.get("stock_code"),
                    current_name=item.get("stock_name"),
                    scanned=scanned,
                    created=created,
                )
                ok, reason = await self._evaluate_and_signal(settings, item)
                stats[reason] += 1
                stats_by[sk][reason] += 1
                scanned += 1
                if ok:
                    created += 1
                    created_by[sk] += 1
                self._update_scan_progress(
                    scanned=scanned,
                    created=created,
                    current_code=item.get("stock_code"),
                    current_name=item.get("stock_name"),
                )
                if reason in _THROTTLE_REASONS:
                    pause = _scan_throttle_from_limiter(settings)
                    self._update_scan_progress(gate_pause_sec=pause)
                    await asyncio.sleep(pause)
                else:
                    await asyncio.sleep(0)

            self._update_scan_progress(phase="pyramiding", current_code=None, current_name=None)
            add_created_pyr = await self._scan_pyramiding_adds(settings)
            if add_created_pyr:
                created += add_created_pyr
                add_created += add_created_pyr
                created_by["legacy"] += add_created_pyr
            try:
                ma_legs = await self._scan_ma1592_scale_legs(settings)
                if ma_legs:
                    created += ma_legs
                    add_created += ma_legs
                    created_by["ma1592"] += ma_legs
            except Exception as e:
                logger.exception(f"📈 [AUTO_SCANNER] MA1592 분할 추가매수 오류: {e}")
        finally:
            summary_lines = _format_scan_summary(
                stats,
                len(targets),
                created,
                settings,
                add_created=add_created,
                targets_by=targets_by,
                stats_by={k: dict(v) for k, v in stats_by.items()},
                created_by=dict(created_by),
            )
            self.last_scan_by_strategy = {
                key: {
                    "label": _STRATEGY_SUMMARY_LABELS[key],
                    "targets": int(targets_by.get(key) or 0),
                    "created": int(created_by.get(key) or 0),
                    "stats": dict(stats_by.get(key) or {}),
                }
                for key in _STRATEGY_SUMMARY_ORDER
                if int(targets_by.get(key) or 0) or int(created_by.get(key) or 0)
            }
            for line in summary_lines:
                logger.info(f"📈 [AUTO_SCANNER] {line}")
                log_activity(
                    "SCANNER",
                    line,
                    "info",
                    targets=len(targets),
                    signals=created,
                    scan_stats=dict(stats),
                    targets_by=dict(targets_by),
                    created_by=dict(created_by),
                )
            _log_ma1592_scan_heartbeat(
                settings,
                dict(targets_by),
                {k: dict(v) for k, v in stats_by.items()},
                dict(created_by),
            )
        return created, len(targets)

    async def _log_disparity_observations(self, targets: List[Dict]) -> None:
        """관찰용: 후보별 이격도(5/20) 계산 후 로그만 남긴다. 게이트·신호에는 미반영."""
        sem = asyncio.Semaphore(_DISPARITY_LOG_CONCURRENCY)
        stats = {"ok5": 0, "ok20": 0, "missing_price": 0, "missing_bars": 0}

        async def _worker(item: Dict) -> None:
            code = KiwoomAPI.normalize_stock_code(item.get("stock_code", ""))
            name = item.get("stock_name") or code
            src = item.get("source", "scanner")
            if not code:
                return

            # 관찰 단계에서는 추가 API 호출을 늘리지 않기 위해 현재가가 없으면 건너뛴다.
            try:
                price = int(item.get("current_price") or 0)
            except (TypeError, ValueError):
                price = 0
            if price <= 0:
                stats["missing_price"] += 1
                logger.info(f"📈 [AUTO_SCANNER][DISP] {name}({code}) src={src} disp5=N/A disp20=N/A reason=no_price")
                return

            async with sem:
                bars = await self.kiwoom_api.get_stock_chart_data(code, "1D")
            if not bars:
                stats["missing_bars"] += 1
                logger.info(f"📈 [AUTO_SCANNER][DISP] {name}({code}) src={src} disp5=N/A disp20=N/A reason=no_bars")
                return

            disp5 = self._calc_disparity(price, bars, 5)
            disp20 = self._calc_disparity(price, bars, 20)
            if disp5 is not None:
                stats["ok5"] += 1
            if disp20 is not None:
                stats["ok20"] += 1
            d5 = f"{disp5:.2f}" if disp5 is not None else "N/A"
            d20 = f"{disp20:.2f}" if disp20 is not None else "N/A"
            logger.info(f"📈 [AUTO_SCANNER][DISP] {name}({code}) src={src} disp5={d5} disp20={d20}")

        await asyncio.gather(*(_worker(item) for item in targets))
        log_activity(
            "SCANNER",
            (
                f"이격도 관찰 — 대상 {len(targets)} · "
                f"5일계산 {stats['ok5']} · 20일계산 {stats['ok20']} · "
                f"가격없음 {stats['missing_price']} · 차트없음 {stats['missing_bars']}"
            ),
            "info",
            disparity_stats=dict(stats),
        )

    @staticmethod
    def _calc_disparity(current_price: int, bars: List[Dict], period: int) -> Optional[float]:
        closes: List[int] = []
        for bar in bars:
            v = bar.get("close")
            try:
                c = int(v or 0)
            except (TypeError, ValueError):
                c = 0
            if c > 0:
                closes.append(c)
        if len(closes) < period:
            return None
        ma = sum(closes[-period:]) / period
        if ma <= 0:
            return None
        return float(current_price) / ma * 100.0

    async def _collect_targets(self, settings: AutoTradeSettings) -> List[Dict]:
        """관심·상따·돌파·프랙탈을 먼저 모은 뒤, 잔여 자리를 레거시(거래대금 상위)로 채운다.

        총 스캔 대상은 SCAN_TARGET_TOTAL_LIMIT(기본 60)을 넘지 않도록
        레거시 상위 N을 동적으로 축소한다.
        """
        by_code: Dict[str, Dict] = {}
        total_limit = max(1, int(getattr(Config, "SCAN_TARGET_TOTAL_LIMIT", None) or 60))
        screener_cap = max(0, int(Config.SCREENER_CANDIDATE_LIMIT or 20))

        # 1) 관심종목 (설정 textarea)
        for code in self._parse_watchlist(settings.watchlist_codes):
            by_code.setdefault(code, {"stock_code": code, "stock_name": code, "source": "watchlist"})

        # 2) 상따 유니버스 — ka10027 등락률상위 풀 → 거래대금순 상위 N
        if getattr(settings, "use_sangtta", True):
            try:
                sang_limit = max(1, int(Config.SANGTTA_CANDIDATE_LIMIT or 20))
                sang_pool = max(sang_limit * 5, 100)
                sang_res = await self.kiwoom_api.get_change_rate_rank(
                    limit=sang_pool, sangtta_filters=True,
                )
                if not sang_res.get("success"):
                    logger.warning(
                        f"📈 [AUTO_SCANNER] 상따 등락률상위 조회 실패: {sang_res.get('error')}"
                    )
                sang_raw = sang_res.get("items") or []
                sang_items = KiwoomAPI.cap_by_trade_amount(sang_raw, sang_limit)
                for it in sang_items:
                    code = it.get("stock_code")
                    if not code:
                        continue
                    by_code[code] = {**it, "source": "sangtta"}
                n = len(sang_items)
                min_chg = sang_res.get("min_change_rate")
                logger.info(
                    f"📈 [AUTO_SCANNER] 상따 후보 수집 — {n}개 "
                    f"(ka10027 등락≥{min_chg}% · 거래대금순 상위 {sang_limit} "
                    f"· 풀 {len(sang_raw)} · 관리제외·천원↑·대금10억↑·ETF제외)"
                )
                self._log_strategy_candidates("상따", sang_items)
            except Exception as e:
                logger.debug(f"📈 [AUTO_SCANNER] 상따 후보 수집 중 오류: {e}")
        else:
            logger.info("📈 [AUTO_SCANNER] 상따 스캔 스킵 (전략 OFF)")

        # 3) 과매도 돌파 전용 조건식 — 다른 유니버스와 합치지 않고 source로 전략을 고정
        from utils.screener_targets import fetch_condition_target_items, parse_condition_names

        try:
            breakout_names = parse_condition_names(
                getattr(settings, "breakout_condition_names", None)
            )
            if getattr(settings, "use_breakout", False) and breakout_names:
                breakout_items, breakout_errs = await fetch_condition_target_items(
                    self.kiwoom_api, breakout_names,
                )
                if breakout_errs:
                    logger.warning(
                        f"📈 [AUTO_SCANNER] 돌파 조건식 조회 실패: {', '.join(breakout_errs)}"
                    )
                for it in breakout_items or []:
                    code = it.get("stock_code")
                    if code and by_code.get(code, {}).get("source") != "sangtta":
                        by_code[code] = {**it, "source": "breakout"}
                n = len(breakout_items or [])
                logger.info(
                    f"📈 [AUTO_SCANNER] 돌파 후보 수집 — {n}개 "
                    f"(설정: {', '.join(breakout_names)})"
                )
                self._log_strategy_candidates("돌파", breakout_items or [])
        except Exception as e:
            logger.debug(f"📈 [AUTO_SCANNER] 돌파 후보 수집 중 오류: {e}")

        # 4) 프랙탈 스캘핑 — HTS 조건식 + WATCHING 스티키 (동시 5)
        try:
            fractal_names = parse_condition_names(
                getattr(settings, "fractal_condition_names", None)
            )
            if getattr(settings, "use_fractal", False) and fractal_names:
                from utils.auto_trade_engine import effective_fractal_watch_slots

                watch_limit = effective_fractal_watch_slots(settings)
                fractal_items, fractal_errs = await fetch_condition_target_items(
                    self.kiwoom_api, fractal_names,
                )
                if fractal_errs:
                    logger.warning(
                        f"📈 [AUTO_SCANNER] 프랙탈 조건식 조회 실패: {', '.join(fractal_errs)}"
                    )
                sticky = self._fractal_sticky_watching()
                sticky_codes = {c for c, _ in sticky}
                for code, meta in sticky:
                    src = by_code.get(code, {}).get("source")
                    if src in ("sangtta", "breakout"):
                        continue
                    by_code[code] = {
                        "stock_code": code,
                        "stock_name": meta.get("stock_name") or code,
                        "current_price": meta.get("current_price"),
                        "change_rate": meta.get("change_rate"),
                        "source": "fractal",
                        "fractal_sticky": True,
                    }
                hts_new = []
                for it in fractal_items or []:
                    code = it.get("stock_code")
                    if not code or code in sticky_codes:
                        continue
                    src = by_code.get(code, {}).get("source")
                    if src in ("sangtta", "breakout"):
                        continue
                    hts_new.append(it)
                remaining = max(0, watch_limit - len(sticky_codes))
                capped_new = KiwoomAPI.cap_by_trade_amount(hts_new, remaining) if remaining else []
                for it in capped_new:
                    code = it.get("stock_code")
                    if code:
                        by_code[code] = {**it, "source": "fractal"}
                logger.info(
                    f"📈 [AUTO_SCANNER] 프랙탈 후보 — HTS {len(fractal_items or [])} "
                    f"스티키 {len(sticky_codes)} 신규 {len(capped_new)} "
                    f"(WATCHING≤{watch_limit} · {', '.join(fractal_names)})"
                )
                self._log_strategy_candidates(
                    "프랙탈",
                    [by_code[c] for c in list(by_code) if by_code[c].get("source") == "fractal"],
                )
        except Exception as e:
            logger.debug(f"📈 [AUTO_SCANNER] 프랙탈 후보 수집 중 오류: {e}")

        # 4b) MA1592 — L1 대금상위 → L2 GC 장부 → L3는 장부만 스캔
        try:
            if getattr(settings, "use_ma1592", False):
                await self._collect_ma1592_targets(settings, by_code)
        except Exception as e:
            logger.debug(f"📈 [AUTO_SCANNER] MA1592 후보 수집 중 오류: {e}")

        # 5) 레거시 — 잔여 자리만큼 거래대금 상위 (총한도 초과 시 여기서 축소)
        reserved = len(by_code)
        legacy_limit = effective_legacy_scan_limit(total_limit, screener_cap, reserved)
        capped_legacy: Dict[str, Dict] = {}
        volume_items: List[Dict] = []

        if not getattr(settings, "use_legacy", True):
            logger.info("📈 [AUTO_SCANNER] 레거시 스캔 스킵 (전략 OFF)")
        elif legacy_limit <= 0:
            logger.info(
                f"📈 [AUTO_SCANNER] 레거시 스캔 0 "
                f"(총한도 {total_limit} · 비레거시 {reserved} · 잔여 없음)"
            )
        else:
            # 중복(상따 등과 겹침) 대비로 잔여+편입 수만큼 조회 후 신규만 채움
            fetch_limit = min(150, max(legacy_limit + reserved, screener_cap, legacy_limit))
            min_chg = float(getattr(Config, "SCREENER_MIN_CHANGE_RATE", 0) or 0)
            max_chg = float(getattr(Config, "SCREENER_MAX_CHANGE_RATE", 0) or 0)
            min_amt = float(getattr(Config, "SCREENER_MIN_TRADE_AMOUNT_EOK", 0) or 0)
            res = await self.kiwoom_api.get_volume_rank(
                market="000",
                sort_tp="3",
                limit=fetch_limit,
                min_change_rate=min_chg or None,
                max_change_rate=max_chg or None,
                min_trade_amount_eok=min_amt or None,
            )
            if res.get("success"):
                volume_items = (res.get("items") or [])[:fetch_limit]
                excl_neg = int(res.get("excluded_negative_count") or 0)
                excl_oh = int(res.get("excluded_overheat_count") or 0)
                excl_amt = int(res.get("excluded_low_amount_count") or 0)
                floor = res.get("min_change_rate")
                ceil = res.get("max_change_rate")
                amt_floor = res.get("min_trade_amount_eok")
                band_s = (
                    f"등락{float(floor):g}~<{float(ceil):g}%"
                    if floor is not None and ceil is not None
                    else (f"등락≥{float(floor):g}%" if floor is not None else "등락률+")
                )
                if amt_floor is not None:
                    band_s = f"{band_s}·대금≥{float(amt_floor):g}억"
                if excl_neg or excl_oh or excl_amt:
                    parts = []
                    if excl_neg:
                        parts.append(f"미달 {excl_neg}")
                    if excl_oh:
                        parts.append(f"과열 {excl_oh}")
                    if excl_amt:
                        parts.append(f"대금 {excl_amt}")
                    logger.info(f"📈 [AUTO_SCANNER] 거래대금순 {band_s} 제외 {', '.join(parts)}건")
                if len(volume_items) < legacy_limit:
                    logger.warning(
                        f"📈 [AUTO_SCANNER] 스크리너 후보 {len(volume_items)}/{legacy_limit}개만 조회됨 "
                        f"(API 제한·페이징·{band_s} 필터)"
                    )
            else:
                err = res.get("error") or "조회 실패"
                logger.warning(f"📈 [AUTO_SCANNER] 거래대금 상위 조회 실패: {err}")
                log_activity("SCANNER", f"스크리너 조회 실패: {err}", "warn")

            def _trade_amt(row: Dict) -> float:
                for key in ("trade_amount", "trading_value", "trde_prica"):
                    try:
                        v = row.get(key)
                        if v is not None and str(v).strip() != "":
                            return float(v)
                    except (TypeError, ValueError):
                        continue
                return 0.0

            pool = []
            for it in volume_items:
                code = it.get("stock_code")
                if not code or code in by_code:
                    continue
                name = it.get("stock_name", "")
                if not KiwoomAPI._is_screener_stock(name, it.get("product_type")):
                    continue
                pool.append((code, {**it, "source": "screener"}))
            pool.sort(key=lambda pair: _trade_amt(pair[1]), reverse=True)
            capped_legacy = dict(pool[:legacy_limit])
            by_code.update(capped_legacy)

            logger.info(
                f"📈 [AUTO_SCANNER] 후보 수집 — 총한도 {total_limit} · 비레거시 {reserved} "
                f"· 레거시 상위 {legacy_limit}/{screener_cap} "
                f"({len(volume_items)}조회 → 스캔 {len(capped_legacy)})"
            )

        if len(by_code) > total_limit:
            logger.warning(
                f"📈 [AUTO_SCANNER] 스캔 대상 {len(by_code)} > 총한도 {total_limit} "
                f"(비레거시만으로 초과 — 레거시로 더 줄일 수 없음)"
            )

        return list(by_code.values())

    @staticmethod
    def _format_candidate_brief(item: Dict) -> str:
        code = item.get("stock_code") or "?"
        name = (item.get("stock_name") or "").strip() or code
        try:
            price = int(item.get("current_price") or 0)
        except (TypeError, ValueError):
            price = 0
        chg = item.get("change_rate")
        try:
            chg_s = f"{float(chg):+.2f}%" if chg is not None else "?"
        except (TypeError, ValueError):
            chg_s = "?"
        price_s = f"{price:,}" if price else "?"
        return f"{name}({code}) {price_s} {chg_s}"

    @classmethod
    def _log_strategy_candidates(cls, label: str, items: List[Dict]) -> None:
        """상따/돌파 편입 종목을 파일 로그에 남긴다."""
        if not items:
            logger.info(f"📈 [AUTO_SCANNER] [{label}] 편입 종목 없음")
            return
        briefs = [cls._format_candidate_brief(it) for it in items]
        # 한 줄에 过多하지 않게 상위 20개 + 나머지는 개수만
        shown = briefs[:20]
        extra = len(briefs) - len(shown)
        detail = ", ".join(shown)
        if extra > 0:
            detail = f"{detail} …외 {extra}개"
        logger.info(f"📈 [AUTO_SCANNER] [{label}] 편입 {len(briefs)}종목: {detail}")
        log_activity("SCANNER", f"[{label}] 편입 {len(briefs)}종목: {detail}", "info")

    async def _collect_ma1592_targets(
        self, settings: AutoTradeSettings, by_code: Dict[str, Dict],
    ) -> None:
        """L1 = HTS 조건식(기본 1592매매) 편입 → 장부 스티키.

        편입 → 장부(GC_WATCH). 조건식 이탈로는 빼지 않음.
        EMA15≤EMA92(추세 전환) 시 L3에서 장부 제거.
        """
        from utils.ma1592 import effective_l1_limit, get_universe_store, maintain_ma1592_universe, params_from_settings, select_l3_codes_for_scan, sync_universe_from_condition_async
        from utils.screener_targets import fetch_condition_target_items, parse_condition_names

        names = parse_condition_names(getattr(settings, "ma1592_condition_names", None))
        if not names:
            # 텔레그램 조건알림과 동일 기본값
            try:
                names = list(getattr(Config, "TELEGRAM_ALERT_CONDITION_NAMES", None) or [])
            except Exception:
                names = []
        if not names:
            names = [Config.MA1592_DEFAULT_CONDITION_NAME]

        store = get_universe_store()
        store.expire_stale()
        p = params_from_settings(settings)

        items, errs = await fetch_condition_target_items(self.kiwoom_api, names)
        if errs:
            logger.warning(f"📈 [AUTO_SCANNER] MA1592 조건식 조회 실패: {', '.join(errs)}")

        present: Dict[str, Dict] = {}
        for it in items or []:
            code = KiwoomAPI.normalize_stock_code(it.get("stock_code") or "")
            if not code:
                continue
            present[code] = {
                "stock_name": it.get("stock_name") or code,
                "current_price": it.get("current_price"),
            }

        stats = await sync_universe_from_condition_async(
            self.kiwoom_api,
            present,
            source="condition",
            params=p,
            store=store,
            cache_ttl_sec=float(getattr(Config, "MA1592_CHART_CACHE_TTL", 60) or 60),
        )

        maint = await maintain_ma1592_universe(
            self.kiwoom_api, params=p, store=store,
            cache_ttl_sec=float(getattr(Config, "MA1592_CHART_CACHE_TTL", 60) or 60),
        )
        purged = maint.get("purged") or []
        trimmed = maint.get("trimmed") or []
        if purged:
            logger.info(
                f"📈 [AUTO_SCANNER] MA1592 추세전환 장부 정리: {', '.join(purged)}"
            )
        if trimmed:
            logger.info(
                f"📈 [AUTO_SCANNER] MA1592 관찰 상한 초과 정리: {', '.join(trimmed)}"
            )

        l3 = select_l3_codes_for_scan(store, params=p)
        l3_total = len(store.l3_codes())
        for code in l3:
            row = store.get(code)
            meta = present.get(code) or {}
            name = (row.stock_name if row else None) or meta.get("stock_name") or code
            by_code[code] = {
                "stock_code": code,
                "stock_name": name,
                "source": "ma1592",
                "current_price": meta.get("current_price"),
            }
        logger.info(
            f"📈 [AUTO_SCANNER] MA1592 — 조건 {', '.join(names)} · "
            f"편입스냅샷 {stats.get('present', 0)} · "
            f"+{stats.get('added', 0)}(스티키) · "
            f"거부 {stats.get('rejected', 0)} · "
            f"상한스킵 {stats.get('limit_skipped', 0)} · "
            f"L3관찰 {len(l3)}/{l3_total} (한도 {stats.get('l1_limit', effective_l1_limit(p))})"
        )
        self._log_strategy_candidates(
            "MA1592",
            [by_code[c] for c in l3 if c in by_code],
        )

    def _fractal_sticky_watching(self) -> List[tuple]:
        """프랙탈 WATCHING 종목 — HTS 이탈해도 관찰 유지."""
        from utils.auto_trade_engine import parse_signal_meta

        out = []
        for db in get_db():
            session: Session = db
            rows = (
                session.query(PendingBuySignal)
                .filter(PendingBuySignal.status == "WATCHING")
                .all()
            )
            for sig in rows:
                meta = parse_signal_meta(sig)
                if str(meta.get("strategy") or "") != "fractal":
                    continue
                code = KiwoomAPI.normalize_stock_code(sig.stock_code or "")
                if not code:
                    continue
                merged = dict(meta)
                merged["stock_name"] = sig.stock_name
                merged["detected_at"] = sig.detected_at
                out.append((code, merged))
            break
        return out

    def _fractal_sticky_meta(self, stock_code: str) -> Optional[Dict]:
        code = KiwoomAPI.normalize_stock_code(stock_code or "")
        for c, meta in self._fractal_sticky_watching():
            if c == code:
                return meta
        return None

    @staticmethod
    def _parse_watchlist(raw: Optional[str]) -> List[str]:
        if not raw:
            return []
        codes = []
        for part in raw.replace("\n", ",").split(","):
            c = part.strip().replace("A", "")
            if c.isdigit() and len(c) == 6:
                codes.append(c)
        return codes

    async def _evaluate_and_signal(self, settings: AutoTradeSettings, item: Dict) -> Tuple[bool, str]:
        code = KiwoomAPI.normalize_stock_code(item.get("stock_code", ""))
        name = item.get("stock_name") or code
        if not code:
            return False, "no_price"

        if await self._has_open_interest(code):
            src = item.get("source")
            if src in ("sangtta", "breakout", "fractal", "ma1592"):
                self._log_scan_skip(name, code, "보유·대기", "이미 보유/대기", strategy=str(src))
            else:
                logger.debug(f"📈 [AUTO_SCANNER] 이미 보유/대기 — 스킵: {name}")
            return False, "holding"

        if await self._in_cooldown(code, settings.reorder_cooldown_sec or 300):
            src = item.get("source")
            if src in ("sangtta", "breakout", "fractal", "ma1592"):
                self._log_scan_skip(name, code, "쿨다운", "재주문 쿨다운", strategy=str(src))
            return False, "cooldown"

        price = item.get("current_price")
        change_rate = item.get("change_rate")
        if not price or price <= 0:
            snap = await self.kiwoom_api.get_stock_snapshot(code)
            if snap.get("success"):
                s = snap.get("snapshot") or {}
                price = s.get("current_price") or price
                if change_rate is None:
                    try:
                        change_rate = float(str(s.get("change_rate", "0")).replace(",", ""))
                    except (TypeError, ValueError):
                        change_rate = None
            else:
                price = await self.kiwoom_api.get_current_price(code)
        if not price or price <= 0:
            src = item.get("source")
            if src in ("sangtta", "breakout", "fractal", "ma1592"):
                self._log_scan_skip(name, code, "시세", "현재가 없음", strategy=str(src))
            return False, "no_price"

        # 전략별 시간대 / 슬롯 제약 확인 (예: sangtta 전용 윈도우 및 쿼터)
        strategy = item.get("source", "scanner")
        if strategy == "sangtta":
            strategy = "sangtta"
        elif strategy in ("screener", "condition", "both", "watchlist", "scanner"):
            strategy = "legacy"
        else:
            strategy = str(strategy or "legacy")

        if strategy in ("sangtta", "breakout", "fractal", "ma1592"):
            label = {
                "sangtta": "상따", "breakout": "돌파", "fractal": "프랙탈", "ma1592": "MA1592",
            }.get(strategy, strategy)
            try:
                chg_s = f"{float(change_rate):+.2f}%" if change_rate is not None else "?"
            except (TypeError, ValueError):
                chg_s = "?"
            logger.info(
                f"📈 [AUTO_SCANNER] [{label}] 평가 {name}({code}) "
                f"가격={int(price):,} 등락={chg_s}"
            )

        # 전략 패키지는 전역 signal_min 대신 자체 등락·과열 규칙을 사용
        if strategy not in ("sangtta", "breakout", "fractal", "ma1592"):
            if not passes_buy_price_conditions(settings, price, change_rate):
                skip = buy_price_skip_reason(settings, price, change_rate) or "매수 조건 미충족"
                self._log_scan_skip(name, code, "등락/가격", skip)
                return False, "price_cond"
        elif settings.buy_below_price and price > int(settings.buy_below_price):
            skip = f"매수가 상한 초과 ({price:,} > {int(settings.buy_below_price):,})"
            self._log_scan_skip(name, code, "등락/가격", skip, strategy=strategy)
            return False, "price_cond"

        from utils.market_risk_gate import check_market_risk_buy_allowed
        risk_ok, risk_reason = True, ""
        for db in get_db():
            risk_ok, risk_reason = check_market_risk_buy_allowed(
                settings,
                strategy,
                eval_cache=getattr(self, "_market_risk_eval_cache", None),
                session=db,
                stock_code=code,
            )
            break
        if not risk_ok:
            self._log_scan_skip(
                name, code, "장세", risk_reason,
                strategy=strategy if strategy in ("sangtta", "breakout") else "",
            )
            return False, "market_risk"

        if strategy == "sangtta":
            # 전략별 시간 허용 여부
            from utils.auto_trade_engine import allows_strategy_new_buy, is_strategy_slot_available
            allowed, reason = allows_strategy_new_buy(settings, "sangtta")
            if not allowed:
                self._log_scan_skip(
                    name, code, "게이트", reason or "상따 시간 외", strategy="sangtta",
                )
                return False, "gate"
            # 전략별 슬롯 확인
            for db in get_db():
                if not is_strategy_slot_available(settings, db, "sangtta", for_new_signal=True):
                    from utils.auto_trade_engine import _count_strategy_slots, effective_sangtta_max_slots
                    used = _count_strategy_slots(db, "sangtta")
                    lim = effective_sangtta_max_slots(settings)
                    self._log_scan_skip(
                        name, code, "게이트", f"상따 슬롯 포화 ({used}/{lim})",
                        strategy="sangtta",
                    )
                    return False, "gate"
                break
            gate_ok, gate_reason = await evaluate_gate_pack(
                self.kiwoom_api,
                settings,
                "sangtta_breakout",
                code,
                price,
                change_rate=change_rate,
                skip_time_check=True,
            )
            gate_ctx = {}
        elif strategy == "breakout":
            from utils.auto_trade_engine import allows_strategy_new_buy, is_strategy_slot_available
            allowed, reason = allows_strategy_new_buy(settings, "breakout")
            if not allowed:
                self._log_scan_skip(
                    name, code, "게이트", reason or "돌파 시간 외", strategy="breakout",
                )
                return False, "gate"
            for db in get_db():
                if not is_strategy_slot_available(settings, db, "breakout", for_new_signal=True):
                    from utils.auto_trade_engine import _count_strategy_slots, effective_breakout_max_slots
                    used = _count_strategy_slots(db, "breakout")
                    lim = effective_breakout_max_slots(settings)
                    self._log_scan_skip(
                        name, code, "게이트", f"돌파 슬롯 포화 ({used}/{lim})",
                        strategy="breakout",
                    )
                    return False, "gate"
                break
            gate_ctx = {}
            gate_ok, gate_reason = await evaluate_gate_pack(
                self.kiwoom_api,
                settings,
                "oversold_breakout",
                code,
                price,
                change_rate=change_rate,
                ctx=gate_ctx,
                skip_time_check=True,
                update_soft_streak=True,
            )
        elif strategy == "fractal":
            from utils.auto_trade_engine import allows_strategy_new_buy, is_strategy_slot_available
            allowed, reason = allows_strategy_new_buy(settings, "fractal")
            if not allowed:
                self._log_scan_skip(
                    name, code, "게이트", reason or "프랙탈 시간 외", strategy="fractal",
                )
                return False, "gate"
            for db in get_db():
                if not is_strategy_slot_available(settings, db, "fractal", for_new_signal=True):
                    from utils.auto_trade_engine import _count_strategy_slots, effective_fractal_max_slots
                    used = _count_strategy_slots(db, "fractal")
                    lim = effective_fractal_max_slots(settings)
                    self._log_scan_skip(
                        name, code, "게이트", f"프랙탈 슬롯 포화 ({used}/{lim})",
                        strategy="fractal",
                    )
                    return False, "gate"
                break
            gate_ctx = {
                "stock_name": name,
                "watching_started_at": item.get("detected_at"),
            }
            if item.get("fractal_sticky"):
                sticky_meta = self._fractal_sticky_meta(code)
                if sticky_meta:
                    gate_ctx["watching_started_at"] = sticky_meta.get("detected_at")
            gate_ok, gate_reason = await evaluate_gate_pack(
                self.kiwoom_api,
                settings,
                "ema_fractal_pullback",
                code,
                price,
                change_rate=change_rate,
                ctx=gate_ctx,
                skip_time_check=True,
            )
        elif strategy == "ma1592":
            from utils.auto_trade_engine import allows_strategy_new_buy, is_strategy_slot_available
            allowed, reason = allows_strategy_new_buy(settings, "ma1592")
            if not allowed:
                self._log_scan_skip(
                    name, code, "게이트", reason or "MA1592 시간 외", strategy="ma1592",
                )
                return False, "gate"
            for db in get_db():
                if not is_strategy_slot_available(settings, db, "ma1592", for_new_signal=True):
                    from utils.auto_trade_engine import _count_strategy_slots, effective_ma1592_max_slots
                    used = _count_strategy_slots(db, "ma1592")
                    lim = effective_ma1592_max_slots(settings)
                    self._log_scan_skip(
                        name, code, "게이트", f"MA1592 슬롯 포화 ({used}/{lim})",
                        strategy="ma1592",
                    )
                    return False, "gate"
                break
            gate_ctx = {"stock_name": name, "already_in_position": False}
            gate_ok, gate_reason = await evaluate_gate_pack(
                self.kiwoom_api,
                settings,
                "ma1592_hold",
                code,
                price,
                change_rate=change_rate,
                ctx=gate_ctx,
                skip_time_check=True,
            )
        else:
            from utils.auto_trade_engine import allows_strategy_new_buy, is_strategy_slot_available
            allowed, reason = allows_strategy_new_buy(settings, "legacy")
            if not allowed:
                self._log_scan_skip(
                    name, code, "게이트", reason or "레거시 시간 외", strategy="legacy",
                )
                return False, "gate"
            for db in get_db():
                if not is_strategy_slot_available(settings, db, "legacy", for_new_signal=True):
                    from utils.auto_trade_engine import _count_strategy_slots, effective_legacy_max_slots
                    used = _count_strategy_slots(db, "legacy")
                    lim = effective_legacy_max_slots(settings)
                    self._log_scan_skip(
                        name, code, "게이트", f"레거시 슬롯 포화 ({used}/{lim})",
                        strategy="legacy",
                    )
                    return False, "gate"
                break
            gate_ctx = {}
            gate_ok, gate_reason = await check_entry_gate(self.kiwoom_api, settings, code, price)

        if not gate_ok:
            from utils.auto_trade_engine import (
                classify_breakout_wait_kind,
                is_breakout_watching_reason,
            )
            wait_kind = classify_breakout_wait_kind(gate_reason) if strategy == "breakout" else None
            if strategy == "breakout" and wait_kind and is_breakout_watching_reason(gate_reason):
                watch_meta = {
                    "current_price": price,
                    "change_rate": change_rate,
                    "source": item.get("source", "scanner"),
                    "strategy": "breakout",
                    "gate_pack": "oversold_breakout",
                    "order_ready": False,
                    "wait_kind": wait_kind,
                    "wait_reason": gate_reason,
                    "level_kind": gate_ctx.get("level_kind"),
                    "level_price": gate_ctx.get("level_price"),
                    "breakout_level_price": gate_ctx.get("breakout_level_price")
                    or gate_ctx.get("level_price"),
                    "day_volume": gate_ctx.get("day_volume"),
                    "prev_volume": gate_ctx.get("prev_volume"),
                    "volume_ratio": gate_ctx.get("volume_ratio"),
                    "confirm_close": gate_ctx.get("confirm_close"),
                    "confirm_high": gate_ctx.get("confirm_high"),
                    "entry_soft_streak": gate_ctx.get("entry_soft_streak"),
                    "entry_soft_polls": gate_ctx.get("entry_soft_polls"),
                    "hold_breakout_low": gate_ctx.get("hold_breakout_low"),
                    "ma20": gate_ctx.get("ma20"),
                    "ma20_grace_bars": gate_ctx.get("ma20_grace_bars"),
                    "ma20_grace_slot": gate_ctx.get("ma20_grace_slot"),
                    "ma20_grace_reason": gate_ctx.get("ma20_grace_reason"),
                    "ma20_grace_breakout_level": gate_ctx.get("ma20_grace_breakout_level"),
                    "ma20_grace_inherit_body_ok": gate_ctx.get("ma20_grace_inherit_body_ok"),
                    "ma20_grace_inherit_volume_ok": gate_ctx.get("ma20_grace_inherit_volume_ok"),
                    "ma20_grace_breakout_body_pct": gate_ctx.get("ma20_grace_breakout_body_pct"),
                    "ma20_grace_breakout_day_volume": gate_ctx.get("ma20_grace_breakout_day_volume"),
                    "ma20_grace_breakout_prev_volume": gate_ctx.get("ma20_grace_breakout_prev_volume"),
                }
                wok, wreason = await signal_manager.create_watching_detail(
                    condition_id=AUTO_TRADE_CONDITION_ID,
                    stock_code=code,
                    stock_name=name,
                    signal_type=SignalType.AUTO_TRADE,
                    additional_data=watch_meta,
                )
                if wok:
                    msg = f"관측(WATCHING) [{wait_kind}]: {name}({code}) — {gate_reason}"
                    logger.info(f"📈 [AUTO_SCANNER] {msg}")
                    log_activity("SCANNER", msg, "info", stock_code=code, stock_name=name)
                    return False, "watching"
                self._log_scan_skip(
                    name, code, "관측", f"{gate_reason} · {wreason}", strategy="breakout",
                )
                return False, "gate"
            from utils.ema_fractal import is_fractal_wait_reason, is_fractal_fail_reason
            if strategy == "fractal" and is_fractal_wait_reason(gate_reason):
                watch_meta = {
                    "current_price": price,
                    "change_rate": change_rate,
                    "source": "fractal",
                    "strategy": "fractal",
                    "gate_pack": "ema_fractal_pullback",
                    "order_ready": False,
                    "wait_kind": "fractal_setup",
                    "wait_reason": gate_reason,
                    "fractal_checks": gate_ctx.get("fractal_checks"),
                    "watching_started_at": gate_ctx.get("watching_started_at"),
                }
                wok, wreason = await signal_manager.create_watching_detail(
                    condition_id=AUTO_TRADE_CONDITION_ID,
                    stock_code=code,
                    stock_name=name,
                    signal_type=SignalType.AUTO_TRADE,
                    additional_data=watch_meta,
                )
                if wok:
                    msg = f"관측(WATCHING) [fractal]: {name}({code}) — {gate_reason}"
                    logger.info(f"📈 [AUTO_SCANNER] {msg}")
                    log_activity("SCANNER", msg, "info", stock_code=code, stock_name=name)
                    return False, "watching"
                self._log_scan_skip(
                    name, code, "관측", f"{gate_reason} · {wreason}", strategy="fractal",
                )
                return False, "gate"
            if strategy == "fractal" and is_fractal_fail_reason(gate_reason):
                # 스티키 관측 중 최종 탈락이면 신호 FAILED
                for db in get_db():
                    from utils.auto_trade_engine import parse_signal_meta
                    sig = (
                        db.query(PendingBuySignal)
                        .filter(
                            PendingBuySignal.stock_code == code,
                            PendingBuySignal.status == "WATCHING",
                        )
                        .first()
                    )
                    if sig and parse_signal_meta(sig).get("strategy") == "fractal":
                        sig.status = "FAILED"
                        sig.failure_reason = gate_reason
                        db.commit()
                    break
            if strategy == "ma1592" and str(gate_reason or "").startswith("MA1592 대기"):
                self._log_scan_skip(
                    name, code, "대기", gate_reason, strategy="ma1592",
                )
                return False, "watching"
            self._log_scan_skip(
                name, code, "게이트", gate_reason,
                strategy=strategy if strategy in ("sangtta", "breakout", "fractal", "ma1592") else "",
            )
            return False, "gate"

        ok, signal_reason = await signal_manager.create_signal_detail(
            condition_id=AUTO_TRADE_CONDITION_ID,
            stock_code=code,
            stock_name=name,
            signal_type=SignalType.AUTO_TRADE,
            additional_data={
                "current_price": price,
                "change_rate": change_rate,
                "prev_close": item.get("prev_close"),
                "source": item.get("source", "scanner"),
                "strategy": strategy,
                "gate_pack": (
                    "sangtta_breakout" if strategy == "sangtta"
                    else (
                        "oversold_breakout" if strategy == "breakout"
                        else (
                            "ema_fractal_pullback" if strategy == "fractal"
                            else ("ma1592_hold" if strategy == "ma1592" else "legacy_momentum")
                        )
                    )
                ),
                "stop_price": gate_ctx.get("stop_price"),
                "take_profit_price": gate_ctx.get("take_profit_price"),
                "ema50_at_entry": gate_ctx.get("ema50_at_entry"),
                "fractal_rr": gate_ctx.get("fractal_rr"),
                "fractal_checks": gate_ctx.get("fractal_checks"),
                "prev_high": gate_ctx.get("prev_high"),
                "tp1_price": gate_ctx.get("tp1_price"),
                "tp1_frac": gate_ctx.get("tp1_frac"),
                "suggested_qty": gate_ctx.get("suggested_qty"),
                "qty_tp1": gate_ctx.get("qty_tp1"),
                "planned_qty": gate_ctx.get("planned_qty"),
                "entry_leg": gate_ctx.get("entry_leg") or gate_ctx.get("ma1592_entry_leg"),
                "ma1592_entry_leg": gate_ctx.get("ma1592_entry_leg") or gate_ctx.get("entry_leg"),
                "is_add_buy": bool(gate_ctx.get("is_add_buy")),
                "gc_at": gate_ctx.get("gc_at"),
                "ma15": gate_ctx.get("ma15"),
                "ma92": gate_ctx.get("ma92"),
                "entry_fill": gate_ctx.get("entry_fill"),
                "max_hold_days": gate_ctx.get("max_hold_days"),
                "ma1592_checks": gate_ctx.get("ma1592_checks"),
                "ma1592_scale": gate_ctx.get("ma1592_scale"),
                "reason": gate_ctx.get("reason"),
                "level_kind": gate_ctx.get("level_kind"),
                "level_price": gate_ctx.get("level_price"),
                "breakout_level_price": gate_ctx.get("breakout_level_price") or gate_ctx.get("level_price") or gate_ctx.get("prev_high"),
                "day_volume": gate_ctx.get("day_volume"),
                "prev_volume": gate_ctx.get("prev_volume"),
                "volume_ratio": gate_ctx.get("volume_ratio"),
                "entry_confirm_mode": gate_ctx.get("entry_confirm_mode"),
                "confirm_close": gate_ctx.get("confirm_close"),
                "entry_soft_streak": gate_ctx.get("entry_soft_streak"),
                "entry_soft_polls": gate_ctx.get("entry_soft_polls"),
                "hold_breakout_low": gate_ctx.get("hold_breakout_low"),
                "hold_rsi": gate_ctx.get("hold_rsi"),
                "hold_rsi_prev": gate_ctx.get("hold_rsi_prev"),
                "hold_rsi_cross": gate_ctx.get("hold_rsi_cross"),
            },
        )
        if ok:
            if strategy == "breakout":
                from utils.auto_trade_engine import clear_breakout_entry_state
                clear_breakout_entry_state(code)
            strat_label = {
                "sangtta": "상따",
                "breakout": "돌파",
                "fractal": "프랙탈",
                "ma1592": "MA1592",
            }.get(strategy, "레거시")
            confirm_bit = ""
            if strategy == "breakout" and gate_ctx.get("entry_confirm_mode"):
                confirm_bit = f" 확인={gate_ctx.get('entry_confirm_mode')}"
            msg = (
                f"매수 신호 생성 [{strat_label}]: {name}({code}) "
                f"가격={price:,} 등락={change_rate}%{confirm_bit}"
            )
            logger.info(f"📈 [AUTO_SCANNER] {msg}")
            log_activity("SCANNER", msg, "info", stock_code=code, stock_name=name)
            return True, "signal_ok"
        self._log_scan_skip(
            name, code, "신호", signal_reason,
            strategy=strategy if strategy in ("sangtta", "breakout", "ma1592") else "",
        )
        return False, "signal_fail"

    @staticmethod
    def _log_scan_skip(
        name: str,
        code: str,
        category: str,
        detail: str,
        *,
        strategy: str = "",
    ) -> None:
        label = ""
        if strategy == "sangtta":
            label = "[상따] "
        elif strategy == "breakout":
            label = "[돌파] "
        elif strategy == "jongga":
            label = "[종가배팅] "
        elif strategy == "ma1592":
            label = "[MA1592] "
        elif strategy == "fractal":
            label = "[프랙탈] "
        msg = f"진입 보류 {label}[{category}] {name}({code}): {detail}"
        # 상따/돌파/종가배팅/MA1592은 파일 로그로 추적 (레거시는 대시보드 링버퍼 + debug만 — 노이즈 방지)
        if strategy in ("sangtta", "breakout", "jongga", "ma1592", "fractal"):
            logger.info(f"📈 [AUTO_SCANNER] {msg}")
        else:
            logger.debug(f"📈 [AUTO_SCANNER] {msg}")
        log_activity(
            "SCANNER",
            msg,
            "warn",
            stock_code=code,
            stock_name=name,
            skip_category=category,
            skip_reason=detail,
            strategy=strategy or None,
        )

    async def _record_jongga_auto_miss(
        self,
        st: Dict,
        *,
        reason: str,
        code: str = "",
        name: str = "",
        auto: Optional[Dict] = None,
        save_jongga_state=None,
    ) -> None:
        """pick_end 이후 1차 자동매수 미실행 — 체결 로그(FAILED) + 활동 로그에 사유 1회 기록."""
        if st.get("auto_miss_logged") or st.get("auto_fired") or st.get("picked_code"):
            return
        reason = (reason or "사유 미기록").strip()[:255] or "사유 미기록"
        auto = auto or {}
        code = KiwoomAPI.normalize_stock_code(
            code or auto.get("stock_code") or st.get("auto_pick_code") or ""
        ) or "JONGGA"
        name = (
            name
            or auto.get("stock_name")
            or (None if code == "JONGGA" else code)
            or "종가배팅"
        )
        st["auto_miss_logged"] = True
        st["auto_miss_reason"] = reason
        st["last_auto_skip_reason"] = reason
        st["status"] = "auto_miss"
        if code and code != "JONGGA":
            st["auto_pick_code"] = code
        if save_jongga_state:
            save_jongga_state(st)

        prefix = f"종가배팅 자동매수 미실행: {name}({code}) · {reason}"
        logger.warning(f"📈 [AUTO_SCANNER] {prefix}")
        log_activity("BUY", prefix, "warn", stock_code=code, strategy="jongga")

        meta = {
            "source": "jongga",
            "strategy": "jongga",
            "gate_pack": "jongga_closing",
            "jongga_mode": "auto_miss",
            "theme": auto.get("theme"),
            "pullback_pct": auto.get("pullback_pct"),
            "score": auto.get("score"),
            "entry_leg": 1,
            "jongga_entry_leg": 1,
            "order_ready": False,
        }
        try:
            ok, detail = await signal_manager.record_failed_signal(
                condition_id=AUTO_TRADE_CONDITION_ID,
                stock_code=code,
                stock_name=name,
                signal_type=SignalType.STRATEGY,
                failure_reason=f"종가배팅 자동매수 미실행: {reason}",
                additional_data=meta,
            )
            if not ok:
                logger.info(
                    f"📈 [AUTO_SCANNER] 종가배팅 실패 이력 DB 스킵 ({detail})"
                )
        except Exception as e:
            logger.warning(f"📈 [AUTO_SCANNER] 종가배팅 실패 이력 저장 오류: {e}")

        try:
            from notifications.trade_alert import (
                is_buy_slot_capacity_reason,
                notify_buy_slot_blocked_async,
            )
            if is_buy_slot_capacity_reason(reason):
                await notify_buy_slot_blocked_async(
                    stock_name=name,
                    stock_code=code,
                    reason=reason,
                    strategy="jongga",
                )
        except Exception as e:
            logger.warning(f"📈 [AUTO_SCANNER] 종가배팅 슬롯 알림 오류: {e}")

    async def _scan_jongga_session(self, settings: AutoTradeSettings) -> int:
        """종가배팅: 14:30 후보 구축 → 미선택 시 pick_end 이후 자동매수 1건."""
        if not getattr(settings, "use_jongga", False):
            return 0

        from utils.auto_trade_engine import (
            allows_strategy_new_buy,
            is_strategy_slot_available,
            _count_strategy_slots,
            effective_jongga_max_slots,
        )
        from utils.jongga_engine import (
            GATE_PACK,
            STRATEGY_KEY,
            build_session_payload_async,
            ensure_leg_state,
            mark_leg,
            past_pick_end,
            pig_split_enabled,
            save_jongga_state,
            today_state_or_empty,
        )
        from utils.theme_map_store import get_latest_map_by_codes

        # 선택 창 전이라도 상태 갱신은 스킵; 자동매수는 pick_end 이후 grace 포함
        allowed, allow_reason = allows_strategy_new_buy(settings, STRATEGY_KEY)
        in_window = allowed
        after_pick = past_pick_end(settings)
        if not in_window and not after_pick:
            return 0

        st = today_state_or_empty()
        # 이미 1차 신호/선택 완료·미실행 기록 완료면 세션(1차)은 스킵 — 2·3차는 _scan_jongga_pig_legs
        if st.get("picked_code") or st.get("auto_fired") or st.get("auto_miss_logged") or st.get("status") in (
            "picked", "auto", "done", "leg1", "leg2", "leg3", "leg3_skip", "auto_miss",
        ):
            return 0

        # 후보가 없거나 오래된 경우 재구축 (슬롯 포화여도 후보·알림은 유지)
        need_build = not (st.get("candidates") or [])
        if need_build or st.get("status") in ("idle", None):
            try:
                limit = max(1, int(getattr(settings, "jongga_rank_limit", None) or 50))
            except (TypeError, ValueError):
                limit = 50
            res = await self.kiwoom_api.get_volume_rank(
                market="000",
                sort_tp="3",
                limit=limit,
                screener_filters=True,
            )
            if not res.get("success"):
                err = res.get("error") or "거래대금순 조회 실패"
                logger.warning(f"📈 [AUTO_SCANNER] 종가배팅 거래대금순 실패: {err}")
                if after_pick:
                    st["last_auto_skip_reason"] = f"거래대금순 실패: {err}"
                    if not allowed:
                        await self._record_jongga_auto_miss(
                            st,
                            reason=st["last_auto_skip_reason"],
                            save_jongga_state=save_jongga_state,
                        )
                return 0
            items = (res.get("items") or [])[:limit]
            codes = [
                KiwoomAPI.normalize_stock_code(it.get("stock_code", ""))
                for it in items
                if it.get("stock_code")
            ]
            theme_map: Dict = {}
            for db in get_db():
                theme_map = get_latest_map_by_codes(codes, session=db) or {}
                break

            # ka10030에 고가 없음 → 최강테마 후보만 일봉으로 눌림 산출
            payload = await build_session_payload_async(
                self.kiwoom_api,
                items=items,
                theme_map=theme_map,
                w_pullback=float(getattr(settings, "jongga_w_pullback", 1.0) or 1.0),
                w_amount=float(getattr(settings, "jongga_w_amount", 1.0) or 1.0),
                w_change=float(getattr(settings, "jongga_w_change", 1.0) or 1.0),
            )
            # 재구축 시에도 당일 스킵 사유 보존
            for k in ("last_auto_skip_reason", "telegram_notified", "telegram_notified_ok"):
                if st.get(k) is not None and k not in payload:
                    payload[k] = st.get(k)
            save_jongga_state(payload)
            st = payload
            theme = st.get("strongest_theme") or "?"
            n = len(st.get("candidates") or [])
            msg = f"종가배팅 후보 {n}개 · 최강테마 {theme}"
            logger.info(f"📈 [AUTO_SCANNER] {msg}")
            log_activity("SCANNER", msg, "info")
            self._log_strategy_candidates("종가배팅", st.get("candidates") or [])
            if not st.get("telegram_notified"):
                try:
                    from notifications.jongga_candidates_notify import (
                        notify_jongga_candidates_async,
                    )
                    ok = await notify_jongga_candidates_async(st)
                    st["telegram_notified"] = True
                    st["telegram_notified_ok"] = bool(ok)
                    save_jongga_state(st)
                    if ok:
                        log_activity("SCANNER", "종가배팅 후보 텔레그램 전송", "info")
                except Exception as e:
                    logger.warning(f"📈 [AUTO_SCANNER] 종가배팅 텔레그램 실패: {e}")
                    st["telegram_notified"] = True  # 스팸 방지 — 같은 세션 재시도 안 함
                    save_jongga_state(st)

        # 사용자 선택 대기 — pick_end 이전이면 신호 생성 안 함
        if not after_pick:
            return 0

        auto = st.get("auto_pick") or ((st.get("candidates") or [None])[0])
        code = ""
        name = ""
        if auto:
            code = KiwoomAPI.normalize_stock_code(auto.get("stock_code", ""))
            name = auto.get("stock_name") or code

        # 매수 창 종료(grace 포함) — 마지막 스킵 사유로 미실행 확정
        if not allowed:
            reason = (
                st.get("last_auto_skip_reason")
                or allow_reason
                or "종가배팅 매수 시간 종료"
            )
            await self._record_jongga_auto_miss(
                st,
                reason=reason,
                code=code,
                name=name,
                auto=auto,
                save_jongga_state=save_jongga_state,
            )
            return 0

        slot_ok = True
        used = lim = 0
        for db in get_db():
            slot_ok = is_strategy_slot_available(
                settings, db, STRATEGY_KEY, for_new_signal=True
            )
            if not slot_ok:
                used = _count_strategy_slots(db, STRATEGY_KEY)
                lim = effective_jongga_max_slots(settings)
            break
        if not slot_ok:
            reason = f"종가배팅 슬롯 포화 ({used}/{lim})"
            await self._record_jongga_auto_miss(
                st,
                reason=reason,
                code=code,
                name=name,
                auto=auto,
                save_jongga_state=save_jongga_state,
            )
            return 0

        if not auto or not code:
            await self._record_jongga_auto_miss(
                st,
                reason="자동매수 후보 없음",
                save_jongga_state=save_jongga_state,
            )
            return 0

        if await self._has_open_interest(code):
            await self._record_jongga_auto_miss(
                st,
                reason="이미 보유/매수대기",
                code=code,
                name=name,
                auto=auto,
                save_jongga_state=save_jongga_state,
            )
            return 0

        try:
            price = int(auto.get("current_price") or 0)
        except (TypeError, ValueError):
            price = 0
        if price <= 0:
            price = await self.kiwoom_api.get_current_price(code) or 0
        if not price:
            st["last_auto_skip_reason"] = "현재가 없음"
            save_jongga_state(st)
            self._log_scan_skip(name, code, "시세", "현재가 없음", strategy=STRATEGY_KEY)
            return 0

        cand_codes = [
            KiwoomAPI.normalize_stock_code(c.get("stock_code", ""))
            for c in (st.get("candidates") or [])
        ]
        gate_ctx = {
            "jongga_candidate_codes": cand_codes,
            "theme": auto.get("theme"),
            "pullback_pct": auto.get("pullback_pct"),
            "score": auto.get("score"),
            "entry_leg": 1,
        }
        gate_ok, gate_reason = await evaluate_gate_pack(
            self.kiwoom_api,
            settings,
            GATE_PACK,
            code,
            int(price),
            change_rate=auto.get("change_rate"),
            ctx=gate_ctx,
            skip_time_check=True,
        )
        if not gate_ok:
            st["last_auto_skip_reason"] = f"게이트: {gate_reason}"
            save_jongga_state(st)
            self._log_scan_skip(name, code, "게이트", gate_reason, strategy=STRATEGY_KEY)
            return 0

        split_on = pig_split_enabled(settings)
        ok, msg = await signal_manager.create_signal_detail(
            condition_id=AUTO_TRADE_CONDITION_ID,
            stock_code=code,
            stock_name=name,
            signal_type=SignalType.STRATEGY,
            additional_data={
                "current_price": int(price),
                "change_rate": auto.get("change_rate"),
                "source": STRATEGY_KEY,
                "strategy": STRATEGY_KEY,
                "gate_pack": GATE_PACK,
                "theme": auto.get("theme"),
                "pullback_pct": auto.get("pullback_pct"),
                "trade_amount": auto.get("trade_amount"),
                "score": auto.get("score"),
                "jongga_mode": "auto",
                "entry_leg": 1,
                "jongga_entry_leg": 1,
                "jongga_pig_split": split_on,
                "order_ready": True,
            },
        )
        if ok:
            st["auto_fired"] = True
            st["picked_code"] = code
            st["status"] = "leg1" if split_on else "auto"
            st.pop("last_auto_skip_reason", None)
            ensure_leg_state(st)
            mark_leg(st, 1, done=True, reason="auto_signal")
            save_jongga_state(st)
            info = (
                f"종가배팅 자동매수 신호(1차): {name}({code}) "
                f"테마={auto.get('theme')} 스코어={auto.get('score')}"
                + (" · 돼지물량분할" if split_on else "")
            )
            logger.info(f"📈 [AUTO_SCANNER] {info}")
            log_activity("SCANNER", info, "info", stock_code=code)
            return 1

        # 이미 대기/체결 등 확정 사유는 미실행으로 남기고, 그 외는 재시도
        definitive = any(
            k in (msg or "")
            for k in ("이미 ", "중복 신호", "DB 저장 실패")
        )
        st["last_auto_skip_reason"] = f"신호 생성 실패: {msg}"
        save_jongga_state(st)
        logger.warning(f"📈 [AUTO_SCANNER] 종가배팅 신호 실패: {msg}")
        if definitive:
            await self._record_jongga_auto_miss(
                st,
                reason=st["last_auto_skip_reason"],
                code=code,
                name=name,
                auto=auto,
                save_jongga_state=save_jongga_state,
            )
        return 0

    async def _scan_jongga_open_avg_down(self, settings: AutoTradeSettings) -> int:
        """전일 종가배팅 · 2차 미실행: 시초 갭(−avg_down%) 또는 손절 직전 2차 물타기."""
        if not getattr(settings, "use_jongga", False):
            return 0
        from utils.jongga_engine import (
            DEFAULT_AVG_DOWN_PCT,
            DEFAULT_STOP_LOSS_PCT,
            GATE_PACK,
            STRATEGY_KEY,
            in_open_avg_down_window,
            is_jongga_leg2_fill_note,
            is_jongga_open_avg_down_day,
            jongga_pct_stop_price,
            leg2_done_in_state,
            mark_open_avg_down,
            open_avg_down_done_in_state,
            open_avg_down_price_ok,
            pig_split_enabled,
            prev_session_state,
        )

        if not pig_split_enabled(settings):
            return 0

        prev = prev_session_state()
        if open_avg_down_done_in_state(prev):
            return 0

        in_open = in_open_avg_down_window()
        drop_pct = getattr(settings, "jongga_avg_down_pct", None)
        if drop_pct is None:
            drop_pct = DEFAULT_AVG_DOWN_PCT

        positions: List[Position] = []
        fills_by_pos: Dict[int, List[PositionBuyFill]] = {}
        for db in get_db():
            positions = (
                db.query(Position)
                .filter(
                    Position.status == "HOLDING",
                    Position.strategy_key == STRATEGY_KEY,
                )
                .all()
            )
            if positions:
                ids = [p.id for p in positions]
                fill_rows = (
                    db.query(PositionBuyFill)
                    .filter(PositionBuyFill.position_id.in_(ids))
                    .all()
                )
                for row in fill_rows:
                    fills_by_pos.setdefault(int(row.position_id), []).append(row)
            break

        created = 0
        for pos in positions:
            if not is_jongga_open_avg_down_day(getattr(pos, "buy_time", None)):
                continue
            code = KiwoomAPI.normalize_stock_code(pos.stock_code or "")
            if not code:
                continue
            if any(
                is_jongga_leg2_fill_note(getattr(f, "note", None))
                for f in fills_by_pos.get(int(pos.id), [])
            ):
                continue
            picked = KiwoomAPI.normalize_stock_code(prev.get("picked_code") or "")
            if picked and picked == code and leg2_done_in_state(prev):
                continue
            if await self._has_pending_signal_only(code):
                continue
            if await self._in_cooldown(code, min(60, int(settings.reorder_cooldown_sec or 300))):
                continue

            price = await self.kiwoom_api.get_current_price(code) or 0
            if not price:
                continue
            buy_px = int(getattr(pos, "buy_price", None) or 0)
            stored_stop = int(getattr(pos, "stop_loss_price", None) or 0)
            sl_pct = getattr(settings, "jongga_stop_loss_pct", None)
            if sl_pct is None:
                sl_pct = DEFAULT_STOP_LOSS_PCT
            calc_stop = jongga_pct_stop_price(buy_px, sl_pct) or 0
            stop_px = stored_stop if stored_stop > 0 else calc_stop
            ok_px, detail = open_avg_down_price_ok(
                buy_px,
                float(price),
                stop_px,
                drop_pct,
                in_open_window=in_open,
            )
            if not ok_px:
                continue

            name = pos.stock_name or code
            ok, msg = await signal_manager.create_signal_detail(
                condition_id=AUTO_TRADE_CONDITION_ID,
                stock_code=code,
                stock_name=name,
                signal_type=SignalType.STRATEGY,
                additional_data={
                    "current_price": int(price),
                    "source": "jongga_open_avg_down",
                    "strategy": STRATEGY_KEY,
                    "gate_pack": GATE_PACK,
                    "is_add_buy": True,
                    "entry_leg": 2,
                    "jongga_entry_leg": 2,
                    "jongga_pig_split": True,
                    "avg_down": True,
                    "open_avg_down": True,
                    "avg_down_pct": float(drop_pct),
                    "avg_down_buy_price": buy_px,
                    "avg_down_detail": detail,
                    "order_ready": True,
                },
            )
            if ok:
                if prev:
                    mark_open_avg_down(prev, done=True, reason=detail)
                label = "시초 물타기" if in_open else "손절 전 물타기"
                info = f"종가배팅 {label}(2차) 신호: {name}({code}) · {detail}"
                logger.info(f"📈 [AUTO_SCANNER] {info}")
                log_activity("SCANNER", info, "info", stock_code=code)
                created += 1
            else:
                logger.warning(f"📈 [AUTO_SCANNER] 종가배팅 손절 전 물타기 실패: {msg}")
        return created

    async def _scan_jongga_pig_legs(self, settings: AutoTradeSettings) -> int:
        """종가배팅 분할 2차(물타기)·3차(동시호가 호가벽) 추가매수."""
        if not getattr(settings, "use_jongga", False):
            return 0
        from utils.jongga_engine import (
            DEFAULT_AVG_DOWN_PCT,
            DEFAULT_LEG2_START,
            DEFAULT_LEG3_END,
            DEFAULT_LEG3_START,
            DEFAULT_PIG_LEVELS,
            DEFAULT_PIG_RATIO,
            GATE_PACK,
            STRATEGY_KEY,
            avg_down_ok,
            ensure_leg_state,
            in_hm_window,
            mark_leg,
            past_hm,
            pig_orderbook_verdict,
            pig_split_enabled,
            today_state_or_empty,
        )

        if not pig_split_enabled(settings):
            return 0

        st = today_state_or_empty()
        code = KiwoomAPI.normalize_stock_code(st.get("picked_code") or "")
        if not code:
            return 0
        ensure_leg_state(st)
        legs = st["legs"]

        # 1차 체결(HOLDING) 대기
        pos = None
        for db in get_db():
            pos = (
                db.query(Position)
                .filter(
                    Position.stock_code == code,
                    Position.status == "HOLDING",
                    Position.strategy_key == STRATEGY_KEY,
                )
                .first()
            )
            break
        if not pos:
            return 0

        name = pos.stock_name or code
        if await self._has_pending_signal_only(code):
            return 0
        if await self._in_cooldown(code, min(60, int(settings.reorder_cooldown_sec or 300))):
            return 0

        created = 0
        leg2_start = getattr(settings, "jongga_leg2_start_time", None) or DEFAULT_LEG2_START
        leg3_start = getattr(settings, "jongga_leg3_start_time", None) or DEFAULT_LEG3_START
        leg3_end = getattr(settings, "jongga_leg3_end_time", None) or DEFAULT_LEG3_END

        # ----- 2차 -----
        if not legs["2"].get("done") and not legs["2"].get("skipped"):
            if past_hm(leg2_start, DEFAULT_LEG2_START):
                # 3차 창에 들어가면 2차 조건 미충족 시 스킵 처리
                force_skip = past_hm(leg3_start, DEFAULT_LEG3_START)
                price = await self.kiwoom_api.get_current_price(code) or 0
                if not price:
                    if force_skip:
                        mark_leg(st, 2, skipped=True, reason="현재가 없음")
                    return created

                ok2 = True
                reasons: List[str] = []

                # 물타기: 평단 대비 −N% (기본 2%)
                buy_px = int(getattr(pos, "buy_price", None) or 0)
                drop_pct = getattr(settings, "jongga_avg_down_pct", None)
                if drop_pct is None:
                    drop_pct = DEFAULT_AVG_DOWN_PCT
                avg_ok, avg_msg = avg_down_ok(buy_px, float(price), drop_pct)
                reasons.append(avg_msg)
                if not avg_ok:
                    ok2 = False

                if ok2:
                    ok, msg = await signal_manager.create_signal_detail(
                        condition_id=AUTO_TRADE_CONDITION_ID,
                        stock_code=code,
                        stock_name=name,
                        signal_type=SignalType.STRATEGY,
                        additional_data={
                            "current_price": int(price),
                            "source": "jongga_leg2",
                            "strategy": STRATEGY_KEY,
                            "gate_pack": GATE_PACK,
                            "is_add_buy": True,
                            "entry_leg": 2,
                            "jongga_entry_leg": 2,
                            "jongga_pig_split": True,
                            "avg_down": True,
                            "avg_down_pct": float(drop_pct),
                            "avg_down_buy_price": buy_px,
                            "avg_down_detail": avg_msg,
                            "order_ready": True,
                        },
                    )
                    if ok:
                        mark_leg(st, 2, done=True, reason="; ".join(reasons))
                        st["status"] = "leg2"
                        from utils.jongga_engine import save_jongga_state
                        save_jongga_state(st)
                        info = f"종가배팅 2차 신호: {name}({code}) · {'; '.join(reasons)}"
                        logger.info(f"📈 [AUTO_SCANNER] {info}")
                        log_activity("SCANNER", info, "info", stock_code=code)
                        created += 1
                        return created
                    logger.warning(f"📈 [AUTO_SCANNER] 종가배팅 2차 신호 실패: {msg}")
                elif force_skip:
                    reason = "; ".join(reasons) or "2차 조건 미충족"
                    mark_leg(st, 2, skipped=True, reason=reason)
                    skip_msg = f"종가배팅 2차 스킵: {name}({code}) · {reason}"
                    log_activity("SCANNER", skip_msg, "warn", stock_code=code)
                    # 1차 FILLED와 동일 종목이라 FAILED 행을 못 남김 → BUY 활동로그로 이력 보존
                    log_activity("BUY", skip_msg, "warn", stock_code=code, strategy="jongga")
                else:
                    logger.debug(
                        f"📈 [AUTO_SCANNER] 종가배팅 2차 대기 {name}: {'; '.join(reasons)}"
                    )
            return created

        # ----- 3차 (동시호가 돼지) -----
        if legs["3"].get("done") or legs["3"].get("skipped"):
            return created
        if not (legs["2"].get("done") or legs["2"].get("skipped")):
            return created

        in_auction = in_hm_window(
            leg3_start,
            leg3_end,
            default_start=DEFAULT_LEG3_START,
            default_end=DEFAULT_LEG3_END,
        )
        past_auction = past_hm(leg3_end, DEFAULT_LEG3_END)
        if not in_auction and not past_auction:
            return created
        if past_auction and not in_auction:
            mark_leg(st, 3, skipped=True, reason="동시호가 창 종료")
            st["status"] = "leg3_skip"
            from utils.jongga_engine import save_jongga_state
            save_jongga_state(st)
            skip_msg = f"종가배팅 3차 스킵: {name}({code}) · 동시호가 창 종료"
            log_activity("SCANNER", skip_msg, "warn", stock_code=code)
            log_activity("BUY", skip_msg, "warn", stock_code=code, strategy="jongga")
            return created

        price = await self.kiwoom_api.get_current_price(code) or 0
        if not price:
            return created

        try:
            snap = await self.kiwoom_api.get_stock_snapshot(code)
            snap_data = snap.get("snapshot") or {}
            if snap.get("success") and snap_data.get("orderbook_live"):
                orderbook = snap_data.get("orderbook") or []
            else:
                orderbook = []
                warn = (snap_data.get("warnings") or []) if snap.get("success") else [snap.get("error")]
                logger.warning(
                    f"📈 [AUTO_SCANNER] 종가배팅 3차 호가 비어있음 {code}: {warn}"
                )
        except Exception as e:
            logger.warning(f"📈 [AUTO_SCANNER] 종가배팅 3차 호가 실패 {code}: {e}")
            orderbook = []

        try:
            min_ratio = float(getattr(settings, "jongga_pig_bid_ask_ratio", None) or DEFAULT_PIG_RATIO)
        except (TypeError, ValueError):
            min_ratio = DEFAULT_PIG_RATIO
        try:
            levels = int(getattr(settings, "jongga_pig_levels", None) or DEFAULT_PIG_LEVELS)
        except (TypeError, ValueError):
            levels = DEFAULT_PIG_LEVELS

        verdict, detail = pig_orderbook_verdict(
            orderbook, levels=levels, min_ratio=min_ratio
        )
        ratio_s = detail.get("ratio")
        ratio_txt = f"{ratio_s:.2f}" if isinstance(ratio_s, (int, float)) else str(ratio_s)
        detail_msg = (
            f"bid={detail.get('bid_qty')} ask={detail.get('ask_qty')} ratio={ratio_txt}"
        )

        if verdict == "buy":
            ok, msg = await signal_manager.create_signal_detail(
                condition_id=AUTO_TRADE_CONDITION_ID,
                stock_code=code,
                stock_name=name,
                signal_type=SignalType.STRATEGY,
                additional_data={
                    "current_price": int(price),
                    "source": "jongga_leg3",
                    "strategy": STRATEGY_KEY,
                    "gate_pack": GATE_PACK,
                    "is_add_buy": True,
                    "entry_leg": 3,
                    "jongga_entry_leg": 3,
                    "jongga_pig_split": True,
                    "pig_verdict": verdict,
                    "pig_detail": detail,
                    "order_ready": True,
                },
            )
            if ok:
                mark_leg(st, 3, done=True, reason=f"돼지매수 {detail_msg}")
                st["status"] = "leg3"
                from utils.jongga_engine import save_jongga_state
                save_jongga_state(st)
                info = f"종가배팅 3차(돼지) 신호: {name}({code}) · {detail_msg}"
                logger.info(f"📈 [AUTO_SCANNER] {info}")
                log_activity("SCANNER", info, "info", stock_code=code)
                created += 1
            else:
                logger.warning(f"📈 [AUTO_SCANNER] 종가배팅 3차 신호 실패: {msg}")
        elif verdict == "sell":
            mark_leg(st, 3, skipped=True, reason=f"매도벽 {detail_msg}")
            st["status"] = "leg3_skip"
            from utils.jongga_engine import save_jongga_state
            save_jongga_state(st)
            skip_msg = f"종가배팅 3차 스킵(매도벽): {name}({code}) · {detail_msg}"
            log_activity("SCANNER", skip_msg, "warn", stock_code=code)
            log_activity("BUY", skip_msg, "warn", stock_code=code, strategy="jongga")
        else:
            logger.debug(
                f"📈 [AUTO_SCANNER] 종가배팅 3차 중립 대기 {name}: {detail_msg}"
            )
        return created

    async def _scan_ma1592_scale_legs(self, settings: AutoTradeSettings) -> int:
        """MA1592 2·3차 분할 추가매수 (15분 이격 → 15선 눌림)."""
        if not getattr(settings, "use_ma1592", False):
            return 0
        from utils.auto_trade_engine import evaluate_gate_pack
        from utils.ma1592 import get_universe_store, params_from_settings

        p = params_from_settings(settings)
        if str(p.get("hold_mode") or "") != "scale_in_gc":
            return 0

        store = get_universe_store()
        created = 0
        for code in list(store.manage_codes()):
            code = KiwoomAPI.normalize_stock_code(code)
            urec = store.get(code)
            if not urec or int(urec.entry_leg or 0) < 1 or int(urec.entry_leg or 0) >= 3:
                continue
            if await self._has_pending_signal_only(code):
                continue
            if await self._in_cooldown(code, min(60, int(settings.reorder_cooldown_sec or 300))):
                continue

            pos = None
            for db in get_db():
                pos = (
                    db.query(Position)
                    .filter(
                        Position.stock_code == code,
                        Position.status == "HOLDING",
                        Position.strategy_key == "ma1592",
                    )
                    .first()
                )
                break
            if not pos:
                continue

            price = await self.kiwoom_api.get_current_price(code) or int(pos.buy_price or 0)
            if not price:
                continue
            name = pos.stock_name or urec.stock_name or code
            gate_ctx: Dict[str, Any] = {
                "stock_name": name,
                "equity": None,
            }
            gate_ok, gate_reason = await evaluate_gate_pack(
                self.kiwoom_api,
                settings,
                "ma1592_scale",
                code,
                int(price),
                ctx=gate_ctx,
                skip_time_check=True,
            )
            if not gate_ok:
                logger.debug(f"📈 [AUTO_SCANNER] MA1592 분할 대기 {name}: {gate_reason}")
                continue

            entry_leg = int(gate_ctx.get("entry_leg") or gate_ctx.get("ma1592_entry_leg") or 2)
            ok, msg = await signal_manager.create_signal_detail(
                condition_id=AUTO_TRADE_CONDITION_ID,
                stock_code=code,
                stock_name=name,
                signal_type=SignalType.STRATEGY,
                additional_data={
                    "current_price": int(price),
                    "source": f"ma1592_leg{entry_leg}",
                    "strategy": "ma1592",
                    "gate_pack": "ma1592_scale",
                    "is_add_buy": True,
                    "entry_leg": entry_leg,
                    "ma1592_entry_leg": entry_leg,
                    "suggested_qty": gate_ctx.get("suggested_qty"),
                    "planned_qty": gate_ctx.get("planned_qty") or urec.planned_qty,
                    "stop_price": gate_ctx.get("stop_price"),
                    "take_profit_price": gate_ctx.get("take_profit_price") or gate_ctx.get("tp1_price"),
                    "tp1_price": gate_ctx.get("tp1_price"),
                    "tp1_frac": gate_ctx.get("tp1_frac"),
                    "prev_high": gate_ctx.get("prev_high"),
                    "ma15": gate_ctx.get("ma15"),
                    "ma92": gate_ctx.get("ma92"),
                    "reason": gate_ctx.get("reason"),
                    "ma1592_scale": gate_ctx.get("ma1592_scale"),
                    "order_ready": True,
                },
            )
            if ok:
                info = (
                    f"MA1592 {entry_leg}차 신호: {name}({code}) · "
                    f"{gate_ctx.get('reason') or gate_reason}"
                )
                logger.info(f"📈 [AUTO_SCANNER] {info}")
                log_activity("SCANNER", info, "info", stock_code=code)
                created += 1
            else:
                logger.warning(f"📈 [AUTO_SCANNER] MA1592 {entry_leg}차 신호 실패: {msg}")
        return created

    async def _scan_pyramiding_adds(self, settings: AutoTradeSettings) -> int:
        """보유 종목 추가매수(피라미딩) 신호."""
        if (settings.sizing_method or "FIXED").upper() != "PYRAMIDING":
            return 0
        trigger = settings.add_buy_trigger
        if trigger is None or not settings.add_buy_amount:
            return 0

        created = 0
        positions = []
        for db in get_db():
            positions = db.query(Position).filter(Position.status == "HOLDING").all()
            break

        for pos in positions:
            if await self._in_cooldown(pos.stock_code, settings.reorder_cooldown_sec or 300):
                continue
            pending = await self._has_pending_signal_only(pos.stock_code)
            if pending:
                continue

            price = await self.kiwoom_api.get_current_price(pos.stock_code)
            if not price or not pos.buy_price:
                continue
            profit_rate = (price - pos.buy_price) / pos.buy_price * 100
            if profit_rate < float(trigger):
                continue

            ok = await signal_manager.create_signal(
                condition_id=AUTO_TRADE_CONDITION_ID,
                stock_code=pos.stock_code,
                stock_name=pos.stock_name,
                signal_type=SignalType.AUTO_TRADE,
                additional_data={
                    "current_price": price,
                    "change_rate": profit_rate,
                    "source": "pyramiding_add",
                    "is_add_buy": True,
                    "strategy": getattr(pos, "strategy_key", None) or "legacy",
                },
            )
            if ok:
                created += 1
                msg = f"추가매수 신호: {pos.stock_name} 수익률={profit_rate:.2f}%"
                logger.info(f"📈 [AUTO_SCANNER] {msg}")
                log_activity("SCANNER", msg, "info", stock_code=pos.stock_code)
            await asyncio.sleep(2)
        return created

    async def _has_pending_signal_only(self, stock_code: str) -> bool:
        for db in get_db():
            return db.query(PendingBuySignal).filter(
                PendingBuySignal.stock_code == stock_code,
                PendingBuySignal.status.in_(["PENDING", "PROCESSING", "ORDERED"]),
            ).first() is not None
        return False

    async def _has_open_interest(self, stock_code: str) -> bool:
        for db in get_db():
            session: Session = db
            holding = session.query(Position).filter(
                Position.stock_code == stock_code,
                Position.status == "HOLDING",
            ).first()
            if holding:
                return True
            pending = session.query(PendingBuySignal).filter(
                PendingBuySignal.stock_code == stock_code,
                PendingBuySignal.status.in_(["PENDING", "PROCESSING", "ORDERED"]),
            ).first()
            if pending:
                return True
        return False

    async def _in_cooldown(self, stock_code: str, cooldown_sec: int) -> bool:
        """재주문 쿨다운. WATCHING은 관측만이라 쿨다운에 넣지 않음."""
        cutoff = utc_now_naive() - timedelta(seconds=cooldown_sec)
        for db in get_db():
            session: Session = db
            recent = session.query(PendingBuySignal).filter(
                PendingBuySignal.stock_code == stock_code,
                PendingBuySignal.detected_at >= cutoff,
                PendingBuySignal.status != "WATCHING",
            ).first()
            return recent is not None
        return False

    async def _daily_buy_limit_reached(self, settings: AutoTradeSettings) -> bool:
        limit = int(settings.max_daily_buys or 0)
        if limit <= 0:
            return False
        start = kst_day_start_utc_naive()
        count = 0
        for db in get_db():
            session: Session = db
            count = session.query(PendingBuySignal).filter(
                PendingBuySignal.detected_at >= start,
                PendingBuySignal.status.in_(["ORDERED", "PROCESSING", "PENDING"]),
            ).count()
            break
        return count >= limit

    async def _max_positions_reached(self, settings: AutoTradeSettings) -> bool:
        from utils.auto_trade_engine import (
            count_open_position_slots,
            describe_open_position_slots,
            is_max_concurrent_positions_reached,
            prune_stale_buy_slot_reservations,
        )

        for db in get_db():
            session: Session = db
            pruned = prune_stale_buy_slot_reservations(session)
            if pruned:
                session.commit()
                logger.info(f"📈 [AUTO_SCANNER] 만료 매수 신호 {pruned}건 정리")
            if is_max_concurrent_positions_reached(settings, session, for_new_signal=True):
                detail = describe_open_position_slots(session)
                limit = int(settings.max_concurrent_positions or 0)
                logger.debug(
                    f"📈 [AUTO_SCANNER] 동시 보유 슬롯 {detail['total']}/{limit} "
                    f"(보유 {detail['holdings']} + 대기 {detail['reserved']})"
                )
                return True
            break
        return False


auto_trade_scanner = AutoTradeScanner()
