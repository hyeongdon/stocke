'use strict';

const $ = (id) => document.getElementById(id);

const SORT_COLS = [
  'stock_code', 'stock_name', 'market', 'current_price', 'market_cap',
  'per', 'pbr', 'roe', 'eps', 'dividend_per_share',
  'revenue', 'operating_profit', 'foreign_ratio', 'as_of_date',
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
  as_of_date: '기준일',
};

let state = {
  sortBy: 'market_cap',
  sortDesc: true,
  selectedCode: null,
  loading: false,
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
    case 'as_of_date': return row.as_of_date || '-';
    default: return esc(row[col] ?? '-');
  }
}

function buildQuery() {
  const p = new URLSearchParams();
  const market = $('fltMarket').value;
  if (market) p.set('market', market);
  p.set('limit', $('fltLimit').value || '500');
  p.set('sort_by', state.sortBy);
  p.set('sort_desc', state.sortDesc ? 'true' : 'false');
  const maxPer = $('fltMaxPer').value.trim();
  const maxPbr = $('fltMaxPbr').value.trim();
  const minRoe = $('fltMinRoe').value.trim();
  if (maxPer) p.set('max_per', maxPer);
  if (maxPbr) p.set('max_pbr', maxPbr);
  if (minRoe) p.set('min_roe', minRoe);
  return p.toString();
}

async function fetchJSON(url) {
  const res = await fetch(url, { headers: { Accept: 'application/json' } });
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
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
  $('statTotal').textContent = summary.total != null ? fmtNum(summary.total) : '-';
  $('statDate').textContent = summary.as_of_date || listMeta.as_of_date || '-';
  $('statKospi').textContent = summary.kospi != null ? fmtNum(summary.kospi) : '-';
  $('statKosdaq').textContent = summary.kosdaq != null ? fmtNum(summary.kosdaq) : '-';
  const shown = listMeta.items ? listMeta.items.length : 0;
  const total = listMeta.count != null ? listMeta.count : shown;
  $('statShown').textContent = `${fmtNum(shown)} / ${fmtNum(total)}`;
}

function sortMark(col) {
  if (col !== state.sortBy) return '';
  return state.sortDesc ? '▼' : '▲';
}

