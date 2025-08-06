from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from typing import List, Optional
import logging
from datetime import datetime

from models import (
    get_db, Condition, StockSignal, ConditionLog,
    ConditionCreate, ConditionUpdate, ConditionResponse,
    StockSignalResponse, ConditionLogResponse
)
from condition_monitor import condition_monitor
from kiwoom_api import KiwoomAPI
from config import Config

# 로깅 설정
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(Config.LOG_FILE),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="키움증권 조건식 모니터링 시스템",
    description="사용자가 지정한 조건식을 통해 종목을 실시간으로 감시하는 시스템",
    version="1.0.0"
)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 키움 API 인스턴스
kiwoom_api = KiwoomAPI()

@app.on_event("startup")
async def startup_event():
    """애플리케이션 시작 시 실행"""
    logger.info("키움증권 조건식 모니터링 시스템 시작")
    
    # 데이터베이스 초기화
    from models import init_db
    init_db()
    
    # 키움 API 인증
    if kiwoom_api.authenticate():
        logger.info("키움증권 API 인증 성공")
    else:
        logger.warning("키움증권 API 인증 실패 - 환경변수 확인 필요")

@app.on_event("shutdown")
async def shutdown_event():
    """애플리케이션 종료 시 실행"""
    logger.info("모니터링 시스템 종료")
    await condition_monitor.stop_all_monitoring()

# 조건식 관리 API
@app.post("/conditions/", response_model=ConditionResponse)
async def create_condition(
    condition: ConditionCreate,
    db: Session = Depends(get_db)
):
    """조건식 생성"""
    try:
        db_condition = Condition(
            condition_name=condition.condition_name,
            condition_expression=condition.condition_expression,
            is_active=True
        )
        
        db.add(db_condition)
        db.commit()
        db.refresh(db_condition)
        
        # 조건식 모니터링 시작
        if db_condition.is_active:
            await condition_monitor.start_monitoring(
                db_condition.id,
                db_condition.condition_name,
                "default"
            )
        
        logger.info(f"조건식 생성: {condition.condition_name}")
        return db_condition
        
    except Exception as e:
        logger.error(f"조건식 생성 오류: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="조건식 생성 중 오류가 발생했습니다.")

@app.get("/conditions/", response_model=List[ConditionResponse])
async def get_conditions(db: Session = Depends(get_db)):
    """조건식 목록 조회"""
    try:
        conditions = db.query(Condition).all()
        return conditions
    except Exception as e:
        logger.error(f"조건식 목록 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="조건식 목록 조회 중 오류가 발생했습니다.")

@app.get("/conditions/{condition_id}", response_model=ConditionResponse)
async def get_condition(condition_id: int, db: Session = Depends(get_db)):
    """조건식 상세 조회"""
    try:
        condition = db.query(Condition).filter(Condition.id == condition_id).first()
        if not condition:
            raise HTTPException(status_code=404, detail="조건식을 찾을 수 없습니다.")
        return condition
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"조건식 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="조건식 조회 중 오류가 발생했습니다.")

@app.delete("/conditions/{condition_id}")
async def delete_condition(condition_id: int, db: Session = Depends(get_db)):
    """조건식 삭제"""
    try:
        condition = db.query(Condition).filter(Condition.id == condition_id).first()
        if not condition:
            raise HTTPException(status_code=404, detail="조건식을 찾을 수 없습니다.")
        
        # 관련 신호와 로그도 삭제
        db.query(StockSignal).filter(StockSignal.condition_id == condition_id).delete()
        db.query(ConditionLog).filter(ConditionLog.condition_id == condition_id).delete()
        
        db.delete(condition)
        db.commit()
        
        logger.info(f"조건식 삭제: {condition.condition_name}")
        return {"message": "조건식이 삭제되었습니다."}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"조건식 삭제 오류: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="조건식 삭제 중 오류가 발생했습니다.")

# 신호 조회 API
@app.get("/signals/", response_model=List[StockSignalResponse])
async def get_signals(
    condition_id: Optional[int] = None,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """신호 목록 조회"""
    try:
        query = db.query(StockSignal)
        if condition_id:
            query = query.filter(StockSignal.condition_id == condition_id)
        signals = query.order_by(StockSignal.signal_time.desc()).limit(limit).all()
        return signals
    except Exception as e:
        logger.error(f"신호 목록 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="신호 목록 조회 중 오류가 발생했습니다.")

# 모니터링 제어 API
@app.post("/monitoring/start")
async def start_monitoring():
    """모든 조건식 모니터링 시작"""
    try:
        await condition_monitor.start_all_monitoring()
        return {"message": "모니터링이 시작되었습니다."}
    except Exception as e:
        logger.error(f"모니터링 시작 오류: {e}")
        raise HTTPException(status_code=500, detail="모니터링 시작 중 오류가 발생했습니다.")

@app.post("/monitoring/stop")
async def stop_monitoring():
    """모든 조건식 모니터링 중지"""
    try:
        await condition_monitor.stop_all_monitoring()
        return {"message": "모니터링이 중지되었습니다."}
    except Exception as e:
        logger.error(f"모니터링 중지 오류: {e}")
        raise HTTPException(status_code=500, detail="모니터링 중지 중 오류가 발생했습니다.")

@app.get("/monitoring/status")
async def get_monitoring_status():
    """모니터링 상태 조회"""
    try:
        status = condition_monitor.get_monitoring_status()
        return status
    except Exception as e:
        logger.error(f"모니터링 상태 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="모니터링 상태 조회 중 오류가 발생했습니다.")

# 키움 API 연동 API
@app.get("/kiwoom/conditions")
async def get_kiwoom_conditions():
    """키움증권 조건식 목록 조회"""
    try:
        conditions = kiwoom_api.get_condition_list()
        return {"conditions": conditions}
    except Exception as e:
        logger.error(f"키움증권 조건식 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="키움증권 조건식 조회 중 오류가 발생했습니다.")