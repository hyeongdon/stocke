"""장마감 역매공파 단계·박스권 차이 텔레그램 알림."""
from __future__ import annotations

import html
import logging
from typing import Any, Dict, List, Optional, Sequence

from notifications.telegram_notifier import TelegramNotifier
from utils.datetime_kst import now_kst
from utils.ymgp_eod_report import stage_label

logger = logging.getLogger(__name__)

_MAX_FILTERED_ROWS = 25
_MAX_READY_ROWS = 8


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def _short(text: str, max_len: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _fmt_signed(v: Optional[float], suffix: str = "%") -> str:
    if v is None:
        return "—"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.1f}{suffix}"


def _fmt_fail_keys(keys: Sequence[str]) -> str:
    if not keys:
        return "—"
    # 박스 관련 우선 표시
    priority = ["box", "double_bottom", "ma_support", "accum_bar", "gonguri", "drop_sideways", "vol_revival"]
    ordered = [k for k in priority if k in keys]
    for k in keys:
        if k not in ordered:
            ordered.append(k)
    return ",".join(ordered[:4])


def format_ymgp_eod_html(report: Dict[str, Any]) -> str:
    day = report.get("day") or "—"
    now = now_kst().strftime("%Y-%m-%d %H:%M")
    total = int(report.get("total") or 0)
    limit = report.get("box_limit_pct")
    filtered = list(report.get("filtered") or [])
    ready = list(report.get("ready") or [])
    armed = list(report.get("armed") or [])
    stage_counts = list(report.get("stage_counts") or [])
    errors = list(report.get("errors") or [])
    names = list(report.get("condition_names") or [])

    lines: List[str] = [
        f"<b>📐 역매공파 단계 · {_esc(day)}</b>",
        f"발송: {_esc(now)}",
        f"후보: <b>{_esc(total)}</b>건"
        + (f" · 박스상한 {_esc(limit)}%" if limit is not None else ""),
    ]
    if names:
        lines.append("조건식: " + _esc(", ".join(names)))
    if errors:
        lines.append("⚠ " + _esc("; ".join(errors)))

    lines.append("")
    lines.append("<b>【단계 퍼널】</b>")
    if stage_counts:
        funnel = " → ".join(
            f"{stage_label(k)} {_esc(n)}" for k, n in stage_counts if int(n) > 0
        )
        lines.append(funnel or "—")
        pre_rows = ["단계 | 건수"]
        for k, n in stage_counts:
            if int(n) <= 0:
                continue
            pre_rows.append(f"{stage_label(k)} | {n}")
        lines.append("<pre>" + _esc("\n".join(pre_rows)) + "</pre>")
    else:
        lines.append("후보 없음")

    # FILTERED 박스 요약
    f_cnt = int(report.get("filtered_count") or len(filtered))
    lines.append("")
    lines.append(f"<b>【FILTERED 박스 {_esc(f_cnt)}】</b>")
    if f_cnt:
        avg_w = report.get("filtered_width_over_avg")
        avg_h = report.get("filtered_to_high_avg")
        lines.append(
            f"평균 폭초과 {_esc(_fmt_signed(avg_w, '%p'))}"
            f" · 평균 고점差 {_esc(_fmt_signed(avg_h))}"
        )
        lines.append("<i>폭초과(+) = 박스 너무 넓음 · 고점差(−) = 돌파까지 남은 %</i>")
        shown = min(f_cnt, _MAX_FILTERED_ROWS)
        lines.append(f"<b>상세 {_esc(shown)}/{_esc(f_cnt)}</b>")
        detail = ["종목 | 폭% | 초과 | 고점差 | 미충족"]
        for row in filtered[:_MAX_FILTERED_ROWS]:
            name = _short(str(row.get("stock_name") or ""), 8)
            code = str(row.get("stock_code") or "")
            w = row.get("box_width_pct")
            over = row.get("width_over_pct")
            to_h = row.get("to_high_pct")
            fails = _fmt_fail_keys(row.get("fail_keys") or [])
            w_s = f"{w:.1f}" if w is not None else "—"
            detail.append(
                f"{name}({code}) | {w_s} | {_fmt_signed(over, '')} | {_fmt_signed(to_h)} | {fails}"
            )
        lines.append("<pre>" + _esc("\n".join(detail)) + "</pre>")
        extra = f_cnt - _MAX_FILTERED_ROWS
        if extra > 0:
            lines.append(f"· … 외 {_esc(extra)}건")
    else:
        lines.append("FILTERED 없음")

    # READY / ARMED 요약
    if ready:
        lines.append("")
        lines.append(f"<b>【READY {_esc(len(ready))}】</b>")
        for row in ready[:_MAX_READY_ROWS]:
            to_h = _fmt_signed(row.get("to_high_pct"))
            fails = _fmt_fail_keys(row.get("fail_keys") or [])
            lines.append(
                f"· {_esc(row.get('stock_name'))}(<code>{_esc(row.get('stock_code'))}</code>)"
                f" 고점差 {_esc(to_h)} · 미충족 {_esc(fails)}"
            )
        if len(ready) > _MAX_READY_ROWS:
            lines.append(f"· … 외 {_esc(len(ready) - _MAX_READY_ROWS)}건")

    if armed:
        lines.append("")
        lines.append(f"<b>【ARMED {_esc(len(armed))}】</b>")
        for row in armed[:_MAX_READY_ROWS]:
            to_h = _fmt_signed(row.get("to_high_pct"))
            lines.append(
                f"· {_esc(row.get('stock_name'))}(<code>{_esc(row.get('stock_code'))}</code>)"
                f" 고점差 {_esc(to_h)}"
            )

    if not total and not errors:
        lines.append("")
        lines.append("오늘 조건식 편입 종목 없음")

    lines.append("\n출처: 역매공파 조건식 + 일봉 재판정 (FILTERED→READY 박스/지지)")
    return "\n".join(lines)


def notify_ymgp_eod(report: Dict[str, Any]) -> bool:
    msg = format_ymgp_eod_html(report)
    notifier = TelegramNotifier()
    if not notifier.is_configured():
        logger.warning("텔레그램 미설정 — 역매공파 EOD 알림 스킵")
        return False
    ok = notifier.send_message(msg, parse_mode="HTML")
    if ok:
        logger.info(
            "역매공파 EOD 텔레그램 전송 완료 day=%s total=%s filtered=%s",
            report.get("day"),
            report.get("total"),
            report.get("filtered_count"),
        )
    else:
        logger.error("역매공파 EOD 텔레그램 전송 실패 day=%s", report.get("day"))
    return ok
