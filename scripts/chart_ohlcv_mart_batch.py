"""
차트 OHLCV 마트 배치 — 일/주/월봉을 장외에 적재해 분석화면 API 부하를 줄인다.

사용 예:
  python scripts/chart_ohlcv_mart_batch.py
  python scripts/chart_ohlcv_mart_batch.py --scope favorites
  python scripts/chart_ohlcv_mart_batch.py --scope fundamental --limit 200
  python scripts/chart_ohlcv_mart_batch.py --codes 000150,005930
  python scripts/chart_ohlcv_mart_batch.py --scope fundamental --intervals 1D --chunk-size 100
"""
from __future__ import annotations

import argparse
import asyncio
import io
import logging
import os
import sys
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from api.kiwoom_api import KiwoomAPI  # noqa: E402
from core.models import (  # noqa: E402
    AnalysisFavorite,
    FundamentalSnapshot,
    ensure_chart_ohlcv_series_table,
    get_db,
)
from notifications.chart_ohlcv_batch_notify import (  # noqa: E402
    notify_chart_ohlcv_done,
    notify_chart_ohlcv_error,
    notify_chart_ohlcv_start,
)
from utils.chart_ohlcv_mart_store import (  # noqa: E402
    FULL_SOURCE,
    MIN_DEPTH,
    get_bars,
    get_depth_map,
    get_meta,
    list_codes,
    needs_deep_fetch,
    upsert_many,
)
from utils.volume_profile_store import PROFILE_INTERVALS, upsert_from_rows  # noqa: E402
from utils.datetime_kst import kst_today, now_kst  # noqa: E402

LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "chart_ohlcv_mart_batch.log")

INTERVAL_BARS = {
    # 차트 요청 봉 수 = 표시 봉 + MA120 워밍업(119). 기본 표시(일 504 · 주 260 · 월 120)를 DB만으로 채우도록 맞춤.
    "1D": 650,   # 504 + 119 = 623
    "1W": 400,   # 260 + 119 = 379
    "1MO": 240,  # 120 + 119 = 239
}
RETRY_ROUNDS = 2
RETRY_DELAY_SEC = 30.0

# 깊게(연속조회로 INTERVAL_BARS 까지) 받는 종목 수 상한 — 실행(run)·간격당.
# 이력이 MIN_DEPTH 미만인 종목만 대상이며, 그 외에는 최신 1페이지만 받아 기존 이력과 병합한다.
# 일봉은 매 평일, 주·월봉은 주 1회 실행되므로 주·월봉 예산을 더 크게 둔다. 0 = 무제한.
DEEP_BUDGET_DEFAULT = {"1D": 400, "1W": 1500, "1MO": 1500}


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
    p = argparse.ArgumentParser(description="차트 OHLCV 마트 배치")
    p.add_argument(
        "--scope",
        choices=["favorites", "fundamental", "codes"],
        default="favorites",
        help="유니버스 (기본: favorites = 분석 즐겨찾기)",
    )
    p.add_argument(
        "--codes",
        default="",
        help="쉼표 구분 종목코드 (--scope codes 또는 추가 포함)",
    )
    p.add_argument(
        "--market",
        choices=["all", "kospi", "kosdaq"],
        default="all",
        help="fundamental 스코프 시장 필터",
    )
    p.add_argument("--limit", type=int, default=0, help="종목 수 제한(0=전체)")
    p.add_argument(
        "--intervals",
        nargs="+",
        default=["1D", "1W", "1MO"],
        help="적재 인터벌 (기본: 1D 1W 1MO). PowerShell에서는 공백 구분 권장",
    )
    p.add_argument(
        "--deep-budget",
        type=int,
        default=None,
        help="간격당 '깊게 받기'(연속조회) 종목 수 상한. 미지정=간격별 기본(일400·주1500·월1500), 0=무제한. "
        "이력이 얕은 종목만 대상, 나머지는 최신 1페이지만 받아 기존 이력과 병합",
    )
    p.add_argument("--workers", type=int, default=2, help="동시 워커 수(기본: 2)")
    p.add_argument("--sleep", type=float, default=0.25, help="종목·인터벌 간 대기(초)")
    p.add_argument(
        "--chunk-size",
        type=int,
        default=100,
        help="조회·저장 청크 크기(기본: 100). 청크마다 upsert 해서 중단 시에도 적재 유지",
    )
    p.add_argument(
        "--no-telegram",
        action="store_true",
        help="시작/완료/실패 텔레그램 알림 비활성화",
    )
    p.add_argument("--skip-profile", action="store_true", help="매물대 계산 생략")
    p.add_argument(
        "--profile-only",
        action="store_true",
        help="API 조회 없이 저장된 일/주봉 마트로 매물대만 재계산",
    )
    return p.parse_args()


