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

/* ===== 성과 통계 (단일 API 호출, 대시보드·자동매매 탭 공유) ===== */
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

async function loadPerformance(force = false) {
  $('perfSourceHint').textContent = '실현손익 조회 중...';
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
    cards += statCard('거래비용', pnlStr(-Math.abs(d.total_cost)), `비용 전 ${pnlStr(d.gross_pnl)}`, 'down');
    cards += statCard('승률', `${d.win_rate}%`, `${d.wins}승 ${d.losses}패${d.breakeven ? ' · 무승부 ' + d.breakeven : ''}`, 'flat');
    cards += statCard('손익비 (Payoff)', `${d.payoff}`, `평균익 ${pnlStr(d.avg_win)} / 평균손 ${pnlStr(d.avg_loss)}`, 'flat');
    cards += statCard('Profit Factor', `${d.profit_factor}`, '총이익 / 총손실', 'flat');
    cards += statCard('1회 기대손익', pnlStr(d.expected), `총 ${d.trade_count}회 청산`, signClass(d.expected));
    cards += statCard('최대 낙폭 (MDD)', pnlStr(d.mdd), '', 'down');
    cards += statCard('일평균 손익', pnlStr(d.daily_avg), `${num(d.trading_days)}일 · ${d.day_wins}승 ${d.day_losses}패`, signClass(d.daily_avg));
    cards += statCard('최고/최악 거래', pnlStr(d.best), `최악 ${pnlStr(d.worst)}`, signClass(d.best));
    $('perfCards').innerHTML = cards;

    $('perfCurveTotal').textContent = pnlStr(d.net_pnl);
    drawPerfChart(d.curve.map((_, i) => i + 1), d.curve.map((c) => c.cum));

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
function statusRow(name, label, cls) {
  return `<div class="status-row"><span class="name">${esc(name)}</span><span class="pill ${cls}">${esc(label)}</span></div>`;
}
async function loadStatus() {
  try {
    const [mon, settings, stopLoss] = await Promise.all([
      fetchJSON('/monitoring/status').catch(() => ({})),
      fetchJSON('/trading/settings').catch(() => ({})),
      fetchJSON('/stop-loss/status').catch(() => ({})),
    ]);
    const monRunning = !!(mon.monitoring && mon.monitoring.is_running);
    const scanRunning = !!(mon.auto_trade_scanner && mon.auto_trade_scanner.is_running);
    const buyRunning = !!(mon.buy_executor && mon.buy_executor.is_running);
    const slRunning = !!stopLoss.is_running;
    const autoOn = !!settings.is_enabled;
    let html = '';
    html += statusRow('자동매매', autoOn ? '활성' : '비활성', autoOn ? 'on' : 'off');
    html += statusRow('종목 스캔', scanRunning ? '실행중' : '중지', scanRunning ? 'run' : 'off');
    html += statusRow('손절/익절 모니터링', slRunning ? '실행중' : '중지', slRunning ? 'run' : 'off');
    html += statusRow('매수 실행기', buyRunning ? '실행중' : '중지', buyRunning ? 'run' : 'off');
    $('statusBody').innerHTML = html;
    $('statusTime').textContent = mon.timestamp ? new Date(mon.timestamp).toLocaleTimeString('ko-KR') : '';

    const sig = mon.signals || {};
    const map = [['총 신호', 'total_signals'], ['주문', 'ordered_signals'], ['처리중', 'processing_signals'], ['실패', 'failed_signals'], ['취소', 'cancelled_signals']];
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

function levelChipPct(l, ex, p, slRate) {
  const buy = positionBuyPrice(p);
  const px = parseNum(l.price);
  if (l.method === 'PCT' && slRate != null) return slRate;
  if (buy > 0 && px > 0) return (buy - px) / buy * 100;
  return null;
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
  if (buyMs < t0 - 60000 || buyMs > t1 + 900000) return null;
  if (buyMs <= t0) return 0;
  if (buyMs >= t1) return width;
  for (let i = 0; i < barTimes.length - 1; i++) {
    if (buyMs >= barTimes[i] && buyMs <= barTimes[i + 1]) {
      const span = barTimes[i + 1] - barTimes[i] || 1;
      const ratio = (buyMs - barTimes[i]) / span;
      return ((i + ratio) / (barTimes.length - 1)) * width;
    }
  }
  return null;
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
  let buyDot = '';
  if (buyPrice > 0 && o.buyTime) {
    const bx = buyMarkerOnSparkline(sp.timestamps, o.buyTime, width);
    if (bx != null) {
      const by = pad + innerH - ((buyPrice - min) / range) * innerH;
      buyDot = `<circle class="pos-spark-buy" cx="${bx.toFixed(1)}" cy="${by.toFixed(1)}" r="2.8"/>`;
    }
  }
  const buyHint = buyPrice > 0 ? ` · 매수 ${num(buyPrice)}` : '';
  return `<div class="pos-spark-wrap" title="당일 15분봉 · 시가 대비 ${rateStr(chg)}${buyHint}">
    <svg class="pos-spark" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" aria-hidden="true">
      <line x1="0" y1="${openY.toFixed(1)}" x2="${width}" y2="${openY.toFixed(1)}" class="pos-spark-open"/>
      <polyline points="${pts}" class="pos-spark-line" style="stroke:${stroke}"/>
      ${buyDot}
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
const SPARKLINE_TTL_MS = 120000;
let sparklineLoading = false;

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
    _sparklineCache.data = { ..._sparklineCache.data, ...(d.sparklines || {}) };
    _sparklineCache.at = now;
    window._positionSparklines = _sparklineCache.data;
    if ($('autoPositionsBody') && (window._appHoldings || []).length) {
      renderPositionCards(window._appHoldings, 'autoPositionsBody');
      bindPositionSellButtons();
    }
  } catch (_) {
    /* 스파크라인 실패는 포지션 표시와 분리 */
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
    const dist = ex.stop_distance_pct;
    const pctRate = pctStopRate(ex, p);
    const safe = dist != null && dist > 1.5;
    const atrTxt = ex.atr ? `ATR ${num(ex.atr)}원 (${ex.atr_period}일)` : (ex.levels_live === false ? 'ATR: 새로고침 시 계산' : 'ATR 미사용 (%기준)');
    const reason = REASON_LABEL[ex.effective_stop_reason] || ex.effective_stop_reason || '-';
    const methodLbl = effMeta.method === 'ATR' ? 'ATR' : (effMeta.method === 'PCT' ? 'PCT' : '');

    const chips = (ex.levels || []).map((l) => renderLevelChip(l, ex, p, effStop, pctRate)).join('');

    return `<div class="pos-card">
      <div class="pos-card-head">
        <div class="pos-card-title">
          <div class="name">${esc(p.stock_name)}</div>
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
        <div><div class="pk">${ex.trailing_armed ? '트레일링' : '트레일 시작'}</div><div class="pv ${ex.trailing_armed ? 'up' : ''}">${ex.trailing_armed && effStop ? num(effStop) : (ex.trailing_start_price ? num(ex.trailing_start_price) : '-')}</div></div>
        ${ex.trailing_floor_price ? `<div><div class="pk">익절 바닥</div><div class="pv up">${num(ex.trailing_floor_price)}</div></div>` : ''}
        <div><div class="pk">${ex.atr ? 'ATR' : '변동성'}</div><div class="pv text-cyan">${ex.atr ? num(ex.atr) + '원' : '-'}</div></div>
      </div>
      <div class="pos-stop-bar ${safe ? 'safe' : ''}">
        <div class="stop-main">유효 손절선 <span style="color:var(--down);">${effPrice ? num(effPrice) + '원' : '-'}</span>
          ${effRate != null && effPrice ? `<span class="stop-pct-hint"> · ${methodLbl || esc(reason)} −${effRate.toFixed(1)}%</span>` : ''}
          <span class="pill run" style="margin-left:6px;">${esc(reason)}</span></div>
        <div class="stop-sub">${atrTxt}${dist != null ? ` · 손절선까지 ${dist.toFixed(2)}%` : ''}${ex.liquidate_time ? ` · ${ex.liquidate_time} 장마감청산` : ''}${ex.levels_live ? ' · 실시간' : ''}</div>
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
    return `<tr>
      <td><span class="stock-name">${esc(p.stock_name)}</span><span class="stock-code">${esc(p.stock_code)}</span></td>
      <td class="num">${num(positionBuyPrice(p))}</td><td class="num">${num(ex.current_price || p.current_price || p.buy_price)}</td>
      <td class="num">${num(p.buy_quantity)}</td>
      <td class="num">${won(buyAmt)}</td><td class="num">${weights[i].toFixed(1)}%</td>
      <td class="num ${signClass(pl)}">${pnlStr(pl)}</td><td class="num ${signClass(rate)}">${rateStr(rate)}</td>
      <td class="num down">${effRate != null ? `−${effRate.toFixed(1)}%` : '-'}</td>
      <td class="num down">${effPrice ? num(effPrice) : '-'}</td>
      <td><span class="hint">${esc(reason)}${effMeta.method ? ` · ${effMeta.method}` : ''}${ex.atr ? ` · ATR ${num(ex.atr)}` : ''}</span></td>
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

let positionsLoading = false;

function shouldLoadPositionsLive() {
  return isAutoTabActive() && isAutoMonitorSubActive();
}

async function loadPositions(live = false, opts = {}) {
  const silent = opts.silent === true;
  const useLive = live || (opts.preferLive !== false && shouldLoadPositionsLive());
  if (positionsLoading) return;
  positionsLoading = true;
  const sk = '<div class="skeleton">현재가·ATR 조회 중...</div>';
  if (useLive && !silent && !$('autoPositionsBody')?.querySelector('.pos-card')) {
    if ($('positionsBody')) $('positionsBody').innerHTML = sk;
    if ($('autoPositionsBody')) $('autoPositionsBody').innerHTML = sk;
  }
  try {
    if (!(window._kiwoomHoldings || []).length) {
      try { await loadAccount(); } catch (_) { /* 비중 계산은 DB 폴백 */ }
    }
    const url = `/positions/?status=HOLDING&limit=100&with_levels=true${useLive ? '&live=true' : ''}`;
    const d = await fetchJSON(url, { timeoutMs: useLive ? 90000 : 12000 });
    const items = d.items || [];
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
    if (useLive) {
      $('lastUpdated').textContent = new Date().toLocaleTimeString('ko-KR');
      if (!silent) toast(`보유 ${items.length}종목 현재가 갱신 완료`);
    }
  } catch (e) {
    const msg = useLive ? '현재가/ATR 갱신 실패 — 잠시 후 다시 시도하세요.' : '포지션을 불러오지 못했습니다.';
    if ($('autoPositionsBody') && !silent) $('autoPositionsBody').innerHTML = emptyRow(msg, '⚠️');
    if (!silent && $('positionsBody')) $('positionsBody').innerHTML = emptyRow(msg, '⚠️');
    if (useLive && !silent) toast('보유종목 갱신 실패', true);
  } finally {
    positionsLoading = false;
  }
}

async function loadAutoPositions(live = true) {
  await loadPositions(live);
}

async function loadSells() {
  try {
    const d = await fetchJSON('/sell-orders/?status=ALL&limit=50');
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
  const m = { ORDERED: '접수', FAILED: '실패', FILLED: '체결', COMPLETED: '체결', PENDING: '대기', CANCELLED: '취소' };
  return m[status] || status || '-';
}

function orderReasonBuy(o) {
  if (o.status === 'FAILED') return o.failure_reason || '사유 미기록';
  if (o.status === 'ORDERED') return '매수 주문 접수';
  if (o.status === 'FILLED' || o.status === 'COMPLETED') {
    const q = o.fill_quantity != null ? `${num(o.fill_quantity)}주` : '';
    const p = o.fill_price != null ? `@ ${num(o.fill_price)}원` : '';
    return ['매수 체결', q, p].filter(Boolean).join(' ');
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
      fetchJSON('/trading/orders').catch(() => ({ orders: [] })),
      fetchJSON('/sell-orders/?status=ALL&limit=100').catch(() => ({ items: [] })),
    ]);
    const items = [];
    for (const o of buyRes.orders || []) {
      items.push({
        side: '매수',
        name: o.stock_name,
        code: o.stock_code,
        status: o.status,
        reason: orderReasonBuy(o),
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
      <td><span class="pill ${stateP(o.status)}">${esc(orderStatusLabel(o.status))}</span></td>
      <td style="white-space:normal;color:var(--muted);max-width:280px;">${esc(o.reason)}</td></tr>`;
    }).join('');
    $('ordersBody').innerHTML = `<table class="tbl"><thead><tr>
      <th>일자</th><th>시각</th><th>구분</th><th>종목</th><th>상태</th><th>사유</th>
    </tr></thead><tbody>${rows}</tbody></table>`;
  } catch (e) {
    $('ordersBody').innerHTML = emptyRow('주문 내역을 불러오지 못했습니다.', '⚠️');
  }
}

