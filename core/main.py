from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import os
import asyncio
import time
from typing import Optional, Dict, Any, List
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
from core.models import (
    get_db,
    AutoTradeCondition,
    PendingBuySignal,
    AutoTradeSettings,
    WatchlistStock,
    TradingStrategy,
    StrategySignal,
    Position,
    PositionBuyFill,
)
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
from managers.stop_loss_manager import stop_loss_manager
from managers.auto_trade_scanner import auto_trade_scanner
from managers.scalping_strategy import scalping_manager
from managers.cleanup_scheduler import cleanup_scheduler
from managers.market_open_scheduler import market_open_scheduler, should_auto_start_now
from utils.debug_tracer import debug_tracer, enable_debug_mode, disable_debug_mode, is_debug_enabled
from utils.auto_trade_engine import (
    allows_new_buy,
    auto_trade_engines_allowed,
    check_daily_limits,
    get_today_realized_pnl,
    has_buy_conditions,
    in_trade_hours,
    new_buy_block_reason,
)
from utils.market_hours import is_krx_trading_day, trading_day_block_reason
from utils.auto_trade_activity_log import activity_log, log_activity
from utils.datetime_kst import utc_naive_to_api_iso

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

# 손절/익절 모니터링 매니저 — managers.stop_loss_manager 싱글톤 사용


def _schedule_stop_loss_monitoring():
    """포지션 동기화 루프 1개만 기동."""
    if stop_loss_manager.monitoring_task_running():
        return
    if stop_loss_manager.schedule_monitoring():
        task = asyncio.create_task(stop_loss_manager.start_monitoring())
        stop_loss_manager.attach_monitor_task(task)


async def apply_auto_trade_state(enabled: bool):
    """UI의 자동매매 ON/OFF에 따라 실행 서브시스템을 일괄 시작/중지한다.
    - ON  : 종목 스캐너 + 매수 실행기 + 손절/익절 모니터링
    - OFF : 위 3가지 + (혹시 돌고 있으면) 조건식 주기 검색 중지
    조건식 실시간 검색(CNSRREQ)은 사용하지 않음 — 대시보드 설정(관심종목/스크리너) 기반.
    """
    try:
        if enabled:
            allowed, off_reason = auto_trade_engines_allowed()
            if not allowed:
                logger.info(
                    f"✅ [AUTO_TRADE] 자동매매 ON (DB) — {off_reason}, 스캐너·매수 미기동"
                )
                log_activity(
                    "SYSTEM",
                    f"자동매매 ON — {off_reason}, 스캐너·매수 대기",
                    "warn",
                )
                return
            if not auto_trade_scanner.is_running:
                asyncio.create_task(auto_trade_scanner.start())
            if not buy_order_executor.is_running:
                asyncio.create_task(buy_order_executor.start_processing())
            if not stop_loss_manager.monitoring_task_running():
                _schedule_stop_loss_monitoring()
            logger.info("✅ [AUTO_TRADE] 자동매매 ON → 스캔/매수/동기화 시작")
            log_activity("SYSTEM", "자동매매 ON — 스캐너·매수·동기화 시작", "info")
        else:
            try:
                await auto_trade_scanner.stop()
            except Exception as e:
                logger.warning(f"[AUTO_TRADE] 스캐너 중지 경고: {e}")
            try:
                await buy_order_executor.stop_processing()
            except Exception as e:
                logger.warning(f"[AUTO_TRADE] 매수 실행기 중지 경고: {e}")
            try:
                await stop_loss_manager.stop_monitoring()
            except Exception as e:
                logger.warning(f"[AUTO_TRADE] 포지션 동기화 루프 중지 경고: {e}")
            try:
                await condition_monitor.stop_all_monitoring()
            except Exception as e:
                logger.warning(f"[AUTO_TRADE] 조건 모니터링 중지 경고: {e}")
            logger.info("🛑 [AUTO_TRADE] 자동매매 OFF → 스캔/매수/동기화 중지")
            log_activity("SYSTEM", "자동매매 OFF — 스캔·매수·동기화 중지", "warn")
    except Exception as e:
            logger.error(f"[AUTO_TRADE] 상태 적용 실패(enabled={enabled}): {e}")


def _disable_windows_console_quickedit() -> None:
    """Windows 콘솔 QuickEdit 클릭 시 프로세스가 멈추는 현상 방지."""
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        enable_quick_edit = 0x0040
        enable_insert_mode = 0x0020
        for handle_id in (-10, -11, -12):
            handle = kernel32.GetStdHandle(handle_id)
            if handle in (0, -1):
                continue
            mode = ctypes.c_uint32()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                continue
            new_mode = mode.value & ~enable_quick_edit & ~enable_insert_mode
            kernel32.SetConsoleMode(handle, new_mode)
    except Exception:
        pass


_disable_windows_console_quickedit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("🌐 [STARTUP] 애플리케이션 시작")

    # DB 테이블 자동 생성 (없으면 생성, 있으면 무시) - SQLite/PostgreSQL 공통
    try:
        from core.models import init_db
        init_db()
        logger.info("🗄️ [STARTUP] DB 테이블 초기화 완료")
        try:
            from utils.position_buy_fills import repair_positions_from_buy_fills
            for _db in get_db():
                repaired = repair_positions_from_buy_fills(_db)
                if repaired:
                    logger.info(f"🔧 [STARTUP] 체결 이력 기준 포지션 {repaired}건 보정")
                break
        except Exception as _e:
            logger.warning(f"🔧 [STARTUP] 포지션 체결 이력 보정 스킵: {_e}")
    except Exception as e:
        logger.error(f"🗄️ [STARTUP] DB 초기화 실패: {e}")

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
    
    # 자동매매 실행기: 장 시작 자동 기동 또는 DB is_enabled에 따라 시작
    try:
        from core.models import AutoTradeSettings as _ATS
        settings_row = None
        for _db in get_db():
            settings_row = _db.query(_ATS).first()
            break

        if should_auto_start_now(settings_row):
            if settings_row and not settings_row.is_enabled:
                for _db in get_db():
                    _s = _db.query(_ATS).first()
                    if _s:
                        _s.is_enabled = True
                        _s.updated_at = datetime.utcnow()
                        _db.commit()
                    break
            await apply_auto_trade_state(True)
            market_open_scheduler.mark_started_today()
            log_activity("SYSTEM", "서버 시작 — 거래일 자동매매 기동", "info")
            logger.info("💰 [STARTUP] 거래일 자동매매 기동")
        elif settings_row and settings_row.is_enabled:
            allowed, off = auto_trade_engines_allowed()
            if allowed:
                await apply_auto_trade_state(True)
                log_activity("SYSTEM", "서버 시작 — 자동매매 ON 상태로 기동", "info")
                logger.info("💰 [STARTUP] 자동매매 ON 상태 → 실행기 가동")
            else:
                log_activity("SYSTEM", f"서버 시작 — 자동매매 ON ({off}, 스캐너·매수 대기)", "warn")
                logger.info(f"💰 [STARTUP] 자동매매 ON — {off}, 스캐너·매수 미기동")
        else:
            logger.info("⏸️ [STARTUP] 자동매매 OFF 상태 → 실행기 미기동 (UI에서 시작)")

        # 장중 세션에서만 보유종목/포지션 동기화 루프를 유지
        allowed, off_reason = auto_trade_engines_allowed()
        if allowed:
            if stop_loss_manager.monitoring_task_running():
                logger.info("🔄 [STARTUP] 포지션 동기화 루프 — 이미 기동됨 (중복 스킵)")
            else:
                _schedule_stop_loss_monitoring()
                logger.info("🔄 [STARTUP] 보유종목·포지션 동기화 루프 시작 (2분 주기)")
                log_activity("SYSTEM", "포지션 동기화 루프 시작 (2분 주기)", "info")
        else:
            logger.info(f"🔄 [STARTUP] {off_reason} — 포지션 동기화 루프 미기동")
            log_activity("SYSTEM", f"{off_reason} — 포지션 동기화 루프 미기동", "warn")

        # 조건식 주기 검색은 기본 중지 (서버 재시작 시 이전 루프 잔존 방지)
        try:
            await condition_monitor.stop_all_monitoring()
        except Exception as e:
            logger.warning(f"[STARTUP] 조건식 모니터링 정리 경고: {e}")
    except Exception as e:
        logger.error(f"[STARTUP] 자동매매 상태 적용 실패: {e}")

    try:
        # 자정 정리 스케줄러 시작 (자동매매와 무관한 정리 작업)
        asyncio.create_task(cleanup_scheduler.start_scheduler())
        logger.info("🕛 [STARTUP] 자정 정리 스케줄러 시작")
    except Exception as e:
        logger.error(f"🕛 [STARTUP] 자정 정리 스케줄러 시작 실패: {e}")

    try:
        asyncio.create_task(market_open_scheduler.start_scheduler())
        logger.info("🕗 [STARTUP] 장 시작 자동매매 스케줄러 시작")
    except Exception as e:
        logger.error(f"🕗 [STARTUP] 장 시작 자동매매 스케줄러 시작 실패: {e}")
    
    yield
    
    # Shutdown
    logger.info("모니터링 시스템 종료")
    
    # 개선된 시스템들 종료
    try:
        await auto_trade_scanner.stop()
        logger.info("📈 [SHUTDOWN] 자동매매 스캐너 종료")
    except Exception as e:
        logger.error(f"📈 [SHUTDOWN] 자동매매 스캐너 종료 실패: {e}")

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


