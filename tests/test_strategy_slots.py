"""상따/돌파 전략 슬롯 집계 — 자기 신호 포함·중복 예약 버그 회귀."""
from types import SimpleNamespace
from datetime import datetime, timedelta

from utils.auto_trade_engine import (
    IN_FLIGHT_BUY_ORDERED_MINUTES,
    _count_strategy_slots,
    is_strategy_slot_available,
    utc_now_naive,
)


class _FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, positions=None, signals=None):
        self.positions = positions or []
        self.signals = signals or []

    def query(self, model):
        name = getattr(model, "__name__", str(model))
        if "Position" in name:
            return _FakeQuery(self.positions)
        return _FakeQuery(self.signals)


def _settings(slots=1):
    return SimpleNamespace(
        legacy_max_slots=slots,
        sangtta_max_slots=slots,
        breakout_max_slots=slots,
        ma1592_max_slots=slots,
    )


def test_executor_allows_self_reserved_signal_when_limit_1():
    """슬롯=1, 상따 보유 0 — 실행기 단계에서 PENDING 자기 신호만 있어도 통과해야 함."""
    now = utc_now_naive()
    sig = SimpleNamespace(
        stock_code="085910",
        status="PROCESSING",
        detected_at=now,
        additional_data={"strategy": "sangtta"},
    )
    session = _FakeSession(signals=[sig])
    assert _count_strategy_slots(session, "sangtta") == 1
    assert is_strategy_slot_available(_settings(1), session, "sangtta", for_new_signal=True) is False
    assert is_strategy_slot_available(_settings(1), session, "sangtta", for_new_signal=False) is True


def test_legacy_holding_does_not_block_sangtta_slot():
    pos = SimpleNamespace(stock_code="005860", status="HOLDING", strategy_key="legacy")
    session = _FakeSession(positions=[pos])
    assert _count_strategy_slots(session, "sangtta") == 0
    assert is_strategy_slot_available(_settings(1), session, "sangtta", for_new_signal=True) is True


def test_legacy_holding_fills_legacy_slot():
    pos = SimpleNamespace(stock_code="005860", status="HOLDING", strategy_key="legacy")
    session = _FakeSession(positions=[pos])
    assert _count_strategy_slots(session, "legacy") == 1
    assert is_strategy_slot_available(_settings(1), session, "legacy", for_new_signal=True) is False
    assert is_strategy_slot_available(_settings(1), session, "legacy", for_new_signal=False) is True


def test_untagged_holding_counts_as_legacy_for_backward_compatibility():
    pos = SimpleNamespace(stock_code="005860", status="HOLDING", strategy_key=None)
    session = _FakeSession(positions=[pos])
    assert _count_strategy_slots(session, "legacy") == 1


def test_sangtta_holding_fills_slot():
    pos = SimpleNamespace(stock_code="005860", status="HOLDING", strategy_key="sangtta")
    session = _FakeSession(positions=[pos])
    assert _count_strategy_slots(session, "sangtta") == 1
    assert is_strategy_slot_available(_settings(1), session, "sangtta", for_new_signal=True) is False
    assert is_strategy_slot_available(_settings(1), session, "sangtta", for_new_signal=False) is True


def test_reserved_dedupes_same_code_and_skips_holding():
    now = utc_now_naive()
    pos = SimpleNamespace(stock_code="085910", status="HOLDING", strategy_key="sangtta")
    sigs = [
        SimpleNamespace(
            stock_code="085910", status="PENDING", detected_at=now,
            additional_data={"strategy": "sangtta"},
        ),
        SimpleNamespace(
            stock_code="085910", status="PENDING", detected_at=now,
            additional_data={"strategy": "sangtta"},
        ),
        SimpleNamespace(
            stock_code="123456", status="PENDING", detected_at=now,
            additional_data={"strategy": "sangtta", "is_add_buy": True},
        ),
    ]
    session = _FakeSession(positions=[pos], signals=sigs)
    assert _count_strategy_slots(session, "sangtta") == 1


def test_stale_ordered_excluded():
    stale = utc_now_naive() - timedelta(minutes=IN_FLIGHT_BUY_ORDERED_MINUTES + 5)
    sig = SimpleNamespace(
        stock_code="085910",
        status="ORDERED",
        detected_at=stale,
        additional_data={"strategy": "sangtta"},
    )
    session = _FakeSession(signals=[sig])
    assert _count_strategy_slots(session, "sangtta") == 0


def test_ma1592_holding_fills_slot():
    positions = [
        SimpleNamespace(stock_code="085620", status="HOLDING", strategy_key="ma1592"),
        SimpleNamespace(stock_code="280360", status="HOLDING", strategy_key="ma1592"),
    ]
    session = _FakeSession(positions=positions)
    assert _count_strategy_slots(session, "ma1592") == 2
    assert is_strategy_slot_available(_settings(2), session, "ma1592", for_new_signal=True) is False
    assert is_strategy_slot_available(_settings(2), session, "ma1592", for_new_signal=False) is True


def test_ma1592_add_buy_does_not_consume_slot():
    now = utc_now_naive()
    pos = SimpleNamespace(stock_code="085620", status="HOLDING", strategy_key="ma1592")
    sig = SimpleNamespace(
        stock_code="204620",
        status="PENDING",
        detected_at=now,
        additional_data={"strategy": "ma1592", "is_add_buy": True, "entry_leg": 3},
    )
    session = _FakeSession(positions=[pos], signals=[sig])
    assert _count_strategy_slots(session, "ma1592") == 1
    assert is_strategy_slot_available(_settings(2), session, "ma1592", for_new_signal=True) is True


def test_ma1590_alias_counts_toward_ma1592_slots():
    pos = SimpleNamespace(stock_code="085620", status="HOLDING", strategy_key="ma1590")
    session = _FakeSession(positions=[pos])
    assert _count_strategy_slots(session, "ma1592") == 1
    assert is_strategy_slot_available(_settings(1), session, "ma1590", for_new_signal=True) is False
