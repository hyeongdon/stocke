'use strict';

/* ===== Helpers ===== */
const $ = (id) => document.getElementById(id);

async function fetchJSON(url, opts) {
  const timeoutMs = (opts && opts.timeoutMs) || 15000;
  const { timeoutMs: _t, ...rest } = opts || {};
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, Object.assign({ headers: { 'Accept': 'application/json' }, signal: ctrl.signal }, rest || {}));
    if (!res.ok) throw new Error(`${res.status} ${url}`);
    return res.json();
  } finally {
    clearTimeout(timer);
  }
}
async function postJSON(url, body) {
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  if (!res.ok) throw new Error(`${res.status} ${url}`);
  return res.json();
}

function parseNum(v) {
  if (v === null || v === undefined) return 0;
  if (typeof v === 'number') return v;
  const s = String(v).trim(); if (!s) return 0;
  const sign = s[0] === '-' ? -1 : 1;
  if (/^-?\d+\.\d+$/.test(s.replace(/,/g, ''))) {
    return sign * parseFloat(s.replace(/[^0-9.]/g, ''));
  }
  const cleaned = s.replace(/[^0-9]/g, '').replace(/^0+(?=\d)/, '') || '0';
  return sign * parseFloat(cleaned);
}
function won(v) { return Math.round(parseNum(v)).toLocaleString('ko-KR') + '원'; }
function num(v) { return Math.round(parseNum(v)).toLocaleString('ko-KR'); }
function numFixed(v, digits) {
  const n = parseNum(v);
  return n.toLocaleString('ko-KR', {
    minimumFractionDigits: digits ?? 0,
    maximumFractionDigits: digits ?? 0,
  });
}
function signClass(n) { n = parseNum(n); return n > 0 ? 'up' : (n < 0 ? 'down' : 'flat'); }

