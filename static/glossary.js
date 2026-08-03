'use strict';

/** 사이트 UI·설정·로그에 등장하는 용어 */
const GLOSSARY = [
  {
    cat: '전략 프로필',
    items: [
      { t: '전략 프로필 / strategy_key', tags: 'legacy sangtta breakout ymgp', d: '진입·청산 규칙이 묶인 전략 단위. 포지션·신호에 strategy 태그로 남고, 슬롯·시간대·손절이 전략별로 갈립니다.', f: '우선순위(같은 종목): 상따 > 수급 돌파 > 역매공파 > 레거시' },
      { t: '게이트 패키지', tags: 'gate_pack', d: '전략별 진입 조건 AND 묶음. 예: sangtta_breakout, oversold_breakout, yeokmaegongpa, legacy_momentum.' },
      { t: '레거시 (legacy)', tags: '거래대금 눌림목', d: '거래대금 상위·스크리너·관심종목 후보에 당일 품질 게이트(시가/VWAP/당일위치/일봉 RSI 등)를 적용하는 기본 전략.' },
      { t: '상따 (sangtta)', tags: '상한가 따라잡기', d: '장초·소형·급등 구간에서 상한가 근접 돌파를 노리는 전략. 청산은 상한가 이탈·급락 HARD/SOFT 중심.' },
      { t: '수급 돌파 (breakout)', tags: 'oversold_breakout volume_breakout 구:과매도돌파', d: '조건식(5분 RSI 전환·완화, 예: ≤35 회복) 유니버스에서 5분봉 장대·거래량·MA20 돌파 시 진입. MA20은 돌파봉 포함 N봉 유예(breakout_ma20_grace_bars) 가능. 극단 과매도(RSI≤30) 전략이 아님. 청산은 구조 이탈 → 고정손절 → 트레일. 오버나잇 허용. 분봉은 통합(_AL).' },
      { t: '역매공파 (ymgp)', tags: 'yeokmaegongpa', d: '역배열 바닥에서 매집봉·공구리 확인 후 돌파(1차)·눌림(2차) 진입. 손절은 기준봉 저·손절 MA, 익절은 박스고점→MA224→MA448 분할. 오버나잇 허용.' },
      { t: '종가배팅 (jongga)', tags: 'closing_bet jongga_closing', d: '14:30 전후 거래대금순 상위 종목을 테마로 묶어 당일 최강 테마에서 1종 매수. 14:30~40 대시보드 선택, 미선택 시 눌림(고가대비)·대금·등락 스코어 자동. 청산은 익일 고정손절+트레일. 미매핑은 미분류.' },
      { t: '전략 슬롯', tags: 'max_slots', d: '전략별로 동시에 잡을 수 있는 포지션(신호 포함) 상한. 총 「동시 보유 슬롯」 안에서 추가로 제한합니다.' },
      { t: '전략 매수 시간창', tags: 'trade_start/end', d: '전략마다 신규 매수가 허용되는 시각. 스캐너·매수 실행기 가동 구간은 전략 시간창의 합집합입니다.' },
    ],
  },
  {
    cat: '역매공파',
    items: [
      { t: '역배열', tags: 'MA 단기<중기<장기', d: '단기 이평이 중·장기 이평보다 아래에 있는 하락 구조. 기본 판정: MA120 < MA240 < MA480. (정배열의 반대)', f: '정배열 = 단기 > 중기 > 장기' },
      { t: '정배열', tags: '상승 이평', d: '단기 > 중기 > 장기 이평. 역매공파 필터 대상이 아님.' },
      { t: '매집봉', tags: 'accum bar', d: '거래량이 최근 평균 대비 배수(기본 2배) 이상이고, 종가가 시가·중간값 위인 양봉. 확인되면 기준봉으로 저장됩니다.' },
      { t: '공구리', tags: '바닥 다지기', d: '박스권에서 20·60·112일선 중 하나 이상을 회복하거나 근접한 상태. 매집봉과 함께 ARMED 조건.' },
      { t: '기준봉', tags: 'ref bar', d: '매집봉으로 확정된 일봉. 고점(돌파 진입)·저점(손절)·시가(2차 눌림 앵커)를 제공합니다.', f: 'ref_high / ref_low / ref_open' },
      { t: '박스권', tags: 'box_days', d: '최근 N일(기본 15) 고저폭이 설정 % 이내인 횡보 구간. READY 판정·T1 익절 목표가(박스 고점)에 사용.' },
      { t: '급락 후 횡보', tags: 'drop_sideways', d: '박스 직전 lookback일 고점 대비 박스 중간가가 drop_pct(기본 −20%) 이하로 내려온 뒤, 최근 박스가 형성된 상태.' },
      { t: '유동성 하한', tags: '거래량·대금', d: '거래가 너무 적은 종목을 빼기 위한 최소 거래량/거래대금. HTS 조건식(유니버스)에서 거는 필터. 서버 역매공파 엔진에는 별도 키가 없음.' },
      { t: 'ymgp 단계 (stage)', tags: 'FILTERED READY ARMED', d: 'NONE→FILTERED(역배열)→READY(박스·지지)→ARMED(매집·공구리)→ENTERED_1/2→MANAGING→DONE. STOPPED는 손절 후 재진입 락.', f: '매수는 보통 ARMED + 고점 돌파에서만' },
      { t: '1차 진입 (돌파)', tags: 'entry_leg 1', d: 'ARMED 후 현재가가 기준봉 고점(또는 설정 시 전일고)을 상향 돌파할 때 소액 매수.' },
      { t: '2차 진입 (눌림)', tags: 'pullback add', d: '1차 보유 중 MA20 또는 기준봉 시가 근처에서 지지될 때 추가 매수. 토글(ymgp_enable_pullback_add)로 ON/OFF.' },
      { t: '재진입 락', tags: 'reentry_lock', d: '손절(STOPPED) 후 N일(기본 5) 동안 같은 종목 신규 역매공파 매수 금지. 매집·박스 재형성 후 재공략.' },
      { t: '분할익절 T1', tags: '박스 고점', d: '목표가 = 최근 박스권 최고가. 현재가 ≥ 목표가 시 보유 × tp1 비중(기본 35%) 매도.' },
      { t: '분할익절 T2', tags: 'MA224', d: '목표가 = 224일 이동평균. T1 이후 현재가 ≥ MA224 시 남은 수량 × tp2 비중 매도.' },
      { t: '분할익절 T3', tags: 'MA448', d: '목표가 = 448일 이동평균. 도달 시 잔량 전량 매도.' },
      { t: '손절 MA (역매공파)', tags: 'ma60 ma112', d: '종가가 설정한 60일선·112일선(또는 either) 아래로 가면 전량 손절. 기준봉 저점 이탈·고정 손절%와 함께 사용.' },
    ],
  },
  {
    cat: '청산·손익',
    items: [
      { t: 'ATR', tags: '변동성 손절 트레일', d: 'Average True Range. 최근 일봉 기준 하루 평균 가격 변동폭(원). 변동이 큰 종목은 손절·트레일 간격을 넓게 잡습니다. 수급 돌파·역매공파는 주로 %·구조 규칙을 씁니다.', f: '손절선 ≈ 매수가 − ATR×손절배수 · 트레일선 ≈ 고점 − ATR×트레일배수' },
      { t: 'PCT', tags: '퍼센트 % 손절', d: 'Percentage. 매수가 대비 고정 %로 잡는 손절·트레일 방식. 포지션 카드·칩에 (PCT)로 표시됩니다.', f: '손절가 ≈ 매수가 × (1 − 손절%)' },
      { t: '유효 손절선', tags: 'effective stop', d: '손절 후보(%, ATR, 트레일 등) 중 실제 매도 판단에 쓰이는 가장 높은 가격선. 현재가가 이 선 이하로 내려가면 매도합니다.' },
      { t: '손절 (STOP_LOSS)', tags: '손실 한도', d: '설정한 손실 %·ATR·구조 이탈 등으로 전량(또는 잔량) 매도하는 규칙.', f: '사유 코드: STOP_LOSS' },
      { t: 'HARD / SOFT', tags: '연속 확인', d: '이탈 강도. HARD는 즉시 매도, SOFT는 설정 횟수(soft_confirm_polls) 연속 충족 시 매도. 상따(상한가·급락)·수급 돌파(구조 이탈)에 사용.' },
      { t: '구조 이탈', tags: 'struct break', d: '수급 돌파에서 진입 시 저장한 돌파 레벨 아래로 가격이 깨지는 것. 고정손절·트레일보다 먼저 평가.' },
      { t: '고정 손절 (STOP_LOSS %)', tags: '매수가 손절', d: '매수가 대비 −N% 이하로 내려가면 청산. 고점·트레일과 무관하며 진입 직후부터 적용. 돌파는 breakout_stop_loss_pct.' },
      { t: '트레일링 (TRAILING)', tags: '트레일 스탑', d: '고점이 「시작 %」 이상 오른 뒤에만 켜짐. 이후 고점 대비 하락폭%에서 청산. 시작%를 못 찍으면 고정손절만 동작. 4% 트레일 ≠ 3% 고정손절.' },
      { t: '트레일링 시작 / armed', tags: '익절 시작%', d: '고점이 「트레일 시작 %」에 도달하면 트레일이 켜집니다. 즉시 전량 익절이 아닙니다. 레거시는 take_profit_rate, 돌파·역매공파는 전략 전용 키.' },
      { t: '익절 바닥', tags: '트레일 바닥 floor', d: '트레일링 armed 후 트레일링선이 내려갈 수 있는 최저 한도. 바닥 이하로는 매도선이 내려가지 않습니다.' },
      { t: '익절 (TAKE_PROFIT)', tags: '이익 실현', d: '목표 가격·수익률 도달 시 매도. 역매공파는 T1~T3 분할, 그 외는 주로 트레일링 시작과 연동.' },
      { t: '분할 익절 / 부분 매도', tags: 'partial TP', d: '전량이 아니라 보유 수량의 일부만 매도. 역매공파 T1·T2. 체결 후 포지션 잔량이 줄어듭니다.' },
      { t: '수익 잠금 (PROFIT_LOCK)', tags: 'PROFIT_LOCK', d: '일정 수익% 도달 후 최소 수익 바닥을 확보하는 청산 규칙(고급 설정). 돌파·역매공파 경로에서는 보통 비우선.' },
      { t: '장마감 청산 (MARKET_CLOSE)', tags: '오버나잇 방지', d: '설정 시각(liquidate_time, 기본 15:10) 이후 보유를 전량 매도. 수급 돌파·역매공파는 오버나잇 허용으로 이 규칙에서 제외.' },
      { t: '수동 청산 (MANUAL)', tags: 'MANUAL', d: '대시보드 보유 포지션에서 사용자가 직접 시장가 전량 매도를 요청한 경우.' },
      { t: '청산 / 매도', tags: 'exit sell', d: '보유 주식을 팔아 포지션을 닫는 것. 활동 로그·검증의 「청산」은 보통 포지션 완전 매도를 뜻하나, 분할 익절은 부분 매도일 수 있습니다.' },
      { t: '실현 손익', tags: 'realized PnL', d: '매도 체결로 확정된 손익. 성과 통계·검증의 합계는 보통 실현 손익 기준입니다.' },
      { t: '평가 손익', tags: '미실현', d: '아직 팔지 않은 보유 종목의 현재가 기준 손익. 키움 API·포지션 카드에 표시됩니다.' },
      { t: '매수 시점 ATR', tags: 'buy_atr 스냅샷', d: '포지션 생성 시 저장한 ATR 값. 검증 화면 청산식·당시 기준 설명에 사용하며 API 재조회 없이 표시합니다.' },
    ],
  },
  {
    cat: '진입·매수',
    items: [
      { t: '진입 타이밍 게이트', tags: 'entry gate', d: '스크리너 통과 후에도 당일 시세 조건을 만족할 때만 매수하는 필터(레거시). 상따·돌파·역매공파는 각자 게이트 패키지를 씁니다.' },
      { t: 'VWAP', tags: '거래량 가중 평균', d: 'Volume Weighted Average Price. 장중 분봉 기준 거래량 가중 평균가. 「현재가 ≥ VWAP」은 당일 강세 확인용(레거시 게이트).' },
      { t: '당일 위치', tags: 'day position', d: '0~1. (현재가−당일저가)÷(당일고가−당일저가). 0.5면 당일 범위 중간, 1에 가까우면 고가 근처.' },
      { t: '고가 근접', tags: 'high proximity', d: '현재가가 당일 고가 대비 설정 % 이내에 있을 때만 매수하는 조건.' },
      { t: '거래량 비율', tags: 'volume ratio', d: '전일 대비 당일 거래량 %. 하한을 두면 거래가 살아 있는 종목만 매수합니다.' },
      { t: '일봉 RSI(14)', tags: 'legacy_rsi_min legacy_rsi_max', d: '레거시 진입 게이트용 Wilder RSI(14). 설정한 하한·상한 밖이면 매수하지 않음. 상한 예: 75(과열 차단). 비우면 미적용.' },
      { t: '돌파 레벨', tags: 'level_price', d: '수급 돌파에서 넘어야 할 가격(직전 5분봉 고가 또는 최근 N봉 고가). 진입·구조 이탈 청산에 사용.' },
      { t: '진입 확인 HARD/SOFT/HOLD', tags: 'breakout entry', d: '수급 돌파 전용. HARD=완성봉이 레벨 돌파 시 즉시, SOFT=레벨 위 연속 N회, HOLD=돌파 후 구조·RSI 유지 확인.' },
      { t: 'MA20 유예', tags: 'breakout_ma20_grace_bars', d: '수급 돌파에서 레벨 돌파 직후 종가가 아직 MA20 아래여도, 돌파봉 포함 N개 5분 완성봉 안에 MA20 판정(상회/상향돌파)을 충족하면 매수. 기본 3=돌파+후속 2봉. 1이면 유예 없음. 대기 중에는 WATCHING으로 추적(조건식 이탈과 무관). 장대·거래량은 돌파봉 상속.', f: '로그 예: 관측(WATCHING) · MA20 유예 대기 (2/3봉) · 관측→매수대기 승격' },
      { t: 'MA20 판정 (above/cross)', tags: 'breakout_ma20_mode', d: 'above=확인봉 종가>MA20. cross=아래에서 위로 뚫는 상향 돌파(전봉 대비 classic·봉중·저가 reclaim). 유예창에도 같은 판정을 씀. 갭 장초는 above 권장.' },
      { t: '과열 컷', tags: 'max_change_pct', d: '당일 등락률이 상한 이상이면 신규 매수 금지. 이미 과도하게 오른 종목 추격 완화.' },
      { t: '역피라미딩 (PYRAMIDING)', tags: '분할매수', d: '등락률이 높을수록 초기 매수 금액을 줄이는 방식. 급등·고변동 구간의 손절 리스크를 낮춥니다.' },
      { t: '고정 금액 (FIXED)', tags: 'FIXED', d: '종목당 매수 금액을 고정하는 사이징 방식.' },
      { t: '예수금 비중 매수', tags: 'deposit_pct', d: '고정 원화가 아니라 예수금의 N%로 1회 매수 금액을 환산. 전략별·레거시 공통 설정에 있음.' },
      { t: '약한 신호 / 강한 신호', tags: '등락% 임계', d: '역피라미딩에서 당일 등락률 구간별 매수 금액 기준. 약한 신호는 큰 금액, 강한 신호는 작은 금액.' },
      { t: '추가 매수', tags: 'add buy', d: '이미 보유 중인 종목에 더 사는 매수. 레거시 피라미딩 트리거 또는 역매공파 2차 눌림.' },
      { t: '매수 신호', tags: 'PendingBuySignal', d: '스캐너가 생성한 매수 관련 레코드. WATCHING(관측)→PENDING(매수대기)→PROCESSING→ORDERED→FILLED/FAILED. WATCHING은 MA20 유예·진입확인 대기 등이며 슬롯을 점유하지 않고, 조건식 이탈과 무관하게 차트 재평가합니다.' },
      { t: 'WATCHING (관측)', tags: 'wait_kind ma20_grace', d: '수급 돌파에서 레벨·거래량 등은 됐지만 MA20 유예·HARD/SOFT/HOLD 대기일 때. 주문·체결 로그에는 안 남고, 통과 시 PENDING으로 승격. 유예 만료·레벨 이탈 시 FAILED.', f: 'WATCHING → PENDING → 주문' },
      { t: '재주문 쿨다운', tags: 'cooldown', d: '같은 종목 신호/매도 후 N초 동안 재매수 신호를 막는 시간. WATCHING은 쿨다운에 포함하지 않음.' },
    ],
  },
  {
    cat: '포지션·계좌',
    items: [
      { t: '포지션', tags: 'Position HOLDING', d: '매수 체결 후 추적하는 1건의 보유 기록. 종목·수량·매입가·strategy_key·고점 등을 담습니다.' },
      { t: '비중', tags: 'weight', d: '해당 종목 평가금액 ÷ 키움 주식 총평가(%). 포지션 카드에 표시됩니다.' },
      { t: '고점 (peak)', tags: 'peak price', d: '진입 후 기록된 최고가. 트레일링·익절 바닥 계산에 사용됩니다.' },
      { t: '동시 보유 슬롯', tags: 'max concurrent', d: '한 번에 가질 수 있는 신규 매수 종목 수(보유+매수대기 포함). 설정의 「최대 동시 보유 종목」.' },
      { t: '현금 보유율', tags: 'cash reserve', d: '예수금 중 매수에 쓰지 않고 남겨 둘 비율(%). 「매수 가능」금액 계산에 반영됩니다.' },
      { t: '매수 가능', tags: 'investable', d: '예수금(D+0)에서 현금 보유율만큼 뺀 금액. D+2 추정예수금보다 크면 D+2로 상한. 보유주식 평가액은 포함되지 않습니다.' },
      { t: 'D+2 추정예수금', tags: '결제일', d: '키움 kt00004의 d2_entra. 결제 반영 후 추정 현금. 총 평가자산과 같게 나오는 경우도 있어 대시보드에 별도 표기합니다.' },
      { t: '총 평가자산', tags: '추정예탁자산', d: '키움 prsm_dpst_aset_amt. 예수금 + 주식 평가 등을 합산한 계좌 추정 자산입니다.' },
      { t: '부분 체결', tags: 'partial fill', d: '주문 수량보다 적게 체결된 경우. 검증·포지션에서 주문/체결 수량 차이로 표시됩니다.' },
      { t: '잔고 드리프트', tags: 'account drift', d: '키움 계좌 잔고와 DB HOLDING이 어긋난 상태. 동기화·Ops 관측 대상.' },
    ],
  },
  {
    cat: '스캐너·조건식',
    items: [
      { t: '스크리너', tags: 'screener', d: '거래대금 상위 등 조건으로 자동매매 후보 종목을 고르는 기능. 스캔 주기는 설정(scan_interval)에 따릅니다.' },
      { t: '거래대금순', tags: 'volume rank', d: '당일 거래대금이 큰 순서로 종목을 정렬해 후보를 뽑는 방식(레거시). 스크리너에서는 등락률 밴드·거래대금 하한(기본 20억)을 적용한다.' },
      { t: '관심종목', tags: 'watchlist', d: '설정에 등록한 종목 코드. 스크리너와 함께 매수 후보에 포함됩니다.' },
      { t: '조건식 (유니버스)', tags: 'HTS condition', d: '키움 HTS에 만든 종목 필터. 돌파·역매공파는 전략별 전용 조건식을 씁니다. 레거시는 거래대금순, 상따는 ka10027 등락률상위 API를 씁니다.' },
      { t: '상따 유니버스 (ka10027)', tags: 'sangtta change rate rank', d: '전일대비등락률상위(≥13%·천원↑·대금10억↑·ETF제외) 풀에서 거래대금순 상위 N(기본 20)만 스캔. 게이트(등락 밴드·시총 등)가 한 번 더 거릅니다.' },
      { t: '검증 전용 조건식', tags: 'verify_condition', d: '실매매 주문에 쓰이지 않는 조건식. 당일 검증·15분봉 시뮬용으로 실매매 식과 분리해 둡니다. 예: 검증(역매공파).' },
      { t: '유니버스', tags: '후보 풀', d: '전략이 볼 수 있는 종목 집합. 보통 전용 조건식(+관심)이며, 전종목 일봉 전수 스캔은 하지 않습니다(API 비용).' },
      { t: 'KRX', tags: '코스피 코스닥', d: '한국거래소 상장 주식. 스크리너는 KRX 개별주만 대상으로 합니다.' },
      { t: 'NXT / 통합(_AL)', tags: '넥스트레이드 대체거래소', d: '같은 종목도 KRX·NXT 분봉이 다를 수 있음. 분봉 게이트·시뮬·검증 차트는 키움 `{코드}_AL`(KRX+NXT 통합)으로 조회해 HTS 통합 차트와 MA·거래량을 맞춘다. 일봉·주문 코드는 6자리 유지.' },
      { t: '개별주', tags: 'STOCK', d: '일반 상장 주식. ETF·ETN·레버리지·인버스·SPAC 등은 스크리너에서 제외됩니다.' },
    ],
  },
  {
    cat: '장세·리스크',
    items: [
      { t: '장세 게이트 (market risk)', tags: '코스피 코스닥', d: '지수 등락이 임계(예: −2%)보다 나쁠 때 전략별 신규 매수 횟수를 제한하거나 막는 규칙.' },
      { t: '일일 손익 한도', tags: 'daily loss/profit', d: '당일 실현 손익이 손실 한도·이익 목표에 닿으면 신규 매수를 중단하는 계좌 가드. 한도를 완화해 저장하면 허용 구간이면 자동매매가 다시 켜집니다. 보유 종목 손절 모니터는 중단되지 않습니다.' },
      { t: '일일 매수 한도', tags: 'max_daily_buys', d: '하루 신규 매수 건수 상한(전략 합산).' },
    ],
  },
  {
    cat: '시스템·로그',
    items: [
      { t: '자동매매 엔진', tags: 'is_enabled', d: '설정 ON 시 스캐너·매수 실행기가 장중 동작합니다. OFF면 신규 매수만 멈춥니다. 손절/익절·잔고 동기화 루프는 보유 종목 보호를 위해 ON/OFF와 무관하게 장중 유지됩니다(장마감 청산 설정은 별도).' },
      { t: '스캐너 (AUTO_SCANNER)', tags: 'SCANNER', d: '후보 종목을 돌며 매수 신호를 만드는 모듈. 활동 로그에 SCANNER로 표시됩니다.' },
      { t: '매수 실행기 (BUY)', tags: 'BUY_EXECUTOR', d: '대기 중인 매수 신호를 검증하고 키움 API로 주문합니다.' },
      { t: '손절 모니터 (SELL)', tags: 'STOP_LOSS', d: '보유 포지션의 청산 조건을 주기적으로 확인하고 매도 주문합니다. 자동매매 OFF·일일 한도 중단 시에도 장중 계속 동작합니다.' },
      { t: 'reconcile / 동기화', tags: 'SYNC', d: '키움 계좌 잔고와 DB 포지션·매도 체결 상태를 맞추는 처리.' },
      { t: 'live=true', tags: '실시간 조회', d: '포지션 API에서 현재가·ATR을 키움에 다시 물어보는 모드. 자동매매 탭은 주기적으로 live로 갱신합니다.' },
      { t: '슬롯 정리', tags: 'FAILED 신호', d: '만료된 ORDERED 매수 신호를 FAILED 처리해 동시 보유 한도가 풀리게 하는 내부 정리.' },
      { t: '하트비트 / stale', tags: 'ops health', d: '스캐너·매수·손절 루프가 최근 사이클을 돌았는지. 기대 주기를 넘기면 stale(지연)로 봅니다. Ops 관측 PRD.' },
      { t: '트레이 알림', tags: 'tray_notify', d: 'Windows 트레이로 보내는 체결·경고 알림. 텔레그램과 병행할 수 있습니다.' },
    ],
  },
  {
    cat: '검증·성과',
    items: [
      { t: '검증 (verify)', tags: '라운드트립', d: '매수·매도 시각, 진입/청산 조건 체크리스트, 계산식을 한 건씩 보여 주는 화면.' },
      { t: '당일 검증', tags: 'day verify', d: '검증 전용 조건식 편입 종목에 대해 게이트·15분봉 시뮬을 돌리는 기능. 주문 없음.' },
      { t: '청산 1건', tags: 'closed trade', d: '성과 통계에서 포지션 1개를 완전히 매도한 것 = 거래 1건으로 집계합니다.' },
      { t: '진입 판정', tags: 'entry verdict', d: '검증에서 매수 조건·체결 상태를 OK / CHECK / FAIL로 요약한 결과.' },
      { t: '15분봉 차트', tags: 'verification chart', d: '검증 카드를 펼치면 매수·매도 시각이 표시된 당일(또는 구간) 15분봉. API는 카드 열 때만 호출합니다.' },
      { t: '전략 리플레이', tags: 'exit replay', d: '과거 일봉·분봉으로 전략 진입·청산을 시뮬하는 화면. 실제 주문과 무관.' },
      { t: '키움 실현손익', tags: 'ka10073', d: '성과 통계 소스를 키움 API 실현손익으로 볼 때의 모드(DB 청산과 숫자가 다를 수 있음).' },
    ],
  },
];

function esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function renderGlossary(filter) {
  const q = (filter || '').trim().toLowerCase();
  const el = document.getElementById('glossaryBody');
  if (!el) return;
  let html = '';
  let count = 0;
  for (const sec of GLOSSARY) {
    const items = sec.items.filter((it) => {
      if (!q) return true;
      const blob = [it.t, it.tags, it.d, it.f].filter(Boolean).join(' ').toLowerCase();
      return blob.includes(q);
    });
    if (!items.length) continue;
    html += `<section class="gloss-section"><h2>${esc(sec.cat)}</h2><div class="gloss-grid">`;
    for (const it of items) {
      count += 1;
      html += `<article class="gloss-card" id="term-${esc(it.t.replace(/\s+/g, '-'))}">
        <h3>${esc(it.t)}</h3>
        ${it.tags ? `<div class="gloss-tags">${esc(it.tags)}</div>` : ''}
        <p class="gloss-desc">${esc(it.d)}</p>
        ${it.f ? `<div class="gloss-formula">${esc(it.f)}</div>` : ''}
      </article>`;
    }
    html += '</div></section>';
  }
  if (!html) {
    el.innerHTML = '<div class="empty"><span class="ico">🔍</span>검색 결과가 없습니다.</div>';
  } else {
    el.innerHTML = html;
  }
  const meta = document.getElementById('glossMeta');
  if (meta) meta.textContent = q ? `검색 "${filter}" · ${count}개` : `총 ${count}개 용어`;
}

document.addEventListener('DOMContentLoaded', () => {
  const input = document.getElementById('glossSearch');
  renderGlossary('');
  if (input) {
    let t;
    input.addEventListener('input', () => {
      clearTimeout(t);
      t = setTimeout(() => renderGlossary(input.value), 120);
    });
  }
  const hash = decodeURIComponent(location.hash.replace(/^#/, ''));
  if (hash) {
    setTimeout(() => {
      const id = hash.startsWith('term-') ? hash : `term-${hash}`;
      document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 80);
  }
});
