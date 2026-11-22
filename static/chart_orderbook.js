'use strict';

const $ = (id) => document.getElementById(id);
const DEFAULT_CODE = '005930';
const FAVORITES_KEY = 'analysisFavorites';
const FAV_FOLDERS_KEY = 'analysisFavoriteFolders';
const FAV_OPEN_KEY = 'analysisFavGroupOpen';
const UNCATEGORIZED_ID = 0;
const INTERVAL_BARS = {
  '5M': 78,
  '15M': 78,
  '1D': 189,
  '1W': 104,
  '1MO': 24,
};
const MA_COLORS = {
  ma5: '#ef4444',
  ma20: '#facc15',
  ma60: '#a78bfa',
  ma120: '#94a3b8',
};
const MA_WIDTHS = {
  ma5: 2,
  ma20: 3,
  ma60: 3,
  ma120: 1,
};
const RSI_PERIOD = 14;
const RSI_COLOR = '#f472b6';

const state = {
  code: '',
  name: '',
  interval: '1D',
  loading: false,
  loadSeq: 0,
  chart: null,
  rsiChart: null,
  candleSeries: null,
  volumeSeries: null,
  rsiSeries: null,
  maSeries: {},
  lastBars: [],
  favorites: [],
  folders: [],
  favFilter: '',
  favOpen: true,
  favPanelOpen: true,
  groupOpen: {},
  activeFolderId: null,
  autoTimer: null,
  resizeObs: null,
  syncingRange: false,
};

const MOBILE_FAV_MQ = window.matchMedia('(max-width: 960px)');
function isMobileFavLayout() {
  return MOBILE_FAV_MQ.matches;
}

const suggest = {
  items: [],
  active: -1,
  seq: 0,
  timer: null,
};

let toastTimer = null;

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function normalizeStockCode(value) {
  const raw = String(value == null ? '' : value).trim().replace(/^A/i, '');
  return /^\d{1,6}$/.test(raw) ? raw.padStart(6, '0') : '';
}

function toast(msg, isErr) {
  const el = $('toast');
  if (!el) return;
  el.textContent = msg;
  el.classList.toggle('err', !!isErr);
  el.classList.add('show');
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2800);
}

function fmtWon(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return '-';
  return Math.round(v).toLocaleString('ko-KR');
}

function fmtQty(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return '-';
  return Math.round(v).toLocaleString('ko-KR');
}