function fmtIndexValue(v) {
  if (v == null || Number.isNaN(Number(v))) return '-';
  return Number(v).toLocaleString('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtIndexDelta(v) {
  if (v == null || Number.isNaN(Number(v))) return '-';
  const n = Number(v);
  const sign = n > 0 ? '+' : '';
  return sign + n.toLocaleString('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtIndexPct(v) {
  if (v == null || Number.isNaN(Number(v))) return '';
  const n = Number(v);
  const sign = n > 0 ? '+' : '';
  return `${sign}${n.toFixed(2)}%`;
}
function renderMarketIndexItem(it) {
  const dir = it.direction || signClass(it.change_pct ?? it.change);
  const deltaParts = [];
  if (it.change != null) deltaParts.push(fmtIndexDelta(it.change));
  if (it.change_pct != null) deltaParts.push(`(${fmtIndexPct(it.change_pct)})`);
  return `<span class="market-index-item ${dir}"><span class="mi-label">${esc(it.label)}</span><span class="mi-value">${fmtIndexValue(it.value)}</span><span class="mi-delta">${deltaParts.join(' ') || '-'}</span></span>`;
}
async function loadMarketIndices(opts) {
  const el = $('marketIndices');
  if (!el) return;
  try {
    const d = await fetchJSON('/market/indices', { timeoutMs: 20000 });
    const items = d.indices || [];
    el.innerHTML = items.length
      ? items.map(renderMarketIndexItem).join('')
      : '<span class="market-index-skeleton">지수 데이터 없음</span>';
  } catch (e) {
    el.innerHTML = '<span class="market-index-skeleton">지수 로드 실패 (서버 재시작 후 새로고침)</span>';
  }
}
function rateStr(n) { n = parseNum(n); const s = n > 0 ? '+' : ''; return `${s}${n.toFixed(2)}%`; }
function pnlStr(n) { n = parseNum(n); const s = n > 0 ? '+' : ''; return `${s}${num(n)}원`; }
function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
function emptyRow(msg, ico) { return `<div class="empty"><span class="ico">${ico || '📭'}</span>${esc(msg)}</div>`; }

const TZ_SEOUL = 'Asia/Seoul';

/** API/DB 시각 — UTC(Z) 또는 naive UTC 문자열을 KST로 표시 */
function dtDb(s, withDate) {
  return dt(s, withDate, true);
}

function parseDbUtc(s) {
  if (!s) return null;
  const raw = String(s).trim();
  if (!raw) return null;
  if (/[zZ]|[+-]\d{2}:?\d{2}$/.test(raw)) {
    const d = new Date(raw);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  const iso = (raw.includes('T') ? raw : raw.replace(' ', 'T')).slice(0, 19);
  const d = new Date(`${iso}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

function kstYmd(d) {
  return d.toLocaleDateString('en-CA', { timeZone: TZ_SEOUL });
}

function tradeWhen(s, assumeUtc) {
  if (!s) return { day: '-', time: '-', full: '-' };
  const d = assumeUtc ? parseDbUtc(s) : new Date(s);
  if (!d || Number.isNaN(d.getTime())) return { day: '-', time: '-', full: '-' };
  const fmt = assumeUtc ? { timeZone: TZ_SEOUL } : {};
  const today = kstYmd(new Date());
  const that = kstYmd(d);
  const diff = Math.round((Date.parse(today) - Date.parse(that)) / 86400000);
  let day;
  if (diff === 0) day = '오늘';
  else if (diff === 1) day = '어제';
  else day = d.toLocaleDateString('ko-KR', Object.assign({ year: 'numeric', month: '2-digit', day: '2-digit' }, fmt));
  const time = d.toLocaleTimeString('ko-KR', Object.assign({ hour: '2-digit', minute: '2-digit', hour12: false }, fmt));
  return { day, time, full: `${day} ${time}` };
}
function dt(s, withDate, assumeUtc) {
  if (!s) return '-';
  const d = assumeUtc ? parseDbUtc(s) : new Date(s);
  if (!d || Number.isNaN(d.getTime())) return '-';
  const fmt = assumeUtc ? { timeZone: TZ_SEOUL } : {};
  if (!withDate) {
    return d.toLocaleTimeString('ko-KR', Object.assign({ hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }, fmt));
  }
  return tradeWhen(s, assumeUtc).full;
}
function sellTradeTs(o) {
  return o.completed_at || o.ordered_at || o.created_at;
}

const REASON_LABEL = {
  STOP_LOSS: '손절',
  TAKE_PROFIT: '익절',
  TRAILING: '트레일링 스탑',
  PROFIT_LOCK: '수익 잠금',
  MARKET_CLOSE: '장마감 청산',
  MANUAL: '수동 매도',
  MANUAL_SELL: '수동 매도',
  INDICATOR: '지표 매도',
  DUPLICATE_HOLDING: '중복 보유 정리',
  '체결': '체결',
};
function reasonLabel(r) { return REASON_LABEL[r] || r || '기타'; }

let toastTimer;
function toast(msg, isErr) {
  const t = $('toast'); t.textContent = msg;
  t.className = 'toast show' + (isErr ? ' err' : '');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.className = 'toast'; }, 3000);
}
function setConn(state, text) { $('connBadge').className = 'conn-badge ' + state; $('connText').textContent = text; }

function updateApiLimitBadge(info, traffic) {
  if (!info) return;
  const wait = Number(info.seconds_until_available || 0);
  const usage = Number(info.usage_percent || 0);
  if (traffic && traffic.defer_dashboard_live) {
    setConn('warn', '스캔 중 — live 조회 지연');
    return;
  }
  if (info.status === 'limited' || wait > 1.5) {
    setConn('warn', `키움 API 대기 (${Math.ceil(wait)}초)`);
    return;
  }
  if (usage > 85) {
    setConn('warn', `키움 API ${Math.round(usage)}%`);
    return;
  }
}

async function pingServer() {
  try {
    await fetchJSON('/health', { timeoutMs: 4000 });
    return true;
  } catch {
    return false;
  }
}

function setAccountBadge(d) {
  const at = $('accountType');
  if (!at) return;
  at.style.display = '';
  const acct = d._account_type ? esc(d._account_type) + ' · ' : '';
  const kiwoom = d._api_connected ? '키움 연결' : '키움 미연결';
  const cache = d._cached ? ' (캐시)' : '';
  at.className = 'conn-badge ' + (d._api_connected ? (d._account_type === '실계좌' ? 'off' : 'warn') : 'warn');
  at.innerHTML = `<span class="dot"></span>${acct}${kiwoom}${cache}`;
}

function accountHoldingsFromBalance(d) {
  const raw = d.stk_acnt_evlt_prst || [];
  return raw.map((h) => ({
    code: String(h.stk_cd || '').replace(/^A/i, ''),
    name: h.stk_nm || h.stock_name || '',
    qty: parseNum(h.qty || h.rmnd_qty),
    pl: parseNum(h.lspft_amt || h.pl_amt),
    rate: parseNum(h.lspft_rt || h.pl_rt),
    buyAmt: parseNum(h.pur_amt || h.buy_amt),
    evalAmt: parseNum(h.evlt_amt),
    cur: parseNum(h.cur_pr || h.cur_prc),
    avg: parseNum(h.avg_pr || h.avg_prc),
  })).filter((h) => h.qty > 0);
}


function resolveStockEvalFromHoldings(d, holdings, b) {
  const stockApi = b.stock_eval_api != null ? b.stock_eval_api : parseNum(d.tot_est_amt);
  const stockSum = b.stock_eval_holdings_sum != null
    ? b.stock_eval_holdings_sum
    : holdings.reduce((s, h) => s + holdingEvalAmount(h), 0);
  const count = b.holding_count != null ? b.holding_count : holdings.length;
  if (b.stock_eval != null) {
    return {
      stockEval: b.stock_eval,
      stockApi,
      stockSum,
      count,
      staleApi: b.stale_api_stock_eval || 0,
    };
  }
  if (count === 0) {
    return { stockEval: 0, stockApi, stockSum: 0, count: 0, staleApi: stockApi > 0 ? stockApi : 0 };
  }
  if (stockSum > 0) return { stockEval: stockSum, stockApi, stockSum, count, staleApi: 0 };
  return { stockEval: stockApi > 0 ? stockApi : 0, stockApi, stockSum, count, staleApi: 0 };
}

function renderCashBreakdown(d, holdings) {
  const b = d.balance_breakdown || {};
  const entr = b.deposit != null ? b.deposit : parseNum(d.entr);
  const { stockEval, stockApi, stockSum, count, staleApi } = resolveStockEvalFromHoldings(d, holdings, b);
  const totalAsset = b.total_asset != null
    ? b.total_asset
    : (parseNum(d.prsm_dpst_aset_amt) || parseNum(d.aset_evlt_amt) || 0);
  const effectiveTotal = b.effective_total_asset != null
    ? b.effective_total_asset
    : (count === 0 ? entr : (totalAsset || entr + stockEval));
  const totalStale = Boolean(b.total_asset_stale) || (count === 0 && totalAsset > entr + 1);
  const d2 = b.d2_deposit != null ? b.d2_deposit : parseNum(d.d2_entra);
  const computed = b.computed_deposit_plus_stock != null ? b.computed_deposit_plus_stock : (entr + stockEval);
  const gap = b.total_asset_gap != null ? b.total_asset_gap : (totalAsset > 0 ? totalAsset - computed : 0);
  const pct = b.cash_reserve_pct != null ? b.cash_reserve_pct : parseNum(d.cash_reserve_pct);
  const reserve = b.cash_reserve != null ? b.cash_reserve : parseNum(d.cash_reserve);
  const investable = b.investable_cash != null ? b.investable_cash : parseNum(d.investable_cash);
  const investableRaw = b.investable_before_d2_cap != null ? b.investable_before_d2_cap : Math.max(0, entr - reserve);
  const d2Cap = Boolean(b.d2_cap_applied) || (d2 > 0 && investable > 0 && investable < investableRaw);

  $('stDeposit').textContent = won(entr);
  window._lastDeposit = entr;
  if (typeof refreshDepositPctPreviews === 'function') {
    try { refreshDepositPctPreviews(); } catch (_) { /* settings form 미생성 */ }
  }

  const elStock = $('stStockEval');
  if (elStock) {
    if (count === 0) {
      let stockLine = '주식 평가합: 0원 (보유 없음)';
      if (staleApi > 0) {
        stockLine += ` · 키움 API 잔여 ${won(staleApi)}`;
      }
      elStock.textContent = stockLine;
      elStock.className = 'delta flat';
    } else {
      let stockLine = `주식 평가합: ${won(stockEval)} (${count}종목)`;
      if (stockApi > 0 && stockSum > 0 && Math.abs(stockApi - stockSum) > 1) {
        stockLine += ` · API ${won(stockApi)}`;
      }
      elStock.textContent = stockLine;
      elStock.className = 'delta flat';
    }
  }

  const elSum = $('stAssetSum');
  if (elSum) {
    let sumLine = `예수금 + 주식 = ${won(computed)}`;
    if (totalAsset > 0 || effectiveTotal > 0) {
      const showTotal = totalStale ? effectiveTotal : (totalAsset || effectiveTotal);
      sumLine += ` · 총 평가자산 ${won(showTotal)}`;
      if (totalStale && totalAsset > effectiveTotal + 1) {
        elSum.className = 'delta flat match-warn';
        sumLine += ` (키움 ${won(totalAsset)} 갱신 지연)`;
      } else if (Math.abs(gap) <= 1) {
        elSum.className = 'delta flat match-ok';
        sumLine += ' ✓';
      } else {
        elSum.className = 'delta flat match-warn';
        sumLine += ` (차이 ${gap > 0 ? '+' : ''}${won(gap)})`;
      }
    } else {
      elSum.className = 'delta flat';
    }
    elSum.textContent = sumLine;
  }

  const elD2 = $('stD2');
  if (elD2) {
    let d2Line = `D+2 추정예수금: ${won(d2)}`;
    if (d2 > 0 && totalAsset > 0 && Math.abs(d2 - totalAsset) <= 1) {
      d2Line += ' (총자산과 동일·키움값)';
    } else if (d2 > entr + 1) {
      d2Line += ` (+${won(d2 - entr)} vs D+0)`;
    }
    elD2.textContent = d2Line;
    elD2.className = 'delta flat';
  }

  const elReserve = $('stCashReserve');
  const elInvestable = $('stInvestable');
  const elFormula = $('stCashFormula');
  if (elReserve && elInvestable) {
    if (d.cash_reserve != null || d.investable_cash != null || b.cash_reserve != null) {
      elReserve.textContent = `현금 보유 (${pct}%): ${won(reserve)} = 예수금 × ${pct}%`;
      let invText = `매수 가능: ${won(investable)}`;
      if (d2 <= 0 && entr > 0) {
        invText += ' (D+2 미수·부족)';
        elInvestable.className = 'delta highlight down';
      } else {
        elInvestable.className = 'delta highlight' + (investable > 0 ? '' : ' down');
      }
      elInvestable.textContent = invText;
      elReserve.className = 'delta flat';

      if (elFormula) {
        let formula = `계산: ${won(entr)} − ${won(reserve)} = ${won(investableRaw)}`;
        if (d2Cap) formula += ` → D+2 상한 ${won(d2)}`;
        formula += ' · 보유주식 평가액은 매수에 사용되지 않음';
        elFormula.textContent = formula;
      }
    } else {
      elReserve.textContent = '현금 보유: -';
      elInvestable.textContent = '매수 가능: -';
      if (elFormula) elFormula.textContent = '';
    }
  }
}

/* ===== 계좌 요약 ===== */
async function loadAccount() {
  try {
    const d = await fetchJSON('/account/balance', { timeoutMs: 25000 });
    setConn('', '서버 연결됨');
    setAccountBadge(d);
    const holdings = accountHoldingsFromBalance(d);
    window._kiwoomHoldings = holdings;
    window._stockEvalTotal = holdings.reduce((s, h) => s + holdingEvalAmount(h), 0);
    const bbd = d.balance_breakdown || {};
    const apiTotal = parseNum(d.prsm_dpst_aset_amt) || parseNum(d.aset_evlt_amt) || 0;
    const effectiveTotal = bbd.effective_total_asset != null
      ? bbd.effective_total_asset
      : (holdings.length === 0 ? parseNum(d.entr) : apiTotal);
    window._totalAsset = effectiveTotal;

    $('stTotalAsset').textContent = won(effectiveTotal);
    const acctSub = d.acnt_no ? `계좌 ${esc(d.acnt_no)}` : '-';
    const staleTotal = Boolean(bbd.total_asset_stale) || (holdings.length === 0 && apiTotal > parseNum(d.entr) + 1);
    $('stAccountNo').textContent = staleTotal && apiTotal > effectiveTotal
      ? `${acctSub} · 키움 추정자산 ${won(apiTotal)} (갱신 지연)`
      : acctSub;

    const elDetail = $('stUnrealizedDetail');
    if (holdings.length === 0) {
      $('stPnl').textContent = pnlStr(0);
      $('stPnl').className = 'value flat';
      $('stPnlRate').textContent = '-';
      $('stPnlRate').className = 'delta flat';
      if (elDetail) elDetail.textContent = '보유 종목 없음 (키움 잔고 기준)';
      $('stHoldingCnt').textContent = '키움 보유 0종목';
      $('stBuyAmt').textContent = won(0);
    } else {
      const evalPl = holdings.reduce((s, h) => s + h.pl, 0);
      const buyTotal = holdings.reduce((s, h) => s + h.buyAmt, 0);
      const evalRate = buyTotal > 0 ? (evalPl / buyTotal) * 100 : (parseNum(d.lspft_rt) || 0);
      $('stPnl').textContent = pnlStr(evalPl);
      $('stPnl').className = 'value ' + signClass(evalPl);
      $('stPnlRate').textContent = rateStr(evalRate);
      $('stPnlRate').className = 'delta ' + signClass(evalRate);
      if (elDetail) {
        elDetail.textContent = holdings.map((h) => `${h.name || h.code} ${num(h.qty)}주 ${pnlStr(h.pl)}`).join(' · ');
      }
      $('stHoldingCnt').textContent = `키움 보유 ${holdings.length}종목`;
      $('stBuyAmt').textContent = won(buyTotal || d.tot_pur_amt);
    }
    renderCashBreakdown(d, holdings);
    if (window._perfStats) applyRealizedSummary(window._perfStats);
    syncKiwoomHoldingsView();
  } catch (e) {
    const serverOk = await pingServer();
    if (!serverOk) {
      setConn('off', '서버 응답 없음');
    } else {
      setConn('', '서버 연결됨');
    }
    const at = $('accountType');
    if (at) {
      at.style.display = '';
      at.className = 'conn-badge warn';
      at.innerHTML = '<span class="dot"></span>키움 잔고 조회 실패 (서버는 연결됨)';
    }
  }
}

/* ===== 성과 통계 (대시보드 탭) ===== */
let perfChart = null;
window._perfStats = null;

function statCard(label, value, sub, cls) {
  return `<div class="card stat compact-stat"><div class="label">${esc(label)}</div><div class="value ${cls || ''}">${value}</div><div class="delta flat">${sub || ''}</div></div>`;
}

function applyRealizedSummary(d) {
  const el = $('stRealizedPnl');
  if (!el || !d) return;
  const net = d.net_pnl ?? 0;
  const cnt = d.trade_count || 0;
  const wr = cnt ? `${d.wins}승 ${d.losses}패 (${d.win_rate}%)` : '';
  el.textContent = `실현손익: ${pnlStr(net)}${wr ? ' · ' + wr : ''}`;
  el.className = 'delta ' + signClass(net);
}

async function fetchPerformanceStats(force = false) {
  const seed = parseNum($('pfSeed')?.value) || 10000000;
  if (!force && window._perfStats && window._perfStats._seed === seed) {
    return window._perfStats;
  }
  const d = await fetchJSON(`/performance/stats?source=db&seed=${seed}`, { timeoutMs: 60000 });
  d._seed = seed;
  window._perfStats = d;
  return d;
}

function isBoardTabActive() {
  const pane = $('pane-board');
  return pane && pane.classList.contains('active');
}

async function loadPerformance(force = false) {
  const hintEl = $('perfSourceHint');
  if (!hintEl) return;
  hintEl.textContent = '실현손익 조회 중...';
  try {
    const d = await fetchPerformanceStats(force);
    applyRealizedSummary(d);

    const pipeLabel = {
      api_stock: '키움 종목별(ka10073)',
      api_daily: '키움 일별(ka10074)',
      db: '자동매매 청산(포지션)',
      empty: '데이터 없음',
    }[d.pipeline] || d.pipeline;
    let hint = `${pipeLabel} · 청산 ${d.trade_count}건 (1포지션 완전 청산 = 1건)`;
    if (d.period?.start) hint += ` · ${d.period.start}~${d.period.end}`;
    if (d.note) hint += ` · ${d.note}`;
    if (d.account_realized_net != null && d.account_realized_net !== d.net_pnl) {
      hint += ` · 계좌합계 ${pnlStr(d.account_realized_net)}`;
    }
    $('perfSourceHint').textContent = hint;

    let cards = '';
    cards += statCard('순손익 (실현)', pnlStr(d.net_pnl), `수익률 ${rateStr(d.return_rate)}`, signClass(d.net_pnl));
    cards += statCard('승률', `${d.win_rate}%`, `${d.wins}승 ${d.losses}패${d.breakeven ? ' · 무승부 ' + d.breakeven : ''}`, 'flat');
    cards += statCard('손익비 (Payoff)', `${d.payoff}`, `평균익 ${pnlStr(d.avg_win)} / 평균손 ${pnlStr(d.avg_loss)}`, 'flat');
    cards += statCard('Profit Factor', `${d.profit_factor}`, '총이익 / 총손실', 'flat');
    cards += statCard('1회 기대손익', pnlStr(d.expected), `총 ${d.trade_count}회 청산`, signClass(d.expected));
    cards += statCard('최대 낙폭 (MDD)', pnlStr(d.mdd), '', 'down');
    cards += statCard('일평균 손익', pnlStr(d.daily_avg), `${num(d.trading_days)}일 · ${d.day_wins}승 ${d.day_losses}패`, signClass(d.daily_avg));
    cards += statCard('최고/최악 거래', pnlStr(d.best), `최악 ${pnlStr(d.worst)}`, signClass(d.best));
    $('perfCards').innerHTML = cards;

    $('perfCurveTotal').textContent = pnlStr(d.net_pnl);
    if (isBoardTabActive()) {
      drawPerfChart(d.curve.map((_, i) => i + 1), d.curve.map((c) => c.cum));
    }

    if (!d.by_reason.length) $('byReasonBody').innerHTML = emptyRow('청산 내역이 없습니다.', '📊');
    else $('byReasonBody').innerHTML = `<table class="tbl"><thead><tr><th>사유</th><th class="num">횟수</th><th class="num">실현손익</th></tr></thead><tbody>${
      d.by_reason.map((r) => `<tr><td>${esc(reasonLabel(r.reason))}</td><td class="num">${num(r.count)}</td><td class="num ${signClass(r.realized)}">${pnlStr(r.realized)}</td></tr>`).join('')}</tbody></table>`;

    if (!d.daily.length) $('dailyBody').innerHTML = emptyRow('일별 데이터가 없습니다.', '📅');
    else $('dailyBody').innerHTML = `<table class="tbl"><thead><tr><th>날짜</th><th class="num">청산</th><th class="num">승</th><th class="num">손익</th></tr></thead><tbody>${
      d.daily.map((r) => `<tr><td>${esc(r.date)}</td><td class="num">${num(r.count)}</td><td class="num">${num(r.wins)}</td><td class="num ${signClass(r.pnl)}">${pnlStr(r.pnl)}</td></tr>`).join('')}</tbody></table>`;
  } catch (e) {
    $('perfSourceHint').textContent = '조회 실패';
    $('perfCards').innerHTML = emptyRow('성과 통계를 불러오지 못했습니다.', '⚠️');
  }
}
function statusRow(name, label, cls, hours) {
  const hoursHtml = hours
    ? `<span class="status-hours">${esc(hours)}</span>`
    : '';
  return `<div class="status-row"><span class="name">${esc(name)}</span>${hoursHtml}<span class="pill ${cls}">${esc(label)}</span></div>`;
}
function _hmToMin(hm, fallback) {
  const raw = String(hm || fallback || '00:00');
  const m = raw.match(/^(\d{1,2}):(\d{2})/);
  if (!m) {
    const f = String(fallback || '00:00').match(/^(\d{1,2}):(\d{2})/);
    return f ? (+f[1]) * 60 + (+f[2]) : 0;
  }
  return (+m[1]) * 60 + (+m[2]);
}
function _minToHm(mins) {
  const h = Math.floor(Math.max(0, mins) / 60);
  const m = Math.max(0, mins) % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}
/** 전략별 매매시간 합집합 — 가동 구간 (표시 전용) */
function linkedSessionWindow(settings) {
  const detail = linkedSessionWindowDetail(settings);
  return `${detail.start}~${detail.end}`;
}
function linkedSessionWindowDetail(settings) {
  const s = settings || {};
  const parts = [
    { key: 'legacy', label: '레거시', start: _v(s, 'trade_start_time') || '10:00', end: _v(s, 'trade_end_time') || '15:20' },
    { key: 'sangtta', label: '상따', start: _v(s, 'sangtta_trade_start_time') || '09:05', end: _v(s, 'sangtta_trade_end_time') || '11:00' },
  ];
  const useBreakout = !!(s.use_breakout || String(s.breakout_condition_names || '').trim());
  if (useBreakout) {
    parts.push({
      key: 'breakout',
      label: '돌파',
      start: _v(s, 'breakout_trade_start_time') || '11:00',
      end: _v(s, 'breakout_trade_end_time') || '14:30',
    });
  }
  const useYmgp = !!(s.use_ymgp || String(s.ymgp_condition_names || '').trim());
  if (useYmgp) {
    parts.push({
      key: 'ymgp',
      label: '역매공파',
      start: _v(s, 'ymgp_trade_start_time') || '09:30',
      end: _v(s, 'ymgp_trade_end_time') || '14:30',
    });
  }
  if (s.use_jongga) {
    const pigOn = s.jongga_pig_split !== false;
    parts.push({
      key: 'jongga',
      label: '종가배팅',
      start: _v(s, 'jongga_trade_start_time') || '14:30',
      end: pigOn
        ? (_v(s, 'jongga_leg3_end_time') || '15:28')
        : (_v(s, 'jongga_pick_end_time') || _v(s, 'jongga_trade_end_time') || '14:40'),
    });
  }
  let startMin = Math.min(...parts.map((p) => _hmToMin(p.start, p.start)));
  let endMin = Math.max(...parts.map((p) => _hmToMin(p.end, p.end)));
  if (s.liquidate_before_close) {
    const liq = _hmToMin(s.liquidate_time, '15:10');
    if (liq > endMin) endMin = liq;
  }
  return {
    start: _minToHm(startMin),
    end: _minToHm(endMin),
    parts,
  };
}
function readSessionOptsFromForm() {
  return {
    trade_start_time: $('set_trade_start_time')?.value || '10:00',
    trade_end_time: $('set_trade_end_time')?.value || '15:20',
    sangtta_trade_start_time: $('set_sangtta_trade_start_time')?.value || '09:05',
    sangtta_trade_end_time: $('set_sangtta_trade_end_time')?.value || '11:00',
    breakout_trade_start_time: $('set_breakout_trade_start_time')?.value || '11:00',
    breakout_trade_end_time: $('set_breakout_trade_end_time')?.value || '14:30',
    use_breakout: !!$('set_use_breakout')?.checked,
    breakout_condition_names: $('set_breakout_condition_names')?.value || '',
    ymgp_trade_start_time: $('set_ymgp_trade_start_time')?.value || '09:30',
    ymgp_trade_end_time: $('set_ymgp_trade_end_time')?.value || '14:30',
    use_ymgp: !!$('set_use_ymgp')?.checked,
    ymgp_condition_names: $('set_ymgp_condition_names')?.value || '',
    jongga_trade_start_time: $('set_jongga_trade_start_time')?.value || '14:30',
    jongga_pick_end_time: $('set_jongga_pick_end_time')?.value || '14:40',
    jongga_trade_end_time: $('set_jongga_trade_end_time')?.value || $('set_jongga_pick_end_time')?.value || '14:40',
    jongga_leg2_start_time: $('set_jongga_leg2_start_time')?.value || '14:50',
    jongga_leg3_start_time: $('set_jongga_leg3_start_time')?.value || '15:20',
    jongga_leg3_end_time: $('set_jongga_leg3_end_time')?.value || '15:28',
    use_jongga: !!$('set_use_jongga')?.checked,
    jongga_pig_split: !!$('set_jongga_pig_split')?.checked,
    liquidate_before_close: !!$('set_liquidate_before_close')?.checked,
    liquidate_time: $('set_liquidate_time')?.value || '15:10',
  };
}
function refreshEngineSessionDisplay() {
  const detail = linkedSessionWindowDetail(readSessionOptsFromForm());
  const startEl = $('engineSessionStart');
  const endEl = $('engineSessionEnd');
  const breakEl = $('engineSessionBreakdown');
  if (startEl) startEl.textContent = detail.start;
  if (endEl) endEl.textContent = detail.end;
  if (breakEl) {
    breakEl.textContent = detail.parts.map((p) => `${p.label} ${p.start}~${p.end}`).join(' · ');
  }
  updateStatusSessionHours(`${detail.start}~${detail.end}`);
}
function updateStatusSessionHours(window) {
  document.querySelectorAll('#statusBody .status-row .status-hours').forEach((el, i) => {
    if (i >= 1 && i <= 3) el.textContent = window || '설정 없음';
  });
}
function bindTradeTimePreview() {
  const ids = [
    'set_trade_start_time', 'set_trade_end_time',
    'set_sangtta_trade_start_time', 'set_sangtta_trade_end_time',
    'set_breakout_trade_start_time', 'set_breakout_trade_end_time',
    'set_ymgp_trade_start_time', 'set_ymgp_trade_end_time',
    'set_jongga_trade_start_time', 'set_jongga_pick_end_time', 'set_jongga_trade_end_time',
    'set_jongga_leg2_start_time', 'set_jongga_leg3_start_time', 'set_jongga_leg3_end_time',
    'set_liquidate_time',
  ];
  ids.forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener('change', refreshEngineSessionDisplay);
    el.addEventListener('input', refreshEngineSessionDisplay);
  });
  const br = $('set_use_breakout');
  if (br) br.addEventListener('change', refreshEngineSessionDisplay);
  const ym = $('set_use_ymgp');
  if (ym) ym.addEventListener('change', refreshEngineSessionDisplay);
  const liq = $('set_liquidate_before_close');
  if (liq) liq.addEventListener('change', refreshEngineSessionDisplay);
  refreshEngineSessionDisplay();
}
function sessionActive(block, runtimeKey, runtime) {
  if (block && typeof block.is_active === 'boolean') return block.is_active;
  if (runtime && typeof runtime[runtimeKey] === 'boolean') return runtime[runtimeKey];
  return false;
}
async function loadStatus() {
  try {
    const [mon, settings, stopLoss, activity] = await Promise.all([
      fetchJSON('/monitoring/status').catch(() => ({})),
      fetchJSON('/trading/settings').catch(() => ({})),
      fetchJSON('/stop-loss/status').catch(() => ({})),
      fetchJSON('/trading/activity-log?limit=1').catch(() => ({})),
    ]);
    const rt = activity.runtime || {};
    const scanRunning = sessionActive(mon.auto_trade_scanner, 'scanner_running', rt);
    const buyRunning = sessionActive(mon.buy_executor, 'buy_executor_running', rt);
    const slRunning = typeof stopLoss.monitoring_active === 'boolean'
      ? stopLoss.monitoring_active
      : (typeof rt.stop_loss_running === 'boolean' ? rt.stop_loss_running : false);
    const autoOn = !!settings.is_enabled;
    const sessionWindow = mon.linked_session_window
      || stopLoss.linked_session_window
      || mon.auto_trade_scanner?.session_window
      || stopLoss.session_window
      || mon.buy_executor?.session_window
      || rt.linked_session_window
      || linkedSessionWindow(settings);
    const engineHours = sessionWindow || '설정 없음';
    let html = '';
    html += statusRow('자동매매', autoOn ? '활성' : '비활성', autoOn ? 'on' : 'off', '설정 ON/OFF');
    html += statusRow('종목 스캔', scanRunning ? '실행중' : '중지', scanRunning ? 'run' : 'off', engineHours);
    html += statusRow('손절/익절 모니터링', slRunning ? '실행중' : '중지', slRunning ? 'run' : 'off', engineHours);
    html += statusRow('매수 실행기', buyRunning ? '실행중' : '중지', buyRunning ? 'run' : 'off', engineHours);
    $('statusBody').innerHTML = html;
    $('statusTime').textContent = mon.timestamp ? new Date(mon.timestamp).toLocaleTimeString('ko-KR') : '';
    updateApiLimitBadge(activity.api_rate_limit, activity.api_traffic);

    const sig = mon.signals || {};
    const map = [['총 신호', 'total_signals'], ['관측', 'watching_signals'], ['주문', 'ordered_signals'], ['처리중', 'processing_signals'], ['실패', 'failed_signals'], ['취소', 'cancelled_signals']];
    let any = false, sigHtml = '<div class="grid cols-2 board-kv-grid">';
    for (const [label, key] of map) { if (sig[key] == null) continue; any = true; sigHtml += `<div class="kv"><span class="k">${label}</span><span class="v">${num(sig[key])}</span></div>`; }
    sigHtml += '</div>';
    $('signalStatBody').innerHTML = any ? sigHtml : emptyRow('신호 통계 데이터가 없습니다.', '📊');
  } catch (e) { $('statusBody').innerHTML = emptyRow('상태를 불러오지 못했습니다.', '⚠️'); }
}

/* ===== 텔레그램 ===== */
async function loadTelegram() {
  try {
    const d = await fetchJSON('/telegram/status');
    const filter = (d.condition_filter && d.condition_filter.length) ? d.condition_filter.join(', ') : '전체 조건식';
    let html = '';
    html += `<div class="kv"><span class="k">설정 상태</span><span class="v ${d.configured ? 'up' : 'flat'}">${d.configured ? '✅ 설정됨' : '❌ 미설정'}</span></div>`;
    html += `<div class="kv"><span class="k">채팅 ID</span><span class="v">${esc(d.chat_id_masked || '-')}</span></div>`;
    html += `<div class="kv"><span class="k">대상 조건식</span><span class="v">${esc(filter)}</span></div>`;
    html += `<div class="kv"><span class="k">정시 알림 주기</span><span class="v">${num(d.interval)}초</span></div>`;
    html += `<div style="margin-top:12px;"><button class="btn primary" id="tgSendBtn" ${d.configured ? '' : 'disabled'} style="width:100%;">지금 텔레그램으로 전송</button></div>`;
    $('telegramBody').innerHTML = html;
    const btn = $('tgSendBtn'); if (btn) btn.onclick = sendTelegramNow;
  } catch (e) { $('telegramBody').innerHTML = emptyRow('텔레그램 상태를 불러오지 못했습니다.', '⚠️'); }
}

async function loadTodayKeywords() {
  const body = $('todayKeywordBody');
  if (!body) return;
  try {
    const d = await fetchJSON('/keywords/today?limit=12', { timeoutMs: 12000 });
    const items = d.items || [];
    const hint = $('todayKeywordHint');
    if (hint) hint.textContent = `${items.length}개`;
    if (!items.length) {
      body.innerHTML = emptyRow('키워드 데이터가 없습니다. 테마맵에서 스냅샷 갱신을 먼저 실행하세요.', '🧩');
      return;
    }
    body.innerHTML = `<table class="tbl"><thead><tr><th>키워드</th><th class="num">언급</th><th class="num">종목</th><th>변화</th></tr></thead><tbody>${
      items.map((r) => {
        const delta = parseNum(r.delta_vs_prev || 0);
        const deltaTxt = delta > 0 ? `+${delta}` : `${delta}`;
        const trend = String(r.trend_label || 'flat').toLowerCase();
        const cls = trend === 'up' || trend === 'new' ? 'up' : (trend === 'down' ? 'down' : 'flat');
        return `<tr>
          <td>${esc(r.keyword || '-')}</td>
          <td class="num">${num(r.mention_count || 0)}</td>
          <td class="num">${num(r.stock_count || 0)}</td>
          <td class="num ${cls}">${esc(r.trend_label || 'flat')} (${deltaTxt})</td>
        </tr>`;
      }).join('')
    }</tbody></table>`;
  } catch (_) {
    body.innerHTML = `<div class="activity-line error"><span class="msg">오늘의 키워드를 불러오지 못했습니다.</span></div>`;
  }
}
const BATCH_PROGRESS_POLL_MS = 5000;
let batchProgressTimer = null;

function fmtEta(seconds) {
  const s = parseInt(seconds, 10);
  if (!s || s < 0) return null;
  if (s < 60) return `약 ${s}초`;
  if (s < 3600) return `약 ${Math.ceil(s / 60)}분`;
  const h = Math.floor(s / 3600);
  const m = Math.ceil((s % 3600) / 60);
  return m ? `약 ${h}시간 ${m}분` : `약 ${h}시간`;
}

function stockNewsRemark(job, rootProgress, fallbackToday) {
  const p = job.progress || {};
  const merged = {
    universe_total: p.universe_total ?? rootProgress.universe_total,
    done_count: p.done_count ?? rootProgress.done_count ?? fallbackToday ?? 0,
    pending_count: p.remaining_count ?? rootProgress.pending_count,
    percent: p.percent ?? rootProgress.percent,
    run_done: p.run_done ?? rootProgress.run_done,
    run_total: p.run_total ?? rootProgress.run_total,
    running: job.running || !!rootProgress.running,
    status: p.last_run_status ?? rootProgress.status,
    current_stock_name: rootProgress.current_stock_name,
    current_stock_code: rootProgress.current_stock_code,
    eta_seconds: p.eta_seconds ?? rootProgress.eta_seconds,
  };
  const done = parseInt(merged.done_count, 10) || 0;
  const total = parseInt(merged.universe_total, 10) || 0;
  const pending = merged.pending_count != null ? parseInt(merged.pending_count, 10) : Math.max(0, total - done);
  const runDone = parseInt(merged.run_done, 10) || 0;
  const runTotal = parseInt(merged.run_total, 10) || 0;
  const pct = merged.percent != null
    ? merged.percent
    : (total ? Math.min(100, Math.round((done / total) * 100)) : 0);
  const runPct = runTotal ? Math.min(100, Math.round((runDone / runTotal) * 100)) : null;
  const eta = fmtEta(merged.eta_seconds);
  const cur = (merged.current_stock_name && merged.current_stock_code)
    ? `${merged.current_stock_name}(${merged.current_stock_code})`
    : null;

  const parts = [];
  if (total > 0) parts.push(`${num(done)}/${num(total)} (${pct}%)`);
  else if (done > 0) parts.push(`${num(done)}종목`);
  if (runTotal > 0) parts.push(`이번 ${num(runDone)}/${num(runTotal)}${runPct != null ? `(${runPct}%)` : ''}`);
  if (pending > 0) parts.push(`남음 ${num(pending)}`);
  if (merged.running && cur) parts.push(`진행 ${cur}`);
  if (merged.running && eta) parts.push(`ETA ${eta}`);
  if (!merged.running && merged.status === 'idle' && parts.length) parts.push('일시중지/종료');
  return parts.length ? parts.join(' · ') : '-';
}

function applyStockNewsProgress() { /* no-op: row remark-only UI */ }

function stopBatchProgressPolling() {
  if (batchProgressTimer) {
    clearInterval(batchProgressTimer);
    batchProgressTimer = null;
  }
}

function startBatchProgressPolling() {
  if (batchProgressTimer) return;
  batchProgressTimer = setInterval(async () => {
    try {
      const p = await fetchJSON('/batch-status/stock-news-progress', { timeoutMs: 8000 });
      applyStockNewsProgress(p);
      const hint = $('themeBatchHint');
      if (hint && p.biz_date) hint.textContent = `기준일 ${p.biz_date}`;
    } catch (_) { /* ignore transient poll errors */ }
  }, BATCH_PROGRESS_POLL_MS);
}

async function loadThemeBatchStatus() {
  const body = $('themeBatchBody');
  if (!body) return;
  try {
    const d = await fetchJSON('/batch-status', { timeoutMs: 25000 });
    const hint = $('themeBatchHint');
    const biz = d.latest_article_biz_date || d.latest_keyword_biz_date || '-';
    if (hint) hint.textContent = `기준일 ${biz}`;
    const fmtAt = (v) => {
      if (!v) return '-';
      if (/오전|오후/.test(String(v))) return String(v);
      return dtDb(v, true);
    };
    const fmtRunState = (job) => {
      if (job.running) return '<span class="tag-chip theme">실행 중</span>';
      if (job.registered === true && job.enabled === false) return '<span class="tag-chip kw">비활성</span>';
      if (job.registered === true) return '<span class="tag-chip">등록됨</span>';
      if (job.registered === false) return '<span class="tag-chip kw">미등록</span>';
      return '<span class="tag-chip kw">-</span>';
    };
    const jobs = Array.isArray(d.batch_jobs) ? d.batch_jobs : [];
    const progress = d.stock_news_progress || {};
    const jobRows = jobs.map((job) => `
      <tr title="${esc(job.description || '')}">
        <td>${esc(job.label || job.id || '-')}</td>
        <td>${fmtRunState(job)}</td>
        <td>${esc(job.schedule || '-')}</td>
        <td>${esc(fmtAt(job.last_run_at || job.log_last_at))}</td>
        <td>${esc(fmtAt(job.next_run_at))}</td>
        <td>${job.id === 'stock_news' ? esc(stockNewsRemark(job, progress, d.article_stock_count_today)) : '-'}</td>
      </tr>
    `).join('');
    body.innerHTML = `
      <div class="grid cols-2 board-kv-grid" style="margin-bottom:10px;">
        <div class="kv"><span class="k">테마 스냅샷 최근</span><span class="v">${esc(fmtAt(d.theme_snapshot_last_at))}</span></div>
        <div class="kv"><span class="k">뉴스 배치 최근</span><span class="v">${esc(fmtAt(d.news_batch_last_at))}</span></div>
        <div class="kv"><span class="k">키워드 집계 최근</span><span class="v">${esc(fmtAt(d.keyword_stats_last_at))}</span></div>
        <div class="kv"><span class="k">오늘 기사/종목</span><span class="v">${num(d.article_count_today || 0)}건 / ${num(d.article_stock_count_today || 0)}종목</span></div>
      </div>
      <table class="tbl">
        <thead><tr>
          <th>배치</th><th>상태</th><th>스케줄</th><th>최근 실행</th><th>다음 실행</th><th>비고</th>
        </tr></thead>
        <tbody>${jobRows || '<tr><td colspan="6"><div class="empty">배치 목록 없음 (서버 재시작 후 새로고침)</div></td></tr>'}</tbody>
      </table>
      <div class="hint" style="margin-top:8px;">Windows 작업 스케줄러의 <code>Stocke*</code> / <code>stocke-*</code> 작업을 표시합니다.</div>
      <div class="hint" style="margin-top:4px;">전체 종목 키워드는 <code>stock_news_daily_batch</code> (장 마감 후, 분할 실행)에서 수집됩니다.</div>
    `;
    if (progress.running) startBatchProgressPolling();
    else stopBatchProgressPolling();
  } catch (_) {
    body.innerHTML = emptyRow('배치 상태를 불러오지 못했습니다.', '⚠️');
  }
}
async function sendTelegramNow() {
  const btn = $('tgSendBtn'); btn.disabled = true; const orig = btn.innerHTML;
  btn.innerHTML = '<span class="spin"></span> 전송 중...';
  try {
    const r = await postJSON('/telegram/send-now');
    if (r.success) toast(`텔레그램 전송 완료 (조건식 ${r.condition_count}개 / ${r.stock_count}종목)`);
    else toast(r.message || '전송 실패', true);
  } catch (e) { toast('전송 중 오류가 발생했습니다.', true); }
  finally { btn.disabled = false; btn.innerHTML = orig; }
}

function holdingEvalAmount(h) {
  if (!h) return 0;
  if (h.evalAmt > 0) return h.evalAmt;
  if (h.cur > 0 && h.qty > 0) return h.cur * h.qty;
  return h.buyAmt || 0;
}

function kiwoomHoldingForPosition(p) {
  const code = String(p.stock_code || '').replace(/^A/i, '').split('_')[0];
  return (window._kiwoomHoldings || []).find((h) => h.code === code);
}

function positionBuyAmount(p) {
  const kh = kiwoomHoldingForPosition(p);
  if (kh?.buyAmt > 0) return kh.buyAmt;
  return parseNum(p.actual_buy_amount) || parseNum(p.buy_amount) || 0;
}

function positionEvalAmount(p) {
  const kh = kiwoomHoldingForPosition(p);
  const fromKiwoom = holdingEvalAmount(kh);
  if (fromKiwoom > 0) return fromKiwoom;
  const qty = parseNum(p.buy_quantity);
  const cur = parseNum(p.exit_levels?.current_price ?? p.current_price ?? p.buy_price);
  if (qty > 0 && cur > 0) return qty * cur;
  return positionBuyAmount(p);
}

function stockEvalTotalDenominator(items) {
  const kiwoom = window._kiwoomHoldings || [];
  const kiwoomSum = kiwoom.reduce((s, h) => s + holdingEvalAmount(h), 0);
  if (kiwoomSum > 0) return kiwoomSum;
  const fromAccount = parseNum(window._stockEvalTotal);
  if (fromAccount > 0) return fromAccount;
  return items.reduce((s, p) => s + positionEvalAmount(p), 0);
}

/** 표시용 — 소수 1자리, 합계 정확히 100.0% */
function normalizeWeightPercents(raw) {
  if (!raw.length) return [];
  const sum = raw.reduce((s, w) => s + w, 0);
  if (sum <= 0) return raw.map(() => 0);
  const scaled = raw.map((w) => (w / sum) * 100);
  if (scaled.length === 1) return [Math.round(scaled[0] * 10) / 10];
  const out = scaled.slice(0, -1).map((w) => Math.round(w * 10) / 10);
  const partial = out.reduce((s, w) => s + w, 0);
  out.push(Math.round((100 - partial) * 10) / 10);
  return out;
}

function allAccountHoldingsShown(items) {
  const kiwoom = window._kiwoomHoldings || [];
  if (!kiwoom.length) return true;
  const codes = new Set(
    items.map((p) => String(p.stock_code || '').replace(/^A/i, '').split('_')[0]),
  );
  return kiwoom.every((h) => codes.has(h.code)) && codes.size === kiwoom.length;
}

function computePositionWeights(items) {
  const total = stockEvalTotalDenominator(items);
  if (total <= 0) return items.map(() => 0);
  const raw = items.map((p) => (positionEvalAmount(p) / total) * 100);
  if (allAccountHoldingsShown(items)) {
    return normalizeWeightPercents(raw);
  }
  return raw.map((w) => Math.round(w * 10) / 10);
}

function positionBuyPrice(p) {
  const kh = kiwoomHoldingForPosition(p);
  if (kh?.avg > 0) return kh.avg;
  const bp = parseNum(p.buy_price) || parseNum(p.avg_buy_price);
  if (bp > 0) return bp;
  const amt = positionBuyAmount(p);
  const qty = parseNum(p.buy_quantity);
  if (amt > 0 && qty > 0) return Math.round(amt / qty);
  return 0;
}

function pctStopRate(ex, p) {
  if (ex.stop_loss_rate != null) return Math.abs(parseNum(ex.stop_loss_rate));
  if (p.applied_stop_loss_rate != null) return Math.abs(parseNum(p.applied_stop_loss_rate));
  const buy = positionBuyPrice(p);
  const lvl = (ex.levels || []).find((l) => l.reason === 'STOP_LOSS' && l.method === 'PCT');
  if (lvl?.price && buy > 0) return (buy - parseNum(lvl.price)) / buy * 100;
  return null;
}
function pctStopPrice(ex, p) {
  if (ex.stop_loss_price_pct) return parseNum(ex.stop_loss_price_pct);
  const lvl = (ex.levels || []).find((l) => l.reason === 'STOP_LOSS' && l.method === 'PCT');
  if (lvl?.price) return parseNum(lvl.price);
  const sl = pctStopRate(ex, p);
  const buy = positionBuyPrice(p);
  if (sl && buy > 0) return Math.round(buy * (1 - sl / 100));
  return null;
}

function effectiveStopMeta(ex) {
  const px = ex.effective_stop_price != null ? parseNum(ex.effective_stop_price) : null;
  if (!px) {
    const levels = ex.levels || [];
    if (!levels.length) return { price: null, method: '', reason: '' };
    const best = levels.reduce((a, b) => (parseNum(a.price) > parseNum(b.price) ? a : b));
    return { price: parseNum(best.price), method: best.method || '', reason: best.reason || '' };
  }
  const lv = (ex.levels || []).find((l) => parseNum(l.price) === px);
  return {
    price: px,
    method: lv?.method || '',
    reason: lv?.reason || ex.effective_stop_reason || '',
  };
}
function effectiveStopRate(ex, p) {
  const buy = positionBuyPrice(p);
  const px = effectiveStopMeta(ex).price;
  if (buy > 0 && px > 0) return (buy - px) / buy * 100;
  return null;
}
function effectiveStopDisplayRate(ex, p) {
  if (ex.effective_stop_reason === 'TRAILING') {
    const trail = parseNum(ex.trailing_stop_pct);
    if (trail > 0) return { prefix: '고점−', rate: trail };
    const peak = parseNum(ex.peak_price || p.peak_price);
    const px = parseNum(ex.effective_stop_price);
    if (peak > 0 && px > 0) return { prefix: '고점−', rate: (peak - px) / peak * 100 };
  }
  const rate = effectiveStopRate(ex, p);
  return { prefix: '', rate };
}

function levelChipPct(l, ex, p, slRate) {
  const peak = parseNum(ex.peak_price || p.peak_price);
  const buy = positionBuyPrice(p);
  const px = parseNum(l.price);
  if (l.reason === 'TRAILING' && peak > 0 && px > 0) {
    return (peak - px) / peak * 100;
  }
  if (l.method === 'PCT' && l.reason === 'STOP_LOSS' && slRate != null) return slRate;
  if (buy > 0 && px > 0) return (buy - px) / buy * 100;
  return null;
}
function peakDropMeta(ex, p, cur) {
  const peak = parseNum(ex.peak_price || p.peak_price || cur);
  const price = parseNum(cur);
  if (!(peak > 0 && price > 0)) return { amount: null, pct: null };
  const amount = Math.max(0, peak - price);
  const pct = amount > 0 ? (amount / peak) * 100 : 0;
  return { amount, pct };
}
function fmtPeakDrop(amount, pct) {
  if (amount == null || pct == null) return '-';
  if (amount <= 0) return '0 (0.00%)';
  return `−${num(amount)} (−${pct.toFixed(2)}%)`;
}
function renderLevelChip(l, ex, p, effStop, slRate) {
  const active = l.price === effStop;
  const lbl = REASON_LABEL[l.reason] || l.reason;
  const pct = levelChipPct(l, ex, p, slRate);
  const pctTxt = pct != null && pct > 0 ? `−${pct.toFixed(1)}% · ` : '';
  return `<span class="pos-level-chip ${active ? 'active' : ''} ${l.method === 'ATR' ? 'atr' : ''}">${lbl} ${pctTxt}${num(l.price)} (${l.method})</span>`;
}

function renderPosSellBtn(p) {
  const pending = !!p.pending_sell;
  return `<button type="button" class="btn danger sm pos-sell-btn" data-pos-id="${p.id}"${pending ? ' disabled title="매도 주문 진행 중"' : ''}>${pending ? '청산 주문 중' : '수동 청산'}</button>`;
}

function posThemeChipsHtml(p) {
  const list = (p.theme_items && p.theme_items.length)
    ? p.theme_items
    : (Array.isArray(p.themes) ? p.themes : []);
  if (!list.length) return '';
  return tagChipHtml(list.slice(0, 3), 'theme');
}

const STRATEGY_LABEL = {
  legacy: '레거시',
  sangtta: '상따',
  breakout: '돌파',
  ymgp: '역매공파',
};

function posStrategyChipHtml(p) {
  const key = String(p.strategy_key || '').trim().toLowerCase();
  if (!key) return '';
  const label = STRATEGY_LABEL[key] || key;
  return `<span class="tag-chip strategy ${esc(key)}" title="전략">${esc(label)}</span>`;
}

function posNameTagsHtml(p) {
  const strat = posStrategyChipHtml(p);
  const themes = posThemeChipsHtml(p);
  if (!strat && !themes) return '';
  return ` <span class="pos-themes">${strat}${themes || ''}</span>`;
}

/** 상따·돌파 SOFT 연속 확인 (exit_levels.soft_confirm_*) */
function softConfirmMeta(p) {
  const key = String(p.strategy_key || '').trim().toLowerCase();
  if (key !== 'sangtta' && key !== 'breakout') return null;
  const ex = p.exit_levels || {};
  const polls = parseInt(ex.soft_confirm_polls, 10);
  const count = parseInt(ex.soft_confirm_count, 10);
  if (!Number.isFinite(polls) || polls <= 0) return null;
  const n = Number.isFinite(count) && count > 0 ? count : 0;
  return {
    count: n,
    polls,
    label: ex.soft_confirm_label || (key === 'sangtta' ? '상한가 이탈·급락' : '구조 이탈'),
    text: `${n}/${polls}`,
    active: n > 0,
  };
}

function softConfirmMetricHtml(p) {
  const soft = softConfirmMeta(p);
  if (!soft) return '';
  return `<div><div class="pk" title="${esc(soft.label)} SOFT 연속 확인">SOFT</div><div class="pv ${soft.active ? 'down' : 'flat'}">${soft.text}</div></div>`;
}

function softConfirmStopHint(p) {
  const soft = softConfirmMeta(p);
  if (!soft) return '';
  if (soft.active) return ` · SOFT ${soft.text} (${esc(soft.label)})`;
  return ` · SOFT ${soft.text}`;
}

function normStockCode(code) {
  return String(code || '').trim().replace(/^A/i, '');
}

function isSparklineStockCode(code) {
  return /^[0-9A-Za-z]{6}$/.test(String(code || '').trim());
}

/** 키움 분봉 timestamp — 타임존 없는 KST 벽시계 */
function parseKstWallClock(s) {
  if (!s) return null;
  const raw = String(s).trim();
  if (!raw) return null;
  const iso = (raw.includes('T') ? raw : raw.replace(' ', 'T')).slice(0, 19);
  const d = new Date(`${iso}+09:00`);
  return Number.isNaN(d.getTime()) ? null : d.getTime();
}

function barTimeMs(ts) {
  if (!ts) return null;
  const raw = String(ts).trim();
  if (/[zZ]|[+-]\d{2}:?\d{2}$/.test(raw)) {
    const d = parseDbUtc(raw);
    return d ? d.getTime() : null;
  }
  return parseKstWallClock(raw);
}

function buyTimeMs(ts) {
  if (!ts) return null;
  const raw = String(ts).trim();
  const d = parseDbUtc(raw.includes('T') ? raw : raw.replace(' ', 'T'));
  return d ? d.getTime() : null;
}

function buyMarkerOnSparkline(timestamps, buyTime, width) {
  const buyMs = buyTimeMs(buyTime);
  if (!buyMs || !timestamps?.length) return null;
  const barTimes = timestamps.map((t) => barTimeMs(t)).filter((t) => t != null);
  if (barTimes.length < 2) return null;
  const t0 = barTimes[0];
  const t1 = barTimes[barTimes.length - 1];
  // 전일·장전 매수 → 시점 점이 아니라 매수가 가이드라인으로 표시
  if (buyMs <= t0) return { prior: true, x: 0 };
  if (buyMs >= t1) return { prior: false, x: width };
  for (let i = 0; i < barTimes.length - 1; i++) {
    if (buyMs >= barTimes[i] && buyMs <= barTimes[i + 1]) {
      const span = barTimes[i + 1] - barTimes[i] || 1;
      const ratio = (buyMs - barTimes[i]) / span;
      return { prior: false, x: ((i + ratio) / (barTimes.length - 1)) * width };
    }
  }
  return { prior: true, x: 0 };
}

function renderSparklineSvg(sp, opts, width = 108, height = 34) {
  if (!sp || !sp.closes || sp.closes.length < 2) {
    return '<span class="pos-spark empty">-</span>';
  }
  const o = opts || {};
  const buyPrice = parseNum(o.buyPrice);
  const closes = sp.closes.map((v) => parseNum(v));
  const open = parseNum(sp.open || closes[0]);
  const min = Math.min(...closes, open, buyPrice > 0 ? buyPrice : open);
  const max = Math.max(...closes, open, buyPrice > 0 ? buyPrice : open);
  const range = max - min || 1;
  const pad = 2;
  const innerH = height - pad * 2;
  const pts = closes.map((v, i) => {
    const x = (i / (closes.length - 1)) * width;
    const y = pad + innerH - ((v - min) / range) * innerH;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const up = closes[closes.length - 1] >= open;
  const stroke = up ? 'var(--up)' : 'var(--down)';
  const openY = pad + innerH - ((open - min) / range) * innerH;
  const chg = sp.change_pct != null ? sp.change_pct : ((closes[closes.length - 1] - open) / open * 100);
  let buyMark = '';
  if (buyPrice > 0 && o.buyTime) {
    const mark = buyMarkerOnSparkline(sp.timestamps, o.buyTime, width);
    if (mark) {
      const by = pad + innerH - ((buyPrice - min) / range) * innerH;
      if (mark.prior) {
        buyMark = `<line class="pos-spark-buy-level" x1="0" y1="${by.toFixed(1)}" x2="${width}" y2="${by.toFixed(1)}"/>`;
      } else {
        buyMark = `<circle class="pos-spark-buy" cx="${mark.x.toFixed(1)}" cy="${by.toFixed(1)}" r="2.8"/>`;
      }
    }
  }
  const buyHint = buyPrice > 0 ? ` · 매수 ${num(buyPrice)}` : '';
  return `<div class="pos-spark-wrap" title="당일 15분봉 · 시가 대비 ${rateStr(chg)}${buyHint}">
    <svg class="pos-spark" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" aria-hidden="true">
      <line x1="0" y1="${openY.toFixed(1)}" x2="${width}" y2="${openY.toFixed(1)}" class="pos-spark-open"/>
      <polyline points="${pts}" class="pos-spark-line" style="stroke:${stroke}"/>
      ${buyMark}
    </svg>
    <span class="pos-spark-chg ${signClass(chg)}">${rateStr(chg)}</span>
  </div>`;
}

function sparklineForPosition(p) {
  const code = normStockCode(p.stock_code);
  return renderSparklineSvg((window._positionSparklines || {})[code], {
    buyPrice: positionBuyPrice(p),
    buyTime: p.buy_time,
  });
}

const _sparklineCache = { at: 0, data: {} };
const SPARKLINE_TTL_MS = 300000;
let sparklineLoading = false;
let _sparklineRetryTimer = null;

async function loadPositionSparklines(items) {
  if (!items?.length || sparklineLoading) return;
  const codes = [...new Set(items.map((p) => normStockCode(p.stock_code)).filter(isSparklineStockCode))];
  if (!codes.length) return;
  const now = Date.now();
  const stale = now - _sparklineCache.at > SPARKLINE_TTL_MS;
  const missing = codes.some((c) => {
    const sp = _sparklineCache.data[c];
    return !sp || !sp.timestamps?.length;
  });
  if (!stale && !missing) {
    window._positionSparklines = _sparklineCache.data;
    if ($('autoPositionsBody') && (window._appHoldings || []).length) {
      renderPositionCards(window._appHoldings, 'autoPositionsBody');
      bindPositionSellButtons();
    }
    return;
  }
  sparklineLoading = true;
  try {
    const d = await fetchJSON(`/positions/intraday-sparklines?codes=${codes.join(',')}`, { timeoutMs: 60000 });
    const got = d.sparklines || {};
    _sparklineCache.data = { ..._sparklineCache.data, ...got };
    // 빈 응답(슬롯 거절 등)은 TTL을 올리지 않아 다음 폴링에서 재시도
    if (Object.keys(got).length) {
      _sparklineCache.at = now;
    }
    window._positionSparklines = _sparklineCache.data;
    if ($('autoPositionsBody') && (window._appHoldings || []).length) {
      renderPositionCards(window._appHoldings, 'autoPositionsBody');
      bindPositionSellButtons();
    }
    const stillMissing = codes.some((c) => {
      const sp = _sparklineCache.data[c];
      return !sp || !sp.timestamps?.length;
    });
    if (stillMissing && !_sparklineRetryTimer) {
      _sparklineRetryTimer = setTimeout(() => {
        _sparklineRetryTimer = null;
        if ((window._appHoldings || []).length) loadPositionSparklines(window._appHoldings);
      }, 8000);
    }
  } catch (_) {
    /* 스파크라인 실패는 포지션 표시와 분리 */
    if (!_sparklineRetryTimer) {
      _sparklineRetryTimer = setTimeout(() => {
        _sparklineRetryTimer = null;
        if ((window._appHoldings || []).length) loadPositionSparklines(window._appHoldings);
      }, 8000);
    }
  } finally {
    sparklineLoading = false;
  }
}

function renderPositionCards(items, containerId) {
  const el = $(containerId);
  if (!el) return;
  if (!items.length) { el.innerHTML = emptyRow('보유 포지션 없음 — 매수 체결 시 여기에 표시됩니다.', '💼'); return; }

  const weights = computePositionWeights(items);
  el.innerHTML = `<div class="pos-grid">${items.map((p, i) => {
    const ex = p.exit_levels || {};
    const rate = parseNum(ex.profit_loss_rate ?? p.current_profit_loss_rate);
    const pl = parseNum(ex.profit_loss ?? p.current_profit_loss);
    const cur = ex.current_price || p.current_price || p.buy_price;
    const buyAmt = positionBuyAmount(p);
    const weight = weights[i];
    const effStop = ex.effective_stop_price;
    const effMeta = effectiveStopMeta(ex);
    const effPrice = effMeta.price;
    const effRate = effectiveStopRate(ex, p);
    const effDisplay = effectiveStopDisplayRate(ex, p);
    const dist = ex.stop_distance_pct;
    const pctRate = pctStopRate(ex, p);
    const peakDrop = peakDropMeta(ex, p, cur);
    const peakDropAmt = ex.peak_drop_amount != null ? parseNum(ex.peak_drop_amount) : peakDrop.amount;
    const peakDropPct = ex.peak_drop_pct != null ? parseNum(ex.peak_drop_pct) : peakDrop.pct;
    const trailPctSetting = parseNum(ex.trailing_stop_pct);
    const safe = dist != null && dist > 1.5;
    const atrTxt = ex.atr ? `ATR ${num(ex.atr)}원 (${ex.atr_period}일)` : (ex.levels_live === false ? 'ATR: 새로고침 시 계산' : 'ATR 미사용 (%기준)');
    const reason = REASON_LABEL[ex.effective_stop_reason] || ex.effective_stop_reason || '-';
    const methodLbl = effMeta.method === 'ATR' ? 'ATR' : (effMeta.method === 'PCT' ? 'PCT' : '');

    const chips = (ex.levels || []).map((l) => renderLevelChip(l, ex, p, effStop, pctRate)).join('');

    const themeChips = posNameTagsHtml(p);
    return `<div class="pos-card">
      <div class="pos-card-head">
        <div class="pos-card-title">
          <div class="name">${esc(p.stock_name)}${themeChips}</div>
          <div class="code">${esc(p.stock_code)} · ${num(p.buy_quantity)}주</div>
        </div>
        <div class="pos-card-spark">${sparklineForPosition(p)}</div>
        <div class="pos-pnl ${signClass(rate)}">${pnlStr(pl)}<small>${rateStr(rate)}</small></div>
      </div>
      <div class="pos-metrics">
        <div><div class="pk">매입단가</div><div class="pv">${num(positionBuyPrice(p))}</div></div>
        <div><div class="pk">현재가</div><div class="pv">${num(cur)}</div></div>
        <div><div class="pk">손절율${methodLbl ? ` <span class="pk-sub">(${methodLbl})</span>` : ''}</div><div class="pv down">${effRate != null ? `−${effRate.toFixed(1)}%` : '-'}</div></div>
        <div><div class="pk">손절가${methodLbl ? ` <span class="pk-sub">(${methodLbl})</span>` : ''}</div><div class="pv down">${effPrice ? num(effPrice) : '-'}</div></div>
        <div><div class="pk">매입금액</div><div class="pv">${won(buyAmt)}</div></div>
        <div><div class="pk">비중</div><div class="pv" title="평가금액 ÷ 키움 주식총평가">${weight.toFixed(1)}%</div></div>
        <div><div class="pk">고점</div><div class="pv">${num(ex.peak_price || p.peak_price || cur)}</div></div>
        <div><div class="pk">고점대비 하락</div><div class="pv ${peakDropAmt > 0 ? 'down' : 'flat'}">${fmtPeakDrop(peakDropAmt, peakDropPct)}</div></div>
        ${softConfirmMetricHtml(p)}
        <div><div class="pk">${ex.trailing_armed ? '트레일링' : '트레일 시작'}</div><div class="pv ${ex.trailing_armed ? 'up' : ''}">${ex.trailing_armed && effStop ? num(effStop) : (ex.trailing_start_price ? num(ex.trailing_start_price) : '-')}</div></div>
        ${ex.trailing_floor_price ? `<div><div class="pk">익절 바닥</div><div class="pv up">${num(ex.trailing_floor_price)}</div></div>` : ''}
        <div><div class="pk">${ex.atr ? 'ATR' : '변동성'}</div><div class="pv text-cyan">${ex.atr ? num(ex.atr) + '원' : '-'}</div></div>
      </div>
      <div class="pos-stop-bar ${safe ? 'safe' : ''}">
        <div class="stop-main">유효 손절선 <span style="color:var(--down);">${effPrice ? num(effPrice) + '원' : '-'}</span>
          ${effDisplay.rate != null && effPrice ? `<span class="stop-pct-hint"> · ${methodLbl || esc(reason)} ${effDisplay.prefix}−${effDisplay.rate.toFixed(1)}%</span>` : ''}
          <span class="pill run" style="margin-left:6px;">${esc(reason)}</span></div>
        <div class="stop-sub">${atrTxt}${dist != null ? ` · 손절선까지 ${dist.toFixed(2)}%` : ''}${ex.trailing_armed && peakDropAmt != null ? ` · 고점대비 ${fmtPeakDrop(peakDropAmt, peakDropPct)}` : ''}${ex.trailing_armed && trailPctSetting > 0 ? ` · 트레일 기준 −${trailPctSetting.toFixed(1)}%` : ''}${softConfirmStopHint(p)}${ex.liquidate_time ? ` · ${ex.liquidate_time} 장마감청산` : ''}${ex.levels_live ? ' · 실시간' : ''}</div>
        ${chips ? `<div class="pos-levels">${chips}</div>` : ''}
      </div>
      <div class="pos-card-actions">${renderPosSellBtn(p)}</div>
    </div>`;
  }).join('')}</div>`;
}

function renderKiwoomHoldingsTable(holdings) {
  if (!holdings.length) return '';
  const rows = holdings.map((h) => `<tr>
    <td><span class="stock-name">${esc(h.name || h.code)}</span><span class="stock-code">${esc(h.code)}</span></td>
    <td class="num">${num(h.qty)}</td><td class="num">${num(h.avg)}</td><td class="num">${num(h.cur)}</td>
    <td class="num">${won(h.buyAmt)}</td><td class="num ${signClass(h.pl)}">${pnlStr(h.pl)}</td>
    <td class="num ${signClass(h.rate)}">${rateStr(h.rate)}</td></tr>`).join('');
  return `<div class="activity-banner warn" style="margin-bottom:12px;">⚠️ 키움 계좌에 보유 종목이 있으나 앱 포지션(DB)에는 없습니다. HTS/키움 앱에서 잔고를 확인하세요.</div>
    <table class="tbl"><thead><tr><th>종목</th><th class="num">수량</th><th class="num">매입가</th><th class="num">현재가</th><th class="num">매입금액</th><th class="num">평가손익</th><th class="num">수익률</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function syncKiwoomHoldingsView() {
  const kiwoom = window._kiwoomHoldings || [];
  const appItems = window._appHoldings || [];
  const appCodes = new Set(appItems.map((p) => String(p.stock_code || '').replace(/^A/i, '')));
  const orphans = kiwoom.filter((h) => !appCodes.has(h.code));
  const el = $('positionsBody');
  if (!el || appItems.length > 0) return;
  if (orphans.length) {
    $('holdingCount').textContent = `앱 0 · 키움 ${orphans.length}종목`;
    el.innerHTML = renderKiwoomHoldingsTable(orphans);
  }
}

function renderPositionsTable(items) {
  if (!items.length) return emptyRow('보유 종목이 없습니다.', '💼');
  const weights = computePositionWeights(items);
  const rows = items.map((p, i) => {
    const ex = p.exit_levels || {};
    const rate = parseNum(ex.profit_loss_rate ?? p.current_profit_loss_rate);
    const pl = parseNum(ex.profit_loss ?? p.current_profit_loss);
    const effMeta = effectiveStopMeta(ex);
    const effPrice = effMeta.price;
    const effRate = effectiveStopRate(ex, p);
    const reason = REASON_LABEL[ex.effective_stop_reason] || '';
    const buyAmt = positionBuyAmount(p);
    const themeChips = posNameTagsHtml(p);
    const soft = softConfirmMeta(p);
    const softHint = soft ? ` · SOFT ${soft.text}` : '';
    return `<tr>
      <td><span class="stock-name">${esc(p.stock_name)}</span>${themeChips}<span class="stock-code">${esc(p.stock_code)}</span></td>
      <td class="num">${num(positionBuyPrice(p))}</td><td class="num">${num(ex.current_price || p.current_price || p.buy_price)}</td>
      <td class="num">${num(p.buy_quantity)}</td>
      <td class="num">${won(buyAmt)}</td><td class="num">${weights[i].toFixed(1)}%</td>
      <td class="num ${signClass(pl)}">${pnlStr(pl)}</td><td class="num ${signClass(rate)}">${rateStr(rate)}</td>
      <td class="num down">${effRate != null ? `−${effRate.toFixed(1)}%` : '-'}</td>
      <td class="num down">${effPrice ? num(effPrice) : '-'}</td>
      <td><span class="hint">${esc(reason)}${effMeta.method ? ` · ${effMeta.method}` : ''}${ex.atr ? ` · ATR ${num(ex.atr)}` : ''}${softHint}</span></td>
      <td class="pos-action-cell">${renderPosSellBtn(p)}</td></tr>`;
  }).join('');
  return `<table class="tbl"><thead><tr>
    <th>종목</th><th class="num">매입단가</th><th class="num">현재가</th><th class="num">수량</th>
    <th class="num">매입금액</th><th class="num">비중</th>
    <th class="num">평가손익</th><th class="num">수익률</th><th class="num">손절율</th><th class="num">손절가</th><th>사유</th><th></th>
  </tr></thead><tbody>${rows}</tbody></table>`;
}

async function manualLiquidatePosition(positionId, stockName) {
  const label = stockName || `포지션 #${positionId}`;
  if (!confirm(`${label} 전량 시장가 청산을 주문할까요?\n\n체결 확인은 키움 계좌·활동 로그에서 하세요.`)) return false;
  const res = await fetch(`/positions/${positionId}/manual-sell`, { method: 'POST', headers: { Accept: 'application/json' } });
  let data = {};
  try { data = await res.json(); } catch (_) { /* empty */ }
  if (!res.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === 'string' ? detail : (Array.isArray(detail) ? detail.map((d) => d.msg || d).join(', ') : `${res.status} 청산 실패`));
  }
  toast(data.message || '청산 주문을 접수했습니다.');
  return true;
}

function bindPositionSellButtons() {
  const onClick = async (e) => {
    const btn = e.target.closest('.pos-sell-btn');
    if (!btn || btn.disabled) return;
    const card = btn.closest('.pos-card, tr');
    const name = card?.querySelector('.name, .stock-name')?.textContent?.trim();
    const id = btn.dataset.posId;
    btn.disabled = true;
    const prev = btn.textContent;
    btn.textContent = '주문 중...';
    try {
      const ok = await manualLiquidatePosition(id, name);
      if (ok) {
        loadPositions(true, { silent: true });
        loadActivity();
        loadLog();
      } else {
        btn.disabled = false;
        btn.textContent = prev;
      }
    } catch (err) {
      toast(err.message || '청산 주문 실패', true);
      btn.disabled = false;
      btn.textContent = prev;
    }
  };
  ['autoPositionsBody', 'positionsBody'].forEach((id) => {
    const el = $(id);
    if (el && !el.dataset.sellBound) {
      el.dataset.sellBound = '1';
      el.addEventListener('click', onClick);
    }
  });
}

let positionsLoadSeq = 0;

function shouldLoadPositionsLive() {
  return isAutoTabActive() && isAutoMonitorSubActive();
}

async function loadPositions(live = false, opts = {}) {
  const silent = opts.silent === true;
  // forceLive: 30초 자동/전체새로고침처럼 silent여도 키움 live
  // 10초 폴링은 forceLive 없이 silent → DB만
  const effectiveLive = opts.forceLive === true
    ? true
    : (silent
      ? false
      : (live === true || (opts.preferLive !== false && shouldLoadPositionsLive())));
  const seq = ++positionsLoadSeq;
  const sk = '<div class="skeleton">현재가·ATR 조회 중...</div>';
  if (effectiveLive && !silent && !$('autoPositionsBody')?.querySelector('.pos-card')) {
    if ($('positionsBody')) $('positionsBody').innerHTML = sk;
    if ($('autoPositionsBody')) $('autoPositionsBody').innerHTML = sk;
  }
  try {
    if (!(window._kiwoomHoldings || []).length) {
      try { await loadAccount(); } catch (_) { /* 비중 계산은 DB 폴백 */ }
    }
    const prevById = {};
    (window._appHoldings || []).forEach((p) => { if (p?.id != null) prevById[p.id] = p; });
    const url = `/positions/?status=HOLDING&limit=100&with_levels=true${effectiveLive ? '&live=true' : ''}&_=${Date.now()}`;
    const d = await fetchJSON(url, { timeoutMs: effectiveLive ? 90000 : 12000 });
    if (seq !== positionsLoadSeq) return;
    let items = d.items || [];
    // DB 폴링 시 직전 live 청산레벨을 유지해 카드가 비지 않게 함
    if (!effectiveLive && items.length) {
      items = items.map((p) => {
        const prev = prevById[p.id];
        if (prev?.exit_levels && !p.exit_levels?.levels?.length) {
          return { ...p, exit_levels: { ...prev.exit_levels, ...p.exit_levels, current_price: p.current_price || prev.exit_levels.current_price } };
        }
        return p;
      });
    }
    window._appHoldings = items;
    if ($('holdingCount')) {
      $('holdingCount').textContent = items.length
        ? `${items.length}종목`
        : ((window._kiwoomHoldings || []).length ? `앱 0 · 키움 ${window._kiwoomHoldings.length}종목` : '0종목');
    }
    if ($('autoHoldingCount')) {
      const wSum = items.length ? computePositionWeights(items).reduce((s, w) => s + w, 0) : 0;
      const wHint = items.length && allAccountHoldingsShown(items) ? ' · 비중합 100%' : '';
      $('autoHoldingCount').textContent = `${items.length}종목${wHint}`;
    }
    if ($('autoPositionsBody')) renderPositionCards(items, 'autoPositionsBody');
    bindPositionSellButtons();
    if (items.length) {
      loadPositionSparklines(items);
    }
    if ($('positionsBody')) {
      if (items.length) {
        $('positionsBody').innerHTML = renderPositionsTable(items);
        bindPositionSellButtons();
      } else if ((window._kiwoomHoldings || []).length) {
        syncKiwoomHoldingsView();
      } else {
        $('positionsBody').innerHTML = emptyRow('보유 종목이 없습니다.', '💼');
      }
    }
    if (effectiveLive) {
      $('lastUpdated').textContent = new Date().toLocaleTimeString('ko-KR');
      if (!silent) toast(`보유 ${items.length}종목 현재가 갱신 완료`);
    }
  } catch (e) {
    const msg = effectiveLive ? '현재가/ATR 갱신 실패 — 잠시 후 다시 시도하세요.' : '포지션을 불러오지 못했습니다.';
    if ($('autoPositionsBody') && !silent) $('autoPositionsBody').innerHTML = emptyRow(msg, '⚠️');
    if (!silent && $('positionsBody')) $('positionsBody').innerHTML = emptyRow(msg, '⚠️');
    if (effectiveLive && !silent) toast('보유종목 갱신 실패', true);
  }
}

async function loadAutoPositions(live = true) {
  await loadPositions(live);
}

async function loadSells() {
  try {
    // 체결만 — FAILED(800033 중복주문 등)는 손익에 넣지 않음 (주문 실패는 '주문' 카드)
    const d = await fetchJSON('/sell-orders/?status=COMPLETED&limit=50');
    const items = (d.items || []).slice().sort((a, b) => (parseDbUtc(sellTradeTs(b)) || 0) - (parseDbUtc(sellTradeTs(a)) || 0));
    $('sellCount').textContent = `${items.length}건`;
    if (!items.length) { $('sellsBody').innerHTML = emptyRow('매도 내역이 없습니다.', '📒'); return; }
    const rows = items.map(o => {
      const rate = parseNum(o.profit_loss_rate), pl = parseNum(o.profit_loss);
      const when = tradeWhen(sellTradeTs(o), true);
      return `<tr><td>${esc(when.day)}</td><td>${esc(when.time)}</td>
        <td><span class="stock-name">${esc(o.stock_name)}</span><span class="stock-code">${esc(o.stock_code)}</span></td>
        <td class="num">${num(o.sell_price)}</td><td class="num">${num(o.sell_quantity)}</td>
        <td class="num ${signClass(pl)}">${pnlStr(pl)}</td><td class="num ${signClass(rate)}">${rateStr(rate)}</td><td>${esc(reasonLabel(o.sell_reason))}</td></tr>`;
    }).join('');
    $('sellsBody').innerHTML = `<table class="tbl"><thead><tr><th>일자</th><th>시각</th><th>종목</th><th class="num">매도가</th><th class="num">수량</th><th class="num">손익</th><th class="num">수익률</th><th>사유</th></tr></thead><tbody>${rows}</tbody></table>`;
  } catch (e) { $('sellsBody').innerHTML = emptyRow('매도 내역을 불러오지 못했습니다.', '⚠️'); }
}
function orderStatusLabel(status) {
  const m = {
    WATCHING: '관측', ORDERED: '접수', FAILED: '실패', FILLED: '체결',
    COMPLETED: '체결', PENDING: '대기', CANCELLED: '취소', EXPIRED: '만료', PROCESSING: '처리중',
  };
  return m[status] || status || '-';
}

const STRATEGY_PROFILE_LABELS = {
  legacy: '거래대금 눌림목',
  sangtta: '상따',
  breakout: '수급 돌파',
  ymgp: '역매공파',
  jongga: '종가배팅',
  oversold_breakout: '수급 돌파',
  sangtta_breakout: '상따',
  yeokmaegongpa: '역매공파',
  jongga_closing: '종가배팅',
  legacy_momentum: '거래대금 눌림목',
};

function strategyProfileLabel(o) {
  if (!o) return '—';
  if (o.strategy_label) return o.strategy_label;
  const key = (o.strategy_key || o.strategy || '').toString().trim().toLowerCase();
  if (!key) return '—';
  return STRATEGY_PROFILE_LABELS[key] || key;
}

function buyLegLabel(o) {
  if (!o) return '';
  if (o.fill_note) return String(o.fill_note);
  if (o.entry_leg) return `${o.entry_leg}차 매수`;
  const ft = String(o.fill_type || '').toUpperCase();
  if (ft === 'ADD') return '추가매수';
  if (ft === 'INITIAL') return '1차 매수';
  return '';
}

function orderReasonBuy(o) {
  if (o.status === 'FAILED') return o.failure_reason || '사유 미기록';
  if (o.status === 'ORDERED') return '매수 주문 접수';
  if (o.status === 'FILLED' || o.status === 'COMPLETED') {
    const leg = buyLegLabel(o);
    const q = o.fill_quantity != null ? `${num(o.fill_quantity)}주` : '';
    const p = o.fill_price != null ? `@ ${num(o.fill_price)}원` : '';
    return [leg || '매수 체결', q, p].filter(Boolean).join(' ');
  }
  return '-';
}

function orderReasonSell(o) {
  if (o.status === 'FAILED') return o.sell_reason_detail || o.sell_order_id || reasonLabel(o.sell_reason) || '사유 미기록';
  if (o.status === 'CANCELLED') return o.sell_reason_detail || '주문 취소(만료·중복 정리)';
  return o.sell_reason_detail || reasonLabel(o.sell_reason) || '-';
}

async function loadOrders() {
  try {
    const [buyRes, sellRes] = await Promise.all([
      fetchJSON('/trading/orders?limit=200').catch(() => ({ orders: [] })),
      fetchJSON('/sell-orders/?status=ALL&limit=100').catch(() => ({ items: [] })),
    ]);
    const items = [];
    for (const o of buyRes.orders || []) {
      if (isWatchingExitFailure(o)) continue;
      items.push({
        side: '매수',
        name: o.stock_name,
        code: o.stock_code,
        status: o.status,
        reason: orderReasonBuy(o),
        strategy: strategyProfileLabel(o),
        ts: o.filled_at || o.detected_at,
      });
    }
    for (const o of sellRes.items || []) {
      if (o.status === 'FAILED' || o.status === 'CANCELLED') {
        items.push({
          side: '매도',
          name: o.stock_name,
          code: o.stock_code,
          status: o.status,
          reason: orderReasonSell(o),
          strategy: strategyProfileLabel(o),
          ts: o.completed_at || o.ordered_at || o.created_at,
        });
      }
    }
    items.sort((a, b) => (parseDbUtc(b.ts) || 0) - (parseDbUtc(a.ts) || 0));
    $('ordersCount').textContent = `${items.length}건`;
    if (!items.length) {
      $('ordersBody').innerHTML = emptyRow('주문 내역이 없습니다.', '🧾');
      return;
    }
    const stateP = (s) => {
      if (s === 'FAILED') return 'off';
      if (s === 'FILLED' || s === 'COMPLETED') return 'on';
      if (s === 'CANCELLED') return 'run';
      return 'run';
    };
    const sideC = (s) => (s === '매수' ? 'up' : 'down');
    const rows = items.map((o) => {
      const when = tradeWhen(o.ts, true);
      return `<tr>
      <td>${esc(when.day)}</td><td>${esc(when.time)}</td>
      <td class="${sideC(o.side)}" style="font-weight:700;">${esc(o.side)}</td>
      <td><span class="stock-name">${esc(o.name)}</span><span class="stock-code">${esc(o.code)}</span></td>
      <td><span class="hint">${esc(o.strategy || '—')}</span></td>
      <td><span class="pill ${stateP(o.status)}">${esc(orderStatusLabel(o.status))}</span></td>
      <td style="white-space:normal;color:var(--muted);max-width:280px;">${esc(o.reason)}</td></tr>`;
    }).join('');
    $('ordersBody').innerHTML = `<table class="tbl"><thead><tr>
      <th>일자</th><th>시각</th><th>구분</th><th>종목</th><th>전략</th><th>상태</th><th>사유</th>
    </tr></thead><tbody>${rows}</tbody></table>`;
  } catch (e) {
    $('ordersBody').innerHTML = emptyRow('주문 내역을 불러오지 못했습니다.', '⚠️');
  }
}

/* ===== 조건식 ===== */
let _conditionsListPromise = null;
let _conditionsListAt = 0;
const CONDITIONS_LIST_TTL_MS = 30000;

async function fetchConditionsList() {
  const now = Date.now();
  if (_conditionsListPromise && (now - _conditionsListAt) < CONDITIONS_LIST_TTL_MS) {
    return _conditionsListPromise;
  }
  _conditionsListAt = now;
  _conditionsListPromise = fetchJSON('/conditions/', { timeoutMs: 45000 })
    .then((d) => {
      if (!d || d.error || d.detail || d.success === false) {
        throw new Error((d && (d.error || d.detail)) || '조건식 목록 조회 실패');
      }
      return Array.isArray(d) ? d : (d.data || []);
    })
    .catch((e) => {
      _conditionsListPromise = null;
      _conditionsListAt = 0;
      throw e;
    });
  return _conditionsListPromise;
}

async function loadConditions() {
  try {
    const conds = await fetchConditionsList();
    $('condCount').textContent = `${conds.length}개`;
    if (!conds.length) { $('conditionsBody').innerHTML = emptyRow('조건식이 없습니다.', '🔍'); return; }
    $('conditionsBody').innerHTML = conds.map(c => `
      <div class="cond-item" data-id="${esc(c.id)}" data-name="${esc(c.condition_name)}">
        <div><div class="cname">${esc(c.condition_name)}</div><div class="cmeta">ID ${esc(c.id)} · API ${esc(c.api_id)}</div></div>
        <span class="pill ${c.is_enabled ? 'on' : 'off'}">${c.is_enabled ? '자동매매' : '대기'}</span></div>`).join('');
    document.querySelectorAll('.cond-item').forEach(el => { el.onclick = () => selectCondition(el.dataset.id, el.dataset.name, el); });
  } catch (e) { $('conditionsBody').innerHTML = emptyRow('조건식을 불러오지 못했습니다.', '⚠️'); }
}
async function selectCondition(id, name, el) {
  document.querySelectorAll('.cond-item').forEach(x => x.classList.remove('active'));
  if (el) el.classList.add('active');
  $('condStockTitle').textContent = name;
  $('condStockHint').innerHTML = '<span class="spin" style="border-color:#ccc;border-top-color:#2f6bff;"></span> 조회 중...';
  $('condStocksBody').innerHTML = '<div class="skeleton">종목 검색 중... (수 초 소요)</div>';
  try {
    const d = await fetchJSON(`/conditions/${id}/stocks?condition_name=${encodeURIComponent(name)}`);
    const stocks = d.stocks || [];
    $('condStockHint').textContent = `${stocks.length}종목`;
    if (!stocks.length) { $('condStocksBody').innerHTML = emptyRow('편입 종목이 없습니다.', '🔍'); return; }
    const rows = stocks.map(s => {
      const rate = parseNum(s.change_rate), diff = parseNum(s.price_diff);
      return `<tr><td><span class="stock-name">${esc(s.stock_name)}</span><span class="stock-code">${esc(s.stock_code)}</span></td>
        <td class="num">${num(s.current_price)}</td><td class="num ${signClass(diff)}">${diff > 0 ? '▲' : (diff < 0 ? '▼' : '')}${num(Math.abs(diff))}</td>
        <td class="num ${signClass(rate)}">${rateStr(rate)}</td><td class="num">${num(s.volume)}</td></tr>`;
    }).join('');
    $('condStocksBody').innerHTML = `<table class="tbl"><thead><tr><th>종목</th><th class="num">현재가</th><th class="num">전일대비</th><th class="num">등락률</th><th class="num">거래량</th></tr></thead><tbody>${rows}</tbody></table>`;
  } catch (e) { $('condStockHint').textContent = '오류'; $('condStocksBody').innerHTML = emptyRow('종목을 불러오지 못했습니다.', '⚠️'); }
}

function drawPerfChart(labels, data) {
  const ctx = $('perfChart'); if (!ctx || !window.Chart) return;
  const up = data.length && data[data.length - 1] >= 0;
  const color = up ? '#34d399' : '#f87171';
  const fillColor = up ? 'rgba(52,211,153,0.12)' : 'rgba(248,113,113,0.12)';
  const peak = data.length ? Math.max(0, Math.max.apply(null, data)) : 0;
  const peakLine = data.map(() => peak);
  if (perfChart) perfChart.destroy();
  perfChart = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets: [
      { data, borderColor: color, backgroundColor: fillColor, fill: true, tension: 0.25, pointRadius: 0, borderWidth: 2, order: 2 },
      { data: peakLine, borderColor: '#3f4859', borderWidth: 1.5, borderDash: [6, 5], fill: false, pointRadius: 0, order: 1 },
    ] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: {
        filter: (c) => c.datasetIndex === 0,
        backgroundColor: '#1a1f2b', borderColor: '#2f3647', borderWidth: 1,
        titleColor: '#8b95a8', bodyColor: '#e8edf5',
        callbacks: { title: () => '', label: (c) => num(c.parsed.y) + '원' },
      } },
      scales: {
        x: { display: false },
        y: {
          display: false,
          ticks: { callback: (v) => (v / 10000).toFixed(0) + '만', color: '#7a8496' },
          grid: { display: false },
          border: { display: false },
        },
      },
    },
  });
}

