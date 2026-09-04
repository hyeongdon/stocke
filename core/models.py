from datetime import datetime
from typing import Generator

from sqlalchemy import Column, Integer, String, DateTime, Boolean, create_engine, UniqueConstraint, Date, text, JSON, Float, Index, Text
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from .config import Config

# 데이터베이스 설정 (SQLite 또는 PostgreSQL 지원)
DATABASE_URL = Config.DATABASE_URL

# PostgreSQL과 SQLite를 모두 지원
if DATABASE_URL.startswith('postgresql'):
    # PostgreSQL용 엔진 설정
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,      # 연결 상태 확인
        pool_size=10,            # 기본 연결 풀 크기
        max_overflow=20,         # 최대 추가 연결 수
        pool_recycle=3600,       # 1시간마다 연결 재생성
        future=True,
    )
else:
    # SQLite용 엔진 설정 (기존 코드 유지)
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        future=True,
    )
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

Base = declarative_base()


class PendingBuySignal(Base):
    __tablename__ = "pending_buy_signals"

    id = Column(Integer, primary_key=True, index=True)
    condition_id = Column(Integer, nullable=False, index=True)
    stock_code = Column(String(20), nullable=False, index=True)
    stock_name = Column(String(100), nullable=False)
    detected_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    detected_date = Column(Date, nullable=False, index=True)  # 일자별 관리용 필드
    status = Column(String(20), nullable=False, default="PENDING")  # WATCHING|PENDING|PROCESSING|ORDERED|FILLED|FAILED|EXPIRED|CANCELLED
    signal_type = Column(String(20), nullable=False, default="condition", index=True)  # 신호 타입: condition, reference, strategy
    failure_reason = Column(String(255), nullable=True)  # 실패 사유 저장
    
    # 대량거래 전략용 필드들
    reference_candle_high = Column(Integer, nullable=True)  # 기준봉 고가
    reference_candle_date = Column(DateTime, nullable=True)  # 기준봉 날짜
    target_price = Column(Integer, nullable=True)  # 목표가 (고가의 절반)
    additional_data = Column(JSON, nullable=True)  # 스캐너 메타 (등락률, is_add_buy 등)

    __table_args__ = (
        # 일자별로 같은 조건식/종목은 하나만 유지 (일자별 관리)
        UniqueConstraint("detected_date", "condition_id", "stock_code", name="uq_pending_daily_unique"),
    )


class AutoTradeCondition(Base):
    __tablename__ = "auto_trade_conditions"

    id = Column(Integer, primary_key=True, index=True)
    condition_name = Column(String(100), nullable=False, unique=True, index=True)
    api_condition_id = Column(String(50), nullable=True)  # 키움 API에서 제공하는 ID(문자열 가능)
    is_enabled = Column(Boolean, nullable=False, default=False, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("condition_name", name="uq_autotrade_condition_name"),
    )


