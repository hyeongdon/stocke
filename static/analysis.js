'use strict';

const $ = (id) => document.getElementById(id);

const SORT_COLS = [
  'stock_code', 'stock_name', 'market', 'current_price', 'market_cap',
  'per', 'pbr', 'roe', 'eps', 'dividend_per_share',
  'revenue', 'operating_profit', 'foreign_ratio', 'weekly_rsi14', 'daily_rsi14', 'as_of_date',
];

const UNSORTABLE_COLS = ['weekly_rsi14', 'daily_rsi14'];
const NUM_COLS = [
  'current_price', 'market_cap', 'per', 'pbr', 'roe', 'eps', 'dividend_per_share',
  'revenue', 'operating_profit', 'foreign_ratio', 'weekly_rsi14', 'daily_rsi14',
];

const COL_LABELS = {
  stock_code: '종목코드',
  stock_name: '종목명',
  market: '시장',
  current_price: '현재가',
  market_cap: '시가총액',
  per: 'PER',
  pbr: 'PBR',
  roe: 'ROE',
  eps: 'EPS',
  dividend_per_share: '배당',
  revenue: '매출',
  operating_profit: '영업이익',
  foreign_ratio: '외국인비율',
  weekly_rsi14: '주봉RSI',
  daily_rsi14: '일봉RSI',
  as_of_date: '기준일',
};

let state = {
  sortBy: 'market_cap',
  sortDesc: true,
  selectedCode: null,
  loading: false,
  lastItems: null,
  lastSummary: null,
  lastListMeta: null,
};

let toastTimer = null;

function toast(msg, isErr) {
  const el = $('toast');
  if (!el) return;
  el.textContent = msg;
  el.classList.toggle('err', !!isErr);
  el.classList.add('show');
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 3200);
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function parseNum(v) {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function fmtNum(v, digits) {
  const n = parseNum(v);
  if (n === null) return '-';
  return n.toLocaleString('ko-KR', { maximumFractionDigits: digits ?? 0 });
}

function fmtFloat(v, digits) {
  const n = parseNum(v);
  if (n === null) return '-';
  return n.toLocaleString('ko-KR', { minimumFractionDigits: digits ?? 1, maximumFractionDigits: digits ?? 2 });
}

function fmtWon(v) {
  const n = parseNum(v);
  if (n === null) return '-';
  return Math.round(n).toLocaleString('ko-KR') + '원';
}

const EOK_PER_JO = 10000; // 1조 = 10,000억 (DB 단위: 억원)

function fmtEok(v) {
  const n = parseNum(v);
  if (n === null) return '-';
  if (Math.abs(n) >= EOK_PER_JO) {
    return fmtFloat(n / EOK_PER_JO, 2) + '조';
  }
  return fmtFloat(n, 1) + '억';
}

function fmtPct(v) {
  const n = parseNum(v);
  if (n === null) return '-';
  return fmtFloat(n, 2) + '%';
}

function badgeHtml(row) {
  let badge = '';
  if (row.below_ma60) badge += ' <span class="trend-break-badge" title="키움 조건식 추세이탈 검색 결과">추세이탈</span>';
  if (row.weekly_rsi_hit) badge += ' <span class="rsi-cond-badge" title="키움 조건식 주봉RSI 검색 결과">주RSI</span>';
  if (row.daily_rsi_hit) badge += ' <span class="rsi-cond-badge" title="키움 조건식 일봉RSI 검색 결과">일RSI</span>';
  if (row.weekly_williams_hit) badge += ' <span class="wr-cond-badge" title="키움 조건식 주봉윌리암 검색 결과">주WR</span>';
  if (row.daily_williams_hit) badge += ' <span class="wr-cond-badge" title="키움 조건식 일봉윌리암 검색 결과">일WR</span>';
  if (row.bowl_hit) badge += ' <span class="bowl-cond-badge" title="키움 조건식 밥그릇 검색 결과">밥그릇</span>';
  if (row.ma2060_hit) badge += ' <span class="gc-cond-badge" title="키움 조건식 2060돌파 검색 결과 — 20일·60일 이평 골든크로스">2060</span>';
  return badge;
}

function cellValue(col, row) {
  switch (col) {
    case 'current_price': return fmtWon(row.current_price);
    case 'market_cap': return fmtEok(row.market_cap);
    case 'per':
    case 'pbr':
    case 'roe':
    case 'eps': return fmtFloat(row[col], 2);
    case 'dividend_per_share': return fmtWon(row.dividend_per_share);
    case 'revenue':
    case 'operating_profit': return fmtEok(row[col]);
    case 'foreign_ratio': return fmtPct(row.foreign_ratio);
    case 'weekly_rsi14': return fmtFloat(row.weekly_rsi14, 1);
    case 'daily_rsi14': return fmtFloat(row.daily_rsi14, 1);
    case 'as_of_date': return row.as_of_date || '-';
    case 'stock_name': return esc(row.stock_name ?? '-') + badgeHtml(row);
    default: return esc(row[col] ?? '-');
  }
}

const DEFAULT_LIMIT = '1000';
const DEFAULT_MIN_CAP = 1000; // 억원. 1000 = 1천억

const PHONE_MQ = window.matchMedia('(max-width: 960px)');
function phoneLayout() { return PHONE_MQ.matches; }
function coarsePointer() { return window.matchMedia('(pointer: coarse)').matches; }

let filterUserOpen = false;
let lastPhoneLayout = phoneLayout();

function minCapInput() {
  return $('fltMinCap').value.trim();
}

function fmtCapThreshold(v) {
  const n = parseNum(v);
  if (n === null) return '';
  if (n >= EOK_PER_JO && n % EOK_PER_JO === 0) return `${n / EOK_PER_JO}조`;
  if (n >= 1000 && n % 1000 === 0) return `${n / 1000}천억`;
  return `${fmtNum(n, 0)}억`;
}

function hasActiveFilters() {
  const minCap = parseNum(minCapInput());
  return Boolean(
    $('fltMarket').value
    || $('fltMaxPer').value.trim()
    || $('fltMaxPbr').value.trim()
    || $('fltMinRoe').value.trim()
    || minCap !== DEFAULT_MIN_CAP
    || $('fltWeeklyRsiOnly').checked
    || $('fltDailyRsiOnly').checked
    || $('fltWeeklyWilliamsOnly').checked
    || $('fltDailyWilliamsOnly').checked
    || $('fltBowlOnly').checked
    || $('fltMa2060Only').checked
    || $('fltExcludeBelowMa60').checked
    || ($('fltSortBy').value || 'market_cap') !== 'market_cap'
    || ($('fltLimit').value || DEFAULT_LIMIT) !== DEFAULT_LIMIT
  );
}

function updateFilterSummary() {
  const el = $('filterSummary');
  if (!el) return;
  const sort = $('fltSortBy').value || state.sortBy;
  const parts = [$('fltMarket').value || '전체', `${COL_LABELS[sort] || sort}${state.sortDesc ? '↓' : '↑'}`];
  const maxPer = $('fltMaxPer').value.trim();
  const maxPbr = $('fltMaxPbr').value.trim();
  const minRoe = $('fltMinRoe').value.trim();
  const minCap = minCapInput();
  if (minCap) parts.push(`시총≥${fmtCapThreshold(minCap)}`);
  if (maxPer) parts.push(`PER≤${maxPer}`);
  if (maxPbr) parts.push(`PBR≤${maxPbr}`);
  if (minRoe) parts.push(`ROE≥${minRoe}`);
  if ($('fltWeeklyRsiOnly').checked) parts.push('주RSI');
  if ($('fltDailyRsiOnly').checked) parts.push('일RSI');
  if ($('fltWeeklyWilliamsOnly').checked) parts.push('주WR');
  if ($('fltDailyWilliamsOnly').checked) parts.push('일WR');
  if ($('fltBowlOnly').checked) parts.push('밥그릇');
  if ($('fltMa2060Only').checked) parts.push('2060');
  if ($('fltExcludeBelowMa60').checked) parts.push('추세이탈제외');
  if (($('fltLimit').value || DEFAULT_LIMIT) !== DEFAULT_LIMIT) parts.push(`${$('fltLimit').value}건`);
  el.textContent = parts.join(' · ');
}

function applyFilterPanel() {
  const card = document.querySelector('.analysis-toolbar-card');
  const btn = $('btnFilterToggle');
  const open = !phoneLayout() || filterUserOpen;
  if (card) card.classList.toggle('is-open', open);
  if (btn) {
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    btn.classList.toggle('has-filters', hasActiveFilters());
  }
  updateFilterSummary();
}

function syncSortDirButton() {
  const btn = $('btnSortDir');
  if (!btn) return;
  btn.textContent = state.sortDesc ? '내림차순' : '오름차순';
  btn.setAttribute('aria-pressed', state.sortDesc ? 'true' : 'false');
}

function scrollAnalysisTarget(id) {
  const el = $(id);
  if (!el) return;
  el.scrollIntoView({ behavior: 'smooth', block: phoneLayout() ? 'start' : 'nearest' });
}

function markSelectedCode(code) {
  document.querySelectorAll('tr.data-row, button.analysis-row').forEach((el) => {
    el.classList.toggle('selected', el.dataset.code === code);
  });
}

function buildQuery() {
  const p = new URLSearchParams();
  const market = $('fltMarket').value;
  if (market) p.set('market', market);
  p.set('limit', $('fltLimit').value || DEFAULT_LIMIT);
  p.set('sort_by', state.sortBy);
  p.set('sort_desc', state.sortDesc ? 'true' : 'false');
  const maxPer = $('fltMaxPer').value.trim();
  const maxPbr = $('fltMaxPbr').value.trim();
  const minRoe = $('fltMinRoe').value.trim();
  const minCap = minCapInput();
  if (minCap) p.set('min_market_cap', minCap);
  if (maxPer) p.set('max_per', maxPer);
  if (maxPbr) p.set('max_pbr', maxPbr);
  if (minRoe) p.set('min_roe', minRoe);
  if ($('fltWeeklyRsiOnly').checked) p.set('weekly_rsi_only', 'true');
  if ($('fltDailyRsiOnly').checked) p.set('daily_rsi_only', 'true');
  if ($('fltWeeklyWilliamsOnly').checked) p.set('weekly_williams_only', 'true');
  if ($('fltDailyWilliamsOnly').checked) p.set('daily_williams_only', 'true');
  if ($('fltBowlOnly').checked) p.set('bowl_only', 'true');
  if ($('fltMa2060Only').checked) p.set('ma2060_only', 'true');
  if ($('fltExcludeBelowMa60').checked) p.set('exclude_below_ma60', 'true');
  return p.toString();
}

async function fetchJSON(url, opts) {
  const timeoutMs = (opts && opts.timeoutMs) || 20000;
  const { timeoutMs: _t, ...rest } = opts || {};
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, Object.assign({
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
      signal: ctrl.signal,
    }, rest || {}));
    if (res.status === 401) {
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      window.location.href = `/login?next=${next}`;
      throw new Error(`401 ${url}`);
    }
    if (!res.ok) {
      let detail = '';
      try {
        const body = await res.json();
        detail = body && body.detail ? String(body.detail) : '';
      } catch (_) { /* ignore */ }
      const err = new Error(detail || `${res.status}`);
      err.status = res.status;
      err.detail = detail;
      throw err;
    }
    return res.json();
  } finally {
    clearTimeout(timer);
  }
}

