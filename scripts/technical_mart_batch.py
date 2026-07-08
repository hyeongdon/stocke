"""
기술적분석 마트 배치(MVP) — 일봉 OHLCV → 기술지표 계산 → DB upsert.

사용 예:
  python scripts/technical_mart_batch.py
  python scripts/technical_mart_batch.py --market kospi --limit 200
  python scripts/technical_mart_batch.py --market all --workers 3
"""
from __future__ import annotations

import argparse
import asyncio
import io
import logging
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from api.kiwoom_api import KiwoomAPI  # noqa: E402
from core.models import FundamentalSnapshot, get_db  # noqa: E402
from utils.technical_mart_store import upsert_many  # noqa: E402

LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "technical_mart_batch.log")


def setup_logging() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="기술적분석 마트(MVP) 배치")
    parser.add_argument(
        "--market",
        choices=["all", "kospi", "kosdaq"],
        default="all",
        help="대상 시장 (기본: all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="처리 종목 수 제한(0=전체, 기본: 0)",
    )
    parser.add_argument(
        "--bars",
        type=int,
        default=150,
        help="종목당 조회 일봉 수(기본: 150, 최소 권장 121)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=3,
        help="동시 처리 워커 수(기본: 3)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.15,
        help="종목 처리 간 대기(초, 기본: 0.15)",
    )
    parser.add_argument(
        "--timeframe",
        default="1D",
        help="타임프레임 태그(기본: 1D)",
    )
    return parser.parse_args()


def _safe_div(num: Optional[float], den: Optional[float]) -> Optional[float]:
    if num is None or den in (None, 0):
        return None
    return float(num) / float(den)


def _avg(nums: Sequence[float]) -> Optional[float]:
    if not nums:
        return None
    return sum(nums) / len(nums)


