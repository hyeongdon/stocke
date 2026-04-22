from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import os
import asyncio
from typing import Optional
import logging
from datetime import datetime
import httpx
import re

# 차트 생성 import
import pandas as pd
import numpy as np
import mplfinance as mpf
import matplotlib.pyplot as plt
import matplotlib.lines as mlines
import io
import base64
from ta.trend import IchimokuIndicator
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands
import warnings

# pandas와 ta 라이브러리의 FutureWarning 억제
warnings.filterwarnings('ignore', category=FutureWarning, module='ta')
warnings.filterwarnings('ignore', category=FutureWarning, module='pandas')

# DB 연동
from core.models import get_db, AutoTradeCondition, PendingBuySignal, AutoTradeSettings, WatchlistStock, TradingStrategy, StrategySignal, Position
from sqlalchemy.orm import Session
from pydantic import BaseModel
from managers.condition_monitor import condition_monitor
from api.kiwoom_api import KiwoomAPI
from core.config import Config
from utils.naver_discussion_crawler import NaverStockDiscussionCrawler

# 개선된 모듈들 import
from managers.signal_manager import signal_manager, SignalType, SignalStatus
from api.api_rate_limiter import api_rate_limiter
from managers.buy_order_executor import buy_order_executor
from managers.strategy_manager import strategy_manager
from managers.watchlist_sync_manager import watchlist_sync_manager
from managers.stop_loss_manager import StopLossManager
from managers.scalping_strategy import scalping_manager
from managers.cleanup_scheduler import cleanup_scheduler
from utils.debug_tracer import debug_tracer, enable_debug_mode, disable_debug_mode, is_debug_enabled

config = Config()