def _sync_condition_api_ids(session: Session, conditions_data: list) -> None:
    """키움 조건식 목록의 API ID를 DB에 저장 (모니터링/동기화 시 CNSRLST 재조회 방지)."""
    for i, condition_data in enumerate(conditions_data):
        name = condition_data.get("condition_name", f"조건식_{i + 1}")
        api_id = str(condition_data.get("condition_id", str(i)))
        row = session.query(AutoTradeCondition).filter(
            AutoTradeCondition.condition_name == name
        ).first()
        if row is None:
            session.add(AutoTradeCondition(
                condition_name=name,
                api_condition_id=api_id,
                is_enabled=False,
                updated_at=datetime.utcnow(),
            ))
        elif row.api_condition_id != api_id:
            row.api_condition_id = api_id
            row.updated_at = datetime.utcnow()
    session.commit()

class TradingSettingsRequest(BaseModel):
    is_enabled: bool
    max_invest_amount: int
    stop_loss_rate: float
    take_profit_rate: float
    # 고급 설정 (모두 선택적 — 미전달 시 기존값 유지)
    watchlist_codes: Optional[str] = None
    include_leverage: Optional[bool] = None
    include_inverse: Optional[bool] = None
    include_double_inverse: Optional[bool] = None
    buy_below_price: Optional[int] = None
    min_change_rate_buy: Optional[float] = None
    trailing_stop_pct: Optional[float] = None
    atr_mult_stop: Optional[float] = None
    atr_mult_trail: Optional[float] = None
    atr_period: Optional[int] = None
    profit_lock_trigger: Optional[float] = None
    profit_lock_floor: Optional[float] = None
    use_entry_gate: Optional[bool] = None
    require_above_open: Optional[bool] = None
    require_above_vwap: Optional[bool] = None
    day_position_min: Optional[float] = None
    day_position_max: Optional[float] = None
    volume_ratio_min: Optional[float] = None
    sizing_method: Optional[str] = None
    initial_min_amount: Optional[int] = None
    initial_max_amount: Optional[int] = None
    signal_min_threshold: Optional[float] = None
    signal_max_threshold: Optional[float] = None
    add_buy_amount: Optional[int] = None
    add_buy_trigger: Optional[float] = None
    max_concurrent_positions: Optional[int] = None
    cash_reserve_pct: Optional[float] = None
    max_daily_buys: Optional[int] = None
    daily_loss_limit: Optional[int] = None
    daily_profit_target: Optional[int] = None
    reorder_cooldown_sec: Optional[int] = None
    trade_start_time: Optional[str] = None
    trade_end_time: Optional[str] = None
    liquidate_before_close: Optional[bool] = None
    liquidate_time: Optional[str] = None
    order_method: Optional[str] = None

# 자동매매 설정에서 프론트와 주고받는 전체 필드 목록
AUTO_TRADE_FIELDS = [
    "is_enabled", "max_invest_amount", "stop_loss_rate", "take_profit_rate",
    "watchlist_codes",
    "include_leverage", "include_inverse", "include_double_inverse",
    "buy_below_price", "min_change_rate_buy",
    "trailing_stop_pct", "atr_mult_stop", "atr_mult_trail", "atr_period",
    "profit_lock_trigger", "profit_lock_floor",
    "use_entry_gate", "require_above_open", "require_above_vwap",
    "day_position_min", "day_position_max", "volume_ratio_min",
    "sizing_method", "initial_min_amount", "initial_max_amount",
    "signal_min_threshold", "signal_max_threshold", "add_buy_amount", "add_buy_trigger",
    "max_concurrent_positions", "cash_reserve_pct", "max_daily_buys", "daily_loss_limit", "daily_profit_target",
    "reorder_cooldown_sec", "trade_start_time", "trade_end_time",
    "liquidate_before_close", "liquidate_time", "order_method",
]


def _balance_int(v) -> int:
    from api.kiwoom_api import _parse_kiwoom_int
    return _parse_kiwoom_int(v)


def _active_holdings(holdings) -> list:
    """수량>0인 보유 종목만."""
    out = []
    for h in holdings or []:
        if not isinstance(h, dict):
            continue
        qty = _balance_int(h.get("qty") or h.get("rmnd_qty"))
        if qty > 0:
            out.append(h)
    return out


def resolve_balance_stock_eval(stock_eval_api: int, holdings) -> tuple:
    """주식 평가합 — 보유 종목 합산 우선. 보유 0이면 API tot_est_amt 무시.

    Returns: (stock_eval, stock_eval_sum, holding_count, stale_api_stock_eval)
    """
    active = _active_holdings(holdings)
    stock_eval_sum = sum(_balance_int(h.get("evlt_amt")) for h in active)
    count = len(active)
    if count == 0:
        stale = stock_eval_api if stock_eval_api > 0 else 0
        return 0, 0, 0, stale
    if stock_eval_sum > 0:
        return stock_eval_sum, stock_eval_sum, count, 0
    if stock_eval_api > 0:
        return stock_eval_api, stock_eval_sum, count, 0
    return 0, stock_eval_sum, count, 0