def _last_sma(values: Sequence[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return _avg(values[-period:])


def _pct_change(values: Sequence[float], lag: int) -> Optional[float]:
    if len(values) <= lag:
        return None
    prev = values[-(lag + 1)]
    curr = values[-1]
    if not prev:
        return None
    return (curr / prev - 1.0) * 100.0


def _rsi14(closes: Sequence[float]) -> Optional[float]:
    period = 14
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    avg_gain = _avg(gains[:period]) or 0.0
    avg_loss = _avg(losses[:period]) or 0.0
    for i in range(period, len(deltas)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _atr14(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> Optional[float]:
    period = 14
    if len(closes) < period + 1:
        return None
    tr_values: List[float] = []
    for i in range(1, len(closes)):
        h = highs[i]
        l = lows[i]
        pc = closes[i - 1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        tr_values.append(tr)
    if len(tr_values) < period:
        return None
    return _avg(tr_values[-period:])


def _calc_mvp_from_bars(bars: Sequence[dict]) -> Optional[Dict]:
    if not bars:
        return None
    ordered = sorted(bars, key=lambda x: x.get("timestamp", ""))
    closes = [float(b.get("close", 0) or 0) for b in ordered]
    highs = [float(b.get("high", 0) or 0) for b in ordered]
    lows = [float(b.get("low", 0) or 0) for b in ordered]
    opens = [float(b.get("open", 0) or 0) for b in ordered]
    volumes = [float(b.get("volume", 0) or 0) for b in ordered]
    if not closes or closes[-1] <= 0:
        return None

    trading_values = [c * v for c, v in zip(closes, volumes)]
    ma5 = _last_sma(closes, 5)
    ma20 = _last_sma(closes, 20)
    ma60 = _last_sma(closes, 60)
    ma120 = _last_sma(closes, 120)
    last_close = closes[-1]
    high_20d = max(highs[-20:]) if len(highs) >= 20 else max(highs)
    low_20d = min(lows[-20:]) if len(lows) >= 20 else min(lows)

    pos_20d = None
    if high_20d is not None and low_20d is not None and high_20d > low_20d:
        pos_20d = (last_close - low_20d) / (high_20d - low_20d)

    return {
        "open_price": int(opens[-1]),
        "high_price": int(highs[-1]),
        "low_price": int(lows[-1]),
        "close_price": int(last_close),
        "volume": int(volumes[-1]),
        "trading_value": float(trading_values[-1]),
        "return_1d": _pct_change(closes, 1),
        "return_5d": _pct_change(closes, 5),
        "return_20d": _pct_change(closes, 20),
        "ma5": ma5,
        "ma20": ma20,
        "ma60": ma60,
        "ma120": ma120,
        "ma5_bias": ((_safe_div(last_close, ma5) - 1.0) * 100.0) if ma5 else None,
        "ma20_bias": ((_safe_div(last_close, ma20) - 1.0) * 100.0) if ma20 else None,
        "rsi14": _rsi14(closes),
        "atr14": _atr14(highs, lows, closes),
        "atr14_pct": ((_safe_div(_atr14(highs, lows, closes), last_close)) * 100.0) if last_close else None,
        "high_20d": high_20d,
        "low_20d": low_20d,
        "pos_20d": pos_20d,
        "avg_volume_20d": _avg(volumes[-20:]) if volumes else None,
        "avg_trading_value_20d": _avg(trading_values[-20:]) if trading_values else None,
    }


def _load_universe(market: str = "all", limit: int = 0) -> Tuple[datetime.date, List[Dict]]:
    """기본적분석 최신일을 기준으로 종목 유니버스 로드."""
    for db in get_db():
        latest = db.query(FundamentalSnapshot.as_of_date).order_by(FundamentalSnapshot.as_of_date.desc()).first()
        if not latest:
            return datetime.now().date(), []
        as_of_date = latest[0]
        q = db.query(
            FundamentalSnapshot.stock_code,
            FundamentalSnapshot.stock_name,
            FundamentalSnapshot.market,
        ).filter(FundamentalSnapshot.as_of_date == as_of_date)
        if market != "all":
            q = q.filter(FundamentalSnapshot.market == market.upper())
        rows = q.order_by(FundamentalSnapshot.stock_code.asc()).all()
        if limit and limit > 0:
            rows = rows[:limit]
        items = [
            {"stock_code": r.stock_code, "stock_name": r.stock_name, "market": r.market}
            for r in rows
        ]
        return as_of_date, items
    return datetime.now().date(), []


async def _process_one(
    api: KiwoomAPI,
    sem: asyncio.Semaphore,
    item: Dict,
    bars: int,
    sleep_sec: float,
) -> Optional[Dict]:
    async with sem:
        code = str(item.get("stock_code", "")).zfill(6)
        try:
            chart = await api.get_stock_chart_data(
                code,
                period="1D",
                max_bars=bars,
                allow_off_hours=True,
            )
            mvp = _calc_mvp_from_bars(chart or [])
            if not mvp:
                return None
            mvp.update(
                {
                    "stock_code": code,
                    "stock_name": item.get("stock_name") or "",
                    "market": item.get("market"),
                    "source": "kiwoom",
                }
            )
            return mvp
        except Exception:
            return None
        finally:
            if sleep_sec > 0:
                await asyncio.sleep(sleep_sec)


async def _run_async(args: argparse.Namespace, logger: logging.Logger) -> int:
    as_of_date, universe = _load_universe(args.market, args.limit)
    if not universe:
        logger.error("유니버스가 비어 있습니다. fundamental_mart_batch를 먼저 실행하세요.")
        return 1

    api = KiwoomAPI()
    if not api.authenticate():
        logger.error("키움 API 인증 실패")
        return 1

    logger.info(
        "기술적분석 마트 배치 시작 — as_of=%s market=%s universe=%d workers=%d",
        as_of_date,
        args.market,
        len(universe),
        args.workers,
    )

    sem = asyncio.Semaphore(max(1, int(args.workers)))
    tasks = [_process_one(api, sem, it, max(50, int(args.bars)), max(0.0, float(args.sleep))) for it in universe]
    done = await asyncio.gather(*tasks, return_exceptions=False)
    rows = [r for r in done if r]
    if not rows:
        logger.error("저장할 기술지표 데이터가 없습니다.")
        return 1

    saved = upsert_many(rows, as_of_date=as_of_date, timeframe=args.timeframe)
    logger.info(
        "완료 — upsert=%d success=%d/%d timeframe=%s as_of=%s",
        saved,
        len(rows),
        len(universe),
        args.timeframe.upper(),
        as_of_date,
    )
    return 0


def main() -> int:
    setup_logging()
    logger = logging.getLogger(__name__)
    args = parse_args()
    started = datetime.now()
    rc = asyncio.run(_run_async(args, logger))
    elapsed = (datetime.now() - started).total_seconds()
    logger.info("종료 코드=%d, 소요=%.1fs", rc, elapsed)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

