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

    def send_message(self, text: str, parse_mode: Optional[str] = None, reply_markup: Optional[dict] = None) -> bool:
        """텍스트 메시지를 전송. 길이 제한 초과 시 여러 건으로 나눠 전송."""
        return self.send_message_id(text, parse_mode=parse_mode, reply_markup=reply_markup) is not None

    def send_message_id(self, text: str, parse_mode: Optional[str] = None, reply_markup: Optional[dict] = None) -> Optional[int]:
        """전송 후 첫 메시지 id. 실패하면 None."""
        if not self.is_configured():
            logger.error("텔레그램 설정 누락: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 를 확인하세요.")
            return None

        prefix = self._mode_prefix()
        chunks = self._split_message(text, max_length=TELEGRAM_MAX_MESSAGE_LENGTH - len(prefix))
        url = f"{self.API_BASE}/bot{self.bot_token}/sendMessage"
        first_id: Optional[int] = None

        for i, chunk in enumerate(chunks):
            payload = {
                "chat_id": self.chat_id,
                "text": f"{prefix}{chunk}",
                "disable_web_page_preview": True,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode
            if reply_markup and i == len(chunks) - 1:
                payload["reply_markup"] = reply_markup
            try:
                resp = requests.post(url, json=payload, timeout=15)
                body = resp.json() if resp.content else {}
                if resp.status_code != 200 or not body.get("ok", False):
                    logger.error(f"텔레그램 전송 실패: status={resp.status_code}, body={resp.text}")
                    return None
                mid = ((body.get("result") or {}).get("message_id"))
                if first_id is None and mid is not None:
                    first_id = int(mid)
                logger.info("텔레그램 메시지 전송 성공")
            except Exception as e:
                logger.error(f"텔레그램 전송 중 오류: {e}")
                return None
        return first_id

    def get_updates(self, offset: Optional[int] = None) -> List[dict]:
        """버튼 응답만 짧게 조회. 대기하지 않는다."""
        if not self.bot_token:
            return []
        params: dict = {"timeout": 0}
        if offset:
            params["offset"] = int(offset)
        try:
            resp = requests.get(
                f"{self.API_BASE}/bot{self.bot_token}/getUpdates",
                params=params,
                timeout=10,
            )
            body = resp.json() if resp.content else {}
            if resp.status_code != 200 or not body.get("ok"):
                logger.error(f"텔레그램 수신 실패: status={resp.status_code}, body={resp.text}")
                return []
            return list(body.get("result") or [])
        except Exception as e:
            logger.error(f"텔레그램 수신 중 오류: {e}")
            return []

    def answer_callback(self, callback_id: str, text: str) -> None:
        if not self.bot_token or not callback_id:
            return
        try:
            requests.post(
                f"{self.API_BASE}/bot{self.bot_token}/answerCallbackQuery",
                json={"callback_query_id": callback_id, "text": text[:180]},
                timeout=10,
            )
        except Exception as e:
            logger.debug(f"텔레그램 버튼 응답 실패: {e}")

    def clear_reply_markup(self, message_id: int) -> None:
        if not self.is_configured() or not message_id:
            return
        try:
            requests.post(
                f"{self.API_BASE}/bot{self.bot_token}/editMessageReplyMarkup",
                json={"chat_id": self.chat_id, "message_id": int(message_id), "reply_markup": {"inline_keyboard": []}},
                timeout=10,
            )
        except Exception as e:
            logger.debug(f"텔레그램 버튼 제거 실패: {e}")
