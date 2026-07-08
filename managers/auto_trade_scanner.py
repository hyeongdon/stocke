"""대시보드 설정 기반 자동매매 스캐너 (KIS 스타일).

관심종목 + 스크리너(거래량/대금 상위) 후보를 주기적으로 점검하고,
매수 조건을 만족하면 PendingBuySignal을 생성한다.
조건식(CNSRREQ) 주기 검색과는 별개이며, 자동매매 ON일 때만 동작한다.
"""
import asyncio
import logging
from datetime import datetime, time as dt_time, timedelta
from typing import Dict, List, Optional, Set

from sqlalchemy.orm import Session

from api.kiwoom_api import KiwoomAPI
from core.config import Config
from core.models import AutoTradeSettings, PendingBuySignal, Position, get_db
from managers.signal_manager import SignalType, signal_manager
from utils.auto_trade_engine import (
    auto_trade_engines_allowed,
    check_daily_limits,
    check_entry_gate,
    disable_auto_trade,
    has_buy_conditions,
    new_buy_block_reason,
    passes_buy_price_conditions,
)
from utils.auto_trade_activity_log import log_activity

logger = logging.getLogger(__name__)

AUTO_TRADE_CONDITION_ID = 99999  # 자동매매 스캐너 전용 condition_id


class AutoTradeScanner:
    def __init__(self):
        self.kiwoom_api = KiwoomAPI()
        self.is_running = False
        self.scan_interval = 120  # 2분 (API 부하 절감)
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
        log_activity("SCANNER", "종목 스캐너 시작 (2분 주기)", "info")

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

    def get_status(self) -> Dict:
        return {
            "is_running": self.is_running,
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
                            self.last_scan_at = datetime.now()
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
        for db in get_db():
            return db.query(AutoTradeSettings).first()
        return None

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

        created = 0
        gate_pause = 6 if settings.use_entry_gate else 2
        for item in targets:
            if await self._daily_buy_limit_reached(settings):
                break
            if await self._max_positions_reached(settings):
                break
            ok = await self._evaluate_and_signal(settings, item)
            if ok:
                created += 1
            await asyncio.sleep(gate_pause)

        add_created = await self._scan_pyramiding_adds(settings)
        created += add_created

        summary = f"스캔 완료 — 대상 {len(targets)}개, 신호 {created}개"
        logger.info(f"📈 [AUTO_SCANNER] {summary}")
        log_activity("SCANNER", summary, "info" if created else "info",
                     targets=len(targets), signals=created)
        return created, len(targets)

    async def _collect_targets(self, settings: AutoTradeSettings) -> List[Dict]:
        """관심종목 + 스크리너(selected) 후보 수집."""
        by_code: Dict[str, Dict] = {}

        # 1) 관심종목 (설정 textarea)
        for code in self._parse_watchlist(settings.watchlist_codes):
            by_code.setdefault(code, {"stock_code": code, "stock_name": code, "source": "watchlist"})

        # 2) 스크리너 — 거래대금순 상위
        limit = Config.SCREENER_CANDIDATE_LIMIT
        res = await self.kiwoom_api.get_volume_rank(market="000", sort_tp="3", limit=limit)
        if res.get("success"):
            items = res.get("items") or []
            codes = [
                str(it.get("stock_code", "")).strip().zfill(6)
                for it in items
                if it.get("stock_code")
            ]
            from utils.fundamental_mart_store import get_latest_map_by_codes as get_fundamental_map
            fundamental_map = get_fundamental_map(codes)
            for it in items:
                name = it.get("stock_name", "")
                if not KiwoomAPI._is_screener_stock(name, it.get("product_type")):
                    continue
                code = str(it.get("stock_code", "")).strip().zfill(6)
                if not code:
                    continue
                per = (fundamental_map.get(code) or {}).get("per")
                if not KiwoomAPI._is_screener_per_eligible(per):
                    continue
                by_code[code] = {**it, "source": "screener"}
            if len(items) < limit:
                logger.warning(
                    f"📈 [AUTO_SCANNER] 스크리너 후보 {len(items)}/{limit}개만 조회됨 (API 제한·페이징)"
                )
        else:
            err = res.get("error") or "조회 실패"
            logger.warning(f"📈 [AUTO_SCANNER] 거래대금 상위 조회 실패: {err}")
            log_activity("SCANNER", f"스크리너 조회 실패: {err}", "warn")

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

    async def _evaluate_and_signal(self, settings: AutoTradeSettings, item: Dict) -> bool:
        code = KiwoomAPI.normalize_stock_code(item.get("stock_code", ""))
        name = item.get("stock_name") or code
        if not code:
            return False

        if await self._has_open_interest(code):
            logger.debug(f"📈 [AUTO_SCANNER] 이미 보유/대기 — 스킵: {name}")
            return False

        if await self._in_cooldown(code, settings.reorder_cooldown_sec or 300):
            return False

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
            return False

        if not passes_buy_price_conditions(settings, price, change_rate):
            return False

        gate_ok, gate_reason = await check_entry_gate(self.kiwoom_api, settings, code, price)
        if not gate_ok:
            logger.debug(f"📈 [AUTO_SCANNER] 진입 게이트 미통과 {name}: {gate_reason}")
            log_activity("SCANNER", f"게이트 미통과 {name}({code}): {gate_reason}", "info")
            return False

        ok = await signal_manager.create_signal(
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
        return ok

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
        cutoff = datetime.now() - timedelta(seconds=cooldown_sec)
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
        today = datetime.now().date()
        count = 0
        for db in get_db():
            session: Session = db
            count = session.query(PendingBuySignal).filter(
                PendingBuySignal.detected_at >= datetime.combine(today, dt_time.min),
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
