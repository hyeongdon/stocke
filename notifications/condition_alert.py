"""
조건식 조회 → 텔레그램 알림 공용 로직

독립 실행 스크립트(scripts/condition_telegram_alert.py)와
웹 대시보드(core/main.py의 /telegram/send-now)에서 동일하게 재사용한다.
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from core.config import Config

logger = logging.getLogger(__name__)


def _market_hours_gate(skip: bool = False) -> Optional[str]:
    """장외·휴장이면 사유 문자열, 전송 가능이면 None."""
    if skip or not Config.TELEGRAM_ALERT_MARKET_HOURS_ONLY:
        return None
    from utils.market_hours import telegram_market_alert_block_reason
    return telegram_market_alert_block_reason()


def filter_conditions(conditions: List[Dict], names: Optional[List[str]]) -> List[Dict]:
    """조건식 이름 부분일치 필터. names가 비어 있으면 전체 반환."""
    if not names:
        return conditions
    return [
        c for c in conditions
        if any(keyword in c.get("condition_name", "") for keyword in names)
    ]


def format_price(stock: Dict) -> str:
    """현재가: 천단위 콤마. 예: 71,000원"""
    try:
        return f"{int(float(stock.get('current_price', ''))):,}원"
    except (ValueError, TypeError):
        return "N/A"


def format_diff(stock: Dict) -> str:
    """전일대비: 부호 + 콤마. 예: ▲1,500 / ▼500"""
    try:
        num = int(float(stock.get("price_diff", "")))
    except (ValueError, TypeError):
        return "N/A"
    if num > 0:
        return f"▲{num:,}"
    if num < 0:
        return f"▼{abs(num):,}"
    return "0"


def format_rate(stock: Dict) -> str:
    try:
        num = float(stock.get("change_rate", ""))
        sign = "+" if num > 0 else ""
        return f"{sign}{num:.2f}%"
    except (ValueError, TypeError):
        return "N/A"


def format_volume(stock: Dict) -> str:
    try:
        num = float(stock.get("volume", ""))
        if num >= 1_000_000:
            return f"{num / 1_000_000:.1f}M"
        return f"{int(num):,}"
    except (ValueError, TypeError):
        return "N/A"


def build_message(condition_results: List[Tuple[Dict, List[Dict]]], max_stocks: int) -> str:
    """조건식별 종목 결과를 텔레그램 메시지 텍스트로 조립."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    blocks = []

    for idx, (cond, stocks) in enumerate(condition_results, 1):
        name = cond.get("condition_name", "이름없음")
        cond_id = cond.get("condition_id", cond.get("api_id", "-"))

        if not stocks:
            stock_text = "  - (해당 종목 없음)"
        else:
            lines = []
            for s in stocks[:max_stocks]:
                stock_name = s.get("stock_name", "이름없음")
                code = s.get("stock_code", "")
                lines.append(
                    f"  - {stock_name}({code}) | {format_price(s)} | "
                    f"{format_diff(s)} ({format_rate(s)}) | {format_volume(s)}"
                )
            if len(stocks) > max_stocks:
                lines.append(f"  - ... 외 {len(stocks) - max_stocks}개")
            stock_text = "\n".join(lines)

        blocks.append(f"{idx}. [{cond_id}] {name} ({len(stocks)}종목)\n{stock_text}")

    header = f"📊 조건식 조회 결과 ({len(condition_results)}개 조건식)\n기준시각: {now}"
    body = "\n\n".join(blocks) if blocks else "조건식이 없습니다."
    return f"{header}\n\n{body}"


async def collect_condition_results(
    api, names: Optional[List[str]] = None
) -> List[Tuple[Dict, List[Dict]]]:
    """조건식 목록 조회 후 각 조건식의 편입 종목을 검색해 반환."""
    conditions = await api.get_condition_list_websocket()
    if not conditions:
        return []

    conditions = filter_conditions(conditions, names)

    results: List[Tuple[Dict, List[Dict]]] = []
    for cond in conditions:
        cond_id = cond.get("condition_id", cond.get("api_id"))
        cond_name = cond.get("condition_name", "")
        try:
            stocks = await api.search_condition_stocks(str(cond_id), cond_name)
        except Exception as e:
            logger.error(f"조건식 검색 실패 [{cond_id}] {cond_name}: {e}")
            stocks = []
        results.append((cond, stocks))
    return results


async def send_condition_alert(
    api,
    notifier,
    names: Optional[List[str]] = None,
    max_stocks: Optional[int] = None,
    *,
    skip_market_hours_check: bool = False,
) -> Dict:
    """조건식 조회 → 텔레그램 전송. 결과 요약 dict 반환."""
    block = _market_hours_gate(skip_market_hours_check)
    if block:
        logger.info(f"조건식 텔레그램 알림 스킵: {block}")
        return {
            "sent": False,
            "skipped": True,
            "skip_reason": block,
            "condition_count": 0,
            "stock_count": 0,
            "message": block,
        }

    if max_stocks is None:
        max_stocks = Config.TELEGRAM_ALERT_MAX_STOCKS

    results = await collect_condition_results(api, names)
    if not results:
        message = "📊 조건식 조회 결과\n\n조건식 목록이 비어 있습니다."
        ok = notifier.send_message(message)
        return {"sent": ok, "condition_count": 0, "stock_count": 0, "message": message}

    message = build_message(results, max_stocks)
    ok = notifier.send_message(message)
    stock_count = sum(len(s) for _, s in results)
    return {
        "sent": ok,
        "condition_count": len(results),
        "stock_count": stock_count,
        "message": message,
    }