def _parse_intervals(raw) -> List[str]:
    parts: List[str] = []
    if isinstance(raw, (list, tuple)):
        seq = raw
    else:
        seq = [str(raw or "")]
    for item in seq:
        for part in str(item or "").replace(" ", ",").upper().split(","):
            part = part.strip()
            if part in INTERVAL_BARS and part not in parts:
                parts.append(part)
    return parts or ["1D", "1W", "1MO"]


def _norm_code(raw: str) -> str:
    s = str(raw or "").strip().lstrip("Aa")
    return s.zfill(6) if s.isdigit() else ""


def _parse_codes(raw: str) -> List[str]:
    out: List[str] = []
    seen = set()
    for part in str(raw or "").replace(" ", "").split(","):
        code = _norm_code(part)
        if code and code not in seen:
            seen.add(code)
            out.append(code)
    return out


def _load_favorites() -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    seen = set()
    for db in get_db():
        rows = (
            db.query(AnalysisFavorite.stock_code, AnalysisFavorite.stock_name)
            .order_by(AnalysisFavorite.stock_code.asc())
            .all()
        )
        for code, name in rows:
            c = _norm_code(code)
            if not c or c in seen:
                continue
            seen.add(c)
            items.append({"stock_code": c, "stock_name": name or ""})
        break
    return items


def _load_fundamental(market: str, limit: int) -> Tuple[date, List[Dict[str, str]]]:
    for db in get_db():
        latest = (
            db.query(FundamentalSnapshot.as_of_date)
            .order_by(FundamentalSnapshot.as_of_date.desc())
            .first()
        )
        if not latest:
            return kst_today(), []
        as_of = latest[0]
        q = db.query(
            FundamentalSnapshot.stock_code,
            FundamentalSnapshot.stock_name,
            FundamentalSnapshot.market,
        ).filter(FundamentalSnapshot.as_of_date == as_of)
        if market != "all":
            q = q.filter(FundamentalSnapshot.market == market.upper())
        rows = q.order_by(FundamentalSnapshot.stock_code.asc()).all()
        if limit > 0:
            rows = rows[:limit]
        items = [
            {
                "stock_code": _norm_code(r.stock_code),
                "stock_name": r.stock_name or "",
                "market": r.market or "",
            }
            for r in rows
            if _norm_code(r.stock_code)
        ]
        return as_of, items
    return kst_today(), []


def load_universe(args: argparse.Namespace) -> Tuple[date, List[Dict[str, str]]]:
    as_of = kst_today()
    by_code: Dict[str, Dict[str, str]] = {}

    if args.scope == "favorites":
        for it in _load_favorites():
            by_code[it["stock_code"]] = it
    elif args.scope == "fundamental":
        as_of, items = _load_fundamental(args.market, 0)
        for it in items:
            by_code[it["stock_code"]] = it
    elif args.scope == "codes":
        for code in _parse_codes(args.codes):
            by_code[code] = {"stock_code": code, "stock_name": ""}

    # --codes 는 어떤 스코프에도 추가 가능
    if args.scope != "codes":
        for code in _parse_codes(args.codes):
            by_code.setdefault(code, {"stock_code": code, "stock_name": ""})

    items = list(by_code.values())
    items.sort(key=lambda x: x["stock_code"])
    if args.limit and args.limit > 0:
        items = items[: int(args.limit)]
    return as_of, items


class FetchPlan:
    """종목×간격별 '깊게 받기 / 최신 1페이지만' 결정 + 간격당 깊게 받기 예산.

    깊게 받기: 이력이 MIN_DEPTH 미만(그리고 키움 이력을 끝까지 받은 종목이 아님)일 때, 예산이 남아 있으면
    연속조회로 INTERVAL_BARS 까지. 그 외: 최신 1페이지(max_bars=None)만 받아 기존 이력과 병합.
    """

    def __init__(
        self,
        depth: Dict[str, Dict[str, Tuple[int, str]]],
        budget: Dict[str, Optional[int]],
    ) -> None:
        self.depth = depth
        self.budget = dict(budget)
        self.deep_taken: Dict[str, int] = {iv: 0 for iv in budget}
        self.deep_exhausted_full: Dict[str, int] = {iv: 0 for iv in budget}

    def take(self, interval: str, code: str) -> bool:
        n, src = self.depth.get(interval, {}).get(code, (0, ""))
        left = self.budget.get(interval)
        if not needs_deep_fetch(interval, n, src, left):
            return False
        if left is not None:
            self.budget[interval] = left - 1
        self.deep_taken[interval] = self.deep_taken.get(interval, 0) + 1
        return True


