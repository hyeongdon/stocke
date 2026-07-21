"""테마/키워드 배치 일일 텔레그램 요약 리포트."""
from __future__ import annotations

import html
import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.models import KeywordDailyStat, ThemeTagEdge, ThemeScoreDaily
from notifications.telegram_notifier import TelegramNotifier
from utils.datetime_kst import now_kst

logger = logging.getLogger(__name__)

# 평소 수집 규모(전체 테마) — 이보다 크게 벗어나면 점검 경고
_EXPECTED_THEMES_MIN = 200
_EXPECTED_EDGES_MIN = 4000
_DURATION_WARN_SEC = 2 * 60 * 60


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def _fmt_int(n: Optional[int]) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}"


def _fmt_delta(n: Optional[int]) -> str:
    if n is None:
        return "—"
    if n > 0:
        return f"+{n:,}"
    return f"{n:,}"


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


def _trend_mark(label: Optional[str]) -> str:
    t = (label or "").lower()
    if t == "new":
        return "NEW"
    if t == "up":
        return "▲"
    if t == "down":
        return "▼"
    return "·"


def _pad(text: str, width: int, *, right: bool = False) -> str:
    raw = str(text)
    # 한글 등 폭 보정은 생략 — 고정폭 대략 정렬용
    if len(raw) >= width:
        return raw[:width]
    pad = " " * (width - len(raw))
    return (pad + raw) if right else (raw + pad)


def _prev_biz_date(session: Session, biz: date) -> Optional[date]:
    prev = (
        session.query(func.max(KeywordDailyStat.biz_date))
        .filter(KeywordDailyStat.biz_date < biz)
        .scalar()
    )
    if prev:
        return prev
    # 키워드가 없을 때 엣지/스코어 기준으로 전일 추정
    for model in (ThemeScoreDaily, ThemeTagEdge):
        col = model.biz_date
        prev = session.query(func.max(col)).filter(col < biz).scalar()
        if prev:
            return prev
    return biz - timedelta(days=1)


def _count_naver_edges(session: Session, biz: date) -> int:
    return int(
        session.query(func.count(ThemeTagEdge.id))
        .filter(
            ThemeTagEdge.source == "naver_theme",
            ThemeTagEdge.biz_date == biz,
        )
        .scalar()
        or 0
    )


def _count_scores(session: Session, biz: date) -> Tuple[int, int]:
    """(scores_written, distinct stocks)."""
    scores = int(
        session.query(func.count(ThemeScoreDaily.id))
        .filter(ThemeScoreDaily.biz_date == biz)
        .scalar()
        or 0
    )
    stocks = int(
        session.query(func.count(func.distinct(ThemeScoreDaily.stock_code)))
        .filter(ThemeScoreDaily.biz_date == biz)
        .scalar()
        or 0
    )
    return scores, stocks


def _keyword_rows(session: Session, biz: date) -> List[KeywordDailyStat]:
    return (
        session.query(KeywordDailyStat)
        .filter(KeywordDailyStat.biz_date == biz)
        .order_by(KeywordDailyStat.mention_count.desc(), KeywordDailyStat.keyword.asc())
        .all()
    )


