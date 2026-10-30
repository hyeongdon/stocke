"""RSI 저점 분할매수(-5% 사다리) 스케줄러 (docs/PRD_RSI_LADDER.md).

- 장중(09:00~15:20) 10분마다 가격 감시: 현재가 ≤ 다음 트리거면 추가 매수 (하루 1단계)
- 15:05~15:20 하루 1회 판정: 현재가 + 확정 일봉으로 1차 진입 / 추가 매수 / S0~S3 매도 결정
- 매수: 기존 매수 신호 경로(PendingBuySignal → BuyOrderExecutor), strategy="rsi_ladder"
- 매도: 계획에 pending_sell 기록 → StopLossManager._monitor_ladder_exit 가 정규장에 지정 수량 매도
- 10초 루프: 체결(PositionBuyFill · SellOrder) 대조로 계획 상태 갱신
- 15:40 장후 요약 텔레그램
"""
from __future__ import annotations

import asyncio
import html
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from utils.datetime_kst import now_kst
from utils.rsi_ladder import (
    OPEN_STATUSES,
    SELL_RULE_LABEL,
    STRATEGY_KEY,
    auto_fills,
    bars_with_today,
    evaluate_add_buy,
    evaluate_entry,
    evaluate_exit,
    execution_avg_price,
    get_ladder_store,
    judge_plan,
    last_fill_price,
    merge_profile,
    moving_averages,
    next_trigger,
    step_qty,
)

logger = logging.getLogger(__name__)

_LOOP_SEC = 10
JUDGE_HM = (15, 5)
JUDGE_END_HM = (15, 20)
SUMMARY_HM = (15, 40)
WATCH_START_HM = (9, 0)
WATCH_END_HM = JUDGE_END_HM
_WATCH_INTERVAL_SEC = 600
_DAILY_MAX_BARS = 400
_SNAPSHOT_TTL = 60.0
_BUY_PENDING_TIMEOUT_MIN = 30
_SELL_SENT_RETRY_MIN = 3
_EXEC_PX_INTERVAL_SEC = 30
_EXEC_PX_MAX_TRIES = 10
_FAILED_SIGNAL = ("FAILED", "EXPIRED", "CANCELLED")


def _hm(now: datetime) -> Tuple[int, int]:
    return (now.hour, now.minute)


def _won(v: Any) -> str:
    try:
        return f"{int(round(float(v))):,}"
    except (TypeError, ValueError):
        return "-"


def _utc_naive() -> datetime:
    return datetime.utcnow().replace(microsecond=0)


