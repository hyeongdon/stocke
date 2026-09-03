"""종가배팅 후보 텔레그램 알림 (14:30 세션 구축 시)."""
from __future__ import annotations

import html
import logging
from typing import Any, Dict, List, Optional

from notifications.telegram_notifier import TelegramNotifier
from utils.datetime_kst import now_kst

logger = logging.getLogger(__name__)

_MAX_CANDIDATE_ROWS = 12
_MAX_THEME_ROWS = 5


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def _fmt_amt(v: Any) -> str:
    """ka10030 trade_amount(백만원) → 억 단위 표시."""
    try:
        m = float(v)
    except (TypeError, ValueError):
        return "—"
    if m <= 0:
        return "—"
    eok = m / 100.0
    if eok >= 10:
        return f"{eok:,.0f}억"
    return f"{eok:,.1f}억"


def _fmt_pct(v: Any, *, signed: bool = False) -> str:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    if signed:
        return f"{n:+.2f}%"
    return f"{n:.2f}%"


def format_jongga_candidates_html(state: Dict[str, Any]) -> str:
    day = state.get("biz_date") or "—"
    now = now_kst().strftime("%Y-%m-%d %H:%M")
    theme = state.get("strongest_theme") or "—"
    cands: List[Dict[str, Any]] = list(state.get("candidates") or [])
    theme_rank: List[Dict[str, Any]] = list(state.get("theme_rank") or [])
    auto = state.get("auto_pick") or (cands[0] if cands else None)

    lines: List[str] = [
        f"<b>🕐 종가배팅 후보 · {_esc(day)}</b>",
        f"발송: {_esc(now)}",
        f"최강테마: <b>{_esc(theme)}</b> · 후보 <b>{_esc(len(cands))}</b>종",
        "14:30~40 대시보드에서 선택 · 미선택 시 ★자동매수",
    ]

    if theme_rank:
        lines.append("")
        lines.append("<b>【테마 대금 TOP】</b>")
        for i, row in enumerate(theme_rank[:_MAX_THEME_ROWS], 1):
            mark = "◀" if str(row.get("theme") or "") == str(theme) else "·"
            lines.append(
                f"{mark} {_esc(i)}. {_esc(row.get('theme'))}"
                f" {_esc(_fmt_amt(row.get('trade_amount')))}"
            )

    lines.append("")
    lines.append(f"<b>【후보 ({_esc(min(len(cands), _MAX_CANDIDATE_ROWS))}/{_esc(len(cands))})】</b>")
    if not cands:
        lines.append("후보 없음 — 거래대금순·테마맵 확인")
    else:
        for i, row in enumerate(cands[:_MAX_CANDIDATE_ROWS], 1):
            star = "★ " if auto and str(auto.get("stock_code")) == str(row.get("stock_code")) else ""
            name = row.get("stock_name") or row.get("stock_code") or "—"
            code = row.get("stock_code") or "—"
            px = row.get("current_price") or row.get("chart_last")
            try:
                px_s = f"{int(px):,}" if px else "—"
            except (TypeError, ValueError):
                px_s = "—"
            lines.append(
                f"{star}{_esc(i)}. {_esc(name)}(<code>{_esc(code)}</code>)"
                f" {_esc(px_s)}원"
                f" · 대금 {_esc(_fmt_amt(row.get('trade_amount')))}"
                f" · 등락 {_esc(_fmt_pct(row.get('change_rate'), signed=True))}"
                f" · 눌림 {_esc(_fmt_pct(row.get('pullback_pct')))}"
                f" · 점수 {_esc(row.get('score') if row.get('score') is not None else '—')}"
            )

    if auto:
        lines.append("")
        lines.append(
            f"자동매수 예정: <b>{_esc(auto.get('stock_name') or auto.get('stock_code'))}</b>"
            f" (<code>{_esc(auto.get('stock_code'))}</code>)"
        )

    lines.append("\n대시보드 → 종가배팅 후보에서 선택")
    return "\n".join(lines)


def notify_jongga_candidates(state: Dict[str, Any]) -> bool:
    """후보 구축 직후 1회 텔레그램 전송."""
    msg = format_jongga_candidates_html(state)
    notifier = TelegramNotifier()
    if not notifier.is_configured():
        logger.warning("텔레그램 미설정 — 종가배팅 후보 알림 스킵")
        return False
    ok = notifier.send_message(msg, parse_mode="HTML")
    if ok:
        logger.info(
            "종가배팅 후보 텔레그램 전송 완료 day=%s theme=%s n=%s",
            state.get("biz_date"),
            state.get("strongest_theme"),
            len(state.get("candidates") or []),
        )
    else:
        logger.error("종가배팅 후보 텔레그램 전송 실패 day=%s", state.get("biz_date"))
    return ok


async def notify_jongga_candidates_async(state: Dict[str, Any]) -> bool:
    import asyncio
    return await asyncio.to_thread(notify_jongga_candidates, state)