def enrich_balance_cash_reserve(balance_data: dict) -> dict:
    """계좌 잔고 응답에 현금 보유·매수 가능·자금 내역 분해 필드를 추가."""
    from utils.auto_trade_engine import compute_investable_cash, cash_reserve_pct

    entr = _balance_int(balance_data.get("entr"))
    d2 = _balance_int(balance_data.get("d2_entra"))
    stock_eval_api = _balance_int(balance_data.get("tot_est_amt"))
    aset_evlt = _balance_int(balance_data.get("aset_evlt_amt"))
    total_asset = _balance_int(balance_data.get("prsm_dpst_aset_amt"))
    if total_asset <= 0:
        total_asset = aset_evlt

    holdings = balance_data.get("stk_acnt_evlt_prst") or []
    stock_eval, stock_eval_sum, holding_count, stale_api_stock = resolve_balance_stock_eval(
        stock_eval_api, holdings,
    )

    settings = None
    for db in get_db():
        settings = db.query(AutoTradeSettings).first()
        break
    pct = cash_reserve_pct(settings) if settings else 10.0
    investable_raw, reserve = compute_investable_cash(entr, settings)
    investable = investable_raw
    d2_cap_applied = False
    if d2 <= 0:
        investable = 0
    elif investable_raw > d2:
        investable = d2
        d2_cap_applied = True

    computed_total = entr + stock_eval
    total_gap = total_asset - computed_total if total_asset > 0 else 0
    if holding_count == 0:
        effective_total = entr
        total_stale = total_asset > entr + 1
    else:
        effective_total = total_asset if total_asset > 0 else computed_total
        total_stale = total_asset > 0 and abs(total_gap) > 1

    balance_data["cash_reserve_pct"] = pct
    balance_data["cash_reserve"] = reserve
    balance_data["investable_cash"] = investable
    balance_data["balance_breakdown"] = {
        "deposit": entr,
        "d2_deposit": d2,
        "stock_eval_api": stock_eval_api,
        "stock_eval_holdings_sum": stock_eval_sum,
        "stock_eval": stock_eval,
        "stale_api_stock_eval": stale_api_stock,
        "holding_count": holding_count,
        "deposit_asset_eval": aset_evlt,
        "total_asset": total_asset,
        "effective_total_asset": effective_total,
        "total_asset_stale": total_stale,
        "cash_reserve_pct": pct,
        "cash_reserve": reserve,
        "investable_cash": investable,
        "investable_before_d2_cap": investable_raw,
        "d2_cap_applied": d2_cap_applied,
        "computed_deposit_plus_stock": computed_total,
        "total_asset_gap": total_gap,
    }
    return balance_data


# 계좌 잔고 단기 캐시 (대시보드 자동갱신·API 제한 완화)
_balance_cache: dict = {"at": 0.0, "data": None}
BALANCE_CACHE_SEC = 60
BALANCE_STALE_MAX_SEC = 300


def _is_usable_balance_cache(data: dict | None) -> bool:
    return bool(data and data.get("_api_connected"))


def _return_stale_balance(cached: dict, reason: str = "") -> dict:
    stale = dict(cached)
    stale["_cached"] = True
    stale["_stale"] = True
    if reason:
        stale["_refresh_note"] = reason
    logger.debug(f"🌐 [API] 계좌 조회 — 캐시 데이터 사용 ({reason})")
    return enrich_balance_cash_reserve(stale)


def _empty_balance_error(account_number: str, account_type: str, err: str = "") -> dict:
    return {
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
        "_account_type": account_type,
        "_error_detail": err,
    }

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


@app.get("/favicon.ico", include_in_schema=False)
async def favicon_ico():
    """브라우저 기본 favicon 요청."""
    return RedirectResponse(url="/static/favicon.svg")


@app.get("/health")
async def health_check():
    """서버 생존 확인 — 키움 API 호출 없이 즉시 응답."""
    return {"ok": True, "ts": datetime.now().isoformat()}

@app.get("/")
async def root():
    """메인 페이지 — 통합 대시보드"""
    return RedirectResponse(url="/dashboard")

@app.get("/status")
async def status_page():
    """서버 상태 페이지로 리다이렉트"""
    return RedirectResponse(url="/static/server_status.html")

@app.get("/dashboard")
async def dashboard_page():
    """통합 대시보드 페이지로 리다이렉트"""
    return RedirectResponse(url="/static/dashboard.html")

@app.get("/glossary")
async def glossary_page():
    """용어정리 페이지"""
    return RedirectResponse(url="/static/glossary.html")

@app.get("/analysis")
async def analysis_page():
    """기본적분석 마트 조회 페이지"""
    return RedirectResponse(url="/static/analysis.html")

@app.get("/verify")
async def verify_page():
    """자동매매 검증 페이지"""
    return RedirectResponse(url="/static/verify.html")

