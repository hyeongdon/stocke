"""장마감 매수 실패 신호 텔레그램 알림 — 전략별 집계 + 상세 표."""
from __future__ import annotations

import html
import logging
from typing import Any, Dict, List, Sequence, Tuple

from notifications.telegram_notifier import TelegramNotifier
from notifications.trade_alert import strategy_label_ko
from utils.datetime_kst import now_kst

logger = logging.getLogger(__name__)

_MAX_DETAIL_ROWS = 40
_MAX_REASON_ROWS = 12


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def _short(text: str, max_len: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def format_failed_buy_signals_html(report: Dict[str, Any]) -> str:
    """집계 dict → HTML 텔레그램 본문 (표 형태)."""
    day = report.get("day") or "—"
    now = now_kst().strftime("%Y-%m-%d %H:%M")
    count = int(report.get("count") or 0)
    items: Sequence[dict] = report.get("items") or []
    strategy_counts: Sequence[Tuple[str, int]] = report.get("strategy_counts") or []
    reason_counts: Sequence[Tuple[str, int]] = report.get("reason_counts") or []

    lines: List[str] = [
        f"<b>🚫 매수 실패 · {_esc(day)}</b>",
        f"발송: {_esc(now)}",
        f"실패 신호: <b>{_esc(count)}</b>건",
        "",
    ]

    if not count:
        lines.append("오늘 FAILED 매수 신호 없음")
        lines.append("\n출처: pending_buy_signals (FAILED)")
        return "\n".join(lines)

    # 전략별 표 (pipe — 한글 폭 이슈 회피)
    strat_rows = ["전략 | 건수"]
    for key, n in strategy_counts:
        strat_rows.append(f"{strategy_label_ko(key)} | {n}")
    lines.append("<b>【전략별】</b>")
    lines.append("<pre>" + _esc("\n".join(strat_rows)) + "</pre>")

    # 사유 TOP
    if reason_counts:
        lines.append("<b>【사유 TOP】</b>")
        for reason, n in list(reason_counts)[:_MAX_REASON_ROWS]:
            lines.append(f"· {_esc(n)} · {_esc(reason)}")
        extra_r = len(reason_counts) - _MAX_REASON_ROWS
        if extra_r > 0:
            lines.append(f"· … 외 {_esc(extra_r)}종 사유")

    # 상세 표
    lines.append("")
    shown = min(count, _MAX_DETAIL_ROWS)
    lines.append(f"<b>【상세 {_esc(shown)}/{_esc(count)}】</b>")
    detail_lines = ["시각 | 전략 | 종목 | 사유"]
    for row in list(items)[:_MAX_DETAIL_ROWS]:
        tag = strategy_label_ko(row.get("strategy"))
        name = str(row.get("stock_name") or "")
        code = str(row.get("stock_code") or "")
        reason = _short(str(row.get("reason") or ""), 48)
        detail_lines.append(
            f"{row.get('time') or '—'} | {tag} | {name}({code}) | {reason}"
        )
    lines.append("<pre>" + _esc("\n".join(detail_lines)) + "</pre>")
    extra = count - _MAX_DETAIL_ROWS
    if extra > 0:
        lines.append(f"· … 외 {_esc(extra)}건 (DB 참고)")

    lines.append("\n출처: pending_buy_signals (FAILED · 신호 생성 후 실패)")
    return "\n".join(lines)


def notify_failed_buy_signals(report: Dict[str, Any]) -> bool:
    msg = format_failed_buy_signals_html(report)
    notifier = TelegramNotifier()
    if not notifier.is_configured():
        logger.warning("텔레그램 미설정 — 매수 실패 알림 스킵")
        return False
    ok = notifier.send_message(msg, parse_mode="HTML")
    if ok:
        logger.info(
            "매수 실패 텔레그램 전송 완료 day=%s count=%s",
            report.get("day"),
            report.get("count"),
        )
    else:
        logger.error(
            "매수 실패 텔레그램 전송 실패 day=%s",
            report.get("day"),
        )
    return ok