function renderTable(items) {
  const wrap = $('tableWrap');
  if (!items.length) {
    wrap.innerHTML = '<div class="empty"><span class="ico">📭</span>조건에 맞는 종목이 없습니다.</div>';
    $('tableHint').textContent = '';
    return;
  }
  const head = SORT_COLS.map((col) => {
    const cls = ['sortable'];
    if (['current_price', 'market_cap', 'per', 'pbr', 'roe', 'eps', 'dividend_per_share', 'revenue', 'operating_profit', 'foreign_ratio'].includes(col)) {
      cls.push('num');
    }
    return `<th class="${cls.join(' ')}" data-sort="${col}">${COL_LABELS[col]}<span class="sort-mark">${sortMark(col)}</span></th>`;
  }).join('');
  const rows = items.map((row) => {
    const sel = row.stock_code === state.selectedCode ? ' selected' : '';
    const cells = SORT_COLS.map((col) => {
      const numCls = ['current_price', 'market_cap', 'per', 'pbr', 'roe', 'eps', 'dividend_per_share', 'revenue', 'operating_profit', 'foreign_ratio'].includes(col) ? ' class="num"' : '';
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
    tr.onclick = () => selectStock(tr.dataset.code);
  });
}

function detailField(label, value) {
  return `<div class="detail-item"><div class="k">${esc(label)}</div><div class="v">${value}</div></div>`;
}

function renderDetail(row) {
  if (!row) {
    $('detailTitle').textContent = '종목 상세';
    $('detailHint').textContent = '행을 클릭하거나 종목코드를 검색하세요';
    $('detailBody').innerHTML = '<div class="empty"><span class="ico">📊</span>목록에서 종목을 선택하면 상세 지표가 표시됩니다.</div>';
    return;
  }
  $('detailTitle').textContent = `${row.stock_name || ''} (${row.stock_code})`;
  $('detailHint').textContent = `기준일 ${row.as_of_date || '-'} · ${row.market || '-'}`;
  const priceBlock = [
    detailField('현재가', fmtWon(row.current_price)),
    detailField('시가총액', fmtEok(row.market_cap)),
    detailField('거래량', fmtNum(row.volume)),
    detailField('거래대금', row.trading_value != null ? fmtNum(row.trading_value) + '백만' : '-'),
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
    <div class="detail-grid">${priceBlock}</div>
    <div class="detail-section"><div class="detail-section-title">밸류에이션</div><div class="detail-grid">${valBlock}</div></div>
    <div class="detail-section"><div class="detail-section-title">재무</div><div class="detail-grid">${finBlock}</div></div>
    <div class="detail-section"><div class="detail-section-title">메타</div><div class="detail-grid">${metaBlock}</div></div>`;
}

async function selectStock(code) {
  const c = String(code || '').trim().padStart(6, '0');
  if (!c || c === '000000') return;
  state.selectedCode = c;
  document.querySelectorAll('tr.data-row').forEach((tr) => {
    tr.classList.toggle('selected', tr.dataset.code === c);
  });
  $('detailBody').innerHTML = '<div class="skeleton">상세 불러오는 중…</div>';
  try {
    const row = await fetchJSON(`/fundamentals/${encodeURIComponent(c)}`);
    renderDetail(row);
  } catch (e) {
    renderDetail(null);
    toast('종목 상세를 불러오지 못했습니다.', true);
  }
}

async function loadSummary() {
  try {
    return await fetchJSON('/fundamentals/summary');
  } catch {
    return { as_of_date: null, total: 0, kospi: 0, kosdaq: 0 };
  }
}

async function loadList() {
  if (state.loading) return;
  state.loading = true;
  const wrap = $('tableWrap');
  if (wrap && !wrap.querySelector('.tbl')) wrap.innerHTML = '<div class="skeleton">데이터 불러오는 중…</div>';
  try {
    const [summary, list] = await Promise.all([
      loadSummary(),
      fetchJSON(`/fundamentals?${buildQuery()}`),
    ]);
    const empty = !summary.as_of_date && (!list.items || !list.items.length);
    showEmptyBanner(empty);
    renderSummary(summary, list);
    renderTable(list.items || []);
    if (state.selectedCode) {
      const found = (list.items || []).some((r) => r.stock_code === state.selectedCode);
      if (!found) await selectStock(state.selectedCode);
    }
    $('lastUpdated').textContent = new Date().toLocaleTimeString('ko-KR');
  } catch (e) {
    wrap.innerHTML = '<div class="empty"><span class="ico">⚠️</span>데이터를 불러오지 못했습니다.</div>';
    toast('목록 조회 실패', true);
  } finally {
    state.loading = false;
  }
}

function resetFilters() {
  $('fltMarket').value = '';
  $('fltSortBy').value = 'market_cap';
  $('fltLimit').value = '500';
  $('fltMaxPer').value = '';
  $('fltMaxPbr').value = '';
  $('fltMinRoe').value = '';
  state.sortBy = 'market_cap';
  state.sortDesc = true;
  loadList();
}

function initFromUrl() {
  const p = new URLSearchParams(window.location.search);
  if (p.get('market')) $('fltMarket').value = p.get('market');
  if (p.get('sort_by')) {
    state.sortBy = p.get('sort_by');
    $('fltSortBy').value = state.sortBy;
  }
  if (p.get('code')) state.selectedCode = p.get('code').padStart(6, '0');
}

document.addEventListener('DOMContentLoaded', () => {
  initFromUrl();
  $('btnRefresh').onclick = loadList;
  $('btnApply').onclick = () => {
    state.sortBy = $('fltSortBy').value || 'market_cap';
    loadList();
  };
  $('btnReset').onclick = resetFilters;
  $('fltSortBy').onchange = () => { state.sortBy = $('fltSortBy').value; };
  $('codeSearch').addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const v = $('codeSearch').value.trim().replace(/\D/g, '');
    if (v.length < 4) { toast('종목코드를 4~6자리로 입력하세요.', true); return; }
    selectStock(v);
  });
  loadList().then(() => {
    if (state.selectedCode) selectStock(state.selectedCode);
  });
});
