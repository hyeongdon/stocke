"""
텔레그램 알림 전송 모듈

키움 조건식 조회 결과 등 임의의 텍스트 메시지를 텔레그램 봇으로 전송합니다.
독립 실행 스크립트와 서버 양쪽에서 재사용할 수 있도록 외부 의존성은 requests만 사용합니다.
"""
import logging
from typing import List, Optional

import requests

from core.config import Config

logger = logging.getLogger(__name__)

# 텔레그램 메시지 길이 제한(4096자)보다 약간 작게 잡아 안전 마진 확보
TELEGRAM_MAX_MESSAGE_LENGTH = 4000


class TelegramNotifier:
    """텔레그램 봇 메시지 전송기."""

    API_BASE = "https://api.telegram.org"

    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        self.bot_token = bot_token or Config.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or Config.TELEGRAM_CHAT_ID

    def is_configured(self) -> bool:
        """봇 토큰과 채팅 ID가 모두 설정되어 있는지 확인."""
        return bool(self.bot_token) and bool(self.chat_id)

    @staticmethod
    def _mode_prefix() -> str:
        mode = "모의" if Config.KIWOOM_USE_MOCK_ACCOUNT else "실전"
        return f"[{mode}] "

    def _split_message(self, text: str, max_length: int = TELEGRAM_MAX_MESSAGE_LENGTH) -> List[str]:
        """길이 제한을 넘는 메시지를 줄 단위로 안전하게 분할."""
        if len(text) <= max_length:
            return [text]

        chunks: List[str] = []
        current = ""
        for line in text.split("\n"):
            # 한 줄 자체가 너무 긴 경우 강제로 잘라서 처리
            while len(line) > max_length:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.append(line[:max_length])
                line = line[max_length:]

            candidate = f"{current}\n{line}" if current else line
            if len(candidate) > max_length:
                chunks.append(current)
                current = line
            else:
                current = candidate

        if current:
            chunks.append(current)
        return chunks

    def send_message(self, text: str, parse_mode: Optional[str] = None) -> bool:
        """텍스트 메시지를 전송. 길이 제한 초과 시 여러 건으로 나눠 전송."""
        if not self.is_configured():
            logger.error("텔레그램 설정 누락: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 를 확인하세요.")
            return False

        prefix = self._mode_prefix()
        chunks = self._split_message(text, max_length=TELEGRAM_MAX_MESSAGE_LENGTH - len(prefix))
        url = f"{self.API_BASE}/bot{self.bot_token}/sendMessage"
        all_ok = True

        for chunk in chunks:
            chunk = f"{prefix}{chunk}"
            payload = {
                "chat_id": self.chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode

            try:
                resp = requests.post(url, json=payload, timeout=15)
                if resp.status_code != 200 or not resp.json().get("ok", False):
                    logger.error(f"텔레그램 전송 실패: status={resp.status_code}, body={resp.text}")
                    all_ok = False
                else:
                    logger.info("텔레그램 메시지 전송 성공")
            except Exception as e:
                logger.error(f"텔레그램 전송 중 오류: {e}")
                all_ok = False

        return all_ok