/* ===== 조건식 ===== */
async function loadConditions() {
  try {
    const d = await fetchJSON('/conditions/');
    const conds = Array.isArray(d) ? d : (d.data || []);
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
    const allRunning = enabled && rt.scanner_running && rt.buy_executor_running && rt.stop_loss_running;
    const banner = $('activityBanner');
    if (banner) {
      banner.className = 'activity-banner' + (allRunning ? '' : ' off');
      const lastScan = fmtScanTime(rt.last_scan_at);
      const lastSync = fmtScanTime(rt.last_sync_at);
      const scanInfo = rt.last_scan_at
        ? `마지막 스캔 ${lastScan} · 대상 ${rt.last_scan_targets || 0} · 신호 ${rt.last_scan_created || 0}`
        : (enabled ? '아직 스캔 없음 (2분 주기)' : '자동매매 OFF — 스캔 미실행');
      const syncInfo = rt.last_sync_at
        ? `마지막 동기화 ${lastSync} (${rt.monitor_interval_sec || 120}초 주기)`
        : `포지션 동기화 대기 (${rt.monitor_interval_sec || 120}초 주기)`;
      banner.innerHTML = `
        <div class="ab-item"><span class="ab-dot ${allRunning ? 'pulse' : ''}"></span>
          <strong>${allRunning ? '자동매매 실행 중' : (enabled ? '일부 중지됨' : '자동매매 OFF · 동기화만')}</strong></div>
        <div class="ab-item">${runtimeBadge(rt.scanner_running, '스캐너')}${runtimeBadge(rt.buy_executor_running, '매수')}${runtimeBadge(rt.stop_loss_running, '동기화')}</div>
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
      if (rt.stop_loss_running) {
        hint = `포지션 동기화 루프 실행 중 (${rt.monitor_interval_sec || 120}초마다 [SYNC] 로그). 자동매매 ON 시 [SCANNER]/[BUY] 로그도 표시됩니다.`;
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
function logStateLabel(status, action) {
  if (status === 'COMPLETED' || status === 'FILLED' || status === 'ORDERED') return '성공';
  if (status === 'FAILED') return '실패';
  if (status === 'PENDING') return '대기';
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

async function loadLog() {
  const days = logDaysFilter();
  try {
    const [sells, orders] = await Promise.all([
      fetchJSON('/sell-orders/?status=ALL&limit=100').catch(() => ({ items: [] })),
      fetchJSON('/trading/orders').catch(() => ({ orders: [] })),
    ]);
    let logs = [];
    for (const o of (sells.items || [])) {
      logs.push({ ts: o.completed_at || o.ordered_at || o.created_at, action: '매도', name: o.stock_name, code: o.stock_code, qty: o.sell_quantity,
        state: logStateLabel(o.status, '매도'), reason: logReasonForSell(o) });
    }
    for (const o of (orders.orders || [])) {
      if (o.status !== 'FILLED' && o.status !== 'COMPLETED' && o.status !== 'ORDERED' && o.status !== 'FAILED') continue;
      logs.push({
        ts: o.filled_at || o.detected_at,
        action: '매수',
        name: o.stock_name,
        code: o.stock_code,
        qty: o.fill_quantity != null ? o.fill_quantity : '-',
        state: logStateLabel(o.status, '매수'),
        reason: logReasonForBuy(o),
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
      <td class="num">${esc(l.qty)}</td><td><span class="pill ${stateP(l.state)}">${esc(l.state)}</span></td>
      <td style="white-space:normal;color:var(--muted);">${esc(l.reason)}</td></tr>`).join('');
    $('logBody').innerHTML = `<table class="tbl"><thead><tr><th>시각</th><th>동작</th><th>종목</th><th class="num">수량</th><th>상태</th><th>사유</th></tr></thead><tbody>${rows}</tbody></table>`;
  } catch (e) { $('logBody').innerHTML = emptyRow('로그를 불러오지 못했습니다.', '⚠️'); }
}

