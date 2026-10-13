import asyncio
from unittest.mock import AsyncMock

import api.kiwoom_api as kiwoom_mod
import utils.rwm_bowl as rwm_mod
from managers.rwm_bowl_scheduler import RwmBowlScheduler
from utils.rwm_bowl import RwmStateStore


def _setup(tmp_path, monkeypatch, tr_resp, fundamental_name=""):
    store = RwmStateStore(tmp_path / "_rwm_bowl_state.json")
    monkeypatch.setattr(rwm_mod, "get_state_store", lambda: store)
    tr = AsyncMock(return_value=tr_resp)
    monkeypatch.setattr(kiwoom_mod.kiwoom_api, "_request_stockinfo_tr", tr)
    monkeypatch.setattr(RwmBowlScheduler, "_name_from_fundamental", staticmethod(lambda code: fundamental_name))
    return store, tr


def test_resolve_name_uses_ka10001(tmp_path, monkeypatch):
    store, tr = _setup(tmp_path, monkeypatch, {"success": True, "data": {"stk_nm": "리노공업"}})
    assert asyncio.run(RwmBowlScheduler().resolve_name("058470")) == "리노공업"
    tr.assert_awaited_once_with("ka10001", {"stk_cd": "058470"})
    assert store.data["names"]["058470"] == "리노공업"


def test_resolve_name_falls_back_to_fundamental(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {"success": False, "error": "x"}, fundamental_name="리노공업")
    assert asyncio.run(RwmBowlScheduler().resolve_name("058470")) == "리노공업"


def test_resolve_name_empty_when_all_fail(tmp_path, monkeypatch):
    store, _ = _setup(tmp_path, monkeypatch, {"success": True, "data": {"cur_prc": "1000"}})
    assert asyncio.run(RwmBowlScheduler().resolve_name("058470")) == ""
    assert "058470" not in store.data.get("names", {})