class AutoTradeSettings(Base):
    __tablename__ = "auto_trade_settings"

    id = Column(Integer, primary_key=True, index=True)
    is_enabled = Column(Boolean, nullable=False, default=False, index=True)
    max_invest_amount = Column(Integer, nullable=False, default=1000000)  # 최대 투자 금액
    stop_loss_rate = Column(Float, nullable=False, default=5.0)  # 손절 비율 (%) — 음수/소수 허용
    take_profit_rate = Column(Float, nullable=False, default=10.0)  # 익절 비율 (%)

    # ===== 관심종목 (자동매매 대상) =====
    watchlist_codes = Column(Text, nullable=True)  # 쉼표 구분 종목코드 (예: "005930, 000660")
    screener_condition_names = Column(Text, nullable=True)  # (legacy) 조건식명 — 실매매 유니버스는 거래대금순
    screener_verify_condition_names = Column(Text, nullable=True)  # 레거시 검증 전용(주문 없음)

    # ===== 스크리너 상품 종류 (종목선정 모드) =====
    include_leverage = Column(Boolean, nullable=False, default=True)        # 레버리지(+2X) 포함
    include_inverse = Column(Boolean, nullable=False, default=True)         # 인버스(-1X) 포함
    include_double_inverse = Column(Boolean, nullable=False, default=False) # 곱버스(-2X) 포함

    # ===== 매수 조건 =====
    buy_below_price = Column(Integer, nullable=True)        # 현재가 이하일 때 매수
    min_change_rate_buy = Column(Float, nullable=True)      # 전일대비 최소 상승률(%) 이상일 때 매수

    # ===== 청산 규칙 (고급) =====
    trailing_stop_pct = Column(Float, nullable=True)        # 트레일링 스탑 (고점 대비 %)
    atr_mult_stop = Column(Float, nullable=True)            # ATR 손절 배수
    atr_mult_trail = Column(Float, nullable=True)           # ATR 트레일링 배수
    atr_period = Column(Integer, nullable=True, default=14) # ATR 기간
    profit_lock_trigger = Column(Float, nullable=True)      # 수익 잠금 트리거(%)
    profit_lock_floor = Column(Float, nullable=True)        # 수익 잠금 바닥(%)

    # ===== 진입 타이밍 게이트 =====
    use_entry_gate = Column(Boolean, nullable=False, default=False)
    require_above_open = Column(Boolean, nullable=False, default=False)   # 현재가 >= 시가 (당일 양봉)
    require_above_vwap = Column(Boolean, nullable=False, default=False)   # 현재가 >= 장중 VWAP
    day_position_min = Column(Float, nullable=True)        # 당일 위치 하한 (0~1)
    day_position_max = Column(Float, nullable=True)        # 당일 위치 상한 (0~1)
    volume_ratio_min = Column(Float, nullable=True)        # 전일대비 거래량비율(%) 하한
    # 레거시 진입: 일봉 RSI(14) 밴드 (None=미적용). 예: max=75 → 과열 매수 차단
    legacy_rsi_min = Column(Float, nullable=True)
    legacy_rsi_max = Column(Float, nullable=True)
    legacy_max_slots = Column(Integer, nullable=False, default=4)
    use_legacy = Column(Boolean, nullable=False, default=True)  # 레거시(거래대금) 전략 ON/OFF
    # 레거시 청산: 1분봉 EMA 이탈 후 SOFT 분 경과 시 전량 청산
    legacy_ema_exit_enabled = Column(Boolean, nullable=False, default=True)
    legacy_ema_exit_period = Column(Integer, nullable=False, default=90)
    legacy_ema_exit_soft_min = Column(Integer, nullable=False, default=10)
    legacy_ema_exit_band_pct = Column(Float, nullable=False, default=1.0)

    # ===== 매수 사이징 =====
    sizing_method = Column(String(30), nullable=False, default="FIXED")  # FIXED, PYRAMIDING
    # WON=고정 금액(원) / DEPOSIT_PCT=예수금 대비 비중(%)
    buy_amount_unit = Column(String(20), nullable=False, default="WON")
    initial_min_amount = Column(Integer, nullable=True, default=2000000) # 초기 진입 최소 금액
    initial_max_amount = Column(Integer, nullable=True, default=5000000) # 초기 최대 / 종목당 상한
    initial_min_deposit_pct = Column(Float, nullable=True, default=None)  # 예수금 비중(%) — 강한 신호
    initial_max_deposit_pct = Column(Float, nullable=True, default=None)  # 예수금 비중(%) — 약한 신호
    signal_min_threshold = Column(Float, nullable=True, default=2)       # 신호(등락%) 최소금액 기준
    signal_max_threshold = Column(Float, nullable=True, default=10)      # 신호(등락%) 최대금액 기준
    add_buy_amount = Column(Integer, nullable=True, default=1000000)     # 추가매수 1회 금액
    add_buy_deposit_pct = Column(Float, nullable=True, default=None)     # 추가매수 예수금 비중(%)
    add_buy_trigger = Column(Float, nullable=True, default=0.7)          # 추가매수 트리거(스텝당 %)
    manual_avg_down_pct = Column(Float, nullable=False, default=50.0)    # 수동 물타기: 최초 매수금 대비 비율(%)
    max_concurrent_positions = Column(Integer, nullable=False, default=2)  # 최대 동시 보유 종목 (0=자동)
    cash_reserve_pct = Column(Float, nullable=False, default=10.0)         # 예수금 중 현금으로 남길 비율(%)
    max_daily_buys = Column(Integer, nullable=False, default=10)         # 1일 최대 매수 횟수
    daily_loss_limit = Column(Integer, nullable=True, default=-200000)   # 1일 손실 한도(원)
    daily_profit_target = Column(Integer, nullable=True, default=150000) # 1일 이익 목표(원)
    # ===== 장세 악화 시 전략별 매수 제한 =====
    # 지수 등락률 ≤ market_risk_change_pct 이면 '나쁨'
    # → 체크된 전략은 금일 신규매수를 전략당 N회로 제한 (0=전면차단)
    market_risk_enabled = Column(Boolean, nullable=False, default=False)
    market_risk_index = Column(String(20), nullable=False, default="kospi")  # kospi|kosdaq|either|both|per_market
    market_risk_change_pct = Column(Float, nullable=False, default=-2.0)  # 예: -2.0 = 2% 이상 하락
    market_risk_max_buys_per_strategy = Column(Integer, nullable=False, default=2)
    market_risk_block_legacy = Column(Boolean, nullable=False, default=True)
    market_risk_block_sangtta = Column(Boolean, nullable=False, default=True)
    market_risk_block_breakout = Column(Boolean, nullable=False, default=False)
    market_risk_block_ymgp = Column(Boolean, nullable=False, default=False)
    market_risk_block_jongga = Column(Boolean, nullable=False, default=False)
    # 급락장(기본 ≤-1.5%)에서 당일고점 눌림이 지수 하락과 거의 같으면 매수 차단
    crash_sync_block_enabled = Column(Boolean, nullable=False, default=True)
    crash_sync_index_pct = Column(Float, nullable=False, default=-1.5)
    crash_sync_error_pct = Column(Float, nullable=False, default=0.5)  # |눌림%p − |지수|| 허용 오차
    crash_sync_pullback_cap_pct = Column(Float, nullable=False, default=2.0)  # 고점대비 눌림 상한(%p), 0=미적용
    # ===== 급등장 시 전략별 매수 제한 =====
    # 지수 등락률 ≥ market_surge_change_pct 이면 '급등' (다음날 낙폭 리스크)
    # → 체크된 전략은 금일 신규매수를 전략당 N회로 제한 (0=전면차단)
    market_surge_enabled = Column(Boolean, nullable=False, default=True)
    market_surge_index = Column(String(20), nullable=False, default="either")  # kospi|kosdaq|either|both|per_market
    market_surge_change_pct = Column(Float, nullable=False, default=3.0)  # 예: 3.0 = 3% 이상 상승
    market_surge_max_buys_per_strategy = Column(Integer, nullable=False, default=0)
    market_surge_block_legacy = Column(Boolean, nullable=False, default=True)
    market_surge_block_sangtta = Column(Boolean, nullable=False, default=True)
    market_surge_block_breakout = Column(Boolean, nullable=False, default=True)
    market_surge_block_ymgp = Column(Boolean, nullable=False, default=True)
    market_surge_block_jongga = Column(Boolean, nullable=False, default=True)
    market_surge_block_fractal = Column(Boolean, nullable=False, default=True)
    market_surge_block_ma1592 = Column(Boolean, nullable=False, default=True)
    reorder_cooldown_sec = Column(Integer, nullable=False, default=300)  # 재주문 콜다운(초)
    trade_start_time = Column(String(5), nullable=False, default="10:00")  # 레거시 매매 시작
    trade_end_time = Column(String(5), nullable=False, default="15:20")    # 레거시 매매 종료
    scan_interval_sec = Column(Integer, nullable=False, default=60)       # 스캐너·매수 폴링 주기(초)

    # ===== 장 시작 자동 기동 =====
    auto_start_at_open = Column(Boolean, nullable=False, default=True)   # 평일 장 시작 시 엔진 자동 ON
    auto_start_time = Column(String(5), nullable=False, default="08:00")  # 엔진 자동 시작 시각
    # ===== 상따(Sangtta) 전용 설정 (멀티게이트) =====
    use_sangtta = Column(Boolean, nullable=False, default=True)  # 상따 전략 ON/OFF
    sangtta_condition_names = Column(Text, nullable=True)  # (legacy) 조건식명 — 실매매 유니버스는 ka10027
    sangtta_verify_condition_names = Column(Text, nullable=True)  # 검증 전용(주문 없음)
    sangtta_max_slots = Column(Integer, nullable=False, default=2)  # 상따 동시 쿼터
    sangtta_buy_amount = Column(Integer, nullable=False, default=500000)  # 상따 1회 매수 금액 (소액)
    sangtta_buy_deposit_pct = Column(Float, nullable=True, default=None)  # 상따 예수금 비중(%)
    sangtta_trade_start_time = Column(String(5), nullable=False, default="09:05")
    sangtta_trade_end_time = Column(String(5), nullable=False, default="11:00")
    sangtta_change_min = Column(Float, nullable=False, default=12.0)  # 진입 등락 밴드 하한(%)
    sangtta_change_max = Column(Float, nullable=False, default=15.0)  # 진입 등락 밴드 상한(%)

    # ===== 과매도 돌파(Breakout) 전용 설정 =====
    use_breakout = Column(Boolean, nullable=False, default=False)
    breakout_condition_names = Column(Text, nullable=True)
    breakout_verify_condition_names = Column(Text, nullable=True)  # 검증 전용(주문 없음)
    breakout_max_slots = Column(Integer, nullable=False, default=1)
    breakout_buy_amount = Column(Integer, nullable=False, default=1000000)
    breakout_buy_deposit_pct = Column(Float, nullable=True, default=None)  # 돌파 예수금 비중(%)
    breakout_trade_start_time = Column(String(5), nullable=False, default="11:00")
    breakout_trade_end_time = Column(String(5), nullable=False, default="14:30")
    breakout_level_mode = Column(String(20), nullable=False, default="prev_high")  # prev_high=직전5분고, n_day_high=최근N봉고
    breakout_n_day = Column(Integer, nullable=False, default=10)  # 5분봉 N개 (레벨·거래량 평균)
    breakout_vol_mult = Column(Float, nullable=False, default=1.5)
    breakout_body_pct = Column(Float, nullable=False, default=2.0)  # 장대 몸통% (0=비활성)
    breakout_range_mult = Column(Float, nullable=False, default=0.0)  # 범위확장배수 (0=비활성)
    # MA20 필터: True면 확인봉 종가가 MA20 조건(mode)을 만족해야 매수
    breakout_require_ma20_cross = Column(Boolean, nullable=False, default=True)
    # above=종가>MA20 / cross=아래에서 상향 돌파(classic·봉중·reclaim)
    breakout_ma20_mode = Column(String(10), nullable=False, default="above")
    # MA20 유예(5분 완성봉 수). 돌파봉 포함 N봉 안에 MA20 상회하면 통과.
    # 예: 3 = 돌파봉 + 후속 2봉. 유예 중 장대·거래량은 돌파봉 충족분을 상속.
    # 대기 중에는 FAILED가 아니라 보류 재시도. 1이면 돌파봉에서 즉시 판정(유예 없음).
    breakout_ma20_grace_bars = Column(Integer, nullable=False, default=3)
    breakout_max_change_pct = Column(Float, nullable=False, default=12.0)
    breakout_stop_loss_pct = Column(Float, nullable=False, default=3.0)
    breakout_trailing_start_pct = Column(Float, nullable=False, default=10.0)
    breakout_trailing_pct = Column(Float, nullable=False, default=4.0)
    struct_break_soft_pct = Column(Float, nullable=False, default=1.0)
    struct_break_hard_pct = Column(Float, nullable=False, default=2.0)
    # 진입 확인: HARD=직전 5분봉 종가>레벨 즉시 / SOFT=레벨 위 연속 N스캔
    # HOLD=고가 돌파 후 다음봉 저가 유지 + RSI>임계
    breakout_entry_hard = Column(Boolean, nullable=False, default=True)
    breakout_entry_soft = Column(Boolean, nullable=False, default=True)
    breakout_entry_soft_polls = Column(Integer, nullable=False, default=3)
    breakout_entry_hold = Column(Boolean, nullable=False, default=True)
    breakout_hold_expire_bars = Column(Integer, nullable=False, default=3)
    breakout_hold_rsi_min = Column(Float, nullable=False, default=30.0)
    breakout_rsi_period = Column(Integer, nullable=False, default=10)
    # 프로그램 순매수(마지막 게이트): 5분 장대·거래량·MA20·진입확인 통과 종목만
    # ka90008 시간대(1분칸). 현재 분 제외, 최근 lookback칸 중 min_buy칸 이상 순매수(>0)
    breakout_require_program_net = Column(Boolean, nullable=False, default=True)  # 기본 ON
    breakout_program_lookback = Column(Integer, nullable=False, default=5)  # N=5칸
    breakout_program_min_buy = Column(Integer, nullable=False, default=3)  # M=3칸

    # ===== 역매공파(ymgp) 전용 설정 =====
    use_ymgp = Column(Boolean, nullable=False, default=False)
    ymgp_condition_names = Column(Text, nullable=True)
    ymgp_verify_condition_names = Column(Text, nullable=True)
    ymgp_max_slots = Column(Integer, nullable=False, default=1)
    ymgp_buy_amount_1 = Column(Integer, nullable=False, default=500000)
    ymgp_buy_amount_2 = Column(Integer, nullable=False, default=500000)
    ymgp_buy_deposit_pct_1 = Column(Float, nullable=True, default=None)
    ymgp_buy_deposit_pct_2 = Column(Float, nullable=True, default=None)
    ymgp_trade_start_time = Column(String(5), nullable=False, default="09:30")
    ymgp_trade_end_time = Column(String(5), nullable=False, default="14:30")
    ymgp_ma_fast = Column(Integer, nullable=False, default=120)
    ymgp_ma_mid = Column(Integer, nullable=False, default=240)
    ymgp_ma_slow = Column(Integer, nullable=False, default=480)
    ymgp_box_days = Column(Integer, nullable=False, default=15)
    ymgp_box_width_pct = Column(Float, nullable=False, default=15.5)
    ymgp_accum_vol_mult = Column(Float, nullable=False, default=2.0)
    ymgp_accum_body_pct = Column(Float, nullable=False, default=7.0)
    ymgp_accum_wick_vol_mult = Column(Float, nullable=False, default=4.0)
    ymgp_accum_wick_body_mult = Column(Float, nullable=False, default=1.5)
    ymgp_ma_near_pct = Column(Float, nullable=False, default=3.0)
    ymgp_pivot_tol_pct = Column(Float, nullable=False, default=2.0)
    ymgp_drop_lookback = Column(Integer, nullable=False, default=60)
    ymgp_drop_pct = Column(Float, nullable=False, default=-20.0)
    ymgp_stop_ma_mode = Column(String(20), nullable=False, default="ma60")
    ymgp_stop_loss_pct = Column(Float, nullable=False, default=4.0)
    ymgp_entry_mode = Column(String(20), nullable=False, default="ref_high")
    ymgp_max_change_pct = Column(Float, nullable=False, default=10.0)
    ymgp_pullback_tol_pct = Column(Float, nullable=False, default=2.0)
    ymgp_reentry_lock_days = Column(Integer, nullable=False, default=5)
    ymgp_tp1_pct_of_pos = Column(Float, nullable=False, default=0.35)
    ymgp_tp2_pct_of_pos = Column(Float, nullable=False, default=0.35)
    ymgp_enable_pullback_add = Column(Boolean, nullable=False, default=True)
    ymgp_enable_partial_tp = Column(Boolean, nullable=False, default=True)
    ymgp_trailing_start_pct = Column(Float, nullable=False, default=15.0)
    ymgp_trailing_pct = Column(Float, nullable=False, default=5.0)

    # ===== 종가배팅(jongga) 전용 설정 =====
    use_jongga = Column(Boolean, nullable=False, default=False)
    jongga_max_slots = Column(Integer, nullable=False, default=1)
    jongga_buy_amount = Column(Integer, nullable=False, default=1000000)
    jongga_buy_deposit_pct = Column(Float, nullable=True, default=None)
    jongga_trade_start_time = Column(String(5), nullable=False, default="14:30")
    jongga_pick_end_time = Column(String(5), nullable=False, default="14:40")
    jongga_trade_end_time = Column(String(5), nullable=False, default="14:40")
    jongga_rank_limit = Column(Integer, nullable=False, default=50)
    jongga_stop_loss_pct = Column(Float, nullable=False, default=3.0)
    jongga_trailing_start_pct = Column(Float, nullable=False, default=5.0)
    jongga_trailing_pct = Column(Float, nullable=False, default=2.0)
    jongga_w_pullback = Column(Float, nullable=False, default=1.0)
    jongga_w_amount = Column(Float, nullable=False, default=1.0)
    jongga_w_change = Column(Float, nullable=False, default=1.0)
    # 돼지물량 반응형 분할 (20/30/50) — 2차는 평단 대비 물타기
    jongga_pig_split = Column(Boolean, nullable=False, default=True)
    jongga_leg1_pct = Column(Float, nullable=False, default=20.0)
    jongga_leg2_pct = Column(Float, nullable=False, default=30.0)
    jongga_leg3_pct = Column(Float, nullable=False, default=50.0)
    jongga_leg2_start_time = Column(String(5), nullable=False, default="14:50")
    jongga_leg3_start_time = Column(String(5), nullable=False, default="15:20")
    jongga_leg3_end_time = Column(String(5), nullable=False, default="15:28")
    jongga_avg_down_pct = Column(Float, nullable=False, default=2.0)  # 2차 물타기: 평단 대비 −N%
    jongga_pig_bid_ask_ratio = Column(Float, nullable=False, default=1.5)
    jongga_pig_levels = Column(Integer, nullable=False, default=5)

    # ===== 프랙탈 스캘핑 (1분 EMA + Williams Fractal) =====
    use_fractal = Column(Boolean, nullable=False, default=False)
    fractal_condition_names = Column(Text, nullable=True)
    fractal_max_slots = Column(Integer, nullable=False, default=1)
    fractal_watch_slots = Column(Integer, nullable=False, default=5)
    fractal_trade_start_time = Column(String(5), nullable=False, default="09:20")
    fractal_trade_end_time = Column(String(5), nullable=False, default="14:50")
    fractal_risk_pct = Column(Float, nullable=False, default=0.5)
    fractal_qty_cap = Column(Integer, nullable=False, default=0)
    fractal_max_amount = Column(Integer, nullable=False, default=0)  # 프랙탈 전략 금액 상한(원). 0=미적용
    fractal_rr = Column(Float, nullable=False, default=1.5)
    fractal_stop_ema = Column(Integer, nullable=False, default=50)
    fractal_stop_tick_buffer = Column(Integer, nullable=False, default=1)
    fractal_watching_timeout_min = Column(Integer, nullable=False, default=15)
    fractal_liquidate_before_close = Column(Boolean, nullable=False, default=True)
    fractal_liquidate_time = Column(String(5), nullable=False, default="15:10")
    fractal_invalidation_100ema = Column(Boolean, nullable=False, default=False)
    market_risk_block_fractal = Column(Boolean, nullable=False, default=True)

    # ===== MA1592 (15/92 홀드) =====
    use_ma1592 = Column(Boolean, nullable=False, default=False)
    ma1592_condition_names = Column(Text, nullable=True)  # HTS 조건식명, 예: 1592매매
    ma1592_max_slots = Column(Integer, nullable=False, default=2)
    ma1592_l1_limit = Column(Integer, nullable=False, default=10)
    ma1592_ma_source = Column(String(20), nullable=False, default="bar")
    ma1592_require_ma_slope_up = Column(Boolean, nullable=False, default=True)
    ma1592_min_trading_value = Column(Integer, nullable=False, default=5_000_000_000)
    ma1592_hold_bars = Column(Integer, nullable=False, default=6)
    ma1592_break_before_entry_pct = Column(Float, nullable=False, default=0.4)
    ma1592_touch_buffer_pct = Column(Float, nullable=False, default=0.15)
    ma1592_require_bullish_candle = Column(Boolean, nullable=False, default=True)
    ma1592_prev_high_lookback_days = Column(Integer, nullable=False, default=20)
    ma1592_tp1_frac = Column(Float, nullable=False, default=0.5)
    ma1592_take_profit_pct = Column(Float, nullable=False, default=4.0)
    ma1592_stop_pct = Column(Float, nullable=False, default=4.0)
    ma1592_hard_break_pct = Column(Float, nullable=False, default=1.0)
    ma1592_large_break_pct = Column(Float, nullable=False, default=0.7)
    ma1592_impulse_min_pct = Column(Float, nullable=False, default=2.0)
    ma1592_crash_pct = Column(Float, nullable=False, default=1.8)
    ma1592_crash_bars = Column(Integer, nullable=False, default=3)
    ma1592_setup_expire_days = Column(Integer, nullable=False, default=8)
    ma1592_max_hold_days = Column(Integer, nullable=False, default=10)
    ma1592_flatten_eod = Column(Boolean, nullable=False, default=True)
    ma1592_risk_per_trade_pct = Column(Float, nullable=False, default=2.0)
    ma1592_max_invest_amount = Column(Integer, nullable=False, default=0)
    ma1592_trade_start_time = Column(String(5), nullable=False, default="09:10")
    ma1592_trade_end_time = Column(String(5), nullable=False, default="15:15")
    ma1592_hold_mode = Column(String(32), nullable=False, default="scale_in_gc")
    ma1592_exec_tf = Column(String(8), nullable=False, default="3M")
    # gc_above(기본) | price_lead
    ma1592_entry_trigger = Column(String(32), nullable=False, default="gc_above")
    ma1592_price_lead_near_pct = Column(Float, nullable=False, default=1.5)
    ma1592_price_lead_far_pct = Column(Float, nullable=False, default=1.0)
    ma1592_ledger_purge_tf = Column(String(8), nullable=False, default="3M")
    ma1592_leg1_pct = Column(Float, nullable=False, default=15.0)
    ma1592_leg2_pct = Column(Float, nullable=False, default=35.0)
    ma1592_leg3_pct = Column(Float, nullable=False, default=50.0)
    ma1592_scale_gap_pct = Column(Float, nullable=False, default=1.0)
    ma1592_scale_hold_bars = Column(Integer, nullable=False, default=2)
    market_risk_block_ma1592 = Column(Boolean, nullable=False, default=True)

    # ===== 상따 청산/소방 규정 =====
    limit_break_soft_pct = Column(Float, nullable=True, default=2.0)   # 상한가 이탈 soft(%)
    limit_break_hard_pct = Column(Float, nullable=True, default=3.0)   # 상한가 이탈 hard(%)
    sharp_drop_soft_pct = Column(Float, nullable=True, default=3.0)    # 당일고 대비 soft(%)
    sharp_drop_hard_pct = Column(Float, nullable=True, default=5.0)    # 당일고 대비 hard(%)
    soft_confirm_polls = Column(Integer, nullable=False, default=3)    # SOFT 연속 확인 회수

    # ===== 장 마감 전 전량 청산 =====
    liquidate_before_close = Column(Boolean, nullable=False, default=True)
    liquidate_time = Column(String(5), nullable=False, default="15:10")
    # 당일 종가배팅은 별도. 나머지 오버나잇 유지 종목 수 (전략당 1개)
    overnight_keep_slots = Column(Integer, nullable=False, default=3)
    overnight_max_per_strategy = Column(Integer, nullable=False, default=1)

    # ===== 주문 방식 =====
    order_method = Column(String(10), nullable=False, default="MARKET")  # MARKET, LIMIT

    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        # 설정은 하나만 유지 (싱글톤)
        UniqueConstraint("id", name="uq_autotrade_settings_singleton"),
    )


