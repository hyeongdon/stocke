import json
import logging
import asyncio
import random
import re
import time
import websockets
import aiohttp
import ssl
from datetime import datetime, timedelta
from typing import Dict, Optional, Callable, List, Tuple
from core.config import Config
from api.api_rate_limiter import api_rate_limiter
from api.token_manager import TokenManager, get_token_manager
from utils.datetime_kst import kst_today, now_kst
from utils.api_traffic_guard import APIPriority

logger = logging.getLogger(__name__)

# 검증 차트: 거래일 1일 분봉 수 상한 (15분봉 09:00~15:30 ≈ 27봉, 여유 포함)
_VERIFY_INTRADAY_BARS_PER_DAY = {
    "1": 400,
    "3": 150,
    "5": 90,
    "10": 50,
    "15": 35,
    "30": 20,
    "60": 12,
}


def _expected_intraday_bars_for_day(tic_scope: str) -> int:
    return _VERIFY_INTRADAY_BARS_PER_DAY.get(str(tic_scope), 40)

# ka10030 당일거래량상위 — 자동매매 스크리너 기본 필터 (공식: openapi.kiwoom.com ka10030)
# mang_stk_incls: 1개 값만 지정 가능 — 0관리포함, 1관리제외, 3우선주제외, 4관리+우선주제외,
#   11정리매매제외, 14 ETF제외, 15 SPAC제외, 16 ETF+ETN제외 (조합 불가)
# → 16으로 API단 ETF/ETN 제거 + 후처리(SPAC·우선주·정리매매·API 누락 파생상품)
SCREENER_VOLUME_RANK_FILTERS = {
    "mang_stk_incls": "16",  # ETF+ETN 제외 (공식 ka10030)
    "crd_tp": "0",           # 0:전체
    "trde_qty_tp": "200",    # 200:20만주 이상
    "pric_tp": "0",          # 0:전체
    "trde_prica_tp": "0",    # 0:전체 — screener 시 대금하한≥10억이면 get_volume_rank가 100으로 상향
    "mrkt_open_tp": "0",     # 0:전체 (1장중 2장전 3장후)
    "stex_tp": "1",          # 1:KRX (3:통합)
}

# ka10027 전일대비등락률상위 — 상따 유니버스 기본 필터 (공식: openapi.kiwoom.com ka10027)
# stk_cnd: 0전체, 1관리종목제외, 3우선주제외, 4우선주+관리주제외, …
# pric_cnd: 8=1천원이상 / trde_prica_cnd: 100=10억원이상
# ETF 제외 옵션은 없어 후처리(_is_etf_family_item / _is_screener_stock)로 제거
SANGTTA_CHANGE_RATE_RANK_FILTERS = {
    "sort_tp": "1",          # 1:상승률
    "trde_qty_cnd": "0",     # 0:전체
    "stk_cnd": "1",          # 1:관리종목제외
    "crd_cnd": "0",          # 0:전체
    "updown_incls": "1",     # 1:상하한 포함 (게이트에서 상한가 진입 금지)
    "pric_cnd": "8",         # 8:1천원이상
    "trde_prica_cnd": "100", # 100:10억원이상
    "stex_tp": "1",          # 1:KRX
}
SANGTTA_UNIVERSE_MIN_CHANGE_RATE = 13.0  # 유니버스 등락률 하한(%) — 게이트 밴드와 별개

# 계좌 잔고 — KiwoomAPI 인스턴스 간 공유 캐시·동시 요청 합치기
_account_balance_cache: dict = {"at": 0.0, "data": None}
_ACCOUNT_BALANCE_FRESH_SEC = 15
_ACCOUNT_BALANCE_STALE_SEC = 300
_balance_fetch_lock: Optional[asyncio.Lock] = None
_balance_inflight: Optional[asyncio.Task] = None
# 조건식 WS(목록/검색) — 동시 연결 시 키움이 Bye/타임아웃으로 끊는 경우 방지
_condition_ws_lock: Optional[asyncio.Lock] = None
_CONDITION_SEARCH_MAX_ATTEMPTS = 2
_condition_list_cache: dict = {"at": 0.0, "data": None}
_CONDITION_LIST_TTL_SEC = 60.0
_condition_search_cache: Dict[str, dict] = {}
_CONDITION_SEARCH_TTL_SEC = 25.0
_CONDITION_SEARCH_STALE_SEC = 180.0


def _get_balance_fetch_lock() -> asyncio.Lock:
    global _balance_fetch_lock
    if _balance_fetch_lock is None:
        _balance_fetch_lock = asyncio.Lock()
    return _balance_fetch_lock


def _get_condition_ws_lock() -> asyncio.Lock:
    global _condition_ws_lock
    if _condition_ws_lock is None:
        _condition_ws_lock = asyncio.Lock()
    return _condition_ws_lock


def _copy_condition_rows(rows: Optional[List[Dict]]) -> List[Dict]:
    return [dict(r) for r in (rows or [])]


def _get_cached_condition_list() -> Optional[List[Dict]]:
    cached = _condition_list_cache.get("data")
    at = float(_condition_list_cache.get("at") or 0.0)
    if cached is None or time.monotonic() - at > _CONDITION_LIST_TTL_SEC:
        return None
    return _copy_condition_rows(cached)


def _set_cached_condition_list(rows: List[Dict]) -> None:
    _condition_list_cache["at"] = time.monotonic()
    _condition_list_cache["data"] = _copy_condition_rows(rows)


def _condition_search_cache_key(condition_id: str, condition_name: str) -> str:
    return f"{condition_id}|{condition_name}"


def _get_cached_condition_search(
    condition_id: str,
    condition_name: str,
    *,
    allow_stale: bool = False,
) -> Optional[List[Dict]]:
    key = _condition_search_cache_key(condition_id, condition_name)
    row = _condition_search_cache.get(key)
    if not row:
        return None
    age = time.monotonic() - float(row.get("at") or 0.0)
    ttl = _CONDITION_SEARCH_STALE_SEC if allow_stale else _CONDITION_SEARCH_TTL_SEC
    if age > ttl:
        return None
    return _copy_condition_rows(row.get("stocks"))


def _set_cached_condition_search(
    condition_id: str,
    condition_name: str,
    stocks: List[Dict],
) -> None:
    key = _condition_search_cache_key(condition_id, condition_name)
    _condition_search_cache[key] = {
        "at": time.monotonic(),
        "stocks": _copy_condition_rows(stocks),
    }


def _stale_account_balance(reason: str) -> Optional[Dict]:
    """최근 성공 조회 데이터가 있으면 stale로 반환."""
    cached = _account_balance_cache.get("data")
    cache_at = _account_balance_cache.get("at", 0.0)
    if not cached or time.monotonic() - cache_at > _ACCOUNT_BALANCE_STALE_SEC:
        return None
    out = dict(cached)
    out["_cached"] = True
    out["_stale"] = True
    logger.debug(f"계좌 잔고 캐시 사용 ({reason})")
    return out


def _parse_kiwoom_int(raw) -> int:
    """키움 금액/수량 문자열 → 정수 (+000010000000 → 10000000)."""
    if raw is None:
        return 0
    s = str(raw).strip()
    if not s:
        return 0
    sign = -1 if s.startswith("-") else 1
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return 0
    try:
        return sign * int(digits.lstrip("0") or "0")
    except (TypeError, ValueError):
        return 0


def _parse_kiwoom_rate(raw) -> str:
    """키움 수익률 문자열 — 소수점 유지."""
    if raw is None:
        return "0.00"
    s = str(raw).strip().replace(",", "")
    if not s:
        return "0.00"
    try:
        return f"{float(s):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _parse_kiwoom_float(raw) -> float:
    """키움 숫자 문자열을 실수로 변환. 부호와 소수점은 유지. 실패 시 0.0."""
    if raw is None:
        return 0.0
    s = str(raw).strip()
    if not s:
        return 0.0
    sign = -1.0 if s[0] == '-' else 1.0
    cleaned = ''.join(ch for ch in s if ch.isdigit() or ch == '.')
    if not cleaned or cleaned == '.':
        return 0.0
    try:
        return sign * float(cleaned)
    except ValueError:
        return 0.0


