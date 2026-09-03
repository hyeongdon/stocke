'use strict';

/* ===== Helpers ===== */
const $ = (id) => document.getElementById(id);

async function fetchJSON(url, opts) {
  const timeoutMs = (opts && opts.timeoutMs) || 15000;
  const { timeoutMs: _t, ...rest } = opts || {};
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, Object.assign({
      credentials: 'same-origin',
      headers: { 'Accept': 'application/json' },
      signal: ctrl.signal,
    }, rest || {}));
    if (res.status === 401) {
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      window.location.href = `/login?next=${next}`;
      throw new Error(`401 ${url}`);
    }
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
function renderMarketIndexMiniChart(_it) {
  return '';
}
function fmtEok(v) {
  if (v == null || Number.isNaN(Number(v))) return '-';
  const n = Number(v);
  const sign = n > 0 ? '+' : '';
  return sign + n.toLocaleString('ko-KR') + '억';
}
function renderInvestorBadges(it) {
  const inv = it && it.investor;
  if (!inv || (inv.foreign == null && inv.institution == null)) return '';
  const chip = (label, val) => (val == null ? '' : `<span class="mi-inv ${signClass(val)}">${label} ${fmtEok(val)}</span>`);
  const tip = [
    inv.bizdate ? `기준 ${inv.bizdate}` : '',
    inv.personal != null ? `개인 ${fmtEok(inv.personal)}` : '',
  ].filter(Boolean).join(' · ');
  return `<span class="mi-investor" title="${esc(tip)}">${chip('외', inv.foreign)}${chip('기', inv.institution)}</span>`;
}
function renderMarketIndexItem(it) {
  const dir = signClass(it.change_pct ?? it.change);
  const deltaParts = [];
  if (it.change != null) deltaParts.push(fmtIndexDelta(it.change));
  if (it.change_pct != null) deltaParts.push(`(${fmtIndexPct(it.change_pct)})`);
  return `<span class="market-index-item ${dir}"><span class="mi-label">${esc(it.label)}</span>${renderMarketIndexMiniChart(it)}<span class="mi-value">${fmtIndexValue(it.value)}</span><span class="mi-delta">${deltaParts.join(' ') || '-'}</span>${renderInvestorBadges(it)}</span>`;
}
function hmMinutes(t) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(t || '').trim());
  if (!m) return null;
  return Number(m[1]) * 60 + Number(m[2]);
}
function fmtEokAxis(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n === 0) return '0';
  const sign = n > 0 ? '+' : '';
  const abs = Math.abs(n);
  if (abs >= 100) return sign + Math.round(n).toLocaleString('ko-KR');
  if (abs >= 10) return sign + n.toFixed(0);
  return sign + n.toFixed(1);
}
let _ifPlotSeq = 0;
function renderInvestorFlowRow(label, series, key) {
  const W = 280, H = 92;
  const padL = 38, padR = 8, padT = 10, padB = 18;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;
  const ptsData = (series || []).map(h => ({
    t: String(h.time || ''),
    v: Number(h[key] ?? 0) || 0,
    min: hmMinutes(h.time),
  })).filter(p => p.min != null);
  if (ptsData.length < 2) return '';
  const vals = ptsData.map(p => p.v);
  const mins = ptsData.map(p => p.min);
  const t0 = Math.min(9 * 60, ...mins);
  const t1 = Math.max(15 * 60 + 30, ...mins);
  const tSpan = Math.max(t1 - t0, 1);
  const cMax = Math.max(0, ...vals);
  const cMin = Math.min(0, ...vals);
  const cSpan = Math.max(cMax - cMin, 1);
  const cx = min => padL + ((min - t0) / tSpan) * plotW;
  const cy = v => padT + ((cMax - v) / cSpan) * plotH;
  const last = vals[vals.length - 1];
  const dir = last >= 0 ? 'up' : 'down';
  const zeroY = cy(0);
  const plotBottom = padT + plotH;
  const uid = `if${++_ifPlotSeq}`;

  const yTicks = [cMax, 0, cMin];
  const yGrid = yTicks.map((v, i) => {
    const y = cy(v);
    if (i > 0 && Math.abs(y - cy(yTicks[i - 1])) < 11) return '';
    const isZero = v === 0;
    return `<line class="${isZero ? 'if-zero' : 'if-grid'}" x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}"></line>`
      + `<text class="if-axis${isZero ? ' if-axis-zero' : ''}" x="${padL - 4}" y="${y.toFixed(1)}" text-anchor="end" dominant-baseline="middle">${fmtEokAxis(v)}</text>`;
  }).join('');

  const xMins = [9 * 60, 12 * 60, 15 * 60, 15 * 60 + 30].filter(m => m >= t0 - 1 && m <= t1 + 1);
  const xGrid = xMins.map(m => {
    const x = cx(m);
    const hh = String(Math.floor(m / 60)).padStart(2, '0');
    const mm = String(m % 60).padStart(2, '0');
    return `<line class="if-grid-v" x1="${x.toFixed(1)}" y1="${padT}" x2="${x.toFixed(1)}" y2="${plotBottom}"></line>`
      + `<text class="if-axis if-axis-x" x="${x.toFixed(1)}" y="${H - 4}" text-anchor="middle">${hh}:${mm}</text>`;
  }).join('');

  const pts = ptsData.map(p => `${cx(p.min).toFixed(1)},${cy(p.v).toFixed(1)}`).join(' ');
  const firstX = cx(ptsData[0].min).toFixed(1);
  const lastX = cx(ptsData[ptsData.length - 1].min).toFixed(1);
  const areaD = `M ${firstX},${zeroY.toFixed(1)} L ${pts} L ${lastX},${zeroY.toFixed(1)} Z`;
  const posH = Math.max(0, zeroY - padT);
  const negH = Math.max(0, plotBottom - zeroY);
  const hoverEvery = Math.max(1, Math.ceil(ptsData.length / 24));
  const dots = ptsData.map((p, i) => {
    if (i !== 0 && i !== ptsData.length - 1 && i % hoverEvery !== 0) return '';
    const tip = `${p.t} ${label} ${fmtEok(p.v)}`;
    return `<circle class="if-cum-dot" cx="${cx(p.min).toFixed(1)}" cy="${cy(p.v).toFixed(1)}" r="6"><title>${esc(tip)}</title></circle>`;
  }).join('');

  return `<div class="if-row"><span class="if-rlabel">${label}</span><svg class="if-bars ${dir}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
    <defs>
      <clipPath id="${uid}-pos"><rect x="${padL}" y="${padT}" width="${plotW}" height="${posH.toFixed(1)}"></rect></clipPath>
      <clipPath id="${uid}-neg"><rect x="${padL}" y="${zeroY.toFixed(1)}" width="${plotW}" height="${negH.toFixed(1)}"></rect></clipPath>
    </defs>
    ${yGrid}${xGrid}
    <path class="if-fill if-fill-pos" d="${areaD}" clip-path="url(#${uid}-pos)"></path>
    <path class="if-fill if-fill-neg" d="${areaD}" clip-path="url(#${uid}-neg)"></path>
    <polyline class="if-cum" points="${pts}"></polyline>${dots}
  </svg></div>`;
}
function renderInvestorFlowCard(it) {
  const series = it.investor_intraday || [];
  if (series.length < 2) return '';
  const inv = it.investor || {};
  const last = series[series.length - 1] || {};
  const chips = [
    inv.foreign != null ? `<span class="mi-inv ${signClass(inv.foreign)}">외 ${fmtEok(inv.foreign)}</span>` : '',
    inv.institution != null ? `<span class="mi-inv ${signClass(inv.institution)}">기 ${fmtEok(inv.institution)}</span>` : '',
  ].join('');
  const t0 = series[0].time || '';
  const t1 = last.time || '';
  const lastF = last.foreign != null ? last.foreign : inv.foreign;
  const lastI = last.institution != null ? last.institution : inv.institution;
  const foot = `<div class="if-foot">${esc(t0)}~${esc(t1)} 누적
    <span class="mi-inv ${signClass(lastF)}">외 ${fmtEok(lastF)}</span>
    <span class="mi-inv ${signClass(lastI)}">기 ${fmtEok(lastI)}</span></div>`;
  return `<div class="if-card">
    <div class="if-card-head"><span class="if-market">${esc(it.label)}</span><span class="mi-investor">${chips}</span></div>
    ${renderInvestorFlowRow('외국인', series, 'foreign')}
    ${renderInvestorFlowRow('기관', series, 'institution')}
    ${foot}
  </div>`;
}
function renderInvestorFlow(items, updatedAt) {
  const panel = $('investorFlow');
  const body = $('investorFlowBody');
  if (!panel || !body) return;
  const cards = (items || [])
    .filter(it => ['kospi', 'kosdaq'].includes(String(it.key || '').toLowerCase()))
    .map(renderInvestorFlowCard)
    .filter(Boolean);
  if (!cards.length) { panel.style.display = 'none'; return; }
  panel.style.display = '';
  const sub = panel.querySelector('.if-sub');
  if (sub) {
    const when = updatedAt ? ` · ${esc(fmtInvestorBatchTime(updatedAt))} 배치` : '';
    sub.textContent = `시간대별 누적 순매수 (억원, 잠정${when}) · 선이 내려가면 매도 누적`;
  }
  body.innerHTML = cards.join('');
}
function fmtInvestorBatchTime(s) {
  const raw = String(s || '').trim();
  const m = raw.match(/T(\d{2}:\d{2})/);
  if (m) return m[1];
  return raw.slice(11, 16) || raw;
}
function setupInvestorFlowToggle() {
  const btn = $('investorFlowToggle');
  const panel = $('investorFlow');
  if (!btn || !panel) return;
  const apply = (collapsed) => {
    panel.classList.toggle('collapsed', collapsed);
    btn.textContent = collapsed ? '펼치기' : '접기';
    btn.setAttribute('aria-expanded', String(!collapsed));
  };
  apply(localStorage.getItem('investorFlowCollapsed') === '1');
  btn.onclick = () => {
    const next = !panel.classList.contains('collapsed');
    localStorage.setItem('investorFlowCollapsed', next ? '1' : '0');
    apply(next);
  };
}
async function loadMarketIndices(opts) {
  const el = $('marketIndices');
  if (!el) return;
  try {
    const d = await fetchJSON('/market/indices', { timeoutMs: 15000 });
    const items = d.indices || [];
    el.innerHTML = items.length
      ? items.map(renderMarketIndexItem).join('')
      : '<span class="market-index-skeleton">지수 데이터 없음</span>';
    renderInvestorFlow(items, d.investor_updated_at);
    const hasFlow = items.some(it => (it.investor_intraday || []).length >= 2);
    if (!hasFlow && !(opts && opts.noRetry)) {
      setTimeout(() => loadMarketIndices({ silent: true, noRetry: true }), 8000);
    }
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
  if (!s) return { day: '-', time: '-', full: '-', ymd: null };
  const d = assumeUtc ? parseDbUtc(s) : new Date(s);
  if (!d || Number.isNaN(d.getTime())) return { day: '-', time: '-', full: '-', ymd: null };
  const fmt = assumeUtc ? { timeZone: TZ_SEOUL } : {};
  const today = kstYmd(new Date());
  const that = kstYmd(d);
  const diff = Math.round((Date.parse(today) - Date.parse(that)) / 86400000);
  let day;
  if (diff === 0) day = '오늘';
  else if (diff === 1) day = '어제';
  else day = d.toLocaleDateString('ko-KR', Object.assign({ year: 'numeric', month: '2-digit', day: '2-digit' }, fmt));
  const time = d.toLocaleTimeString('ko-KR', Object.assign({ hour: '2-digit', minute: '2-digit', hour12: false }, fmt));
  const md = d.toLocaleDateString('ko-KR', Object.assign({ month: '2-digit', day: '2-digit' }, fmt));
  return { day, time, full: `${day} ${time}`, shortCross: `${md} ${time}`, ymd: that };
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
function reasonLabel(r, pl, rate) {
  const raw = (r || '').toUpperCase();
  const sign = (pl != null && !Number.isNaN(Number(pl)))
    ? Math.sign(Number(pl))
    : ((rate != null && !Number.isNaN(Number(rate))) ? Math.sign(Number(rate)) : null);
  if (raw === 'TRAILING') {
    if (sign > 0) return '익절 (트레일)';
    if (sign < 0) return '손절 (트레일)';
  }
  if (raw === 'PROFIT_LOCK') {
    if (sign > 0) return '익절 (수익잠금)';
    if (sign < 0) return '손절 (수익잠금)';
  }
  // 상따 상한가/급락 이탈 등은 STOP_LOSS로 기록돼도 실현 +이면 익절
  if (raw === 'STOP_LOSS' && sign > 0) return '익절 (이탈)';
  if (raw === 'TAKE_PROFIT' && sign < 0) return '손절';
  return REASON_LABEL[raw] || REASON_LABEL[r] || r || '기타';
}

let toastTimer;
function toast(msg, isErr) {
  const t = $('toast'); t.textContent = msg;
  t.className = 'toast show' + (isErr ? ' err' : '');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.className = 'toast'; }, 3000);
}
function setConn(state, text) { $('connBadge').className = 'conn-badge ' + state; $('connText').textContent = text; }