def collect_theme_batch_report_stats(
    session: Session,
    result: Dict[str, Any],
    *,
    duration_sec: Optional[float] = None,
) -> Dict[str, Any]:
    """배치 결과 + DB로 리포트용 통계 구성."""
    biz_raw = result.get("biz_date")
    if isinstance(biz_raw, date):
        biz = biz_raw
    elif biz_raw:
        biz = date.fromisoformat(str(biz_raw)[:10])
    else:
        from utils.datetime_kst import kst_today
        biz = kst_today()

    ok = bool(result.get("ok"))
    scores = result.get("scores") if isinstance(result.get("scores"), dict) else {}
    prev = _prev_biz_date(session, biz)

    today_edges = int(result.get("edges") or _count_naver_edges(session, biz))
    today_themes = int(result.get("themes") or 0)
    today_kw = int(result.get("keywords") or 0)
    today_scores = int(scores.get("scores_written") or 0)
    today_stocks = int(scores.get("stocks") or 0)
    if not today_scores:
        today_scores, today_stocks = _count_scores(session, biz)
    if not today_edges:
        today_edges = _count_naver_edges(session, biz)

    prev_edges = _count_naver_edges(session, prev) if prev else None
    prev_scores, prev_stocks = _count_scores(session, prev) if prev else (None, None)
    prev_kw = None
    if prev:
        prev_kw = int(
            session.query(func.count(KeywordDailyStat.id))
            .filter(KeywordDailyStat.biz_date == prev)
            .scalar()
            or 0
        )

    kw_rows = _keyword_rows(session, biz)
    if not today_kw:
        today_kw = len(kw_rows)

    top = kw_rows[:10]
    rising = sorted(
        [r for r in kw_rows if (r.trend_label or "") == "up"],
        key=lambda r: int(r.delta_vs_prev or 0),
        reverse=True,
    )[:5]
    newcomers = [r for r in kw_rows if (r.trend_label or "") == "new"][:5]
    falling = sorted(
        [r for r in kw_rows if (r.trend_label or "") == "down"],
        key=lambda r: int(r.delta_vs_prev or 0),
    )[:5]

    warnings: List[str] = []
    if not ok:
        warnings.append(f"배치 실패: {result.get('error') or 'unknown'}")
    if scores and not scores.get("ok", True):
        warnings.append(f"스코어 실패: {scores.get('error') or 'unknown'}")
    if today_themes and today_themes < _EXPECTED_THEMES_MIN:
        warnings.append(f"테마 수 적음 ({today_themes} < {_EXPECTED_THEMES_MIN})")
    if today_edges < _EXPECTED_EDGES_MIN:
        warnings.append(f"편입 엣지 적음 ({today_edges:,} < {_EXPECTED_EDGES_MIN:,})")
    if today_kw <= 0:
        warnings.append("키워드 0건 — KeyBERT/추출 경로 확인")
    if prev_edges and today_edges and prev_edges > 0:
        drop_pct = (prev_edges - today_edges) / prev_edges * 100
        if drop_pct >= 10:
            warnings.append(f"편입 엣지 전일 대비 {drop_pct:.1f}% 감소")
    if duration_sec is not None and duration_sec >= _DURATION_WARN_SEC:
        warnings.append(f"실행 시간 김 ({_fmt_duration(duration_sec)})")

    return {
        "ok": ok,
        "biz_date": biz.isoformat(),
        "prev_biz_date": prev.isoformat() if prev else None,
        "duration_sec": duration_sec,
        "today": {
            "themes": today_themes,
            "edges": today_edges,
            "keywords": today_kw,
            "scores": today_scores,
            "stocks": today_stocks,
            "scores_ok": bool(scores.get("ok", True)) if scores else today_scores > 0,
        },
        "prev": {
            "edges": prev_edges,
            "keywords": prev_kw,
            "scores": prev_scores,
            "stocks": prev_stocks,
        },
        "top_keywords": [
            {
                "keyword": r.keyword,
                "mention_count": int(r.mention_count or 0),
                "stock_count": int(r.stock_count or 0),
                "delta": int(r.delta_vs_prev or 0),
                "trend": r.trend_label or "flat",
            }
            for r in top
        ],
        "rising": [
            {
                "keyword": r.keyword,
                "delta": int(r.delta_vs_prev or 0),
                "mention_count": int(r.mention_count or 0),
            }
            for r in rising
        ],
        "newcomers": [
            {
                "keyword": r.keyword,
                "mention_count": int(r.mention_count or 0),
                "stock_count": int(r.stock_count or 0),
            }
            for r in newcomers
        ],
        "falling": [
            {
                "keyword": r.keyword,
                "delta": int(r.delta_vs_prev or 0),
                "mention_count": int(r.mention_count or 0),
            }
            for r in falling
        ],
        "warnings": warnings,
        "error": result.get("error"),
    }