function showEmptyBanner(show) {
  const el = $('emptyBanner');
  if (!el) return;
  if (!show) {
    el.style.display = 'none';
    return;
  }
  el.style.display = 'block';
  el.innerHTML = '기본적분석 데이터가 없습니다. 프로젝트 루트에서 <code>run_fundamental_batch.bat</code>을 실행해 네이버 시가총액 마트를 적재하세요.';
}

function renderSummary(summary, listMeta) {
  state.lastSummary = summary;
  state.lastListMeta = listMeta;
  $('statTotal').textContent = summary.total != null ? fmtNum(summary.total) : '-';
  const asOf = summary.as_of_date || listMeta.as_of_date || '-';
  $('statDate').textContent = asOf;
  $('statDate').title = asOf === '-' ? '' : asOf;
  $('statKospi').textContent = summary.kospi != null ? fmtNum(summary.kospi) : '-';
  $('statKosdaq').textContent = summary.kosdaq != null ? fmtNum(summary.kosdaq) : '-';
  const shown = listMeta.items ? listMeta.items.length : 0;
  const total = listMeta.count != null ? listMeta.count : shown;
  $('statShown').textContent = `${fmtNum(shown)} / ${fmtNum(total)}`;
}

function renderConditionSetInfo(sets) {
  const el = $('conditionSetInfo');
  if (!el) return;
  const texts = [];
  const titles = [];
  for (const cs of sets || []) {
    if (!cs.fetched_at) {
      if (cs.error) {
        texts.push(`${cs.label} 조회 실패`);
        titles.push(`조건식 '${cs.condition_name}': ${cs.error}`);
      }
      continue;
    }
    const at = String(cs.fetched_at).replace('T', ' ').slice(5, 16);
    texts.push(`${cs.label} ${fmtNum(cs.count)}종목 (${at}${cs.error ? ' · 갱신 실패, 이전 결과' : ''})`);
    titles.push(cs.error ? `조건식 '${cs.condition_name}': ${cs.error}` : `키움 조건식 '${cs.condition_name}'`);
  }
  el.textContent = texts.join(' · ');
  el.title = titles.join('\n');
}

function sortMark(col) {
  if (col !== state.sortBy) return '';
  return state.sortDesc ? '▼' : '▲';
}

function renderMobileList(items) {
  const wrap = $('tableWrap');
  const cards = items.map((row) => {
    const sel = row.stock_code === state.selectedCode ? ' selected' : '';
    const metrics = [
      ['PER', fmtFloat(row.per, 2)],
      ['PBR', fmtFloat(row.pbr, 2)],
      ['ROE', fmtFloat(row.roe, 2)],
    ].map(([k, v]) => `<span><em>${k}</em>${v}</span>`).join('');
    return `<button type="button" class="analysis-row${sel}" data-code="${esc(row.stock_code)}">
      <span class="analysis-row-body">
        <span class="analysis-row-top">
          <span class="analysis-row-name">${esc(row.stock_name ?? '-')}${badgeHtml(row)}</span>
          <span class="analysis-row-price">${fmtWon(row.current_price)}</span>
        </span>
        <span class="analysis-row-sub">
          <span class="analysis-row-meta">${esc(row.stock_code)} · ${esc(row.market || '-')}</span>
          <span class="analysis-row-cap">${fmtEok(row.market_cap)}</span>
        </span>
        <span class="analysis-row-metrics">${metrics}</span>
      </span>
    </button>`;
  }).join('');
  wrap.innerHTML = `<div class="analysis-list">${cards}</div>`;
  const selected = wrap.querySelector('.analysis-row.selected');
  if (!selected) return;
  const rowTop = selected.getBoundingClientRect().top - wrap.getBoundingClientRect().top + wrap.scrollTop;
  const rowBottom = rowTop + selected.offsetHeight;
  if (rowTop < wrap.scrollTop) wrap.scrollTop = rowTop;
  else if (rowBottom > wrap.scrollTop + wrap.clientHeight) wrap.scrollTop = rowBottom - wrap.clientHeight;
}

function renderTable(items) {
  state.lastItems = items;
  const wrap = $('tableWrap');
  if (!items.length) {
    wrap.innerHTML = '<div class="empty"><span class="ico">📭</span>조건에 맞는 종목이 없습니다.</div>';
    $('tableHint').textContent = '';
    syncSortDirButton();
    return;
  }
  if (phoneLayout()) {
    renderMobileList(items);
    $('tableHint').textContent = '';
    syncSortDirButton();
    wrap.querySelectorAll('button.analysis-row').forEach((btn) => {
      btn.onclick = () => selectStock(btn.dataset.code, { scrollDetail: true });
    });
    return;
  }
  const head = SORT_COLS.map((col) => {
    const cls = UNSORTABLE_COLS.includes(col) ? [] : ['sortable'];
    if (NUM_COLS.includes(col)) cls.push('num');
    return `<th class="${cls.join(' ')}" data-sort="${col}">${COL_LABELS[col]}<span class="sort-mark">${sortMark(col)}</span></th>`;
  }).join('');
  const rows = items.map((row) => {
    const sel = row.stock_code === state.selectedCode ? ' selected' : '';
    const cells = SORT_COLS.map((col) => {
      const numCls = NUM_COLS.includes(col) ? ' class="num"' : '';
      return `<td${numCls}>${cellValue(col, row)}</td>`;
    }).join('');
    return `<tr class="data-row${sel}" data-code="${esc(row.stock_code)}">${cells}</tr>`;
  }).join('');
  wrap.innerHTML = `<table class="tbl"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>`;
  $('tableHint').textContent = `정렬: ${COL_LABELS[state.sortBy] || state.sortBy} ${state.sortDesc ? '내림차순' : '오름차순'}`;

  wrap.querySelectorAll('th.sortable').forEach((th) => {
    th.onclick = () => {
      const col = th.dataset.sort;
      if (state.sortBy === col) state.sortDesc = !state.sortDesc;
      else { state.sortBy = col; state.sortDesc = true; }
      $('fltSortBy').value = state.sortBy;
      loadList();
    };
  });
  wrap.querySelectorAll('tr.data-row').forEach((tr) => {
    tr.onclick = () => selectStock(tr.dataset.code, { scrollDetail: true });
  });
  syncSortDirButton();
}

