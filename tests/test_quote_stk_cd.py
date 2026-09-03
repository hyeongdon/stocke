"""시세 조회용 종목코드(_AL/_NX) 헬퍼."""
from api.kiwoom_api import KiwoomAPI


def test_quote_stk_cd_venues():
    assert KiwoomAPI.quote_stk_cd("005930") == "005930_AL"
    assert KiwoomAPI.quote_stk_cd("005930", "AL") == "005930_AL"
    assert KiwoomAPI.quote_stk_cd("005930", "NX") == "005930_NX"
    assert KiwoomAPI.quote_stk_cd("005930", "NXT") == "005930_NX"
    assert KiwoomAPI.quote_stk_cd("005930", "KRX") == "005930"


def test_quote_stk_cd_strips_suffix():
    assert KiwoomAPI.quote_stk_cd("005930_NX", "AL") == "005930_AL"
    assert KiwoomAPI.minute_chart_stk_cd("A005930") == "005930_AL"