/* ===== 실시간 활동 로그 ===== */
function fmtScanTime(iso) {
  if (!iso) return '-';
  try { return new Date(iso).toLocaleTimeString('ko-KR'); } catch { return iso; }
}
function runtimeBadge(on, label) {
  return `<span class="pill ${on ? 'on' : 'off'}">${label}</span>`;
}
async function loadActivity() {
  try {
    const d = await fetchJSON('/trading/activity-log?limit=80');
    const rt = d.runtime || {};
    const enabled = !!rt.auto_trade_enabled;
    const stopActive = rt.stop_loss_running !== false;
    const allRunning = enabled && rt.scanner_running && rt.buy_executor_running && stopActive;
    const banner = $('activityBanner');
    if (banner) {
      banner.className = 'activity-banner' + (allRunning ? '' : ' off');
      const lastScan = fmtScanTime(rt.last_scan_at);
      const lastSync = fmtScanTime(rt.last_sync_at);
      const byStrat = rt.last_scan_by_strategy || {};
      const stratBits = ['legacy', 'sangtta', 'breakout', 'ymgp']
        .map((k) => byStrat[k])
        .filter((x) => x && (x.targets > 0 || x.created > 0))
        .map((x) => `${x.label || '?'} ${x.targets || 0}/${x.created || 0}`);
      const scanInfo = rt.last_scan_at
        ? `마지막 스캔 ${lastScan} · 대상 ${rt.last_scan_targets || 0} · 신호 ${rt.last_scan_created || 0}`
          + (stratBits.length ? `<br><span class="hint">전략별 대상/신호: ${stratBits.map(esc).join(' · ')}</span>` : '')
        : (enabled ? '아직 스캔 없음 (1분 주기)' : '자동매매 OFF — 스캔 미실행');
      const syncInfo = stopActive
        ? (rt.last_sync_at
          ? `마지막 동기화 ${lastSync} (${rt.monitor_interval_sec || 30}초 주기)`
          : `포지션 동기화 대기 (${rt.monitor_interval_sec || 30}초 주기)`)
        : '장외 — 손절/익절 모니터 일시 중지 (다음 장 시작 시 재개)';
      banner.innerHTML = `
        <div class="ab-item"><span class="ab-dot ${allRunning ? 'pulse' : ''}"></span>
          <strong>${allRunning ? '자동매매 실행 중' : (enabled ? '일부 중지됨' : '자동매매 OFF · 동기화만')}</strong></div>
        <div class="ab-item">${runtimeBadge(rt.scanner_running, '스캐너')}${runtimeBadge(rt.buy_executor_running, '매수')}${runtimeBadge(stopActive, '동기화')}</div>
        <div class="ab-item hint">${syncInfo}</div>
        <div class="ab-item hint">${scanInfo}</div>
        <div class="ab-item hint">${rt.is_trading_day === false ? esc(rt.trading_day_block_reason || '휴장') : (rt.in_trade_hours ? '장중' : '장외')} · ${rt.allows_new_buy === false ? esc(rt.new_buy_block_reason || '매수 차단') : '매수 허용'}${rt.mock_mode ? ' · 모의' : ''}</div>`;
    }
    const events = d.events || [];
    const body = $('activityBody');
    if (!body) return;
    if (!events.length) {
      const rt = d.runtime || {};
      let hint = '활동 이벤트 없음 — 서버 재시작 직후이거나 아직 한 사이클이 돌지 않았습니다.';
      if (stopActive) {
        hint = `포지션 동기화 루프 실행 중 (${rt.monitor_interval_sec || 30}초마다 [SYNC] 로그). 자동매매 ON 시 [SCANNER]/[BUY] 로그도 표시됩니다.`;
      } else if (rt.stop_loss_loop_alive) {
        hint = '장외 — 손절/익절 모니터 일시 중지. 다음 거래일 장 시작 시 자동 재개됩니다.';
      }
      body.innerHTML = `<div class="activity-line info"><span class="msg">${esc(hint)}</span></div>`;
      return;
    }
    body.innerHTML = events.map(e => {
      const t = e.ts ? e.ts.replace('T', ' ').slice(11, 19) : '--:--:--';
      const lvl = e.level || 'info';
      return `<div class="activity-line ${lvl}"><span class="ts">${t}</span><span class="src">[${esc(e.source || '?')}]</span><span class="msg">${esc(e.message || '')}</span></div>`;
    }).join('');
  } catch (e) {
    const body = $('activityBody');
    if (body) body.innerHTML = '<div class="activity-line error"><span class="msg">활동 로그를 불러오지 못했습니다.</span></div>';
  }
}

