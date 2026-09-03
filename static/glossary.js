'use strict';

/** 사이트 UI·설정·로그에 등장하는 용어 */
const GLOSSARY = [
  {
    cat: '전략 프로필',
    items: [
      { t: '전략 프로필 / strategy_key', tags: 'legacy sangtta breakout fractal jongga', d: '진입·청산 규칙이 묶인 전략 단위. 포지션·신호에 strategy 태그로 남고, 슬롯·시간대·손절이 전략별로 갈립니다.', f: '우선순위(같은 종목): 상따 > 수급 돌파 > 레거시' },
      { t: '게이트 패키지', tags: 'gate_pack', d: '전략별 진입 조건 AND 묶음. 예: sangtta_breakout, oversold_breakout, legacy_momentum.' },
      { t: '레거시 (legacy)', tags: '거래대금 눌림목', d: '거래대금 상위·스크리너·관심종목 후보에 당일 품질 게이트(시가/VWAP/당일위치/일봉 RSI 등)를 적용하는 기본 전략. 거래대금 상위 최대 20종(기본)만 스캔하며, 1회 스캔 총한도(기본 60)에서 상따·돌파·프랙탈·관심 자리를 뺀 잔여와 교차해 더 줄어들 수 있다. 청산은 5분 EMA 이탈 SOFT(이격 1% 초과) → 고정손절 → 트레일.' },
      { t: '상따 (sangtta)', tags: '상한가 따라잡기', d: '장초·소형·급등 구간에서 상한가 근접 돌파를 노리는 전략. 청산은 상한가 이탈·급락 HARD/SOFT → 5분 EMA90 이탈 SOFT.' },
      { t: '수급 돌파 (breakout)', tags: 'oversold_breakout volume_breakout 구:과매도돌파', d: '조건식(5분 RSI 전환·완화, 예: ≤35 회복) 유니버스에서 5분봉 장대·거래량·MA20 돌파 시 진입. MA20은 돌파봉 포함 N봉 유예(breakout_ma20_grace_bars) 가능. 5분 게이트를 통과한 종목만 프로그램 시간대(ka90008, 1분칸)에서 최근 5칸 중 3칸 이상 순매수를 확인. 극단 과매도(RSI≤30) 전략이 아님. 청산은 구조 이탈 → 5분 EMA90 이탈 SOFT → 고정손절 → 트레일. 오버나잇 허용. 분봉은 통합(_AL).' },
      { t: '역매공파 (ymgp)', tags: 'yeokmaegongpa retired', d: '폐기된 전략. 신규 진입·설정 UI 없음. 과거 포지션·체결 라벨·청산 분기만 유지.' },
      { t: '프랙탈 스캘핑 (fractal)', tags: 'williams ema 눌림목', d: '1분봉 EMA20>50>100 정배열에서 20EMA 눌림 후 확정 녹색 프랙탈·20EMA 종가 재돌파로 진입. 손절은 진입 시 50EMA 아래 가격, 익절은 손절폭×1.5. 유니버스는 HTS 조건식+WATCHING 5종.' },
      { t: '전략 슬롯', tags: 'max_slots', d: '전략별로 동시에 잡을 수 있는 포지션(신호 포함) 상한. 총 「동시 보유 슬롯」 안에서 추가로 제한합니다.' },
      { t: '전략 매수 시간창', tags: 'trade_start/end', d: '전략마다 신규 매수가 허용되는 시각. 스캐너·매수 실행기 가동 구간은 전략 시간창의 합집합입니다.' },
    ],
  },
  {
    cat: '청산·손익',
    items: [
      { t: 'ATR', tags: '변동성 손절 트레일', d: 'Average True Range. 최근 일봉 기준 하루 평균 가격 변동폭(원). 변동이 큰 종목은 손절·트레일 간격을 넓게 잡습니다. 수급 돌파는 주로 %·구조 규칙을 씁니다.', f: '손절선 ≈ 매수가 − ATR×손절배수 · 트레일선 ≈ 고점 − ATR×트레일배수' },
      { t: 'PCT', tags: '퍼센트 % 손절', d: 'Percentage. 매수가 대비 고정 %로 잡는 손절·트레일 방식. 포지션 카드·칩에 (PCT)로 표시됩니다.', f: '손절가 ≈ 매수가 × (1 − 손절%)' },
      { t: '유효 손절선', tags: 'effective stop', d: '손절 후보(%, ATR, 트레일 등) 중 실제 매도 판단에 쓰이는 가장 높은 가격선. 현재가가 이 선 이하로 내려가면 매도합니다.' },
      { t: '손절 (STOP_LOSS)', tags: '손실 한도', d: '실현 손익이 −인 청산. 고정 %·ATR·상따 이탈·구조 이탈 등도 메커니즘상 STOP_LOSS로 잡힐 수 있으나, 실현 손익이 +이면 표시·통계는 익절(이탈)로 재분류합니다.', f: '사유 코드: STOP_LOSS · +수익 시 → 익절 (이탈)' },
      { t: 'HARD / SOFT', tags: '연속 확인', d: '이탈 강도. HARD는 즉시 매도, SOFT는 확인 후 매도. 상따·수급 돌파 구조 이탈은 설정 횟수(soft_confirm_polls) 연속, EMA 이탈은 연속 N분(legacy_ema_exit_soft_min).' },
      { t: '구조 이탈', tags: 'struct break', d: '수급 돌파에서 진입 시 저장한 돌파 레벨 아래로 가격이 깨지는 것. 고정손절·트레일보다 먼저 평가.' },
      { t: '고정 손절 (STOP_LOSS %)', tags: '매수가 손절', d: '매수가 대비 −N% 이하로 내려가면 청산. 고점·트레일과 무관하며 진입 직후부터 적용. 돌파는 breakout_stop_loss_pct.' },
      { t: 'EMA 이탈 SOFT', tags: 'legacy_ema_exit 거래대금 수급돌파 상따', d: '거래대금(레거시)·수급 돌파·상따 공통. 5분봉 EMA(기본 90) 대비 허용 이격(기본 1%)을 넘는 하락이, 당일·매수 이후 확정봉 2개(기본 10분) 연속 유지되고 현재가도 이탈선 아래면 전량 청산. 이격 안이면 카운트 리셋. 돌파는 구조 이탈 다음, 상따는 이탈·급락 다음에 평가.', f: 'legacy_ema_exit_period · legacy_ema_exit_band_pct · legacy_ema_exit_soft_min' },
      { t: '트레일링 (TRAILING)', tags: '트레일 스탑 익절 보호', d: '고점이 「시작 %」 이상 오른 뒤에만 켜짐. 이후 고점 대비 하락폭% 또는 익절 바닥에서 청산. 메커니즘은 트레일이지만, 실현 손익이 +이면 익절·−이면 손절로 분류합니다.', f: '수익(+) → 익절 (트레일) · 손실(−) → 손절 (트레일)' },
      { t: '트레일링 시작 / armed', tags: '익절 시작%', d: '고점이 「트레일 시작 %」에 도달하면 트레일이 켜집니다. 즉시 전량 익절이 아닙니다. 레거시는 take_profit_rate, 돌파·역매공파는 전략 전용 키.' },
      { t: '익절 바닥', tags: '트레일 바닥 floor', d: '트레일링 armed 후 트레일링선이 내려갈 수 있는 최저 한도(최소 익절가). 이 선에서 나가면 사실상 익절이며, +수익이면 익절로 기록됩니다.' },
      { t: '익절 (TAKE_PROFIT)', tags: '이익 실현', d: '실현 손익이 +인 청산. 목표가·분할 익절뿐 아니라 익절 바닥·트레일 보호·상따 상한가 이탈로 수익을 확정한 경우도 포함합니다.', f: '사유 코드: TAKE_PROFIT' },
      { t: '분할 익절 / 부분 매도', tags: 'partial TP', d: '전량이 아니라 보유 수량의 일부만 매도. 역매공파 T1·T2. 체결 후 포지션 잔량이 줄어듭니다.' },
      { t: '수익 잠금 (PROFIT_LOCK)', tags: 'PROFIT_LOCK', d: '일정 수익% 도달 후 최소 수익 바닥을 확보하는 청산 규칙(고급 설정). 돌파·역매공파 경로에서는 보통 비우선.' },
      { t: '장마감 청산 (MARKET_CLOSE)', tags: '오버나잇 슬롯', d: 'liquidate_time 이후 슬롯 정리. 당일 종가배팅은 별도 유지. 종가배팅 익일 플러스·사흘째(이틀 초과)는 슬롯과 무관하게 청산. 나머지는 overnight_keep_slots만 오버나잇.' },
      { t: '오버나잇 슬롯', tags: 'overnight_keep_slots 공통설정', d: '공통 · 포트폴리오에서 숫자를 정함. 당일 종가배팅은 종가배팅 슬롯만큼 별도 유지하고, overnight_keep_slots(기본 3)만 추가로 오버나잇. 전략당 overnight_max_per_strategy(기본 1). 종가배팅은 익일 플러스·이틀 초과 시 강제 청산.' },
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
      { t: '프로그램 순매수 칸', tags: 'ka90008 breakout_program', d: '수급 돌파 마지막 게이트. 5분 장대·거래량·MA20을 통과한 종목만 종목시간별 프로그램매매를 조회한다. 한 칸≈1분(현재 분 제외). 기본 최근 5칸 중 3칸 이상 순매수(수량>0).', f: 'N=5 · M=3 · 0은 순매수로 안 침' },
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
      { t: '스크리너', tags: 'screener', d: '거래대금 상위 등 조건으로 자동매매 후보 종목을 고르는 기능. 스캔 주기는 설정(scan_interval)에 따르며, 1회 총 대상은 SCAN_TARGET_TOTAL_LIMIT(기본 60)으로 제한되고 레거시 상위가 잔여 자리에 맞춰 축소됩니다.' },
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
      { t: '급등장 게이트 (market surge)', tags: '코스피 코스닥 +3%', d: '코스피 또는 코스닥이 +3% 이상인 급등장에 신규 매수를 막거나 횟수를 제한합니다. 급등 다음 날 낙폭이 큰 경우가 많아 당일 추격을 줄입니다. 보유 청산·추가매수는 그대로입니다.' },
      { t: '당일 수급 흐름', tags: '외국인 기관 investor', d: '자동매매 상단의 코스피·코스닥 외인/기관 누적 순매수 차트. 화면에서 실시간으로 긁지 않고, 서버가 장중 5분마다 네이버 잠정치를 스냅샷으로 저장한 값을 보여 줍니다.' },
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
      { t: '손절 모니터 (SELL)', tags: 'STOP_LOSS NXT', d: '보유 포지션 청산 조건을 주기적으로 확인하고 매도. 거래일 08:00~19:30(NXT 포함) — 매수·스캔 창과 무관. 정규장 외에는 SOR 지정가. 자동매매 OFF·일일 한도 중단 시에도 동작.' },
      { t: 'reconcile / 동기화', tags: 'SYNC', d: '키움 계좌 잔고와 DB 포지션·매도 체결 상태를 맞추는 처리.' },
      { t: 'live=true', tags: '실시간 조회', d: '포지션 API에서 현재가·ATR을 키움에 다시 물어보는 모드. 자동매매 탭은 주기적으로 live로 갱신합니다.' },
      { t: '스캔 live 지연', tags: 'api traffic scan_load', d: '스캐너가 키움 API를 쓰는 동안(및 종료 후 10초) 대시보드 live 조회를 미룹니다. 종목 N개 임계값이 아니라 스캔 시작 즉시 발생. 활동 배너·연결 배지에 진행 n/m·키움 분당 호출·남은 ETA가 표시됩니다. 종목 간 추가는기는 API 잔여 호출에 맞춰 짧아집니다(여유≥5면 ~1초대).' },
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
