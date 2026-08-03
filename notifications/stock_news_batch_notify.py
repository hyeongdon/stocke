"""종목 뉴스 배치 시작/종료/오류 텔레그램 알림."""
from __future__ import annotations

import html
import logging
from typing import Any, Optional

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


def format_stock_news_start_html(
    *,
    biz_date: str,
    universe: str,
    max_per_day: Optional[int] = None,
    chunk: Optional[int] = None,
    mode: str = "run",
) -> str:
    now = now_kst().strftime("%Y-%m-%d %H:%M")
    mode_label = "이어달리기(자동)" if mode == "loop" else "단일 실행"
    lines = [
        "<b>📰 종목 뉴스 배치 시작</b>",
        f"기준일: <code>{_esc(biz_date)}</code>",
        f"모드: {_esc(mode_label)}",
        f"유니버스: <code>{_esc(universe)}</code>",
    ]
    if max_per_day is not None:
        cap = "무제한" if int(max_per_day) <= 0 else _fmt_int(max_per_day)
        lines.append(f"일일 상한: {_esc(cap)}")
    if chunk is not None and int(chunk) > 0:
        lines.append(f"청크: {_esc(_fmt_int(chunk))}종목/회")
    lines.append(f"시각: {_esc(now)}")
    lines.append("\n로그: <code>logs/stock_news_daily_batch.log</code>")
    return "\n".join(lines)


def format_stock_news_done_html(
    *,
    biz_date: str,
    ok: bool,
    universe: str,
    status: Optional[str] = None,
    done_count: Optional[int] = None,
    ok_count: Optional[int] = None,
    fail_count: Optional[int] = None,
    skip_count: Optional[int] = None,
    remaining: Optional[int] = None,
    day_cap: Optional[int] = None,
    duration_sec: Optional[float] = None,
    error: Optional[str] = None,
    mode: str = "run",
) -> str:
    now = now_kst().strftime("%Y-%m-%d %H:%M")
    if ok and not error and not (fail_count and int(fail_count) > 0):
        status_label = "✅ 완료"
    elif ok:
        status_label = "⚠️ 완료(일부 실패)"
    else:
        status_label = "❌ 실패"

    mode_label = "이어달리기(자동)" if mode == "loop" else "단일 실행"
    lines = [
        "<b>📰 종목 뉴스 배치 종료</b>",
        f"기준일: <code>{_esc(biz_date)}</code>",
        f"상태: {status_label}",
        f"모드: {_esc(mode_label)} · 유니버스 <code>{_esc(universe)}</code>",
        f"소요: {_esc(_fmt_duration(duration_sec))} · 발송 {_esc(now)}",
        "",
        "<b>【요약】</b>",
        f"상태코드: <code>{_esc(status or ('ok' if ok else 'fail'))}</code>",
        f"완료 종목: {_esc(_fmt_int(done_count))}",
        f"이번 실행 ok/skip/fail: {_esc(_fmt_int(ok_count))} / {_esc(_fmt_int(skip_count))} / {_esc(_fmt_int(fail_count))}",
        f"남은 종목: {_esc(_fmt_int(remaining))}",
    ]
    if day_cap is not None:
        cap = "무제한" if int(day_cap) <= 0 else _fmt_int(day_cap)
        lines.append(f"일일 상한: {_esc(cap)}")
    if error:
        lines.append(f"\n오류: <code>{_esc(error)}</code>")
    lines.append("\n로그: <code>logs/stock_news_daily_batch.log</code>")
    return "\n".join(lines)


def format_stock_news_error_html(
    *,
    biz_date: Optional[str] = None,
    error: str,
    duration_sec: Optional[float] = None,
    context: Optional[str] = None,
) -> str:
    now = now_kst().strftime("%Y-%m-%d %H:%M")
    lines = [
        "<b>📰 종목 뉴스 배치 오류</b>",
        f"기준일: <code>{_esc(biz_date or '—')}</code>",
        f"시각: {_esc(now)}",
        f"소요: {_esc(_fmt_duration(duration_sec))}",
    ]
    if context:
        lines.append(f"구간: {_esc(context)}")
    lines.append(f"\n오류: <code>{_esc(error)}</code>")
    lines.append("\n로그: <code>logs/stock_news_daily_batch.log</code>")
    return "\n".join(lines)


def _send_html(msg: str, *, label: str) -> bool:
    notifier = TelegramNotifier()
    if not notifier.is_configured():
        logger.warning("텔레그램 미설정 — 종목 뉴스 배치 알림 스킵 (%s)", label)
        return False
    ok = notifier.send_message(msg, parse_mode="HTML")
    if ok:
        logger.info("종목 뉴스 배치 텔레그램 알림 전송 완료 (%s)", label)
    else:
        logger.error("종목 뉴스 배치 텔레그램 알림 전송 실패 (%s)", label)
    return ok


def notify_stock_news_start(**kwargs: Any) -> bool:
    return _send_html(format_stock_news_start_html(**kwargs), label="start")


def notify_stock_news_done(**kwargs: Any) -> bool:
    return _send_html(format_stock_news_done_html(**kwargs), label="done")


def notify_stock_news_error(**kwargs: Any) -> bool:
    return _send_html(format_stock_news_error_html(**kwargs), label="error")