/* ===== 자동매매 로그 ===== */
function logReasonForSell(o) {
  if (o.status === 'FAILED') {
    return o.sell_reason_detail || o.sell_order_id || reasonLabel(o.sell_reason) || '사유 미기록';
  }
  return o.sell_reason_detail || reasonLabel(o.sell_reason) || '';
}
function logReasonForBuy(o) {
  if (o.status === 'FAILED') {
    return o.failure_reason || '사유 미기록';
  }
  if (o.status === 'ORDERED') return '매수 주문 접수';
  if (o.status === 'FILLED' || o.status === 'COMPLETED') {
    return orderReasonBuy(o);
  }
  return '';
}

/** 관측(WATCHING) 만료 실패 — 체결 시도가 아니므로 체결 로그에서 제외 */
function isWatchingExitFailure(o) {
  if (!o || o.status !== 'FAILED') return false;
  const r = String(o.failure_reason || '');
  return r.startsWith('관측 종료');
}
function logStateLabel(status, action) {
  if (status === 'COMPLETED' || status === 'FILLED' || status === 'ORDERED') return '성공';
  if (status === 'FAILED') return '실패';
  if (status === 'PENDING') return '대기';
  if (status === 'WATCHING') return '관측';
  if (status === 'EXPIRED') return '만료';
  return status || '-';
}

function logDaysFilter() {
  const el = $('logDays');
  const n = el ? parseInt(el.value, 10) : 1;
  if (n === 1 || n === 3 || n === 7) return n;
  return 1;
}

function isWithinLogDays(ts, days, assumeUtc) {
  if (!ts) return false;
  const d = assumeUtc ? parseDbUtc(ts) : new Date(ts);
  if (!d || Number.isNaN(d.getTime())) return false;
  const cutoff = new Date();
  cutoff.setHours(0, 0, 0, 0);
  cutoff.setDate(cutoff.getDate() - (days - 1));
  return d >= cutoff;
}

function logPnlCell(l) {
  if (l.action !== '매도' || l.pl == null || l.pl === '') return '—';
  const pl = parseNum(l.pl);
  const ratePart = (l.rate != null && l.rate !== '')
    ? `<small>${rateStr(parseNum(l.rate))}</small>`
    : '';
  return `<div class="pos-pnl ${signClass(pl)}">${pnlStr(pl)}${ratePart}</div>`;
}

async function loadLog() {
  const days = logDaysFilter();
  try {
    const [sells, orders] = await Promise.all([
      fetchJSON('/sell-orders/?status=ALL&limit=100').catch(() => ({ items: [] })),
      fetchJSON('/trading/orders?limit=200').catch(() => ({ orders: [] })),
    ]);
    let logs = [];
    for (const o of (sells.items || [])) {
      const filled = o.status === 'COMPLETED' || o.status === 'FILLED';
      logs.push({
        ts: o.completed_at || o.ordered_at || o.created_at,
        action: '매도',
        name: o.stock_name,
        code: o.stock_code,
        qty: o.sell_quantity,
        state: logStateLabel(o.status, '매도'),
        reason: logReasonForSell(o),
        strategy: strategyProfileLabel(o),
        pl: filled ? o.profit_loss : null,
        rate: filled ? o.profit_loss_rate : null,
      });
    }
    for (const o of (orders.orders || [])) {
      if (o.status !== 'FILLED' && o.status !== 'COMPLETED' && o.status !== 'ORDERED' && o.status !== 'FAILED') continue;
      // 관측 만료는 체결 시도가 아님 — 매수 게이트/주문 실패만 표시
      if (isWatchingExitFailure(o)) continue;
      logs.push({
        ts: o.filled_at || o.detected_at,
        action: '매수',
        name: o.stock_name,
        code: o.stock_code,
        qty: o.fill_quantity != null ? o.fill_quantity : '-',
        state: logStateLabel(o.status, '매수'),
        reason: logReasonForBuy(o),
        strategy: strategyProfileLabel(o),
        pl: null,
        rate: null,
      });
    }
    logs = logs.filter(l => l.ts && isWithinLogDays(l.ts, days, true)).sort((a, b) => {
      const ta = parseDbUtc(a.ts);
      const tb = parseDbUtc(b.ts);
      return (tb || 0) - (ta || 0);
    });
    $('logCount').textContent = `${logs.length}건 · 최근 ${days}일`;
    if (!logs.length) {
      $('logBody').innerHTML = emptyRow(`최근 ${days}일 체결 내역이 없습니다.`, '🧾');
      return;
    }
    const stateP = (s) => s === '성공' ? 'on' : (s === '실패' ? 'off' : 'run');
    const actC = (a) => a === '매수' ? 'up' : 'down';
    const rows = logs.map(l => `<tr>
      <td>${dtDb(l.ts, true)}</td><td class="${actC(l.action)}" style="font-weight:700;">${esc(l.action)}</td>
      <td><span class="stock-name">${esc(l.name)}</span><span class="stock-code">${esc(l.code)}</span></td>
      <td><span class="hint">${esc(l.strategy || '—')}</span></td>
      <td class="num">${esc(l.qty)}</td><td><span class="pill ${stateP(l.state)}">${esc(l.state)}</span></td>
      <td class="num">${logPnlCell(l)}</td>
      <td style="white-space:normal;color:var(--muted);">${esc(l.reason)}</td></tr>`).join('');
    $('logBody').innerHTML = `<table class="tbl"><thead><tr><th>시각</th><th>동작</th><th>종목</th><th>전략</th><th class="num">수량</th><th>상태</th><th class="num">손익</th><th>사유</th></tr></thead><tbody>${rows}</tbody></table>`;
  } catch (e) { $('logBody').innerHTML = emptyRow('로그를 불러오지 못했습니다.', '⚠️'); }
}

/* ===== 스크리너 후보 ===== */
const PT_LABEL = { LEVERAGE: '레버리지', INVERSE: '인버스', DOUBLE_INVERSE: '곱버스', ETF: 'ETF', ETN: 'ETN', STOCK: '일반' };
const PT_CLS = { LEVERAGE: 'on', INVERSE: 'run', DOUBLE_INVERSE: 'off', ETF: 'off', ETN: 'off', STOCK: 'run' };
const SRC_LABEL = { screener: '거래대금', condition: '조건식', both: '거래대금+조건식' };
function sourceLabel(s) {
  const base = SRC_LABEL[s.source] || s.source || '—';
  if (s.condition_name) return `${base} (${s.condition_name})`;
  return base;
}
function fmtEokOrJo(v) {
  if (v === null || v === undefined || v === '') return '-';
  const n = parseNum(v);
  if (Math.abs(n) >= 10000) return numFixed(n / 10000, 2) + '조';
  return numFixed(n, 1) + '억';
}
function tagChipHtml(list, cls) {
  const arr = Array.isArray(list) ? list.filter(Boolean) : [];
  if (!arr.length) return '<span class="muted">-</span>';
  return `<span class="tag-chips">${arr.map((t) => {
    if (t && typeof t === 'object' && t.name) {
      const tier = t.tier === 'core' ? ' core' : (t.tier === 'related' ? ' related' : (t.tier === 'event' ? ' event' : ''));
      const score = t.score != null ? ` ${(t.score * 100).toFixed(0)}%` : '';
      return `<span class="tag-chip ${cls || ''}${tier}" title="연관도${score}">${esc(t.name)}</span>`;
    }
    return `<span class="tag-chip ${cls || ''}">${esc(String(t))}</span>`;
  }).join('')}</span>`;
}
async function loadScreener() {
  const market = $('scrMarket').value;
  $('screenerBody').innerHTML = '<div class="skeleton">거래대금순 후보 조회 중...</div>';
  $('screenerCount').textContent = '';
  try {
    const d = await fetchJSON(`/screener/candidates?market=${market}`, { timeoutMs: 90000 });
    if (!d.success) { $('screenerBody').innerHTML = emptyRow(d.error || '조회 실패 (장중/토큰 확인)', '⚠️'); return; }
    const items = d.items || [];
    const rawN = d.raw_count != null ? d.raw_count : d.total;
    const excl = d.excluded_etf_count != null ? d.excluded_etf_count : 0;
    const exclNeg = d.excluded_negative_count != null ? d.excluded_negative_count : 0;
    const exclOh = d.excluded_overheat_count != null ? d.excluded_overheat_count : 0;
    const exclAmt = d.excluded_low_amount_count != null ? d.excluded_low_amount_count : 0;
    const exclParts = [`ETF·파생 ${excl}`];
    if (exclNeg > 0) exclParts.push(`등락미달 ${exclNeg}`);
    if (exclOh > 0) exclParts.push(`과열 ${exclOh}`);
    if (exclAmt > 0) exclParts.push(`대금미달 ${exclAmt}`);
    const limitPart = d.candidate_limit ? ` · 상위 ${d.candidate_limit}` : '';
    const amtFloor = d.min_trade_amount_eok != null ? d.min_trade_amount_eok : null;
    const amtPart = amtFloor ? ` · 대금≥${amtFloor}억` : '';
    const tagged = items.filter(s => (s.themes && s.themes.length) || (s.keywords && s.keywords.length)).length;
    $('screenerCount').textContent = `후보 ${d.selected_count} / 전체 ${items.length}${limitPart}${amtPart} (${exclParts.join(', ')} 제외) · 테마표시 ${tagged}`;
    if (!items.length) { $('screenerBody').innerHTML = emptyRow('데이터가 없습니다.', '🧭'); return; }
    const rows = items.map(s => {
      const rate = parseNum(s.change_rate);
      const amtEok = parseNum(s.trade_amount) / 100; // 백만원 → 억원
      return `<tr class="${s.included ? '' : 'screener-out'}">
        <td style="text-align:center;">${s.included ? '<span class="scr-in" title="편입">✓</span>' : '<span class="scr-out-mark">—</span>'}</td>
        <td><span class="stock-name">${esc(s.stock_name)}</span><span class="stock-code">${esc(s.stock_code)}</span></td>
        <td><span class="pill run">${esc(sourceLabel(s))}</span></td>
        <td class="screener-tags">${tagChipHtml(s.theme_items && s.theme_items.length ? s.theme_items : s.themes, 'theme')}</td>
        <td class="screener-tags">${tagChipHtml(s.keywords, 'kw')}</td>
        <td><span class="pill ${PT_CLS[s.product_type] || 'off'}">${esc(PT_LABEL[s.product_type] || s.product_type)}</span></td>
        <td class="num">${num(s.current_price)}</td>
        <td class="num ${signClass(rate)}">${rateStr(rate)}</td>
        <td class="num">${s.volume ? num(s.volume) : '—'}</td>
        <td class="num">${s.trade_amount ? `${num(amtEok)}억` : '—'}</td>
        <td class="num">${fmtEokOrJo(s.market_cap)}</td>
        <td class="num">${s.per == null ? '-' : numFixed(s.per, 2)}</td>
        <td class="num">${s.pbr == null ? '-' : numFixed(s.pbr, 2)}</td>
        <td class="num">${s.roe == null ? '-' : numFixed(s.roe, 2)}</td></tr>`;
    }).join('');
    $('screenerBody').innerHTML = `<table class="tbl"><thead><tr><th>편입</th><th>종목</th><th>출처</th><th>테마</th><th>키워드</th><th>구분</th><th class="num">현재가</th><th class="num">등락률</th><th class="num">거래량</th><th class="num">거래대금</th><th class="num">시총</th><th class="num">PER</th><th class="num">PBR</th><th class="num">ROE</th></tr></thead><tbody>${rows}</tbody></table>`;
  } catch (e) {
    const timedOut = e && (e.name === 'AbortError' || /aborted/i.test(String(e.message || e)));
    console.error('[screener]', e);
    $('screenerBody').innerHTML = emptyRow(
      timedOut
        ? '조회 시간 초과 (키움 API 지연 후 재시도)'
        : `조회 중 오류: ${esc(String((e && e.message) || e || '서버/네트워크'))}`,
      '⚠️',
    );
  }
}

/* ===== 상따 후보 (ka10027 등락률상위) ===== */
async function loadSangtta() {
  $('sangttaBody').innerHTML = '<div class="skeleton">상따 후보 조회 중...</div>';
  $('sangttaCount').textContent = '';
  try {
    const d = await fetchJSON(`/sangtta/candidates`, { timeoutMs: 90000 });
    if (!d.success) { $('sangttaBody').innerHTML = emptyRow(d.error || '조회 실패', '⚠️'); return; }
    const items = d.items || [];
    const excl = d.excluded_etf_count != null ? d.excluded_etf_count : 0;
    const minChg = d.min_change_rate != null ? d.min_change_rate : 13;
    $('sangttaCount').textContent = `후보 ${items.length}${d.candidate_limit ? ` · 대금상위 ${d.candidate_limit}` : ''}${d.pool_count != null ? ` (풀 ${d.pool_count})` : ''} · 등락≥${minChg}% · ETF제외 ${excl}`;
    if (!items.length) { $('sangttaBody').innerHTML = emptyRow('데이터가 없습니다. (장중·필터 확인)', '🔎'); return; }
    const rows = items.map(s => {
      const rate = parseNum(s.change_rate);
      return `<tr>
        <td><span class="stock-name">${esc(s.stock_name)}</span><span class="stock-code">${esc(s.stock_code)}</span></td>
        <td class="num">${num(s.current_price)}</td>
        <td class="num ${signClass(rate)}">${rateStr(rate)}</td>
        <td class="num">${s.volume ? num(s.volume) : '—'}</td>
        <td class="screener-tags">${tagChipHtml(s.theme_items && s.theme_items.length ? s.theme_items : s.themes, 'theme')}</td>
      </tr>`;
    }).join('');
    $('sangttaBody').innerHTML = `<table class="tbl"><thead><tr><th>종목</th><th class="num">현재가</th><th class="num">등락률</th><th class="num">거래량</th><th>테마</th></tr></thead><tbody>${rows}</tbody></table>`;
  } catch (e) {
    const timedOut = e && (e.name === 'AbortError' || /aborted/i.test(String(e.message || e)));
    $('sangttaBody').innerHTML = emptyRow(
      timedOut ? '조회 시간 초과 (API 지연 후 재시도)' : '조회 중 오류 (서버/네트워크 확인)',
      '⚠️',
    );
  }
}

