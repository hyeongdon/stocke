# 상따(상한가 따라잡기) — 키움(HTS) 조건식 예시

아래는 PRD 기준의 상따 전용 조건식을 HTS(키움) 조건식으로 작성할 때 참고할 예시입니다.

핵심 필터
- 시가총액 제한: 시가총액 ≤ 3,000억
- 등락률 밴드: +15% ≤ 등락률 ≤ +19% (상한가 미도달)
- 시가 대비 상승: 현재가 ≥ 시가 × 1.03 또는 현재가 ≥ 시가
- 전일 대비 거래량 급증: 당일 거래량 ≥ 전일 거래량 × 2
- 이미 상한가 종목 제외: 현재가 < 상한가
- 제외: ETF/ETN/스팩/우선주/관리종목 등

키움 HTS 예시 (문법은 HTS 조건식 작성 환경에 따라 약간 차이납니다 — 아래는 의사표현 예시)

1) 시총(시가총액) 필터
    MARKET_CAP <= 300000000000  # 원 단위(예시 환경에 따라 단위 조정)

2) 등락률 밴드
    CHANGE_RATE >= 15 && CHANGE_RATE <= 19

3) 시가 대비 돌파
    CURRENT_PRICE >= OPEN_PRICE * 1.03

4) 거래량 급증
    VOLUME >= PREV_VOLUME * 2

5) 상한가 배제
    CURRENT_PRICE < UPPER_LIMIT_PRICE

조합 예시 (의사 코드)

    MARKET_CAP <= 300000000000
    && CHANGE_RATE >= 15 && CHANGE_RATE <= 19
    && CURRENT_PRICE >= OPEN_PRICE * 1.03
    && VOLUME >= PREV_VOLUME * 2
    && CURRENT_PRICE < UPPER_LIMIT_PRICE
    && NOT (IS_ETF || IS_ETN || IS_SPAC || IS_PREFERRED || IS_MANAGEMENT)

등록 및 운영
- HTS에서 조건식을 만든 뒤 조건식 이름을 `sangtta_condition_names` 설정에 추가하거나,
  스크립트 `scripts/register_sangtta_condition.py`로 DB에 등록하세요.

예:
  python scripts/register_sangtta_condition.py "SANGTTA_15_19_SMALLCAP" 12345

운영 팁
- Phase0: 조건식은 관찰용(로그)으로 먼저 켜서 후보 품질을 검증하세요.
- Phase1: 소액 실매수 전, 분봉 급증(1~5분) 확인을 추가하면 노이즈를 줄일 수 있습니다.
- 상한가 가격 계산(UPPER_LIMIT_PRICE)은 전일종가 기반 ±시장 규칙(예: 30%)으로 코드에서 계산하여 동기화하세요.

