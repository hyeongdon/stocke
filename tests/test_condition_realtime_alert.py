"""실시간 조건식 편입 파싱 단위 테스트."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from notifications.condition_realtime_alert import (
    ConditionRealtimeAlerter,
    _is_insert_event,
    _parse_real_items,
    build_entry_message,
)


def test_is_insert_markers():
    assert _is_insert_event({"843": "I"}) is True
    assert _is_insert_event({"843": "D"}) is False
    assert _is_insert_event({"843": "1"}) is True
    assert _is_insert_event({"841": "편입"}) is True
    assert _is_insert_event({"841": "이탈"}) is False
    assert _is_insert_event({}) is False


def test_parse_real_items_nested():
    msg = {
        "trnm": "REAL",
        "seq": "4",
        "data": [
            {
                "type": "0A",
                "name": "A001720",
                "values": {"843": "I", "9001": "A001720", "302": "신영증권", "10": "10000"},
            }
        ],
    }
    items = _parse_real_items(msg)
    assert len(items) == 1
    code, values, is_insert = items[0]
    assert code == "001720"
    assert is_insert is True
    assert values.get("302") == "신영증권"


def test_build_entry_message():
    text = build_entry_message(
        "1592매매",
        "001720",
        stock_name="신영증권",
        values={"10": "10000", "11": "500", "12": "5.2"},
    )
    assert "1592매매" in text
    assert "신영증권" in text
    assert "001720" in text
    assert "조건식 편입" in text
    assert "시세" not in text
    assert "10000" not in text
    assert "5.2%" not in text


def test_build_entry_message_falls_back_to_code():
    text = build_entry_message("1592매매", "382800")
    assert "종목: 382800(382800)" in text


def test_resolve_stock_name_from_ka10001():
    api = SimpleNamespace(
        normalize_stock_code=lambda c: c,
        _request_stockinfo_tr=AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "stk_cd": "264850",
                    "stk_nm": "메디톡스",
                    "cur_prc": "-5840",
                    "flu_rt": "-1.85",
                },
            }
        ),
    )
    alerter = ConditionRealtimeAlerter(api, notifier=SimpleNamespace(), names=["1592매매"])
    name = asyncio.run(alerter._resolve_stock_name("264850", {}))
    assert name == "메디톡스"
    assert alerter._code_names["264850"] == "메디톡스"
    api._request_stockinfo_tr.assert_awaited_once_with("ka10001", {"stk_cd": "264850"})


def test_resolve_stock_name_does_not_fetch_quote_when_name_is_present():
    api = SimpleNamespace(_request_stockinfo_tr=AsyncMock())
    alerter = ConditionRealtimeAlerter(api, notifier=SimpleNamespace(), names=[])

    name = asyncio.run(alerter._resolve_stock_name("264850", {"302": "메디톡스"}))

    assert name == "메디톡스"
    api._request_stockinfo_tr.assert_not_awaited()


def test_is_ma1592_condition_matches_1592_and_legacy():
    api = SimpleNamespace()
    alerter = ConditionRealtimeAlerter(api, notifier=SimpleNamespace(), names=[])
    assert alerter._is_ma1592_condition("1592매매") is True
    assert alerter._is_ma1592_condition("1590매매") is True
    assert alerter._is_ma1592_condition("돌파") is False


def test_ma1592_realtime_insert_rejects_dead_cross():
    api = SimpleNamespace()
    alerter = ConditionRealtimeAlerter(api, notifier=SimpleNamespace(), names=["1592매매"])
    with patch(
        "utils.ma1592.upsert_from_condition_async",
        new=AsyncMock(return_value=(False, "NO_GC", None)),
    ) as mocked:
        asyncio.run(alerter._ma1592_universe_on_insert(
            "1592매매", "005950", {"302": "이수화학", "10": "11880"},
        ))
        mocked.assert_awaited_once()
