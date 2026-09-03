"""웹 UI 세션 인증 — 환경변수 자격증명 + PBKDF2 해시 검증."""
from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Optional

from core.config import Config

logger = logging.getLogger(__name__)

SESSION_USER_KEY = "auth_user"
SESSION_AT_KEY = "auth_at"

_PBKDF2_ITERATIONS = 120_000
_PBKDF2_DKLEN = 32


def _password_salt() -> bytes:
    """세션 시크릿 기반 고정 솔트 (평문 비번을 메모리 비교하지 않기 위함)."""
    material = f"stocke-web-auth|{Config.AUTH_SESSION_SECRET}".encode("utf-8")
    return hashlib.sha256(material).digest()


def hash_password(password: str) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        _password_salt(),
        _PBKDF2_ITERATIONS,
        dklen=_PBKDF2_DKLEN,
    )


_EXPECTED_PASSWORD_HASH: Optional[bytes] = None


def _expected_password_hash() -> bytes:
    global _EXPECTED_PASSWORD_HASH
    if _EXPECTED_PASSWORD_HASH is None:
        _EXPECTED_PASSWORD_HASH = hash_password(Config.AUTH_PASSWORD)
    return _EXPECTED_PASSWORD_HASH


def verify_credentials(username: str, password: str) -> bool:
    if not username or not password:
        return False
    user_ok = hmac.compare_digest(
        username.strip().encode("utf-8"),
        Config.AUTH_USERNAME.encode("utf-8"),
    )
    pass_ok = hmac.compare_digest(hash_password(password), _expected_password_hash())
    return user_ok and pass_ok


def session_max_age_seconds() -> int:
    return max(1, int(Config.AUTH_SESSION_HOURS)) * 3600


def mark_session_login(session: dict, username: str) -> None:
    session.clear()
    session[SESSION_USER_KEY] = username.strip()
    session[SESSION_AT_KEY] = datetime.now(timezone.utc).isoformat()


def clear_session(session: dict) -> None:
    session.clear()


def session_username(session: dict) -> Optional[str]:
    if not Config.AUTH_ENABLED:
        return "auth-disabled"
    user = session.get(SESSION_USER_KEY)
    at_raw = session.get(SESSION_AT_KEY)
    if not user or not at_raw:
        return None
    try:
        at = datetime.fromisoformat(str(at_raw))
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - at).total_seconds()
        if age > session_max_age_seconds():
            return None
    except Exception:
        return None
    return str(user)


def is_public_path(path: str) -> bool:
    """인증 없이 접근 가능한 경로."""
    if path in {
        "/",
        "/login",
        "/auth/login",
        "/auth/logout",
        "/health",
        "/favicon.ico",
        "/static/favicon.svg",
        "/static/login.html",
        "/docs",
        "/openapi.json",
        "/redoc",
    }:
        return True
    return False