/* ===== 스크리너 후보 ===== */
const PT_LABEL = { LEVERAGE: '레버리지', INVERSE: '인버스', DOUBLE_INVERSE: '곱버스', ETF: 'ETF', ETN: 'ETN', STOCK: '일반' };
const PT_CLS = { LEVERAGE: 'on', INVERSE: 'run', DOUBLE_INVERSE: 'off', ETF: 'off', ETN: 'off', STOCK: 'run' };
function fmtEokOrJo(v) {
  if (v === null || v === undefined || v === '') return '-';
  const n = parseNum(v);
  if (Math.abs(n) >= 10000) return numFixed(n / 10000, 2) + '조';
  return numFixed(n, 1) + '억';
}
async function loadScreener() {
  const market = $('scrMarket').value;
  $('screenerBody').innerHTML = '<div class="skeleton">거래대금순 상위 조회 중...</div>';
  $('screenerCount').textContent = '';
  try {
    const d = await fetchJSON(`/screener/candidates?market=${market}&limit=50`);
    if (!d.success) { $('screenerBody').innerHTML = emptyRow(d.error || '조회 실패 (장중/토큰 확인)', '⚠️'); return; }
    const items = d.items || [];
    const rawN = d.raw_count != null ? d.raw_count : d.total;
    const excl = d.excluded_etf_count != null ? d.excluded_etf_count : 0;
    const exclPer = d.excluded_per_count != null ? d.excluded_per_count : 0;
    const exclParts = [`ETF·파생 ${excl}`];
    if (exclPer > 0) exclParts.push(`PER ${exclPer}`);
    $('screenerCount').textContent = `후보 ${d.selected_count} / API ${rawN} (${exclParts.join(', ')} 제외)`;
    if (!items.length) { $('screenerBody').innerHTML = emptyRow('데이터가 없습니다.', '🧭'); return; }
    const rows = items.map(s => {
      const rate = parseNum(s.change_rate);
      const amtEok = parseNum(s.trade_amount) / 100; // 백만원 → 억원
      return `<tr style="${s.included ? '' : 'opacity:.42;'}">
        <td style="text-align:center;">${s.included ? '✅' : '—'}</td>
        <td><span class="stock-name">${esc(s.stock_name)}</span><span class="stock-code">${esc(s.stock_code)}</span></td>
        <td><span class="pill ${PT_CLS[s.product_type] || 'off'}">${esc(PT_LABEL[s.product_type] || s.product_type)}</span></td>
        <td class="num">${num(s.current_price)}</td>
        <td class="num ${signClass(rate)}">${rateStr(rate)}</td>
        <td class="num">${num(s.volume)}</td>
        <td class="num">${num(amtEok)}억</td>
        <td class="num">${fmtEokOrJo(s.market_cap)}</td>
        <td class="num">${s.per == null ? '-' : numFixed(s.per, 2)}</td>
        <td class="num">${s.pbr == null ? '-' : numFixed(s.pbr, 2)}</td>
        <td class="num">${s.roe == null ? '-' : numFixed(s.roe, 2)}</td></tr>`;
    }).join('');
    $('screenerBody').innerHTML = `<table class="tbl"><thead><tr><th>편입</th><th>종목</th><th>구분</th><th class="num">현재가</th><th class="num">등락률</th><th class="num">거래량</th><th class="num">거래대금</th><th class="num">시총</th><th class="num">PER</th><th class="num">PBR</th><th class="num">ROE</th></tr></thead><tbody>${rows}</tbody></table>`;
  } catch (e) { $('screenerBody').innerHTML = emptyRow('조회 중 오류 (서버/네트워크 확인)', '⚠️'); }
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
  const spread = weakAmt - strongAmt;
  const spreadHint = spread < 500000
    ? '<div class="fhint warn" style="margin-top:8px;">금액 차이가 작으면 등락률별 매수금이 거의 같아집니다. 약한 신호 금액을 더 크게 두는 것을 권장합니다.</div>'
    : '';
  return `<div class="box-soft" id="sizingPyramidPanel">
    <div class="box-title">첫 매수 — 역피라미딩 (등락률↑ 금액↓)</div>
    <div class="desc" style="margin-bottom:10px;">아래 <b>최소 등락</b> 미만이면 매수하지 않습니다. 등락이 커질수록 금액을 줄여 변동성·손절 리스크를 낮춥니다.</div>
    <div class="desc" style="margin-bottom:10px;">적용 중: 약한 신호 <b>${num(weakAmt)}원</b> · 강한 신호 <b>${num(strongAmt)}원</b></div>
    <div class="sizing-ladder">
      <div class="sizing-row">
        <span class="sr-label">약한 신호</span>
        <span class="sr-eq">등락</span>
        <input type="number" class="w-rate" id="set_signal_min_threshold" value="${esc(smin)}" step="any" placeholder="2">
        <span class="sr-unit">% 이상 →</span>
        <input type="number" class="w-amt" id="set_initial_max_amount" value="${esc(weakAmt)}" step="any" placeholder="5000000">
        <span class="sr-unit" style="grid-column:auto;">원 (큰 금액)</span>
      </div>
      <div class="sizing-between">↓ 등락률이 올라갈수록 매수 금액 감소 (자동 비례) ↓</div>
      <div class="sizing-row strong">
        <span class="sr-label">강한 신호</span>
        <span class="sr-eq">등락</span>
        <input type="number" class="w-rate" id="set_signal_max_threshold" value="${esc(smax)}" step="any" placeholder="10">
        <span class="sr-unit">% 이상 →</span>
        <input type="number" class="w-amt" id="set_initial_min_amount" value="${esc(strongAmt)}" step="any" placeholder="2000000">
        <span class="sr-unit" style="grid-column:auto;">원 (작은 금액)</span>
      </div>
    </div>${spreadHint}
    <div style="margin-top:14px;padding-top:14px;border-top:1px dashed var(--border);">
      <div class="box-title" style="color:var(--green);">추가매수 — 이미 보유 중일 때</div>
      <div class="desc" style="margin-bottom:10px;">매수 후 수익이 나면 같은 종목에 조금씩 더 삽니다.</div>
      <div class="sizing-add-row">
        <span class="sr-label">수익률</span>
        <input type="number" class="w-rate" id="set_add_buy_trigger" value="${esc(_v(s, 'add_buy_trigger') || '0.7')}" step="any">
        <span class="sr-unit">% 이상 →</span>
        <input type="number" class="w-amt" id="set_add_buy_amount" value="${esc(_v(s, 'add_buy_amount') || '1000000')}" step="any">
        <span class="sr-unit">원 추가</span>
      </div>
    </div>
  </div>`;
}