function detailField(label, value) {
  return `<div class="detail-item"><div class="k">${esc(label)}</div><div class="v">${value}</div></div>`;
}

function companyProfilePlaceholderHtml() {
  return '<div id="companyProfileSection" class="company-profile-section"><div class="skeleton">회사 정보 불러오는 중…</div></div>';
}

function renderCompanyProfileHtml(profile) {
  if (!profile || !profile.ok) {
    return '<div class="hint">회사 개요를 불러오지 못했습니다.</div>';
  }
  const overview = (profile.overview || []).map((p) => esc(p)).join('<br><br>');
  const peers = (profile.industry_peers || []).map(
    (name) => `<span class="company-profile-peer">${esc(name)}</span>`,
  ).join('');
  const metaParts = [
    profile.market ? `시장 ${esc(profile.market)}` : '',
    profile.industry_code ? `업종코드 ${esc(profile.industry_code)}` : '',
    profile.source ? `출처 ${esc(profile.source)}` : '',
  ].filter(Boolean);
  return `
    ${overview ? `<div class="company-profile-overview">${overview}</div>` : '<div class="hint">회사 개요 없음</div>'}
    ${peers ? `<div class="detail-section-title" style="margin-top:12px;margin-bottom:6px;">동종업종</div><div class="company-profile-peers">${peers}</div>` : ''}
    ${metaParts.length ? `<div class="company-profile-meta">${metaParts.join(' · ')}</div>` : ''}`;
}

async function loadCompanyProfile(code) {
  const el = $('companyProfileSection');
  if (!el) return;
  const c = String(code || '').trim().replace(/^A/i, '').replace(/\D/g, '').padStart(6, '0');
  if (!c || c === '000000') {
    el.innerHTML = '';
    return;
  }
  try {
    const profile = await fetchJSON(`/fundamentals/${encodeURIComponent(c)}/profile`);
    el.innerHTML = renderCompanyProfileHtml(profile);
  } catch (_) {
    el.innerHTML = '<div class="hint">회사 개요를 불러오지 못했습니다.</div>';
  }
}

function renderDetail(row) {
  if (!row) {
    $('detailTitle').textContent = '종목 상세';
    $('detailHint').textContent = '행을 클릭하거나 종목코드를 검색하세요';
    $('detailBody').innerHTML = '<div class="empty"><span class="ico">📊</span>목록에서 종목을 선택하면 상세 지표가 표시됩니다.</div>';
    resetChartState();
    return;
  }
  $('detailTitle').textContent = `${row.stock_name || ''} (${row.stock_code})`;
  $('detailHint').textContent = `기준일 ${row.as_of_date || '-'} · ${row.market || '-'}`;
  const priceBlock = [
    detailField('현재가', fmtWon(row.current_price)),
    detailField('시가총액', fmtEok(row.market_cap)),
    detailField('거래량', fmtNum(row.volume)),
    detailField('거래대금', row.trading_value != null ? fmtNum(row.trading_value) + '백만' : '-'),
    indicatorFieldsHtml(),
  ].join('');
  const valBlock = [
    detailField('PER', fmtFloat(row.per, 2)),
    detailField('PBR', fmtFloat(row.pbr, 2)),
    detailField('ROE', fmtPct(row.roe)),
    detailField('EPS', fmtFloat(row.eps, 0) + '원'),
    detailField('보통주배당금', fmtWon(row.dividend_per_share)),
    detailField('외국인비율', fmtPct(row.foreign_ratio)),
  ].join('');
  const finBlock = [
    detailField('매출액', fmtEok(row.revenue)),
    detailField('영업이익', fmtEok(row.operating_profit)),
    detailField('자산총계', fmtEok(row.total_assets)),
    detailField('부채총계', fmtEok(row.total_debt)),
    detailField('상장주식수', row.listed_shares != null ? fmtNum(row.listed_shares) + '주' : '-'),
  ].join('');
  const metaBlock = [
    detailField('데이터 출처', esc(row.source || '-')),
    detailField('수집 시각', esc(row.fetched_at || '-')),
  ].join('');
  $('detailBody').innerHTML = `
    ${chartSectionsHtml()}
    <div class="detail-section">
      <div class="detail-section-title">회사 정보</div>
      ${companyProfilePlaceholderHtml()}
    </div>
    <div class="detail-grid">${priceBlock}</div>
    <div class="detail-section"><div class="detail-section-title">밸류에이션</div><div class="detail-grid">${valBlock}</div></div>
    <div class="detail-section"><div class="detail-section-title">재무</div><div class="detail-grid">${finBlock}</div></div>
    <div class="detail-section"><div class="detail-section-title">메타</div><div class="detail-grid">${metaBlock}</div></div>`;
  applyChartDrawMode();
  loadCompanyProfile(row.stock_code);
  loadAnalysisCharts(row.stock_code);
}

const MA_PERIODS = [5, 10, 20, 60, 120];
const MA_COLORS = {
  5: '#ff1a1a',
  10: '#ff4fa3',
  20: '#ffd400',
  60: '#2563eb',
  120: '#9ca3af',
};
const MA_WIDTHS = {
  5: 1.2,
  10: 1.2,
  20: 2.4,
  60: 2.4,
  120: 3.6,
};
const MA_VISIBLE_KEY = 'analysis.maVisible';
const MA_VISIBLE_DEFAULT = [20, 60];

function loadMaVisible() {
  try {
    const saved = JSON.parse(localStorage.getItem(MA_VISIBLE_KEY));
    if (Array.isArray(saved)) return new Set(saved.filter((n) => MA_PERIODS.includes(n)));
  } catch (e) { /* 저장값 손상 시 기본값 */ }
  return new Set(MA_VISIBLE_DEFAULT);
}

const maVisible = loadMaVisible();

function toggleMaVisible(n) {
  if (maVisible.has(n)) maVisible.delete(n);
  else maVisible.add(n);
  try { localStorage.setItem(MA_VISIBLE_KEY, JSON.stringify([...maVisible])); } catch (e) { /* ignore */ }
  CHART_ORDER.forEach((k) => { if (chartPayloads[k]) renderChart(k); });
}

const CHART_ORDER = ['1D', '1W'];
const CHART_SPECS = {
  '1W': { title: '주봉 차트', hint: '최근 104주 · MA5/10/20/60/120주', bars: 104, unit: '주' },
  '1D': { title: '일봉 차트', hint: '최근 1년(250일) · MA5/10/20/60/120일', bars: 250, unit: '일' },
};
const CHART_HEIGHT = 400;

function chartPixelHeight() {
  if (!phoneLayout()) return CHART_HEIGHT;
  if (window.matchMedia('(max-height: 500px)').matches) return 240;
  return 300;
}
const PRICE_MARKER_ROOM = 38;

const SIGNAL_META = {
  RSI_OS: { label: 'RSI 과매도 진입', short: 'RSI과매도', shape: 'triangle', color: 'rsi', pane: 'rsi', key: 'rsi', order: 0 },
  WR_BUY: { label: 'Williams 매수', short: 'W매수', shape: 'triangle', color: 'wr', pane: 'wr', key: 'wr', order: 1 },
};

const chartPayloads = { '1W': null, '1D': null };
let chartLoadSeq = 0;
let chartResizeTimer = null;

const chartPriceLines = {};
// 추세선 끝점은 픽셀이 아니라 (봉 인덱스, 가격)으로 저장해야 리사이즈·MA 토글 후에도 같은 캔들에 붙어 있다
const chartTrendLines = {};
const chartTrendDrafts = {};
const TREND_DRAG_MIN_PX = 5;
const LINE_HIT_PX = 5;
const COARSE_DRAG_MIN_PX = 12;
const COARSE_LINE_HIT_PX = 20;
const CHART_DRAW_MODES = ['view', 'hline', 'trend'];
const CHART_DRAW_HINTS = {
  view: '봉을 누르면 시세',
  hline: '가격칸을 누르면 가로선, 선 위를 누르면 삭제',
  trend: '가격칸을 끌면 추세선, 선 위를 누르면 삭제',
};
let chartDrawMode = 'view';