class KiwoomAPI:
    def __init__(self):
        self.base_url = Config.KIWOOM_BASE_URL
        self.ws_url = Config.KIWOOM_WS_URL
        self.token_manager = get_token_manager()
        self.websocket = None
        self.condition_callbacks = {}
        self.running = False
        # 재연결 관련 속성 추가
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.reconnect_delay = 5  # 초
        self.auto_reconnect = True
        self.message_task = None
        
        # 현재가 캐시 (종목코드 -> (가격, 시간)) - API 호출 최소화
        self._price_cache = {}
        self._price_cache_ttl = 30  # 30초 캐시 (API 제한 고려)
        # 차트 캐시 (종목+주기 -> (bars, timestamp))
        self._chart_cache: Dict[str, tuple] = {}
        self._chart_cache_ttl = 300  # 분봉 등: 5분
        self._daily_chart_cache_ttl = 43200  # 일봉: 12시간 (ATR은 stop_loss에서 일 1회 캐시)

    @staticmethod
    def normalize_stock_code(stock_code: str) -> str:
        """키움 종목코드 정규화 — A 접두사·거래소 접미사(_NX/_AL/_L/_K 등) 제거."""
        code = str(stock_code or "").strip().replace("A", "")
        if "_" in code:
            code = code.split("_", 1)[0]
        return code.strip()

    @staticmethod
    def minute_chart_stk_cd(stock_code: str) -> str:
        """분봉 조회용 종목코드 — KRX+NXT 통합(_AL).

        HTS 통합/NXT 차트와 MA·거래량을 맞추기 위해 분봉(ka10080)은
        기본 6자리가 아니라 `{code}_AL` 로 요청한다. 일봉·주문은 기존 6자리.
        """
        base = KiwoomAPI.normalize_stock_code(stock_code)
        if not base or len(base) != 6:
            return base
        return f"{base}_AL"

    async def _acquire_api_slot(
        self,
        api_name: str,
        max_wait: float | None = None,
        *,
        priority=None,
    ) -> bool:
        """레이트 리미터 슬롯 확보 — 간격·분당한도·LIMITED면 대기 후 재시도."""
        from utils.api_traffic_guard import (
            APIPriority,
            effective_max_wait,
            should_yield_low_priority,
        )
        pri = priority if priority is not None else APIPriority.NORMAL
        if max_wait is None:
            max_wait = effective_max_wait(pri)
        deadline = time.time() + max_wait
        while time.time() < deadline:
            # LOW는 스캔 버스트 중 양보하되, max_wait 안에서는 대기 후 재시도
            if pri == APIPriority.LOW and should_yield_low_priority():
                wait_sec = min(0.5, deadline - time.time())
                if wait_sec <= 0:
                    return False
                await asyncio.sleep(wait_sec)
                continue
            if not api_rate_limiter.is_api_available():
                wait_sec = min(
                    max(api_rate_limiter.seconds_until_available(), 0.2),
                    5.0,
                    deadline - time.time(),
                )
                if wait_sec > 0:
                    await asyncio.sleep(wait_sec)
                    continue
                # LIMITED가 방금 풀렸을 수 있음
            if api_rate_limiter.record_api_call(api_name):
                return True
            wait_sec = min(
                max(
                    api_rate_limiter.seconds_until_available()
                    or api_rate_limiter.min_call_interval,
                    0.2,
                ),
                5.0,
                deadline - time.time(),
            )
            if wait_sec > 0:
                await asyncio.sleep(wait_sec)
        return False

    @staticmethod
    def _is_kiwoom_token_invalid(error_msg: str, data: Optional[Dict] = None) -> bool:
        msg_lower = f"{error_msg or ''} {data or {}}".lower()
        return ("8005" in msg_lower) or (
            "token" in msg_lower and ("invalid" in msg_lower or "유효하지" in msg_lower)
        )

    async def _reauthenticate_async(self) -> bool:
        """토큰 무효 시 재발급 (동기 requests가 이벤트 루프를 막지 않도록 스레드 실행)."""
        self.token_manager.access_token = None
        self.token_manager.token_expiry = None
        return await asyncio.to_thread(self.authenticate)

    def authenticate(self) -> bool:
        """키움증권 API 인증"""
        try:
            return self.token_manager.authenticate()
        except Exception as e:
            logger.error(f"키움증권 API 인증 실패: {e}")
            return False
        
    async def connect(self):
        """웹소켓 연결 및 인증"""
        # 토큰이 없거나 만료된 경우 재인증 시도
        if not self.token_manager.get_valid_token():
            logger.warning("토큰이 없거나 만료됨 - 재인증 시도")
            if not self.authenticate():
                logger.error("토큰 재인증 실패 - WebSocket 연결 불가")
                return False
            
        try:
            # 실전/모의에 따른 WebSocket 호스트 및 앱키 선택
            use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
            ws_host = Config.KIWOOM_MOCK_WS_URL if use_mock else Config.KIWOOM_WS_URL
            app_key = Config.KIWOOM_MOCK_APP_KEY if use_mock else Config.KIWOOM_APP_KEY
            app_secret = Config.KIWOOM_MOCK_APP_SECRET if use_mock else Config.KIWOOM_APP_SECRET

            # 키움 API WebSocket 연결 URL 구성
            ws_url = f"{ws_host}/api/dostk/websocket"
            logger.info(f"WebSocket 연결 시도: {ws_url} (모의투자: {use_mock})")
            
            headers = {
                "Content-Type": "application/json;charset=UTF-8",
                "Authorization": f"Bearer {self.token_manager.get_valid_token()}",
                "appkey": app_key,
                "appsecret": app_secret
            }
            logger.info(f"연결 헤더 준비 완료 - 토큰 길이: {len(self.token_manager.get_valid_token() or '')}")
            
            self.websocket = await websockets.connect(
                ws_url,
                extra_headers=headers,
                ping_interval=60,  # 60초마다 ping (서버 부하 감소)
                ping_timeout=20,   # ping 응답 대기 시간 증가
                close_timeout=30,  # 연결 종료 대기 시간 증가
                max_size=2**20,    # 최대 메시지 크기 1MB
                max_queue=32       # 최대 큐 크기
            )
            logger.info("🔄 [DEBUG] self.running을 True로 설정 (connect 메서드)")
            self.running = True
            logger.info("WebSocket 연결 성공 - 메시지 핸들러 시작")
            
            # 재연결 성공 시 카운터 리셋
            if self.reconnect_attempts > 0:
                logger.info(f"🔄 재연결 성공! (시도 횟수: {self.reconnect_attempts})")
                self.reconnect_attempts = 0
            
            # 메시지 핸들러 태스크 생성
            self.message_task = asyncio.create_task(self._message_handler())
            return True
            
        except websockets.exceptions.InvalidStatusCode as e:
            logger.error(f"WebSocket 연결 실패 - HTTP 상태 코드: {e.status_code}")
            logger.error(f"응답 헤더: {e.response_headers}")
            return False
        except websockets.exceptions.InvalidURI as e:
            logger.error(f"WebSocket URL이 잘못되었습니다: {e}")
            return False
        except Exception as e:
            logger.error(f"웹소켓 연결 실패: {type(e).__name__}: {e}")
            return False
            
    async def disconnect(self):
        """웹소켓 연결 종료 (빠르고 안전하게)"""
        logger.info("🔄 [DEBUG] self.running을 False로 설정 (disconnect 메서드)")
        self.running = False
        # 자동 재연결 방지
        try:
            self.auto_reconnect = False
        except Exception:
            pass
        # 메시지 태스크 취소
        message_task = getattr(self, 'message_task', None)
        if message_task:
            message_task.cancel()
            try:
                await message_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            finally:
                self.message_task = None
        # 웹소켓 종료 (타임아웃 포함)
        if self.websocket:
            try:
                await asyncio.wait_for(self.websocket.close(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning("WebSocket close timed out; forcing cleanup")
            except Exception as e:
                logger.warning(f"WebSocket close error: {e}")
            finally:
                self.websocket = None
            
    async def _message_handler(self):
        """웹소켓 메시지 처리 - Keep-Alive 포함"""
        logger.info("🔄 [DEBUG] 메시지 핸들러 시작 - running 상태 모니터링")
        while self.running and self.websocket:
            try:
                # 타임아웃을 ping_interval보다 약간 길게 설정
                message = await asyncio.wait_for(self.websocket.recv(), timeout=90.0)
                data = json.loads(message)
                
                # 안전한 키 접근으로 수정
                message_type = data.get("type")
                if message_type == "condition":
                    condition_name = data.get("condition_name")
                    if condition_name and condition_name in self.condition_callbacks:
                        await self.condition_callbacks[condition_name](data)
                else:
                    # 예상하지 못한 메시지 타입 로깅
                    logger.debug(f"알 수 없는 메시지 타입: {message_type}, 데이터: {data}")
                    
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"🔄 [DEBUG] ConnectionClosed 예외 발생 - 코드: {e.code}, 이유: {e.reason}")
                
                # 정상 종료(1000) vs 비정상 종료 구분
                if e.code == 1000:
                    logger.info("서버에서 정상적으로 연결을 종료했습니다.")
                    # 정상 종료 시 재연결하지 않고 종료
                    logger.info("🔄 [DEBUG] self.running을 False로 설정 (정상 종료)")
                    self.running = False
                    self.websocket = None
                    break
                else:
                    logger.warning(f"비정상적인 연결 종료: 코드 {e.code}")
                
                # 비정상 종료 시에만 재연결 시도
                if self.auto_reconnect and self.reconnect_attempts < self.max_reconnect_attempts:
                    self.reconnect_attempts += 1
                    wait_time = self.reconnect_delay * self.reconnect_attempts
                    logger.info(f"🔄 재연결 시도 {self.reconnect_attempts}/{self.max_reconnect_attempts} - {wait_time}초 후")
                    
                    self.websocket = None
                    await asyncio.sleep(wait_time)
                    
                    # 재연결 시도
                    if await self.connect():
                        logger.info("🔄 재연결 성공!")
                        return  # 새로운 메시지 핸들러가 시작됨
                    else:
                        logger.error(f"🔄 재연결 실패 ({self.reconnect_attempts}/{self.max_reconnect_attempts})")
                        continue  # 다시 시도
                else:
                    logger.error("🔄 최대 재연결 시도 횟수 초과 또는 자동 재연결 비활성화")
                    logger.info("🔄 [DEBUG] self.running을 False로 설정 (ConnectionClosed)")
                    logger.info("웹소켓 연결이 정상적으로 종료되었습니다.")
                    self.running = False
                    self.websocket = None
                    break
            except json.JSONDecodeError as e:
                logger.error(f"JSON 파싱 오류: {e}, 원본 메시지: {message}")
                await asyncio.sleep(1)
            except KeyError as e:
                logger.error(f"필수 키 누락: {e}, 데이터: {data}")
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"웹소켓 메시지 처리 중 예상치 못한 오류: {e}")
                await asyncio.sleep(1)
        
        logger.info("🔄 [DEBUG] 메시지 핸들러 종료")
        
    
    async def graceful_shutdown(self):
        """우아한 종료"""
        logger.info("WebSocket 우아한 종료 시작")
        self.auto_reconnect = False  # 자동 재연결 비활성화
        self.running = False
        
        if self.websocket:
            try:
                await self.websocket.close(code=1000, reason="Client shutdown")
                logger.info("WebSocket 정상 종료 완료")
            except Exception as e:
                logger.warning(f"WebSocket 종료 중 오류: {e}")
            finally:
                self.websocket = None

    
    async def _suspend_main_websocket(self) -> dict:
        """조건식 전용 WS를 위해 메인 실시간 WS를 잠시 중지.

        키움은 동일 토큰으로 /api/dostk/websocket 동시 연결 시 한쪽을 Bye로 끊는 경우가 많음.
        """
        state = {
            "auto_reconnect": bool(getattr(self, "auto_reconnect", True)),
            "had_ws": bool(self.websocket) or bool(self.running),
        }
        self.auto_reconnect = False
        self.running = False
        message_task = getattr(self, "message_task", None)
        if message_task:
            message_task.cancel()
            try:
                await message_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self.message_task = None
        if self.websocket:
            try:
                await asyncio.wait_for(self.websocket.close(), timeout=2.0)
            except Exception:
                pass
            self.websocket = None
        return state

    async def _resume_main_websocket(self, state: Optional[dict]) -> None:
        """조건식 조회 후 메인 WS 복구."""
        if not state:
            return
        self.auto_reconnect = bool(state.get("auto_reconnect", True))
        if not state.get("had_ws"):
            return
        try:
            ok = await self.connect()
            if not ok:
                logger.warning("조건식 조회 후 메인 WebSocket 재연결 실패")
        except Exception as e:
            logger.warning(f"조건식 조회 후 메인 WebSocket 재연결 오류: {e}")

    async def get_condition_list_websocket(self) -> List[Dict]:
        """조건식 목록 조회 (WebSocket) - 키움증권 API 방식"""
        logger.debug("get_condition_list_websocket 시작")
        cached = _get_cached_condition_list()
        if cached is not None:
            logger.debug(f"조건식 목록 캐시 사용 ({len(cached)}개)")
            return cached
        async with _get_condition_ws_lock():
            cached = _get_cached_condition_list()
            if cached is not None:
                return cached
            rows = await self._get_condition_list_websocket_unlocked()
            if rows:
                _set_cached_condition_list(rows)
            return rows

    async def _get_condition_list_websocket_unlocked(self) -> List[Dict]:
        prev = await self._suspend_main_websocket()
        try:
            return await self._get_condition_list_websocket_body()
        finally:
            await self._resume_main_websocket(prev)

    async def _get_condition_list_websocket_body(self) -> List[Dict]:
        # 새로운 WebSocket 연결 생성 (기존 연결과 충돌 방지)
        try:
            # 실전/모의에 따른 WebSocket 호스트 및 앱키 선택
            use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
            ws_host = Config.KIWOOM_MOCK_WS_URL if use_mock else Config.KIWOOM_WS_URL
            app_key = Config.KIWOOM_MOCK_APP_KEY if use_mock else Config.KIWOOM_APP_KEY
            app_secret = Config.KIWOOM_MOCK_APP_SECRET if use_mock else Config.KIWOOM_APP_SECRET

            ws_url = f"{ws_host}/api/dostk/websocket"
            
            websocket = await websockets.connect(
                ws_url,
                extra_headers={
                    "Content-Type": "application/json;charset=UTF-8",
                    "Authorization": f"Bearer {self.token_manager.get_valid_token()}",
                    "appkey": app_key,
                    "appsecret": app_secret
                }
            )
            
            # 먼저 로그인 인증 메시지 전송
            auth_param = {
                'trnm': 'LOGIN',
                'token': self.token_manager.get_valid_token()
            }
            
            auth_json = json.dumps(auth_param)
            logger.info(f"LOGIN 패킷 전송: {auth_json}")
            await websocket.send(auth_json)
            
            # 로그인 응답 대기
            auth_response = await asyncio.wait_for(websocket.recv(), timeout=10.0)
            logger.info(f"LOGIN 응답 수신: {auth_response}")
            
            # LOGIN 응답 파싱 및 토큰 오류 확인
            try:
                auth_data = json.loads(auth_response)
                if auth_data.get("return_code") == 805004:  # 토큰 인증 실패
                    logger.error("토큰 인증 실패 - 재인증 시도")
                    # 기존 토큰 무효화
                    self.token_manager.access_token = None
                    self.token_manager.token_expiry = None
                    
                    # 재인증 시도
                    if self.authenticate():
                        logger.info("토큰 재인증 성공 - WebSocket 재연결 필요")
                        await websocket.close()
                        return None  # 재연결 필요
                    else:
                        logger.error("토큰 재인증 실패")
                        await websocket.close()
                        return None
            except json.JSONDecodeError:
                logger.warning("LOGIN 응답 파싱 실패 - JSON 형식이 아님")
            
            # 조건식 목록 조회 요청 패킷 (키움증권 API 방식)
            param = {
                'trnm': 'CNSRLST',
                'token': self.token_manager.get_valid_token()
            }
            
            logger.debug(f"CNSRLST 패킷 전송: {param}")
            await websocket.send(json.dumps(param))
            logger.info("CNSRLST 패킷 전송")
            
            # OnReceiveConditionVer 응답 대기 (타임아웃 10초)
            logger.debug("응답 대기 중...")
            response = await asyncio.wait_for(websocket.recv(), timeout=10.0)
            logger.debug(f"응답 수신: {response}")
            data = json.loads(response)
            
            if data.get("trnm") == "CNSRLST":
                if data.get("return_code") == 0:
                    # 조건식 목록 파싱 (배열 형태: [['0', '조건식명'], ['1', '조건식명'], ...])
                    condition_data = data.get("data", [])
                    conditions = []
                    
                    if condition_data:
                        logger.info(f"키움 API 원본 조건식 데이터: {condition_data}")
                        logger.info(f"원본 데이터 개수: {len(condition_data)}")
                        for i, item in enumerate(condition_data):
                            logger.info(f"원본 아이템 {i}: {item} (타입: {type(item)}, 길이: {len(item) if isinstance(item, list) else 'N/A'})")
                            if isinstance(item, list) and len(item) == 2:
                                conditions.append({
                                    "condition_id": item[0],
                                    "condition_name": item[1]
                                })
                                logger.info(f"조건식 추가: ID={item[0]}, 이름={item[1]}")
                            else:
                                logger.warning(f"조건식 파싱 실패: {item}")
                    
                    logger.info(f"조건식 목록 조회 성공: {len(conditions)}개")
                    logger.info(f"반환할 조건식 목록:")
                    for i, cond in enumerate(conditions):
                        logger.info(f"  {i+1}. {cond.get('condition_name')} (API ID: {cond.get('condition_id')})")
                    return conditions
                else:
                    logger.error(f"조건식 목록 응답 오류: {data}")
                    return []
            else:
                logger.error(f"CNSRLST 실패: {data}")
                return []
                
        except asyncio.TimeoutError:
            logger.error("조건식 목록 조회 타임아웃")
            return []
        except Exception as e:
            logger.error(f"WebSocket 조건식 목록 조회 중 오류: {e}")
            return []
        finally:
            # WebSocket 연결 정리
            if 'websocket' in locals():
                await websocket.close()
    
    def _condition_ws_headers(self) -> Dict[str, str]:
        use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
        app_key = Config.KIWOOM_MOCK_APP_KEY if use_mock else Config.KIWOOM_APP_KEY
        app_secret = Config.KIWOOM_MOCK_APP_SECRET if use_mock else Config.KIWOOM_APP_SECRET
        return {
            "Content-Type": "application/json;charset=UTF-8",
            "Authorization": f"Bearer {self.token_manager.get_valid_token()}",
            "appkey": app_key,
            "appsecret": app_secret,
        }

    def _condition_ws_url(self) -> str:
        use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
        ws_host = Config.KIWOOM_MOCK_WS_URL if use_mock else Config.KIWOOM_WS_URL
        return f"{ws_host}/api/dostk/websocket"

    async def _open_condition_websocket(self):
        """조건식 검색용 WebSocket — LOGIN + CNSRLST까지 완료.

        키움은 LOGIN만 하고 CNSRREQ를내면 응답이 오지 않는 경우가 있음.
        동일 연결에서 CNSRLST를 한 번 받은 뒤 검색해야 안정적이다.
        """
        websocket = await websockets.connect(
            self._condition_ws_url(),
            extra_headers=self._condition_ws_headers(),
        )
        auth_param = {"trnm": "LOGIN", "token": self.token_manager.get_valid_token()}
        await websocket.send(json.dumps(auth_param))
        login_ok = False
        for _ in range(8):
            response = await asyncio.wait_for(websocket.recv(), timeout=12.0)
            try:
                data = json.loads(response)
            except json.JSONDecodeError:
                continue
            trnm = data.get("trnm")
            if trnm == "PING":
                await websocket.send(response)
                continue
            if trnm == "SYSTEM":
                continue
            if trnm == "LOGIN":
                if data.get("return_code") not in (0, None):
                    await websocket.close()
                    raise RuntimeError(f"조건식 WS LOGIN 실패: {data}")
                login_ok = True
                break
            logger.debug(f"조건식 WS LOGIN 대기 중 기타 응답: {trnm}")
        if not login_ok:
            await websocket.close()
            raise TimeoutError("조건식 WS LOGIN 미수신")

        await websocket.send(json.dumps({
            "trnm": "CNSRLST",
            "token": self.token_manager.get_valid_token(),
        }))
        list_ok = False
        for _ in range(8):
            response = await asyncio.wait_for(websocket.recv(), timeout=12.0)
            try:
                data = json.loads(response)
            except json.JSONDecodeError:
                continue
            trnm = data.get("trnm")
            if trnm == "PING":
                await websocket.send(response)
                continue
            if trnm in ("SYSTEM", "LOGIN"):
                continue
            if trnm == "CNSRLST":
                if data.get("return_code") not in (0, None):
                    await websocket.close()
                    raise RuntimeError(f"조건식 WS CNSRLST 실패: {data}")
                list_ok = True
                break
            logger.debug(f"조건식 WS CNSRLST 대기 중 기타 응답: {trnm}")
        if not list_ok:
            await websocket.close()
            raise TimeoutError("조건식 WS CNSRLST 미수신")

        await asyncio.sleep(0.15)
        return websocket

    async def _recv_cnsrreq_on_ws(self, websocket) -> Optional[Dict]:
        for attempt in range(6):
            response = await asyncio.wait_for(websocket.recv(), timeout=10.0)
            try:
                data = json.loads(response)
            except json.JSONDecodeError:
                logger.warning(f"JSON 파싱 실패 (시도 {attempt + 1}): {str(response)[:100]}...")
                continue
            trnm = data.get("trnm")
            if trnm == "PING":
                await websocket.send(response)
                continue
            # 키움이 LOGIN 직후/검색 중 SYSTEM 알림을 끼워 넣는 경우가 있음
            if trnm in ("SYSTEM", "LOGIN"):
                logger.debug(f"조건식 WS 무시 응답: {trnm}")
                continue
            if trnm == "CNSRREQ":
                return data
            logger.warning(f"예상치 못한 응답 (시도 {attempt + 1}): {trnm or 'UNKNOWN'}")
        return None

    def _parse_cnsrreq_stocks(self, data: Dict, condition_name: str) -> List[Dict]:
        stocks: List[Dict] = []
        for item in data.get("data") or []:
            if not isinstance(item, dict):
                continue
            stock_code = str(item.get("9001", "")).replace("A", "")
            stock_name = item.get("302", "")
            current_price_int = abs(_parse_kiwoom_int(item.get("10", "0")))
            price_diff_int = _parse_kiwoom_int(item.get("11", "0"))
            volume_int = abs(_parse_kiwoom_int(item.get("13", "0")))
            prev_close_int = current_price_int - price_diff_int
            if prev_close_int > 0:
                change_rate_float = round(price_diff_int / prev_close_int * 100, 2)
            else:
                change_rate_float = _parse_kiwoom_float(item.get("12", "0"))
            stocks.append({
                "stock_code": stock_code,
                "stock_name": stock_name,
                "current_price": str(current_price_int),
                "price_diff": str(price_diff_int),
                "prev_close": str(prev_close_int),
                "change_rate": str(round(change_rate_float, 2)),
                "volume": str(volume_int),
            })
        logger.info(f"조건식 검색 성공: {condition_name}, 종목 수: {len(stocks)}개")
        return stocks

    async def _search_condition_on_websocket(
        self,
        websocket,
        condition_id: str,
        condition_name: str,
    ) -> List[Dict]:
        search_param = {
            "trnm": "CNSRREQ",
            "seq": condition_id,
            "search_type": "0",
            "stex_tp": "K",
            "cont_yn": "N",
            "next_key": "",
        }
        await websocket.send(json.dumps(search_param))
        logger.debug(f"CNSRREQ 전송: {condition_name} (ID={condition_id})")
        data = await self._recv_cnsrreq_on_ws(websocket)
        if not data:
            raise TimeoutError(f"조건식 CNSRREQ 미수신: {condition_name}")
        if data.get("return_code") not in (0, None):
            raise RuntimeError(f"조건식 검색 실패: {condition_name} — {data}")
        return self._parse_cnsrreq_stocks(data, condition_name)

    def condition_search_session(self) -> "ConditionSearchSession":
        return ConditionSearchSession(self)

    async def search_condition_stocks(self, condition_id: str, condition_name: str) -> List[Dict]:
        """조건식으로 종목 검색 (WebSocket) — 단일 호출."""
        logger.debug(f"조건식 검색 시작: {condition_name} (ID: {condition_id})")
        try:
            async with self.condition_search_session() as session:
                return await session.search(condition_id, condition_name)
        except asyncio.TimeoutError:
            logger.error("조건식 검색 타임아웃")
            return []
        except Exception as e:
            logger.error(f"조건식 검색 중 오류: {e}")
            return []


    async def get_stock_chart_data(
        self, stock_code: str, period: str = "1D", max_bars: Optional[int] = None,
        *, allow_off_hours: bool = False,
    ):
        """종목 차트 데이터 조회 - 실제 키움 API 사용.

        max_bars: 파싱 후 최근 N봉만 유지 (ATR 등 — API는 전체 일봉을 주므로 슬라이스).
        allow_off_hours: True면 일봉 조회 시 장외에도 API 호출 (검증·ATR 표시용).
        """
        import time
        from utils.market_hours import is_krx_session

        stock_code = self.normalize_stock_code(stock_code)
        normalized = (period or "1D").strip().upper()
        is_daily = normalized in {"1D", "D", "DAY", "DAILY"}
        is_minute_chart = normalized in {
            "5M", "5MIN", "M5", "5MINUTE", "5",
            "1M", "M1", "1MIN", "3M", "M3", "3MIN",
            "10M", "M10", "10MIN", "15M", "M15",
            "30M", "M30", "60M", "M60", "60MIN", "1H",
        }
        # 분봉은 통합(_AL) 시세 — 캐시 키에 venue 구분
        cache_key = (
            f"{stock_code}:AL:{normalized}" if is_minute_chart else f"{stock_code}:{normalized}"
        )
        ttl = self._daily_chart_cache_ttl if is_daily else self._chart_cache_ttl
        cached = self._chart_cache.get(cache_key)
        if cached:
            data, ts = cached
            if time.time() - ts < ttl:
                logger.debug(f"💾 [CHART_CACHE_HIT] {cache_key}")
                return data[-max_bars:] if max_bars and data else data

        # 분봉도 검증·시뮬용 allow_off_hours 허용
        if not is_krx_session() and not (allow_off_hours and (is_daily or is_minute_chart)):
            if cached:
                logger.debug(f"장외 시간 — 차트 캐시 사용: {cache_key}")
                data = cached[0]
                return data[-max_bars:] if max_bars and data else data
            logger.debug(f"장외 시간 — 차트 조회 생략: {cache_key}")
            return []

        try:
            logger.info(f"차트 데이터 조회 시작: {stock_code}, 기간: {period}")
            
            if not self.token_manager.get_valid_token():
                logger.error("키움 API 토큰이 없습니다")
                return cached[0] if cached else []
            
            # LIMITED여도 allow_off_hours(검증·시뮬)는 즉시 포기하지 않고 슬롯 대기로 넘긴다.
            if not api_rate_limiter.is_api_available() and not allow_off_hours:
                logger.warning("차트 조회 건너뜀 - API 제한 상태")
                return cached[0] if cached else []
            
            use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
            host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
            endpoint = '/api/dostk/chart'
            url = host + endpoint
            
            if is_minute_chart:
                api_id = 'ka10080'
                if normalized in {"5M", "5MIN", "M5", "5MINUTE", "5"}:
                    tic_scope = "5"
                elif normalized in {"1M", "M1", "1MIN"}:
                    tic_scope = "1"
                elif normalized in {"3M", "M3", "3MIN"}:
                    tic_scope = "3"
                elif normalized in {"10M", "M10", "10MIN"}:
                    tic_scope = "10"
                elif normalized in {"15M", "M15", "15MIN"}:
                    tic_scope = "15"
                elif normalized in {"30M", "M30", "30MIN"}:
                    tic_scope = "30"
                elif normalized in {"60M", "M60", "60MIN", "1H"}:
                    tic_scope = "60"
                else:
                    tic_scope = "5"
                request_data = {
                    "stk_cd": self.minute_chart_stk_cd(stock_code),
                    "tic_scope": tic_scope,
                    "upd_stkpc_tp": "1",
                }
            else:
                api_id = 'ka10081'
                today = now_kst()
                if today.weekday() == 5:
                    base_dt = (today - timedelta(days=1)).strftime('%Y%m%d')
                elif today.weekday() == 6:
                    base_dt = (today - timedelta(days=2)).strftime('%Y%m%d')
                else:
                    base_dt = today.strftime('%Y%m%d')
                request_data = {
                    "dmst_stex_tp": "KRX",
                    "stk_cd": stock_code,
                    "base_dt": base_dt,
                    "upd_stkpc_tp": "1",
                }
            
            headers = {
                'Content-Type': 'application/json;charset=UTF-8',
                'authorization': f'Bearer {self.token_manager.get_valid_token()}',
                'cont-yn': 'N',
                'next-key': '',
                'api-id': api_id,
            }

            max_attempts = 3
            slot_wait = 45.0 if allow_off_hours else 6.0
            for attempt in range(max_attempts):
                if not await self._acquire_api_slot("chart_data", max_wait=slot_wait, priority=APIPriority.HIGH):
                    logger.warning("차트 조회 슬롯 확보 실패")
                    if attempt < max_attempts - 1:
                        # 분당 한도/간격 — 윈도우가 밀릴 때까지 대기
                        wait_more = max(
                            2.0,
                            min(api_rate_limiter.seconds_until_available() or 3.0, 20.0),
                        )
                        await asyncio.sleep(wait_more)
                        continue
                    return cached[0] if cached else []

                async with aiohttp.ClientSession() as session:
                    async with session.post(url, headers=headers, json=request_data) as response:
                        if response.status == 200:
                            data = await response.json()
                            if data.get('return_code') == 0:
                                parsed = self._parse_kiwoom_chart_data(data, stock_code)
                                if parsed and max_bars and len(parsed) > max_bars:
                                    parsed = parsed[-max_bars:]
                                if parsed:
                                    self._chart_cache[cache_key] = (parsed, time.time())
                                return parsed
                            msg = (data.get('return_msg') or "").lower()
                            if any(k in msg for k in ["rate limit", "too many", "요청 한도", "429", "제한", "초과"]):
                                api_rate_limiter.handle_api_error(Exception(data.get('return_msg', 'rate limit')))
                                backoff = (2 ** attempt) + random.uniform(0, 0.5)
                                logger.warning(f"차트 조회 제한 — {backoff:.2f}s 후 재시도 {attempt+1}/{max_attempts}")
                                await asyncio.sleep(backoff)
                                continue
                            if attempt == 0 and self._is_kiwoom_token_invalid(data.get('return_msg', ''), data):
                                logger.warning("🔑 [TOKEN] 차트 조회 토큰 무효 — 재인증 후 재시도")
                                if await self._reauthenticate_async():
                                    headers['authorization'] = f'Bearer {self.token_manager.get_valid_token()}'
                                    continue
                            logger.error(f"키움 API 오류: {data.get('return_msg')}")
                            return cached[0] if cached else []
                        elif response.status == 429:
                            api_rate_limiter.handle_api_error(Exception("429 Too Many Requests"))
                            backoff = (2 ** attempt) + random.uniform(0, 0.5)
                            await asyncio.sleep(backoff)
                            continue
                        else:
                            logger.error(f"키움 API 호출 실패: {response.status}")
                            return cached[0] if cached else []
            return cached[0] if cached else []
                        
        except Exception as e:
            logger.error(f"실제 차트 데이터 조회 중 오류: {e}")
            return cached[0] if cached else []

    async def get_intraday_chart_for_date(
        self,
        stock_code: str,
        trade_date: str,
        tic_scope: str = "15",
        max_pages: int = 1,
        *,
        priority=APIPriority.NORMAL,
    ) -> Dict:
        """특정 거래일(KST) 분봉 OHLCV 조회 (ka10080, 검증 페이지용).

        장외 시간에도 조회 가능. trade_date: YYYY-MM-DD.
        키움 API는 페이지당 최대 약 900봉을 반환할 수 있으나, 해당 일자만 필터해 사용한다.
        15분봉 1일치(≈27봉)면 1페이지로 충분하다.
        """
        stock_code = self.normalize_stock_code(stock_code)
        date_str = (trade_date or "").strip()[:10]
        try:
            target = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return {"success": False, "error": "날짜 형식이 올바르지 않습니다 (YYYY-MM-DD)", "bars": []}

        yyyymmdd = target.strftime("%Y%m%d")
        # 분봉 검증/시뮬은 통합(_AL) 시세
        cache_key = f"{stock_code}:AL:M{tic_scope}:{yyyymmdd}"
        today_kst = kst_today().strftime("%Y%m%d")
        ttl = self._chart_cache_ttl if yyyymmdd == today_kst else 86400

        cached = self._chart_cache.get(cache_key)
        if cached:
            data, ts = cached
            if time.time() - ts < ttl:
                logger.debug(f"💾 [VERIFY_CHART_CACHE_HIT] {cache_key}")
                return {"success": True, "bars": data, "cached": True}

        if not self.token_manager.get_valid_token():
            if cached:
                return {"success": True, "bars": cached[0], "cached": True, "warning": "토큰 없음 — 캐시 사용"}
            return {"success": False, "error": "키움 API 토큰이 없습니다", "bars": []}

        if not api_rate_limiter.is_api_available():
            # 검증용 분봉은 LIMITED에서도 슬롯 대기로 진행 (즉시 포기하지 않음)
            pass

        use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
        host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
        url = host + "/api/dostk/chart"
        headers_base = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.token_manager.get_valid_token()}",
            "api-id": "ka10080",
        }
        body = {
            "stk_cd": self.minute_chart_stk_cd(stock_code),
            "tic_scope": str(tic_scope),
            "upd_stkpc_tp": "1",
            "date": yyyymmdd,
        }

        all_bars: List[Dict] = []
        cont_yn, next_key = "N", ""
        pages = 0
        last_error = ""
        day_prefix = date_str
        need_bars = _expected_intraday_bars_for_day(tic_scope)

        try:
            timeout = aiohttp.ClientTimeout(total=45)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                while pages < max_pages:
                    pages += 1
                    if not await self._acquire_api_slot("verify_intraday_chart", max_wait=45.0, priority=priority):
                        last_error = "API 슬롯 확보 실패"
                        break

                    headers = {**headers_base, "cont-yn": cont_yn, "next-key": next_key}
                    async with session.post(url, headers=headers, json=body) as resp:
                        text = await resp.text()
                        resp_cont = resp.headers.get("cont-yn") or resp.headers.get("Cont-Yn") or "N"
                        resp_next = resp.headers.get("next-key") or resp.headers.get("Next-Key") or ""

                        if resp.status == 429:
                            api_rate_limiter.handle_api_error(Exception("429 Too Many Requests"))
                            last_error = "API 호출 제한 (429)"
                            break

                        if resp.status != 200:
                            last_error = f"HTTP {resp.status}"
                            break

                        data = json.loads(text)

                    ok = (data.get("return_code") == 0) or (data.get("returnCode") == 0) or (data.get("rt_cd") == "0")
                    if not ok:
                        last_error = data.get("return_msg") or data.get("returnMsg") or "조회 실패"
                        msg_lower = str(last_error).lower()
                        if any(k in msg_lower for k in ["rate limit", "too many", "요청 한도", "429", "제한", "초과"]):
                            api_rate_limiter.handle_api_error(Exception(last_error))
                        break

                    parsed = self._parse_kiwoom_chart_data(
                        data, stock_code, trade_yyyymmdd=yyyymmdd, verbose=False,
                    )
                    all_bars.extend(parsed)

                    if len(all_bars) >= need_bars:
                        break

                    cont_yn = data.get("cont_yn") or resp_cont or "N"
                    next_key = data.get("next_key") or data.get("next-key") or resp_next or ""
                    if str(cont_yn).upper() != "Y" or not next_key:
                        break

            # 해당 거래일 봉만 유지 (파싱 단계에서 1차 필터, 여기서 재확인)
            bars = [b for b in all_bars if str(b.get("timestamp", "")).startswith(day_prefix)]
            bars.sort(key=lambda x: x["timestamp"])

            if bars:
                self._chart_cache[cache_key] = (bars, time.time())
                return {"success": True, "bars": bars, "pages": pages, "bar_count": len(bars)}

            if cached:
                return {"success": True, "bars": cached[0], "cached": True, "warning": "당일 데이터 없음 — 캐시 사용"}

            hint = last_error or "해당 일자 분봉 데이터가 없습니다"
            return {"success": False, "error": hint, "bars": []}

        except Exception as e:
            logger.error(f"검증용 분봉 조회 오류: {stock_code} {date_str} — {e}")
            if cached:
                return {"success": True, "bars": cached[0], "cached": True, "warning": str(e)}
            return {"success": False, "error": str(e), "bars": []}
    
    async def get_current_price(self, stock_code: str, *, priority=None) -> Optional[int]:
        """종목 현재가 조회 (캐싱 적용)"""
        try:
            from utils.market_hours import is_krx_session

            stock_code = self.normalize_stock_code(stock_code)
            if stock_code in self._price_cache:
                price, timestamp = self._price_cache[stock_code]
                age = datetime.now().timestamp() - timestamp
                if age < self._price_cache_ttl:
                    logger.debug(f"💾 [CACHE_HIT] {stock_code} 캐시 사용 (나이: {age:.1f}초)")
                    return price

            if not is_krx_session():
                if stock_code in self._price_cache:
                    price, _ = self._price_cache[stock_code]
                    logger.debug(f"장외 시간 — 현재가 캐시 사용: {stock_code}")
                    return price
                logger.debug(f"장외 시간 — 현재가 조회 생략: {stock_code}")
                return None
            
            logger.debug(f"현재가 조회 시작: {stock_code}")
            
            if not self.token_manager.get_valid_token():
                logger.error("키움 API 토큰이 없습니다")
                return None

            if not await self._acquire_api_slot(
                f"get_current_price_{stock_code}", priority=priority,
            ):
                if stock_code in self._price_cache:
                    price, _ = self._price_cache[stock_code]
                    logger.debug(f"API 슬롯 대기 초과 — 캐시 사용: {stock_code}")
                    return price
                return None
            
            # 키움 API 호출 설정 - 실전/모의 분기
            use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
            host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
            endpoint = '/api/dostk/chart'
            url = host + endpoint
            
            # 요청 헤더
            headers = {
                'Content-Type': 'application/json;charset=UTF-8',
                'authorization': f'Bearer {self.token_manager.get_valid_token()}',
                'cont-yn': 'N',
                'next-key': '',
                'api-id': 'ka10081',  # 일봉 차트 API 사용
            }
            
            # 요청 데이터 (최근 1일 데이터만 조회)
            # 최근 거래일 계산 (주말 제외)
            today = now_kst()
            if today.weekday() == 5:  # 토요일
                base_dt = (today - timedelta(days=1)).strftime('%Y%m%d')
            elif today.weekday() == 6:  # 일요일
                base_dt = (today - timedelta(days=2)).strftime('%Y%m%d')
            else:  # 평일
                base_dt = today.strftime('%Y%m%d')
            
            request_data = {
                'dmst_stex_tp': 'KRX',
                'stk_cd': stock_code,
                'period': '1D',
                'limit': 1,
                'base_dt': base_dt,  # 기준일자 추가
                'upd_stkpc_tp': '1'  # 주가 업데이트 타입 추가
            }
            
            # SSL 검증 완화 및 타임아웃 설정 (모의투자 서버 연결 문제 해결)
            timeout = aiohttp.ClientTimeout(total=60, connect=20, sock_read=30)
            # SSL 컨텍스트 생성 - 인증서 검증 비활성화
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                async with session.post(url, headers=headers, json=request_data) as response:
                    if response.status == 200:
                        try:
                            response_data = await response.json()
                            
                            # 응답 데이터 파싱 및 디버깅
                            rt_cd = response_data.get("rt_cd")
                            return_msg = response_data.get("return_msg", "")
                            
                            # 디버깅: 전체 응답 구조 로깅
                            logger.debug(f"현재가 조회 응답 - {stock_code}: rt_cd={rt_cd}, return_msg={return_msg}")
                            logger.debug(f"응답 데이터 키: {list(response_data.keys())}")
                            
                            # 성공 조건 확인 (rt_cd가 "0"이거나 "정상적으로 처리되었습니다" 메시지)
                            is_success = (rt_cd == "0" or rt_cd == "1" or "정상적으로 처리되었습니다" in return_msg)
                            
                            if is_success:
                                # 다양한 가능한 필드명 시도
                                chart_list = None
                                possible_fields = [
                                    'stk_dt_pole_chart_qry',
                                    'output',
                                    'data',
                                    'chart_data',
                                    'stock_data'
                                ]
                                
                                for field in possible_fields:
                                    if field in response_data and response_data[field]:
                                        chart_list = response_data[field]
                                        logger.debug(f"데이터 필드 발견: {field}")
                                        break
                                
                                if chart_list and len(chart_list) > 0:
                                    # 다양한 가격 필드명 시도
                                    price_fields = ['cur_prc', 'close_price', 'price', 'current_price', 'close', 'last_price']
                                    current_price = None
                                    
                                    for price_field in price_fields:
                                        if price_field in chart_list[0]:
                                            current_price = int(chart_list[0].get(price_field, 0))
                                            logger.debug(f"가격 필드 발견: {price_field} = {current_price}")
                                            break
                                    
                                    if current_price and current_price > 0:
                                        # 캐시에 저장
                                        self._price_cache[stock_code] = (current_price, datetime.now().timestamp())
                                        logger.info(f"💾 현재가 조회 성공 (캐시 저장): {stock_code} = {current_price:,}원")
                                        return current_price
                                    else:
                                        logger.warning(f"유효한 가격 데이터 없음: {stock_code}")
                                        logger.debug(f"차트 데이터: {chart_list[0]}")
                                        return None
                                else:
                                    logger.warning(f"차트 데이터 없음: {stock_code}")
                                    logger.debug(f"전체 응답: {response_data}")
                                    return None
                            else:
                                logger.error(f"현재가 조회 실패: {stock_code} - rt_cd={rt_cd}, return_msg={return_msg}")
                                return None
                                
                        except json.JSONDecodeError as e:
                            logger.error(f"현재가 조회 응답 파싱 실패: {e}")
                            return None
                    else:
                        # 429 에러는 Rate Limit 초과를 의미
                        if response.status == 429:
                            logger.error(f"❌ [429 ERROR] API 호출 제한 초과!")
                            logger.error(f"   - 종목코드: {stock_code}")
                            logger.error(f"   - API URL: {url}")
                            logger.error(f"   - 응답 헤더: {dict(response.headers)}")
                            
                            # 응답 본문 확인
                            try:
                                error_body = await response.text()
                                logger.error(f"   - 응답 본문: {error_body}")
                            except:
                                pass
                            
                            logger.error(f"   ⚠️  해결방법:")
                            logger.error(f"      1. API 호출 간격을 더 늘리세요 (현재: {api_rate_limiter.min_call_interval}초)")
                            logger.error(f"      2. 동시에 여러 종목 조회를 줄이세요")
                            logger.error(f"      3. 키움 API 제한 정책 확인: 1초당 1회, 1분당 20회")
                        else:
                            logger.error(f"현재가 조회 API 호출 실패: HTTP {response.status}")
                            try:
                                error_body = await response.text()
                                logger.error(f"   - 오류 내용: {error_body}")
                            except:
                                pass
                        
                        return None
                        
        except Exception as e:
            logger.error(f"현재가 조회 중 오류: {e}")
            return None

    async def _request_stockinfo_tr(self, api_id: str, request_data: Dict) -> Dict:
        """국내주식 시세/호가 TR 호출 공용 함수 (환경별 URI 차이를 자동 재시도)."""
        if not self.token_manager.get_valid_token():
            return {"success": False, "error": "토큰 없음"}

        try:
            use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
            host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
            # 키움 REST 게이트웨이는 계정/버전에 따라 API ID별 허용 URI가 다를 수 있다.
            # 1504(해당 URI에서 지원하지 않는 API ID) 발생 시 다음 URI로 재시도한다.
            endpoint_candidates = ["/api/dostk/stkinfo", "/api/dostk/chart", "/api/dostk/iteminfo"]
            if api_id == "ka10004":
                # 주식호가 — 공식 URI는 /api/dostk/mrkcond (hoga/stkinfo 등은 1504)
                endpoint_candidates = [
                    "/api/dostk/mrkcond",
                    "/api/dostk/hoga",
                    "/api/dostk/stkinfo",
                ]
            elif api_id in ("ka10008", "ka10009", "ka10131"):
                endpoint_candidates = ["/api/dostk/frgnistt", "/api/dostk/stkinfo", "/api/dostk/mrkcond"]
            elif api_id in ("ka10045", "ka10063", "ka10066"):
                endpoint_candidates = ["/api/dostk/mrkcond", "/api/dostk/stkinfo"]
            elif api_id in ("ka90005", "ka90006", "ka90007", "ka90008", "ka90010", "ka90013"):
                endpoint_candidates = ["/api/dostk/mrkcond", "/api/dostk/stkinfo"]
            elif api_id in ("ka90003", "ka90004"):
                endpoint_candidates = ["/api/dostk/stkinfo", "/api/dostk/mrkcond"]
            elif api_id in ("ka90001", "ka90002"):
                endpoint_candidates = ["/api/dostk/thme"]
            elif api_id == "ka10006":
                # 주식시분요청도 시세(mrkcond) 계열
                endpoint_candidates = [
                    "/api/dostk/mrkcond",
                    "/api/dostk/stkinfo",
                    "/api/dostk/chart",
                    "/api/dostk/iteminfo",
                ]

            headers = {
                "Content-Type": "application/json;charset=UTF-8",
                "authorization": f"Bearer {self.token_manager.get_valid_token()}",
                "cont-yn": "N",
                "next-key": "",
                "api-id": api_id,
            }

            logger.info(f"[TR_REQUEST] api_id={api_id}, stock={request_data.get('stk_cd','')}, endpoint_candidates={endpoint_candidates}")
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                last_error = ""
                for endpoint in endpoint_candidates:
                    url = host + endpoint
                    logger.info(f"[TR_ATTEMPT] api_id={api_id}, endpoint={endpoint}, request_data={request_data}")
                    async with session.post(url, headers=headers, json=request_data) as response:
                        body_text = await response.text()
                        if response.status != 200:
                            last_error = f"HTTP {response.status} @ {endpoint}"
                            logger.warning(f"[TR_HTTP_FAIL] api_id={api_id}, endpoint={endpoint}, status={response.status}, body={body_text[:500]}")
                            continue

                        try:
                            data = json.loads(body_text)
                        except json.JSONDecodeError:
                            last_error = f"JSON 파싱 실패 @ {endpoint}"
                            logger.error(f"[TR_JSON_FAIL] api_id={api_id}, endpoint={endpoint}, body={body_text[:500]}")
                            continue

                        ok = (data.get("return_code") == 0) or (data.get("rt_cd") == "0")
                        logger.info(
                            f"[TR_RESPONSE] api_id={api_id}, endpoint={endpoint}, "
                            f"return_code={data.get('return_code')}, rt_cd={data.get('rt_cd')}, "
                            f"return_msg={data.get('return_msg') or data.get('msg1') or ''}"
                        )
                        if ok:
                            sample_keys = list(data.keys())[:20]
                            logger.info(f"[TR_SUCCESS] api_id={api_id}, endpoint={endpoint}, keys={sample_keys}")
                            return {"success": True, "data": data, "endpoint": endpoint}

                        msg = data.get("return_msg") or data.get("msg1") or "TR 호출 실패"
                        # URI/API-ID 매핑 오류(1504)면 다음 URI 재시도
                        if "1504" in str(msg) or "지원하는 API ID가 아닙니다" in str(msg):
                            last_error = f"{msg} @ {endpoint}"
                            logger.warning(f"[TR_RETRY_URI] api_id={api_id}, endpoint={endpoint}, msg={msg}")
                            continue

                        logger.error(f"[TR_FAIL] api_id={api_id}, endpoint={endpoint}, msg={msg}, body_keys={list(data.keys())[:20]}")
                        return {"success": False, "error": f"{msg} @ {endpoint}", "raw": data}

                logger.error(f"[TR_ALL_FAILED] api_id={api_id}, last_error={last_error}, request_data={request_data}")
                return {"success": False, "error": last_error or "TR 호출 실패(모든 URI 재시도 실패)"}
        except Exception as e:
            logger.exception(f"[TR_EXCEPTION] api_id={api_id}, request_data={request_data}, error={e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def _is_etf_like_name(name: str) -> bool:
        """KODEX/TIGER 등 ETF·ETN·레버리지·인버스·곱버스 계열 종목명 판별."""
        nm = name or ""
        upper = nm.upper().replace(" ", "")
        if "ETN" in upper or "ETF" in upper:
            return True
        if ("인버스" in nm) or ("레버리지" in nm) or ("곱버스" in nm):
            return True
        if "INVERSE" in upper or "LEVERAGE" in upper:
            return True
        etf_prefixes = (
            "KODEX", "TIGER", "KBSTAR", "ARIRANG", "HANARO", "SOL", "KOSEF", "KINDEX",
            "ACE", "PLUS", "RISE", "KIWOOM", "TIMEFOLIO", "히어로즈", "WOORI", "BNK", "FOCUS",
        )
        return upper.startswith(etf_prefixes)

    @staticmethod
    def classify_product_type(name: str) -> str:
        """종목명 기반 상품 종류 분류.
        반환: LEVERAGE(레버리지+2X) / INVERSE(인버스-1X) / DOUBLE_INVERSE(곱버스-2X) / ETF / ETN / STOCK
        """
        nm = name or ""
        upper = nm.upper().replace(" ", "")
        is_inverse = ("인버스" in nm) or ("INVERSE" in upper)
        is_double = ("곱버스" in nm) or ("2X" in upper) or ("2배" in nm)
        is_leverage = ("레버리지" in nm) or ("LEVERAGE" in upper)

        if is_inverse and is_double:
            return "DOUBLE_INVERSE"
        if is_inverse:
            return "INVERSE"
        if is_leverage or is_double:
            return "LEVERAGE"
        if "ETN" in upper:
            return "ETN"
        # 대표적인 ETF 브랜드 프리픽스(레버리지/인버스가 아닌 일반 ETF)
        etf_prefixes = (
            "KODEX", "TIGER", "KBSTAR", "ARIRANG", "HANARO", "SOL", "KOSEF", "KINDEX",
            "ACE", "PLUS", "RISE", "KIWOOM", "TIMEFOLIO", "히어로즈", "WOORI", "BNK", "FOCUS",
        )
        if upper.startswith(etf_prefixes):
            return "ETF"
        return "STOCK"

    @staticmethod
    def _is_etf_family_item(name: str, product_type: Optional[str] = None) -> bool:
        """ETF/ETN/레버리지/인버스/곱버스 계열 여부 (mang_stk_incls=16이 놓치는 파생상품 포함)."""
        nm = (name or "").strip()
        if not nm:
            return False
        pt = product_type if product_type is not None else KiwoomAPI.classify_product_type(nm)
        return pt != "STOCK" or KiwoomAPI._is_etf_like_name(nm)

    @staticmethod
    def _is_screener_stock(name: str, product_type: Optional[str] = None) -> bool:
        """스크리너 편입 가능한 개별 주식인지 판별."""
        nm = (name or "").strip()
        if not nm:
            return False
        if KiwoomAPI._is_etf_family_item(nm, product_type):
            return False
        if "스팩" in nm or "SPAC" in nm.upper():
            return False
        if "정리매매" in nm:
            return False
        if re.search(r"우(B|C)?$", nm) or nm.endswith("우선주"):
            return False
        return True

    @staticmethod
    def _post_filter_screener_items(items: List[Dict]) -> Tuple[List[Dict], int]:
        """스크리너 — 개별 주식만 남김 (ETF/ETN/레버리지/인버스/곱버스·SPAC·우선주·정리매매 제외).

        Returns:
            (kept_items, excluded_etf_count)
        """
        out: List[Dict] = []
        excluded_etf = 0
        for it in items:
            name = (it.get("stock_name") or "").strip()
            if not name:
                continue
            pt = it.get("product_type")
            if KiwoomAPI._is_etf_family_item(name, pt):
                excluded_etf += 1
                continue
            if not KiwoomAPI._is_screener_stock(name, pt):
                continue
            out.append(it)
        return out, excluded_etf

    @staticmethod
    def _parse_volume_rank_row(r: Dict) -> Dict:
        name = r.get("stk_nm", "")
        return {
            "stock_code": KiwoomAPI.normalize_stock_code(str(r.get("stk_cd", ""))),
            "stock_name": name,
            "current_price": abs(_parse_kiwoom_int(r.get("cur_prc", "0"))),
            "price_diff": _parse_kiwoom_int(r.get("pred_pre", "0")),
            "change_rate": _parse_kiwoom_float(r.get("flu_rt", "0")),
            "volume": abs(_parse_kiwoom_int(r.get("trde_qty", "0"))),
            "trade_amount": abs(_parse_kiwoom_int(r.get("trde_amt", "0"))),
            "product_type": KiwoomAPI.classify_product_type(name),
        }

    async def get_volume_rank(
        self,
        market: str = "000",
        sort_tp: str = "3",
        limit: int = 50,
        *,
        screener_filters: bool = True,
        positive_change_only: Optional[bool] = None,
        min_change_rate: Optional[float] = None,
        max_change_rate: Optional[float] = None,
        min_trade_amount_eok: Optional[float] = None,
        mang_stk_incls: Optional[str] = None,
        crd_tp: Optional[str] = None,
        trde_qty_tp: Optional[str] = None,
        pric_tp: Optional[str] = None,
        trde_prica_tp: Optional[str] = None,
        mrkt_open_tp: Optional[str] = None,
        stex_tp: Optional[str] = None,
    ) -> Dict:
        """당일거래량상위 조회 (ka10030, /api/dostk/rkinfo).

        market: 000 전체 / 001 코스피 / 101 코스닥
        sort_tp: 1 거래량 / 2 거래회전율 / 3 거래대금
        screener_filters: True면 KRX·20만주+·관리/우선주 제외 등 스크리너 기본값 적용
        positive_change_only: True면 등락률>0만 채움(기본: screener_filters와 동일).
            API에 등락 필터가 없어 후처리하며, limit개 채울 때까지 페이징한다.
        min_change_rate: 지정 시 등락률>=이 값만 채움. screener_filters면
            Config.SCREENER_MIN_CHANGE_RATE(기본 3.3)를 쓰고, 0이면 플러스(>0)만.
        max_change_rate: 지정 시 등락률>=이 값은 과열로 제외. screener_filters면
            Config.SCREENER_MAX_CHANGE_RATE(기본 15). 0이면 상한 미적용.
        min_trade_amount_eok: 당일 거래대금 하한(억원). screener_filters면
            Config.SCREENER_MIN_TRADE_AMOUNT_EOK(기본 20). 0이면 하한 미적용.
            trde_amt 단위는 백만원이므로 내부에서 ×100 변환한다.
        """
        if positive_change_only is None:
            positive_change_only = bool(screener_filters)
        if min_change_rate is None and screener_filters:
            try:
                min_change_rate = float(Config.SCREENER_MIN_CHANGE_RATE)
            except (TypeError, ValueError, AttributeError):
                min_change_rate = 0.0
        if max_change_rate is None and screener_filters:
            try:
                max_change_rate = float(Config.SCREENER_MAX_CHANGE_RATE)
            except (TypeError, ValueError, AttributeError):
                max_change_rate = 0.0
        if min_trade_amount_eok is None and screener_filters:
            try:
                min_trade_amount_eok = float(Config.SCREENER_MIN_TRADE_AMOUNT_EOK)
            except (TypeError, ValueError, AttributeError):
                min_trade_amount_eok = 0.0
        # 0 이하면 플러스(>0) 규칙으로 폴백
        change_floor: Optional[float] = None
        if min_change_rate is not None and float(min_change_rate) > 0:
            change_floor = float(min_change_rate)
        change_ceil: Optional[float] = None
        if max_change_rate is not None and float(max_change_rate) > 0:
            change_ceil = float(max_change_rate)
        # 억원 → 백만원 (ka10030 trde_amt)
        amount_floor_m: Optional[float] = None
        if min_trade_amount_eok is not None and float(min_trade_amount_eok) > 0:
            amount_floor_m = float(min_trade_amount_eok) * 100.0
        if screener_filters:
            filt = dict(SCREENER_VOLUME_RANK_FILTERS)
            # API 거래대금구분에 20억 단계는 없음(10억=100, 30억=300).
            # 10억 이상으로 1차 축소 후 후처리로 정확한 하한 적용.
            if amount_floor_m is not None and amount_floor_m >= 1000 and trde_prica_tp is None:
                filt["trde_prica_tp"] = "100"
        else:
            filt = {
                "mang_stk_incls": "0",
                "crd_tp": "0",
                "trde_qty_tp": "0",
                "pric_tp": "0",
                "trde_prica_tp": "0",
                "mrkt_open_tp": "0",
                "stex_tp": "3",
            }
        for key, val in (
            ("mang_stk_incls", mang_stk_incls),
            ("crd_tp", crd_tp),
            ("trde_qty_tp", trde_qty_tp),
            ("pric_tp", pric_tp),
            ("trde_prica_tp", trde_prica_tp),
            ("mrkt_open_tp", mrkt_open_tp),
            ("stex_tp", stex_tp),
        ):
            if val is not None:
                filt[key] = val

        body = {"mrkt_tp": market, "sort_tp": sort_tp, **filt}
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
            host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
            url = host + "/api/dostk/rkinfo"

            for auth_try in range(2):
                token = self.token_manager.get_valid_token()
                if not token:
                    return {"success": False, "error": "토큰 없음", "items": []}

                headers = {
                    "Content-Type": "application/json;charset=UTF-8",
                    "authorization": f"Bearer {token}",
                    "cont-yn": "N",
                    "next-key": "",
                    "api-id": "ka10030",
                }
                kept: List[Dict] = []
                raw_count = 0
                excluded_etf_count = 0
                excluded_negative_count = 0
                excluded_overheat_count = 0
                excluded_low_amount_count = 0
                seen_codes: set[str] = set()
                cont_yn, next_key = "N", ""
                pages = 0
                # 등락·대금 필터 시 비중이 크면 페이지가 더 필요할 수 있음
                need_paging = (
                    screener_filters or positive_change_only
                    or change_floor is not None or change_ceil is not None
                    or amount_floor_m is not None
                )
                max_pages = 12 if need_paging else 1
                retry_auth = False

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    while pages < max_pages:
                        pages += 1
                        if not await self._acquire_api_slot("ka10030", max_wait=20.0 if limit > 20 else 8.0, priority=APIPriority.HIGH):
                            logger.warning("[VOLUME_RANK] rate limit slot timeout")
                            if kept:
                                break
                            return {"success": False, "error": "API 호출 제한", "items": []}
                        req_headers = {
                            **headers,
                            "cont-yn": cont_yn,
                            "next-key": next_key,
                        }
                        async with session.post(url, headers=req_headers, json=body) as resp:
                            text = await resp.text()
                            resp_cont = resp.headers.get("cont-yn") or resp.headers.get("Cont-Yn") or "N"
                            resp_next = resp.headers.get("next-key") or resp.headers.get("Next-Key") or ""
                            if resp.status == 429:
                                api_rate_limiter.handle_api_error(Exception("429 Too Many Requests"))
                                logger.warning(f"[VOLUME_RANK] HTTP 429, body={text[:300]}")
                                if kept:
                                    break
                                return {"success": False, "error": "HTTP 429", "items": []}
                            if resp.status != 200:
                                logger.warning(f"[VOLUME_RANK] HTTP {resp.status}, body={text[:300]}")
                                if kept:
                                    break
                                return {"success": False, "error": f"HTTP {resp.status}", "items": []}
                            data = json.loads(text)

                        ok = (data.get("return_code") == 0) or (data.get("returnCode") == 0) or (data.get("rt_cd") == "0")
                        if not ok:
                            msg = data.get("return_msg") or data.get("returnMsg") or "조회 실패"
                            logger.warning(f"[VOLUME_RANK] 실패 msg={msg}")
                            if kept:
                                break
                            if auth_try == 0 and self._is_kiwoom_token_invalid(msg, data):
                                logger.warning("🔑 [TOKEN] VOLUME_RANK 토큰 무효 — 재인증 후 재시도")
                                if await self._reauthenticate_async():
                                    retry_auth = True
                                    break
                            return {"success": False, "error": msg, "items": []}

                        rows = data.get("tdy_trde_qty_upper") or []
                        raw_count += len(rows)
                        for r in rows:
                            it = self._parse_volume_rank_row(r)
                            code = it.get("stock_code", "")
                            if code and code in seen_codes:
                                continue
                            if code:
                                seen_codes.add(code)
                            if screener_filters:
                                if self._is_etf_family_item(it.get("stock_name", ""), it.get("product_type")):
                                    excluded_etf_count += 1
                                    continue
                                if not self._is_screener_stock(it.get("stock_name", ""), it.get("product_type")):
                                    continue
                            chg = float(it.get("change_rate") or 0)
                            if change_floor is not None:
                                if chg < change_floor:
                                    excluded_negative_count += 1
                                    continue
                            elif positive_change_only and chg <= 0:
                                excluded_negative_count += 1
                                continue
                            if change_ceil is not None and chg >= change_ceil:
                                excluded_overheat_count += 1
                                continue
                            if amount_floor_m is not None:
                                amt = float(it.get("trade_amount") or 0)
                                if amt < amount_floor_m:
                                    excluded_low_amount_count += 1
                                    continue
                            kept.append(it)
                            if len(kept) >= limit:
                                break

                        if len(kept) >= limit:
                            break
                        next_key = data.get("next_key") or data.get("next-key") or resp_next or ""
                        cont_yn = "Y" if (resp_cont or "").upper() == "Y" and next_key else "N"
                        if cont_yn != "Y":
                            break

                if retry_auth:
                    continue

                items = kept[:limit]
                logger.info(
                    f"[VOLUME_RANK] success count={len(items)} raw={raw_count} "
                    f"excluded_etf={excluded_etf_count} excluded_neg={excluded_negative_count} "
                    f"excluded_oh={excluded_overheat_count} excluded_amt={excluded_low_amount_count} "
                    f"pages={pages} market={market} sort={sort_tp} screener={screener_filters} "
                    f"pos_only={positive_change_only} min_chg={change_floor} "
                    f"max_chg={change_ceil} min_amt_eok="
                    f"{(amount_floor_m / 100.0) if amount_floor_m is not None else None} "
                    f"body={body}"
                )
                return {
                    "success": True,
                    "items": items,
                    "raw_count": raw_count,
                    "excluded_etf_count": excluded_etf_count,
                    "excluded_negative_count": excluded_negative_count,
                    "excluded_overheat_count": excluded_overheat_count,
                    "excluded_low_amount_count": excluded_low_amount_count,
                    "positive_change_only": positive_change_only,
                    "min_change_rate": change_floor,
                    "max_change_rate": change_ceil,
                    "min_trade_amount_eok": (
                        (amount_floor_m / 100.0) if amount_floor_m is not None else None
                    ),
                    "api_filters": body,
                }

            return {"success": False, "error": "토큰 재인증 후에도 조회 실패", "items": []}
        except Exception as e:
            logger.exception(f"[VOLUME_RANK] error={e}")
            return {"success": False, "error": str(e), "items": []}

    @staticmethod
    def _parse_change_rate_rank_row(r: Dict) -> Dict:
        name = r.get("stk_nm", "")
        trade_amt_raw = (
            r.get("trde_amt")
            or r.get("acc_trde_amt")
            or r.get("trde_prica")
            or r.get("now_trde_prica")
            or "0"
        )
        return {
            "stock_code": KiwoomAPI.normalize_stock_code(str(r.get("stk_cd", ""))),
            "stock_name": name,
            "current_price": abs(_parse_kiwoom_int(r.get("cur_prc", "0"))),
            "price_diff": _parse_kiwoom_int(r.get("pred_pre", "0")),
            "change_rate": _parse_kiwoom_float(r.get("flu_rt", "0")),
            "volume": abs(_parse_kiwoom_int(r.get("now_trde_qty", "0"))),
            "trade_amount": abs(_parse_kiwoom_int(trade_amt_raw)),
            "product_type": KiwoomAPI.classify_product_type(name),
            "stock_class": (r.get("stk_cls") or "").strip() or None,
        }

    @staticmethod
    def cap_by_trade_amount(items: List[Dict], limit: int) -> List[Dict]:
        """거래대금(없으면 거래량×현재가) 내림차순으로 상위 limit개."""
        n = max(1, int(limit or 1))

        def _amt(it: Dict) -> float:
            try:
                v = it.get("trade_amount")
                if v is not None and str(v).strip() != "" and float(v) > 0:
                    return float(v)
            except (TypeError, ValueError):
                pass
            try:
                return float(it.get("volume") or 0) * float(it.get("current_price") or 0)
            except (TypeError, ValueError):
                return 0.0

        return sorted(items or [], key=_amt, reverse=True)[:n]

    @staticmethod
    def _parse_theme_group_row(r: Dict) -> Dict:
        return {
            "theme_code": str(r.get("thema_grp_cd") or "").strip(),
            "theme_name": str(r.get("thema_nm") or "").strip(),
            "stock_count": abs(_parse_kiwoom_int(r.get("stk_num", "0"))),
            "change_rate": _parse_kiwoom_float(r.get("flu_rt", "0")),
            "rising_count": abs(_parse_kiwoom_int(r.get("rising_stk_num", "0"))),
            "fall_count": abs(_parse_kiwoom_int(r.get("fall_stk_num", "0"))),
            "period_return": _parse_kiwoom_float(r.get("dt_prft_rt", "0")),
            "main_stocks": str(r.get("main_stk") or "").strip(),
            "flu_sig": str(r.get("flu_sig") or "").strip(),
        }

    @staticmethod
    def _parse_theme_stock_row(r: Dict) -> Dict:
        name = str(r.get("stk_nm") or "").strip()
        return {
            "stock_code": KiwoomAPI.normalize_stock_code(str(r.get("stk_cd", ""))),
            "stock_name": name,
            "current_price": abs(_parse_kiwoom_int(r.get("cur_prc", "0"))),
            "price_diff": _parse_kiwoom_int(r.get("pred_pre", "0")),
            "change_rate": _parse_kiwoom_float(r.get("flu_rt", "0")),
            "volume": abs(_parse_kiwoom_int(r.get("acc_trde_qty", "0"))),
            "period_return": _parse_kiwoom_float(r.get("dt_prft_rt_n", "0")),
            "product_type": KiwoomAPI.classify_product_type(name),
        }

    async def get_theme_group_list(
        self,
        *,
        qry_tp: str = "0",
        stk_cd: str = "",
        date_tp: str = "1",
        thema_nm: str = "",
        flu_pl_amt_tp: str = "3",
        stex_tp: str = "3",
        max_pages: int = 20,
    ) -> Dict:
        """테마그룹별요청 (ka90001, /api/dostk/thme).

        qry_tp: 0전체검색 / 1테마검색 / 2종목검색
        date_tp: 기간수익률 기준 일수 (1~99)
        flu_pl_amt_tp: 1상위기간수익 2하위기간수익 3상위등락률 4하위등락률
        stex_tp: 1KRX / 2NXT / 3통합
        """
        body: Dict = {
            "qry_tp": str(qry_tp or "0"),
            "date_tp": str(date_tp or "1"),
            "flu_pl_amt_tp": str(flu_pl_amt_tp or "3"),
            "stex_tp": str(stex_tp or "3"),
        }
        code = self.normalize_stock_code(stk_cd) if stk_cd else ""
        if code:
            body["stk_cd"] = code
        if thema_nm:
            body["thema_nm"] = str(thema_nm)

        try:
            timeout = aiohttp.ClientTimeout(total=30)
            use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
            host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
            url = host + "/api/dostk/thme"

            for auth_try in range(2):
                token = self.token_manager.get_valid_token()
                if not token:
                    if not self.authenticate():
                        return {"success": False, "error": "토큰 없음", "items": []}
                    token = self.token_manager.get_valid_token()
                if not token:
                    return {"success": False, "error": "토큰 없음", "items": []}

                headers_base = {
                    "Content-Type": "application/json;charset=UTF-8",
                    "authorization": f"Bearer {token}",
                    "api-id": "ka90001",
                }
                items: List[Dict] = []
                seen: set[str] = set()
                cont_yn, next_key = "N", ""
                pages = 0
                retry_auth = False

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    while pages < max(1, int(max_pages or 1)):
                        pages += 1
                        if not await self._acquire_api_slot(
                            "ka90001", max_wait=30.0, priority=APIPriority.LOW
                        ):
                            if items:
                                break
                            return {"success": False, "error": "API 호출 제한", "items": []}

                        headers = {**headers_base, "cont-yn": cont_yn, "next-key": next_key}
                        async with session.post(url, headers=headers, json=body) as resp:
                            body_text = await resp.text()
                            resp_cont = resp.headers.get("cont-yn") or resp.headers.get("Cont-Yn") or "N"
                            resp_next = resp.headers.get("next-key") or resp.headers.get("Next-Key") or ""

                            if resp.status == 429:
                                api_rate_limiter.handle_api_error(Exception("429 Too Many Requests"))
                                continue
                            if resp.status != 200:
                                if self._is_kiwoom_token_invalid(body_text) and auth_try == 0:
                                    retry_auth = True
                                    break
                                return {
                                    "success": False,
                                    "error": f"HTTP {resp.status}",
                                    "items": items,
                                }

                            try:
                                data = json.loads(body_text)
                            except json.JSONDecodeError:
                                return {
                                    "success": False,
                                    "error": "JSON 파싱 실패",
                                    "items": items,
                                }

                            ok = (data.get("return_code") == 0) or (data.get("rt_cd") == "0")
                            if not ok:
                                msg = data.get("return_msg") or data.get("msg1") or "ka90001 실패"
                                if self._is_kiwoom_token_invalid(msg, data) and auth_try == 0:
                                    retry_auth = True
                                    break
                                if any(
                                    k in str(msg).lower()
                                    for k in ["rate limit", "too many", "요청 한도", "429", "제한", "초과"]
                                ):
                                    api_rate_limiter.handle_api_error(Exception(msg))
                                    continue
                                return {"success": False, "error": msg, "items": items, "raw": data}

                            for raw in data.get("thema_grp") or []:
                                parsed = self._parse_theme_group_row(raw if isinstance(raw, dict) else {})
                                tc = parsed.get("theme_code") or ""
                                if not tc or tc in seen:
                                    continue
                                seen.add(tc)
                                items.append(parsed)

                            cont_yn = (resp_cont or data.get("cont_yn") or data.get("cont-yn") or "N").upper()
                            next_key = data.get("next_key") or data.get("next-key") or resp_next or ""
                            if cont_yn != "Y" or not next_key:
                                break

                if retry_auth:
                    await self._reauthenticate_async()
                    continue

                logger.info("[THEME_GROUP] pages=%s items=%s", pages, len(items))
                return {"success": True, "items": items, "pages": pages}

            return {"success": False, "error": "토큰 재인증 실패", "items": []}
        except Exception as e:
            logger.exception(f"[THEME_GROUP] error={e}")
            return {"success": False, "error": str(e), "items": []}

    async def get_theme_component_stocks(
        self,
        thema_grp_cd: str,
        *,
        date_tp: str = "1",
        stex_tp: str = "3",
        max_pages: int = 10,
    ) -> Dict:
        """테마구성종목요청 (ka90002, /api/dostk/thme)."""
        theme_code = str(thema_grp_cd or "").strip()
        if not theme_code:
            return {"success": False, "error": "thema_grp_cd 필요", "items": []}

        body = {
            "thema_grp_cd": theme_code,
            "date_tp": str(date_tp or "1"),
            "stex_tp": str(stex_tp or "3"),
        }

        try:
            timeout = aiohttp.ClientTimeout(total=30)
            use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
            host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
            url = host + "/api/dostk/thme"

            for auth_try in range(2):
                token = self.token_manager.get_valid_token()
                if not token:
                    if not self.authenticate():
                        return {"success": False, "error": "토큰 없음", "items": []}
                    token = self.token_manager.get_valid_token()
                if not token:
                    return {"success": False, "error": "토큰 없음", "items": []}

                headers_base = {
                    "Content-Type": "application/json;charset=UTF-8",
                    "authorization": f"Bearer {token}",
                    "api-id": "ka90002",
                }
                items: List[Dict] = []
                seen: set[str] = set()
                cont_yn, next_key = "N", ""
                pages = 0
                theme_flu_rt = None
                theme_period_return = None
                retry_auth = False

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    while pages < max(1, int(max_pages or 1)):
                        pages += 1
                        if not await self._acquire_api_slot(
                            "ka90002", max_wait=30.0, priority=APIPriority.LOW
                        ):
                            if items:
                                break
                            return {"success": False, "error": "API 호출 제한", "items": []}

                        headers = {**headers_base, "cont-yn": cont_yn, "next-key": next_key}
                        async with session.post(url, headers=headers, json=body) as resp:
                            body_text = await resp.text()
                            resp_cont = resp.headers.get("cont-yn") or resp.headers.get("Cont-Yn") or "N"
                            resp_next = resp.headers.get("next-key") or resp.headers.get("Next-Key") or ""

                            if resp.status == 429:
                                api_rate_limiter.handle_api_error(Exception("429 Too Many Requests"))
                                continue
                            if resp.status != 200:
                                if self._is_kiwoom_token_invalid(body_text) and auth_try == 0:
                                    retry_auth = True
                                    break
                                return {
                                    "success": False,
                                    "error": f"HTTP {resp.status}",
                                    "items": items,
                                }

                            try:
                                data = json.loads(body_text)
                            except json.JSONDecodeError:
                                return {
                                    "success": False,
                                    "error": "JSON 파싱 실패",
                                    "items": items,
                                }

                            ok = (data.get("return_code") == 0) or (data.get("rt_cd") == "0")
                            if not ok:
                                msg = data.get("return_msg") or data.get("msg1") or "ka90002 실패"
                                if self._is_kiwoom_token_invalid(msg, data) and auth_try == 0:
                                    retry_auth = True
                                    break
                                if any(
                                    k in str(msg).lower()
                                    for k in ["rate limit", "too many", "요청 한도", "429", "제한", "초과"]
                                ):
                                    api_rate_limiter.handle_api_error(Exception(msg))
                                    continue
                                return {"success": False, "error": msg, "items": items, "raw": data}

                            if theme_flu_rt is None and data.get("flu_rt") is not None:
                                theme_flu_rt = _parse_kiwoom_float(data.get("flu_rt", "0"))
                            if theme_period_return is None and data.get("dt_prft_rt") is not None:
                                theme_period_return = _parse_kiwoom_float(data.get("dt_prft_rt", "0"))

                            for raw in data.get("thema_comp_stk") or []:
                                parsed = self._parse_theme_stock_row(raw if isinstance(raw, dict) else {})
                                sc = parsed.get("stock_code") or ""
                                if not sc or len(sc) != 6 or sc in seen:
                                    continue
                                seen.add(sc)
                                items.append(parsed)

                            cont_yn = (resp_cont or data.get("cont_yn") or data.get("cont-yn") or "N").upper()
                            next_key = data.get("next_key") or data.get("next-key") or resp_next or ""
                            if cont_yn != "Y" or not next_key:
                                break

                if retry_auth:
                    await self._reauthenticate_async()
                    continue

                return {
                    "success": True,
                    "items": items,
                    "theme_code": theme_code,
                    "change_rate": theme_flu_rt,
                    "period_return": theme_period_return,
                    "pages": pages,
                }

            return {"success": False, "error": "토큰 재인증 실패", "items": []}
        except Exception as e:
            logger.exception(f"[THEME_STOCKS] theme={theme_code} error={e}")
            return {"success": False, "error": str(e), "items": []}

    async def get_change_rate_rank(
        self,
        market: str = "000",
        limit: int = 100,
        *,
        sangtta_filters: bool = True,
        min_change_rate: Optional[float] = None,
        exclude_etf: bool = True,
        sort_tp: Optional[str] = None,
        trde_qty_cnd: Optional[str] = None,
        stk_cnd: Optional[str] = None,
        crd_cnd: Optional[str] = None,
        updown_incls: Optional[str] = None,
        pric_cnd: Optional[str] = None,
        trde_prica_cnd: Optional[str] = None,
        stex_tp: Optional[str] = None,
    ) -> Dict:
        """전일대비등락률상위 조회 (ka10027, /api/dostk/rkinfo).

        market: 000 전체 / 001 코스피 / 101 코스닥
        sangtta_filters: True면 상따 기본 필터(관리제외·천원↑·대금10억↑·KRX) 적용
        min_change_rate: 등락률 하한(%). sangtta_filters면 기본 13.0
        exclude_etf: True면 ETF/ETN/파생·스팩·우선주 후처리 제외
        """
        if min_change_rate is None and sangtta_filters:
            min_change_rate = float(SANGTTA_UNIVERSE_MIN_CHANGE_RATE)
        change_floor: Optional[float] = None
        if min_change_rate is not None and float(min_change_rate) > 0:
            change_floor = float(min_change_rate)

        if sangtta_filters:
            filt = dict(SANGTTA_CHANGE_RATE_RANK_FILTERS)
        else:
            filt = {
                "sort_tp": "1",
                "trde_qty_cnd": "0",
                "stk_cnd": "0",
                "crd_cnd": "0",
                "updown_incls": "1",
                "pric_cnd": "0",
                "trde_prica_cnd": "0",
                "stex_tp": "1",
            }
        for key, val in (
            ("sort_tp", sort_tp),
            ("trde_qty_cnd", trde_qty_cnd),
            ("stk_cnd", stk_cnd),
            ("crd_cnd", crd_cnd),
            ("updown_incls", updown_incls),
            ("pric_cnd", pric_cnd),
            ("trde_prica_cnd", trde_prica_cnd),
            ("stex_tp", stex_tp),
        ):
            if val is not None:
                filt[key] = val

        body = {"mrkt_tp": market, **filt}
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
            host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
            url = host + "/api/dostk/rkinfo"

            for auth_try in range(2):
                token = self.token_manager.get_valid_token()
                if not token:
                    return {"success": False, "error": "토큰 없음", "items": []}

                headers = {
                    "Content-Type": "application/json;charset=UTF-8",
                    "authorization": f"Bearer {token}",
                    "cont-yn": "N",
                    "next-key": "",
                    "api-id": "ka10027",
                }
                kept: List[Dict] = []
                raw_count = 0
                excluded_etf_count = 0
                excluded_low_chg_count = 0
                seen_codes: set[str] = set()
                cont_yn, next_key = "N", ""
                pages = 0
                max_pages = 8 if (exclude_etf or change_floor is not None) else 1
                retry_auth = False
                # 상승률 정렬이면 하한 미만이 연속으로 나오면 이후도 더 낮음 → 조기 종료
                hit_below_floor = False

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    while pages < max_pages and not hit_below_floor:
                        pages += 1
                        if not await self._acquire_api_slot(
                            "ka10027",
                            max_wait=20.0 if limit > 20 else 8.0,
                            priority=APIPriority.HIGH,
                        ):
                            logger.warning("[CHANGE_RATE_RANK] rate limit slot timeout")
                            if kept:
                                break
                            return {"success": False, "error": "API 호출 제한", "items": []}
                        req_headers = {
                            **headers,
                            "cont-yn": cont_yn,
                            "next-key": next_key,
                        }
                        async with session.post(url, headers=req_headers, json=body) as resp:
                            text = await resp.text()
                            resp_cont = resp.headers.get("cont-yn") or resp.headers.get("Cont-Yn") or "N"
                            resp_next = resp.headers.get("next-key") or resp.headers.get("Next-Key") or ""
                            if resp.status == 429:
                                api_rate_limiter.handle_api_error(Exception("429 Too Many Requests"))
                                logger.warning(f"[CHANGE_RATE_RANK] HTTP 429, body={text[:300]}")
                                if kept:
                                    break
                                return {"success": False, "error": "HTTP 429", "items": []}
                            if resp.status != 200:
                                logger.warning(f"[CHANGE_RATE_RANK] HTTP {resp.status}, body={text[:300]}")
                                if kept:
                                    break
                                return {"success": False, "error": f"HTTP {resp.status}", "items": []}
                            data = json.loads(text)

                        ok = (data.get("return_code") == 0) or (data.get("returnCode") == 0) or (data.get("rt_cd") == "0")
                        if not ok:
                            msg = data.get("return_msg") or data.get("returnMsg") or "조회 실패"
                            logger.warning(f"[CHANGE_RATE_RANK] 실패 msg={msg}")
                            if kept:
                                break
                            if auth_try == 0 and self._is_kiwoom_token_invalid(msg, data):
                                logger.warning("🔑 [TOKEN] CHANGE_RATE_RANK 토큰 무효 — 재인증 후 재시도")
                                if await self._reauthenticate_async():
                                    retry_auth = True
                                    break
                            return {"success": False, "error": msg, "items": []}

                        rows = data.get("pred_pre_flu_rt_upper") or []
                        raw_count += len(rows)
                        for r in rows:
                            it = self._parse_change_rate_rank_row(r)
                            code = it.get("stock_code", "")
                            if code and code in seen_codes:
                                continue
                            if code:
                                seen_codes.add(code)
                            chg = float(it.get("change_rate") or 0)
                            if change_floor is not None and chg < change_floor:
                                excluded_low_chg_count += 1
                                # sort_tp=1(상승률)이면 이후 행도 하한 미만
                                if str(filt.get("sort_tp") or "1") == "1":
                                    hit_below_floor = True
                                    break
                                continue
                            if exclude_etf:
                                if self._is_etf_family_item(it.get("stock_name", ""), it.get("product_type")):
                                    excluded_etf_count += 1
                                    continue
                                if not self._is_screener_stock(it.get("stock_name", ""), it.get("product_type")):
                                    continue
                            kept.append(it)
                            if len(kept) >= limit:
                                break

                        if len(kept) >= limit or hit_below_floor:
                            break
                        next_key = data.get("next_key") or data.get("next-key") or resp_next or ""
                        cont_yn = "Y" if (resp_cont or "").upper() == "Y" and next_key else "N"
                        if cont_yn != "Y":
                            break

                if retry_auth:
                    continue

                items = kept[:limit]
                logger.info(
                    f"[CHANGE_RATE_RANK] success count={len(items)} raw={raw_count} "
                    f"excluded_etf={excluded_etf_count} excluded_low_chg={excluded_low_chg_count} "
                    f"pages={pages} market={market} min_chg={change_floor} "
                    f"sangtta={sangtta_filters} body={body}"
                )
                return {
                    "success": True,
                    "items": items,
                    "raw_count": raw_count,
                    "excluded_etf_count": excluded_etf_count,
                    "excluded_low_chg_count": excluded_low_chg_count,
                    "min_change_rate": change_floor,
                    "api_filters": body,
                }

            return {"success": False, "error": "토큰 재인증 후에도 조회 실패", "items": []}
        except Exception as e:
            logger.exception(f"[CHANGE_RATE_RANK] error={e}")
            return {"success": False, "error": str(e), "items": []}

    async def get_executions(
        self,
        sell_tp: str = "0",
        stk_cd: str = "",
        ord_no: str = "",
        stex_tp: str = "0",
        max_pages: int = 20,
    ) -> Dict:
        """체결 내역 조회 (ka10076, /api/dostk/acnt).
        sell_tp: 0 전체 / 1 매도 / 2 매수
        """
        token = self.token_manager.get_valid_token()
        if not token:
            return {"success": False, "error": "토큰 없음", "items": []}

        use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
        host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
        account_no = Config.KIWOOM_MOCK_ACCOUNT_NUMBER if use_mock else Config.KIWOOM_ACCOUNT_NUMBER
        url = host + "/api/dostk/acnt"
        headers_base = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": "ka10076",
        }
        body = {
            "stk_cd": stk_cd or "",
            "qry_tp": "0",
            "sell_tp": sell_tp,
            "ord_no": ord_no or "",
            "stex_tp": stex_tp,
            "acnt_no": account_no or "",
        }

        items: List[Dict] = []
        cont_yn, next_key = "N", ""
        pages = 0

        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                while pages < max_pages:
                    pages += 1
                    headers = {**headers_base, "cont-yn": cont_yn, "next-key": next_key}
                    async with session.post(url, headers=headers, json=body) as resp:
                        text = await resp.text()
                        resp_cont = resp.headers.get("cont-yn") or resp.headers.get("Cont-Yn") or "N"
                        resp_next = resp.headers.get("next-key") or resp.headers.get("Next-Key") or ""

                        if resp.status != 200:
                            logger.warning(f"[EXECUTIONS] HTTP {resp.status} body={text[:300]}")
                            return {"success": False, "error": f"HTTP {resp.status}", "items": items}

                        data = json.loads(text)

                    ok = (data.get("return_code") == 0) or (data.get("returnCode") == 0) or (data.get("rt_cd") == "0")
                    if not ok:
                        msg = data.get("return_msg") or data.get("returnMsg") or data.get("msg1") or "조회 실패"
                        logger.warning(f"[EXECUTIONS] fail msg={msg}")
                        if items:
                            break
                        return {"success": False, "error": msg, "items": []}

                    rows = data.get("cntr") or data.get("output") or []
                    for r in rows:
                        items.append(r)

                    cont_yn = data.get("cont_yn") or resp_cont or "N"
                    next_key = data.get("next_key") or data.get("next-key") or resp_next or ""
                    if str(cont_yn).upper() != "Y" or not next_key:
                        break

            logger.info(f"[EXECUTIONS] ka10076 success count={len(items)} pages={pages}")
            return {"success": True, "items": items}
        except Exception as e:
            logger.exception(f"[EXECUTIONS] error={e}")
            return {"success": False, "error": str(e), "items": items}

    async def get_daily_stock_realized_pnl(
        self,
        strt_dt: str,
        end_dt: str,
        stk_cd: str = "",
        max_pages: int = 50,
    ) -> Dict:
        """일자별종목별실현손익 조회 (ka10073, /api/dostk/acnt).

        매도 체결이 발생한 종목/일자별로 실현손익을 반환한다.
        stk_cd를 비우면 계좌 전체 종목을 조회한다.
        각 행의 tdy_sel_pl은 수수료·거래세가 차감된 '순실현손익'이다.
        strt_dt/end_dt: YYYYMMDD
        """
        token = self.token_manager.get_valid_token()
        if not token:
            return {"success": False, "error": "토큰 없음", "items": []}

        use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
        host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
        url = host + "/api/dostk/acnt"
        headers_base = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": "ka10073",
        }
        body = {
            "stk_cd": stk_cd or "",
            "strt_dt": strt_dt,
            "end_dt": end_dt,
        }

        items: List[Dict] = []
        cont_yn, next_key = "N", ""
        pages = 0

        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                while pages < max_pages:
                    pages += 1
                    headers = {**headers_base, "cont-yn": cont_yn, "next-key": next_key}
                    async with session.post(url, headers=headers, json=body) as resp:
                        text = await resp.text()
                        resp_cont = resp.headers.get("cont-yn") or resp.headers.get("Cont-Yn") or "N"
                        resp_next = resp.headers.get("next-key") or resp.headers.get("Next-Key") or ""

                        if resp.status != 200:
                            logger.warning(f"[REALIZED_PNL] HTTP {resp.status} body={text[:300]}")
                            return {"success": False, "error": f"HTTP {resp.status}", "items": items}

                        data = json.loads(text)

                    if data.get("return_code") != 0:
                        msg = data.get("return_msg") or "조회 실패"
                        logger.warning(f"[REALIZED_PNL] fail msg={msg}")
                        if items:
                            break
                        return {"success": False, "error": msg.strip(), "items": []}

                    rows = data.get("dt_stk_rlzt_pl") or []
                    items.extend(rows)

                    cont_yn = resp_cont or "N"
                    next_key = resp_next or ""
                    if str(cont_yn).upper() != "Y" or not next_key:
                        break

            logger.info(
                f"[REALIZED_PNL] ka10073 success count={len(items)} pages={pages} "
                f"period={strt_dt}~{end_dt}"
            )
            return {"success": True, "items": items}
        except Exception as e:
            logger.exception(f"[REALIZED_PNL] error={e}")
            return {"success": False, "error": str(e), "items": items}

    async def get_daily_realized_pnl(
        self,
        strt_dt: str,
        end_dt: str,
        max_pages: int = 50,
    ) -> Dict:
        """일자별실현손익 조회 (ka10074, /api/dostk/acnt).

        기간 내 일별 실현손익 합계. rlzt_pl은 기간 전체 실현손익 합계이다.
        strt_dt/end_dt: YYYYMMDD
        """
        token = self.token_manager.get_valid_token()
        if not token:
            return {"success": False, "error": "토큰 없음", "items": [], "rlzt_pl": 0}

        use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
        host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
        url = host + "/api/dostk/acnt"
        headers_base = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": "ka10074",
        }
        body = {"strt_dt": strt_dt, "end_dt": end_dt}

        items: List[Dict] = []
        cont_yn, next_key = "N", ""
        pages = 0
        rlzt_pl = 0

        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                while pages < max_pages:
                    pages += 1
                    headers = {**headers_base, "cont-yn": cont_yn, "next-key": next_key}
                    async with session.post(url, headers=headers, json=body) as resp:
                        text = await resp.text()
                        resp_cont = resp.headers.get("cont-yn") or resp.headers.get("Cont-Yn") or "N"
                        resp_next = resp.headers.get("next-key") or resp.headers.get("Next-Key") or ""

                        if resp.status != 200:
                            logger.warning(f"[DAILY_PNL] HTTP {resp.status} body={text[:300]}")
                            return {"success": False, "error": f"HTTP {resp.status}", "items": items, "rlzt_pl": rlzt_pl}

                        data = json.loads(text)

                    if data.get("return_code") != 0:
                        msg = data.get("return_msg") or "조회 실패"
                        logger.warning(f"[DAILY_PNL] fail msg={msg}")
                        if items:
                            break
                        return {"success": False, "error": msg.strip(), "items": [], "rlzt_pl": 0}

                    if pages == 1:
                        rlzt_pl = _parse_kiwoom_int(data.get("rlzt_pl"))

                    rows = data.get("dt_rlzt_pl") or []
                    items.extend(rows)

                    cont_yn = resp_cont or "N"
                    next_key = resp_next or ""
                    if str(cont_yn).upper() != "Y" or not next_key:
                        break

            logger.info(
                f"[DAILY_PNL] ka10074 success count={len(items)} pages={pages} "
                f"rlzt_pl={rlzt_pl} period={strt_dt}~{end_dt}"
            )
            return {"success": True, "items": items, "rlzt_pl": rlzt_pl}
        except Exception as e:
            logger.exception(f"[DAILY_PNL] error={e}")
            return {"success": False, "error": str(e), "items": items, "rlzt_pl": rlzt_pl}

    @staticmethod
    def _pick_int(row: Dict, keys: List[str], default: int = 0) -> int:
        for key in keys:
            if key in row and row.get(key) not in (None, ""):
                try:
                    return abs(int(str(row.get(key)).replace(",", "").replace("+", "")))
                except Exception:
                    continue
        return default

    @staticmethod
    def _pick_str(row: Dict, keys: List[str], default: str = "") -> str:
        for key in keys:
            if key in row and row.get(key) not in (None, ""):
                return str(row.get(key))
        return default

    @staticmethod
    def _ka10004_level_keys(level: int) -> Dict[str, List[str]]:
        """ka10004(REST) 호가 레벨별 필드명. 1호가는 *_fpr_*, 2~10은 *_{n}th_pre_*."""
        if level == 1:
            return {
                "ask_price": ["sel_fpr_bid", "askp1", "offerho1", "sel_prc1"],
                "ask_qty": ["sel_fpr_req", "askp_rsqn1", "offerrem1", "sel_qty1"],
                "bid_price": ["buy_fpr_bid", "bidp1", "bidho1", "buy_prc1"],
                "bid_qty": ["buy_fpr_req", "bidp_rsqn1", "bidrem1", "buy_qty1"],
            }
        return {
            "ask_price": [
                f"sel_{level}th_pre_bid",
                f"askp{level}",
                f"offerho{level}",
                f"sel_prc{level}",
            ],
            "ask_qty": [
                f"sel_{level}th_pre_req",
                f"askp_rsqn{level}",
                f"offerrem{level}",
                f"sel_qty{level}",
            ],
            "bid_price": [
                f"buy_{level}th_pre_bid",
                f"bidp{level}",
                f"bidho{level}",
                f"buy_prc{level}",
            ],
            "bid_qty": [
                f"buy_{level}th_pre_req",
                f"bidp_rsqn{level}",
                f"bidrem{level}",
                f"buy_qty{level}",
            ],
        }

    @classmethod
    def _extract_quote_row(cls, quote_data: Dict) -> Dict:
        """ka10004 응답에서 호가 행 dict 추출 (flat 본문 또는 stk_hoga[0])."""
        if not isinstance(quote_data, dict) or not quote_data:
            return {}
        # REST flat 응답: sel_fpr_bid / buy_fpr_bid 가 본문에 직접 있음
        if any(
            k in quote_data
            for k in ("sel_fpr_bid", "buy_fpr_bid", "askp1", "bidp1", "bid_req_base_tm")
        ):
            return quote_data
        rows = (
            quote_data.get("stk_hoga")
            or quote_data.get("output")
            or quote_data.get("data")
            or []
        )
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return rows[0]
        if isinstance(rows, dict):
            return rows
        return {}

    @classmethod
    def parse_ka10004_orderbook(cls, quote_data: Dict) -> List[Dict]:
        """ka10004 응답 → [{level, ask_price, ask_qty, bid_price, bid_qty}, ...]."""
        quote_row = cls._extract_quote_row(quote_data or {})
        if not quote_row:
            return []
        out: List[Dict] = []
        for level in range(1, 11):
            keys = cls._ka10004_level_keys(level)
            ask_px = cls._pick_int(quote_row, keys["ask_price"])
            bid_px = cls._pick_int(quote_row, keys["bid_price"])
            ask_qty = cls._pick_int(quote_row, keys["ask_qty"])
            bid_qty = cls._pick_int(quote_row, keys["bid_qty"])
            if ask_px == 0 and bid_px == 0 and ask_qty == 0 and bid_qty == 0:
                continue
            out.append(
                {
                    "level": level,
                    "ask_price": ask_px,
                    "ask_qty": ask_qty,
                    "bid_price": bid_px,
                    "bid_qty": bid_qty,
                }
            )
        return out

    async def get_stock_snapshot(self, stock_code: str) -> Dict:
        """현재가(ka10006) + 10호가(ka10004) 스냅샷 조회."""
        code = self.normalize_stock_code(stock_code)
        if not code:
            return {"success": False, "error": "종목코드가 비어 있습니다."}

        logger.info(f"[SNAPSHOT_START] stock_code={code}")

        basic_resp = await self._request_stockinfo_tr("ka10006", {"stk_cd": code})
        quote_resp = await self._request_stockinfo_tr("ka10004", {"stk_cd": code})
        # ka10006/ka10004가 환경에 따라 실패할 수 있어,
        # 이미 검증된 현재가 조회 로직(ka10081)을 fallback으로 사용한다.
        fallback_price = None
        fallback_volume = 0
        fallback_prev_close = 0
        if not basic_resp.get("success"):
            fallback_price = await self.get_current_price(code)
            if fallback_price is None:
                logger.error(f"[SNAPSHOT_FAIL] stock_code={code}, reason=ka10006_failed_and_no_fallback, error={basic_resp.get('error')}")
                return {"success": False, "error": f"ka10006 실패: {basic_resp.get('error', 'unknown error')}"}
            # ka10081 일봉 데이터로 거래량/전일종가 보정 시도
            try:
                chart_data = await self.get_stock_chart_data(code, "1D")
                if chart_data:
                    last_row = chart_data[-1]
                    fallback_volume = int(last_row.get("volume", 0) or 0)
                    if len(chart_data) >= 2:
                        fallback_prev_close = int(chart_data[-2].get("close", 0) or 0)
            except Exception:
                pass
            logger.warning(f"get_stock_snapshot: ka10006 실패, fallback 현재가 사용 - {code}")
            basic_resp = {"success": True, "data": {"stk_mkprc": [{"cur_prc": fallback_price}]}}

        basic_data = basic_resp.get("data", {})
        quote_data = quote_resp.get("data", {}) if quote_resp.get("success") else {}

        basic_rows = (
            basic_data.get("stk_mkprc")
            or basic_data.get("output")
            or basic_data.get("data")
            or []
        )
        # ka10006도 flat 본문일 수 있음
        if isinstance(basic_data, dict) and basic_data.get("cur_prc") not in (None, "") and not (
            isinstance(basic_rows, list) and basic_rows
        ):
            basic_row = basic_data
        else:
            basic_row = basic_rows[0] if isinstance(basic_rows, list) and basic_rows else {}

        quote_row = self._extract_quote_row(quote_data)
        logger.info(
            f"[SNAPSHOT_RAW] stock_code={code}, basic_endpoint={basic_resp.get('endpoint','')}, "
            f"quote_endpoint={quote_resp.get('endpoint','')}, basic_keys={list(basic_data.keys())[:20]}, "
            f"quote_keys={list(quote_data.keys())[:20]}"
        )
        logger.info(
            f"[SNAPSHOT_ROW_KEYS] stock_code={code}, "
            f"basic_row_keys={list(basic_row.keys())[:40] if isinstance(basic_row, dict) else []}, "
            f"quote_row_keys={list(quote_row.keys())[:40] if isinstance(quote_row, dict) else []}"
        )

        orderbook = self.parse_ka10004_orderbook(quote_data)
        orderbook_live = bool(orderbook) and bool(quote_resp.get("success"))

        snapshot = {
            "stock_code": code,
            "stock_name": self._pick_str(basic_row, ["stk_nm", "stock_name", "name", "302"], ""),
            "current_price": self._pick_int(basic_row, ["cur_prc", "price", "10"]),
            "price_diff": self._pick_int(basic_row, ["pred_pre", "diff", "11"]),
            "change_rate": self._pick_str(basic_row, ["flu_rt", "change_rate", "12"], "0"),
            "volume": self._pick_int(basic_row, ["trde_qty", "volume", "13"]),
            "orderbook_time": self._pick_str(
                quote_row,
                ["bid_req_base_tm", "hotime", "hoga_time", "time"],
                now_kst().strftime("%H:%M:%S"),
            ),
            "orderbook": orderbook,
            "orderbook_live": orderbook_live,
            "raw_basic": basic_row,
            "raw_quote": quote_row,
        }

        # fallback 가격이 있으면 current_price를 보정
        if fallback_price is not None and snapshot["current_price"] == 0:
            snapshot["current_price"] = fallback_price
        if snapshot["volume"] == 0 and fallback_volume > 0:
            snapshot["volume"] = fallback_volume
        if snapshot["price_diff"] == 0 and fallback_prev_close > 0 and snapshot["current_price"] > 0:
            snapshot["price_diff"] = snapshot["current_price"] - fallback_prev_close
        if (snapshot["change_rate"] in ("", "0", "0.0", "0.00")) and fallback_prev_close > 0:
            try:
                diff = snapshot["current_price"] - fallback_prev_close
                snapshot["change_rate"] = f"{(diff / fallback_prev_close) * 100:.2f}"
            except Exception:
                pass

        # 호가 TR 실패 시, 화면 표시용 가짜 호가(잔량 0). 돼지 판정에는 쓰지 않음.
        if not snapshot["orderbook"] and snapshot["current_price"] > 0:
            tick = self._calc_tick_size(snapshot["current_price"])
            base = snapshot["current_price"]
            for i in range(1, 4):
                snapshot["orderbook"].append(
                    {
                        "level": i,
                        "ask_price": base + (tick * i),
                        "ask_qty": 0,
                        "bid_price": max(0, base - (tick * i)),
                        "bid_qty": 0,
                    }
                )
            snapshot["orderbook_live"] = False
            logger.warning(f"[SNAPSHOT_ORDERBOOK_FALLBACK] stock_code={code}, reason=empty_orderbook_from_tr")

        warnings = []
        if fallback_price is not None:
            warnings.append("ka10006 unavailable; current_price from fallback")
        if not quote_resp.get("success"):
            warnings.append(f"ka10004 unavailable: {quote_resp.get('error', 'unknown error')}")
        elif not orderbook_live:
            warnings.append("ka10004 returned empty orderbook")
        if warnings:
            snapshot["warnings"] = warnings

        logger.info(
            f"[SNAPSHOT_DONE] stock_code={code}, stock_name={snapshot.get('stock_name','')}, "
            f"current_price={snapshot.get('current_price',0)}, price_diff={snapshot.get('price_diff',0)}, "
            f"change_rate={snapshot.get('change_rate','0')}, volume={snapshot.get('volume',0)}, "
            f"orderbook_rows={len(snapshot.get('orderbook', []))}, "
            f"orderbook_live={snapshot.get('orderbook_live')}, warnings={snapshot.get('warnings', [])}"
        )
        return {"success": True, "snapshot": snapshot}

    async def get_stock_institution_foreign_net(self, stock_code: str) -> Dict:
        """종목별 기관·외인 일별 순매매 (ka10009, 필요 시 ka10045 보정).

        Returns:
            success, foreign_net, institution_net, date, source, raw
            순매매 수량은 양수=순매수, 음수=순매도.
        """
        code = self.normalize_stock_code(stock_code)
        if not code:
            return {"success": False, "error": "종목코드가 비어 있습니다."}

        def _to_int(v) -> int:
            try:
                s = str(v or "").replace(",", "").replace("+", "").strip()
                if not s or s in ("-", "--"):
                    return 0
                return int(float(s))
            except (TypeError, ValueError):
                return 0

        # 1) ka10009 — 주식기관요청 (종목 1건, 기관·외인 순매매)
        resp = await self._request_stockinfo_tr("ka10009", {"stk_cd": code})
        if resp.get("success"):
            data = resp.get("data") or {}
            rows = data.get("stk_orgn") or data.get("output") or data.get("data")
            row = None
            if isinstance(rows, list) and rows:
                row = rows[0] if isinstance(rows[0], dict) else None
            elif isinstance(data, dict):
                # 단일 객체 응답
                if data.get("orgn_daly_nettrde") is not None or data.get("frgnr_daly_nettrde") is not None:
                    row = data
            if row:
                foreign = _to_int(
                    row.get("frgnr_daly_nettrde")
                    or row.get("frgnr_daly_nettrde_qty")
                    or row.get("for_daly_nettrde_qty")
                )
                institution = _to_int(
                    row.get("orgn_daly_nettrde")
                    or row.get("orgn_daly_nettrde_qty")
                    or row.get("orgn_daly_nettrde")
                )
                # 빈 문자열만 온 경우(모의/장마감 전)는 fallback
                has_any = any(
                    str(row.get(k) or "").strip() not in ("",)
                    for k in (
                        "orgn_daly_nettrde",
                        "frgnr_daly_nettrde",
                        "orgn_daly_nettrde_qty",
                        "frgnr_daly_nettrde_qty",
                        "for_daly_nettrde_qty",
                    )
                )
                if has_any:
                    return {
                        "success": True,
                        "stock_code": code,
                        "foreign_net": foreign,
                        "institution_net": institution,
                        "date": self._pick_str(row, ["date", "dt"], ""),
                        "source": "ka10009",
                        "raw": row,
                    }

        # 2) ka10045 — 종목별기관매매추이 (당일 구간)
        from utils.datetime_kst import kst_today

        today = kst_today().strftime("%Y%m%d")
        resp45 = await self._request_stockinfo_tr(
            "ka10045",
            {
                "stk_cd": code,
                "strt_dt": today,
                "end_dt": today,
                "orgn_prsm_unp_tp": "1",
                "for_prsm_unp_tp": "1",
            },
        )
        if resp45.get("success"):
            data = resp45.get("data") or {}
            rows = (
                data.get("stk_orgn_trde_trnsn")
                or data.get("output")
                or data.get("data")
                or []
            )
            if isinstance(rows, dict):
                rows = [rows]
            if isinstance(rows, list) and rows:
                # 최신 일자 우선
                def _dt_key(r):
                    return str((r or {}).get("dt") or "")

                row = sorted(
                    [r for r in rows if isinstance(r, dict)],
                    key=_dt_key,
                    reverse=True,
                )[0]
                foreign = _to_int(
                    row.get("for_daly_nettrde_qty")
                    or row.get("frgnr_daly_nettrde")
                    or row.get("for_dt_acc")
                )
                institution = _to_int(
                    row.get("orgn_daly_nettrde_qty")
                    or row.get("orgn_daly_nettrde")
                    or row.get("orgn_dt_acc")
                )
                return {
                    "success": True,
                    "stock_code": code,
                    "foreign_net": foreign,
                    "institution_net": institution,
                    "date": self._pick_str(row, ["dt", "date"], today),
                    "source": "ka10045",
                    "raw": row,
                }

        err = (resp45.get("error") if not resp45.get("success") else None) or (
            resp.get("error") if not resp.get("success") else "기관/외인 순매매 없음"
        )
        return {"success": False, "error": err, "stock_code": code}

    async def get_stock_program_net(self, stock_code: str) -> Dict:
        """종목별 프로그램 순매수 (장중 가능).

        Primary: ka90013 종목일별프로그램매매추이 (`/api/dostk/mrkcond`)
        Fallback: ka90008 종목시간별 최신 봉

        Returns:
            success, net_qty, buy_qty, sell_qty, net_amt, date, source, raw
            양수=프로그램 순매수.
        """
        code = self.normalize_stock_code(stock_code)
        if not code:
            return {"success": False, "error": "종목코드가 비어 있습니다."}

        def _to_int(v) -> int:
            try:
                s = str(v or "").replace(",", "").replace("+", "").strip()
                if not s or s in ("-", "--"):
                    return 0
                return int(float(s))
            except (TypeError, ValueError):
                return 0

        from utils.datetime_kst import kst_today

        today = kst_today().strftime("%Y%m%d")

        # 1) ka90013 — 일별(당일 누적 포함, 장중에도 값 있음)
        resp = await self._request_stockinfo_tr(
            "ka90013",
            {"amt_qty_tp": "2", "stk_cd": code},
        )
        if resp.get("success"):
            data = resp.get("data") or {}
            rows = (
                data.get("stk_daly_prm_trde_trnsn")
                or data.get("output")
                or data.get("data")
                or []
            )
            if isinstance(rows, dict):
                rows = [rows]
            if isinstance(rows, list) and rows:
                dict_rows = [r for r in rows if isinstance(r, dict)]

                def _dt_key(r):
                    return str(r.get("dt") or r.get("date") or "")

                today_rows = [r for r in dict_rows if _dt_key(r) == today]
                row = (today_rows or sorted(dict_rows, key=_dt_key, reverse=True) or [None])[0]
                if row:
                    net_qty = _to_int(
                        row.get("prm_netprps_qty") or row.get("prm_netprps_amt")
                    )
                    return {
                        "success": True,
                        "stock_code": code,
                        "net_qty": net_qty,
                        "buy_qty": _to_int(row.get("prm_buy_qty")),
                        "sell_qty": _to_int(row.get("prm_sell_qty")),
                        "net_amt": _to_int(row.get("prm_netprps_amt")),
                        "buy_amt": _to_int(row.get("prm_buy_amt")),
                        "sell_amt": _to_int(row.get("prm_sell_amt")),
                        "date": _dt_key(row) or today,
                        "source": "ka90013",
                        "raw": row,
                    }

        # 2) ka90008 — 시간별 최신 구간
        resp8 = await self._request_stockinfo_tr(
            "ka90008",
            {"amt_qty_tp": "2", "stk_cd": code, "date": today},
        )
        if resp8.get("success"):
            data = resp8.get("data") or {}
            rows = (
                data.get("stk_tm_prm_trde_trnsn")
                or data.get("output")
                or data.get("data")
                or []
            )
            if isinstance(rows, dict):
                rows = [rows]
            if isinstance(rows, list) and rows:
                dict_rows = [r for r in rows if isinstance(r, dict)]
                # 최신 시간 우선
                row = sorted(
                    dict_rows,
                    key=lambda r: str(r.get("tm") or ""),
                    reverse=True,
                )[0]
                net_qty = _to_int(
                    row.get("prm_netprps_qty") or row.get("prm_netprps_amt")
                )
                return {
                    "success": True,
                    "stock_code": code,
                    "net_qty": net_qty,
                    "buy_qty": _to_int(row.get("prm_buy_qty")),
                    "sell_qty": _to_int(row.get("prm_sell_qty")),
                    "net_amt": _to_int(row.get("prm_netprps_amt")),
                    "buy_amt": _to_int(row.get("prm_buy_amt")),
                    "sell_amt": _to_int(row.get("prm_sell_amt")),
                    "date": today,
                    "time": str(row.get("tm") or ""),
                    "source": "ka90008",
                    "raw": row,
                }

        err = (resp8.get("error") if not resp8.get("success") else None) or (
            resp.get("error") if not resp.get("success") else "프로그램 매매 없음"
        )
        return {"success": False, "error": err, "stock_code": code}

    @staticmethod
    def _calc_tick_size(price: int) -> int:
        """국내주식 호가 단위(대략) 계산."""
        p = abs(int(price or 0))
        if p < 2000:
            return 1
        if p < 5000:
            return 5
        if p < 20000:
            return 10
        if p < 50000:
            return 50
        if p < 200000:
            return 100
        if p < 500000:
            return 500
        return 1000
    
    def _parse_kiwoom_chart_data(
        self,
        api_response: dict,
        stock_code: str,
        *,
        trade_yyyymmdd: Optional[str] = None,
        verbose: bool = True,
    ) -> list:
        """키움 API 응답을 차트 데이터로 변환.

        trade_yyyymmdd: ka10080 date(기준일자)와 맞춰 해당 일자 봉만 파싱 (검증 차트용).
        """
        chart_data = []
        
        try:
            # 분봉 데이터 (ka10080)
            minute_chart_list = api_response.get('stk_min_pole_chart_qry', [])
            # 일봉 데이터 (ka10081)
            daily_chart_list = api_response.get('stk_dt_pole_chart_qry', [])
            
            if minute_chart_list:
                if verbose:
                    logger.debug(f"📊 [CHART] 분봉 파싱: raw={len(minute_chart_list)}")
                
                for item in minute_chart_list:
                    cntr_tm = str(item.get('cntr_tm', '') or '')
                    if trade_yyyymmdd and len(cntr_tm) >= 8 and cntr_tm[:8] != trade_yyyymmdd:
                        continue
                    open_price = abs(_parse_kiwoom_int(item.get('open_pric', 0)))
                    high_price = abs(_parse_kiwoom_int(item.get('high_pric', 0)))
                    low_price = abs(_parse_kiwoom_int(item.get('low_pric', 0)))
                    close_price = abs(_parse_kiwoom_int(item.get('cur_prc', 0)))
                    # 일봉과 동일: 부호·제로패딩 문자열 → 절대 거래량
                    volume = abs(_parse_kiwoom_int(item.get('trde_qty', 0)))
                    
                    if len(cntr_tm) >= 12:
                        formatted_date = f"{cntr_tm[:4]}-{cntr_tm[4:6]}-{cntr_tm[6:8]} {cntr_tm[8:10]}:{cntr_tm[10:12]}:00"
                    else:
                        formatted_date = now_kst().strftime("%Y-%m-%d %H:%M:%S")
                    
                    chart_data.append({
                        "timestamp": formatted_date,
                        "open": open_price,
                        "high": high_price,
                        "low": low_price,
                        "close": close_price,
                        "volume": volume,
                    })
                    
            elif daily_chart_list:
                if verbose:
                    logger.debug(f"📊 [CHART] 일봉 파싱: {len(daily_chart_list)}개")
                for item in daily_chart_list:
                    dt = item.get('dt', '')
                    open_price = abs(_parse_kiwoom_int(item.get('open_pric', 0)))
                    high_price = abs(_parse_kiwoom_int(item.get('high_pric', 0)))
                    low_price = abs(_parse_kiwoom_int(item.get('low_pric', 0)))
                    close_price = abs(_parse_kiwoom_int(item.get('cur_prc', 0)))
                    volume = abs(_parse_kiwoom_int(item.get('trde_qty', 0)))
                    
                    # 날짜 형식 변환: YYYYMMDD → YYYY-MM-DD 15:30:00 (장마감 기준)
                    if len(dt) == 8:
                        formatted_date = f"{dt[:4]}-{dt[4:6]}-{dt[6:8]} 15:30:00"
                    else:
                        formatted_date = now_kst().strftime("%Y-%m-%d %H:%M:%S")
                    
                    chart_data.append({
                        "timestamp": formatted_date,
                        "open": open_price,
                        "high": high_price,
                        "low": low_price,
                        "close": close_price,
                        "volume": volume
                    })
            
            chart_data.sort(key=lambda x: x['timestamp'])
            
            if verbose:
                logger.debug(f"차트 파싱 완료: {stock_code}, {len(chart_data)}봉")
            return chart_data
            
        except Exception as e:
            logger.error(f"차트 데이터 파싱 중 오류: {e}")
            return []
    
    

    async def get_account_profit(self, stex_tp: str = "0", limit: int = 500) -> Dict:
        """ka10085: 계좌수익률요청 - 보유종목별 수익현황 조회"""
        if not self.token_manager.get_valid_token():
            logger.error("키움 API 토큰이 없습니다")
            return {"positions": [], "_data_source": "API_ERROR"}

        try:
            # API 제한 확인 및 기록
            if not api_rate_limiter.is_api_available():
                logger.warning("🚫 [KIWOOM_API] API 제한 상태로 인해 손익 조회 건너뜀")
                return {"positions": [], "_data_source": "API_ERROR"}
            
            # API 호출 기록 (간격 체크 포함)
            if not api_rate_limiter.record_api_call("get_account_profit"):
                logger.warning("🚫 [KIWOOM_API] API 호출 간격 부족으로 손익 조회 건너뜀")
                return {"positions": [], "_data_source": "API_ERROR"}
            
            use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
            host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
            url = host + "/api/dostk/acnt"

            headers = {
                'Content-Type': 'application/json;charset=UTF-8',
                'authorization': f'Bearer {self.token_manager.get_valid_token()}',
                'api-id': 'ka10085',
            }

            body = {
                'stex_tp': stex_tp,
            }

            positions: List[Dict] = []
            cont_yn, next_key = 'N', ''

            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                while True:
                    h = dict(headers)
                    h['cont-yn'] = cont_yn
                    h['next-key'] = next_key

                    async with session.post(url, headers=h, json=body) as resp:
                        text = await resp.text()
                        if resp.status != 200:
                            logger.error(f"ka10085 호출 실패: {resp.status} {text}")
                            break
                        try:
                            data = json.loads(text)
                        except json.JSONDecodeError:
                            logger.error(f"ka10085 JSON 파싱 실패: {text}")
                            break

                        if data.get('return_code') != 0:
                            logger.error(f"ka10085 오류: {data}")
                            break

                        for it in data.get('acnt_prft_rt', []) or []:
                            def _to_int(v: str) -> int:
                                try:
                                    if isinstance(v, str) and (v.startswith('+') or v.startswith('-')):
                                        return int(v.replace('+',''))
                                    return int(v)
                                except Exception:
                                    return 0

                            positions.append({
                                "stock_code": it.get('stk_cd', ''),
                                "stock_name": it.get('stk_nm', ''),
                                "quantity": _to_int(it.get('rmnd_qty', '0')),
                                "avg_price": _to_int(it.get('pur_pric', '0')),
                                "purchase_amount": _to_int(it.get('pur_amt', '0')),
                                "current_price_delta": _to_int(it.get('cur_prc', '0')),
                                "today_pl": _to_int(it.get('tdy_sel_pl', '0')),
                                "commission_today": _to_int(it.get('tdy_trde_cmsn', '0')),
                                "tax_today": _to_int(it.get('tdy_trde_tax', '0')),
                                "credit_type": it.get('crd_tp', ''),
                                "loan_date": it.get('loan_dt', ''),
                                "settle_remain": _to_int(it.get('setl_remn', '0')),
                            })
                            if len(positions) >= limit:
                                break

                        if len(positions) >= limit:
                            break

                        cont_yn = resp.headers.get('cont-yn', 'N')
                        next_key = resp.headers.get('next-key', '')
                        if cont_yn != 'Y' or not next_key:
                            break

            return {
                "positions": positions[:limit],
                "_data_source": "REAL_API",
                "_api_connected": True,
                "_token_valid": True,
            }

        except Exception as e:
            logger.error(f"계좌수익률 요청 오류: {e}")
            return {"positions": [], "_data_source": "API_ERROR"}

    async def place_buy_order(self, stock_code: str, quantity: int, price: int = 0, order_type: str = "3") -> Dict:
        """주식 매수 주문 (키움 API kt10000 스펙)"""
        if not self.token_manager.get_valid_token():
            logger.error("키움 API 토큰이 없습니다")
            return {"success": False, "error": "토큰 없음"}
            
        try:
            # 계좌 타입에 따른 도메인 설정
            use_mock_account = Config.KIWOOM_USE_MOCK_ACCOUNT
            if use_mock_account:
                host = Config.KIWOOM_MOCK_API_URL
            else:
                host = Config.KIWOOM_REAL_API_URL
            
            endpoint = '/api/dostk/ordr'
            url = host + endpoint

            # 계좌번호 (모의/실전 분기)
            account_no = Config.KIWOOM_MOCK_ACCOUNT_NUMBER if Config.KIWOOM_USE_MOCK_ACCOUNT else Config.KIWOOM_ACCOUNT_NUMBER
            if not account_no:
                logger.error("매수 주문 실패 - 계좌번호가 설정되지 않았습니다")
                return {"success": False, "error": "계좌번호 없음"}
            
            # appkey/appsecret (실전/모의 분기) - 일부 엔드포인트에서 필수일 수 있음
            app_key = Config.KIWOOM_MOCK_APP_KEY if use_mock_account else Config.KIWOOM_APP_KEY
            app_secret = Config.KIWOOM_MOCK_APP_SECRET if use_mock_account else Config.KIWOOM_APP_SECRET

            # 주문 요청 데이터 (kt10000 스펙)
            account_pw = Config.KIWOOM_MOCK_ACCOUNT_PASSWORD if use_mock_account else Config.KIWOOM_ACCOUNT_PASSWORD
            request_data = {
                'dmst_stex_tp': 'KRX',  # 국내거래소구분 KRX,NXT,SOR
                'acnt_no': account_no,  # 계좌번호
                'stk_cd': stock_code,   # 종목코드
                'ord_qty': str(quantity),  # 주문수량
                # 주문단가: API 구현에 따라 시장가에서도 "0"을 요구하는 경우가 있어 0으로 고정
                'ord_uv': str(price if price > 0 else 0),
                'trde_tp': order_type,  # 매매구분 (3:시장가, 0:보통)
                'cond_uv': '0',  # 조건단가
                'ord_side_cd': '1',  # 매수 주문 (1:매수, 2:매도)
            }
            # 계좌 비밀번호가 설정된 경우만 포함(필수인 환경에서 RC4058 해결 가능)
            if account_pw:
                request_data['acnt_pwd'] = account_pw
                request_data['acnt_pw'] = account_pw

            order_kind = "시장가" if order_type == "3" else "지정가"
            logger.info(
                f"매수 주문 요청: {stock_code}, 수량: {quantity}, "
                f"가격: {price}, 타입: {order_type}({order_kind})"
            )

            async def _do_request() -> tuple[int, Optional[dict], str]:
                headers = {
                    'Content-Type': 'application/json;charset=UTF-8',
                    'authorization': f'Bearer {self.token_manager.get_valid_token()}',
                    'appkey': app_key,
                    'appsecret': app_secret,
                    'cont-yn': 'N',
                    'next-key': '',
                    'api-id': 'kt10000',
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url,
                        headers=headers,
                        json=request_data,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as response:
                        response_text = await response.text()
                        if response.status != 200:
                            return response.status, None, response_text
                        try:
                            return response.status, json.loads(response_text), response_text
                        except json.JSONDecodeError:
                            return response.status, None, response_text

            for attempt in range(2):
                resp_status, data, response_text = await _do_request()
                logger.info(f"매수 주문 응답: {resp_status} - {response_text}")

                if resp_status == 200 and data:
                    return_code = data.get("return_code")
                    rt_cd = data.get("rt_cd")
                    success = (return_code == 0) or (rt_cd == "0")

                    if success:
                        order_no = data.get('ord_no', '') or data.get("order_no", "")
                        logger.info(f"매수 주문 성공: {stock_code} - 주문번호: {order_no}")
                        return {
                            "success": True,
                            "order_id": order_no,
                            "order_no": order_no,
                            "message": data.get('return_msg') or data.get("msg1") or '정상적으로 처리되었습니다',
                        }

                    error_msg = data.get('return_msg') or data.get("msg1") or '알 수 없는 오류'
                    if attempt == 0 and self._is_kiwoom_token_invalid(error_msg, data):
                        logger.warning("🔑 [TOKEN] 매수 주문 토큰 무효(8005) — 재인증 후 1회 재시도")
                        if await self._reauthenticate_async():
                            continue
                    logger.error(f"매수 주문 실패: {error_msg}")
                    return {
                        "success": False,
                        "error": error_msg,
                        "_request": request_data,
                        "_response": data,
                    }

                err = response_text or f"HTTP {resp_status}"
                if attempt == 0 and self._is_kiwoom_token_invalid(err, data):
                    logger.warning("🔑 [TOKEN] 매수 주문 HTTP 오류 중 토큰 무효 — 재인증 후 1회 재시도")
                    if await self._reauthenticate_async():
                        continue
                logger.error(f"매수 주문 API 호출 실패: {resp_status}")
                return {
                    "success": False,
                    "error": f"API 호출 실패: {resp_status}" if resp_status != 200 else "응답 파싱 실패",
                }
                        
        except Exception as e:
            logger.error(f"매수 주문 중 오류: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def place_sell_order(self, stock_code: str, quantity: int, price: int = 0, order_type: str = "3") -> Dict:
        """주식 매도 주문 (키움 API kt10001 스펙 — kt10000은 매수 전용)."""
        if not self.token_manager.get_valid_token():
            logger.error("키움 API 토큰이 없습니다")
            return {"success": False, "error": "토큰 없음"}

        stock_code = self.normalize_stock_code(stock_code)
            
        try:
            # 계좌 타입에 따른 도메인 설정
            use_mock_account = Config.KIWOOM_USE_MOCK_ACCOUNT
            if use_mock_account:
                host = Config.KIWOOM_MOCK_API_URL
            else:
                host = Config.KIWOOM_REAL_API_URL
            
            endpoint = '/api/dostk/ordr'
            url = host + endpoint

            # 계좌번호 (모의/실전 분기)
            account_no = Config.KIWOOM_MOCK_ACCOUNT_NUMBER if Config.KIWOOM_USE_MOCK_ACCOUNT else Config.KIWOOM_ACCOUNT_NUMBER
            if not account_no:
                logger.error("매도 주문 실패 - 계좌번호가 설정되지 않았습니다")
                return {"success": False, "error": "계좌번호 없음"}
            
            # appkey/appsecret (실전/모의 분기) - 일부 엔드포인트에서 필수일 수 있음
            app_key = Config.KIWOOM_MOCK_APP_KEY if use_mock_account else Config.KIWOOM_APP_KEY
            app_secret = Config.KIWOOM_MOCK_APP_SECRET if use_mock_account else Config.KIWOOM_APP_SECRET

            # 요청 헤더 (kt10001 = 매도 주문)
            headers = {
                'Content-Type': 'application/json;charset=UTF-8',
                'authorization': f'Bearer {self.token_manager.get_valid_token()}',
                'appkey': app_key,
                'appsecret': app_secret,
                'cont-yn': 'N',  # 연속조회여부
                'next-key': '',  # 연속조회키
                'api-id': 'kt10001',  # TR명 (매도)
            }
            
            # 주문 요청 데이터 (kt10001 스펙)
            account_pw = Config.KIWOOM_MOCK_ACCOUNT_PASSWORD if use_mock_account else Config.KIWOOM_ACCOUNT_PASSWORD
            request_data = {
                'dmst_stex_tp': 'KRX',  # 국내거래소구분 KRX,NXT,SOR
                'acnt_no': account_no,  # 계좌번호
                'stk_cd': stock_code,   # 종목코드
                'ord_qty': str(quantity),  # 주문수량
                'ord_uv': str(price if price > 0 else 0),
                'trde_tp': order_type,  # 매매구분 (3:시장가, 0:보통)
                'cond_uv': '0',  # 조건단가
            }
            if account_pw:
                request_data['acnt_pwd'] = account_pw
                request_data['acnt_pw'] = account_pw
            
            logger.info(f"매도 주문 요청: {stock_code}, 수량: {quantity}, 가격: {price}")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=request_data) as response:
                    if response.status == 200:
                        try:
                            response_text = await response.text()
                            logger.info(f"매도 주문 응답: {response.status} - {response_text}")

                            response_data = json.loads(response_text)

                            # 키움 응답 키가 버전에 따라 다를 수 있어 둘 다 허용
                            return_code = response_data.get("return_code")
                            rt_cd = response_data.get("rt_cd")
                            success = (return_code == 0) or (rt_cd == "0")

                            if success:
                                order_no = response_data.get("ord_no", "") or response_data.get("order_no", "")
                                return {
                                    "success": True,
                                    "order_id": order_no,
                                    "order_no": order_no,
                                    "message": response_data.get("return_msg") or response_data.get("msg1") or "매도 주문이 성공적으로 접수되었습니다."
                                }

                            error_msg = response_data.get("return_msg") or response_data.get("msg1") or "매도 주문 실패"
                            logger.error(f"매도 주문 실패: {error_msg}")
                            return {
                                "success": False,
                                "error": error_msg,
                                "_request": request_data,
                                "_response": response_data,
                            }
                        except json.JSONDecodeError as e:
                            logger.error(f"매도 주문 응답 파싱 실패: {e}")
                            return {
                                "success": False,
                                "error": "응답 파싱 실패"
                            }
                    else:
                        logger.error(f"매도 주문 API 호출 실패: {response.status}")
                        return {
                            "success": False,
                            "error": f"API 호출 실패: {response.status}"
                        }
                        
        except Exception as e:
            logger.error(f"매도 주문 중 오류: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def get_account_balance(
        self,
        account_number: str = None,
        *,
        priority: Optional[APIPriority] = None,
        max_wait: float = 8.0,
    ) -> Dict:
        """계좌 잔고 정보 조회 - 키움 API kt00004 (동시 호출은 하나로 합침).

        매수/손절 등 주문 경로에서는 priority=HIGH·max_wait를 넉넉히 넘겨
        스캐너 LOW 트래픽에 밀려 '계좌 정보 조회 실패'로 매수가 지연되지 않게 한다.
        """
        now = time.monotonic()
        cached = _account_balance_cache.get("data")
        if cached and now - _account_balance_cache.get("at", 0.0) < _ACCOUNT_BALANCE_FRESH_SEC:
            out = dict(cached)
            out["_cached"] = True
            return out

        lock = _get_balance_fetch_lock()
        async with lock:
            now = time.monotonic()
            cached = _account_balance_cache.get("data")
            if cached and now - _account_balance_cache.get("at", 0.0) < _ACCOUNT_BALANCE_FRESH_SEC:
                out = dict(cached)
                out["_cached"] = True
                return out

            global _balance_inflight
            if _balance_inflight is not None and not _balance_inflight.done():
                task = _balance_inflight
            else:
                task = asyncio.create_task(
                    self._fetch_account_balance(
                        account_number,
                        priority=priority,
                        max_wait=max_wait,
                    )
                )
                _balance_inflight = task

        try:
            return await task
        finally:
            async with lock:
                if _balance_inflight is task:
                    _balance_inflight = None

    async def _fetch_account_balance(
        self,
        account_number: str = None,
        *,
        priority: Optional[APIPriority] = None,
        max_wait: float = 8.0,
    ) -> Dict:
        """계좌 잔고 실제 조회 (get_account_balance에서 단일 inflight로만 호출)."""
        if not self.token_manager.get_valid_token():
            stale = _stale_account_balance("token_missing")
            if stale:
                return stale
            logger.error("키움 API 토큰이 없습니다")
            return {"_error": "no_token", "_error_msg": "토큰 없음"}

        slot_priority = priority if priority is not None else APIPriority.NORMAL
        slot_wait = max(1.0, float(max_wait or 8.0))
        try:
            if not await self._acquire_api_slot(
                "get_account_balance",
                max_wait=slot_wait,
                priority=slot_priority,
            ):
                stale = _stale_account_balance("rate_limit")
                if stale:
                    return stale
                return {"_error": "rate_limited", "_error_msg": "API 호출 제한 또는 슬롯 대기 초과"}

            # 계좌번호 설정 (매개변수 우선, 없으면 환경변수 사용)
            if not account_number:
                account_number = Config.KIWOOM_MOCK_ACCOUNT_NUMBER if Config.KIWOOM_USE_MOCK_ACCOUNT else Config.KIWOOM_ACCOUNT_NUMBER

            if not account_number:
                logger.error("계좌번호가 설정되지 않았습니다 (KIWOOM_ACCOUNT_NUMBER / KIWOOM_MOCK_ACCOUNT_NUMBER)")
                return {}
            
            # 계좌 타입에 따른 도메인 설정
            use_mock_account = Config.KIWOOM_USE_MOCK_ACCOUNT
            if use_mock_account:
                # 모의투자 계좌인 경우
                host = Config.KIWOOM_MOCK_API_URL  # 모의투자용
                account_type = "모의투자"
            else:
                # 실계좌인 경우
                host = Config.KIWOOM_REAL_API_URL  # 실전투자용
                account_type = "실계좌"
            
            endpoint = '/api/dostk/acnt'
            url = host + endpoint
            
            logger.info(f"계좌 타입: {account_type}, 도메인: {host}")
            
            # 요청 데이터 - 참고 소스와 동일하게 수정
            request_data = {
                'qry_tp': '0',         # 상장폐지조회구분 0:전체, 1:상장폐지종목제외
                'dmst_stex_tp': 'KRX', # 국내거래소구분 KRX:한국거래소,NXT:넥스트트레이드
                'acnt_no': account_number,  # 계좌번호 추가
            }
            # 디버깅을 위한 로깅 추가
            logger.info(f"키움 API 호출 ({account_type}): {url}")
            logger.info(f"계좌번호: {account_number}")
            logger.info(f"앱키 존재: {bool(Config.KIWOOM_APP_KEY)}")
            
            def _is_token_invalid(error_msg: str, data: dict) -> bool:
                msg = (error_msg or "") + " " + str(data or "")
                msg_lower = msg.lower()
                # 예: '인증에 실패했습니다[8005:Token이 유효하지 않습니다]'
                return ("8005" in msg_lower) or ("token" in msg_lower and ("invalid" in msg_lower or "유효하지" in msg_lower))

            async def _do_request() -> tuple[int, dict | None, str]:
                # 요청 헤더(토큰은 매 시도마다 최신 값으로 넣어야 함)
                headers = {
                    "Content-Type": "application/json;charset=UTF-8",
                    "authorization": f"Bearer {self.token_manager.get_valid_token()}",
                    "cont-yn": "N",        # 연속조회여부
                    "next-key": "",        # 연속조회키
                    "api-id": "kt00004",   # TR명
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url,
                        headers=headers,
                        json=request_data,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as response:
                        response_text = await response.text()
                        logger.debug(f"계좌 조회 응답 상태: {response.status}")
                        if response.status != 200:
                            return response.status, None, response_text
                        try:
                            return response.status, json.loads(response_text), response_text
                        except json.JSONDecodeError as e:
                            return response.status, None, response_text

            # 1회 시도 + 토큰 무효(8005)면 1회 재인증 후 재시도
            for attempt in range(2):
                resp_status, data, raw_text = await _do_request()
                if resp_status != 200 or not data:
                    logger.error(f"키움 API 호출 실패: status={resp_status}")
                    if raw_text:
                        logger.error(f"오류 응답: {raw_text}")
                    stale = _stale_account_balance("http_or_json_error")
                    if stale:
                        return stale
                    return {"_error": "http_or_json_error", "_error_msg": f"HTTP {resp_status}"}

                return_code = data.get("return_code")
                logger.debug(f"계좌 조회 return_code: {return_code}")
                if return_code == 0:
                    result = self._parse_account_balance_safe(data)
                    global _account_balance_cache
                    _account_balance_cache = {"at": time.monotonic(), "data": result}
                    return result

                error_msg = data.get("msg1") or "알 수 없는 오류"
                logger.error(f"키움 API 계좌조회 오류: {error_msg}")
                logger.error(f"전체 응답: {data}")

                if attempt == 0 and _is_token_invalid(error_msg, data):
                    logger.warning("🔑 [TOKEN] 토큰 무효(8005) 감지 — 토큰 재인증 후 1회 재시도")
                    # 토큰 무효화 → 재인증
                    self.token_manager.access_token = None
                    self.token_manager.token_expiry = None
                    self.token_manager.refresh_token = self.token_manager.refresh_token  # 유지
                    if not self.authenticate():
                        logger.error("토큰 재인증 실패")
                        break

                    # 재시도 전에 슬롯/레이트리밋 다시 확인
                    if not await self._acquire_api_slot(
                        "get_account_balance",
                        max_wait=min(slot_wait, 8.0),
                        priority=slot_priority,
                    ):
                        break
                    continue

                stale = _stale_account_balance("api_error")
                if stale:
                    return stale
                return {"_error": "api_error", "_error_msg": error_msg}

        except aiohttp.ClientError as e:
            logger.error(f"HTTP 클라이언트 오류: {e}")
            stale = _stale_account_balance("client_error")
            if stale:
                return stale
            return {"_error": "client_error", "_error_msg": str(e)}
        except asyncio.TimeoutError:
            logger.error("키움 API 호출 타임아웃")
            stale = _stale_account_balance("timeout")
            if stale:
                return stale
            return {"_error": "timeout", "_error_msg": "키움 API 타임아웃"}
        except Exception as e:
            logger.error(f"계좌 정보 조회 중 예상치 못한 오류: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"스택 트레이스: {traceback.format_exc()}")
            stale = _stale_account_balance("unexpected")
            if stale:
                return stale
            return {"_error": "unexpected", "_error_msg": str(e)}
    
    def _parse_account_balance_safe(self, api_response: dict) -> dict:
        """키움 API 계좌 잔고 응답 파싱 - kt00004 필드명 정규화"""
        try:
            logger.debug("계좌 응답 파싱 시작")

            def amt_str(raw) -> str:
                return str(_parse_kiwoom_int(raw))

            def map_holding(item: dict) -> dict:
                if not isinstance(item, dict):
                    return {}
                return {
                    "stk_cd": str(item.get("stk_cd") or ""),
                    "stk_nm": str(item.get("stk_nm") or ""),
                    "qty": amt_str(item.get("rmnd_qty") or item.get("qty", "0")),
                    "pur_amt": amt_str(item.get("pur_amt", "0")),
                    "evlt_amt": amt_str(item.get("evlt_amt", "0")),
                    "lspft_amt": amt_str(item.get("pl_amt") or item.get("lspft_amt", "0")),
                    "lspft_rt": _parse_kiwoom_rate(item.get("pl_rt") or item.get("lspft_rt", "0")),
                    "cur_pr": amt_str(item.get("cur_prc") or item.get("cur_pr", "0")),
                    "avg_pr": amt_str(item.get("avg_prc") or item.get("avg_pr", "0")),
                }

            stk_acnt_evlt_prst = []
            stk_data = api_response.get("stk_acnt_evlt_prst")
            if isinstance(stk_data, list):
                stk_acnt_evlt_prst = [map_holding(item) for item in stk_data if isinstance(item, dict)]
            elif isinstance(stk_data, dict):
                mapped = map_holding(stk_data)
                if mapped.get("stk_cd"):
                    stk_acnt_evlt_prst = [mapped]

            amount_fields = (
                "entr", "d2_entra", "tot_est_amt", "aset_evlt_amt", "tot_pur_amt",
                "prsm_dpst_aset_amt", "tot_grnt_sella", "tdy_lspft_amt", "invt_bsamt",
                "lspft_amt", "tdy_lspft", "lspft2", "lspft",
            )
            result = {
                "acnt_nm": str(api_response.get("acnt_nm") or ""),
                "brch_nm": str(api_response.get("brch_nm") or ""),
                "tdy_lspft_rt": _parse_kiwoom_rate(api_response.get("tdy_lspft_rt", "0")),
                "lspft_ratio": _parse_kiwoom_rate(api_response.get("lspft_ratio", "0")),
                "lspft_rt": _parse_kiwoom_rate(api_response.get("lspft_rt", "0")),
                "stk_acnt_evlt_prst": stk_acnt_evlt_prst,
            }
            for key in amount_fields:
                result[key] = amt_str(api_response.get(key, "0"))

            # 계좌 레벨 손익이 0이면 보유종목 pl_amt 합산
            sum_pl = sum(_parse_kiwoom_int(h.get("lspft_amt")) for h in stk_acnt_evlt_prst)
            if _parse_kiwoom_int(result.get("lspft_amt")) == 0 and sum_pl != 0:
                result["lspft_amt"] = str(sum_pl)
                result["lspft"] = str(sum_pl)
                tot_pur = _parse_kiwoom_int(result.get("tot_pur_amt"))
                if tot_pur > 0:
                    result["lspft_rt"] = f"{sum_pl / tot_pur * 100:.2f}"

            aset_amt = _parse_kiwoom_int(result["aset_evlt_amt"])
            tot_amt = _parse_kiwoom_int(result["tot_est_amt"])
            pl_amt = _parse_kiwoom_int(result["lspft_amt"])
            logger.info(
                f"계좌 파싱 완료 - 보유 {len(stk_acnt_evlt_prst)}개, "
                f"예수금 {_parse_kiwoom_int(result['entr']):,}원, "
                f"평가손익 {pl_amt:+,}원, 자산평가 {aset_amt:,}원"
            )
            return result

        except Exception as e:
            logger.error(f"계좌 잔고 응답 파싱 오류: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"스택 트레이스: {traceback.format_exc()}")
            return {}


class ConditionSearchSession:
    """조건식 WS 1연결로 여러 CNSRREQ — LOGIN 1회.

    전역 락 + 메인 WS 일시 중지로 동시 연결 Bye를 방지한다.
    """

    def __init__(self, api: KiwoomAPI):
        self._api = api
        self._websocket = None
        self._lock_held = False
        self._main_ws_state: Optional[dict] = None

    async def __aenter__(self):
        await _get_condition_ws_lock().acquire()
        self._lock_held = True
        try:
            self._main_ws_state = await self._api._suspend_main_websocket()
            self._websocket = await self._api._open_condition_websocket()
        except Exception:
            try:
                await self._api._resume_main_websocket(self._main_ws_state)
            finally:
                self._main_ws_state = None
                _get_condition_ws_lock().release()
                self._lock_held = False
            raise
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if self._websocket:
                try:
                    await self._websocket.close()
                except Exception:
                    pass
                self._websocket = None
        finally:
            try:
                await self._api._resume_main_websocket(self._main_ws_state)
            finally:
                self._main_ws_state = None
                if self._lock_held:
                    _get_condition_ws_lock().release()
                    self._lock_held = False

    async def _reopen(self) -> None:
        if self._websocket:
            try:
                await self._websocket.close()
            except Exception:
                pass
            self._websocket = None
        self._websocket = await self._api._open_condition_websocket()

    async def search(self, condition_id: str, condition_name: str) -> List[Dict]:
        cached = _get_cached_condition_search(condition_id, condition_name)
        if cached is not None:
            logger.debug(f"조건식 검색 캐시 사용: {condition_name} ({len(cached)}개)")
            return cached

        last_err: Optional[BaseException] = None
        for attempt in range(1, _CONDITION_SEARCH_MAX_ATTEMPTS + 1):
            try:
                if not self._websocket:
                    self._websocket = await self._api._open_condition_websocket()
                stocks = await self._api._search_condition_on_websocket(
                    self._websocket, condition_id, condition_name,
                )
                _set_cached_condition_search(condition_id, condition_name, stocks)
                return stocks
            except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed) as e:
                last_err = e
                logger.warning(
                    f"조건식 검색 재시도 ({attempt}/{_CONDITION_SEARCH_MAX_ATTEMPTS}): "
                    f"{condition_name} — {type(e).__name__}: {e!r}"
                )
            except Exception as e:
                last_err = e
                logger.warning(
                    f"조건식 검색 오류 재시도 ({attempt}/{_CONDITION_SEARCH_MAX_ATTEMPTS}): "
                    f"{condition_name} — {type(e).__name__}: {e!r}"
                )
            if attempt >= _CONDITION_SEARCH_MAX_ATTEMPTS:
                break
            await asyncio.sleep(0.4 * attempt)
            try:
                await self._reopen()
            except Exception as e:
                last_err = e
                logger.warning(f"조건식 WS 재연결 실패: {type(e).__name__}: {e!r}")

        stale = _get_cached_condition_search(
            condition_id, condition_name, allow_stale=True,
        )
        if stale is not None:
            logger.warning(
                f"조건식 검색 실패 → stale 캐시 사용: {condition_name} ({len(stale)}개)"
            )
            return stale
        if last_err:
            raise last_err
        return []


# 전역 인스턴스
kiwoom_api = KiwoomAPI()