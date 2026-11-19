"""차트 OHLCV 마트 배치 시작/종료/오류 텔레그램 알림."""
from __future__ import annotations

import html
import logging
from typing import Any, Optional, Sequence

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


def _intervals_label(intervals: Optional[Sequence[str]]) -> str:
    parts = [str(x).strip() for x in (intervals or []) if str(x).strip()]
    return ",".join(parts) if parts else "—"


def format_chart_ohlcv_start_html(
    *,
    as_of: str,
    scope: str,
    universe: int,
    intervals: Optional[Sequence[str]] = None,
) -> str:
    now = now_kst().strftime("%Y-%m-%d %H:%M")
    return "\n".join(
        [
            "<b>📈 차트 OHLCV 마트 시작</b>",
            f"기준일: <code>{_esc(as_of)}</code>",
            f"스코프: <code>{_esc(scope)}</code> · 종목 {_esc(_fmt_int(universe))}",
            f"인터벌: <code>{_esc(_intervals_label(intervals))}</code>",
            f"시각: {_esc(now)}",
            "\n로그: <code>logs/chart_ohlcv_mart_batch.log</code>",
        ]
    )


def format_chart_ohlcv_done_html(
    *,
    ok: bool,
    as_of: str,
    scope: str,
    universe: int,
    intervals: Optional[Sequence[str]] = None,
    saved: Optional[int] = None,
    success_codes: Optional[int] = None,
    duration_sec: Optional[float] = None,
    error: Optional[str] = None,
) -> str:
    now = now_kst().strftime("%Y-%m-%d %H:%M")
    ok_n = int(success_codes or 0)
    uni = int(universe or 0)
    if ok and not error and (uni <= 0 or ok_n >= uni):
        status_label = "✅ 완료"
    elif ok:
        status_label = "⚠️ 완료(일부 누락)"
    else:
        status_label = "❌ 실패"

    lines = [
        "<b>📈 차트 OHLCV 마트 종료</b>",
        f"기준일: <code>{_esc(as_of)}</code>",
        f"상태: {status_label}",
        f"스코프: <code>{_esc(scope)}</code> · 인터벌 <code>{_esc(_intervals_label(intervals))}</code>",
        f"소요: {_esc(_fmt_duration(duration_sec))} · 발송 {_esc(now)}",
        "",
        "<b>【요약】</b>",
        f"성공 종목: {_esc(_fmt_int(success_codes))} / {_esc(_fmt_int(universe))}",
        f"upsert series: {_esc(_fmt_int(saved))}",
    ]
    if error:
        lines.append(f"\n오류: <code>{_esc(error)}</code>")
    lines.append("\n로그: <code>logs/chart_ohlcv_mart_batch.log</code>")
    return "\n".join(lines)


def format_chart_ohlcv_error_html(
    *,
    as_of: Optional[str] = None,
    scope: Optional[str] = None,
    intervals: Optional[Sequence[str]] = None,
    error: str,
    duration_sec: Optional[float] = None,
) -> str:
    now = now_kst().strftime("%Y-%m-%d %H:%M")
    lines = [
        "<b>📈 차트 OHLCV 마트 오류</b>",
        f"기준일: <code>{_esc(as_of or '—')}</code>",
        f"스코프: <code>{_esc(scope or '—')}</code> · 인터벌 <code>{_esc(_intervals_label(intervals))}</code>",
        f"시각: {_esc(now)}",
        f"소요: {_esc(_fmt_duration(duration_sec))}",
        f"\n오류: <code>{_esc(error)}</code>",
        "\n로그: <code>logs/chart_ohlcv_mart_batch.log</code>",
    ]
    return "\n".join(lines)


def _send_html(msg: str, *, label: str) -> bool:
    notifier = TelegramNotifier()
    if not notifier.is_configured():
        logger.warning("텔레그램 미설정 — 차트 OHLCV 배치 알림 스킵 (%s)", label)
        return False
    ok = notifier.send_message(msg, parse_mode="HTML", notification_key=f"chart_ohlcv.{label}")
    if ok:
        logger.info("차트 OHLCV 배치 텔레그램 알림 전송 완료 (%s)", label)
    else:
        logger.error("차트 OHLCV 배치 텔레그램 알림 전송 실패 (%s)", label)
    return ok


def notify_chart_ohlcv_start(**kwargs: Any) -> bool:
    return _send_html(format_chart_ohlcv_start_html(**kwargs), label="start")


def notify_chart_ohlcv_done(**kwargs: Any) -> bool:
    return _send_html(format_chart_ohlcv_done_html(**kwargs), label="done")


def notify_chart_ohlcv_error(**kwargs: Any) -> bool:
    return _send_html(format_chart_ohlcv_error_html(**kwargs), label="error")
