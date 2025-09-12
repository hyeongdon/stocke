from datetime import datetime
from typing import Generator

from sqlalchemy import Column, Integer, String, DateTime, Boolean, create_engine, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# SQLite DB (workspace 루트의 stock_pipeline.db 사용)
DATABASE_URL = "sqlite:///stock_pipeline.db"

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
    status = Column(String(20), nullable=False, default="PENDING")  # PENDING, ORDERED, CANCELED 등

    __table_args__ = (
        # 동일 조건식/종목이 같은 시각대(초 단위)에 중복 저장되는 것을 1차 방지
        UniqueConstraint("condition_id", "stock_code", "status", name="uq_pending_unique"),
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


