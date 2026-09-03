from datetime import datetime, timezone

from utils.overnight_keep import (
    OvernightCandidate as C,
    is_today_jongga,
    jongga_force_liquidate_at_close,
    select_overnight_keep,
)


def _ids(rows):
    return [r.position_id for r in rows]


def test_today_jongga_always_kept_plus_three_others():
    today = datetime(2026, 8, 19, tzinfo=timezone.utc).date()
    rows = [
        C(1, "jongga", -0.2, is_today_jongga=is_today_jongga("jongga", datetime(2026, 8, 19, 5, 40), today)),
        C(2, "breakout", 4.0),
        C(3, "ymgp", -1.0),
        C(4, "legacy", -0.4),
        C(5, "sangtta", -8.0),
        C(6, "breakout", -2.0),
    ]
    keep_ids, kept, liq = select_overnight_keep(rows, keep_slots=3)
    assert 1 in keep_ids
    assert len([r for r in kept if not r.is_today_jongga]) == 3
    # 익절(2) 우선 정리. 같은 전략이면 작은 손실(6)을 남기고 +4%(2)는 정리.
    assert 2 in {r.position_id for r in liq}
    assert keep_ids == {1, 3, 4, 6}


def test_trim_winners_then_large_losses_one_per_strategy():
    rows = [
        C(10, "breakout", 5.0),
        C(11, "ymgp", -0.5),
        C(12, "legacy", -1.2),
        C(13, "sangtta", -9.0),
        C(14, "fractal", 1.0),
    ]
    keep_ids, _, liq = select_overnight_keep(rows, keep_slots=3)
    assert keep_ids == {11, 12, 13}
    assert set(_ids(liq)) == {10, 14}


def test_same_strategy_keeps_one_smallest_loss():
    rows = [
        C(1, "ymgp", -0.3),
        C(2, "ymgp", -4.0),
        C(3, "ymgp", 2.0),
        C(4, "breakout", -1.0),
        C(5, "legacy", -1.5),
    ]
    keep_ids, _, liq = select_overnight_keep(rows, keep_slots=3)
    assert 1 in keep_ids
    assert 2 not in keep_ids
    assert 3 not in keep_ids
    assert keep_ids == {1, 4, 5}
    assert {r.position_id for r in liq} == {2, 3}


def test_only_winners_keep_smallest_profits():
    rows = [
        C(1, "breakout", 8.0),
        C(2, "ymgp", 1.2),
        C(3, "legacy", 3.0),
        C(4, "sangtta", 0.5),
    ]
    keep_ids, _, _ = select_overnight_keep(rows, keep_slots=3)
    assert keep_ids == {2, 3, 4}


def test_keep_slots_zero_only_today_jongga():
    rows = [
        C(1, "jongga", 0.0, is_today_jongga=True),
        C(2, "ymgp", -1.0),
        C(3, "breakout", -0.2),
    ]
    keep_ids, _, liq = select_overnight_keep(rows, keep_slots=0)
    assert keep_ids == {1}
    assert {r.position_id for r in liq} == {2, 3}


def test_force_liquidate_skips_keep_even_if_best_loss():
    rows = [
        C(1, "fractal", -0.1, force_liquidate=True),
        C(2, "ymgp", -2.0),
        C(3, "breakout", -2.1),
        C(4, "legacy", -2.2),
    ]
    keep_ids, _, liq = select_overnight_keep(rows, keep_slots=3)
    assert 1 not in keep_ids
    assert 1 in {r.position_id for r in liq}
    assert keep_ids == {2, 3, 4}


def test_yesterday_jongga_competes_for_slots():
    today = datetime(2026, 8, 19).date()
    buy_y = datetime(2026, 8, 18, 6, 0)  # UTC naive → 8/18 KST next calendar? 06:00 UTC = 15:00 KST 8/18
    assert is_today_jongga("jongga", buy_y, today) is False
    rows = [
        C(1, "jongga", -0.4, is_today_jongga=False),
        C(2, "ymgp", -0.3),
        C(3, "breakout", -0.2),
        C(4, "legacy", 6.0),
    ]
    keep_ids, _, liq = select_overnight_keep(rows, keep_slots=3)
    assert 4 in {r.position_id for r in liq}
    assert keep_ids == {1, 2, 3}


def test_jongga_next_day_plus_force_liquidates():
    today = datetime(2026, 8, 19).date()
    buy_y = datetime(2026, 8, 18, 6, 0)
    assert jongga_force_liquidate_at_close("jongga", buy_y, today, 0.8) is True
    assert jongga_force_liquidate_at_close("jongga", buy_y, today, 0.0) is False
    assert jongga_force_liquidate_at_close("jongga", buy_y, today, -0.5) is False
    rows = [
        C(1, "jongga", 0.8, is_today_jongga=False, force_liquidate=True),
        C(2, "ymgp", -0.3),
        C(3, "breakout", -0.2),
        C(4, "legacy", -0.1),
    ]
    keep_ids, _, liq = select_overnight_keep(rows, keep_slots=3)
    assert 1 not in keep_ids
    assert 1 in {r.position_id for r in liq}
    assert keep_ids == {2, 3, 4}


def test_jongga_third_session_force_even_if_loss():
    today = datetime(2026, 8, 3).date()  # Mon — 7/30(목) 매수 기준 세션 3
    buy_thu = datetime(2026, 7, 30, 5, 40)
    assert jongga_force_liquidate_at_close("jongga", buy_thu, today, -1.2) is True
    assert jongga_force_liquidate_at_close("legacy", buy_thu, today, 2.0) is False


def test_today_jongga_plus_not_forced():
    today = datetime(2026, 8, 19).date()
    buy_today = datetime(2026, 8, 19, 5, 40)
    assert is_today_jongga("jongga", buy_today, today) is True
    assert jongga_force_liquidate_at_close("jongga", buy_today, today, 1.5) is False
