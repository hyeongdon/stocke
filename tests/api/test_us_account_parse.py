"""미국주식 계좌 응답 파서 단위 테스트 (네트워크 없음)."""

from api.kiwoom_account import _AccountMixin


def test_parse_us_holdings_basic():
    raw = {
        "return_code": 0,
        "result_list": [
            {
                "stk_cd": "AAPL",
                "stk_nm": "Apple",
                "stex_tp": "ND",
                "poss_qty": "10",
                "sell_alowq": "10",
                "frgn_stk_book_uv": "180.5000",
                "now_pric": "+190.2500",
                "evlt_amt": "1902.50",
                "pur_amt": "1805.00",
                "pl_amt": "+97.50",
                "pl_rt": "5.40",
            },
            {
                "stk_cd": "EMPTY",
                "poss_qty": "0",
                "now_pric": "1",
            },
        ],
    }
    rows = _AccountMixin._parse_us_holdings(raw)
    assert len(rows) == 1
    h = rows[0]
    assert h["code"] == "AAPL"
    assert h["name"] == "Apple"
    assert h["exchange"] == "ND"
    assert h["qty"] == 10.0
    assert h["avg_usd"] == 180.5
    assert h["cur_usd"] == 190.25
    assert abs(h["eval_amt"] - 1902.5) < 1e-6
    assert abs(h["pl_amt"] - 97.5) < 1e-6
    assert abs(h["pl_rt"] - 5.4) < 1e-6


def test_parse_us_holdings_typo_key_and_estimate():
    raw = {
        "result_lsit": [
            {
                "stk_cd": "QLD",
                "poss_qty": "2.5",
                "frgn_stk_book_uv": "80",
                "now_pric": "88.36",
            }
        ]
    }
    rows = _AccountMixin._parse_us_holdings(raw)
    assert len(rows) == 1
    assert rows[0]["code"] == "QLD"
    assert abs(rows[0]["eval_amt"] - 2.5 * 88.36) < 1e-6
    assert abs(rows[0]["buy_amt"] - 2.5 * 80) < 1e-6


def test_parse_us_deposit_d0_field():
    dep = _AccountMixin._parse_us_deposit({
        "return_code": 0,
        "d0_usd_fx_entr": "1234.56",
        "exrt": "1350.12",
    })
    assert abs(dep["usd_deposit"] - 1234.56) < 1e-6
    assert dep["usd_deposit_field"] == "d0_usd_fx_entr"
    assert abs(dep["exrt"] - 1350.12) < 1e-6


def test_parse_us_deposit_nested():
    dep = _AccountMixin._parse_us_deposit({
        "return_code": 0,
        "detail": {"usd_fx_entr": "+99.10"},
    })
    assert abs(dep["usd_deposit"] - 99.10) < 1e-6
    assert dep["usd_deposit_field"] == "detail.usd_fx_entr"