def _summary_table(stats: Dict[str, Any]) -> str:
    today = stats["today"]
    prev = stats["prev"]

    def row(label: str, cur: Optional[int], old: Optional[int]) -> str:
        delta = None if cur is None or old is None else cur - old
        return (
            f"{_pad(label, 10)}"
            f"{_pad(_fmt_int(cur), 8, right=True)}  "
            f"{_pad(_fmt_int(old), 8, right=True)}  "
            f"{_pad(_fmt_delta(delta), 8, right=True)}"
        )

    lines = [
        f"{_pad('항목', 10)}{_pad('오늘', 8, right=True)}  {_pad('전일', 8, right=True)}  {_pad('증감', 8, right=True)}",
        "-" * 38,
        row("테마", today.get("themes"), None),
        row("편입엣지", today.get("edges"), prev.get("edges")),
        row("키워드", today.get("keywords"), prev.get("keywords")),
        row("스코어행", today.get("scores"), prev.get("scores")),
        row("종목수", today.get("stocks"), prev.get("stocks")),
    ]
    return "\n".join(lines)


def _kw_table(rows: Sequence[Dict[str, Any]], *, with_delta: bool = True) -> str:
    if not rows:
        return "(없음)"
    header = (
        f"{_pad('키워드', 12)}{_pad('언급', 5, right=True)} "
        f"{_pad('종목', 5, right=True)} {_pad('추세', 6, right=True)}"
    )
    lines = [header, "-" * 32]
    for r in rows:
        kw = str(r.get("keyword") or "")
        if len(kw) > 12:
            kw = kw[:11] + "…"
        trend = _trend_mark(r.get("trend"))
        if with_delta and r.get("delta") is not None and (r.get("trend") or "") != "new":
            trend = f"{trend}{_fmt_delta(int(r['delta']))}"
        lines.append(
            f"{_pad(kw, 12)}"
            f"{_pad(_fmt_int(r.get('mention_count')), 5, right=True)} "
            f"{_pad(_fmt_int(r.get('stock_count')), 5, right=True)} "
            f"{_pad(trend, 6, right=True)}"
        )
    return "\n".join(lines)


def _simple_delta_table(rows: Sequence[Dict[str, Any]]) -> str:
    if not rows:
        return "(없음)"
    lines = [
        f"{_pad('키워드', 14)}{_pad('Δ', 6, right=True)} {_pad('언급', 5, right=True)}",
        "-" * 28,
    ]
    for r in rows:
        kw = str(r.get("keyword") or "")
        if len(kw) > 14:
            kw = kw[:13] + "…"
        lines.append(
            f"{_pad(kw, 14)}"
            f"{_pad(_fmt_delta(r.get('delta')), 6, right=True)} "
            f"{_pad(_fmt_int(r.get('mention_count')), 5, right=True)}"
        )
    return "\n".join(lines)


