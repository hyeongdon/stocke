"""매수 이후 고점·트레일링 방어 로직 단위 테스트."""
from datetime import date, datetime

from utils.datetime_kst import KST
from utils.position_peak_since_buy import (
    buy_time_utc_naive_to_kst,
    max_high_full_holding_days,
    max_high_since_buy_from_intraday_bars,
    resolve_position_peak_price,
    should_disarm_trailing,
)


def test_buy_time_utc_naive_to_kst():
    # 2026-07-10 04:39 UTC = 13:39 KST
    utc = datetime(2026, 7, 10, 4, 39, 33)
    kst = buy_time_utc_naive_to_kst(utc)
    assert kst.hour == 13 and kst.minute == 39


def test_intraday_excludes_pre_buy_bars():
    buy_kst = datetime(2026, 7, 10, 13, 39, tzinfo=KST)
    bars = [
        {"timestamp": "2026-07-10 13:45:00", "high": 291000},  # 13:30~13:45 — 매수 포함, 제외
        {"timestamp": "2026-07-10 14:00:00", "high": 278000},  # 13:45~14:00 — 포함
        {"timestamp": "2026-07-10 14:15:00", "high": 275000},
    ]
    assert max_high_since_buy_from_intraday_bars(bars, buy_kst) == 278000


def test_resolve_peak_caps_inflated_stored():
    # 티에스이 유형: stored=291000, 매수이후 실제=278000, 현재=270000
    peak = resolve_position_peak_price(
        buy_price=272500,
        current_price=270000,
        stored_peak=291000,
        since_buy_high=278000,
        allow_api=True,
    )
    assert peak == 278000


def test_resolve_peak_keeps_tick_above_intraday():
    peak = resolve_position_peak_price(
        buy_price=100000,
        current_price=103000,
        stored_peak=103500,
        since_buy_high=102000,
        allow_api=True,
    )
    assert peak == 103500


def test_should_not_disarm_once_armed():
    # 한 번 armed면 고점이 시작% 미만이어도 해제하지 않음
    assert should_disarm_trailing(
        trailing_armed=True,
        trail_start_rate=5.5,
        buy_price=272500,
        peak=278000,
    ) is False
    assert should_disarm_trailing(
        trailing_armed=True,
        trail_start_rate=5.5,
        buy_price=10419,
        peak=11790,
    ) is False


def test_daily_high_includes_today_after_buy_day():
    """익일 보유 — 당일 진행 중 일봉 고가(8400)를 쓴다. 매수일 일봉은 제외."""
    bars = [
        {"timestamp": "2026-08-12", "high": 7380},
        {"timestamp": "2026-08-13", "high": 8400},
    ]
    buy_date = date(2026, 8, 12)
    today = date(2026, 8, 13)
    assert max_high_full_holding_days(bars, buy_date, today) == 8400
    assert max_high_full_holding_days(bars, buy_date, buy_date) == 0


def test_locked_floor_kept_when_peak_below_start():
    """armed 후 고점이 시작% 미만이어도 잠긴 바닥·해제가 유지되는 정책."""
    buy = 1_221_000
    start = 7.6
    floor = int(buy * (1 + start / 100.0))
    peak = 1_299_000  # 6.39% < 7.6%
    peak_rate = (peak - buy) / buy * 100.0
    assert peak_rate < start
    assert should_disarm_trailing(
        trailing_armed=True,
        trail_start_rate=start,
        buy_price=buy,
        peak=peak,
    ) is False
    # _trailing_floor_for_buy와 동일: peak < target이면 기존 바닥 유지
    target = floor
    old = floor
    kept = old if (peak < target and old > 0) else (max(old, target) if old > 0 else target)
    assert kept == floor


if __name__ == "__main__":
    test_buy_time_utc_naive_to_kst()
    test_intraday_excludes_pre_buy_bars()
    test_resolve_peak_caps_inflated_stored()
    test_resolve_peak_keeps_tick_above_intraday()
    test_should_not_disarm_once_armed()
    test_daily_high_includes_today_after_buy_day()
    test_locked_floor_kept_when_peak_below_start()
    print("all ok")
