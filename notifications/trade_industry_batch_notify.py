"""수출입 업종 지표 배치 시작/종료/오류 텔레그램 알림."""
from __future__ import annotations

import html
import logging
from typing import Any, List, Optional, Sequence

from notifications.telegram_notifier import TelegramNotifier
from utils.datetime_kst import now_kst

logger = logging.getLogger(__name__)


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def _fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0:
        return "—"
    sec = int(round(seconds))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}시간 {m}분 {s}초"
    if m:
        return f"{m}분 {s}초"
    return f"{s}초"


def _fmt_int(n: Optional[int]) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}"


def _fmt_usd(n: Optional[float]) -> str:
    if n is None:
        return "—"
    v = float(n)
    abs_v = abs(v)
    if abs_v >= 1e9:
        return f"{v / 1e9:.2f}B"
    if abs_v >= 1e6:
        return f"{v / 1e6:.1f}M"
    if abs_v >= 1e3:
        return f"{v / 1e3:.0f}K"
    return f"{v:.0f}"


def _fmt_pct(n: Optional[float]) -> str:
    if n is None:
        return "—"
    v = float(n)
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.1f}%"


def format_trade_industry_start_html(
    *,
    end_yyyymm: str,
    months: int,
    hs_count: int,
    country_count: int,
) -> str:
    now = now_kst().strftime("%Y-%m-%d %H:%M")
    return "\n".join(
        [
            "<b>📦 수출입 지표 배치 시작</b>",
            f"종료월: <code>{_esc(end_yyyymm)}</code> · 수집 {_esc(months)}개월",
            f"HS {_esc(_fmt_int(hs_count))} · 국가 {_esc(_fmt_int(country_count))}",
            f"시각: {_esc(now)}",
            "\n로그: <code>logs/trade_industry_batch.log</code>",
        ]
    )


def format_trade_industry_done_html(
    *,
    ok: bool,
    end_yyyymm: str,
    months: int,
    hs_rows: Optional[int] = None,
    industry_rows: Optional[int] = None,
    errors: Optional[int] = None,
    source: Optional[str] = None,
    duration_sec: Optional[float] = None,
    top_tags: Optional[Sequence[dict]] = None,
    error: Optional[str] = None,
) -> str:
    now = now_kst().strftime("%Y-%m-%d %H:%M")
    err_n = int(errors or 0)
    if ok and err_n <= 0 and not error:
        status_label = "✅ 완료"
    elif ok:
        status_label = "⚠️ 완료(일부 실패)"
    else:
        status_label = "❌ 실패"

    lines: List[str] = [
        "<b>📦 수출입 지표 배치 종료</b>",
        f"종료월: <code>{_esc(end_yyyymm)}</code> · {_esc(months)}개월",
        f"상태: {status_label}",
        f"소요: {_esc(_fmt_duration(duration_sec))} · 발송 {_esc(now)}",
        "",
        "<b>【요약】</b>",
        f"HS upsert: {_esc(_fmt_int(hs_rows))}",
        f"업종 집계: {_esc(_fmt_int(industry_rows))}",
        f"오류 창: {_esc(_fmt_int(errors))}",
        f"소스: <code>{_esc(source or '—')}</code>",
    ]
    if top_tags:
        lines.append("")
        lines.append("<b>【섹터 수출 YoY】</b>")
        for row in list(top_tags)[:8]:
            tag = row.get("tag") or row.get("grain_key") or "?"
            yoy = row.get("exp_yoy")
            exp = row.get("exp_usd")
            lines.append(
                f"· {_esc(tag)} {_esc(_fmt_usd(exp))} ({_esc(_fmt_pct(yoy))})"
            )
    if error:
        lines.append(f"\n오류: <code>{_esc(error)}</code>")
    lines.append("\n로그: <code>logs/trade_industry_batch.log</code>")
    return "\n".join(lines)


def format_trade_industry_error_html(
    *,
    end_yyyymm: Optional[str] = None,
    error: str,
    duration_sec: Optional[float] = None,
    context: Optional[str] = None,
) -> str:
    now = now_kst().strftime("%Y-%m-%d %H:%M")
    lines = [
        "<b>📦 수출입 지표 배치 오류</b>",
        f"종료월: <code>{_esc(end_yyyymm or '—')}</code>",
        f"시각: {_esc(now)}",
        f"소요: {_esc(_fmt_duration(duration_sec))}",
    ]
    if context:
        lines.append(f"구간: {_esc(context)}")
    lines.append(f"\n오류: <code>{_esc(error)}</code>")
    lines.append("\n로그: <code>logs/trade_industry_batch.log</code>")
    return "\n".join(lines)


def _send_html(msg: str, *, label: str) -> bool:
    notifier = TelegramNotifier()
    if not notifier.is_configured():
        logger.warning("텔레그램 미설정 — 수출입 배치 알림 스킵 (%s)", label)
        return False
    ok = notifier.send_message(msg, parse_mode="HTML")
    if ok:
        logger.info("수출입 배치 텔레그램 알림 전송 완료 (%s)", label)
    else:
        logger.error("수출입 배치 텔레그램 알림 전송 실패 (%s)", label)
    return ok


def notify_trade_industry_start(**kwargs: Any) -> bool:
    return _send_html(format_trade_industry_start_html(**kwargs), label="start")


def notify_trade_industry_done(**kwargs: Any) -> bool:
    return _send_html(format_trade_industry_done_html(**kwargs), label="done")


def notify_trade_industry_error(**kwargs: Any) -> bool:
    return _send_html(format_trade_industry_error_html(**kwargs), label="error")
