from utils.volume_profile import compute_profile, compute_profiles, is_downtrend


def _bar(i, close, vol, spread=50):
    return {
        "timestamp": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
        "open": close,
        "high": close + spread,
        "low": close - spread,
        "close": close,
        "volume": vol,
    }


def _downtrend_into_zone():
    # 고점 10000 부근에서 대량 거래(매물대) 후 8000 까지 하락, 막판 9400 으로 반등
    bars = [_bar(i, 10000, 1_000_000) for i in range(40)]
    bars += [_bar(40 + i, 10000 - i * 25, 50_000) for i in range(80)]
    bars += [_bar(120 + i, 8000 + i * 10, 50_000) for i in range(5)]
    return bars


def test_resistance_zone_above_close():
    p = compute_profile(_downtrend_into_zone(), 120)
    assert p is not None
    assert p["resist_low"] is not None and p["resist_low"] > p["close_price"]
    assert 9800 <= p["poc_price"] <= 10100
    assert p["resist_gap_pct"] > 0
    assert p["resist_share"] > 30


def test_downtrend_flag():
    closes = [10000 - i * 20 for i in range(100)]
    assert is_downtrend(closes)
    assert not is_downtrend(list(reversed(closes)))
    assert not is_downtrend(closes[:30])


def test_insufficient_bars_returns_none():
    assert compute_profile([_bar(i, 1000, 100) for i in range(5)], 60) is None


def test_compute_profiles_lookbacks():
    bars = _downtrend_into_zone()
    assert [p["lookback"] for p in compute_profiles(bars, "1D")] == [60, 120, 240]
    assert [p["lookback"] for p in compute_profiles(bars, "1W")] == [52, 156]
