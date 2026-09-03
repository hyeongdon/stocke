from datetime import datetime

from utils.program_net_continuation import (
    completed_program_slots,
    parse_program_tm_minutes,
    program_net_continuation_ok,
)


def _rows(pairs):
    return [{"tm": tm, "net_qty": q} for tm, q in pairs]


def test_parse_program_tm():
    assert parse_program_tm_minutes("123300") == 12 * 60 + 33
    assert parse_program_tm_minutes("12:33:00") == 12 * 60 + 33
    assert parse_program_tm_minutes("1233") == 12 * 60 + 33
    assert parse_program_tm_minutes("") is None


def test_excludes_forming_minute():
    now = datetime(2026, 8, 14, 12, 33, 20)
    rows = _rows([
        ("1229", 10),
        ("1230", 20),
        ("1231", -5),
        ("1232", 8),
        ("1233", 99),
    ])
    slots = completed_program_slots(rows, now=now)
    assert [s["tm"] for s in slots] == ["1229", "1230", "1231", "1232"]
    assert slots[-1]["net_qty"] == 8


def test_five_of_three_pass():
    now = datetime(2026, 8, 14, 12, 40, 5)
    rows = _rows([
        ("1235", 1),
        ("1236", 0),
        ("1237", 2),
        ("1238", -1),
        ("1239", 3),
        ("1240", 9),
    ])
    ok, reason, d = program_net_continuation_ok(rows, lookback=5, min_buy=3, now=now)
    assert ok, reason
    assert d["program_buy_count"] == 3
    assert d["program_nets"] == [1, 0, 2, -1, 3]


def test_five_of_three_fail():
    now = datetime(2026, 8, 14, 12, 40, 5)
    rows = _rows([
        ("1235", 1),
        ("1236", 0),
        ("1237", 0),
        ("1238", -1),
        ("1239", 3),
    ])
    ok, reason, d = program_net_continuation_ok(rows, lookback=5, min_buy=3, now=now)
    assert not ok
    assert d["program_buy_count"] == 2
    assert "부족" in reason


def test_not_enough_slots():
    now = datetime(2026, 8, 14, 9, 5, 0)
    rows = _rows([("0903", 10), ("0904", 10)])
    ok, reason, _ = program_net_continuation_ok(rows, lookback=5, min_buy=3, now=now)
    assert not ok
    assert "구간 부족" in reason