def _parse_iso(s: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None


async def _send_telegram(text: str) -> None:
    try:
        from notifications.telegram_notifier import TelegramNotifier

        tg = TelegramNotifier()
        if not tg.is_configured():
            return
        await asyncio.to_thread(tg.send_message, text, "HTML")
    except Exception as e:
        logger.debug(f"[LADDER] 텔레그램 전송 실패: {e}")


def _log(msg: str, level: str = "info", stock_code: Optional[str] = None) -> None:
    try:
        from utils.auto_trade_activity_log import log_activity

        log_activity("SCANNER", msg, level, stock_code=stock_code, strategy=STRATEGY_KEY)
    except Exception:
        pass


def _load_settings():
    from core.models import get_auto_trade_settings_row, get_db

    for db in get_db():
        return get_auto_trade_settings_row(db)
    return None


def _account_mode() -> str:
    from core.config import get_account_mode

    return get_account_mode()


class LadderError(Exception):
    pass


class RsiLadderScheduler:
    def __init__(self) -> None:
        self.is_running = False
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._fail_until = 0.0
        self._exec_px_after = 0.0
        self._watch_after = 0.0
        self._snap_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    # ───────────────────────── lifecycle ─────────────────────────

    async def start_scheduler(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("🪜 [LADDER] 분할매수 스케줄러 시작")

    async def stop_scheduler(self) -> None:
        self.is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("🪜 [LADDER] 분할매수 스케줄러 중지")

    async def _loop(self) -> None:
        while self.is_running:
            try:
                if time.time() >= self._fail_until:
                    await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._fail_until = time.time() + 120
                logger.warning(f"🪜 [LADDER] 스케줄러 오류, 2분 후 재시도: {e}")
            await asyncio.sleep(_LOOP_SEC)

    async def _tick(self) -> None:
        from utils.market_hours import is_krx_trading_day

        store = get_ladder_store()
        if not self.my_plans(open_only=True):
            return
        self.reconcile()
        await self._refine_fill_prices()
        now = now_kst()
        if not store.enabled or not is_krx_trading_day(now):
            return
        today = now.date().isoformat()
        last = store.data.get("last_judge") or {}
        if JUDGE_HM <= _hm(now) < JUDGE_END_HM and last.get("date") != today:
            await self.run_judge(trigger="15:05")
        if WATCH_START_HM <= _hm(now) < WATCH_END_HM and time.time() >= self._watch_after:
            self._watch_after = time.time() + _WATCH_INTERVAL_SEC
            await self.run_price_watch()
        if SUMMARY_HM <= _hm(now) and store.data.get("last_summary") != today:
            store.data["last_summary"] = today
            store.save()
            await self._send_summary()

    # ───────────────────────── 조회 ─────────────────────────

    @staticmethod
    def my_plans(*, open_only: bool = False) -> List[Dict[str, Any]]:
        mode = _account_mode()
        out = []
        for p in get_ladder_store().plans().values():
            if p.get("account_mode", mode) != mode:
                continue
            if open_only and p.get("status") not in OPEN_STATUSES:
                continue
            out.append(p)
        return out

    @staticmethod
    def _holdings() -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
        """(code → 분할매수 HOLDING 포지션, code → 타 전략 HOLDING strategy_key)."""
        from core.models import Position, get_db

        mode = _account_mode()
        mine: Dict[str, Dict[str, Any]] = {}
        others: Dict[str, str] = {}
        for db in get_db():
            for pos in db.query(Position).filter(Position.status == "HOLDING").all():
                if getattr(pos, "account_mode", None) not in (None, mode):
                    continue
                code = str(pos.stock_code or "").strip()
                sk = str(pos.strategy_key or "legacy").strip().lower()
                if sk == STRATEGY_KEY:
                    mine[code] = {
                        "id": int(pos.id),
                        "qty": int(pos.buy_quantity or 0),
                        "avg": int(pos.buy_price or 0),
                        "current_price": int(getattr(pos, "current_price", None) or 0),
                    }
                else:
                    others[code] = sk
            break
        return mine, others

    async def _bars(self, code: str, now: datetime) -> List[Dict[str, Any]]:
        from api.kiwoom_api import kiwoom_api
        from utils.rwm_bowl import drop_unconfirmed_daily, normalize_daily_bars

        raw = await kiwoom_api.get_stock_chart_data(
            code, "1D", max_bars=_DAILY_MAX_BARS, allow_off_hours=True, cache_ttl_sec=60,
        )
        return drop_unconfirmed_daily(normalize_daily_bars(raw or []), now.replace(tzinfo=None))

    @staticmethod
    async def _price(code: str) -> Optional[int]:
        from api.kiwoom_api import kiwoom_api

        try:
            px = await kiwoom_api.get_current_price(code, allow_off_hours=True)
            return int(px) if px else None
        except Exception:
            return None

    async def _market_inputs(self, code: str, now: datetime):
        """(현재가, 판정용 일봉(당일 임시봉 포함), 주봉용 확정 일봉)."""
        confirmed = await self._bars(code, now)
        price = await self._price(code)
        if not price and confirmed:
            price = int(confirmed[-1]["close"])
        today = now.date()
        if confirmed and confirmed[-1]["date"] == today:
            bars = bars_with_today(confirmed, today, price)
        else:
            bars = bars_with_today(confirmed, today, price) if price else list(confirmed)
        weekly = [b for b in confirmed if b["date"] < today] or confirmed
        return price, bars, weekly

    @staticmethod
    async def resolve_name(code: str) -> str:
        try:
            from managers.rwm_bowl_scheduler import rwm_bowl_scheduler

            return await rwm_bowl_scheduler.resolve_name(code)
        except Exception:
            return ""

    # ───────────────────────── 판정 ─────────────────────────

    async def run_judge(self, *, trigger: str = "manual", dry_run: bool = False) -> Dict[str, Any]:
        async with self._lock:
            return await self._run_judge(trigger, dry_run)

    async def run_price_watch(self) -> List[Dict[str, Any]]:
        """장중 가격 감시 — ACTIVE 계획의 추가 매수 트리거만 현재가로 확인 (일봉·매도 판정 없음)."""
        async with self._lock:
            today = now_kst().date()
            results: List[Dict[str, Any]] = []
            for plan in self.my_plans(open_only=True):
                if plan.get("status") != "ACTIVE" or plan.get("pending_buy") or plan.get("pending_sell"):
                    continue
                price = await self._price(plan["code"])
                if not price:
                    continue
                add = evaluate_add_buy(plan, price=price, today=today)
                row = {"plan_id": plan["id"], "code": plan["code"], "price": price,
                       "trigger": add["trigger"], "buy": add["buy"]}
                if add["buy"]:
                    b = add["buy"]
                    ok, msg = await self._submit_buy(plan, step=b["step"], qty=b["qty"], price=price,
                                                     reason=f"[장중 감시] {b['reason']}", extra=False)
                    row["buy_result"] = msg
                    self._snap_cache.pop(plan["id"], None)
                results.append(row)
            return results

    async def _run_judge(self, trigger: str, dry_run: bool) -> Dict[str, Any]:
        store = get_ladder_store()
        now = now_kst()
        today = now.date()
        if not dry_run:
            self.reconcile()
        mine, _ = self._holdings()
        results: List[Dict[str, Any]] = []
        for plan in self.my_plans(open_only=True):
            code = plan["code"]
            try:
                price, bars, weekly = await self._market_inputs(code, now)
            except Exception as e:
                results.append({"plan_id": plan["id"], "code": code, "error": f"시세 조회 실패: {e}"})
                continue
            if not price or len(bars) < 61:
                results.append({"plan_id": plan["id"], "code": code, "error": "일봉 부족 또는 현재가 없음"})
                continue
            pos = mine.get(code) or {}
            hold, avg = int(pos.get("qty") or 0), float(pos.get("avg") or 0)
            res = judge_plan(plan, price=price, bars=bars, weekly_bars=weekly, hold=hold, avg=avg, today=today)
            row = {
                "plan_id": plan["id"], "code": code, "name": plan.get("name"), "price": price,
                "buy": res["buy"], "sells": res["sells"], "trigger": res["trigger"], "notes": res["notes"],
                "entry_ok": (res["entry"] or {}).get("ok"),
            }
            results.append(row)
            if dry_run:
                continue
            plan["last_judge"] = {
                "at": now.isoformat(timespec="seconds"), "price": price, "trigger": res["trigger"],
                "entry": res["entry"], "ma": res["ma"],
                "actions": ([] if res["sells"] or not res["buy"] else [f"BUY {res['buy']['step']}차"])
                + [f"SELL {s['rule']}" for s in res["sells"]],
            }
            ex = res["exit"]
            if ex:
                if ex["armed"] and not plan.get("armed"):
                    store.add_event(plan["id"], "ARM", f"익절 무장 — 최고 수익률 {ex['peak_pct']:.1f}%", save=False)
                plan.update(armed=ex["armed"], peak_pnl_pct=round(ex["peak_pct"], 2), base_qty=ex["base_qty"])
            if res["sells"] and plan.get("pending_sell"):
                row["notes"].append("이전 매도 대기 중 — 매도 판정 보류")
            elif res["sells"]:
                plan["sell_flags"] = ex["flags"]
                self._queue_sell(plan, [s["rule"] for s in res["sells"]], sum(s["qty"] for s in res["sells"]),
                                 price, hold)
                if any(s["rule"] in ("S1", "S2", "S3") for s in res["sells"]):
                    plan["status"] = "EXITING"
            elif res["buy"]:
                b = res["buy"]
                ok, msg = await self._submit_buy(plan, step=b["step"], qty=b["qty"], price=price,
                                                 reason=b["reason"], extra=False)
                row["buy_result"] = msg
        if not dry_run:
            store.data["last_judge"] = {"date": today.isoformat(), "at": now.isoformat(timespec="seconds"),
                                        "trigger": trigger, "count": len(results)}
            store.add_event(None, "JUDGE", f"{trigger} 판정 — {len(results)}개 계획")
            self._snap_cache.clear()
        return {"trigger": trigger, "dry_run": dry_run, "results": results}

    # ───────────────────────── 매수 · 매도 ─────────────────────────

    async def _submit_buy(self, plan: Dict[str, Any], *, step: int, qty: int, price: int, reason: str,
                          extra: bool) -> Tuple[bool, str]:
        from managers.auto_trade_scanner import AUTO_TRADE_CONDITION_ID
        from managers.signal_manager import SignalType, signal_manager

        store = get_ladder_store()
        code, name = plan["code"], plan.get("name") or plan["code"]
        settings = _load_settings()
        fail = ""
        mine, others = self._holdings()
        if not settings or not getattr(settings, "is_enabled", False):
            fail = "자동매매 OFF — 주문 불가"
        elif not store.enabled:
            fail = "분할매수 자동 감시 OFF — 주문 불가"
        elif code in others:
            fail = f"다른 전략({others[code]})이 보유 중 — 분할매수 주문 불가"
        elif plan.get("pending_buy"):
            fail = "이전 매수 체결 대기 중"
        elif qty <= 0:
            fail = "수량 0"
        if fail:
            store.add_event(plan["id"], "BUY_FAIL", f"{step}차 매수 실패: {fail}")
            _log(f"분할매수 {name} {step}차 매수 실패: {fail}", "warn", stock_code=code)
            return False, fail

        is_add = code in mine
        meta = {
            "strategy": STRATEGY_KEY,
            "source": STRATEGY_KEY,
            "current_price": int(price),
            "suggested_qty": int(qty),
            "is_add_buy": is_add,
            "entry_leg": int(step) if step > 0 else len(plan.get("fills") or []) + 1,
            "ladder_plan_id": plan["id"],
            "ladder_extra": bool(extra),
            "reason": reason,
            "order_ready": True,
        }
        ok, msg = await signal_manager.create_signal_detail(
            condition_id=AUTO_TRADE_CONDITION_ID,
            stock_code=code,
            stock_name=name,
            signal_type=SignalType.STRATEGY,
            additional_data=meta,
        )
        label = "수동 추가" if extra else f"{step}차"
        if not ok:
            store.add_event(plan["id"], "BUY_FAIL", f"{label} 매수 신호 실패: {msg}")
            _log(f"분할매수 {name} {label} 신호 실패: {msg}", "warn", stock_code=code)
            return False, msg
        plan["pending_buy"] = {
            "step": int(step), "qty": int(qty), "price": int(price), "extra": bool(extra),
            "created_utc": _utc_naive().isoformat(), "created_at": now_kst().isoformat(timespec="seconds"),
            "reason": reason,
        }
        plan["last_buy_date"] = now_kst().date().isoformat()
        store.add_event(plan["id"], "BUY_SIGNAL", f"{label} 매수 신호 {qty}주 @ {_won(price)} — {reason}")
        _log(f"분할매수 {name} {label} 매수 신호 {qty}주 @ {_won(price)}", stock_code=code)
        await _send_telegram(
            f"<b>[분할매수 · {label} 매수]</b> {html.escape(name)} ({code}) {qty}주 @ {_won(price)}\n{html.escape(reason)}"
        )
        return True, msg

    def _queue_sell(self, plan: Dict[str, Any], rules: List[str], qty: int, price: int, hold: int) -> None:
        store = get_ladder_store()
        plan["pending_sell"] = {
            "rules": rules, "qty": int(qty), "price": int(price), "hold": int(hold), "status": "QUEUED",
            "created_at": now_kst().isoformat(timespec="seconds"),
        }
        labels = " + ".join(SELL_RULE_LABEL.get(r, r) for r in rules)
        store.add_event(plan["id"], "SELL_QUEUED", f"매도 대기 {qty}주 / 보유 {hold}주 — {labels}")
        _log(f"분할매수 {plan.get('name')} 매도 대기 {qty}주 — {labels}", stock_code=plan["code"])
        asyncio.create_task(_send_telegram(
            f"<b>[분할매수 · 매도]</b> {html.escape(plan.get('name') or '')} ({plan['code']}) "
            f"{qty}주 / 보유 {hold}주 @ {_won(price)}\n{html.escape(labels)}"
        ))

    # ───────────────────────── 체결 대조 ─────────────────────────

    def reconcile(self) -> None:
        """PositionBuyFill · SellOrder · Position 으로 계획 상태 갱신 (동기 DB, 가벼움)."""
        from core.models import PendingBuySignal, Position, PositionBuyFill, SellOrder, get_db

        store = get_ladder_store()
        plans = self.my_plans(open_only=True)
        if not plans:
            return
        mine, _ = self._holdings()
        changed = False
        notes: List[str] = []
        for db in get_db():
            for plan in plans:
                code = plan["code"]
                pb = plan.get("pending_buy")
                if pb:
                    created = _parse_iso(pb.get("created_utc")) or _utc_naive()
                    used = {f.get("fill_id") for f in plan.get("fills") or []}
                    fill = (
                        db.query(PositionBuyFill)
                        .filter(PositionBuyFill.stock_code == code,
                                PositionBuyFill.filled_at >= created - timedelta(seconds=5))
                        .order_by(PositionBuyFill.id.asc())
                        .all()
                    )
                    fill = next((f for f in fill if f.id not in used), None)
                    if fill is not None:
                        self._apply_fill(plan, pb, fill, notes)
                        changed = True
                    else:
                        sig = (
                            db.query(PendingBuySignal)
                            .filter(PendingBuySignal.stock_code == code,
                                    PendingBuySignal.detected_date == now_kst().date())
                            .order_by(PendingBuySignal.id.desc())
                            .first()
                        )
                        age_min = (_utc_naive() - created).total_seconds() / 60.0
                        if sig is not None and sig.status in _FAILED_SIGNAL and age_min > 0.3:
                            plan["pending_buy"] = None
                            if not pb.get("extra"):
                                plan["last_buy_date"] = None
                            store.add_event(plan["id"], "BUY_FAIL",
                                            f"{pb.get('step')}차 매수 실패: {sig.failure_reason or sig.status}", save=False)
                            notes.append(f"{plan.get('name')} 매수 실패: {sig.failure_reason or sig.status}")
                            changed = True
                        elif age_min > _BUY_PENDING_TIMEOUT_MIN:
                            plan["pending_buy"] = None
                            store.add_event(plan["id"], "BUY_FAIL", f"{pb.get('step')}차 매수 체결 확인 시간 초과",
                                            save=False)
                            changed = True

                ps = plan.get("pending_sell")
                pos = mine.get(code)
                if ps and ps.get("status") == "SENT":
                    sent = _parse_iso(ps.get("sent_utc")) or _utc_naive()
                    q = db.query(SellOrder).filter(SellOrder.created_at >= sent - timedelta(seconds=10),
                                                   SellOrder.stock_code == code)
                    if pos:
                        q = q.filter(SellOrder.position_id == pos["id"])
                    so = q.order_by(SellOrder.id.desc()).first()
                    if so is None:
                        if (_utc_naive() - sent).total_seconds() / 60.0 > _SELL_SENT_RETRY_MIN:
                            ps["status"] = "QUEUED"
                            changed = True
                    elif so.status == "COMPLETED":
                        self._apply_sell(plan, ps, so)
                        changed = True
                    elif so.status in ("FAILED", "CANCELLED"):
                        for r in ps.get("rules") or []:
                            if r in ("S0", "S1", "S2", "S3"):
                                plan.setdefault("sell_flags", {}).pop(r, None)
                        plan["pending_sell"] = None
                        store.add_event(plan["id"], "SELL_FAIL", f"매도 실패: {so.sell_reason_detail or so.status}",
                                        save=False)
                        changed = True

                if (plan.get("fills") and not pos and not plan.get("pending_buy")
                        and not (plan.get("pending_sell") or {}).get("status") == "SENT"
                        and plan.get("status") != "DONE"):
                    closed = (
                        db.query(Position)
                        .filter(Position.stock_code == code, Position.strategy_key == STRATEGY_KEY,
                                Position.status != "HOLDING")
                        .order_by(Position.id.desc())
                        .first()
                    )
                    if closed is not None:
                        plan["status"] = "DONE"
                        plan["pending_sell"] = None
                        plan["closed_at"] = now_kst().isoformat(timespec="seconds")
                        store.add_event(plan["id"], "DONE", f"보유 0주 — 사이클 종료 ({closed.status})", save=False)
                        notes.append(f"{plan.get('name')} 사이클 종료")
                        changed = True
            break
        if changed:
            store.save()
            self._snap_cache.clear()
        for n in notes:
            _log(f"분할매수 {n}")

    def _apply_fill(self, plan: Dict[str, Any], pb: Dict[str, Any], fill, notes: List[str]) -> None:
        store = get_ladder_store()
        prof = merge_profile(plan.get("profile"))
        extra = bool(pb.get("extra"))
        step = 0 if extra else len(auto_fills(plan)) + 1
        plan.setdefault("fills", []).append({
            "step": step, "price": int(fill.price), "qty": int(fill.quantity), "fill_id": int(fill.id),
            "order_id": str(fill.order_id or ""), "exec_px": False,
            "at": now_kst().isoformat(timespec="seconds"), "extra": extra,
        })
        plan["pending_buy"] = None
        label = "수동 추가" if extra else f"{step}차"
        msg = f"{label} 체결 {int(fill.quantity)}주 @ {_won(fill.price)}"
        if plan.get("status") == "RSI_WAIT":
            plan["status"] = "ACTIVE"
        if not extra and step >= int(prof["max_steps"]) and plan.get("status") == "ACTIVE":
            plan["status"] = "FULL"
            msg += f" — {step}차 완료(FULL), 이후는 수동 판단"
            asyncio.create_task(_send_telegram(
                f"<b>[분할매수 · {step}차 완료]</b> {html.escape(plan.get('name') or '')} ({plan['code']}) "
                f"사다리 소진 — 추가 매수는 수동으로 판단하세요."
            ))
        store.add_event(plan["id"], "FILL", msg, save=False)
        notes.append(f"{plan.get('name')} {msg}")

    async def _refine_fill_prices(self) -> None:
        """체결 기록가(주문 직전 현재가)를 ka10076 실제 체결가로 보정 — 다음 차수 트리거 기준."""
        todo = [
            (plan, f) for plan in self.my_plans(open_only=True) for f in plan.get("fills") or []
            if f.get("order_id") and not f.get("exec_px") and int(f.get("px_tries") or 0) < _EXEC_PX_MAX_TRIES
        ]
        if not todo or time.time() < self._exec_px_after:
            return
        self._exec_px_after = time.time() + _EXEC_PX_INTERVAL_SEC
        from api.kiwoom_api import kiwoom_api

        store = get_ladder_store()
        rows_by_code: Dict[str, Optional[List[Dict[str, Any]]]] = {}
        for plan, f in todo:
            code = plan["code"]
            if code not in rows_by_code:
                try:
                    res = await kiwoom_api.get_executions(sell_tp="2", stk_cd=code)
                    rows_by_code[code] = res.get("items") or [] if res.get("success") else None
                except Exception as e:
                    logger.debug(f"[LADDER] 체결내역 조회 실패 {code}: {e}")
                    rows_by_code[code] = None
            rows = rows_by_code[code]
            if rows is None:
                continue
            f["px_tries"] = int(f.get("px_tries") or 0) + 1
            px = execution_avg_price(rows, f["order_id"])
            if not px:
                continue
            f["exec_px"] = True
            old = int(f.get("price") or 0)
            if px == old:
                continue
            f["quote_price"] = old
            f["price"] = px
            self._update_fill_row(f.get("fill_id"), px)
            label = "수동 추가" if f.get("extra") else f"{f.get('step')}차"
            store.add_event(plan["id"], "FILL_PX", f"{label} 체결가 보정 {_won(old)} → {_won(px)} (실제 체결가)",
                            save=False)
            _log(f"분할매수 {plan.get('name')} {label} 체결가 보정 {_won(old)} → {_won(px)}", stock_code=code)
        store.save()
        self._snap_cache.clear()

    @staticmethod
    def _update_fill_row(fill_id: Any, price: int) -> None:
        from core.models import PositionBuyFill, get_db

        if not fill_id:
            return
        try:
            for db in get_db():
                row = db.query(PositionBuyFill).filter(PositionBuyFill.id == int(fill_id)).first()
                if row is not None:
                    row.price = int(price)
                    row.amount = int(price) * int(row.quantity or 0)
                    db.commit()
                break
        except Exception as e:
            logger.debug(f"[LADDER] 체결 이력 가격 갱신 실패 {fill_id}: {e}")

    def _apply_sell(self, plan: Dict[str, Any], ps: Dict[str, Any], so) -> None:
        store = get_ladder_store()
        qty = int(so.sell_quantity or ps.get("sent_qty") or 0)
        plan.setdefault("sells", []).append({
            "rules": ps.get("rules"), "qty": qty, "price": int(so.sell_price or 0),
            "pnl": int(so.profit_loss or 0) if so.profit_loss is not None else None,
            "at": now_kst().isoformat(timespec="seconds"), "sell_order_id": int(so.id),
        })
        plan["pending_sell"] = None
        labels = " + ".join(SELL_RULE_LABEL.get(r, r) for r in ps.get("rules") or [])
        store.add_event(plan["id"], "SELL_FILL", f"매도 체결 {qty}주 @ {_won(so.sell_price)} — {labels}", save=False)

    # ───────────────────────── 수동 조작 (API) ─────────────────────────

    def _get_plan(self, plan_id: str) -> Dict[str, Any]:
        plan = get_ladder_store().get(plan_id)
        if not plan or plan.get("account_mode", _account_mode()) != _account_mode():
            raise LadderError("계획을 찾을 수 없습니다")
        return plan

    async def create_plan(self, *, code: str, name: str, budget: int, profile: Dict[str, Any],
                          memo: str = "", buy_now: bool = False) -> Dict[str, Any]:
        from utils.rwm_bowl import normalize_code

        store = get_ladder_store()
        code = normalize_code(code)
        if not code:
            raise LadderError("종목코드가 올바르지 않습니다")
        if any(p["code"] == code for p in self.my_plans(open_only=True)):
            raise LadderError("이미 진행 중인 사다리가 있습니다")
        if int(budget) <= 0:
            raise LadderError("예산을 입력하세요")
        _, others = self._holdings()
        if code in others:
            raise LadderError(f"다른 전략({others[code]})이 보유 중인 종목입니다")
        if not name:
            name = await self.resolve_name(code) or code
        plan = store.create_plan(code=code, name=name, budget=int(budget), profile=profile, memo=memo)
        plan["account_mode"] = _account_mode()
        store.save()
        result: Dict[str, Any] = {"plan": plan}
        if buy_now:
            try:
                result["buy"] = await self.manual_buy(plan["id"])
            except LadderError as e:
                result["buy_error"] = str(e)
        return result

    def update_plan(self, plan_id: str, *, budget: Optional[int] = None, memo: Optional[str] = None,
                    profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        store = get_ladder_store()
        plan = self._get_plan(plan_id)
        if budget is not None and int(budget) > 0:
            plan["budget"] = int(budget)
        if memo is not None:
            plan["memo"] = memo
        if profile:
            merged = dict(plan.get("profile") or {})
            merged.update(profile)
            plan["profile"] = merge_profile(merged)
        store.add_event(plan_id, "UPDATE", "설정 변경")
        self._snap_cache.pop(plan_id, None)
        return plan

    def pause(self, plan_id: str) -> Dict[str, Any]:
        plan = self._get_plan(plan_id)
        if plan["status"] in ("PAUSED", "DONE"):
            return plan
        plan["paused_from"] = plan["status"]
        plan["status"] = "PAUSED"
        get_ladder_store().add_event(plan_id, "PAUSE", "일시정지")
        return plan

    def resume(self, plan_id: str) -> Dict[str, Any]:
        plan = self._get_plan(plan_id)
        if plan["status"] != "PAUSED":
            return plan
        prev = plan.get("paused_from") or ("ACTIVE" if plan.get("fills") else "RSI_WAIT")
        plan["status"] = prev
        plan["paused_from"] = None
        get_ladder_store().add_event(plan_id, "RESUME", f"재개 → {prev}")
        return plan

    def delete(self, plan_id: str) -> None:
        plan = self._get_plan(plan_id)
        mine, _ = self._holdings()
        if plan.get("status") != "DONE" and plan["code"] in mine:
            raise LadderError("보유 중인 계획은 삭제할 수 없습니다 (전량 매도 후 삭제)")
        get_ladder_store().delete_plan(plan_id)

    def finish(self, plan_id: str) -> Dict[str, Any]:
        """보유 없는 계획을 종료 처리 (기록 유지)."""
        plan = self._get_plan(plan_id)
        mine, _ = self._holdings()
        if plan["code"] in mine:
            raise LadderError("보유 중 — 먼저 전량 매도하세요")
        plan["status"] = "DONE"
        plan["closed_at"] = now_kst().isoformat(timespec="seconds")
        get_ladder_store().add_event(plan_id, "DONE", "수동 종료")
        return plan

    async def manual_buy(self, plan_id: str) -> Dict[str, Any]:
        from utils.market_hours import is_krx_session

        plan = self._get_plan(plan_id)
        if plan["status"] in ("DONE", "PAUSED", "EXITING"):
            raise LadderError(f"{plan['status']} 상태에서는 매수할 수 없습니다")
        if not is_krx_session():
            raise LadderError("정규장(09:00~15:20)에만 수동 매수할 수 있습니다")
        price = await self._price(plan["code"])
        if not price:
            raise LadderError("현재가 조회 실패")
        qty = step_qty(plan, price)
        n_auto = len(auto_fills(plan))
        max_steps = int(merge_profile(plan.get("profile"))["max_steps"])
        extra = n_auto >= max_steps
        step = 0 if extra else n_auto + 1
        reason = "수동 추가 매수 (사다리 소진 후)" if extra else f"{step}차 수동 매수"
        async with self._lock:
            ok, msg = await self._submit_buy(plan, step=step, qty=qty, price=price, reason=reason, extra=extra)
        if not ok:
            raise LadderError(msg)
        return {"step": step, "qty": qty, "price": price, "message": msg}

    async def manual_sell(self, plan_id: str, mode: str) -> Dict[str, Any]:
        from utils.market_hours import is_krx_session

        plan = self._get_plan(plan_id)
        if not is_krx_session():
            raise LadderError("정규장에만 수동 매도할 수 있습니다")
        if (plan.get("pending_sell") or {}).get("status") in ("QUEUED", "SENT"):
            raise LadderError("이미 매도 대기 중입니다")
        mine, _ = self._holdings()
        pos = mine.get(plan["code"])
        if not pos or pos["qty"] <= 0:
            raise LadderError("보유 수량이 없습니다")
        hold = int(pos["qty"])
        if mode == "all":
            rule, qty = "MANUAL_ALL", hold
        else:
            rule, qty = "MANUAL_HALF", max(1, hold // 2)
        price = await self._price(plan["code"]) or pos.get("current_price") or 0
        self._queue_sell(plan, [rule], qty, int(price), hold)
        if mode == "all" and plan["status"] in ("ACTIVE", "FULL", "RSI_WAIT"):
            plan["status"] = "EXITING"
        get_ladder_store().save()
        return {"rule": rule, "qty": qty, "hold": hold}

    def set_enabled(self, on: bool) -> bool:
        get_ladder_store().set_enabled(on)
        return get_ladder_store().enabled

    # ───────────────────────── UI 스냅샷 ─────────────────────────

    async def plan_view(self, plan: Dict[str, Any], mine: Dict[str, Dict[str, Any]], *,
                        force: bool = False) -> Dict[str, Any]:
        pid = plan["id"]
        cached = self._snap_cache.get(pid)
        if cached and not force and time.time() - cached[0] < _SNAPSHOT_TTL:
            market = cached[1]
        else:
            market = await self._market_view(plan)
            self._snap_cache[pid] = (time.time(), market)
        pos = mine.get(plan["code"]) or {}
        hold, avg = int(pos.get("qty") or 0), int(pos.get("avg") or 0)
        price = market.get("price") or pos.get("current_price") or 0
        prof = merge_profile(plan.get("profile"))
        lp = last_fill_price(plan)
        trig = next_trigger(lp, prof["step_pct"]) if lp else None
        exit_preview = None
        if hold > 0 and avg > 0 and price and market.get("ma"):
            ex = evaluate_exit(plan, price=float(price), avg=float(avg), hold=hold, ma=market["ma"])
            exit_preview = {"sells": ex["sells"], "armed": ex["armed"], "peak_pct": round(ex["peak_pct"], 2)}
        invested = sum(int(f["price"]) * int(f["qty"]) for f in plan.get("fills") or [])
        return {
            **plan,
            "hold": hold,
            "avg": avg,
            "price": price,
            "pnl_pct": round((price / avg - 1) * 100, 2) if avg and price else None,
            "eval_amount": hold * int(price or 0),
            "invested": invested,
            "auto_steps": len(auto_fills(plan)),
            "next_trigger": trig if plan.get("status") == "ACTIVE" else None,
            "next_qty": step_qty(plan, trig) if trig else None,
            "market": market,
            "exit_preview": exit_preview,
        }

    async def _market_view(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        if plan.get("status") == "DONE":
            return {}
        now = now_kst()
        try:
            price, bars, weekly = await self._market_inputs(plan["code"], now)
        except Exception as e:
            return {"error": f"시세 조회 실패: {e}"}
        if not bars:
            return {"error": "일봉 없음", "price": price}
        from utils.rwm_bowl import sma_series

        closes = [float(b["close"]) for b in bars]
        entry = evaluate_entry(bars, plan.get("profile") or {}, weekly_bars=weekly) if len(bars) >= 61 else None
        tail = 120
        series = {f"ma{n}": [round(v, 1) if v is not None else None for v in sma_series(closes, n)[-tail:]]
                  for n in (10, 15, 20, 60)}
        return {
            "price": price,
            "ma": moving_averages(closes),
            "entry": entry,
            "chart": {
                "dates": [b["date"].isoformat() for b in bars[-tail:]],
                "close": [round(c, 1) for c in closes[-tail:]],
                **series,
            },
        }

    async def state(self, *, force: bool = False, plan_id: Optional[str] = None) -> Dict[str, Any]:
        store = get_ladder_store()
        mine, _ = self._holdings()
        plans = self.my_plans()
        views = []
        for p in plans:
            if plan_id and p["id"] != plan_id:
                continue
            views.append(await self.plan_view(p, mine, force=force))
        settings = _load_settings()
        now = now_kst()
        return {
            "enabled": store.enabled,
            "auto_trade_on": bool(getattr(settings, "is_enabled", False)) if settings else False,
            "account_mode": _account_mode(),
            "now": now.isoformat(timespec="seconds"),
            "judge_time": f"{JUDGE_HM[0]:02d}:{JUDGE_HM[1]:02d}",
            "last_judge": store.data.get("last_judge") or {},
            "plans": views,
            "events": list(reversed((store.data.get("events") or [])[-100:])),
        }

    # ───────────────────────── 장후 요약 ─────────────────────────

    async def _send_summary(self) -> None:
        mine, _ = self._holdings()
        plans = self.my_plans(open_only=True)
        if not plans:
            return
        lines = ["<b>[분할매수 · 장후 요약]</b>"]
        for p in plans:
            pos = mine.get(p["code"]) or {}
            hold, avg = pos.get("qty") or 0, pos.get("avg") or 0
            cur = pos.get("current_price") or 0
            pnl = f"{(cur / avg - 1) * 100:+.1f}%" if avg and cur else "-"
            lines.append(
                f"• {html.escape(p.get('name') or p['code'])} {p['status']} "
                f"{len(auto_fills(p))}/{merge_profile(p.get('profile'))['max_steps']}차 · "
                f"{hold}주 · 평단 {_won(avg)} · {pnl}"
            )
        await _send_telegram("\n".join(lines))


rsi_ladder_scheduler = RsiLadderScheduler()
