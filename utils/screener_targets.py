"""스크리너·자동매매 스캐너 후보 수집 — 거래대금순 + 선택 조건식."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from api.kiwoom_api import KiwoomAPI, _parse_kiwoom_float, _parse_kiwoom_int
from core.models import AutoTradeCondition, get_db

logger = logging.getLogger(__name__)


def parse_condition_names(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    names = []
    for part in str(raw).replace("\n", ",").split(","):
        name = part.strip()
        if name and name not in names:
            names.append(name)
    return names


def sync_stored_condition_ids(ids: Dict[str, str]) -> None:
    """현재 계좌 목록에서 찾은 ID로 DB 저장값을 맞춘다."""
    try:
        for db in get_db():
            changed = False
            for name, api_id in ids.items():
                row = db.query(AutoTradeCondition).filter(AutoTradeCondition.condition_name == name).first()
                if row and row.api_condition_id != api_id:
                    logger.info(f"조건식 ID 보정: {name} {row.api_condition_id} → {api_id}")
                    row.api_condition_id = api_id
                    row.updated_at = datetime.utcnow()
                    changed = True
            if changed:
                db.commit()
            break
    except Exception as e:
        logger.warning(f"조건식 ID DB 보정 실패: {e}")


async def resolve_condition_ids(kiwoom_api, names: List[str]) -> Tuple[Dict[str, str], str]:
    """조건식 이름 → 현재 계좌의 키움 API ID.

    키움 조건식 ID는 HTS 목록 순번이라 계좌(실전·모의)·목록 변경마다 달라진다.
    항상 현재 목록에서 이름으로 찾고(DB도 보정), 목록 조회가 실패할 때만 DB 저장값을 쓴다.
    반환: (ids, "live" | "db")
    """
    try:
        rows = await kiwoom_api.get_condition_list_websocket() or []
    except Exception as e:
        logger.warning(f"조건식 목록 조회 실패 — DB 저장 ID 사용: {e}")
        rows = []
    ids: Dict[str, str] = {}
    if rows:
        wanted = set(names)
        for row in rows:
            name = str(row.get("condition_name", "")).strip()
            if name in wanted and name not in ids:
                ids[name] = str(row.get("condition_id", ""))
        if ids:
            sync_stored_condition_ids(ids)
        return ids, "live"
    for name in names:
        api_id, _ = _resolve_condition_api_id(name)
        if api_id:
            ids[name] = api_id
    return ids, "db"


def _resolve_condition_api_id(condition_name: str) -> Tuple[Optional[str], str]:
    """DB에 저장된 api_condition_id (목록 조회 실패 시 대체용)."""
    for db in get_db():
        row = db.query(AutoTradeCondition).filter(
            AutoTradeCondition.condition_name == condition_name
        ).first()
        if row and row.api_condition_id:
            return str(row.api_condition_id), row.condition_name
        break
    return None, condition_name


def _normalize_condition_stock(stock: Dict, condition_name: str) -> Dict:
    code = KiwoomAPI.normalize_stock_code(str(stock.get("stock_code", "")))
    price = abs(_parse_kiwoom_int(stock.get("current_price", 0)))
    return {
        "stock_code": code,
        "stock_name": (stock.get("stock_name") or "").strip(),
        "current_price": price,
        "price_diff": _parse_kiwoom_int(stock.get("price_diff", 0)),
        "change_rate": _parse_kiwoom_float(stock.get("change_rate", 0)),
        "volume": abs(_parse_kiwoom_int(stock.get("volume", 0))),
        "trade_amount": 0,
        "product_type": KiwoomAPI.classify_product_type(stock.get("stock_name", "")),
        "source": "condition",
        "condition_name": condition_name,
    }


async def _merge_stocks_into_map(
    by_code: Dict[str, Dict],
    stocks: List[Dict],
    resolved: str,
) -> None:
    for stock in stocks or []:
        item = _normalize_condition_stock(stock, resolved)
        code = item.get("stock_code")
        if not code:
            continue
        if code in by_code:
            prev = by_code[code]
            prev_names = prev.get("condition_names") or (
                [prev["condition_name"]] if prev.get("condition_name") else []
            )
            if resolved not in prev_names:
                prev_names.append(resolved)
            prev["condition_names"] = prev_names
            prev["condition_name"] = ", ".join(prev_names)
            if prev.get("source") == "screener":
                prev["source"] = "both"
            continue
        by_code[code] = item


async def fetch_condition_target_items(
    kiwoom_api: KiwoomAPI,
    condition_names: List[str],
    *,
    pause_sec: float = 2.0,
) -> Tuple[List[Dict], List[str]]:
    """선택 조건식별 종목 조회 → 스크리너 후보 형식으로 반환.

    Returns:
        (items, errors) — errors는 조회 실패한 조건식명 목록
    """
    if not condition_names:
        return [], []

    by_code: Dict[str, Dict] = {}
    errors: List[str] = []
    resolved_queries: List[Tuple[str, str, str]] = []

    ids, _ = await resolve_condition_ids(kiwoom_api, condition_names)
    for name in condition_names:
        api_id = ids.get(name)
        if not api_id:
            errors.append(name)
            logger.warning(f"조건식 API ID 없음 — 스킵: {name}")
            continue
        resolved_queries.append((name, api_id, name))

    if not resolved_queries:
        return [], errors

    try:
        async with kiwoom_api.condition_search_session() as session:
            for i, (name, api_id, resolved) in enumerate(resolved_queries):
                try:
                    stocks = await session.search(api_id, resolved)
                except Exception as e:
                    logger.warning(f"조건식 종목 조회 실패 ({resolved}): {e}")
                    errors.append(resolved)
                    continue
                await _merge_stocks_into_map(by_code, stocks, resolved)
                if pause_sec > 0 and i < len(resolved_queries) - 1:
                    await asyncio.sleep(pause_sec)
    except Exception as e:
        logger.warning(f"조건식 WS 배치 세션 실패 — 단건 폴백: {e}")
        for i, (name, api_id, resolved) in enumerate(resolved_queries):
            try:
                stocks = await kiwoom_api.search_condition_stocks(api_id, resolved)
            except Exception as ex:
                logger.warning(f"조건식 종목 조회 실패 ({resolved}): {ex}")
                errors.append(resolved)
                continue
            await _merge_stocks_into_map(by_code, stocks, resolved)
            if pause_sec > 0 and i < len(resolved_queries) - 1:
                await asyncio.sleep(pause_sec)

    return list(by_code.values()), errors


def merge_target_maps(
    volume_items: List[Dict],
    condition_items: List[Dict],
) -> Dict[str, Dict]:
    """종목코드 기준 병합 — 거래대금순·조건식 중복 시 source 갱신."""
    by_code: Dict[str, Dict] = {}
    for it in volume_items:
        code = KiwoomAPI.normalize_stock_code(str(it.get("stock_code", "")))
        if not code:
            continue
        row = {**it, "source": it.get("source") or "screener"}
        by_code[code] = row
    for it in condition_items:
        code = KiwoomAPI.normalize_stock_code(str(it.get("stock_code", "")))
        if not code:
            continue
        if code in by_code:
            prev = by_code[code]
            prev["source"] = "both"
            cname = it.get("condition_name")
            if cname:
                prev["condition_name"] = cname
                prev_names = it.get("condition_names") or [cname]
                prev["condition_names"] = prev_names
            continue
        by_code[code] = it
    return by_code
