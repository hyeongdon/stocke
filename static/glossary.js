'use strict';

/** 사이트 UI·설정·로그에 등장하는 용어 */
const GLOSSARY = [
  {
    cat: '청산·손익',
    items: [
      { t: 'ATR', tags: '변동성 손절 트레일', d: 'Average True Range. 최근 일봉 기준 하루 평균 가격 변동폭(원). 변동이 큰 종목은 손절·트레일 간격을 넓게 잡습니다.', f: '손절선 ≈ 매수가 − ATR×손절배수 · 트레일선 ≈ 고점 − ATR×트레일배수' },
      { t: 'PCT', tags: '퍼센트 % 손절', d: 'Percentage. 매수가 대비 고정 %로 잡는 손절·트레일 방식. 포지션 카드·칩에 (PCT)로 표시됩니다.', f: '손절가 ≈ 매수가 × (1 − 손절%)' },
      { t: '유효 손절선', tags: 'effective stop', d: '손절 후보(%, ATR, 트레일 등) 중 실제 매도 판단에 쓰이는 가장 높은 가격선. 현재가가 이 선 이하로 내려가면 매도합니다.' },
      { t: '손절 (STOP_LOSS)', tags: '손실 한도', d: '설정한 손실 % 또는 ATR 손절선 이하로 떨어지면 전량 매도하는 규칙.', f: '사유 코드: STOP_LOSS' },
      { t: '트레일링 (TRAILING)', tags: '트레일 스탑', d: '고점이 일정 수익률 이상 오른 뒤, 고점 대비 일정 %·ATR만큼 아래에 따라오는 매도선. 수익을 지키며 상승을 탑니다.' },
      { t: '트레일링 시작 / armed', tags: '익절 시작%', d: '「트레일링 시작 %」(설정의 take_profit_rate)에 고점이 도달하면 트레일링이 켜집니다. 즉시 익절 매도가 아닙니다.' },
      { t: '익절 바닥', tags: '트레일 바닥 floor', d: '트레일링 armed 후 트레일링선이 내려갈 수 있는 최저 한도. 바닥 이하로는 매도선이 내려가지 않습니다.' },
      { t: '익절 (TAKE_PROFIT)', tags: '이익 실현', d: '목표 수익률 도달 시 매도. 본 사이트에서는 주로 「트레일링 시작 %」 설정과 연동됩니다.' },
      { t: '수익 잠금 (PROFIT_LOCK)', tags: 'PROFIT_LOCK', d: '일정 수익% 도달 후 최소 수익 바닥을 확보하는 청산 규칙(고급 설정).' },
      { t: '장마감 청산 (MARKET_CLOSE)', tags: '오버나잇 방지', d: '설정 시각(liquidate_time, 기본 15:10) 이후 보유 종목을 전량 시장가 매도합니다. 같은 시각부터 신규 매수·스캔도 중단됩니다.' },
      { t: '수동 청산 (MANUAL)', tags: 'MANUAL', d: '대시보드 보유 포지션에서 사용자가 직접 시장가 전량 매도를 요청한 경우.' },
      { t: '청산 / 매도', tags: 'exit sell', d: '보유 주식을 팔아 포지션을 닫는 것. 활동 로그·검증 페이지의 「청산」은 포지션 1건 완전 매도를 뜻합니다.' },
      { t: '실현 손익', tags: 'realized PnL', d: '매도 체결로 확정된 손익. 성과 통계·검증의 합계는 보통 실현 손익 기준입니다.' },
      { t: '평가 손익', tags: '미실현', d: '아직 팔지 않은 보유 종목의 현재가 기준 손익. 키움 API·포지션 카드에 표시됩니다.' },
      { t: '매수 시점 ATR', tags: 'buy_atr 스냅샷', d: '포지션 생성 시 저장한 ATR 값. 검증 화면 청산식·당시 기준 설명에 사용하며 API 재조회 없이 표시합니다.' },
    ],
  },
  {
    cat: '진입·매수',
    items: [
      { t: '진입 타이밍 게이트', tags: 'entry gate', d: '스크리너 통과 후에도 당일 시세 조건을 만족할 때만 매수하는 필터.' },
      { t: 'VWAP', tags: '거래량 가중 평균', d: 'Volume Weighted Average Price. 장중 분봉 기준 거래량 가중 평균가. 「현재가 ≥ VWAP」은 당일 강세 확인용입니다.' },
      { t: '당일 위치', tags: 'day position', d: '0~1. (현재가−당일저가)÷(당일고가−당일저가). 0.5면 당일 범위 중간, 1에 가까우면 고가 근처.' },
      { t: '고가 근접', tags: 'high proximity', d: '현재가가 당일 고가 대비 설정 % 이내에 있을 때만 매수하는 조건.' },
      { t: '거래량 비율', tags: 'volume ratio', d: '전일 대비 당일 거래량 %. 하한을 두면 거래가 살아 있는 종목만 매수합니다.' },
      { t: '역피라미딩 (PYRAMIDING)', tags: '분할매수', d: '등락률이 높을수록 초기 매수 금액을 줄이는 방식. 급등·고변동 구간의 손절 리스크를 낮춥니다.' },
      { t: '고정 금액 (FIXED)', tags: 'FIXED', d: '종목당 매수 금액을 고정하는 사이징 방식.' },
      { t: '약한 신호 / 강한 신호', tags: '등락% 임계', d: '역피라미딩에서 당일 등락률 구간별 매수 금액 기준. 약한 신호는 큰 금액, 강한 신호는 작은 금액.' },
      { t: '추가 매수', tags: 'add buy 피라미딩', d: '이미 보유 중인 종목에 수익률·트리거 조건으로 더 사는 매수. 동시 보유 슬롯에는 잡히지 않습니다.' },
      { t: '매수 신호', tags: 'PendingBuySignal', d: '스캐너가 생성한 매수 대기 레코드. PENDING→PROCESSING→ORDERED→FILLED/FAILED 순으로 진행됩니다.' },
      { t: '재주문 쿨다운', tags: 'cooldown', d: '같은 종목을 매도한 뒤 N초 동안 재매수 신호를 막는 시간.' },
    ],
  },
  {
    cat: '포지션·계좌',
    items: [
      { t: '포지션', tags: 'Position HOLDING', d: '매수 체결 후 추적하는 1건의 보유 기록. 종목·수량·매입가·손절 설정·고점 등을 담습니다.' },
      { t: '비중', tags: 'weight', d: '해당 종목 평가금액 ÷ 키움 주식 총평가(%). 포지션 카드에 표시됩니다.' },
      { t: '고점 (peak)', tags: 'peak price', d: '진입 후 기록된 최고가. 트레일링·익절 바닥 계산에 사용됩니다.' },
      { t: '동시 보유 슬롯', tags: 'max concurrent', d: '한 번에 가질 수 있는 신규 매수 종목 수(보유+매수대기 포함). 설정의 「최대 동시 보유 종목」.' },
      { t: '현금 보유율', tags: 'cash reserve', d: '예수금 중 매수에 쓰지 않고 남겨 둘 비율(%). 「매수 가능」금액 계산에 반영됩니다.' },
      { t: '매수 가능', tags: 'investable', d: '예수금(D+0)에서 현금 보유율만큼 뺀 금액. D+2 추정예수금보다 크면 D+2로 상한. 보유주식 평가액은 포함되지 않습니다.' },
      { t: 'D+2 추정예수금', tags: '결제일', d: '키움 kt00004의 d2_entra. 결제 반영 후 추정 현금. 총 평가자산과 같게 나오는 경우도 있어 대시보드에 별도 표기합니다.' },
      { t: '총 평가자산', tags: '추정예탁자산', d: '키움 prsm_dpst_aset_amt. 예수금 + 주식 평가 등을 합산한 계좌 추정 자산입니다.' },
      { t: '부분 체결', tags: 'partial fill', d: '주문 수량보다 적게 체결된 경우. 검증·포지션에서 주문/체결 수량 차이로 표시됩니다.' },
    ],
  },
  {
    cat: '스캐너·종목',
    items: [
      { t: '스크리너', tags: 'screener', d: '거래대금 상위 등 조건으로 자동매매 후보 종목을 고르는 기능. 2분 주기로 스캔합니다.' },
      { t: '거래대금순', tags: 'volume rank', d: '당일 거래대금이 큰 순서로 종목을 정렬해 후보를 뽑는 방식.' },
      { t: '관심종목', tags: 'watchlist', d: '설정에 등록한 종목 코드. 스크리너와 함께 매수 후보에 포함됩니다.' },
      { t: 'KRX', tags: '코스피 코스닥', d: '한국거래소 상장 주식. 스크리너는 KRX 개별주만 대상으로 합니다.' },
      { t: '개별주', tags: 'STOCK', d: '일반 상장 주식. ETF·ETN·레버리지·인버스·SPAC 등은 스크리너에서 제외됩니다.' },
    ],
  },
  {
    cat: '시스템·로그',
    items: [
      { t: '자동매매 엔진', tags: 'is_enabled', d: '설정 ON 시 스캐너·매수 실행기·손절 모니터가 장중 동작합니다. OFF면 신규 매수·손절 판단은 멈춥니다(장마감 청산 설정은 별도).' },
      { t: '스캐너 (AUTO_SCANNER)', tags: 'SCANNER', d: '후보 종목을 돌며 매수 신호를 만드는 모듈. 활동 로그에 SCANNER로 표시됩니다.' },
      { t: '매수 실행기 (BUY)', tags: 'BUY_EXECUTOR', d: '대기 중인 매수 신호를 검증하고 키움 API로 주문합니다.' },
      { t: '손절 모니터 (SELL)', tags: 'STOP_LOSS', d: '보유 포지션의 청산 조건을 주기적으로 확인하고 매도 주문합니다. 약 2분 주기.' },
      { t: 'reconcile / 동기화', tags: 'SYNC', d: '키움 계좌 잔고와 DB 포지션·매도 체결 상태를 맞추는 처리.' },
      { t: 'live=true', tags: '실시간 조회', d: '포지션 API에서 현재가·ATR을 키움에 다시 물어보는 모드. 자동매매 탭은 10초마다 live로 갱신합니다.' },
      { t: '슬롯 정리', tags: 'FAILED 신호', d: '만료된 ORDERED 매수 신호를 FAILED 처리해 동시 보유 한도가 풀리게 하는 내부 정리.' },
    ],
  },
  {
    cat: '검증·성과',
    items: [
      { t: '검증 (verify)', tags: '라운드트립', d: '매수·매도 시각, 진입/청산 조건 체크리스트, 계산식을 한 건씩 보여 주는 화면.' },
      { t: '청산 1건', tags: 'closed trade', d: '성과 통계에서 포지션 1개를 완전히 매도한 것 = 거래 1건으로 집계합니다.' },
      { t: '진입 판정', tags: 'entry verdict', d: '검증에서 매수 조건·체결 상태를 OK / CHECK / FAIL로 요약한 결과.' },
      { t: '15분봉 차트', tags: 'verification chart', d: '검증 카드를 펼치면 매수·매도 시각이 표시된 당일(또는 구간) 15분봉. API는 카드 열 때만 호출합니다.' },
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