function trendDragMinPx() {
  return coarsePointer() ? COARSE_DRAG_MIN_PX : TREND_DRAG_MIN_PX;
}

function chartLineHitPx() {
  return coarsePointer() ? COARSE_LINE_HIT_PX : LINE_HIT_PX;
}

function chartToolsHtml() {
  const buttons = [
    ['view', '보기'],
    ['hline', '가로선'],
    ['trend', '추세선'],
  ].map(([id, label]) => {
    const on = id === chartDrawMode;
    return `<button type="button" class="analysis-chart-tool${on ? ' on' : ''}" data-mode="${id}" aria-pressed="${on ? 'true' : 'false'}">${label}</button>`;
  }).join('');
  return `<div class="analysis-chart-tools" role="group" aria-label="차트 그리기">${buttons}<span class="analysis-chart-tool-hint">${CHART_DRAW_HINTS[chartDrawMode]}</span></div>`;
}

function applyChartDrawMode() {
  document.querySelectorAll('.analysis-chart-tool').forEach((btn) => {
    const on = btn.dataset.mode === chartDrawMode;
    btn.classList.toggle('on', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  document.querySelectorAll('.analysis-chart-tool-hint').forEach((el) => {
    el.textContent = CHART_DRAW_HINTS[chartDrawMode] || '';
  });
  const coarse = coarsePointer();
  CHART_ORDER.forEach((k) => {
    const canvas = $(`chartCanvas${k}`);
    if (!canvas) return;
    canvas.classList.toggle('draw-trend', coarse && chartDrawMode === 'trend');
    canvas.classList.toggle('draw-hline', coarse && chartDrawMode === 'hline');
  });
}

function setChartDrawMode(mode) {
  if (!CHART_DRAW_MODES.includes(mode) || mode === chartDrawMode) return;
  chartDrawMode = mode;
  CHART_ORDER.forEach((k) => hideChartTooltip(k));
  applyChartDrawMode();
}

function resetChartState() {
  chartLoadSeq += 1;
  CHART_ORDER.forEach((k) => {
    chartPayloads[k] = null;
    chartPriceLines[k] = [];
    chartTrendLines[k] = [];
    chartTrendDrafts[k] = null;
  });
}

function indicatorFieldsHtml() {
  return [
    ...MA_PERIODS.map((n) => detailField(`MA${n}`, `<span id="detailMa${n}">-</span>`)),
    detailField('주봉 RSI', '<span id="detailRsi1W">-</span>'),
    detailField('주봉 %R', '<span id="detailWr1W">-</span>'),
    detailField('일봉 RSI', '<span id="detailRsi1D">-</span>'),
    detailField('일봉 %R', '<span id="detailWr1D">-</span>'),
  ].join('');
}

function chartSectionsHtml() {
  return CHART_ORDER.map((key) => {
    const spec = CHART_SPECS[key];
    return `
    <div class="detail-section analysis-chart-section">
      <div class="detail-section-title">${spec.title} <span class="hint">${spec.hint}</span></div>
      ${chartToolsHtml()}
      <div class="analysis-chart-summary" id="chartSummary${key}"></div>
      <div class="analysis-chart-wrap" id="chartWrap${key}">
        <div class="analysis-chart-status" id="chartStatus${key}">차트 불러오는 중…</div>
        <canvas class="analysis-chart-canvas" id="chartCanvas${key}" aria-label="${spec.title}"></canvas>
        <div class="analysis-chart-cursor" id="chartCursor${key}"></div>
        <div class="analysis-chart-tip" id="chartTip${key}"></div>
      </div>
    </div>`;
  }).join('');
}

function fmtInd(v) {
  const n = parseNum(v);
  return n === null ? '-' : n.toFixed(1);
}

function barDate(ts) {
  return String(ts || '').slice(0, 10);
}

function barTimeLabel(ts, interval) {
  const s = barDate(ts);
  if (s.length < 10) return s;
  return interval === '1W' ? `${s.slice(2, 4)}.${s.slice(5, 7)}` : s.slice(5, 10);
}

function signalDateLabel(d) {
  const s = barDate(d);
  return s.length >= 10 ? `${s.slice(2, 4)}.${s.slice(5, 7)}.${s.slice(8, 10)}` : s;
}

function chartTheme() {
  const css = getComputedStyle(document.documentElement);
  const v = (name, fb) => css.getPropertyValue(name).trim() || fb;
  return {
    bg: v('--card', '#1a1d24'),
    grid: v('--border', '#333'),
    text: v('--muted', '#888'),
    up: '#ef4444',
    down: '#3b82f6',
    osc: v('--osc-line', '#cbd5e1'),
    priceLine: v('--price-line', '#fbbf24'),
    trendLine: v('--trend-line', '#22d3ee'),
    sig: { rsi: v('--sig-rsi', '#ef4444'), wr: v('--sig-wr', '#ffffff') },
  };
}

function signalsByIdx(payload) {
  if (payload._sigByIdx) return payload._sigByIdx;
  const map = new Map();
  (payload.signals || []).forEach((s) => {
    if (!SIGNAL_META[s.type]) return;
    if (!map.has(s.idx)) map.set(s.idx, []);
    map.get(s.idx).push(s);
  });
  map.forEach((list) => list.sort((a, b) => SIGNAL_META[a.type].order - SIGNAL_META[b.type].order));
  payload._sigByIdx = map;
  return map;
}

function valueSegments(bars, key) {
  const segs = [];
  let cur = null;
  bars.forEach((b, i) => {
    const v = b[key];
    if (v == null || !Number.isFinite(v)) {
      cur = null;
      return;
    }
    if (!cur) {
      cur = [];
      segs.push(cur);
    }
    cur.push(i);
  });
  return segs;
}

function layoutPanes(W, H, n) {
  const padL = W < 480 ? 58 : 48;
  const padR = W < 480 ? 6 : 8;
  const padT = 14;
  const padB = 22;
  const gap = 10;
  const avail = H - padT - padB - gap * 2;
  const priceH = Math.round(avail * 0.55);
  const rsiH = Math.round(avail * 0.22);
  const plotW = W - padL - padR;
  const slotW = plotW / Math.max(n, 1);
  const price = { top: padT, h: priceH };
  const rsi = { top: price.top + priceH + gap, h: rsiH };
  const wr = { top: rsi.top + rsiH + gap, h: avail - priceH - rsiH };
  return {
    W, H, padL, padR, padT, padB, plotW, slotW, price, rsi, wr,
    xOf: (i) => padL + i * slotW + slotW / 2,
    idxOf: (x) => (x - padL - slotW / 2) / slotW,
  };
}

function drawMaLine(ctx, bars, key, color, width, geo, yOf) {
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineJoin = 'round';
  valueSegments(bars, key).forEach((seg) => {
    ctx.beginPath();
    seg.forEach((i, j) => {
      const x = geo.xOf(i);
      const y = yOf(bars[i][key]);
      if (j === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });
}

function drawChartLegend(ctx, items, W, padT) {
  let x = W - 8;
  const hits = [];
  ctx.font = '10px system-ui, sans-serif';
  ctx.textAlign = 'right';
  items.slice().reverse().forEach((item) => {
    const label = item.label;
    const tw = ctx.measureText(label).width;
    x -= tw + 14;
    ctx.globalAlpha = item.off ? 0.3 : 1;
    ctx.strokeStyle = item.color;
    ctx.lineWidth = Math.max(2, item.width || 0);
    ctx.beginPath();
    ctx.moveTo(x, padT + 6);
    ctx.lineTo(x + 12, padT + 6);
    ctx.stroke();
    ctx.fillStyle = item.color;
    ctx.textAlign = 'left';
    ctx.fillText(label, x + 16, padT + 9);
    ctx.globalAlpha = 1;
    hits.push({ id: item.id, x0: x - 3, x1: x + 16 + tw + 3, y0: padT - 3, y1: padT + 14 });
    x -= 8;
  });
  return hits;
}

function legendHitAt(geo, mx, my) {
  const hits = (geo && geo.legendHits) || [];
  return hits.find((h) => mx >= h.x0 && mx <= h.x1 && my >= h.y0 && my <= h.y1) || null;
}

function drawPricePane(ctx, geo, payload, theme) {
  const bars = payload.bars;
  const pane = geo.price;
  let lo = Infinity;
  let hi = -Infinity;
  bars.forEach((b) => {
    lo = Math.min(lo, b.low, b.open, b.close);
    hi = Math.max(hi, b.high, b.open, b.close);
    MA_PERIODS.forEach((n) => {
      if (!maVisible.has(n)) return;
      const v = b[`ma${n}`];
      if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    });
  });
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || lo === hi) {
    lo = Number.isFinite(lo) ? lo : 0;
    hi = lo + 1;
  }
  const range = hi - lo;
  lo -= range * 0.03;
  hi += range * 0.05;
  // 아래쪽 픽셀 여백은 저가 아래 신호 마커(최대 2개)와 "잠정" 글자가 들어갈 자리
  const candleH = pane.h - PRICE_MARKER_ROOM;
  const yOf = (p) => pane.top + candleH - ((p - lo) / (hi - lo)) * candleH;
  pane.yOf = yOf;
  pane.priceOf = (y) => lo + ((pane.top + candleH - y) / candleH) * (hi - lo);
  pane.candleH = candleH;

  ctx.strokeStyle = theme.grid;
  ctx.lineWidth = 1;
  ctx.font = '10px system-ui, sans-serif';
  ctx.textAlign = 'right';
  for (let i = 0; i <= 3; i++) {
    const y = pane.top + (candleH * i) / 3;
    ctx.beginPath();
    ctx.moveTo(geo.padL, y);
    ctx.lineTo(geo.W - geo.padR, y);
    ctx.stroke();
    ctx.fillStyle = theme.text;
    ctx.fillText(Math.round(hi - ((hi - lo) * i) / 3).toLocaleString('ko-KR'), geo.padL - 4, y + 3);
  }

  const bodyW = geo.slotW >= 4 ? geo.slotW * 0.55 : Math.max(1, geo.slotW - 1);
  bars.forEach((b, i) => {
    const x = geo.xOf(i);
    const color = b.close >= b.open ? theme.up : theme.down;
    ctx.globalAlpha = b.partial ? 0.6 : 1;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, yOf(b.high));
    ctx.lineTo(x, yOf(b.low));
    ctx.stroke();
    ctx.fillStyle = color;
    const top = Math.min(yOf(b.open), yOf(b.close));
    ctx.fillRect(x - bodyW / 2, top, bodyW, Math.max(1, Math.abs(yOf(b.close) - yOf(b.open))));
  });
  ctx.globalAlpha = 1;

  MA_PERIODS.slice().reverse().forEach((n) => {
    if (!maVisible.has(n)) return;
    drawMaLine(ctx, bars, `ma${n}`, MA_COLORS[n], MA_WIDTHS[n], geo, yOf);
  });
  geo.legendHits = drawChartLegend(ctx, MA_PERIODS.map((n) => ({
    id: n, label: `MA${n}`, color: MA_COLORS[n], width: MA_WIDTHS[n], off: !maVisible.has(n),
  })), geo.W, pane.top);
}

function oscSpecs(payload, theme) {
  const p = payload.params || {};
  const rsiOs = parseNum(p.rsi_os) ?? 30;
  const wrOs = parseNum(p.wr_os) ?? -80;
  return {
    rsi: {
      key: 'rsi', min: 0, max: 100, levels: [70, rsiOs], zone: rsiOs,
      label: `RSI(${p.rsi_period || 14})`, color: theme.sig.rsi,
    },
    wr: {
      key: 'wr', min: -100, max: 0, levels: [-20, wrOs], zone: wrOs,
      label: `%R(${p.wr_period || 93})`, color: theme.sig.wr,
    },
  };
}

function drawOscPane(ctx, geo, pane, bars, spec, theme) {
  const yOf = (v) => pane.top + ((spec.max - v) / (spec.max - spec.min)) * pane.h;
  pane.yOf = yOf;
  const left = geo.padL;
  const right = geo.W - geo.padR;
  const bottom = pane.top + pane.h;
  const yZone = yOf(spec.zone);
  const segs = valueSegments(bars, spec.key);

  ctx.fillStyle = spec.color;
  ctx.globalAlpha = 0.07;
  ctx.fillRect(left, yZone, right - left, bottom - yZone);
  ctx.globalAlpha = 1;

  ctx.strokeStyle = theme.grid;
  ctx.lineWidth = 1;
  ctx.strokeRect(left + 0.5, pane.top + 0.5, right - left - 1, pane.h - 1);

  ctx.setLineDash([3, 3]);
  ctx.font = '9px system-ui, sans-serif';
  ctx.textAlign = 'right';
  spec.levels.forEach((lv) => {
    const y = Math.round(yOf(lv)) + 0.5;
    ctx.strokeStyle = theme.text;
    ctx.globalAlpha = 0.5;
    ctx.beginPath();
    ctx.moveTo(left, y);
    ctx.lineTo(right, y);
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.fillStyle = theme.text;
    ctx.fillText(String(lv), left - 4, y + 3);
  });
  ctx.setLineDash([]);

  ctx.save();
  ctx.beginPath();
  ctx.rect(left, yZone, right - left, bottom - yZone);
  ctx.clip();
  ctx.fillStyle = spec.color;
  ctx.globalAlpha = 0.28;
  segs.forEach((seg) => {
    ctx.beginPath();
    ctx.moveTo(geo.xOf(seg[0]), yZone);
    seg.forEach((i) => ctx.lineTo(geo.xOf(i), yOf(bars[i][spec.key])));
    ctx.lineTo(geo.xOf(seg[seg.length - 1]), yZone);
    ctx.closePath();
    ctx.fill();
  });
  ctx.restore();

  ctx.strokeStyle = theme.osc;
  ctx.lineWidth = 1.3;
  segs.forEach((seg) => {
    ctx.beginPath();
    seg.forEach((i, j) => {
      const x = geo.xOf(i);
      const y = yOf(bars[i][spec.key]);
      if (j === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });

  const lastSeg = segs[segs.length - 1];
  const last = lastSeg ? bars[lastSeg[lastSeg.length - 1]][spec.key] : null;
  const label = `${spec.label} ${fmtInd(last)}`;
  ctx.font = '10px system-ui, sans-serif';
  ctx.textAlign = 'left';
  ctx.fillStyle = theme.bg;
  ctx.globalAlpha = 0.85;
  ctx.fillRect(left + 1, pane.top + 1, ctx.measureText(label).width + 8, 14);
  ctx.globalAlpha = 1;
  ctx.fillStyle = theme.text;
  ctx.fillText(label, left + 4, pane.top + 11);
}

function drawMarker(ctx, shape, x, y, r, color, hollow, bg) {
  ctx.beginPath();
  if (shape === 'circle') {
    ctx.arc(x, y, r, 0, Math.PI * 2);
  } else {
    ctx.moveTo(x, y - r * 1.15);
    ctx.lineTo(x + r * 1.1, y + r * 0.85);
    ctx.lineTo(x - r * 1.1, y + r * 0.85);
    ctx.closePath();
  }
  if (hollow) {
    ctx.fillStyle = bg;
    ctx.fill();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.stroke();
  } else {
    ctx.fillStyle = color;
    ctx.fill();
  }
}

function drawSignalMarkers(ctx, geo, payload, theme) {
  const bars = payload.bars;
  const r = Math.min(4.5, Math.max(2.5, geo.slotW * 0.4));
  const step = r * 2 + 3;
  const priceBottom = geo.price.top + geo.price.h - r - 1;
  signalsByIdx(payload).forEach((sigs, idx) => {
    const b = bars[idx];
    if (!b) return;
    const x = geo.xOf(idx);
    let y = geo.price.yOf(b.low) + r + 4;
    let lastY = y;
    sigs.forEach((s) => {
      const meta = SIGNAL_META[s.type];
      const color = theme.sig[meta.color];
      lastY = Math.min(y, priceBottom);
      drawMarker(ctx, meta.shape, x, lastY, r, color, s.partial, theme.bg);
      y += step;
      const v = b[meta.key] ?? s.value;
      if (v != null && Number.isFinite(v)) {
        drawMarker(ctx, meta.shape, x, geo[meta.pane].yOf(v), r, color, s.partial, theme.bg);
      }
    });
    const partial = sigs.find((s) => s.partial);
    if (partial) {
      // 진행 중 봉은 항상 맨 오른쪽 — 글자를 옆에 두면 직전 봉 마커를 가리므로 마커 아래(자리 없으면 고가 위)에 둔다
      let ty = lastY + r + 10;
      if (ty > geo.price.top + geo.price.h) ty = Math.max(geo.price.yOf(b.high) - 4, geo.price.top + 22);
      ctx.font = '9px system-ui, sans-serif';
      ctx.textAlign = 'right';
      ctx.fillStyle = theme.sig[SIGNAL_META[partial.type].color];
      ctx.fillText('잠정', x + r + 1, ty);
    }
  });
}

function drawPriceLines(ctx, geo, lines, theme) {
  const pane = geo.price;
  const left = geo.padL;
  const right = geo.W - geo.padR;
  ctx.font = 'bold 10px system-ui, sans-serif';
  ctx.textAlign = 'right';
  const coarse = coarsePointer();
  lines.forEach((price) => {
    const y = Math.round(pane.yOf(price)) + 0.5;
    if (y < pane.top || y > pane.top + pane.candleH) return;
    ctx.strokeStyle = theme.priceLine;
    ctx.lineWidth = coarse ? 2 : 1;
    ctx.beginPath();
    ctx.moveTo(left, y);
    ctx.lineTo(right, y);
    ctx.stroke();
    const label = Math.round(price).toLocaleString('ko-KR');
    const tw = ctx.measureText(label).width;
    ctx.fillStyle = theme.priceLine;
    ctx.fillRect(right - tw - 8, y - 7, tw + 8, 14);
    ctx.fillStyle = theme.bg;
    ctx.fillText(label, right - 4, y + 3.5);
  });
}

function trendLinePx(geo, t) {
  const pane = geo.price;
  return { x1: geo.xOf(t.i1), y1: pane.yOf(t.p1), x2: geo.xOf(t.i2), y2: pane.yOf(t.p2) };
}

function drawTrendLines(ctx, geo, lines, theme) {
  const pane = geo.price;
  ctx.save();
  ctx.beginPath();
  ctx.rect(geo.padL, pane.top, geo.plotW, pane.candleH);
  ctx.clip();
  const coarse = coarsePointer();
  ctx.strokeStyle = theme.trendLine;
  ctx.fillStyle = theme.trendLine;
  ctx.lineWidth = coarse ? 2.5 : 1.5;
  const knob = coarse ? 4 : 2.5;
  lines.forEach((t) => {
    const { x1, y1, x2, y2 } = trendLinePx(geo, t);
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.stroke();
    [[x1, y1], [x2, y2]].forEach(([x, y]) => {
      ctx.beginPath();
      ctx.arc(x, y, knob, 0, Math.PI * 2);
      ctx.fill();
    });
  });
  ctx.restore();
}

function segmentDistance(px, py, { x1, y1, x2, y2 }) {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len2 = dx * dx + dy * dy;
  const t = len2 ? Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / len2)) : 0;
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

function removeTrendAt(key, geo, mx, my) {
  const trends = chartTrendLines[key] || [];
  const near = trends.findIndex((t) => segmentDistance(mx, my, trendLinePx(geo, t)) <= chartLineHitPx());
  if (near < 0) return false;
  trends.splice(near, 1);
  renderChart(key);
  return true;
}

function togglePriceLineAt(key, geo, my) {
  const pane = geo.price;
  const lines = chartPriceLines[key] || (chartPriceLines[key] = []);
  const near = lines.findIndex((p) => Math.abs(pane.yOf(p) - my) <= chartLineHitPx());
  if (near >= 0) lines.splice(near, 1);
  else lines.push(pane.priceOf(my));
  renderChart(key);
}

function inPricePane(geo, mx, my) {
  const pane = geo && geo.price;
  return !!(pane && pane.priceOf && mx >= geo.padL && mx <= geo.W - geo.padR
    && my >= pane.top && my <= pane.top + pane.candleH);
}

function drawTimeAxis(ctx, geo, bars, interval, theme) {
  const n = bars.length;
  if (!n) return;
  ctx.fillStyle = theme.text;
  ctx.font = '9px system-ui, sans-serif';
  ctx.textAlign = 'center';
  const ticks = Math.max(2, Math.min(n, Math.floor(geo.plotW / 90), 8));
  const idxs = Array.from({ length: ticks }, (_, k) => Math.round((k * (n - 1)) / (ticks - 1)));
  [...new Set(idxs)].forEach((i) => {
    const label = barTimeLabel(bars[i].timestamp, interval);
    if (label) ctx.fillText(label, geo.xOf(i), geo.H - 6);
  });
}

function tooltipHtml(payload, i) {
  const b = payload.bars[i];
  const p = payload.params || {};
  const ma = MA_PERIODS
    .filter((n) => b[`ma${n}`] != null)
    .map((n) => `MA${n} ${fmtNum(b[`ma${n}`])}`)
    .join(' · ');
  const sigs = (signalsByIdx(payload).get(i) || []).map((s) => {
    const meta = SIGNAL_META[s.type];
    const mark = meta.shape === 'circle' ? (s.partial ? '○' : '●') : (s.partial ? '△' : '▲');
    return `<div class="tt-sig ${meta.color}">${mark} ${meta.label} (${fmtInd(s.value)})${s.partial ? ' · 잠정' : ''}</div>`;
  }).join('');
  return `
    <div class="tt-date">${esc(barDate(b.timestamp))}${b.partial ? ' <span class="tt-partial">진행 중</span>' : ''}</div>
    <div class="tt-ohlc">
      <span>시</span><b>${fmtNum(b.open)}</b><span>고</span><b>${fmtNum(b.high)}</b>
      <span>저</span><b>${fmtNum(b.low)}</b><span>종</span><b>${fmtNum(b.close)}</b>
    </div>
    <div class="tt-line">거래량 ${fmtNum(b.volume)}</div>
    ${ma ? `<div class="tt-line">${ma}</div>` : ''}
    <div class="tt-line">RSI(${p.rsi_period || 14}) <b>${fmtInd(b.rsi)}</b> · %R(${p.wr_period || 93}) <b>${fmtInd(b.wr)}</b></div>
    ${sigs}`;
}

function showChartTooltip(key, mx, my) {
  const canvas = $(`chartCanvas${key}`);
  const tip = $(`chartTip${key}`);
  const cursor = $(`chartCursor${key}`);
  const payload = chartPayloads[key];
  const geo = canvas && canvas._geo;
  if (!canvas || !tip || !cursor || !payload || !geo) return;
  if (legendHitAt(geo, mx, my)) {
    hideChartTooltip(key);
    return;
  }
  const i = Math.floor((mx - geo.padL) / geo.slotW);
  if (i < 0 || i >= payload.bars.length || my < geo.padT || my > geo.H - geo.padB) {
    hideChartTooltip(key);
    return;
  }
  const x = geo.xOf(i);
  cursor.style.display = 'block';
  cursor.style.left = `${Math.round(x)}px`;
  cursor.style.top = `${canvas.offsetTop + geo.padT}px`;
  cursor.style.height = `${geo.H - geo.padT - geo.padB}px`;
  tip.innerHTML = tooltipHtml(payload, i);
  tip.style.display = 'block';
  const tw = tip.offsetWidth;
  const th = tip.offsetHeight;
  const left = x > geo.W / 2 ? x - tw - 12 : x + 12;
  const top = Math.min(Math.max(my - th / 2, 4), Math.max(4, geo.H - th - 4));
  tip.style.left = `${Math.max(4, left)}px`;
  tip.style.top = `${canvas.offsetTop + top}px`;
}

function hideChartTooltip(key) {
  const tip = $(`chartTip${key}`);
  const cursor = $(`chartCursor${key}`);
  if (tip) tip.style.display = 'none';
  if (cursor) cursor.style.display = 'none';
}

function bindChartTooltip(key) {
  const canvas = $(`chartCanvas${key}`);
  const tip = $(`chartTip${key}`);
  const cursor = $(`chartCursor${key}`);
  if (!canvas || !tip || !cursor || canvas.dataset.tipBound) return;
  canvas.dataset.tipBound = '1';
  let press = null;
  let suppressClick = false;
  let pointerMoved = false;
  let gestureOrigin = null;
  const localPoint = (e) => {
    const rect = canvas.getBoundingClientRect();
    return { mx: e.clientX - rect.left, my: e.clientY - rect.top };
  };
  const anchorAt = (geo, mx, my) => {
    const pane = geo.price;
    const x = Math.min(Math.max(mx, geo.padL), geo.W - geo.padR);
    const y = Math.min(Math.max(my, pane.top), pane.top + pane.candleH);
    return { i: geo.idxOf(x), p: pane.priceOf(y) };
  };
  canvas.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    const { mx, my } = localPoint(e);
    pointerMoved = false;
    gestureOrigin = { mx, my };
    if (coarsePointer() && chartDrawMode !== 'trend') return;
    const geo = canvas._geo;
    if (!inPricePane(geo, mx, my) || legendHitAt(geo, mx, my)) return;
    press = { mx, my, start: anchorAt(geo, mx, my), dragging: false };
    if (coarsePointer()) e.preventDefault();
    try { canvas.setPointerCapture(e.pointerId); } catch (err) { /* 캡처 불가 */ }
  }, { passive: false });
  canvas.addEventListener('pointermove', (e) => {
    const { mx, my } = localPoint(e);
    if (gestureOrigin && Math.hypot(mx - gestureOrigin.mx, my - gestureOrigin.my) >= trendDragMinPx()) {
      pointerMoved = true;
    }
    const geo = canvas._geo;
    if (!press || !geo) return;
    if (!press.dragging && Math.hypot(mx - press.mx, my - press.my) < trendDragMinPx()) return;
    press.dragging = true;
    hideChartTooltip(key);
    const end = anchorAt(geo, mx, my);
    chartTrendDrafts[key] = { i1: press.start.i, p1: press.start.p, i2: end.i, p2: end.p };
    renderChart(key);
  });
  const endPress = (commit) => {
    if (!press) return;
    const draft = chartTrendDrafts[key];
    const dragged = press.dragging;
    const origin = { mx: press.mx, my: press.my };
    if (dragged) {
      suppressClick = true;
      if (commit && draft) (chartTrendLines[key] || (chartTrendLines[key] = [])).push(draft);
    } else if (commit && coarsePointer() && chartDrawMode === 'trend') {
      suppressClick = true;
      const geo = canvas._geo;
      if (geo && inPricePane(geo, origin.mx, origin.my) && !legendHitAt(geo, origin.mx, origin.my)) {
        removeTrendAt(key, geo, origin.mx, origin.my);
      }
    }
    press = null;
    chartTrendDrafts[key] = null;
    if (draft) renderChart(key);
  };
  canvas.addEventListener('pointerup', () => endPress(true));
  canvas.addEventListener('pointercancel', () => endPress(false));
  canvas.addEventListener('contextmenu', (e) => {
    if (coarsePointer() && chartDrawMode !== 'view') e.preventDefault();
  });
  canvas.addEventListener('mousemove', (e) => {
    if (coarsePointer()) return;
    const payload = chartPayloads[key];
    const geo = canvas._geo;
    if (!payload || !geo || press) return;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    if (legendHitAt(geo, mx, my)) {
      canvas.style.cursor = 'pointer';
      hideChartTooltip(key);
      return;
    }
    canvas.style.cursor = '';
    showChartTooltip(key, mx, my);
  });
  canvas.addEventListener('mouseleave', () => hideChartTooltip(key));
  canvas.addEventListener('click', (e) => {
    if (suppressClick) {
      suppressClick = false;
      return;
    }
    const geo = canvas._geo;
    const { mx, my } = localPoint(e);
    const hit = legendHitAt(geo, mx, my);
    if (hit) {
      toggleMaVisible(hit.id);
      return;
    }
    if (coarsePointer()) {
      if (pointerMoved) {
        pointerMoved = false;
        return;
      }
      if (chartDrawMode === 'view') {
        showChartTooltip(key, mx, my);
        return;
      }
      if (chartDrawMode === 'trend' || !inPricePane(geo, mx, my)) return;
      togglePriceLineAt(key, geo, my);
      return;
    }
    if (!inPricePane(geo, mx, my)) return;
    if (removeTrendAt(key, geo, mx, my)) return;
    togglePriceLineAt(key, geo, my);
  });
}

function renderChartSummary(key, payload) {
  const el = $(`chartSummary${key}`);
  if (!el) return;
  if (!payload) {
    el.innerHTML = '';
    el.style.display = 'none';
    return;
  }
  const spec = CHART_SPECS[key];
  const bars = payload.bars || [];
  const parts = [];
  if (bars.length) {
    const a = barDate(bars[0].timestamp);
    const b = barDate(bars[bars.length - 1].timestamp);
    parts.push(`${a === b ? a : `${a} ~ ${b}`} · ${bars.length}${spec.unit}`);
  }
  const latest = {};
  (payload.signals || []).forEach((s) => {
    if (SIGNAL_META[s.type] && (!latest[s.type] || s.idx >= latest[s.type].idx)) latest[s.type] = s;
  });
  const sigParts = Object.keys(SIGNAL_META)
    .filter((t) => latest[t])
    .map((t) => {
      const s = latest[t];
      const meta = SIGNAL_META[t];
      return `<span class="sig ${meta.color}">${meta.short} ${signalDateLabel(s.date)}${s.partial ? ' (잠정)' : ''}</span>`;
    });
  parts.push(sigParts.length ? `최근 신호: ${sigParts.join(' · ')}` : '최근 신호 없음');
  const il = payload.indicator_latest || {};
  parts.push(`RSI ${fmtInd(il.rsi)} · %R ${fmtInd(il.wr)}`);
  if (payload.warning) parts.push(`<span class="warn">${esc(payload.warning)}</span>`);
  el.innerHTML = parts.join(' · ');
  el.style.display = 'block';
}

function setChartStatus(key, text, isErr) {
  const el = $(`chartStatus${key}`);
  if (!el) return;
  if (!text) {
    el.style.display = 'none';
    el.classList.remove('err');
    return;
  }
  el.textContent = text;
  el.classList.toggle('err', !!isErr);
  el.style.display = 'block';
}

function renderChart(key) {
  const payload = chartPayloads[key];
  const canvas = $(`chartCanvas${key}`);
  const wrap = $(`chartWrap${key}`);
  if (!canvas) return;
  hideChartTooltip(key);
  const bars = (payload && payload.bars) || [];
  if (!bars.length) {
    canvas.style.display = 'none';
    canvas._geo = null;
    renderChartSummary(key, null);
    setChartStatus(key, '차트 데이터가 없습니다', true);
    return;
  }
  setChartStatus(key, '');
  renderChartSummary(key, payload);

  const W = Math.max((wrap && wrap.clientWidth) || 360, 280);
  const H = chartPixelHeight();
  const dpr = window.devicePixelRatio || 1;
  const bw = Math.round(W * dpr);
  const bh = Math.round(H * dpr);
  // 드래그 중 width를 다시 넣으면 터치 포인터가 끊길 수 있어 크기가 같을 때는 비트맵을 유지한다
  if (canvas.width !== bw || canvas.height !== bh) {
    canvas.width = bw;
    canvas.height = bh;
  }
  canvas.style.width = `${W}px`;
  canvas.style.height = `${H}px`;
  canvas.style.display = 'block';
  canvas.classList.toggle('draw-trend', coarsePointer() && chartDrawMode === 'trend');
  canvas.classList.toggle('draw-hline', coarsePointer() && chartDrawMode === 'hline');

  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const theme = chartTheme();
  ctx.fillStyle = theme.bg;
  ctx.fillRect(0, 0, W, H);

  const geo = layoutPanes(W, H, bars.length);
  const osc = oscSpecs(payload, theme);
  drawPricePane(ctx, geo, payload, theme);
  drawOscPane(ctx, geo, geo.rsi, bars, osc.rsi, theme);
  drawOscPane(ctx, geo, geo.wr, bars, osc.wr, theme);
  drawSignalMarkers(ctx, geo, payload, theme);
  drawPriceLines(ctx, geo, chartPriceLines[key] || [], theme);
  const draft = chartTrendDrafts[key];
  drawTrendLines(ctx, geo, draft ? [...(chartTrendLines[key] || []), draft] : (chartTrendLines[key] || []), theme);
  drawTimeAxis(ctx, geo, bars, String(payload.interval || key).toUpperCase(), theme);
  canvas._geo = geo;
  bindChartTooltip(key);
}

function updateIndicatorFields() {
  const ma = (chartPayloads['1D'] && chartPayloads['1D'].ma_latest) || {};
  MA_PERIODS.forEach((n) => {
    const el = $(`detailMa${n}`);
    if (el) el.textContent = ma[`ma${n}`] != null ? fmtWon(ma[`ma${n}`]) : '-';
  });
  CHART_ORDER.forEach((key) => {
    const il = (chartPayloads[key] && chartPayloads[key].indicator_latest) || {};
    const rsiEl = $(`detailRsi${key}`);
    const wrEl = $(`detailWr${key}`);
    if (rsiEl) rsiEl.textContent = fmtInd(il.rsi);
    if (wrEl) wrEl.textContent = fmtInd(il.wr);
  });
}

async function loadOneChart(key, stockCode, seq) {
  const spec = CHART_SPECS[key];
  const canvas = $(`chartCanvas${key}`);
  if (!canvas) return;
  canvas.style.display = 'none';
  renderChartSummary(key, null);
  setChartStatus(key, `${spec.title} 불러오는 중…`);
  try {
    const data = await fetchJSON(
      `/fundamentals/${encodeURIComponent(stockCode)}/chart?interval=${key}&bars=${spec.bars}`,
    );
    if (seq !== chartLoadSeq) return;
    chartPayloads[key] = data;
    renderChart(key);
  } catch (e) {
    if (seq !== chartLoadSeq) return;
    chartPayloads[key] = null;
    const detail = (e && e.detail) ? String(e.detail) : '';
    renderChartSummary(key, null);
    setChartStatus(key, detail
      ? `${spec.title} 실패: ${detail}`
      : `${spec.title}를 불러오지 못했습니다 (장외·API 제한일 수 있음)`, true);
    canvas.style.display = 'none';
  }
  updateIndicatorFields();
}

function loadAnalysisCharts(stockCode) {
  resetChartState();
  const seq = chartLoadSeq;
  updateIndicatorFields();
  return Promise.all(CHART_ORDER.map((key) => loadOneChart(key, stockCode, seq)));
}

function onChartResize() {
  if (chartResizeTimer) clearTimeout(chartResizeTimer);
  chartResizeTimer = setTimeout(() => {
    CHART_ORDER.forEach((key) => {
      if (chartPayloads[key]) renderChart(key);
    });
  }, 120);
}

async function renderDetailFallback(code, name) {
  const c = String(code || '').trim().replace(/^A/i, '').padStart(6, '0');
  $('detailTitle').textContent = name ? `${name} (${c})` : `종목 (${c})`;
  $('detailHint').textContent = '기본적분석 마트에 없음 · 주봉·일봉 차트만 표시';
  $('detailBody').innerHTML = `
    ${chartSectionsHtml()}
    <div class="detail-section">
      <div class="detail-section-title">회사 정보</div>
      ${companyProfilePlaceholderHtml()}
    </div>
    <div class="detail-grid">${indicatorFieldsHtml()}</div>
    <div class="empty" style="margin-top:12px;"><span class="ico">ℹ️</span>재무 지표는 <code>run_fundamental_batch.bat</code> 적재 후 표시됩니다.</div>`;
  applyChartDrawMode();
  loadCompanyProfile(c);
  await loadAnalysisCharts(c);
}

async function selectStock(code, options) {
  const c = String(code || '').trim().replace(/^A/i, '').replace(/\D/g, '').padStart(6, '0');
  if (!c || c === '000000') return;
  state.selectedCode = c;
  syncUrlCode(c);
  const search = $('codeSearch');
  if (search) search.value = c;
  markSelectedCode(c);
  $('detailBody').innerHTML = '<div class="skeleton">상세 불러오는 중…</div>';
  if (options && options.scrollDetail) scrollAnalysisTarget('detailCard');
  try {
    const row = await fetchJSON(`/fundamentals/${encodeURIComponent(c)}`);
    renderDetail(row);
  } catch (e) {
    if (e && e.status === 404) {
      await renderDetailFallback(c);
      return;
    }
    await renderDetailFallback(c);
    toast('재무 지표 없음 · 차트·회사 정보만 표시', false);
  }
}

async function loadSummary() {
  try {
    return await fetchJSON('/fundamentals/summary');
  } catch {
    return { as_of_date: null, total: 0, kospi: 0, kosdaq: 0 };
  }
}

async function loadList(options) {
  const autoSelect = !options || options.autoSelect !== false;
  if (state.loading) return;
  state.loading = true;
  const wrap = $('tableWrap');
  if (wrap && !wrap.querySelector('.tbl, .analysis-list')) wrap.innerHTML = '<div class="skeleton">데이터 불러오는 중…</div>';
  try {
    const [summary, list] = await Promise.all([
      loadSummary(),
      fetchJSON(`/fundamentals?${buildQuery()}`, { timeoutMs: 60000 }),
    ]);
    const empty = !summary.as_of_date && (!list.items || !list.items.length);
    showEmptyBanner(empty);
    renderSummary(summary, list);
    renderConditionSetInfo(list.condition_sets);
    renderTable(list.items || []);
    if (autoSelect && state.selectedCode) {
      await selectStock(state.selectedCode);
    }
    $('lastUpdated').textContent = new Date().toLocaleTimeString('ko-KR');
  } catch (e) {
    if (wrap) wrap.innerHTML = '<div class="empty"><span class="ico">⚠️</span>데이터를 불러오지 못했습니다.</div>';
    toast('목록 조회 실패', true);
  } finally {
    state.loading = false;
    applyFilterPanel();
  }
}

function resetFilters() {
  $('fltMarket').value = '';
  $('fltSortBy').value = 'market_cap';
  $('fltLimit').value = DEFAULT_LIMIT;
  $('fltMinCap').value = String(DEFAULT_MIN_CAP);
  $('fltMaxPer').value = '';
  $('fltMaxPbr').value = '';
  $('fltMinRoe').value = '';
  $('fltWeeklyRsiOnly').checked = false;
  $('fltDailyRsiOnly').checked = false;
  $('fltWeeklyWilliamsOnly').checked = false;
  $('fltDailyWilliamsOnly').checked = false;
  $('fltBowlOnly').checked = false;
  $('fltMa2060Only').checked = false;
  $('fltExcludeBelowMa60').checked = false;
  state.sortBy = 'market_cap';
  state.sortDesc = true;
  if (phoneLayout()) filterUserOpen = false;
  applyFilterPanel();
  loadList();
}

function codeFromUrl() {
  const raw = String(new URLSearchParams(window.location.search).get('code') || '')
    .trim()
    .replace(/^A/i, '')
    .replace(/\D/g, '');
  return raw ? raw.padStart(6, '0') : null;
}

function syncUrlCode(code) {
  const c = String(code || '').trim().replace(/^A/i, '').replace(/\D/g, '').padStart(6, '0');
  if (!c || c === '000000') return;
  const url = new URL(window.location.href);
  if (url.searchParams.get('code') === c) return;
  url.searchParams.set('code', c);
  window.history.replaceState(null, '', url.pathname + url.search);
}

function initFromUrl() {
  const p = new URLSearchParams(window.location.search);
  if (p.get('market')) $('fltMarket').value = p.get('market');
  if (p.get('sort_by')) {
    state.sortBy = p.get('sort_by');
    $('fltSortBy').value = state.sortBy;
  }
  const code = codeFromUrl();
  if (code) {
    state.selectedCode = code;
    const search = $('codeSearch');
    if (search) search.value = code;
  }
}

function onPhoneLayoutChange() {
  const next = phoneLayout();
  if (next === lastPhoneLayout) return;
  lastPhoneLayout = next;
  applyFilterPanel();
  if (state.lastItems) renderTable(state.lastItems);
}

document.addEventListener('DOMContentLoaded', () => {
  initFromUrl();
  if (phoneLayout() && hasActiveFilters()) filterUserOpen = true;
  applyFilterPanel();
  syncSortDirButton();
  $('btnRefresh').onclick = () => loadList();
  $('btnFilterToggle').onclick = () => {
    filterUserOpen = !filterUserOpen;
    applyFilterPanel();
  };
  $('btnSortDir').onclick = () => {
    state.sortDesc = !state.sortDesc;
    syncSortDirButton();
    updateFilterSummary();
    loadList();
  };
  $('btnBackList').onclick = () => {
    const card = document.querySelector('.analysis-table-card');
    if (card) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
  $('btnApply').onclick = () => {
    state.sortBy = $('fltSortBy').value || 'market_cap';
    if (phoneLayout()) filterUserOpen = false;
    applyFilterPanel();
    loadList();
  };
  $('btnReset').onclick = resetFilters;
  $('fltSortBy').onchange = () => {
    state.sortBy = $('fltSortBy').value;
    updateFilterSummary();
  };
  $('codeSearch').addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const v = $('codeSearch').value.trim().replace(/\D/g, '');
    if (v.length < 4) { toast('종목코드를 4~6자리로 입력하세요.', true); return; }
    selectStock(v, { scrollDetail: true });
  });
  const pendingCode = state.selectedCode;
  if (pendingCode) selectStock(pendingCode, { scrollDetail: true });
  loadList({ autoSelect: !pendingCode });
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.analysis-chart-tool');
    if (!btn) return;
    setChartDrawMode(btn.dataset.mode);
  });
  window.addEventListener('resize', onChartResize);
  if (PHONE_MQ.addEventListener) PHONE_MQ.addEventListener('change', onPhoneLayoutChange);
  else if (PHONE_MQ.addListener) PHONE_MQ.addListener(onPhoneLayoutChange);
});