function updateApiLimitBadge(info, traffic, scanLoad) {
  if (!info && !traffic) return;
  const wait = Number((info && info.seconds_until_available) || 0);
  const usage = Number((info && info.usage_percent) || 0);
  const recent = Number((info && info.recent_calls) || 0);
  const maxCalls = Number((info && info.max_calls_per_window) || 0);
  const apiBit = maxCalls
    ? `API ${recent}/${maxCalls}`
    : (usage ? `API ${Math.round(usage)}%` : '');
  const load = scanLoad || {};
  if (traffic && traffic.defer_dashboard_live) {
    if (traffic.defer_reason === 'scan' || load.in_progress) {
      const done = Number(load.scanned || 0);
      const total = Number(load.targets_total || 0);
      const rem = Number(load.remaining || Math.max(0, total - done));
      const prog = total > 0 ? `${done}/${total}` : '진행';
      const eta = load.eta_sec != null && Number(load.eta_sec) > 0
        ? ` · 남은≈${Math.round(Number(load.eta_sec))}초`
        : '';
      setConn('warn', `스캔 live지연 ${prog} (남음 ${rem})${eta}${apiBit ? ` · ${apiBit}` : ''}`);
      return;
    }
    if (traffic.defer_reason === 'post_scan_burst') {
      const left = Math.ceil(Number(traffic.defer_remaining_sec || 0));
      setConn('warn', `스캔직후 live지연 ${left}초${apiBit ? ` · ${apiBit}` : ''}`);
      return;
    }
    setConn('warn', `스캔 중 — live 조회 지연${apiBit ? ` · ${apiBit}` : ''}`);
    return;
  }
  if (info && (info.status === 'limited' || wait > 1.5)) {
    setConn('warn', `키움 API 대기 (${Math.ceil(wait)}초)`);
    return;
  }
  if (usage > 85) {
    setConn('warn', `키움 API ${Math.round(usage)}%${maxCalls ? ` (${recent}/${maxCalls})` : ''}`);
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
    const cashSnap = b.db_cash_snapshot;
    if (cashSnap && cashSnap.date) {
      d2Line += ` · DB ${cashSnap.date} D+0 ${won(parseNum(cashSnap.deposit_d0))}`
        + ` / D+2 ${won(parseNum(cashSnap.deposit_d2))}`;
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

function reasonOutcomeCards(d) {
  const rows = Array.isArray(d.by_reason) ? d.by_reason : [];
  const pick = (code) => {
    const hit = rows.find((r) => String(r.reason || '').toUpperCase() === code);
    return { count: hit ? Number(hit.count) || 0 : 0, realized: hit ? Number(hit.realized) || 0 : 0 };
  };
  const sl = pick('STOP_LOSS');
  const tp = pick('TAKE_PROFIT');
  const others = rows.filter((r) => {
    const k = String(r.reason || '').toUpperCase();
    return k !== 'STOP_LOSS' && k !== 'TAKE_PROFIT';
  });
  const total = rows.reduce((s, r) => s + (Number(r.count) || 0), 0);
  const share = (n) => (total ? `${Math.round((n / total) * 1000) / 10}%` : '0%');
  const avg = (realized, count) => (count ? `평균 ${pnlStr(Math.round(realized / count))}` : '평균 -');
  let html = '';
  html += statCard(
    '손절',
    pnlStr(sl.realized),
    `${num(sl.count)}건 · ${share(sl.count)} · ${avg(sl.realized, sl.count)}`,
    sl.realized ? signClass(sl.realized) : 'down',
  );
  html += statCard(
    '익절',
    pnlStr(tp.realized),
    `${num(tp.count)}건 · ${share(tp.count)} · ${avg(tp.realized, tp.count)}`,
    tp.realized ? signClass(tp.realized) : 'up',
  );
  if (others.length) {
    const oc = others.reduce((s, r) => s + (Number(r.count) || 0), 0);
    const or = others.reduce((s, r) => s + (Number(r.realized) || 0), 0);
    const sub = others.map((r) => `${reasonLabel(r.reason, r.realized)} ${num(r.count)}건`).join(' · ');
    html += statCard('기타 청산', pnlStr(or), `${num(oc)}건 · ${share(oc)} · ${sub}`, signClass(or));
  }
  return html;
}

function payoffWinRateCard(d) {
  const payoff = Number(d.payoff) || 0;
  const wr = Number(d.win_rate) || 0;
  const be = payoff > 0 ? Math.round((100 / (1 + payoff)) * 10) / 10 : 0;
  const wl = `${d.wins || 0}승 ${d.losses || 0}패${d.breakeven ? ' · 무승부 ' + d.breakeven : ''}`;
  let explain;
  if (!(d.wins || d.losses)) {
    explain = '청산된 승·패가 없어 손익비와 승률을 비교할 수 없습니다.';
  } else if (!payoff) {
    explain = '평균 손실이 없어 손익비를 계산하지 않습니다. 승률만 참고하세요.';
  } else if (wr >= be) {
    explain = `손익비 ${payoff}면 본전 승률은 ${be}%. 지금 승률이 그보다 높아, 이길 때 금액 × 횟수가 질 때보다 큽니다.`;
  } else {
    explain = `손익비 ${payoff}는 한 번 이길 때 금액이 클 뿐, 본전엔 승률 ${be}%가 필요합니다. 지금은 ${wr}%라 패가 많아 계좌 합계는 손실입니다.`;
  }
  return `<div class="card stat compact-stat" title="${esc(explain)}">
    <div class="label">손익비 · 승률</div>
    <div class="value flat">${esc(String(payoff))} · ${esc(String(wr))}%</div>
    <div class="delta flat">본전 ${be ? be + '%' : '-'} · 평균익 ${pnlStr(d.avg_win)} / 평균손 ${pnlStr(d.avg_loss)} · ${esc(wl)}</div>
  </div>`;
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
  if (!force && window._perfStats) {
    return window._perfStats;
  }
  const d = await fetchJSON('/performance/stats?source=db', { timeoutMs: 60000 });
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
    applyCurveMdd(d);
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
    cards += statCard(
      '순손익 (실현)',
      pnlStr(d.net_pnl),
      `계좌평가자산 ${pnlStr(d.base_asset || d.seed)} 기준 수익률 ${rateStr(d.return_rate)} · PF ${d.profit_factor} (참고 · 총이익/총손실, 1 미만이면 합계 손실)`,
      signClass(d.net_pnl),
    );
    cards += payoffWinRateCard(d);
    const mddSub = [
      d.mdd_pct ? `고점 대비 ${rateStr(d.mdd_pct)}` : '',
      (d.mdd_peak_date && d.mdd_trough_date)
        ? (d.mdd_peak_date === d.mdd_trough_date
          ? d.mdd_trough_date
          : `${d.mdd_peak_date} → ${d.mdd_trough_date}`)
        : '',
    ].filter(Boolean).join(' · ');
    cards += statCard('최대 낙폭 (MDD)', pnlStr(d.mdd), mddSub || '누적 곡선 고점 대비', 'down');
    cards += reasonOutcomeCards(d);
    $('perfCards').innerHTML = cards;

    const mddHint = d.mdd
      ? ` · MDD ${pnlStr(d.mdd)}${d.mdd_pct ? ` (${rateStr(d.mdd_pct)})` : ''}`
      : '';
    $('perfCurveTotal').textContent = pnlStr(d.net_pnl) + mddHint;
    if (isBoardTabActive()) {
      drawPerfChart(d.curve || [], d);
    }

    if (!d.daily.length) $('dailyBody').innerHTML = emptyRow('일별 데이터가 없습니다.', '📅');
    else $('dailyBody').innerHTML = `<table class="tbl"><thead><tr><th>날짜</th><th class="num">청산</th><th class="num">승</th><th class="num">패</th><th class="num">손익</th></tr></thead><tbody>${
      d.daily.map((r) => `<tr><td>${esc(r.date)}</td><td class="num">${num(r.count)}</td><td class="num">${num(r.wins)}</td><td class="num">${num(r.losses)}</td><td class="num ${signClass(r.pnl)}">${pnlStr(r.pnl)}</td></tr>`).join('')}</tbody></table>`;
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
  const parts = [];
  if (s.use_legacy !== false) {
    parts.push({ key: 'legacy', label: '레거시', start: _v(s, 'trade_start_time') || '10:00', end: _v(s, 'trade_end_time') || '15:20' });
  }
  if (s.use_sangtta !== false) {
    parts.push({ key: 'sangtta', label: '상따', start: _v(s, 'sangtta_trade_start_time') || '09:05', end: _v(s, 'sangtta_trade_end_time') || '11:00' });
  }
  const useBreakout = !!(s.use_breakout || String(s.breakout_condition_names || '').trim());
  if (useBreakout) {
    parts.push({
      key: 'breakout',
      label: '돌파',
      start: _v(s, 'breakout_trade_start_time') || '11:00',
      end: _v(s, 'breakout_trade_end_time') || '14:30',
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
  const useFractal = !!(s.use_fractal || String(s.fractal_condition_names || '').trim());
  if (useFractal) {
    parts.push({
      key: 'fractal',
      label: '프랙탈',
      start: _v(s, 'fractal_trade_start_time') || '09:20',
      end: _v(s, 'fractal_trade_end_time') || '14:50',
    });
  }
  if (s.use_ma1592) {
    parts.push({
      key: 'ma1592',
      label: '15/92',
      start: _v(s, 'ma1592_trade_start_time') || '09:10',
      end: _v(s, 'ma1592_trade_end_time') || '15:15',
    });
  }
  if (!parts.length) {
    parts.push({ key: 'none', label: '전략없음', start: '09:00', end: '15:30' });
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
    jongga_trade_start_time: $('set_jongga_trade_start_time')?.value || '14:30',
    jongga_pick_end_time: $('set_jongga_pick_end_time')?.value || '14:40',
    jongga_trade_end_time: $('set_jongga_trade_end_time')?.value || $('set_jongga_pick_end_time')?.value || '14:40',
    jongga_leg2_start_time: $('set_jongga_leg2_start_time')?.value || '14:50',
    jongga_leg3_start_time: $('set_jongga_leg3_start_time')?.value || '15:20',
    jongga_leg3_end_time: $('set_jongga_leg3_end_time')?.value || '15:28',
    use_jongga: !!$('set_use_jongga')?.checked,
    fractal_trade_start_time: $('set_fractal_trade_start_time')?.value || '09:20',
    fractal_trade_end_time: $('set_fractal_trade_end_time')?.value || '14:50',
    use_fractal: !!$('set_use_fractal')?.checked,
    fractal_condition_names: $('set_fractal_condition_names')?.value || '',
    use_ma1592: !!$('set_use_ma1592')?.checked,
    ma1592_trade_start_time: $('set_ma1592_trade_start_time')?.value || '09:10',
    ma1592_trade_end_time: $('set_ma1592_trade_end_time')?.value || '15:15',
    use_legacy: $('set_use_legacy') ? !!$('set_use_legacy').checked : true,
    use_sangtta: $('set_use_sangtta') ? !!$('set_use_sangtta').checked : true,
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
function refreshOvernightSlotSummary() {
  const keepEl = $('set_overnight_keep_slots');
  const perEl = $('set_overnight_max_per_strategy');
  const jonggaEl = $('set_jongga_max_slots');
  const preview = $('overnightJonggaSlotsPreview');
  const out = $('overnightSlotSummary');
  const keep = Math.max(0, parseInt(keepEl?.value || '3', 10) || 0);
  const per = Math.max(1, parseInt(perEl?.value || '1', 10) || 1);
  const jongga = Math.max(0, parseInt(jonggaEl?.value || preview?.value || '1', 10) || 0);
  if (preview) preview.value = String(jongga);
  const total = keep + jongga;
  if (out) {
    out.innerHTML = `장마감 후 목표: 당일 종가배팅 <b>${jongga}</b> + 그 외 <b>${keep}</b> = <b>최대 ${total}종목</b> · 전략당 ${per}개 · 익절·큰 손실부터 정리 · 종가배팅 익일 플러스·사흘째는 청산`;
  }
}
function bindOvernightSlotPreview() {
  ['set_overnight_keep_slots', 'set_overnight_max_per_strategy', 'set_jongga_max_slots'].forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener('input', refreshOvernightSlotSummary);
    el.addEventListener('change', refreshOvernightSlotSummary);
  });
  refreshOvernightSlotSummary();
}
function bindTradeTimePreview() {
  const ids = [
    'set_trade_start_time', 'set_trade_end_time',
    'set_sangtta_trade_start_time', 'set_sangtta_trade_end_time',
    'set_breakout_trade_start_time', 'set_breakout_trade_end_time',
    'set_jongga_trade_start_time', 'set_jongga_pick_end_time', 'set_jongga_trade_end_time',
    'set_jongga_leg2_start_time', 'set_jongga_leg3_start_time', 'set_jongga_leg3_end_time',
    'set_fractal_trade_start_time', 'set_fractal_trade_end_time',
    'set_ma1592_trade_start_time', 'set_ma1592_trade_end_time',
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
  const fr = $('set_use_fractal');
  if (fr) fr.addEventListener('change', refreshEngineSessionDisplay);
  const m15 = $('set_use_ma1592');
  if (m15) m15.addEventListener('change', refreshEngineSessionDisplay);
  const leg = $('set_use_legacy');
  if (leg) leg.addEventListener('change', refreshEngineSessionDisplay);
  const sang = $('set_use_sangtta');
  if (sang) sang.addEventListener('change', refreshEngineSessionDisplay);
  const jg = $('set_use_jongga');
  if (jg) jg.addEventListener('change', refreshEngineSessionDisplay);
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
    const buyWindow = mon.linked_session_window
      || mon.auto_trade_scanner?.session_window
      || mon.buy_executor?.session_window
      || rt.linked_session_window
      || linkedSessionWindow(settings);
    const slWindow = stopLoss.stop_loss_session_window
      || mon.stop_loss_session_window
      || rt.stop_loss_session_window
      || stopLoss.session_window
      || '08:00~19:30';
    const engineHours = buyWindow || '설정 없음';
    let html = '';
    html += statusRow('자동매매', autoOn ? '활성' : '비활성', autoOn ? 'on' : 'off', '설정 ON/OFF');
    html += statusRow('종목 스캔', scanRunning ? '실행중' : '중지', scanRunning ? 'run' : 'off', engineHours);
    html += statusRow('손절/익절 모니터링', slRunning ? '실행중' : '중지', slRunning ? 'run' : 'off', slWindow);
    html += statusRow('매수 실행기', buyRunning ? '실행중' : '중지', buyRunning ? 'run' : 'off', engineHours);
    if ($('statusBody')) $('statusBody').innerHTML = html;
    if ($('statusTime')) $('statusTime').textContent = mon.timestamp ? new Date(mon.timestamp).toLocaleTimeString('ko-KR') : '';
    updateApiLimitBadge(
      activity.api_rate_limit,
      activity.api_traffic,
      (activity.runtime || {}).scan_load,
    );
  } catch (e) {
    if ($('statusBody')) $('statusBody').innerHTML = emptyRow('상태를 불러오지 못했습니다.', '⚠️');
  }
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
        <div class="kv"><span class="k">키움 테마</span><span class="v">${esc(fmtAt(d.kiwoom_snapshot_last_at))} · ${esc((d.kiwoom_today||{}).themes||0)}테마/${esc((d.kiwoom_today||{}).edges||0)}편입</span></div>
        <div class="kv"><span class="k">알파스퀘어 테마</span><span class="v">${esc(fmtAt(d.alphasquare_snapshot_last_at))} · ${esc((d.alphasquare_today||{}).themes||0)}테마/${esc((d.alphasquare_today||{}).edges||0)}편입</span></div>
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

function renderPosAvgDownBtn(p) {
  if (p.manual_avg_down_done) return '';
  const disabled = !!p.pending_sell;
  const amount = Number(p.manual_avg_down_amount || 0);
  const pct = Number(p.manual_avg_down_pct || 50);
  return `<button type="button" class="btn sm pos-avg-down-btn" data-pos-id="${p.id}" data-amount="${amount}" data-pct="${pct}"${disabled ? ' disabled title="청산 주문 진행 중"' : ''}>물타기 1회</button>`;
}

function renderPosActionButtons(p) {
  const code = normStockCode(p.stock_code);
  const analysisBtn = code ? analysisLinkHtml(code) : '';
  return `${analysisBtn}${renderPosAvgDownBtn(p)}${renderPosSellBtn(p)}`;
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
  jongga: '종가배팅',
  fractal: '프랙탈',
  ma1592: '15/92홀드',
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

/** 상따·돌파 SOFT 연속 확인 · 레거시·돌파·상따 EMA SOFT 분 (exit_levels.*) */
function softConfirmMeta(p) {
  const key = String(p.strategy_key || '').trim().toLowerCase();
  const ex = p.exit_levels || {};
  const isLegacy = !key || key === 'legacy' || key === 'scanner' || key === 'screener' || key === 'condition' || key === 'both' || key === 'watchlist';
  const usesEma = isLegacy || key === 'breakout' || key === 'sangtta';

  const emaSoft = () => {
    if (!usesEma || !ex.legacy_ema_soft_min) return null;
    const polls = parseInt(ex.legacy_ema_soft_min, 10);
    const count = parseInt(ex.legacy_ema_consecutive, 10);
    if (!Number.isFinite(polls) || polls <= 0) return null;
    const n = Number.isFinite(count) && count > 0 ? count : 0;
    const period = parseInt(ex.legacy_ema_period, 10) || 90;
    return {
      count: n,
      polls,
      label: ex.legacy_ema_label || `EMA${period} 이탈`,
      text: `${n}/${polls}분`,
      active: n > 0 || !!ex.legacy_ema_below,
    };
  };

  const structSoft = () => {
    if (key !== 'sangtta' && key !== 'breakout') return null;
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
  };

  if (key === 'sangtta' || key === 'breakout') {
    const soft = structSoft();
    if (soft && soft.active) return soft;
    const ema = emaSoft();
    if (ema && ema.active) return ema;
    return soft || ema;
  }
  return emaSoft();
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
      <div class="pos-card-actions">${renderPosActionButtons(p)}</div>
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
      <td class="pos-action-cell">${renderPosActionButtons(p)}</td></tr>`;
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

async function manualAvgDownPosition(positionId, stockName, amount, pct) {
  const label = stockName || `포지션 #${positionId}`;
  const amountText = amount > 0 ? `${num(amount)}원` : '설정 비율 금액';
  if (!confirm(`${label}에 ${amountText}(최초 매수금의 ${pct}%) 물타기를 주문할까요?\n\n포지션당 1회만 가능하며 주문 후 버튼이 사라집니다.`)) return false;
  const res = await fetch(`/positions/${positionId}/manual-avg-down`, { method: 'POST', headers: { Accept: 'application/json' } });
  let data = {};
  try { data = await res.json(); } catch (_) { /* empty */ }
  if (!res.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === 'string' ? detail : (Array.isArray(detail) ? detail.map((d) => d.msg || d).join(', ') : `${res.status} 물타기 실패`));
  }
  toast(data.message || '물타기 주문을 접수했습니다.');
  return true;
}

function bindPositionSellButtons() {
  const onClick = async (e) => {
    const btn = e.target.closest('.pos-sell-btn, .pos-avg-down-btn');
    if (!btn || btn.disabled) return;
    const card = btn.closest('.pos-card, tr');
    const name = card?.querySelector('.name, .stock-name')?.textContent?.trim();
    const id = btn.dataset.posId;
    btn.disabled = true;
    const prev = btn.textContent;
    btn.textContent = '주문 중...';
    try {
      const isAvgDown = btn.classList.contains('pos-avg-down-btn');
      const ok = isAvgDown
        ? await manualAvgDownPosition(id, name, Number(btn.dataset.amount || 0), Number(btn.dataset.pct || 50))
        : await manualLiquidatePosition(id, name);
      if (ok) {
        loadPositions(true, { silent: true });
        loadActivity();
        loadLog();
      } else {
        btn.disabled = false;
        btn.textContent = prev;
      }
    } catch (err) {
      toast(err.message || '주문 실패', true);
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
let positionsLiveInFlight = 0;

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
  // live 갱신 중 DB 폴링이 seq를 올리면 live 결과가 버려져 "새로고침 무반응"처럼 보임
  if (!effectiveLive && positionsLiveInFlight > 0) return;

  const seq = ++positionsLoadSeq;
  if (effectiveLive) positionsLiveInFlight += 1;
  const sk = '<div class="skeleton">현재가·ATR 조회 중...</div>';
  if (effectiveLive && !silent && !$('autoPositionsBody')?.querySelector('.pos-card')) {
    if ($('positionsBody')) $('positionsBody').innerHTML = sk;
    if ($('autoPositionsBody')) $('autoPositionsBody').innerHTML = sk;
  }
  const refreshBtn = !silent && effectiveLive ? $('autoPosRefresh') : null;
  const refreshPrev = refreshBtn ? refreshBtn.textContent : '';
  if (refreshBtn) {
    refreshBtn.disabled = true;
    refreshBtn.textContent = '갱신 중…';
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
      if ($('lastUpdated')) $('lastUpdated').textContent = new Date().toLocaleTimeString('ko-KR');
      if (!silent) toast(`보유 ${items.length}종목 현재가 갱신 완료`);
    }
  } catch (e) {
    const msg = effectiveLive ? '현재가/ATR 갱신 실패 — 잠시 후 다시 시도하세요.' : '포지션을 불러오지 못했습니다.';
    if ($('autoPositionsBody') && !silent) $('autoPositionsBody').innerHTML = emptyRow(msg, '⚠️');
    if (!silent && $('positionsBody')) $('positionsBody').innerHTML = emptyRow(msg, '⚠️');
    if (effectiveLive && !silent) toast('보유종목 갱신 실패', true);
  } finally {
    if (effectiveLive) positionsLiveInFlight = Math.max(0, positionsLiveInFlight - 1);
    if (refreshBtn) {
      refreshBtn.disabled = false;
      refreshBtn.textContent = refreshPrev || '새로고침';
    }
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
    const amtOrDash = (v) => (v == null || v === '' ? '-' : `${num(v)}원`);
    const rows = items.map(o => {
      const rate = parseNum(o.profit_loss_rate), pl = parseNum(o.profit_loss);
      const gross = o.gross_profit_loss;
      const when = tradeWhen(sellTradeTs(o), true);
      const buyWhen = tradeWhen(o.buy_time, true);
      // 매수·매도 같은 날이면 시각만, 다른 날이면 MM.DD HH:MM (좌측 일자는 매도 기준)
      const buyCell = o.buy_time
        ? (buyWhen.ymd && when.ymd && buyWhen.ymd === when.ymd
          ? buyWhen.time
          : (buyWhen.day === '어제' ? `어제 ${buyWhen.time}` : buyWhen.shortCross))
        : '-';
      return `<tr><td>${esc(when.day)}</td>
        <td title="${esc(buyWhen.full)}">${esc(buyCell)}</td>
        <td title="매도">${esc(when.time)}</td>
        <td><span class="stock-name">${esc(o.stock_name)}</span><span class="stock-code">${esc(o.stock_code)}</span></td>
        <td class="num">${o.buy_price != null ? num(o.buy_price) : '-'}</td>
        <td class="num">${num(o.sell_price)}</td>
        <td class="num">${num(o.sell_quantity)}</td>
        <td class="num ${gross != null ? signClass(gross) : ''}">${gross != null ? pnlStr(gross) : '-'}</td>
        <td class="num cost">${amtOrDash(o.trading_commission)}</td>
        <td class="num cost">${amtOrDash(o.transaction_tax)}</td>
        <td class="num ${signClass(pl)}">${pnlStr(pl)}</td>
        <td class="num ${signClass(rate)}">${rateStr(rate)}</td>
        <td>${esc(reasonLabel(o.sell_reason, pl, rate))}</td></tr>`;
    }).join('');
    $('sellsBody').innerHTML = `<table class="tbl"><thead><tr>
      <th>일자</th><th title="포지션 매수 시각">매수</th><th title="매도 체결 시각">매도</th><th>종목</th>
      <th class="num" title="포지션 매수 평균단가">매수가</th>
      <th class="num">매도가</th>
      <th class="num">수량</th>
      <th class="num" title="(매도가 − 매수가) × 수량">매매차익</th>
      <th class="num" title="매수·매도 수수료 합계 (키움 동기화)">수수료</th>
      <th class="num" title="증권거래세 (키움 동기화)">거래세</th>
      <th class="num" title="수수료·거래세 차감 후. 미동기화면 매매차익과 같음">순손익</th>
      <th class="num">수익률</th>
      <th>사유</th>
    </tr></thead><tbody>${rows}</tbody></table>`;
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
  fractal: '프랙탈 스캘핑',
  ma1592: '15/92홀드',
  oversold_breakout: '수급 돌파',
  sangtta_breakout: '상따',
  yeokmaegongpa: '역매공파',
  jongga_closing: '종가배팅',
  legacy_momentum: '거래대금 눌림목',
  ema_fractal_pullback: '프랙탈 스캘핑',
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
  const pl = parseNum(o.profit_loss);
  const rate = parseNum(o.profit_loss_rate);
  if (o.status === 'FAILED') return o.sell_reason_detail || o.sell_order_id || reasonLabel(o.sell_reason, pl, rate) || '사유 미기록';
  if (o.status === 'CANCELLED') return o.sell_reason_detail || '주문 취소(만료·중복 정리)';
  return reasonLabel(o.sell_reason, pl, rate) || o.sell_reason_detail || '-';
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

function _curveDate(pt) {
  const d = String(pt?.date || '');
  if (d.length >= 10) return d.slice(0, 10);
  const ts = String(pt?.ts || '');
  return ts.length >= 10 ? ts.slice(0, 10) : '';
}

/** 건별 누적 곡선의 고점 대비 최대 낙폭. */
function _mddOnCurve(data) {
  let peak = 0;
  let peakIdx = 0;
  let mddPeakIdx = 0;
  let troughIdx = -1;
  let mdd = 0;
  (data || []).forEach((v, i) => {
    if (v >= peak) {
      peak = v;
      peakIdx = i;
    }
    const dd = v - peak;
    if (dd < mdd) {
      mdd = dd;
      mddPeakIdx = peakIdx;
      troughIdx = i;
    }
  });
  return { peakIdx: mddPeakIdx, troughIdx, mdd };
}

function applyCurveMdd(d) {
  const curve = d.curve || [];
  const data = curve.map((c) => c.cum);
  const vis = _mddOnCurve(data);
  if (!(vis.mdd < 0) || vis.troughIdx < 0) return vis;
  d.mdd = Math.round(vis.mdd);
  const seed = Number(d.base_asset || d.seed) || 0;
  const peakEq = seed + (data[vis.peakIdx] || 0);
  d.mdd_pct = peakEq ? Math.round((vis.mdd / peakEq) * 10000) / 100 : 0;
  d.mdd_peak_date = _curveDate(curve[vis.peakIdx]);
  d.mdd_trough_date = _curveDate(curve[vis.troughIdx]);
  return vis;
}

function _roundRect(c, x, y, w, h, r) {
  const rad = Math.min(r, w / 2, h / 2);
  c.beginPath();
  c.moveTo(x + rad, y);
  c.arcTo(x + w, y, x + w, y + h, rad);
  c.arcTo(x + w, y + h, x, y + h, rad);
  c.arcTo(x, y + h, x, y, rad);
  c.arcTo(x, y, x + w, y, rad);
  c.closePath();
}

function _drawPinnedTooltip(c, chartArea, x, y, title, valueText, color) {
  const padX = 10;
  const padY = 8;
  c.font = '600 11px ui-sans-serif, system-ui, sans-serif';
  const tw1 = c.measureText(title).width;
  c.font = '600 12px ui-sans-serif, system-ui, sans-serif';
  const tw2 = c.measureText(valueText).width;
  const sw = 8;
  const innerW = Math.max(tw1, sw + 6 + tw2);
  const boxW = innerW + padX * 2;
  const boxH = 38;
  let bx = x - boxW / 2;
  let by = y - boxH - 10;
  if (bx < chartArea.left + 2) bx = chartArea.left + 2;
  if (bx + boxW > chartArea.right - 2) bx = chartArea.right - boxW - 2;
  if (by < chartArea.top + 2) by = Math.min(y + 12, chartArea.bottom - boxH - 2);
  c.fillStyle = '#1a1f2b';
  c.strokeStyle = '#2f3647';
  c.lineWidth = 1;
  _roundRect(c, bx, by, boxW, boxH, 6);
  c.fill();
  c.stroke();
  c.fillStyle = '#8b95a8';
  c.font = '600 11px ui-sans-serif, system-ui, sans-serif';
  c.fillText(title, bx + padX, by + padY + 10);
  c.fillStyle = color;
  c.fillRect(bx + padX, by + padY + 18, sw, sw);
  c.fillStyle = '#e8edf5';
  c.font = '600 12px ui-sans-serif, system-ui, sans-serif';
  c.fillText(valueText, bx + padX + sw + 6, by + padY + 26);
}

function drawPerfChart(curve, stats) {
  const ctx = $('perfChart'); if (!ctx || !window.Chart) return;
  const data = (curve || []).map((c) => c.cum);
  const labels = (curve || []).map((c) => {
    const d = _curveDate(c);
    return d.length >= 10 ? d.slice(5) : (d || '');
  });
  const up = data.length && data[data.length - 1] >= 0;
  const color = up ? '#34d399' : '#f87171';
  const fillColor = up ? 'rgba(52,211,153,0.12)' : 'rgba(248,113,113,0.12)';
  const ath = data.length ? Math.max(0, Math.max.apply(null, data)) : 0;
  const athLine = data.map(() => ath);
  const vis = _mddOnCurve(data);
  const peakIdx = vis.peakIdx;
  const troughIdx = vis.troughIdx;
  const showMdd = vis.mdd < 0 && troughIdx >= 0;
  const mddMarks = data.map((_, i) => {
    if (!showMdd) return null;
    if (i === peakIdx || i === troughIdx) return data[i];
    return null;
  });
  if (perfChart) perfChart.destroy();
  perfChart = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets: [
      { data, borderColor: color, backgroundColor: fillColor, fill: true, tension: 0, pointRadius: 0, borderWidth: 2, order: 2 },
      { data: athLine, borderColor: '#3f4859', borderWidth: 1.5, borderDash: [6, 5], fill: false, pointRadius: 0, order: 3 },
      {
        data: mddMarks,
        borderColor: '#f87171',
        backgroundColor: '#f87171',
        showLine: false,
        pointRadius: showMdd ? 4.5 : 0,
        pointHoverRadius: 5,
        order: 1,
      },
    ] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          filter: (c) => {
            if (showMdd && c.dataIndex === troughIdx) return false;
            return c.datasetIndex === 0 || (c.datasetIndex === 2 && c.raw != null);
          },
          backgroundColor: '#1a1f2b', borderColor: '#2f3647', borderWidth: 1,
          titleColor: '#8b95a8', bodyColor: '#e8edf5',
          callbacks: {
            title: (items) => _curveDate(curve[items?.[0]?.dataIndex]) || '',
            label: (c) => num(c.parsed.y) + '원',
          },
        },
      },
      layout: { padding: { top: 6, right: 8, bottom: 0, left: 2 } },
      scales: {
        x: {
          display: true,
          ticks: {
            color: '#7a8496',
            font: { size: 10 },
            maxRotation: 0,
            autoSkip: true,
            maxTicksLimit: 6,
            padding: 4,
          },
          grid: { color: 'rgba(127,132,150,0.10)', drawTicks: false },
          border: { color: '#2f3647' },
        },
        y: {
          display: true,
          ticks: {
            color: '#7a8496',
            font: { size: 10 },
            maxTicksLimit: 5,
            padding: 4,
            callback: (v) => {
              const man = Number(v) / 10000;
              if (!Number.isFinite(man)) return '';
              if (Math.abs(man) >= 10) return `${Math.round(man)}만`;
              if (Math.abs(man) >= 1) return `${man.toFixed(1).replace(/\.0$/, '')}만`;
              return `${Math.round(v / 1000)}천`;
            },
          },
          grid: {
            color: (ctx) => (ctx.tick && ctx.tick.value === 0
              ? 'rgba(139,149,168,0.38)'
              : 'rgba(127,132,150,0.10)'),
            drawTicks: false,
          },
          border: { color: '#2f3647' },
        },
      },
    },
    plugins: [{
      id: 'mddPinnedTip',
      afterDraw(chart) {
        if (!showMdd) return;
        const { ctx: c, chartArea, scales } = chart;
        if (!chartArea || !scales.x || !scales.y) return;
        const x = scales.x.getPixelForValue(troughIdx);
        const y = scales.y.getPixelForValue(data[troughIdx]);
        const title = _curveDate(curve[troughIdx]) || '';
        c.save();
        _drawPinnedTooltip(c, chartArea, x, y, title, num(data[troughIdx]) + '원', '#f87171');
        c.restore();
      },
    }],
  });
}

/* ===== 실시간 활동 로그 ===== */
const SCAN_STRATEGY_ORDER = ['legacy', 'sangtta', 'breakout', 'fractal', 'jongga', 'ma1592'];

const ACTIVITY_STRATEGY_FILTERS = [
  { key: 'all', label: '전체' },
  { key: 'legacy', label: '레거시' },
  { key: 'sangtta', label: '상따' },
  { key: 'breakout', label: '돌파' },
  { key: 'fractal', label: '프랙탈' },
  { key: 'jongga', label: '종가배팅' },
  { key: 'ma1592', label: '15/92' },
  { key: 'system', label: '시스템' },
];

const ACTIVITY_MSG_STRATEGY_PATTERNS = [
  { key: 'ma1592', re: /\[MA1592\]|15\/90\s*홀드|\[15\/90\s*홀드\]|장부\s*편입/i },
  { key: 'sangtta', re: /\[상따\]/ },
  { key: 'breakout', re: /\[돌파\]|수급\s*돌파/ },
  { key: 'fractal', re: /\[프랙탈\]|프랙탈\s*스캘핑/ },
  { key: 'jongga', re: /\[종가배팅\]|종가배팅/ },
  { key: 'legacy', re: /거래대금\s*눌림목|레거시/ },
];

let activityStrategyFilter = 'all';
let _activityEventsCache = [];

function syncActivityFilterButtons() {
  const root = $('activityFilters');
  if (!root) return;
  root.querySelectorAll('[data-filter]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.filter === activityStrategyFilter);
  });
}

function setActivityStrategyFilter(filterKey) {
  activityStrategyFilter = filterKey || 'all';
  syncActivityFilterButtons();
  const body = $('activityBody');
  if (body) {
    body.innerHTML = renderActivityEvents(_activityEventsCache, activityStrategyFilter);
  }
}

function bindActivityFilters() {
  const root = $('activityFilters');
  if (!root || root.dataset.bound === '1') return;
  root.dataset.bound = '1';
  root.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-filter]');
    if (!btn || !root.contains(btn)) return;
    e.preventDefault();
    setActivityStrategyFilter(btn.dataset.filter);
  });
}

function inferActivityStrategyKey(e) {
  const explicit = String(e?.strategy || '').trim().toLowerCase();
  if (explicit) return explicit;
  const msg = String(e?.message || '');
  for (const { key, re } of ACTIVITY_MSG_STRATEGY_PATTERNS) {
    if (re.test(msg)) return key;
  }
  if (/진입\s*보류/.test(msg) && !/\[(상따|돌파|프랙탈|종가배팅|MA1592)\]/.test(msg)) {
    return 'legacy';
  }
  return '';
}

function isSystemActivityEvent(e) {
  const src = String(e?.source || '').toUpperCase();
  return src === 'SYNC' || src === 'SYSTEM';
}

function activityEventMatchesFilter(e, filterKey) {
  if (!filterKey || filterKey === 'all') return true;
  if (filterKey === 'system') return isSystemActivityEvent(e);
  return inferActivityStrategyKey(e) === filterKey;
}

function activityStrategyLabel(key) {
  if (!key) return '';
  return STRATEGY_LABEL[key] || key;
}

function renderActivityEvents(events, filterKey) {
  const filtered = (events || []).filter((e) => activityEventMatchesFilter(e, filterKey));
  if (!filtered.length) {
    const label = ACTIVITY_STRATEGY_FILTERS.find((f) => f.key === filterKey)?.label || filterKey;
    const hint = filterKey === 'all'
      ? '활동 이벤트 없음 — 서버 재시작 직후이거나 아직 한 사이클이 돌지 않았습니다.'
      : `「${label}」 필터에 해당하는 로그가 없습니다.`;
    return `<div class="activity-line info"><span class="msg">${esc(hint)}</span></div>`;
  }
  return filtered.map((e) => {
    const t = e.ts ? e.ts.replace('T', ' ').slice(11, 19) : '--:--:--';
    const lvl = e.level || 'info';
    const stratKey = inferActivityStrategyKey(e);
    const stratHtml = stratKey
      ? `<span class="strat">[${esc(activityStrategyLabel(stratKey))}]</span>`
      : '';
    return `<div class="activity-line ${lvl}"><span class="ts">${t}</span><span class="src">[${esc(e.source || '?')}]</span>${stratHtml}<span class="msg">${esc(e.message || '')}</span></div>`;
  }).join('');
}

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
      const scanLoad = rt.scan_load || {};
      const apiRl = d.api_rate_limit || {};
      const traffic = d.api_traffic || {};
      const byStrat = rt.last_scan_by_strategy || {};
      const stratBits = SCAN_STRATEGY_ORDER
        .map((k) => byStrat[k])
        .filter((x) => x && (x.targets > 0 || x.created > 0))
        .map((x) => `${x.label || '?'} ${x.targets || 0}/${x.created || 0}`);
      let scanInfo;
      if (scanLoad.in_progress) {
        const cur = scanLoad.current_name || scanLoad.current_code || '';
        const eta = scanLoad.eta_sec != null && Number(scanLoad.eta_sec) > 0
          ? ` · ETA≈${Math.round(Number(scanLoad.eta_sec))}초`
          : '';
        const pause = scanLoad.gate_pause_sec != null
          ? ` · 게이트 ${scanLoad.gate_pause_sec}초`
          : '';
        scanInfo = `스캔 진행 ${scanLoad.scanned || 0}/${scanLoad.targets_total || 0}`
          + ` (남음 ${scanLoad.remaining || 0}${eta}${pause})`
          + (cur ? ` · 현재 ${esc(cur)}` : '')
          + (stratBits.length ? `<br><span class="hint">전략별 대상/신호: ${stratBits.map(esc).join(' · ')}</span>` : '');
      } else {
        const lastScan = fmtScanTime(rt.last_scan_at);
        const dur = rt.last_scan_duration_sec != null
          ? ` · ${rt.last_scan_duration_sec}초 소요`
          : '';
        scanInfo = rt.last_scan_at
          ? `마지막 스캔 ${lastScan} · 대상 ${rt.last_scan_targets || 0} · 신호 ${rt.last_scan_created || 0}${dur}`
            + (stratBits.length ? `<br><span class="hint">전략별 대상/신호: ${stratBits.map(esc).join(' · ')}</span>` : '')
          : (enabled ? '아직 스캔 없음 (1분 주기)' : '자동매매 OFF — 스캔 미실행');
      }
      const syncInfo = stopActive
        ? (rt.last_sync_at
          ? `마지막 동기화 ${fmtScanTime(rt.last_sync_at)} (${rt.monitor_interval_sec || 30}초 주기)`
          : `포지션 동기화 대기 (${rt.monitor_interval_sec || 30}초 주기)`)
        : '장외 — 손절/익절 모니터 일시 중지 (08:00~19:30 외, 다음 세션 재개)';
      const recent = Number(apiRl.recent_calls || 0);
      const maxC = Number(apiRl.max_calls_per_window || 0);
      const usage = Math.round(Number(apiRl.usage_percent || 0));
      let deferHint = 'live 정상';
      if (traffic.defer_dashboard_live) {
        if (traffic.defer_reason === 'scan') deferHint = 'live 지연 (스캔 중 · 종목수 무관, 스캔 시작 즉시)';
        else if (traffic.defer_reason === 'post_scan_burst') {
          deferHint = `live 지연 (스캔직후 ${Math.ceil(Number(traffic.defer_remaining_sec || 0))}초)`;
        } else deferHint = 'live 지연';
      }
      const loadInfo = maxC
        ? `부하 · 키움 ${recent}/${maxC} (${usage}%) · ${deferHint}`
        : `부하 · ${deferHint}`;
      banner.innerHTML = `
        <div class="ab-item"><span class="ab-dot ${allRunning ? 'pulse' : ''}"></span>
          <strong>${allRunning ? '자동매매 실행 중' : (enabled ? '일부 중지됨' : '자동매매 OFF · 동기화만')}</strong></div>
        <div class="ab-item">${runtimeBadge(rt.scanner_running, '스캐너')}${runtimeBadge(rt.buy_executor_running, '매수')}${runtimeBadge(stopActive, '동기화')}</div>
        <div class="ab-item hint">${syncInfo}</div>
        <div class="ab-item hint">${scanInfo}</div>
        <div class="ab-item hint">${loadInfo}</div>
        <div class="ab-item hint">${rt.is_trading_day === false ? esc(rt.trading_day_block_reason || '휴장') : (rt.in_trade_hours ? '장중' : '장외')} · ${rt.allows_new_buy === false ? esc(rt.new_buy_block_reason || '매수 차단') : '매수 허용'}${rt.mock_mode ? ' · 모의' : ''}</div>`;
    }
    const events = d.events || [];
    _activityEventsCache = events;
    updateApiLimitBadge(d.api_rate_limit, d.api_traffic, rt.scan_load);
    syncActivityFilterButtons();
    const body = $('activityBody');
    if (!body) return;
    if (!events.length) {
      const rt = d.runtime || {};
      let hint = '활동 이벤트 없음 — 서버 재시작 직후이거나 아직 한 사이클이 돌지 않았습니다.';
      if (stopActive) {
        hint = `포지션 동기화 루프 실행 중 (${rt.monitor_interval_sec || 30}초마다 [SYNC] 로그). 자동매매 ON 시 [SCANNER]/[BUY] 로그도 표시됩니다.`;
      } else if (rt.stop_loss_loop_alive) {
        hint = '장외 — 손절/익절 모니터 일시 중지. 거래일 08:00~19:30에 자동 재개됩니다.';
      }
      body.innerHTML = `<div class="activity-line info"><span class="msg">${esc(hint)}</span></div>`;
      return;
    }
    body.innerHTML = renderActivityEvents(events, activityStrategyFilter);
  } catch (e) {
    const body = $('activityBody');
    if (body) body.innerHTML = '<div class="activity-line error"><span class="msg">활동 로그를 불러오지 못했습니다.</span></div>';
  }
}