class KrxHoliday(Base):
    """KRX 휴장일 — 자동매매·장 시간 판별에 사용 (DB 관리)."""
    __tablename__ = "krx_holidays"

    id = Column(Integer, primary_key=True, index=True)
    holiday_date = Column(Date, nullable=False, unique=True, index=True)
    name = Column(String(100), nullable=False, default="휴장")
    is_closed = Column(Boolean, nullable=False, default=True, index=True)
    source = Column(String(20), nullable=False, default="manual")  # seed, manual, env
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("holiday_date", name="uq_krx_holiday_date"),
    )


class WatchlistStock(Base):
    """관심종목 테이블 - 수기등록과 조건식 종목 구분"""
    __tablename__ = "watchlist_stocks"

    id = Column(Integer, primary_key=True, index=True)
    stock_code = Column(String(20), nullable=False, unique=True, index=True)
    stock_name = Column(String(100), nullable=False)
    added_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    notes = Column(String(255), nullable=True)  # 메모
    
    # 종목 등록 방식 구분
    source_type = Column(String(20), nullable=False, default="MANUAL", index=True)  # MANUAL, CONDITION
    condition_id = Column(Integer, nullable=True, index=True)  # 조건식 ID (조건식 종목인 경우)
    condition_name = Column(String(100), nullable=True)  # 조건식 이름 (조건식 종목인 경우)
    
    # 조건식 종목 관련 필드
    last_condition_check = Column(DateTime, nullable=True)  # 마지막 조건식 확인 시간
    condition_status = Column(String(20), nullable=True, default="ACTIVE")  # ACTIVE, REMOVED, EXPIRED

    __table_args__ = (
        UniqueConstraint("stock_code", name="uq_watchlist_stock_code"),
    )


class TradingStrategy(Base):
    """매매 전략 설정 테이블"""
    __tablename__ = "trading_strategies"

    id = Column(Integer, primary_key=True, index=True)
    strategy_name = Column(String(50), nullable=False, unique=True, index=True)
    strategy_type = Column(String(20), nullable=False, index=True)  # MOMENTUM, DISPARITY, BOLLINGER, RSI
    is_enabled = Column(Boolean, nullable=False, default=True, index=True)
    parameters = Column(JSON, nullable=True)  # 전략별 파라미터 (JSON 형태)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("strategy_name", name="uq_trading_strategy_name"),
    )


class StrategySignal(Base):
    """전략별 신호 히스토리 테이블"""
    __tablename__ = "strategy_signals"

    id = Column(Integer, primary_key=True, index=True)
    strategy_id = Column(Integer, nullable=False, index=True)
    stock_code = Column(String(20), nullable=False, index=True)
    stock_name = Column(String(100), nullable=False)
    signal_type = Column(String(10), nullable=False, index=True)  # BUY, SELL
    signal_value = Column(Float, nullable=True)  # 신호 값 (RSI 값, 이격도 등)
    detected_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    detected_date = Column(Date, nullable=False, index=True)  # 일자별 관리용
    status = Column(String(20), nullable=False, default="ACTIVE", index=True)  # ACTIVE, EXPIRED, EXECUTED
    additional_data = Column(JSON, nullable=True)  # 추가 데이터 (현재가, 이동평균 등)

    __table_args__ = (
        UniqueConstraint("strategy_id", "stock_code", "detected_at", name="uq_strategy_signal_unique"),
    )


class Position(Base):
    """매수 완료 후 포지션 추적 테이블"""
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, index=True)
    stock_code = Column(String(20), nullable=False, index=True)
    stock_name = Column(String(100), nullable=False)
    
    # 매수 정보
    buy_price = Column(Integer, nullable=False)  # 매수 단가
    buy_quantity = Column(Integer, nullable=False)  # 매수 수량 (실제 체결)
    order_quantity = Column(Integer, nullable=True)  # 주문 수량 (부분체결 추적)
    buy_amount = Column(Integer, nullable=False)  # 매수 금액
    buy_order_id = Column(String(50), nullable=True)  # 매수 주문 ID
    actual_buy_amount = Column(Integer, nullable=True)  # 실제 매입금액 (수수료 포함, 키움 pur_amt)
    
    # 손절/익절 설정
    stop_loss_rate = Column(Float, nullable=False, default=5.0)  # 손절 비율 (%)
    take_profit_rate = Column(Float, nullable=False, default=10.0)  # 익절 비율 (%)
    stop_loss_price = Column(Integer, nullable=True)  # 손절가
    take_profit_price = Column(Integer, nullable=True)  # 익절가
    
    # 상태 관리
    status = Column(String(20), nullable=False, default="HOLDING", index=True)  # HOLDING, STOP_LOSS, TAKE_PROFIT, MANUAL_SELL
    current_price = Column(Integer, nullable=True)  # 현재가
    current_profit_loss = Column(Integer, nullable=True)  # 현재 손익
    current_profit_loss_rate = Column(Float, nullable=True)  # 현재 손익률 (%)
    peak_price = Column(Integer, nullable=True)  # 진입 후 고점 (트레일링/수익잠금 추적용)
    trailing_armed = Column(Boolean, nullable=False, default=False)  # 트레일링 구간 진입(시작% 도달)
    trailing_floor_price = Column(Integer, nullable=True)  # 활성화 후 최소 매도가(익절 바닥)
    buy_atr = Column(Float, nullable=True)  # 매수 시점 ATR (일봉, 검증·청산 기준 스냅샷)
    buy_atr_period = Column(Integer, nullable=True)  # 스냅샷 계산 기간(일)
    
    # 시간 정보
    buy_time = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    sell_time = Column(DateTime, nullable=True)
    last_monitored = Column(DateTime, nullable=True)  # 마지막 모니터링 시간
    
    # 추가 정보
    condition_id = Column(Integer, nullable=True)  # 매수 신호가 발생한 조건식 ID
    signal_id = Column(Integer, nullable=True)  # 매수 신호 ID
    # 전략 키(예: "sangtta", "legacy") — 포지션별 전략 태그
    strategy_key = Column(String(50), nullable=True, index=True)
    breakout_level_kind = Column(String(20), nullable=True)
    breakout_level_price = Column(Integer, nullable=True)
    ymgp_ref_high = Column(Integer, nullable=True)
    ymgp_ref_low = Column(Integer, nullable=True)
    ymgp_ref_open = Column(Integer, nullable=True)
    ymgp_entry_leg = Column(Integer, nullable=True, default=1)
    ymgp_tp_stage = Column(Integer, nullable=True, default=0)
    
    __table_args__ = (
        Index("idx_position_status_stock", "status", "stock_code"),
        Index("idx_position_monitoring", "status", "last_monitored"),
    )


