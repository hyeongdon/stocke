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
import io
import base64
from ta.trend import IchimokuIndicator

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

@app.get("/stocks/{stock_code}/chart")
async def get_stock_chart(stock_code: str, period: str = "1D"):
    """종목 차트 데이터 조회"""
    try:
        logger.info(f"차트 데이터 요청: {stock_code}, 기간: {period}")
        
        # 키움 API를 통해 차트 데이터 조회
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

# 키움 API 연동 API
@app.get("/kiwoom/conditions")
async def get_kiwoom_conditions():
    """키움증권 조건식 목록 조회 (HTTP API)"""
    try:
        conditions = await kiwoom_api.get_condition_list()
        return {"conditions": conditions}
    except Exception as e:
        logger.error(f"키움증권 조건식 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="키움증권 조건식 조회 중 오류가 발생했습니다.")

@app.get("/kiwoom/conditions/websocket")
async def get_kiwoom_conditions_websocket():
    """키움증권 조건식 목록 조회 (WebSocket API)"""
    logger.debug("WebSocket 조건식 조회 API 호출됨")
    try:
        logger.debug("kiwoom_api.get_condition_list_websocket() 호출")
        conditions = await kiwoom_api.get_condition_list_websocket()
        logger.debug(f"조건식 조회 결과: {conditions}")
        return {"conditions": conditions}
    except Exception as e:
        logger.error(f"키움증권 조건식 WebSocket 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="키움증권 조건식 WebSocket 조회 중 오류가 발생했습니다.")

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
        
        # 4-1. 일목균형표 데이터 생성
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
        
        # 8-1. 범례 추가 (더 명확하게)
        if fig and axes and len(axes) > 0:
            try:
                # 메인 차트에 범례 추가
                legend_elements = [
                    plt.Line2D([0], [0], color='orange', lw=2, alpha=0.7, label='선행스팬A'),
                    plt.Line2D([0], [0], color='purple', lw=2, alpha=0.7, label='선행스팬B'),
                    plt.Line2D([0], [0], color='green', lw=2, alpha=0.8, label='기준선'),
                    plt.Line2D([0], [0], color='red', lw=2, alpha=0.8, label='전환선'),
                    plt.Line2D([0], [0], color='blue', lw=1, label='20일 이평선'),
                    plt.Line2D([0], [0], color='orange', lw=1, label='60일 이평선')
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
        
        return {"image": f"data:image/png;base64,{img_base64}"}
        
    except Exception as e:
        logger.error(f"차트 생성 오류: {e}")
        raise HTTPException(status_code=500, detail=f"차트 생성 실패: {str(e)}")

@app.get("/stocks/{stock_code}/news")
# 기존 /stocks/{stock_code}/news 엔드포인트를 /news/{stock_code}로 변경
@app.get("/news/{stock_code}")
async def get_stock_news(stock_code: str, stock_name: str = None):
    """
    네이버 뉴스 검색 API를 사용하여 종목 관련 뉴스 조회
    """
    try:
        # API 키 확인
        if not config.NAVER_CLIENT_ID or not config.NAVER_CLIENT_SECRET:
            logger.error("네이버 API 키가 설정되지 않았습니다.")
            return {
                "items": [],
                "total": 0,
                "start": 1,
                "display": 0,
                "error": "API 키가 설정되지 않았습니다."
            }
        
        # 검색 쿼리 생성 (종목명 우선, 없으면 종목코드 사용)
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
            logger.error(f"네이버 API 오류: {response.status_code} - {response.text}")
            return {
                "items": [],
                "total": 0,
                "start": 1,
                "display": 0,
                "error": f"API 오류: {response.status_code}"
            }
            
        news_data = response.json()
        
        # HTML 태그 제거 및 데이터 정리
        if "items" in news_data:
            for item in news_data["items"]:
                item["title"] = re.sub(r'<[^>]+>', '', item["title"])
                item["description"] = re.sub(r'<[^>]+>', '', item["description"])
                
                if "pubDate" in item:
                    try:
                        from datetime import datetime
                        pub_date = datetime.strptime(item["pubDate"], "%a, %d %b %Y %H:%M:%S %z")
                        item["pubDate"] = pub_date.strftime("%Y-%m-%d %H:%M")
                    except:
                        pass
        
        logger.info(f"종목 {stock_code}({stock_name}) 뉴스 {len(news_data.get('items', []))}건 조회")
        return news_data
        
    except Exception as e:
        logger.error(f"뉴스 조회 오류: {e}")
        return {
            "items": [],
            "total": 0,
            "start": 1,
            "display": 0,
            "error": str(e)
        }
