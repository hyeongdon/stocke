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
    get_auto_trade_settings_sync,
    has_buy_conditions,
    new_buy_block_reason,
    passes_buy_price_conditions,
)
from utils.auto_trade_activity_log import log_activity
from utils.market_hours import linked_trading_session_window_str
from utils.datetime_kst import kst_day_start_utc_naive, kst_now_iso, utc_now_naive

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

# API 호출·게이트 검사가 있었던 경로만 스캔 간 대기 (등락미달 등은 즉시 스킵)
_THROTTLE_REASONS = frozenset({"gate", "signal_ok", "signal_fail", "no_price"})
_DISPARITY_LOG_CONCURRENCY = 4


def _format_scan_summary(
    stats: Dict[str, int],
    total: int,
    created: int,
    settings: AutoTradeSettings,
    *,
    add_created: int = 0,
) -> str:
    parts = [f"대상 {total}"]
    for key in ("price_cond", "holding", "cooldown", "gate", "no_price", "signal_fail", "skipped"):
        n = stats.get(key, 0)
        if n:
            parts.append(f"{_SCAN_STAT_LABELS[key]} {n}")
    parts.append(f"신호 {created}")
    if add_created:
        parts.append(f"(추가매수 {add_created})")
    min_rate = effective_min_change_rate(settings)
    if min_rate is not None:
        parts.append(f"최소등락 {min_rate:g}%")
    return "스캔 요약 — " + " · ".join(parts)