class SellOrder(Base):
    """매도 주문 테이블"""
    __tablename__ = "sell_orders"

    id = Column(Integer, primary_key=True, index=True)
    position_id = Column(Integer, nullable=False, index=True)
    stock_code = Column(String(20), nullable=False, index=True)
    stock_name = Column(String(100), nullable=False)
    
    # 매도 정보
    sell_price = Column(Integer, nullable=False)  # 매도 단가
    sell_quantity = Column(Integer, nullable=False)  # 매도 수량
    sell_amount = Column(Integer, nullable=False)  # 매도 금액
    sell_order_id = Column(String(50), nullable=True)  # 매도 주문 ID
    
    # 매도 사유
    sell_reason = Column(String(50), nullable=False, index=True)  # STOP_LOSS, TAKE_PROFIT, MANUAL, INDICATOR
    sell_reason_detail = Column(String(200), nullable=True)  # 매도 사유 상세
    
    # 손익 정보
    profit_loss = Column(Integer, nullable=True)  # 순손익 (수수료·거래세 차감 후)
    profit_loss_rate = Column(Float, nullable=True)  # 손익률 (%)
    trading_commission = Column(Integer, nullable=True)  # 키움 매수·매도 수수료 합계
    transaction_tax = Column(Integer, nullable=True)  # 키움 거래세
    
    # 상태 관리
    status = Column(String(20), nullable=False, default="PENDING", index=True)  # PENDING, ORDERED, COMPLETED, FAILED
    
    # 시간 정보
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    ordered_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    
    __table_args__ = (
        Index("idx_sell_order_status", "status"),
        Index("idx_sell_order_reason", "sell_reason"),
    )


class AccountBalanceSnapshot(Base):
    """키움 계좌의 장마감 현금·자산 스냅샷."""
    __tablename__ = "account_balance_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    as_of_date = Column(Date, nullable=False, unique=True, index=True)
    deposit_d0 = Column(Integer, nullable=False)  # entr: 현재 예수금
    deposit_d2 = Column(Integer, nullable=False)  # d2_entra: D+2 추정예수금
    settlement_gap = Column(Integer, nullable=False)  # D+2 - D+0
    stock_evaluation = Column(Integer, nullable=True)  # tot_est_amt
    total_purchase = Column(Integer, nullable=True)  # tot_pur_amt
    asset_evaluation = Column(Integer, nullable=True)  # aset_evlt_amt
    estimated_deposit_asset = Column(Integer, nullable=True)  # prsm_dpst_aset_amt
    holding_count = Column(Integer, nullable=False, default=0)
    account_type = Column(String(20), nullable=True)
    data_source = Column(String(30), nullable=False, default="kt00004")
    synced_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class PositionBuyFill(Base):
    """포지션별 매수 체결 이력 (초기·피라미딩 추가매수)"""
    __tablename__ = "position_buy_fills"

    id = Column(Integer, primary_key=True, index=True)
    position_id = Column(Integer, nullable=False, index=True)
    stock_code = Column(String(20), nullable=False, index=True)
    stock_name = Column(String(100), nullable=False)
    fill_type = Column(String(20), nullable=False, default="INITIAL")  # INITIAL, ADD
    signal_id = Column(Integer, nullable=True)
    order_id = Column(String(50), nullable=True)
    price = Column(Integer, nullable=False)
    quantity = Column(Integer, nullable=False)  # 실제 체결 수량
    order_quantity = Column(Integer, nullable=True)  # 주문 수량 (체결과 다를 수 있음)
    amount = Column(Integer, nullable=False)
    planned_amount = Column(Integer, nullable=True)
    change_rate = Column(Float, nullable=True)
    sizing_method = Column(String(30), nullable=True)
    filled_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    note = Column(String(255), nullable=True)
    condition_checks = Column(JSON, nullable=True)

    __table_args__ = (
        Index("idx_buy_fill_position", "position_id", "filled_at"),
    )


class FundamentalSnapshot(Base):
    """기본적분석 마트 — 네이버 시가총액 페이지 일별 스냅샷 (오프라인 배치)."""
    __tablename__ = "fundamental_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    stock_code = Column(String(20), nullable=False, index=True)
    stock_name = Column(String(100), nullable=False)
    as_of_date = Column(Date, nullable=False, index=True)
    market = Column(String(10), nullable=True, index=True)  # KOSPI, KOSDAQ

    current_price = Column(Integer, nullable=True)
    market_cap = Column(Float, nullable=True)       # 시가총액 (억원)
    volume = Column(Integer, nullable=True)
    per = Column(Float, nullable=True)
    pbr = Column(Float, nullable=True)
    roe = Column(Float, nullable=True)
    eps = Column(Float, nullable=True)
    dividend_per_share = Column(Float, nullable=True)  # 보통주배당금 (원)
    listed_shares = Column(Float, nullable=True)       # 상장주식수 (주)
    foreign_ratio = Column(Float, nullable=True)     # 외국인비율 (%)
    trading_value = Column(Float, nullable=True)     # 거래대금 (백만)
    total_assets = Column(Float, nullable=True)    # 자산총계 (억원)
    total_debt = Column(Float, nullable=True)      # 부채총계 (억원)
    revenue = Column(Float, nullable=True)         # 매출액 (억원)
    operating_profit = Column(Float, nullable=True)  # 영업이익 (억원)

    source = Column(String(20), nullable=False, default="naver")
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("stock_code", "as_of_date", name="uq_fundamental_code_date"),
        Index("idx_fundamental_as_of_market", "as_of_date", "market"),
    )


class TechnicalSnapshot(Base):
    """기술적분석 마트 — 일봉 기반 MVP 스냅샷."""
    __tablename__ = "technical_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    stock_code = Column(String(20), nullable=False, index=True)
    stock_name = Column(String(100), nullable=False)
    as_of_date = Column(Date, nullable=False, index=True)
    market = Column(String(10), nullable=True, index=True)  # KOSPI, KOSDAQ
    timeframe = Column(String(10), nullable=False, default="1D", index=True)

    open_price = Column(Integer, nullable=True)
    high_price = Column(Integer, nullable=True)
    low_price = Column(Integer, nullable=True)
    close_price = Column(Integer, nullable=True)
    volume = Column(Integer, nullable=True)
    trading_value = Column(Float, nullable=True)  # 원 기준 추정치 (close * volume)

    return_1d = Column(Float, nullable=True)
    return_5d = Column(Float, nullable=True)
    return_20d = Column(Float, nullable=True)

    ma5 = Column(Float, nullable=True)
    ma20 = Column(Float, nullable=True)
    ma60 = Column(Float, nullable=True)
    ma120 = Column(Float, nullable=True)
    ma5_bias = Column(Float, nullable=True)
    ma20_bias = Column(Float, nullable=True)

    rsi14 = Column(Float, nullable=True)
    atr14 = Column(Float, nullable=True)
    atr14_pct = Column(Float, nullable=True)

    high_20d = Column(Float, nullable=True)
    low_20d = Column(Float, nullable=True)
    pos_20d = Column(Float, nullable=True)  # 20일 범위 내 위치(0~1)
    avg_volume_20d = Column(Float, nullable=True)
    avg_trading_value_20d = Column(Float, nullable=True)

    source = Column(String(20), nullable=False, default="kiwoom")
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("stock_code", "as_of_date", "timeframe", name="uq_technical_code_date_tf"),
        Index("idx_technical_as_of_market_tf", "as_of_date", "market", "timeframe"),
    )


class ConditionWatchlistSync(Base):
    """조건식 관심종목 동기화 테이블"""
    __tablename__ = "condition_watchlist_sync"

    id = Column(Integer, primary_key=True, index=True)
    condition_id = Column(Integer, nullable=False, index=True)
    condition_name = Column(String(100), nullable=False)
    stock_code = Column(String(20), nullable=False, index=True)
    stock_name = Column(String(100), nullable=False)
    sync_status = Column(String(20), nullable=False, default="ACTIVE", index=True)  # ACTIVE, REMOVED, EXPIRED
    last_sync_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    added_to_watchlist = Column(Boolean, nullable=False, default=False, index=True)
    
    # 조건식 종목 정보
    current_price = Column(Integer, nullable=True)
    change_rate = Column(Float, nullable=True)
    volume = Column(Integer, nullable=True)
    
    __table_args__ = (
        UniqueConstraint("condition_id", "stock_code", name="uq_condition_stock_unique"),
    )


class ThemeTag(Base):
    """테마/키워드 매핑용 태그."""
    __tablename__ = "theme_tags"

    id = Column(Integer, primary_key=True, index=True)
    tag_key = Column(String(120), nullable=False, unique=True, index=True)
    name_ko = Column(String(120), nullable=False, index=True)
    tag_type = Column(String(30), nullable=False, default="theme", index=True)  # theme, keyword, sector, manual
    source = Column(String(30), nullable=False, default="naver_theme")
    meta_json = Column(JSON, nullable=True)  # naver_theme_no, valid_from, valid_to
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("tag_key", name="uq_theme_tag_key"),
        Index("idx_theme_tag_type_name", "tag_type", "name_ko"),
    )


class ThemeTagEdge(Base):
    """종목 ↔ 태그 매핑(다대다)."""
    __tablename__ = "theme_tag_edges"

    id = Column(Integer, primary_key=True, index=True)
    stock_code = Column(String(20), nullable=False, index=True)
    stock_name = Column(String(100), nullable=False)
    tag_id = Column(Integer, nullable=False, index=True)
    source = Column(String(30), nullable=False, default="naver_theme", index=True)
    role = Column(String(20), nullable=True, default="member")
    weight = Column(Float, nullable=False, default=1.0)
    biz_date = Column(Date, nullable=True, index=True)
    rank = Column(Integer, nullable=True)
    inclusion_flag = Column(Boolean, nullable=False, default=True)
    reason_text = Column(String(500), nullable=True)
    observed_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    meta_json = Column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("stock_code", "tag_id", "source", "observed_at", name="uq_theme_edge_snapshot"),
        Index("idx_theme_edge_tag_stock", "tag_id", "stock_code"),
    )


