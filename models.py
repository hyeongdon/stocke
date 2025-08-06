from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime
from typing import Optional
from pydantic import BaseModel

from config import Config

# SQLAlchemy 설정
engine = create_engine(Config.DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 데이터베이스 세션 의존성
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 데이터베이스 초기화 함수
def init_db():
    Base.metadata.create_all(bind=engine)

# SQLAlchemy 모델
class Condition(Base):
    __tablename__ = "conditions"

    id = Column(Integer, primary_key=True, index=True)
    condition_name = Column(String, index=True)
    condition_expression = Column(String)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class StockSignal(Base):
    __tablename__ = "stock_signals"

    id = Column(Integer, primary_key=True, index=True)
    condition_id = Column(Integer, ForeignKey("conditions.id"))
    stock_code = Column(String, index=True)
    stock_name = Column(String)
    signal_type = Column(String)  # "buy" or "sell"
    signal_time = Column(DateTime, default=datetime.utcnow)

class ConditionLog(Base):
    __tablename__ = "condition_logs"

    id = Column(Integer, primary_key=True, index=True)
    condition_id = Column(Integer, ForeignKey("conditions.id"))
    log_level = Column(String)  # "info", "warning", "error"
    message = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

# Pydantic 모델
class ConditionBase(BaseModel):
    condition_name: str
    condition_expression: str

class ConditionCreate(ConditionBase):
    pass

class ConditionUpdate(BaseModel):
    condition_name: Optional[str] = None
    condition_expression: Optional[str] = None
    is_active: Optional[bool] = None

class ConditionResponse(ConditionBase):
    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True

class StockSignalResponse(BaseModel):
    id: int
    condition_id: int
    stock_code: str
    stock_name: str
    signal_type: str
    signal_time: datetime

    class Config:
        orm_mode = True

class ConditionLogResponse(BaseModel):
    id: int
    condition_id: int
    log_level: str
    message: str
    created_at: datetime

    class Config:
        orm_mode = True