/* ===== 수급 돌파 후보 (Phase0~3 관측용) ===== */
function renderBreakoutGateChecks(checks) {
  const list = Array.isArray(checks) ? checks : [];
  if (!list.length) return '';
  const chips = list.map((c) => {
    const enabled = c.enabled !== false;
    const ok = !!c.ok;
    let cls = 'gate-chip';
    if (!enabled) cls += ' off';
    else cls += ok ? ' ok' : ' bad';
    const mark = !enabled ? '–' : (ok ? '✓' : '✗');
    const title = [c.key, c.detail].filter(Boolean).join(' · ');
    return `<span class="${cls}" title="${esc(title)}"><b>${mark}</b> ${esc(c.key)}${
      c.detail ? `<i>${esc(c.detail)}</i>` : ''
    }</span>`;
  }).join('');
  return `<div class="gate-checks">${chips}</div>`;
}

async function loadBreakout() {
  $('breakoutBody').innerHTML = '<div class="skeleton">돌파 후보와 레벨 계산 중...</div>';
  $('breakoutCount').textContent = '';
  try {
    const d = await fetchJSON('/breakout/candidates', { timeoutMs: 90000 });
    if (!d.success) {
      $('breakoutBody').innerHTML = emptyRow(d.error || d.detail || '조회 실패', '⚠️');
      return;
    }
    const items = d.items || [];
    const errHint = (d.errors && d.errors.length)
      ? ` · 조건식 오류: ${d.errors.join(', ')}`
      : '';
    $('breakoutCount').textContent = `후보 ${items.length}${errHint}`;
    if (!items.length) {
      const emptyMsg = (d.errors && d.errors.length)
        ? `조건식 조회 실패 (${d.errors.join(', ')})`
        : (d.message || '조건식 편입 종목이 없습니다.');
      $('breakoutBody').innerHTML = emptyRow(emptyMsg, d.errors && d.errors.length ? '⚠️' : '🔎');
      return;
    }
    const rows = items.map((s) => {
      const proximity = s.proximity_pct == null ? '—' : `${parseNum(s.proximity_pct) >= 0 ? '+' : ''}${parseNum(s.proximity_pct).toFixed(2)}%`;
      const gateCls = s.gate_ok ? 'up' : 'down';
      const checksHtml = renderBreakoutGateChecks(s.gate_checks);
      const failKeys = (s.gate_checks || [])
        .filter((c) => c && c.enabled !== false && !c.ok && c.key !== '진입확인')
        .map((c) => c.key);
      const summary = s.gate_ok
        ? (s.entry_confirm_mode ? `통과 · ${s.entry_confirm_mode}` : '통과')
        : (failKeys.length ? `미충족: ${failKeys.join(', ')}` : (s.gate_reason || '대기'));
      const reasonHint = (!s.gate_ok && s.gate_reason && s.gate_reason !== summary)
        ? `<div class="hint">${esc(s.gate_reason)}</div>`
        : '';
      return `<tr>
        <td><b>${esc(s.stock_name || s.stock_code)}</b><div class="hint">${esc(s.stock_code)}</div></td>
        <td>${esc(s.condition_name || '—')}</td>
        <td>${esc(s.level_kind || '—')}<div class="hint">${s.level_price ? `${num(s.level_price)}원` : '—'}</div></td>
        <td class="num ${gateCls}">${proximity}</td>
        <td class="num">${s.volume_ratio == null ? '—' : `${parseNum(s.volume_ratio).toFixed(2)}배`}</td>
        <td class="${gateCls}">
          <div class="gate-status">${esc(summary)}</div>
          ${reasonHint}
          ${checksHtml}
        </td>
      </tr>`;
    }).join('');
    $('breakoutBody').innerHTML = `<table class="tbl"><thead><tr><th>종목</th><th>조건식</th><th>돌파 레벨</th><th class="num">근접도</th><th class="num">거래량</th><th>조건 체크</th></tr></thead><tbody>${rows}</tbody></table>`;
  } catch (e) {
    const msg = String(e && e.message || '');
    const hint = msg.includes('404')
      ? 'API 없음 — 서버를 재시작해 주세요'
      : (msg.includes('AbortError') || msg.includes('aborted')
        ? '조회 시간 초과 — 잠시 후 다시 시도'
        : `조회 실패${msg ? ` (${msg})` : ''}`);
    $('breakoutBody').innerHTML = emptyRow(hint, '⚠️');
  }
}

/* ===== 역매공파 후보 (일봉 단계 관측용) ===== */
const YMGP_STAGE_LABEL = {
  NONE: '탈락',
  FILTERED: '역배열',
  READY: '바닥/지지',
  ARMED: '매집·공구리',
  ENTERED_1: '1차 진입',
  ENTERED_2: '2차 진입',
  MANAGING: '익절 관리',
  STOPPED: '손절 락',
  DONE: '종료',
};
const YMGP_STAGE_CLS = {
  NONE: 'off',
  FILTERED: 'off',
  READY: 'run',
  ARMED: 'on',
  ENTERED_1: 'on',
  ENTERED_2: 'on',
  MANAGING: 'on',
  STOPPED: 'off',
  DONE: 'off',
};
function renderYmgpChecks(checks) {
  const list = Array.isArray(checks) ? checks : [];
  if (!list.length) return '';
  const chips = list.map((c) => {
    const passed = !!c.passed;
    const cls = passed ? 'gate-chip ok' : 'gate-chip bad';
    const mark = passed ? '✓' : '✗';
    const title = [c.label, c.actual].filter(Boolean).join(' · ');
    return `<span class="${cls}" title="${esc(title)}"><b>${mark}</b> ${esc(c.label || c.key)}${
      c.actual ? `<i>${esc(c.actual)}</i>` : ''
    }</span>`;
  }).join('');
  return `<div class="gate-checks">${chips}</div>`;
}
async function loadYmgp() {
  $('ymgpBody').innerHTML = '<div class="skeleton">역매공파 후보와 일봉 단계 계산 중...</div>';
  $('ymgpCount').textContent = '';
  try {
    const d = await fetchJSON('/ymgp/candidates', { timeoutMs: 120000 });
    if (!d.success) {
      $('ymgpBody').innerHTML = emptyRow(d.error || d.detail || '조회 실패', '⚠️');
      return;
    }
    const items = d.items || [];
    const errHint = (d.errors && d.errors.length)
      ? ` · 조건식 오류: ${d.errors.join(', ')}`
      : '';
    $('ymgpCount').textContent = `후보 ${items.length}${errHint}`;
    if (!items.length) {
      const emptyMsg = (d.errors && d.errors.length)
        ? `조건식 조회 실패 (${d.errors.join(', ')})`
        : (d.message || '조건식 편입 종목이 없습니다.');
      $('ymgpBody').innerHTML = emptyRow(emptyMsg, d.errors && d.errors.length ? '⚠️' : '🔎');
      return;
    }
    const rows = items.map((s) => {
      const stage = s.ymgp_stage || 'NONE';
      const stageCls = YMGP_STAGE_CLS[stage] || 'off';
      const stageLabel = YMGP_STAGE_LABEL[stage] || stage;
      const ref = s.ymgp_ref || {};
      const refHtml = ref.high
        ? `${num(ref.high)}원<div class="hint">${esc(ref.date || '')}${ref.vol_mult ? ` · x${ref.vol_mult}` : ''}</div>`
        : '—';
      const armed = stage === 'ARMED';
      const gateCls = s.gate_ok ? 'up' : 'down';
      const gateHtml = armed
        ? `<div class="gate-status">${s.gate_ok ? '통과' : (esc(s.gate_reason) || '대기')}</div>`
        : `<span class="hint">${esc(s.ymgp_reason || stageLabel)}</span>`;
      const checksHtml = renderYmgpChecks(s.ymgp_checks);
      return `<tr>
        <td><b>${esc(s.stock_name || s.stock_code)}</b><div class="hint">${esc(s.stock_code)}</div></td>
        <td>${esc(s.condition_name || '—')}</td>
        <td><span class="pill ${stageCls}">${esc(stageLabel)}</span>${s.reentry_locked ? ' <span class="pill off">락</span>' : ''}</td>
        <td>${refHtml}</td>
        <td class="${armed ? gateCls : ''}">${gateHtml}</td>
        <td>${checksHtml}</td>
      </tr>`;
    }).join('');
    $('ymgpBody').innerHTML = `<table class="tbl"><thead><tr><th>종목</th><th>조건식</th><th>단계</th><th>기준봉</th><th>게이트</th><th>체크요약</th></tr></thead><tbody>${rows}</tbody></table>`;
  } catch (e) {
    const msg = String(e && e.message || '');
    const hint = msg.includes('404')
      ? 'API 없음 — 서버를 재시작해 주세요'
      : (msg.includes('AbortError') || msg.includes('aborted')
        ? '조회 시간 초과 — 잠시 후 다시 시도'
        : `조회 실패${msg ? ` (${msg})` : ''}`);
    $('ymgpBody').innerHTML = emptyRow(hint, '⚠️');
  }
}

/* ===== 종가배팅 후보 ===== */
async function pickJongga(code, name) {
  if (!code) return;
  const ok = window.confirm(`${name || code}(${code}) 종가배팅 매수할까요?`);
  if (!ok) return;
  try {
    const d = await postJSON('/jongga/pick', { stock_code: code });
    if (!d.success) {
      window.alert(d.detail || d.error || d.message || '선택 실패');
      return;
    }
    window.alert(d.message || '매수 신호 생성됨');
    loadJongga(false);
  } catch (e) {
    window.alert(String(e && e.message || e) || '선택 실패');
  }
}

async function loadJongga(rebuild = true) {
  if (!$('jonggaBody')) return;
  $('jonggaBody').innerHTML = '<div class="skeleton">종가배팅 후보(거래대금·테마) 조회 중...</div>';
  if ($('jonggaCount')) $('jonggaCount').textContent = '';
  try {
    const q = rebuild ? '?rebuild=1' : '';
    const d = await fetchJSON(`/jongga/candidates${q}`, { timeoutMs: 120000 });
    if (!d.success) {
      $('jonggaBody').innerHTML = emptyRow(d.error || d.detail || '조회 실패', '⚠️');
      return;
    }
    const items = d.items || [];
    const theme = d.strongest_theme || '—';
    const win = d.pick_window || {};
    const picked = d.picked_code || '';
    const status = d.status || '';
    const w = d.score_weights || { pullback: 1, amount: 1, change: 1 };
    window._jonggaScoreWeights = w;
    if ($('jonggaCount')) {
      $('jonggaCount').textContent =
        `최강테마 ${theme} · 후보 ${items.length}`
        + (win.start ? ` · ${win.start}~${win.end}` : '')
        + (picked ? ` · 선택 ${picked}` : '')
        + (status ? ` · ${status}` : '');
    }
    if (!items.length) {
      $('jonggaBody').innerHTML = emptyRow('최강 테마 후보가 없습니다. (테마맵·거래대금순 확인)', '🔎');
      return;
    }
    const canPick = !picked && status !== 'auto' && status !== 'done';
    const wLine = [
      w.pullback != null ? `눌림×${Number(w.pullback)}` : null,
      w.amount != null ? `대금×${Number(w.amount)}` : null,
      w.change != null ? `등락×${Number(w.change)}` : null,
    ].filter(Boolean).join(' + ');
    const scoreHint = d.score_hint
      || '눌림 = 15분봉 고가 대비 하락률. 스코어 = min-max(눌림·대금·등락) 가중합. ★=자동매수 1순위.';
    window._jonggaItems = items.map((s) => ({ ...s }));
    window._jonggaRenderOpts = {
      canPick, picked, autoPick: d.auto_pick, scoreHint, wLine,
    };
    renderJonggaTable(window._jonggaItems, window._jonggaRenderOpts);
    await loadJonggaSparklines(window._jonggaItems);
  } catch (e) {
    const msg = String(e && e.message || '');
    const hint = msg.includes('404')
      ? 'API 없음 — 서버를 재시작해 주세요'
      : (msg.includes('AbortError') || msg.includes('aborted')
        ? '조회 시간 초과 — 잠시 후 다시 시도'
        : `조회 실패${msg ? ` (${msg})` : ''}`);
    $('jonggaBody').innerHTML = emptyRow(hint, '⚠️');
  }
}

function _jonggaMinMax(vals) {
  if (!vals.length) return [];
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  if (hi <= lo) return vals.map(() => 0.5);
  return vals.map((v) => (v - lo) / (hi - lo));
}

function rescoreJonggaItems(items, w) {
  const wp = Number(w?.pullback ?? 1) || 1;
  const wa = Number(w?.amount ?? 1) || 1;
  const wc = Number(w?.change ?? 1) || 1;
  const wsum = wp + wa + wc || 3;
  const pulls = items.map((s) => Number(s.pullback_pct) || 0);
  const amts = items.map((s) => Number(s.trade_amount) || 0);
  const chgs = items.map((s) => Number(s.change_rate) || 0);
  const np = _jonggaMinMax(pulls);
  const na = _jonggaMinMax(amts);
  const nc = _jonggaMinMax(chgs);
  items.forEach((s, i) => {
    s.score = Math.round(((wp * np[i] + wa * na[i] + wc * nc[i]) / wsum) * 1e6) / 1e6;
    s.score_parts = {
      pullback_n: Math.round(np[i] * 1e4) / 1e4,
      amount_n: Math.round(na[i] * 1e4) / 1e4,
      change_n: Math.round(nc[i] * 1e4) / 1e4,
    };
  });
  items.sort((a, b) => (Number(b.score) || 0) - (Number(a.score) || 0)
    || (Number(b.trade_amount) || 0) - (Number(a.trade_amount) || 0));
  return items;
}

function renderJonggaTable(items, opts) {
  const { canPick, picked, autoPick, scoreHint, wLine } = opts || {};
  const autoCode = autoPick && autoPick.stock_code;
  const rows = items.map((s, idx) => {
    const code = s.stock_code || '';
    const isAuto = autoCode && autoCode === code;
    const isPicked = picked && picked === code;
    const btn = canPick
      ? `<button type="button" class="btn primary sm jongga-pick" data-code="${esc(code)}" data-name="${esc(s.stock_name || code)}">선택</button>`
      : (isPicked ? '<span class="pill on">선택됨</span>' : '—');
    const parts = s.score_parts || {};
    const scoreDetail = [
      parts.pullback_n != null ? `눌 ${Number(parts.pullback_n).toFixed(2)}` : null,
      parts.amount_n != null ? `대 ${Number(parts.amount_n).toFixed(2)}` : null,
      parts.change_n != null ? `등 ${Number(parts.change_n).toFixed(2)}` : null,
    ].filter(Boolean).join(' · ');
    const hiTitle = s.day_high
      ? `차트고가 ${num(s.day_high)}${s.chart_last ? ` · 종가 ${num(s.chart_last)}` : ''}`
      : '15분봉 고가 대비 하락률';
    return `<tr class="${isAuto ? 'row-hl' : ''}" data-code="${esc(code)}">
      <td>${idx + 1}${isAuto ? ' ★' : ''}</td>
      <td><b>${esc(s.stock_name || code)}</b><div class="hint">${esc(code)}</div></td>
      <td class="jongga-spark-cell" data-code="${esc(code)}"><span class="pos-spark empty">…</span></td>
      <td>${esc(s.theme || '미분류')}</td>
      <td class="num">${fmtEokOrJo(s.market_cap)}</td>
      <td>${s.trade_amount != null ? num(Math.round(Number(s.trade_amount))) : '—'}</td>
      <td>${s.change_rate != null ? `${Number(s.change_rate).toFixed(2)}%` : '—'}</td>
      <td class="jongga-pb-cell" data-code="${esc(code)}" title="${esc(hiTitle)}">${s.pullback_pct != null ? `${Number(s.pullback_pct).toFixed(2)}%` : '—'}</td>
      <td class="jongga-score-cell" data-code="${esc(code)}" title="${esc(scoreDetail || scoreHint || '')}">${s.score != null ? Number(s.score).toFixed(3) : '—'}
        ${scoreDetail ? `<div class="hint jongga-score-parts">${esc(scoreDetail)}</div>` : ''}</td>
      <td>${btn}</td>
    </tr>`;
  }).join('');
  $('jonggaBody').innerHTML =
    `<div class="hint jongga-score-hint" style="margin:0 0 8px;">${esc(scoreHint || '')}${wLine ? ` · 가중 ${wLine}` : ''}</div>`
    + `<table class="tbl"><thead><tr>
      <th>#</th><th>종목</th><th title="당일 15분봉">차트</th><th>테마</th><th class="num" title="기본적분석 마트(억원)">시총</th><th>대금</th><th>등락</th><th title="15분봉 고가 대비 하락률">눌림</th><th title="${esc(scoreHint || '')}">스코어</th><th>매수</th>
    </tr></thead><tbody>${rows}</tbody></table>`;
  $('jonggaBody').querySelectorAll('.jongga-pick').forEach((btn) => {
    btn.onclick = () => pickJongga(btn.dataset.code, btn.dataset.name);
  });
}

function pullbackFromSparkline(sp) {
  if (!sp) return null;
  const closes = (sp.closes || []).map((v) => Number(v)).filter((v) => v > 0);
  if (closes.length < 2) return null;
  let hi = Number(sp.day_high) || 0;
  const maxClose = Math.max(...closes);
  if (!hi || hi < maxClose) hi = maxClose;
  const last = Number(sp.last) > 0 ? Number(sp.last) : closes[closes.length - 1];
  if (hi <= 0 || last <= 0) return null;
  return Math.max(0, ((hi - last) / hi) * 100);
}

async function loadJonggaSparklines(items) {
  const list = items || window._jonggaItems || [];
  const codes = [...new Set(list.map((s) => normStockCode(s.stock_code)).filter(isSparklineStockCode))];
  if (!codes.length || !$('jonggaBody')) return;

  let map = {};
  list.forEach((s) => {
    const code = normStockCode(s.stock_code);
    if (s.sparkline && s.sparkline.closes) map[code] = s.sparkline;
  });
  const missing = codes.filter((c) => !map[c]);
  if (missing.length) {
    try {
      const d = await fetchJSON(`/positions/intraday-sparklines?codes=${missing.join(',')}`, { timeoutMs: 60000 });
      map = { ...map, ...(d.sparklines || {}) };
    } catch (_) { /* ignore */ }
  }

  let changed = false;
  list.forEach((s) => {
    const code = normStockCode(s.stock_code);
    const sp = map[code];
    if (!sp) return;
    s.sparkline = sp;
    const closes = (sp.closes || []).map((v) => Number(v)).filter((v) => v > 0);
    const maxClose = closes.length ? Math.max(...closes) : 0;
    const hi = Math.max(Number(sp.day_high) || 0, maxClose);
    const last = Number(sp.last) > 0 ? Number(sp.last) : (closes.length ? closes[closes.length - 1] : 0);
    if (hi > 0) s.day_high = hi;
    if (last > 0) s.chart_last = last;
    const pb = pullbackFromSparkline(sp);
    if (pb == null || Number.isNaN(pb)) return;
    // 백엔드가 0/미산출이어도 차트 고가 기준으로 항상 덮어씀
    if (Number(s.pullback_pct) !== pb) {
      s.pullback_pct = Math.round(pb * 10000) / 10000;
      changed = true;
    }
  });

  if (changed) {
    rescoreJonggaItems(list, window._jonggaScoreWeights);
    const opts = {
      ...(window._jonggaRenderOpts || {}),
      autoPick: list[0],
    };
    window._jonggaRenderOpts = opts;
    renderJonggaTable(list, opts);
  }

  $('jonggaBody').querySelectorAll('.jongga-spark-cell').forEach((cell) => {
    const code = normStockCode(cell.dataset.code);
    cell.innerHTML = renderSparklineSvg(map[code], {}, 120, 36);
  });
  $('jonggaBody').querySelectorAll('.jongga-pb-cell').forEach((cell) => {
    const code = normStockCode(cell.dataset.code);
    const s = list.find((x) => normStockCode(x.stock_code) === code);
    const sp = map[code];
    const pb = s?.pullback_pct != null ? Number(s.pullback_pct) : pullbackFromSparkline(sp);
    if (pb == null || Number.isNaN(pb)) {
      cell.textContent = '—';
      return;
    }
    const hi = s?.day_high ?? sp?.day_high;
    const last = s?.chart_last ?? sp?.last;
    cell.textContent = `${pb.toFixed(2)}%`;
    cell.title = hi
      ? `차트고가 ${num(hi)}${last != null ? ` · 종가 ${num(last)}` : ''} (15분봉)`
      : '15분봉 고가 대비';
  });
}

/* ===== 자동매매 설정 폼 ===== */
function _v(s, k) { return (s[k] === null || s[k] === undefined) ? '' : s[k]; }
function fNum(k, s, ph) { return `<input type="number" id="set_${k}" value="${esc(_v(s, k))}" step="any" placeholder="${esc(ph || '')}">`; }
function fTime(k, s) { return `<input type="time" id="set_${k}" value="${esc(_v(s, k))}">`; }
function fSelect(k, s, opts) { return `<select id="set_${k}">${opts.map(([ov, ol]) => `<option value="${ov}" ${String(_v(s, k)) === ov ? 'selected' : ''}>${esc(ol)}</option>`).join('')}</select>`; }
function fCheck(k, s, label, hint, warn) { return `<label class="check"><input type="checkbox" id="set_${k}" ${s[k] ? 'checked' : ''}>${esc(label)}${hint ? ` <span class="fhint ${warn ? 'warn' : ''}">${esc(hint)}</span>` : ''}</label>`; }
function field(label, inner, hint) { return `<div class="field"><label>${esc(label)}</label>${inner}${hint ? `<span class="fhint">${esc(hint)}</span>` : ''}</div>`; }

function fieldSizingPyramiding(s) {
  const smin = _v(s, 'signal_min_threshold') !== '' ? _v(s, 'signal_min_threshold') : '2';
  const smax = _v(s, 'signal_max_threshold') !== '' ? _v(s, 'signal_max_threshold') : '10';
  const rawMin = parseNum(_v(s, 'initial_min_amount') || 0);
  const rawMax = parseNum(_v(s, 'initial_max_amount') || 0);
  const weakAmt = (rawMin > 0 || rawMax > 0) ? Math.max(rawMin, rawMax) : 5000000;
  const strongAmt = (rawMin > 0 || rawMax > 0) ? Math.min(rawMin, rawMax) : 2000000;
  const weakPct = _v(s, 'initial_max_deposit_pct') !== '' ? _v(s, 'initial_max_deposit_pct') : '';
  const strongPct = _v(s, 'initial_min_deposit_pct') !== '' ? _v(s, 'initial_min_deposit_pct') : '';
  const addPct = _v(s, 'add_buy_deposit_pct') !== '' ? _v(s, 'add_buy_deposit_pct') : '';
  const spread = weakAmt - strongAmt;
  const spreadHint = spread < 500000
    ? '<div class="fhint warn" style="margin-top:8px;">금액 차이가 작으면 등락률별 매수금이 거의 같아집니다. 약한 신호 금액을 더 크게 두는 것을 권장합니다.</div>'
    : '';
  return `<div class="box-soft" id="sizingPyramidPanel">
    <div class="box-title">첫 매수 — 역피라미딩 (등락률↑ 금액↓)</div>
    <div class="desc" style="margin-bottom:10px;">아래 <b>최소 등락</b> 미만이면 매수하지 않습니다. 등락이 커질수록 금액을 줄여 변동성·손절 리스크를 낮춥니다.</div>
    <div class="desc buy-unit-won" style="margin-bottom:10px;">적용 중: 약한 신호 <b>${num(weakAmt)}원</b> · 강한 신호 <b>${num(strongAmt)}원</b></div>
    <div class="desc buy-unit-pct" style="margin-bottom:10px;">예수금 비중으로 환산됩니다. (예: 3천만 × 10% = 300만)</div>
    <div class="sizing-ladder">
      <div class="sizing-row">
        <span class="sr-label">약한 신호</span>
        <span class="sr-eq">등락</span>
        <input type="number" class="w-rate" id="set_signal_min_threshold" value="${esc(smin)}" step="any" placeholder="2">
        <span class="sr-unit">% 이상 →</span>
        <input type="number" class="w-amt buy-unit-won" id="set_initial_max_amount" value="${esc(weakAmt)}" step="any" placeholder="5000000">
        <span class="sr-unit buy-unit-won" style="grid-column:auto;">원 (큰 금액)</span>
        <input type="number" class="w-amt buy-unit-pct" id="set_initial_max_deposit_pct" value="${esc(weakPct)}" step="0.1" min="0" max="100" placeholder="10" data-pct-preview="pyramid_weak">
        <span class="sr-unit buy-unit-pct" style="grid-column:auto;">% (큰 비중)</span>
      </div>
      <div class="hint buy-unit-pct" data-pct-preview-label="pyramid_weak" style="margin:4px 0 8px;"></div>
      <div class="sizing-between">↓ 등락률이 올라갈수록 매수 금액 감소 (자동 비례) ↓</div>
      <div class="sizing-row strong">
        <span class="sr-label">강한 신호</span>
        <span class="sr-eq">등락</span>
        <input type="number" class="w-rate" id="set_signal_max_threshold" value="${esc(smax)}" step="any" placeholder="10">
        <span class="sr-unit">% 이상 →</span>
        <input type="number" class="w-amt buy-unit-won" id="set_initial_min_amount" value="${esc(strongAmt)}" step="any" placeholder="2000000">
        <span class="sr-unit buy-unit-won" style="grid-column:auto;">원 (작은 금액)</span>
        <input type="number" class="w-amt buy-unit-pct" id="set_initial_min_deposit_pct" value="${esc(strongPct)}" step="0.1" min="0" max="100" placeholder="5" data-pct-preview="pyramid_strong">
        <span class="sr-unit buy-unit-pct" style="grid-column:auto;">% (작은 비중)</span>
      </div>
      <div class="hint buy-unit-pct" data-pct-preview-label="pyramid_strong" style="margin:4px 0 8px;"></div>
    </div>${spreadHint}
    <div class="sizing-add-section">
      <div class="box-title">추가매수 — 이미 보유 중일 때</div>
      <div class="desc">매수 후 수익이 나면 같은 종목에 조금씩 더 삽니다.</div>
      <div class="sizing-add-row">
        <span class="sr-label">수익률</span>
        <input type="number" class="w-rate" id="set_add_buy_trigger" value="${esc(_v(s, 'add_buy_trigger') || '0.7')}" step="any">
        <span class="sr-unit">% 이상 →</span>
        <input type="number" class="w-amt buy-unit-won" id="set_add_buy_amount" value="${esc(_v(s, 'add_buy_amount') || '1000000')}" step="any">
        <span class="sr-unit buy-unit-won">원 추가</span>
        <input type="number" class="w-amt buy-unit-pct" id="set_add_buy_deposit_pct" value="${esc(addPct)}" step="0.1" min="0" max="100" placeholder="3" data-pct-preview="add_buy">
        <span class="sr-unit buy-unit-pct">% 추가</span>
      </div>
      <div class="hint buy-unit-pct" data-pct-preview-label="add_buy" style="margin-top:4px;"></div>
    </div>
  </div>`;
}