function fmtRate(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return '-';
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toFixed(2)}%`;
}

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function chartTheme() {
  // 국내 시세 관례: 상승=빨강, 하락=파랑 (기본적분석 차트와 동일)
  return {
    bg: cssVar('--card', '#131720'),
    text: cssVar('--muted', '#7a8496'),
    grid: cssVar('--border', '#252b38'),
    up: '#ef4444',
    down: '#3b82f6',
  };
}

async function fetchJSON(url, opts) {
  const res = await fetch(url, Object.assign({
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
  }, opts || {}));
  let body = null;
  try {
    body = await res.json();
  } catch (_) {
    body = null;
  }
  if (!res.ok) {
    const detail = body && body.detail;
    const msg = Array.isArray(detail)
      ? detail.map((d) => d.msg || d).join(', ')
      : (detail || (body && body.message) || `HTTP ${res.status}`);
    const err = new Error(String(msg));
    err.status = res.status;
    err.detail = detail;
    throw err;
  }
  return body;
}

function isIntraday(interval) {
  return interval === '5M' || interval === '15M';
}

/** 키움 timestamp → Lightweight Charts time */
function toChartTime(ts, interval) {
  const raw = String(ts || '').replace('T', ' ').trim();
  const m = raw.match(/^(\d{4})-(\d{2})-(\d{2})(?:[ ](\d{2}):(\d{2})(?::(\d{2}))?)?/);
  if (!m) return null;
  const y = +m[1];
  const mo = +m[2];
  const d = +m[3];
  if (!isIntraday(interval)) {
    return `${m[1]}-${m[2]}-${m[3]}`;
  }
  const h = +(m[4] || 0);
  const mi = +(m[5] || 0);
  const se = +(m[6] || 0);
  // KST 벽시계를 UTC timestamp로 취급 → 축에 09:00~15:30 그대로 표시
  return Math.floor(Date.UTC(y, mo - 1, d, h, mi, se) / 1000);
}

/** Wilder RSI — 서버 지표와 동일 계열. 분봉 등 rsi 미포함 시 클라이언트 보정. */
function wilderRsiSeries(closes, period = RSI_PERIOD) {
  const n = closes.length;
  const out = new Array(n).fill(null);
  if (n <= period) return out;
  let gain = 0;
  let loss = 0;
  for (let i = 1; i <= period; i += 1) {
    const d = closes[i] - closes[i - 1];
    if (d >= 0) gain += d;
    else loss -= d;
  }
  let avgGain = gain / period;
  let avgLoss = loss / period;
  out[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  for (let i = period + 1; i < n; i += 1) {
    const d = closes[i] - closes[i - 1];
    const g = d > 0 ? d : 0;
    const l = d < 0 ? -d : 0;
    avgGain = (avgGain * (period - 1) + g) / period;
    avgLoss = (avgLoss * (period - 1) + l) / period;
    out[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return out;
}

function barsToSeries(bars, interval) {
  const candles = [];
  const volumes = [];
  const maMaps = { ma5: [], ma20: [], ma60: [], ma120: [] };
  const rsiPoints = [];
  const closes = [];
  const times = [];
  let prevTime = null;

  for (const b of bars || []) {
    const time = toChartTime(b.timestamp, interval);
    if (time == null) continue;
    // Lightweight Charts는 시간이 오름차순·중복 없어야 함
    if (prevTime != null && time <= prevTime) continue;
    prevTime = time;

    const open = Number(b.open) || 0;
    const high = Number(b.high) || 0;
    const low = Number(b.low) || 0;
    const close = Number(b.close) || 0;
    if (close <= 0) continue;

    candles.push({ time, open, high, low, close });
    closes.push(close);
    times.push(time);
    const vol = Number(b.volume) || 0;
    volumes.push({
      time,
      value: vol,
      color: close >= open ? 'rgba(239, 68, 68, 0.42)' : 'rgba(59, 130, 246, 0.42)',
    });

    Object.keys(maMaps).forEach((key) => {
      const v = b[key];
      if (v != null && Number.isFinite(Number(v)) && Number(v) > 0) {
        maMaps[key].push({ time, value: Number(v) });
      }
    });

    if (b.rsi != null && Number.isFinite(Number(b.rsi))) {
      rsiPoints.push({ time, value: Number(b.rsi) });
    }
  }

  // API에 rsi가 없으면(분봉 등) 클라이언트에서 계산
  let rsiSeries = rsiPoints;
  if (rsiSeries.length < Math.max(8, Math.floor(candles.length * 0.5))) {
    const computed = wilderRsiSeries(closes, RSI_PERIOD);
    rsiSeries = [];
    computed.forEach((v, i) => {
      if (v != null && Number.isFinite(v)) rsiSeries.push({ time: times[i], value: Math.round(v * 100) / 100 });
    });
  }
  return { candles, volumes, maMaps, rsiSeries };
}

function destroyChart() {
  if (state.resizeObs) {
    state.resizeObs.disconnect();
    state.resizeObs = null;
  }
  if (state.chart) {
    try { state.chart.remove(); } catch (_) { /* ignore */ }
  }
  if (state.rsiChart) {
    try { state.rsiChart.remove(); } catch (_) { /* ignore */ }
  }
  state.chart = null;
  state.rsiChart = null;
  state.candleSeries = null;
  state.volumeSeries = null;
  state.rsiSeries = null;
  state.maSeries = {};
}

function syncVisibleRange(source, target) {
  if (!source || !target || state.syncingRange) return;
  state.syncingRange = true;
  try {
    const range = source.timeScale().getVisibleLogicalRange();
    if (range) target.timeScale().setVisibleLogicalRange(range);
  } catch (_) { /* ignore */ }
  state.syncingRange = false;
}

function resizeCharts() {
  const host = $('chartHost');
  const rsiHost = $('rsiHost');
  if (state.chart && host) {
    const { width, height } = host.getBoundingClientRect();
    if (width > 0 && height > 0) state.chart.applyOptions({ width, height });
  }
  if (state.rsiChart && rsiHost) {
    const { width, height } = rsiHost.getBoundingClientRect();
    if (width > 0 && height > 0) state.rsiChart.applyOptions({ width, height });
  }
}

function ensureChart() {
  const host = $('chartHost');
  const rsiHost = $('rsiHost');
  if (!host || !rsiHost) return null;
  if (state.chart && state.rsiChart) return state.chart;

  if (typeof LightweightCharts === 'undefined') {
    setChartStatus('Lightweight Charts 로드 실패', true);
    return null;
  }

  destroyChart();

  const theme = chartTheme();
  const sharedOpts = {
    layout: {
      background: { type: 'solid', color: theme.bg },
      textColor: theme.text,
    },
    grid: {
      vertLines: { color: theme.grid },
      horzLines: { color: theme.grid },
    },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: theme.grid },
    handleScroll: { vertTouchDrag: false },
  };

  const chart = LightweightCharts.createChart(host, {
    ...sharedOpts,
    layout: { ...sharedOpts.layout, attributionLogo: true },
    timeScale: {
      borderColor: theme.grid,
      timeVisible: isIntraday(state.interval),
      secondsVisible: false,
    },
  });

  const candleSeries = chart.addCandlestickSeries({
    upColor: theme.up,
    downColor: theme.down,
    borderUpColor: theme.up,
    borderDownColor: theme.down,
    wickUpColor: theme.up,
    wickDownColor: theme.down,
  });

  const volumeSeries = chart.addHistogramSeries({
    priceFormat: { type: 'volume' },
    priceScaleId: 'vol',
  });
  chart.priceScale('vol').applyOptions({
    scaleMargins: { top: 0.78, bottom: 0 },
  });
  chart.priceScale('right').applyOptions({
    scaleMargins: { top: 0.08, bottom: 0.22 },
  });

  state.chart = chart;
  state.candleSeries = candleSeries;
  state.volumeSeries = volumeSeries;

  Object.keys(MA_COLORS).forEach((key) => {
    state.maSeries[key] = chart.addLineSeries({
      color: MA_COLORS[key],
      lineWidth: MA_WIDTHS[key] || 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
  });

  const rsiChart = LightweightCharts.createChart(rsiHost, {
    ...sharedOpts,
    layout: { ...sharedOpts.layout, attributionLogo: false },
    timeScale: {
      borderColor: theme.grid,
      timeVisible: isIntraday(state.interval),
      secondsVisible: false,
      visible: true,
    },
  });
  rsiChart.priceScale('right').applyOptions({
    scaleMargins: { top: 0.12, bottom: 0.08 },
  });

  const rsiSeries = rsiChart.addLineSeries({
    color: RSI_COLOR,
    lineWidth: 2,
    priceLineVisible: false,
    lastValueVisible: true,
    crosshairMarkerVisible: true,
    priceFormat: { type: 'custom', formatter: (v) => Number(v).toFixed(1) },
  });
  rsiSeries.createPriceLine({ price: 70, color: 'rgba(239, 68, 68, 0.45)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: '70' });
  rsiSeries.createPriceLine({ price: 30, color: 'rgba(59, 130, 246, 0.45)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: '30' });
  rsiSeries.createPriceLine({ price: 50, color: 'rgba(148, 163, 184, 0.35)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: false });

  state.rsiChart = rsiChart;
  state.rsiSeries = rsiSeries;

  chart.timeScale().subscribeVisibleLogicalRangeChange(() => syncVisibleRange(chart, rsiChart));
  rsiChart.timeScale().subscribeVisibleLogicalRangeChange(() => syncVisibleRange(rsiChart, chart));

  if (!state.resizeObs) {
    const stack = host.closest('.cob-chart-stack') || host;
    state.resizeObs = new ResizeObserver(() => resizeCharts());
    state.resizeObs.observe(stack);
  }
  resizeCharts();
  return chart;
}

function setChartStatus(msg, isErr) {
  const el = $('chartStatus');
  if (!el) return;
  if (!msg) {
    el.hidden = true;
    el.textContent = '';
    return;
  }
  el.hidden = false;
  el.textContent = msg;
  el.classList.toggle('err', !!isErr);
}

function applyMaVisibility() {
  Object.keys(state.maSeries).forEach((key) => {
    const series = state.maSeries[key];
    if (!series) return;
    const checked = !!document.querySelector(`.cob-ma input[data-ma="${key}"]:checked`);
    series.applyOptions({ visible: checked });
  });
}

function applyRsiVisibility() {
  const on = !$('togRsi') || $('togRsi').checked;
  const pane = $('rsiPane');
  if (pane) pane.hidden = !on;
  resizeCharts();
}

function renderChartData(payload) {
  const empty = $('chartEmpty');
  const bars = (payload && payload.bars) || [];
  state.lastBars = bars;

  if (!bars.length) {
    if (empty) {
      empty.hidden = false;
      empty.textContent = '차트 데이터가 없습니다';
    }
    setChartStatus('차트 데이터가 없습니다', true);
    return;
  }

  ensureChart();
  if (!state.candleSeries || !state.rsiSeries) return;

  const { candles, volumes, maMaps, rsiSeries } = barsToSeries(bars, state.interval);
  if (!candles.length) {
    if (empty) {
      empty.hidden = false;
      empty.textContent = '표시할 봉이 없습니다';
    }
    setChartStatus('표시할 봉이 없습니다', true);
    return;
  }

  if (empty) empty.hidden = true;
  const timeOpts = { timeVisible: isIntraday(state.interval), secondsVisible: false };
  state.chart.applyOptions({ timeScale: timeOpts });
  state.rsiChart.applyOptions({ timeScale: timeOpts });

  state.candleSeries.setData(candles);
  state.volumeSeries.setData(volumes);
  Object.keys(state.maSeries).forEach((key) => {
    state.maSeries[key].setData(maMaps[key] || []);
  });
  state.rsiSeries.setData(rsiSeries || []);
  applyMaVisibility();
  applyRsiVisibility();
  state.chart.timeScale().fitContent();
  syncVisibleRange(state.chart, state.rsiChart);

  const latest = rsiSeries.length ? rsiSeries[rsiSeries.length - 1].value : null;
  const rsiEl = $('rsiLatest');
  if (rsiEl) rsiEl.textContent = latest != null ? Number(latest).toFixed(1) : '-';

  const warn = payload && payload.warning;
  setChartStatus(warn || '', !!warn);
  const hint = $('chartHint');
  if (hint) {
    const range = payload.date_range && payload.date_range.length
      ? ` · ${payload.date_range.join('~')}`
      : '';
    const rsiTxt = latest != null ? ` · RSI ${Number(latest).toFixed(1)}` : '';
    hint.textContent = `${payload.interval || state.interval} · ${candles.length}봉${range}${rsiTxt}`;
  }
}

function renderQuote(snap) {
  const name = (snap && snap.stock_name) || state.name || '';
  const code = (snap && snap.stock_code) || state.code || '';
  state.name = name;
  if (code) state.code = normalizeStockCode(code);

  $('stockName').textContent = name || (code ? `종목 ${code}` : '종목을 조회하세요');
  $('stockCode').textContent = code || '';
  const priceEl = $('currentPrice');
  priceEl.textContent = snap ? fmtWon(snap.current_price) : '-';

  const changeEl = $('priceChange');
  let diff = Number(snap && snap.price_diff);
  const rateRaw = snap && snap.change_rate;
  const rate = Number(String(rateRaw == null ? '' : rateRaw).replace(/[+%]/g, ''));
  // 등락률 부호를 우선 (과거 API가 price_diff를 절댓값으로 주던 경우 보정)
  if (Number.isFinite(diff) && Number.isFinite(rate) && rate !== 0 && Math.sign(diff) !== Math.sign(rate)) {
    diff = Math.sign(rate) * Math.abs(diff);
  }
  let cls = 'flat';
  if (Number.isFinite(rate) && rate !== 0) cls = rate > 0 ? 'up' : 'down';
  else if (Number.isFinite(diff) && diff !== 0) cls = diff > 0 ? 'up' : 'down';
  priceEl.className = `cob-price ${cls}`;
  changeEl.className = `cob-change ${cls}`;
  const diffText = Number.isFinite(diff) ? `${diff > 0 ? '+' : ''}${fmtWon(diff)}` : '-';
  const rateText = Number.isFinite(rate) ? fmtRate(rate) : (rateRaw != null ? String(rateRaw) : '-');
  changeEl.textContent = `${diffText} (${rateText})`;

  $('quoteVolume').textContent = snap ? fmtQty(snap.volume) : '-';
  $('orderbookTime').textContent = (snap && snap.orderbook_time) || '-';
}

function renderOrderbook(snap) {
  const body = $('orderbookBody');
  const hint = $('obHint');
  if (!body) return;

  const rows = (snap && snap.orderbook) || [];
  if (!rows.length) {
    body.innerHTML = '<div class="cob-empty sm">호가 없음</div>';
    if (hint) hint.textContent = '10호가';
    return;
  }

  const maxQty = Math.max(
    1,
    ...rows.map((r) => Math.max(Number(r.ask_qty) || 0, Number(r.bid_qty) || 0)),
  );
  const asks = [...rows].sort((a, b) => (Number(b.ask_price) || 0) - (Number(a.ask_price) || 0));
  const bids = [...rows].sort((a, b) => (Number(b.bid_price) || 0) - (Number(a.bid_price) || 0));
  const midPrice = snap.current_price != null ? fmtWon(snap.current_price) : '-';

  const askHtml = asks.map((r) => {
    const qty = Number(r.ask_qty) || 0;
    const w = Math.round((qty / maxQty) * 100);
    return `<tr class="cob-ob-ask">
      <td class="price">${fmtWon(r.ask_price)}</td>
      <td class="qty"><span class="cob-ob-bar" style="width:${w}%"></span>${fmtQty(qty)}</td>
    </tr>`;
  }).join('');

  const bidHtml = bids.map((r) => {
    const qty = Number(r.bid_qty) || 0;
    const w = Math.round((qty / maxQty) * 100);
    return `<tr class="cob-ob-bid">
      <td class="price">${fmtWon(r.bid_price)}</td>
      <td class="qty"><span class="cob-ob-bar" style="width:${w}%"></span>${fmtQty(qty)}</td>
    </tr>`;
  }).join('');

  body.innerHTML = `<table class="cob-ob-table">
    <thead><tr><th>가격</th><th>잔량</th></tr></thead>
    <tbody>
      ${askHtml}
      <tr class="cob-ob-mid"><td colspan="2">현재가 ${esc(midPrice)}</td></tr>
      ${bidHtml}
    </tbody>
  </table>`;

  if (hint) {
    const live = snap.orderbook_live === false ? ' · 추정/폴백' : '';
    hint.textContent = `${rows.length}호가${live}`;
  }
}

function readLocalFavorites() {
  try {
    const raw = localStorage.getItem(FAVORITES_KEY);
    if (!raw) return [];
    const saved = JSON.parse(raw);
    if (!Array.isArray(saved)) return [];
    return saved
      .map((item) => {
        const code = normalizeStockCode(item && typeof item === 'object' ? item.code : item);
        const name = String((item && item.name) || '');
        const folderId = item && item.folder_id != null ? Number(item.folder_id) : null;
        return code ? { code, name, folder_id: Number.isFinite(folderId) ? folderId : null } : null;
      })
      .filter(Boolean);
  } catch (_) {
    return [];
  }
}

function readLocalFolders() {
  try {
    const raw = localStorage.getItem(FAV_FOLDERS_KEY);
    if (!raw) return [];
    const saved = JSON.parse(raw);
    if (!Array.isArray(saved)) return [];
    return saved
      .map((item) => {
        const id = Number(item && item.id);
        const name = String((item && item.name) || '').trim();
        if (!Number.isFinite(id) || id <= 0 || !name) return null;
        return { id, name, sort_order: Number(item.sort_order) || 0 };
      })
      .filter(Boolean);
  } catch (_) {
    return [];
  }
}

function readGroupOpen() {
  try {
    const raw = localStorage.getItem(FAV_OPEN_KEY);
    if (!raw) return {};
    const saved = JSON.parse(raw);
    return saved && typeof saved === 'object' ? saved : {};
  } catch (_) {
    return {};
  }
}

function saveGroupOpen() {
  try {
    localStorage.setItem(FAV_OPEN_KEY, JSON.stringify(state.groupOpen || {}));
  } catch (_) { /* ignore */ }
}

function persistLocalFavorites() {
  try {
    localStorage.setItem(FAVORITES_KEY, JSON.stringify(
      state.favorites.map((f) => ({ code: f.code, name: f.name || '', folder_id: f.folder_id ?? null })),
    ));
    localStorage.setItem(FAV_FOLDERS_KEY, JSON.stringify(state.folders || []));
  } catch (_) { /* ignore */ }
}

function mergeFavorites(primary, secondary) {
  const map = new Map();
  [...(primary || []), ...(secondary || [])].forEach((item) => {
    const code = normalizeStockCode(item && item.code);
    if (!code) return;
    const prev = map.get(code);
    const name = String((item && item.name) || (prev && prev.name) || '');
    const folderId = item && item.folder_id != null
      ? Number(item.folder_id)
      : (prev ? prev.folder_id : null);
    map.set(code, {
      code,
      name,
      folder_id: Number.isFinite(folderId) ? folderId : null,
    });
  });
  return [...map.values()].sort((a, b) => {
    const an = a.name || a.code;
    const bn = b.name || b.code;
    return an.localeCompare(bn, 'ko');
  });
}

function mergeFolders(primary, secondary) {
  const map = new Map();
  [...(primary || []), ...(secondary || [])].forEach((item) => {
    const id = Number(item && item.id);
    const name = String((item && item.name) || '').trim();
    if (!Number.isFinite(id) || id <= 0 || !name) return;
    if (!map.has(id)) {
      map.set(id, { id, name, sort_order: Number(item.sort_order) || 0 });
    }
  });
  return [...map.values()].sort((a, b) => (a.sort_order - b.sort_order) || a.id - b.id);
}

async function loadFavoritesList() {
  let remoteFavs = [];
  let remoteFolders = [];
  try {
    const saved = await fetchJSON('/api/analysis/preferences');
    remoteFolders = (saved.folders || []).map((item) => ({
      id: Number(item.id),
      name: String(item.name || ''),
      sort_order: Number(item.sort_order) || 0,
    })).filter((item) => Number.isFinite(item.id) && item.id > 0 && item.name);
    remoteFavs = (saved.favorites || []).map((item) => ({
      code: normalizeStockCode(item.code),
      name: String(item.name || ''),
      folder_id: item.folder_id == null ? null : Number(item.folder_id),
    })).filter((item) => item.code);
  } catch (_) {
    /* 로그인·API 실패 시 로컬만 사용 */
  }
  state.folders = mergeFolders(remoteFolders, readLocalFolders());
  const folderIds = new Set(state.folders.map((f) => f.id));
  state.favorites = mergeFavorites(remoteFavs, readLocalFavorites()).map((f) => ({
    ...f,
    folder_id: f.folder_id != null && folderIds.has(f.folder_id) ? f.folder_id : null,
  }));
  state.groupOpen = { ...readGroupOpen() };
  if (state.groupOpen[UNCATEGORIZED_ID] == null) state.groupOpen[UNCATEGORIZED_ID] = true;
  state.folders.forEach((f) => {
    if (state.groupOpen[f.id] == null) state.groupOpen[f.id] = true;
  });
  persistLocalFavorites();
  renderFavorites();
  return state.favorites;
}

function filteredFavorites() {
  const q = String(state.favFilter || '').trim().toLowerCase();
  if (!q) return state.favorites;
  return state.favorites.filter((f) => {
    const name = String(f.name || '').toLowerCase();
    const folder = state.folders.find((x) => x.id === f.folder_id);
    const folderName = folder ? folder.name.toLowerCase() : '미분류';
    return f.code.includes(q) || name.includes(q) || folderName.includes(q);
  });
}

function folderGroups(favs) {
  const byId = new Map();
  state.folders.forEach((f) => {
    byId.set(f.id, { id: f.id, name: f.name, items: [] });
  });
  const uncategorized = { id: UNCATEGORIZED_ID, name: '미분류', items: [] };
  favs.forEach((f) => {
    if (f.folder_id != null && byId.has(f.folder_id)) byId.get(f.folder_id).items.push(f);
    else uncategorized.items.push(f);
  });
  const groups = state.folders.map((f) => byId.get(f.id));
  groups.push(uncategorized);
  const q = String(state.favFilter || '').trim();
  if (q) return groups.filter((g) => g.items.length);
  return groups;
}

function favItemHtml(f) {
  const on = f.code === state.code ? ' active' : '';
  const name = f.name || f.code;
  return `<div class="cob-fav-item${on}" role="treeitem" aria-selected="${f.code === state.code}" data-code="${esc(f.code)}" title="${esc(f.code)}">
    <button type="button" class="cob-fav-item-main" data-code="${esc(f.code)}">
      <span class="cob-fav-item-name">${esc(name)}</span>
      <span class="cob-fav-item-code">${esc(f.code)}</span>
    </button>
    <div class="cob-fav-item-actions">
      <button type="button" class="cob-fav-item-rename" data-rename="${esc(f.code)}" title="표시명 변경">✎</button>
      <button type="button" class="cob-fav-item-move" data-move="${esc(f.code)}" title="폴더 이동">이동</button>
    </div>
  </div>`;
}

function resolveActiveFolder(groups) {
  if (state.activeFolderId == null) return null;
  return groups.find((g) => g.id === state.activeFolderId) || null;
}

function renderMobileFolderList(groups) {
  return groups.map((g) => {
    const canEdit = g.id !== UNCATEGORIZED_ID;
    const actions = canEdit
      ? `<div class="cob-fav-group-actions">
          <button type="button" class="btn sm" data-rename-folder="${g.id}" title="이름 변경">✎</button>
          <button type="button" class="btn sm" data-del-folder="${g.id}" title="폴더 삭제">×</button>
        </div>`
      : '';
    return `<div class="cob-fav-group cob-fav-folder-pick" data-group="${g.id}">
      <div class="cob-fav-group-head">
        <button type="button" class="cob-fav-group-toggle" data-open-folder="${g.id}" aria-label="${esc(g.name)} 폴더 열기">
          <span class="cob-fav-group-ico" aria-hidden="true">${canEdit ? '📂' : '☆'}</span>
          <span class="cob-fav-group-name">${esc(g.name)}</span>
          <span class="cob-fav-group-count">${g.items.length}</span>
          <span class="cob-fav-group-enter" aria-hidden="true">›</span>
        </button>
        ${actions}
      </div>
    </div>`;
  }).join('');
}

function renderMobileStockList(group) {
  const canEdit = group.id !== UNCATEGORIZED_ID;
  const items = group.items.length
    ? group.items.map(favItemHtml).join('')
    : `<div class="cob-fav-empty" style="padding:10px 6px;">비어 있음</div>`;
  return `<div class="cob-fav-drill" data-group="${group.id}">
    <div class="cob-fav-drill-head">
      <button type="button" class="cob-fav-back" data-fav-back title="폴더 목록">←</button>
      <div class="cob-fav-drill-title">
        <span class="cob-fav-group-ico" aria-hidden="true">${canEdit ? '📂' : '☆'}</span>
        <span class="cob-fav-group-name">${esc(group.name)}</span>
        <span class="cob-fav-group-count">${group.items.length}</span>
      </div>
    </div>
    <div class="cob-fav-group-items cob-fav-drill-items">${items}</div>
  </div>`;
}

function renderDesktopGroups(groups) {
  return groups.map((g) => {
    const open = state.groupOpen[g.id] !== false;
    const canEdit = g.id !== UNCATEGORIZED_ID;
    const actions = canEdit
      ? `<div class="cob-fav-group-actions">
          <button type="button" class="btn sm" data-rename-folder="${g.id}" title="이름 변경">✎</button>
          <button type="button" class="btn sm" data-del-folder="${g.id}" title="폴더 삭제">×</button>
        </div>`
      : '';
    const items = g.items.length
      ? g.items.map(favItemHtml).join('')
      : `<div class="cob-fav-empty" style="padding:10px 6px;">비어 있음</div>`;
    return `<div class="cob-fav-group${open ? '' : ' is-collapsed'}" data-group="${g.id}">
      <div class="cob-fav-group-head">
        <button type="button" class="cob-fav-group-toggle" data-toggle-group="${g.id}" aria-expanded="${open}">
          <span class="cob-fav-group-ico" aria-hidden="true">${canEdit ? '📂' : '☆'}</span>
          <span class="cob-fav-group-name">${esc(g.name)}</span>
          <span class="cob-fav-group-count">${g.items.length}</span>
          <span class="cob-fav-group-chevron" aria-hidden="true"></span>
        </button>
        ${actions}
      </div>
      <div class="cob-fav-group-items">${items}</div>
    </div>`;
  }).join('');
}

function renderFavorites() {
  const folder = $('favFolder');
  const list = $('favList');
  const count = $('favCount');
  if (count) count.textContent = String(state.favorites.length);
  if (folder) {
    folder.classList.toggle('is-collapsed', !state.favOpen);
    folder.classList.toggle('is-mobile-drill', isMobileFavLayout() && state.activeFolderId != null);
  }

  if (!list) return;
  const favs = filteredFavorites();
  if (!state.favorites.length) {
    state.activeFolderId = null;
    list.innerHTML = `<div class="cob-fav-empty">기본적분석에서 ★로 추가하거나<br>+ 로 폴더를 만드세요</div>`;
    return;
  }
  if (!favs.length) {
    list.innerHTML = `<div class="cob-fav-empty">검색 결과 없음</div>`;
    return;
  }

  const groups = folderGroups(favs);
  if (isMobileFavLayout()) {
    const active = resolveActiveFolder(groups);
    if (active) {
      list.innerHTML = renderMobileStockList(active);
      return;
    }
    state.activeFolderId = null;
    list.innerHTML = renderMobileFolderList(groups);
    return;
  }

  state.activeFolderId = null;
  list.innerHTML = renderDesktopGroups(groups);
}

function setFavPanelOpen(open) {
  state.favPanelOpen = !!open;
  const main = $('cobMain');
  const btn = $('btnFavToggle');
  if (main) main.classList.toggle('fav-collapsed', !state.favPanelOpen);
  if (btn) {
    btn.setAttribute('aria-expanded', state.favPanelOpen ? 'true' : 'false');
    btn.textContent = state.favPanelOpen ? '★ 폴더' : '★ 폴더 열기';
  }
  resizeCharts();
}

function ensureFolderPickerStyle() {
  if (document.getElementById('folder-picker-style')) return;
  const style = document.createElement('style');
  style.id = 'folder-picker-style';
  style.textContent = `