/* ===== 자동매매 로그 ===== */
function logReasonForSell(o) {
  const pl = parseNum(o.profit_loss);
  const rate = parseNum(o.profit_loss_rate);
  if (o.status === 'FAILED') {
    return o.sell_reason_detail || o.sell_order_id || reasonLabel(o.sell_reason, pl, rate) || '사유 미기록';
  }
  return reasonLabel(o.sell_reason, pl, rate) || o.sell_reason_detail || '';
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

function logTradeDateKst(ts) {
  const d = parseDbUtc(ts);
  return d ? kstYmd(d) : null;
}

/** 체결 로그 → 검증: 체결된 매도(익절·손절·트레일·수익잠금·장마감 등) */
const LOG_VERIFY_SELL_REASONS = new Set([
  'STOP_LOSS', 'TAKE_PROFIT', 'TRAILING', 'PROFIT_LOCK', 'MARKET_CLOSE',
]);
function isLogVerifyEligibleSell(o, filled) {
  if (!filled) return false;
  const raw = String(o.sell_reason || '').trim().toUpperCase();
  if (LOG_VERIFY_SELL_REASONS.has(raw)) return true;
  const label = reasonLabel(o.sell_reason, o.profit_loss, o.profit_loss_rate);
  return (
    label.startsWith('익절')
    || label.startsWith('손절')
    || label.startsWith('장마감')
  );
}

function verifyPageUrl(code, ts) {
  const norm = String(code || '').replace(/^A/i, '').split('_')[0];
  const url = new URL('/verify', window.location.origin);
  const date = logTradeDateKst(ts);
  if (date) url.searchParams.set('date', date);
  if (norm) url.searchParams.set('code', norm);
  return url.pathname + url.search;
}

function analysisPageUrl(code) {
  const raw = String(code || '').trim().replace(/^A/i, '');
  const norm = /^\d+$/.test(raw) ? raw.padStart(6, '0') : raw;
  const url = new URL('/static/analysis.html', window.location.origin);
  if (norm) url.searchParams.set('code', norm);
  return url.pathname + url.search;
}

function analysisLinkHtml(code, { label = '분석', title = '기본적분석에서 보기' } = {}) {
  const norm = normStockCode(code);
  if (!norm) return '—';
  return `<a href="${esc(analysisPageUrl(norm))}" class="btn sm analysis-link" title="${esc(title)}">${esc(label)}</a>`;
}

function openVerifyPage(code, ts) {
  const href = verifyPageUrl(code, ts);
  // 사용자 제스처 유지: <a> 클릭이 window.open 팝업 차단에 더 강함
  const a = document.createElement('a');
  a.href = href;
  a.target = '_blank';
  a.rel = 'noopener';
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function bindLogVerifyNavigation() {
  if (document.documentElement.dataset.logVerifyBound) return;
  document.documentElement.dataset.logVerifyBound = '1';
  // logBody가 교체돼도 동작하도록 document 위임
  document.addEventListener('dblclick', (e) => {
    const row = e.target && e.target.closest && e.target.closest('#logBody .log-verify-row');
    if (!row) return;
    const code = row.getAttribute('data-code') || row.dataset.code;
    const ts = row.getAttribute('data-ts') || row.dataset.ts;
    if (!code) return;
    e.preventDefault();
    openVerifyPage(code, ts);
  });
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
        verifyEligible: isLogVerifyEligibleSell(o, filled),
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
        verifyEligible: false,
      });
    }
    logs = logs.filter(l => l.ts && isWithinLogDays(l.ts, days, true)).sort((a, b) => {
      const ta = parseDbUtc(a.ts);
      const tb = parseDbUtc(b.ts);
      return (tb || 0) - (ta || 0);
    });
    let pnlSum = 0;
    let pnlN = 0;
    for (const l of logs) {
      if (l.action !== '매도' || l.pl == null || l.pl === '') continue;
      pnlSum += parseNum(l.pl);
      pnlN += 1;
    }
    const pnlHint = pnlN
      ? ` · 손익합산 <span class="${signClass(pnlSum)}">${pnlStr(pnlSum)}</span> (${pnlN}건)`
      : '';
    $('logCount').innerHTML = `${logs.length}건 · 최근 ${days}일${pnlHint}`;
    if (!logs.length) {
      $('logBody').innerHTML = emptyRow(`최근 ${days}일 체결 내역이 없습니다.`, '🧾');
      return;
    }
    const stateP = (s) => s === '성공' ? 'on' : (s === '실패' ? 'off' : 'run');
    const actC = (a) => a === '매수' ? 'up' : 'down';
    const rows = logs.map((l) => {
      const verifyRow = l.verifyEligible
        ? ` class="log-verify-row" data-code="${esc(l.code)}" data-ts="${esc(l.ts)}" title="더블클릭: 검증 페이지"`
        : '';
      return `<tr${verifyRow}>
      <td>${dtDb(l.ts, true)}</td><td class="${actC(l.action)}" style="font-weight:700;">${esc(l.action)}</td>
      <td><span class="stock-name">${esc(l.name)}</span><span class="stock-code">${esc(l.code)}</span></td>
      <td><span class="hint">${esc(l.strategy || '—')}</span></td>
      <td class="num">${esc(l.qty)}</td><td><span class="pill ${stateP(l.state)}">${esc(l.state)}</span></td>
      <td class="num">${logPnlCell(l)}</td>
      <td style="white-space:normal;color:var(--muted);">${esc(l.reason)}</td></tr>`;
    }).join('');
    $('logBody').innerHTML = `<table class="tbl"><thead><tr><th>시각</th><th>동작</th><th>종목</th><th>전략</th><th class="num">수량</th><th>상태</th><th class="num">손익</th><th>사유</th></tr></thead><tbody>${rows}</tbody></table>`;
    bindLogVerifyNavigation();
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
    const rows = items.map((s, i) => {
      const rate = parseNum(s.change_rate);
      const amtEok = parseNum(s.trade_amount) / 100; // 백만원 → 억원
      return `<tr>
        <td class="num hint">${i + 1}</td>
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
    $('screenerBody').innerHTML = `<table class="tbl"><thead><tr><th class="num">#</th><th>종목</th><th>출처</th><th>테마</th><th>키워드</th><th>구분</th><th class="num">현재가</th><th class="num">등락률</th><th class="num">거래량</th><th class="num">거래대금</th><th class="num">시총</th><th class="num">PER</th><th class="num">PBR</th><th class="num">ROE</th></tr></thead><tbody>${rows}</tbody></table>`;
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



/* ===== 프랙탈 스캘핑 후보 ===== */
function renderFractalChecks(checks) {
  const src = checks && typeof checks === 'object' ? checks : {};
  const keys = [
    ['stack', '정배열'],
    ['pullback', '눌림'],
    ['buy_fractal', '프랙탈'],
    ['reclaim', '재돌파'],
    ['close_gt_ema20', '종가>EMA20'],
    ['close_gt_ema100', '종가>EMA100'],
  ];
  const chips = keys.map(([key, label]) => {
    if (!(key in src)) return '';
    const ok = !!src[key];
    const cls = ok ? 'gate-chip ok' : 'gate-chip bad';
    const mark = ok ? '✓' : '✗';
    return `<span class="${cls}"><b>${mark}</b> ${esc(label)}</span>`;
  }).filter(Boolean).join('');
  return chips ? `<div class="gate-checks">${chips}</div>` : '';
}

async function loadFractal() {
  if (!$('fractalBody')) return;
  $('fractalBody').innerHTML = '<div class="skeleton">스캘핑 후보와 1분 게이트 계산 중...</div>';
  if ($('fractalCount')) $('fractalCount').textContent = '';
  try {
    const d = await fetchJSON('/fractal/candidates', { timeoutMs: 120000 });
    if (!d.success) {
      $('fractalBody').innerHTML = emptyRow(d.error || d.detail || '조회 실패', '⚠️');
      return;
    }
    const items = d.items || [];
    const errHint = (d.errors && d.errors.length)
      ? ` · 조건식 오류: ${d.errors.join(', ')}`
      : '';
    if ($('fractalCount')) $('fractalCount').textContent = `후보 ${items.length}${errHint}`;
    if (!items.length) {
      const emptyMsg = (d.errors && d.errors.length)
        ? `조건식 조회 실패 (${d.errors.join(', ')})`
        : (d.message || '조건식 편입 종목이 없습니다.');
      $('fractalBody').innerHTML = emptyRow(emptyMsg, d.errors && d.errors.length ? '⚠️' : '⚡');
      return;
    }
    const statusLabel = { pass: '통과', wait: '대기', fail: '탈락' };
    const rows = items.map((s) => {
      const st = s.fractal_status || (s.gate_ok ? 'pass' : 'wait');
      const gateCls = st === 'pass' ? 'up' : (st === 'fail' ? 'down' : '');
      const emaHtml = (s.ema20 != null)
        ? `${num(s.ema20)}<div class="hint">${s.ema50 != null ? num(s.ema50) : '—'} / ${s.ema100 != null ? num(s.ema100) : '—'}</div>`
        : '—';
      const levels = (s.stop_price || s.take_profit_price)
        ? `${s.stop_price ? `손절 ${num(s.stop_price)}` : ''}${s.take_profit_price ? `<div class="hint">익절 ${num(s.take_profit_price)}</div>` : ''}`
        : '—';
      return `<tr>
        <td><b>${esc(s.stock_name || s.stock_code)}</b><div class="hint">${esc(s.stock_code)}</div></td>
        <td>${esc(s.condition_name || '—')}</td>
        <td class="${gateCls}"><span class="pill ${st === 'pass' ? 'on' : (st === 'fail' ? 'off' : 'run')}">${esc(statusLabel[st] || st)}</span>
          <div class="hint">${esc(s.gate_reason || '')}</div></td>
        <td class="num">${emaHtml}</td>
        <td>${levels}</td>
        <td>${renderFractalChecks(s.fractal_checks)}</td>
      </tr>`;
    }).join('');
    $('fractalBody').innerHTML = `<table class="tbl"><thead><tr><th>종목</th><th>조건식</th><th>게이트</th><th class="num">EMA20<div class="hint">50 / 100</div></th><th>손절·익절</th><th>체크</th></tr></thead><tbody>${rows}</tbody></table>`;
  } catch (e) {
    const msg = String(e && e.message || '');
    const hint = msg.includes('404')
      ? 'API 없음 — 서버를 재시작해 주세요'
      : (msg.includes('AbortError') || msg.includes('aborted')
        ? '조회 시간 초과 — 잠시 후 다시 시도'
        : `조회 실패${msg ? ` (${msg})` : ''}`);
    $('fractalBody').innerHTML = emptyRow(hint, '⚠️');
  }
}

/* ===== 1592매매(15/92) 장부 후보 ===== */
function ma1592GatePill(s) {
  if (s.gate_ok === true) return '<span class="pill on">돌파OK</span>';
  if (s.gate_ok === false) {
    const reason = esc(s.gate_reason || s.reason_code || '대기');
    return `<span class="pill">${reason}</span>`;
  }
  return '—';
}

function ma1592InMaHtml(s) {
  if (s.in_ma15 == null && s.in_ma92 == null) return '<span class="hint">편입 스냅 없음</span>';
  const in15 = s.in_ma15 != null ? num(s.in_ma15) : '—';
  const in92 = s.in_ma92 != null ? num(s.in_ma92) : '—';
  const gap = s.ema_gap_pct != null ? `<div class="hint">이평이격 ${s.ema_gap_pct}%</div>` : '';
  return `${in15}<div class="hint">${in92}</div>${gap}`;
}

function ma1592LedgerAtHtml(s, { watch = false } = {}) {
  if (watch) return '—';
  const at = s.ledger_at || s.in_at || s.gc_at;
  if (!at) return '—';
  const ts = esc(String(at).slice(0, 16).replace('T', ' '));
  const src = s.universe_source
    ? `<div class="hint">${esc(s.universe_source)}</div>`
    : '';
  const cond = s.condition_present === true
    ? '<div class="hint">조건편입중</div>'
    : (s.condition_present === false ? '<div class="hint">조건이탈</div>' : '');
  return `${ts}${src}${cond}`;
}

function ma1592RowHtml(s, { watch = false } = {}) {
  const statePill = (st) => {
    if (st === 'WAIT_HOLD' || st === 'GC_WATCH') return 'run';
    if (st === 'MANAGE_FULL' || st === 'MANAGE_HALF') return 'on';
    if (st === 'CONDITION_ONLY') return '';
    return '';
  };
  const ma1592Ma15Hint = (px, ma15) => {
    if (px == null || ma15 == null || !ma15) return '';
    const pct = ((Number(px) / Number(ma15) - 1) * 100);
    if (!Number.isFinite(pct)) return '';
    const sign = pct >= 0 ? '+' : '';
    return `<div class="hint">15선 ${sign}${pct.toFixed(2)}%</div>`;
  };
  const maHtml = (s.ma15 != null)
    ? `${num(s.ma15)}<div class="hint">${s.ma92 != null ? num(s.ma92) : '—'}</div>`
    : '—';
  const hold = (!watch && (s.state === 'GC_WATCH' || s.state === 'WAIT_HOLD'))
    ? `${s.hold_ok_bars ?? 0}봉<div class="hint">GC후 ${s.bars_since_gc ?? 0}</div>`
    : (s.tp1_filled ? 'TP1완료' : '—');
  const levelParts = [
    s.prev_high ? `전고 ${num(s.prev_high)}` : '',
    s.tp1_price ? `TP1 ${num(s.tp1_price)}` : '',
    s.entry_price ? `진입 ${num(s.entry_price)}` : '',
  ].filter(Boolean);
  const levels = levelParts.length
    ? `${levelParts[0]}${levelParts.slice(1).map((x) => `<div class="hint">${x}</div>`).join('')}`
    : '—';
  const expire = s.expire_date
    ? `<div class="hint">만료 ${esc(String(s.expire_date).slice(0, 10))}</div>`
    : '';
  const pxHtml = s.current_price != null
    ? `${num(s.current_price)}${ma1592Ma15Hint(s.current_price, s.ma15)}`
    : '—';
  const gcCell = watch
    ? '—'
    : (s.gc_price != null && s.gc_price > 0 ? num(s.gc_price) : '—');
  return `<tr>
    <td><a href="${esc(analysisPageUrl(s.stock_code))}" class="stock-analysis-link" title="기본적분석"><b>${esc(s.stock_name || s.stock_code)}</b><div class="hint">${esc(s.stock_code)}</div></a></td>
    <td class="pos-action-cell">${analysisLinkHtml(s.stock_code)}</td>
    <td><span class="pill ${statePill(s.state)}">${esc(s.state_label || s.state || '—')}</span>${expire}</td>
    <td class="num">${ma1592LedgerAtHtml(s, { watch })}</td>
    <td class="num">${pxHtml}</td>
    <td class="num">${ma1592InMaHtml(s)}</td>
    <td class="num">${maHtml}</td>
    <td>${ma1592GatePill(s)}</td>
    <td class="num">${gcCell}</td>
    <td class="num">${hold}</td>
    <td>${levels}</td>
  </tr>`;
}

async function loadMa1592() {
  if (!$('ma1592Body')) return;
  $('ma1592Body').innerHTML = '<div class="skeleton">15/92 장부 조회 중...</div>';
  if ($('ma1592Count')) $('ma1592Count').textContent = '';
  try {
    const d = await fetchJSON('/ma1592/candidates', { timeoutMs: 120000 });
    if (!d.success) {
      $('ma1592Body').innerHTML = emptyRow(d.error || d.detail || '조회 실패', '⚠️');
      return;
    }
    const items = d.items || [];
    const watch = d.watch || [];
    const cond = (d.condition_names || []).join(', ') || '1592매매';
    const off = d.use_ma1592 === false ? ' · 전략 OFF' : '';
    if ($('ma1592Count')) {
      $('ma1592Count').textContent =
        `장부 ${items.length}${d.l3_count != null ? ` · L3 ${d.l3_count}` : ''}`
        + `${d.watch_count != null ? ` · 조건만 ${d.watch_count}` : ''}`
        + ` · ${cond}${off}`;
    }
    if (!items.length && !watch.length) {
      $('ma1592Body').innerHTML = emptyRow(
        d.message || '장부·조건식에 종목이 없습니다.',
        '📐',
      );
      return;
    }
    const tableHead =
      `<table class="tbl"><thead><tr>`
      + `<th>종목</th><th>분석</th><th>장부상태</th><th class="num">장부편입<div class="hint">최신순</div></th>`
      + `<th class="num">현재가</th>`
      + `<th class="num">편입 EMA15<div class="hint">편입 EMA92</div></th>`
      + `<th class="num">현재 EMA15<div class="hint">현재 EMA92</div></th>`
      + `<th>게이트</th><th class="num">돌파가</th><th class="num">홀드</th><th>전고·TP</th>`
      + `</tr></thead><tbody>`;
    const ledgerRows = items.map((s) => ma1592RowHtml(s)).join('');
    const watchSection = watch.length
      ? `<tr><td colspan="11" class="hint" style="padding:10px 8px;background:var(--bg-soft,#f6f7f9);">`
        + `조건식 편입(장부 미등록) — 돌파 직후 이탈·EMA92 아래 등으로 장부에서 빠진 종목 확인</td></tr>`
        + watch.map((s) => ma1592RowHtml(s, { watch: true })).join('')
      : '';
    $('ma1592Body').innerHTML = `${tableHead}${ledgerRows}${watchSection}</tbody></table>`;
  } catch (e) {
    const msg = String(e && e.message || '');
    const hint = msg.includes('404')
      ? 'API 없음 — 서버를 재시작해 주세요'
      : (msg.includes('AbortError') || msg.includes('aborted')
        ? '조회 시간 초과 — 잠시 후 다시 시도'
        : `조회 실패${msg ? ` (${msg})` : ''}`);
    $('ma1592Body').innerHTML = emptyRow(hint, '⚠️');
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

function jonggaCurrentPrice(s) {
  const px = Number(s?.current_price) || Number(s?.chart_last) || 0;
  return px > 0 ? px : null;
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
    const px = jonggaCurrentPrice(s);
    const hiTitle = s.day_high
      ? `차트고가 ${num(s.day_high)}${s.chart_last ? ` · 종가 ${num(s.chart_last)}` : ''}`
      : '15분봉 고가 대비 하락률';
    return `<tr class="${isAuto ? 'row-hl' : ''}" data-code="${esc(code)}">
      <td>${idx + 1}${isAuto ? ' ★' : ''}</td>
      <td><b>${esc(s.stock_name || code)}</b><div class="hint">${esc(code)}</div></td>
      <td class="jongga-spark-cell" data-code="${esc(code)}"><span class="pos-spark empty">…</span></td>
      <td>${esc(s.theme || '미분류')}</td>
      <td class="num jongga-px-cell" data-code="${esc(code)}">${px != null ? num(px) : '—'}</td>
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
      <th>#</th><th>종목</th><th title="당일 15분봉">차트</th><th>테마</th><th class="num">현재가</th><th class="num" title="기본적분석 마트(억원)">시총</th><th>대금</th><th>등락</th><th title="15분봉 고가 대비 하락률">눌림</th><th title="${esc(scoreHint || '')}">스코어</th><th>매수</th>
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
    if (last > 0) {
      s.chart_last = last;
      // 스파크라인 종가가 있으면 현재가 갱신(랭킹 스냅샷보다 최신)
      s.current_price = last;
    }
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
  $('jonggaBody').querySelectorAll('.jongga-px-cell').forEach((cell) => {
    const code = normStockCode(cell.dataset.code);
    const s = list.find((x) => normStockCode(x.stock_code) === code);
    const px = jonggaCurrentPrice(s);
    cell.textContent = px != null ? num(px) : '—';
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
  const emaPeriod = _v(s, 'legacy_ema_exit_period') !== '' ? _v(s, 'legacy_ema_exit_period') : '90';
  const emaSoft = _v(s, 'legacy_ema_exit_soft_min') !== '' ? _v(s, 'legacy_ema_exit_soft_min') : '10';
  const emaBand = _v(s, 'legacy_ema_exit_band_pct') !== '' ? _v(s, 'legacy_ema_exit_band_pct') : '1';
  const emaOn = { ...s, legacy_ema_exit_enabled: s.legacy_ema_exit_enabled !== false };
  return `<div class="exit-stack">
    <div class="exit-note">📌 <b>레거시(거래대금·스크리너)</b> 청산입니다. <b>EMA 이탈 SOFT</b>는 수급 돌파·상따에도 동일 설정으로 적용됩니다. ATR을 입력하면 아래 % 방식을 대체합니다.</div>

    <div class="exit-card">
      <h5><span class="exit-num">1</span> 손절 — 손실 한도</h5>
      <div class="exit-desc">매수가 대비 <b>손실 %</b> 이하로 떨어지면 전량 매도합니다. (ATR 손절 입력 시 아래 ATR 방식으로 대체)</div>
      <div class="exit-fields">
        ${field('손절 — 손익률(%) 이하이면 매도', fNum('stop_loss_rate', s, '예: 5 (양수 입력, −5% 의미)'))}
      </div>
    </div>

    <div class="exit-card alt">
      <h5><span class="exit-num">2</span> EMA 이탈 — 추세 붕괴 SOFT 청산</h5>
      <div class="exit-desc">5분봉 <b>EMA</b> 대비 허용 이격(기본 1%)을 넘는 하락이, 당일·매수 이후 <b>확정봉 2개(10분)</b> 연속 유지되고 현재가도 이탈선 아래면 전량 매도합니다. 이격 안으로 돌아오면 카운트는 리셋됩니다. 고정손절보다 먼저 평가합니다.</div>
      ${fCheck('legacy_ema_exit_enabled', emaOn, '5분 EMA 이탈 SOFT 청산 사용')}
      <div class="exit-fields">
        ${field('EMA 기간(봉)', `<input type="number" id="set_legacy_ema_exit_period" value="${esc(emaPeriod)}" step="1" min="5" max="300" placeholder="90">`, '5분봉 기준 · 기본 90')}
        ${field('허용 이격(%)', `<input type="number" id="set_legacy_ema_exit_band_pct" value="${esc(emaBand)}" step="0.1" min="0" max="10" placeholder="1">`, '이 값까지는 이탈로 안 봄 · 기본 1')}
        ${field('SOFT 하락 시간(분)', `<input type="number" id="set_legacy_ema_exit_soft_min" value="${esc(emaSoft)}" step="5" min="5" max="60" placeholder="10">`, '확정 5분봉 연속 개수로 환산 · 기본 10분=2개')}
      </div>
      <div class="exit-example">예: EMA 90 · 이격 1% · 10분 → EMA90보다 1% 넘게 아래인 확정 5분봉이 2개 연속이고 현재가도 이탈선 아래면 청산</div>
    </div>

    <div class="exit-card">
      <h5><span class="exit-num">3</span> 트레일링 스탑 — 고점 따라 수익 실현</h5>
      <div class="exit-desc">고점이 <b>시작 %</b>에 도달하면 트레일링이 켜지고 <b>익절 바닥</b>이 잠깁니다. 고점이 오를수록 트레일링선도 올라가며, 바닥 이하로는 내려가지 않습니다.</div>
      <div class="exit-fields">
        ${field('트레일링 시작 — 고점 수익률(%) 도달 후 적용 (0=즉시)', fNum('take_profit_rate', s, '예: 10'), '도달 시 즉시매도 아님 · 활성화만')}
        ${field('고점 대비 하락 % (비우면 미사용)', fNum('trailing_stop_pct', s, '예: 1.8'), 'ATR 트레일 배수를 입력하면 이 값은 사용하지 않습니다')}
      </div>
      <div class="exit-example">예: 시작 10% · 하락 3% → +10% 도달 시 바닥 잠금 · 이후 고점 대비 3% 하락 시 매도</div>
    </div>

    <div class="exit-card atr">
      <h5><span class="exit-num">4</span> ATR 변동성 — 종목별 동적 손절·트레일 (입력 시 ③·손절% 대체)</h5>
      <div class="exit-desc">
        <b>ATR</b> = 최근 일봉 기준, 하루 평균 가격 변동폭(원).<br>
        <span class="text-cyan">손절선 ≈ 매수가 − ATR×손절배수 · 트레일선 ≈ 고점 − ATR×트레일배수</span>
      </div>
      <div class="exit-fields cols-3">
        ${field('손절 배수 (비우면 ① 손절 % 사용)', fNum('atr_mult_stop', s, '예: 1.5'))}
        ${field('트레일 배수 (비우면 ③ 트레일 % 사용)', fNum('atr_mult_trail', s, '예: 2'))}
        ${field('ATR 계산 기간(일)', fNum('atr_period', s, '14'))}
      </div>
      <div class="exit-example">${hasAtr ? '<span class="text-accent" style="font-weight:600;">✓ ATR 값이 설정되어 있어 손절/트레일은 변동성 기준으로 동작합니다.</span>' : '비워 두면 ①③의 % 방식만 사용합니다.'}</div>
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

  const legacySlots = _v(s, 'legacy_max_slots') !== '' ? _v(s, 'legacy_max_slots') : '4';
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
  if (s.breakout_require_program_net == null) s.breakout_require_program_net = true;
  if (s.breakout_program_lookback == null || s.breakout_program_lookback === '') s.breakout_program_lookback = 5;
  if (s.breakout_program_min_buy == null || s.breakout_program_min_buy === '') s.breakout_program_min_buy = 3;
  const breakoutEnd = _v(s, 'breakout_trade_end_time') || '14:30';
  const jonggaBuy = _v(s, 'jongga_buy_amount') !== '' ? _v(s, 'jongga_buy_amount') : '1000000';
  const jonggaBuyPct = _v(s, 'jongga_buy_deposit_pct') !== '' ? _v(s, 'jongga_buy_deposit_pct') : '';
  const jonggaSlots = _v(s, 'jongga_max_slots') !== '' ? _v(s, 'jongga_max_slots') : '1';
  const jonggaStart = _v(s, 'jongga_trade_start_time') || '14:30';
  const jonggaPickEnd = _v(s, 'jongga_pick_end_time') || '14:40';
  const jonggaEnd = _v(s, 'jongga_trade_end_time') || jonggaPickEnd;
  const fractalSlots = _v(s, 'fractal_max_slots') !== '' ? _v(s, 'fractal_max_slots') : '1';
  const fractalWatch = _v(s, 'fractal_watch_slots') !== '' ? _v(s, 'fractal_watch_slots') : '5';
  const fractalStart = _v(s, 'fractal_trade_start_time') || '09:20';
  const fractalEnd = _v(s, 'fractal_trade_end_time') || '14:50';
  const fractalRisk = _v(s, 'fractal_risk_pct') !== '' ? _v(s, 'fractal_risk_pct') : '0.5';
  const fractalRr = _v(s, 'fractal_rr') !== '' ? _v(s, 'fractal_rr') : '1.5';
  const overnightKeep = _v(s, 'overnight_keep_slots') !== '' ? _v(s, 'overnight_keep_slots') : '3';
  const overnightPer = _v(s, 'overnight_max_per_strategy') !== '' ? _v(s, 'overnight_max_per_strategy') : '1';
  const liqTime = _v(s, 'liquidate_time') || '15:10';
  const screenerLimit = _v(s, 'screener_candidate_limit') || '20';
  const scanTotalLimit = _v(s, 'scan_target_total_limit') || '60';
  const screenerMinChg = _v(s, 'screener_min_change_rate') || '3.3';
  const screenerMaxChg = _v(s, 'screener_max_change_rate') || '12';
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
        ${field('최대 동시 보유 종목 (0=자동)', fNum('max_concurrent_positions', s, '0'), '장중 매수 한도 · 오버나잇 한도와 별개')}
        ${field('1일 최대 매수 횟수', fNum('max_daily_buys', s, '20'))}
        ${field('1일 손실 한도(원)', fNum('daily_loss_limit', s, '-500000'))}
        ${field('1일 이익목표(원)', fNum('daily_profit_target', s, '150000'))}
        ${field('재주문 쿨다운(초)', fNum('reorder_cooldown_sec', s, '300'))}
        ${field('매매 주기(초)', fNum('scan_interval_sec', s, '60'), '스캐너·매수 폴링 · 15~600 · 기본 60')}
        ${field('매수 주문 방식', fSelect('order_method', s, [['MARKET', '시장가 (권장)'], ['LIMIT', '지정가 (현재가)']]))}
        ${field('수동 물타기 비율(%)', fNum('manual_avg_down_pct', { ...s, manual_avg_down_pct: _v(s, 'manual_avg_down_pct') || '50' }, '50'), '최초 매수금 기준 · 포지션당 1회')}
        ${field('SOFT 연속 확인 횟수', fNum('soft_confirm_polls', s, '3'), `SOFT=${softPolls}회 · HARD=1회(즉시) · 상따·돌파 공통`)}
      </div>
      <div class="desc" id="buyAmountUnitHint" style="margin-top:8px;"></div>
      <div class="box-title" style="margin-top:14px;">장세 악화 시 매수 제한</div>
      <div class="desc">예: 코스피 ≤ -2% 이면 체크한 전략은 <b>금일 신규매수 N회</b>까지만. <b>시장별 매칭</b>이면 코스피 악화→코스피 종목만, 코스닥 악화→코스닥 종목만(한도도 시장별). 보유 청산·추가매수는 그대로입니다. N=0이면 전면 차단.</div>
      ${fCheck('market_risk_enabled', s, '장세 게이트 사용')}
      <div class="form-grid" style="margin-top:8px;">
        ${field('판정 지수', fSelect('market_risk_index', s, [
          ['kospi', '코스피만'],
          ['kosdaq', '코스닥만'],
          ['either', '코스피 또는 코스닥 (하나라도)'],
          ['both', '코스피·코스닥 둘 다'],
          ['per_market', '시장별 매칭 (코스피↔코스피, 코스닥↔코스닥)'],
        ]))}
        ${field('나쁜 기준 등락(%)', fNum('market_risk_change_pct', s, '-2.0'), '예: -2 = 2% 이상 하락 시 나쁨')}
        ${field('전략당 금일 매수 한도', fNum('market_risk_max_buys_per_strategy', s, '2'), '나쁠 때 전략별 신규매수 상한 · 시장별 매칭 시 시장마다 · 0=전면차단')}
      </div>
      <div style="margin-top:8px;">
        ${fCheck('market_risk_block_legacy', s, '나쁠 때 레거시에 한도 적용')}
        ${fCheck('market_risk_block_sangtta', s, '나쁠 때 상따에 한도 적용')}
        ${fCheck('market_risk_block_breakout', s, '나쁠 때 돌파에 한도 적용')}
        ${fCheck('market_risk_block_jongga', s, '나쁠 때 종가배팅에 한도 적용')}
        ${fCheck('market_risk_block_fractal', { ...s, market_risk_block_fractal: s.market_risk_block_fractal !== false }, '나쁠 때 프랙탈에 한도 적용')}
        ${fCheck('market_risk_block_ma1592', { ...s, market_risk_block_ma1592: s.market_risk_block_ma1592 !== false }, '나쁠 때 15/92에 한도 적용')}
      </div>
      <div class="box-title" style="margin-top:14px;">급등장 시 매수 제한</div>
      <div class="desc">코스피·코스닥이 <b>+3% 이상</b>인 급등장은 다음날 낙폭이 큰 경우가 많습니다. 체크한 전략은 금일 신규매수 N회까지(0=전면차단). <b>시장별 매칭</b>이면 해당 지수 급등 시 같은 시장 종목만 한도 적용. 보유 청산·추가매수는 그대로입니다.</div>
      ${fCheck('market_surge_enabled', { ...s, market_surge_enabled: s.market_surge_enabled !== false }, '급등장 게이트 사용')}
      <div class="form-grid" style="margin-top:8px;">
        ${field('판정 지수', fSelect('market_surge_index', {
          ...s,
          market_surge_index: s.market_surge_index || 'either',
        }, [
          ['kospi', '코스피만'],
          ['kosdaq', '코스닥만'],
          ['either', '코스피 또는 코스닥 (하나라도)'],
          ['both', '코스피·코스닥 둘 다'],
          ['per_market', '시장별 매칭 (코스피↔코스피, 코스닥↔코스닥)'],
        ]))}
        ${field('급등 기준 등락(%)', fNum('market_surge_change_pct', s, '3.0'), '예: 3 = 3% 이상 상승 시 급등')}
        ${field('전략당 금일 매수 한도', fNum('market_surge_max_buys_per_strategy', s, '0'), '급등 때 전략별 신규매수 상한 · 시장별 매칭 시 시장마다 · 0=전면차단')}
      </div>
      <div style="margin-top:8px;">
        ${fCheck('market_surge_block_legacy', { ...s, market_surge_block_legacy: s.market_surge_block_legacy !== false }, '급등 때 레거시에 한도 적용')}
        ${fCheck('market_surge_block_sangtta', { ...s, market_surge_block_sangtta: s.market_surge_block_sangtta !== false }, '급등 때 상따에 한도 적용')}
        ${fCheck('market_surge_block_breakout', { ...s, market_surge_block_breakout: s.market_surge_block_breakout !== false }, '급등 때 돌파에 한도 적용')}
        ${fCheck('market_surge_block_jongga', { ...s, market_surge_block_jongga: s.market_surge_block_jongga !== false }, '급등 때 종가배팅에 한도 적용')}
        ${fCheck('market_surge_block_fractal', { ...s, market_surge_block_fractal: s.market_surge_block_fractal !== false }, '급등 때 프랙탈에 한도 적용')}
        ${fCheck('market_surge_block_ma1592', { ...s, market_surge_block_ma1592: s.market_surge_block_ma1592 !== false }, '급등 때 15/92에 한도 적용')}
      </div>
      <div class="box-title" style="margin-top:12px;">장마감 청산 · 오버나잇 슬롯</div>
      ${fCheck('liquidate_before_close', s, '장 마감 시 오버나잇 슬롯으로 정리')}
      <div class="desc">갭하락 리스크를 줄이려고 장마감에 종목을 줄입니다. <b>당일 종가배팅은 별도</b>이고, 나머지는 아래 숫자만 남깁니다. 익절 → 큰 손실 순 · 종가배팅은 익일 플러스·사흘째(이틀 초과)면 강제 청산 · 프랙탈 당일청산 ON이면 프랙탈은 포함하지 않습니다.</div>
      <div class="form-grid" style="margin-top:8px;">
        ${field('청산 시작 시각', `<input type="time" id="set_liquidate_time" value="${esc(liqTime)}">`)}
        ${field('종가배팅 제외 오버나잇', `<input type="number" id="set_overnight_keep_slots" value="${esc(overnightKeep)}" step="1" min="0" max="20">`, '당일 종가배팅을 뺀 나머지')}
        ${field('전략당 최대', `<input type="number" id="set_overnight_max_per_strategy" value="${esc(overnightPer)}" step="1" min="1" max="5">`, '같은 전략은 이 개수만')}
        ${field('당일 종가배팅', `<input type="number" id="overnightJonggaSlotsPreview" value="${esc(jonggaSlots)}" step="1" min="0" disabled>`, '종가배팅 카드의 동시 보유 슬롯 · 별도 유지')}
      </div>
      <div class="exit-note" id="overnightSlotSummary" style="margin-top:8px;"></div>
    </div>
  </div>`;

  // ===== 레거시 =====
  h += `<div class="form-section strategy-card strategy-legacy">
    <h4>레거시 · 거래대금 / 스크리너</h4>
    <div class="desc">거래대금 상위 후보의 <b>매수·진입·청산</b>입니다. 상따·돌파와 규칙을 공유하지 않습니다.</div>
    <div class="box-soft screener-policy">
      ${fCheck('use_legacy', { ...s, use_legacy: s.use_legacy !== false }, '레거시 전략 사용')}
      <div class="box-title" style="margin-top:12px;">매수 시간</div>
      <div class="desc">레거시 신규매수에만 적용됩니다. (공통란 매매시간은 전략별 시간을 합친 표시값)</div>
      <div class="form-grid">
        ${field('매수 시작', fTime('trade_start_time', s))}
        ${field('매수 종료', fTime('trade_end_time', s))}
        ${field('동시 보유 슬롯', `<input type="number" id="set_legacy_max_slots" value="${esc(legacySlots)}" step="1" min="1" placeholder="4">`, '기본 4개 · 기존 보유는 강제 매도하지 않음')}
      </div>

      <div class="box-title" style="margin-top:14px;">종목 선정</div>
      <ul class="policy-list">
        <li><span class="policy-tag on">포함</span> 거래대금 상위 최대 <b>${esc(screenerLimit)}</b> · 등락 <b>${esc(screenerMinChg)}</b>~&lt;<b>${esc(screenerMaxChg)}</b>% · KRX · 당일 20만주 이상</li>
        <li><span class="policy-tag on">한도</span> 1회 스캔 총 <b>${esc(scanTotalLimit)}</b>종목 — 상따·돌파·프랙탈·관심 편입 후 <b>남은 자리만</b> 레거시 상위로 채움</li>
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
    <div class="desc">등락률상위(ka10027) 풀 → <b>거래대금순 상위 후보</b> · 소액 매수 · <b>상한가 이탈 / 급락 → 5분 EMA90 이탈</b> 청산. 레거시 손절·트레일과 별개입니다.</div>
    <div class="box-soft screener-policy">
      ${fCheck('use_sangtta', { ...s, use_sangtta: s.use_sangtta !== false }, '상따 전략 사용')}
      <div class="box-title" style="margin-top:12px;">종목 선정 · 매수</div>
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
      <div class="desc">우선순위: 상한가 이탈 HARD/SOFT → 급락 HARD/SOFT → <b>5분 EMA 이탈 SOFT</b>(레거시와 동일 설정). · ${esc(softHint)} · ${esc(hardHint)}</div>
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
        <div class="exit-card">
          <h5><span class="exit-num">3</span> EMA 이탈 (레거시와 공유)</h5>
          <div class="exit-desc">5분봉 EMA(기본 90) 대비 허용 이격(기본 1%)을 넘는 하락이 확정봉 2개(기본 10분) 연속이면 전량 청산. 설정은 레거시 「EMA 이탈」과 동일합니다.</div>
          <div class="exit-example">레거시 카드의 EMA 기간·이격·SOFT 분 설정을 따름 · 상한가 이탈·급락 미발동 시 평가</div>
        </div>
      </div>
      <div class="exit-note">등락 ${esc(sangChgMin)}~${esc(sangChgMax)}% · 시간창 ${esc(sangStart)}~${esc(sangEnd)} · 1회 ${esc(sangBuy)}원 · 슬롯 ${esc(sangSlots)}</div>
    </div>
  </div>`;

  // ===== 수급 돌파 =====
  h += `<div class="form-section strategy-card strategy-breakout">
    <h4>수급 돌파</h4>
    <div class="desc">조건식 유니버스(5분 RSI 전환·완화) · 5분봉 <b>장대+거래량+MA20</b> 돌파 진입(MA20은 돌파봉 포함 N봉 유예 가능) · 통과 종목만 <b>프로그램 순매수 5칸 중 3칸</b> · <b>구조 이탈 → 5분 EMA90 이탈 → 고정손절 → 트레일</b>. 장마감 강제청산 제외 · 분봉은 통합(_AL).</div>
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
        ${field('고점대비 눌림 상한(%p)', fNum('crash_sync_pullback_cap_pct', s, '2.0'), '돌파 전용 · 당일고점 대비 전일종가 %p · 이상이면 차단 · 0=미적용')}
      </div>
      <div class="desc" style="margin-top:6px;">돌파 직후라도 당일 고점에서 이미 2%p 넘게 밀린 자리는 실패로 보고 사지 않습니다. 레거시·상따에는 적용하지 않습니다.</div>
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

      <div class="box-title" style="margin-top:14px;">프로그램 순매수 (마지막 게이트)</div>
      <div class="desc">5분봉 장대·거래량·MA20·진입확인을 <b>모두 통과한 종목만</b> 조회합니다(ka90008, 유니버스 전종목 호출 없음). 한 칸≈<b>1분</b>이며 <b>현재 분(형성 중)은 제외</b>. 기본값 <b>최근 5칸 중 3칸 이상</b> 순매수(수량&gt;0). 0은 순매수로 치지 않습니다. 기관·외인 일별 누적은 쓰지 않습니다.</div>
      <div class="form-grid">
        ${fCheck('breakout_require_program_net', s, '프로그램 순매수 확인', '끄면 5분 게이트만으로 매수 · 켜면 최종 종목에만 시간대 순매수 검사')}
        ${field('최근 칸 수 (N)', fNum('breakout_program_lookback', s, '5'), '완성된 1분 구간. 기본 5 = 돌파 5분봉 길이')}
        ${field('최소 순매수 칸 (M)', fNum('breakout_program_min_buy', s, '3'), 'N칸 중 순매수(>0) 개수. 기본 3 = 5칸 중 3칸')}
      </div>

      <div class="box-title" style="margin-top:14px;">돌파 청산 (매도)</div>
      <div class="desc">우선순위: <b>구조 이탈</b> → <b>5분 EMA 이탈 SOFT</b>(레거시와 동일 설정) → <b>고정손절(매수가 −%)</b> → <b>트레일(고점 −%, +시작% 이후만)</b>. · ${esc(softHint)} · ${esc(hardHint)}</div>
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
          <h5><span class="exit-num">2</span> EMA 이탈 (레거시와 공유)</h5>
          <div class="exit-desc">5분봉 EMA(기본 90) 대비 허용 이격(기본 1%)을 넘는 하락이 확정봉 2개(기본 10분) 연속이면 전량 청산. 설정은 레거시 「EMA 이탈」과 동일합니다.</div>
          <div class="exit-example">레거시 카드의 EMA 기간·이격·SOFT 분 설정을 따름 · 구조 이탈 미발동 시 평가</div>
        </div>
        <div class="exit-card">
          <h5><span class="exit-num">3</span> 고정 손절</h5>
          <div class="exit-desc">매수가 기준. 진입 직후부터 적용. 예: 3%면 매수가×0.97 이하 시 손절. <b>고점과 무관</b>.</div>
          <div class="exit-fields">
            ${field('고정 손절(%)', fNum('breakout_stop_loss_pct', s, '3'), '매수가 대비 하락폭')}
          </div>
        </div>
        <div class="exit-card alt">
          <h5><span class="exit-num">4</span> 트레일링 (익절 보호)</h5>
          <div class="exit-desc">고점 수익률이 <b>시작%</b>에 도달한 뒤에만 켜짐. 이후 <b>고점 − 하락폭%</b>에서 청산. 시작%를 못 찍으면 트레일은 동작하지 않고 ③ 고정손절만 유효.</div>
          <div class="exit-fields">
            ${field('트레일 시작 — 고점 수익률(%)', fNum('breakout_trailing_start_pct', s, '10'), '이 % 도달 시 트레일 ON (즉시 전량매도 아님)')}
            ${field('트레일 폭 — 고점 대비 하락(%)', fNum('breakout_trailing_pct', s, '4'), 'armed 후 고점×(1−이%) 이탈 시 청산')}
          </div>
        </div>
      </div>
      <div class="exit-note">우선순위: 상따 &gt; 수급 돌파 &gt; 레거시 · ${esc(breakoutStart)}~${esc(breakoutEnd)} · ${esc(breakoutBuy)}원 · 슬롯 ${esc(breakoutSlots)} · 프로그램 순매수 ${esc(_v(s, 'breakout_program_min_buy') || '3')}/${esc(_v(s, 'breakout_program_lookback') || '5')}칸</div>
    </div>
  </div>`;

  // ===== 종가배팅 =====
  h += `<div class="form-section strategy-card strategy-jongga">
    <h4>종가배팅</h4>
    <div class="desc">장 마감 전 거래대금순 → 테마 매핑 → <b>당일 최강 테마</b> 1종. 1차 씨드 → 2차 물타기(−2%) → 3차 돼지호가 분할 · 청산은 익일 고정손절+트레일. 익일 장마감 플러스·사흘째는 전량 청산. 전일 2차를 안 했고 시초(09:00~09:10) 갭하락으로 손절가면 그때 2차 물타기.</div>
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

      <div class="box-title" style="margin-top:14px;">분할매수 (물타기 + 돼지)</div>
      <div class="desc">1차 씨드 → <b>2차 물타기</b>(평단 −N% 이하일 때 추가매수) → 3차(동시호가 매수벽). 전일 2차 미실행 시 익일 시초 손절가 터치면 물타기. OFF면 1회 전량.</div>
      ${fCheck('jongga_pig_split', { ...s, jongga_pig_split: s.jongga_pig_split !== false }, '분할매수 사용')}
      <div class="form-grid" style="margin-top:8px;">
        ${field('1차 비중(%)', fNum('jongga_leg1_pct', s, '20'), '씨드')}
        ${field('2차 비중(%)', fNum('jongga_leg2_pct', s, '30'), '물타기 추가매수')}
        ${field('3차 비중(%)', fNum('jongga_leg3_pct', s, '50'), '동시호가')}
        ${field('물타기 하락(%)', `<input type="number" id="set_jongga_avg_down_pct" value="${esc(_v(s,'jongga_avg_down_pct') || '2')}" step="0.1" min="0.1" max="20" placeholder="2">`, '평단 대비 −N% 이하이면 2차 매수')}
        ${field('2차 시작', `<input type="time" id="set_jongga_leg2_start_time" value="${esc(_v(s,'jongga_leg2_start_time')||'14:50')}">`, '이때부터 물타기 감시')}
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
      <div class="desc">매수 당일은 손절/트레일 미적용 · 장마감 강제청산 제외(오버나잇). 익일부터 아래 규칙. 익일 장마감에 플러스면 전량 청산, 사흘째(이틀 초과) 장마감은 손익 무관 청산.</div>
      <div class="form-grid">
        ${field('고정 손절(%)', fNum('jongga_stop_loss_pct', s, '3'))}
        ${field('트레일 시작 수익률(%)', fNum('jongga_trailing_start_pct', s, '5'))}
        ${field('트레일 폭(%)', fNum('jongga_trailing_pct', s, '2'), '고점 대비 하락')}
      </div>
      <div class="exit-note">${esc(jonggaStart)}~${esc(jonggaPickEnd)} 선택 · 총 ${esc(jonggaBuy)}원 · 슬롯 ${esc(jonggaSlots)} · 분할 20/30/50</div>
    </div>
  </div>`;

  // ===== 프랙탈 스캘핑 =====
  h += `<div class="form-section strategy-card strategy-fractal">
    <h4>프랙탈 스캘핑</h4>
    <div class="desc">HTS 조건식 유니버스 → 동시 관측 5종 1분봉. <b>EMA20&gt;50&gt;100 정배열 + 눌림 + 확정 녹색 프랙탈 + 20EMA 종가 재돌파</b>. 손절은 진입 시 50EMA 아래, 익절은 손절폭×손익비. 전역 손익률(%)과 별개.</div>
    <div class="box-soft screener-policy">
      ${fCheck('use_fractal', s, '프랙탈 스캘핑 전략 사용')}
      <div class="box-title" style="margin-top:8px;">종목 선정</div>
      <div class="desc">조건식에는 재돌파·프랙탈·종가&gt;EMA20을 넣지 마세요. 눌림 중에도 편입되도록 <b>1분 EMA20&gt;50&gt;100</b> 정도만.</div>
      <div id="fractalCondPicker" class="cond-picker"><div class="skeleton">조건식 목록 불러오는 중...</div></div>
      <input type="hidden" id="set_fractal_condition_names" value="${esc(_v(s, 'fractal_condition_names'))}">
      <div class="form-grid" style="margin-top:12px;">
        ${field('동시 보유 슬롯', `<input type="number" id="set_fractal_max_slots" value="${esc(fractalSlots)}" step="1" min="1" max="3">`)}
        ${field('동시 관측(WATCHING)', `<input type="number" id="set_fractal_watch_slots" value="${esc(fractalWatch)}" step="1" min="1" max="5">`, '1분봉 조회 상한')}
        ${field('매수 시작', `<input type="time" id="set_fractal_trade_start_time" value="${esc(fractalStart)}">`)}
        ${field('매수 종료', `<input type="time" id="set_fractal_trade_end_time" value="${esc(fractalEnd)}">`)}
        ${field('관찰 만료(분)', fNum('fractal_watching_timeout_min', s, '15'))}
      </div>
      <div class="box-title" style="margin-top:14px;">사이징 · 청산 (가격)</div>
      <div class="desc">수량 = 계좌×리스크% ÷ (진입−손절). 미리보기: 진입 100 · 손절 98 → 익절 103 (RR 1.5).</div>
      <div class="form-grid">
        ${field('1회 리스크(%)', `<input type="number" id="set_fractal_risk_pct" value="${esc(fractalRisk)}" step="0.1" min="0.1" max="2">`)}
        ${field('손익비', `<input type="number" id="set_fractal_rr" value="${esc(fractalRr)}" step="0.1" min="0.5" max="5">`)}
        ${field('손절 EMA', fNum('fractal_stop_ema', s, '50'))}
        ${field('EMA 아래 호가', fNum('fractal_stop_tick_buffer', s, '1'))}
        ${field('수량 상한(주)', fNum('fractal_qty_cap', s, '0'), '0=제한없음')}
        ${field('매수금액 상한(원)', `<input type="number" id="set_fractal_max_amount" value="${esc(_v(s,'fractal_max_amount')||'0')}" step="10000" min="0" placeholder="0">`, '0=미적용')}
      </div>
      ${fCheck('fractal_liquidate_before_close', { ...s, fractal_liquidate_before_close: s.fractal_liquidate_before_close !== false }, '당일 강제청산 (이 전략만)')}
      <div class="form-grid" style="margin-top:8px;">
        ${field('당일 청산 시각', `<input type="time" id="set_fractal_liquidate_time" value="${esc(_v(s,'fractal_liquidate_time')||'15:10')}">`)}
      </div>
      <div class="exit-note">${esc(fractalStart)}~${esc(fractalEnd)} · 리스크 ${esc(fractalRisk)}% · RR ${esc(fractalRr)} · 관측 ${esc(fractalWatch)} · 슬롯 ${esc(fractalSlots)}</div>
      <div class="hint" style="margin-top:6px;">프랙탈 예상 매수금액 미리보기: 계산식 = 수량 × 현재가 (매수 시점의 계좌/현금 제한 및 상한 적용). 실제 주문 전 금액 제한이 적용됩니다. <span id="fractalPreviewAmount" class="bold"></span></div>
    </div>
  </div>`;

  // ===== MA1592 15/92 홀드 =====
  const ma1592Slots = _v(s, 'ma1592_max_slots') !== '' ? _v(s, 'ma1592_max_slots') : '2';
  const ma1592Start = _v(s, 'ma1592_trade_start_time') || '09:10';
  const ma1592End = _v(s, 'ma1592_trade_end_time') || '15:15';
  h += `<div class="form-section strategy-card strategy-ma1592">
    <h4>15/92 홀드 (MA1592)</h4>
    <div class="desc">유니버스 = HTS <b>1592매매</b> 편입(임진왜란 1592·집중 매매, 스티키 관찰). 1차=3분봉 <b>GC</b>(EMA15&gt;92) 후 <b>15%→35%→50%</b>. 2차=15분 이격≥1%, 3차=유지 N봉. 장부 OUT=추세전환·이격과다. 기본 OFF.</div>
    <div class="box-soft">
      ${fCheck('use_ma1592', s, '15/92 홀드 전략 사용')}
      <div class="form-grid" style="margin-top:12px;">
        ${field('조건식 이름', `<input type="text" id="set_ma1592_condition_names" value="${esc(_v(s,'ma1592_condition_names') || '1592매매')}" placeholder="1592매매">`, '쉼표로 여러 개 · 비우면 .env TELEGRAM_ALERT_CONDITION_NAMES')}
        ${field('1차 트리거', fSelect('ma1592_entry_trigger', { ...s, ma1592_entry_trigger: _v(s, 'ma1592_entry_trigger') || 'price_lead' }, [['price_lead', '가격 선행 돌파'], ['gc_above', 'EMA15>EMA92만']]), '조건식은 관찰만 · 기본 가격선행')}
        ${field('근접 이격%', fNum('ma1592_price_lead_near_pct', s, '1.5'), 'EMA15-92 근접 · gc_above 1차 추격 상한에도 동일 적용')}
        ${field('과다이격% (폐기)', fNum('ma1592_price_lead_far_pct', s, '3'), '이보다 멀면 장부 제거')}
        ${field('동시 보유 슬롯', `<input type="number" id="set_ma1592_max_slots" value="${esc(ma1592Slots)}" step="1" min="1" max="5">`)}
        ${field('매수 시작', `<input type="time" id="set_ma1592_trade_start_time" value="${esc(ma1592Start)}">`)}
        ${field('매수 종료', `<input type="time" id="set_ma1592_trade_end_time" value="${esc(ma1592End)}">`)}
        ${field('1차%', fNum('ma1592_leg1_pct', s, '15'))}
        ${field('2차%', fNum('ma1592_leg2_pct', s, '35'))}
        ${field('3차%', fNum('ma1592_leg3_pct', s, '50'))}
        ${field('이격%(15분)', fNum('ma1592_scale_gap_pct', s, '1'))}
        ${field('3차 유지봉', fNum('ma1592_scale_hold_bars', s, '2'), 'hold 모드 시만')}
        ${field('장부 TTL(일)', fNum('ma1592_setup_expire_days', s, '8'))}
        ${field('최대 보유(일)', fNum('ma1592_max_hold_days', s, '10'))}
        ${field('1회 리스크(%)', fNum('ma1592_risk_per_trade_pct', s, '2'))}
        ${field('손절%', fNum('ma1592_stop_pct', s, '4'))}
        ${field('하드이탈%', fNum('ma1592_hard_break_pct', s, '1'), '리스크 손절가(사이징) · 시세 전 청산은 급락+DC')}
        ${field('92선이탈%', fNum('ma1592_large_break_pct', s, '0.7'), 'impulse 후 STOP_MA_CRASH · 종가가 EMA92 대비 이탈%')}
        ${field('급락%', fNum('ma1592_crash_pct', s, '1.8'), '고점 대비 하락% · STOP_MA_DC_CRASH/CRASH 공통')}
      </div>
      ${fCheck('ma1592_flatten_eod', s, '장종료 전량청산 (flatten_eod)')}
      <div class="exit-note">${esc(ma1592Start)}~${esc(ma1592End)} · 슬롯 ${esc(ma1592Slots)} · 1차=가격선행 · 분할 15/35/50 · 3차=EMA92유지+15선눌림 · 익절=전고 50% · 장부OUT=추세전환</div>
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
  bindOvernightSlotPreview();
  loadHolidaySection();
  // 레거시 유니버스는 거래대금순 — 조건식 피커 없음
  loadBreakoutConditionPicker(s);
  loadFractalConditionPicker(s);
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

async function loadFractalConditionPicker(settings) {
  const box = $('fractalCondPicker');
  if (!box) return;
  const selected = new Set(parseConditionNameList(settings?.fractal_condition_names));
  try {
    const conds = await fetchConditionsList();
    if (!conds.length) {
      box.innerHTML = '<div class="desc">키움 조건식이 없습니다.</div>';
      syncFractalConditionNamesField();
      return;
    }
    box.innerHTML = conds.map((c) => {
      const name = c.condition_name || '';
      const checked = selected.has(name) ? 'checked' : '';
      return `<label class="check cond-pick"><input type="checkbox" class="fractal-cond-check" value="${esc(name)}" ${checked}>${esc(name)} <span class="fhint">API ${esc(c.api_id)}</span></label>`;
    }).join('');
    document.querySelectorAll('.fractal-cond-check').forEach((el) => {
      el.addEventListener('change', syncFractalConditionNamesField);
    });
    syncFractalConditionNamesField();
  } catch (e) {
    box.innerHTML = emptyRow('조건식 목록을 불러오지 못했습니다.', '⚠️');
  }
}

function syncFractalConditionNamesField() {
  const hidden = $('set_fractal_condition_names');
  if (!hidden) return;
  hidden.value = [...document.querySelectorAll('.fractal-cond-check:checked')]
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
    'fractal_condition_names',
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
  const fractalNames = [...document.querySelectorAll('.fractal-cond-check:checked')]
    .map((el) => el.value.trim())
    .filter(Boolean);
  out.fractal_condition_names = fractalNames.join(', ');

  const hiddenBreakout = $('set_breakout_condition_names');
  if (hiddenBreakout) hiddenBreakout.value = out.breakout_condition_names;
  const hiddenFractal = $('set_fractal_condition_names');
  if (hiddenFractal) hiddenFractal.value = out.fractal_condition_names;

  if (out.legacy_max_slots == null || Number(out.legacy_max_slots) <= 0) {
    out.legacy_max_slots = 4;
  }
  if (out.legacy_ema_exit_enabled == null) out.legacy_ema_exit_enabled = true;
  if (out.legacy_ema_exit_period == null || out.legacy_ema_exit_period === '' || Number(out.legacy_ema_exit_period) <= 0) {
    out.legacy_ema_exit_period = 90;
  }
  if (out.legacy_ema_exit_soft_min == null || out.legacy_ema_exit_soft_min === '' || Number(out.legacy_ema_exit_soft_min) <= 0) {
    out.legacy_ema_exit_soft_min = 10;
  }
  if (out.legacy_ema_exit_band_pct == null || out.legacy_ema_exit_band_pct === '') {
    out.legacy_ema_exit_band_pct = 1;
  }

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
  if (out.market_surge_index == null || out.market_surge_index === '') {
    out.market_surge_index = 'either';
  }
  if (out.market_surge_change_pct == null || out.market_surge_change_pct === '') {
    out.market_surge_change_pct = 3.0;
  }
  if (out.market_surge_max_buys_per_strategy == null || out.market_surge_max_buys_per_strategy === '') {
    out.market_surge_max_buys_per_strategy = 0;
  }
  if (out.crash_sync_block_enabled == null) out.crash_sync_block_enabled = true;
  if (out.crash_sync_index_pct == null || out.crash_sync_index_pct === '') {
    out.crash_sync_index_pct = -1.5;
  }
  if (out.crash_sync_error_pct == null || out.crash_sync_error_pct === '') {
    out.crash_sync_error_pct = 0.5;
  }
  if (out.crash_sync_pullback_cap_pct == null || out.crash_sync_pullback_cap_pct === '') {
    out.crash_sync_pullback_cap_pct = 2.0;
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
  if (out.breakout_require_program_net == null) out.breakout_require_program_net = true;
  if (out.breakout_program_lookback == null || Number(out.breakout_program_lookback) <= 0) {
    out.breakout_program_lookback = 5;
  }
  if (out.breakout_program_min_buy == null || Number(out.breakout_program_min_buy) <= 0) {
    out.breakout_program_min_buy = 3;
  }
  if (out.breakout_rsi_period == null || Number(out.breakout_rsi_period) <= 0) {
    out.breakout_rsi_period = 10;
  }
  if (out.jongga_buy_amount == null || Number(out.jongga_buy_amount) <= 0) out.jongga_buy_amount = 1000000;
  if (out.jongga_max_slots == null || Number(out.jongga_max_slots) <= 0) out.jongga_max_slots = 1;
  if (!out.jongga_trade_end_time) out.jongga_trade_end_time = out.jongga_pick_end_time || '14:40';
  if (!out.jongga_pick_end_time) out.jongga_pick_end_time = out.jongga_trade_end_time || '14:40';

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

function switchCandTab(name) {
  document.querySelectorAll('.cand-tab').forEach((t) => {
    t.classList.toggle('active', t.dataset.cand === name);
  });
  document.querySelectorAll('.cand-pane').forEach((p) => {
    p.classList.toggle('active', p.dataset.candPane === name);
  });
}

function switchAutoSubTab(name) {
  document.querySelectorAll('.auto-subtab[data-auto-sub]').forEach((t) => {
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
  if (name === 'settings') {
    loadStatus();
    loadThemeBatchStatus();
  }
  if (name === 'trades') {
    const sub = document.querySelector('.history-subtab.active')?.dataset.historySub || 'fills';
    switchHistorySubTab(sub);
  }
}

function switchHistorySubTab(name) {
  document.querySelectorAll('.history-subtab').forEach((t) => {
    t.classList.toggle('active', t.dataset.historySub === name);
  });
  document.querySelectorAll('.history-subpane').forEach((p) => {
    p.classList.toggle('active', p.id === `history-sub-${name}`);
  });
  if (name === 'fills') loadSells();
  else if (name === 'orders') loadOrders();
}

/* ===== Refresh orchestration ===== */
function refreshAll() {
  $('lastUpdated').textContent = new Date().toLocaleTimeString('ko-KR');
  loadMarketIndices({ silent: true });
  loadPerformance(true);
  loadAccount(); loadStatus(); loadTelegram();
  loadTodayKeywords();
  loadThemeBatchStatus();
  // 재시작·NXT 직후: DB로 먼저 그린 뒤 live 갱신 (동기화 대기로 목록이 비지 않게)
  loadPositions(false, { silent: true }).finally(() => {
    loadPositions(true, { silent: true, forceLive: true });
  });
  loadSells(); loadOrders();
  loadActivity(); loadLog(); loadSettings();
}
const refreshMap = {
  positions: () => loadPositions(true), sells: loadSells, orders: loadOrders,
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
  document.querySelectorAll('.auto-subtab[data-auto-sub]').forEach((t) => {
    t.onclick = () => switchAutoSubTab(t.dataset.autoSub);
  });
  document.querySelectorAll('.cand-tab').forEach((t) => {
    t.onclick = () => switchCandTab(t.dataset.cand);
  });
  document.querySelectorAll('.history-subtab').forEach((t) => {
    t.onclick = () => switchHistorySubTab(t.dataset.historySub);
  });
  $('refreshAll').onclick = refreshAll;
  $('pfRefresh').onclick = () => loadPerformance(true);
  $('logRefresh').onclick = loadLog;
  if ($('logDays')) $('logDays').onchange = loadLog;
  bindLogVerifyNavigation();
  bindActivityFilters();
  if ($('activityRefresh')) $('activityRefresh').onclick = loadActivity;
  $('autoPosRefresh').onclick = () => loadPositions(true);
  $('scrRefresh').onclick = loadScreener;
  if ($('sangRefresh')) $('sangRefresh').onclick = loadSangtta;
  if ($('breakoutRefresh')) $('breakoutRefresh').onclick = loadBreakout;
  if ($('fractalRefresh')) $('fractalRefresh').onclick = loadFractal;
  if ($('jonggaRefresh')) $('jonggaRefresh').onclick = () => loadJongga(true);
  if ($('ma1592Refresh')) $('ma1592Refresh').onclick = loadMa1592;
  document.querySelectorAll('[data-refresh]').forEach(btn => { btn.onclick = () => { $('lastUpdated').textContent = new Date().toLocaleTimeString('ko-KR'); (refreshMap[btn.dataset.refresh] || (() => {}))(); }; });
  setupAutoRefresh();
  setupInvestorFlowToggle();
  startActivityPolling();
  startPositionsLivePolling();
  bindAutoTradeToggle();
  bindPositionSellButtons();
  refreshAll();
});
