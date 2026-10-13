"""RWM 밥그릇 스케줄러 (PRD_RWM_BOWL v0.4.0).

- 15:20 조건식 사전 조회 (장 마감 후 조건검색 실패 대비 캐시)
- 15:40 장후 배치: 조건식 ∪ 바닥 후보 풀 ∪ 수동 ∪ 보유 → 확정 일봉 판정 → 익일 감시 목록 · 보유 청산 판정 · 텔레그램
- 08:40~13:30 캐치업: 전 거래일 배치를 놓쳤으면 그 날 확정 일봉으로 1회 실행
- 장중 3분봉 감시: 감시 종목 EMA20 눌림 반등 확정 봉에서 BUY 신호 생성
- 09:30~15:00 30분마다 장중 신호 예상: 바닥 후보 풀·수동 종목을 현재가 기준으로 재판정 (표시·알림만, 매매 없음)
- 15:00 보유 종목 청산선(MA20, 급등 후 MA10) 1% 이내 접근 경고
- 15:10 급등(+15% 양봉) 이후 보유 종목이 현재가 기준 10일선 아래면 당일 장중 전량 익절
"""
from __future__ import annotations

import asyncio
import html
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from utils.datetime_kst import now_kst

logger = logging.getLogger(__name__)

_LOOP_SEC = 10
_PREFETCH_HM = (15, 20)
_PREVIEW_FROM_HM = (9, 30)
_PREFETCH_RETRY_SEC = 60.0
_BATCH_HM = (15, 40)
_CATCHUP_FROM_HM = (8, 40)
_CATCHUP_TO_HM = (13, 30)
_NEAR_WARN_HM = (15, 0)
_NEAR_WARN_PCT = 1.0
_DAILY_MAX_BARS = 600
_MINUTE_MAX_BARS = 400
_MINUTE_CACHE_TTL = 5.0
_MINUTE_RETRY_SEC = 15.0
_BAR_MIN = 3


def _hm(now: datetime) -> Tuple[int, int]:
    return (now.hour, now.minute)


def _parse_hm(s: Any, default: Tuple[int, int] = (15, 10)) -> Tuple[int, int]:
    try:
        h, m = str(s).split(":")
        return (int(h), int(m))
    except (TypeError, ValueError):
        return default


def _load_settings():
    from core.models import get_auto_trade_settings_row, get_db

    for db in get_db():
        return get_auto_trade_settings_row(db)
    return None


def _fmt_won(v: Any) -> str:
    try:
        return f"{int(round(float(v))):,}"
    except (TypeError, ValueError):
        return "-"


def _md(d: Any) -> str:
    s = str(d or "")
    return f"{s[5:7]}/{s[8:10]}" if len(s) >= 10 else s


async def _send_telegram(text: str) -> None:
    try:
        from notifications.telegram_notifier import TelegramNotifier

        tg = TelegramNotifier()
        if not tg.is_configured():
            return
        await asyncio.to_thread(tg.send_message, text, "HTML")
    except Exception as e:
        logger.debug(f"[RWM] 텔레그램 전송 실패: {e}")


def _log(msg: str, level: str = "info", stock_code: Optional[str] = None) -> None:
    try:
        from utils.auto_trade_activity_log import log_activity

        log_activity("SCANNER", msg, level, stock_code=stock_code, strategy="rwm_bowl")
    except Exception:
        pass


