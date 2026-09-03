"""키움↔DB 손익 동기화 결과 텔레그램."""
from __future__ import annotations

import html
import logging
from typing import Any, Dict, List

from notifications.telegram_notifier import TelegramNotifier
from utils.datetime_kst import now_kst
from utils.kiwoom_db_pnl_sync import format_diff_table, summarize_report

logger = logging.getLogger(__name__)


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def format_kiwoom_db_pnl_sync_html(report: Dict[str, Any]) -> str:
    now = now_kst().strftime("%Y-%m-%d %H:%M")
    applied = "반영" if report.get("applied") else "조회만"
    k = int(report.get("kiwoom_net_sum") or 0)
    d = int(report.get("db_net_sum") or 0)
    delta = int(report.get("delta_sum") or 0)
    fee = int(report.get("kiwoom_fee_sum") or 0)
    tax = int(report.get("kiwoom_tax_sum") or 0)
    ka74 = report.get("ka10074_total")
    lines: List[str] = [
        f"<b>키움↔DB 손익 싱크 · {_esc(applied)}</b>",
        f"발송: {_esc(now)}",
        f"기간: {_esc(report.get('start'))} ~ {_esc(report.get('end'))}"
        f" ({_esc(report.get('source') or '-')})",
        f"키움 실현합: <b>{k:+,}</b>",
        f"DB 실현합: <b>{d:+,}</b>",
        f"차이: <b>{delta:+,}</b>",
        f"거래비용: 수수료 <b>{fee:,}</b> · 거래세 <b>{tax:,}</b>",
    ]
    if ka74 is not None:
        lines.append(f"ka10074 기간합: {_esc(f'{int(ka74):+,}')}")
    adjustment = int(report.get("stock_account_adjustment") or 0)
    if adjustment:
        lines.append(f"종목합 원단위 보정: {_esc(f'{adjustment:+,}')}")
    if report.get("reconcile"):
        lines.append(f"체결 reconcile: {_esc(report.get('reconcile'))}")

    cash = report.get("account_balance_snapshot")
    if cash:
        d0 = int(cash.get("deposit_d0") or 0)
        d2 = int(cash.get("deposit_d2") or 0)
        gap = int(cash.get("settlement_gap") or 0)
        lines.extend([
            "",
            "<b>【예수금】</b>",
            f"D+0 현재: <b>{d0:,}</b>",
            f"D+2 추정: <b>{d2:,}</b>",
            f"정산 차이(D+2−D+0): <b>{gap:+,}</b>",
        ])

    applied_res = report.get("apply_result") or {}
    if applied_res:
        skipped = applied_res.get("skipped") or []
        lines.append(
            f"매도 반영 {applied_res.get('updated_sells') or 0}건"
            f" · 이력보정 {applied_res.get('backfilled') or 0}건"
            f" · 보유 {report.get('holdings_updated') or 0}건"
            f" · 건너뜀 {len(skipped)}건"
        )

    diffs = report.get("realized_diffs") or []
    lines.append("")
    lines.append(f"<b>【실현 차이 {len(diffs)}】</b>")
    lines.append("<pre>" + _esc(format_diff_table(diffs)) + "</pre>")

    holdings = report.get("holding_diffs") or []
    if holdings:
        lines.append(f"<b>【보유 차이 {len(holdings)}】</b>")
        hlines = ["종목 | 키움 | DB | 수량(키움/DB)"]
        for row in holdings[:20]:
            name = f"{row.get('stock_name') or ''}({row.get('stock_code')})"
            kp = row.get("kiwoom_pl")
            kp_s = f"{int(kp):+,}" if kp is not None else "-"
            hlines.append(
                f"{name} | {kp_s} | {int(row.get('db_pl') or 0):+,} | "
                f"{row.get('kiwoom_qty')}/{row.get('db_qty')}"
            )
        extra = len(holdings) - 20
        if extra > 0:
            hlines.append(f"… 외 {extra}건")
        lines.append("<pre>" + _esc("\n".join(hlines)) + "</pre>")

    skipped = (applied_res.get("skipped") or []) if applied_res else []
    if skipped:
        lines.append(f"<b>【건너뜀 {len(skipped)}】</b>")
        for row in skipped[:12]:
            lines.append(
                f"· {_esc(row.get('date'))} {_esc(row.get('stock_name'))}"
                f"({_esc(row.get('stock_code'))}) {_esc(row.get('reason'))}"
            )

    lines.append("\n출처: ka10073/74 · kt00004 · sell_orders/positions")
    return "\n".join(lines)


def notify_kiwoom_db_pnl_sync(report: Dict[str, Any]) -> bool:
    msg = format_kiwoom_db_pnl_sync_html(report)
    notifier = TelegramNotifier()
    if not notifier.is_configured():
        logger.warning("텔레그램 미설정 — 손익 싱크 알림 스킵")
        return False
    ok = notifier.send_message(msg, parse_mode="HTML")
    if ok:
        logger.info("손익 싱크 텔레그램 전송 완료 %s", summarize_report(report).replace("\n", " / "))
    else:
        logger.error("손익 싱크 텔레그램 전송 실패")
    return ok