function fieldSizingFixed(s) {
  const amt = _v(s, 'initial_max_amount') || _v(s, 'max_invest_amount') || '5000000';
  const pct = _v(s, 'initial_max_deposit_pct') !== '' ? _v(s, 'initial_max_deposit_pct') : '';
  const smin = _v(s, 'signal_min_threshold') !== '' ? _v(s, 'signal_min_threshold') : '2';
  return `<div class="box-soft sizing-panel-hidden" id="sizingFixedPanel">
    <div class="box-title">고정 금액 매수</div>
    <div class="desc">조건 충족 시 아래 규칙으로 매수합니다.</div>
    <div class="sizing-row" style="margin-bottom:10px;">
      <span class="sr-label">매수 조건</span>
      <span class="sr-eq">등락</span>
      <input type="number" class="w-rate" id="set_signal_min_threshold_fixed" value="${esc(smin)}" step="any">
      <span class="sr-unit">% 이상 →</span>
      <input type="number" class="w-amt buy-unit-won" id="set_initial_max_amount_fixed" value="${esc(amt)}" step="any">
      <span class="sr-unit buy-unit-won">원</span>
      <input type="number" class="w-amt buy-unit-pct" id="set_initial_max_deposit_pct_fixed" value="${esc(pct)}" step="0.1" min="0" max="100" placeholder="10" data-pct-preview="fixed">
      <span class="sr-unit buy-unit-pct">%</span>
    </div>
    <div class="hint buy-unit-pct" data-pct-preview-label="fixed"></div>
  </div>`;
}

function fieldExitRules(s) {
  const hasAtr = _v(s, 'atr_mult_stop') !== '' || _v(s, 'atr_mult_trail') !== '';
  return `<div class="exit-stack">
    <div class="exit-note">📌 <b>레거시(거래대금·스크리너) 포지션 전용</b> 청산입니다. 상따·수급 돌파는 각 전략 카드의 매도 규칙을 씁니다. ATR을 입력하면 아래 % 방식을 대체합니다.</div>

    <div class="exit-card">
      <h5><span class="exit-num">1</span> 손절 — 손실 한도</h5>
      <div class="exit-desc">매수가 대비 <b>손실 %</b> 이하로 떨어지면 전량 매도합니다. (ATR 손절 입력 시 아래 ATR 방식으로 대체)</div>
      <div class="exit-fields">
        ${field('손절 — 손익률(%) 이하이면 매도', fNum('stop_loss_rate', s, '예: 5 (양수 입력, −5% 의미)'))}
      </div>
    </div>

    <div class="exit-card alt">
      <h5><span class="exit-num">2</span> 트레일링 스탑 — 고점 따라 수익 실현</h5>
      <div class="exit-desc">고점이 <b>시작 %</b>에 도달하면 트레일링이 켜지고 <b>익절 바닥</b>이 잠깁니다. 고점이 오를수록 트레일링선도 올라가며, 바닥 이하로는 내려가지 않습니다.</div>
      <div class="exit-fields">
        ${field('트레일링 시작 — 고점 수익률(%) 도달 후 적용 (0=즉시)', fNum('take_profit_rate', s, '예: 10'), '도달 시 즉시매도 아님 · 활성화만')}
        ${field('고점 대비 하락 % (비우면 미사용)', fNum('trailing_stop_pct', s, '예: 1.8'), 'ATR 트레일 배수를 입력하면 이 값은 사용하지 않습니다')}
      </div>
      <div class="exit-example">예: 시작 10% · 하락 3% → +10% 도달 시 바닥 잠금 · 이후 고점 대비 3% 하락 시 매도</div>
    </div>

    <div class="exit-card atr">
      <h5><span class="exit-num">3</span> ATR 변동성 — 종목별 동적 손절·트레일 (입력 시 ②·손절% 대체)</h5>
      <div class="exit-desc">
        <b>ATR</b> = 최근 일봉 기준, 하루 평균 가격 변동폭(원).<br>
        <span class="text-cyan">손절선 ≈ 매수가 − ATR×손절배수 · 트레일선 ≈ 고점 − ATR×트레일배수</span>
      </div>
      <div class="exit-fields cols-3">
        ${field('손절 배수 (비우면 ① 손절 % 사용)', fNum('atr_mult_stop', s, '예: 1.5'))}
        ${field('트레일 배수 (비우면 ② 트레일 % 사용)', fNum('atr_mult_trail', s, '예: 2'))}
        ${field('ATR 계산 기간(일)', fNum('atr_period', s, '14'))}
      </div>
      <div class="exit-example">${hasAtr ? '<span class="text-accent" style="font-weight:600;">✓ ATR 값이 설정되어 있어 손절/트레일은 변동성 기준으로 동작합니다.</span>' : '비워 두면 ①②의 % 방식만 사용합니다.'}</div>
    </div>
  </div>`;
}

function bindSizingModeToggle() {
  const sel = $('set_sizing_method');
  if (!sel) return;
  const syncRateFields = () => {
    const pyramid = $('set_signal_min_threshold');
    const fixed = $('set_signal_min_threshold_fixed');
    if (!pyramid || !fixed) return;
    if (sel.value === 'PYRAMIDING') fixed.value = pyramid.value;
    else pyramid.value = fixed.value;
  };
  const sync = () => {
    const pyramid = sel.value === 'PYRAMIDING';
    const pPanel = $('sizingPyramidPanel');
    const fPanel = $('sizingFixedPanel');
    if (pPanel) pPanel.classList.toggle('sizing-panel-hidden', !pyramid);
    if (fPanel) fPanel.classList.toggle('sizing-panel-hidden', pyramid);
    syncRateFields();
  };
  sel.onchange = sync;
  const pRate = $('set_signal_min_threshold');
  const fRate = $('set_signal_min_threshold_fixed');
  if (pRate) pRate.addEventListener('input', () => { if (sel.value === 'PYRAMIDING' && fRate) fRate.value = pRate.value; });
  if (fRate) fRate.addEventListener('input', () => { if (sel.value !== 'PYRAMIDING' && pRate) pRate.value = fRate.value; });
  sync();
}

function getAutoTradeEnabled() {
  const top = $('btnToggleAutoTop');
  if (top) return top.dataset.on === '1';
  const bottom = $('btnToggleAuto');
  return bottom ? bottom.dataset.on === '1' : false;
}

function updateAutoTradeButtons(on) {
  const label = on ? '자동매매 중지' : '자동매매 시작';
  const onStyle = 'background:var(--down);border-color:var(--down);color:#fff;';
  const offStyle = 'background:var(--green);border-color:var(--green);color:#fff;';
  const style = on ? onStyle : offStyle;
  ['btnToggleAutoTop', 'btnToggleAuto'].forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.textContent = label;
    el.dataset.on = on ? '1' : '0';
    el.style.cssText = style;
    el.classList.toggle('is-on', on);
    el.classList.toggle('is-off', !on);
  });
  const badge = $('autoStateBadge');
  if (badge) {
    badge.className = 'pill ' + (on ? 'on' : 'off');
    badge.textContent = on ? '동작 중' : '중지';
  }
}

function bindAutoTradeToggle() {
  const click = () => saveSettings(!getAutoTradeEnabled());
  const top = $('btnToggleAutoTop');
  if (top) top.onclick = click;
  const bottom = $('btnToggleAuto');
  if (bottom) bottom.onclick = click;
}