@app.get("/verification/trades")
async def get_verification_trades(limit: int = 100, date: Optional[str] = None):
    """자동매매 라운드트립 검증 — 매수/매도 시각·조건·계산식.
    date: YYYY-MM-DD (KST 기준). 매수 또는 매도가 해당 일자인 건만 표시.
    date=this_week — 이번 주 월~금(KST). 생략 또는 all 이면 전체.
    """
    try:
        from utils.trade_verification import build_verification_report

        for db in get_db():
            session: Session = db
            return await build_verification_report(
                session,
                limit=min(max(limit, 1), 500),
                trade_date=date,
            )
        return {"success": False, "error": "DB 연결 실패"}
    except Exception as e:
        logger.error(f"검증 리포트 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="검증 리포트 조회 중 오류가 발생했습니다.")

@app.get("/verification/chart")
async def get_verification_intraday_chart(
    stock_code: str,
    date: str,
    end_date: Optional[str] = None,
    buy_time: Optional[str] = None,
    sell_time: Optional[str] = None,
    sell_date: Optional[str] = None,
    buy_price: Optional[int] = None,
    sell_price: Optional[int] = None,
):
    """매매 구간 15분봉 차트 — 매수·매도일 봉을 한 차트로 합침."""
    import re
    from datetime import timedelta

    if not re.match(r"^\d{4}-\d{2}-\d{2}$", (date or "").strip()[:10]):
        raise HTTPException(status_code=400, detail="date는 YYYY-MM-DD 형식이어야 합니다.")

    start_s = date.strip()[:10]
    end_s = (end_date or sell_date or start_s).strip()[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", end_s):
        end_s = start_s

    try:
        d0 = datetime.strptime(start_s, "%Y-%m-%d").date()
        d1 = datetime.strptime(end_s, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="날짜 형식이 올바르지 않습니다.")

    if d1 < d0:
        d0, d1 = d1, d0

    fetch_dates: List[str] = []
    cur = d0
    while cur <= d1 and len(fetch_dates) < 7:
        fetch_dates.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)

    all_bars: List[Dict[str, Any]] = []
    warnings: List[str] = []
    cached_any = False
    last_error = ""

    for fd in fetch_dates:
        result = await kiwoom_api.get_intraday_chart_for_date(
            stock_code, fd, tic_scope="15", max_pages=1
        )
        if result.get("cached"):
            cached_any = True
        if result.get("warning"):
            warnings.append(str(result["warning"]))
        if result.get("error") and not result.get("bars"):
            last_error = str(result["error"])
        all_bars.extend(result.get("bars") or [])

    seen: set = set()
    bars: List[Dict[str, Any]] = []
    for b in sorted(all_bars, key=lambda x: x.get("timestamp", "")):
        ts = b.get("timestamp")
        if ts and ts not in seen:
            seen.add(ts)
            bars.append(b)

    markers: Dict[str, Any] = {}
    if buy_time:
        markers["buy"] = {"time": buy_time, "price": buy_price}
    if sell_time:
        markers["sell"] = {"time": sell_time, "price": sell_price}

    date_range = [fetch_dates[0], fetch_dates[-1]] if fetch_dates else [start_s]
    ok = bool(bars)

    return {
        "success": ok,
        "stock_code": stock_code,
        "date": start_s,
        "date_range": date_range,
        "interval": "15M",
        "bars": bars,
        "bar_count": len(bars),
        "markers": markers,
        "error": None if ok else (last_error or "분봉 데이터가 없습니다"),
        "warning": " · ".join(dict.fromkeys(warnings)) if warnings else None,
        "cached": cached_any,
    }

@app.get("/telegram/status")
async def telegram_status():
    """텔레그램 알림 설정 상태 조회"""
    from notifications.telegram_notifier import TelegramNotifier
    from utils.market_hours import telegram_market_alert_block_reason, telegram_market_alert_allowed
    notifier = TelegramNotifier()
    chat_id = Config.TELEGRAM_CHAT_ID or ""
    masked = (chat_id[:3] + "***" + chat_id[-2:]) if len(chat_id) > 5 else ("***" if chat_id else "")
    return {
        "configured": notifier.is_configured(),
        "chat_id_masked": masked,
        "condition_filter": Config.TELEGRAM_ALERT_CONDITION_NAMES,
        "interval": Config.TELEGRAM_ALERT_INTERVAL,
        "max_stocks": Config.TELEGRAM_ALERT_MAX_STOCKS,
        "market_hours_only": Config.TELEGRAM_ALERT_MARKET_HOURS_ONLY,
        "alert_allowed_now": telegram_market_alert_allowed(),
        "alert_block_reason": telegram_market_alert_block_reason(),
    }

@app.post("/telegram/send-now")
async def telegram_send_now():
    """조건식 조회 결과를 지금 즉시 텔레그램으로 전송"""
    try:
        from notifications.telegram_notifier import TelegramNotifier
        from notifications.condition_alert import send_condition_alert

        notifier = TelegramNotifier()
        if not notifier.is_configured():
            return {"success": False, "message": "텔레그램 설정이 없습니다. .env의 TELEGRAM_BOT_TOKEN/CHAT_ID를 확인하세요."}

        names = Config.TELEGRAM_ALERT_CONDITION_NAMES or None
        result = await send_condition_alert(
            kiwoom_api, notifier, names=names, skip_market_hours_check=True
        )
        return {
            "success": bool(result.get("sent")),
            "condition_count": result.get("condition_count", 0),
            "stock_count": result.get("stock_count", 0),
            "message": "전송 완료" if result.get("sent") else "전송 실패 (로그 확인)",
        }
    except Exception as e:
        logger.error(f"텔레그램 즉시 전송 오류: {e}")
        raise HTTPException(status_code=500, detail="텔레그램 전송 중 오류가 발생했습니다.")

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
                    "detected_at": utc_naive_to_api_iso(r.detected_at),
                    "status": r.status,
                    "signal_type": getattr(r, "signal_type", "condition"),
                    "failure_reason": getattr(r, "failure_reason", None),
                    "target_price": getattr(r, "target_price", None),
                    "current_price": current_price,
                    "target_quantity": target_quantity,
                    "target_amount": target_amount,
                    "additional_data": getattr(r, "additional_data", None),
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
                        "buy_time": utc_naive_to_api_iso(position.buy_time),
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

            result = {f: getattr(settings, f, None) for f in AUTO_TRADE_FIELDS}
            result["updated_at"] = settings.updated_at.isoformat() if settings.updated_at else None
            return result
    except Exception as e:
        logger.error(f"자동매매 설정 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="자동매매 설정 조회 실패")

@app.get("/trading/readiness")
async def get_trading_readiness():
    """자동매매 파이프라인 준비 상태 (장외 점검용)."""
    try:
        settings = None
        for db in get_db():
            settings = db.query(AutoTradeSettings).first()
            break

        mock = Config.KIWOOM_USE_MOCK_ACCOUNT
        acct = Config.KIWOOM_MOCK_ACCOUNT_NUMBER if mock else Config.KIWOOM_ACCOUNT_NUMBER
        token_ok = bool(kiwoom_api.token_manager.get_valid_token() or kiwoom_api.authenticate())

        daily_halt = check_daily_limits(settings) if settings else None
        checks = {
            "api_authenticated": token_ok,
            "account_configured": bool(acct),
            "settings_exist": settings is not None,
            "buy_conditions_set": has_buy_conditions(settings) if settings else False,
            "auto_trade_enabled": bool(settings and settings.is_enabled),
            "in_trade_hours": in_trade_hours(settings) if settings else False,
            "is_trading_day": is_krx_trading_day(),
            "trading_day_block_reason": trading_day_block_reason(),
            "allows_new_buy": allows_new_buy(settings) if settings else False,
            "new_buy_block_reason": new_buy_block_reason(settings) if settings else None,
            "daily_limit_ok": daily_halt is None,
            "scanner_running": auto_trade_scanner.is_running,
            "buy_executor_running": buy_order_executor.is_running,
            "stop_loss_running": stop_loss_manager.is_running,
        }
        phase2 = {
            "entry_gate": bool(settings and settings.use_entry_gate),
            "pyramiding": bool(settings and (settings.sizing_method or "").upper() == "PYRAMIDING"),
            "daily_limits": bool(settings and (settings.daily_loss_limit or settings.daily_profit_target)),
            "order_method": (settings.order_method if settings else "MARKET") or "MARKET",
        }
        ready = (
            checks["api_authenticated"]
            and checks["account_configured"]
            and checks["settings_exist"]
            and checks["buy_conditions_set"]
            and checks["daily_limit_ok"]
        )
        return {
            "ready": ready,
            "checks": checks,
            "phase2": phase2,
            "today_realized_pnl": get_today_realized_pnl(),
            "daily_halt_reason": daily_halt,
            "mock_mode": mock,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"자동매매 readiness 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="readiness 조회 실패")

@app.get("/trading/activity-log")
async def get_trading_activity_log(limit: int = 80):
    """자동매매 실시간 활동 로그 + 실행 상태."""
    try:
        settings = None
        for db in get_db():
            settings = db.query(AutoTradeSettings).first()
            break

        scan = auto_trade_scanner.get_status()
        daily_halt = check_daily_limits(settings) if settings else None
        runtime = {
            "auto_trade_enabled": bool(settings and settings.is_enabled),
            "in_trade_hours": in_trade_hours(settings) if settings else False,
            "is_trading_day": is_krx_trading_day(),
            "trading_day_block_reason": trading_day_block_reason(),
            "allows_new_buy": allows_new_buy(settings) if settings else False,
            "new_buy_block_reason": new_buy_block_reason(settings) if settings else None,
            "scanner_running": auto_trade_scanner.is_running,
            "buy_executor_running": buy_order_executor.is_running,
            "stop_loss_running": stop_loss_manager.is_running,
            "last_sync_at": (
                stop_loss_manager._last_cycle_at.isoformat()
                if getattr(stop_loss_manager, "_last_cycle_at", None)
                else None
            ),
            "monitor_interval_sec": stop_loss_manager.monitoring_interval,
            "last_scan_at": scan.get("last_scan_at"),
            "last_scan_targets": scan.get("last_scan_targets", 0),
            "last_scan_created": scan.get("last_scan_created", 0),
            "scan_interval_sec": scan.get("scan_interval_sec", 120),
            "daily_halt_reason": daily_halt,
            "today_realized_pnl": get_today_realized_pnl(),
            "mock_mode": Config.KIWOOM_USE_MOCK_ACCOUNT,
        }
        return {
            "runtime": runtime,
            "events": activity_log.get_recent(min(max(limit, 1), 200)),
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"활동 로그 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="활동 로그 조회 실패")

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

            # 전달된 값만 반영 (None은 기존값 유지, 수익잠금은 요청에 포함된 null로 비활성화)
            fields_set = getattr(req, "model_fields_set", None) or set()
            for f in AUTO_TRADE_FIELDS:
                if not hasattr(req, f):
                    continue
                val = getattr(req, f)
                if val is not None:
                    setattr(settings, f, val)
                elif f in ("profit_lock_trigger", "profit_lock_floor") and f in fields_set:
                    setattr(settings, f, None)
            if (settings.sizing_method or "").upper() == "PYRAMIDING":
                from utils.auto_trade_engine import normalize_pyramid_amounts
                strong_amt, weak_amt = normalize_pyramid_amounts(
                    settings.initial_min_amount, settings.initial_max_amount,
                )
                settings.initial_min_amount = strong_amt
                settings.initial_max_amount = weak_amt
            settings.updated_at = datetime.utcnow()

            from managers.stop_loss_manager import stop_loss_manager
            synced = stop_loss_manager.propagate_exit_settings_to_holdings(session)
            session.commit()

            result = {f: getattr(settings, f, None) for f in AUTO_TRADE_FIELDS}
            if synced:
                result["positions_exit_synced"] = synced

        # UI의 자동매매 ON/OFF를 실제 실행기 상태에 반영 (세션 종료 후 호출)
        try:
            await apply_auto_trade_state(bool(req.is_enabled))
        except Exception as e:
            logger.error(f"자동매매 상태 적용 실패: {e}")

        result["message"] = "자동매매 설정이 저장되었습니다."
        return result
    except Exception as e:
        logger.error(f"자동매매 설정 저장 오류: {e}")
        raise HTTPException(status_code=500, detail="자동매매 설정 저장 실패")


class KrxHolidayRequest(BaseModel):
    holiday_date: str  # YYYY-MM-DD
    name: str
    is_closed: Optional[bool] = True


@app.get("/trading/holidays")
async def get_trading_holidays(year: Optional[int] = None):
    """KRX 휴장일 목록 (DB). year 생략 시 전체."""
    try:
        from utils.krx_holiday_store import list_holidays
        y = year if year is not None else datetime.now().year
        rows = list_holidays(y)
        return {"year": y, "holidays": rows, "count": len(rows)}
    except Exception as e:
        logger.error(f"휴장일 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="휴장일 조회 실패")


@app.post("/trading/holidays")
async def add_trading_holiday(req: KrxHolidayRequest):
    """휴장일 추가·같은 날짜면 이름 갱신."""
    try:
        from utils.krx_holiday_store import add_holiday
        d = datetime.strptime(req.holiday_date.strip()[:10], "%Y-%m-%d").date()
        row = add_holiday(d, req.name.strip() or "휴장", is_closed=req.is_closed is not False)
        return {"message": "휴장일이 저장되었습니다.", "holiday": row}
    except ValueError:
        raise HTTPException(status_code=400, detail="날짜 형식은 YYYY-MM-DD")
    except Exception as e:
        logger.error(f"휴장일 저장 오류: {e}")
        raise HTTPException(status_code=500, detail="휴장일 저장 실패")


@app.delete("/trading/holidays/{holiday_id}")
async def remove_trading_holiday(holiday_id: int):
    try:
        from utils.krx_holiday_store import delete_holiday
        if not delete_holiday(holiday_id):
            raise HTTPException(status_code=404, detail="휴장일을 찾을 수 없습니다")
        return {"message": "휴장일이 삭제되었습니다."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"휴장일 삭제 오류: {e}")
        raise HTTPException(status_code=500, detail="휴장일 삭제 실패")


@app.get("/fundamentals")
async def get_fundamentals(
    market: Optional[str] = None,
    as_of: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
    sort_by: str = "market_cap",
    sort_desc: bool = True,
    max_per: Optional[float] = None,
    max_pbr: Optional[float] = None,
    min_roe: Optional[float] = None,
):
    """기본적분석 마트 최신 스냅샷 목록 (배치 적재 데이터)."""
    try:
        from utils.fundamental_mart_store import list_latest
        from datetime import datetime as dt

        as_of_date = None
        if as_of:
            as_of_date = dt.strptime(as_of.strip()[:10], "%Y-%m-%d").date()
        return list_latest(
            market=market,
            as_of_date=as_of_date,
            limit=min(limit, 2000),
            offset=max(offset, 0),
            sort_by=sort_by,
            sort_desc=sort_desc,
            max_per=max_per,
            max_pbr=max_pbr,
            min_roe=min_roe,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="as_of 형식은 YYYY-MM-DD")
    except Exception as e:
        logger.error(f"기본적분석 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="기본적분석 조회 실패")


@app.get("/fundamentals/summary")
async def get_fundamentals_summary(as_of: Optional[str] = None):
    """기본적분석 마트 현황 (총 건수·시장별 분포)."""
    try:
        from utils.fundamental_mart_store import get_summary
        from datetime import datetime as dt

        as_of_date = None
        if as_of:
            as_of_date = dt.strptime(as_of.strip()[:10], "%Y-%m-%d").date()
        return get_summary(as_of_date=as_of_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="as_of 형식은 YYYY-MM-DD")
    except Exception as e:
        logger.error(f"기본적분석 현황 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="기본적분석 현황 조회 실패")


@app.get("/fundamentals/{stock_code}")
async def get_fundamental_by_code(stock_code: str):
    """종목코드별 최신 기본적분석 스냅샷."""
    try:
        from utils.fundamental_mart_store import get_latest_by_code

        row = get_latest_by_code(stock_code)
        if not row:
            raise HTTPException(status_code=404, detail="해당 종목의 기본적분석 데이터가 없습니다")
        return row
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"기본적분석 조회 오류 ({stock_code}): {e}")
        raise HTTPException(status_code=500, detail="기본적분석 조회 실패")


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

        # DB의 자동매매 활성화 상태 로드 + API ID 동기화
        enabled_map = {}
        for db in get_db():
            session: Session = db
            _sync_condition_api_ids(session, conditions_data)
            rows = session.query(AutoTradeCondition).all()
            enabled_map = {row.condition_name: bool(row.is_enabled) for row in rows}
            break

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
async def get_condition_stocks(condition_id: int, condition_name: Optional[str] = None):
    """조건식으로 종목 목록 조회 (수동 호출 전용)"""
    logger.info(f"🌐 [API] /conditions/{condition_id}/stocks 엔드포인트 호출됨")
    try:
        logger.debug(f"조건식 종목 조회 시작: condition_id={condition_id}, name={condition_name}")

        condition_api_id = None
        resolved_name = condition_name

        if condition_name:
            for db in get_db():
                session: Session = db
                row = session.query(AutoTradeCondition).filter(
                    AutoTradeCondition.condition_name == condition_name
                ).first()
                if row and row.api_condition_id:
                    condition_api_id = row.api_condition_id
                    resolved_name = row.condition_name
                break

        if not condition_api_id:
            conditions_data = await kiwoom_api.get_condition_list_websocket()
            if not conditions_data:
                raise HTTPException(status_code=404, detail="조건식 목록을 가져올 수 없습니다.")
            condition_index = condition_id - 1
            if condition_index < 0 or condition_index >= len(conditions_data):
                raise HTTPException(status_code=404, detail="해당 조건식을 찾을 수 없습니다.")
            condition_info = conditions_data[condition_index]
            resolved_name = condition_info.get("condition_name", f"조건식_{condition_id}")
            condition_api_id = condition_info.get("condition_id", str(condition_index))
            for db in get_db():
                session: Session = db
                _sync_condition_api_ids(session, conditions_data)
                break

        logger.info(f"🌐 [API] 조건식 검색 시작: {resolved_name} (API ID: {condition_api_id})")
        stocks_data = await kiwoom_api.search_condition_stocks(condition_api_id, resolved_name)

        if not stocks_data:
            logger.info(f"🌐 [API] 조건식 '{resolved_name}'에 해당하는 종목이 없습니다.")
            return JSONResponse(
                content={
                    "condition_id": condition_id,
                    "condition_name": resolved_name,
                    "stocks": [],
                    "total_count": 0,
                },
                media_type="application/json; charset=utf-8",
            )

        response_data = {
            "condition_id": condition_id,
            "condition_name": resolved_name,
            "stocks": stocks_data,
            "total_count": len(stocks_data),
        }
        logger.info(f"🌐 [API] 조건식 종목 조회 완료: {resolved_name}, 종목 수: {len(stocks_data)}개")

        print(f"\n=== 조건식: {resolved_name} ===\n")
        print(f"총 {len(stocks_data)}개 종목")
        print("-" * 80)
        print(f"{'순번':<4} {'종목코드':<8} {'종목명':<20} {'현재가':<10} {'등락률':<8}")
        print("-" * 80)
        for i, stock in enumerate(stocks_data, 1):
            print(
                f"{i:<4} {stock.get('stock_code', ''):<8} {stock.get('stock_name', ''):<20} "
                f"{stock.get('current_price', ''):<10} {stock.get('change_rate', ''):<8}"
            )
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
    """(비활성) 예전 조건식 주기 검색 — 대시보드 자동매매 ON을 사용하세요."""
    logger.info("🌐 [API] /monitoring/start 호출 — 거부 (대시보드 자동매매 사용)")
    await condition_monitor.stop_all_monitoring()
    raise HTTPException(
        status_code=400,
        detail="조건식 주기 검색은 중단되었습니다. 대시보드(/dashboard)에서 '자동매매 시작'을 사용하세요.",
    )

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

        # 자동매매 스캐너 상태
        scanner_status = auto_trade_scanner.get_status()
        
        # 통합 상태 정보
        status = {
            "monitoring": monitoring_status,
            "auto_trade_scanner": scanner_status,
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
        now = time.monotonic()
        cached = _balance_cache.get("data")
        cache_age = now - _balance_cache.get("at", 0)
        if cached and cache_age < BALANCE_CACHE_SEC:
            return cached

        # 모의투자 계좌 사용 여부 확인
        use_mock_account = config.KIWOOM_USE_MOCK_ACCOUNT
        account_number = config.KIWOOM_MOCK_ACCOUNT_NUMBER if use_mock_account else config.KIWOOM_ACCOUNT_NUMBER
        account_type = "모의투자" if use_mock_account else "실계좌"
        
        logger.info(f"🌐 [API] 계좌 설정 - 타입: {account_type}, 번호: {account_number}")
        logger.info(f"🌐 [API] 계좌 정보 조회 - {account_type} 계좌: {account_number}")
        
        token_valid = bool(kiwoom_api.token_manager.get_valid_token())
        logger.info(f"🌐 [API] REST API 토큰 유효성: {token_valid}")
        
        if not token_valid:
            if _is_usable_balance_cache(cached) and cache_age < BALANCE_STALE_MAX_SEC:
                return _return_stale_balance(cached, "토큰 만료")
            logger.info("🌐 [API] 키움 API 토큰이 유효하지 않습니다.")
            balance_data = _empty_balance_error(account_number, account_type, "토큰 없음")
        else:
            logger.info(f"🌐 [API] 키움 REST API에서 {account_type} 계좌 정보 조회 중...")
            try:
                balance_data = await asyncio.wait_for(
                    kiwoom_api.get_account_balance(account_number),
                    timeout=15.0,
                )
            except asyncio.TimeoutError:
                if _is_usable_balance_cache(cached) and cache_age < BALANCE_STALE_MAX_SEC:
                    return _return_stale_balance(cached, "조회 타임아웃")
                logger.warning("🌐 [API] 키움 계좌 조회 타임아웃 (15초)")
                balance_data = _empty_balance_error(account_number, account_type, "타임아웃")
            
            if balance_data and balance_data.get("_cached"):
                balance_data.setdefault("_data_source", "CACHE")
                balance_data["_api_connected"] = True
                balance_data["_token_valid"] = True
                balance_data["_account_type"] = account_type
                balance_data["acnt_no"] = account_number
                logger.debug(f"🌐 [API] {account_type} 계좌 정보 — 캐시 데이터 사용")
            elif not balance_data or balance_data.get("_error"):
                err = (balance_data or {}).get("_error_msg") or (balance_data or {}).get("_error") or "키움 API 응답 없음"
                if _is_usable_balance_cache(cached) and cache_age < BALANCE_STALE_MAX_SEC:
                    return _return_stale_balance(cached, err)
                logger.warning(f"🌐 [API] 계좌 조회 실패 — {err}")
                balance_data = _empty_balance_error(account_number, account_type, err)
            else:
                balance_data["_data_source"] = "REAL_API"
                balance_data["_api_connected"] = True
                balance_data["_token_valid"] = True
                balance_data["_account_type"] = account_type
                balance_data["acnt_no"] = account_number
                logger.info(f"🌐 [API] 키움 REST API {account_type} 계좌 정보 조회 성공")
        
        logger.info(f"{account_type} 계좌 잔고 정보 조회 완료")
        result = enrich_balance_cash_reserve(balance_data)
        if result.get("_api_connected"):
            _balance_cache["at"] = time.monotonic()
            _balance_cache["data"] = result
        return result
        
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
async def get_order_history(limit: int = 100):
    """매수 주문·체결 내역 (ORDERED·FILLED·FAILED)."""
    try:
        orders = []
        for db in get_db():
            session: Session = db
            cap = min(max(limit, 1), 200)
            rows = (
                session.query(PendingBuySignal)
                .filter(
                    PendingBuySignal.status.in_(["ORDERED", "FAILED", "FILLED", "COMPLETED"])
                )
                .order_by(PendingBuySignal.detected_at.desc())
                .limit(cap)
                .all()
            )
            signal_ids = [r.id for r in rows]
            fills_by_signal: Dict[int, List[PositionBuyFill]] = {}
            if signal_ids:
                for fill in (
                    session.query(PositionBuyFill)
                    .filter(PositionBuyFill.signal_id.in_(signal_ids))
                    .order_by(PositionBuyFill.filled_at.asc())
                    .all()
                ):
                    fills_by_signal.setdefault(fill.signal_id, []).append(fill)

            for row in rows:
                fill_rows = fills_by_signal.get(row.id) or []
                initial = next(
                    (f for f in fill_rows if f.fill_type == "INITIAL"),
                    fill_rows[0] if fill_rows else None,
                )
                fill_qty = sum(int(f.quantity or 0) for f in fill_rows) if fill_rows else None
                fill_amt = sum(int(f.amount or 0) for f in fill_rows) if fill_rows else None
                orders.append(
                    {
                        "id": row.id,
                        "stock_code": row.stock_code,
                        "stock_name": row.stock_name,
                        "status": row.status,
                        "detected_at": utc_naive_to_api_iso(row.detected_at),
                        "filled_at": utc_naive_to_api_iso(initial.filled_at) if initial else None,
                        "fill_price": int(initial.price) if initial else None,
                        "fill_quantity": fill_qty,
                        "fill_amount": fill_amt,
                        "condition_id": row.condition_id,
                        "failure_reason": getattr(row, "failure_reason", None),
                    }
                )
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
                    "detected_at": utc_naive_to_api_iso(signal.detected_at),
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

@app.post("/stop-loss/reconcile")
async def reconcile_stop_loss():
    """키움 계좌 잔고 기준 매도 체결 확정 및 포지션 DB 동기화."""
    try:
        await stop_loss_manager._reconcile_sell_orders_and_holdings()
        return {"success": True, "message": "계좌 잔고와 포지션 동기화 완료"}
    except Exception as e:
        logger.error(f"손절/익절 동기화 오류: {e}")
        raise HTTPException(status_code=500, detail="포지션 동기화 중 오류가 발생했습니다.")

@app.post("/stop-loss/start")
async def start_stop_loss_monitoring():
    """손절/익절 모니터링 시작"""
    try:
        _schedule_stop_loss_monitoring()
        if stop_loss_manager.monitoring_task_running():
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
    """키움 잔고 기준 포지션 동기화 (체결 확인 + 현재가·평가손익)."""
    try:
        logger.info("📊 [API] Position 동기화 요청")
        await stop_loss_manager.sync_holdings_from_api()
        return {"message": "포지션 동기화가 완료되었습니다."}
    except Exception as e:
        logger.error(f"Position 현재가 업데이트 오류: {e}")
        raise HTTPException(status_code=500, detail="Position 현재가 업데이트 중 오류가 발생했습니다.")

@app.post("/positions/sync-actual-buy-amount")
async def sync_actual_buy_amount():
    """키움 kt00004 잔고 → 포지션 매입금액·평가손익 동기화."""
    try:
        await stop_loss_manager.sync_holdings_from_api()
        for db in get_db():
            session: Session = db
            from core.models import Position
            updated = session.query(Position).filter(
                Position.status == "HOLDING",
                Position.actual_buy_amount.isnot(None),
            ).count()
            break
        return {
            "message": f"포지션 API 동기화 완료: {updated}개",
            "updated_count": updated,
            "success": True,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"포지션 API 동기화 오류: {e}")
        raise HTTPException(status_code=500, detail=f"포지션 동기화 중 오류: {str(e)}")

@app.get("/positions/")
async def get_positions(status: str = "HOLDING", limit: int = 50, with_levels: bool = False, live: bool = False):
    """포지션 목록 조회. with_levels=true 시 청산 레벨 포함(live=false면 DB만, 빠름)."""
    try:
        if status == "HOLDING":
            await stop_loss_manager.sync_holdings_from_api(force=live)
        elif live:
            await stop_loss_manager.sync_holdings_from_api(force=True)

        positions = []
        global_settings = None
        for db in get_db():
            session: Session = db
            from core.models import AutoTradeSettings, Position
            global_settings = session.query(AutoTradeSettings).first()
            query = session.query(Position)
            if status != "ALL":
                query = query.filter(Position.status == status)
            positions = query.order_by(Position.buy_time.desc()).limit(min(limit, 100)).all()
            break

        items = []
        pending_sell_ids: set = set()
        if positions:
            pos_ids = [p.id for p in positions]
            for db in get_db():
                session: Session = db
                from core.models import SellOrder
                rows = session.query(SellOrder.position_id).filter(
                    SellOrder.position_id.in_(pos_ids),
                    SellOrder.status.in_(("PENDING", "ORDERED")),
                ).distinct().all()
                pending_sell_ids = {r[0] for r in rows}
                break

        for pos in positions:
            buy_amt = pos.actual_buy_amount or pos.buy_amount
            avg_price = pos.buy_price
            if (not avg_price or avg_price <= 0) and buy_amt and pos.buy_quantity:
                avg_price = int(round(buy_amt / pos.buy_quantity))
            row = {
                "id": pos.id,
                "stock_code": pos.stock_code,
                "stock_name": pos.stock_name,
                "buy_price": avg_price,
                "avg_buy_price": avg_price,
                "buy_quantity": pos.buy_quantity,
                "buy_amount": buy_amt,
                "current_price": pos.current_price,
                "current_profit_loss": pos.current_profit_loss,
                "current_profit_loss_rate": pos.current_profit_loss_rate,
                "stop_loss_rate": (
                    global_settings.stop_loss_rate
                    if global_settings and pos.status == "HOLDING"
                    else pos.stop_loss_rate
                ),
                "take_profit_rate": (
                    global_settings.take_profit_rate
                    if global_settings and pos.status == "HOLDING"
                    else pos.take_profit_rate
                ),
                "applied_stop_loss_rate": (
                    global_settings.stop_loss_rate if global_settings else pos.stop_loss_rate
                ),
                "applied_take_profit_rate": (
                    global_settings.take_profit_rate if global_settings else pos.take_profit_rate
                ),
                "stop_loss_price": pos.stop_loss_price,
                "peak_price": pos.peak_price,
                "status": pos.status,
                "signal_id": pos.signal_id,
                "condition_id": pos.condition_id,
                "actual_buy_amount": pos.actual_buy_amount,
                "amount_source": "kiwoom_api" if pos.actual_buy_amount else "db",
                "buy_time": utc_naive_to_api_iso(pos.buy_time),
                "sell_time": utc_naive_to_api_iso(pos.sell_time),
                "last_monitored": pos.last_monitored.isoformat() if pos.last_monitored else None,
                "pending_sell": pos.id in pending_sell_ids,
            }
            if with_levels and pos.status == "HOLDING":
                try:
                    ex = await stop_loss_manager.compute_exit_levels(pos, live=live)
                    row["exit_levels"] = stop_loss_manager.overlay_global_exit_settings(
                        ex, avg_price, global_settings,
                    )
                    ex = row["exit_levels"]
                    if ex.get("current_price"):
                        row["current_price"] = ex["current_price"]
                    # live=false일 때 exit_levels가 잘못된 재계산값으로 DB PnL을 덮어쓰지 않음
                    if live:
                        if ex.get("profit_loss") is not None:
                            row["current_profit_loss"] = ex["profit_loss"]
                        if ex.get("profit_loss_rate") is not None:
                            row["current_profit_loss_rate"] = ex["profit_loss_rate"]
                except Exception as e:
                    logger.warning(f"청산 레벨 계산 실패 {pos.stock_name}: {e}")
                    row["exit_levels"] = {}
            items.append(row)

        # 하위 호환: 기존 클라이언트가 positions 키를 기대하는 경우를 위해 동일 데이터 제공
        return {
            "items": items,
            "positions": items,
            "total": len(items),
            "status": status,
        }
    except Exception as e:
        logger.error(f"포지션 목록 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="포지션 목록 조회 중 오류가 발생했습니다.")


@app.get("/positions/intraday-sparklines")
async def get_position_intraday_sparklines(codes: str = ""):
    """보유 종목 당일 분봉 스파크라인 (15분봉 종가, 서버·키움 캐시 활용)."""
    from utils.intraday_sparkline import bars_to_sparkline, today_kst_date

    raw = [c.strip().replace("A", "") for c in (codes or "").split(",") if c.strip()]
    seen: set = set()
    code_list: List[str] = []
    for c in raw:
        if len(c) == 6 and c.isalnum() and c not in seen:
            seen.add(c)
            code_list.append(c)
        if len(code_list) >= 10:
            break

    trade_date = today_kst_date()
    sparklines: Dict[str, Any] = {}
    errors: Dict[str, str] = {}

    for code in code_list:
        try:
            result = await kiwoom_api.get_intraday_chart_for_date(
                code, trade_date, tic_scope="15", max_pages=1,
            )
            sp = bars_to_sparkline(result.get("bars") or [])
            if sp:
                sparklines[code] = sp
            elif result.get("error"):
                errors[code] = str(result["error"])
        except Exception as e:
            errors[code] = str(e)
        if len(code_list) > 1:
            await asyncio.sleep(0.05)

    return {
        "success": bool(sparklines),
        "date": trade_date,
        "interval": "15M",
        "sparklines": sparklines,
        "errors": errors,
    }


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
                    "created_at": utc_naive_to_api_iso(order.created_at),
                    "ordered_at": utc_naive_to_api_iso(order.ordered_at),
                    "completed_at": utc_naive_to_api_iso(order.completed_at)
                }
                for order in orders
            ],
            "total": len(orders),
            "status": status
        }
    except Exception as e:
        logger.error(f"매도 주문 목록 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="매도 주문 목록 조회 중 오류가 발생했습니다.")

@app.get("/performance/stats")
async def get_performance_stats(
    seed: int = 10000000,
    source: str = "db",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """매도 청산 기준 실현손익 성과 통계.

    source=db:   앱 DB 포지션 청산 (1포지션 완전 청산 = 1건, 검증 페이지와 동일 기준)
    source=auto: DB 우선 → ka10073(종목별) → ka10074(일별)
    """
    try:
        from datetime import timedelta

        from utils.performance_stats import (
            compute_performance,
            period_from_trades,
            trades_from_daily_realized_pnl,
            trades_from_db_closures,
            trades_from_realized_pnl,
        )
        from api.kiwoom_api import _parse_kiwoom_int

        source = (source or "db").lower()
        if source not in ("auto", "db"):
            raise HTTPException(status_code=400, detail="source는 auto 또는 db 여야 합니다.")

        KIWOOM_PNL_MAX_DAYS = 90
        end_dt = (end_date or datetime.now().strftime("%Y%m%d")).replace("-", "")
        end_base = datetime.strptime(end_dt, "%Y%m%d")
        strt_dt = (start_date.replace("-", "") if start_date
                   else (end_base - timedelta(days=KIWOOM_PNL_MAX_DAYS)).strftime("%Y%m%d"))
        min_strt = (end_base - timedelta(days=KIWOOM_PNL_MAX_DAYS)).strftime("%Y%m%d")
        if strt_dt < min_strt:
            strt_dt = min_strt

        kiwoom_period = {"start": strt_dt, "end": end_dt}

        def _respond(trades, pipeline, data_source, account_total=None, note=None, period=None):
            out = compute_performance(trades, seed, pipeline, data_source)
            out["period"] = period or kiwoom_period
            if account_total is not None:
                out["account_realized_net"] = account_total
            if note:
                out["note"] = note
            return out

        from core.models import Position, SellOrder

        sell_rows, pos_rows = [], []
        for db in get_db():
            session: Session = db
            sell_rows = session.query(SellOrder).order_by(SellOrder.completed_at.asc()).all()
            pos_rows = session.query(Position).order_by(Position.sell_time.asc()).all()
            break

        db_trades = trades_from_db_closures(sell_rows, pos_rows)
        db_period = period_from_trades(db_trades)

        if source == "db":
            return _respond(
                db_trades, "db", "app",
                note="포지션 청산 완료 기준 · 검증 페이지와 동일",
                period=db_period or kiwoom_period,
            )

        account_total = None
        res74 = {"success": False, "items": []}

        if db_trades:
            res74 = await kiwoom_api.get_daily_realized_pnl(strt_dt, end_dt)
            if res74.get("success"):
                account_total = _parse_kiwoom_int(res74.get("rlzt_pl"))
            return _respond(
                db_trades, "db", "app",
                account_total,
                note="앱 청산 기록 (포지션 1건 = 청산 1건)",
                period=db_period or kiwoom_period,
            )

        res74 = await kiwoom_api.get_daily_realized_pnl(strt_dt, end_dt)
        if res74.get("success"):
            account_total = _parse_kiwoom_int(res74.get("rlzt_pl"))

        res73 = await kiwoom_api.get_daily_stock_realized_pnl(strt_dt, end_dt, stk_cd="")
        if res73.get("success"):
            trades = trades_from_realized_pnl(res73.get("items") or [])
            if trades:
                return _respond(
                    trades, "api_stock", "kiwoom", account_total,
                    note="키움 체결 단위(분할매도 시 건수 증가)",
                )

        if res74.get("success"):
            trades = trades_from_daily_realized_pnl(res74.get("items") or [])
            if trades:
                return _respond(
                    trades, "api_daily", "kiwoom", account_total,
                    "종목별 내역 없음 — 일별 실현손익 사용 (승률은 일 단위)",
                )

        return _respond([], "empty", "none", account_total, period=kiwoom_period)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"성과 통계 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="성과 통계 조회 중 오류가 발생했습니다.")

@app.get("/screener/candidates")
async def get_screener_candidates(
    market: str = "000",
    limit: int = Config.SCREENER_CANDIDATE_LIMIT,
    exclude_etf: bool = True,
):
    """스크리너 후보 종목 조회.
    당일거래대금순(ka10030, sort_tp=3) 상위를 받아 **개별 주식(STOCK)** 만 후보로 반환한다.
    ETF/ETN/레버리지/인버스/곱버스는 API·후처리 단계에서 제외된다.
    """
    try:
        res = await kiwoom_api.get_volume_rank(market=market, sort_tp="3", limit=limit, screener_filters=True)
        if not res.get("success"):
            return {
                "success": False,
                "error": res.get("error", "조회 실패"),
                "items": [],
                "selected": [],
                "total": 0,
                "selected_count": 0,
                "raw_count": 0,
                "excluded_etf_count": 0,
            }

        raw_items = res.get("items") or []
        filtered, extra_excluded = KiwoomAPI._post_filter_screener_items(raw_items)
        raw_count = int(res.get("raw_count") or 0)
        excluded_etf_count = int(res.get("excluded_etf_count") or 0) + extra_excluded

        from utils.fundamental_mart_store import get_latest_map_by_codes as get_fundamental_map

        codes = [str(it.get("stock_code", "")).strip().zfill(6) for it in filtered if it.get("stock_code")]
        fundamental_map = get_fundamental_map(codes)

        candidates = []
        selected = []
        excluded_per_count = 0
        for it in filtered:
            row = dict(it)
            code = str(row.get("stock_code", "")).strip().zfill(6)
            fundamental = fundamental_map.get(code) or {}
            per = fundamental.get("per")
            per_ok = KiwoomAPI._is_screener_per_eligible(per)
            row["included"] = per_ok
            row["market_cap"] = fundamental.get("market_cap")
            row["per"] = per
            row["pbr"] = fundamental.get("pbr")
            row["roe"] = fundamental.get("roe")
            candidates.append(row)
            if per_ok:
                selected.append(row)
            else:
                excluded_per_count += 1

        return {
            "success": True,
            "items": candidates,
            "selected": selected,
            "total": len(candidates),
            "selected_count": len(selected),
            "raw_count": raw_count,
            "excluded_etf_count": excluded_etf_count,
            "excluded_per_count": excluded_per_count,
            "filter": {
                "stocks_only": True,
                "exclude_etf_family": True,
                "max_per": 100.0,
                "exclude_negative_per": True,
                "api": res.get("api_filters"),
            },
        }
    except Exception as e:
        logger.error(f"스크리너 후보 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="스크리너 후보 조회 중 오류가 발생했습니다.")

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
    """수동 강제 청산 (전량 시장가). sell_price는 호환용이며 실제로는 시장가 주문."""
    try:
        result = await stop_loss_manager.execute_manual_liquidation(position_id)
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error") or "매도 주문 실패")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"수동 매도 주문 오류: {e}")
        raise HTTPException(status_code=500, detail="수동 매도 주문 중 오류가 발생했습니다.")