def format_theme_batch_report_html(stats: Dict[str, Any]) -> str:
    """텔레그램 HTML 메시지."""
    ok = bool(stats.get("ok"))
    status = "✅ 성공" if ok and not stats.get("warnings") else ("⚠️ 성공(점검)" if ok else "❌ 실패")
    now = now_kst().strftime("%Y-%m-%d %H:%M")
    prev_label = stats.get("prev_biz_date") or "—"

    parts = [
        f"<b>📊 테마/키워드 배치 일일 리포트</b>",
        f"기준일: <code>{_esc(stats.get('biz_date'))}</code> (전일 {_esc(prev_label)})",
        f"상태: {status}",
        f"소요: {_esc(_fmt_duration(stats.get('duration_sec')))} · 발송 {_esc(now)}",
        "",
        "<b>【수집 요약】</b>",
        f"<pre>{_esc(_summary_table(stats))}</pre>",
        "",
        "<b>【키워드 TOP10】</b>",
        f"<pre>{_esc(_kw_table(stats.get('top_keywords') or []))}</pre>",
    ]

    if stats.get("newcomers"):
        parts.extend([
            "",
            "<b>【신규 키워드】</b>",
            f"<pre>{_esc(_kw_table(stats['newcomers'], with_delta=False))}</pre>",
        ])
    if stats.get("rising"):
        parts.extend([
            "",
            "<b>【상승 TOP】</b>",
            f"<pre>{_esc(_simple_delta_table(stats['rising']))}</pre>",
        ])
    if stats.get("falling"):
        parts.extend([
            "",
            "<b>【하락 TOP】</b>",
            f"<pre>{_esc(_simple_delta_table(stats['falling']))}</pre>",
        ])

    warnings = list(stats.get("warnings") or [])
    if warnings:
        parts.append("")
        parts.append("<b>【점검】</b>")
        for w in warnings:
            parts.append(f"• {_esc(w)}")
    else:
        parts.append("")
        parts.append("<b>【점검】</b> 특이사항 없음")

    if stats.get("error"):
        parts.append(f"\n오류: <code>{_esc(stats['error'])}</code>")

    parts.append("\n로그: <code>logs/theme_mart_batch.log</code>")
    return "\n".join(parts)


def format_theme_batch_report_text(stats: Dict[str, Any]) -> str:
    """테스트/로그용 plain text."""
    html_msg = format_theme_batch_report_html(stats)
    # 태그 粗제거
    import re
    return re.sub(r"<[^>]+>", "", html_msg)


def send_theme_batch_report(
    session: Session,
    result: Dict[str, Any],
    *,
    duration_sec: Optional[float] = None,
) -> bool:
    """리포트 생성 후 텔레그램 전송. 미설정 시 False."""
    notifier = TelegramNotifier()
    if not notifier.is_configured():
        logger.warning("텔레그램 미설정 — 테마 배치 리포트 스킵")
        return False

    stats = collect_theme_batch_report_stats(session, result, duration_sec=duration_sec)
    msg = format_theme_batch_report_html(stats)
    ok = notifier.send_message(msg, parse_mode="HTML")
    if ok:
        logger.info(
            "테마 배치 텔레그램 리포트 전송 완료 biz_date=%s ok=%s",
            stats.get("biz_date"),
            stats.get("ok"),
        )
    else:
        logger.error("테마 배치 텔레그램 리포트 전송 실패")
    return ok


def build_notify_only_result(session: Session, biz: Optional[date] = None) -> Dict[str, Any]:
    """재전송용: DB 스냅샷으로 성공 결과 형태 구성."""
    from utils.datetime_kst import kst_today

    biz = biz or kst_today()
    edges = _count_naver_edges(session, biz)
    kw_n = int(
        session.query(func.count(KeywordDailyStat.id))
        .filter(KeywordDailyStat.biz_date == biz)
        .scalar()
        or 0
    )
    scores, stocks = _count_scores(session, biz)
    themes = int(
        session.query(func.count(func.distinct(ThemeTagEdge.tag_id)))
        .filter(
            ThemeTagEdge.source == "naver_theme",
            ThemeTagEdge.biz_date == biz,
        )
        .scalar()
        or 0
    )
    ok = edges > 0 and kw_n > 0
    return {
        "ok": ok,
        "themes": themes,
        "edges": edges,
        "keywords": kw_n,
        "biz_date": biz.isoformat(),
        "scores": {
            "ok": scores > 0,
            "stocks": stocks,
            "scores_written": scores,
            "themes": themes,
            "biz_date": biz.isoformat(),
        },
        "error": None if ok else "당일 스냅샷이 비어 있습니다.",
    }