# 로깅 설정
import sys
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(config.LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

# 콘솔 출력 인코딩 설정
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

logger = logging.getLogger(__name__)

# 손절/익절 모니터링 매니저 인스턴스
stop_loss_manager = StopLossManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("🌐 [STARTUP] 애플리케이션 시작")
    
    # 정적 파일 디렉토리 재확인
    static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
    logger.info(f"🌐 [STARTUP] 정적 파일 디렉토리 재확인: {static_dir}")
    logger.info(f"🌐 [STARTUP] 디렉토리 존재: {os.path.exists(static_dir)}")
    if os.path.exists(static_dir):
        files = os.listdir(static_dir)
        logger.info(f"🌐 [STARTUP] 정적 파일 목록: {files}")
    
    # 키움 API 인증 및 연결
    # 기존 토큰 무효화 (투자구분이 바뀌었을 수 있음)
    kiwoom_api.token_manager.access_token = None
    kiwoom_api.token_manager.token_expiry = None
    
    if kiwoom_api.authenticate():
        logger.info("키움증권 API 인증 성공")
        
        # WebSocket 연결 시도
        try:
            if await kiwoom_api.connect():
                logger.info("키움 API WebSocket 연결 성공")
                logger.info(f"키움 API 상태 - running: {kiwoom_api.running}, websocket: {kiwoom_api.websocket is not None}")
            else:
                logger.warning("키움 API WebSocket 연결 실패 - REST API만 사용")
        except Exception as e:
            logger.error(f"키움 API WebSocket 연결 중 오류: {e}")
            logger.warning("WebSocket 연결 실패 - REST API만 사용")
    else:
        logger.warning("키움 API 인증 실패 - 환경변수 확인 필요")
    
    logger.info("키움증권 조건식 모니터링 시스템 시작")
    
    # 개선된 시스템들 시작
    try:
        # 매수 주문 실행기 시작
        asyncio.create_task(buy_order_executor.start_processing())
        logger.info("💰 [STARTUP] 매수 주문 실행기 시작")
    except Exception as e:
        logger.error(f"💰 [STARTUP] 매수 주문 실행기 시작 실패: {e}")
    
    try:
        # 손절/익절 모니터링 시작
        asyncio.create_task(stop_loss_manager.start_monitoring())
        logger.info("🛡️ [STARTUP] 손절/익절 모니터링 시작")
    except Exception as e:
        logger.error(f"🛡️ [STARTUP] 손절/익절 모니터링 시작 실패: {e}")
    
    try:
        # 자정 정리 스케줄러 시작
        asyncio.create_task(cleanup_scheduler.start_scheduler())
        logger.info("🕛 [STARTUP] 자정 정리 스케줄러 시작")
    except Exception as e:
        logger.error(f"🕛 [STARTUP] 자정 정리 스케줄러 시작 실패: {e}")
    
    yield
    
    # Shutdown
    logger.info("모니터링 시스템 종료")
    
    # 개선된 시스템들 종료
    try:
        await buy_order_executor.stop_processing()
        logger.info("💰 [SHUTDOWN] 매수 주문 실행기 종료")
    except Exception as e:
        logger.error(f"💰 [SHUTDOWN] 매수 주문 실행기 종료 실패: {e}")
    
    try:
        await stop_loss_manager.stop_monitoring()
        logger.info("🛡️ [SHUTDOWN] 손절/익절 모니터링 종료")
    except Exception as e:
        logger.error(f"🛡️ [SHUTDOWN] 손절/익절 모니터링 종료 실패: {e}")
    
    await condition_monitor.stop_all_monitoring()
    # WebSocket 우아한 종료
    await kiwoom_api.graceful_shutdown()
    logger.info("키움 API WebSocket 연결 종료 완료")

app = FastAPI(
    title="키움증권 조건식 모니터링 시스템",
    description="사용자가 지정한 조건식을 통해 종목을 실시간으로 감시하는 시스템",
    version="1.0.0",
    lifespan=lifespan
)

# 정적 파일 서빙 설정
static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
logger.info(f"🌐 [STATIC] 정적 파일 디렉토리: {static_dir}")
logger.info(f"🌐 [STATIC] 디렉토리 존재 여부: {os.path.exists(static_dir)}")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    logger.info("🌐 [STATIC] 정적 파일 마운트 완료")
else:
    logger.error("🌐 [STATIC] 정적 파일 디렉토리를 찾을 수 없습니다!")

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

# 네이버 토론 크롤러 인스턴스
discussion_crawler = NaverStockDiscussionCrawler()


from fastapi.responses import RedirectResponse
class ToggleConditionRequest(BaseModel):
    condition_name: str
    is_enabled: bool

class TradingSettingsRequest(BaseModel):
    is_enabled: bool
    max_invest_amount: int
    stop_loss_rate: int
    take_profit_rate: int

# 관심종목 관리용 Pydantic 모델들
class WatchlistAddRequest(BaseModel):
    stock_code: str
    stock_name: str
    notes: Optional[str] = None

class WatchlistToggleRequest(BaseModel):
    stock_code: str
    is_active: bool

class StrategyConfigureRequest(BaseModel):
    strategy_type: str  # MOMENTUM, DISPARITY, BOLLINGER, RSI
    parameters: dict

class StrategyToggleRequest(BaseModel):
    strategy_id: int
    is_enabled: bool

@app.post("/conditions/toggle")
async def toggle_condition(req: ToggleConditionRequest):
    try:
        for db in get_db():
            session: Session = db
            row = session.query(AutoTradeCondition).filter(AutoTradeCondition.condition_name == req.condition_name).first()
            if row is None:
                row = AutoTradeCondition(condition_name=req.condition_name, is_enabled=req.is_enabled, updated_at=datetime.utcnow())
                session.add(row)
            else:
                row.is_enabled = req.is_enabled
                row.updated_at = datetime.utcnow()
            session.commit()
        return {"condition_name": req.condition_name, "is_enabled": req.is_enabled}
    except Exception as e:
        logger.error(f"조건식 토글 실패: {e}")
        raise HTTPException(status_code=500, detail="조건식 토글 실패")


@app.get("/")
async def root():
    """메인 페이지 - 웹 인터페이스로 리다이렉트"""
    logger.info("🌐 [STATIC] 루트 경로 접근 - /static/index.html로 리다이렉트")
    return RedirectResponse(url="/static/index.html")

@app.get("/status")
async def status_page():
    """서버 상태 페이지로 리다이렉트"""
    return RedirectResponse(url="/static/server_status.html")

@app.get("/api")
async def api_info():
    """API 정보 엔드포인트"""
    return {
        "message": "키움증권 조건식 모니터링 시스템 API",
        "version": "1.0.0",
        "endpoints": {
            "conditions": "/conditions/",
            "signals": "/signals/",
            "monitoring": "/monitoring/",
            "kiwoom": "/kiwoom/"
        }
    }


@app.get("/signals/pending")
async def get_pending_signals(limit: int = 100, status: str = "PENDING", skip_price: bool = False):
    """매수대기(PENDING) 신호 목록 조회. status=ALL 전달 시 전체 조회, skip_price=True면 현재가 조회 생략"""
    try:
        logger.info(f"[PENDING_API] request: limit={limit} status={status} skip_price={skip_price}")
        items = []
        for db in get_db():
            session: Session = db
            # 디버그: 전체/페딩 카운트 로깅
            total_all = session.query(PendingBuySignal).count()
            total_pending = session.query(PendingBuySignal).filter(PendingBuySignal.status == "PENDING").count()
            logger.info(f"[PENDING_API] DB URL={Config.DATABASE_URL} total_all={total_all} total_pending={total_pending}")

            q = session.query(PendingBuySignal)
            if status.upper() != "ALL":
                q = q.filter(PendingBuySignal.status == status.upper())
            rows = q.order_by(PendingBuySignal.detected_at.desc()).limit(limit).all()
            logger.info(f"[PENDING_API] rows fetched={len(rows)}")
            
            # 자동매매 설정을 미리 조회 (반복문 밖으로 이동)
            auto_trade_settings = session.query(AutoTradeSettings).first()
            max_invest_amount = auto_trade_settings.max_invest_amount if auto_trade_settings else 100000
            
            for i, r in enumerate(rows):
                # Position 조회
                position = session.query(Position).filter(Position.signal_id == r.id).first()
                
                # ⚠️ API 호출 제한으로 인해 현재가 조회 기본값을 skip_price=True로 변경
                # DB에 저장된 target_price를 우선 사용
                current_price = getattr(r, "target_price", 0) or 0
                
                # skip_price=False인 경우에만 API 호출 (수동 요청 시)
                if not skip_price and current_price == 0:
                    try:
                        current_price = await kiwoom_api.get_current_price(r.stock_code)
                        logger.debug(f"[PENDING_API] 현재가 조회 성공: {r.stock_code} = {current_price}")
                    except Exception as e:
                        logger.warning(f"[PENDING_API] 현재가 조회 실패 {r.stock_code}: {e}")
                        current_price = 0
                
                # 매수 수량 계산
                target_quantity = max_invest_amount // current_price if current_price and current_price > 0 else 0
                if target_quantity < 1:
                    target_quantity = 1
                
                # 매수 금액 계산
                target_amount = target_quantity * current_price if current_price and current_price > 0 else 0
                
                # Signal 기본 정보
                signal_data = {
                    "id": r.id,
                    "condition_id": r.condition_id,
                    "stock_code": r.stock_code,
                    "stock_name": r.stock_name,
                    "detected_at": r.detected_at.isoformat() if r.detected_at else None,
                    "status": r.status,
                    "signal_type": getattr(r, "signal_type", "condition"),
                    "failure_reason": getattr(r, "failure_reason", None),
                    "target_price": getattr(r, "target_price", None),
                    "current_price": current_price,
                    "target_quantity": target_quantity,
                    "target_amount": target_amount,
                }
                
                # Position 정보 추가
                if position:
                    signal_data["position"] = {
                        "id": position.id,
                        "buy_price": position.buy_price,
                        "buy_quantity": position.buy_quantity,
                        "buy_amount": position.buy_amount,
                        "current_price": position.current_price or position.buy_price,
                        "stop_loss_price": position.stop_loss_price,
                        "take_profit_price": position.take_profit_price,
                        "status": position.status,
                        "actual_buy_amount": getattr(position, 'actual_buy_amount', None),
                        "current_profit_loss": position.current_profit_loss,
                        "current_profit_loss_rate": position.current_profit_loss_rate,
                        "buy_time": position.buy_time.isoformat() if position.buy_time else None,
                    }
                
                items.append(signal_data)
        payload = {"items": items, "total": len(items), "_debug": {"db": Config.DATABASE_URL, "limit": limit, "status": status}}
        logger.info(f"[PENDING_API] response total={payload['total']}")
        return payload
    except Exception as e:
        logger.error(f"매수대기 신호 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="매수대기 신호 조회 실패")

@app.get("/trading/settings")
async def get_trading_settings():
    """자동매매 설정 조회"""
    try:
        for db in get_db():
            session: Session = db
            settings = session.query(AutoTradeSettings).first()
            if not settings:
                # 기본 설정 생성
                settings = AutoTradeSettings(
                    is_enabled=False,
                    max_invest_amount=1000000,
                    stop_loss_rate=5,
                    take_profit_rate=10
                )
                session.add(settings)
                session.commit()
            
            return {
                "is_enabled": settings.is_enabled,
                "max_invest_amount": settings.max_invest_amount,
                "stop_loss_rate": settings.stop_loss_rate,
                "take_profit_rate": settings.take_profit_rate,
                "updated_at": settings.updated_at.isoformat() if settings.updated_at else None
            }
    except Exception as e:
        logger.error(f"자동매매 설정 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="자동매매 설정 조회 실패")

@app.post("/trading/settings")
async def save_trading_settings(req: TradingSettingsRequest):
    """자동매매 설정 저장"""
    try:
        for db in get_db():
            session: Session = db
            settings = session.query(AutoTradeSettings).first()
            if not settings:
                settings = AutoTradeSettings()
                session.add(settings)
            
            settings.is_enabled = req.is_enabled
            settings.max_invest_amount = req.max_invest_amount
            settings.stop_loss_rate = req.stop_loss_rate
            settings.take_profit_rate = req.take_profit_rate
            settings.updated_at = datetime.utcnow()
            
            session.commit()
            
            return {
                "message": "자동매매 설정이 저장되었습니다.",
                "is_enabled": settings.is_enabled,
                "max_invest_amount": settings.max_invest_amount,
                "stop_loss_rate": settings.stop_loss_rate,
                "take_profit_rate": settings.take_profit_rate
            }
    except Exception as e:
        logger.error(f"자동매매 설정 저장 오류: {e}")
        raise HTTPException(status_code=500, detail="자동매매 설정 저장 실패")

@app.get("/conditions/")
async def get_conditions():
    """조건식 목록 조회 (키움 API)"""
    try:
        logger.debug("키움 API를 통한 조건식 목록 조회 시작")
        
        # 키움 API를 통해 조건식 목록 조회 (WebSocket 방식)
        conditions_data = await kiwoom_api.get_condition_list_websocket()
        logger.debug(f"키움 API에서 조건식 개수: {len(conditions_data) if conditions_data else 0}")
        
        if not conditions_data:
            logger.debug("키움 API에서 조건식이 없습니다.")
            return JSONResponse(content=[], media_type="application/json; charset=utf-8")
        
        # DB의 자동매매 활성화 상태 로드
        enabled_map = {}
        for db in get_db():
            session: Session = db
            rows = session.query(AutoTradeCondition).all()
            enabled_map = {row.condition_name: bool(row.is_enabled) for row in rows}

        # 키움 API 응답을 ConditionResponse 형태로 변환 (+ is_enabled 병합)
        conditions = []
        for i, condition_data in enumerate(conditions_data):
            # 키움 API 응답 형태에 따라 조정 필요
            condition = {
                "id": i + 1,  # UI에서 사용할 순서 ID (1부터 시작)
                "api_id": condition_data.get('condition_id', str(i)),  # 키움 API의 실제 조건식 ID
                "condition_name": condition_data.get('condition_name', f'조건식_{i+1}'),
                "condition_expression": condition_data.get('expression', ''),
                "is_active": True,
                "is_enabled": enabled_map.get(condition_data.get('condition_name', f'조건식_{i+1}'), False),
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }
            conditions.append(condition)
            logger.debug(f"조건식: {condition['condition_name']} (ID: {condition['id']}, API ID: {condition['api_id']})")
        
        return JSONResponse(content=conditions, media_type="application/json; charset=utf-8")
    except Exception as e:
        logger.error(f"키움 API 조건식 목록 조회 오류: {e}")
        logger.error(f"오류 타입: {type(e).__name__}")
        import traceback
        logger.error(f"스택 트레이스: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="키움 API 조건식 목록 조회 중 오류가 발생했습니다.")

# 조건식 상세 조회는 키움 API를 통해 처리됨

@app.get("/conditions/{condition_id}/stocks")
async def get_condition_stocks(condition_id: int):
    """조건식으로 종목 목록 조회"""
    logger.info(f"🌐 [API] /conditions/{condition_id}/stocks 엔드포인트 호출됨")
    try:
        logger.debug(f"조건식 종목 조회 시작: condition_id={condition_id}")
        
        # 먼저 조건식 목록을 가져와서 해당 ID의 조건식 정보 확인
        conditions_data = await kiwoom_api.get_condition_list_websocket()
        
        if not conditions_data:
            raise HTTPException(status_code=404, detail="조건식 목록을 가져올 수 없습니다.")
        
        # condition_id는 1부터 시작하므로 인덱스로 변환 (0부터 시작)
        condition_index = condition_id - 1
        
        if condition_index < 0 or condition_index >= len(conditions_data):
            raise HTTPException(status_code=404, detail="해당 조건식을 찾을 수 없습니다.")
        
        condition_info = conditions_data[condition_index]
        condition_name = condition_info.get('condition_name', f'조건식_{condition_id}')
        condition_api_id = condition_info.get('condition_id', str(condition_index))
        
        logger.info(f"🌐 [API] 조건식 검색 시작: {condition_name} (API ID: {condition_api_id})")
        logger.info(f"🌐 [API] 조건식 인덱스: {condition_index}, 요청된 ID: {condition_id}")
        logger.info(f"🌐 [API] 전체 조건식 목록:")
        for i, cond in enumerate(conditions_data):
            logger.info(f"🌐 [API]   {i+1}. {cond.get('condition_name')} (API ID: {cond.get('condition_id')})")
        
        # 키움 API를 통해 조건식으로 종목 검색
        stocks_data = await kiwoom_api.search_condition_stocks(condition_api_id, condition_name)
        
        if not stocks_data:
            logger.info(f"🌐 [API] 조건식 '{condition_name}'에 해당하는 종목이 없습니다.")
            return JSONResponse(content={
                "condition_id": condition_id,
                "condition_name": condition_name,
                "stocks": [],
                "total_count": 0
            }, media_type="application/json; charset=utf-8")
        
        # 응답 데이터 구성
        response_data = {
            "condition_id": condition_id,
            "condition_name": condition_name,
            "stocks": stocks_data,
            "total_count": len(stocks_data)
        }
        
        logger.info(f"🌐 [API] 조건식 종목 조회 완료: {condition_name}, 종목 수: {len(stocks_data)}개")
        
        # 종목 목록 출력 (콘솔에 프린트)
        print(f"\n=== 조건식: {condition_name} ===\n")
        print(f"총 {len(stocks_data)}개 종목")
        print("-" * 80)
        print(f"{'순번':<4} {'종목코드':<8} {'종목명':<20} {'현재가':<10} {'등락률':<8}")
        print("-" * 80)
        
        for i, stock in enumerate(stocks_data, 1):
            print(f"{i:<4} {stock.get('stock_code', ''):<8} {stock.get('stock_name', ''):<20} {stock.get('current_price', ''):<10} {stock.get('change_rate', ''):<8}")
        
        print("-" * 80)
        print(f"총 {len(stocks_data)}개 종목 조회 완료\n")
        
        return JSONResponse(content=response_data, media_type="application/json; charset=utf-8")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"🌐 [API] 조건식 종목 조회 오류: {e}")
        logger.error(f"오류 타입: {type(e).__name__}")
        import traceback
        logger.error(f"스택 트레이스: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="조건식 종목 조회 중 오류가 발생했습니다.")

@app.get("/stocks/{stock_code}/chart")
async def get_stock_chart(stock_code: str, period: str = "1D"):
    """종목 차트 데이터 조회"""
    try:
        logger.info(f"차트 데이터 요청: {stock_code}, 기간: {period}")
        
        # 키움 API에서 차트 데이터 조회
        chart_data = await kiwoom_api.get_stock_chart_data(stock_code, period)
        
        if not chart_data:
            raise HTTPException(status_code=404, detail="차트 데이터를 찾을 수 없습니다.")
        
        return JSONResponse(content={
            "stock_code": stock_code,
            "period": period,
            "chart_data": chart_data
        }, media_type="application/json; charset=utf-8")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"차트 데이터 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="차트 데이터 조회 중 오류가 발생했습니다.")


# 모니터링 제어 API
@app.post("/monitoring/start")
async def start_monitoring():
    """모든 조건식 모니터링 시작"""
    logger.info("🌐 [API] /monitoring/start 엔드포인트 호출됨")
    try:
        await condition_monitor.start_periodic_monitoring()
        logger.info("🌐 [API] 모니터링 시작 성공")
        return {
            "message": "모니터링이 시작되었습니다.",
            "is_running": True,
            "is_monitoring": True
        }
    except Exception as e:
        logger.error(f"🌐 [API] 모니터링 시작 오류: {e}")
        raise HTTPException(status_code=500, detail="모니터링 시작 중 오류가 발생했습니다.")

@app.post("/monitoring/stop")
async def stop_monitoring():
    """모든 조건식 모니터링 중지"""
    logger.info("🌐 [API] /monitoring/stop 엔드포인트 호출됨")
    try:
        await condition_monitor.stop_all_monitoring()
        logger.info("🌐 [API] 모니터링 중지 성공")
        return {
            "message": "모니터링이 중지되었습니다.",
            "is_running": False,
            "is_monitoring": False
        }
    except Exception as e:
        logger.error(f"🌐 [API] 모니터링 중지 오류: {e}")
        raise HTTPException(status_code=500, detail="모니터링 중지 중 오류가 발생했습니다.")

# ===== 디버그 모드 제어 API =====

@app.post("/debug/enable")
async def enable_debugging():
    """디버그 모드 활성화 - 함수 호출 추적 시작"""
    try:
        enable_debug_mode()
        debug_tracer.reset_statistics()
        return {
            "message": "디버그 모드가 활성화되었습니다",
            "debug_enabled": True,
            "description": "이제 모든 함수 호출이 상세하게 로깅됩니다"
        }
    except Exception as e:
        logger.error(f"디버그 모드 활성화 오류: {e}")
        raise HTTPException(status_code=500, detail=f"디버그 모드 활성화 실패: {str(e)}")

@app.post("/debug/disable")
async def disable_debugging():
    """디버그 모드 비활성화 - 함수 호출 추적 중지"""
    try:
        # 통계 출력
        debug_tracer.print_statistics()
        
        disable_debug_mode()
        return {
            "message": "디버그 모드가 비활성화되었습니다",
            "debug_enabled": False,
            "description": "로그 레벨이 INFO로 변경되었습니다"
        }
    except Exception as e:
        logger.error(f"디버그 모드 비활성화 오류: {e}")
        raise HTTPException(status_code=500, detail=f"디버그 모드 비활성화 실패: {str(e)}")

@app.get("/debug/status")
async def get_debug_status():
    """디버그 모드 상태 확인"""
    return {
        "debug_enabled": is_debug_enabled(),
        "call_count": len(debug_tracer.call_count),
        "tracked_functions": list(debug_tracer.call_count.keys()),
        "execution_times": {
            func: {
                "avg": sum(times) / len(times),
                "total": sum(times),
                "count": len(times)
            }
            for func, times in debug_tracer.execution_times.items()
        }
    }

@app.post("/debug/statistics")
async def print_debug_statistics():
    """디버그 통계 출력"""
    try:
        debug_tracer.print_statistics()
        return {
            "message": "디버그 통계가 로그에 출력되었습니다",
            "call_count": debug_tracer.call_count,
            "total_functions": len(debug_tracer.call_count)
        }
    except Exception as e:
        logger.error(f"디버그 통계 출력 오류: {e}")
        raise HTTPException(status_code=500, detail=f"디버그 통계 출력 실패: {str(e)}")

@app.get("/monitoring/status")
async def get_monitoring_status():
    """모니터링 상태 조회 (개선된 상태 정보 포함)"""
    logger.info("🌐 [API] /monitoring/status 엔드포인트 호출됨")
    try:
        # 기본 모니터링 상태
        monitoring_status = await condition_monitor.get_monitoring_status()
        
        # 신호 통계
        signal_stats = await signal_manager.get_signal_statistics()
        
        # API 제한 상태
        api_status = api_rate_limiter.get_status_info()
        
        # 매수 주문 실행기 상태
        buy_executor_status = {
            "is_running": buy_order_executor.is_running,
            "max_invest_amount": buy_order_executor.auto_trade_settings.max_invest_amount if buy_order_executor.auto_trade_settings else 0,
            "max_retry_attempts": buy_order_executor.max_retry_attempts
        }
        
        # 통합 상태 정보
        status = {
            "monitoring": monitoring_status,
            "signals": signal_stats,
            "api_limiter": api_status,
            "buy_executor": buy_executor_status,
            "timestamp": datetime.now().isoformat()
        }
        
        logger.info(f"🌐 [API] 모니터링 상태 조회 성공")
        return status
    except Exception as e:
        logger.error(f"🌐 [API] 모니터링 상태 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="모니터링 상태 조회 중 오류가 발생했습니다.")

@app.get("/chart/image/{stock_code}")
async def get_chart_image(stock_code: str, period: str = "1M"):
    try:
        # 1. 키움 API에서 데이터 가져오기
        chart_data = await kiwoom_api.get_stock_chart_data(stock_code, "1D")
        
        if not chart_data:
            raise HTTPException(status_code=404, detail="차트 데이터가 없습니다")
        
        # 2. DataFrame으로 변환 (chart_data는 이미 리스트)
        df = pd.DataFrame(chart_data)
        
        # 3. 날짜 컬럼을 인덱스로 설정
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        
        # 3-1. 기간에 따른 데이터 필터링
        df = df.sort_index()
        if period == "1Y":
            df = df.tail(250)  # 1년치 데이터 (약 250 거래일)
        elif period == "1M":
            df = df.tail(30)   # 1개월치 데이터
        elif period == "1W":
            df = df.tail(7)    # 1주치 데이터
        else:
            df = df.tail(500)  # 기본값 (약 2년치)
        
        # 4. mplfinance에 필요한 컬럼명으로 변경
        df = df.rename(columns={
            'open': 'Open',
            'high': 'High', 
            'low': 'Low',
            'close': 'Close',
            'volume': 'Volume'
        })
        
        # 4-1. 일목균형표 데이터 생성 (경고 억제)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            id_ichimoku = IchimokuIndicator(high=df['High'], low=df['Low'], visual=True, fillna=True)
            df['span_a'] = id_ichimoku.ichimoku_a()
            df['span_b'] = id_ichimoku.ichimoku_b()
            df['base_line'] = id_ichimoku.ichimoku_base_line()
            df['conv_line'] = id_ichimoku.ichimoku_conversion_line()
        
        # 5. 색상 설정
        mc = mpf.make_marketcolors(
            up="red",
            down="blue",
            volume="inherit"
        )
        
        # 6. 일목균형표 그래프 추가
        added_plots = [
            mpf.make_addplot(df['span_a'], color='orange', alpha=0.7, width=1.5),
            mpf.make_addplot(df['span_b'], color='purple', alpha=0.7, width=1.5),
            mpf.make_addplot(df['base_line'], color='green', alpha=0.8, width=2),
            mpf.make_addplot(df['conv_line'], color='red', alpha=0.8, width=2)
        ]
        
        # 7. 스타일 설정
        s = mpf.make_mpf_style(
            base_mpf_style="charles",
            marketcolors=mc,
            gridaxis='both',
            y_on_right=True,
            facecolor='white',
            edgecolor='black'
        )
        
        # 8. 차트 생성 (메모리에 저장)
        buf = io.BytesIO()
        fig, axes = mpf.plot(
            data=df,
            type='candle',
            style=s,
            figratio=(18, 10),  # 차트 크기 증가
            mav=(20, 60),  # 이동평균 20일선, 60일선으로 변경
            volume=True,
            scale_width_adjustment=dict(volume=0.6, candle=1.2),
            addplot=added_plots,
            savefig=dict(fname=buf, format='png', dpi=200, bbox_inches='tight'),  # DPI 증가
            returnfig=True,
            tight_layout=True
        )
        
        # 8-1. 범례 추가 (수정된 버전)
        if fig and axes and len(axes) > 0:
            try:
                # 메인 차트에 범례 추가 - mlines.Line2D 사용
                legend_elements = [
                    mlines.Line2D([0], [0], color='orange', lw=2, alpha=0.7, label='선행스팬A'),
                    mlines.Line2D([0], [0], color='purple', lw=2, alpha=0.7, label='선행스팬B'),
                    mlines.Line2D([0], [0], color='green', lw=2, alpha=0.8, label='기준선'),
                    mlines.Line2D([0], [0], color='red', lw=2, alpha=0.8, label='전환선'),
                    mlines.Line2D([0], [0], color='blue', lw=1, label='20일 이평선'),
                    mlines.Line2D([0], [0], color='orange', lw=1, label='60일 이평선')
                ]
                
                axes[0].legend(
                    handles=legend_elements,
                    loc='upper left',
                    fontsize=10,
                    frameon=True,
                    fancybox=True,
                    shadow=True,
                    ncol=3,
                    bbox_to_anchor=(0, 1)
                )
            except Exception as legend_error:
                logger.warning(f"Legend 설정 오류: {legend_error}")
        
        buf.seek(0)
        
        # 9. 이미지를 base64로 인코딩
        img_base64 = base64.b64encode(buf.getvalue()).decode('utf-8')
        buf.close()
        
        # matplotlib figure 메모리 정리
        if fig:
            plt.close(fig)
        
        return {"image": f"data:image/png;base64,{img_base64}"}
        
    except Exception as e:
        logger.error(f"차트 생성 오류: {e}")
        raise HTTPException(status_code=500, detail=f"차트 생성 실패: {str(e)}")

@app.get("/stocks/{stock_code}/news")
async def get_stock_news(stock_code: str, stock_name: str = None):
    """
    네이버 뉴스 검색 API를 사용하여 종목 관련 뉴스 조회
    """
    try:
        # API 키 확인 - 없으면 조용히 빈 결과 반환
        if not config.NAVER_CLIENT_ID or not config.NAVER_CLIENT_SECRET:
            return {
                "items": [],
                "total": 0,
                "start": 1,
                "display": 0
            }
        
        # 검색 쿼리 생성
        query = stock_name if stock_name else stock_code
        
        # 네이버 뉴스 검색 API 호출
        headers = {
            "X-Naver-Client-Id": config.NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": config.NAVER_CLIENT_SECRET
        }
        
        params = {
            "query": f"{query} 주식",
            "display": 10,
            "start": 1,
            "sort": "date"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                config.NAVER_NEWS_API_URL,
                headers=headers,
                params=params,
                timeout=10.0
            )
            
        if response.status_code != 200:
            return {
                "items": [],
                "total": 0,
                "start": 1,
                "display": 0
            }
            
        news_data = response.json()
        
        # HTML 태그 제거
        if "items" in news_data:
            for item in news_data["items"]:
                item["title"] = re.sub(r'<[^>]+>', '', item["title"])
                item["description"] = re.sub(r'<[^>]+>', '', item["description"])
                
                if "pubDate" in item:
                    try:
                        pub_date = datetime.strptime(item["pubDate"], "%a, %d %b %Y %H:%M:%S %z")
                        item["pubDate"] = pub_date.strftime("%Y-%m-%d %H:%M")
                    except:
                        pass
        
        return news_data
        
    except Exception as e:
        # 에러 발생시에도 조용히 빈 결과 반환
        return {
            "items": [],
            "total": 0,
            "start": 1,
            "display": 0
        }

@app.get("/stocks/{stock_code}/discussions")
async def get_stock_discussions(stock_code: str, page: int = 1, max_pages: int = 2):
    """
    네이버 종목토론방에서 토론 글 조회
    """
    try:
        logger.info(f"🌐 [API] 종목토론 조회 시작 - 종목코드: {stock_code}, 페이지: {page}")
        
        # 네이버 토론 크롤링 (당일 글만, 최대 2페이지)
        discussions = discussion_crawler.crawl_discussion_posts(
            stock_code=stock_code,
            page=page,
            max_pages=max_pages,
            today_only=True
        )
        
        logger.info(f"🌐 [API] 종목토론 조회 완료 - {len(discussions)}개 글")
        
        return {
            "stock_code": stock_code,
            "discussions": discussions,
            "total_count": len(discussions),
            "page": page,
            "max_pages": max_pages
        }
        
    except Exception as e:
        logger.error(f"🌐 [API] 종목토론 조회 오류: {e}")
        return {
            "stock_code": stock_code,
            "discussions": [],
            "total_count": 0,
            "page": page,
            "max_pages": max_pages,
            "error": str(e)
        }

@app.get("/stocks/{stock_code}/info")
async def get_stock_info(stock_code: str, stock_name: str = None):
    """
    종목의 뉴스와 토론 글을 함께 조회
    """
    try:
        logger.info(f"🌐 [API] 종목 정보 조회 시작 - 종목코드: {stock_code}, 종목명: {stock_name}")
        
        # 뉴스와 토론 글을 병렬로 조회
        import asyncio
        
        # 뉴스 조회
        news_task = get_stock_news(stock_code, stock_name)
        
        # 토론 글 조회
        discussions_task = get_stock_discussions(stock_code, page=1, max_pages=2)
        
        # 병렬 실행
        news_data, discussions_data = await asyncio.gather(
            news_task,
            discussions_task,
            return_exceptions=True
        )
        
        # 예외 처리
        if isinstance(news_data, Exception):
            logger.error(f"뉴스 조회 오류: {news_data}")
            news_data = {"items": [], "total": 0, "start": 1, "display": 0}
            
        if isinstance(discussions_data, Exception):
            logger.error(f"토론 조회 오류: {discussions_data}")
            discussions_data = {"discussions": [], "total_count": 0}
        
        result = {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "news": news_data,
            "discussions": discussions_data,
            "timestamp": datetime.now().isoformat()
        }
        
        logger.info(f"🌐 [API] 종목 정보 조회 완료 - 뉴스: {len(news_data.get('items', []))}개, 토론: {len(discussions_data.get('discussions', []))}개")
        
        return result
        
    except Exception as e:
        logger.error(f"🌐 [API] 종목 정보 조회 오류: {e}")
        return {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "news": {"items": [], "total": 0, "start": 1, "display": 0},
            "discussions": {"discussions": [], "total_count": 0},
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }

@app.get("/stocks/{stock_code}/snapshot")
async def get_stock_snapshot(stock_code: str, include_debug: bool = False):
    """현재가/전일대비/등락률/거래량 + 10호가 스냅샷 조회."""
    try:
        logger.info(f"🌐 [API] 종목 스냅샷 조회 시작 - 종목코드: {stock_code}, include_debug={include_debug}")
        result = await kiwoom_api.get_stock_snapshot(stock_code)
        if not result.get("success"):
            logger.warning(f"🌐 [API] 종목 스냅샷 실패 - 종목코드: {stock_code}, reason={result.get('error')}")
            return {
                "success": False,
                "message": result.get("error", "snapshot lookup failed"),
                "stock_code": stock_code,
                "timestamp": datetime.now().isoformat(),
            }

        snapshot = result.get("snapshot", {})
        response_payload = {
            "success": True,
            "stock_code": snapshot.get("stock_code", stock_code),
            "stock_name": snapshot.get("stock_name", ""),
            "current_price": snapshot.get("current_price", 0),
            "price_diff": snapshot.get("price_diff", 0),
            "change_rate": snapshot.get("change_rate", "0"),
            "volume": snapshot.get("volume", 0),
            "orderbook_time": snapshot.get("orderbook_time", ""),
            "orderbook": snapshot.get("orderbook", []),
            "warnings": snapshot.get("warnings", []),
            "timestamp": datetime.now().isoformat(),
        }
        if include_debug:
            response_payload["debug"] = {
                "raw_basic": snapshot.get("raw_basic", {}),
                "raw_quote": snapshot.get("raw_quote", {}),
            }
        logger.info(
            f"🌐 [API] 종목 스냅샷 완료 - code={stock_code}, "
            f"price={response_payload['current_price']}, volume={response_payload['volume']}, "
            f"orderbook_rows={len(response_payload['orderbook'])}, warnings={response_payload['warnings']}"
        )
        return response_payload
    except Exception as e:
        logger.error(f"🌐 [API] 종목 스냅샷 조회 오류: {e}")
        return {
            "success": False,
            "message": f"snapshot endpoint error: {str(e)}",
            "stock_code": stock_code,
            "timestamp": datetime.now().isoformat(),
        }

@app.get("/api/status")
async def get_status():
    logger.info("🔄 [DEBUG] API 상태 체크 요청")
    logger.info(f"🔄 [DEBUG] kiwoom_api.running: {kiwoom_api.running}")
    logger.info(f"🔄 [DEBUG] kiwoom_api.websocket: {kiwoom_api.websocket}")
    logger.info(f"🔄 [DEBUG] kiwoom_api.websocket is not None: {kiwoom_api.websocket is not None}")
    
    return {
        "running": kiwoom_api.running,
        "websocket_connected": kiwoom_api.websocket is not None,
        "token_valid": kiwoom_api.token_manager.is_token_valid(),
        "api_rate_limit": api_rate_limiter.get_status_info()
    }

@app.get("/api/rate-limit-status")
async def get_rate_limit_status():
    """API 제한 상태 상세 조회"""
    try:
        status_info = api_rate_limiter.get_status_info()
        
        # 로그에도 현재 상태 출력
        api_rate_limiter.log_current_status()
        
        return JSONResponse(content=status_info, media_type="application/json; charset=utf-8")
        
    except Exception as e:
        logger.error(f"API 제한 상태 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="API 제한 상태 조회 중 오류가 발생했습니다.")

@app.get("/account/balance")
async def get_account_balance():
    """계좌 잔고 정보 조회 - 키움 API kt00004 스펙 기반"""
    try:
        # 모의투자 계좌 사용 여부 확인
        use_mock_account = config.KIWOOM_USE_MOCK_ACCOUNT
        account_number = config.KIWOOM_MOCK_ACCOUNT_NUMBER if use_mock_account else config.KIWOOM_ACCOUNT_NUMBER
        account_type = "모의투자" if use_mock_account else "실계좌"
        
        logger.info(f"🌐 [API] 계좌 설정 - 타입: {account_type}, 번호: {account_number}")
        logger.info(f"🌐 [API] 계좌 정보 조회 - {account_type} 계좌: {account_number}")
        
        # 키움 API 상태 상세 로깅 (디버깅용)
        logger.debug(f"=== 키움 API 상태 확인 ===")
        logger.debug(f"WebSocket running: {kiwoom_api.running}")
        logger.debug(f"WebSocket 객체: {kiwoom_api.websocket is not None}")
        logger.debug(f"REST API 토큰 유효성: {bool(kiwoom_api.token_manager.get_valid_token())}")
        
        # 키움 API 토큰 유효성 확인 (REST API는 WebSocket과 독립적)
        token_valid = bool(kiwoom_api.token_manager.get_valid_token())
        logger.info(f"🌐 [API] REST API 토큰 유효성: {token_valid}")
        
        if not token_valid:
            logger.warning("🌐 [API] 키움 API 토큰이 유효하지 않습니다. 빈 데이터를 반환합니다.")
            # API 연결 실패 시 빈 데이터 반환
            balance_data = {
                "acnt_nm": "",
                "brch_nm": "",
                "acnt_no": account_number,
                "acnt_type": account_type,
                "entr": "0",
                "d2_entra": "0",
                "tot_est_amt": "0",
                "aset_evlt_amt": "0",
                "tot_pur_amt": "0",
                "prsm_dpst_aset_amt": "0",
                "tot_grnt_sella": "0",
                "tdy_lspft_amt": "0",
                "invt_bsamt": "0",
                "lspft_amt": "0",
                "tdy_lspft": "0",
                "lspft2": "0",
                "lspft": "0",
                "tdy_lspft_rt": "0.00",
                "lspft_ratio": "0.00",
                "lspft_rt": "0.00",
                "_data_source": "API_ERROR",
                "_api_connected": False,
                "_token_valid": False,
                "_account_type": account_type
            }
        else:
            # 실제 키움 API 호출 (모의투자 계좌 사용)
            logger.info(f"🌐 [API] 키움 REST API에서 {account_type} 계좌 정보 조회 중...")
            balance_data = await kiwoom_api.get_account_balance(account_number)
            
            if not balance_data:
                logger.warning("🌐 [API] 키움 REST API 호출 실패, 빈 데이터를 반환합니다.")
                balance_data = {
                    "acnt_nm": "",
                    "brch_nm": "",
                    "acnt_no": account_number,
                    "acnt_type": account_type,
                    "entr": "0",
                    "d2_entra": "0",
                    "tot_est_amt": "0",
                    "aset_evlt_amt": "0",
                    "tot_pur_amt": "0",
                    "prsm_dpst_aset_amt": "0",
                    "tot_grnt_sella": "0",
                    "tdy_lspft_amt": "0",
                    "invt_bsamt": "0",
                    "lspft_amt": "0",
                    "tdy_lspft": "0",
                    "lspft2": "0",
                    "lspft": "0",
                    "tdy_lspft_rt": "0.00",
                    "lspft_ratio": "0.00",
                    "lspft_rt": "0.00",
                    "_data_source": "API_ERROR",
                    "_api_connected": False,
                    "_token_valid": False,
                    "_account_type": account_type
                }
            else:
                balance_data["_data_source"] = "REAL_API"
                balance_data["_api_connected"] = True
                balance_data["_token_valid"] = True
                balance_data["_account_type"] = account_type
                balance_data["acnt_no"] = account_number
                logger.info(f"🌐 [API] 키움 REST API {account_type} 계좌 정보 조회 성공")
        
        logger.info(f"{account_type} 계좌 잔고 정보 조회 완료")
        return balance_data
        
    except Exception as e:
        logger.error(f"계좌 잔고 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="계좌 잔고 조회 중 오류가 발생했습니다.")

@app.get("/account/holdings")
async def get_account_holdings():
    """보유종목 정보 조회 - 키움 API kt00004 스펙 기반"""
    try:
        # 모의투자 계좌 사용 여부 확인
        use_mock_account = config.KIWOOM_USE_MOCK_ACCOUNT
        account_number = config.KIWOOM_MOCK_ACCOUNT_NUMBER if use_mock_account else config.KIWOOM_ACCOUNT_NUMBER
        account_type = "모의투자" if use_mock_account else "실계좌"
        
        logger.info(f"🌐 [API] 계좌 설정 - 타입: {account_type}, 번호: {account_number}")
        logger.info(f"🌐 [API] 보유종목 조회 - {account_type} 계좌: {account_number}")
        
        # 키움 API 토큰 유효성 확인 (REST API는 WebSocket과 독립적)
        token_valid = bool(kiwoom_api.token_manager.get_valid_token())
        logger.info(f"🌐 [API] REST API 토큰 유효성: {token_valid}")
        
        if not token_valid:
            logger.warning("🌐 [API] 키움 API 토큰이 유효하지 않습니다. 빈 데이터를 반환합니다.")
            # API 연결 실패 시 빈 데이터 반환
            holdings_data = {
                "acnt_no": account_number,
                "acnt_type": account_type,
                "stk_acnt_evlt_prst": [],
                "_data_source": "API_ERROR",
                "_api_connected": False,
                "_token_valid": False,
                "_account_type": account_type
            }
        else:
            # 실제 키움 API에서 보유종목 조회 (모의투자 계좌 사용)
            logger.info(f"🌐 [API] 키움 REST API에서 {account_type} 보유종목 조회 중...")
            balance_data = await kiwoom_api.get_account_balance(account_number)
            
            if balance_data and 'stk_acnt_evlt_prst' in balance_data:
                holdings_data = {
                    "acnt_no": account_number,
                    "acnt_type": account_type,
                    "stk_acnt_evlt_prst": balance_data['stk_acnt_evlt_prst']
                }
                logger.info(f"🌐 [API] 실제 {account_type} 보유종목 {len(holdings_data['stk_acnt_evlt_prst'])}건 조회 성공")
            else:
                logger.warning("🌐 [API] 보유종목 데이터가 없습니다. 빈 목록을 반환합니다.")
                holdings_data = {
                    "acnt_no": account_number,
                    "acnt_type": account_type,
                    "stk_acnt_evlt_prst": []
                }
        
        logger.info(f"{account_type} 보유종목 {len(holdings_data['stk_acnt_evlt_prst'])}건 조회 완료")
        return holdings_data
        
    except Exception as e:
        logger.error(f"보유종목 조회 오류: {e}")
        return {
            "error": str(e),
            "acnt_no": config.KIWOOM_MOCK_ACCOUNT_NUMBER if config.KIWOOM_USE_MOCK_ACCOUNT else config.KIWOOM_ACCOUNT_NUMBER,
            "acnt_type": "모의투자" if config.KIWOOM_USE_MOCK_ACCOUNT else "실계좌",
            "stk_acnt_evlt_prst": []
        }
@app.get("/account/profit")
async def get_account_profit(limit: int = 200, stex_tp: str = "0"):
    """보유종목 수익현황(ka10085)"""
    try:
        token_valid = bool(kiwoom_api.token_manager.get_valid_token())
        logger.info(f"🌐 [API] REST API 토큰 유효성: {token_valid}")

        if not token_valid:
            logger.warning("🌐 [API] 토큰 없음 - 빈 데이터 반환")
            return {
                "positions": [],
                "_data_source": "API_ERROR",
                "_api_connected": False,
                "_token_valid": False
            }

        result = await kiwoom_api.get_account_profit(stex_tp=stex_tp, limit=limit)
        logger.info(f"보유종목 수익현황 {len(result.get('positions', []))}건")
        return result

    except Exception as e:
        logger.error(f"보유종목 수익현황 조회 오류: {e}")
        return {"positions": [], "_data_source": "API_ERROR"}

# 매수 주문 관련 API
class BuyOrderRequest(BaseModel):
    stock_code: str
    quantity: int
    price: int = 0  # 0이면 시장가
    order_type: str = "3"  # "3": 시장가, "0": 지정가(보통)  (키움 kt10000 기준)

@app.post("/trading/buy")
async def place_buy_order(req: BuyOrderRequest):
    """주식 매수 주문"""
    try:
        logger.info(f"매수 주문 요청: {req.stock_code}, 수량: {req.quantity}, 가격: {req.price}")

        # UI에서 들어오는 코드(01/00) 호환: 01(시장가)->3, 00(지정가)->0
        mapped_order_type = req.order_type
        if mapped_order_type in ("01", "market", "MARKET"):
            mapped_order_type = "3"
        elif mapped_order_type in ("00", "limit", "LIMIT"):
            mapped_order_type = "0"
        
        result = await kiwoom_api.place_buy_order(
            stock_code=req.stock_code,
            quantity=req.quantity,
            price=req.price,
            order_type=mapped_order_type
        )
        
        if result.get("success"):
            logger.info(f"매수 주문 성공: {req.stock_code}")
            return {
                "success": True,
                "message": "매수 주문이 성공적으로 접수되었습니다.",
                "order_id": result.get("order_id", ""),
                "stock_code": req.stock_code,
                "quantity": req.quantity,
                "price": req.price
            }
        else:
            logger.error(f"매수 주문 실패: {req.stock_code} - {result.get('error')}")
            return {
                "success": False,
                "message": f"매수 주문 실패: {result.get('error')}",
                "stock_code": req.stock_code
            }
            
    except Exception as e:
        logger.error(f"매수 주문 API 오류: {e}")
        raise HTTPException(status_code=500, detail="매수 주문 중 오류가 발생했습니다.")

@app.get("/trading/orders")
async def get_order_history():
    """주문 내역 조회"""
    try:
        # 매수대기 테이블에서 주문 내역 조회
        orders = []
        for db in get_db():
            session: Session = db
            rows = session.query(PendingBuySignal).filter(
                PendingBuySignal.status.in_(["ORDERED", "FAILED"])
            ).order_by(PendingBuySignal.detected_at.desc()).limit(50).all()
            
            orders = [
                {
                    "id": row.id,
                    "stock_code": row.stock_code,
                    "stock_name": row.stock_name,
                    "status": row.status,
                    "detected_at": row.detected_at.isoformat() if row.detected_at else None,
                    "condition_id": row.condition_id
                }
                for row in rows
            ]
            break
        
        return {
            "orders": orders,
            "total": len(orders)
        }
        
    except Exception as e:
        logger.error(f"주문 내역 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="주문 내역 조회 중 오류가 발생했습니다.")

# 개선된 시스템 관련 API 엔드포인트들
@app.get("/api/rate-limiter/status")
async def get_api_rate_limiter_status():
    """API 제한 상태 조회"""
    try:
        status = api_rate_limiter.get_status_info()
        return status
    except Exception as e:
        logger.error(f"API 제한 상태 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="API 제한 상태 조회 중 오류가 발생했습니다.")

@app.post("/api/rate-limiter/reset")
async def reset_api_rate_limiter():
    """API 제한 상태 수동 리셋"""
    try:
        api_rate_limiter.reset_limits()
        return {"message": "API 제한 상태가 리셋되었습니다."}
    except Exception as e:
        logger.error(f"API 제한 상태 리셋 오류: {e}")
        raise HTTPException(status_code=500, detail="API 제한 상태 리셋 중 오류가 발생했습니다.")

@app.get("/signals/statistics")
async def get_signal_statistics():
    """신호 통계 조회"""
    try:
        stats = await signal_manager.get_signal_statistics()
        return stats
    except Exception as e:
        logger.error(f"신호 통계 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="신호 통계 조회 중 오류가 발생했습니다.")

@app.post("/signals/cleanup")
async def cleanup_old_signals(days: int = 7):
    """오래된 신호 정리"""
    try:
        deleted_count = await signal_manager.cleanup_old_signals(days)
        return {
            "message": f"오래된 신호 {deleted_count}개가 정리되었습니다.",
            "deleted_count": deleted_count
        }
    except Exception as e:
        logger.error(f"신호 정리 오류: {e}")
        raise HTTPException(status_code=500, detail="신호 정리 중 오류가 발생했습니다.")

@app.post("/signals/cleanup-failed")
async def cleanup_failed_signals():
    """실패한 신호 일괄 정리"""
    try:
        deleted_count = 0
        for db in get_db():
            session: Session = db
            # FAILED 상태인 Signal 조회
            failed_signals = session.query(PendingBuySignal).filter(
                PendingBuySignal.status == "FAILED"
            ).all()
            
            # 관련 Position이 없는 Signal만 삭제
            for signal in failed_signals:
                position = session.query(Position).filter(Position.signal_id == signal.id).first()
                if not position:  # Position이 없으면 삭제
                    session.delete(signal)
                    deleted_count += 1
            
            session.commit()
            break
        
        logger.info(f"🗑️ [API] 실패한 신호 {deleted_count}개 정리 완료")
        return {
            "message": f"실패한 신호 {deleted_count}개가 정리되었습니다.",
            "deleted_count": deleted_count
        }
    except Exception as e:
        logger.error(f"실패 신호 정리 오류: {e}")
        raise HTTPException(status_code=500, detail="실패 신호 정리 중 오류가 발생했습니다.")

@app.get("/buy-executor/status")
async def get_buy_executor_status():
    """매수 주문 실행기 상태 조회"""
    try:
        status = {
            "is_running": buy_order_executor.is_running,
            "auto_trade_settings_loaded": buy_order_executor.auto_trade_settings is not None,
            "auto_trade_enabled": buy_order_executor.auto_trade_settings.is_enabled if buy_order_executor.auto_trade_settings else False,
            "max_invest_amount": buy_order_executor.auto_trade_settings.max_invest_amount if buy_order_executor.auto_trade_settings else 0,
            "max_retry_attempts": buy_order_executor.max_retry_attempts,
            "retry_delay_seconds": buy_order_executor.retry_delay_seconds
        }
        return status
    except Exception as e:
        logger.error(f"매수 주문 실행기 상태 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="매수 주문 실행기 상태 조회 중 오류가 발생했습니다.")

@app.post("/buy-executor/start")
async def start_buy_executor():
    """매수 주문 실행기 시작"""
    try:
        if not buy_order_executor.is_running:
            asyncio.create_task(buy_order_executor.start_processing())
            return {"message": "매수 주문 실행기가 시작되었습니다."}
        else:
            return {"message": "매수 주문 실행기가 이미 실행 중입니다."}
    except Exception as e:
        logger.error(f"매수 주문 실행기 시작 오류: {e}")
        raise HTTPException(status_code=500, detail="매수 주문 실행기 시작 중 오류가 발생했습니다.")

@app.post("/buy-executor/stop")
async def stop_buy_executor():
    """매수 주문 실행기 중지"""
    try:
        await buy_order_executor.stop_processing()
        return {"message": "매수 주문 실행기가 중지되었습니다."}
    except Exception as e:
        logger.error(f"매수 주문 실행기 중지 오류: {e}")
        raise HTTPException(status_code=500, detail="매수 주문 실행기 중지 중 오류가 발생했습니다.")

# ===== 관심종목 관리 API =====

@app.get("/watchlist/")
async def get_watchlist():
    """관심종목 목록 조회 (수기등록과 조건식 종목 구분)"""
    try:
        for db in get_db():
            session: Session = db
            watchlist = session.query(WatchlistStock).order_by(WatchlistStock.added_at.desc()).all()
            
            result = []
            for stock in watchlist:
                result.append({
                    "id": stock.id,
                    "stock_code": stock.stock_code,
                    "stock_name": stock.stock_name,
                    "added_at": stock.added_at.isoformat(),
                    "is_active": stock.is_active,
                    "notes": stock.notes,
                    "source_type": stock.source_type,
                    "condition_id": stock.condition_id,
                    "condition_name": stock.condition_name,
                    "last_condition_check": stock.last_condition_check.isoformat() if stock.last_condition_check else None,
                    "condition_status": stock.condition_status
                })
            
            return {"watchlist": result}
    except Exception as e:
        logger.error(f"관심종목 목록 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="관심종목 목록 조회 중 오류가 발생했습니다.")

@app.post("/watchlist/add")
async def add_watchlist_stock(req: WatchlistAddRequest):
    """관심종목 추가"""
    try:
        for db in get_db():
            session: Session = db
            
            # 중복 확인
            existing = session.query(WatchlistStock).filter(
                WatchlistStock.stock_code == req.stock_code
            ).first()
            
            if existing:
                raise HTTPException(status_code=400, detail=f"이미 관심종목에 등록된 종목입니다: {req.stock_code}")
            
            # 새 관심종목 추가 (수기등록으로 표시)
            new_stock = WatchlistStock(
                stock_code=req.stock_code,
                stock_name=req.stock_name,
                notes=req.notes,
                is_active=True,
                source_type="MANUAL"
            )
            
            session.add(new_stock)
            session.commit()
            
            logger.info(f"관심종목 추가 완료: {req.stock_name}({req.stock_code})")
            return {"message": f"관심종목이 추가되었습니다: {req.stock_name}({req.stock_code})"}
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"관심종목 추가 오류: {e}")
        raise HTTPException(status_code=500, detail="관심종목 추가 중 오류가 발생했습니다.")

@app.delete("/watchlist/{stock_code}")
async def remove_watchlist_stock(stock_code: str):
    """관심종목 제거"""
    try:
        for db in get_db():
            session: Session = db
            
            stock = session.query(WatchlistStock).filter(
                WatchlistStock.stock_code == stock_code
            ).first()
            
            if not stock:
                raise HTTPException(status_code=404, detail=f"관심종목을 찾을 수 없습니다: {stock_code}")
            
            session.delete(stock)
            session.commit()
            
            logger.info(f"관심종목 제거 완료: {stock.stock_name}({stock_code})")
            return {"message": f"관심종목이 제거되었습니다: {stock.stock_name}({stock_code})"}
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"관심종목 제거 오류: {e}")
        raise HTTPException(status_code=500, detail="관심종목 제거 중 오류가 발생했습니다.")

@app.put("/watchlist/{stock_code}/toggle")
async def toggle_watchlist_stock(stock_code: str, req: WatchlistToggleRequest):
    """관심종목 활성화/비활성화"""
    try:
        for db in get_db():
            session: Session = db
            
            stock = session.query(WatchlistStock).filter(
                WatchlistStock.stock_code == stock_code
            ).first()
            
            if not stock:
                raise HTTPException(status_code=404, detail=f"관심종목을 찾을 수 없습니다: {stock_code}")
            
            stock.is_active = req.is_active
            session.commit()
            
            status = "활성화" if req.is_active else "비활성화"
            logger.info(f"관심종목 {status} 완료: {stock.stock_name}({stock_code})")
            return {"message": f"관심종목이 {status}되었습니다: {stock.stock_name}({stock_code})"}
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"관심종목 토글 오류: {e}")
        raise HTTPException(status_code=500, detail="관심종목 토글 중 오류가 발생했습니다.")

# ===== 전략 설정 관리 API =====

@app.get("/strategies/")
async def get_strategies():
    """전략 목록 조회"""
    try:
        for db in get_db():
            session: Session = db
            strategies = session.query(TradingStrategy).order_by(TradingStrategy.strategy_type).all()
            
            result = []
            for strategy in strategies:
                result.append({
                    "id": strategy.id,
                    "strategy_name": strategy.strategy_name,
                    "strategy_type": strategy.strategy_type,
                    "is_enabled": strategy.is_enabled,
                    "parameters": strategy.parameters,
                    "updated_at": strategy.updated_at.isoformat()
                })
            
            return {"strategies": result}
    except Exception as e:
        logger.error(f"전략 목록 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="전략 목록 조회 중 오류가 발생했습니다.")

@app.post("/strategies/{strategy_type}/configure")
async def configure_strategy(strategy_type: str, req: StrategyConfigureRequest):
    """전략 파라미터 설정"""
    try:
        valid_types = ["MOMENTUM", "DISPARITY", "BOLLINGER", "RSI", "CHAIKIN"]
        if strategy_type not in valid_types:
            raise HTTPException(status_code=400, detail=f"유효하지 않은 전략 타입입니다: {strategy_type}")
        
        for db in get_db():
            session: Session = db
            
            strategy = session.query(TradingStrategy).filter(
                TradingStrategy.strategy_type == strategy_type
            ).first()
            
            if not strategy:
                raise HTTPException(status_code=404, detail=f"전략을 찾을 수 없습니다: {strategy_type}")
            
            strategy.parameters = req.parameters
            strategy.updated_at = datetime.utcnow()
            session.commit()
            
            logger.info(f"전략 파라미터 설정 완료: {strategy.strategy_name}")
            return {"message": f"전략 파라미터가 설정되었습니다: {strategy.strategy_name}"}
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"전략 파라미터 설정 오류: {e}")
        raise HTTPException(status_code=500, detail="전략 파라미터 설정 중 오류가 발생했습니다.")

@app.put("/strategies/{strategy_id}/toggle")
async def toggle_strategy(strategy_id: int, req: StrategyToggleRequest):
    """전략 활성화/비활성화"""
    try:
        for db in get_db():
            session: Session = db
            
            strategy = session.query(TradingStrategy).filter(
                TradingStrategy.id == strategy_id
            ).first()
            
            if not strategy:
                raise HTTPException(status_code=404, detail=f"전략을 찾을 수 없습니다: {strategy_id}")
            
            strategy.is_enabled = req.is_enabled
            strategy.updated_at = datetime.utcnow()
            session.commit()
            
            status = "활성화" if req.is_enabled else "비활성화"
            logger.info(f"전략 {status} 완료: {strategy.strategy_name}")
            return {"message": f"전략이 {status}되었습니다: {strategy.strategy_name}"}
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"전략 토글 오류: {e}")
        raise HTTPException(status_code=500, detail="전략 토글 중 오류가 발생했습니다.")

# ===== 전략 모니터링 관리 API =====

@app.post("/strategy/start")
async def start_strategy_monitoring():
    """전략 모니터링 시작"""
    try:
        await strategy_manager.start_strategy_monitoring()
        return {"message": "전략 모니터링이 시작되었습니다."}
    except Exception as e:
        logger.error(f"전략 모니터링 시작 오류: {e}")
        raise HTTPException(status_code=500, detail="전략 모니터링 시작 중 오류가 발생했습니다.")

@app.post("/strategy/stop")
async def stop_strategy_monitoring():
    """전략 모니터링 중지"""
    try:
        await strategy_manager.stop_strategy_monitoring()
        return {"message": "전략 모니터링이 중지되었습니다."}
    except Exception as e:
        logger.error(f"전략 모니터링 중지 오류: {e}")
        raise HTTPException(status_code=500, detail="전략 모니터링 중지 중 오류가 발생했습니다.")

@app.get("/strategy/status")
async def get_strategy_status():
    """전략 모니터링 상태 조회"""
    try:
        # strategy_manager에서 상태 조회
        status = await strategy_manager.get_monitoring_status()
        return status
    except Exception as e:
        logger.error(f"전략 모니터링 상태 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="전략 모니터링 상태 조회 중 오류가 발생했습니다.")

# ===== 전략 신호 조회 API =====

@app.get("/signals/by-strategy/{strategy_id}")
async def get_strategy_signals(strategy_id: int, limit: int = 50):
    """특정 전략의 신호 조회"""
    try:
        for db in get_db():
            session: Session = db
            
            signals = session.query(StrategySignal).filter(
                StrategySignal.strategy_id == strategy_id
            ).order_by(StrategySignal.detected_at.desc()).limit(limit).all()
            
            result = []
            for signal in signals:
                result.append({
                    "id": signal.id,
                    "stock_code": signal.stock_code,
                    "stock_name": signal.stock_name,
                    "signal_type": signal.signal_type,
                    "signal_value": signal.signal_value,
                    "detected_at": signal.detected_at.isoformat(),
                    "status": signal.status,
                    "additional_data": signal.additional_data
                })
            
            return {"signals": result}
    except Exception as e:
        logger.error(f"전략 신호 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="전략 신호 조회 중 오류가 발생했습니다.")

# ===== 관심종목 동기화 관리 API =====

@app.post("/watchlist/sync/start")
async def start_watchlist_sync():
    """관심종목 동기화 시작"""
    try:
        await watchlist_sync_manager.start_auto_sync()
        return {"message": "관심종목 동기화가 시작되었습니다."}
    except Exception as e:
        logger.error(f"관심종목 동기화 시작 오류: {e}")
        raise HTTPException(status_code=500, detail="관심종목 동기화 시작 중 오류가 발생했습니다.")

@app.post("/watchlist/sync/stop")
async def stop_watchlist_sync():
    """관심종목 동기화 중지"""
    try:
        await watchlist_sync_manager.stop_auto_sync()
        return {"message": "관심종목 동기화가 중지되었습니다."}
    except Exception as e:
        logger.error(f"관심종목 동기화 중지 오류: {e}")
        raise HTTPException(status_code=500, detail="관심종목 동기화 중지 중 오류가 발생했습니다.")

@app.get("/watchlist/sync/status")
async def get_watchlist_sync_status():
    """관심종목 동기화 상태 조회"""
    try:
        status = await watchlist_sync_manager.get_sync_status()
        return status
    except Exception as e:
        logger.error(f"관심종목 동기화 상태 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="관심종목 동기화 상태 조회 중 오류가 발생했습니다.")

@app.post("/watchlist/sync/manual")
async def manual_watchlist_sync():
    """관심종목 수동 동기화"""
    try:
        await watchlist_sync_manager.sync_all_conditions()
        return {"message": "관심종목 수동 동기화가 완료되었습니다."}
    except Exception as e:
        logger.error(f"관심종목 수동 동기화 오류: {e}")
        raise HTTPException(status_code=500, detail="관심종목 수동 동기화 중 오류가 발생했습니다.")

@app.get("/watchlist/sync/config")
async def get_watchlist_sync_config():
    """관심종목 동기화 설정 조회"""
    return {
        "target_condition_names": watchlist_sync_manager.target_condition_names,
        "sync_only_target_conditions": watchlist_sync_manager.sync_only_target_conditions,
        "auto_sync_enabled": watchlist_sync_manager.auto_sync_enabled,
        "remove_expired_stocks": watchlist_sync_manager.remove_expired_stocks,
        "expired_threshold_hours": watchlist_sync_manager.expired_threshold_hours
    }

@app.post("/watchlist/sync/config")
async def update_watchlist_sync_config(config: dict):
    """관심종목 동기화 설정 업데이트"""
    try:
        if "target_condition_names" in config:
            watchlist_sync_manager.target_condition_names = config["target_condition_names"]
        if "sync_only_target_conditions" in config:
            watchlist_sync_manager.sync_only_target_conditions = config["sync_only_target_conditions"]
        if "auto_sync_enabled" in config:
            watchlist_sync_manager.auto_sync_enabled = config["auto_sync_enabled"]
        if "remove_expired_stocks" in config:
            watchlist_sync_manager.remove_expired_stocks = config["remove_expired_stocks"]
        if "expired_threshold_hours" in config:
            watchlist_sync_manager.expired_threshold_hours = config["expired_threshold_hours"]
        
        return {"message": "관심종목 동기화 설정이 업데이트되었습니다."}
    except Exception as e:
        logger.error(f"관심종목 동기화 설정 업데이트 오류: {e}")
        raise HTTPException(status_code=500, detail="설정 업데이트 실패")

@app.post("/watchlist/sync/cleanup")
async def cleanup_expired_watchlist_stocks():
    """만료된 관심종목 수동 정리"""
    try:
        await watchlist_sync_manager._cleanup_expired_stocks()
        return {"message": "만료된 관심종목 정리가 완료되었습니다."}
    except Exception as e:
        logger.error(f"만료된 관심종목 정리 오류: {e}")
        raise HTTPException(status_code=500, detail="만료된 관심종목 정리 중 오류가 발생했습니다.")

# ===== 스캘핑 전략 API =====

@app.post("/scalping/start")
async def start_scalping():
    """스캘핑 전략 시작"""
    try:
        await scalping_manager.start_scalping_monitoring()
        return {"message": "스캘핑 전략이 시작되었습니다."}
    except Exception as e:
        logger.error(f"스캘핑 전략 시작 오류: {e}")
        raise HTTPException(status_code=500, detail="스캘핑 전략 시작 중 오류가 발생했습니다.")

@app.post("/scalping/stop")
async def stop_scalping():
    """스캘핑 전략 중지"""
    try:
        await scalping_manager.stop_scalping_monitoring()
        return {"message": "스캘핑 전략이 중지되었습니다."}
    except Exception as e:
        logger.error(f"스캘핑 전략 중지 오류: {e}")
        raise HTTPException(status_code=500, detail="스캘핑 전략 중지 중 오류가 발생했습니다.")

@app.get("/scalping/status")
async def get_scalping_status():
    """스캘핑 전략 상태 조회"""
    try:
        status = await scalping_manager.get_scalping_status()
        return status
    except Exception as e:
        logger.error(f"스캘핑 전략 상태 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="스캘핑 전략 상태 조회 중 오류가 발생했습니다.")

# ===== 매수대기 목록 정리 API =====

@app.post("/cleanup/manual")
async def manual_cleanup():
    """매수대기 목록 수동 정리"""
    try:
        result = await cleanup_scheduler.manual_cleanup()
        return result
    except Exception as e:
        logger.error(f"수동 정리 오류: {e}")
        raise HTTPException(status_code=500, detail="수동 정리 중 오류가 발생했습니다.")

@app.get("/cleanup/status")
async def get_cleanup_status():
    """정리 스케줄러 상태 조회"""
    try:
        status = await cleanup_scheduler.get_cleanup_status()
        return status
    except Exception as e:
        logger.error(f"정리 상태 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="정리 상태 조회 중 오류가 발생했습니다.")

@app.delete("/signals/pending")
async def clear_pending_signals():
    """매수대기 신호 목록 전체 삭제"""
    try:
        deleted_count = 0
        for db in get_db():
            session: Session = db
            try:
                # PENDING 상태인 모든 신호 삭제
                pending_signals = session.query(PendingBuySignal).filter(
                    PendingBuySignal.status == "PENDING"
                ).all()
                
                for signal in pending_signals:
                    session.delete(signal)
                    deleted_count += 1
                
                session.commit()
                logger.info(f"매수대기 신호 {deleted_count}개 삭제 완료")
                break
            except Exception as e:
                logger.error(f"매수대기 신호 삭제 오류: {e}")
                session.rollback()
                continue
        
        return {"message": f"매수대기 신호 {deleted_count}개가 삭제되었습니다."}
    except Exception as e:
        logger.error(f"매수대기 신호 삭제 중 오류: {e}")
        raise HTTPException(status_code=500, detail="매수대기 신호 삭제 중 오류가 발생했습니다.")

@app.delete("/signals/all")
async def clear_all_signals():
    """모든 신호 목록 삭제 (PENDING, ORDERED, FAILED 등)"""
    try:
        deleted_count = 0
        for db in get_db():
            session: Session = db
            try:
                # 모든 신호 삭제
                all_signals = session.query(PendingBuySignal).all()
                
                for signal in all_signals:
                    session.delete(signal)
                    deleted_count += 1
                
                session.commit()
                logger.info(f"모든 신호 {deleted_count}개 삭제 완료")
                break
            except Exception as e:
                logger.error(f"모든 신호 삭제 오류: {e}")
                session.rollback()
                continue
        
        return {"message": f"모든 신호 {deleted_count}개가 삭제되었습니다."}
    except Exception as e:
        logger.error(f"모든 신호 삭제 중 오류: {e}")
        raise HTTPException(status_code=500, detail="모든 신호 삭제 중 오류가 발생했습니다.")

# ===== 전략별 차트 시각화 API =====

@app.get("/chart/strategy/{stock_code}/{strategy_type}")
async def get_strategy_chart(stock_code: str, strategy_type: str, period: str = "1M"):
    """특정 전략 지표가 포함된 차트 생성"""
    try:
        # 1. 키움 API에서 데이터 가져오기
        chart_data = await kiwoom_api.get_stock_chart_data(stock_code, "1D")
        
        if not chart_data:
            raise HTTPException(status_code=404, detail="차트 데이터가 없습니다")
        
        # 2. DataFrame으로 변환
        df = pd.DataFrame(chart_data)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        df = df.sort_index()
        
        # 3. 기간에 따른 데이터 필터링
        if period == "1Y":
            df = df.tail(250)
        elif period == "1M":
            df = df.tail(30)
        elif period == "1W":
            df = df.tail(7)
        else:
            df = df.tail(500)
        
        # 4. 컬럼명 변경
        df = df.rename(columns={
            'open': 'Open',
            'high': 'High', 
            'low': 'Low',
            'close': 'Close',
            'volume': 'Volume'
        })
        
        # 5. 전략별 지표 계산
        added_plots = []
        legend_elements = []
        
        if strategy_type.upper() == "MOMENTUM":
            # 모멘텀 계산 (10일 기준)
            df['momentum'] = df['Close'] - df['Close'].shift(10)
            df['momentum_ma'] = df['momentum'].rolling(window=5).mean()
            
            # 0선 추가
            df['zero_line'] = 0
            
            added_plots = [
                mpf.make_addplot(df['momentum'], color='blue', alpha=0.8, width=2, secondary_y=True),
                mpf.make_addplot(df['momentum_ma'], color='red', alpha=0.8, width=1.5, secondary_y=True),
                mpf.make_addplot(df['zero_line'], color='black', alpha=0.5, width=1, linestyle='--', secondary_y=True)
            ]
            
            legend_elements = [
                mlines.Line2D([0], [0], color='blue', lw=2, label='모멘텀'),
                mlines.Line2D([0], [0], color='red', lw=1.5, label='모멘텀 이동평균'),
                mlines.Line2D([0], [0], color='black', lw=1, linestyle='--', label='0선')
            ]
            
        elif strategy_type.upper() == "DISPARITY":
            # 이격도 계산 (20일 이동평균 기준)
            df['ma20'] = df['Close'].rolling(window=20).mean()
            df['disparity'] = (df['Close'] / df['ma20']) * 100
            
            added_plots = [
                mpf.make_addplot(df['ma20'], color='orange', alpha=0.8, width=2),
                mpf.make_addplot(df['disparity'], color='purple', alpha=0.8, width=2, secondary_y=True)
            ]
            
            legend_elements = [
                mlines.Line2D([0], [0], color='orange', lw=2, label='20일 이동평균'),
                mlines.Line2D([0], [0], color='purple', lw=2, label='이격도(%)')
            ]
            
        elif strategy_type.upper() == "BOLLINGER":
            # 볼린저밴드 계산
            bb_indicator = BollingerBands(close=df['Close'], window=20, window_dev=2)
            df['bb_upper'] = bb_indicator.bollinger_hband()
            df['bb_middle'] = bb_indicator.bollinger_mavg()
            df['bb_lower'] = bb_indicator.bollinger_lband()
            
            added_plots = [
                mpf.make_addplot(df['bb_upper'], color='red', alpha=0.7, width=1.5),
                mpf.make_addplot(df['bb_middle'], color='blue', alpha=0.8, width=2),
                mpf.make_addplot(df['bb_lower'], color='red', alpha=0.7, width=1.5)
            ]
            
            legend_elements = [
                mlines.Line2D([0], [0], color='red', lw=1.5, alpha=0.7, label='볼린저밴드 상단'),
                mlines.Line2D([0], [0], color='blue', lw=2, alpha=0.8, label='볼린저밴드 중간'),
                mlines.Line2D([0], [0], color='red', lw=1.5, alpha=0.7, label='볼린저밴드 하단')
            ]
            
        elif strategy_type.upper() == "RSI":
            # RSI 계산
            rsi_indicator = RSIIndicator(close=df['Close'], window=14)
            df['rsi'] = rsi_indicator.rsi()
            
            # RSI 기준선 추가
            df['rsi_70'] = 70
            df['rsi_30'] = 30
            df['rsi_50'] = 50
            
            # 가중평균거래량 계산 (RSI 전략용)
            volume_period = 20
            def calculate_weighted_avg_volume(volumes):
                if len(volumes) == volume_period:
                    weights = list(range(1, len(volumes) + 1))
                    return sum(v * w for v, w in zip(volumes, weights)) / sum(weights)
                else:
                    return float('nan')
            
            df['weighted_avg_volume'] = df['Volume'].rolling(window=volume_period).apply(calculate_weighted_avg_volume)
            
            # 거래량 비율 계산
            df['volume_ratio'] = df['Volume'] / df['weighted_avg_volume']
            
            # 거래량 기준선 추가
            df['volume_threshold'] = 1.5  # 1.5배 기준선
            
            added_plots = [
                mpf.make_addplot(df['rsi'], color='purple', alpha=0.8, width=2, secondary_y=True),
                mpf.make_addplot(df['rsi_70'], color='red', alpha=0.5, width=1, linestyle='--', secondary_y=True),
                mpf.make_addplot(df['rsi_30'], color='blue', alpha=0.5, width=1, linestyle='--', secondary_y=True),
                mpf.make_addplot(df['rsi_50'], color='gray', alpha=0.3, width=1, linestyle=':', secondary_y=True),
                mpf.make_addplot(df['volume_ratio'], color='orange', alpha=0.7, width=1, secondary_y=True),
                mpf.make_addplot(df['volume_threshold'], color='red', alpha=0.5, width=1, linestyle='--', secondary_y=True)
            ]
            
            legend_elements = [
                mlines.Line2D([0], [0], color='purple', lw=2, label='RSI'),
                mlines.Line2D([0], [0], color='red', lw=1, linestyle='--', alpha=0.5, label='과매수(70)'),
                mlines.Line2D([0], [0], color='blue', lw=1, linestyle='--', alpha=0.5, label='과매도(30)'),
                mlines.Line2D([0], [0], color='gray', lw=1, linestyle=':', alpha=0.3, label='중립(50)'),
                mlines.Line2D([0], [0], color='orange', lw=1, label='거래량비율'),
                mlines.Line2D([0], [0], color='red', lw=1, linestyle='--', alpha=0.5, label='거래량기준(1.5배)')
            ]
            
        elif strategy_type.upper() == "CHAIKIN":
            # 차이킨 오실레이터 계산
            df['hlc3'] = (df['High'] + df['Low'] + df['Close']) / 3
            df['clv'] = ((df['Close'] - df['Low']) - (df['High'] - df['Close'])) / (df['High'] - df['Low'])
            df['clv'] = df['clv'].fillna(0)
            df['ad'] = (df['clv'] * df['Volume']).cumsum()
            
            # 차이킨 오실레이터 (3일 MA - 10일 MA)
            df['ad_short_ma'] = df['ad'].rolling(window=3).mean()
            df['ad_long_ma'] = df['ad'].rolling(window=10).mean()
            df['chaikin_oscillator'] = df['ad_short_ma'] - df['ad_long_ma']
            
            # 기준선 추가
            df['zero_line'] = 0
            
            added_plots = [
                mpf.make_addplot(df['chaikin_oscillator'], color='orange', alpha=0.8, width=2, secondary_y=True),
                mpf.make_addplot(df['zero_line'], color='gray', alpha=0.5, width=1, linestyle='--', secondary_y=True)
            ]
            
            legend_elements = [
                mlines.Line2D([0], [0], color='orange', lw=2, label='차이킨 오실레이터'),
                mlines.Line2D([0], [0], color='gray', lw=1, linestyle='--', alpha=0.5, label='기준선(0)')
            ]
        
        else:
            raise HTTPException(status_code=400, detail=f"지원하지 않는 전략 타입입니다: {strategy_type}")
        
        # 6. 색상 설정
        mc = mpf.make_marketcolors(
            up="red",
            down="blue",
            volume="inherit"
        )
        
        # 7. 스타일 설정
        s = mpf.make_mpf_style(
            base_mpf_style="charles",
            marketcolors=mc,
            gridaxis='both',
            y_on_right=True,
            facecolor='white',
            edgecolor='black'
        )
        
        # 8. 차트 생성
        buf = io.BytesIO()
        
        # 전략에 따라 secondary_y 사용 여부 결정
        use_secondary_y = strategy_type.upper() in ["MOMENTUM", "DISPARITY", "RSI", "CHAIKIN"]
        
        fig, axes = mpf.plot(
            data=df,
            type='candle',
            style=s,
            figratio=(18, 10),
            mav=(20, 60),
            volume=True,
            scale_width_adjustment=dict(volume=0.6, candle=1.2),
            addplot=added_plots,
            savefig=dict(fname=buf, format='png', dpi=200, bbox_inches='tight'),
            returnfig=True,
            tight_layout=True
        )
        
        # 9. 범례 추가
        if fig and axes and len(axes) > 0:
            try:
                # 기본 범례 요소 추가
                base_legend_elements = [
                    mlines.Line2D([0], [0], color='blue', lw=1, label='20일 이평선'),
                    mlines.Line2D([0], [0], color='orange', lw=1, label='60일 이평선')
                ]
                
                all_legend_elements = legend_elements + base_legend_elements
                
                axes[0].legend(
                    handles=all_legend_elements,
                    loc='upper left',
                    fontsize=10,
                    frameon=True,
                    fancybox=True,
                    shadow=True,
                    ncol=2,
                    bbox_to_anchor=(0, 1)
                )
            except Exception as e:
                logger.warning(f"범례 추가 실패: {e}")
        
        # 10. 이미지 반환
        buf.seek(0)
        image_data = buf.getvalue()
        buf.close()
        
        # Base64 인코딩
        image_base64 = base64.b64encode(image_data).decode('utf-8')
        
        return {
            "image": f"data:image/png;base64,{image_base64}",
            "strategy_type": strategy_type.upper(),
            "stock_code": stock_code,
            "period": period
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"전략 차트 생성 오류: {e}")
        raise HTTPException(status_code=500, detail="전략 차트 생성 중 오류가 발생했습니다.")

@app.get("/chart/strategy/{stock_code}")
async def get_all_strategies_chart(stock_code: str, period: str = "1M"):
    """모든 전략 지표가 포함된 종합 차트 생성"""
    try:
        # 1. 키움 API에서 데이터 가져오기
        chart_data = await kiwoom_api.get_stock_chart_data(stock_code, "1D")
        
        if not chart_data:
            raise HTTPException(status_code=404, detail="차트 데이터가 없습니다")
        
        # 2. DataFrame으로 변환
        df = pd.DataFrame(chart_data)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        df = df.sort_index()
        
        # 3. 기간에 따른 데이터 필터링
        if period == "1Y":
            df = df.tail(250)
        elif period == "1M":
            df = df.tail(30)
        elif period == "1W":
            df = df.tail(7)
        else:
            df = df.tail(500)
        
        # 4. 컬럼명 변경
        df = df.rename(columns={
            'open': 'Open',
            'high': 'High', 
            'low': 'Low',
            'close': 'Close',
            'volume': 'Volume'
        })
        
        # 5. 모든 전략 지표 계산
        # 모멘텀
        df['momentum'] = df['Close'] - df['Close'].shift(10)
        
        # 이격도
        df['ma20'] = df['Close'].rolling(window=20).mean()
        df['disparity'] = (df['Close'] / df['ma20']) * 100
        
        # 볼린저밴드
        bb_indicator = BollingerBands(close=df['Close'], window=20, window_dev=2)
        df['bb_upper'] = bb_indicator.bollinger_hband()
        df['bb_middle'] = bb_indicator.bollinger_mavg()
        df['bb_lower'] = bb_indicator.bollinger_lband()
        
        # RSI
        rsi_indicator = RSIIndicator(close=df['Close'], window=14)
        df['rsi'] = rsi_indicator.rsi()
        
        # 차이킨 오실레이터
        df['hlc3'] = (df['High'] + df['Low'] + df['Close']) / 3
        df['clv'] = ((df['Close'] - df['Low']) - (df['High'] - df['Close'])) / (df['High'] - df['Low'])
        df['clv'] = df['clv'].fillna(0)
        df['ad'] = (df['clv'] * df['Volume']).cumsum()
        df['ad_short_ma'] = df['ad'].rolling(window=3).mean()
        df['ad_long_ma'] = df['ad'].rolling(window=10).mean()
        df['chaikin_oscillator'] = df['ad_short_ma'] - df['ad_long_ma']
        
        # 6. 차트 플롯 설정
        added_plots = [
            # 볼린저밴드
            mpf.make_addplot(df['bb_upper'], color='red', alpha=0.5, width=1),
            mpf.make_addplot(df['bb_middle'], color='blue', alpha=0.7, width=1.5),
            mpf.make_addplot(df['bb_lower'], color='red', alpha=0.5, width=1),
            # 이동평균
            mpf.make_addplot(df['ma20'], color='orange', alpha=0.8, width=2),
        ]
        
        # 7. 색상 설정
        mc = mpf.make_marketcolors(
            up="red",
            down="blue",
            volume="inherit"
        )
        
        # 8. 스타일 설정
        s = mpf.make_mpf_style(
            base_mpf_style="charles",
            marketcolors=mc,
            gridaxis='both',
            y_on_right=True,
            facecolor='white',
            edgecolor='black'
        )
        
        # 9. 차트 생성
        buf = io.BytesIO()
        fig, axes = mpf.plot(
            data=df,
            type='candle',
            style=s,
            figratio=(18, 10),
            mav=(20, 60),
            volume=True,
            scale_width_adjustment=dict(volume=0.6, candle=1.2),
            addplot=added_plots,
            savefig=dict(fname=buf, format='png', dpi=200, bbox_inches='tight'),
            returnfig=True,
            tight_layout=True
        )
        
        # 10. 범례 추가
        if fig and axes and len(axes) > 0:
            try:
                legend_elements = [
                    mlines.Line2D([0], [0], color='red', lw=1, alpha=0.5, label='볼린저밴드 상/하단'),
                    mlines.Line2D([0], [0], color='blue', lw=1.5, alpha=0.7, label='볼린저밴드 중간'),
                    mlines.Line2D([0], [0], color='orange', lw=2, alpha=0.8, label='20일 이동평균'),
                    mlines.Line2D([0], [0], color='blue', lw=1, label='20일 이평선'),
                    mlines.Line2D([0], [0], color='orange', lw=1, label='60일 이평선')
                ]
                
                axes[0].legend(
                    handles=legend_elements,
                    loc='upper left',
                    fontsize=10,
                    frameon=True,
                    fancybox=True,
                    shadow=True,
                    ncol=2,
                    bbox_to_anchor=(0, 1)
                )
            except Exception as e:
                logger.warning(f"범례 추가 실패: {e}")
        
        # 11. 이미지 반환
        buf.seek(0)
        image_data = buf.getvalue()
        buf.close()
        
        # Base64 인코딩
        image_base64 = base64.b64encode(image_data).decode('utf-8')
        
        return {
            "image": f"data:image/png;base64,{image_base64}",
            "stock_code": stock_code,
            "period": period,
            "strategies": ["MOMENTUM", "DISPARITY", "BOLLINGER", "RSI", "CHAIKIN"]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"종합 전략 차트 생성 오류: {e}")
        raise HTTPException(status_code=500, detail="종합 전략 차트 생성 중 오류가 발생했습니다.")


# ===== 손절/익절 모니터링 API =====

@app.get("/stop-loss/status")
async def get_stop_loss_status():
    """손절/익절 모니터링 상태 조회"""
    try:
        status = await stop_loss_manager.get_monitoring_status()
        return status
    except Exception as e:
        logger.error(f"손절/익절 모니터링 상태 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="손절/익절 모니터링 상태 조회 중 오류가 발생했습니다.")

@app.post("/stop-loss/start")
async def start_stop_loss_monitoring():
    """손절/익절 모니터링 시작"""
    try:
        if not stop_loss_manager.is_running:
            asyncio.create_task(stop_loss_manager.start_monitoring())
            logger.info("🛡️ [API] 손절/익절 모니터링 시작 요청")
            return {"message": "손절/익절 모니터링이 시작되었습니다.", "is_running": True}
        else:
            return {"message": "손절/익절 모니터링이 이미 실행 중입니다.", "is_running": True}
    except Exception as e:
        logger.error(f"손절/익절 모니터링 시작 오류: {e}")
        raise HTTPException(status_code=500, detail="손절/익절 모니터링 시작 중 오류가 발생했습니다.")

@app.post("/stop-loss/stop")
async def stop_stop_loss_monitoring():
    """손절/익절 모니터링 중지"""
    try:
        await stop_loss_manager.stop_monitoring()
        logger.info("🛡️ [API] 손절/익절 모니터링 중지 요청")
        return {"message": "손절/익절 모니터링이 중지되었습니다.", "is_running": False}
    except Exception as e:
        logger.error(f"손절/익절 모니터링 중지 오류: {e}")
        raise HTTPException(status_code=500, detail="손절/익절 모니터링 중지 중 오류가 발생했습니다.")

@app.post("/positions/update-prices")
async def update_positions_prices():
    """모든 Position의 현재가만 업데이트 (손절/익절 판단 없음)"""
    try:
        logger.info("📊 [API] Position 현재가 업데이트 요청")
        await stop_loss_manager._update_all_positions_price()
        return {"message": "Position 현재가 업데이트가 완료되었습니다."}
    except Exception as e:
        logger.error(f"Position 현재가 업데이트 오류: {e}")
        raise HTTPException(status_code=500, detail="Position 현재가 업데이트 중 오류가 발생했습니다.")

@app.post("/positions/sync-actual-buy-amount")
async def sync_actual_buy_amount():
    """키움 API에서 실제 매입금액(pur_amt)을 가져와서 포지션 업데이트"""
    try:
        from core.models import Position
        from api.kiwoom_api import kiwoom_api
        from core.config import Config
        
        account_number = Config.KIWOOM_MOCK_ACCOUNT_NUMBER if Config.KIWOOM_USE_MOCK_ACCOUNT else Config.KIWOOM_ACCOUNT_NUMBER
        balance_data = await kiwoom_api.get_account_balance(account_number)
        
        if not balance_data or 'stk_acnt_evlt_prst' not in balance_data:
            raise HTTPException(status_code=500, detail="보유종목 정보 조회 실패")
        
        holdings = balance_data.get('stk_acnt_evlt_prst', [])
        holdings_map = {}
        for holding in holdings:
            stock_code = holding.get('stk_cd', '').replace('A', '')
            pur_amt = int(float(holding.get('pur_amt', '0')))  # 매입금액 (수수료 포함)
            evlt_amt = int(float(holding.get('evlt_amt', '0')))  # 평가금액 (키움 실제 값)
            lspft_amt = int(float(holding.get('lspft_amt', '0')))  # 평가손익
            lspft_rt = float(holding.get('lspft_rt', '0'))  # 수익률
            if pur_amt > 0:
                holdings_map[stock_code] = {
                    'pur_amt': pur_amt,
                    'evlt_amt': evlt_amt,  # 평가금액 추가
                    'lspft_amt': lspft_amt,
                    'lspft_rt': lspft_rt
                }
        
        updated_count = 0
        for db in get_db():
            session: Session = db
            positions = session.query(Position).filter(Position.status == "HOLDING").all()
            
            for position in positions:
                stock_code = position.stock_code.replace('A', '')
                if stock_code in holdings_map:
                    holding_info = holdings_map[stock_code]
                    actual_buy_amount = holding_info['pur_amt']
                    evlt_amt = holding_info.get('evlt_amt', 0)  # 평가금액
                    kiwoom_profit_loss = holding_info['lspft_amt']
                    kiwoom_profit_rate = holding_info['lspft_rt']
                    
                    # actual_buy_amount 업데이트
                    current_actual_buy_amount = getattr(position, 'actual_buy_amount', None)
                    if current_actual_buy_amount != actual_buy_amount:
                        position.actual_buy_amount = actual_buy_amount
                        logger.info(f"💰 [API] 포지션 실제매입금액 업데이트 - {position.stock_name}: {actual_buy_amount:,}원")
                    
                    # 키움 API의 평가손익과 수익률이 있으면 우선 사용 (가장 정확함)
                    if kiwoom_profit_loss != 0 or kiwoom_profit_rate != 0:
                        if position.current_profit_loss != kiwoom_profit_loss or abs(position.current_profit_loss_rate - kiwoom_profit_rate) > 0.01:
                            position.current_profit_loss = int(kiwoom_profit_loss)
                            position.current_profit_loss_rate = kiwoom_profit_rate
                            updated_count += 1
                            logger.info(f"💰 [API] 포지션 평가손익 업데이트 - {position.stock_name}: {kiwoom_profit_loss:+,}원 ({kiwoom_profit_rate:+.2f}%)")
                    # 키움 API 값이 0이면 키움 방식으로 계산 (매도 수수료 + 거래세 포함)
                    elif position.current_price:
                        # 키움 공식 적용 (모의투자/실계좌 구분)
                        import math
                        from core.config import Config
                        
                        is_mock_account = Config.KIWOOM_USE_MOCK_ACCOUNT
                        
                        if is_mock_account:
                            # 모의투자 계좌: 매도 수수료 0.35%, 제세금 총 0.557% (기본 0.23% + 추가)
                            sell_fee = math.floor(position.current_price * position.buy_quantity * 0.0035)  # 0.35%
                            # 제세금: 기본 0.23% + 추가(제세금1+2+3+농특세) = 총 0.557%
                            tax = math.floor(position.current_price * position.buy_quantity * 0.00557)  # 0.557%
                        else:
                            # 실계좌: 매도 수수료 0.015% (10원미만 절사), 제세금 0.05% + 0.15%
                            sell_fee_base = position.current_price * position.buy_quantity * 0.00015
                            sell_fee = math.floor(sell_fee_base / 10) * 10  # 10원미만 절사
                            
                            tax_005 = math.floor(position.current_price * position.buy_quantity * 0.0005)  # 0.05%, 원미만 절사
                            tax_015 = math.floor(position.current_price * position.buy_quantity * 0.0015)  # 0.15%, 원미만 절사
                            tax = tax_005 + tax_015
                        
                        # 평가금액 = 현재가 × 수량 - 매도 수수료 - 제세금
                        evaluation_amount = position.current_price * position.buy_quantity - sell_fee - tax
                        
                        # 손익 = 평가금액 - 매입금액
                        calculated_profit_loss = evaluation_amount - actual_buy_amount
                        
                        # 수익률 = 손익 / 매입금액 × 100
                        calculated_profit_rate = (calculated_profit_loss / actual_buy_amount) * 100 if actual_buy_amount > 0 else 0
                        
                        if position.current_profit_loss != int(calculated_profit_loss) or abs(position.current_profit_loss_rate - calculated_profit_rate) > 0.01:
                            position.current_profit_loss = int(calculated_profit_loss)
                            position.current_profit_loss_rate = calculated_profit_rate
                            updated_count += 1
                            logger.info(f"💰 [API] 포지션 평가손익 업데이트 (계산) - {position.stock_name}: {calculated_profit_loss:+,}원 ({calculated_profit_rate:+.2f}%)")
            
            session.commit()
            break
        
        return {
            "message": f"포지션 실제매입금액 업데이트 완료: {updated_count}개",
            "updated_count": updated_count,
            "success": True
        }
    except Exception as e:
        logger.error(f"포지션 실제매입금액 업데이트 오류: {e}")
        raise HTTPException(status_code=500, detail=f"포지션 실제매입금액 업데이트 중 오류가 발생했습니다: {str(e)}")

@app.get("/positions/")
async def get_positions(status: str = "HOLDING", limit: int = 50):
    """포지션 목록 조회"""
    try:
        positions = []
        for db in get_db():
            session: Session = db
            from core.models import Position
            query = session.query(Position)
            if status != "ALL":
                query = query.filter(Position.status == status)
            positions = query.order_by(Position.buy_time.desc()).limit(limit).all()
            break
        
        return {
            "items": [
                {
                    "id": pos.id,
                    "stock_code": pos.stock_code,
                    "stock_name": pos.stock_name,
                    "buy_price": pos.buy_price,
                    "buy_quantity": pos.buy_quantity,
                    "buy_amount": pos.buy_amount,
                    "current_price": pos.current_price,
                    "current_profit_loss": pos.current_profit_loss,
                    "current_profit_loss_rate": pos.current_profit_loss_rate,
                    "stop_loss_rate": pos.stop_loss_rate,
                    "take_profit_rate": pos.take_profit_rate,
                    "status": pos.status,
                    "signal_id": pos.signal_id,
                    "condition_id": pos.condition_id,
                    "actual_buy_amount": pos.actual_buy_amount,
                    "buy_time": pos.buy_time.isoformat() if pos.buy_time else None,
                    "sell_time": pos.sell_time.isoformat() if pos.sell_time else None,
                    "last_monitored": pos.last_monitored.isoformat() if pos.last_monitored else None
                }
                for pos in positions
            ],
            "total": len(positions),
            "status": status
        }
    except Exception as e:
        logger.error(f"포지션 목록 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="포지션 목록 조회 중 오류가 발생했습니다.")

@app.get("/sell-orders/")
async def get_sell_orders(status: str = "ALL", limit: int = 50):
    """매도 주문 목록 조회"""
    try:
        orders = []
        for db in get_db():
            session: Session = db
            from core.models import SellOrder
            query = session.query(SellOrder)
            if status != "ALL":
                query = query.filter(SellOrder.status == status)
            orders = query.order_by(SellOrder.created_at.desc()).limit(limit).all()
            break
        
        return {
            "items": [
                {
                    "id": order.id,
                    "position_id": order.position_id,
                    "stock_code": order.stock_code,
                    "stock_name": order.stock_name,
                    "sell_price": order.sell_price,
                    "sell_quantity": order.sell_quantity,
                    "sell_amount": order.sell_amount,
                    "sell_reason": order.sell_reason,
                    "sell_reason_detail": order.sell_reason_detail,
                    "profit_loss": order.profit_loss,
                    "profit_loss_rate": order.profit_loss_rate,
                    "status": order.status,
                    "created_at": order.created_at.isoformat() if order.created_at else None,
                    "ordered_at": order.ordered_at.isoformat() if order.ordered_at else None,
                    "completed_at": order.completed_at.isoformat() if order.completed_at else None
                }
                for order in orders
            ],
            "total": len(orders),
            "status": status
        }
    except Exception as e:
        logger.error(f"매도 주문 목록 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="매도 주문 목록 조회 중 오류가 발생했습니다.")

# ===== 급등 종목 조회 API =====

@app.get("/stocks/surge")
async def get_surge_stocks(
    min_change_rate: float = 5.0,  # 최소 등락률 (%)
    min_volume_ratio: float = 2.0,  # 최소 거래량 비율 (전일 대비)
    min_price: int = 1000,  # 최소 주가 (페니주식 제외)
    limit: int = 50,  # 최대 조회 개수
    condition_id: Optional[str] = None,  # 조건식 ID (선택)
    use_chart_data: bool = False  # 차트 데이터 사용 여부
):
    """
    급등 종목 조회
    
    - min_change_rate: 최소 등락률 (%)
    - min_volume_ratio: 최소 거래량 비율 (전일 대비, 예: 2.0 = 2배)
    - min_price: 최소 주가 (원)
    - limit: 최대 조회 개수
    - condition_id: 조건식 ID (지정 시 조건식 검색 사용)
    - use_chart_data: True면 차트 데이터 기반 조회 (관심종목 대상)
    """
    try:
        logger.info(f"🚀 [SURGE] 급등 종목 조회 시작 - 등락률>={min_change_rate}%, 거래량>={min_volume_ratio}배")
        
        surge_stocks = []
        
        # 방법 1: 조건식 기반 조회 (가장 효율적)
        if condition_id:
            logger.info(f"🚀 [SURGE] 조건식 기반 조회: {condition_id}")
            stocks = await kiwoom_api.search_condition_stocks(
                condition_id=condition_id,
                condition_name="급등종목"
            )
            
            for stock in stocks:
                try:
                    change_rate = float(stock.get('change_rate', 0))
                    volume = int(stock.get('volume', 0))
                    current_price = int(stock.get('current_price', 0))
                    
                    # 기본 필터링
                    if (change_rate >= min_change_rate and 
                        current_price >= min_price):
                        surge_stocks.append({
                            'stock_code': stock.get('stock_code'),
                            'stock_name': stock.get('stock_name'),
                            'current_price': current_price,
                            'prev_close': int(stock.get('prev_close', 0)),
                            'change_rate': change_rate,
                            'volume': volume,
                            'price_diff': current_price - int(stock.get('prev_close', 0))
                        })
                except (ValueError, TypeError) as e:
                    logger.warning(f"🚀 [SURGE] 종목 데이터 파싱 오류: {e}")
                    continue
        
        # 방법 2: 차트 데이터 기반 조회 (관심종목 대상)
        elif use_chart_data:
            logger.info(f"🚀 [SURGE] 차트 데이터 기반 조회 (관심종목)")
            
            # 관심종목 목록 조회
            watchlist = []
            for db in get_db():
                session: Session = db
                from core.models import WatchlistStock
                watchlist_stocks = session.query(WatchlistStock).filter(
                    WatchlistStock.is_active == True
                ).limit(100).all()  # 최대 100개 종목만 조회 (API 제한 고려)
                watchlist = [stock.stock_code for stock in watchlist_stocks]
                break
            
            if not watchlist:
                logger.warning("🚀 [SURGE] 관심종목이 없습니다")
                return {
                    "stocks": [],
                    "total": 0,
                    "message": "관심종목이 없습니다"
                }
            
            logger.info(f"🚀 [SURGE] 관심종목 {len(watchlist)}개 조회 시작")
            
            # 각 종목의 최근 일봉 데이터 조회
            for idx, stock_code in enumerate(watchlist, 1):
                try:
                    # API 제한 고려: 5초마다 1개 종목 조회
                    if idx > 1:
                        await asyncio.sleep(5)
                    
                    chart_data = await kiwoom_api.get_stock_chart_data(stock_code, "1D")
                    
                    if not chart_data or len(chart_data) < 2:
                        continue
                    
                    # 최근 2일 데이터
                    today = chart_data[-1]
                    yesterday = chart_data[-2]
                    
                    current_price = int(today.get('close', 0))
                    prev_close = int(yesterday.get('close', 0))
                    today_volume = int(today.get('volume', 0))
                    yesterday_volume = int(yesterday.get('volume', 0))
                    
                    if prev_close == 0:
                        continue
                    
                    # 등락률 계산
                    change_rate = ((current_price - prev_close) / prev_close) * 100
                    
                    # 거래량 증가율 계산
                    volume_ratio = today_volume / yesterday_volume if yesterday_volume > 0 else 0
                    
                    # 급등 조건 확인
                    if (change_rate >= min_change_rate and 
                        volume_ratio >= min_volume_ratio and
                        current_price >= min_price):
                        
                        # 종목명 조회
                        stock_name = stock_code
                        for db in get_db():
                            session: Session = db
                            from core.models import WatchlistStock
                            watchlist_stock = session.query(WatchlistStock).filter(
                                WatchlistStock.stock_code == stock_code
                            ).first()
                            if watchlist_stock:
                                stock_name = watchlist_stock.stock_name
                            break
                        
                        surge_stocks.append({
                            'stock_code': stock_code,
                            'stock_name': stock_name,
                            'current_price': current_price,
                            'prev_close': prev_close,
                            'change_rate': round(change_rate, 2),
                            'volume': today_volume,
                            'volume_ratio': round(volume_ratio, 2),
                            'price_diff': current_price - prev_close
                        })
                        
                except Exception as e:
                    logger.warning(f"🚀 [SURGE] {stock_code} 조회 오류: {e}")
                    continue
        
        # 방법 3: 기본 조회 (조건식 없을 경우 빈 결과)
        else:
            logger.warning("🚀 [SURGE] 조건식 ID가 지정되지 않았습니다. condition_id 파라미터를 지정하거나 use_chart_data=true를 사용하세요.")
            return {
                "stocks": [],
                "total": 0,
                "message": "조건식 ID를 지정하거나 use_chart_data=true를 사용하세요"
            }
        
        # 등락률 기준 내림차순 정렬
        surge_stocks.sort(key=lambda x: x['change_rate'], reverse=True)
        
        # limit 적용
        surge_stocks = surge_stocks[:limit]
        
        logger.info(f"🚀 [SURGE] 급등 종목 {len(surge_stocks)}개 발견")
        
        return {
            "stocks": surge_stocks,
            "total": len(surge_stocks),
            "criteria": {
                "min_change_rate": min_change_rate,
                "min_volume_ratio": min_volume_ratio,
                "min_price": min_price
            }
        }
        
    except Exception as e:
        logger.error(f"🚀 [SURGE] 급등 종목 조회 오류: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"급등 종목 조회 중 오류가 발생했습니다: {str(e)}")

@app.post("/positions/{position_id}/manual-sell")
async def manual_sell_position(position_id: int, sell_price: int = 0):
    """수동 매도 주문"""
    try:
        # 포지션 조회
        position = None
        for db in get_db():
            session: Session = db
            from core.models import Position
            position = session.query(Position).filter(Position.id == position_id).first()
            break
        
        if not position:
            raise HTTPException(status_code=404, detail="포지션을 찾을 수 없습니다.")
        
        if position.status != "HOLDING":
            raise HTTPException(status_code=400, detail="매도 가능한 포지션이 아닙니다.")
        
        # 현재가 조회 (시장가인 경우)
        if sell_price == 0:
            current_price = await kiwoom_api.get_current_price(position.stock_code)
            if not current_price:
                raise HTTPException(status_code=500, detail="현재가 조회 실패")
            sell_price = current_price
        
        # 매도 주문 실행
        await stop_loss_manager._execute_sell_order(
            position=position,
            sell_price=sell_price,
            sell_reason="MANUAL",
            sell_reason_detail="수동 매도 주문"
        )
        
        return {
            "message": f"{position.stock_name} 수동 매도 주문이 실행되었습니다.",
            "position_id": position_id,
            "sell_price": sell_price
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"수동 매도 주문 오류: {e}")
        raise HTTPException(status_code=500, detail="수동 매도 주문 중 오류가 발생했습니다.")