class AutoTradeScanner:
    def __init__(self):
        self.kiwoom_api = KiwoomAPI()
        self.is_running = False
        self.scan_interval = 60  # 1분 (후보 수·스킵 로직으로 API 부하 완화)
        self._task: Optional[asyncio.Task] = None
        self.last_scan_at: Optional[datetime] = None
        self.last_scan_created = 0
        self.last_scan_targets = 0

    async def start(self):
        if self.is_running:
            return
        self.is_running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("📈 [AUTO_SCANNER] 자동매매 스캐너 시작")
        log_activity("SCANNER", "종목 스캐너 시작 (1분 주기)", "info")

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
        return {
            "is_running": self.is_running,
            "is_active": active,
            "session_window": window,
            "trade_start_time": settings.trade_start_time if settings else None,
            "trade_end_time": settings.trade_end_time if settings else None,
            "last_scan_at": self.last_scan_at.isoformat() if self.last_scan_at else None,
            "last_scan_targets": self.last_scan_targets,
            "last_scan_created": self.last_scan_created,
            "scan_interval_sec": self.scan_interval,
        }

    async def _loop(self):
        try:
            while self.is_running:
                settings = self._load_settings()
                if settings and settings.is_enabled:
                    allowed, off_reason = auto_trade_engines_allowed()
                    if not allowed:
                        msg = f"{off_reason} — 스캔 건너뜀"
                        logger.info(f"📈 [AUTO_SCANNER] {msg}")
                        log_activity("SCANNER", msg, "warn")
                    else:
                        try:
                            created, targets = await self._scan_once(settings)
                            self.last_scan_at = kst_now_iso()
                            self.last_scan_created = created
                            self.last_scan_targets = targets
                        except Exception as e:
                            logger.error(f"📈 [AUTO_SCANNER] 스캔 오류: {e}")
                else:
                    logger.debug("📈 [AUTO_SCANNER] 자동매매 OFF — 스캔 건너뜀")
                await asyncio.sleep(self.scan_interval)
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("📈 [AUTO_SCANNER] 스캔 루프 종료")

    def _load_settings(self) -> Optional[AutoTradeSettings]:
        return get_auto_trade_settings_sync()

    async def _scan_once(self, settings: AutoTradeSettings) -> tuple:
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
                f"최대 동시 보유 종목 도달 — 스캔 건너뜀 "
                f"({detail['total']}/{limit}: 보유 {detail['holdings']} + 대기 {detail['reserved']})"
            )
            logger.info(f"📈 [AUTO_SCANNER] {msg}")
            log_activity("SCANNER", msg, "warn")
            return 0, 0

        targets = await self._collect_targets(settings)
        if not targets:
            logger.debug("📈 [AUTO_SCANNER] 스캔 대상 없음")
            log_activity("SCANNER", "스캔 완료 — 대상 0개 (관심종목·스크리너 조건 확인)", "info")
            return 0, 0
        if os.getenv("SCANNER_DISPARITY_LOG", "").lower() in ("1", "true", "yes"):
            await self._log_disparity_observations(targets)

        created = 0
        add_created = 0
        stats: Dict[str, int] = defaultdict(int)
        gate_pause = 6 if settings.use_entry_gate else 2
        scanned = 0
        log_activity(
            "SCANNER",
            f"스캔 시작 — 대상 {len(targets)}개",
            "info",
            targets=len(targets),
        )
        try:
            for item in targets:
                if await self._daily_buy_limit_reached(settings):
                    stats["skipped"] += len(targets) - scanned
                    break
                if await self._max_positions_reached(settings):
                    stats["skipped"] += len(targets) - scanned
                    break
                ok, reason = await self._evaluate_and_signal(settings, item)
                stats[reason] += 1
                scanned += 1
                if ok:
                    created += 1
                if reason in _THROTTLE_REASONS:
                    await asyncio.sleep(gate_pause)
                else:
                    await asyncio.sleep(0)

            add_created = await self._scan_pyramiding_adds(settings)
            created += add_created
        finally:
            summary = _format_scan_summary(
                stats, len(targets), created, settings, add_created=add_created,
            )
            logger.info(f"📈 [AUTO_SCANNER] {summary}")
            log_activity(
                "SCANNER",
                summary,
                "info" if created else "info",
                targets=len(targets),
                signals=created,
                scan_stats=dict(stats),
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

        # 2) 스크리너 — 거래대금순 상위
        limit = Config.SCREENER_CANDIDATE_LIMIT
        res = await self.kiwoom_api.get_volume_rank(market="000", sort_tp="3", limit=limit)
        volume_items: List[Dict] = []
        if res.get("success"):
            volume_items = res.get("items") or []
            if len(volume_items) < limit:
                logger.warning(
                    f"📈 [AUTO_SCANNER] 스크리너 후보 {len(volume_items)}/{limit}개만 조회됨 (API 제한·페이징)"
                )
        else:
            err = res.get("error") or "조회 실패"
            logger.warning(f"📈 [AUTO_SCANNER] 거래대금 상위 조회 실패: {err}")
            log_activity("SCANNER", f"스크리너 조회 실패: {err}", "warn")

        from utils.screener_targets import (
            fetch_condition_target_items,
            merge_target_maps,
            parse_condition_names,
        )

        condition_names = parse_condition_names(settings.screener_condition_names)
        condition_items: List[Dict] = []
        if condition_names:
            condition_items, cond_errors = await fetch_condition_target_items(
                self.kiwoom_api, condition_names,
            )
            if cond_errors:
                logger.warning(f"📈 [AUTO_SCANNER] 조건식 조회 실패: {', '.join(cond_errors)}")
                log_activity("SCANNER", f"조건식 조회 실패: {', '.join(cond_errors)}", "warn")
            if condition_items:
                log_activity(
                    "SCANNER",
                    f"조건식 후보 {len(condition_items)}개 ({', '.join(condition_names)})",
                    "info",
                )
            else:
                log_activity(
                    "SCANNER",
                    f"조건식 편입 0개 ({', '.join(condition_names)}) — 장중·조건식 결과 확인",
                    "warn",
                )
        else:
            logger.debug("📈 [AUTO_SCANNER] screener_condition_names 미설정 — 거래대금순만 사용")

        merged = merge_target_maps(volume_items, condition_items)
        codes = list(merged.keys())
        from utils.fundamental_mart_store import get_latest_map_by_codes as get_fundamental_map
        fundamental_map = get_fundamental_map(codes) if codes else {}

        cond_added = 0
        for code, it in merged.items():
            name = it.get("stock_name", "")
            if not KiwoomAPI._is_screener_stock(name, it.get("product_type")):
                continue
            per = (fundamental_map.get(code) or {}).get("per")
            if not KiwoomAPI._is_screener_per_eligible(per):
                continue
            src = it.get("source") or "screener"
            by_code[code] = {**it, "source": src}
            if src in ("condition", "both"):
                cond_added += 1

        if condition_names:
            logger.info(
                f"📈 [AUTO_SCANNER] 후보 수집 — 거래대금 {len(volume_items)} + "
                f"조건식 편입 {cond_added} (설정: {', '.join(condition_names)})"
            )

        return list(by_code.values())

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
            logger.debug(f"📈 [AUTO_SCANNER] 이미 보유/대기 — 스킵: {name}")
            return False, "holding"

        if await self._in_cooldown(code, settings.reorder_cooldown_sec or 300):
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
            return False, "no_price"

        if not passes_buy_price_conditions(settings, price, change_rate):
            skip = buy_price_skip_reason(settings, price, change_rate) or "매수 조건 미충족"
            self._log_scan_skip(name, code, "등락/가격", skip)
            return False, "price_cond"

        gate_ok, gate_reason = await check_entry_gate(self.kiwoom_api, settings, code, price)
        if not gate_ok:
            logger.debug(f"📈 [AUTO_SCANNER] 진입 게이트 미통과 {name}: {gate_reason}")
            self._log_scan_skip(name, code, "게이트", gate_reason)
            return False, "gate"

        ok, signal_reason = await signal_manager.create_signal_detail(
            condition_id=AUTO_TRADE_CONDITION_ID,
            stock_code=code,
            stock_name=name,
            signal_type=SignalType.AUTO_TRADE,
            additional_data={
                "current_price": price,
                "change_rate": change_rate,
                "source": item.get("source", "scanner"),
            },
        )
        if ok:
            msg = f"매수 신호 생성: {name}({code}) 가격={price:,} 등락={change_rate}%"
            logger.info(f"📈 [AUTO_SCANNER] {msg}")
            log_activity("SCANNER", msg, "info", stock_code=code, stock_name=name)
            return True, "signal_ok"
        self._log_scan_skip(name, code, "신호", signal_reason)
        return False, "signal_fail"

    @staticmethod
    def _log_scan_skip(name: str, code: str, category: str, detail: str) -> None:
        log_activity(
            "SCANNER",
            f"진입 보류 [{category}] {name}({code}): {detail}",
            "warn",
            stock_code=code,
            stock_name=name,
            skip_category=category,
            skip_reason=detail,
        )

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
        cutoff = utc_now_naive() - timedelta(seconds=cooldown_sec)
        for db in get_db():
            session: Session = db
            recent = session.query(PendingBuySignal).filter(
                PendingBuySignal.stock_code == stock_code,
                PendingBuySignal.detected_at >= cutoff,
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