function fieldSizingFixed(s) {
  const amt = _v(s, 'initial_max_amount') || _v(s, 'max_invest_amount') || '5000000';
  const smin = _v(s, 'signal_min_threshold') !== '' ? _v(s, 'signal_min_threshold') : '2';
  return `<div class="box-soft sizing-panel-hidden" id="sizingFixedPanel">
    <div class="box-title">고정 금액 매수</div>
    <div class="desc">조건 충족 시 아래 규칙으로 매수합니다.</div>
    <div class="sizing-row" style="margin-bottom:10px;">
      <span class="sr-label">매수 조건</span>
      <span class="sr-eq">등락</span>
      <input type="number" class="w-rate" id="set_signal_min_threshold_fixed" value="${esc(smin)}" step="any">
      <span class="sr-unit">% 이상 →</span>
      <input type="number" class="w-amt" id="set_initial_max_amount_fixed" value="${esc(amt)}" step="any">
      <span class="sr-unit">원</span>
    </div>
  </div>`;
}

function fieldExitRules(s) {
  const hasAtr = _v(s, 'atr_mult_stop') !== '' || _v(s, 'atr_mult_trail') !== '';
  return `<div class="exit-stack">
    <div class="exit-note">📌 <b>매도(청산)</b> 전용 설정입니다. 추가매수·진입 타이밍과 무관합니다. 아래 ①②③ 중 <b>ATR을 입력하면 ②③의 % 방식을 대체</b>합니다.</div>

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
        ${field('트레일링 시작 — 고점 수익률(%) 도달 후 적용 (0=즉시)', fNum('take_profit_rate', s, '예: 10'), '기존 「익절 %」 필드 · 도달 시 즉시매도 아님')}
        ${field('고점 대비 하락 % (비우면 미사용)', fNum('trailing_stop_pct', s, '예: 1.8'), 'ATR 트레일 배수를 입력하면 이 값은 사용하지 않습니다')}
      </div>
      <div class="exit-example">예: 시작 10% · 하락 3% → +10% 도달 시 바닥 233,200원 잠금 · 고점 251,000이면 매도선 max(243,470, 233,200)=243,470</div>
    </div>

    <div class="exit-card atr">
      <h5><span class="exit-num">3</span> ATR 변동성 — 종목별 동적 손절·트레일 (입력 시 ②·손절% 대체)</h5>
      <div class="exit-desc">
        <b>ATR</b> = 최근 일봉 기준, 하루 평균 가격 변동폭(원). 변동 큰 종목은 손절선을 넓게, 작은 종목은 좁게 잡습니다.<br>
        <span class="text-cyan">손절선 ≈ 매수가 − ATR×손절배수 · 트레일선 ≈ 고점 − ATR×트레일배수</span>
      </div>
      <div class="exit-fields cols-3">
        ${field('손절 배수 (비우면 ① 손절 % 사용)', fNum('atr_mult_stop', s, '예: 1.5'))}
        ${field('트레일 배수 (비우면 ② 트레일 % 사용)', fNum('atr_mult_trail', s, '예: 2'))}
        ${field('ATR 계산 기간(일)', fNum('atr_period', s, '14'))}
      </div>
      <div class="exit-example">예: 매수 1만원, ATR 400원, 손절배수 1.5 → <b>9,400원</b> 이하 매도 · 고점 1만2천, 트레일배수 2 → <b>1만1,200원</b> 이하 매도<br>
      ${hasAtr ? '<span class="text-accent" style="font-weight:600;">✓ ATR 값이 설정되어 있어 손절/트레일은 변동성 기준으로 동작합니다.</span>' : '비워 두면 ①②의 % 방식만 사용합니다.'}</div>
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

  // 종목 선정 — 개별주만 고정 (ETF/파생상품 설정 UI 제거됨)
  h += `<div class="form-section">
    <h4>종목 선정 (스크리너)</h4>
    <div class="box-soft screener-policy">
      <div class="desc">거래대금 상위 <b>코스피·코스닥 개별 주식</b>만 자동 후보로 사용합니다. ETF·ETN·파생상품은 설정과 무관하게 항상 제외됩니다.</div>
      <ul class="policy-list">
        <li><span class="policy-tag on">포함</span> 거래대금 상위 50 · KRX · 당일 20만주 이상</li>
        <li><span class="policy-tag off">제외</span> ETF · ETN · 레버리지 · 인버스 · 곱버스 · SPAC · 우선주 · 정리매매</li>
        <li><span class="policy-tag off">제외</span> PER 100배 이상 · PER 마이너스 (재무 데이터 없으면 통과)</li>
      </ul>
      <div class="exit-note">2분 주기 스캔 · API(ETF·ETN 제외) + 종목명 후처리 이중 필터 · 우측 「스크리너 후보」에서 확인</div>
    </div>
  </div>`;

  // 청산 규칙 (전체 너비, 3단 카드)
  h += `<div class="form-section">
    <h4>청산 규칙 (매도)</h4>
    <div class="desc">이미 산 종목을 <b>언제 팔지</b> 정합니다. 2분마다 현재가를 확인해 조건 충족 시 전량 매도합니다.</div>
    ${fieldExitRules(s)}
  </div>`;

  // 진입 타이밍
  h += `<div class="form-section">
    <h4>진입 타이밍</h4>
    <div class="desc">스크리너를 통과해도 아래 타이밍을 만족할 때만 신규 진입. (데이터 없으면 통과)</div>
    <div class="box-soft" style="margin-top:0;">
      ${fCheck('use_entry_gate', s, '진입 타이밍 게이트 사용')}
      ${fCheck('require_above_open', s, '현재가 ≥ 시가 (당일 양봉만)')}
      ${fCheck('require_above_vwap', s, '현재가 ≥ 장중 VWAP (분봉 강세 확인)')}
      <div class="form-grid" style="margin-top:12px;">
        ${field('당일 위치 하한 (0~1, 고가·저가 사이)', fNum('day_position_min', s, '0.5'))}
        ${field('당일 위치 상한 (0~1, 이 값 초과 시 매수 금지)', fNum('day_position_max', s, '비우면 미적용'))}
        ${field('전일대비 거래량비율(%) 하한', fNum('volume_ratio_min', s, '비우면 미적용'))}
      </div>
    </div>
  </div>`;

  // 매수 사이징
  h += `<div class="form-section">
    <h4>매수 사이징</h4>
    <div class="desc">등락률·금액으로 <b>언제·얼마 살지</b> 정합니다. 역피라미딩은 등락이 클수록 금액을 줄입니다. 「약한 신호」 등락 % 미만이면 매수하지 않습니다.</div>
    ${field('방식', fSelect('sizing_method', s, [['PYRAMIDING', '역피라미딩 (등락↑ 금액↓)'], ['FIXED', '고정 금액']]))}
    ${fieldSizingPyramiding(s)}
    ${fieldSizingFixed(s)}
    <div class="form-grid" style="margin-top:14px;">
      ${field('예수금 현금 보유율(%) — 매수에 쓰지 않고 남길 비율', fNum('cash_reserve_pct', s, '10'), '0=전액 사용 · 10=예수금의 10%는 현금 유지')}
      ${field('최대 동시 보유 종목 (0=예수금 기준 자동)', fNum('max_concurrent_positions', s, '0'))}
      ${field('1일 최대 매수 횟수', fNum('max_daily_buys', s, '20'))}
      ${field('1일 손실 한도(원)', fNum('daily_loss_limit', s, '-500000'))}
    </div>
    <div class="form-grid-4" style="margin-top:12px;">
      ${field('1일 이익목표(원, 도달 시 신규매수 종료)', fNum('daily_profit_target', s, '150000'))}
      ${field('재주문 쿨다운(초)', fNum('reorder_cooldown_sec', s, '300'))}
      ${field('매매 시작', fTime('trade_start_time', s))}
      ${field('매매 종료', fTime('trade_end_time', s))}
    </div>
    <div class="desc" style="margin-top:8px;">거래일 08:50~「매매 종료」까지 엔진(스캐너·매수기)이 자동 기동되며, 종료 시각 이후 루프가 중지됩니다. 실제 매수는 「매매 시작」부터 허용됩니다.</div>
    <input type="hidden" id="set_max_invest_amount" value="${esc(_v(s, 'max_invest_amount') || s.initial_max_amount || 5000000)}">
  </div>`;

  // 장 마감 전 전량 청산
  h += `<div class="form-section">
    <div class="box-soft">
      ${fCheck('liquidate_before_close', s, '장 마감 전 전량 청산 (오버나잇 방지)')}
      <div class="desc">지정 시각이 지나면 보유 종목을 손익과 무관하게 모두 시장가로 정리합니다. 매매 종료(15:20) 이전 시각으로 설정하세요.</div>
      ${field('청산 시작 시각', fTime('liquidate_time', s))}
      <input type="hidden" id="set_order_method" value="${esc(_v(s, 'order_method') || 'MARKET')}">
    </div>
  </div>`;

  // actions
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
  $('btnSaveSettings').onclick = () => saveSettings(null);
  bindAutoTradeToggle();
  loadHolidaySection();
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
  const out = {};
  const sizingMethod = ($('set_sizing_method')?.value || 'PYRAMIDING').toUpperCase();
  const skipKeys = new Set(['initial_max_amount_fixed', 'signal_min_threshold_fixed']);
  if (sizingMethod === 'FIXED') {
    skipKeys.add('initial_min_amount');
    skipKeys.add('initial_max_amount');
    skipKeys.add('signal_min_threshold');
    skipKeys.add('signal_max_threshold');
    skipKeys.add('add_buy_amount');
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
    const fr = $('set_signal_min_threshold_fixed');
    if (fr && fr.value !== '') out.signal_min_threshold = parseNum(fr.value);
  } else {
    const weak = parseNum($('set_initial_max_amount')?.value || 0);
    const strong = parseNum($('set_initial_min_amount')?.value || 0);
    if (weak > 0 && strong > 0) {
      out.initial_max_amount = Math.max(weak, strong);
      out.initial_min_amount = Math.min(weak, strong);
    }
  }
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
  try {
    const r = await postJSON('/trading/settings', payload);
    toast(enableOverride === true ? '자동매매를 시작했습니다.' : (enableOverride === false ? '자동매매를 중지했습니다.' : '설정을 저장했습니다.'));
    renderSettingsForm(r);
    if (isAutoTabActive()) loadPositions(true, { silent: true });
  } catch (e) { toast('설정 저장 실패', true); }
}

/* ===== Tabs ===== */
function isAutoMonitorSubActive() {
  const sub = $('auto-sub-monitor');
  return sub && sub.classList.contains('active');
}

function switchAutoSubTab(name) {
  document.querySelectorAll('.auto-subtab').forEach((t) => {
    t.classList.toggle('active', t.dataset.autoSub === name);
  });
  document.querySelectorAll('.auto-subpane').forEach((p) => {
    p.classList.toggle('active', p.id === `auto-sub-${name}`);
  });
  if (name === 'monitor' && isAutoTabActive()) {
    loadPositions(true, { silent: true });
    loadPerformance(true);
    loadLog();
    loadScreener();
    loadActivity();
  } else if (name === 'settings') {
    loadSettings();
  }
}

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.pane').forEach(p => p.classList.toggle('active', p.id === 'pane-' + name));
  if (name === 'auto' && isAutoMonitorSubActive()) {
    loadPositions(true, { silent: true });
  }
}

/* ===== Refresh orchestration ===== */
function refreshAll() {
  $('lastUpdated').textContent = new Date().toLocaleTimeString('ko-KR');
  loadPerformance(true);
  loadAccount(); loadStatus(); loadTelegram();
  loadPositions(shouldLoadPositionsLive(), { silent: shouldLoadPositionsLive() });
  loadSells(); loadOrders();
  loadActivity(); loadLog(); loadSettings();
  if (isAutoTabActive() && isAutoMonitorSubActive()) {
    loadScreener();
    loadPerformance(false);
  }
}
const refreshMap = {
  positions: () => loadPositions(true), sells: loadSells, orders: loadOrders, conditions: loadConditions,
};
const ACTIVITY_REFRESH_MS = 3000;
const POSITION_LIVE_REFRESH_MS = 10000;

let autoTimer = null;
let activityTimer = null;
let positionsLiveTimer = null;

function isAutoTabActive() {
  const pane = $('pane-auto');
  return pane && pane.classList.contains('active');
}

function startPositionsLivePolling() {
  if (positionsLiveTimer) clearInterval(positionsLiveTimer);
  positionsLiveTimer = setInterval(() => {
    if (isAutoTabActive() && isAutoMonitorSubActive()) loadPositions(true, { silent: true });
  }, POSITION_LIVE_REFRESH_MS);
}
function setupAutoRefresh() {
  const cb = $('autoRefresh');
  function applyAuto() {
    if (autoTimer) { clearInterval(autoTimer); autoTimer = null; }
    if (cb.checked) {
      autoTimer = setInterval(() => {
        loadAccount(); loadStatus();
        loadPositions(shouldLoadPositionsLive(), { silent: shouldLoadPositionsLive() });
        loadSells(); loadOrders(); loadLog();
        $('lastUpdated').textContent = new Date().toLocaleTimeString('ko-KR');
      }, 30000);
    }
  }
  cb.onchange = applyAuto;
  applyAuto();
}

function startActivityPolling() {
  if (activityTimer) clearInterval(activityTimer);
  if (!$('activityBody')) return;
  activityTimer = setInterval(() => {
    if (isAutoTabActive() && isAutoMonitorSubActive()) loadActivity();
  }, ACTIVITY_REFRESH_MS);
}

document.addEventListener('DOMContentLoaded', () => {
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
  document.querySelectorAll('[data-refresh]').forEach(btn => { btn.onclick = () => { $('lastUpdated').textContent = new Date().toLocaleTimeString('ko-KR'); (refreshMap[btn.dataset.refresh] || (() => {}))(); }; });
  setupAutoRefresh();
  startActivityPolling();
  startPositionsLivePolling();
  bindAutoTradeToggle();
  bindPositionSellButtons();
  refreshAll();
});
