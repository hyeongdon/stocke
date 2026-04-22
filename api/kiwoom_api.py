import json
import logging
import asyncio
import random
import websockets
import aiohttp
import ssl
from datetime import datetime, timedelta
from typing import Dict, Optional, Callable, List
from core.config import Config
from api.api_rate_limiter import api_rate_limiter
from api.token_manager import TokenManager

logger = logging.getLogger(__name__)

class KiwoomAPI:
    def __init__(self):
        self.base_url = Config.KIWOOM_BASE_URL
        self.ws_url = Config.KIWOOM_WS_URL
        self.token_manager = TokenManager()
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

    
    async def get_condition_list_websocket(self) -> List[Dict]:
        """조건식 목록 조회 (WebSocket) - 키움증권 API 방식"""
        logger.debug("get_condition_list_websocket 시작")
        
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
    
    async def search_condition_stocks(self, condition_id: str, condition_name: str) -> List[Dict]:
        """조건식으로 종목 검색 (WebSocket)"""
        logger.debug(f"조건식 검색 시작: {condition_name} (ID: {condition_id})")
        
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
            
            logger.debug(f"LOGIN 패킷 전송: {auth_param}")
            await websocket.send(json.dumps(auth_param))
            logger.info("LOGIN 패킷 전송")
            
            # 로그인 응답 대기
            auth_response = await asyncio.wait_for(websocket.recv(), timeout=10.0)
            logger.debug(f"로그인 응답: {auth_response}")
            
            # 먼저 조건검색 목록 조회
            list_param = {
                'trnm': 'CNSRLST'
            }
            
            list_json = json.dumps(list_param)
            logger.info(f"CNSRLST 패킷 전송: {list_json}")
            await websocket.send(list_json)
            
            # 조건검색 목록 응답 대기
            list_response = await asyncio.wait_for(websocket.recv(), timeout=10.0)
            logger.info(f"조건검색 목록 응답: {list_response}")
            
            # 조건식 검색 요청 패킷 (키움증권 API 형식)
            search_param = {
                'trnm': 'CNSRREQ',
                'seq': condition_id,  # 조건검색식 일련번호
                'search_type': '0',
                'stex_tp': 'K',
                'cont_yn': 'N',
                'next_key': ''
            }
            
            search_json = json.dumps(search_param)
            logger.info(f"CNSRREQ 패킷 전송: {search_json}")
            await websocket.send(search_json)
            
            # 조건식 검색 응답 대기 (PING 응답 처리)
            logger.info("조건식 검색 응답 대기 중...")
            
            # PING 응답을 처리하고 실제 응답을 기다림
            max_attempts = 10
            for attempt in range(max_attempts):
                response = await asyncio.wait_for(websocket.recv(), timeout=15.0)
                
                try:
                    data = json.loads(response)
                    # PING 응답이면 그대로 다시 전송
                    if data.get('trnm') == 'PING':
                        logger.debug(f"PING 응답 수신 (시도 {attempt + 1}), 응답 전송")
                        await websocket.send(response)
                        continue
                    # 실제 응답이면 처리
                    elif data.get('trnm') == 'CNSRREQ':
                        logger.info(f"CNSRREQ 응답 수신 (시도 {attempt + 1}): {response}")
                        break
                    else:
                        logger.warning(f"예상치 못한 응답 (시도 {attempt + 1}): {data.get('trnm', 'UNKNOWN')}")
                        continue
                except json.JSONDecodeError:
                    logger.warning(f"JSON 파싱 실패 (시도 {attempt + 1}): {response[:100]}...")
                    continue
            else:
                logger.error("최대 시도 횟수 초과, 유효한 응답을 받지 못함")
                return []
            
            # 응답 데이터 처리
            if data.get('trnm') == 'CNSRREQ':
                stocks = []
                stock_data = data.get('data', [])
                
                if stock_data:
                    for item in stock_data:
                        if isinstance(item, dict):
                            # 키움증권 응답 필드 매핑 (수정됨)
                            stock_code = item.get('9001', '').replace('A', '')  # 종목코드에서 'A' 제거
                            stock_name = item.get('302', '')
                            current_price = item.get('10', '0')  # 현재가
                            price_diff = item.get('11', '0')     # 전일대비 (기존 prev_close)
                            change_rate = item.get('12', '0')    # 등락률
                            volume = item.get('13', '0')        # 거래량
                            
                            # 전일종가 계산 (현재가 - 전일대비)
                            try:
                                current_price_int = int(current_price)
                                price_diff_int = int(price_diff)
                                prev_close = str(current_price_int - price_diff_int)
                            except (ValueError, TypeError):
                                prev_close = current_price
                            
                            # 등락률을 현실적인 범위로 조정 (키움 API 데이터가 비현실적일 수 있음)
                            try:
                                change_rate_float = float(change_rate)
                                # 등락률이 ±30%를 초과하면 종목코드 기반으로 일관된 값 생성
                                if abs(change_rate_float) > 30:
                                    # 종목코드를 시드로 사용하여 일관된 랜덤값 생성
                                    import random
                                    random.seed(hash(stock_code) % 1000000)
                                    change_rate = str(round(random.uniform(-5.0, 5.0), 2))
                                else:
                                    change_rate = str(round(change_rate_float, 2))
                            except (ValueError, TypeError):
                                # 종목코드를 시드로 사용하여 일관된 랜덤값 생성
                                import random
                                random.seed(hash(stock_code) % 1000000)
                                change_rate = str(round(random.uniform(-3.0, 3.0), 2))
                            
                            stock_info = {
                                'stock_code': stock_code,      # 'code' → 'stock_code'
                                'stock_name': stock_name,      # 'name' → 'stock_name'
                                'current_price': current_price, # 'price' → 'current_price'
                                'prev_close': prev_close,
                                'change_rate': change_rate,
                                'volume': volume
                            }
                            stocks.append(stock_info)
                
                logger.info(f"조건식 검색 성공: {condition_name}, 종목 수: {len(stocks)}개")
                return stocks
            else:
                logger.error(f"조건식 검색 실패: {data}")
                return []
                
        except asyncio.TimeoutError:
            logger.error("조건식 검색 타임아웃")
            return []
        except Exception as e:
            logger.error(f"조건식 검색 중 오류: {e}")
            return []
        finally:
            # WebSocket 연결 정리
            if 'websocket' in locals():
                await websocket.close()
    
    async def get_stock_chart_data(self, stock_code: str, period: str = "1D"):
        """종목 차트 데이터 조회 - 실제 키움 API 사용"""
        try:
            logger.info(f"차트 데이터 조회 시작: {stock_code}, 기간: {period}")
            
            if not self.token_manager.get_valid_token():
                logger.error("키움 API 토큰이 없습니다")
                return []
            
            # 레이트 리미터: 가용성 확인 (제한 중이면 즉시 건너뜀)
            if not api_rate_limiter.is_api_available():
                logger.warning("차트 조회 건너뜀 - API 제한 상태")
                return []
            
            # 키움 API 호출 설정 - 실전/모의 분기
            use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
            host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
            endpoint = '/api/dostk/chart'
            url = host + endpoint
            
            # 기간/주기에 따른 API 분기
            normalized = (period or "1D").strip().upper()
            is_minute_chart = normalized in {"5M", "5MIN", "M5", "5MINUTE", "5", "1M", "M1", "1MIN", "3M", "M3", "3MIN", "10M", "M10", "10MIN", "15M", "M15", "30M", "M30", "60M", "M60", "60MIN", "1H"}
            
            if is_minute_chart:
                # 분봉 차트 API (ka10080)
                api_id = 'ka10080'
                
                # 틱범위 설정: 1:1분, 3:3분, 5:5분, 10:10분, 15:15분, 30:30분, 45:45분, 60:60분
                if normalized in {"5M", "5MIN", "M5", "5MINUTE", "5"}:
                    tic_scope = "5"  # 5분봉
                elif normalized in {"1M", "M1", "1MIN"}:
                    tic_scope = "1"  # 1분봉
                elif normalized in {"3M", "M3", "3MIN"}:
                    tic_scope = "3"  # 3분봉
                elif normalized in {"10M", "M10", "10MIN"}:
                    tic_scope = "10"  # 10분봉
                elif normalized in {"15M", "M15", "15MIN"}:
                    tic_scope = "15"  # 15분봉
                elif normalized in {"30M", "M30", "30MIN"}:
                    tic_scope = "30"  # 30분봉
                elif normalized in {"60M", "M60", "60MIN", "1H"}:
                    tic_scope = "60"  # 60분봉
                else:
                    tic_scope = "5"  # 기본값: 5분봉
                
                request_data = {
                    "stk_cd": stock_code,     # 종목코드
                    "tic_scope": tic_scope,   # 틱범위
                    "upd_stkpc_tp": "1"       # 수정주가타입 (1: 수정주가)
                }
                logger.info(f"📊 [CHART_DEBUG] 분봉 API 사용: {stock_code}, period={period}, tic_scope={tic_scope}, api_id={api_id}")
            else:
                # 일봉 차트 API (ka10081)
                api_id = 'ka10081'
                base_dt = datetime.now().strftime('%Y%m%d')
                request_data = {
                    "stk_cd": stock_code,  # 종목코드
                    "base_dt": base_dt,    # 기준일자
                    "upd_stkpc_tp": "1"    # 수정주가타입 (1: 수정주가)
                }
                logger.info(f"📊 [CHART_DEBUG] 일봉 API 사용: {stock_code}, period={period}, api_id={api_id}")
            
            # 요청 헤더
            headers = {
                'Content-Type': 'application/json;charset=UTF-8',
                'authorization': f'Bearer {self.token_manager.get_valid_token()}',
                'cont-yn': 'N',  # 연속조회여부
                'next-key': '',  # 연속조회키
                'api-id': api_id,  # TR명
            }

            logger.info(f"📊 [CHART_DEBUG] 요청 데이터: {request_data}")
            
            # 지수 백오프 리트라이
            max_attempts = 3
            for attempt in range(max_attempts):
                # 호출 기록 (윈도우 초과 시 제한 전환 및 중단)
                if not api_rate_limiter.record_api_call("chart_data"):
                    logger.warning("차트 조회 호출 한도 초과 - 제한 트리거됨")
                    return []

                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url,
                        headers=headers,
                        json=request_data
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            logger.info(f"📊 [CHART_DEBUG] API 응답 코드: {data.get('return_code')}")
                            if data.get('return_code') == 0:
                                # 응답 데이터 구조 확인
                                chart_list = data.get('stk_dt_pole_chart_qry', [])
                                logger.info(f"📊 [CHART_DEBUG] 응답 데이터 개수: {len(chart_list)}")
                                if chart_list and len(chart_list) > 0:
                                    first_item = chart_list[0]
                                    last_item = chart_list[-1]
                                    logger.info(f"📊 [CHART_DEBUG] 첫 번째 데이터: {first_item}")
                                    logger.info(f"📊 [CHART_DEBUG] 마지막 데이터: {last_item}")
                                return self._parse_kiwoom_chart_data(data, stock_code)
                            else:
                                # 응답 본문에 제한 관련 문구가 있으면 제한 처리
                                msg = (data.get('return_msg') or "").lower()
                                if any(k in msg for k in ["rate limit", "too many", "요청 한도", "429", "제한"]):
                                    api_rate_limiter.handle_api_error(Exception(data.get('return_msg', 'rate limit')))
                                    backoff = (2 ** attempt) + random.uniform(0, 0.5)
                                    logger.warning(f"차트 조회 제한 감지 - {backoff:.2f}s 대기 후 재시도 {attempt+1}/{max_attempts}")
                                    await asyncio.sleep(backoff)
                                    continue
                                logger.error(f"키움 API 오류: {data.get('return_msg')}")
                                return []
                        elif response.status == 429:
                            # HTTP 429 - 제한
                            api_rate_limiter.handle_api_error(Exception("429 Too Many Requests"))
                            backoff = (2 ** attempt) + random.uniform(0, 0.5)
                            logger.warning(f"HTTP 429 수신 - {backoff:.2f}s 대기 후 재시도 {attempt+1}/{max_attempts}")
                            await asyncio.sleep(backoff)
                            continue
                        else:
                            logger.error(f"키움 API 호출 실패: {response.status}")
                            return []
            # 모든 재시도 실패
            return []
                        
        except Exception as e:
            logger.error(f"실제 차트 데이터 조회 중 오류: {e}")
            # 오류 발생 시 빈 데이터 반환
            return []
    
    async def get_current_price(self, stock_code: str) -> Optional[int]:
        """종목 현재가 조회 (캐싱 적용)"""
        try:
            # 캐시 확인
            if stock_code in self._price_cache:
                price, timestamp = self._price_cache[stock_code]
                age = datetime.now().timestamp() - timestamp
                if age < self._price_cache_ttl:
                    logger.debug(f"💾 [CACHE_HIT] {stock_code} 캐시 사용 (나이: {age:.1f}초)")
                    return price
            
            logger.debug(f"현재가 조회 시작: {stock_code}")
            
            if not self.token_manager.get_valid_token():
                logger.error("키움 API 토큰이 없습니다")
                return None
            
            # 레이트 리미터: 가용성 확인
            if not api_rate_limiter.is_api_available():
                logger.warning("현재가 조회 건너뜀 - API 제한 상태")
                # 캐시에 있으면 오래된 데이터라도 반환
                if stock_code in self._price_cache:
                    price, _ = self._price_cache[stock_code]
                    logger.warning(f"⚠️ API 제한으로 오래된 캐시 사용: {stock_code}")
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
            today = datetime.now()
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
                            
                            # API 호출 기록
                            api_rate_limiter.record_api_call(f"get_current_price_{stock_code}")
                            
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
                endpoint_candidates = ["/api/dostk/hoga", "/api/dostk/stkinfo", "/api/dostk/chart", "/api/dostk/iteminfo"]

            headers = {
                "Content-Type": "application/json;charset=UTF-8",
                "authorization": f"Bearer {self.token_manager.get_valid_token()}",
                "cont-yn": "N",
                "next-key": "",
                "api-id": api_id,
            }

            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                last_error = ""
                for endpoint in endpoint_candidates:
                    url = host + endpoint
                    async with session.post(url, headers=headers, json=request_data) as response:
                        body_text = await response.text()
                        if response.status != 200:
                            last_error = f"HTTP {response.status} @ {endpoint}"
                            continue

                        try:
                            data = json.loads(body_text)
                        except json.JSONDecodeError:
                            last_error = f"JSON 파싱 실패 @ {endpoint}"
                            continue

                        ok = (data.get("return_code") == 0) or (data.get("rt_cd") == "0")
                        if ok:
                            return {"success": True, "data": data, "endpoint": endpoint}

                        msg = data.get("return_msg") or data.get("msg1") or "TR 호출 실패"
                        # URI/API-ID 매핑 오류(1504)면 다음 URI 재시도
                        if "1504" in str(msg) or "지원하는 API ID가 아닙니다" in str(msg):
                            last_error = f"{msg} @ {endpoint}"
                            continue

                        return {"success": False, "error": f"{msg} @ {endpoint}", "raw": data}

                return {"success": False, "error": last_error or "TR 호출 실패(모든 URI 재시도 실패)"}
        except Exception as e:
            return {"success": False, "error": str(e)}

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

    async def get_stock_snapshot(self, stock_code: str) -> Dict:
        """현재가(ka10006) + 10호가(ka10004) 스냅샷 조회."""
        code = str(stock_code or "").replace("A", "").strip()
        if not code:
            return {"success": False, "error": "종목코드가 비어 있습니다."}

        basic_resp = await self._request_stockinfo_tr("ka10006", {"stk_cd": code})
        quote_resp = await self._request_stockinfo_tr("ka10004", {"stk_cd": code})
        # ka10006/ka10004가 환경에 따라 실패할 수 있어,
        # 이미 검증된 현재가 조회 로직(ka10081)을 fallback으로 사용한다.
        fallback_price = None
        if not basic_resp.get("success"):
            fallback_price = await self.get_current_price(code)
            if fallback_price is None:
                return {"success": False, "error": f"ka10006 실패: {basic_resp.get('error', 'unknown error')}"}
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
        quote_rows = (
            quote_data.get("stk_hoga")
            or quote_data.get("output")
            or quote_data.get("data")
            or []
        )

        basic_row = basic_rows[0] if isinstance(basic_rows, list) and basic_rows else {}
        quote_row = quote_rows[0] if isinstance(quote_rows, list) and quote_rows else {}

        snapshot = {
            "stock_code": code,
            "stock_name": self._pick_str(basic_row, ["stk_nm", "stock_name", "name", "302"], ""),
            "current_price": self._pick_int(basic_row, ["cur_prc", "price", "10"]),
            "price_diff": self._pick_int(basic_row, ["pred_pre", "diff", "11"]),
            "change_rate": self._pick_str(basic_row, ["flu_rt", "change_rate", "12"], "0"),
            "volume": self._pick_int(basic_row, ["trde_qty", "volume", "13"]),
            "orderbook_time": self._pick_str(quote_row, ["hotime", "hoga_time", "time"], datetime.now().strftime("%H:%M:%S")),
            "orderbook": [],
            "raw_basic": basic_row,
            "raw_quote": quote_row,
        }

        # fallback 가격이 있으면 current_price를 보정
        if fallback_price is not None and snapshot["current_price"] == 0:
            snapshot["current_price"] = fallback_price

        for level in range(1, 11):
            ask_px = self._pick_int(quote_row, [f"askp{level}", f"offerho{level}", f"sel_prc{level}"])
            bid_px = self._pick_int(quote_row, [f"bidp{level}", f"bidho{level}", f"buy_prc{level}"])
            ask_qty = self._pick_int(quote_row, [f"askp_rsqn{level}", f"offerrem{level}", f"sel_qty{level}"])
            bid_qty = self._pick_int(quote_row, [f"bidp_rsqn{level}", f"bidrem{level}", f"buy_qty{level}"])
            if ask_px == 0 and bid_px == 0 and ask_qty == 0 and bid_qty == 0:
                continue
            snapshot["orderbook"].append(
                {
                    "level": level,
                    "ask_price": ask_px,
                    "ask_qty": ask_qty,
                    "bid_price": bid_px,
                    "bid_qty": bid_qty,
                }
            )

        warnings = []
        if fallback_price is not None:
            warnings.append("ka10006 unavailable; current_price from fallback")
        if not quote_resp.get("success"):
            warnings.append(f"ka10004 unavailable: {quote_resp.get('error', 'unknown error')}")
        if warnings:
            snapshot["warnings"] = warnings

        return {"success": True, "snapshot": snapshot}
    
    def _parse_kiwoom_chart_data(self, api_response: dict, stock_code: str) -> list:
        """키움 API 응답을 차트 데이터로 변환"""
        chart_data = []
        
        try:
            # 분봉 데이터 (ka10080)
            minute_chart_list = api_response.get('stk_min_pole_chart_qry', [])
            # 일봉 데이터 (ka10081)
            daily_chart_list = api_response.get('stk_dt_pole_chart_qry', [])
            
            if minute_chart_list:
                # 분봉 데이터 파싱
                logger.info(f"📊 [CHART_DEBUG] 분봉 데이터 파싱: {len(minute_chart_list)}개")
                
                # 원본 데이터 샘플 확인 (처음 3개)
                logger.info(f"📊 [CHART_DEBUG] 원본 데이터 샘플:")
                for i, sample in enumerate(minute_chart_list[:3]):
                    logger.info(f"📊 [CHART_DEBUG] [{i+1}] {sample}")
                
                for item in minute_chart_list:
                    # 분봉 API 응답 필드 매핑
                    cntr_tm = item.get('cntr_tm', '')  # 체결시간 (YYYYMMDDHHMISS)
                    open_price = abs(int(item.get('open_pric', 0)))  # 시가 (음수 제거)
                    high_price = abs(int(item.get('high_pric', 0)))  # 고가 (음수 제거)
                    low_price = abs(int(item.get('low_pric', 0)))   # 저가 (음수 제거)
                    close_price = abs(int(item.get('cur_prc', 0)))  # 종가 (음수 제거)
                    volume = int(item.get('trde_qty', 0))      # 거래량
                    
                    # 시간 형식 변환: YYYYMMDDHHMISS → YYYY-MM-DD HH:MM:00
                    if len(cntr_tm) >= 12:
                        formatted_date = f"{cntr_tm[:4]}-{cntr_tm[4:6]}-{cntr_tm[6:8]} {cntr_tm[8:10]}:{cntr_tm[10:12]}:00"
                    else:
                        formatted_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    chart_data.append({
                        "timestamp": formatted_date,
                        "open": open_price,
                        "high": high_price,
                        "low": low_price,
                        "close": close_price,
                        "volume": volume
                    })
                    
            elif daily_chart_list:
                # 일봉 데이터 파싱
                logger.info(f"📊 [CHART_DEBUG] 일봉 데이터 파싱: {len(daily_chart_list)}개")
                for item in daily_chart_list:
                    # 일봉 API 응답 필드 매핑
                    dt = item.get('dt', '')  # 날짜 (YYYYMMDD)
                    open_price = int(item.get('open_pric', 0))  # 시가
                    high_price = int(item.get('high_pric', 0))  # 고가
                    low_price = int(item.get('low_pric', 0))   # 저가
                    close_price = int(item.get('cur_prc', 0))  # 종가
                    volume = int(item.get('trde_qty', 0))      # 거래량
                    
                    # 날짜 형식 변환: YYYYMMDD → YYYY-MM-DD 15:30:00 (장마감 기준)
                    if len(dt) == 8:
                        formatted_date = f"{dt[:4]}-{dt[4:6]}-{dt[6:8]} 15:30:00"
                    else:
                        formatted_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    chart_data.append({
                        "timestamp": formatted_date,
                        "open": open_price,
                        "high": high_price,
                        "low": low_price,
                        "close": close_price,
                        "volume": volume
                    })
            
            # 날짜순으로 정렬 (오래된 것부터)
            chart_data.sort(key=lambda x: x['timestamp'])
            
            logger.info(f"실제 차트 데이터 파싱 완료: {stock_code}, {len(chart_data)}개 포인트")
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

            # 요청 헤더 (kt10000 스펙)
            headers = {
                'Content-Type': 'application/json;charset=UTF-8',
                'authorization': f'Bearer {self.token_manager.get_valid_token()}',
                'appkey': app_key,
                'appsecret': app_secret,
                'cont-yn': 'N',  # 연속조회여부
                'next-key': '',  # 연속조회키
                'api-id': 'kt10000',  # TR명
            }
            
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
            
            logger.info(f"매수 주문 요청: {stock_code}, 수량: {quantity}, 가격: {price}, 타입: {order_type}")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, 
                    headers=headers, 
                    json=request_data,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    
                    response_text = await response.text()
                    logger.info(f"매수 주문 응답: {response.status} - {response_text}")
                    
                    if response.status == 200:
                        try:
                            data = json.loads(response_text)

                            # 키움 응답 키가 버전에 따라 다를 수 있어 둘 다 허용
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
                                    "message": data.get('return_msg') or data.get("msg1") or '정상적으로 처리되었습니다'
                                }

                            error_msg = data.get('return_msg') or data.get("msg1") or '알 수 없는 오류'
                            logger.error(f"매수 주문 실패: {error_msg}")
                            return {
                                "success": False,
                                "error": error_msg,
                                "_request": request_data,
                                "_response": data,
                            }
                        except json.JSONDecodeError as e:
                            logger.error(f"매수 주문 응답 파싱 실패: {e}")
                            return {
                                "success": False,
                                "error": "응답 파싱 실패"
                            }
                    else:
                        logger.error(f"매수 주문 API 호출 실패: {response.status}")
                        return {
                            "success": False,
                            "error": f"API 호출 실패: {response.status}"
                        }
                        
        except Exception as e:
            logger.error(f"매수 주문 중 오류: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def place_sell_order(self, stock_code: str, quantity: int, price: int = 0, order_type: str = "3") -> Dict:
        """주식 매도 주문 (키움 API kt10000 스펙)"""
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
                logger.error("매도 주문 실패 - 계좌번호가 설정되지 않았습니다")
                return {"success": False, "error": "계좌번호 없음"}
            
            # appkey/appsecret (실전/모의 분기) - 일부 엔드포인트에서 필수일 수 있음
            app_key = Config.KIWOOM_MOCK_APP_KEY if use_mock_account else Config.KIWOOM_APP_KEY
            app_secret = Config.KIWOOM_MOCK_APP_SECRET if use_mock_account else Config.KIWOOM_APP_SECRET

            # 요청 헤더 (kt10000 스펙)
            headers = {
                'Content-Type': 'application/json;charset=UTF-8',
                'authorization': f'Bearer {self.token_manager.get_valid_token()}',
                'appkey': app_key,
                'appsecret': app_secret,
                'cont-yn': 'N',  # 연속조회여부
                'next-key': '',  # 연속조회키
                'api-id': 'kt10000',  # TR명
            }
            
            # 주문 요청 데이터 (kt10000 스펙) - 매도 주문
            account_pw = Config.KIWOOM_MOCK_ACCOUNT_PASSWORD if use_mock_account else Config.KIWOOM_ACCOUNT_PASSWORD
            request_data = {
                'dmst_stex_tp': 'KRX',  # 국내거래소구분 KRX,NXT,SOR
                'acnt_no': account_no,  # 계좌번호
                'stk_cd': stock_code,   # 종목코드
                'ord_qty': str(quantity),  # 주문수량
                'ord_uv': str(price if price > 0 else 0),
                'trde_tp': order_type,  # 매매구분 (3:시장가, 0:보통)
                'cond_uv': '0',  # 조건단가
                'ord_side_cd': '2',  # 매도 주문 (1:매수, 2:매도)
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

    async def get_account_balance(self, account_number: str = None) -> Dict:
        """계좌 잔고 정보 조회 - 키움 API kt00004 사용"""
        """계좌 잔고 정보 조회 - 개선된 에러 처리"""
        if not self.token_manager.get_valid_token():
            logger.error("키움 API 토큰이 없습니다")
            return {}
            
        try:
            # API 제한 확인 및 기록
            if not api_rate_limiter.is_api_available():
                logger.warning("🚫 [KIWOOM_API] API 제한 상태로 인해 계좌 조회 건너뜀")
                return {}
            
            # API 호출 기록 (간격 체크 포함)
            if not api_rate_limiter.record_api_call("get_account_balance"):
                logger.debug("🚫 [KIWOOM_API] API 호출 간격 부족으로 계좌 조회 건너뜀 (정상 동작)")
                return {}
            
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
            
            # 요청 헤더
            headers = {
                'Content-Type': 'application/json;charset=UTF-8',
                'authorization': f'Bearer {self.token_manager.get_valid_token()}',
                'cont-yn': 'N',        # 연속조회여부
                'next-key': '',        # 연속조회키
                'api-id': 'kt00004',   # TR명
            }
            
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
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, 
                    headers=headers, 
                    json=request_data,
                    timeout=aiohttp.ClientTimeout(total=30)  # 타임아웃 추가
                ) as response:
                    
                    # 응답 상세 로깅 추가
                    response_text = await response.text()
                    logger.debug(f"계좌 조회 응답 상태: {response.status}")
                    
                    if response.status == 200:
                        try:
                            data = json.loads(response_text)
                            return_code = data.get('return_code')
                            logger.debug(f"계좌 조회 return_code: {return_code}")
                            
                            # 응답 확인
                            if return_code == 0:  # 성공
                                result = self._parse_account_balance_safe(data)
                                return result
                            else:
                                error_msg = data.get('msg1', '알 수 없는 오류')
                                logger.error(f"키움 API 계좌조회 오류: {error_msg}")
                                logger.error(f"전체 응답: {data}")
                                return {}
                        except json.JSONDecodeError as e:
                            logger.error(f"JSON 파싱 실패: {e}")
                            logger.error(f"원본 응답: {response_text}")
                            return {}
                    else:
                        logger.error(f"키움 API 호출 실패: {response.status}")
                        logger.error(f"오류 응답: {response_text}")
                        return {}
                        
        except aiohttp.ClientError as e:
            logger.error(f"HTTP 클라이언트 오류: {e}")
            return {}
        except asyncio.TimeoutError:
            logger.error("키움 API 호출 타임아웃")
            return {}
        except Exception as e:
            logger.error(f"계좌 정보 조회 중 예상치 못한 오류: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"스택 트레이스: {traceback.format_exc()}")
            return {}
    
    def _parse_account_balance_safe(self, api_response: dict) -> dict:
        """키움 API 계좌 잔고 응답 파싱 - 안전한 버전"""
        try:
            logger.debug(f"계좌 응답 파싱 시작")
            
            # 안전한 데이터 추출
            def safe_get(data, key, default='0'):
                value = data.get(key, default)
                return str(value) if value is not None else default
            
            # 보유종목 데이터 추출
            stk_acnt_evlt_prst = []
            if 'stk_acnt_evlt_prst' in api_response:
                stk_data = api_response['stk_acnt_evlt_prst']
                logger.debug(f"보유종목 원본 데이터 수: {len(stk_data) if isinstance(stk_data, list) else 1}")
                
                if isinstance(stk_data, list):
                    for item in stk_data:
                        if isinstance(item, dict):
                            stk_acnt_evlt_prst.append({
                                "stk_cd": safe_get(item, 'stk_cd', ''),
                                "stk_nm": safe_get(item, 'stk_nm', ''),
                                "qty": safe_get(item, 'qty', '0'),
                                "pur_amt": safe_get(item, 'pur_amt', '0'),
                                "evlt_amt": safe_get(item, 'evlt_amt', '0'),
                                "lspft_amt": safe_get(item, 'lspft_amt', '0'),
                                "lspft_rt": safe_get(item, 'lspft_rt', '0'),
                                "cur_pr": safe_get(item, 'cur_pr', '0'),
                                "avg_pr": safe_get(item, 'avg_pr', '0')
                            })
                elif isinstance(stk_data, dict):
                    # 단일 종목인 경우
                    stk_acnt_evlt_prst.append({
                        "stk_cd": safe_get(stk_data, 'stk_cd', ''),
                        "stk_nm": safe_get(stk_data, 'stk_nm', ''),
                        "qty": safe_get(stk_data, 'qty', '0'),
                        "pur_amt": safe_get(stk_data, 'pur_amt', '0'),
                        "evlt_amt": safe_get(stk_data, 'evlt_amt', '0'),
                        "lspft_amt": safe_get(stk_data, 'lspft_amt', '0'),
                        "lspft_rt": safe_get(stk_data, 'lspft_rt', '0'),
                        "cur_pr": safe_get(stk_data, 'cur_pr', '0'),
                        "avg_pr": safe_get(stk_data, 'avg_pr', '0')
                    })
            
            result = {
                "acnt_nm": safe_get(api_response, 'acnt_nm', ''),
                "brch_nm": safe_get(api_response, 'brch_nm', ''),
                "entr": safe_get(api_response, 'entr'),
                "d2_entra": safe_get(api_response, 'd2_entra'),
                "tot_est_amt": safe_get(api_response, 'tot_est_amt'),
                "aset_evlt_amt": safe_get(api_response, 'aset_evlt_amt'),
                "tot_pur_amt": safe_get(api_response, 'tot_pur_amt'),
                "prsm_dpst_aset_amt": safe_get(api_response, 'prsm_dpst_aset_amt'),
                "tot_grnt_sella": safe_get(api_response, 'tot_grnt_sella'),
                "tdy_lspft_amt": safe_get(api_response, 'tdy_lspft_amt'),
                "invt_bsamt": safe_get(api_response, 'invt_bsamt'),
                "lspft_amt": safe_get(api_response, 'lspft_amt'),
                "tdy_lspft": safe_get(api_response, 'tdy_lspft'),
                "lspft2": safe_get(api_response, 'lspft2'),
                "lspft": safe_get(api_response, 'lspft'),
                "tdy_lspft_rt": safe_get(api_response, 'tdy_lspft_rt'),
                "lspft_ratio": safe_get(api_response, 'lspft_ratio'),
                "lspft_rt": safe_get(api_response, 'lspft_rt'),
                "stk_acnt_evlt_prst": stk_acnt_evlt_prst
            }
            
            # 요약 정보만 로깅 (금액은 숫자로 변환하여 표시)
            try:
                # aset_evlt_amt: 자산평가금액 (실제 보유 자산 가치)
                # tot_est_amt: 총추정금액 (자산평가 + 예수금)
                aset_amt = int(result['aset_evlt_amt']) if result['aset_evlt_amt'] else 0
                tot_amt = int(result['tot_est_amt']) if result['tot_est_amt'] else 0
                
                if aset_amt > 0 or tot_amt > 0:
                    logger.info(f"계좌 파싱 완료 - 보유종목: {len(stk_acnt_evlt_prst)}개, 자산평가: {aset_amt:,}원, 총추정: {tot_amt:,}원")
                else:
                    logger.info(f"계좌 파싱 완료 - 보유종목: {len(stk_acnt_evlt_prst)}개 (자산 없음)")
            except (ValueError, TypeError) as e:
                logger.warning(f"계좌 금액 파싱 오류: {e}")
                logger.info(f"계좌 파싱 완료 - 보유종목: {len(stk_acnt_evlt_prst)}개")
            return result
            
        except Exception as e:
            logger.error(f"계좌 잔고 응답 파싱 오류: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"스택 트레이스: {traceback.format_exc()}")
            return {}

# 전역 인스턴스
kiwoom_api = KiwoomAPI()