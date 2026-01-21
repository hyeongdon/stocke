from datetime import datetime
from typing import Generator

from sqlalchemy import Column, Integer, String, DateTime, Boolean, create_engine, UniqueConstraint, Date, text, JSON, Float, Index
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
    stop_loss_rate = Column(Integer, nullable=False, default=5)  # 손절 비율 (%)
    take_profit_rate = Column(Integer, nullable=False, default=10)  # 익절 비율 (%)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        # 설정은 하나만 유지 (싱글톤)
        UniqueConstraint("id", name="uq_autotrade_settings_singleton"),
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
    buy_quantity = Column(Integer, nullable=False)  # 매수 수량
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
    # 간단한 마이그레이션: 컬럼이 없으면 추가 (SQLite 전용)
    try:
        with engine.connect() as conn:
            # pending_buy_signals 테이블 마이그레이션
            result = conn.execute(text("PRAGMA table_info('pending_buy_signals')"))
            columns = {row[1] for row in result}
            if 'failure_reason' not in columns:
                conn.execute(text("ALTER TABLE pending_buy_signals ADD COLUMN failure_reason VARCHAR(255)"))
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