function renderSettingsForm(s) {
  const on = !!s.is_enabled;
  let h = '';

  const sangBuy = _v(s, 'sangtta_buy_amount') !== '' ? _v(s, 'sangtta_buy_amount') : '500000';
  const sangBuyPct = _v(s, 'sangtta_buy_deposit_pct') !== '' ? _v(s, 'sangtta_buy_deposit_pct') : '';
  const sangSlots = _v(s, 'sangtta_max_slots') !== '' ? _v(s, 'sangtta_max_slots') : '2';
  const sangStart = _v(s, 'sangtta_trade_start_time') || '09:05';
  const sangEnd = _v(s, 'sangtta_trade_end_time') || '11:00';
  const sangChgMin = _v(s, 'sangtta_change_min') !== '' ? _v(s, 'sangtta_change_min') : '12';
  const sangChgMax = _v(s, 'sangtta_change_max') !== '' ? _v(s, 'sangtta_change_max') : '15';
  const breakoutBuy = _v(s, 'breakout_buy_amount') !== '' ? _v(s, 'breakout_buy_amount') : '1000000';
  const breakoutBuyPct = _v(s, 'breakout_buy_deposit_pct') !== '' ? _v(s, 'breakout_buy_deposit_pct') : '';
  const breakoutSlots = _v(s, 'breakout_max_slots') !== '' ? _v(s, 'breakout_max_slots') : '1';
  const breakoutStart = _v(s, 'breakout_trade_start_time') || '11:00';
  if (s.buy_amount_unit == null || s.buy_amount_unit === '') s.buy_amount_unit = 'WON';
  if (s.breakout_entry_hard == null) s.breakout_entry_hard = true;
  if (s.breakout_entry_soft == null) s.breakout_entry_soft = true;
  if (s.breakout_entry_soft_polls == null || s.breakout_entry_soft_polls === '') s.breakout_entry_soft_polls = 3;
  if (s.breakout_entry_hold == null) s.breakout_entry_hold = true;
  if (s.breakout_hold_expire_bars == null || s.breakout_hold_expire_bars === '') s.breakout_hold_expire_bars = 3;
  if (s.breakout_hold_rsi_min == null || s.breakout_hold_rsi_min === '') s.breakout_hold_rsi_min = 30;
  if (s.breakout_rsi_period == null || s.breakout_rsi_period === '') s.breakout_rsi_period = 10;
  if (s.breakout_body_pct == null || s.breakout_body_pct === '') s.breakout_body_pct = 2;
  if (s.breakout_range_mult == null || s.breakout_range_mult === '') s.breakout_range_mult = 0;
  if (s.breakout_require_ma20_cross == null) s.breakout_require_ma20_cross = true;
  if (!s.breakout_ma20_mode) s.breakout_ma20_mode = 'above';
  if (s.breakout_ma20_grace_bars == null || s.breakout_ma20_grace_bars === '') s.breakout_ma20_grace_bars = 3;
  const breakoutEnd = _v(s, 'breakout_trade_end_time') || '14:30';
  const ymgpBuy1 = _v(s, 'ymgp_buy_amount_1') !== '' ? _v(s, 'ymgp_buy_amount_1') : '500000';
  const ymgpBuy2 = _v(s, 'ymgp_buy_amount_2') !== '' ? _v(s, 'ymgp_buy_amount_2') : '500000';
  const ymgpBuyPct1 = _v(s, 'ymgp_buy_deposit_pct_1') !== '' ? _v(s, 'ymgp_buy_deposit_pct_1') : '';
  const ymgpBuyPct2 = _v(s, 'ymgp_buy_deposit_pct_2') !== '' ? _v(s, 'ymgp_buy_deposit_pct_2') : '';
  const ymgpSlots = _v(s, 'ymgp_max_slots') !== '' ? _v(s, 'ymgp_max_slots') : '1';
  const ymgpStart = _v(s, 'ymgp_trade_start_time') || '09:30';
  const ymgpEnd = _v(s, 'ymgp_trade_end_time') || '14:30';
  const jonggaBuy = _v(s, 'jongga_buy_amount') !== '' ? _v(s, 'jongga_buy_amount') : '1000000';
  const jonggaBuyPct = _v(s, 'jongga_buy_deposit_pct') !== '' ? _v(s, 'jongga_buy_deposit_pct') : '';
  const jonggaSlots = _v(s, 'jongga_max_slots') !== '' ? _v(s, 'jongga_max_slots') : '1';
  const jonggaStart = _v(s, 'jongga_trade_start_time') || '14:30';
  const jonggaPickEnd = _v(s, 'jongga_pick_end_time') || '14:40';
  const jonggaEnd = _v(s, 'jongga_trade_end_time') || jonggaPickEnd;
  const screenerLimit = _v(s, 'screener_candidate_limit') || '50';
  const screenerMinChg = _v(s, 'screener_min_change_rate') || '3.3';
  const screenerMaxChg = _v(s, 'screener_max_change_rate') || '15';
  const softPolls = Math.max(1, parseInt(_v(s, 'soft_confirm_polls') || '3', 10) || 3);
  const softHint = `SOFT ${softPolls}회 연속 확인 후 매도`;
  const hardHint = 'HARD 1회(즉시) 매도 · 확인 대기 없음';
  const engineDetail = linkedSessionWindowDetail(s);
  const engineBreak = engineDetail.parts.map((p) => `${p.label} ${p.start}~${p.end}`).join(' · ');

  // ===== 공통 (포트폴리오) =====
  h += `<div class="form-section strategy-card strategy-common">
    <h4>공통 · 포트폴리오</h4>
    <div class="desc">전략과 무관한 계좌·한도입니다. 매매 시작/종료는 <b>각 전략 카드에서만</b> 설정합니다. 아래는 합친 결과 표시입니다.</div>
    <div class="box-soft">
      <div class="box-title">매매시간 (표시 전용 · 수정 불가)</div>
      <div class="engine-session-readonly" id="engineSessionPanel">
        <div class="engine-session-pair">
          <span class="engine-session-label">매매시작시간</span>
          <strong class="engine-session-value" id="engineSessionStart">${esc(engineDetail.start)}</strong>
        </div>
        <div class="engine-session-pair">
          <span class="engine-session-label">매매종료시간</span>
          <strong class="engine-session-value" id="engineSessionEnd">${esc(engineDetail.end)}</strong>
        </div>
        <span class="fhint" id="engineSessionBreakdown">${esc(engineBreak)}</span>
      </div>
      <div class="form-grid" style="margin-top:12px;">
        ${field('매수금액 기준', fSelect('buy_amount_unit', s, [['WON', '고정 금액(원)'], ['DEPOSIT_PCT', '예수금 비중(%)']]), '예: 예수금 3천만 × 10% = 300만')}
        ${field('예수금 현금 보유율(%)', fNum('cash_reserve_pct', s, '10'), '0=전액 사용')}
        ${field('최대 동시 보유 종목 (0=자동)', fNum('max_concurrent_positions', s, '0'))}
        ${field('1일 최대 매수 횟수', fNum('max_daily_buys', s, '20'))}
        ${field('1일 손실 한도(원)', fNum('daily_loss_limit', s, '-500000'))}
        ${field('1일 이익목표(원)', fNum('daily_profit_target', s, '150000'))}
        ${field('재주문 쿨다운(초)', fNum('reorder_cooldown_sec', s, '300'))}
        ${field('매매 주기(초)', fNum('scan_interval_sec', s, '60'), '스캐너·매수 폴링 · 15~600 · 기본 60')}
        ${field('매수 주문 방식', fSelect('order_method', s, [['MARKET', '시장가 (권장)'], ['LIMIT', '지정가 (현재가)']]))}
        ${field('SOFT 연속 확인 횟수', fNum('soft_confirm_polls', s, '3'), `SOFT=${softPolls}회 · HARD=1회(즉시) · 상따·돌파 공통`)}
      </div>
      <div class="desc" id="buyAmountUnitHint" style="margin-top:8px;"></div>
      <div class="box-title" style="margin-top:14px;">장세 악화 시 매수 제한</div>
      <div class="desc">예: 코스피 ≤ -2% 이면 체크한 전략은 <b>금일 신규매수 N회</b>까지만. 보유 청산·추가매수는 그대로입니다. N=0이면 전면 차단.</div>
      ${fCheck('market_risk_enabled', s, '장세 게이트 사용')}
      <div class="form-grid" style="margin-top:8px;">
        ${field('판정 지수', fSelect('market_risk_index', s, [
          ['kospi', '코스피만'],
          ['kosdaq', '코스닥만'],
          ['either', '코스피 또는 코스닥 (하나라도)'],
          ['both', '코스피·코스닥 둘 다'],
        ]))}
        ${field('나쁜 기준 등락(%)', fNum('market_risk_change_pct', s, '-2.0'), '예: -2 = 2% 이상 하락 시 나쁨')}
        ${field('전략당 금일 매수 한도', fNum('market_risk_max_buys_per_strategy', s, '2'), '나쁠 때 전략별 신규매수 상한 · 0=전면차단')}
      </div>
      <div style="margin-top:8px;">
        ${fCheck('market_risk_block_legacy', s, '나쁠 때 레거시에 한도 적용')}
        ${fCheck('market_risk_block_sangtta', s, '나쁠 때 상따에 한도 적용')}
        ${fCheck('market_risk_block_breakout', s, '나쁠 때 돌파에 한도 적용')}
        ${fCheck('market_risk_block_ymgp', s, '나쁠 때 역매공파에 한도 적용')}
        ${fCheck('market_risk_block_jongga', s, '나쁠 때 종가배팅에 한도 적용')}
      </div>
      <div class="box-title" style="margin-top:12px;">장마감 전량 청산</div>
      ${fCheck('liquidate_before_close', s, '장 마감 전 전량 청산 (오버나잇 방지)')}
      <div class="desc">지정 시각 이후 레거시·상따 보유를 시장가 정리합니다. 수급 돌파는 적용하지 않습니다.</div>
      ${field('청산 시작 시각', fTime('liquidate_time', s))}
    </div>
  </div>`;

  // ===== 레거시 =====
  h += `<div class="form-section strategy-card strategy-legacy">
    <h4>레거시 · 거래대금 / 스크리너</h4>
    <div class="desc">거래대금 상위 후보의 <b>매수·진입·청산</b>입니다. 상따·돌파와 규칙을 공유하지 않습니다.</div>
    <div class="box-soft screener-policy">
      <div class="box-title">매수 시간</div>
      <div class="desc">레거시 신규매수에만 적용됩니다. (공통란 매매시간은 전략별 시간을 합친 표시값)</div>
      <div class="form-grid">
        ${field('매수 시작', fTime('trade_start_time', s))}
        ${field('매수 종료', fTime('trade_end_time', s))}
      </div>

      <div class="box-title" style="margin-top:14px;">종목 선정</div>
      <ul class="policy-list">
        <li><span class="policy-tag on">포함</span> 거래대금 상위 <b>${esc(screenerLimit)}</b> · 등락 <b>${esc(screenerMinChg)}</b>~&lt;<b>${esc(screenerMaxChg)}</b>% · KRX · 당일 20만주 이상</li>
        <li><span class="policy-tag off">제외</span> ETF · ETN · 레버리지 · 인버스 · 곱버스 · SPAC · 우선주 · 등락 과열(≥${esc(screenerMaxChg)}%)</li>
      </ul>

      <div class="box-title" style="margin-top:14px;">진입 타이밍</div>
      <div class="desc">스크리너 통과 후 아래를 만족할 때만 신규 진입. (데이터 없으면 통과)</div>
      ${fCheck('use_entry_gate', s, '진입 타이밍 게이트 사용')}
      ${fCheck('require_above_open', s, '현재가 ≥ 시가 (당일 양봉만)')}
      ${fCheck('require_above_vwap', s, '현재가 ≥ 장중 VWAP')}
      <div class="form-grid" style="margin-top:10px;">
        ${field('당일 위치 하한 (0~1)', fNum('day_position_min', s, '0.5'))}
        ${field('당일 위치 상한 (0~1)', fNum('day_position_max', s, '비우면 미적용'))}
        ${field('전일대비 거래량비율(%) 하한', fNum('volume_ratio_min', s, '비우면 미적용'))}
        ${field('일봉 RSI(14) 하한', fNum('legacy_rsi_min', s, '비우면 미적용'), '미만이면 매수 안 함')}
        ${field('일봉 RSI(14) 상한', fNum('legacy_rsi_max', s, '75'), '초과면 매수 안 함 (예: 75)')}
      </div>

      <div class="box-title" style="margin-top:14px;">매수 사이징</div>
      <div class="desc">등락률·금액으로 얼마 살지 정합니다. 상따·돌파 금액과는 별개입니다.</div>
      ${field('방식', fSelect('sizing_method', s, [['PYRAMIDING', '역피라미딩 (등락↑ 금액↓)'], ['FIXED', '고정 금액']]))}
      ${fieldSizingPyramiding(s)}
      ${fieldSizingFixed(s)}
      <input type="hidden" id="set_max_invest_amount" value="${esc(_v(s, 'max_invest_amount') || s.initial_max_amount || 5000000)}">

      <div class="box-title" style="margin-top:14px;">레거시 청산 (매도)</div>
      ${fieldExitRules(s)}
    </div>
  </div>`;

  // ===== 상따 =====
  h += `<div class="form-section strategy-card strategy-sangtta">
    <h4>상따</h4>
    <div class="desc">등락률상위(ka10027) 풀 → <b>거래대금순 상위 후보</b> · 소액 매수 · <b>상한가 이탈 / 급락</b> 청산. 레거시 손절·트레일과 별개입니다.</div>
    <div class="box-soft screener-policy">
      <div class="box-title">종목 선정 · 매수</div>
      <ul class="policy-list">
        <li><span class="policy-tag on">포함</span> 전일대비등락률상위(ka10027) · 등락 ≥13%</li>
        <li><span class="policy-tag on">포함</span> 현재가 1천원↑ · 당일 거래대금 10억↑ · KRX</li>
        <li><span class="policy-tag on">제한</span> 위 풀에서 <b>거래대금순 상위 ${esc(_v(s, 'sangtta_candidate_limit') || 20)}</b>만 스캔</li>
        <li><span class="policy-tag on">관찰</span> 게이트 등락 ${esc(sangChgMin)}~${esc(sangChgMax)}% · 시총≤3000억</li>
        <li><span class="policy-tag off">제외</span> 관리종목 · ETF·ETN·파생 · 상한가 도달</li>
      </ul>
      <div class="form-grid" style="margin-top:12px;">
        ${field('1회 매수 금액(원)', `<input type="number" class="buy-unit-won" id="set_sangtta_buy_amount" value="${esc(sangBuy)}" step="10000" min="0" placeholder="500000">`, '기본 50만원')}
        ${field('1회 매수 비중(%)', `<input type="number" class="buy-unit-pct" id="set_sangtta_buy_deposit_pct" value="${esc(sangBuyPct)}" step="0.1" min="0" max="100" placeholder="10" data-pct-preview="sangtta">`, '예수금 대비')}
        ${field('동시 보유 슬롯', `<input type="number" id="set_sangtta_max_slots" value="${esc(sangSlots)}" step="1" min="0" placeholder="2">`)}
        ${field('매수 시작', `<input type="time" id="set_sangtta_trade_start_time" value="${esc(sangStart)}">`)}
        ${field('매수 종료', `<input type="time" id="set_sangtta_trade_end_time" value="${esc(sangEnd)}">`)}
        ${field('진입 등락 하한(%)', `<input type="number" id="set_sangtta_change_min" value="${esc(sangChgMin)}" step="0.5" min="0" max="30" placeholder="12">`, '전일대비 최소 등락')}
        ${field('진입 등락 상한(%)', `<input type="number" id="set_sangtta_change_max" value="${esc(sangChgMax)}" step="0.5" min="0" max="30" placeholder="15">`, '전일대비 최대 등락 · 상한가 미도달')}
      </div>
      <div class="hint buy-unit-pct" data-pct-preview-label="sangtta" style="margin-top:4px;"></div>

      <div class="box-title" style="margin-top:14px;">상따 청산 (매도)</div>
      <div class="desc">우선순위: 상한가 이탈 HARD/SOFT → 급락 HARD/SOFT. · ${esc(softHint)} · ${esc(hardHint)}</div>
      <div class="exit-stack">
        <div class="exit-card">
          <h5><span class="exit-num">1</span> 상한가 이탈</h5>
          <div class="exit-desc">상한가 터치 이후, 상한가 대비 하락 %로 이탈을 판정합니다.</div>
          <div class="exit-fields">
            ${field('이탈 SOFT (%)', fNum('limit_break_soft_pct', s, '2'), softHint)}
            ${field('이탈 HARD (%)', fNum('limit_break_hard_pct', s, '3'), hardHint)}
          </div>
        </div>
        <div class="exit-card alt">
          <h5><span class="exit-num">2</span> 급락 (고점 대비)</h5>
          <div class="exit-desc">진입 후 고점 대비 하락 %입니다.</div>
          <div class="exit-fields">
            ${field('급락 SOFT (%)', fNum('sharp_drop_soft_pct', s, '3'), softHint)}
            ${field('급락 HARD (%)', fNum('sharp_drop_hard_pct', s, '5'), hardHint)}
          </div>
        </div>
      </div>
      <div class="exit-note">등락 ${esc(sangChgMin)}~${esc(sangChgMax)}% · 시간창 ${esc(sangStart)}~${esc(sangEnd)} · 1회 ${esc(sangBuy)}원 · 슬롯 ${esc(sangSlots)}</div>
    </div>
  </div>`;

  // ===== 수급 돌파 =====
  h += `<div class="form-section strategy-card strategy-breakout">
    <h4>수급 돌파</h4>
    <div class="desc">조건식 유니버스(5분 RSI 전환·완화) · 5분봉 <b>장대+거래량+MA20</b> 돌파 진입(MA20은 돌파봉 포함 N봉 유예 가능) · <b>구조 이탈 → 고정손절 → 트레일</b>. 장마감 강제청산 제외 · 분봉은 통합(_AL).</div>
    <div class="box-soft screener-policy">
      ${fCheck('use_breakout', s, '수급 돌파 전략 사용')}
      <div class="box-title" style="margin-top:8px;">종목 선정 · 매수</div>
      <div class="desc">유니버스는 조건식(예: 5분 RSI 35 상향 전환). “극단 과매도”가 아니라 <b>RSI 회복·전환 후보</b>를 모읍니다. 실제 매수는 서버 돌파 게이트.</div>
      <div id="breakoutCondPicker" class="cond-picker"><div class="skeleton">조건식 목록 불러오는 중...</div></div>
      <input type="hidden" id="set_breakout_condition_names" value="${esc(_v(s, 'breakout_condition_names'))}">
      <div class="form-grid" style="margin-top:12px;">
        ${field('1회 매수 금액(원)', `<input type="number" class="buy-unit-won" id="set_breakout_buy_amount" value="${esc(breakoutBuy)}" step="10000" min="0">`)}
        ${field('1회 매수 비중(%)', `<input type="number" class="buy-unit-pct" id="set_breakout_buy_deposit_pct" value="${esc(breakoutBuyPct)}" step="0.1" min="0" max="100" placeholder="10" data-pct-preview="breakout">`)}
        ${field('동시 보유 슬롯', `<input type="number" id="set_breakout_max_slots" value="${esc(breakoutSlots)}" step="1" min="1">`)}
        ${field('매수 시작', `<input type="time" id="set_breakout_trade_start_time" value="${esc(breakoutStart)}">`)}
        ${field('매수 종료', `<input type="time" id="set_breakout_trade_end_time" value="${esc(breakoutEnd)}">`)}
      </div>
      <div class="hint buy-unit-pct" data-pct-preview-label="breakout" style="margin-top:4px;"></div>

      <div class="box-title" style="margin-top:14px;">진입 게이트</div>
      <div class="form-grid">
        ${field('돌파 레벨', fSelect('breakout_level_mode', s, [['prev_high', '직전 5분봉 고가'], ['n_day_high', '최근 N봉 고가(5분)']]))}
        <div id="breakoutNDayField">${field('N봉 고가 기간', fNum('breakout_n_day', s, '10'), '5분봉 N개 (예: 10=50분)')}</div>
        ${field('분봉 거래량 배수', fNum('breakout_vol_mult', s, '1.5'), '확인봉 ÷ 직전 N봉 평균 (파세코형 권장 2.5~3)')}
        ${field('장대 몸통(%)', fNum('breakout_body_pct', s, '2'), '확인봉 (종가/시가-1)×100 · 0=비활성')}
        ${field('범위 확장 배수', fNum('breakout_range_mult', s, '0'), '고저÷직전12봉 평균 · 0=비활성')}
        ${fCheck('breakout_require_ma20_cross', s, 'MA20 필터 필수', '끄면 MA20·유예 모두 비활성 · 켤 때만 아래 판정/유예 적용')}
        ${field('MA20 판정', fSelect('breakout_ma20_mode', s, [['above', '상회(종가>MA20)'], ['cross', '상향돌파(아래에서 뚫기)']]), '갭 장초는 상회 권장 · 유예창에도 동일 판정')}
        ${field('MA20 유예(봉)', fNum('breakout_ma20_grace_bars', s, '3'), '돌파봉 포함 N봉(5분). 3=돌파+후속2 · 대기 시 WATCHING(슬롯 미점유·유니버스 이탈무관) → 통과 시 PENDING · 장대·거래량 돌파봉 상속')}
        ${field('과열 컷 등락률(%)', fNum('breakout_max_change_pct', s, '12'), '이 이상이면 매수 금지')}
      </div>
      <div class="desc" style="margin-top:6px;">MA20 유예: 레벨 돌파 직후 종가가 아직 MA20 아래여도 설정 봉 수 안에 상회하면 매수. 대기 중에는 <b>WATCHING</b>으로 추적하고, 통과 시 <b>PENDING</b>으로 승격합니다.</div>
      <div class="box-title" style="margin-top:10px;">진입 확인</div>
      <div class="desc">레벨 위 + 거래량 통과 후 HARD 또는 SOFT 중 하나면 매수. 둘 다 끄면 터치(기존) 진입.</div>
      <div class="form-grid">
        ${fCheck('breakout_entry_hard', s, 'HARD — 직전 완성봉 고가/종가 > 레벨 시 즉시', '확인봉 기준(레벨은 확인봉 이전)')}
        ${fCheck('breakout_entry_soft', s, 'SOFT — 레벨 위 연속 확인', '스캔 횟수 또는 5분봉 연속')}
        ${field('SOFT 필요 횟수', fNum('breakout_entry_soft_polls', s, '3'), '스캐너 1분 간격 N회 연속 유지(또는 레벨 위 완성봉 N개)')}
        ${fCheck('breakout_entry_hold', s, 'HOLD — 전봉 RSI교차 + 현재봉 양봉', '되돌림 허용 · 다음봉 유지')}
        ${field('HOLD 만료(봉)', fNum('breakout_hold_expire_bars', s, '3'), '다음봉 확인 전 N봉 지나면 해제')}
        ${field('HOLD RSI 임계', fNum('breakout_hold_rsi_min', s, '30'), '전봉에서 직전≤임계 < 전봉, 현재봉은 임계 위 유지')}
        ${field('RSI 기간', fNum('breakout_rsi_period', s, '10'), '5분봉 RSI 기간 (조건식과 맞춤)')}
      </div>

      <div class="box-title" style="margin-top:14px;">돌파 청산 (매도)</div>
      <div class="desc">우선순위: <b>구조 이탈</b> → <b>고정손절(매수가 −%)</b> → <b>트레일(고점 −%, +시작% 이후만)</b>. · ${esc(softHint)} · ${esc(hardHint)}</div>
      <div class="exit-stack">
        <div class="exit-card">
          <h5><span class="exit-num">1</span> 구조 이탈</h5>
          <div class="exit-desc">돌파 레벨(또는 돌파봉 저가) 기준. 테제가 깨지면 고정손절·트레일보다 먼저 청산.</div>
          <div class="exit-fields">
            ${field('구조 SOFT (%)', fNum('struct_break_soft_pct', s, '1'), softHint)}
            ${field('구조 HARD (%)', fNum('struct_break_hard_pct', s, '2'), hardHint)}
          </div>
        </div>
        <div class="exit-card alt">
          <h5><span class="exit-num">2</span> 고정 손절</h5>
          <div class="exit-desc">매수가 기준. 진입 직후부터 적용. 예: 3%면 매수가×0.97 이하 시 손절. <b>고점과 무관</b>.</div>
          <div class="exit-fields">
            ${field('고정 손절(%)', fNum('breakout_stop_loss_pct', s, '3'), '매수가 대비 하락폭')}
          </div>
        </div>
        <div class="exit-card">
          <h5><span class="exit-num">3</span> 트레일링 (익절 보호)</h5>
          <div class="exit-desc">고점 수익률이 <b>시작%</b>에 도달한 뒤에만 켜짐. 이후 <b>고점 − 하락폭%</b>에서 청산. 시작%를 못 찍으면 트레일은 동작하지 않고 ② 고정손절만 유효.</div>
          <div class="exit-fields">
            ${field('트레일 시작 — 고점 수익률(%)', fNum('breakout_trailing_start_pct', s, '10'), '이 % 도달 시 트레일 ON (즉시 전량매도 아님)')}
            ${field('트레일 폭 — 고점 대비 하락(%)', fNum('breakout_trailing_pct', s, '4'), 'armed 후 고점×(1−이%) 이탈 시 청산')}
          </div>
        </div>
      </div>
      <div class="exit-note">우선순위: 상따 &gt; 수급 돌파 &gt; 레거시 · ${esc(breakoutStart)}~${esc(breakoutEnd)} · ${esc(breakoutBuy)}원 · 슬롯 ${esc(breakoutSlots)}</div>
    </div>
  </div>`;

  // ===== 역매공파 =====
  h += `<div class="form-section strategy-card strategy-ymgp">
    <h4>역매공파</h4>
    <div class="desc">역배열 → 바닥(박스/지지) → 매집봉·공구리 → 기준봉 고점 돌파(1차) · 눌림 추가(2차) · 기준선 손절 · 저항/224/448 분할익절. 수급 돌파와 별도 전략.</div>
    <div class="box-soft screener-policy">
      ${fCheck('use_ymgp', s, '역매공파 전략 사용')}
      <div class="box-title" style="margin-top:8px;">종목 선정 · 매수</div>
      <div class="desc">역배열·바닥·매집 단계는 일봉 엔진이 자체 판정합니다. 조건식은 유니버스(관찰 대상)만 좁힙니다.</div>
      <div id="ymgpCondPicker" class="cond-picker"><div class="skeleton">조건식 목록 불러오는 중...</div></div>
      <input type="hidden" id="set_ymgp_condition_names" value="${esc(_v(s, 'ymgp_condition_names'))}">
      <div class="form-grid" style="margin-top:12px;">
        ${field('1차 매수 금액(원)', `<input type="number" class="buy-unit-won" id="set_ymgp_buy_amount_1" value="${esc(ymgpBuy1)}" step="10000" min="0" placeholder="500000">`, '기준봉 고점 돌파 시')}
        ${field('1차 매수 비중(%)', `<input type="number" class="buy-unit-pct" id="set_ymgp_buy_deposit_pct_1" value="${esc(ymgpBuyPct1)}" step="0.1" min="0" max="100" placeholder="10" data-pct-preview="ymgp1">`, '예수금 대비')}
        ${field('2차 매수 금액(원)', `<input type="number" class="buy-unit-won" id="set_ymgp_buy_amount_2" value="${esc(ymgpBuy2)}" step="10000" min="0" placeholder="500000">`, '눌림 추가 시')}
        ${field('2차 매수 비중(%)', `<input type="number" class="buy-unit-pct" id="set_ymgp_buy_deposit_pct_2" value="${esc(ymgpBuyPct2)}" step="0.1" min="0" max="100" placeholder="10" data-pct-preview="ymgp2">`, '예수금 대비')}
        ${field('동시 보유 슬롯', `<input type="number" id="set_ymgp_max_slots" value="${esc(ymgpSlots)}" step="1" min="1">`)}
        ${field('매수 시작', `<input type="time" id="set_ymgp_trade_start_time" value="${esc(ymgpStart)}">`)}
        ${field('매수 종료', `<input type="time" id="set_ymgp_trade_end_time" value="${esc(ymgpEnd)}">`)}
      </div>
      <div class="hint buy-unit-pct" data-pct-preview-label="ymgp1" style="margin-top:4px;"></div>
      <div class="hint buy-unit-pct" data-pct-preview-label="ymgp2" style="margin-top:2px;"></div>

      <div class="box-title" style="margin-top:14px;">일봉 단계 판정 (이동평균)</div>
      <div class="desc">역배열 판정·지지 근접에 쓰는 이동평균 기간(일)입니다.</div>
      <div class="form-grid">
        ${field('단기 MA', fNum('ymgp_ma_fast', s, '120'))}
        ${field('중기 MA', fNum('ymgp_ma_mid', s, '240'))}
        ${field('장기 MA', fNum('ymgp_ma_slow', s, '480'))}
      </div>

      <div class="box-title" style="margin-top:14px;">바닥(박스·지지) 판정</div>
      <div class="form-grid">
        ${field('박스 기간(일)', fNum('ymgp_box_days', s, '15'))}
        ${field('박스 폭 상한(%)', fNum('ymgp_box_width_pct', s, '15.5'), '(고-저)÷중간가 · 최근 박스일수')}
        ${field('매집봉 몸통(%)', fNum('ymgp_accum_body_pct', s, '7.0'), '장대 양봉 · (종가-시가)/시가')}
        ${field('매집봉 거래량 배수', fNum('ymgp_accum_vol_mult', s, '2.0'), '양봉 경로 · 직전 20일 평균 대비')}
        ${field('윗꼬리 매집 거래량', fNum('ymgp_accum_wick_vol_mult', s, '4.0'), '장대 윗꼬리 경로 · 양봉 배수보다 크게')}
        ${field('윗꼬리/몸통 배수', fNum('ymgp_accum_wick_body_mult', s, '1.5'), '윗꼬리 ≥ 몸통×이 값')}
        ${field('MA 근접 허용(%)', fNum('ymgp_ma_near_pct', s, '3.0'), '60/112일선 지지 판정')}
        ${field('이중저점 허용오차(%)', fNum('ymgp_pivot_tol_pct', s, '2.0'))}
      </div>

      <div class="box-title" style="margin-top:14px;">급락 이력 (역배열 보조 판정)</div>
      <div class="form-grid">
        ${field('급락 조회기간(봉)', fNum('ymgp_drop_lookback', s, '60'))}
        ${field('급락 기준(%)', fNum('ymgp_drop_pct', s, '-20'), '음수 · 이 이상 급락 후 횡보')}
      </div>

      <div class="box-title" style="margin-top:14px;">진입 (1차/2차)</div>
      <div class="form-grid">
        ${field('1차 진입 기준', fSelect('ymgp_entry_mode', s, [['ref_high', '기준봉 고점 돌파'], ['prev_high', '직전봉 고가 돌파'], ['either', '둘 중 하나']]))}
        ${field('과열 컷 등락률(%)', fNum('ymgp_max_change_pct', s, '10'), '이 이상이면 매수 금지')}
        ${field('2차 눌림 허용오차(%)', fNum('ymgp_pullback_tol_pct', s, '2.0'), '기준봉 시가/저점 근접')}
      </div>

      <div class="box-title" style="margin-top:14px;">역매공파 청산 (매도)</div>
      <div class="desc">손절 → 분할익절 → 트레일 순. 분할익절은 <b>목표가 도달 시</b> 비중만큼 매도합니다.</div>
      <div class="form-grid">
        ${field('기준선 손절 MA', fSelect('ymgp_stop_ma_mode', s, [['ma60', '60일선'], ['ma112', '112일선'], ['either', '둘 중 하나']]), '종가가 해당 이평 아래면 전량 손절')}
        ${field('고정 손절(%)', fNum('ymgp_stop_loss_pct', s, '4'), '매수가 대비 백업 손절 (기준봉 저점 이탈도 손절)')}
        ${field('재진입 락(일)', fNum('ymgp_reentry_lock_days', s, '5'), '손절 후 동일 종목 재진입 금지 기간')}
      </div>
      ${fCheck('ymgp_enable_pullback_add', s, '눌림 추가매수(2차) 사용')}
      ${fCheck('ymgp_enable_partial_tp', s, '분할 익절 사용')}
      <div class="exit-stack" style="margin-top:10px;">
        <div class="exit-card">
          <h5><span class="exit-num">T1</span> 박스 고점 (최근 저항)</h5>
          <div class="desc">목표가 = 최근 박스권(<code>박스 일수</code>)의 <b>최고가</b>. 현재가 ≥ 목표가 시 아래 비중 매도.</div>
          <div class="exit-fields">
            ${field('T1 청산 비중', fNum('ymgp_tp1_pct_of_pos', s, '0.35'), '보유수량 × 비중 (0~1, 기본 35%)')}
          </div>
        </div>
        <div class="exit-card">
          <h5><span class="exit-num">T2</span> MA224</h5>
          <div class="desc">목표가 = <b>224일 이동평균</b>. T1 이후 현재가 ≥ MA224 시 비중 매도.</div>
          <div class="exit-fields">
            ${field('T2 청산 비중', fNum('ymgp_tp2_pct_of_pos', s, '0.35'), '남은 보유수량 × 비중 (기본 35%)')}
          </div>
        </div>
        <div class="exit-card alt">
          <h5><span class="exit-num">T3</span> MA448 · 잔량</h5>
          <div class="desc">목표가 = <b>448일 이동평균</b>. 도달 시 <b>잔량 전량</b> 매도. (비중 설정 없음)</div>
        </div>
        <div class="exit-card alt">
          <h5><span class="exit-num">T4</span> 트레일 (선택)</h5>
          <div class="desc">고점 수익률이 시작%에 도달하면 트레일 무장. 이후 고점 대비 하락폭%면 잔량 청산.</div>
          <div class="exit-fields">
            ${field('트레일링 시작 수익률(%)', fNum('ymgp_trailing_start_pct', s, '15'))}
            ${field('활성 후 고점 하락폭(%)', fNum('ymgp_trailing_pct', s, '5'))}
          </div>
        </div>
      </div>
      <div class="exit-note">분할익절 OFF면 T1~T3 생략 · 손절·트레일만 동작 · ${esc(ymgpStart)}~${esc(ymgpEnd)} · 1차 ${esc(ymgpBuy1)}원 · 2차 ${esc(ymgpBuy2)}원 · 슬롯 ${esc(ymgpSlots)}</div>
    </div>
  </div>`;

  // ===== 종가배팅 =====
  h += `<div class="form-section strategy-card strategy-jongga">
    <h4>종가배팅</h4>
    <div class="desc">장 마감 전 거래대금순 → 테마 매핑 → <b>당일 최강 테마</b> 1종. 돼지물량 반응형(20/30/50) 분할매수 · 청산은 익일 고정손절+트레일.</div>
    <div class="box-soft screener-policy">
      ${fCheck('use_jongga', s, '종가배팅 전략 사용')}
      <div class="box-title" style="margin-top:8px;">종목 선정 · 매수</div>
      <div class="form-grid" style="margin-top:12px;">
        ${field('총 매수 금액(원)', `<input type="number" class="buy-unit-won" id="set_jongga_buy_amount" value="${esc(jonggaBuy)}" step="10000" min="0" placeholder="1000000">`, '분할 비중 합산 기준')}
        ${field('총 매수 비중(%)', `<input type="number" class="buy-unit-pct" id="set_jongga_buy_deposit_pct" value="${esc(jonggaBuyPct)}" step="0.1" min="0" max="100" placeholder="10" data-pct-preview="jongga">`, '예수금 대비')}
        ${field('동시 보유 슬롯', `<input type="number" id="set_jongga_max_slots" value="${esc(jonggaSlots)}" step="1" min="1" max="1">`, '당일 1종 권장')}
        ${field('선택 시작', `<input type="time" id="set_jongga_trade_start_time" value="${esc(jonggaStart)}">`)}
        ${field('선택 종료(자동)', `<input type="time" id="set_jongga_pick_end_time" value="${esc(jonggaPickEnd)}">`)}
        ${field('매수 종료(표시)', `<input type="time" id="set_jongga_trade_end_time" value="${esc(jonggaEnd)}">`, '분할 ON이면 3차 종료까지 실제 매수')}
        ${field('거래대금순 상위 N', fNum('jongga_rank_limit', s, '50'))}
      </div>
      <div class="hint buy-unit-pct" data-pct-preview-label="jongga" style="margin-top:4px;"></div>

      <div class="box-title" style="margin-top:14px;">돼지물량 반응형 분할</div>
      <div class="desc">1차 씨드 → 2차(14:50+ 저점지지·프로그램 순매수) → 3차(동시호가 매수벽만). OFF면 1회 전량.</div>
      ${fCheck('jongga_pig_split', { ...s, jongga_pig_split: s.jongga_pig_split !== false }, '돼지물량 분할매수 사용')}
      <div class="form-grid" style="margin-top:8px;">
        ${field('1차 비중(%)', fNum('jongga_leg1_pct', s, '20'), '씨드')}
        ${field('2차 비중(%)', fNum('jongga_leg2_pct', s, '30'), '14:50+')}
        ${field('3차 비중(%)', fNum('jongga_leg3_pct', s, '50'), '동시호가')}
        ${field('2차 시작', `<input type="time" id="set_jongga_leg2_start_time" value="${esc(_v(s,'jongga_leg2_start_time')||'14:50')}">`)}
        ${field('3차 시작', `<input type="time" id="set_jongga_leg3_start_time" value="${esc(_v(s,'jongga_leg3_start_time')||'15:20')}">`)}
        ${field('3차 종료', `<input type="time" id="set_jongga_leg3_end_time" value="${esc(_v(s,'jongga_leg3_end_time')||'15:28')}">`)}
        ${field('돼지 잔량비', fNum('jongga_pig_bid_ask_ratio', s, '1.5'), '매수잔량/매도잔량 ≥')}
        ${field('호가 단계 수', fNum('jongga_pig_levels', s, '5'), '상위 N호가 합')}
      </div>

      <div class="box-title" style="margin-top:14px;">자동선택 가중치</div>
      <div class="desc">미선택 시 스코어 = 눌림(고가대비↓) · 거래대금 · 등락률 min-max 가중합.</div>
      <div class="form-grid">
        ${field('눌림 가중', fNum('jongga_w_pullback', s, '1.0'))}
        ${field('대금 가중', fNum('jongga_w_amount', s, '1.0'))}
        ${field('등락 가중', fNum('jongga_w_change', s, '1.0'))}
      </div>

      <div class="box-title" style="margin-top:14px;">익일 청산</div>
      <div class="desc">매수 당일은 손절/트레일 미적용 · 장마감 강제청산 제외(오버나잇). 익일부터 아래 규칙.</div>
      <div class="form-grid">
        ${field('고정 손절(%)', fNum('jongga_stop_loss_pct', s, '3'))}
        ${field('트레일 시작 수익률(%)', fNum('jongga_trailing_start_pct', s, '5'))}
        ${field('트레일 폭(%)', fNum('jongga_trailing_pct', s, '2'), '고점 대비 하락')}
      </div>
      <div class="exit-note">${esc(jonggaStart)}~${esc(jonggaPickEnd)} 선택 · 총 ${esc(jonggaBuy)}원 · 슬롯 ${esc(jonggaSlots)} · 분할 20/30/50</div>
    </div>
  </div>`;

  // ===== 휴장일 · 저장 =====
  h += `<div class="form-section" id="holidaySection">
    <h4>거래소 휴장일 (KRX)</h4>
    <div class="desc">주말(토·일)은 자동 휴장. 아래는 <b>추가 휴장일</b>만 등록합니다.</div>
    <div id="holidayList" class="holiday-panel">불러오는 중…</div>
    <details class="holiday-add-details">
      <summary>휴장일 추가</summary>
      <div class="holiday-add-row">
        <label class="field"><span class="lbl">날짜</span><input type="date" id="holidayDate" class="inp"></label>
        <label class="field"><span class="lbl">명칭</span><input type="text" id="holidayName" class="inp" placeholder="추석"></label>
        <button type="button" class="btn primary sm" id="btnAddHoliday">추가</button>
      </div>
      <label class="field holiday-bulk-field">
        <span class="lbl">일괄 추가 (한 줄에 하나 · <code>YYYY-MM-DD,명칭</code>)</span>
        <textarea id="holidayBulk" class="inp holiday-bulk" rows="4" placeholder="2026-07-17,제헌절&#10;2026-09-24,추석"></textarea>
      </label>
      <button type="button" class="btn sm" id="btnBulkHoliday">일괄 저장</button>
    </details>
  </div>`;

  h += `<div class="form-actions">
    <button class="btn auto-toggle-top ${on ? 'is-on' : 'is-off'}" id="btnToggleAuto" data-on="${on ? '1' : '0'}" style="${on ? 'background:var(--down);border-color:var(--down);color:#fff;' : 'background:var(--green);border-color:var(--green);color:#fff;'}">${on ? '자동매매 중지' : '자동매매 시작'}</button>
    <button class="btn primary" id="btnSaveSettings">설정만 저장</button>
    <span class="hint">상단 바에서도 시작/중지 가능 · 주문: <b>${s.order_method === 'LIMIT' ? '지정가' : '시장가'}</b></span>
  </div>`;

  $('settingsForm').innerHTML = h;
  updateAutoTradeButtons(on);
  bindSizingModeToggle();
  bindBuyAmountUnitToggle();
  // 고정금액 모드: 별도 id → 저장 시 initial_max_amount 로 복사
  const fixedAmt = $('set_initial_max_amount_fixed');
  if (fixedAmt) {
    fixedAmt.addEventListener('input', () => {
      const main = $('set_initial_max_amount');
      const hidden = $('set_max_invest_amount');
      if (main) main.value = fixedAmt.value;
      if (hidden) hidden.value = fixedAmt.value;
    });
  }
  const fixedPct = $('set_initial_max_deposit_pct_fixed');
  if (fixedPct) {
    fixedPct.addEventListener('input', () => {
      const main = $('set_initial_max_deposit_pct');
      if (main) main.value = fixedPct.value;
      refreshDepositPctPreviews();
    });
  }
  $('btnSaveSettings').onclick = () => saveSettings(null);
  bindAutoTradeToggle();
  bindTradeTimePreview();
  loadHolidaySection();
  // 레거시 유니버스는 거래대금순 — 조건식 피커 없음
  loadBreakoutConditionPicker(s);
  loadYmgpConditionPicker(s);
  bindBreakoutLevelModeToggle();
}

function formatDepositPctPreview(pct) {
  const d = Number(window._lastDeposit || 0);
  const p = parseNum(pct);
  if (!d) return '예수금 조회 후 환산액 표시';
  if (!p || p <= 0) return `예수금 ${won(d)} 기준`;
  return `${won(d)} × ${p}% = ${won(Math.floor(d * Math.min(p, 100) / 100))}`;
}

function refreshDepositPctPreviews() {
  const map = {
    pyramid_weak: 'set_initial_max_deposit_pct',
    pyramid_strong: 'set_initial_min_deposit_pct',
    add_buy: 'set_add_buy_deposit_pct',
    fixed: 'set_initial_max_deposit_pct_fixed',
    sangtta: 'set_sangtta_buy_deposit_pct',
    breakout: 'set_breakout_buy_deposit_pct',
    ymgp1: 'set_ymgp_buy_deposit_pct_1',
    ymgp2: 'set_ymgp_buy_deposit_pct_2',
    jongga: 'set_jongga_buy_deposit_pct',
  };
  Object.entries(map).forEach(([key, inputId]) => {
    const label = document.querySelector(`[data-pct-preview-label="${key}"]`);
    if (!label) return;
    const el = $(inputId);
    label.textContent = formatDepositPctPreview(el ? el.value : '');
  });
  const hint = $('buyAmountUnitHint');
  if (hint) {
    const unit = ($('set_buy_amount_unit')?.value || 'WON').toUpperCase();
    const d = Number(window._lastDeposit || 0);
    if (unit === 'DEPOSIT_PCT') {
      hint.textContent = d
        ? `현재 예수금 ${won(d)} 기준으로 비중(%) → 매수금액이 환산됩니다.`
        : '예수금 비중 모드 — 잔고 조회 후 환산 미리보기가 표시됩니다.';
    } else {
      hint.textContent = '고정 금액(원) 모드 — 예수금이 바뀌어도 매수금액은 그대로입니다.';
    }
  }
}

function _fillPctFromWonIfEmpty(pctId, wonId) {
  const pctEl = $(pctId);
  const wonEl = $(wonId);
  if (!pctEl || !wonEl) return;
  if (pctEl.value !== '') return;
  const d = Number(window._lastDeposit || 0);
  const w = parseNum(wonEl.value);
  if (d > 0 && w > 0) pctEl.value = (w / d * 100).toFixed(1);
}

