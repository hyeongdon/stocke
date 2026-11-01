from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Dict, List, Optional

import websockets

from api.kiwoom_api_shared import (
    _CONDITION_SEARCH_MAX_ATTEMPTS,
    _get_cached_condition_list,
    _get_cached_condition_search,
    _get_condition_ws_lock,
    _parse_kiwoom_float,
    _parse_kiwoom_int,
    _set_cached_condition_list,
    _set_cached_condition_search,
    logger,
)
from core.config import Config

__all__ = ["_RealtimeMixin", "ConditionSearchSession"]


if TYPE_CHECKING:
    from api.kiwoom_api import KiwoomAPI


class _RealtimeMixin:
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

            # 재연결 시 실시간 구독 복구
            if self._realtime_subscribed:
                asyncio.create_task(self._restore_realtime_subscriptions())

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

    async def _restore_realtime_subscriptions(self):
        """WebSocket 재연결 후 기존 실시간 구독 복구."""
        codes = list(self._realtime_subscribed)
        self._realtime_subscribed.clear()
        await asyncio.sleep(0.5)  # 연결 안정화 대기
        for code in codes:
            await self.subscribe_realtime_stock(code)
            await asyncio.sleep(0.05)
        logger.info(f"📡 [REALTIME] 재연결 후 {len(codes)}종목 구독 복구 완료")

    async def subscribe_realtime_stock(self, stock_code: str) -> bool:
        """
        종목 실시간 체결 데이터 구독 (WebSocket REG 전송).
        RealtimeCandleManager.on_realtime_trade()가 REAL 메시지를 수신한다.

        키움 WebSocket REG 형식:
          {"trnm":"REG","data":[{"item":["005930"],"type":["0D"]}]}
        0D = 주식체결 실시간
        """
        code = str(stock_code or "").strip().lstrip("A")
        if not code:
            return False
        if code in self._realtime_subscribed:
            return True
        if not self.websocket or not self.running:
            logger.warning(f"📡 [REALTIME] WebSocket 미연결 — 구독 보류: {code}")
            return False
        try:
            msg = {"trnm": "REG", "data": [{"item": [code], "type": ["0D"]}]}
            await self.websocket.send(json.dumps(msg))
            self._realtime_subscribed.add(code)
            logger.info(f"📡 [REALTIME] 실시간 체결 구독: {code}")
            return True
        except Exception as e:
            logger.error(f"📡 [REALTIME] REG 전송 실패 {code}: {e}")
            return False

    async def subscribe_realtime_stocks_batch(self, stock_codes: list) -> bool:
        """
        여러 종목 실시간 체결 구독 — WebSocket REG 1회 전송 (효율적).
        개별 subscribe_realtime_stock()을 반복 호출하는 것보다 서버 부하가 적다.
        """
        codes = [str(c or "").strip().lstrip("A") for c in stock_codes]
        codes = [c for c in codes if c and c not in self._realtime_subscribed]
        if not codes:
            return True
        if not self.websocket or not self.running:
            logger.warning(f"📡 [REALTIME] WebSocket 미연결 — 배치 구독 보류: {codes}")
            return False
        try:
            msg = {"trnm": "REG", "data": [{"item": codes, "type": ["0D"]}]}
            await self.websocket.send(json.dumps(msg))
            for code in codes:
                self._realtime_subscribed.add(code)
            logger.info(f"📡 [REALTIME] 배치 구독 REG 전송: {len(codes)}종목 → {codes}")
            return True
        except Exception as e:
            logger.error(f"📡 [REALTIME] 배치 REG 전송 실패: {e}")
            return False

    async def unsubscribe_realtime_stock(self, stock_code: str) -> bool:
        """종목 실시간 체결 구독 해제 (WebSocket UNREG)."""
        code = str(stock_code or "").strip().lstrip("A")
        self._realtime_subscribed.discard(code)
        if not self.websocket or not self.running:
            return False
        try:
            msg = {"trnm": "UNREG", "data": [{"item": [code], "type": ["0D"]}]}
            await self.websocket.send(json.dumps(msg))
            logger.info(f"📡 [REALTIME] 실시간 체결 구독 해제: {code}")
            return True
        except Exception as e:
            logger.error(f"📡 [REALTIME] UNREG 전송 실패 {code}: {e}")
            return False

    def register_realtime_callback(self, callback) -> None:
        """
        실시간 체결 콜백 등록.
        callback(stock_code: str, values: dict) — 코루틴 또는 일반 함수.
        """
        self._realtime_trade_callbacks.append(callback)

    async def _dispatch_realtime(self, data: dict) -> None:
        """WebSocket REAL 메시지를 파싱해 등록된 콜백들에 전달."""
        try:
            items = data.get("data")
            if not isinstance(items, list):
                # flat REAL
                items = [data] if data.get("values") else []
            for item in items:
                data_type = item.get("type", "")
                if data_type != "0D":
                    continue
                # 종목코드
                code = (
                    item.get("name")
                    or item.get("item")
                    or (item.get("values") or {}).get("9001")
                    or ""
                )
                code = str(code).strip().lstrip("A")
                if not code:
                    continue
                values = item.get("values") or {}
                for cb in self._realtime_trade_callbacks:
                    try:
                        if asyncio.iscoroutinefunction(cb):
                            await cb(code, values)
                        else:
                            cb(code, values)
                    except Exception as e:
                        logger.warning(f"📡 [REALTIME] 콜백 오류: {e}")
        except Exception as e:
            logger.warning(f"📡 [REALTIME] REAL 메시지 처리 오류: {e}")

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
                trnm = data.get("trnm")

                # ── 실시간 체결 메시지 (REAL 0D) ─────────────────────
                if trnm == "REAL":
                    await self._dispatch_realtime(data)
                elif trnm == "PING":
                    if self.websocket:
                        await self.websocket.send(json.dumps({"trnm": "PONG"}))
                elif message_type == "condition":
                    condition_name = data.get("condition_name")
                    if condition_name and condition_name in self.condition_callbacks:
                        await self.condition_callbacks[condition_name](data)
                else:
                    # 예상하지 못한 메시지 타입 로깅
                    logger.debug(f"알 수 없는 메시지 타입: trnm={trnm} type={message_type}")
                    
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
        *,
        max_pages: int = 1,
    ) -> List[Dict]:
        stocks: List[Dict] = []
        cont_yn, next_key = "N", ""
        for page in range(max(1, max_pages)):
            search_param = {
                "trnm": "CNSRREQ",
                "seq": condition_id,
                "search_type": "0",
                "stex_tp": "K",
                "cont_yn": cont_yn,
                "next_key": next_key,
            }
            await websocket.send(json.dumps(search_param))
            logger.debug(f"CNSRREQ 전송: {condition_name} (ID={condition_id}, page={page + 1})")
            data = await self._recv_cnsrreq_on_ws(websocket)
            if not data:
                raise TimeoutError(f"조건식 CNSRREQ 미수신: {condition_name}")
            if data.get("return_code") not in (0, None):
                raise RuntimeError(f"조건식 검색 실패: {condition_name} — {data}")
            stocks.extend(self._parse_cnsrreq_stocks(data, condition_name))
            cont_yn = str(data.get("cont_yn") or "N").upper()
            next_key = str(data.get("next_key") or "")
            if cont_yn != "Y" or not next_key:
                break
            await asyncio.sleep(0.3)
        return stocks

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

    async def search_all(self, condition_id: str, condition_name: str, *, max_pages: int = 50) -> List[Dict]:
        """연속조회(cont_yn) 끝까지 수집 — 메모리 캐시 미사용 (search()의 캐시는 1페이지분)."""
        last_err: Optional[BaseException] = None
        for attempt in range(1, _CONDITION_SEARCH_MAX_ATTEMPTS + 1):
            try:
                if not self._websocket:
                    self._websocket = await self._api._open_condition_websocket()
                return await self._api._search_condition_on_websocket(
                    self._websocket, condition_id, condition_name, max_pages=max_pages,
                )
            except Exception as e:
                last_err = e
                logger.warning(
                    f"조건식 전체 검색 재시도 ({attempt}/{_CONDITION_SEARCH_MAX_ATTEMPTS}): "
                    f"{condition_name} — {type(e).__name__}: {e!r}"
                )
            if attempt >= _CONDITION_SEARCH_MAX_ATTEMPTS:
                break
            await asyncio.sleep(0.4 * attempt)
            try:
                await self._reopen()
            except Exception as e:
                last_err = e
        raise last_err or RuntimeError(f"조건식 전체 검색 실패: {condition_name}")
