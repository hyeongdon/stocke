"""장마감 매매 일지 텔레그램 알림 — 금일 매수 평가 + 보유 평가."""
from __future__ import annotations

import html
import logging
from typing import Any, Dict, List, Optional, Sequence

from notifications.telegram_notifier import TelegramNotifier
from notifications.trade_alert import SELL_REASON_KO, strategy_label_ko
from utils.datetime_kst import now_kst

logger = logging.getLogger(__name__)

_MAX_LIST_ROWS = 20


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def _fmt_int(n: Optional[int]) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}"


def _fmt_price(value: Optional[int]) -> str:
    if value is None:
        return "—"
    return f"{int(value):,}원"


def _fmt_pnl(amount: Optional[int], rate: Optional[float] = None) -> str:
    if amount is None:
        return "—"
    sign = "+" if amount > 0 else ""
    rate_str = ""
    if rate is not None:
        rate_str = f" ({sign}{rate:.2f}%)"
    return f"{sign}{amount:,}원{rate_str}"


def _reason_ko(code: Optional[str]) -> str:
    if not code:
        return ""
    return SELL_REASON_KO.get(code, code)


def _strategy_totals(today_rows: Sequence[dict]) -> List[tuple]:
    """전략별 (라벨, 건수, eval_pnl 합) — 건수 내림차순."""
    buckets: Dict[str, Dict[str, Any]] = {}
    for row in today_rows:
        tag = strategy_label_ko(row.get("strategy"))
        b = buckets.setdefault(tag, {"count": 0, "pnl": 0})
        b["count"] += 1
        b["pnl"] += int(row.get("eval_pnl") or 0)
    items = [(k, v["count"], int(v["pnl"])) for k, v in buckets.items()]
    items.sort(key=lambda x: (-x[1], x[0]))
    return items


