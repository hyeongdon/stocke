"""매수/매도 내역·매수 조건·청산(ATR) 설정을 엑셀로 정리."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "stock_pipeline.db"
DEFAULT_OUT = ROOT / "exports" / f"매매내역_{datetime.now():%Y%m%d_%H%M}.xlsx"

SIGNAL_TYPE_KO = {
    "condition": "키움 조건식",
    "reference": "기준봉 전략",
    "strategy": "차트 전략",
    "auto_trade": "자동매매 스캐너",
}

SELL_REASON_KO = {
    "STOP_LOSS": "손절",
    "TAKE_PROFIT": "익절",
    "TRAILING": "트레일링 스탑",
    "PROFIT_LOCK": "수익 잠금",
    "MARKET_CLOSE": "장마감 청산",
    "MANUAL": "수동 매도",
    "INDICATOR": "지표 매도",
}


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _cols(conn: sqlite3.Connection, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _fmt_dt(v: Any) -> str:
    if not v:
        return ""
    if isinstance(v, str):
        return v.replace("T", " ")[:19]
    return str(v)


def _condition_name(conn: sqlite3.Connection, condition_id: Optional[int]) -> str:
    if condition_id is None:
        return ""
    if condition_id in (0, 99999):
        return "자동매매 스캐너"
    row = conn.execute(
        "SELECT condition_name FROM auto_trade_conditions WHERE id = ?",
        (condition_id,),
    ).fetchone()
    if row:
        return row["condition_name"]
    row = conn.execute(
        "SELECT condition_name FROM auto_trade_conditions WHERE api_condition_id = ?",
        (str(condition_id),),
    ).fetchone()
    return row["condition_name"] if row else f"조건식#{condition_id}"


def _load_settings(conn: sqlite3.Connection) -> Dict[str, Any]:
    if not _table_exists(conn, "auto_trade_settings"):
        return {}
    row = conn.execute("SELECT * FROM auto_trade_settings LIMIT 1").fetchone()
    return dict(row) if row else {}


def _build_buy_condition_text(
    conn: sqlite3.Connection, settings: Dict[str, Any], signal: sqlite3.Row,
) -> str:
    parts: List[str] = []
    st = signal["signal_type"] if "signal_type" in signal.keys() else ""
    parts.append(SIGNAL_TYPE_KO.get(st, st or "알 수 없음"))

    cid = signal["condition_id"] if "condition_id" in signal.keys() else None
    cname = _condition_name(conn, cid)
    if cname:
        parts.append(f"조건: {cname}")

    ref_high = signal["reference_candle_high"] if "reference_candle_high" in signal.keys() else None
    target = signal["target_price"] if "target_price" in signal.keys() else None
    if ref_high:
        parts.append(f"기준봉 고가 {ref_high:,}원")
    if target:
        parts.append(f"목표가 {target:,}원")

    if settings:
        buy_bits: List[str] = []
        if settings.get("buy_below_price"):
            buy_bits.append(f"현재가 ≤ {settings['buy_below_price']:,}원")
        if settings.get("min_change_rate_buy") is not None:
            buy_bits.append(f"등락률 ≥ {settings['min_change_rate_buy']}%")
        if settings.get("use_entry_gate"):
            gates = []
            if settings.get("require_above_open"):
                gates.append("시가 이상")
            if settings.get("require_above_vwap"):
                gates.append("VWAP 이상")
            if settings.get("day_position_min") is not None:
                gates.append(f"당일위치 ≥ {settings['day_position_min']}")
            if settings.get("day_position_max") is not None:
                gates.append(f"당일위치 ≤ {settings['day_position_max']}")
            if settings.get("volume_ratio_min") is not None:
                gates.append(f"거래량비 ≥ {settings['volume_ratio_min']}%")
            if gates:
                buy_bits.append("진입게이트: " + ", ".join(gates))
        sizing = (settings.get("sizing_method") or "FIXED").upper()
        if sizing == "PYRAMIDING":
            buy_bits.append(
                f"역피라미딩 (등락 {settings.get('signal_min_threshold', 2)}%→"
                f"{settings.get('initial_max_amount', 0):,}원 · "
                f"{settings.get('signal_max_threshold', 10)}%→"
                f"{settings.get('initial_min_amount', 0):,}원, "
                f"추가 {settings.get('add_buy_amount', 0):,}원 @ +{settings.get('add_buy_trigger')}%)"
            )
        else:
            buy_bits.append(f"고정금액 (최대 {settings.get('max_invest_amount', 0):,}원)")
        if buy_bits:
            parts.append("매수조건: " + " · ".join(buy_bits))

    return " | ".join(p for p in parts if p)


def _build_exit_rule_text(settings: Dict[str, Any], pos: sqlite3.Row) -> str:
    parts: List[str] = []
    sl = pos["stop_loss_rate"] if "stop_loss_rate" in pos.keys() else None
    tp = pos["take_profit_rate"] if "take_profit_rate" in pos.keys() else None
    if tp is not None:
        parts.append(f"익절 {tp}%")
    if sl is not None:
        parts.append(f"손절 {sl}%")
    if settings.get("trailing_stop_pct"):
        parts.append(f"트레일 {settings['trailing_stop_pct']}% (고점 대비)")
    if settings.get("atr_mult_stop") or settings.get("atr_mult_trail"):
        atr_bits = []
        if settings.get("atr_mult_stop"):
            atr_bits.append(f"손절×{settings['atr_mult_stop']}")
        if settings.get("atr_mult_trail"):
            atr_bits.append(f"트레일×{settings['atr_mult_trail']}")
        period = settings.get("atr_period") or 14
        parts.append(f"ATR({period}일) " + ", ".join(atr_bits))
    if settings.get("profit_lock_trigger") is not None:
        parts.append(
            f"수익잠금 +{settings['profit_lock_trigger']}%→바닥 {settings.get('profit_lock_floor')}%"
        )
    if settings.get("liquidate_before_close"):
        parts.append(f"장마감 {settings.get('liquidate_time', '15:10')} 전량청산")
    return " · ".join(parts)


def fetch_trade_rows(conn: sqlite3.Connection) -> tuple:
    settings = _load_settings(conn)

    # --- 매수 신호 ---
    signal_rows: List[Dict] = []
    if _table_exists(conn, "pending_buy_signals"):
        for r in conn.execute(
            """
            SELECT * FROM pending_buy_signals
            ORDER BY detected_at DESC
            """
        ):
            d = dict(r)
            d["신호유형"] = SIGNAL_TYPE_KO.get(d.get("signal_type", ""), d.get("signal_type", ""))
            d["조건식명"] = _condition_name(conn, d.get("condition_id"))
            d["매수조건요약"] = _build_buy_condition_text(conn, settings, r)
            d["감지시각"] = _fmt_dt(d.get("detected_at"))
            d["감지일"] = d.get("detected_date")
            signal_rows.append(d)

    # --- 포지션 (매수) ---
    buy_rows: List[Dict] = []
    if _table_exists(conn, "positions"):
        for r in conn.execute("SELECT * FROM positions ORDER BY buy_time DESC"):
            d = dict(r)
            sig = None
            if d.get("signal_id"):
                sig = conn.execute(
                    "SELECT * FROM pending_buy_signals WHERE id = ?",
                    (d["signal_id"],),
                ).fetchone()
            d["매수시각"] = _fmt_dt(d.get("buy_time"))
            d["매도시각"] = _fmt_dt(d.get("sell_time"))
            d["조건식명"] = _condition_name(conn, d.get("condition_id"))
            if sig:
                d["신호유형"] = SIGNAL_TYPE_KO.get(sig["signal_type"], sig["signal_type"])
                d["신호상태"] = sig["status"]
                d["신호감지시각"] = _fmt_dt(sig["detected_at"])
                d["매수조건요약"] = _build_buy_condition_text(conn, settings, sig)
                if sig["reference_candle_high"]:
                    d["기준봉고가"] = sig["reference_candle_high"]
                if sig["target_price"]:
                    d["목표가"] = sig["target_price"]
            else:
                d["신호유형"] = ""
                d["매수조건요약"] = _condition_name(conn, d.get("condition_id")) or "신호 기록 없음"
            d["청산규칙"] = _build_exit_rule_text(settings, r)
            buy_rows.append(d)

    # --- 매도 주문 ---
    sell_rows: List[Dict] = []
    if _table_exists(conn, "sell_orders"):
        for r in conn.execute(
            "SELECT * FROM sell_orders ORDER BY created_at DESC"
        ):
            d = dict(r)
            reason = d.get("sell_reason", "")
            d["매도사유한글"] = SELL_REASON_KO.get(reason, reason)
            d["주문시각"] = _fmt_dt(d.get("created_at"))
            d["체결시각"] = _fmt_dt(d.get("completed_at"))
            sell_rows.append(d)

    return settings, signal_rows, buy_rows, sell_rows


def _atr_guide_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            ["ATR이란?", "최근 N일(기본 14일) 일봉의 평균 가격 변동폭(원). 변동성 큰 종목은 손절선을 넓게 잡음."],
            ["손절선 (ATR)", "매수가 − ATR × 손절배수(atr_mult_stop). 배수 입력 시 고정 % 손절 대체."],
            ["트레일선 (ATR)", "진입 후 고점 − ATR × 트레일배수(atr_mult_trail). 배수 입력 시 % 트레일 대체."],
            ["트레일링 B", "고점≥시작% → armed + 바닥 잠금 · 매도선=max(고점−트레일, 바닥)."],
            ["트레일 (%)", "ATR 미사용 시: 고점 대비 trailing_stop_pct% 하락 시 매도."],
            ["장마감 청산", "liquidate_time(예: 15:10)에 보유 전 종목 시장가 매도."],
            ["우선순위", "동시에 여러 조건 충족 시: 익절 > 트레일 > 손절 > 장마감 (코드 기준)."],
        ],
        columns=["항목", "설명"],
    )


def _settings_df(settings: Dict[str, Any]) -> pd.DataFrame:
    if not settings:
        return pd.DataFrame([{"메시지": "auto_trade_settings 없음"}])
    labels = {
        "is_enabled": "자동매매 ON",
        "max_invest_amount": "종목당 최대 투자(원)",
        "stop_loss_rate": "손절 %",
        "take_profit_rate": "익절 %",
        "buy_below_price": "매수: 현재가 이하(원)",
        "min_change_rate_buy": "매수: 최소 등락률 %",
        "trailing_stop_pct": "트레일 % (고점 대비)",
        "atr_mult_stop": "ATR 손절 배수",
        "atr_mult_trail": "ATR 트레일 배수",
        "atr_period": "ATR 기간(일)",
        "profit_lock_trigger": "수익잠금 트리거 %",
        "profit_lock_floor": "수익잠금 바닥 %",
        "sizing_method": "매수 사이징",
        "initial_min_amount": "초기 최소 금액",
        "initial_max_amount": "초기/종목 최대 금액",
        "add_buy_amount": "추가매수 금액",
        "add_buy_trigger": "추가매수 트리거 %",
        "liquidate_before_close": "장마감 청산",
        "liquidate_time": "청산 시각",
        "trade_start_time": "매매 시작",
        "trade_end_time": "매매 종료",
        "order_method": "주문 방식",
        "cash_reserve_pct": "현금 보유 %",
        "max_concurrent_positions": "최대 동시 보유",
    }
    rows = [{"설정항목": labels.get(k, k), "값": v} for k, v in settings.items() if k in labels]
    return pd.DataFrame(rows)


def _positions_summary_df(buy_rows: List[Dict]) -> pd.DataFrame:
    cols = [
        ("id", "포지션ID"),
        ("stock_code", "종목코드"),
        ("stock_name", "종목명"),
        ("매수시각", "매수시각"),
        ("buy_price", "매수단가"),
        ("buy_quantity", "수량"),
        ("buy_amount", "매수금액"),
        ("actual_buy_amount", "실매입금액(pur_amt)"),
        ("신호유형", "매수경로"),
        ("조건식명", "조건식"),
        ("매수조건요약", "매수조건"),
        ("청산규칙", "적용 청산규칙"),
        ("status", "상태"),
        ("current_price", "현재가"),
        ("current_profit_loss", "평가손익"),
        ("current_profit_loss_rate", "수익률%"),
        ("peak_price", "진입후고점"),
        ("매도시각", "매도시각"),
    ]
    out = []
    for r in buy_rows:
        out.append({ko: r.get(en) for en, ko in cols})
    return pd.DataFrame(out)


def _sells_summary_df(sell_rows: List[Dict]) -> pd.DataFrame:
    cols = [
        ("id", "주문ID"),
        ("position_id", "포지션ID"),
        ("stock_code", "종목코드"),
        ("stock_name", "종목명"),
        ("매도사유한글", "매도사유"),
        ("sell_reason_detail", "상세"),
        ("sell_price", "매도단가"),
        ("sell_quantity", "수량"),
        ("sell_amount", "매도금액"),
        ("profit_loss", "손익"),
        ("profit_loss_rate", "손익률%"),
        ("status", "상태"),
        ("주문시각", "주문시각"),
        ("체결시각", "체결시각"),
    ]
    return pd.DataFrame([{ko: r.get(en) for en, ko in cols} for r in sell_rows])


def _signals_summary_df(signal_rows: List[Dict]) -> pd.DataFrame:
    cols = [
        ("id", "신호ID"),
        ("stock_code", "종목코드"),
        ("stock_name", "종목명"),
        ("신호유형", "유형"),
        ("조건식명", "조건식"),
        ("status", "상태"),
        ("감지시각", "감지시각"),
        ("reference_candle_high", "기준봉고가"),
        ("target_price", "목표가"),
        ("failure_reason", "실패사유"),
        ("매수조건요약", "당시 매수조건"),
    ]
    return pd.DataFrame([{ko: r.get(en) for en, ko in cols} for r in signal_rows])


def export_excel(db_path: Path, out_path: Path) -> Path:
    conn = _connect(db_path)
    try:
        settings, signal_rows, buy_rows, sell_rows = fetch_trade_rows(conn)
    finally:
        conn.close()

    out_path.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        _positions_summary_df(buy_rows).to_excel(writer, sheet_name="매수_포지션", index=False)
        _sells_summary_df(sell_rows).to_excel(writer, sheet_name="매도_주문", index=False)
        _signals_summary_df(signal_rows).to_excel(writer, sheet_name="매수_신호", index=False)
        _settings_df(settings).to_excel(writer, sheet_name="현재_설정", index=False)
        _atr_guide_df().to_excel(writer, sheet_name="ATR_청산_설명", index=False)

        # 포지션↔매도 연결
        if buy_rows and sell_rows:
            merged = []
            sells_by_pos = {}
            for s in sell_rows:
                sells_by_pos.setdefault(s.get("position_id"), []).append(s)
            for b in buy_rows:
                for s in sells_by_pos.get(b.get("id"), []):
                    merged.append({
                        "종목": b.get("stock_name"),
                        "매수시각": b.get("매수시각"),
                        "매수단가": b.get("buy_price"),
                        "매수금액": b.get("buy_amount"),
                        "매수조건": b.get("매수조건요약"),
                        "매도사유": SELL_REASON_KO.get(s.get("sell_reason", ""), s.get("sell_reason")),
                        "매도상세": s.get("sell_reason_detail"),
                        "매도금액": s.get("sell_amount"),
                        "손익": s.get("profit_loss"),
                        "손익률%": s.get("profit_loss_rate"),
                    })
            if merged:
                pd.DataFrame(merged).to_excel(writer, sheet_name="매수매도_연결", index=False)

    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="매수/매도 내역을 엑셀로 내보냅니다.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite DB 경로")
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUT, help="출력 xlsx 경로")
    args = parser.parse_args()

    if not args.db.is_file():
        raise SystemExit(f"DB 파일 없음: {args.db}")

    out = export_excel(args.db, args.output)
    print(f"저장 완료: {out}")
    print(f"  - 매수_포지션: 종목별 매수 금액·조건·청산규칙")
    print(f"  - 매도_주문: 매도 사유(손절/익절/ATR트레일/장마감 등)")
    print(f"  - 매수_신호: 신호가 어떻게 생성됐는지")
    print(f"  - ATR_청산_설명: ATR 매도 개념 요약")


if __name__ == "__main__":
    main()