.fp-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:10000;display:flex;align-items:flex-end;justify-content:center}
@media(min-width:700px){.fp-backdrop{align-items:center}}
.fp-sheet{background:#1b1f27;color:#e6e9ef;width:100%;max-width:520px;max-height:80vh;overflow:auto;border-radius:16px 16px 0 0;padding:16px;box-sizing:border-box}
@media(min-width:700px){.fp-sheet{border-radius:16px}}
.fp-title{font-weight:700;font-size:16px;margin-bottom:12px}
.fp-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
.fp-card{background:#262b36;border:2px solid transparent;border-radius:12px;padding:16px 12px;font-size:15px;color:inherit;text-align:left;cursor:pointer;min-height:56px;word-break:break-all}
.fp-card:active{transform:scale(.97)}
.fp-card.current{border-color:#f5c542}
.fp-card.new{border:2px dashed #4a5263;color:#9aa4b8}
.fp-cancel{margin-top:14px;width:100%;padding:12px;border-radius:10px;border:none;background:#333a48;color:#e6e9ef;font-size:15px}`;
  document.head.appendChild(style);
}

/** @returns {Promise<{type:'folder',id:number|null}|{type:'new'}|null>} null=취소 */
function showFolderCardPicker({ title, folders, currentId, allowNew }) {
  ensureFolderPickerStyle();
  return new Promise((resolve) => {
    const backdrop = document.createElement('div');
    backdrop.className = 'fp-backdrop';
    const sheet = document.createElement('div');
    sheet.className = 'fp-sheet';
    const head = document.createElement('div');
    head.className = 'fp-title';
    head.textContent = title;
    const grid = document.createElement('div');
    grid.className = 'fp-grid';
    const done = (v) => { backdrop.remove(); resolve(v); };
    const addCard = (label, cls, value) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = `fp-card ${cls}`;
      b.textContent = label;
      b.addEventListener('click', () => done(value));
      grid.appendChild(b);
    };
    addCard('미분류', currentId == null ? 'current' : '', { type: 'folder', id: null });
    folders.forEach((f) => addCard(`📁 ${f.name}`, f.id === currentId ? 'current' : '', { type: 'folder', id: f.id }));
    if (allowNew) addCard('＋ 새 폴더', 'new', { type: 'new' });
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'fp-cancel';
    cancel.textContent = '취소';
    cancel.addEventListener('click', () => done(null));
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) done(null); });
    sheet.append(head, grid, cancel);
    backdrop.appendChild(sheet);
    document.body.appendChild(backdrop);
  });
}

