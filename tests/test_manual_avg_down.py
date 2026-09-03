from types import SimpleNamespace

from core.models import PositionBuyFill
from utils.position_buy_fills import MANUAL_AVG_DOWN_NOTE, manual_avg_down_state


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *_args):
        return self

    def order_by(self, *_args):
        return self

    def all(self):
        return self.rows


class _Session:
    def __init__(self, rows):
        self.rows = rows

    def query(self, model):
        assert model is PositionBuyFill
        return _Query(self.rows)


def _fill(fill_type, amount, note=None, row_id=1):
    return SimpleNamespace(
        id=row_id,
        fill_type=fill_type,
        amount=amount,
        planned_amount=None,
        note=note,
        filled_at=None,
    )


def test_manual_avg_down_uses_initial_fill_as_baseline():
    state = manual_avg_down_state(
        _Session([_fill("INITIAL", 1_000_000), _fill("ADD", 300_000, row_id=2)]),
        position_id=7,
        fallback_amount=1_300_000,
    )

    assert state == {"baseline_amount": 1_000_000, "done": False}


def test_manual_avg_down_is_done_after_manual_fill():
    state = manual_avg_down_state(
        _Session([
            _fill("INITIAL", 1_000_000),
            _fill("ADD", 500_000, MANUAL_AVG_DOWN_NOTE, row_id=2),
        ]),
        position_id=7,
    )

    assert state["done"] is True


def test_manual_avg_down_falls_back_for_legacy_position():
    state = manual_avg_down_state(_Session([]), position_id=7, fallback_amount=800_000)

    assert state == {"baseline_amount": 800_000, "done": False}
