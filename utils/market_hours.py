"""KRX 정규장·거래일 판별 — 현재가 조회·자동매매 허용 구간 (KST)."""
from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta
from typing import Optional, Tuple

from utils.datetime_kst import KST, as_kst
from utils.krx_holiday_store import holiday_dates_for_year, holiday_label, is_holiday


def krx_holidays_for_year(year: int):
    return holiday_dates_for_year(year)


def is_krx_holiday(day: date) -> bool:
    return is_holiday(day)


def is_krx_trading_day(now: Optional[datetime] = None) -> bool:
    """평일이면서 KRX 휴장일이 아닌 날."""
    kst = as_kst(now)
    if kst.weekday() >= 5:
        return False
    return not is_krx_holiday(kst.date())


def trading_day_block_reason(now: Optional[datetime] = None) -> Optional[str]:
    """거래 불가일이면 사유 문자열, 거래일이면 None."""
    kst = as_kst(now)
    if kst.weekday() >= 5:
        return "주말(토·일) - 거래소 휴장"
    if is_krx_holiday(kst.date()):
        label = holiday_label(kst.date())
        if label:
            return f"거래소 휴장일({kst.strftime('%Y-%m-%d')} {label})"
        return f"거래소 휴장일({kst.strftime('%Y-%m-%d')})"
    return None


def is_krx_session(now: Optional[datetime] = None) -> bool:
    """평일 거래일 09:00~15:30 KST — 현재가·일봉 차트·ATR 실시간 조회 허용 구간."""
    kst = as_kst(now)
    if not is_krx_trading_day(kst):
        return False
    return dt_time(9, 0) <= kst.time() <= dt_time(15, 30)


def _parse_hm(value: Optional[str], default: Tuple[int, int]) -> dt_time:
    try:
        h, m = map(int, (value or f"{default[0]:02d}:{default[1]:02d}").split(":"))
        return dt_time(h, m)
    except Exception:
        return dt_time(*default)


def _strategy_windows(settings) -> list:
    """전략별 (라벨, 시작, 종료) — 엔진 가동 합집합·신규매수 any-open 판정용."""
    windows = [
        (
            "레거시",
            _parse_hm(getattr(settings, "trade_start_time", None), (10, 0)),
            _parse_hm(getattr(settings, "trade_end_time", None), (15, 20)),
        ),
        (
            "상따",
            _parse_hm(getattr(settings, "sangtta_trade_start_time", None), (9, 5)),
            _parse_hm(getattr(settings, "sangtta_trade_end_time", None), (11, 0)),
        ),
    ]
    use_breakout = bool(getattr(settings, "use_breakout", False))
    has_breakout_conds = bool(str(getattr(settings, "breakout_condition_names", None) or "").strip())
    if use_breakout or has_breakout_conds:
        windows.append(
            (
                "돌파",
                _parse_hm(getattr(settings, "breakout_trade_start_time", None), (11, 0)),
                _parse_hm(getattr(settings, "breakout_trade_end_time", None), (14, 30)),
            )
        )
    use_ymgp = bool(getattr(settings, "use_ymgp", False))
    has_ymgp_conds = bool(str(getattr(settings, "ymgp_condition_names", None) or "").strip())
    if use_ymgp or has_ymgp_conds:
        windows.append(
            (
                "역매공파",
                _parse_hm(getattr(settings, "ymgp_trade_start_time", None), (9, 30)),
                _parse_hm(getattr(settings, "ymgp_trade_end_time", None), (14, 30)),
            )
        )
    if bool(getattr(settings, "use_jongga", False)):
        if bool(getattr(settings, "jongga_pig_split", True)):
            j_end = getattr(settings, "jongga_leg3_end_time", None) or "15:28"
            j_end_default = (15, 28)
        else:
            j_end = (
                getattr(settings, "jongga_pick_end_time", None)
                or getattr(settings, "jongga_trade_end_time", None)
            )
            j_end_default = (14, 40)
        windows.append(
            (
                "종가배팅",
                _parse_hm(getattr(settings, "jongga_trade_start_time", None), (14, 30)),
                _parse_hm(j_end, j_end_default),
            )
        )
    return windows


def should_auto_shutdown_server(
    now: Optional[datetime] = None,
    *,
    enabled: Optional[bool] = None,
    shutdown_hm: Optional[str] = None,
) -> bool:
    """KST 기준 서버 자동 종료 시각 도달 여부 (기본 19:00)."""
    if enabled is None:
        try:
            from core.config import Config
            enabled = bool(Config.SERVER_AUTO_SHUTDOWN_ENABLED)
        except Exception:
            enabled = True
    if not enabled:
        return False
    if shutdown_hm is None:
        try:
            from core.config import Config
            shutdown_hm = str(Config.SERVER_AUTO_SHUTDOWN_TIME or "19:00")
        except Exception:
            shutdown_hm = "19:00"
    kst = as_kst(now)
    cutoff = _parse_hm(shutdown_hm, (19, 0))
    return kst.time() >= cutoff


