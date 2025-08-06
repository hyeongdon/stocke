import json
import logging
import asyncio
import websockets
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
        
    async def connect(self):
        """웹소켓 연결 및 인증"""
        if not self.token_manager.get_valid_token():
            return False
            
        try:
            self.websocket = await websockets.connect(
                self.ws_url,
                extra_headers={
                    "Authorization": f"Bearer {self.token_manager.get_valid_token()}",
                    "appkey": Config.KIWOOM_APP_KEY,
                    "appsecret": Config.KIWOOM_APP_SECRET
                }
            )
            self.running = True
            asyncio.create_task(self._message_handler())
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

# 전역 인스턴스
kiwoom_api = KiwoomAPI()