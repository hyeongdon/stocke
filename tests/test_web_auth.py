from core.config import Config
import utils.web_auth as web_auth
from utils.web_auth import (
    create_trusted_device_token,
    trusted_device_username,
)


def test_trusted_device_token_round_trip():
    token = create_trusted_device_token(Config.AUTH_USERNAME)

    assert trusted_device_username(token) == Config.AUTH_USERNAME


def test_trusted_device_token_rejects_tampering():
    token = create_trusted_device_token(Config.AUTH_USERNAME)

    assert trusted_device_username(token + "tampered") is None


def test_trusted_device_token_rejects_another_username(monkeypatch):
    token = create_trusted_device_token(Config.AUTH_USERNAME)
    monkeypatch.setattr(Config, "AUTH_USERNAME", "another-user")

    assert trusted_device_username(token) is None


def test_trusted_device_token_expires(monkeypatch):
    token = create_trusted_device_token(Config.AUTH_USERNAME)
    monkeypatch.setattr(web_auth, "TRUSTED_DEVICE_MAX_AGE_SECONDS", -1)

    assert trusted_device_username(token) is None
