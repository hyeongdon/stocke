"""키움 실현손익(ka10073/74) · 잔고(kt00004)와 앱 DB 손익 비교·동기화."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from api.kiwoom_api import KiwoomAPI, _parse_kiwoom_int
from core.config import Config
from core.models import AccountBalanceSnapshot, Position, SellOrder
from utils.datetime_kst import (
    kst_day_end_utc_naive_exclusive,
    kst_day_start_utc_naive,
    kst_today,
    utc_now_naive,
)
from utils.eval_pnl import apply_holding_to_position, holdings_by_code
from utils.performance_stats import _kst_date_of, _to_float
from utils.position_sell_backfill import ensure_completed_sell_order

logger = logging.getLogger(__name__)

DiffKey = Tuple[str, str]  # (YYYY-MM-DD, stock_code)


def _norm_code(code: Any) -> str:
    return KiwoomAPI.normalize_stock_code(str(code or ""))


def _is_sample(code: Any) -> bool:
    return str(code or "").startswith("SAMPLE_")


def allocate_target_net(current: Sequence[int], target_net: int) -> List[int]:
    """여러 매도 건에 당일 순손익을 배분. 합계는 target_net과 일치."""
    n = len(current)
    if n == 0:
        return []
    if n == 1:
        return [int(target_net)]
    weights = [abs(int(v)) for v in current]
    if sum(weights) == 0:
        weights = [1] * n
    total_w = sum(weights)
    out = [int(target_net * w / total_w) for w in weights]
    out[-1] += int(target_net) - sum(out)
    return out


def allocate_by_sell_amount(sells: Sequence[SellOrder], target: int) -> List[int]:
    """키움 일/종목 합계 비용을 매도금액 비중으로 배분."""
    if not sells:
        return []
    weights = [
        max(
            int(getattr(s, "sell_amount", None) or 0),
            int(getattr(s, "sell_price", None) or 0)
            * int(getattr(s, "sell_quantity", None) or 0),
            0,
        )
        for s in sells
    ]
    if sum(weights) <= 0:
        weights = [1] * len(sells)
    total_weight = sum(weights)
    out = [int(int(target) * weight / total_weight) for weight in weights]
    out[-1] += int(target) - sum(out)
    return out


def aggregate_kiwoom_realized(rows: Iterable[Dict[str, Any]]) -> Dict[DiffKey, Dict[str, Any]]:
    """ka10073 행 → (일자, 종목) 순실현손익 합계."""
    out: Dict[DiffKey, Dict[str, Any]] = {}
    for r in rows:
        dt = str(r.get("dt") or "").strip()
        if len(dt) < 8:
            continue
        date_str = f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}"
        code = _norm_code(r.get("stk_cd"))
        if not code or _is_sample(code):
            continue
        net = int(round(_to_float(r.get("tdy_sel_pl"))))
        fee = abs(int(round(_to_float(r.get("tdy_trde_cmsn")))))
        tax = abs(int(round(_to_float(r.get("tdy_trde_tax")))))
        key = (date_str, code)
        cur = out.get(key)
        if cur is None:
            out[key] = {
                "date": date_str,
                "stock_code": code,
                "stock_name": str(r.get("stk_nm") or ""),
                "kiwoom_net": net,
                "fee": fee,
                "tax": tax,
                "rows": 1,
            }
        else:
            cur["kiwoom_net"] += net
            cur["fee"] += fee
            cur["tax"] += tax
            cur["rows"] += 1
            if not cur.get("stock_name"):
                cur["stock_name"] = str(r.get("stk_nm") or "")
    return out


def reconcile_stock_net_to_account_total(
    kiwoom_map: Dict[DiffKey, Dict[str, Any]],
    account_total: Optional[int],
) -> int:
    """ka10073 종목합의 원단위 오차를 ka10074 계좌합에 맞춤."""
    if not kiwoom_map or account_total is None:
        return 0
    stock_total = sum(int(row.get("kiwoom_net") or 0) for row in kiwoom_map.values())
    adjustment = int(account_total) - stock_total
    if adjustment == 0:
        return 0
    target = max(
        kiwoom_map.values(),
        key=lambda row: abs(int(row.get("kiwoom_net") or 0)),
    )
    target["kiwoom_net"] = int(target.get("kiwoom_net") or 0) + adjustment
    target["account_total_adjustment"] = adjustment
    return adjustment


def aggregate_db_sells(
    sell_orders: Iterable[SellOrder],
    *,
    start_day: date,
    end_day: date,
) -> Dict[DiffKey, Dict[str, Any]]:
    """COMPLETED 매도 → (KST 일자, 종목) DB 손익 합계."""
    out: Dict[DiffKey, Dict[str, Any]] = {}
    for sell in sell_orders:
        if getattr(sell, "status", None) != "COMPLETED":
            continue
        code = _norm_code(getattr(sell, "stock_code", None))
        if not code or _is_sample(code):
            continue
        ts = getattr(sell, "completed_at", None) or getattr(sell, "created_at", None)
        day = _kst_date_of(ts)
        if day is None or day < start_day or day > end_day:
            continue
        date_str = day.isoformat()
        key = (date_str, code)
        cur = out.get(key)
        if cur is None:
            out[key] = {
                "date": date_str,
                "stock_code": code,
                "stock_name": str(getattr(sell, "stock_name", None) or ""),
                "db_net": int(getattr(sell, "profit_loss", None) or 0),
                "db_fee": int(getattr(sell, "trading_commission", None) or 0),
                "db_tax": int(getattr(sell, "transaction_tax", None) or 0),
                "sells": [sell],
            }
        else:
            cur["db_net"] += int(getattr(sell, "profit_loss", None) or 0)
            cur["db_fee"] += int(getattr(sell, "trading_commission", None) or 0)
            cur["db_tax"] += int(getattr(sell, "transaction_tax", None) or 0)
            cur["sells"].append(sell)
            if not cur.get("stock_name"):
                cur["stock_name"] = str(getattr(sell, "stock_name", None) or "")
    return out


def compare_realized(
    kiwoom_map: Dict[DiffKey, Dict[str, Any]],
    db_map: Dict[DiffKey, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """키움 vs DB 차이 행. 값이 같으면 제외."""
    keys = sorted(set(kiwoom_map) | set(db_map))
    diffs: List[Dict[str, Any]] = []
    for key in keys:
        krow = kiwoom_map.get(key) or {}
        drow = db_map.get(key) or {}
        kiwoom_net = int(krow.get("kiwoom_net") or 0)
        db_net = int(drow.get("db_net") or 0)
        fee = int(krow.get("fee") or 0)
        tax = int(krow.get("tax") or 0)
        db_fee = int(drow.get("db_fee") or 0)
        db_tax = int(drow.get("db_tax") or 0)
        delta = kiwoom_net - db_net
        if delta == 0 and fee == db_fee and tax == db_tax and krow and drow:
            continue
        kind = "mismatch"
        if krow and not drow:
            kind = "db_missing"
        elif drow and not krow:
            kind = "kiwoom_missing"
        diffs.append({
            "date": key[0],
            "stock_code": key[1],
            "stock_name": krow.get("stock_name") or drow.get("stock_name") or "",
            "kiwoom_net": kiwoom_net if krow else None,
            "db_net": db_net if drow else None,
            "delta": delta if (krow and drow) else (kiwoom_net if krow else -db_net),
            "kind": kind,
            "fee": fee,
            "tax": tax,
            "db_fee": db_fee,
            "db_tax": db_tax,
            "cost_delta": (fee + tax) - (db_fee + db_tax),
            "sells": list(drow.get("sells") or []),
            "sell_count": len(drow.get("sells") or []),
        })
    diffs.sort(key=lambda r: (r["date"], abs(int(r.get("delta") or 0))), reverse=False)
    diffs.sort(key=lambda r: abs(int(r.get("delta") or 0)), reverse=True)
    return diffs


def _sell_rate(sell: SellOrder, pl: int) -> Optional[float]:
    qty = int(getattr(sell, "sell_quantity", None) or 0)
    price = int(getattr(sell, "sell_price", None) or 0)
    proceeds = int(getattr(sell, "sell_amount", None) or 0) or (price * qty)
    if proceeds > 0:
        cost = proceeds - pl
        if cost > 0:
            return (pl / cost) * 100
    return None


def _apply_sell_financials(
    sells: Sequence[SellOrder],
    target_net: int,
    target_fee: int,
    target_tax: int,
) -> List[Dict[str, Any]]:
    """순손익·수수료·거래세 합계를 개별 매도 건에 배분해 저장."""
    current = [int(getattr(s, "profit_loss", None) or 0) for s in sells]
    allocated_net = allocate_target_net(current, int(target_net))
    allocated_fee = allocate_by_sell_amount(sells, int(target_fee))
    allocated_tax = allocate_by_sell_amount(sells, int(target_tax))
    updates: List[Dict[str, Any]] = []
    for sell, new_pl, new_fee, new_tax in zip(
        sells, allocated_net, allocated_fee, allocated_tax,
    ):
        old_pl = int(getattr(sell, "profit_loss", None) or 0)
        old_fee = int(getattr(sell, "trading_commission", None) or 0)
        old_tax = int(getattr(sell, "transaction_tax", None) or 0)
        if old_pl == new_pl and old_fee == new_fee and old_tax == new_tax:
            continue
        sell.profit_loss = new_pl
        sell.trading_commission = new_fee
        sell.transaction_tax = new_tax
        rate = _sell_rate(sell, new_pl)
        if rate is not None:
            sell.profit_loss_rate = rate
        updates.append({
            "sell_id": getattr(sell, "id", None),
            "position_id": getattr(sell, "position_id", None),
            "old": old_pl,
            "new": new_pl,
            "old_fee": old_fee,
            "new_fee": new_fee,
            "old_tax": old_tax,
            "new_tax": new_tax,
        })
    return updates


def _closed_position_for_day(session: Session, code: str, day: date) -> Optional[Position]:
    start = kst_day_start_utc_naive(day)
    end = kst_day_end_utc_naive_exclusive(day)
    rows = (
        session.query(Position)
        .filter(
            Position.stock_code == code,
            Position.status != "HOLDING",
            Position.sell_time.isnot(None),
            Position.sell_time >= start,
            Position.sell_time < end,
        )
        .order_by(Position.sell_time.desc())
        .all()
    )
    return rows[0] if len(rows) == 1 else None


def _refresh_closed_position_pnl(session: Session, position_ids: Iterable[int]) -> None:
    ids = {int(i) for i in position_ids if i}
    if not ids:
        return
    positions = session.query(Position).filter(Position.id.in_(ids)).all()
    for pos in positions:
        if pos.status == "HOLDING":
            continue
        sells = (
            session.query(SellOrder)
            .filter(SellOrder.position_id == pos.id, SellOrder.status == "COMPLETED")
            .all()
        )
        if not sells:
            continue
        total = sum(int(s.profit_loss or 0) for s in sells)
        pos.current_profit_loss = total
        buy_amt = int(pos.actual_buy_amount or pos.buy_amount or 0)
        if buy_amt > 0:
            pos.current_profit_loss_rate = (total / buy_amt) * 100


def aggregate_ka10074_daily(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """ka10074 dt_rlzt_pl → 일자별 순실현손익."""
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        dt = str(r.get("dt") or "").strip()
        net = _parse_kiwoom_int(r.get("tdy_sel_pl"))
        if len(dt) < 8 and net == 0:
            continue
        if len(dt) < 8:
            continue
        date_str = f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}"
        fee = abs(_parse_kiwoom_int(r.get("tdy_trde_cmsn")))
        tax = abs(_parse_kiwoom_int(r.get("tdy_trde_tax")))
        cur = out.get(date_str)
        if cur is None:
            out[date_str] = {"date": date_str, "kiwoom_net": net, "fee": fee, "tax": tax}
        else:
            cur["kiwoom_net"] += net
            cur["fee"] += fee
            cur["tax"] += tax
    return out


def db_sells_by_day(db_map: Dict[DiffKey, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for (_date, _code), row in db_map.items():
        day = row["date"]
        cur = out.get(day)
        if cur is None:
            out[day] = {
                "date": day,
                "db_net": int(row.get("db_net") or 0),
                "db_fee": int(row.get("db_fee") or 0),
                "db_tax": int(row.get("db_tax") or 0),
                "sells": list(row.get("sells") or []),
            }
        else:
            cur["db_net"] += int(row.get("db_net") or 0)
            cur["db_fee"] += int(row.get("db_fee") or 0)
            cur["db_tax"] += int(row.get("db_tax") or 0)
            cur["sells"].extend(row.get("sells") or [])
    return out


def compare_daily_totals(
    kiwoom_daily: Dict[str, Dict[str, Any]],
    db_daily: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """일자 합계만 비교 (ka10073 불가 시)."""
    diffs: List[Dict[str, Any]] = []
    for day in sorted(set(kiwoom_daily) | set(db_daily)):
        krow = kiwoom_daily.get(day) or {}
        drow = db_daily.get(day) or {}
        kiwoom_net = int(krow.get("kiwoom_net") or 0)
        db_net = int(drow.get("db_net") or 0)
        fee = int(krow.get("fee") or 0)
        tax = int(krow.get("tax") or 0)
        db_fee = int(drow.get("db_fee") or 0)
        db_tax = int(drow.get("db_tax") or 0)
        if (
            krow
            and drow
            and kiwoom_net == db_net
            and fee == db_fee
            and tax == db_tax
        ):
            continue
        kind = "day_total"
        if krow and not drow:
            kind = "db_missing"
        elif drow and not krow:
            kind = "kiwoom_missing"
        diffs.append({
            "date": day,
            "stock_code": "*",
            "stock_name": "(당일합)",
            "kiwoom_net": kiwoom_net if krow else None,
            "db_net": db_net if drow else None,
            "delta": (kiwoom_net - db_net) if (krow and drow) else (kiwoom_net if krow else -db_net),
            "kind": kind,
            "fee": fee,
            "tax": tax,
            "db_fee": db_fee,
            "db_tax": db_tax,
            "cost_delta": (fee + tax) - (db_fee + db_tax),
            "sells": list(drow.get("sells") or []),
            "sell_count": len(drow.get("sells") or []),
        })
    diffs.sort(key=lambda r: abs(int(r.get("delta") or 0)), reverse=True)
    return diffs


def apply_realized_diffs(session: Session, diffs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """차이 행을 DB에 반영. 키움 순손익을 기준으로 맞춤."""
    updated_sells = 0
    backfilled = 0
    skipped: List[Dict[str, Any]] = []
    pos_ids: List[int] = []

    for row in diffs:
        kind = row.get("kind")
        sells: List[SellOrder] = list(row.get("sells") or [])
        kiwoom_net = row.get("kiwoom_net")
        fee = int(row.get("fee") or 0)
        tax = int(row.get("tax") or 0)
        code = row["stock_code"]
        day = datetime.strptime(row["date"], "%Y-%m-%d").date()

        if kind == "kiwoom_missing":
            skipped.append({**{k: v for k, v in row.items() if k != "sells"}, "reason": "키움 실현내역 없음"})
            continue

        if kiwoom_net is None:
            skipped.append({**{k: v for k, v in row.items() if k != "sells"}, "reason": "키움 손익 없음"})
            continue

        if sells:
            changes = _apply_sell_financials(sells, int(kiwoom_net), fee, tax)
            updated_sells += len(changes)
            pos_ids.extend(int(c["position_id"]) for c in changes if c.get("position_id"))
            row["applied"] = changes
            continue

        if code in ("", "*"):
            skipped.append({
                **{k: v for k, v in row.items() if k != "sells"},
                "reason": "당일 매도 이력 없음",
            })
            continue

        pos = _closed_position_for_day(session, code, day)
        if pos is None:
            skipped.append({
                **{k: v for k, v in row.items() if k != "sells"},
                "reason": "매칭 청산 포지션 없음",
            })
            continue
        sell = ensure_completed_sell_order(
            session,
            pos,
            sell_reason="MANUAL" if pos.status == "MANUAL_SELL" else (pos.status or "MANUAL"),
            sell_reason_detail="키움 실현손익 동기화 — 매도 이력 보정",
            completed_at=pos.sell_time,
        )
        if sell is None:
            skipped.append({
                **{k: v for k, v in row.items() if k != "sells"},
                "reason": "매도 이력 생성 실패",
            })
            continue
        _apply_sell_financials([sell], int(kiwoom_net), fee, tax)
        backfilled += 1
        updated_sells += 1
        pos_ids.append(int(pos.id))
        row["applied"] = [{
            "sell_id": sell.id,
            "position_id": pos.id,
            "old": None,
            "new": int(kiwoom_net),
            "old_fee": None,
            "new_fee": fee,
            "old_tax": None,
            "new_tax": tax,
        }]

    _refresh_closed_position_pnl(session, pos_ids)
    return {
        "updated_sells": updated_sells,
        "backfilled": backfilled,
        "skipped": skipped,
    }


def compare_holdings(positions: Iterable[Position], holding_map: Dict[str, dict]) -> List[Dict[str, Any]]:
    diffs: List[Dict[str, Any]] = []
    for pos in positions:
        if getattr(pos, "status", None) != "HOLDING":
            continue
        code = _norm_code(getattr(pos, "stock_code", None))
        if not code or _is_sample(code):
            continue
        holding = holding_map.get(code)
        db_pl = int(getattr(pos, "current_profit_loss", None) or 0)
        db_qty = int(getattr(pos, "buy_quantity", None) or 0)
        if not holding:
            diffs.append({
                "stock_code": code,
                "stock_name": getattr(pos, "stock_name", "") or "",
                "position_id": getattr(pos, "id", None),
                "kind": "not_in_account",
                "db_pl": db_pl,
                "db_qty": db_qty,
                "kiwoom_pl": None,
                "kiwoom_qty": 0,
                "delta": -db_pl,
            })
            continue
        from utils.eval_pnl import pl_from_holding

        kiwoom_pl, _rate = pl_from_holding(holding)
        kiwoom_qty = _parse_kiwoom_int(holding.get("qty"))
        if int(kiwoom_pl) == db_pl and kiwoom_qty == db_qty:
            continue
        diffs.append({
            "stock_code": code,
            "stock_name": getattr(pos, "stock_name", "") or "",
            "position_id": getattr(pos, "id", None),
            "kind": "holding_mismatch",
            "db_pl": db_pl,
            "db_qty": db_qty,
            "kiwoom_pl": int(kiwoom_pl),
            "kiwoom_qty": kiwoom_qty,
            "delta": int(kiwoom_pl) - db_pl,
        })
    return diffs


def apply_holding_diffs(session: Session, positions: Iterable[Position], holding_map: Dict[str, dict]) -> int:
    n = 0
    for pos in positions:
        if pos.status != "HOLDING":
            continue
        code = _norm_code(pos.stock_code)
        holding = holding_map.get(code)
        if not holding:
            continue
        before_pl = int(pos.current_profit_loss or 0)
        before_qty = int(pos.buy_quantity or 0)
        apply_holding_to_position(pos, holding)
        if int(pos.current_profit_loss or 0) != before_pl or int(pos.buy_quantity or 0) != before_qty:
            n += 1
    session.flush()
    return n


def sync_account_balance_snapshot(
    session: Session,
    balance: Dict[str, Any],
    *,
    day: date,
) -> Dict[str, Any]:
    """kt00004의 D+0/D+2 예수금과 자산 값을 일별 upsert."""
    if not balance or balance.get("_error") or balance.get("_stale"):
        raise ValueError("신선한 키움 계좌 잔고가 아닙니다.")

    deposit_d0 = _parse_kiwoom_int(balance.get("entr"))
    deposit_d2 = _parse_kiwoom_int(balance.get("d2_entra"))
    values = {
        "deposit_d0": deposit_d0,
        "deposit_d2": deposit_d2,
        "settlement_gap": deposit_d2 - deposit_d0,
        "stock_evaluation": _parse_kiwoom_int(balance.get("tot_est_amt")),
        "total_purchase": _parse_kiwoom_int(balance.get("tot_pur_amt")),
        "asset_evaluation": _parse_kiwoom_int(balance.get("aset_evlt_amt")),
        "estimated_deposit_asset": _parse_kiwoom_int(balance.get("prsm_dpst_aset_amt")),
        "holding_count": len(balance.get("stk_acnt_evlt_prst") or []),
        "account_type": "모의투자" if Config.KIWOOM_USE_MOCK_ACCOUNT else "실계좌",
        "data_source": "kt00004",
        "synced_at": utc_now_naive(),
    }
    snapshot = (
        session.query(AccountBalanceSnapshot)
        .filter(AccountBalanceSnapshot.as_of_date == day)
        .first()
    )
    created = snapshot is None
    if snapshot is None:
        snapshot = AccountBalanceSnapshot(as_of_date=day, **values)
        session.add(snapshot)
    else:
        for key, value in values.items():
            setattr(snapshot, key, value)
    session.flush()
    return {
        "created": created,
        "date": day.isoformat(),
        **{k: v for k, v in values.items() if k != "synced_at"},
    }


def summarize_report(report: Dict[str, Any]) -> str:
    realized = report.get("realized_diffs") or []
    holdings = report.get("holding_diffs") or []
    k_sum = int(report.get("kiwoom_net_sum") or 0)
    d_sum = int(report.get("db_net_sum") or 0)
    lines = [
        f"기간 {report.get('start')}~{report.get('end')} ({report.get('source') or '-'})",
        f"키움 실현합 {k_sum:+,} / DB 실현합 {d_sum:+,} / 차이 {k_sum - d_sum:+,}",
        f"수수료 {int(report.get('kiwoom_fee_sum') or 0):,}"
        f" / 거래세 {int(report.get('kiwoom_tax_sum') or 0):,}",
        f"실현 차이 {len(realized)}건 · 보유 차이 {len(holdings)}건",
    ]
    applied = report.get("apply_result")
    if applied:
        lines.append(
            f"반영 매도 {applied.get('updated_sells') or 0}건"
            f" · 이력보정 {applied.get('backfilled') or 0}건"
            f" · 보유동기화 {report.get('holdings_updated') or 0}건"
            f" · 건너뜀 {len(applied.get('skipped') or [])}건"
        )
    cash = report.get("account_balance_snapshot")
    if cash:
        lines.append(
            f"예수금 D+0 {int(cash.get('deposit_d0') or 0):,}"
            f" / D+2 {int(cash.get('deposit_d2') or 0):,}"
            f" / 정산차 {int(cash.get('settlement_gap') or 0):+,}"
        )
    return "\n".join(lines)


def format_diff_table(diffs: List[Dict[str, Any]], *, limit: int = 40) -> str:
    if not diffs:
        return "차이 없음"
    lines = ["일자 | 종목 | 키움 | DB | 손익差 | 비용(K/DB) | 구분"]
    for row in diffs[:limit]:
        name = f"{row.get('stock_name') or ''}({row.get('stock_code')})"
        k = row.get("kiwoom_net")
        d = row.get("db_net")
        k_s = f"{int(k):+,}" if k is not None else "-"
        d_s = f"{int(d):+,}" if d is not None else "-"
        lines.append(
            f"{row.get('date')} | {name} | {k_s} | {d_s} | "
            f"{int(row.get('delta') or 0):+,} | "
            f"{int(row.get('fee') or 0) + int(row.get('tax') or 0):,}/"
            f"{int(row.get('db_fee') or 0) + int(row.get('db_tax') or 0):,} | "
            f"{row.get('kind')}"
        )
    extra = len(diffs) - limit
    if extra > 0:
        lines.append(f"… 외 {extra}건")
    return "\n".join(lines)


async def collect_and_sync(
    session: Session,
    api: KiwoomAPI,
    *,
    day: Optional[date] = None,
    days: int = 1,
    apply: bool = False,
    reconcile: bool = False,
    sync_holdings: bool = True,
) -> Dict[str, Any]:
    """키움 조회 후 DB와 비교. apply=True면 키움 순손익으로 DB를 맞춤."""
    if day is not None:
        start_day = end_day = day
    else:
        end_day = kst_today()
        start_day = end_day - timedelta(days=max(int(days) - 1, 0))

    strt_dt = start_day.strftime("%Y%m%d")
    end_dt = end_day.strftime("%Y%m%d")

    reconcile_note = None
    if reconcile:
        try:
            from managers.stop_loss_manager import stop_loss_manager

            await stop_loss_manager._reconcile_sell_orders_and_holdings()
            reconcile_note = "ok"
        except Exception as e:
            logger.exception("잔고/체결 reconcile 실패: %s", e)
            reconcile_note = f"fail:{e}"

    async def _retry(label: str, factory, attempts: int = 6, base_wait: float = 8.0):
        last = {"success": False, "error": "미호출"}
        for i in range(attempts):
            last = await factory()
            if last.get("success"):
                return last
            err = str(last.get("error") or "")
            if "429" not in err and "초과" not in err:
                return last
            wait = base_wait * (i + 1)
            logger.warning("%s 제한(%s) — %.0fs 후 재시도 %s/%s", label, err, wait, i + 1, attempts)
            await asyncio.sleep(wait)
        return last

    await asyncio.sleep(1.0)
    res74 = await _retry(
        "ka10074",
        lambda: api.get_daily_realized_pnl(strt_dt, end_dt),
        attempts=4,
        base_wait=5.0,
    )
    ka10074_total = _parse_kiwoom_int(res74.get("rlzt_pl")) if res74.get("success") else None
    kiwoom_daily = aggregate_ka10074_daily(res74.get("items") or []) if res74.get("success") else {}

    await asyncio.sleep(1.5)
    res73 = await _retry(
        "ka10073",
        lambda: api.get_daily_stock_realized_pnl(strt_dt, end_dt, stk_cd=""),
        attempts=2 if (kiwoom_daily or ka10074_total is not None) else 4,
        base_wait=8.0,
    )
    kiwoom_map = aggregate_kiwoom_realized(res73.get("items") or []) if res73.get("success") else {}
    stock_account_adjustment = reconcile_stock_net_to_account_total(
        kiwoom_map, ka10074_total,
    )
    source = "ka10073" if kiwoom_map else ("ka10074_daily" if kiwoom_daily or ka10074_total is not None else "none")
    if source == "none":
        raise RuntimeError(
            (res73.get("error") if res73 else None)
            or (res74.get("error") if res74 else None)
            or "키움 실현손익 조회 실패"
        )

    start_ts = kst_day_start_utc_naive(start_day)
    end_ts = kst_day_end_utc_naive_exclusive(end_day)
    sells = (
        session.query(SellOrder)
        .filter(
            SellOrder.status == "COMPLETED",
            SellOrder.completed_at >= start_ts,
            SellOrder.completed_at < end_ts,
        )
        .all()
    )
    db_map = aggregate_db_sells(sells, start_day=start_day, end_day=end_day)
    if source == "ka10073":
        realized_diffs = compare_realized(kiwoom_map, db_map)
        kiwoom_net_sum = sum(int(v["kiwoom_net"]) for v in kiwoom_map.values())
    else:
        db_daily = db_sells_by_day(db_map)
        realized_diffs = compare_daily_totals(kiwoom_daily, db_daily)
        kiwoom_net_sum = (
            int(ka10074_total)
            if ka10074_total is not None
            else sum(int(v["kiwoom_net"]) for v in kiwoom_daily.values())
        )
    db_net_sum = sum(int(v["db_net"]) for v in db_map.values())

    holding_diffs: List[Dict[str, Any]] = []
    holdings_updated = 0
    holding_map: Dict[str, dict] = {}
    account_balance_snapshot: Optional[Dict[str, Any]] = None
    balance: Dict[str, Any] = {}
    try:
        balance = await api.get_account_balance(force_refresh=True, max_wait=25.0)
        if balance.get("_error"):
            raise RuntimeError(balance.get("_error_msg") or balance.get("_error"))
        if balance.get("_stale"):
            raise RuntimeError("키움 잔고가 캐시된 이전 값입니다.")
        account_balance_snapshot = {
            "created": False,
            "date": end_day.isoformat(),
            "deposit_d0": _parse_kiwoom_int(balance.get("entr")),
            "deposit_d2": _parse_kiwoom_int(balance.get("d2_entra")),
            "settlement_gap": (
                _parse_kiwoom_int(balance.get("d2_entra"))
                - _parse_kiwoom_int(balance.get("entr"))
            ),
            "stock_evaluation": _parse_kiwoom_int(balance.get("tot_est_amt")),
            "total_purchase": _parse_kiwoom_int(balance.get("tot_pur_amt")),
            "asset_evaluation": _parse_kiwoom_int(balance.get("aset_evlt_amt")),
            "estimated_deposit_asset": _parse_kiwoom_int(balance.get("prsm_dpst_aset_amt")),
            "holding_count": len(balance.get("stk_acnt_evlt_prst") or []),
            "account_type": "모의투자" if Config.KIWOOM_USE_MOCK_ACCOUNT else "실계좌",
            "data_source": "kt00004",
        }
        if sync_holdings:
            holding_map = holdings_by_code(balance)
    except Exception as e:
        logger.exception("잔고·예수금 조회 실패: %s", e)
        balance = {}

    if sync_holdings:
        positions = session.query(Position).filter(Position.status == "HOLDING").all()
        holding_diffs = compare_holdings(positions, holding_map)
        if apply and holding_map:
            holdings_updated = apply_holding_diffs(session, positions, holding_map)

    apply_result = None
    if apply:
        apply_result = apply_realized_diffs(session, realized_diffs)
        if balance:
            account_balance_snapshot = sync_account_balance_snapshot(
                session, balance, day=end_day,
            )
        session.commit()
        sells2 = (
            session.query(SellOrder)
            .filter(
                SellOrder.status == "COMPLETED",
                SellOrder.completed_at >= start_ts,
                SellOrder.completed_at < end_ts,
            )
            .all()
        )
        db_map2 = aggregate_db_sells(sells2, start_day=start_day, end_day=end_day)
        if source == "ka10073":
            realized_diffs = compare_realized(kiwoom_map, db_map2)
        else:
            realized_diffs = compare_daily_totals(kiwoom_daily, db_sells_by_day(db_map2))
        db_net_sum = sum(int(v["db_net"]) for v in db_map2.values())
        db_map = db_map2
        if sync_holdings and holding_map:
            positions = session.query(Position).filter(Position.status == "HOLDING").all()
            holding_diffs = compare_holdings(positions, holding_map)

    source_rows = kiwoom_map.values() if source == "ka10073" else kiwoom_daily.values()
    kiwoom_fee_sum = sum(int(v.get("fee") or 0) for v in source_rows)
    source_rows = kiwoom_map.values() if source == "ka10073" else kiwoom_daily.values()
    kiwoom_tax_sum = sum(int(v.get("tax") or 0) for v in source_rows)
    db_fee_sum = sum(int(v.get("db_fee") or 0) for v in db_map.values())
    db_tax_sum = sum(int(v.get("db_tax") or 0) for v in db_map.values())

    report: Dict[str, Any] = {
        "start": start_day.isoformat(),
        "end": end_day.isoformat(),
        "kiwoom_net_sum": kiwoom_net_sum,
        "db_net_sum": db_net_sum,
        "delta_sum": kiwoom_net_sum - db_net_sum,
        "kiwoom_fee_sum": kiwoom_fee_sum,
        "kiwoom_tax_sum": kiwoom_tax_sum,
        "db_fee_sum": db_fee_sum,
        "db_tax_sum": db_tax_sum,
        "ka10074_total": ka10074_total,
        "stock_account_adjustment": stock_account_adjustment,
        "source": source,
        "kiwoom_row_count": len(kiwoom_map),
        "db_group_count": len(db_map),
        "realized_diffs": [
            {k: v for k, v in row.items() if k != "sells"}
            for row in realized_diffs
        ],
        "holding_diffs": holding_diffs,
        "account_balance_snapshot": account_balance_snapshot,
        "holdings_updated": holdings_updated,
        "apply_result": (
            {
                "updated_sells": apply_result.get("updated_sells"),
                "backfilled": apply_result.get("backfilled"),
                "skipped": apply_result.get("skipped"),
            }
            if apply_result
            else None
        ),
        "reconcile": reconcile_note,
        "applied": bool(apply),
    }
    return report