async function createFavoriteFolder() {
  const name = window.prompt('새 폴더 이름');
  if (name == null) return;
  const trimmed = name.trim();
  if (!trimmed) {
    toast('폴더 이름을 입력하세요', true);
    return;
  }
  try {
    const row = await fetchJSON('/api/analysis/favorite-folders', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: trimmed }),
    });
    state.folders = mergeFolders(state.folders, [row]);
    state.groupOpen[row.id] = true;
    saveGroupOpen();
    persistLocalFavorites();
    renderFavorites();
    toast(`폴더 "${row.name}" 추가`);
  } catch (e) {
    toast((e && (e.detail || e.message)) || '폴더 추가 실패', true);
  }
}

async function renameFavoriteFolder(folderId) {
  const folder = state.folders.find((f) => f.id === folderId);
  if (!folder) return;
  const name = window.prompt('폴더 이름 변경', folder.name);
  if (name == null) return;
  const trimmed = name.trim();
  if (!trimmed) {
    toast('폴더 이름을 입력하세요', true);
    return;
  }
  try {
    const row = await fetchJSON(`/api/analysis/favorite-folders/${folderId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: trimmed }),
    });
    folder.name = row.name;
    persistLocalFavorites();
    renderFavorites();
  } catch (e) {
    toast((e && (e.detail || e.message)) || '이름 변경 실패', true);
  }
}

async function deleteFavoriteFolder(folderId) {
  const folder = state.folders.find((f) => f.id === folderId);
  if (!folder) return;
  if (!window.confirm(`"${folder.name}" 폴더를 삭제할까요?\n안의 종목은 미분류로 이동합니다.`)) return;
  try {
    await fetchJSON(`/api/analysis/favorite-folders/${folderId}`, { method: 'DELETE' });
    state.folders = state.folders.filter((f) => f.id !== folderId);
    state.favorites.forEach((f) => {
      if (f.folder_id === folderId) f.folder_id = null;
    });
    if (state.activeFolderId === folderId) state.activeFolderId = null;
    delete state.groupOpen[folderId];
    saveGroupOpen();
    persistLocalFavorites();
    renderFavorites();
    toast('폴더 삭제됨 · 종목은 미분류');
  } catch (e) {
    toast((e && (e.detail || e.message)) || '폴더 삭제 실패', true);
  }
}

async function renameFavorite(code) {
  const fav = state.favorites.find((f) => f.code === code);
  if (!fav) {
    toast('즐겨찾기에 없는 종목입니다', true);
    return;
  }
  const name = window.prompt('즐겨찾기 표시명 변경', fav.name || fav.code);
  if (name == null) return;
  const trimmed = name.trim();
  if (!trimmed) {
    toast('표시명을 입력하세요', true);
    return;
  }
  try {
    const row = await fetchJSON(`/api/analysis/favorites/${encodeURIComponent(code)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stock_name: trimmed }),
    });
    fav.name = row.name || trimmed;
    persistLocalFavorites();
    renderFavorites();
    if (state.code === code) {
      state.name = fav.name;
      if ($('codeSearch')) $('codeSearch').value = `${fav.name} (${code})`;
    }
    toast(`표시명 → ${fav.name}`);
  } catch (e) {
    toast((e && (e.detail || e.message)) || '표시명 변경 실패', true);
  }
}

