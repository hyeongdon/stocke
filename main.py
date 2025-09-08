from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from typing import List, Optional
import logging
from datetime import datetime
import httpx
from urllib.parse import quote
import re

# 차트 생성을 위한 추가 import
import pandas as pd
import mplfinance as mpf
import matplotlib.pyplot as plt  # matplotlib.pyplot 추가
import matplotlib.lines as mlines  # Line2D를 위한 import 추가
import io
import base64
from ta.trend import IchimokuIndicator
import warnings

# pandas와 ta 라이브러리의 FutureWarning 억제
warnings.filterwarnings('ignore', category=FutureWarning, module='ta')
warnings.filterwarnings('ignore', category=FutureWarning, module='pandas')

from models import (
    get_db, Condition, StockSignal, ConditionLog,
    ConditionCreate, ConditionUpdate, ConditionResponse,
    StockSignalResponse, ConditionLogResponse
)
from condition_monitor import condition_monitor
from kiwoom_api import KiwoomAPI
from config import Config

# Config 인스턴스 생성 추가
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

app = FastAPI(
    title="키움증권 조건식 모니터링 시스템",
    description="사용자가 지정한 조건식을 통해 종목을 실시간으로 감시하는 시스템",
    version="1.0.0"
)

# 정적 파일 서빙 설정
app.mount("/static", StaticFiles(directory="static"), name="static")

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

from fastapi.responses import RedirectResponse

@app.get("/")
async def root():
    """메인 페이지 - 웹 인터페이스로 리다이렉트"""
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