def format_daily_trade_journal_html(journal: Dict[str, Any]) -> str:
    """집계 dict → HTML 텔레그램 본문."""
    day = journal.get("day") or "—"
    now = now_kst().strftime("%Y-%m-%d %H:%M")
    today_buy_eval = int(journal.get("today_buy_eval") or 0)
    today_buy_realized = int(journal.get("today_buy_realized") or 0)
    today_buy_unrealized = int(journal.get("today_buy_unrealized") or 0)
    holding_unrealized = int(journal.get("holding_unrealized") or 0)
    day_eval_total = int(journal.get("day_eval_total") or 0)
    realized_all = int(journal.get("realized_pnl") or 0)
    buy_count = int(journal.get("buy_count") or 0)
    sell_count = int(journal.get("sell_count") or 0)
    win = int(journal.get("win_count") or 0)
    loss = int(journal.get("loss_count") or 0)
    flat = int(journal.get("flat_count") or 0)
    buy_amount = int(journal.get("buy_amount_sum") or 0)
    sell_amount = int(journal.get("sell_amount_sum") or 0)
    today_rows: Sequence[dict] = journal.get("today_buy_positions") or []
    sells: Sequence[dict] = journal.get("sells") or []
    holdings: Sequence[dict] = journal.get("holdings") or []
    reason_counts: Dict[str, int] = dict(journal.get("reason_counts") or {})
    has_activity = bool(journal.get("has_activity"))

    lines: List[str] = [
        f"<b>📒 매매 일지 · {_esc(day)}</b>",
        f"발송: {_esc(now)}",
        "",
        "<b>【합산】</b>",
        f"매수 {_esc(_fmt_int(buy_count))}건 / 매도 {_esc(_fmt_int(sell_count))}건"
        f" / 보유 {_esc(_fmt_int(len(holdings)))}종목",
        f"매수금액 {_esc(_fmt_price(buy_amount))} · 매도금액 {_esc(_fmt_price(sell_amount))}",
        f"당일 실현손익: <b>{_esc(_fmt_pnl(realized_all))}</b>"
        f" (승 {_esc(_fmt_int(win))} / 패 {_esc(_fmt_int(loss))}"
        f" / 무 {_esc(_fmt_int(flat))})",
    ]

    if reason_counts:
        reason_bits = []
        for code, cnt in sorted(reason_counts.items(), key=lambda x: (-x[1], x[0])):
            reason_bits.append(f"{_reason_ko(code)} {_esc(_fmt_int(cnt))}")
        lines.append("매도사유: " + " · ".join(reason_bits))

    strat_items = _strategy_totals(today_rows)
    if strat_items:
        lines.append("전략별(금일매수):")
        for tag, cnt, pnl in strat_items:
            lines.append(
                f"· {_esc(tag)} {_esc(_fmt_int(cnt))}건 "
                f"<b>{_esc(_fmt_pnl(pnl))}</b>"
            )

    lines.extend(
        [
            "",
            "<b>【평가】</b>",
            f"일일 평가합: <b>{_esc(_fmt_pnl(day_eval_total))}</b>",
            f"· 금일 매수 손익: {_esc(_fmt_pnl(today_buy_eval))}"
            f" (실현 {_esc(_fmt_pnl(today_buy_realized))}"
            f" + 미실현 {_esc(_fmt_pnl(today_buy_unrealized))})",
            f"· 보유 평가손익: {_esc(_fmt_pnl(holding_unrealized))}",
            f"<i>일일 평가합 = 금일매수 실현 + 보유 미실현</i>",
        ]
    )

    if not has_activity:
        lines.append("")
        lines.append("오늘 매수·매도·보유 없음")

    if today_rows:
        lines.append("")
        lines.append(
            f"<b>【금일 매수 {_esc(_fmt_int(len(today_rows)))}】</b>"
        )
        for row in list(today_rows)[:_MAX_LIST_ROWS]:
            tag = strategy_label_ko(row.get("strategy"))
            status = row.get("status") or "—"
            reason = _reason_ko(row.get("sell_reason"))
            reason_part = f"/{_esc(reason)}" if reason else ""
            lines.append(
                f"· {_esc(row.get('stock_name'))}(<code>{_esc(row.get('stock_code'))}</code>) "
                f"{_esc(_fmt_int(row.get('quantity')))}주 @ {_esc(_fmt_price(row.get('buy_price')))} "
                f"[{_esc(tag)}] {_esc(status)}{reason_part} "
                f"<b>{_esc(_fmt_pnl(row.get('eval_pnl'), row.get('eval_pnl_rate')))}</b> "
                f"{_esc(row.get('buy_time') or '—')}"
            )
        extra = len(today_rows) - _MAX_LIST_ROWS
        if extra > 0:
            lines.append(f"· … 외 {_esc(_fmt_int(extra))}건")

    if sells:
        lines.append("")
        lines.append(f"<b>【금일 매도 {_esc(_fmt_int(len(sells)))}】</b>")
        for row in list(sells)[:_MAX_LIST_ROWS]:
            reason = _reason_ko(row.get("sell_reason"))
            lines.append(
                f"· {_esc(row.get('stock_name'))}(<code>{_esc(row.get('stock_code'))}</code>) "
                f"{_esc(_fmt_int(row.get('quantity')))}주 @ {_esc(_fmt_price(row.get('price')))} "
                f"{_esc(reason)} "
                f"<b>{_esc(_fmt_pnl(row.get('profit_loss'), row.get('profit_loss_rate')))}</b> "
                f"{_esc(row.get('time') or '—')}"
            )
        extra = len(sells) - _MAX_LIST_ROWS
        if extra > 0:
            lines.append(f"· … 외 {_esc(_fmt_int(extra))}건")

    if holdings:
        lines.append("")
        lines.append(
            f"<b>【보유 {_esc(_fmt_int(len(holdings)))} · "
            f"{_esc(_fmt_pnl(holding_unrealized))}】</b>"
        )
        for row in list(holdings)[:_MAX_LIST_ROWS]:
            pnl = row.get("current_profit_loss")
            rate = row.get("current_profit_loss_rate")
            today_mark = " ·금일" if row.get("bought_today") else ""
            lines.append(
                f"· {_esc(row.get('stock_name'))}(<code>{_esc(row.get('stock_code'))}</code>) "
                f"{_esc(_fmt_int(row.get('quantity')))}주"
                f" · 평단 {_esc(_fmt_price(row.get('buy_price')))} "
                f"<b>{_esc(_fmt_pnl(pnl, rate))}</b>{_esc(today_mark)}"
            )
        extra = len(holdings) - _MAX_LIST_ROWS
        if extra > 0:
            lines.append(f"· … 외 {_esc(_fmt_int(extra))}건")

    lines.append("\n출처: 앱 DB (금일매수 실현+보유 미실현)")
    return "\n".join(lines)


def notify_daily_trade_journal(journal: Dict[str, Any]) -> bool:
    msg = format_daily_trade_journal_html(journal)
    notifier = TelegramNotifier()
    if not notifier.is_configured():
        logger.warning("텔레그램 미설정 — 매매 일지 알림 스킵")
        return False
    ok = notifier.send_message(msg, parse_mode="HTML")
    if ok:
        logger.info("매매 일지 텔레그램 전송 완료 day=%s", journal.get("day"))
    else:
        logger.error("매매 일지 텔레그램 전송 실패 day=%s", journal.get("day"))
    return ok
