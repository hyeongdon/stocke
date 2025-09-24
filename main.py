from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import os
import asyncio
# DB 관련 import는 나중에 필요시 추가
# from sqlalchemy.orm import Session
from typing import List, Optional
import logging
from datetime import datetime
import httpx
import re

# 차트 생성 import
import pandas as pd
import mplfinance as mpf
import matplotlib.pyplot as plt
import matplotlib.lines as mlines
import io
import base64
from ta.trend import IchimokuIndicator
import warnings

# pandas와 ta 라이브러리의 FutureWarning 억제
warnings.filterwarnings('ignore', category=FutureWarning, module='ta')
warnings.filterwarnings('ignore', category=FutureWarning, module='pandas')

# DB 연동
from models import get_db, AutoTradeCondition, PendingBuySignal, AutoTradeSettings
from sqlalchemy.orm import Session
from pydantic import BaseModel
from condition_monitor import condition_monitor
from kiwoom_api import KiwoomAPI
from config import Config
from naver_discussion_crawler import NaverStockDiscussionCrawler

# 개선된 모듈들 import
from signal_manager import signal_manager, SignalType, SignalStatus
from api_rate_limiter import api_rate_limiter
from buy_order_executor import buy_order_executor

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("🌐 [STARTUP] 애플리케이션 시작")
    
    # 정적 파일 디렉토리 재확인
    static_dir = os.path.join(os.path.dirname(__file__), "static")
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
    
    yield
    
    # Shutdown
    logger.info("모니터링 시스템 종료")
    
    # 개선된 시스템들 종료
    try:
        await buy_order_executor.stop_processing()
        logger.info("💰 [SHUTDOWN] 매수 주문 실행기 종료")
    except Exception as e:
        logger.error(f"💰 [SHUTDOWN] 매수 주문 실행기 종료 실패: {e}")
    
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
static_dir = os.path.join(os.path.dirname(__file__), "static")
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
async def get_pending_signals(limit: int = 100, status: str = "PENDING"):
    """매수대기(PENDING) 신호 목록 조회. status=ALL 전달 시 전체 조회"""
    try:
        logger.info(f"[PENDING_API] request: limit={limit} status={status}")
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
            
            for i, r in enumerate(rows):
                # 현재가격 조회
                current_price = 0
                try:
                    # API 호출 제한을 피하기 위해 종목 간 1초 대기
                    if i > 0:
                        await asyncio.sleep(1)
                    
                    # 키움 API로 현재가 조회
                    chart_data = await kiwoom_api.get_stock_chart_data(r.stock_code, "1D")
                    if chart_data and len(chart_data) > 0:
                        current_price = int(chart_data[0].get('close', 0))
                except Exception as e:
                    logger.warning(f"[PENDING_API] 현재가 조회 실패 {r.stock_code}: {e}")
                    # 429 오류인 경우 더 긴 대기 시간
                    if "429" in str(e):
                        await asyncio.sleep(5)
                
                # 매수목표금액 계산
                if r.target_price:  # 조건식 기준봉 전략
                    # 기준봉 기반 목표가 사용
                    target_amount = r.target_price
                    max_invest_amount = 100000  # 10만원 상당
                    target_quantity = max_invest_amount // current_price if current_price > 0 else 0
                    if target_quantity < 1:
                        target_quantity = 1
                    target_amount = target_quantity * current_price if current_price > 0 else r.target_price
                else:
                    # 일반 조건식의 경우 10만원 상당
                    max_invest_amount = 100000
                    target_quantity = max_invest_amount // current_price if current_price > 0 else 0
                    target_amount = target_quantity * current_price if current_price > 0 else 0
                
                items.append({
                    "id": r.id,
                    "condition_id": r.condition_id,
                    "stock_code": r.stock_code,
                    "stock_name": r.stock_name,
                    "detected_at": r.detected_at.isoformat() if r.detected_at else None,
                    "status": r.status,
                    "current_price": current_price,
                    "target_quantity": target_quantity,
                    "target_amount": target_amount,
                })
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
                "id": i + 1,  # 임시 ID
                "condition_name": condition_data.get('condition_name', f'조건식_{i+1}'),
                "condition_expression": condition_data.get('expression', ''),
                "is_active": True,
                "is_enabled": enabled_map.get(condition_data.get('condition_name', f'조건식_{i+1}'), False),
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }
            conditions.append(condition)
            logger.debug(f"조건식: {condition['condition_name']}")
        
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
            "max_invest_amount": buy_order_executor.max_invest_amount,
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
    order_type: str = "01"  # 01: 시장가, 00: 지정가

@app.post("/trading/buy")
async def place_buy_order(req: BuyOrderRequest):
    """주식 매수 주문"""
    try:
        logger.info(f"매수 주문 요청: {req.stock_code}, 수량: {req.quantity}, 가격: {req.price}")
        
        result = await kiwoom_api.place_buy_order(
            stock_code=req.stock_code,
            quantity=req.quantity,
            price=req.price,
            order_type=req.order_type
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

@app.get("/buy-executor/status")
async def get_buy_executor_status():
    """매수 주문 실행기 상태 조회"""
    try:
        status = {
            "is_running": buy_order_executor.is_running,
            "max_invest_amount": buy_order_executor.max_invest_amount,
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