class TagArticle(Base):
    """종목별(또는 키워드/태그 기반) 네이버 뉴스 기사 저장소."""
    __tablename__ = "tag_articles"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String(30), nullable=False, default="naver_news", index=True)

    # 수집 시점/기준일(오늘 키워드 정규화용)
    biz_date = Column(Date, nullable=False, index=True)
    collected_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    # 기사 메타
    title = Column(String(1000), nullable=False, index=True)
    url = Column(String(2000), nullable=False, unique=True, index=True)
    published_at = Column(DateTime, nullable=True, index=True)

    # 종목별 검색 쿼리 기반 연결(종목-기사 direct mapping)
    stock_code = Column(String(20), nullable=True, index=True)
    stock_name = Column(String(100), nullable=True)

    meta_json = Column(JSON, nullable=True)


class TagArticleKeywordEdge(Base):
    """기사(타이틀) -> 키워드(news_keyword) 태그 연결 edge."""
    __tablename__ = "tag_article_keyword_edges"

    id = Column(Integer, primary_key=True, index=True)
    article_id = Column(Integer, nullable=False, index=True)
    tag_id = Column(Integer, nullable=False, index=True)
    source = Column(String(30), nullable=False, default="news_title", index=True)
    weight = Column(Float, nullable=False, default=1.0)
    observed_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    meta_json = Column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("article_id", "tag_id", "source", "observed_at", name="uq_article_kw_snapshot"),
        Index("idx_article_kw_tag_obs", "tag_id", "observed_at"),
    )


class KeywordDailyStat(Base):
    """오늘의 키워드 집계."""
    __tablename__ = "keyword_daily_stats"

    id = Column(Integer, primary_key=True, index=True)
    keyword = Column(String(100), nullable=False, index=True)
    biz_date = Column(Date, nullable=False, index=True)
    mention_count = Column(Integer, nullable=False, default=0)
    stock_count = Column(Integer, nullable=False, default=0)
    delta_vs_prev = Column(Integer, nullable=False, default=0)
    trend_label = Column(String(20), nullable=False, default="flat")  # new, up, flat, down
    source = Column(String(30), nullable=False, default="theme_name")
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("keyword", "biz_date", name="uq_keyword_daily"),
        Index("idx_keyword_daily_date_count", "biz_date", "mention_count"),
    )


class ThemeEvidence(Base):
    """종목-테마 연관 근거 (정적·뉴스·시장동조 등)."""
    __tablename__ = "theme_evidence"

    id = Column(Integer, primary_key=True, index=True)
    biz_date = Column(Date, nullable=False, index=True)
    stock_code = Column(String(20), nullable=False, index=True)
    tag_id = Column(Integer, nullable=False, index=True)
    evidence_type = Column(String(30), nullable=False, index=True)  # static, news, comove, supply
    evidence_score = Column(Float, nullable=False, default=0.0)
    raw_ref_type = Column(String(30), nullable=True)  # edge, article, batch
    raw_ref_id = Column(Integer, nullable=True)
    meta_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint(
            "biz_date", "stock_code", "tag_id", "evidence_type",
            name="uq_theme_evidence_daily",
        ),
        Index("idx_theme_evidence_tag_date", "tag_id", "biz_date"),
    )


class ThemeScoreDaily(Base):
    """종목×테마 일별 연관도 점수."""
    __tablename__ = "theme_score_daily"

    id = Column(Integer, primary_key=True, index=True)
    biz_date = Column(Date, nullable=False, index=True)
    stock_code = Column(String(20), nullable=False, index=True)
    tag_id = Column(Integer, nullable=False, index=True)
    static_score = Column(Float, nullable=False, default=0.0)
    news_score = Column(Float, nullable=False, default=0.0)
    market_score = Column(Float, nullable=False, default=0.0)
    supply_score = Column(Float, nullable=False, default=0.0)
    final_score = Column(Float, nullable=False, default=0.0)
    tier = Column(String(20), nullable=False, default="none")  # core, related, event, none
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("biz_date", "stock_code", "tag_id", name="uq_theme_score_daily"),
        Index("idx_theme_score_stock_date", "stock_code", "biz_date", "final_score"),
    )


class MtiHsMap(Base):
    """HS ↔ MTI 연계 (버전 고정). v1에서는 수동 바스켓과 병행."""
    __tablename__ = "mti_hs_map"

    id = Column(Integer, primary_key=True, index=True)
    mti_code = Column(String(20), nullable=False, index=True)
    mti_name = Column(String(100), nullable=True)
    hs_code = Column(String(12), nullable=False, index=True)
    mti_version = Column(String(20), nullable=False, default="manual_v1")
    effective_from = Column(String(10), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("mti_code", "hs_code", "mti_version", name="uq_mti_hs_map"),
    )


class TagMtiMap(Base):
    """stocke 테마 태그 ↔ MTI(또는 수동 바스켓 키)."""
    __tablename__ = "tag_mti_map"

    id = Column(Integer, primary_key=True, index=True)
    tag_name = Column(String(80), nullable=False, index=True)
    mti_code = Column(String(20), nullable=False, index=True)
    weight = Column(Float, nullable=False, default=1.0)
    note = Column(String(200), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("tag_name", "mti_code", name="uq_tag_mti_map"),
    )


class TradeHsMonthly(Base):
    """관심 HS 월별 원시 수출입 (재집계용)."""
    __tablename__ = "trade_hs_monthly"

    id = Column(Integer, primary_key=True, index=True)
    period_yyyymm = Column(String(6), nullable=False, index=True)
    hs_code = Column(String(12), nullable=False, index=True)
    exp_usd = Column(Float, nullable=False, default=0.0)
    imp_usd = Column(Float, nullable=False, default=0.0)
    exp_wgt = Column(Float, nullable=True)
    imp_wgt = Column(Float, nullable=True)
    source = Column(String(40), nullable=False, default="data.go.kr")
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("period_yyyymm", "hs_code", "source", name="uq_trade_hs_monthly"),
        Index("idx_trade_hs_period", "period_yyyymm", "hs_code"),
    )


class TradeIndustryMonthly(Base):
    """업종(MTI)/태그 월별 수출입 집계."""
    __tablename__ = "trade_industry_monthly"

    id = Column(Integer, primary_key=True, index=True)
    period_yyyymm = Column(String(6), nullable=False, index=True)
    grain = Column(String(10), nullable=False, index=True)  # mti | tag
    grain_key = Column(String(80), nullable=False, index=True)
    exp_usd = Column(Float, nullable=False, default=0.0)
    imp_usd = Column(Float, nullable=False, default=0.0)
    exp_yoy = Column(Float, nullable=True)
    imp_yoy = Column(Float, nullable=True)
    exp_mom = Column(Float, nullable=True)
    imp_mom = Column(Float, nullable=True)
    source = Column(String(40), nullable=False, default="data.go.kr")
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    meta_json = Column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("period_yyyymm", "grain", "grain_key", name="uq_trade_industry_monthly"),
        Index("idx_trade_ind_grain_period", "grain", "grain_key", "period_yyyymm"),
    )