@app.on_event("startup")
async def startup_event():
    logger.info("애플리케이션 시작")
    
    # 데이터베이스 초기화
    from models import init_db
    init_db()
    
    # 키움 API 인증 및 연결
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
    # WebSocket 연결 종료 추가
    await kiwoom_api.disconnect()
    logger.info("키움 API WebSocket 연결 종료 완료")

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
        
        # 키움 API 응답을 ConditionResponse 형태로 변환
        conditions = []
        for i, condition_data in enumerate(conditions_data):
            # 키움 API 응답 형태에 따라 조정 필요
            condition = {
                "id": i + 1,  # 임시 ID
                "condition_name": condition_data.get('condition_name', f'조건식_{i+1}'),
                "condition_expression": condition_data.get('expression', ''),
                "is_active": True,
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

@app.get("/conditions/{condition_id}/stocks")
async def get_condition_stocks(condition_id: int):
    """조건식으로 종목 목록 조회"""
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
        
        logger.info(f"조건식 검색 시작: {condition_name} (API ID: {condition_api_id})")
        
        # 키움 API를 통해 조건식으로 종목 검색
        stocks_data = await kiwoom_api.search_condition_stocks(condition_api_id, condition_name)
        
        if not stocks_data:
            logger.info(f"조건식 '{condition_name}'에 해당하는 종목이 없습니다.")
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
        
        logger.info(f"조건식 종목 조회 완료: {condition_name}, 종목 수: {len(stocks_data)}개")
        
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
        logger.error(f"조건식 종목 조회 오류: {e}")
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

@app.get("/api/status")
async def get_status():
    logger.info("🔄 [DEBUG] API 상태 체크 요청")
    logger.info(f"🔄 [DEBUG] kiwoom_api.running: {kiwoom_api.running}")
    logger.info(f"🔄 [DEBUG] kiwoom_api.websocket: {kiwoom_api.websocket}")
    logger.info(f"🔄 [DEBUG] kiwoom_api.websocket is not None: {kiwoom_api.websocket is not None}")
    
    return {
        "running": kiwoom_api.running,
        "websocket_connected": kiwoom_api.websocket is not None,
        "token_valid": kiwoom_api.token_manager.is_token_valid()
    }

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

@app.get("/account/balance")
async def get_account_balance():
    """계좌 잔고 정보 조회 - 키움 API kt00004 스펙 기반"""
    try:
        # 키움 API 연결 상태 상세 로깅
        logger.info(f"=== 키움 API 상태 확인 ===")
        logger.info(f"kiwoom_api.running: {kiwoom_api.running}")
        logger.info(f"kiwoom_api.websocket: {kiwoom_api.websocket}")
        logger.info(f"kiwoom_api.websocket is not None: {kiwoom_api.websocket is not None}")
        logger.info(f"토큰 유효성: {bool(kiwoom_api.token_manager.get_valid_token())}")
        logger.info(f"실제 토큰: {kiwoom_api.token_manager.get_valid_token()}")
        logger.info(f"조건문 결과: {not (kiwoom_api.running and kiwoom_api.websocket)}")
        
        # 키움 API 연결 상태 확인
        if not (kiwoom_api.running and kiwoom_api.websocket):
            logger.warning("키움 API가 연결되지 않았습니다. 임시 데이터를 반환합니다.")
            # 각 조건별 상세 로깅
            if not kiwoom_api.running:
                logger.warning("- kiwoom_api.running이 False입니다.")
            if not kiwoom_api.websocket:
                logger.warning("- kiwoom_api.websocket이 None입니다.")
                
            # 임시 데이터에 데이터 소스 정보 추가
            balance_data = {
                "acnt_nm": "홍길동",
                "brch_nm": "강남지점",
                "entr": "5000000",
                "d2_entra": "5000000",
                "tot_est_amt": "8500000",
                "aset_evlt_amt": "13500000",
                "tot_pur_amt": "8150000",
                "prsm_dpst_aset_amt": "13500000",
                "tot_grnt_sella": "0",
                "tdy_lspft_amt": "8150000",
                "invt_bsamt": "8150000",
                "lspft_amt": "8150000",
                "tdy_lspft": "350000",
                "lspft2": "350000",
                "lspft": "350000",
                "tdy_lspft_rt": "4.29",
                "lspft_ratio": "4.29",
                "lspft_rt": "4.29",
                "_data_source": "MOCK_DATA",
                "_api_connected": kiwoom_api.running,
                "_token_valid": bool(kiwoom_api.token_manager.get_valid_token())
            }
        else:
            # 실제 키움 API 호출
            logger.info("키움 API에서 계좌 정보 조회 중...")
            balance_data = await kiwoom_api.get_account_balance()
            
            if not balance_data:
                logger.warning("키움 API 호출 실패, 임시 데이터를 반환합니다.")
                balance_data = {
                    "acnt_nm": "홍길동",
                    "brch_nm": "강남지점",
                    "entr": "5000000",
                    "d2_entra": "5000000",
                    "tot_est_amt": "8500000",
                    "aset_evlt_amt": "13500000",
                    "tot_pur_amt": "8150000",
                    "prsm_dpst_aset_amt": "13500000",
                    "tot_grnt_sella": "0",
                    "tdy_lspft_amt": "8150000",
                    "invt_bsamt": "8150000",
                    "lspft_amt": "8150000",
                    "tdy_lspft": "350000",
                    "lspft2": "350000",
                    "lspft": "350000",
                    "tdy_lspft_rt": "4.29",
                    "lspft_ratio": "4.29",
                    "lspft_rt": "4.29",
                    "_data_source": "MOCK_DATA",
                    "_api_connected": False,
                    "_token_valid": False
                }
            else:
                balance_data["_data_source"] = "REAL_API"
                balance_data["_api_connected"] = True
                balance_data["_token_valid"] = True
        
        logger.info("계좌 잔고 정보 조회 완료")
        return balance_data
        
    except Exception as e:
        logger.error(f"계좌 잔고 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="계좌 잔고 조회 중 오류가 발생했습니다.")

@app.get("/account/holdings")
async def get_account_holdings():
    """보유종목 정보 조회 - 키움 API kt00004 스펙 기반"""
    try:
        # 키움 API 연결 상태 확인
        if not (kiwoom_api.running and kiwoom_api.websocket):
            logger.warning("키움 API가 연결되지 않았습니다. 임시 데이터를 반환합니다.")
        
        # 키움 API kt00004 응답 구조에 맞춘 임시 데이터
        holdings_data = {
            "stk_acnt_evlt_prst": [
                {
                    "stk_cd": "005930",  # 종목코드
                    "stk_nm": "삼성전자",  # 종목명
                    "rmnd_qty": "10",  # 보유수량
                    "avg_prc": "75000",  # 평균단가
                    "cur_prc": "78000",  # 현재가
                    "evlt_amt": "780000",  # 평가금액
                    "pl_amt": "30000",  # 손익금액
                    "pl_rt": "4.00",  # 손익율
                    "loan_dt": "",  # 대출일
                    "pur_amt": "750000",  # 매입금액
                    "setl_remn": "10",  # 결제잔고
                    "pred_buyq": "0",  # 전일매수수량
                    "pred_sellq": "0",  # 전일매도수량
                    "tdy_buyq": "0",  # 금일매수수량
                    "tdy_sellq": "0"  # 금일매도수량
                },
                {
                    "stk_cd": "000660",
                    "stk_nm": "SK하이닉스",
                    "rmnd_qty": "5",
                    "avg_prc": "120000",
                    "cur_prc": "125000",
                    "evlt_amt": "625000",
                    "pl_amt": "25000",
                    "pl_rt": "4.17",
                    "loan_dt": "",
                    "pur_amt": "600000",
                    "setl_remn": "5",
                    "pred_buyq": "0",
                    "pred_sellq": "0",
                    "tdy_buyq": "0",
                    "tdy_sellq": "0"
                },
                {
                    "stk_cd": "035420",
                    "stk_nm": "NAVER",
                    "rmnd_qty": "3",
                    "avg_prc": "200000",
                    "cur_prc": "210000",
                    "evlt_amt": "630000",
                    "pl_amt": "30000",
                    "pl_rt": "5.00",
                    "loan_dt": "",
                    "pur_amt": "600000",
                    "setl_remn": "3",
                    "pred_buyq": "0",
                    "pred_sellq": "0",
                    "tdy_buyq": "0",
                    "tdy_sellq": "0"
                }
            ]
        }
        
        logger.info(f"보유종목 {len(holdings_data['stk_acnt_evlt_prst'])}건 조회 완료")
        return holdings_data
        
    except Exception as e:
        logger.error(f"보유종목 조회 오류: {e}")
        return {
            "error": str(e),
            "stk_acnt_evlt_prst": []
        }
@app.get("/account/history")
async def get_trading_history(limit: int = 50):
    """거래내역 조회"""
    try:
        # 키움 API 연결 상태 확인
        if not (kiwoom_api.running and kiwoom_api.websocket):
            logger.warning("키움 API가 연결되지 않았습니다. 임시 데이터를 반환합니다.")
        
        # 실제 키움 API 호출 (임시 데이터로 대체)
        history_data = {
            "history": [
                {
                    "date": "2024-01-15",
                    "time": "09:30:15",
                    "stock_code": "005930",
                    "stock_name": "삼성전자",
                    "type": "매수",
                    "quantity": 5,
                    "price": 75000,
                    "amount": 375000
                },
                {
                    "date": "2024-01-14",
                    "time": "14:25:30",
                    "stock_code": "000660",
                    "stock_name": "SK하이닉스",
                    "type": "매도",
                    "quantity": 3,
                    "price": 122000,
                    "amount": 366000
                }
            ]
        }
        
        logger.info(f"거래내역 {len(history_data['history'])}건 조회 완료")
        return history_data
        
    except Exception as e:
        logger.error(f"거래내역 조회 오류: {e}")
        return {
            "error": str(e),
            "history": []
        }
