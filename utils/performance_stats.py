"""성과 통계 — 앱 DB 청산(포지션) 또는 키움 실현손익(ka10073/74)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from api.kiwoom_api import _parse_kiwoom_int

KST = timezone(timedelta(hours=9))


def _kst_date_of(v: Any) -> Optional[date]:
    """DB naive UTC → KST 날짜."""
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if not isinstance(v, datetime):
        return None
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.astimezone(KST).date()


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    text = str(value).strip().replace(",", "")
    if not text or text == "-":
        return default
    if "." in text:
        try:
            sign = -1 if text.startswith("-") else 1
            return sign * float(text.lstrip("+-"))
        except ValueError:
            return default
    return float(_parse_kiwoom_int(value))


def trades_from_realized_pnl(rows: List[Dict]) -> List[Dict]:
    """ka10073(일자별종목별실현손익) → 매도 1건 = trade 1건."""
    trades: List[Dict] = []
    for r in rows:
        net = _to_float(r.get("tdy_sel_pl"))
        fee = abs(_to_float(r.get("tdy_trde_cmsn")))
        tax = abs(_to_float(r.get("tdy_trde_tax")))
        dt = str(r.get("dt", "")).strip()
        date_str = f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}" if len(dt) >= 8 else (dt or "-")
        trades.append({
            "ts": f"{date_str}T00:00:00" if len(dt) >= 8 else None,
            "date": date_str,
            "reason": "실현매도",
            "stock_code": str(r.get("stk_cd", "")).replace("A", "").strip(),
            "stock_name": r.get("stk_nm", ""),
            "gross": net + fee + tax,
            "cost": fee + tax,
            "net": net,
        })
    trades.sort(key=lambda t: (t.get("date") or "", t.get("stock_code") or ""))
    return trades


def trades_from_daily_realized_pnl(rows: List[Dict]) -> List[Dict]:
    """ka10074(일자별실현손익) dt_rlzt_pl → 일별 trade."""
    trades: List[Dict] = []
    for r in rows:
        net = _parse_kiwoom_int(r.get("tdy_sel_pl"))
        fee = abs(_parse_kiwoom_int(r.get("tdy_trde_cmsn")))
        tax = abs(_parse_kiwoom_int(r.get("tdy_trde_tax")))
        dt = str(r.get("dt", "")).strip()
        if not dt and net == 0:
            continue
        date_str = f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}" if len(dt) >= 8 else (dt or "-")
        trades.append({
            "ts": f"{date_str}T00:00:00" if len(dt) >= 8 else None,
            "date": date_str,
            "reason": "실현(일별)",
            "stock_code": "",
            "stock_name": "",
            "gross": net + fee + tax,
            "cost": fee + tax,
            "net": float(net),
        })
    trades.sort(key=lambda t: t.get("date") or "")
    return trades


def _normalize_sell_reason(reason: Optional[str]) -> str:
    """포지션 status(MANUAL_SELL 등) → sell_reason 코드로 통일."""
    r = str(reason or "").strip() or "매도"
    if r == "MANUAL_SELL":
        return "MANUAL"
    return r


def _outcome_sell_reason(reason: Optional[str], net: float) -> str:
    """트레일 등 메커니즘 + 실현손익 → 익절/손절 결과 코드."""
    from utils.sell_reason_labels import classify_exit_reason

    return classify_exit_reason(_normalize_sell_reason(reason), profit_loss=net)

def trades_from_db_closures(sell_orders, positions) -> List[Dict]:
    """앱 DB 청산 — 포지션 1개 완전 청산 = trade 1건.

    동일 종목을 매도 후 다시 매수·청산하면 position_id가 달라 각각 1건으로 집계한다.
    SellOrder(COMPLETED) 손익 우선, 없으면 종료된 Position의 sell_time·손익 사용.
    """
    sells_by_pos: Dict[int, list] = defaultdict(list)
    for r in sell_orders:
        if r.status != "COMPLETED":
            continue
        if str(r.stock_code or "").startswith("SAMPLE_"):
            continue
        sells_by_pos[r.position_id].append(r)

    pos_by_id = {p.id: p for p in positions}
    closed_ids: set[int] = set(sells_by_pos.keys())
    for p in positions:
        if p.status == "HOLDING":
            continue
        if str(p.stock_code or "").startswith("SAMPLE_"):
            continue
        if p.sell_time or p.id in sells_by_pos:
            closed_ids.add(p.id)

    trades: List[Dict] = []
    for pos_id in closed_ids:
        sells = sells_by_pos.get(pos_id, [])
        pos = pos_by_id.get(pos_id)

        if sells:
            net = sum(int(s.profit_loss or 0) for s in sells)
            last = max(sells, key=lambda s: s.completed_at or s.created_at)
            ts = last.completed_at or last.created_at
            reason = _outcome_sell_reason(last.sell_reason or "매도", net)
            stock_code = last.stock_code
            stock_name = last.stock_name
        elif pos and pos.sell_time:
            net = int(pos.current_profit_loss or 0)
            ts = pos.sell_time
            reason = _outcome_sell_reason(pos.status or "청산", net)
            stock_code = pos.stock_code
            stock_name = pos.stock_name
        else:
            continue

        if not ts:
            continue

        date_kst = _kst_date_of(ts)
        trades.append({
            "position_id": pos_id,
            "ts": ts.isoformat(),
            "date": str(date_kst) if date_kst else "-",
            "reason": reason,
            "stock_code": stock_code,
            "stock_name": stock_name,
            "gross": float(net),
            "cost": 0.0,
            "net": float(net),
        })

    trades.sort(key=lambda t: (t.get("date") or "", t.get("ts") or "", t.get("position_id") or 0))
    return trades


def period_from_trades(trades: List[Dict]) -> Optional[Dict[str, str]]:
    """trade date 범위 → YYYYMMDD period."""
    dates = sorted({t.get("date") for t in trades if t.get("date") and t["date"] != "-"})
    if not dates:
        return None
    def _compact(d: str) -> str:
        return d.replace("-", "")
    return {"start": _compact(dates[0]), "end": _compact(dates[-1])}


def compute_performance(
    trades: List[Dict],
    seed: int,
    pipeline: str,
    data_source: str,
) -> Dict[str, Any]:
    """청산 trade 목록 → KPI/곡선/일별 집계."""
    n = len(trades)
    base = {
        "seed": seed,
        "pipeline": pipeline,
        "data_source": data_source,
        "trade_count": 0,
        "net_pnl": 0,
        "gross_pnl": 0,
        "total_cost": 0,
        "return_rate": 0,
        "win_rate": 0,
        "wins": 0,
        "losses": 0,
        "breakeven": 0,
        "payoff": 0,
        "profit_factor": 0,
        "expected": 0,
        "mdd": 0,
        "mdd_pct": 0,
        "mdd_peak_date": None,
        "mdd_trough_date": None,
        "best": 0,
        "worst": 0,
        "avg_win": 0,
        "avg_loss": 0,
        "daily_avg": 0,
        "trading_days": 0,
        "day_wins": 0,
        "day_losses": 0,
        "curve": [],
        "by_reason": [],
        "daily": [],
    }
    if n == 0:
        return base

    net_pnl = sum(float(t["net"]) for t in trades)
    gross_pnl = sum(float(t["gross"]) for t in trades)
    total_cost = sum(float(t.get("cost") or 0) for t in trades)
    wins = [t for t in trades if float(t["net"]) > 0]
    losses = [t for t in trades if float(t["net"]) < 0]
    closed = len(wins) + len(losses)
    win_sum = sum(float(t["net"]) for t in wins)
    loss_sum = sum(float(t["net"]) for t in losses)
    avg_win = win_sum / len(wins) if wins else 0
    avg_loss = loss_sum / len(losses) if losses else 0

    curve, cum = [], 0.0
    for t in trades:
        cum += float(t["net"])
        curve.append({"ts": t.get("ts"), "date": t.get("date"), "cum": round(cum)})

    reason_map: Dict[str, Dict] = {}
    for t in trades:
        reason = t.get("reason") or "기타"
        d = reason_map.setdefault(reason, {"reason": reason, "count": 0, "realized": 0.0})
        d["count"] += 1
        d["realized"] += float(t["net"])

    daily_map: Dict[str, Dict] = {}
    for t in trades:
        d = daily_map.setdefault(
            t["date"],
            {"date": t["date"], "count": 0, "wins": 0, "losses": 0, "pnl": 0.0},
        )
        d["count"] += 1
        if float(t["net"]) > 0:
            d["wins"] += 1
        elif float(t["net"]) < 0:
            d["losses"] += 1
        d["pnl"] += float(t["net"])

    daily = [
        {
            "date": k,
            "count": v["count"],
            "wins": v["wins"],
            "losses": v["losses"],
            "pnl": round(v["pnl"]),
        }
             for k, v in daily_map.items()]
    daily.sort(key=lambda x: x["date"], reverse=True)
    trading_days = len(daily)

    # 트레이딩 MDD: 일별 실현 자산(시드+누적)의 고점 대비 최대 낙폭.
    # 건별 곡선이 아니라 일자 종가 기준 — 당일 회복한 장중 저점은 낙폭으로 보지 않음.
    mdd, mdd_pct = 0.0, 0.0
    mdd_peak_date, mdd_trough_date = None, None
    chrono = sorted(daily, key=lambda x: x["date"])
    if chrono:
        equity = float(seed or 0)
        peak = equity
        peak_date = chrono[0]["date"]
        for row in chrono:
            equity += float(row["pnl"])
            if equity >= peak:
                peak = equity
                peak_date = row["date"]
            dd = equity - peak
            if dd < mdd:
                mdd = dd
                mdd_pct = (dd / peak * 100) if peak else 0.0
                mdd_peak_date = peak_date
                mdd_trough_date = row["date"]

    return {
        **base,
        "trade_count": n,
        "net_pnl": round(net_pnl),
        "gross_pnl": round(gross_pnl),
        "total_cost": round(total_cost),
        "return_rate": round(net_pnl / seed * 100, 2) if seed else 0,
        "win_rate": round(len(wins) / closed * 100, 1) if closed else 0,
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": n - closed,
        "payoff": round((avg_win / abs(avg_loss)) if avg_loss else 0, 2),
        "profit_factor": round((win_sum / abs(loss_sum)) if loss_sum else 0, 2),
        "expected": round(net_pnl / n),
        "mdd": round(mdd),
        "mdd_pct": round(mdd_pct, 2),
        "mdd_peak_date": mdd_peak_date,
        "mdd_trough_date": mdd_trough_date,
        "best": round(max(float(t["net"]) for t in trades)),
        "worst": round(min(float(t["net"]) for t in trades)),
        "avg_win": round(avg_win),
        "avg_loss": round(avg_loss),
        "daily_avg": round(net_pnl / trading_days) if trading_days else 0,
        "trading_days": trading_days,
        "day_wins": sum(1 for d in daily if d["pnl"] > 0),
        "day_losses": trading_days - sum(1 for d in daily if d["pnl"] > 0),
        "curve": curve,
        "by_reason": sorted(
            [{"reason": k, "count": v["count"], "realized": round(v["realized"])} for k, v in reason_map.items()],
            key=lambda x: x["realized"],
        ),
        "daily": daily,
    }