def resolve_budget(args: argparse.Namespace, intervals: Sequence[str]) -> Dict[str, Optional[int]]:
    """--deep-budget(전 간격 공통) 또는 간격별 기본값. 0 이하(명시)는 무제한(None)."""
    explicit = getattr(args, "deep_budget", None)
    out: Dict[str, Optional[int]] = {}
    for iv in intervals:
        value = DEEP_BUDGET_DEFAULT.get(iv, 0) if explicit is None else int(explicit)
        out[iv] = value if value > 0 else None
    return out


def prioritize_universe(items: List[Dict[str, str]], favorites: set) -> List[Dict[str, str]]:
    """분석 즐겨찾기를 먼저 (깊게 받기 예산과 최신화가 즐겨찾기부터 소진·적용되도록), 나머지는 코드순."""
    return sorted(items, key=lambda x: (0 if x["stock_code"] in favorites else 1, x["stock_code"]))


async def _fetch_one(
    api: KiwoomAPI,
    sem: asyncio.Semaphore,
    item: Dict[str, str],
    intervals: Sequence[str],
    sleep_sec: float,
    plan: Optional[FetchPlan] = None,
) -> List[Dict]:
    async with sem:
        code = item["stock_code"]
        name = item.get("stock_name") or ""
        out: List[Dict] = []
        for interval in intervals:
            deep = plan.take(interval, code) if plan is not None else True
            max_bars = INTERVAL_BARS.get(interval, 400) if deep else None
            try:
                raw = await api.get_stock_chart_data(
                    code,
                    period=interval,
                    max_bars=max_bars,
                    allow_off_hours=True,
                )
            except Exception:
                raw = None
            if raw:
                # None = 기존 source 유지(병합 성공 시). 깊게 받았는데도 MIN_DEPTH 에 못 미치고
                # 키움이 줄 수 있는 이력 전부였다면 FULL_SOURCE 로 표시해 다음부터 재시도하지 않는다.
                source: Optional[str] = None
                if deep:
                    full = (
                        len(raw) < MIN_DEPTH.get(interval, 0)
                        and api.chart_history_exhausted(code, interval, max_bars)
                    )
                    source = FULL_SOURCE if full else "kiwoom"
                    if full and plan is not None:
                        plan.deep_exhausted_full[interval] = plan.deep_exhausted_full.get(interval, 0) + 1
                out.append(
                    {
                        "stock_code": code,
                        "stock_name": name,
                        "interval": interval,
                        "bars": raw,
                        "source": source,
                        "merge": True,
                    }
                )
            if sleep_sec > 0:
                await asyncio.sleep(sleep_sec)
        return out


def _save_profiles(
    args: argparse.Namespace, rows: List[Dict], as_of_date: date, logger: logging.Logger
) -> int:
    if getattr(args, "skip_profile", False):
        return 0
    try:
        return upsert_from_rows(rows, as_of_date=as_of_date)
    except Exception as e:
        logger.exception("매물대 계산 실패(봉 적재는 유지): %s", e)
        return 0


def run_profile_only(args: argparse.Namespace, logger: logging.Logger) -> int:
    as_of = kst_today()
    explicit = _parse_codes(args.codes)
    total = 0
    for interval in PROFILE_INTERVALS:
        codes = explicit or list_codes(interval)
        if args.limit and args.limit > 0:
            codes = codes[: int(args.limit)]
        rows = []
        for code in codes:
            bars = get_bars(code, interval)
            if bars:
                meta = get_meta(code, interval) or {}
                rows.append(
                    {
                        "stock_code": code,
                        "stock_name": meta.get("stock_name") or "",
                        "interval": interval,
                        "bars": bars,
                    }
                )
        saved = upsert_from_rows(rows, as_of_date=as_of)
        total += saved
        logger.info("매물대 재계산 %s — codes=%d profiles=%d", interval, len(rows), saved)
    return 0 if total > 0 else 1