function bindBuyAmountUnitToggle() {
  const sel = $('set_buy_amount_unit');
  if (!sel) return;
  const sync = () => {
    const pctMode = (sel.value || 'WON').toUpperCase() === 'DEPOSIT_PCT';
    document.querySelectorAll('.buy-unit-won').forEach((el) => {
      const field = el.closest('.field');
      if (field) field.style.display = pctMode ? 'none' : '';
      else el.style.display = pctMode ? 'none' : '';
    });
    document.querySelectorAll('.buy-unit-pct').forEach((el) => {
      const field = el.closest('.field');
      if (field) field.style.display = pctMode ? '' : 'none';
      else el.style.display = pctMode ? '' : 'none';
    });
    if (pctMode) {
      _fillPctFromWonIfEmpty('set_initial_max_deposit_pct', 'set_initial_max_amount');
      _fillPctFromWonIfEmpty('set_initial_min_deposit_pct', 'set_initial_min_amount');
      _fillPctFromWonIfEmpty('set_add_buy_deposit_pct', 'set_add_buy_amount');
      _fillPctFromWonIfEmpty('set_initial_max_deposit_pct_fixed', 'set_initial_max_amount_fixed');
      _fillPctFromWonIfEmpty('set_sangtta_buy_deposit_pct', 'set_sangtta_buy_amount');
      _fillPctFromWonIfEmpty('set_breakout_buy_deposit_pct', 'set_breakout_buy_amount');
      _fillPctFromWonIfEmpty('set_ymgp_buy_deposit_pct_1', 'set_ymgp_buy_amount_1');
      _fillPctFromWonIfEmpty('set_ymgp_buy_deposit_pct_2', 'set_ymgp_buy_amount_2');
      _fillPctFromWonIfEmpty('set_jongga_buy_deposit_pct', 'set_jongga_buy_amount');
    }
    refreshDepositPctPreviews();
  };
  sel.onchange = sync;
  document.querySelectorAll('[data-pct-preview]').forEach((el) => {
    el.addEventListener('input', refreshDepositPctPreviews);
  });
  sync();
}

function bindBreakoutLevelModeToggle() {
  const sel = $('set_breakout_level_mode');
  const wrap = $('breakoutNDayField');
  const input = $('set_breakout_n_day');
  if (!sel || !wrap) return;
  const sync = () => {
    const useNDay = sel.value === 'n_day_high';
    wrap.classList.toggle('is-dimmed', !useNDay);
    if (input) input.disabled = !useNDay;
  };
  sel.onchange = sync;
  sync();
}

function parseConditionNameList(raw) {
  if (!raw) return [];
  return String(raw).split(/[,\n]/).map((s) => s.trim()).filter(Boolean);
}

// 레거시 유니버스는 거래대금순 — 조건식 피커 없음
// 상따 유니버스는 ka10027(등락률상위) — 조건식 피커 없음

async function loadBreakoutConditionPicker(settings) {
  const box = $('breakoutCondPicker');
  if (!box) return;
  const selected = new Set(parseConditionNameList(settings?.breakout_condition_names));
  try {
    const conds = await fetchConditionsList();
    if (!conds.length) {
      box.innerHTML = '<div class="desc">키움 조건식이 없습니다.</div>';
      syncBreakoutConditionNamesField();
      return;
    }
    box.innerHTML = conds.map((c) => {
      const name = c.condition_name || '';
      const checked = selected.has(name) ? 'checked' : '';
      return `<label class="check cond-pick"><input type="checkbox" class="breakout-cond-check" value="${esc(name)}" ${checked}>${esc(name)} <span class="fhint">API ${esc(c.api_id)}</span></label>`;
    }).join('');
    document.querySelectorAll('.breakout-cond-check').forEach((el) => {
      el.addEventListener('change', syncBreakoutConditionNamesField);
    });
    syncBreakoutConditionNamesField();
  } catch (e) {
    box.innerHTML = emptyRow('조건식 목록을 불러오지 못했습니다.', '⚠️');
  }
}

function syncBreakoutConditionNamesField() {
  const hidden = $('set_breakout_condition_names');
  if (!hidden) return;
  hidden.value = [...document.querySelectorAll('.breakout-cond-check:checked')]
    .map((el) => el.value.trim())
    .filter(Boolean)
    .join(', ');
}

async function loadYmgpConditionPicker(settings) {
  const box = $('ymgpCondPicker');
  if (!box) return;
  const selected = new Set(parseConditionNameList(settings?.ymgp_condition_names));
  try {
    const conds = await fetchConditionsList();
    if (!conds.length) {
      box.innerHTML = '<div class="desc">키움 조건식이 없습니다.</div>';
      syncYmgpConditionNamesField();
      return;
    }
    box.innerHTML = conds.map((c) => {
      const name = c.condition_name || '';
      const checked = selected.has(name) ? 'checked' : '';
      return `<label class="check cond-pick"><input type="checkbox" class="ymgp-cond-check" value="${esc(name)}" ${checked}>${esc(name)} <span class="fhint">API ${esc(c.api_id)}</span></label>`;
    }).join('');
    document.querySelectorAll('.ymgp-cond-check').forEach((el) => {
      el.addEventListener('change', syncYmgpConditionNamesField);
    });
    syncYmgpConditionNamesField();
  } catch (e) {
    box.innerHTML = emptyRow('조건식 목록을 불러오지 못했습니다.', '⚠️');
  }
}

function syncYmgpConditionNamesField() {
  const hidden = $('set_ymgp_condition_names');
  if (!hidden) return;
  hidden.value = [...document.querySelectorAll('.ymgp-cond-check:checked')]
    .map((el) => el.value.trim())
    .filter(Boolean)
    .join(', ');
}

async function loadHolidaySection() {
  const box = $('holidayList');
  if (!box) return;
  const y = new Date().getFullYear();
  const today = new Date().toISOString().slice(0, 10);
  try {
    const data = await fetchJSON(`/trading/holidays?year=${y}`);
    const rows = (data.holidays || []).slice().sort((a, b) => a.holiday_date.localeCompare(b.holiday_date));
    if (!rows.length) {
      box.innerHTML = `<div class="holiday-summary">${y}년 등록된 휴장일 없음</div>`;
      bindHolidayActions();
      return;
    }
    const byMonth = new Map();
    rows.forEach(r => {
      const ym = r.holiday_date.slice(0, 7);
      if (!byMonth.has(ym)) byMonth.set(ym, []);
      byMonth.get(ym).push(r);
    });
    const currentYm = today.slice(0, 7);
    let html = `<div class="holiday-summary">${y}년 휴장 <b>${rows.length}</b>일 · 월별 접기</div><div class="holiday-scroll">`;
    for (const ym of [...byMonth.keys()].sort()) {
      const items = byMonth.get(ym);
      const monthNum = parseInt(ym.split('-')[1], 10);
      const open = ym >= currentYm || items.some(i => i.holiday_date >= today);
      html += `<details class="holiday-month"${open ? ' open' : ''}><summary>${monthNum}월 <span class="holiday-count">${items.length}일</span></summary><div class="holiday-chips">`;
      html += items.map(r => {
        const p = r.holiday_date.split('-');
        const short = `${parseInt(p[1], 10)}/${parseInt(p[2], 10)}`;
        return `<span class="holiday-chip" title="${esc(r.holiday_date)}">${esc(short)} ${esc(r.name)}<button type="button" class="holiday-chip-x" data-del-holiday="${r.id}" aria-label="삭제">×</button></span>`;
      }).join('');
      html += '</div></details>';
    }
    html += '</div>';
    box.innerHTML = html;
    box.querySelectorAll('[data-del-holiday]').forEach(btn => {
      btn.onclick = async (ev) => {
        ev.stopPropagation();
        if (!confirm('이 휴장일을 삭제할까요?')) return;
        try {
          await fetch(`/trading/holidays/${btn.dataset.delHoliday}`, { method: 'DELETE' });
          toast('휴장일 삭제됨');
          loadHolidaySection();
        } catch (e) { toast('삭제 실패', true); }
      };
    });
  } catch (e) {
    box.innerHTML = '<span class="hint">휴장일 목록을 불러오지 못했습니다.</span>';
  }
  bindHolidayActions();
}

function bindHolidayActions() {
  const addBtn = $('btnAddHoliday');
  if (addBtn && !addBtn._bound) {
    addBtn._bound = true;
    addBtn.onclick = async () => {
      const d = $('holidayDate')?.value;
      const name = ($('holidayName')?.value || '').trim();
      if (!d) { toast('날짜를 선택하세요', true); return; }
      try {
        await postJSON('/trading/holidays', { holiday_date: d, name: name || '휴장', is_closed: true });
        toast('휴장일 추가됨');
        if ($('holidayName')) $('holidayName').value = '';
        loadHolidaySection();
      } catch (e) { toast('추가 실패', true); }
    };
  }
  const bulkBtn = $('btnBulkHoliday');
  if (bulkBtn && !bulkBtn._bound) {
    bulkBtn._bound = true;
    bulkBtn.onclick = async () => {
      const raw = ($('holidayBulk')?.value || '').trim();
      if (!raw) { toast('일괄 입력 내용이 없습니다', true); return; }
      const lines = raw.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
      let ok = 0;
      for (const line of lines) {
        const [datePart, ...nameParts] = line.split(',');
        const holiday_date = (datePart || '').trim();
        const name = nameParts.join(',').trim() || '휴장';
        if (!/^\d{4}-\d{2}-\d{2}$/.test(holiday_date)) continue;
        try {
          await postJSON('/trading/holidays', { holiday_date, name, is_closed: true });
          ok += 1;
        } catch (e) { /* skip dup/errors */ }
      }
      if (ok) {
        toast(`${ok}건 저장됨`);
        if ($('holidayBulk')) $('holidayBulk').value = '';
        loadHolidaySection();
      } else {
        toast('저장된 항목 없음 (형식 확인)', true);
      }
    };
  }
}

function collectSettings() {
  syncBreakoutConditionNamesField();
  syncYmgpConditionNamesField();
  const out = {};
  const sizingMethod = ($('set_sizing_method')?.value || 'PYRAMIDING').toUpperCase();
  // hidden 조건식 필드는 아래에서 체크박스 기준으로 직접 수집
  const skipKeys = new Set([
    'initial_max_amount_fixed',
    'initial_max_deposit_pct_fixed',
    'signal_min_threshold_fixed',
    'screener_condition_names',
    'sangtta_condition_names',
    'breakout_condition_names',
    'ymgp_condition_names',
  ]);
  if (sizingMethod === 'FIXED') {
    skipKeys.add('initial_min_amount');
    skipKeys.add('initial_max_amount');
    skipKeys.add('initial_min_deposit_pct');
    skipKeys.add('initial_max_deposit_pct');
    skipKeys.add('signal_min_threshold');
    skipKeys.add('signal_max_threshold');
    skipKeys.add('add_buy_amount');
    skipKeys.add('add_buy_deposit_pct');
    skipKeys.add('add_buy_trigger');
  }

  document.querySelectorAll('#settingsForm [id^="set_"]').forEach(el => {
    const key = el.id.slice(4);
    if (skipKeys.has(key)) return;
    if (el.type === 'checkbox') out[key] = el.checked;
    else if (el.tagName === 'SELECT') out[key] = el.value;
    else if (el.type === 'time') out[key] = el.value || null;
    else if (el.type === 'number') out[key] = (el.value === '' ? null : parseNum(el.value));
    else if (el.type === 'hidden') out[key] = (el.value === '' ? null : (/amount|rate|threshold|limit|positions|buys|cooldown|period/i.test(key) ? parseNum(el.value) : el.value));
    else out[key] = el.value;
  });

  if (sizingMethod === 'FIXED') {
    const fixedEl = $('set_initial_max_amount_fixed');
    if (fixedEl && fixedEl.value !== '') {
      out.initial_max_amount = parseNum(fixedEl.value);
      out.initial_min_amount = parseNum(fixedEl.value);
    }
    const fixedPct = $('set_initial_max_deposit_pct_fixed');
    if (fixedPct && fixedPct.value !== '') {
      const p = parseNum(fixedPct.value);
      out.initial_max_deposit_pct = p;
      out.initial_min_deposit_pct = p;
    }
    const fr = $('set_signal_min_threshold_fixed');
    if (fr && fr.value !== '') out.signal_min_threshold = parseNum(fr.value);
  } else {
    const weak = parseNum($('set_initial_max_amount')?.value || 0);
    const strong = parseNum($('set_initial_min_amount')?.value || 0);
    if (weak > 0 && strong > 0) {
      out.initial_max_amount = Math.max(weak, strong);
      out.initial_min_amount = Math.min(weak, strong);
    }
    const weakPct = parseNum($('set_initial_max_deposit_pct')?.value || 0);
    const strongPct = parseNum($('set_initial_min_deposit_pct')?.value || 0);
    if (weakPct > 0 || strongPct > 0) {
      const hi = Math.max(weakPct || 0, strongPct || 0);
      const lo = Math.min(
        weakPct > 0 ? weakPct : hi,
        strongPct > 0 ? strongPct : hi,
      );
      out.initial_max_deposit_pct = hi || null;
      out.initial_min_deposit_pct = lo || null;
    }
  }

  if (!out.buy_amount_unit) out.buy_amount_unit = 'WON';
  out.buy_amount_unit = String(out.buy_amount_unit).toUpperCase() === 'DEPOSIT_PCT'
    ? 'DEPOSIT_PCT'
    : 'WON';

  // 조건식은 체크박스 상태를 최우선으로 수집 (hidden 값에 의존하지 않음)
  // 레거시·상따 유니버스는 조건식 미사용 — screener/sangtta_condition_names는 저장하지 않음(기존값 유지)
  const breakoutNames = [...document.querySelectorAll('.breakout-cond-check:checked')]
    .map((el) => el.value.trim())
    .filter(Boolean);
  out.breakout_condition_names = breakoutNames.join(', ');
  const ymgpNames = [...document.querySelectorAll('.ymgp-cond-check:checked')]
    .map((el) => el.value.trim())
    .filter(Boolean);
  out.ymgp_condition_names = ymgpNames.join(', ');

  const hiddenBreakout = $('set_breakout_condition_names');
  if (hiddenBreakout) hiddenBreakout.value = out.breakout_condition_names;
  const hiddenYmgp = $('set_ymgp_condition_names');
  if (hiddenYmgp) hiddenYmgp.value = out.ymgp_condition_names;

  // 상따 소액/슬롯 기본값
  if (out.sangtta_buy_amount == null || out.sangtta_buy_amount === '' || Number(out.sangtta_buy_amount) <= 0) {
    out.sangtta_buy_amount = 500000;
  }
  if (out.sangtta_max_slots == null || out.sangtta_max_slots === '') {
    out.sangtta_max_slots = 2;
  }
  if (!out.sangtta_trade_start_time) out.sangtta_trade_start_time = '09:05';
  if (!out.sangtta_trade_end_time) out.sangtta_trade_end_time = '11:00';
  if (out.sangtta_change_min == null || out.sangtta_change_min === '') out.sangtta_change_min = 12;
  if (out.sangtta_change_max == null || out.sangtta_change_max === '') out.sangtta_change_max = 15;
  {
    let lo = Number(out.sangtta_change_min);
    let hi = Number(out.sangtta_change_max);
    if (Number.isFinite(lo) && Number.isFinite(hi) && hi < lo) {
      out.sangtta_change_min = hi;
      out.sangtta_change_max = lo;
    }
  }
  if (out.limit_break_soft_pct == null || out.limit_break_soft_pct === '') out.limit_break_soft_pct = 2;
  if (out.limit_break_hard_pct == null || out.limit_break_hard_pct === '') out.limit_break_hard_pct = 3;
  if (out.sharp_drop_soft_pct == null || out.sharp_drop_soft_pct === '') out.sharp_drop_soft_pct = 3;
  if (out.sharp_drop_hard_pct == null || out.sharp_drop_hard_pct === '') out.sharp_drop_hard_pct = 5;
  if (out.soft_confirm_polls == null || out.soft_confirm_polls === '' || Number(out.soft_confirm_polls) <= 0) {
    out.soft_confirm_polls = 3;
  }
  if (out.market_risk_index == null || out.market_risk_index === '') {
    out.market_risk_index = 'kospi';
  }
  if (out.market_risk_change_pct == null || out.market_risk_change_pct === '') {
    out.market_risk_change_pct = -2.0;
  }
  if (out.market_risk_max_buys_per_strategy == null || out.market_risk_max_buys_per_strategy === '') {
    out.market_risk_max_buys_per_strategy = 2;
  }
  if (out.scan_interval_sec == null || out.scan_interval_sec === '' || Number(out.scan_interval_sec) <= 0) {
    out.scan_interval_sec = 60;
  } else {
    out.scan_interval_sec = Math.max(15, Math.min(600, Number(out.scan_interval_sec) || 60));
  }
  if (out.breakout_buy_amount == null || Number(out.breakout_buy_amount) <= 0) out.breakout_buy_amount = 1000000;
  if (out.breakout_max_slots == null || Number(out.breakout_max_slots) <= 0) out.breakout_max_slots = 1;
  if (!out.breakout_trade_start_time) out.breakout_trade_start_time = '11:00';
  if (!out.breakout_trade_end_time) out.breakout_trade_end_time = '14:30';
  if (out.breakout_entry_hard == null) out.breakout_entry_hard = true;
  if (out.breakout_entry_soft == null) out.breakout_entry_soft = true;
  if (out.breakout_entry_soft_polls == null || Number(out.breakout_entry_soft_polls) <= 0) {
    out.breakout_entry_soft_polls = 3;
  }
  if (out.breakout_entry_hold == null) out.breakout_entry_hold = true;
  if (out.breakout_hold_expire_bars == null || Number(out.breakout_hold_expire_bars) <= 0) {
    out.breakout_hold_expire_bars = 3;
  }
  if (out.breakout_hold_rsi_min == null || out.breakout_hold_rsi_min === '') {
    out.breakout_hold_rsi_min = 30;
  }
  if (out.breakout_body_pct == null || out.breakout_body_pct === '') out.breakout_body_pct = 2;
  if (out.breakout_range_mult == null || out.breakout_range_mult === '') out.breakout_range_mult = 0;
  if (out.breakout_require_ma20_cross == null) out.breakout_require_ma20_cross = true;
  if (!out.breakout_ma20_mode) out.breakout_ma20_mode = 'above';
  if (out.breakout_ma20_grace_bars == null || out.breakout_ma20_grace_bars === '') out.breakout_ma20_grace_bars = 3;
  if (out.breakout_rsi_period == null || Number(out.breakout_rsi_period) <= 0) {
    out.breakout_rsi_period = 10;
  }
  if (out.ymgp_buy_amount_1 == null || Number(out.ymgp_buy_amount_1) <= 0) out.ymgp_buy_amount_1 = 500000;
  if (out.ymgp_buy_amount_2 == null || Number(out.ymgp_buy_amount_2) <= 0) out.ymgp_buy_amount_2 = 500000;
  if (out.jongga_buy_amount == null || Number(out.jongga_buy_amount) <= 0) out.jongga_buy_amount = 1000000;
  if (out.jongga_max_slots == null || Number(out.jongga_max_slots) <= 0) out.jongga_max_slots = 1;
  if (!out.jongga_trade_end_time) out.jongga_trade_end_time = out.jongga_pick_end_time || '14:40';
  if (!out.jongga_pick_end_time) out.jongga_pick_end_time = out.jongga_trade_end_time || '14:40';
  if (out.ymgp_max_slots == null || Number(out.ymgp_max_slots) <= 0) out.ymgp_max_slots = 1;
  if (!out.ymgp_trade_start_time) out.ymgp_trade_start_time = '09:30';
  if (!out.ymgp_trade_end_time) out.ymgp_trade_end_time = '14:30';

  return out;
}

async function loadSettings() {
  try { const s = await fetchJSON('/trading/settings'); renderSettingsForm(s); }
  catch (e) { $('settingsForm').innerHTML = emptyRow('설정을 불러오지 못했습니다.', '⚠️'); }
}

async function saveSettings(enableOverride) {
  const payload = collectSettings();
  payload.is_enabled = (enableOverride === null || enableOverride === undefined)
    ? getAutoTradeEnabled() : enableOverride;
  if (payload.max_invest_amount == null && payload.initial_max_amount != null) payload.max_invest_amount = payload.initial_max_amount;
  if (payload.max_invest_amount == null) payload.max_invest_amount = 5000000;
  if (payload.stop_loss_rate == null) payload.stop_loss_rate = 5;
  if (payload.take_profit_rate == null) payload.take_profit_rate = 10; // 트레일링 시작 %
  payload.profit_lock_trigger = null;
  payload.profit_lock_floor = null;
  if (!payload.order_method) payload.order_method = 'MARKET';
  try {
    const data = await postJSON('/trading/settings', payload);
    toast(
      data && data.resumed_from_daily_halt
        ? '일일 한도 완화로 자동매매를 다시 시작했습니다.'
        : (enableOverride === true ? '자동매매를 시작했습니다.' : (enableOverride === false ? '자동매매를 중지했습니다.' : '설정을 저장했습니다.'))
    );
    await loadSettings();
    await loadStatus();
    if (isAutoTabActive()) loadPositions(true, { silent: true });
  } catch (e) {
    const msg = (e && e.message) ? String(e.message) : '';
    toast(msg ? `설정 저장 실패 (${msg})` : '설정 저장 실패', true);
  }
}

/* ===== Tabs ===== */
function isAutoMonitorSubActive() {
  const sub = $('auto-sub-monitor');
  return sub && sub.classList.contains('active');
}

function syncDashStickyOffsets() {
  const topbar = document.querySelector('.topbar');
  const wrap = document.querySelector('.wrap');
  const root = document.documentElement;
  root.style.setProperty('--dash-topbar-h', `${Math.ceil(topbar?.getBoundingClientRect().height || 54)}px`);
  const wrapPad = wrap
    ? Math.ceil(wrap.getBoundingClientRect().top + (window.innerHeight - wrap.getBoundingClientRect().bottom))
    : 32;
  root.style.setProperty('--dash-wrap-pad', `${Math.max(wrapPad, 24)}px`);
}

function switchAutoSubTab(name) {
  document.querySelectorAll('.auto-subtab').forEach((t) => {
    t.classList.toggle('active', t.dataset.autoSub === name);
  });
  document.querySelectorAll('.auto-subpane').forEach((p) => {
    p.classList.toggle('active', p.id === `auto-sub-${name}`);
  });
  requestAnimationFrame(syncDashStickyOffsets);
  if (name === 'monitor' && isAutoTabActive()) {
    loadPositions(true, { silent: true });
    loadLog();
    loadActivity();
  } else if (name === 'settings') {
    loadSettings();
  }
}

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.pane').forEach(p => p.classList.toggle('active', p.id === 'pane-' + name));
  if (name === 'auto') requestAnimationFrame(syncDashStickyOffsets);
  if (name === 'auto' && isAutoMonitorSubActive()) {
    loadPositions(true, { silent: true });
  }
  if (name === 'board') {
    requestAnimationFrame(() => loadPerformance(true));
  }
}

/* ===== Refresh orchestration ===== */
function refreshAll() {
  $('lastUpdated').textContent = new Date().toLocaleTimeString('ko-KR');
  loadMarketIndices({ silent: true });
  loadPerformance(true);
  loadAccount(); loadStatus(); loadTelegram();
  loadTodayKeywords();
  loadThemeBatchStatus();
  loadPositions(true, { silent: true, forceLive: true });
  loadSells(); loadOrders();
  loadActivity(); loadLog(); loadSettings();
}
const refreshMap = {
  positions: () => loadPositions(true), sells: loadSells, orders: loadOrders, conditions: loadConditions,
};
const ACTIVITY_REFRESH_MS = 5000;
const LOG_REFRESH_EVERY_N_ACTIVITY = 3; // 활동 로그 3회(~15초)마다 체결 로그도 갱신
const POSITION_LIVE_REFRESH_MS = 10000;
const AUTO_REFRESH_MS = 60000;

let autoTimer = null;
let activityTimer = null;
let positionsLiveTimer = null;
let _activityPollCount = 0;

function isAutoTabActive() {
  const pane = $('pane-auto');
  return pane && pane.classList.contains('active');
}

function startPositionsLivePolling() {
  if (positionsLiveTimer) clearInterval(positionsLiveTimer);
  positionsLiveTimer = setInterval(() => {
    if (isAutoTabActive() && isAutoMonitorSubActive()) loadPositions(false, { silent: true });
  }, POSITION_LIVE_REFRESH_MS);
}
function setupAutoRefresh() {
  const cb = $('autoRefresh');
  function applyAuto() {
    if (autoTimer) { clearInterval(autoTimer); autoTimer = null; }
    if (cb.checked) {
      autoTimer = setInterval(() => {
        loadAccount(); loadStatus();
        loadMarketIndices({ silent: true });
        loadTodayKeywords();
        loadThemeBatchStatus();
        loadPositions(true, { silent: true });
        loadSells(); loadOrders(); loadLog();
        $('lastUpdated').textContent = new Date().toLocaleTimeString('ko-KR');
      }, AUTO_REFRESH_MS);
    }
  }
  cb.onchange = applyAuto;
  applyAuto();
}

function startActivityPolling() {
  if (activityTimer) clearInterval(activityTimer);
  if (!$('activityBody')) return;
  activityTimer = setInterval(() => {
    if (isAutoTabActive() && isAutoMonitorSubActive()) {
      loadActivity();
      _activityPollCount += 1;
      // 매수 실패가 PENDING→FAILED로 바뀌면 체결 로그에 바로 보이도록
      if (_activityPollCount % LOG_REFRESH_EVERY_N_ACTIVITY === 0) {
        loadLog();
        loadOrders();
      }
    }
  }, ACTIVITY_REFRESH_MS);
}

document.addEventListener('DOMContentLoaded', () => {
  syncDashStickyOffsets();
  window.addEventListener('resize', syncDashStickyOffsets);
  document.querySelectorAll('.tab[data-tab]').forEach(t => {
    t.onclick = (e) => { e.preventDefault(); switchTab(t.dataset.tab); };
  });
  document.querySelectorAll('.auto-subtab').forEach((t) => {
    t.onclick = () => switchAutoSubTab(t.dataset.autoSub);
  });
  $('refreshAll').onclick = refreshAll;
  $('pfRefresh').onclick = () => loadPerformance(true);
  $('logRefresh').onclick = loadLog;
  if ($('logDays')) $('logDays').onchange = loadLog;
  if ($('activityRefresh')) $('activityRefresh').onclick = loadActivity;
  $('autoPosRefresh').onclick = () => loadPositions(true);
  $('scrRefresh').onclick = loadScreener;
  if ($('sangRefresh')) $('sangRefresh').onclick = loadSangtta;
  if ($('breakoutRefresh')) $('breakoutRefresh').onclick = loadBreakout;
  if ($('ymgpRefresh')) $('ymgpRefresh').onclick = loadYmgp;
  if ($('jonggaRefresh')) $('jonggaRefresh').onclick = () => loadJongga(true);
  document.querySelectorAll('[data-refresh]').forEach(btn => { btn.onclick = () => { $('lastUpdated').textContent = new Date().toLocaleTimeString('ko-KR'); (refreshMap[btn.dataset.refresh] || (() => {}))(); }; });
  setupAutoRefresh();
  startActivityPolling();
  startPositionsLivePolling();
  bindAutoTradeToggle();
  bindPositionSellButtons();
  refreshAll();
});
