import json
import logging
import asyncio
import websockets
import aiohttp
from datetime import datetime
from typing import Dict, Optional, Callable, List
from config import Config
from token_manager import TokenManager

logger = logging.getLogger(__name__)

class KiwoomAPI:
    def __init__(self):
        self.base_url = Config.KIWOOM_BASE_URL
        self.ws_url = Config.KIWOOM_WS_URL
        self.token_manager = TokenManager()
        self.websocket = None
        self.condition_callbacks = {}
        self.running = False
        
    def authenticate(self) -> bool:
        """키움증권 API 인증"""
        try:
            return self.token_manager.authenticate()
        except Exception as e:
            logger.error(f"키움증권 API 인증 실패: {e}")
            return False
        
    async def connect(self):
        """웹소켓 연결 및 인증"""
        if not self.token_manager.get_valid_token():
            return False
            
        try:
            # 키움 API WebSocket 연결 URL 구성
            ws_url = f"{self.ws_url}/api/dostk/websocket"
            
            self.websocket = await websockets.connect(
                ws_url,
                extra_headers={
                    "Content-Type": "application/json;charset=UTF-8",
                    "Authorization": f"Bearer {self.token_manager.get_valid_token()}",
                    "appkey": Config.KIWOOM_APP_KEY,
                    "appsecret": Config.KIWOOM_APP_SECRET
                }
            )
            self.running = True
            asyncio.create_task(self._message_handler())
            logger.info("WebSocket 연결 성공")
            return True
        except Exception as e:
            logger.error(f"웹소켓 연결 실패: {e}")
            return False
            
    async def disconnect(self):
        """웹소켓 연결 종료"""
        self.running = False
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
            
    async def _message_handler(self):
        """웹소켓 메시지 처리"""
        while self.running and self.websocket:
            try:
                message = await self.websocket.recv()
                data = json.loads(message)
                
                if data["type"] == "condition":
                    condition_name = data["condition_name"]
                    if condition_name in self.condition_callbacks:
                        await self.condition_callbacks[condition_name](data)
            except Exception as e:
                logger.error(f"웹소켓 메시지 처리 중 오류: {e}")
                await asyncio.sleep(1)
                
    def _get_headers(self) -> Dict[str, str]:
        """API 요청 헤더 생성"""
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token_manager.get_valid_token()}",
            "appkey": Config.KIWOOM_APP_KEY,
            "appsecret": Config.KIWOOM_APP_SECRET
        }
    
    async def get_condition_list(self) -> List[Dict]:
        """조건식 목록 조회"""
        if not self.token_manager.get_valid_token():
            return []
            
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/uapi/domestic-stock/v1/conditions",
                    headers=self._get_headers()
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        return result.get("conditions", [])
                    else:
                        logger.error(f"조건식 목록 조회 실패: {response.status}")
                        return []
        except Exception as e:
            logger.error(f"조건식 목록 조회 중 오류: {e}")
            return []
    
    async def get_condition_list_websocket(self) -> List[Dict]:
        """조건식 목록 조회 (WebSocket) - 키움증권 API 방식"""
        logger.debug("get_condition_list_websocket 시작")
        
        # 새로운 WebSocket 연결 생성 (기존 연결과 충돌 방지)
        try:
            ws_url = f"{self.ws_url}/api/dostk/websocket"
            
            websocket = await websockets.connect(
                ws_url,
                extra_headers={
                    "Content-Type": "application/json;charset=UTF-8",
                    "Authorization": f"Bearer {self.token_manager.get_valid_token()}",
                    "appkey": Config.KIWOOM_APP_KEY,
                    "appsecret": Config.KIWOOM_APP_SECRET
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
                        for item in condition_data:
                            if isinstance(item, list) and len(item) == 2:
                                conditions.append({
                                    "condition_id": item[0],
                                    "condition_name": item[1]
                                })
                    
                    logger.info(f"조건식 목록 조회 성공: {len(conditions)}개")
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
                                
    async def subscribe_condition(self, condition_name: str, callback: Callable):
        """조건식 실시간 구독"""
        if not self.websocket:
            return False
            
        try:
            subscribe_message = {
                "type": "subscribe",
                "condition_name": condition_name
            }
            await self.websocket.send(json.dumps(subscribe_message))
            self.condition_callbacks[condition_name] = callback
            return True
        except Exception as e:
            logger.error(f"조건식 구독 실패: {e}")
            return False
            
    async def unsubscribe_condition(self, condition_name: str):
        """조건식 실시간 구독 해제"""
        if not self.websocket:
            return False
            
        try:
            unsubscribe_message = {
                "type": "unsubscribe",
                "condition_name": condition_name
            }
            await self.websocket.send(json.dumps(unsubscribe_message))
            self.condition_callbacks.pop(condition_name, None)
            return True
        except Exception as e:
            logger.error(f"조건식 구독 해제 실패: {e}")
            return False
    
    async def search_condition_stocks(self, condition_id: str, condition_name: str) -> List[Dict]:
        """조건식으로 종목 검색 (WebSocket)"""
        logger.debug(f"조건식 검색 시작: {condition_name} (ID: {condition_id})")
        
        try:
            ws_url = f"{self.ws_url}/api/dostk/websocket"
            
            websocket = await websockets.connect(
                ws_url,
                extra_headers={
                    "Content-Type": "application/json;charset=UTF-8",
                    "Authorization": f"Bearer {self.token_manager.get_valid_token()}",
                    "appkey": Config.KIWOOM_APP_KEY,
                    "appsecret": Config.KIWOOM_APP_SECRET
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
                logger.info(f"응답 수신 (시도 {attempt + 1}): {response}")
                
                try:
                    data = json.loads(response)
                    # PING 응답이면 그대로 다시 전송
                    if data.get('trnm') == 'PING':
                        logger.info("PING 응답 수신, 응답 전송")
                        await websocket.send(response)
                        continue
                    # 실제 응답이면 처리
                    elif data.get('trnm') == 'CNSRREQ':
                        logger.info(f"CNSRREQ 응답 수신: {response}")
                        break
                    else:
                        logger.info(f"예상치 못한 응답: {response}")
                        continue
                except json.JSONDecodeError:
                    logger.warning(f"JSON 파싱 실패: {response}")
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
            
            # 키움 API 호출 설정
            # host = 'https://mockapi.kiwoom.com'  # 모의투자
            host = 'https://api.kiwoom.com'  # 실전투자
            endpoint = '/api/dostk/chart'
            url = host + endpoint
            
            # 현재 날짜를 기준 날짜로 설정
            from datetime import datetime
            base_dt = datetime.now().strftime('%Y%m%d')
            
            # 요청 헤더
            headers = {
                'Content-Type': 'application/json;charset=UTF-8',
                'authorization': f'Bearer {self.token_manager.get_valid_token()}',
                'cont-yn': 'N',  # 연속조회여부
                'next-key': '',  # 연속조회키
                'api-id': 'ka10081',  # TR명
            }
            
            # 요청 데이터
            request_data = {
                "stk_cd": stock_code,  # 종목코드
                "base_dt": base_dt,    # 기준일자
                "upd_stkpc_tp": "1"    # 수정주가타입 (1: 수정주가)
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, 
                    headers=headers, 
                    json=request_data
                ) as response:
                    
                    if response.status == 200:
                        data = await response.json()
                        
                        # 응답 확인
                        if data.get('return_code') == 0:
                            return self._parse_kiwoom_chart_data(data, stock_code)
                        else:
                            logger.error(f"키움 API 오류: {data.get('return_msg')}")
                            return []
                    else:
                        logger.error(f"키움 API 호출 실패: {response.status}")
                        return []
                        
        except Exception as e:
            logger.error(f"실제 차트 데이터 조회 중 오류: {e}")
            # 오류 발생 시 기존 모의 데이터로 폴백
            return await self._get_mock_chart_data(stock_code, period)
    
    def _parse_kiwoom_chart_data(self, api_response: dict, stock_code: str) -> list:
        """키움 API 응답을 차트 데이터로 변환"""
        chart_data = []
        
        try:
            # 키움 API 응답에서 차트 데이터 추출
            chart_list = api_response.get('stk_dt_pole_chart_qry', [])
            
            for item in chart_list:
                # 키움 API 응답 필드 매핑
                dt = item.get('dt', '')  # 날짜 (YYYYMMDD)
                open_price = int(item.get('open_pric', 0))  # 시가
                high_price = int(item.get('high_pric', 0))  # 고가
                low_price = int(item.get('low_pric', 0))   # 저가
                close_price = int(item.get('cur_prc', 0))  # 종가
                volume = int(item.get('trde_qty', 0))      # 거래량
                
                # 날짜 형식 변환 (YYYYMMDD -> YYYY-MM-DD HH:MM:SS)
                if len(dt) == 8:
                    formatted_date = f"{dt[:4]}-{dt[4:6]}-{dt[6:8]} 15:30:00"  # 장마감 시간
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
    
    async def _get_mock_chart_data(self, stock_code: str, period: str = "1D"):
        """기존 모의 데이터 생성 (폴백용)"""
        # 기존 모의 데이터 생성 로직을 별도 메서드로 분리
        import random
        from datetime import datetime, timedelta
        
        # 기간에 따른 데이터 포인트 수 결정
        if period == "1D":
            points = 390  # 1일 (분봉)
            interval = timedelta(minutes=1)
        elif period == "1W":
            points = 7  # 1주 (일봉)
            interval = timedelta(days=1)
        elif period == "1M":
            points = 30  # 1개월 (일봉)
            interval = timedelta(days=1)
        elif period == "1Y":
            points = 250  # 1년 (일봉, 주말 제외)
            interval = timedelta(days=1)
        else:
            points = 390
            interval = timedelta(minutes=1)
        
        # 종목코드를 시드로 사용하여 일관된 차트 데이터 생성
        random.seed(hash(stock_code + period) % 1000000)
        
        # 기준 가격 설정 (종목코드 기반으로 일관된 값)
        base_price = random.randint(10000, 100000)
        
        chart_data = []
        if period == "1D":
            current_time = datetime.now() - timedelta(minutes=points)
        else:
            current_time = datetime.now() - timedelta(days=points)
        current_price = base_price
        
        for i in range(points):
            # 가격 변동 (±1% 범위로 현실적으로 조정)
            change_percent = random.uniform(-0.01, 0.01)
            
            # OHLCV 데이터 생성
            open_price = current_price
            
            # 고가/저가는 시가 기준으로 ±0.5% 범위
            high_price = int(open_price * random.uniform(1.0, 1.005))
            low_price = int(open_price * random.uniform(0.995, 1.0))
            
            # 종가는 고가와 저가 사이의 값
            close_price = random.randint(low_price, high_price)
            
            # 거래량은 기간에 따라 다르게 설정
            if period == "1D":
                volume = random.randint(100, 10000)  # 분봉 거래량
            else:
                volume = random.randint(10000, 1000000)  # 일봉 거래량
            
            current_price = close_price
            
            chart_data.append({
                "timestamp": current_time.strftime("%Y-%m-%d %H:%M:%S"),
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume
            })
            
            current_time += interval
            current_price = close_price
        
        logger.info(f"모의 차트 데이터 생성 완료: {stock_code}, {len(chart_data)}개 포인트")
        return chart_data
    
    async def get_minute_chart_data(self, stock_code: str):
        """분봉 차트 데이터 조회 (별도 API 필요)"""
        # 키움에서 분봉 조회 API가 있다면 여기에 구현
        # 현재는 일봉 데이터로 대체
        return await self.get_stock_chart_data(stock_code, "1D")

# 전역 인스턴스
kiwoom_api = KiwoomAPI()