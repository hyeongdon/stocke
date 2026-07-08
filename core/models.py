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
    status = Column(String(20), nullable=False, default="PENDING")  # PENDING, ORDERED, CANCELED 등
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

    # ===== 매수 사이징 =====
    sizing_method = Column(String(30), nullable=False, default="FIXED")  # FIXED, PYRAMIDING
    initial_min_amount = Column(Integer, nullable=True, default=2000000) # 초기 진입 최소 금액
    initial_max_amount = Column(Integer, nullable=True, default=5000000) # 초기 최대 / 종목당 상한
    signal_min_threshold = Column(Float, nullable=True, default=2)       # 신호(등락%) 최소금액 기준
    signal_max_threshold = Column(Float, nullable=True, default=10)      # 신호(등락%) 최대금액 기준
    add_buy_amount = Column(Integer, nullable=True, default=1000000)     # 추가매수 1회 금액
    add_buy_trigger = Column(Float, nullable=True, default=0.7)          # 추가매수 트리거(스텝당 %)
    max_concurrent_positions = Column(Integer, nullable=False, default=2)  # 최대 동시 보유 종목 (0=자동)
    cash_reserve_pct = Column(Float, nullable=False, default=10.0)         # 예수금 중 현금으로 남길 비율(%)
    max_daily_buys = Column(Integer, nullable=False, default=10)         # 1일 최대 매수 횟수
    daily_loss_limit = Column(Integer, nullable=True, default=-200000)   # 1일 손실 한도(원)
    daily_profit_target = Column(Integer, nullable=True, default=150000) # 1일 이익 목표(원)
    reorder_cooldown_sec = Column(Integer, nullable=False, default=300)  # 재주문 콜다운(초)
    trade_start_time = Column(String(5), nullable=False, default="10:00")  # 매매 시작
    trade_end_time = Column(String(5), nullable=False, default="15:20")    # 매매 종료

    # ===== 장 시작 자동 기동 =====
    auto_start_at_open = Column(Boolean, nullable=False, default=True)   # 평일 장 시작 시 엔진 자동 ON
    auto_start_time = Column(String(5), nullable=False, default="08:00")  # 엔진 자동 시작 시각

    # ===== 장 마감 전 전량 청산 =====
    liquidate_before_close = Column(Boolean, nullable=False, default=True)
    liquidate_time = Column(String(5), nullable=False, default="15:10")

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
    profit_loss = Column(Integer, nullable=True)  # 손익
    profit_loss_rate = Column(Float, nullable=True)  # 손익률 (%)
    
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

            # position_buy_fills 테이블
            tbl = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='position_buy_fills'")
            ).fetchone()
            if not tbl:
                conn.execute(text("""
                    CREATE TABLE position_buy_fills (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        position_id INTEGER NOT NULL,
                        stock_code VARCHAR(20) NOT NULL,
                        stock_name VARCHAR(100) NOT NULL,
                        fill_type VARCHAR(20) NOT NULL DEFAULT 'INITIAL',
                        signal_id INTEGER,
                        order_id VARCHAR(50),
                        price INTEGER NOT NULL,
                        quantity INTEGER NOT NULL,
                        amount INTEGER NOT NULL,
                        planned_amount INTEGER,
                        change_rate FLOAT,
                        sizing_method VARCHAR(30),
                        filled_at DATETIME NOT NULL,
                        note VARCHAR(255)
                    )
                """))
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_buy_fill_position ON position_buy_fills (position_id, filled_at)"
                ))
                conn.commit()

            # 체결 이력 없는 포지션 backfill
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

            result = conn.execute(text("PRAGMA table_info('position_buy_fills')"))
            pbf_cols2 = {row[1] for row in result} if result else set()
            if pbf_cols2 and 'order_quantity' not in pbf_cols2:
                conn.execute(text("ALTER TABLE position_buy_fills ADD COLUMN order_quantity INTEGER"))
                conn.commit()
                conn.execute(text(
                    "UPDATE position_buy_fills SET order_quantity = quantity WHERE order_quantity IS NULL"
                ))
                conn.commit()

            # auto_trade_settings 테이블 마이그레이션 (고급 자동매매 설정 컬럼 추가)
            result = conn.execute(text("PRAGMA table_info('auto_trade_settings')"))
            ats_columns = {row[1] for row in result}
            ats_new_columns = [
                ('watchlist_codes', 'TEXT'),
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
                ('initial_min_amount', 'INTEGER DEFAULT 2000000'),
                ('initial_max_amount', 'INTEGER DEFAULT 5000000'),
                ('signal_min_threshold', 'FLOAT DEFAULT 2'),
                ('signal_max_threshold', 'FLOAT DEFAULT 10'),
                ('add_buy_amount', 'INTEGER DEFAULT 1000000'),
                ('add_buy_trigger', 'FLOAT DEFAULT 0.7'),
                ('max_concurrent_positions', 'INTEGER DEFAULT 2'),
                ('cash_reserve_pct', 'FLOAT DEFAULT 10'),
                ('max_daily_buys', 'INTEGER DEFAULT 10'),
                ('daily_loss_limit', 'INTEGER DEFAULT -200000'),
                ('daily_profit_target', 'INTEGER DEFAULT 150000'),
                ('reorder_cooldown_sec', 'INTEGER DEFAULT 300'),
                ('trade_start_time', 'VARCHAR(5) DEFAULT "10:00"'),
                ('trade_end_time', 'VARCHAR(5) DEFAULT "15:20"'),
                ('auto_start_at_open', 'BOOLEAN DEFAULT 1'),
                ('auto_start_time', 'VARCHAR(5) DEFAULT "08:00"'),
                ('liquidate_before_close', 'BOOLEAN DEFAULT 1'),
                ('liquidate_time', 'VARCHAR(5) DEFAULT "15:10"'),
                ('order_method', 'VARCHAR(10) DEFAULT "MARKET"'),
            ]
            if ats_columns:  # 테이블이 이미 존재할 때만
                for col_name, col_def in ats_new_columns:
                    if col_name not in ats_columns:
                        conn.execute(text(f"ALTER TABLE auto_trade_settings ADD COLUMN {col_name} {col_def}"))
                        conn.commit()

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
                    ("차이킨 오실레이터 전략", "CHAIKIN", '{"short_period": 3, "long_period": 10, "buy_threshold": 0.0, "sell_threshold": 0.0}')
                ]
                
                for name, strategy_type, params in default_strategies:
                    conn.execute(text("""
                        INSERT INTO trading_strategies (strategy_name, strategy_type, is_enabled, parameters, updated_at)
                        VALUES (:name, :type, 1, :params, datetime('now'))
                    """), {"name": name, "type": strategy_type, "params": params})
                
                conn.commit()
                
    except Exception as e:
        # 마이그레이션 실패는 치명적이지 않게 무시
        print(f"Migration warning: {e}")
        pass


# 모듈 import 시점에 테이블 보장
init_db()


