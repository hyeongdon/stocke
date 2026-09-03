"""당일 전략 검증 — 검증 전용 조건식 편입 종목에 대해 게이트·15분봉 시뮬 (주문 없음)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from api.kiwoom_api import KiwoomAPI
from utils.auto_trade_engine import get_auto_trade_settings_sync
from utils.datetime_kst import kst_today
from utils.screener_targets import fetch_condition_target_items, parse_condition_names
from utils.stock_exit_replay import STRATEGY_LABELS, _normalize_strategy
from utils.stock_exit_replay_15m import run_stock_exit_replay_15m_async

logger = logging.getLogger(__name__)

VERIFY_FIELD_BY_STRATEGY = {
    "legacy": "screener_verify_condition_names",
    "sangtta": "sangtta_verify_condition_names",
    "breakout": "breakout_verify_condition_names",
    "ymgp": "ymgp_verify_condition_names",
    "fractal": "fractal_verify_condition_names",
}

# 종목 간 기본 텀 — API_MIN_CALL_INTERVAL(기본 3초)보다 여유 있게
DEFAULT_PAUSE_SEC = 3.2


def resolve_verify_condition_names(
    strategy: str,
    *,
    settings: Any = None,
    override_names: Optional[str] = None,
) -> List[str]:
    if override_names is not None and str(override_names).strip():
        return parse_condition_names(override_names)
    settings = settings or get_auto_trade_settings_sync()
    if not settings:
        return []
    key = _normalize_strategy(strategy)
    field = VERIFY_FIELD_BY_STRATEGY.get(key, "sangtta_verify_condition_names")
    return parse_condition_names(getattr(settings, field, None))


def list_verify_conditions(strategy: str) -> Dict[str, Any]:
    """설정에 등록된 검증 전용 조건식 목록 (시뮬 없음)."""
    strategy_key = _normalize_strategy(strategy)
    if strategy_key not in STRATEGY_LABELS:
        return {"success": False, "error": "지원하지 않는 전략"}
    settings = get_auto_trade_settings_sync()
    field = VERIFY_FIELD_BY_STRATEGY[strategy_key]
    names = resolve_verify_condition_names(strategy_key, settings=settings)
    live_field = {
        "legacy": "screener_condition_names",
        "sangtta": "sangtta_condition_names",
        "breakout": "breakout_condition_names",
        "ymgp": "ymgp_condition_names",
        "fractal": "fractal_condition_names",
    }[strategy_key]
    live_names = parse_condition_names(getattr(settings, live_field, None) if settings else None)
    return {
        "success": True,
        "strategy": {
            "key": strategy_key,
            "label": STRATEGY_LABELS[strategy_key],
        },
        "verify_field": field,
        "live_field": live_field,
        "note": "검증 전용 조건식만 표시 — 실매매 조건식과 분리(주문 없음)",
        "conditions": [
            {
                "name": n,
                "also_in_live": n in live_names,
            }
            for n in names
        ],
        "empty_hint": (
            f"대시보드 → 자동매매 → 설정 → {STRATEGY_LABELS[strategy_key]} → "
            f"「검증 전용 조건식」에 등록하세요. 예: 검증({STRATEGY_LABELS[strategy_key]})"
        ),
    }


def estimate_chart_api_calls(n_stocks: int, hold_days: int) -> Dict[str, Any]:
    """종목당 차트 API 호출 대략치 (캐시/마트를 모르므로 구간으로).

    종목당:
      - 일봉(ka10081) 0~1회 (technical_mart 충분하면 0, 아니면 1)
      - 15분봉(ka10080) hold_days회 (보통 1페이지/일)
    조건식 편입 조회 약 1회 별도.
    """
    n = max(0, int(n_stocks))
    d = max(1, int(hold_days))
    per_min = d
    per_max = 1 + d
    return {
        "condition_list": 1,
        "per_stock_min": per_min,
        "per_stock_max": per_max,
        "chart_total_min": n * per_min,
        "chart_total_max": n * per_max,
        "grand_total_min": 1 + n * per_min,
        "grand_total_max": 1 + n * per_max,
        "note": (
            f"종목당 15분봉≈{d}회 + 일봉 0~1회(마트 미비 시). "
            f"{n}종목이면 차트 약 {n * per_min}~{n * per_max}회 + 조건식 1회."
        ),
    }


def _failed_gate_checks(checks: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """진입 미통과 원인만 요약."""
    out: List[Dict[str, Any]] = []
    for c in checks or []:
        if c.get("enabled") is False:
            continue
        if c.get("passed") is not False:
            continue
        out.append({
            "group": c.get("group"),
            "label": c.get("label"),
            "actual": c.get("actual"),
            "required": c.get("required"),
            "note": c.get("note"),
            "key": c.get("key"),
        })
    return out[:8]


def _row_from_sim(
    it: Dict[str, Any],
    focus_name: str,
    sim: Dict[str, Any],
) -> Dict[str, Any]:
    code = KiwoomAPI.normalize_stock_code(str(it.get("stock_code") or ""))
    name = (it.get("stock_name") or code).strip()
    row: Dict[str, Any] = {
        "stock_code": code,
        "stock_name": name,
        "condition_name": it.get("condition_name") or focus_name,
        "quote_change_rate": it.get("change_rate"),
        "quote_price": it.get("current_price"),
    }
    if not sim.get("success"):
        row["success"] = False
        row["error"] = sim.get("error") or "시뮬 실패"
        return row

    entry = sim.get("entry") or {}
    exit_ev = sim.get("exit") or {}
    summary = sim.get("summary") or {}
    buy_checks = sim.get("buy_condition_checks") or []
    failed = _failed_gate_checks(buy_checks)
    row.update({
        "success": True,
        "entry_passed": entry.get("passed"),
        "entry_reason": entry.get("reason"),
        "entry_assumed": entry.get("assumed"),
        "buy_time": entry.get("time"),
        "buy_price": entry.get("price"),
        "sell_time": exit_ev.get("time"),
        "sell_price": exit_ev.get("price"),
        "sell_reason": summary.get("reason_label") or exit_ev.get("reason_label"),
        "profit_loss_rate_pct": summary.get("profit_loss_rate_pct"),
        "intraday_chart": sim.get("intraday_chart"),
        "buy_condition_summary": sim.get("buy_condition_summary"),
        "sell_condition_summary": sim.get("sell_condition_summary"),
        "buy_condition_checks": buy_checks,
        "sell_condition_checks": sim.get("sell_condition_checks"),
        "gate_failed_checks": failed,
        "gate_fail_summary": " · ".join(
            f"{c.get('label')}: {c.get('actual') or c.get('note') or '미충족'}"
            for c in failed[:3]
        ) or (entry.get("reason") if not entry.get("passed") else ""),
    })
    return row


def _build_done_payload(
    *,
    strategy_key: str,
    trade_date: str,
    focus_name: str,
    names: List[str],
    fetch_errors: Any,
    uniq: List[Dict[str, Any]],
    results: List[Dict[str, Any]],
    truncated: bool,
    limit: int,
    hold_days: int,
    pause_sec: float,
    api_estimate: Dict[str, Any],
    skipped: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    passed = sum(1 for r in results if r.get("entry_passed") is True)
    failed_gate = sum(1 for r in results if r.get("entry_passed") is False)
    skipped = skipped or []
    pnl_rows = [
        r for r in results
        if r.get("profit_loss_rate_pct") is not None and r.get("entry_passed")
    ]
    avg_pnl = (
        round(sum(float(r["profit_loss_rate_pct"]) for r in pnl_rows) / len(pnl_rows), 2)
        if pnl_rows else None
    )
    return {
        "success": True,
        "dry_run": True,
        "orders": False,
        "note": "검증 전용 — 실제 주문·실매매 조건식과 분리됨",
        "strategy": {
            "key": strategy_key,
            "label": STRATEGY_LABELS[strategy_key],
        },
        "trade_date": trade_date[:10],
        "condition_name": focus_name,
        "condition_names": names,
        "fetch_errors": fetch_errors,
        "universe_count": len(uniq),
        "simulated_count": len(results),
        "skipped_count": len(skipped),
        "skipped": skipped,
        "truncated": truncated,
        "limit": limit,
        "hold_days": hold_days,
        "pause_sec": pause_sec,
        "api_estimate": api_estimate,
        "summary": {
            "entry_passed": passed,
            "entry_failed": failed_gate,
            "avg_pnl_pct_on_passed": avg_pnl,
            "skipped_by_limit": len(skipped),
        },
        "results": results,
        "today_kst": kst_today().isoformat(),
    }


async def iter_strategy_day_verify(
    strategy: str,
    trade_date: str,
    *,
    condition_names: Optional[str] = None,
    limit: int = 8,
    run_sim: bool = True,
    hold_days: int = 1,
    pause_sec: float = DEFAULT_PAUSE_SEC,
) -> AsyncIterator[Dict[str, Any]]:
    """NDJSON 스트림용 — start / progress / stock / done (또는 error)."""
    strategy_key = _normalize_strategy(strategy)
    if strategy_key not in STRATEGY_LABELS:
        yield {"event": "error", "success": False, "error": "지원하지 않는 전략"}
        return

    try:
        from utils.stock_exit_replay import _parse_date
        _parse_date(trade_date)
    except ValueError:
        yield {"event": "error", "success": False, "error": "trade_date는 YYYY-MM-DD 형식"}
        return

    limit = max(1, min(int(limit or 8), 20))
    hold_days = max(1, min(int(hold_days or 1), 7))
    pause_sec = max(0.0, min(float(pause_sec if pause_sec is not None else DEFAULT_PAUSE_SEC), 5.0))

    settings = get_auto_trade_settings_sync()
    names = resolve_verify_condition_names(
        strategy_key, settings=settings, override_names=condition_names,
    )
    if not names:
        field = VERIFY_FIELD_BY_STRATEGY[strategy_key]
        yield {
            "event": "error",
            "success": False,
            "error": (
                f"검증 전용 조건식이 없습니다. 대시보드 설정에서 "
                f"「검증({STRATEGY_LABELS[strategy_key]})」 조건식을 등록하거나 "
                f"condition_names 파라미터를 주세요. (필드: {field})"
            ),
            "strategy": strategy_key,
            "verify_field": field,
        }
        return

    focus_name = names[0]
    names = [focus_name]

    yield {
        "event": "progress",
        "phase": "condition",
        "message": f"조건식 「{focus_name}」 편입 종목 조회 중…",
        "index": 0,
        "total": 0,
    }

    api = KiwoomAPI()
    if not api.token_manager.get_valid_token():
        api.authenticate()

    items, fetch_errors = await fetch_condition_target_items(api, names, pause_sec=0.35)
    seen = set()
    uniq: List[Dict[str, Any]] = []
    for it in items or []:
        code = KiwoomAPI.normalize_stock_code(str(it.get("stock_code") or ""))
        if not code or code in seen:
            continue
        seen.add(code)
        uniq.append(it)

    truncated = len(uniq) > limit
    selected = uniq[:limit]
    skipped = [
        {
            "stock_code": KiwoomAPI.normalize_stock_code(str(it.get("stock_code") or "")),
            "stock_name": (it.get("stock_name") or "").strip()
            or KiwoomAPI.normalize_stock_code(str(it.get("stock_code") or "")),
            "condition_name": it.get("condition_name") or focus_name,
            "reason": "limit_cap",
            "reason_label": (
                f"시뮬 종목수 한도({limit})로 제외 — 상단 「종목수」를 {len(uniq)} 이상으로 올리면 포함"
            ),
        }
        for it in uniq[limit:]
    ]
    api_estimate = estimate_chart_api_calls(len(selected), hold_days if run_sim else 0)

    yield {
        "event": "start",
        "success": True,
        "strategy": {"key": strategy_key, "label": STRATEGY_LABELS[strategy_key]},
        "trade_date": trade_date[:10],
        "condition_name": focus_name,
        "universe_count": len(uniq),
        "total": len(selected),
        "truncated": truncated,
        "limit": limit,
        "skipped_count": len(skipped),
        "skipped": skipped,
        "hold_days": hold_days,
        "pause_sec": pause_sec,
        "run_sim": run_sim,
        "api_estimate": api_estimate,
        "fetch_errors": fetch_errors,
        "message": (
            f"편입 {len(uniq)}종목 중 {len(selected)}개 시뮬"
            + (f" · {len(skipped)}개는 종목수 한도로 제외" if skipped else "")
            + f" (예상 API ≈{api_estimate['grand_total_min']}~{api_estimate['grand_total_max']}회, "
            f"종목 간 {pause_sec:.2f}s)"
        ),
    }

    results: List[Dict[str, Any]] = []
    for i, it in enumerate(selected):
        code = KiwoomAPI.normalize_stock_code(str(it.get("stock_code") or ""))
        name = (it.get("stock_name") or code).strip()
        idx = i + 1

        if not run_sim:
            row = {
                "stock_code": code,
                "stock_name": name,
                "condition_name": it.get("condition_name") or focus_name,
                "quote_change_rate": it.get("change_rate"),
                "quote_price": it.get("current_price"),
                "sim": None,
                "note": "편입만 조회 (시뮬 생략)",
            }
            results.append(row)
            yield {"event": "stock", "index": idx, "total": len(selected), "result": row}
            continue

        yield {
            "event": "progress",
            "phase": "sim",
            "index": idx,
            "total": len(selected),
            "stock_code": code,
            "stock_name": name,
            "message": f"{idx}/{len(selected)} {name} ({code}) 시뮬 중…",
        }

        try:
            sim = await run_stock_exit_replay_15m_async(
                code,
                trade_date,
                strategy=strategy_key,
                days=hold_days,
                force_exit=True,
            )
            row = _row_from_sim(it, focus_name, sim)
        except Exception as e:
            logger.warning(f"당일 검증 시뮬 실패 {code}: {e}")
            row = {
                "stock_code": code,
                "stock_name": name,
                "condition_name": it.get("condition_name") or focus_name,
                "quote_change_rate": it.get("change_rate"),
                "quote_price": it.get("current_price"),
                "success": False,
                "error": str(e),
            }

        results.append(row)
        yield {"event": "stock", "index": idx, "total": len(selected), "result": row}

        if pause_sec > 0 and i < len(selected) - 1:
            yield {
                "event": "progress",
                "phase": "pause",
                "index": idx,
                "total": len(selected),
                "stock_code": code,
                "stock_name": name,
                "message": f"{idx}/{len(selected)} 완료 — {pause_sec:.1f}s 대기 (API 부하 완화)",
            }
            await asyncio.sleep(pause_sec)

    done = _build_done_payload(
        strategy_key=strategy_key,
        trade_date=trade_date,
        focus_name=focus_name,
        names=names,
        fetch_errors=fetch_errors,
        uniq=uniq,
        results=results,
        truncated=truncated,
        limit=limit,
        hold_days=hold_days,
        pause_sec=pause_sec,
        api_estimate=api_estimate,
        skipped=skipped,
    )
    yield {"event": "done", **done}


async def run_strategy_day_verify(
    strategy: str,
    trade_date: str,
    *,
    condition_names: Optional[str] = None,
    limit: int = 8,
    run_sim: bool = True,
    hold_days: int = 1,
    pause_sec: float = DEFAULT_PAUSE_SEC,
) -> Dict[str, Any]:
    """검증 조건식 편입 종목 → (선택) 15분봉 진입·청산 시뮬. 주문하지 않음."""
    final: Dict[str, Any] = {"success": False, "error": "결과 없음"}
    async for ev in iter_strategy_day_verify(
        strategy,
        trade_date,
        condition_names=condition_names,
        limit=limit,
        run_sim=run_sim,
        hold_days=hold_days,
        pause_sec=pause_sec,
    ):
        if ev.get("event") == "error":
            return {k: v for k, v in ev.items() if k != "event"}
        if ev.get("event") == "done":
            final = {k: v for k, v in ev.items() if k != "event"}
    return final
