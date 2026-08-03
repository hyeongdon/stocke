"""매도가능수량 부족(800033) 판별 — 이미 청산 후 중복 주문 복구용."""

from managers.stop_loss_manager import is_sell_qty_shortage_error


def test_detects_kiwoom_800033_message():
    msg = "[2000](800033:모의투자 매도가능수량이 부족합니다.)"
    assert is_sell_qty_shortage_error(msg) is True


def test_detects_plain_shortage_text():
    assert is_sell_qty_shortage_error("매도가능수량이 부족합니다") is True


def test_ignores_unrelated_errors():
    assert is_sell_qty_shortage_error("토큰 없음") is False
    assert is_sell_qty_shortage_error("") is False
    assert is_sell_qty_shortage_error(None) is False
