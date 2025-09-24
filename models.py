from datetime import datetime
from typing import Generator

from sqlalchemy import Column, Integer, String, DateTime, Boolean, create_engine, UniqueConstraint, Date
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from config import Config

# SQLite DB (절대경로 사용 - config의 DATABASE_URL과 통일)
DATABASE_URL = Config.DATABASE_URL

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
    signal_type = Column(String(20), nullable=False, default="condition", index=True)  # 신호 타입
    
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


def get_db() -> Generator[Session, None, None]:
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


# 모듈 import 시점에 테이블 보장
init_db()