def get_db() -> Generator[Session, None, None]:
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    try:
        from utils.krx_holiday_store import seed_default_holidays
        seed_default_holidays()
    except Exception as seed_err:
        print(f"KRX holiday seed warning: {seed_err}")
    # 간단한 마이그레이션: 컬럼이 없으면 추가 (SQLite 전용)
    try:
        with engine.connect() as conn:
            # pending_buy_signals 테이블 마이그레이션
            result = conn.execute(text("PRAGMA table_info('pending_buy_signals')"))
            columns = {row[1] for row in result}
            if 'failure_reason' not in columns:
                conn.execute(text("ALTER TABLE pending_buy_signals ADD COLUMN failure_reason VARCHAR(255)"))
                conn.commit()
            if 'additional_data' not in columns:
                conn.execute(text("ALTER TABLE pending_buy_signals ADD COLUMN additional_data TEXT"))
                conn.commit()

            # sell_orders 실현손익 비용 마이그레이션
            result = conn.execute(text("PRAGMA table_info('sell_orders')"))
            sell_columns = {row[1] for row in result}
            if sell_columns and 'trading_commission' not in sell_columns:
                conn.execute(text("ALTER TABLE sell_orders ADD COLUMN trading_commission INTEGER"))
                conn.commit()
            if sell_columns and 'transaction_tax' not in sell_columns:
                conn.execute(text("ALTER TABLE sell_orders ADD COLUMN transaction_tax INTEGER"))
                conn.commit()

            # 체결 이력 없는 포지션 backfill (create_all이 테이블은 이미 보장)
            conn.execute(text("""
                INSERT INTO position_buy_fills (
                    position_id, stock_code, stock_name, fill_type, signal_id, order_id,
                    price, quantity, amount, filled_at, note
                )
                SELECT
                    id, stock_code, stock_name, 'INITIAL', signal_id, buy_order_id,
                    buy_price, buy_quantity, buy_amount, buy_time,
                    '마이그레이션 추정 (체결 이력 도입 이전)'
                FROM positions
                WHERE id NOT IN (SELECT DISTINCT position_id FROM position_buy_fills WHERE position_id IS NOT NULL)
            """))
            conn.commit()

            result = conn.execute(text("PRAGMA table_info('position_buy_fills')"))
            pbf_cols = {row[1] for row in result}
            if pbf_cols and 'condition_checks' not in pbf_cols:
                conn.execute(text("ALTER TABLE position_buy_fills ADD COLUMN condition_checks TEXT"))
                conn.commit()
            if pbf_cols and 'order_quantity' not in pbf_cols:
                conn.execute(text("ALTER TABLE position_buy_fills ADD COLUMN order_quantity INTEGER"))
                conn.commit()
                conn.execute(text(
                    "UPDATE position_buy_fills SET order_quantity = quantity WHERE order_quantity IS NULL"
                ))
                conn.commit()
            
            # watchlist_stocks 테이블 마이그레이션
            result = conn.execute(text("PRAGMA table_info('watchlist_stocks')"))
            columns = {row[1] for row in result}
            
            # 새로운 컬럼들 추가
            new_columns = [
                ('source_type', 'VARCHAR(20) DEFAULT "MANUAL"'),
                ('condition_id', 'INTEGER'),
                ('condition_name', 'VARCHAR(100)'),
                ('last_condition_check', 'DATETIME'),
                ('condition_status', 'VARCHAR(20)')
            ]
            
            for col_name, col_def in new_columns:
                if col_name not in columns:
                    conn.execute(text(f"ALTER TABLE watchlist_stocks ADD COLUMN {col_name} {col_def}"))
                    conn.commit()

            # positions 테이블 마이그레이션 (고점 추적 컬럼)
            result = conn.execute(text("PRAGMA table_info('positions')"))
            pos_columns = {row[1] for row in result}
            if pos_columns and 'peak_price' not in pos_columns:
                conn.execute(text("ALTER TABLE positions ADD COLUMN peak_price INTEGER"))
                conn.commit()
            if pos_columns and 'trailing_armed' not in pos_columns:
                conn.execute(text("ALTER TABLE positions ADD COLUMN trailing_armed BOOLEAN DEFAULT 0"))
                conn.commit()
            if pos_columns and 'trailing_floor_price' not in pos_columns:
                conn.execute(text("ALTER TABLE positions ADD COLUMN trailing_floor_price INTEGER"))
                conn.commit()
            if pos_columns and 'order_quantity' not in pos_columns:
                conn.execute(text("ALTER TABLE positions ADD COLUMN order_quantity INTEGER"))
                conn.commit()
                conn.execute(text("""
                    UPDATE positions SET order_quantity = COALESCE(
                        (SELECT MAX(COALESCE(pbf.order_quantity, pbf.quantity))
                         FROM position_buy_fills pbf WHERE pbf.position_id = positions.id),
                        buy_quantity
                    ) WHERE order_quantity IS NULL
                """))
                conn.commit()
            if pos_columns and 'buy_atr' not in pos_columns:
                conn.execute(text("ALTER TABLE positions ADD COLUMN buy_atr FLOAT"))
                conn.commit()
            if pos_columns and 'buy_atr_period' not in pos_columns:
                conn.execute(text("ALTER TABLE positions ADD COLUMN buy_atr_period INTEGER"))
                conn.commit()
            if pos_columns and 'strategy_key' not in pos_columns:
                conn.execute(text("ALTER TABLE positions ADD COLUMN strategy_key VARCHAR(50)"))
                conn.commit()
                try:
                    conn.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_positions_strategy_key ON positions (strategy_key)"
                    ))
                    conn.commit()
                except Exception:
                    pass
            if pos_columns and 'breakout_level_kind' not in pos_columns:
                conn.execute(text("ALTER TABLE positions ADD COLUMN breakout_level_kind VARCHAR(20)"))
                conn.commit()
            if pos_columns and 'breakout_level_price' not in pos_columns:
                conn.execute(text("ALTER TABLE positions ADD COLUMN breakout_level_price INTEGER"))
                conn.commit()
            if pos_columns and 'ymgp_ref_high' not in pos_columns:
                conn.execute(text("ALTER TABLE positions ADD COLUMN ymgp_ref_high INTEGER"))
                conn.commit()
            if pos_columns and 'ymgp_ref_low' not in pos_columns:
                conn.execute(text("ALTER TABLE positions ADD COLUMN ymgp_ref_low INTEGER"))
                conn.commit()
            if pos_columns and 'ymgp_ref_open' not in pos_columns:
                conn.execute(text("ALTER TABLE positions ADD COLUMN ymgp_ref_open INTEGER"))
                conn.commit()
            if pos_columns and 'ymgp_entry_leg' not in pos_columns:
                conn.execute(text("ALTER TABLE positions ADD COLUMN ymgp_entry_leg INTEGER DEFAULT 1"))
                conn.commit()
            if pos_columns and 'ymgp_tp_stage' not in pos_columns:
                conn.execute(text("ALTER TABLE positions ADD COLUMN ymgp_tp_stage INTEGER DEFAULT 0"))
                conn.commit()

            # auto_trade_settings 테이블 마이그레이션 (고급 자동매매 설정 컬럼 추가)
            result = conn.execute(text("PRAGMA table_info('auto_trade_settings')"))
            ats_columns = {row[1] for row in result}
            ats_new_columns = [
                ('watchlist_codes', 'TEXT'),
                ('screener_condition_names', 'TEXT'),
                ('include_leverage', 'BOOLEAN DEFAULT 1'),
                ('include_inverse', 'BOOLEAN DEFAULT 1'),
                ('include_double_inverse', 'BOOLEAN DEFAULT 0'),
                ('buy_below_price', 'INTEGER'),
                ('min_change_rate_buy', 'FLOAT'),
                ('trailing_stop_pct', 'FLOAT'),
                ('atr_mult_stop', 'FLOAT'),
                ('atr_mult_trail', 'FLOAT'),
                ('atr_period', 'INTEGER DEFAULT 14'),
                ('profit_lock_trigger', 'FLOAT'),
                ('profit_lock_floor', 'FLOAT'),
                ('use_entry_gate', 'BOOLEAN DEFAULT 0'),
                ('require_above_open', 'BOOLEAN DEFAULT 0'),
                ('require_above_vwap', 'BOOLEAN DEFAULT 0'),
                ('day_position_min', 'FLOAT'),
                ('day_position_max', 'FLOAT'),
                ('volume_ratio_min', 'FLOAT'),
                ('sizing_method', 'VARCHAR(30) DEFAULT "FIXED"'),
                ('buy_amount_unit', 'VARCHAR(20) DEFAULT "WON"'),
                ('initial_min_amount', 'INTEGER DEFAULT 2000000'),
                ('initial_max_amount', 'INTEGER DEFAULT 5000000'),
                ('initial_min_deposit_pct', 'FLOAT'),
                ('initial_max_deposit_pct', 'FLOAT'),
                ('signal_min_threshold', 'FLOAT DEFAULT 2'),
                ('signal_max_threshold', 'FLOAT DEFAULT 10'),
                ('add_buy_amount', 'INTEGER DEFAULT 1000000'),
                ('add_buy_deposit_pct', 'FLOAT'),
                ('add_buy_trigger', 'FLOAT DEFAULT 0.7'),
                ('manual_avg_down_pct', 'FLOAT DEFAULT 50.0'),
                ('max_concurrent_positions', 'INTEGER DEFAULT 2'),
                ('cash_reserve_pct', 'FLOAT DEFAULT 10'),
                ('max_daily_buys', 'INTEGER DEFAULT 10'),
                ('daily_loss_limit', 'INTEGER DEFAULT -200000'),
                ('daily_profit_target', 'INTEGER DEFAULT 150000'),
                ('reorder_cooldown_sec', 'INTEGER DEFAULT 300'),
                ('trade_start_time', 'VARCHAR(5) DEFAULT "10:00"'),
                ('trade_end_time', 'VARCHAR(5) DEFAULT "15:20"'),
                ('scan_interval_sec', 'INTEGER DEFAULT 60'),
                ('auto_start_at_open', 'BOOLEAN DEFAULT 1'),
                ('auto_start_time', 'VARCHAR(5) DEFAULT "08:00"'),
                ('liquidate_before_close', 'BOOLEAN DEFAULT 1'),
                ('liquidate_time', 'VARCHAR(5) DEFAULT "15:10"'),
                ('overnight_keep_slots', 'INTEGER DEFAULT 3'),
                ('overnight_max_per_strategy', 'INTEGER DEFAULT 1'),
                ('order_method', 'VARCHAR(10) DEFAULT "MARKET"'),
                ('sangtta_condition_names', 'TEXT'),
                ('sangtta_verify_condition_names', 'TEXT'),
                ('sangtta_max_slots', 'INTEGER DEFAULT 2'),
                ('sangtta_buy_amount', 'INTEGER DEFAULT 500000'),
                ('sangtta_buy_deposit_pct', 'FLOAT'),
                ('sangtta_trade_start_time', 'VARCHAR(5) DEFAULT "09:05"'),
                ('sangtta_trade_end_time', 'VARCHAR(5) DEFAULT "11:00"'),
                ('sangtta_change_min', 'FLOAT DEFAULT 12.0'),
                ('sangtta_change_max', 'FLOAT DEFAULT 15.0'),
                ('limit_break_soft_pct', 'FLOAT DEFAULT 2.0'),
                ('limit_break_hard_pct', 'FLOAT DEFAULT 3.0'),
                ('sharp_drop_soft_pct', 'FLOAT DEFAULT 3.0'),
                ('sharp_drop_hard_pct', 'FLOAT DEFAULT 5.0'),
                ('soft_confirm_polls', 'INTEGER DEFAULT 3'),
                ('use_breakout', 'BOOLEAN DEFAULT 0'),
                ('breakout_condition_names', 'TEXT'),
                ('breakout_verify_condition_names', 'TEXT'),
                ('screener_verify_condition_names', 'TEXT'),
                ('breakout_max_slots', 'INTEGER DEFAULT 1'),
                ('breakout_buy_amount', 'INTEGER DEFAULT 1000000'),
                ('breakout_buy_deposit_pct', 'FLOAT'),
                ('breakout_trade_start_time', 'VARCHAR(5) DEFAULT "11:00"'),
                ('breakout_trade_end_time', 'VARCHAR(5) DEFAULT "14:30"'),
                ('breakout_level_mode', 'VARCHAR(20) DEFAULT "prev_high"'),
                ('breakout_n_day', 'INTEGER DEFAULT 10'),
                ('breakout_vol_mult', 'FLOAT DEFAULT 1.5'),
                ('breakout_body_pct', 'FLOAT DEFAULT 2.0'),
                ('breakout_range_mult', 'FLOAT DEFAULT 0.0'),
                ('breakout_require_ma20_cross', 'BOOLEAN DEFAULT 1'),
                ('breakout_ma20_mode', 'VARCHAR(10) DEFAULT "above"'),
                ('breakout_ma20_grace_bars', 'INTEGER DEFAULT 3'),
                ('breakout_max_change_pct', 'FLOAT DEFAULT 12.0'),
                ('breakout_stop_loss_pct', 'FLOAT DEFAULT 3.0'),
                ('breakout_trailing_start_pct', 'FLOAT DEFAULT 10.0'),
                ('breakout_trailing_pct', 'FLOAT DEFAULT 4.0'),
                ('struct_break_soft_pct', 'FLOAT DEFAULT 1.0'),
                ('struct_break_hard_pct', 'FLOAT DEFAULT 2.0'),
                ('breakout_entry_hard', 'BOOLEAN DEFAULT 1'),
                ('breakout_entry_soft', 'BOOLEAN DEFAULT 1'),
                ('breakout_entry_soft_polls', 'INTEGER DEFAULT 3'),
                ('breakout_entry_hold', 'BOOLEAN DEFAULT 1'),
                ('breakout_hold_expire_bars', 'INTEGER DEFAULT 3'),
                ('breakout_hold_rsi_min', 'FLOAT DEFAULT 30.0'),
                ('breakout_rsi_period', 'INTEGER DEFAULT 10'),
                ('breakout_require_program_net', 'BOOLEAN DEFAULT 1'),
                ('breakout_program_lookback', 'INTEGER DEFAULT 5'),
                ('breakout_program_min_buy', 'INTEGER DEFAULT 3'),
                ('use_ymgp', 'BOOLEAN DEFAULT 0'),
                ('ymgp_condition_names', 'TEXT'),
                ('ymgp_verify_condition_names', 'TEXT'),
                ('ymgp_max_slots', 'INTEGER DEFAULT 1'),
                ('ymgp_buy_amount_1', 'INTEGER DEFAULT 500000'),
                ('ymgp_buy_amount_2', 'INTEGER DEFAULT 500000'),
                ('ymgp_buy_deposit_pct_1', 'FLOAT'),
                ('ymgp_buy_deposit_pct_2', 'FLOAT'),
                ('ymgp_trade_start_time', 'VARCHAR(5) DEFAULT "09:30"'),
                ('ymgp_trade_end_time', 'VARCHAR(5) DEFAULT "14:30"'),
                ('ymgp_ma_fast', 'INTEGER DEFAULT 120'),
                ('ymgp_ma_mid', 'INTEGER DEFAULT 240'),
                ('ymgp_ma_slow', 'INTEGER DEFAULT 480'),
                ('ymgp_box_days', 'INTEGER DEFAULT 15'),
                ('ymgp_box_width_pct', 'FLOAT DEFAULT 15.5'),
                ('ymgp_accum_vol_mult', 'FLOAT DEFAULT 2.0'),
                ('ymgp_accum_body_pct', 'FLOAT DEFAULT 7.0'),
                ('ymgp_accum_wick_vol_mult', 'FLOAT DEFAULT 4.0'),
                ('ymgp_accum_wick_body_mult', 'FLOAT DEFAULT 1.5'),
                ('ymgp_ma_near_pct', 'FLOAT DEFAULT 3.0'),
                ('ymgp_pivot_tol_pct', 'FLOAT DEFAULT 2.0'),
                ('ymgp_drop_lookback', 'INTEGER DEFAULT 60'),
                ('ymgp_drop_pct', 'FLOAT DEFAULT -20.0'),
                ('ymgp_stop_ma_mode', 'VARCHAR(20) DEFAULT "ma60"'),
                ('ymgp_stop_loss_pct', 'FLOAT DEFAULT 4.0'),
                ('ymgp_entry_mode', 'VARCHAR(20) DEFAULT "ref_high"'),
                ('ymgp_max_change_pct', 'FLOAT DEFAULT 10.0'),
                ('ymgp_pullback_tol_pct', 'FLOAT DEFAULT 2.0'),
                ('ymgp_reentry_lock_days', 'INTEGER DEFAULT 5'),
                ('ymgp_tp1_pct_of_pos', 'FLOAT DEFAULT 0.35'),
                ('ymgp_tp2_pct_of_pos', 'FLOAT DEFAULT 0.35'),
                ('ymgp_enable_pullback_add', 'BOOLEAN DEFAULT 1'),
                ('ymgp_enable_partial_tp', 'BOOLEAN DEFAULT 1'),
                ('ymgp_trailing_start_pct', 'FLOAT DEFAULT 15.0'),
                ('ymgp_trailing_pct', 'FLOAT DEFAULT 5.0'),
                ('market_risk_enabled', 'BOOLEAN DEFAULT 0'),
                ('market_risk_index', 'VARCHAR(20) DEFAULT "kospi"'),
                ('market_risk_change_pct', 'FLOAT DEFAULT -2.0'),
                ('market_risk_max_buys_per_strategy', 'INTEGER DEFAULT 2'),
                ('market_risk_block_legacy', 'BOOLEAN DEFAULT 1'),
                ('market_risk_block_sangtta', 'BOOLEAN DEFAULT 1'),
                ('market_risk_block_breakout', 'BOOLEAN DEFAULT 0'),
                ('market_risk_block_ymgp', 'BOOLEAN DEFAULT 0'),
                ('market_risk_block_jongga', 'BOOLEAN DEFAULT 0'),
                ('legacy_rsi_min', 'FLOAT'),
                ('legacy_rsi_max', 'FLOAT'),
                ('legacy_max_slots', 'INTEGER DEFAULT 4'),
                ('use_legacy', 'BOOLEAN DEFAULT 1'),
                ('use_sangtta', 'BOOLEAN DEFAULT 1'),
                ('legacy_ema_exit_enabled', 'BOOLEAN DEFAULT 1'),
                ('legacy_ema_exit_period', 'INTEGER DEFAULT 90'),
                ('legacy_ema_exit_soft_min', 'INTEGER DEFAULT 10'),
                ('legacy_ema_exit_band_pct', 'FLOAT DEFAULT 1.0'),
                ('use_jongga', 'BOOLEAN DEFAULT 0'),
                ('jongga_max_slots', 'INTEGER DEFAULT 1'),
                ('jongga_buy_amount', 'INTEGER DEFAULT 1000000'),
                ('jongga_buy_deposit_pct', 'FLOAT'),
                ('jongga_trade_start_time', 'VARCHAR(5) DEFAULT "14:30"'),
                ('jongga_pick_end_time', 'VARCHAR(5) DEFAULT "14:40"'),
                ('jongga_trade_end_time', 'VARCHAR(5) DEFAULT "14:40"'),
                ('jongga_rank_limit', 'INTEGER DEFAULT 50'),
                ('jongga_stop_loss_pct', 'FLOAT DEFAULT 3.0'),
                ('jongga_trailing_start_pct', 'FLOAT DEFAULT 5.0'),
                ('jongga_trailing_pct', 'FLOAT DEFAULT 2.0'),
                ('jongga_w_pullback', 'FLOAT DEFAULT 1.0'),
                ('jongga_w_amount', 'FLOAT DEFAULT 1.0'),
                ('jongga_w_change', 'FLOAT DEFAULT 1.0'),
                ('jongga_pig_split', 'BOOLEAN DEFAULT 1'),
                ('jongga_leg1_pct', 'FLOAT DEFAULT 20.0'),
                ('jongga_leg2_pct', 'FLOAT DEFAULT 30.0'),
                ('jongga_leg3_pct', 'FLOAT DEFAULT 50.0'),
                ('jongga_leg2_start_time', 'VARCHAR(5) DEFAULT "14:50"'),
                ('jongga_leg3_start_time', 'VARCHAR(5) DEFAULT "15:20"'),
                ('jongga_leg3_end_time', 'VARCHAR(5) DEFAULT "15:28"'),
                ('jongga_avg_down_pct', 'FLOAT DEFAULT 2.0'),
                ('jongga_pig_bid_ask_ratio', 'FLOAT DEFAULT 1.5'),
                ('jongga_pig_levels', 'INTEGER DEFAULT 5'),
                ('use_fractal', 'BOOLEAN DEFAULT 0'),
                ('fractal_condition_names', 'TEXT'),
                ('fractal_max_slots', 'INTEGER DEFAULT 1'),
                ('fractal_watch_slots', 'INTEGER DEFAULT 5'),
                ('fractal_trade_start_time', 'VARCHAR(5) DEFAULT "09:20"'),
                ('fractal_trade_end_time', 'VARCHAR(5) DEFAULT "14:50"'),
                ('fractal_risk_pct', 'FLOAT DEFAULT 0.5'),
                ('fractal_qty_cap', 'INTEGER DEFAULT 0'),
                ('fractal_max_amount', 'INTEGER DEFAULT 0'),
                ('fractal_rr', 'FLOAT DEFAULT 1.5'),
                ('fractal_stop_ema', 'INTEGER DEFAULT 50'),
                ('fractal_stop_tick_buffer', 'INTEGER DEFAULT 1'),
                ('fractal_watching_timeout_min', 'INTEGER DEFAULT 15'),
                ('fractal_liquidate_before_close', 'BOOLEAN DEFAULT 1'),
                ('fractal_liquidate_time', 'VARCHAR(5) DEFAULT "15:10"'),
                ('fractal_invalidation_100ema', 'BOOLEAN DEFAULT 0'),
                ('market_risk_block_fractal', 'BOOLEAN DEFAULT 1'),
                ('crash_sync_block_enabled', 'BOOLEAN DEFAULT 1'),
                ('crash_sync_index_pct', 'FLOAT DEFAULT -1.5'),
                ('crash_sync_error_pct', 'FLOAT DEFAULT 0.5'),
                ('crash_sync_pullback_cap_pct', 'FLOAT DEFAULT 2.0'),
                ('market_surge_enabled', 'BOOLEAN DEFAULT 1'),
                ('market_surge_index', 'VARCHAR(20) DEFAULT "either"'),
                ('market_surge_change_pct', 'FLOAT DEFAULT 3.0'),
                ('market_surge_max_buys_per_strategy', 'INTEGER DEFAULT 0'),
                ('market_surge_block_legacy', 'BOOLEAN DEFAULT 1'),
                ('market_surge_block_sangtta', 'BOOLEAN DEFAULT 1'),
                ('market_surge_block_breakout', 'BOOLEAN DEFAULT 1'),
                ('market_surge_block_ymgp', 'BOOLEAN DEFAULT 1'),
                ('market_surge_block_jongga', 'BOOLEAN DEFAULT 1'),
                ('market_surge_block_fractal', 'BOOLEAN DEFAULT 1'),
                ('market_surge_block_ma1592', 'BOOLEAN DEFAULT 1'),
                ('use_ma1592', 'BOOLEAN DEFAULT 0'),
                ('ma1592_condition_names', 'TEXT'),
                ('ma1592_max_slots', 'INTEGER DEFAULT 2'),
                ('ma1592_l1_limit', 'INTEGER DEFAULT 10'),
                ('ma1592_ma_source', 'VARCHAR(20) DEFAULT "bar"'),
                ('ma1592_require_ma_slope_up', 'BOOLEAN DEFAULT 1'),
                ('ma1592_min_trading_value', 'INTEGER DEFAULT 5000000000'),
                ('ma1592_hold_bars', 'INTEGER DEFAULT 6'),
                ('ma1592_break_before_entry_pct', 'FLOAT DEFAULT 0.4'),
                ('ma1592_touch_buffer_pct', 'FLOAT DEFAULT 0.15'),
                ('ma1592_require_bullish_candle', 'BOOLEAN DEFAULT 1'),
                ('ma1592_prev_high_lookback_days', 'INTEGER DEFAULT 20'),
                ('ma1592_tp1_frac', 'FLOAT DEFAULT 0.5'),
                ('ma1592_take_profit_pct', 'FLOAT DEFAULT 4.0'),
                ('ma1592_stop_pct', 'FLOAT DEFAULT 4.0'),
                ('ma1592_hard_break_pct', 'FLOAT DEFAULT 1.0'),
                ('ma1592_large_break_pct', 'FLOAT DEFAULT 0.7'),
                ('ma1592_impulse_min_pct', 'FLOAT DEFAULT 2.0'),
                ('ma1592_crash_pct', 'FLOAT DEFAULT 1.8'),
                ('ma1592_crash_bars', 'INTEGER DEFAULT 3'),
                ('ma1592_setup_expire_days', 'INTEGER DEFAULT 8'),
                ('ma1592_max_hold_days', 'INTEGER DEFAULT 10'),
                ('ma1592_flatten_eod', 'BOOLEAN DEFAULT 1'),
                ('ma1592_risk_per_trade_pct', 'FLOAT DEFAULT 2.0'),
                ('ma1592_max_invest_amount', 'INTEGER DEFAULT 0'),
                ('ma1592_trade_start_time', 'VARCHAR(5) DEFAULT "09:10"'),
                ('ma1592_trade_end_time', 'VARCHAR(5) DEFAULT "15:15"'),
                ('ma1592_hold_mode', 'VARCHAR(32) DEFAULT "scale_in_gc"'),
                ('ma1592_exec_tf', 'VARCHAR(8) DEFAULT "3M"'),
                ('ma1592_entry_trigger', 'VARCHAR(32) DEFAULT "gc_above"'),
                ('ma1592_price_lead_near_pct', 'FLOAT DEFAULT 1.5'),
                ('ma1592_price_lead_far_pct', 'FLOAT DEFAULT 1.0'),
                ('ma1592_ledger_purge_tf', 'VARCHAR(8) DEFAULT "3M"'),
                ('ma1592_leg1_pct', 'FLOAT DEFAULT 15.0'),
                ('ma1592_leg2_pct', 'FLOAT DEFAULT 35.0'),
                ('ma1592_leg3_pct', 'FLOAT DEFAULT 50.0'),
                ('ma1592_scale_gap_pct', 'FLOAT DEFAULT 1.0'),
                ('ma1592_scale_hold_bars', 'INTEGER DEFAULT 2'),
                ('market_risk_block_ma1592', 'BOOLEAN DEFAULT 1'),
            ]
            if ats_columns:
                for col_name, col_def in ats_new_columns:
                    if col_name not in ats_columns:
                        conn.execute(text(f"ALTER TABLE auto_trade_settings ADD COLUMN {col_name} {col_def}"))
                        conn.commit()
            # 기존 NULL sangtta_buy_amount → 기본 소액
            try:
                conn.execute(text(
                    "UPDATE auto_trade_settings SET sangtta_buy_amount = 500000 "
                    "WHERE sangtta_buy_amount IS NULL OR sangtta_buy_amount <= 0"
                ))
                conn.commit()
            except Exception:
                pass
            # 과매도 돌파 SOFT: 스캐너 1분 간격 3회 유지가 기본
            try:
                conn.execute(text(
                    "UPDATE auto_trade_settings SET breakout_entry_soft_polls = 3 "
                    "WHERE breakout_entry_soft_polls IS NULL OR breakout_entry_soft_polls <= 0"
                ))
                conn.commit()
            except Exception:
                pass
            # MA1590 → MA1592 컬럼·데이터 리네이밍 (1회)
            try:
                result = conn.execute(text("PRAGMA table_info('auto_trade_settings')"))
                ats_cols = {row[1] for row in result}
                _ma_renames = [
                    ("market_surge_block_ma1590", "market_surge_block_ma1592"),
                    ("use_ma1590", "use_ma1592"),
                    ("ma1590_condition_names", "ma1592_condition_names"),
                    ("ma1590_max_slots", "ma1592_max_slots"),
                    ("ma1590_l1_limit", "ma1592_l1_limit"),
                    ("ma1590_ma_source", "ma1592_ma_source"),
                    ("ma1590_require_ma_slope_up", "ma1592_require_ma_slope_up"),
                    ("ma1590_min_trading_value", "ma1592_min_trading_value"),
                    ("ma1590_hold_bars", "ma1592_hold_bars"),
                    ("ma1590_break_before_entry_pct", "ma1592_break_before_entry_pct"),
                    ("ma1590_touch_buffer_pct", "ma1592_touch_buffer_pct"),
                    ("ma1590_require_bullish_candle", "ma1592_require_bullish_candle"),
                    ("ma1590_prev_high_lookback_days", "ma1592_prev_high_lookback_days"),
                    ("ma1590_tp1_frac", "ma1592_tp1_frac"),
                    ("ma1590_take_profit_pct", "ma1592_take_profit_pct"),
                    ("ma1590_stop_pct", "ma1592_stop_pct"),
                    ("ma1590_hard_break_pct", "ma1592_hard_break_pct"),
                    ("ma1590_large_break_pct", "ma1592_large_break_pct"),
                    ("ma1590_impulse_min_pct", "ma1592_impulse_min_pct"),
                    ("ma1590_crash_pct", "ma1592_crash_pct"),
                    ("ma1590_crash_bars", "ma1592_crash_bars"),
                    ("ma1590_setup_expire_days", "ma1592_setup_expire_days"),
                    ("ma1590_max_hold_days", "ma1592_max_hold_days"),
                    ("ma1590_flatten_eod", "ma1592_flatten_eod"),
                    ("ma1590_risk_per_trade_pct", "ma1592_risk_per_trade_pct"),
                    ("ma1590_max_invest_amount", "ma1592_max_invest_amount"),
                    ("ma1590_trade_start_time", "ma1592_trade_start_time"),
                    ("ma1590_trade_end_time", "ma1592_trade_end_time"),
                    ("ma1590_hold_mode", "ma1592_hold_mode"),
                    ("ma1590_exec_tf", "ma1592_exec_tf"),
                    ("ma1590_entry_trigger", "ma1592_entry_trigger"),
                    ("ma1590_price_lead_near_pct", "ma1592_price_lead_near_pct"),
                    ("ma1590_price_lead_far_pct", "ma1592_price_lead_far_pct"),
                    ("ma1590_ledger_purge_tf", "ma1592_ledger_purge_tf"),
                    ("ma1590_leg1_pct", "ma1592_leg1_pct"),
                    ("ma1590_leg2_pct", "ma1592_leg2_pct"),
                    ("ma1590_leg3_pct", "ma1592_leg3_pct"),
                    ("ma1590_scale_gap_pct", "ma1592_scale_gap_pct"),
                    ("ma1590_scale_hold_bars", "ma1592_scale_hold_bars"),
                    ("market_risk_block_ma1590", "market_risk_block_ma1592"),
                ]
                for old_col, new_col in _ma_renames:
                    if old_col in ats_cols and new_col not in ats_cols:
                        conn.execute(text(
                            f"ALTER TABLE auto_trade_settings RENAME COLUMN {old_col} TO {new_col}"
                        ))
                        ats_cols.discard(old_col)
                        ats_cols.add(new_col)
                    elif old_col in ats_cols and new_col in ats_cols:
                        conn.execute(text(
                            f"UPDATE auto_trade_settings SET {new_col} = {old_col}"
                        ))
                conn.execute(text(
                    "UPDATE positions SET strategy_key = 'ma1592' "
                    "WHERE strategy_key = 'ma1590'"
                ))
                conn.execute(text(
                    "UPDATE trading_strategies SET strategy_type = 'MA1592' "
                    "WHERE strategy_type = 'MA1590'"
                ))
                conn.commit()
            except Exception:
                pass
            # MA1592: 3분봉 GC 매수 프로필 (기존 price_lead/5분 → gc_above/3분)
            try:
                conn.execute(text(
                    "UPDATE auto_trade_settings SET ma1592_entry_trigger = 'gc_above' "
                    "WHERE ma1592_entry_trigger IS NULL OR ma1592_entry_trigger = 'price_lead'"
                ))
                conn.execute(text(
                    "UPDATE auto_trade_settings SET ma1592_exec_tf = '3M' "
                    "WHERE ma1592_exec_tf IS NULL OR ma1592_exec_tf = '' OR ma1592_exec_tf = '5M'"
                ))
                conn.execute(text(
                    "UPDATE auto_trade_settings SET ma1592_ledger_purge_tf = '3M' "
                    "WHERE ma1592_ledger_purge_tf IS NULL OR ma1592_ledger_purge_tf = ''"
                ))
                conn.execute(text(
                    "UPDATE auto_trade_settings SET ma1592_l1_limit = 10 "
                    "WHERE ma1592_l1_limit IS NULL OR ma1592_l1_limit <= 0 OR ma1592_l1_limit = 30"
                ))
                conn.execute(text(
                    "UPDATE auto_trade_settings SET ma1592_condition_names = REPLACE("
                    "ma1592_condition_names, '1590매매', '1592매매') "
                    "WHERE ma1592_condition_names LIKE '%1590매매%'"
                ))
                conn.execute(text(
                    "UPDATE auto_trade_settings SET ma1592_price_lead_near_pct = 1.5 "
                    "WHERE ma1592_price_lead_near_pct IS NULL "
                    "OR ma1592_price_lead_near_pct IN (1.0, 2.0)"
                ))
                conn.commit()
            except Exception:
                pass

            # fundamental_snapshots 컬럼 마이그레이션 (v1 확장)
            result = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='fundamental_snapshots'")
            ).fetchone()
            if result:
                result = conn.execute(text("PRAGMA table_info('fundamental_snapshots')"))
                fs_cols = {row[1] for row in result}
                fs_new_columns = [
                    ("dividend_per_share", "FLOAT"),
                    ("listed_shares", "FLOAT"),
                    ("foreign_ratio", "FLOAT"),
                    ("trading_value", "FLOAT"),
                ]
                for col_name, col_def in fs_new_columns:
                    if col_name not in fs_cols:
                        conn.execute(text(f"ALTER TABLE fundamental_snapshots ADD COLUMN {col_name} {col_def}"))
                        conn.commit()

            # theme_tags / theme_tag_edges 확장 (연관도 v2)
            result = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='theme_tags'")
            ).fetchone()
            if result:
                result = conn.execute(text("PRAGMA table_info('theme_tags')"))
                tt_cols = {row[1] for row in result}
                if "meta_json" not in tt_cols:
                    conn.execute(text("ALTER TABLE theme_tags ADD COLUMN meta_json TEXT"))
                    conn.commit()
            result = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='theme_tag_edges'")
            ).fetchone()
            if result:
                result = conn.execute(text("PRAGMA table_info('theme_tag_edges')"))
                te_cols = {row[1] for row in result}
                for col_name, col_def in [
                    ("biz_date", "DATE"),
                    ("rank", "INTEGER"),
                    ("inclusion_flag", "BOOLEAN DEFAULT 1"),
                    ("reason_text", "VARCHAR(500)"),
                ]:
                    if col_name not in te_cols:
                        conn.execute(text(f"ALTER TABLE theme_tag_edges ADD COLUMN {col_name} {col_def}"))
                        conn.commit()
            
            # 기본 전략 데이터 삽입 (없는 경우만)
            strategies_exist = conn.execute(text("SELECT COUNT(*) FROM trading_strategies")).scalar()
            if strategies_exist == 0:
                # 기본 전략들 삽입
                default_strategies = [
                    ("모멘텀 전략", "MOMENTUM", '{"momentum_period": 24, "trend_confirmation_days": 3}'),
                    ("이격도 전략", "DISPARITY", '{"ma_period": 20, "buy_threshold": 95.0, "sell_threshold": 105.0}'),
                    ("볼린저밴드 전략", "BOLLINGER", '{"ma_period": 20, "std_multiplier": 2.0, "confirmation_days": 3}'),
                    ("RSI 전략", "RSI", '{"rsi_period": 14, "oversold_threshold": 30.0, "overbought_threshold": 70.0}'),
                    ("일목균형표 전략", "ICHIMOKU", '{"conversion_period": 9, "base_period": 26, "span_b_period": 52, "displacement": 26}'),
                    ("차이킨 오실레이터 전략", "CHAIKIN", '{"short_period": 3, "long_period": 10, "buy_threshold": 0.0, "sell_threshold": 0.0}'),
                ]
                
                for name, strategy_type, params in default_strategies:
                    conn.execute(text("""
                        INSERT INTO trading_strategies (strategy_name, strategy_type, is_enabled, parameters, updated_at)
                        VALUES (:name, :type, 1, :params, datetime('now'))
                    """), {"name": name, "type": strategy_type, "params": params})
                
                conn.commit()

            # MA1592 시드 (기본 OFF) — 테이블에 없을 때만
            try:
                ma1592_exists = conn.execute(text(
                    "SELECT COUNT(*) FROM trading_strategies WHERE strategy_type = 'MA1592'"
                )).scalar()
                if not ma1592_exists:
                    import json as _json
                    from utils.ma1592 import DEFAULT_PARAMS as _MA1592_DEFAULTS
                    conn.execute(text("""
                        INSERT INTO trading_strategies (strategy_name, strategy_type, is_enabled, parameters, updated_at)
                        VALUES (:name, :type, 0, :params, datetime('now'))
                    """), {
                        "name": "15/92 홀드",
                        "type": "MA1592",
                        "params": _json.dumps(_MA1592_DEFAULTS, ensure_ascii=False),
                    })
                    conn.commit()
            except Exception:
                pass
                
    except Exception as e:
        # 마이그레이션 실패는 치명적이지 않게 무시
        print(f"Migration warning: {e}")


# 모듈 import 시점에 테이블 보장
init_db()