def _telegram_on(args: argparse.Namespace) -> bool:
    return not bool(getattr(args, "no_telegram", False))


def _notify(enabled: bool, fn, logger: logging.Logger, **kwargs) -> None:
    if not enabled:
        return
    try:
        ok = fn(**kwargs)
        logger.info("텔레그램 알림 %s: %s", getattr(fn, "__name__", "notify"), "OK" if ok else "FAIL/SKIP")
    except Exception as e:
        logger.exception("텔레그램 알림 전송 중 오류: %s", e)


async def _run_async(args: argparse.Namespace, logger: logging.Logger) -> Tuple[int, Dict]:
    ensure_chart_ohlcv_series_table()
    as_of, universe = load_universe(args)
    intervals = _parse_intervals(args.intervals)
    if universe:
        universe = prioritize_universe(universe, {it["stock_code"] for it in _load_favorites()})
    plan = FetchPlan(
        {iv: get_depth_map(iv) for iv in intervals},
        resolve_budget(args, intervals),
    )
    chunk_size = max(1, int(getattr(args, "chunk_size", 100) or 100))
    as_of_date = as_of if isinstance(as_of, date) else kst_today()
    meta: Dict = {
        "as_of": str(as_of),
        "scope": args.scope,
        "universe": len(universe),
        "intervals": intervals,
        "chunk_size": chunk_size,
        "saved": 0,
        "success_codes": 0,
        "error": None,
    }
    if not universe:
        msg = "유니버스가 비어 있습니다. 즐겨찾기를 추가하거나 --scope fundamental / --codes 를 지정하세요."
        logger.error(msg)
        meta["error"] = msg
        return 1, meta

    api = KiwoomAPI()
    if not api.authenticate():
        msg = "키움 API 인증 실패"
        logger.error(msg)
        meta["error"] = msg
        return 1, meta

    total_chunks = (len(universe) + chunk_size - 1) // chunk_size
    logger.info(
        "차트 OHLCV 마트 배치 시작 — as_of=%s scope=%s universe=%d intervals=%s "
        "workers=%d chunk_size=%d chunks=%d",
        as_of,
        args.scope,
        len(universe),
        ",".join(intervals),
        args.workers,
        chunk_size,
        total_chunks,
    )
    for iv in intervals:
        shallow = sum(
            1
            for code, (n, src) in plan.depth.get(iv, {}).items()
            if n < MIN_DEPTH.get(iv, 0) and src != FULL_SOURCE
        )
        logger.info(
            "깊게 받기 계획 %s — 이력 얕은 종목=%d 예산=%s (최소 %d봉, 목표 %d봉)",
            iv,
            shallow,
            plan.budget.get(iv) if plan.budget.get(iv) is not None else "무제한",
            MIN_DEPTH.get(iv, 0),
            INTERVAL_BARS.get(iv, 0),
        )
    _notify(
        _telegram_on(args),
        notify_chart_ohlcv_start,
        logger,
        as_of=str(as_of),
        scope=args.scope,
        universe=len(universe),
        intervals=intervals,
    )

    sem = asyncio.Semaphore(max(1, int(args.workers)))
    sleep_sec = max(0.0, float(args.sleep))
    ok_codes: set[str] = set()
    saved_total = 0

    for chunk_idx in range(0, len(universe), chunk_size):
        chunk = universe[chunk_idx : chunk_idx + chunk_size]
        chunk_no = chunk_idx // chunk_size + 1
        logger.info(
            "청크 %d/%d 시작 — codes=%d (%s … %s)",
            chunk_no,
            total_chunks,
            len(chunk),
            chunk[0]["stock_code"],
            chunk[-1]["stock_code"],
        )
        tasks = [
            _fetch_one(api, sem, it, intervals, sleep_sec, plan) for it in chunk
        ]
        done = await asyncio.gather(*tasks, return_exceptions=False)
        rows: List[Dict] = []
        for part in done:
            rows.extend(part or [])

        if not rows:
            logger.warning(
                "청크 %d/%d — 저장할 데이터 없음 (API 빈 응답·한도 가능)",
                chunk_no,
                total_chunks,
            )
            continue

        saved = upsert_many(rows, as_of_date=as_of_date)
        profiles = _save_profiles(args, rows, as_of_date, logger)
        codes = {r["stock_code"] for r in rows}
        ok_codes.update(codes)
        saved_total += int(saved)
        logger.info(
            "청크 %d/%d 저장 — upsert=%d series  매물대=%d  codes=%d  누적 upsert=%d success_codes=%d/%d",
            chunk_no,
            total_chunks,
            saved,
            profiles,
            len(codes),
            saved_total,
            len(ok_codes),
            len(universe),
        )

    for attempt in range(1, RETRY_ROUNDS + 1):
        failed = [it for it in universe if it["stock_code"] not in ok_codes]
        if not failed:
            break
        logger.warning(
            "실패 종목 재시도 %d/%d — codes=%d (%s)",
            attempt,
            RETRY_ROUNDS,
            len(failed),
            ",".join(it["stock_code"] for it in failed[:20]),
        )
        await asyncio.sleep(RETRY_DELAY_SEC)
        done = await asyncio.gather(
            *[_fetch_one(api, sem, it, intervals, sleep_sec, plan) for it in failed]
        )
        rows = [r for part in done for r in (part or [])]
        if not rows:
            continue
        saved = upsert_many(rows, as_of_date=as_of_date)
        _save_profiles(args, rows, as_of_date, logger)
        ok_codes.update(r["stock_code"] for r in rows)
        saved_total += int(saved)
        logger.info(
            "재시도 %d/%d 저장 — upsert=%d series  누적 success_codes=%d/%d",
            attempt,
            RETRY_ROUNDS,
            saved,
            len(ok_codes),
            len(universe),
        )

    failed_codes = [it["stock_code"] for it in universe if it["stock_code"] not in ok_codes]
    if failed_codes:
        logger.warning("최종 누락 종목 %d건: %s", len(failed_codes), ",".join(failed_codes))

    for iv in intervals:
        logger.info(
            "깊게 받기 결과 %s — 시도=%d (이력 전부 확인=%d) 남은 예산=%s",
            iv,
            plan.deep_taken.get(iv, 0),
            plan.deep_exhausted_full.get(iv, 0),
            plan.budget.get(iv) if plan.budget.get(iv) is not None else "무제한",
        )
    meta["deep_fetched"] = dict(plan.deep_taken)
    meta["saved"] = int(saved_total)
    meta["success_codes"] = len(ok_codes)
    meta["failed_codes"] = failed_codes
    if saved_total <= 0:
        msg = "저장할 차트 데이터가 없습니다 (API 빈 응답·한도 가능)."
        logger.error(msg)
        meta["error"] = msg
        return 1, meta

    logger.info(
        "완료 — upsert=%d series  success_codes=%d/%d as_of=%s chunk_size=%d",
        saved_total,
        len(ok_codes),
        len(universe),
        as_of,
        chunk_size,
    )
    return 0, meta


