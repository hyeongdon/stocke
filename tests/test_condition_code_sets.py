import asyncio
from unittest import mock

from utils.condition_code_sets import ConditionCodeSet


class _FakeKiwoom:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error

    async def get_condition_list_websocket(self):
        if self.error:
            raise self.error
        return self.rows


def _set():
    return ConditionCodeSet("weekly_rsi", "주봉RSI", ["주봉RSI"], "_test_weekly_rsi.json")


@mock.patch("utils.screener_targets.sync_stored_condition_ids")
@mock.patch("utils.screener_targets._resolve_condition_api_id", return_value=("10", "주봉RSI"))
def test_live_list_id_wins_over_stored_id(_stored, sync):
    cs = _set()
    api = _FakeKiwoom(rows=[{"condition_id": "3", "condition_name": "주봉RSI"},
                            {"condition_id": "10", "condition_name": "다른조건"}])
    assert asyncio.run(cs._resolve_condition_ids(api)) == {"주봉RSI": "3"}
    assert cs._id_source == "live"
    sync.assert_called_once_with({"주봉RSI": "3"})


@mock.patch("utils.screener_targets.sync_stored_condition_ids")
@mock.patch("utils.screener_targets._resolve_condition_api_id", return_value=("10", "주봉RSI"))
def test_stored_id_used_only_when_list_unavailable(_stored, _sync):
    cs = _set()
    assert asyncio.run(cs._resolve_condition_ids(_FakeKiwoom(error=RuntimeError("ws")))) == {"주봉RSI": "10"}
    assert cs._id_source == "db"


@mock.patch("utils.screener_targets.sync_stored_condition_ids")
@mock.patch("utils.screener_targets._resolve_condition_api_id", return_value=("10", "주봉RSI"))
def test_missing_name_in_live_list_is_not_filled_from_db(_stored, _sync):
    cs = _set()
    api = _FakeKiwoom(rows=[{"condition_id": "10", "condition_name": "다른조건"}])
    assert asyncio.run(cs._resolve_condition_ids(api)) == {}


def test_cache_without_id_source_is_stale():
    cs = _set()
    from utils.datetime_kst import now_kst

    now = now_kst()
    cache = {"condition_name": "주봉RSI", "fetched_at": now.isoformat()}
    assert cs._is_fresh(cache, now) is False
