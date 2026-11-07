from notifications.condition_alert import build_message


def test_build_message_lists_stock_names_without_market_data():
    message = build_message(
        [
            (
                {"condition_id": "1", "condition_name": "돌파"},
                [
                    {
                        "stock_name": "삼성전자",
                        "stock_code": "005930",
                        "current_price": "71000",
                        "price_diff": "1500",
                        "change_rate": "2.1",
                        "volume": "1200000",
                    },
                ],
            ),
        ],
        max_stocks=10,
    )

    assert "삼성전자(005930)" in message
    assert "71000" not in message
    assert "1,500" not in message
    assert "2.10%" not in message
    assert "1.2M" not in message