def linked_trading_session_bounds(settings, now: Optional[datetime] = None) -> Tuple[dt_time, dt_time]:
    """스캐너·매수·손절 모니터 가동 구간 = 전략 시간창 합집합(+장마감청산 연장)."""
    windows = _strategy_windows(settings)
    start = min(w[1] for w in windows)
    end = max(w[2] for w in windows)
    if getattr(settings, "liquidate_before_close", False):
        liq = _parse_hm(getattr(settings, "liquidate_time", None), (15, 10))
        if liq > end:
            end = liq
    return start, end


def linked_trading_session_window_str(settings, now: Optional[datetime] = None) -> str:
    start, end = linked_trading_session_bounds(settings, now)
    return f"{start.strftime('%H:%M')}~{end.strftime('%H:%M')}"


def any_strategy_buy_window_open(settings, now: Optional[datetime] = None) -> bool:
    """레거시·상따·돌파 중 하나라도 신규매수 시간창이면 True."""
    if settings is None:
        return False
    kst = as_kst(now)
    t = kst.time()
    for _label, start, end in _strategy_windows(settings):
        if start <= t <= end:
            return True
    return False


def in_linked_trading_session(settings, now: Optional[datetime] = None) -> bool:
    """종목 스캔·매수 실행기·손절/익절 모니터 — 전략 합집합 매매 시간."""
    if settings is None:
        return False
    kst = as_kst(now)
    if not is_krx_trading_day(kst):
        return False
    start, end = linked_trading_session_bounds(settings, now)
    return start <= kst.time() <= end


def linked_trading_session_block_reason(
    settings,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """연동 세션 외 사유. None이면 가동 가능."""
    off = trading_day_block_reason(now)
    if off:
        return off
    if settings is None:
        return "자동매매 설정 없음"
    if in_linked_trading_session(settings, now):
        return None
    window = linked_trading_session_window_str(settings, now)
    return f"매매 시간 외 ({window})"


def in_auto_trade_engine_session(settings, now: Optional[datetime] = None) -> bool:
    """거래일 trade_start ~ trade_end — 스캐너·매수기 가동 구간."""
    return in_linked_trading_session(settings, now)


def auto_trade_engine_block_reason(settings, now: Optional[datetime] = None) -> Optional[str]:
    """스캐너·매수 실행기가 멈춰야 할 사유. None이면 가동 가능."""
    return linked_trading_session_block_reason(settings, now)


def is_stop_loss_monitoring_session(
    settings=None,
    now: Optional[datetime] = None,
) -> bool:
    """손절/익절 모니터 — 스캐너·매수와 동일 매매 시간."""
    if settings is None:
        from core.models import AutoTradeSettings, get_db

        for db in get_db():
            settings = db.query(AutoTradeSettings).first()
            break
    return in_linked_trading_session(settings, now)


def seconds_until_stop_loss_monitoring(
    settings=None,
    now: Optional[datetime] = None,
) -> int:
    """다음 연동 세션 시작까지 초 (trade_start KST, 최소 60초)."""
    kst = as_kst(now)

    if settings is None:
        from core.models import AutoTradeSettings, get_db

        for db in get_db():
            settings = db.query(AutoTradeSettings).first()
            break

    if settings and is_krx_trading_day(kst):
        start, _ = linked_trading_session_bounds(settings, kst)
        if kst.time() < start:
            target = kst.replace(
                hour=start.hour,
                minute=start.minute,
                second=0,
                microsecond=0,
            )
            return max(60, int((target - kst).total_seconds()))

    d = kst.date() + timedelta(days=1)
    default_start = dt_time(10, 0)
    if settings:
        default_start, _ = linked_trading_session_bounds(settings, kst)

    for _ in range(400):
        probe = datetime.combine(d, default_start, tzinfo=KST)
        if is_krx_trading_day(probe):
            return max(60, min(int((probe - kst).total_seconds()), 3600))
        d += timedelta(days=1)
    return 3600


def telegram_market_alert_block_reason(now: Optional[datetime] = None) -> Optional[str]:
    """조건식·정기 텔레그램 알림 — 장중(거래일 09:00~15:30 KST)만 허용."""
    kst = as_kst(now)
    off_day = trading_day_block_reason(kst)
    if off_day:
        return off_day
    if kst.time() < dt_time(9, 0):
        return f"장 시작 전 ({kst.strftime('%H:%M')}) — 알림 미전송"
    if kst.time() > dt_time(15, 30):
        return f"장 마감 후 ({kst.strftime('%H:%M')}) — 알림 미전송"
    return None


def telegram_market_alert_allowed(now: Optional[datetime] = None) -> bool:
    return telegram_market_alert_block_reason(now) is None