def main() -> int:
    setup_logging()
    logger = logging.getLogger(__name__)
    args = parse_args()
    started = now_kst()
    telegram_on = _telegram_on(args)
    intervals = _parse_intervals(args.intervals)
    if args.profile_only:
        rc = run_profile_only(args, logger)
        logger.info("매물대 재계산 종료 코드=%d, 소요=%.1fs", rc, (now_kst() - started).total_seconds())
        return rc
    try:
        rc, meta = asyncio.run(_run_async(args, logger))
    except Exception as e:
        elapsed = (now_kst() - started).total_seconds()
        logger.exception("차트 OHLCV 배치 예외: %s", e)
        _notify(
            telegram_on,
            notify_chart_ohlcv_error,
            logger,
            as_of=str(kst_today()),
            scope=getattr(args, "scope", None),
            intervals=intervals,
            error=str(e),
            duration_sec=elapsed,
        )
        return 1

    elapsed = (now_kst() - started).total_seconds()
    logger.info("종료 코드=%d, 소요=%.1fs", rc, elapsed)
    if rc == 0:
        _notify(
            telegram_on,
            notify_chart_ohlcv_done,
            logger,
            ok=True,
            as_of=meta.get("as_of") or str(kst_today()),
            scope=meta.get("scope") or args.scope,
            universe=int(meta.get("universe") or 0),
            intervals=meta.get("intervals") or intervals,
            saved=meta.get("saved"),
            success_codes=meta.get("success_codes"),
            duration_sec=elapsed,
        )
    else:
        _notify(
            telegram_on,
            notify_chart_ohlcv_error,
            logger,
            as_of=meta.get("as_of"),
            scope=meta.get("scope") or args.scope,
            intervals=meta.get("intervals") or intervals,
            error=meta.get("error") or f"exit={rc}",
            duration_sec=elapsed,
        )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