async function moveFavoriteToFolder(code) {
  const fav = state.favorites.find((f) => f.code === code);
  if (!fav) {
    toast('즐겨찾기에 없는 종목입니다', true);
    return;
  }
  if (!state.folders.length) {
    const make = window.confirm('이동할 폴더가 없습니다. 새 폴더를 만들까요?');
    if (!make) return;
    await createFavoriteFolder();
    if (!state.folders.length) return;
  }
  const picked = await showFolderCardPicker({
    title: '이동할 폴더',
    folders: state.folders,
    currentId: fav.folder_id ?? null,
    allowNew: false,
  });
  if (!picked) return;
  const folderId = picked.id;
  try {
    const row = await fetchJSON(`/api/analysis/favorites/${encodeURIComponent(code)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder_id: folderId }),
    });
    fav.folder_id = row.folder_id == null ? null : Number(row.folder_id);
    if (fav.folder_id != null) state.groupOpen[fav.folder_id] = true;
    persistLocalFavorites();
    renderFavorites();
    const dest = fav.folder_id == null
      ? '미분류'
      : ((state.folders.find((f) => f.id === fav.folder_id) || {}).name || '폴더');
    toast(`${fav.name || code} → ${dest}`);
  } catch (e) {
    toast((e && (e.detail || e.message)) || '이동 실패', true);
  }
}

function syncUrlCode(code) {
  const url = new URL(location.href);
  if (code) url.searchParams.set('code', code);
  else url.searchParams.delete('code');
  history.replaceState(null, '', url);
}

function setIntervalActive(interval) {
  state.interval = interval;
  document.querySelectorAll('.cob-interval').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.interval === interval);
  });
}

function markUpdated() {
  const el = $('lastUpdated');
  if (!el) return;
  el.textContent = new Date().toLocaleTimeString('ko-KR', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

async function loadChart(code, seq) {
  const bars = INTERVAL_BARS[state.interval] || 189;
  const data = await fetchJSON(
    `/fundamentals/${encodeURIComponent(code)}/chart?interval=${encodeURIComponent(state.interval)}&bars=${bars}`,
  );
  if (seq !== state.loadSeq) return;
  renderChartData(data);
}

async function loadSnapshot(code, seq) {
  const data = await fetchJSON(`/stocks/${encodeURIComponent(code)}/snapshot`);
  if (seq !== state.loadSeq) return;
  renderQuote(data);
  renderOrderbook(data);
}

async function loadAll(code, { quiet } = {}) {
  const c = normalizeStockCode(code);
  if (!c) {
    toast('종목코드 6자리를 입력하세요', true);
    return;
  }

  state.code = c;
  state.loading = true;
  state.loadSeq += 1;
  const seq = state.loadSeq;
  syncUrlCode(c);
  renderFavorites();

  const search = $('codeSearch');
  if (search) {
    const knownName = state.name
      || (state.favorites.find((f) => f.code === c) || {}).name
      || '';
    search.value = knownName ? `${knownName} (${c})` : c;
  }
  // 로딩 오버레이는 띄우지 않음(.cob-empty display:flex가 [hidden]을 덮어 차트 위에 남는 문제 방지)
  setChartStatus('');

  try {
    const tasks = [loadChart(c, seq), loadSnapshot(c, seq)];
    // 이름 보강 + 즐겨찾기 표시명 갱신
    tasks.push(
      fetchJSON(`/fundamentals/${encodeURIComponent(c)}`)
        .then((row) => {
          if (seq !== state.loadSeq) return;
          if (row && row.stock_name) {
            state.name = row.stock_name;
            $('stockName').textContent = row.stock_name;
            if ($('codeSearch')) $('codeSearch').value = `${row.stock_name} (${c})`;
            const fav = state.favorites.find((f) => f.code === c);
            if (fav && !fav.name) {
              fav.name = row.stock_name;
              renderFavorites();
            }
          }
        })
        .catch(() => {}),
    );
    await Promise.all(tasks);
    if (seq !== state.loadSeq) return;
    markUpdated();
  } catch (e) {
    if (seq !== state.loadSeq) return;
    const msg = (e && (e.detail || e.message)) || '조회 실패';
    setChartStatus(String(msg), true);
    if (!quiet) toast(String(msg), true);
    const empty = $('chartEmpty');
    if (empty && !state.candleSeries) {
      empty.hidden = false;
      empty.textContent = '조회 실패';
    }
  } finally {
    if (seq === state.loadSeq) state.loading = false;
  }
}

async function refreshSnapshotOnly() {
  const c = state.code;
  if (!c || state.loading) return;
  try {
    const data = await fetchJSON(`/stocks/${encodeURIComponent(c)}/snapshot`);
    renderQuote(data);
    renderOrderbook(data);
    markUpdated();
  } catch (_) {
    /* 자동갱신 실패는 조용히 */
  }
}

function setupAutoRefresh() {
  if (state.autoTimer) {
    clearInterval(state.autoTimer);
    state.autoTimer = null;
  }
  const on = $('autoRefresh') && $('autoRefresh').checked;
  if (!on) return;
  state.autoTimer = setInterval(() => {
    if (document.hidden) return;
    refreshSnapshotOnly();
  }, 10000);
}

function hideSuggest() {
  const ul = $('stockSuggest');
  const input = $('codeSearch');
  if (ul) ul.hidden = true;
  if (input) input.setAttribute('aria-expanded', 'false');
  suggest.active = -1;
}

function renderSuggest() {
  const ul = $('stockSuggest');
  if (!ul) return;
  if (!suggest.items.length) {
    ul.innerHTML = '<li class="empty">검색 결과 없음</li>';
    ul.hidden = false;
    $('codeSearch')?.setAttribute('aria-expanded', 'true');
    return;
  }
  ul.innerHTML = suggest.items.map((s, i) => {
    const on = i === suggest.active ? ' active' : '';
    return `<li class="${on.trim()}" role="option" data-idx="${i}" aria-selected="${i === suggest.active}">
      <span class="name">${esc(s.name || s.code)}</span>
      <span class="code">${esc(s.code)}</span>
      <span class="mkt">${esc(s.market || '')}</span>
    </li>`;
  }).join('');
  ul.hidden = false;
  $('codeSearch')?.setAttribute('aria-expanded', 'true');
}

function pickSuggest(i) {
  const s = suggest.items[i];
  if (!s) return;
  const code = normalizeStockCode(s.code);
  if (!code) return;
  state.name = s.name || '';
  if ($('codeSearch')) $('codeSearch').value = s.name ? `${s.name} (${code})` : code;
  hideSuggest();
  loadAll(code);
}

function onSearchInput() {
  const q = ($('codeSearch')?.value || '').trim();
  clearTimeout(suggest.timer);
  if (!q) {
    suggest.items = [];
    hideSuggest();
    return;
  }
  // 숫자만 6자리면 자동완성 없이 코드로 바로 쓸 수 있게 둠
  const onlyCode = normalizeStockCode(q.replace(/\D/g, ''));
  if (/^\d{6}$/.test(q.trim()) && onlyCode) {
    suggest.items = [];
    hideSuggest();
    return;
  }
  const seq = ++suggest.seq;
  suggest.timer = setTimeout(async () => {
    try {
      const data = await fetchJSON(`/api/ladder/search?q=${encodeURIComponent(q)}&limit=12`);
      if (seq !== suggest.seq) return;
      suggest.items = data.items || [];
      suggest.active = suggest.items.length ? 0 : -1;
      renderSuggest();
    } catch (_) {
      if (seq === suggest.seq) {
        suggest.items = [];
        hideSuggest();
      }
    }
  }, 160);
}

async function resolveAndLoad(raw) {
  const text = String(raw || '').trim();
  if (!text) {
    toast('종목명 또는 코드를 입력하세요', true);
    return;
  }
  // "삼성전자 (005930)" / 숫자 코드
  const fromParen = text.match(/\((\d{4,6})\)\s*$/);
  if (fromParen) {
    await loadAll(fromParen[1]);
    return;
  }
  const digits = text.replace(/\D/g, '');
  if (/^\d{4,6}$/.test(digits) && !/[가-힣a-zA-Z]/.test(text)) {
    await loadAll(digits);
    return;
  }
  try {
    const data = await fetchJSON(`/api/ladder/search?q=${encodeURIComponent(text)}&limit=8`);
    const items = data.items || [];
    if (!items.length) {
      toast('검색 결과가 없습니다', true);
      return;
    }
    const exact = items.find((s) => String(s.name || '').replace(/\s/g, '') === text.replace(/\s/g, ''))
      || items.find((s) => normalizeStockCode(s.code) === normalizeStockCode(text));
    const pick = exact || items[0];
    state.name = pick.name || '';
    if ($('codeSearch')) {
      $('codeSearch').value = pick.name ? `${pick.name} (${normalizeStockCode(pick.code)})` : pick.code;
    }
    hideSuggest();
    await loadAll(pick.code);
  } catch (e) {
    toast((e && e.message) || '검색 실패', true);
  }
}

function onSearchKey(e) {
  const ul = $('stockSuggest');
  if (e.key === 'Escape') {
    if (ul && !ul.hidden) {
      e.preventDefault();
      hideSuggest();
    }
    return;
  }
  if (e.key === 'Enter') {
    e.preventDefault();
    if (ul && !ul.hidden && suggest.active >= 0 && suggest.items.length) {
      pickSuggest(suggest.active);
      return;
    }
    resolveAndLoad($('codeSearch')?.value);
    return;
  }
  if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && suggest.items.length) {
    e.preventDefault();
    const n = suggest.items.length;
    const open = ul && !ul.hidden;
    suggest.active = open
      ? (suggest.active + (e.key === 'ArrowDown' ? 1 : -1) + n) % n
      : 0;
    renderSuggest();
  }
}

function bindEvents() {
  $('btnLoad')?.addEventListener('click', () => resolveAndLoad($('codeSearch')?.value));
  $('btnRefresh')?.addEventListener('click', () => {
    if (state.code) loadAll(state.code);
    else resolveAndLoad($('codeSearch')?.value);
  });
  $('codeSearch')?.addEventListener('input', onSearchInput);
  $('codeSearch')?.addEventListener('keydown', onSearchKey);
  // 포커스 시 기존 검색어 전체 선택 → 바로 타이핑하면 덮어씀 (지우지 않아도 됨)
  $('codeSearch')?.addEventListener('focus', (e) => {
    const el = e.target;
    el.dataset.selectAll = '1';
    requestAnimationFrame(() => {
      if (el.dataset.selectAll === '1') el.select();
    });
    if (suggest.items.length) renderSuggest();
  });
  $('codeSearch')?.addEventListener('mousedown', (e) => {
    // 이미 포커스된 상태에서 클릭하면 캐럿 위치 지정 허용
    if (document.activeElement === e.target) e.target.dataset.selectAll = '0';
  });
  $('codeSearch')?.addEventListener('mouseup', (e) => {
    // 클릭으로 포커스될 때 mouseup이 선택을 풀지 않도록
    if (e.target.dataset.selectAll === '1') e.preventDefault();
  });
  $('stockSuggest')?.addEventListener('mousedown', (e) => {
    const li = e.target.closest('li[data-idx]');
    if (!li) return;
    e.preventDefault();
    pickSuggest(Number(li.dataset.idx));
  });
  document.addEventListener('click', (e) => {
    if (e.target.closest('.cob-search-wrap')) return;
    hideSuggest();
  });
  $('autoRefresh')?.addEventListener('change', setupAutoRefresh);

  document.querySelectorAll('.cob-interval').forEach((btn) => {
    btn.addEventListener('click', () => {
      const iv = btn.dataset.interval;
      if (!iv || iv === state.interval) return;
      setIntervalActive(iv);
      if (state.code) loadAll(state.code, { quiet: true });
    });
  });

  document.querySelectorAll('.cob-ma input[data-ma]').forEach((input) => {
    input.addEventListener('change', applyMaVisibility);
  });
  $('togRsi')?.addEventListener('change', applyRsiVisibility);

  $('btnFavToggle')?.addEventListener('click', () => setFavPanelOpen(!state.favPanelOpen));
  $('btnFavFolder')?.addEventListener('click', () => {
    state.favOpen = !state.favOpen;
    $('btnFavFolder')?.setAttribute('aria-expanded', state.favOpen ? 'true' : 'false');
    renderFavorites();
  });
  $('btnFavAddFolder')?.addEventListener('click', () => createFavoriteFolder());
  $('btnFavRefresh')?.addEventListener('click', async () => {
    await loadFavoritesList();
    toast(`즐겨찾기 ${state.favorites.length}종목 · 폴더 ${state.folders.length}`);
  });
  $('favFilter')?.addEventListener('input', (e) => {
    state.favFilter = e.target.value || '';
    renderFavorites();
  });
  $('favList')?.addEventListener('click', async (e) => {
    const backBtn = e.target.closest('[data-fav-back]');
    if (backBtn) {
      state.activeFolderId = null;
      renderFavorites();
      return;
    }
    const openFolder = e.target.closest('[data-open-folder]');
    if (openFolder) {
      const id = Number(openFolder.dataset.openFolder);
      state.activeFolderId = Number.isFinite(id) ? id : null;
      renderFavorites();
      return;
    }
    const toggle = e.target.closest('[data-toggle-group]');
    if (toggle) {
      const id = Number(toggle.dataset.toggleGroup);
      state.groupOpen[id] = state.groupOpen[id] === false;
      saveGroupOpen();
      renderFavorites();
      return;
    }
    const renameBtn = e.target.closest('[data-rename-folder]');
    if (renameBtn) {
      await renameFavoriteFolder(Number(renameBtn.dataset.renameFolder));
      return;
    }
    const delBtn = e.target.closest('[data-del-folder]');
    if (delBtn) {
      await deleteFavoriteFolder(Number(delBtn.dataset.delFolder));
      return;
    }
    const renameItemBtn = e.target.closest('[data-rename]');
    if (renameItemBtn) {
      e.stopPropagation();
      await renameFavorite(renameItemBtn.dataset.rename);
      return;
    }
    const moveBtn = e.target.closest('[data-move]');
    if (moveBtn) {
      e.stopPropagation();
      await moveFavoriteToFolder(moveBtn.dataset.move);
      return;
    }
    const itemBtn = e.target.closest('.cob-fav-item-main');
    if (!itemBtn) return;
    const code = itemBtn.dataset.code;
    const fav = state.favorites.find((f) => f.code === code);
    if (fav && fav.name) state.name = fav.name;
    loadAll(code);
  });

  const onFavLayoutChange = () => {
    if (!isMobileFavLayout()) state.activeFolderId = null;
    renderFavorites();
  };
  if (typeof MOBILE_FAV_MQ.addEventListener === 'function') {
    MOBILE_FAV_MQ.addEventListener('change', onFavLayoutChange);
  } else if (typeof MOBILE_FAV_MQ.addListener === 'function') {
    MOBILE_FAV_MQ.addListener(onFavLayoutChange);
  }

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && state.code && $('autoRefresh')?.checked) {
      refreshSnapshotOnly();
    }
  });
}

function initialCode() {
  const fromUrl = normalizeStockCode(new URLSearchParams(location.search).get('code'));
  if (fromUrl) return fromUrl;
  if (state.favorites.length) return state.favorites[0].code;
  return DEFAULT_CODE;
}

async function init() {
  bindEvents();
  setFavPanelOpen(true);
  setIntervalActive(state.interval);
  setupAutoRefresh();
  await loadFavoritesList();
  const code = initialCode();
  if ($('codeSearch')) $('codeSearch').value = code;
  loadAll(code, { quiet: true });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