class RwmBowlScheduler:
    def __init__(self) -> None:
        self.is_running = False
        self._task: Optional[asyncio.Task] = None
        self._batch_lock = asyncio.Lock()
        self._fail_until = 0.0
        self._minute_next_fetch: Dict[str, float] = {}
        self._preview_task: Optional[asyncio.Task] = None
        self._preview_slot = ""
        self._surge_checked: Dict[int, str] = {}

    # ───────────────────────── lifecycle ─────────────────────────

    async def start_scheduler(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("🥣 [RWM] RWM 밥그릇 스케줄러 시작")

    async def stop_scheduler(self) -> None:
        self.is_running = False
        if self._preview_task and not self._preview_task.done():
            self._preview_task.cancel()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("🥣 [RWM] RWM 밥그릇 스케줄러 중지")

    async def _loop(self) -> None:
        import time

        while self.is_running:
            try:
                if time.time() >= self._fail_until:
                    await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._fail_until = time.time() + 120
                logger.warning(f"🥣 [RWM] 스케줄러 오류, 2분 후 재시도: {e}")
            await asyncio.sleep(_LOOP_SEC)

    async def _tick(self) -> None:
        from utils.market_hours import is_krx_trading_day
        from utils.rwm_bowl import get_state_store, params_from_settings, prev_trading_day

        now = now_kst()
        if not is_krx_trading_day(now):
            return
        settings = _load_settings()
        if not settings or not getattr(settings, "use_rwm_bowl", False):
            return
        store = get_state_store()
        today = now.date()
        last_sig = str((store.data.get("last_batch") or {}).get("signal_date") or "")

        if _BATCH_HM <= _hm(now):
            if last_sig != today.isoformat():
                await self.run_batch(trigger="15:40")
            return
        if _PREFETCH_HM <= _hm(now) < _BATCH_HM:
            await self._prefetch_condition(settings, today)
        if _NEAR_WARN_HM <= _hm(now) < (15, 30):
            await self._warn_near_ma20(settings, today)
        if _parse_hm(params_from_settings(settings).get("surge_exit_hm")) <= _hm(now) < _PREFETCH_HM:
            await self._check_surge_exits(settings, today)
        if _CATCHUP_FROM_HM <= _hm(now) <= _CATCHUP_TO_HM:
            expected = prev_trading_day(today).isoformat()
            if last_sig < expected:
                await self.run_batch(trigger="catchup")
        if (9, 0) <= _hm(now) < (15, 30):
            await self._watch_pullbacks(settings, now)
        if _PREVIEW_FROM_HM <= _hm(now) < _PREFETCH_HM:
            self._maybe_start_preview(settings, now)

    # ───────────────────────── 장중 신호 예상 (바닥 후보 풀 미리보기) ─────────────────────────

    def _maybe_start_preview(self, settings, now: datetime) -> None:
        """preview_interval_min 마다 1회. 별도 태스크로 돌려 3분봉 눌림 감시를 막지 않는다."""
        from utils.rwm_bowl import params_from_settings

        p = params_from_settings(settings)
        if not p.get("intraday_preview", True):
            return
        if self._preview_task and not self._preview_task.done():
            return
        step = max(5, int(p["preview_interval_min"]))
        mins = now.hour * 60 + now.minute
        slot_m = mins - (mins - 9 * 60) % step
        slot = f"{now.date().isoformat()} {slot_m // 60:02d}:{slot_m % 60:02d}"
        if slot == self._preview_slot:
            return
        self._preview_slot = slot
        self._preview_task = asyncio.create_task(self._run_preview(settings, slot[-5:]))

    async def _run_preview(self, settings, slot_hm: str) -> None:
        """바닥 후보 풀 + 수동 종목을 오늘 진행 중 일봉(현재가 = 종가 가정)으로 재판정. 매매에는 쓰지 않음."""
        from api.kiwoom_api import kiwoom_api
        from utils.rwm_bowl import (
            M_TYPE_KO,
            REASON_KO,
            evaluate_signal,
            get_state_store,
            is_week_closed,
            normalize_daily_bars,
            params_from_settings,
            parse_code_list,
        )

        try:
            now = now_kst()
            today = now.date()
            store = get_state_store()
            p = params_from_settings(settings)
            _, buys, all_held = self._rwm_positions()
            watching = set(store.active_watch(today).keys())
            names = store.data.get("names") or {}
            codes: Dict[str, str] = {
                code: row.get("name") or names.get(code) or "" for code, row in store.r_pool().items()
            }
            for code in parse_code_list(getattr(settings, "rwm_manual_codes", None))[: int(p["max_manual_codes"])]:
                codes.setdefault(code, names.get(code) or "")
            week_closed = is_week_closed(today)

            rows: Dict[str, Dict[str, Any]] = {}
            for code, name in codes.items():
                if code in all_held or code in watching:
                    continue
                raw = await kiwoom_api.get_stock_chart_data(
                    code, "1D", max_bars=_DAILY_MAX_BARS, allow_off_hours=True, cache_ttl_sec=60,
                )
                bars = normalize_daily_bars(raw or [])
                if not bars or bars[-1]["date"] != today:
                    continue
                dates = buys.get(code) or []
                sig = evaluate_signal(
                    bars, p, last_week_closed=week_closed,
                    cycle_bought=lambda bowl_d, ds=dates: any(d >= bowl_d for d in ds),
                )
                ck = sig.get("checks") or {}
                rows[code] = {
                    "name": name,
                    "ok": bool(sig.get("ok")),
                    "reason": sig.get("reason"),
                    "reason_ko": REASON_KO.get(str(sig.get("reason") or ""), sig.get("reason")),
                    "checks": {k: ck.get(k) for k in ("r_weekly", "r_daily", "w", "m_type", "ma60_ok", "macd_gc")},
                    "price": sig.get("signal_close"),
                    "day_return_pct": sig.get("day_return_pct"),
                    "macd": sig.get("macd"),
                    "macd_signal": sig.get("macd_signal"),
                    "macd_gc_date": sig.get("macd_gc_date"),
                    "m_type": sig.get("m_type"),
                    "gap_pct": sig.get("gap_pct"),
                }

            prev = store.data.get("preview") or {}
            alerted = set(prev.get("alerted") or []) if prev.get("date") == today.isoformat() else set()
            new_ok = [c for c, r in rows.items() if r["ok"] and c not in alerted]
            store.data["preview"] = {
                "date": today.isoformat(),
                "slot": slot_hm,
                "at": now.isoformat(timespec="seconds"),
                "rows": rows,
                "alerted": sorted(alerted | set(new_ok)),
            }
            store.save()
            n_ok = sum(1 for r in rows.values() if r["ok"])
            logger.info(f"🥣 [RWM] 장중 신호 예상 {slot_hm} — 판정 {len(rows)} · 예상 {n_ok} · 신규 {len(new_ok)}")

            if new_ok and p.get("preview_alert", True):
                lines = []
                for c in new_ok:
                    r = rows[c]
                    ret = r.get("day_return_pct")
                    ret_s = f" ({ret:+.1f}%)" if isinstance(ret, (int, float)) else ""
                    lines.append(
                        f"{html.escape(r['name'] or c)} ({c}) 현재가 {_fmt_won(r.get('price'))}{ret_s} · "
                        f"MACD GC {_md(r.get('macd_gc_date'))} · {M_TYPE_KO.get(r.get('m_type') or '', '-')}"
                    )
                await _send_telegram(
                    f"<b>[RWM 밥그릇 · 장중 신호 예상 {slot_hm}]</b>\n"
                    + "\n".join(lines)
                    + "\n현재가로 마감하면 15:40 판정에서 매수 감시 등록 예상 (확정 아님 · 주문 없음)"
                )
        except Exception as e:
            logger.warning(f"🥣 [RWM] 장중 신호 예상 오류: {e}")

    # ───────────────────────── 유니버스 ─────────────────────────

    async def _prefetch_condition(self, settings, today: date) -> None:
        from api.kiwoom_api import kiwoom_api
        from utils.rwm_bowl import DEFAULT_CONDITION_NAME, get_state_store
        from utils.screener_targets import fetch_condition_target_items, parse_condition_names

        store = get_state_store()
        cache = store.data.get("condition_cache") or {}
        if cache.get("date") == today.isoformat():
            if cache.get("items") or not cache.get("errors"):
                return
            try:
                if (now_kst() - datetime.fromisoformat(str(cache.get("at")))).total_seconds() < _PREFETCH_RETRY_SEC:
                    return
            except (TypeError, ValueError):
                pass
        names = parse_condition_names(getattr(settings, "rwm_condition_names", None) or DEFAULT_CONDITION_NAME)
        items, errors = await fetch_condition_target_items(kiwoom_api, names)
        store.data["condition_cache"] = {
            "date": today.isoformat(),
            "at": now_kst().isoformat(timespec="seconds"),
            "items": [
                {k: it.get(k) for k in ("stock_code", "stock_name", "current_price", "volume", "product_type")}
                for it in items
            ],
            "errors": errors,
        }
        store.save()
        logger.info(f"🥣 [RWM] 15:20 조건식 사전 조회 {len(items)}종목 (실패 {errors or '-'})")

    async def _collect_universe(
        self, settings, p: Dict[str, Any], today: date, held_codes: Dict[str, str],
    ) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
        """code → {name, source(condition|pool|manual|both|holding)}. 조건식은 유동성 프록시 순 상위 N."""
        from api.kiwoom_api import KiwoomAPI, kiwoom_api
        from utils.rwm_bowl import (
            DEFAULT_CONDITION_NAME,
            get_state_store,
            parse_code_list,
            trading_days_back,
            update_r_pool,
        )
        from utils.screener_targets import fetch_condition_target_items, parse_condition_names

        store = get_state_store()
        names_cache: Dict[str, str] = store.data.setdefault("names", {})
        errors: List[str] = []
        names = parse_condition_names(getattr(settings, "rwm_condition_names", None) or DEFAULT_CONDITION_NAME)
        items: List[Dict[str, Any]] = []
        try:
            items, errors = await fetch_condition_target_items(kiwoom_api, names)
        except Exception as e:
            errors = list(names)
            logger.warning(f"🥣 [RWM] 조건식 조회 실패: {e}")
        if not items:
            cache = store.data.get("condition_cache") or {}
            if cache.get("date") == today.isoformat() and cache.get("items"):
                items = list(cache["items"])
                logger.info(f"🥣 [RWM] 장후 조건식 결과 없음 → 15:20 캐시 {len(items)}종목 사용")

        cond: List[Dict[str, Any]] = []
        for it in items:
            code = KiwoomAPI.normalize_stock_code(str(it.get("stock_code") or ""))
            nm = str(it.get("stock_name") or "")
            if not code or not KiwoomAPI._is_screener_stock(nm, it.get("product_type")):
                continue
            liq = float(it.get("current_price") or 0) * float(it.get("volume") or 0)
            cond.append({"code": code, "name": nm, "liq": liq})
            if nm:
                names_cache[code] = nm
        cond.sort(key=lambda r: -r["liq"])
        cond = cond[: int(p["max_l0_candidates"])]

        pool = store.r_pool()
        removed = update_r_pool(
            pool,
            {r["code"]: r["name"] for r in cond},
            today,
            keep_from=trading_days_back(today, int(p["bowl_lookback"]) - 1),
            max_codes=int(p["max_pool_codes"]),
        )
        if removed:
            logger.info(f"🥣 [RWM] 바닥 후보 풀 만료 {len(removed)}종목")

        out: Dict[str, Dict[str, Any]] = {}
        for r in cond:
            out[r["code"]] = {"name": r["name"], "source": "condition"}
        for code, row in pool.items():
            if code not in out:
                out[code] = {"name": row.get("name") or names_cache.get(code) or "", "source": "pool"}
        manual = parse_code_list(getattr(settings, "rwm_manual_codes", None))[: int(p["max_manual_codes"])]
        if p.get("include_global_watchlist"):
            manual += [c for c in parse_code_list(getattr(settings, "watchlist_codes", None)) if c not in manual]
        for code in manual:
            if code in out:
                out[code]["source"] = "both"
            else:
                out[code] = {"name": names_cache.get(code) or "", "source": "manual"}
        for code, nm in held_codes.items():
            if code not in out:
                out[code] = {"name": nm, "source": "holding"}
            elif nm and not out[code]["name"]:
                out[code]["name"] = nm
        for code, row in out.items():
            if not row["name"]:
                row["name"] = await self.resolve_name(code)
        if errors:
            await _send_telegram(
                "[RWM 밥그릇] 조건식 조회 실패: "
                + html.escape(", ".join(errors))
                + " — 수동·보유 종목만으로 판정합니다."
            )
        return out, errors

    async def resolve_name(self, code: str) -> str:
        from api.kiwoom_api import kiwoom_api
        from utils.rwm_bowl import get_state_store

        store = get_state_store()
        cache: Dict[str, str] = store.data.setdefault("names", {})
        if cache.get(code):
            return cache[code]
        name = ""
        # ka10006(스냅샷) 응답에는 stk_nm 이 없어 ka10001(주식기본정보)로 조회
        try:
            resp = await kiwoom_api._request_stockinfo_tr("ka10001", {"stk_cd": code})
            if resp.get("success"):
                name = str((resp.get("data") or {}).get("stk_nm") or "").strip()
        except Exception:
            name = ""
        if not name:
            name = self._name_from_fundamental(code)
        if name:
            cache[code] = name
            store.save()
        return name

    @staticmethod
    def _name_from_fundamental(code: str) -> str:
        from core.models import FundamentalSnapshot, get_db

        for db in get_db():
            row = (
                db.query(FundamentalSnapshot.stock_name)
                .filter(FundamentalSnapshot.stock_code == code)
                .order_by(FundamentalSnapshot.as_of_date.desc())
                .first()
            )
            return str(row[0]).strip() if row and row[0] else ""
        return ""

    # ───────────────────────── DB 조회 ─────────────────────────

    @staticmethod
    def _rwm_positions() -> Tuple[List[Dict[str, Any]], Dict[str, List[date]], set]:
        """(RWM HOLDING 행, code → RWM 매수일 목록(전 기간), 전 전략 HOLDING 코드)."""
        from core.config import get_account_mode
        from core.models import Position, get_db
        from utils.position_peak_since_buy import buy_time_utc_naive_to_kst

        mode = get_account_mode()
        holding: List[Dict[str, Any]] = []
        buys: Dict[str, List[date]] = {}
        all_held: set = set()
        for db in get_db():
            for pos in db.query(Position).all():
                if getattr(pos, "account_mode", None) not in (None, mode):
                    continue
                code = str(pos.stock_code or "").strip()
                if pos.status == "HOLDING" and code:
                    all_held.add(code)
                if str(pos.strategy_key or "").strip().lower() != "rwm_bowl":
                    continue
                bt = buy_time_utc_naive_to_kst(pos.buy_time)
                if bt is not None:
                    buys.setdefault(code, []).append(bt.date())
                if pos.status == "HOLDING":
                    holding.append({
                        "id": int(pos.id),
                        "code": code,
                        "name": pos.stock_name or code,
                        "buy_price": int(pos.buy_price or 0),
                        "qty": int(pos.buy_quantity or 0),
                        "entry_date": bt.date() if bt else None,
                        "stop_loss_price": int(pos.stop_loss_price or 0),
                    })
            break
        return holding, buys, all_held

    @staticmethod
    def _set_stop_price(position_id: int, price: int) -> None:
        from core.models import Position, get_db

        for db in get_db():
            pos = db.query(Position).filter(Position.id == position_id).first()
            if pos and pos.status == "HOLDING" and price > 0:
                pos.stop_loss_price = int(price)
                if pos.buy_price:
                    pos.stop_loss_rate = round(abs(pos.buy_price - price) / pos.buy_price * 100.0, 4)
                db.commit()
            break

    # ───────────────────────── 일봉 ─────────────────────────

    async def _daily_bars(self, code: str, now: datetime) -> List[Dict[str, Any]]:
        from api.kiwoom_api import kiwoom_api
        from utils.rwm_bowl import drop_unconfirmed_daily, normalize_daily_bars

        raw = await kiwoom_api.get_stock_chart_data(
            code, "1D", max_bars=_DAILY_MAX_BARS, allow_off_hours=True, cache_ttl_sec=60,
        )
        return drop_unconfirmed_daily(normalize_daily_bars(raw or []), now.replace(tzinfo=None))

    @staticmethod
    async def _equity() -> int:
        try:
            from managers.buy_order_executor import buy_order_executor

            info = await buy_order_executor._get_account_info()
            if not info:
                return 0
            raw = info.get("raw") or {}
            try:
                eq = int(float(str(raw.get("prsm_dpst_aset_amt") or 0).replace(",", "")))
            except (TypeError, ValueError):
                eq = 0
            return eq if eq > 0 else int(info.get("deposit") or 0)
        except Exception:
            return 0

    # ───────────────────────── 장후 배치 ─────────────────────────

    async def run_batch(self, *, trigger: str = "manual") -> Dict[str, Any]:
        import time

        if self._batch_lock.locked():
            return {"ok": False, "message": "배치 실행 중"}
        async with self._batch_lock:
            try:
                return await self._run_batch(trigger)
            except Exception as e:
                logger.error(f"🥣 [RWM] 배치 오류: {e}", exc_info=True)
                self._fail_until = time.time() + 300
                return {"ok": False, "message": str(e)}

    async def _run_batch(self, trigger: str) -> Dict[str, Any]:
        from core.models import get_db
        from utils.auto_trade_engine import _count_strategy_slots, effective_rwm_max_slots
        from utils.rwm_bowl import (
            M_TYPE_KO,
            REASON_KO,
            default_is_trading_day,
            entry_cap,
            evaluate_daily_exit,
            evaluate_signal,
            get_state_store,
            hard_stop_price,
            is_week_closed,
            manual_status_line,
            next_trading_day,
            params_from_settings,
            prev_trading_day,
            signal_priority_key,
            size_position,
        )

        now = now_kst()
        today = now.date()
        signal_day = (
            today if default_is_trading_day(today) and _hm(now) >= _BATCH_HM else prev_trading_day(today)
        )
        settings = _load_settings()
        p = params_from_settings(settings)
        store = get_state_store()
        logger.info(f"🥣 [RWM] 장후 배치 시작 ({trigger}) — 신호일 {signal_day}")

        holding, buys, all_held = self._rwm_positions()
        held_names = {h["code"]: h["name"] for h in holding}
        universe, cond_errors = await self._collect_universe(settings, p, today, held_names)
        valid_date = next_trading_day(signal_day)
        last_week_closed = is_week_closed(signal_day)

        # ── 보유 청산 판정 + 하드 손절가 갱신 ──
        exit_msgs: List[str] = []
        holding_ids = {h["id"] for h in holding}
        for pid in [k for k in list(store.exits().keys()) if int(k) not in holding_ids]:
            store.exits().pop(pid, None)
        hold_mode: Dict[str, Dict[str, Any]] = {}
        store.data["hold_mode"] = hold_mode
        bars_cache: Dict[str, List[Dict[str, Any]]] = {}
        for h in holding:
            bars = await self._daily_bars(h["code"], now)
            bars_cache[h["code"]] = bars
            if not bars or bars[-1]["date"] != signal_day:
                logger.warning(f"🥣 [RWM] 보유 {h['name']} 일봉 미확정 — 청산 판정 생략")
                continue
            ex = evaluate_daily_exit(bars, p, entry_date=h["entry_date"])
            if ex.get("hard_price"):
                self._set_stop_price(h["id"], int(ex["hard_price"]))
            hold_mode[str(h["id"])] = {k: ex.get(k) for k in ("surge_date", "exit_ma_n", "exit_ma")}
            if ex.get("reason") and not store.pending_exit(h["id"]):
                ma_n = ex["exit_ma_n"] if ex["reason"] == "EXIT_MA10" else int(p["exit_ma"])
                ma_v = ex.get("exit_ma") if ex["reason"] == "EXIT_MA10" else ex.get("ma20")
                row = {
                    "reason": ex["reason"],
                    "code": h["code"],
                    "name": h["name"],
                    "judged_date": signal_day.isoformat(),
                    "exec_from": valid_date.isoformat(),
                    "ma20": ex.get("ma20"),
                    "ma_n": ma_n,
                    "ma": ma_v,
                    "close": ex.get("close"),
                    "hold_days": ex.get("hold_days"),
                }
                store.set_exit(h["id"], row)
                exit_msgs.append(
                    f"{html.escape(h['name'])} ({h['code']}) — {REASON_KO.get(ex['reason'], ex['reason'])}: "
                    f"종가 {_fmt_won(ex.get('close'))} / MA{ma_n} {_fmt_won(ma_v)} → {_md(valid_date.isoformat())} 시가 전량"
                )
                _log(f"RWM 청산 예약 {h['name']}: {ex['reason']}", "warn", stock_code=h["code"])

        # ── 신호 판정 ──
        def _cycle_bought_for(code: str):
            dates = buys.get(code) or []
            return lambda bowl_d: any(d >= bowl_d for d in dates)

        results: Dict[str, Dict[str, Any]] = {}
        insufficient = 0
        for code, meta in universe.items():
            if meta["source"] == "holding":
                continue
            bars = bars_cache.get(code)
            if bars is None:
                bars = await self._daily_bars(code, now)
            if not bars or bars[-1]["date"] != signal_day:
                results[code] = {"ok": False, "reason": "INSUFFICIENT_BARS", "bars": len(bars or [])}
                insufficient += 1
                continue
            sig = evaluate_signal(bars, p, last_week_closed=last_week_closed, cycle_bought=_cycle_bought_for(code))
            results[code] = sig

        ok_rows = [(c, s) for c, s in results.items() if s.get("ok")]
        ok_rows.sort(key=lambda cs: signal_priority_key(cs[1]))
        slots_used = 0
        for db in get_db():
            slots_used = _count_strategy_slots(db, "rwm_bowl")
            break
        free = max(0, effective_rwm_max_slots(settings) - slots_used)
        daily_cap = max(0, int(p["max_new_per_day"]))
        store.prune_watch(valid_date)
        existing = set(store.active_watch(valid_date).keys())
        equity = await self._equity() if ok_rows else 0

        watch_added: List[Tuple[str, Dict[str, Any]]] = []
        for code, sig in ok_rows:
            if code in existing:
                continue
            if code in all_held:
                sig["ok"], sig["reason"] = False, "ALREADY_IN_POSITION"
                continue
            n_watch = len(existing) + len(watch_added)
            if n_watch >= free:
                sig["ok"], sig["reason"] = False, "SLOT_FULL"
                continue
            if n_watch >= daily_cap:
                sig["ok"], sig["reason"] = False, "DAILY_NEW_LIMIT"
                continue
            ma20 = float(sig.get("ma20") or 0)
            cap = entry_cap(sig["signal_close"], p)
            hard = hard_stop_price(ma20, p)
            qty_est = 0
            if equity > 0:
                qty_est = int(size_position(
                    equity, sig["signal_close"], hard, p,
                    max_invest_amount=int(getattr(settings, "rwm_max_invest_amount", None) or 0),
                ).get("qty") or 0)
            row = {
                "status": "WATCH",
                "code": code,
                "name": universe[code]["name"],
                "universe_source": universe[code]["source"],
                "valid_date": valid_date.isoformat(),
                "signal_date": sig["signal_date"],
                "signal_close": sig["signal_close"],
                "ma20_d": ma20,
                "entry_cap": cap,
                "hard_price": hard,
                "qty_est": qty_est,
                "created_at": now.isoformat(timespec="seconds"),
                "sig": {k: v for k, v in sig.items() if k not in ("checks",)},
            }
            store.watch_rows()[code] = row
            watch_added.append((code, row))
        store.save()

        # ── M 대기 목록 (R·W 통과, M(MACD)·20/60 이평만 미달) ──
        near: List[Dict[str, Any]] = []
        for code, sig in results.items():
            ck = sig.get("checks") or {}
            if sig.get("ok") or not (ck.get("r_weekly") and ck.get("r_daily") and ck.get("w")):
                continue
            if sig.get("reason") in ("NO_MA_SETUP", "MA60_DOWN", "NO_MACD_GC"):
                near.append({
                    "code": code,
                    "name": universe[code]["name"],
                    "source": universe[code]["source"],
                    "reason": sig.get("reason"),
                    "checks": {k: ck.get(k) for k in ("m_type", "ma60_ok", "macd_gc")},
                    "gap_pct": sig.get("gap_pct"),
                    "ma60_slope_pct": sig.get("ma60_slope_pct"),
                    "macd": sig.get("macd"),
                    "macd_signal": sig.get("macd_signal"),
                    "signal_close": sig.get("signal_close"),
                })
        store.data["near"] = near

        for code, row in store.r_pool().items():
            sig = results.get(code)
            if not sig:
                continue
            row.update(
                judged_date=signal_day.isoformat(),
                reason=sig.get("reason"),
                checks={
                    k: (sig.get("checks") or {}).get(k)
                    for k in ("r_weekly", "r_daily", "w", "m_type", "ma60_ok", "macd_gc")
                },
                gap_pct=sig.get("gap_pct"),
                rsi_w_min=sig.get("rsi_w_min"),
                w_count=sig.get("w_count") or 0,
                tv_ratio_5_60=sig.get("tv_ratio_5_60"),
            )

        # ── 수동 종목 현황 ──
        prev_report = {r.get("code"): r for r in (store.data.get("manual_report") or {}).get("rows", [])}
        manual_rows: List[Dict[str, Any]] = []
        for code, meta in universe.items():
            if meta["source"] not in ("manual", "both"):
                continue
            sig = results.get(code) or {"reason": "INSUFFICIENT_BARS", "bars": 0}
            line = manual_status_line(code, meta["name"], sig, p)
            manual_rows.append({
                "code": code,
                "name": meta["name"],
                "source": meta["source"],
                "ok": bool(sig.get("ok")),
                "reason": sig.get("reason"),
                "reason_ko": REASON_KO.get(sig.get("reason") or "", sig.get("reason") or ""),
                "bars": sig.get("bars"),
                "line": line,
                "checks": sig.get("checks") or {},
                "gap_pct": sig.get("gap_pct"),
                "ma60_slope_pct": sig.get("ma60_slope_pct"),
                "rsi_w_min": sig.get("rsi_w_min"),
                "changed": (prev_report.get(code) or {}).get("reason") != sig.get("reason"),
            })
        store.data["manual_report"] = {"date": signal_day.isoformat(), "rows": manual_rows}

        reason_counts: Dict[str, int] = {}
        for sig in results.values():
            r = str(sig.get("reason") or "")
            reason_counts[r] = reason_counts.get(r, 0) + 1
        stages = {"universe": len(results), "liquidity": 0, "r": 0, "w": 0, "m": 0, "watch": len(watch_added)}
        for sig in results.values():
            ck = sig.get("checks") or {}
            if not ck.get("liquidity"):
                continue
            stages["liquidity"] += 1
            if not (ck.get("r_weekly") and ck.get("r_daily")):
                continue
            stages["r"] += 1
            if not ck.get("w"):
                continue
            stages["w"] += 1
            if ck.get("m_type") and ck.get("ma60_ok") and ck.get("macd_gc"):
                stages["m"] += 1
        store.data["last_batch"] = {
            "signal_date": signal_day.isoformat(),
            "valid_date": valid_date.isoformat(),
            "ran_at": now.isoformat(timespec="seconds"),
            "trigger": trigger,
            "universe": len(universe),
            "condition": sum(1 for m in universe.values() if m["source"] in ("condition", "both")),
            "pool": sum(1 for m in universe.values() if m["source"] == "pool"),
            "manual": sum(1 for m in universe.values() if m["source"] in ("manual", "both")),
            "holding": len(holding),
            "condition_errors": cond_errors,
            "insufficient_bars": insufficient,
            "reasons": reason_counts,
            "stages": stages,
            "watch_added": [c for c, _ in watch_added],
            "exits": len(exit_msgs),
            "rejected": [
                {"code": c, "name": universe[c]["name"], "reason": s.get("reason")}
                for c, s in results.items()
                if s.get("reason") in ("ALREADY_IN_POSITION", "SLOT_FULL", "DAILY_NEW_LIMIT")
            ],
        }
        store.save()

        # ── 텔레그램 ──
        pb_s, pb_e = p["pb_start"], p["pb_end"]
        for code, row in watch_added:
            s = row["sig"]
            m_ko = M_TYPE_KO.get(s.get("m_type") or "", s.get("m_type") or "")
            gap_prev = s.get("gap_prev_pct")
            gap_prev_s = f", 5일 전 {gap_prev:.1f}%" if isinstance(gap_prev, (int, float)) else ""
            slope = s.get("ma60_slope_pct")
            slope_s = f"{slope:+.1f}%" if isinstance(slope, (int, float)) else "-"
            off = s.get("r_pair_offset")
            off_s = f"{off:+d}일" if isinstance(off, int) else "-"
            qty_s = f"예상 수량 {row['qty_est']:,}주" if row["qty_est"] else "수량은 체결 시 산출"
            tv_ratio = s.get("tv_ratio_5_60")
            tv_s = f"×{tv_ratio:.2f}" if isinstance(tv_ratio, (int, float)) else "-"
            msg = (
                f"<b>[RWM 밥그릇 · 내일 눌림 대기]</b> {html.escape(row['name'])} ({code}) — MACD 골든크로스\n"
                f"M MACD: {_md(s.get('macd_gc_date'))} 시그널 상향 돌파 · MACD {s.get('macd')} / 시그널 {s.get('macd_signal')}\n"
                f"이평: {m_ko} · 20선 {_fmt_won(s.get('ma20'))} / 60선 {_fmt_won(s.get('ma60'))} "
                f"(이격 {s.get('gap_pct')}%{gap_prev_s}) · 60선 5일 {slope_s} · 종가 {_fmt_won(s.get('signal_close'))}\n"
                f"R 바닥: 주봉 RSI {s.get('r_week_rsi')} ({_md(s.get('r_week'))} 주) → "
                f"일봉 RSI {s.get('rsi_d_at_r')} ({_md(s.get('r_date'))}, 주봉 기준 {off_s})\n"
                f"W 반전: %R {s.get('w_from')} → {s.get('w_to')} ({_md(s.get('w_date'))}, 바닥 체류 {s.get('w_bottom_bars')}일 · R 이후 신호 {s.get('w_count') or 1}회)\n"
                f"거래대금: 5일 평균 / 60일 평균 {tv_s} · {p['liquidity_days']}일 평균 {_fmt_won(s.get('trading_value_avg'))}원\n"
                f"매수: {_md(row['valid_date'])} {pb_s}~{pb_e} 3분봉 EMA20 눌림 반등 시 · 상한 {_fmt_won(row['entry_cap'])} "
                f"(+{p['max_entry_above_pct']}%) · 시가/장중 {_fmt_won(row['ma20_d'])} 아래면 취소\n"
                f"{qty_s} · 매도 종가 20선 이탈 전량 · 장중 하드 손절 {_fmt_won(row['hard_price'])}"
            )
            await _send_telegram(msg)
            _log(f"RWM 감시 등록 {row['name']}({code}) {m_ko}", "info", stock_code=code)
        if exit_msgs:
            await _send_telegram("<b>[RWM 밥그릇 · 청산 예약]</b>\n" + "\n".join(exit_msgs))
        if manual_rows and p.get("manual_daily_report", True):
            rows = [r for r in manual_rows if r["changed"]] if p.get("manual_report_changes_only") else manual_rows
            if rows:
                body = "\n".join(html.escape(r["line"]) for r in rows)
                await _send_telegram(f"<b>[RWM 수동 종목 현황 · {_md(signal_day.isoformat())}]</b>\n{body}")

        summary = (
            f"RWM 장후 배치 ({trigger}) {signal_day} — 대상 {len(universe)} · 감시 {len(watch_added)} · "
            f"청산 예약 {len(exit_msgs)} · 일봉 부족 {insufficient}"
        )
        logger.info(f"🥣 [RWM] {summary}")
        _log(summary)
        return {"ok": True, "message": summary, **store.data["last_batch"]}

    # ───────────────────────── 장중 3분봉 감시 ─────────────────────────

    async def _watch_pullbacks(self, settings, now: datetime) -> None:
        import time

        from api.kiwoom_api import kiwoom_api
        from utils.rwm_bowl import evaluate_pullback, get_state_store, normalize_minute_bars, params_from_settings

        store = get_state_store()
        today = now.date()
        rows = store.active_watch(today)
        if not rows:
            return
        p = params_from_settings(settings)
        now_naive = now.replace(tzinfo=None)
        bar_start = now_naive.replace(minute=(now_naive.minute // _BAR_MIN) * _BAR_MIN, second=0, microsecond=0)
        expected_last = (bar_start - timedelta(minutes=_BAR_MIN)).strftime("%Y-%m-%d %H:%M:%S")

        for code, row in list(rows.items()):
            if row.get("last_bar") == expected_last:
                continue
            if time.time() < self._minute_next_fetch.get(code, 0.0):
                continue
            self._minute_next_fetch[code] = time.time() + _MINUTE_RETRY_SEC
            raw = await kiwoom_api.get_stock_chart_data(
                code, "3M", max_bars=_MINUTE_MAX_BARS, cache_ttl_sec=_MINUTE_CACHE_TTL,
            )
            bars = normalize_minute_bars(raw or [])
            done = [b for b in bars if b["dt"] + timedelta(minutes=_BAR_MIN) <= now_naive]
            last_bar = done[-1]["timestamp"] if done else ""
            res = evaluate_pullback(
                bars,
                today=today,
                now=now_naive,
                ma20_d=float(row["ma20_d"]),
                signal_close=float(row["signal_close"]),
                cap=float(row["entry_cap"]),
                p=p,
            )
            fields: Dict[str, Any] = {
                "last_bar": last_bar,
                "checked_at": now.isoformat(timespec="seconds"),
                "day_open": res.get("day_open"),
                "day_high": res.get("day_high"),
                "ema20_3m": res.get("ema20_3m"),
                "rise_ok": res.get("rise_ok"),
                "events": (row.get("events") or []) + [
                    e for e in res.get("events") or [] if e not in (row.get("events") or [])
                ],
            }
            if res["action"] == "BUY":
                await self._on_buy(settings, code, row, res, fields)
            elif res["action"] == "EXPIRE":
                await self._on_expire(code, row, res["reason"], fields)
            else:
                store.update_watch(code, **fields)

    async def _on_buy(self, settings, code: str, row: Dict[str, Any], res: Dict[str, Any], fields: Dict[str, Any]) -> None:
        from managers.auto_trade_scanner import AUTO_TRADE_CONDITION_ID
        from managers.signal_manager import SignalType, signal_manager
        from utils.rwm_bowl import get_state_store, params_from_settings

        store = get_state_store()
        p = params_from_settings(settings)
        s = row.get("sig") or {}
        name = row.get("name") or code
        entry = int(res.get("entry_price") or 0)
        pb_fields = {k: res.get(k) for k in (
            "pb_day_high", "pb_low", "pb_depth_pct", "pb_ema20_3m", "pb_confirm_time", "pb_touch_time",
        )}
        fields.update(pb_fields)
        auto_on = bool(getattr(settings, "is_enabled", False)) and bool(getattr(settings, "use_rwm_bowl", False))
        if not auto_on:
            fields.update(status="SIGNAL_OFF", reason="PULLBACK_3M", ended_at=now_kst().isoformat(timespec="seconds"))
            store.update_watch(code, **fields)
            await _send_telegram(
                f"<b>[RWM 밥그릇 · 눌림 반등 확인]</b> {html.escape(name)} ({code}) {_fmt_won(entry)} "
                f"({res.get('pb_confirm_time', '')[11:16]}) — 자동매매 OFF 로 주문 안 함"
            )
            return
        meta = {
            "current_price": entry,
            "source": "rwm_bowl",
            "strategy": "rwm_bowl",
            "universe_source": row.get("universe_source"),
            "reason": "PULLBACK_3M",
            "m_type": s.get("m_type"),
            "gc_date": s.get("gc_date"),
            "signal_date": row.get("signal_date"),
            "signal_close": row.get("signal_close"),
            "ma20": s.get("ma20"),
            "ma60": s.get("ma60"),
            "gap_pct": s.get("gap_pct"),
            "ma60_slope_pct": s.get("ma60_slope_pct"),
            "r_week": s.get("r_week"),
            "rsi_w_min": s.get("rsi_w_min"),
            "r_date": s.get("r_date"),
            "rsi_d_min": s.get("rsi_d_min"),
            "w_date": s.get("w_date"),
            "w_bottom_bars": s.get("w_bottom_bars"),
            "w_count": s.get("w_count"),
            "tv_ratio_5_60": s.get("tv_ratio_5_60"),
            "bowl_date": s.get("bowl_date"),
            "bowl_low": s.get("bowl_low"),
            **pb_fields,
            "entry_cap": row.get("entry_cap"),
            "stop_price": row.get("hard_price"),
            "suggested_stop": row.get("hard_price"),
            "max_hold_days": int(p["max_hold_days"]),
            "order_ready": True,
        }
        ok, msg = await signal_manager.create_signal_detail(
            condition_id=AUTO_TRADE_CONDITION_ID,
            stock_code=code,
            stock_name=name,
            signal_type=SignalType.STRATEGY,
            additional_data=meta,
        )
        fields.update(
            status="SIGNALED" if ok else "FAILED",
            reason="PULLBACK_3M" if ok else f"SIGNAL_FAIL: {msg}",
            ended_at=now_kst().isoformat(timespec="seconds"),
            entry_price=entry,
        )
        store.update_watch(code, **fields)
        info = (
            f"RWM 눌림 반등 매수 신호 {name}({code}) {entry:,}원 · "
            f"EMA20 {res.get('pb_ema20_3m')} · 깊이 {res.get('pb_depth_pct')}%"
        )
        logger.info(f"🥣 [RWM] {info} → {msg}")
        _log(info if ok else f"{info} 실패: {msg}", "info" if ok else "warn", stock_code=code)
        await _send_telegram(
            f"<b>[RWM 밥그릇 · 매수 신호]</b> {html.escape(name)} ({code}) {_fmt_won(entry)} "
            f"({str(res.get('pb_confirm_time') or '')[11:16]} 3분봉 확정)\n"
            f"고점 {_fmt_won(res.get('pb_day_high'))} → 눌림 {_fmt_won(res.get('pb_low'))} "
            f"(−{res.get('pb_depth_pct')}%) · EMA20 {_fmt_won(res.get('pb_ema20_3m'))} · "
            f"하드 손절 {_fmt_won(row.get('hard_price'))}"
            + ("" if ok else f"\n신호 생성 실패: {html.escape(str(msg))}")
        )

    async def _on_expire(self, code: str, row: Dict[str, Any], reason: str, fields: Dict[str, Any]) -> None:
        from utils.rwm_bowl import REASON_KO, get_state_store

        fields.update(status="EXPIRED", reason=reason, ended_at=now_kst().isoformat(timespec="seconds"))
        get_state_store().update_watch(code, **fields)
        name = row.get("name") or code
        ko = REASON_KO.get(reason, reason)
        _log(f"RWM 감시 종료 {name}({code}): {ko}", "info", stock_code=code)
        await _send_telegram(f"[RWM 밥그릇 · 감시 종료] {html.escape(name)} ({code}) — {ko} ({reason}), 매수 안 함")

    # ───────────────────────── 15:10 급등 종목 10일선 이탈 장중 익절 ─────────────────────────

    async def _check_surge_exits(self, settings, today: date) -> None:
        """급등 양봉 뒤 보유 종목: 현재가를 종가로 보고 10일선 아래면 당일 장중 전량 익절 예약.

        여기서 놓치면 15:40 확정 종가 판정에서 EXIT_MA10 → 다음 날 시가 매도.
        """
        from api.kiwoom_api import kiwoom_api
        from utils.rwm_bowl import (
            REASON_KO,
            evaluate_daily_exit,
            get_state_store,
            normalize_daily_bars,
            params_from_settings,
        )

        p = params_from_settings(settings)
        store = get_state_store()
        holding, _, _ = self._rwm_positions()
        iso = today.isoformat()
        msgs: List[str] = []
        for h in holding:
            if self._surge_checked.get(h["id"]) == iso or store.pending_exit(h["id"]):
                continue
            raw = await kiwoom_api.get_stock_chart_data(
                h["code"], "1D", max_bars=_DAILY_MAX_BARS, allow_off_hours=True, cache_ttl_sec=30,
            )
            bars = normalize_daily_bars(raw or [])
            if not bars or bars[-1]["date"] != today:
                continue
            self._surge_checked[h["id"]] = iso
            ex = evaluate_daily_exit(bars, p, entry_date=h["entry_date"])
            if ex.get("reason") != "EXIT_MA10":
                continue
            store.set_exit(h["id"], {
                "reason": "EXIT_MA10",
                "code": h["code"],
                "name": h["name"],
                "judged_date": iso,
                "exec_from": iso,
                "intraday": True,
                "ma20": ex.get("ma20"),
                "ma_n": ex.get("exit_ma_n"),
                "ma": ex.get("exit_ma"),
                "close": ex.get("close"),
                "surge_date": ex.get("surge_date"),
                "hold_days": ex.get("hold_days"),
            })
            ko = REASON_KO["EXIT_MA10"]
            _log(f"RWM {ko} {h['name']}: 현재 {ex.get('close')} < MA{ex.get('exit_ma_n')} {ex.get('exit_ma')}", "warn", stock_code=h["code"])
            msgs.append(
                f"{html.escape(h['name'])} ({h['code']}) 급등 {_md(ex.get('surge_date'))} → "
                f"현재 {_fmt_won(ex.get('close'))} / MA{ex.get('exit_ma_n')} {_fmt_won(ex.get('exit_ma'))}"
            )
        if msgs:
            store.save()
            await _send_telegram(
                f"<b>[RWM 밥그릇 · {p.get('surge_exit_hm')} 10일선 이탈 익절]</b> 장 마감 전 전량 매도\n" + "\n".join(msgs)
            )

    # ───────────────────────── 15:00 청산선(20선 · 급등 시 10선) 접근 경고 ─────────────────────────

    async def _warn_near_ma20(self, settings, today: date) -> None:
        from utils.rwm_bowl import evaluate_daily_exit, get_state_store, normalize_daily_bars, params_from_settings
        from api.kiwoom_api import kiwoom_api

        p = params_from_settings(settings)
        store = get_state_store()
        warned: Dict[str, str] = store.data.setdefault("warned", {})
        holding, _, _ = self._rwm_positions()
        iso = today.isoformat()
        msgs: List[str] = []
        for h in holding:
            key = str(h["id"])
            if warned.get(key) == iso:
                continue
            warned[key] = iso
            raw = await kiwoom_api.get_stock_chart_data(h["code"], "1D", max_bars=_DAILY_MAX_BARS, cache_ttl_sec=60)
            bars = normalize_daily_bars(raw or [])
            if len(bars) < 20 or bars[-1]["date"] != today:
                continue
            ex = evaluate_daily_exit(bars, p, entry_date=h["entry_date"])
            ma, n = ex.get("exit_ma"), ex.get("exit_ma_n")
            px = bars[-1]["close"]
            if ma and px <= ma * (1.0 + _NEAR_WARN_PCT / 100.0):
                when = f"{p.get('surge_exit_hm')} 장중 매도" if ex.get("surge_date") else "종가 이탈 시 내일 시가 매도"
                msgs.append(
                    f"{html.escape(h['name'])} ({h['code']}) 현재 {_fmt_won(px)} / MA{n} {_fmt_won(ma)} "
                    f"({(px / ma - 1) * 100:+.2f}%) · {when}"
                )
        store.save()
        if msgs:
            await _send_telegram("<b>[RWM 밥그릇 · 청산선 접근 경고 15:00]</b>\n" + "\n".join(msgs))


rwm_bowl_scheduler = RwmBowlScheduler()
