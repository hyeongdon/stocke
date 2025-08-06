import asyncio
import json
import logging
from typing import Dict, Set, Optional, Callable
from fastapi import WebSocket, WebSocketDisconnect
from datetime import datetime
import websockets
from config import Config

logger = logging.getLogger(__name__)

class WebSocketManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.connection_groups: Dict[str, Set[str]] = {}
        self.kiwoom_websocket: Optional[websockets.WebSocketServerProtocol] = None
        self.kiwoom_connected = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 1
        
    async def connect(self, websocket: WebSocket, client_id: str):
        """클라이언트 WebSocket 연결"""
        await websocket.accept()
        self.active_connections[client_id] = websocket
        logger.info(f"클라이언트 연결: {client_id}")
        
        # 연결 그룹에 추가
        if "default" not in self.connection_groups:
            self.connection_groups["default"] = set()
        self.connection_groups["default"].add(client_id)
        
    async def disconnect(self, client_id: str):
        """클라이언트 WebSocket 연결 해제"""
        if client_id in self.active_connections:
            del self.active_connections[client_id]
            
        # 모든 그룹에서 제거
        for group in self.connection_groups.values():
            group.discard(client_id)
            
        logger.info(f"클라이언트 연결 해제: {client_id}")
        
    async def send_personal_message(self, message: dict, client_id: str):
        """특정 클라이언트에게 메시지 전송"""
        if client_id in self.active_connections:
            try:
                await self.active_connections[client_id].send_text(json.dumps(message))
            except Exception as e:
                logger.error(f"개인 메시지 전송 실패: {e}")
                await self.disconnect(client_id)
                
    async def broadcast(self, message: dict, group: str = "default"):
        """그룹 내 모든 클라이언트에게 메시지 브로드캐스트"""
        if group in self.connection_groups:
            disconnected_clients = []
            
            for client_id in self.connection_groups[group]:
                try:
                    await self.send_personal_message(message, client_id)
                except Exception as e:
                    logger.error(f"브로드캐스트 실패: {e}")
                    disconnected_clients.append(client_id)
                    
            # 연결이 끊어진 클라이언트들 제거
            for client_id in disconnected_clients:
                await self.disconnect(client_id)
                
    async def connect_kiwoom_websocket(self):
        """키움증권 WebSocket 연결"""
        try:
            # 키움증권 WebSocket URL (실제 URL로 변경 필요)
            kiwoom_ws_url = "wss://openapi.kiwoom.com/ws"
            
            self.kiwoom_websocket = await websockets.connect(
                kiwoom_ws_url,
                ping_interval=Config.WEBSOCKET_PING_INTERVAL,
                ping_timeout=Config.WEBSOCKET_PING_TIMEOUT
            )
            
            self.kiwoom_connected = True
            self.reconnect_attempts = 0
            logger.info("키움증권 WebSocket 연결 성공")
            
            # 연결 유지를 위한 핑 메시지 전송
            asyncio.create_task(self._keep_kiwoom_alive())
            
        except Exception as e:
            logger.error(f"키움증권 WebSocket 연결 실패: {e}")
            self.kiwoom_connected = False
            await self._handle_kiwoom_reconnect()
            
    async def _keep_kiwoom_alive(self):
        """키움증권 WebSocket 연결 유지"""
        while self.kiwoom_connected and self.kiwoom_websocket:
            try:
                await asyncio.sleep(30)  # 30초마다 핑
                if self.kiwoom_websocket:
                    await self.kiwoom_websocket.ping()
            except Exception as e:
                logger.error(f"키움증권 WebSocket 핑 실패: {e}")
                self.kiwoom_connected = False
                await self._handle_kiwoom_reconnect()
                break
                
    async def _handle_kiwoom_reconnect(self):
        """키움증권 WebSocket 재연결 처리"""
        if self.reconnect_attempts < self.max_reconnect_attempts:
            self.reconnect_attempts += 1
            logger.info(f"키움증권 WebSocket 재연결 시도 {self.reconnect_attempts}/{self.max_reconnect_attempts}")
            
            await asyncio.sleep(5 * self.reconnect_attempts)  # 지수 백오프
            await self.connect_kiwoom_websocket()
        else:
            logger.error("키움증권 WebSocket 최대 재연결 시도 횟수 초과")
            
    async def subscribe_stock_price(self, stock_codes: list):
        """실시간 주가 구독"""
        if not self.kiwoom_connected or not self.kiwoom_websocket:
            logger.error("키움증권 WebSocket이 연결되지 않았습니다.")
            return False
            
        try:
            subscribe_message = {
                "type": "subscribe",
                "symbols": stock_codes,
                "channels": ["price", "volume"]
            }
            
            await self.kiwoom_websocket.send(json.dumps(subscribe_message))
            logger.info(f"실시간 주가 구독 요청: {stock_codes}")
            return True
            
        except Exception as e:
            logger.error(f"실시간 주가 구독 실패: {e}")
            return False
            
    async def unsubscribe_stock_price(self, stock_codes: list):
        """실시간 주가 구독 해제"""
        if not self.kiwoom_connected or not self.kiwoom_websocket:
            return False
            
        try:
            unsubscribe_message = {
                "type": "unsubscribe",
                "symbols": stock_codes
            }
            
            await self.kiwoom_websocket.send(json.dumps(unsubscribe_message))
            logger.info(f"실시간 주가 구독 해제: {stock_codes}")
            return True
            
        except Exception as e:
            logger.error(f"실시간 주가 구독 해제 실패: {e}")
            return False
            
    async def listen_kiwoom_messages(self):
        """키움증권 WebSocket 메시지 수신"""
        while self.kiwoom_connected and self.kiwoom_websocket:
            try:
                message = await self.kiwoom_websocket.recv()
                data = json.loads(message)
                
                # 실시간 데이터를 모든 클라이언트에게 브로드캐스트
                await self.broadcast({
                    "type": "realtime_data",
                    "data": data,
                    "timestamp": datetime.utcnow().isoformat()
                })
                
                logger.debug(f"키움증권 실시간 데이터 수신: {data}")
                
            except websockets.exceptions.ConnectionClosed:
                logger.warning("키움증권 WebSocket 연결이 끊어졌습니다.")
                self.kiwoom_connected = False
                await self._handle_kiwoom_reconnect()
                break
            except Exception as e:
                logger.error(f"키움증권 메시지 수신 오류: {e}")
                
    async def close_kiwoom_connection(self):
        """키움증권 WebSocket 연결 종료"""
        if self.kiwoom_websocket:
            await self.kiwoom_websocket.close()
            self.kiwoom_websocket = None
            self.kiwoom_connected = False
            logger.info("키움증권 WebSocket 연결 종료")
            
    def get_connection_status(self) -> dict:
        """연결 상태 조회"""
        return {
            "active_clients": len(self.active_connections),
            "kiwoom_connected": self.kiwoom_connected,
            "reconnect_attempts": self.reconnect_attempts,
            "connection_groups": {k: len(v) for k, v in self.connection_groups.items()}
        }

# 전역 WebSocket 매니저 인스턴스
websocket_manager = WebSocketManager() 