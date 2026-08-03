"""대시보드 설정 기반 자동매매 스캐너 (KIS 스타일).

관심종목 + 스크리너(거래량/대금 상위) 후보를 주기적으로 점검하고,
매수 조건을 만족하면 PendingBuySignal을 생성한다.
조건식(CNSRREQ) 주기 검색과는 별개이며, 자동매매 ON일 때만 동작한다.
"""
import asyncio
import logging
import os
from collections import defaultdict
from datetime import datetime, time as dt_time, timedelta
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from api.kiwoom_api import KiwoomAPI
from core.config import Config
from core.models import AutoTradeSettings, PendingBuySignal, Position, get_db
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

_STRATEGY_SUMMARY_ORDER = ("legacy", "sangtta", "breakout", "ymgp", "jongga")
_STRATEGY_SUMMARY_LABELS = {
    "legacy": "거래대금 눌림목",
    "sangtta": "상따",
    "breakout": "수급 돌파",
    "ymgp": "역매공파",
    "jongga": "종가배팅",
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
    if src == "ymgp":
        return "ymgp"
    if src == "jongga":
        return "jongga"
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
        try:
            return await self._scan_once_inner(settings)
        finally:
            mark_scan_end()

    async def _scan_once_inner(self, settings: AutoTradeSettings) -> tuple:
        if not has_buy_conditions(settings):
            msg = "매수 조건 미설정 — 스캔 건너뜀"
            logger.info(f"📈 [AUTO_SCANNER] {msg}")
            log_activity("SCANNER", msg, "warn")
            return 0, 0

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
            # 종가배팅은 전역 슬롯과 별도 — 후보 수집 전에 먼저 시도
            created = 0
            add_created = 0
            created_by: Dict[str, int] = defaultdict(int)
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

        targets = await self._collect_targets(settings)
        if not targets:
            logger.debug("📈 [AUTO_SCANNER] 스캔 대상 없음")
            log_activity("SCANNER", "스캔 대상 0개 (관심종목·스크리너 조건 확인)", "info")
        if targets and os.getenv("SCANNER_DISPARITY_LOG", "").lower() in ("1", "true", "yes"):
            await self._log_disparity_observations(targets)

        targets_by = _count_targets_by_strategy(targets or [])
        gate_pause = 6 if settings.use_entry_gate else 2
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
                ok, reason = await self._evaluate_and_signal(settings, item)
                stats[reason] += 1
                stats_by[sk][reason] += 1
                scanned += 1
                if ok:
                    created += 1
                    created_by[sk] += 1
                if reason in _THROTTLE_REASONS:
                    await asyncio.sleep(gate_pause)
                else:
                    await asyncio.sleep(0)

            add_created_pyr = await self._scan_pyramiding_adds(settings)
            if add_created_pyr:
                created += add_created_pyr
                add_created += add_created_pyr
                created_by["legacy"] += add_created_pyr
            ymgp_adds = await self._scan_ymgp_pullback_adds(settings)
            if ymgp_adds:
                created += ymgp_adds
                add_created += ymgp_adds
                created_by["ymgp"] += ymgp_adds
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
        """관심종목 + 스크리너(selected) 후보 수집."""
        by_code: Dict[str, Dict] = {}

        # 1) 관심종목 (설정 textarea)
        for code in self._parse_watchlist(settings.watchlist_codes):
            by_code.setdefault(code, {"stock_code": code, "stock_name": code, "source": "watchlist"})

        # 2) 스크리너 — 거래대금순 상위·등락 밴드·대금 하한 (레거시 유니버스 상한)
        limit = max(1, int(Config.SCREENER_CANDIDATE_LIMIT or 50))
        min_chg = float(getattr(Config, "SCREENER_MIN_CHANGE_RATE", 0) or 0)
        max_chg = float(getattr(Config, "SCREENER_MAX_CHANGE_RATE", 0) or 0)
        min_amt = float(getattr(Config, "SCREENER_MIN_TRADE_AMOUNT_EOK", 0) or 0)
        res = await self.kiwoom_api.get_volume_rank(
            market="000",
            sort_tp="3",
            limit=limit,
            min_change_rate=min_chg or None,
            max_change_rate=max_chg or None,
            min_trade_amount_eok=min_amt or None,
        )
        volume_items: List[Dict] = []
        if res.get("success"):
            volume_items = (res.get("items") or [])[:limit]
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
            if len(volume_items) < limit:
                logger.warning(
                    f"📈 [AUTO_SCANNER] 스크리너 후보 {len(volume_items)}/{limit}개만 조회됨 "
                    f"(API 제한·페이징·{band_s} 필터)"
                )
        else:
            err = res.get("error") or "조회 실패"
            logger.warning(f"📈 [AUTO_SCANNER] 거래대금 상위 조회 실패: {err}")
            log_activity("SCANNER", f"스크리너 조회 실패: {err}", "warn")

        # 레거시: 거래대금 상위 limit만 (조건식 미사용 — 상따와 동일 패턴)
        for it in volume_items:
            code = it.get("stock_code")
            if not code:
                continue
            name = it.get("stock_name", "")
            if not KiwoomAPI._is_screener_stock(name, it.get("product_type")):
                continue
            by_code[code] = {**it, "source": "screener"}

        watch = {
            c: it for c, it in by_code.items()
            if (it.get("source") or "") == "watchlist"
        }
        legacy_pool = [
            (c, it) for c, it in by_code.items()
            if (it.get("source") or "") != "watchlist"
        ]

        def _trade_amt(row: Dict) -> float:
            for key in ("trade_amount", "trading_value", "trde_prica"):
                try:
                    v = row.get(key)
                    if v is not None and str(v).strip() != "":
                        return float(v)
                except (TypeError, ValueError):
                    continue
            return 0.0

        legacy_pool.sort(key=lambda pair: _trade_amt(pair[1]), reverse=True)
        capped_legacy = dict(legacy_pool[:limit])
        by_code = {**watch, **capped_legacy}

        logger.info(
            f"📈 [AUTO_SCANNER] 후보 수집 — 거래대금 상위 {limit} "
            f"({len(volume_items)}조회) · 레거시 스캔 {len(capped_legacy)}"
        )
        # 3) 상따 유니버스 — ka10027 등락률상위 풀 → 거래대금순 상위 N
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

        # 4) 과매도 돌파 전용 조건식 — 다른 유니버스와 합치지 않고 source로 전략을 고정
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

        # 5) 역매공파 전용 조건식
        try:
            ymgp_names = parse_condition_names(
                getattr(settings, "ymgp_condition_names", None)
            )
            if getattr(settings, "use_ymgp", False) and ymgp_names:
                ymgp_items, ymgp_errs = await fetch_condition_target_items(
                    self.kiwoom_api, ymgp_names,
                )
                if ymgp_errs:
                    logger.warning(
                        f"📈 [AUTO_SCANNER] 역매공파 조건식 조회 실패: {', '.join(ymgp_errs)}"
                    )
                for it in ymgp_items or []:
                    code = it.get("stock_code")
                    src = by_code.get(code, {}).get("source")
                    if code and src not in ("sangtta", "breakout"):
                        by_code[code] = {**it, "source": "ymgp"}
                n = len(ymgp_items or [])
                logger.info(
                    f"📈 [AUTO_SCANNER] 역매공파 후보 수집 — {n}개 "
                    f"(설정: {', '.join(ymgp_names)})"
                )
                self._log_strategy_candidates("역매공파", ymgp_items or [])
        except Exception as e:
            logger.debug(f"📈 [AUTO_SCANNER] 역매공파 후보 수집 중 오류: {e}")

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
            if src in ("sangtta", "breakout", "ymgp"):
                self._log_scan_skip(name, code, "보유·대기", "이미 보유/대기", strategy=str(src))
            else:
                logger.debug(f"📈 [AUTO_SCANNER] 이미 보유/대기 — 스킵: {name}")
            return False, "holding"

        if await self._in_cooldown(code, settings.reorder_cooldown_sec or 300):
            src = item.get("source")
            if src in ("sangtta", "breakout", "ymgp"):
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
            if src in ("sangtta", "breakout", "ymgp"):
                self._log_scan_skip(name, code, "시세", "현재가 없음", strategy=str(src))
            return False, "no_price"

        # 전략별 시간대 / 슬롯 제약 확인 (예: sangtta 전용 윈도우 및 쿼터)
        strategy = item.get("source", "scanner")
        if strategy == "sangtta":
            strategy = "sangtta"
        elif strategy == "ymgp":
            strategy = "ymgp"
        elif strategy in ("screener", "condition", "both", "watchlist", "scanner"):
            strategy = "legacy"
        else:
            strategy = str(strategy or "legacy")

        if strategy in ("sangtta", "breakout", "ymgp"):
            label = {"sangtta": "상따", "breakout": "돌파", "ymgp": "역매공파"}.get(strategy, strategy)
            try:
                chg_s = f"{float(change_rate):+.2f}%" if change_rate is not None else "?"
            except (TypeError, ValueError):
                chg_s = "?"
            logger.info(
                f"📈 [AUTO_SCANNER] [{label}] 평가 {name}({code}) "
                f"가격={int(price):,} 등락={chg_s}"
            )

        # 전략 패키지는 전역 signal_min 대신 자체 등락·과열 규칙을 사용
        if strategy not in ("sangtta", "breakout", "ymgp"):
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
            )
            break
        if not risk_ok:
            self._log_scan_skip(
                name, code, "장세", risk_reason,
                strategy=strategy if strategy in ("sangtta", "breakout", "ymgp") else "",
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
        elif strategy == "ymgp":
            from utils.auto_trade_engine import allows_strategy_new_buy, is_strategy_slot_available
            allowed, reason = allows_strategy_new_buy(settings, "ymgp")
            if not allowed:
                self._log_scan_skip(
                    name, code, "게이트", reason or "역매공파 시간 외", strategy="ymgp",
                )
                return False, "gate"
            for db in get_db():
                if not is_strategy_slot_available(settings, db, "ymgp", for_new_signal=True):
                    from utils.auto_trade_engine import _count_strategy_slots, effective_ymgp_max_slots
                    used = _count_strategy_slots(db, "ymgp")
                    lim = effective_ymgp_max_slots(settings)
                    self._log_scan_skip(
                        name, code, "게이트", f"역매공파 슬롯 포화 ({used}/{lim})",
                        strategy="ymgp",
                    )
                    return False, "gate"
                break
            gate_ctx = {"entry_leg": 1, "stock_name": name}
            gate_ok, gate_reason = await evaluate_gate_pack(
                self.kiwoom_api,
                settings,
                "yeokmaegongpa",
                code,
                price,
                change_rate=change_rate,
                ctx=gate_ctx,
                skip_time_check=True,
            )
        else:
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
            self._log_scan_skip(
                name, code, "게이트", gate_reason,
                strategy=strategy if strategy in ("sangtta", "breakout", "ymgp") else "",
            )
            return False, "gate"

        ref = gate_ctx.get("ymgp_ref") or {}
        ok, signal_reason = await signal_manager.create_signal_detail(
            condition_id=AUTO_TRADE_CONDITION_ID,
            stock_code=code,
            stock_name=name,
            signal_type=SignalType.AUTO_TRADE,
            additional_data={
                "current_price": price,
                "change_rate": change_rate,
                "source": item.get("source", "scanner"),
                "strategy": strategy,
                "gate_pack": (
                    "sangtta_breakout" if strategy == "sangtta"
                    else (
                        "oversold_breakout" if strategy == "breakout"
                        else ("yeokmaegongpa" if strategy == "ymgp" else "legacy_momentum")
                    )
                ),
                "level_kind": gate_ctx.get("level_kind"),
                "level_price": gate_ctx.get("level_price"),
                "breakout_level_price": gate_ctx.get("breakout_level_price") or gate_ctx.get("level_price"),
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
                "ymgp_stage": gate_ctx.get("ymgp_stage"),
                "ymgp_ref": ref,
                "ymgp_ref_high": ref.get("high"),
                "ymgp_ref_low": ref.get("low"),
                "ymgp_ref_open": ref.get("open"),
                "entry_leg": 1 if strategy == "ymgp" else None,
                "ymgp_entry_leg": 1 if strategy == "ymgp" else None,
            },
        )
        if ok:
            if strategy == "breakout":
                from utils.auto_trade_engine import clear_breakout_entry_state
                clear_breakout_entry_state(code)
            if strategy == "ymgp":
                from utils.ymgp_engine import update_stock_state
                update_stock_state(code, stage="ENTERED_1", ref=ref)
            strat_label = {
                "sangtta": "상따",
                "breakout": "돌파",
                "ymgp": "역매공파",
            }.get(strategy, "레거시")
            confirm_bit = ""
            if strategy == "breakout" and gate_ctx.get("entry_confirm_mode"):
                confirm_bit = f" 확인={gate_ctx.get('entry_confirm_mode')}"
            if strategy == "ymgp" and gate_ctx.get("ymgp_stage"):
                confirm_bit = f" stage={gate_ctx.get('ymgp_stage')}"
            msg = (
                f"매수 신호 생성 [{strat_label}]: {name}({code}) "
                f"가격={price:,} 등락={change_rate}%{confirm_bit}"
            )
            logger.info(f"📈 [AUTO_SCANNER] {msg}")
            log_activity("SCANNER", msg, "info", stock_code=code, stock_name=name)
            return True, "signal_ok"
        self._log_scan_skip(
            name, code, "신호", signal_reason,
            strategy=strategy if strategy in ("sangtta", "breakout", "ymgp") else "",
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
        elif strategy == "ymgp":
            label = "[역매공파] "
        elif strategy == "jongga":
            label = "[종가배팅] "
        msg = f"진입 보류 {label}[{category}] {name}({code}): {detail}"
        # 상따/돌파/역매공파/종가배팅은 파일 로그로 추적 (레거시는 대시보드 링버퍼 + debug만 — 노이즈 방지)
        if strategy in ("sangtta", "breakout", "ymgp", "jongga"):
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

    async def _scan_ymgp_pullback_adds(self, settings: AutoTradeSettings) -> int:
        """역매공파 1차 보유 종목의 2차 눌림 추가매수."""
        if not getattr(settings, "use_ymgp", False):
            return 0
        if not getattr(settings, "ymgp_enable_pullback_add", True):
            return 0

        created = 0
        positions = []
        for db in get_db():
            positions = (
                db.query(Position)
                .filter(Position.status == "HOLDING", Position.strategy_key == "ymgp")
                .all()
            )
            break

        for pos in positions:
            leg = int(getattr(pos, "ymgp_entry_leg", None) or 1)
            if leg >= 2:
                continue
            if await self._in_cooldown(pos.stock_code, settings.reorder_cooldown_sec or 300):
                continue
            if await self._has_pending_signal_only(pos.stock_code):
                continue

            price = await self.kiwoom_api.get_current_price(pos.stock_code)
            if not price:
                continue
            gate_ctx = {
                "entry_leg": 2,
                "ymgp_ref": {
                    "high": getattr(pos, "ymgp_ref_high", None),
                    "low": getattr(pos, "ymgp_ref_low", None),
                    "open": getattr(pos, "ymgp_ref_open", None),
                },
            }
            gate_ok, gate_reason = await evaluate_gate_pack(
                self.kiwoom_api,
                settings,
                "yeokmaegongpa",
                pos.stock_code,
                price,
                ctx=gate_ctx,
                skip_time_check=True,
            )
            if not gate_ok:
                logger.debug(
                    f"📈 [AUTO_SCANNER] [역매공파] 2차 보류 {pos.stock_name}: {gate_reason}"
                )
                continue

            ref = gate_ctx.get("ymgp_ref") or {}
            ok = await signal_manager.create_signal(
                condition_id=AUTO_TRADE_CONDITION_ID,
                stock_code=pos.stock_code,
                stock_name=pos.stock_name,
                signal_type=SignalType.AUTO_TRADE,
                additional_data={
                    "current_price": price,
                    "source": "ymgp_pullback_add",
                    "is_add_buy": True,
                    "strategy": "ymgp",
                    "gate_pack": "yeokmaegongpa",
                    "entry_leg": 2,
                    "ymgp_entry_leg": 2,
                    "ymgp_ref": ref,
                    "ymgp_ref_high": ref.get("high") or getattr(pos, "ymgp_ref_high", None),
                    "ymgp_ref_low": ref.get("low") or getattr(pos, "ymgp_ref_low", None),
                    "ymgp_ref_open": ref.get("open") or getattr(pos, "ymgp_ref_open", None),
                },
            )
            if ok:
                created += 1
                from utils.ymgp_engine import update_stock_state
                update_stock_state(pos.stock_code, stage="ENTERED_2")
                msg = f"역매공파 2차(눌림) 신호: {pos.stock_name} @ {price:,}"
                logger.info(f"📈 [AUTO_SCANNER] {msg}")
                log_activity("SCANNER", msg, "info", stock_code=pos.stock_code)
            await asyncio.sleep(2)
        return created

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

    async def _scan_jongga_pig_legs(self, settings: AutoTradeSettings) -> int:
        """종가배팅 돼지물량 2차(14:50+)·3차(동시호가 호가벽) 추가매수."""
        if not getattr(settings, "use_jongga", False):
            return 0
        from utils.jongga_engine import (
            DEFAULT_LEG2_START,
            DEFAULT_LEG3_END,
            DEFAULT_LEG3_START,
            DEFAULT_LOW_HOLD_BARS,
            DEFAULT_PIG_LEVELS,
            DEFAULT_PIG_RATIO,
            GATE_PACK,
            STRATEGY_KEY,
            ensure_leg_state,
            in_hm_window,
            low_support_ok,
            mark_leg,
            past_hm,
            pig_orderbook_verdict,
            pig_split_enabled,
            program_net_ok,
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

                # 저점 지지 (분봉)
                try:
                    chart = await self.kiwoom_api.get_stock_chart_data(code, "1M")
                    bars = chart or []
                except Exception as e:
                    bars = []
                    logger.warning(f"📈 [AUTO_SCANNER] 종가배팅 2차분봉 실패 {code}: {e}")
                low_ok, low_msg = low_support_ok(
                    bars, float(price), lookback=DEFAULT_LOW_HOLD_BARS
                )
                if not low_ok:
                    ok2 = False
                    reasons.append(low_msg)

                # 프로그램 매수세 (ka90013 / ka90008) — 장중 외인·기관은 미집계인 경우가 많음
                prog = await self.kiwoom_api.get_stock_program_net(code)
                if not prog.get("success"):
                    ok2 = False
                    reasons.append(f"프로그램조회실패:{prog.get('error')}")
                else:
                    net_ok, net_msg = program_net_ok(prog.get("net_qty"))
                    if not net_ok:
                        ok2 = False
                        reasons.append(net_msg)
                    else:
                        reasons.append(net_msg)

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
                            "program_net_qty": prog.get("net_qty"),
                            "program_buy_qty": prog.get("buy_qty"),
                            "program_sell_qty": prog.get("sell_qty"),
                            "program_source": prog.get("source"),
                            "low_support": low_msg,
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
