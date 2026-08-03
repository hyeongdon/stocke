"""
업종/테마별 수출입 월간 통계 배치 (PRD_TRADE_INDUSTRY_STATS Phase1)

사용:
  python scripts/trade_industry_batch.py
  python scripts/trade_industry_batch.py --months 24 --sleep 0.2
  python scripts/trade_industry_batch.py --no-telegram
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import sys
import time
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from core.config import Config  # noqa: E402
from core.models import get_db, init_db  # noqa: E402
from notifications.trade_industry_batch_notify import (  # noqa: E402
    notify_trade_industry_done,
    notify_trade_industry_error,
    notify_trade_industry_start,
)
from utils.customs_trade_api import fetch_hs_monthly_world  # noqa: E402
from utils.trade_industry_store import (  # noqa: E402
    list_latest_by_tag,
    load_baskets_config,
    recompute_industry_monthly,
    seed_maps_from_baskets,
    upsert_hs_monthly,
)

LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "trade_industry_batch.log")


def setup_logging() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )


def _month_windows(months: int, end: date) -> List[Tuple[str, str]]:
    """API 1년 제한을 고려해 [start,end] 윈도우 목록(최대 12개월) 생성."""
    points: List[str] = []
    cy, cm = end.year, end.month
    for _ in range(months):
        points.append(f"{cy:04d}{cm:02d}")
        cm -= 1
        if cm <= 0:
            cm = 12
            cy -= 1
    points = list(reversed(points))
    windows: List[Tuple[str, str]] = []
    i = 0
    while i < len(points):
        chunk = points[i : i + 12]
        windows.append((chunk[0], chunk[-1]))
        i += 12
    return windows


def _end_month(args: argparse.Namespace) -> date:
    end = date.today().replace(day=1)
    if end.month == 1:
        end = date(end.year - 1, 12, 1)
    else:
        end = date(end.year, end.month - 1, 1)
    if args.end_yyyymm:
        end = date(int(args.end_yyyymm[:4]), int(args.end_yyyymm[4:6]), 1)
    return end


def _telegram_notify(enabled: bool, fn, log: logging.Logger, **kwargs) -> None:
    if not enabled:
        return
    try:
        fn(**kwargs)
    except Exception as e:
        log.exception("텔레그램 알림 전송 중 오류: %s", e)


def _top_tag_rows(db, limit: int = 8) -> List[Dict[str, Any]]:
    try:
        payload = list_latest_by_tag(db, limit_tags=max(limit, 12))
        items = payload.get("items") or []
        rows: List[Dict[str, Any]] = []
        for it in items:
            latest = it.get("latest") or {}
            rows.append(
                {
                    "tag": it.get("tag"),
                    "exp_usd": latest.get("exp_usd"),
                    "exp_yoy": latest.get("exp_yoy"),
                }
            )
        rows.sort(
            key=lambda r: (
                r.get("exp_yoy") is None,
                -(float(r["exp_yoy"]) if r.get("exp_yoy") is not None else 0.0),
            )
        )
        return rows[:limit]
    except Exception:
        return []


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="관세청 수출입 → 테마별 월간 집계 배치")
    p.add_argument("--months", type=int, default=24, help="수집 개월 수(기본 24)")
    p.add_argument("--sleep", type=float, default=0.2, help="국가/요청 간 대기(초)")
    p.add_argument("--end-yyyymm", default=None, help="종료 월 YYYYMM (기본: 전월)")
    p.add_argument("--seed-only", action="store_true", help="매핑 시드만")
    p.add_argument("--recompute-only", action="store_true", help="원시 HS로 재집계만")
    p.add_argument(
        "--no-telegram",
        action="store_true",
        help="시작/종료/오류 텔레그램 알림 비활성화",
    )
    return p.parse_args()


def main() -> int:
    setup_logging()
    log = logging.getLogger(__name__)
    args = parse_args()
    telegram_on = not bool(args.no_telegram)
    init_db()
    end = _end_month(args)
    end_yyyymm = f"{end.year:04d}{end.month:02d}"
    months = max(1, int(args.months))
    started = time.monotonic()

    if not Config.DATA_GO_KR_SERVICE_KEY and not args.seed_only and not args.recompute_only:
        log.error("DATA_GO_KR_SERVICE_KEY 가 .env 에 없습니다.")
        _telegram_notify(
            telegram_on,
            notify_trade_industry_error,
            log,
            end_yyyymm=end_yyyymm,
            error="DATA_GO_KR_SERVICE_KEY 미설정",
            duration_sec=time.monotonic() - started,
            context="preflight",
        )
        return 2

    cfg = load_baskets_config()
    countries = [str(c).upper() for c in (cfg.get("partner_countries") or ["CN", "US", "JP"])]
    baskets = cfg.get("baskets") or []
    hs_set = sorted(
        {
            str(h).strip()
            for b in baskets
            for h in (b.get("hs_codes") or [])
            if str(h).strip()
        }
    )

    for db in get_db():
        seeded = seed_maps_from_baskets(db, cfg)
        log.info("매핑 시드: %s", seeded)
        if args.seed_only:
            return 0

        if args.recompute_only:
            stats = recompute_industry_monthly(db, cfg, source="data.go.kr/nitemtrade+partners")
            log.info("재집계: %s", stats)
            return 0

        windows = _month_windows(months, end)
        log.info(
            "수집 시작 hs=%d countries=%d windows=%s",
            len(hs_set),
            len(countries),
            windows,
        )
        _telegram_notify(
            telegram_on,
            notify_trade_industry_start,
            log,
            end_yyyymm=end_yyyymm,
            months=months,
            hs_count=len(hs_set),
            country_count=len(countries),
        )

        source_used = "data.go.kr/nitemtrade+partners"
        total_rows = 0
        errors = 0
        t0 = time.monotonic()
        try:
            for hs in hs_set:
                for w_start, w_end in windows:
                    try:
                        rows, src = fetch_hs_monthly_world(
                            hs_sgn=hs,
                            strt_yymm=w_start,
                            end_yymm=w_end,
                            countries=countries,
                            sleep_sec=float(args.sleep),
                            prefer_itemtrade=True,
                        )
                        source_used = src
                        n = upsert_hs_monthly(db, rows, source=src)
                        total_rows += n
                        log.info(
                            "HS %s %s~%s → %d행 (source=%s)",
                            hs,
                            w_start,
                            w_end,
                            n,
                            src,
                        )
                    except Exception as e:
                        errors += 1
                        log.exception("HS %s 창 %s~%s 실패: %s", hs, w_start, w_end, e)
                    time.sleep(float(args.sleep))

            stats = recompute_industry_monthly(db, cfg, source=source_used)
            elapsed = time.monotonic() - t0
            industry_rows = int((stats or {}).get("industry_rows") or 0)
            log.info(
                "완료 rows=%d industry=%s errors=%d elapsed=%.1fs source=%s",
                total_rows,
                stats,
                errors,
                elapsed,
                source_used,
            )
            ok = errors == 0 or total_rows > 0
            _telegram_notify(
                telegram_on,
                notify_trade_industry_done,
                log,
                ok=ok,
                end_yyyymm=end_yyyymm,
                months=months,
                hs_rows=total_rows,
                industry_rows=industry_rows,
                errors=errors,
                source=source_used,
                duration_sec=elapsed,
                top_tags=_top_tag_rows(db),
                error=None if errors == 0 else f"HS 창 실패 {errors}건",
            )
            return 0 if ok else 1
        except Exception as e:
            elapsed = time.monotonic() - t0
            log.exception("배치 예외: %s", e)
            _telegram_notify(
                telegram_on,
                notify_trade_industry_error,
                log,
                end_yyyymm=end_yyyymm,
                error=f"{type(e).__name__}: {e}",
                duration_sec=elapsed,
                context="collect",
            )
            return 1

    log.error("DB 세션을 열지 못했습니다.")
    _telegram_notify(
        telegram_on,
        notify_trade_industry_error,
        log,
        end_yyyymm=end_yyyymm,
        error="DB 세션을 열지 못했습니다.",
        duration_sec=time.monotonic() - started,
        context="db",
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
