'use strict';

const $ = (id) => document.getElementById(id);

const SORT_COLS = [
  'stock_name', 'market', 'current_price', 'market_cap',
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
  selectedName: null,
  loading: false,
  lastItems: null,
  lastSummary: null,
  lastListMeta: null,
  favorites: new Map(),
  favoriteFolderIds: new Map(),
  folders: [],
  memos: new Map(),
  reloadAfterLoad: false,
};

let toastTimer = null;
const FAVORITES_KEY = 'analysisFavorites';
const FAV_FOLDERS_KEY = 'analysisFavoriteFolders';
const FAV_LAST_FOLDER_KEY = 'analysisFavoriteLastFolder';
const MEMOS_KEY = 'analysisMemos';
const PREFERENCES_MIGRATED_KEY = 'analysisPreferencesMigrated';
const MAX_MEMO_LENGTH = 5000;
const DEFAULT_STOCK_CODE = '005930';

function normalizeStockCode(value) {
  const raw = String(value == null ? '' : value).trim().replace(/^A/i, '');
  return /^\d{1,6}$/.test(raw) ? raw.padStart(6, '0') : '';
}

function loadFavoriteFolderIds() {
  const map = new Map();
  try {
    const raw = localStorage.getItem(FAVORITES_KEY);
    if (!raw) return map;
    const saved = JSON.parse(raw);
    if (!Array.isArray(saved)) return map;
    saved.forEach((item) => {
      const code = normalizeStockCode(item && typeof item === 'object' ? item.code : item);
      if (!code) return;
      const folderId = item && item.folder_id != null ? Number(item.folder_id) : null;
      map.set(code, Number.isFinite(folderId) ? folderId : null);
    });
  } catch (e) { /* ignore */ }
  return map;
}

function loadLocalFolders() {
  try {
    const raw = localStorage.getItem(FAV_FOLDERS_KEY);
    if (!raw) return [];
    const saved = JSON.parse(raw);
    if (!Array.isArray(saved)) return [];
    return saved.map((f) => ({
      id: Number(f.id),
      name: String(f.name || ''),
      sort_order: Number(f.sort_order) || 0,
    })).filter((f) => Number.isFinite(f.id) && f.id > 0 && f.name)
      .sort((a, b) => (a.sort_order - b.sort_order) || (a.id - b.id));
  } catch (e) {
    return [];
  }
}

function loadLastAddFolderId() {
  try {
    const raw = localStorage.getItem(FAV_LAST_FOLDER_KEY);
    if (raw == null || raw === '') return null;
    if (raw === 'null') return null;
    const id = Number(raw);
    return Number.isFinite(id) ? id : null;
  } catch (e) {
    return null;
  }
}

function saveLastAddFolderId(folderId) {
  try {
    localStorage.setItem(FAV_LAST_FOLDER_KEY, folderId == null ? 'null' : String(folderId));
  } catch (e) { /* ignore */ }
}

function loadFavorites() {
  try {
    const raw = localStorage.getItem(FAVORITES_KEY);
    if (!raw) return new Map();
    const saved = JSON.parse(raw);
    if (!Array.isArray(saved)) throw new Error('즐겨찾기 저장 형식이 올바르지 않습니다.');
    const favorites = new Map();
    saved.forEach((item) => {
      const code = normalizeStockCode(item && typeof item === 'object' ? item.code : item);
      if (code) favorites.set(code, String((item && item.name) || ''));
    });
    return favorites;
  } catch (e) {
    toast('즐겨찾기 정보를 불러오지 못했습니다. 브라우저 저장 공간을 확인하세요.', true);
    return new Map();
  }
}

function saveFavorites(favorites, folderIds) {
  try {
    const prevFolders = folderIds || state.favoriteFolderIds || loadFavoriteFolderIds();
    localStorage.setItem(FAVORITES_KEY, JSON.stringify(
      [...favorites].map(([code, name]) => ({
        code,
        name,
        folder_id: prevFolders.has(code) ? prevFolders.get(code) : null,
      })),
    ));
    return true;
  } catch (e) {
    toast('즐겨찾기를 저장하지 못했습니다. 브라우저 저장 공간을 확인하세요.', true);
    return false;
  }
}

function saveFavoriteFolders(folders) {
  try {
    const rows = (folders || []).map((f) => ({
      id: Number(f.id),
      name: String(f.name || ''),
      sort_order: Number(f.sort_order) || 0,
    })).filter((f) => Number.isFinite(f.id) && f.id > 0 && f.name)
      .sort((a, b) => (a.sort_order - b.sort_order) || (a.id - b.id));
    localStorage.setItem(FAV_FOLDERS_KEY, JSON.stringify(rows));
    state.folders = rows;
    syncFavoriteFolderFilter();
  } catch (e) { /* ignore */ }
}

function folderNameById(folderId) {
  if (folderId == null) return '미분류';
  const folder = state.folders.find((f) => f.id === folderId);
  return folder ? folder.name : '미분류';
}

async function createFavoriteFolder() {
  const name = window.prompt('새 폴더 이름');
  if (name == null) return null;
  const trimmed = name.trim();
  if (!trimmed) {
    toast('폴더 이름을 입력하세요', true);
    return null;
  }
  try {
    const row = await fetchJSON('/api/analysis/favorite-folders', preferenceRequest('POST', { name: trimmed }));
    const folders = [...state.folders, row];
    saveFavoriteFolders(folders);
    toast(`폴더 "${row.name}" 추가`);
    return row;
  } catch (e) {
    toast((e && (e.detail || e.message)) || '폴더 추가 실패', true);
    return null;
  }
}

/** @returns {Promise<number|null|undefined>} folder id, null=미분류, undefined=취소 */
async function pickFavoriteFolder(preferredId) {
  if (!state.folders.length) {
    const make = window.confirm('즐겨찾기 폴더가 없습니다. 새 폴더를 만들까요?\n취소하면 미분류로 추가합니다.');
    if (!make) return null;
    const created = await createFavoriteFolder();
    return created ? created.id : undefined;
  }
  const fallback = preferredId != null && state.folders.some((f) => f.id === preferredId)
    ? preferredId
    : loadLastAddFolderId();
  const defaultIdx = fallback == null
    ? 0
    : (state.folders.findIndex((f) => f.id === fallback) + 1);
  const picked = await showFolderCardPicker({
    title: '넣을 즐겨찾기 폴더',
    folders: state.folders,
    currentId: defaultIdx > 0 ? fallback : null,
    allowNew: true,
  });
  if (!picked) return undefined;
  if (picked.type === 'new') {
    const created = await createFavoriteFolder();
    return created ? created.id : undefined;
  }
  return picked.id;
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

function favoriteCodesInScope() {
  const sel = $('fltFavoriteFolder');
  const scope = sel ? sel.value : '';
  const codes = [];
  for (const code of state.favorites.keys()) {
    const folderId = state.favoriteFolderIds.has(code) ? state.favoriteFolderIds.get(code) : null;
    if (scope === '') {
      codes.push(code);
    } else if (scope === 'none') {
      if (folderId == null) codes.push(code);
    } else if (String(folderId) === scope) {
      codes.push(code);
    }
  }
  return codes;
}

function syncFavoriteFolderFilter() {
  const sel = $('fltFavoriteFolder');
  const wrap = $('fltFavoriteFolderWrap');
  if (!sel) return;
  const prev = sel.value;
  const opts = ['<option value="">전체 폴더</option>', '<option value="none">미분류</option>'];
  state.folders.forEach((f) => {
    opts.push(`<option value="${esc(String(f.id))}">${esc(f.name)}</option>`);
  });
  sel.innerHTML = opts.join('');
  const valid = prev === '' || prev === 'none' || state.folders.some((f) => String(f.id) === prev);
  sel.value = valid ? prev : '';
  if (wrap) {
    const favoritesOnly = Boolean($('fltFavoritesOnly') && $('fltFavoritesOnly').checked);
    wrap.hidden = !favoritesOnly;
    wrap.classList.toggle('is-visible', favoritesOnly);
  }
}

function saveMemos(memos) {
  try {
    localStorage.setItem(MEMOS_KEY, JSON.stringify(Object.fromEntries(memos)));
  } catch (e) {
    toast('분석 메모 브라우저 백업을 저장하지 못했습니다.', true);
  }
}

function preferenceRequest(method, body) {
  return {
    method,
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  };
}

async function loadServerPreferences() {
  const localFavorites = state.favorites;
  const localMemos = state.memos;
  let saved;
  try {
    saved = await fetchJSON('/api/analysis/preferences');
  } catch (e) {
    toast('서버 저장 정보를 불러오지 못했습니다. 브라우저에 남아 있는 저장값을 표시합니다.', true);
    return;
  }

  const favorites = new Map();
  const folderIds = new Map();
  (saved.favorites || []).forEach((item) => {
    const code = normalizeStockCode(item && item.code);
    if (!code) return;
    favorites.set(code, String((item && item.name) || ''));
    const folderId = item && item.folder_id != null ? Number(item.folder_id) : null;
    folderIds.set(code, Number.isFinite(folderId) ? folderId : null);
  });
  const memos = new Map();
  Object.entries(saved.memos || {}).forEach(([key, text]) => {
    const code = normalizeStockCode(key);
    if (code && typeof text === 'string') memos.set(code, text.slice(0, MAX_MEMO_LENGTH));
  });
  const migrationFailures = [];
  let shouldMigrate = true;
  try {
    shouldMigrate = localStorage.getItem(PREFERENCES_MIGRATED_KEY) !== '1';
  } catch (e) {
    toast('기존 저장값 이전 상태를 확인하지 못했습니다.', true);
  }
  const shouldRestoreLocalFavorites = favorites.size === 0 && localFavorites.size > 0;
  if (shouldMigrate || shouldRestoreLocalFavorites) {
    for (const [code, name] of localFavorites) {
      if (favorites.has(code)) continue;
      try {
        await fetchJSON(`/api/analysis/favorites/${encodeURIComponent(code)}`, preferenceRequest('PUT', { stock_name: name }));
        favorites.set(code, name);
        if (!folderIds.has(code)) folderIds.set(code, null);
      } catch (e) {
        favorites.set(code, name);
        if (!folderIds.has(code)) folderIds.set(code, null);
        migrationFailures.push(`즐겨찾기 ${code}`);
      }
    }
    for (const [code, content] of localMemos) {
      if (memos.has(code)) continue;
      try {
        await fetchJSON(`/api/analysis/memos/${encodeURIComponent(code)}`, preferenceRequest('PUT', { content }));
        memos.set(code, content);
      } catch (e) {
        memos.set(code, content);
        migrationFailures.push(`메모 ${code}`);
      }
    }
  }
  state.favorites = favorites;
  state.favoriteFolderIds = folderIds;
  state.memos = memos;
  saveFavoriteFolders(saved.folders || []);
  saveFavorites(favorites, folderIds);
  saveMemos(memos);
  syncFavoriteFolderFilter();
  if (migrationFailures.length) {
    toast(`기존 브라우저 저장값 일부를 서버로 옮기지 못했습니다: ${migrationFailures.join(', ')}`, true);
  } else {
    try { localStorage.setItem(PREFERENCES_MIGRATED_KEY, '1'); } catch (e) {
      toast('기존 저장값 이전 상태를 기억하지 못했습니다.', true);
    }
  }
}

function loadMemos() {
  try {
    const raw = localStorage.getItem(MEMOS_KEY);
    if (!raw) return new Map();
    const saved = JSON.parse(raw);
    if (!saved || typeof saved !== 'object' || Array.isArray(saved)) {
      throw new Error('메모 저장 형식이 올바르지 않습니다.');
    }
    const memos = new Map();
    Object.entries(saved).forEach(([key, text]) => {
      const code = normalizeStockCode(key);
      if (code && typeof text === 'string') memos.set(code, text.slice(0, MAX_MEMO_LENGTH));
    });
    return memos;
  } catch (e) {
    toast('분석 메모를 불러오지 못했습니다. 브라우저 저장 공간을 확인하세요.', true);
    return new Map();
  }
}

async function persistMemo(stockCode, text) {
  const code = normalizeStockCode(stockCode);
  if (!code) return false;
  try {
    await fetchJSON(
      `/api/analysis/memos/${encodeURIComponent(code)}`,
      text ? preferenceRequest('PUT', { content: text }) : preferenceRequest('DELETE'),
    );
  } catch (e) {
    toast('분석 메모를 서버에 저장하지 못했습니다. 로그인과 서버 연결을 확인하세요.', true);
    return false;
  }
  const next = new Map(state.memos);
  if (text) next.set(code, text);
  else next.delete(code);
  state.memos = next;
  saveMemos(next);
  return true;
}

function stockMemoHtml(stockCode) {
  const code = normalizeStockCode(stockCode);
  const text = state.memos.get(code) || '';
  return `<section class="detail-section analysis-memo" data-analysis-memo="${esc(code)}">
    <div class="detail-section-title">분석 메모 <span class="hint">이 종목에 대한 투자 아이디어와 체크 포인트</span></div>
    <label class="analysis-memo-label" for="analysisMemoInput">메모</label>
    <textarea id="analysisMemoInput" class="analysis-memo-input" maxlength="${MAX_MEMO_LENGTH}" placeholder="분석 내용이나 매매 전 확인할 점을 기록하세요.">${esc(text)}</textarea>
    <div class="analysis-memo-footer">
      <span class="analysis-memo-status" data-memo-status aria-live="polite">서버에 종목별로 저장됩니다.</span>
      <span class="analysis-memo-count" data-memo-count>${text.length.toLocaleString('ko-KR')} / ${MAX_MEMO_LENGTH.toLocaleString('ko-KR')}</span>
    </div>
    <div class="analysis-memo-actions">
      <button type="button" class="btn sm primary" data-memo-save>메모 저장</button>
      <button type="button" class="btn sm" data-memo-delete${text ? '' : ' disabled'}>메모 삭제</button>
    </div>
  </section>`;
}

function favoriteButtonHtml(row) {
  const code = normalizeStockCode(row.stock_code);
  const saved = state.favorites.has(code);
  const folderId = saved && state.favoriteFolderIds.has(code) ? state.favoriteFolderIds.get(code) : null;
  const folderLabel = saved ? folderNameById(folderId) : '';
  const label = saved ? `즐겨찾기 삭제 (${folderLabel})` : '즐겨찾기 추가 · 폴더 선택';
  return `<button type="button" class="analysis-favorite-toggle${saved ? ' is-favorite' : ''}" data-code="${esc(code)}" title="${label}" aria-label="${esc(row.stock_name || code)} ${label}" aria-pressed="${saved}">${saved ? '★' : '☆'}</button>`;
}

function syncDetailFavoriteButton() {
  const btn = $('btnToggleFavorite');
  if (!btn) return;
  const code = normalizeStockCode(state.selectedCode);
  if (!code) {
    btn.hidden = true;
    return;
  }
  const saved = state.favorites.has(code);
  const folderId = saved && state.favoriteFolderIds.has(code) ? state.favoriteFolderIds.get(code) : null;
  const folderLabel = saved ? folderNameById(folderId) : '';
  btn.hidden = false;
  btn.textContent = saved ? `★ 즐겨찾기 삭제 (${folderLabel})` : '☆ 즐겨찾기 추가';
  btn.classList.toggle('is-favorite', saved);
  btn.setAttribute('aria-pressed', saved ? 'true' : 'false');
}

async function toggleFavorite(stockCode, stockName) {
  const code = normalizeStockCode(stockCode);
  if (!code) return;
  const next = new Map(state.favorites);
  const nextFolders = new Map(state.favoriteFolderIds);
  const removing = next.has(code);
  let folderId = null;
  if (!removing) {
    const picked = await pickFavoriteFolder(loadLastAddFolderId());
    if (picked === undefined) return;
    folderId = picked;
  }
  try {
    await fetchJSON(
      `/api/analysis/favorites/${encodeURIComponent(code)}`,
      removing
        ? preferenceRequest('DELETE')
        : preferenceRequest('PUT', {
          stock_name: String(stockName || ''),
          folder_id: folderId,
        }),
    );
  } catch (e) {
    toast('즐겨찾기를 서버에 저장하지 못했습니다. 로그인과 서버 연결을 확인하세요.', true);
    return;
  }
  if (removing) {
    next.delete(code);
    nextFolders.delete(code);
  } else {
    next.set(code, String(stockName || ''));
    nextFolders.set(code, folderId);
    saveLastAddFolderId(folderId);
  }
  state.favorites = next;
  state.favoriteFolderIds = nextFolders;
  saveFavorites(next, nextFolders);
  syncDetailFavoriteButton();
  updateFilterSummary();
  syncFavoritesQuickButton();
  syncFavoriteFolderFilter();
  if ($('fltFavoritesOnly').checked) {
    if (state.loading) state.reloadAfterLoad = true;
    else loadList({ autoSelect: false });
  }
  else if (state.lastItems) renderTable(state.lastItems);
  if (removing) toast('즐겨찾기에서 삭제했습니다.', false);
  else toast(`즐겨찾기에 추가했습니다 · ${folderNameById(folderId)}`, false);
}

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
  if (row.ma2060_soon_hit) badge += ' <span class="gc-cond-badge" title="키움 조건식 2060돌파임박 검색 결과 — 20일·60일 이평 골든크로스 임박">2060임박</span>';
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
    || $('fltMa2060SoonOnly').checked
    || $('fltExcludeBelowMa60').checked
    || $('fltFavoritesOnly').checked
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
  if (minCap && !$('fltFavoritesOnly').checked) parts.push(`시총≥${fmtCapThreshold(minCap)}`);
  if (maxPer) parts.push(`PER≤${maxPer}`);
  if (maxPbr) parts.push(`PBR≤${maxPbr}`);
  if (minRoe) parts.push(`ROE≥${minRoe}`);
  if ($('fltWeeklyRsiOnly').checked) parts.push('주RSI');
  if ($('fltDailyRsiOnly').checked) parts.push('일RSI');
  if ($('fltWeeklyWilliamsOnly').checked) parts.push('주WR');
  if ($('fltDailyWilliamsOnly').checked) parts.push('일WR');
  if ($('fltBowlOnly').checked) parts.push('밥그릇');
  if ($('fltMa2060Only').checked) parts.push('2060');
  if ($('fltMa2060SoonOnly').checked) parts.push('2060임박');
  if ($('fltExcludeBelowMa60').checked) parts.push('추세이탈제외');
  if ($('fltFavoritesOnly').checked) {
    const scopeCodes = favoriteCodesInScope();
    const sel = $('fltFavoriteFolder');
    const scope = sel ? sel.value : '';
    let scopeLabel = '즐겨찾기';
    if (scope === 'none') scopeLabel = '미분류';
    else if (scope) scopeLabel = folderNameById(Number(scope));
    parts.push(`${scopeLabel} ${scopeCodes.length}종목`);
  }
  if (($('fltLimit').value || DEFAULT_LIMIT) !== DEFAULT_LIMIT) parts.push(`${$('fltLimit').value}건`);
  el.textContent = parts.join(' · ');
}

function syncFavoritesQuickButton() {
  const btn = $('btnFavoritesQuick');
  const countEl = $('favoritesQuickCount');
  if (!btn) return;
  const active = Boolean($('fltFavoritesOnly') && $('fltFavoritesOnly').checked);
  const count = active ? favoriteCodesInScope().length : state.favorites.size;
  btn.classList.toggle('is-active', active);
  btn.setAttribute('aria-pressed', active ? 'true' : 'false');
  const label = active
    ? `즐겨찾기만 보기 해제${count ? ` (${count}종목)` : ''}`
    : `즐겨찾기만 보기${count ? ` (${count}종목)` : ''}`;
  btn.title = label;
  btn.setAttribute('aria-label', label);
  if (countEl) countEl.textContent = count ? String(count) : '';
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
  syncFavoriteFolderFilter();
  updateFilterSummary();
  syncFavoritesQuickButton();
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
  const favoritesOnly = $('fltFavoritesOnly').checked;
  p.set('limit', favoritesOnly ? '2000' : ($('fltLimit').value || DEFAULT_LIMIT));
  p.set('sort_by', state.sortBy);
  p.set('sort_desc', state.sortDesc ? 'true' : 'false');
  const maxPer = $('fltMaxPer').value.trim();
  const maxPbr = $('fltMaxPbr').value.trim();
  const minRoe = $('fltMinRoe').value.trim();
  const minCap = minCapInput();
  if (!favoritesOnly && minCap) p.set('min_market_cap', minCap);
  if (favoritesOnly) {
    const codes = favoriteCodesInScope();
    if (codes.length) p.set('stock_codes', codes.join(','));
  }
  if (maxPer) p.set('max_per', maxPer);
  if (maxPbr) p.set('max_pbr', maxPbr);
  if (minRoe) p.set('min_roe', minRoe);
  if ($('fltWeeklyRsiOnly').checked) p.set('weekly_rsi_only', 'true');
  if ($('fltDailyRsiOnly').checked) p.set('daily_rsi_only', 'true');
  if ($('fltWeeklyWilliamsOnly').checked) p.set('weekly_williams_only', 'true');
  if ($('fltDailyWilliamsOnly').checked) p.set('daily_williams_only', 'true');
  if ($('fltBowlOnly').checked) p.set('bowl_only', 'true');
  if ($('fltMa2060Only').checked) p.set('ma2060_only', 'true');
  if ($('fltMa2060SoonOnly').checked) p.set('ma2060_soon_only', 'true');
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
    return `<div class="analysis-row-item">
      ${favoriteButtonHtml(row)}
      <button type="button" class="analysis-row${sel}" data-code="${esc(row.stock_code)}">
        <span class="analysis-row-body analysis-row-body-compact">
          <span class="analysis-row-name">${esc(row.stock_name ?? '-')}${badgeHtml(row)}</span>
          <span class="analysis-row-price">${fmtWon(row.current_price)}</span>
        </span>
      </button>
    </div>`;
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
  const favoriteScope = $('fltFavoritesOnly').checked
    ? new Set(favoriteCodesInScope())
    : null;
  const visibleItems = favoriteScope
    ? items.filter((row) => favoriteScope.has(normalizeStockCode(row.stock_code)))
    : items;
  const orderedItems = visibleItems.slice().sort((a, b) => {
    const aFavorite = state.favorites.has(normalizeStockCode(a.stock_code));
    const bFavorite = state.favorites.has(normalizeStockCode(b.stock_code));
    return Number(bFavorite) - Number(aFavorite);
  });
  const listCount = state.lastListMeta && state.lastListMeta.count != null
    ? state.lastListMeta.count
    : visibleItems.length;
  $('statShown').textContent = `${fmtNum(visibleItems.length)} / ${fmtNum(listCount)}`;
  if (!visibleItems.length) {
    const message = $('fltFavoritesOnly').checked && favoriteCodesInScope().length
      ? '선택한 즐겨찾기 폴더 종목이 현재 필터 조건에 없습니다.'
      : $('fltFavoritesOnly').checked
        ? '즐겨찾기에 등록된 종목이 없습니다. 목록의 ☆ 버튼으로 폴더를 골라 추가하세요.'
        : '조건에 맞는 종목이 없습니다.';
    wrap.innerHTML = `<div class="empty"><span class="ico">📭</span>${message}</div>`;
    $('tableHint').textContent = '';
    syncSortDirButton();
    return;
  }
  if (phoneLayout()) {
    renderMobileList(orderedItems);
    $('tableHint').textContent = '';
    syncSortDirButton();
    wrap.querySelectorAll('button.analysis-row').forEach((btn) => {
      btn.onclick = () => selectStock(btn.dataset.code, { scrollDetail: true });
    });
    bindFavoriteButtons(wrap);
    return;
  }
  const head = SORT_COLS.map((col) => {
    const cls = UNSORTABLE_COLS.includes(col) ? [] : ['sortable'];
    if (NUM_COLS.includes(col)) cls.push('num');
    return `<th class="${cls.join(' ')}" data-sort="${col}">${COL_LABELS[col]}<span class="sort-mark">${sortMark(col)}</span></th>`;
  }).join('');
  const rows = orderedItems.map((row) => {
    const sel = row.stock_code === state.selectedCode ? ' selected' : '';
    const cells = SORT_COLS.map((col) => {
      const numCls = NUM_COLS.includes(col) ? ' class="num"' : '';
      return `<td${numCls}>${cellValue(col, row)}</td>`;
    }).join('');
    return `<tr class="data-row${sel}" data-code="${esc(row.stock_code)}"><td class="analysis-favorite-cell">${favoriteButtonHtml(row)}</td>${cells}</tr>`;
  }).join('');
  wrap.innerHTML = `<table class="tbl"><thead><tr><th class="analysis-favorite-cell">즐겨찾기</th>${head}</tr></thead><tbody>${rows}</tbody></table>`;
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
  bindFavoriteButtons(wrap);
  syncSortDirButton();
}

function bindFavoriteButtons(container) {
  container.querySelectorAll('.analysis-favorite-toggle').forEach((btn) => {
    btn.onclick = (event) => {
      event.stopPropagation();
      const row = (state.lastItems || []).find((item) => normalizeStockCode(item.stock_code) === btn.dataset.code);
      toggleFavorite(btn.dataset.code, row && row.stock_name);
    };
  });
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

function clearDetailPanel() {
  $('detailTitle').textContent = '종목 상세';
  $('detailThemes').textContent = '';
  $('detailThemes').dataset.stockCode = '';
  $('detailHint').textContent = '행을 클릭하거나 종목코드를 검색하세요';
  $('detailBody').innerHTML = '<div class="empty"><span class="ico">📊</span>목록에서 종목을 선택하면 상세 지표가 표시됩니다.</div>';
  state.selectedName = null;
  syncDetailFavoriteButton();
  resetChartState();
}

function detailRestHtml(stockCode, { priceBlock, metaBlock, emptyFinance }) {
  const c = String(stockCode || '').trim();
  return `
    ${emptyFinance
      ? '<div class="empty" style="margin-top:12px;"><span class="ico">ℹ️</span>재무 지표는 <code>run_fundamental_batch.bat</code> 적재 후 표시됩니다.</div>'
      : ''}
    ${stockMemoHtml(c)}
    <div class="detail-section">
      <div class="detail-section-title">회사 정보</div>
      ${companyProfilePlaceholderHtml()}
    </div>
    <div class="detail-grid">${priceBlock || indicatorFieldsHtml()}</div>
    ${metaBlock
      ? `<div class="detail-section"><div class="detail-section-title">메타</div><div class="detail-grid">${metaBlock}</div></div>`
      : ''}
    ${stockInvestorHistoryHtml(c)}`;
}

/** 차트 DOM을 먼저 붙이고 일봉 로딩을 즉시 시작 (재무 API와 병행). */
function mountDetailChartShell(stockCode) {
  $('detailBody').innerHTML = `
    <div id="analysisFinanceTop"><div class="skeleton">재무 요약 불러오는 중…</div></div>
    ${chartSectionsHtml()}
    <div id="analysisDetailRest"><div class="skeleton">상세 불러오는 중…</div></div>`;
  applyChartDrawMode();
  return loadAnalysisCharts(stockCode);
}

function fillDetailFromRow(row) {
  state.selectedName = row.stock_name || '';
  syncDetailFavoriteButton();
  $('detailTitle').textContent = `${row.stock_name || ''} (${row.stock_code})`;
  $('detailHint').textContent = `기준일 ${row.as_of_date || '-'} · ${row.market || '-'}`;
  loadAnalysisThemes(row.stock_code);
  const priceBlock = [
    detailField('현재가', fmtWon(row.current_price)),
    detailField('시가총액', fmtEok(row.market_cap)),
    detailField('거래량', fmtNum(row.volume)),
    detailField('거래대금', row.trading_value != null ? fmtNum(row.trading_value) + '백만' : '-'),
    indicatorFieldsHtml(),
  ].join('');
  const metaBlock = [
    detailField('상장주식수', row.listed_shares != null ? fmtNum(row.listed_shares) + '주' : '-'),
    detailField('데이터 출처', esc(row.source || '-')),
    detailField('수집 시각', esc(row.fetched_at || '-')),
  ].join('');
  const fin = $('analysisFinanceTop');
  const rest = $('analysisDetailRest');
  if (fin) fin.innerHTML = financeStripHtml(row);
  if (rest) {
    rest.innerHTML = detailRestHtml(row.stock_code, { priceBlock, metaBlock });
  }
  updateIndicatorFields();
  loadCompanyProfile(row.stock_code);
}

function fillDetailFallback(code, name) {
  const c = String(code || '').trim().replace(/^A/i, '').padStart(6, '0');
  state.selectedName = name || state.selectedName || '';
  syncDetailFavoriteButton();
  $('detailTitle').textContent = name ? `${name} (${c})` : `종목 (${c})`;
  $('detailHint').textContent = '기본적분석 마트에 없음 · 일봉·주봉·월봉 차트만 표시';
  loadAnalysisThemes(c);
  const fin = $('analysisFinanceTop');
  const rest = $('analysisDetailRest');
  if (fin) fin.innerHTML = '<div class="hint">재무 지표 없음</div>';
  if (rest) {
    rest.innerHTML = detailRestHtml(c, {
      priceBlock: indicatorFieldsHtml(),
      emptyFinance: true,
    });
  }
  updateIndicatorFields();
  loadCompanyProfile(c);
}

function renderDetail(row) {
  if (!row) {
    clearDetailPanel();
    return;
  }
  mountDetailChartShell(row.stock_code).then(() => {
    loadStockInvestorHistoryForDetail(row.stock_code);
  });
  fillDetailFromRow(row);
}

function stockInvestorHistoryHtml(stockCode) {
  return `<section class="if-stock-lookup analysis-investor-lookup" data-stock-investor-stock-code="${esc(stockCode)}" aria-label="종목별 외국인·기관 순매수">
    <div class="if-stock-title"><strong>외국인·기관 순매수</strong><label>기간 <select data-investor-days aria-label="수급 조회 기간"><option value="20" selected>20일</option><option value="60">60일</option></select></label></div>
    <div class="if-stock-result" data-stock-investor-result aria-live="polite">차트 조회 후 수급 내역을 불러옵니다.</div>
  </section>`;
}

function loadStockInvestorHistoryForDetail(stockCode) {
  const code = String(stockCode || '').trim().replace(/^A/i, '').replace(/\D/g, '').padStart(6, '0');
  if (!code || code === '000000' || state.selectedCode !== code) return;
  const section = $('detailBody').querySelector(
    `[data-stock-investor-stock-code="${code}"]`
  );
  const result = section && section.querySelector('[data-stock-investor-result]');
  if (result && window.loadStockInvestorHistory) {
    window.loadStockInvestorHistory(result, code);
  }
}

async function loadAnalysisThemes(stockCode) {
  const el = $('detailThemes');
  const code = String(stockCode || '').trim().replace(/^A/i, '').replace(/\D/g, '').padStart(6, '0');
  if (!el || !code || code === '000000') {
    if (el) {
      el.textContent = '';
      el.dataset.stockCode = '';
    }
    return;
  }
  if (state.selectedCode && state.selectedCode !== code) return;
  el.dataset.stockCode = code;
  el.innerHTML = '<span class="analysis-detail-themes-label">테마 조회 중…</span>';
  try {
    const result = await fetchJSON(`/theme-map/stocks/${encodeURIComponent(code)}/tags?limit=100`);
    if (el.dataset.stockCode !== code) return;
    const themes = [];
    const seen = new Set();
    (result.items || []).forEach((item) => {
      const name = String(item.tag_name || '').trim();
      if (item.tag_type !== 'theme' || !name || seen.has(name)) return;
      seen.add(name);
      themes.push(name);
    });
    if (!themes.length) {
      el.textContent = '';
      return;
    }
    const visibleThemes = themes.slice(0, 3)
      .map((name) => `<span class="analysis-detail-theme" title="${esc(name)}">${esc(name)}</span>`);
    if (themes.length > visibleThemes.length) {
      visibleThemes.push(`<span class="analysis-detail-theme-more">외 ${themes.length - 3}개</span>`);
    }
    el.innerHTML = `<span class="analysis-detail-themes-label">테마</span>${visibleThemes.join('')}`;
  } catch (_) {
    if (el.dataset.stockCode === code) {
      el.innerHTML = '<span class="analysis-detail-themes-label">테마 정보를 불러오지 못했습니다</span>';
    }
  }
}

const MA_PERIODS = [5, 10, 20, 60, 120];
const WEEKLY_MA_PERIODS = MA_PERIODS;
const ALL_MA_PERIODS = WEEKLY_MA_PERIODS;
const MA_COLORS = {
  5: '#ff1a1a',
  10: '#ff4fa3',
  20: '#ffd400',
  60: '#2563eb',
  120: '#9ca3af',
  240: '#a855f7',
};
const MA_WIDTHS = {
  5: 1.2,
  10: 1.2,
  20: 2.4,
  60: 2.4,
  120: 3.6,
  240: 2.4,
};
const MA_VISIBLE_KEY = 'analysis.maVisible';
const MA_VISIBLE_DEFAULT = [20, 60, 120];

function loadMaVisible() {
  try {
    const saved = JSON.parse(localStorage.getItem(MA_VISIBLE_KEY));
    if (Array.isArray(saved)) return new Set(saved.filter((n) => ALL_MA_PERIODS.includes(n)));
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

const VP_VISIBLE_KEY = 'analysis.vpVisible';
const VP_BINS = 24;
const VP_MAX_WIDTH_RATIO = 0.28;
let vpVisible = (() => {
  try { return localStorage.getItem(VP_VISIBLE_KEY) !== '0'; } catch (e) { return true; }
})();

function toggleVolumeProfile() {
  vpVisible = !vpVisible;
  try { localStorage.setItem(VP_VISIBLE_KEY, vpVisible ? '1' : '0'); } catch (e) { /* ignore */ }
  CHART_ORDER.forEach((k) => { if (chartPayloads[k]) renderChart(k); });
}

/** 매물대: 각 봉의 거래량을 고가~저가 구간에 겹친 비율만큼 가격 구간(bin)에 나눠 담는다. */
function volumeProfile(payload) {
  if (payload._vp) return payload._vp;
  const bars = payload.bars || [];
  let lo = Infinity;
  let hi = -Infinity;
  bars.forEach((b) => {
    if (!(b.volume > 0)) return;
    lo = Math.min(lo, b.low);
    hi = Math.max(hi, b.high);
  });
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return (payload._vp = null);
  if (hi === lo) hi = lo + 1;
  const step = (hi - lo) / VP_BINS;
  const bins = new Array(VP_BINS).fill(0);
  bars.forEach((b) => {
    const vol = Number(b.volume) || 0;
    if (vol <= 0) return;
    const bLo = Math.min(b.low, b.high);
    const bHi = Math.max(b.low, b.high);
    if (bHi - bLo <= 0) {
      bins[Math.min(VP_BINS - 1, Math.floor((bLo - lo) / step))] += vol;
      return;
    }
    const first = Math.max(0, Math.floor((bLo - lo) / step));
    const last = Math.min(VP_BINS - 1, Math.floor((bHi - lo) / step));
    for (let k = first; k <= last; k++) {
      const overlap = Math.min(bHi, lo + (k + 1) * step) - Math.max(bLo, lo + k * step);
      if (overlap > 0) bins[k] += vol * (overlap / (bHi - bLo));
    }
  });
  const max = Math.max(...bins);
  const poc = bins.indexOf(max);
  const lastClose = bars.length ? bars[bars.length - 1].close : null;
  const cur = lastClose == null ? -1 : Math.max(0, Math.min(VP_BINS - 1, Math.floor((lastClose - lo) / step)));
  return (payload._vp = {
    lo, step, bins, max, poc, cur,
    pocPrice: lo + (poc + 0.5) * step,
  });
}

function drawVolumeProfile(ctx, geo, payload, yOf, theme) {
  const vp = volumeProfile(payload);
  if (!vp || !(vp.max > 0)) return;
  const right = geo.W - geo.padR;
  const maxW = geo.plotW * VP_MAX_WIDTH_RATIO;
  ctx.save();
  ctx.beginPath();
  ctx.rect(geo.padL, geo.price.top, geo.plotW, geo.price.candleH);
  ctx.clip();
  vp.bins.forEach((v, k) => {
    if (v <= 0) return;
    const yTop = yOf(vp.lo + (k + 1) * vp.step);
    const yBot = yOf(vp.lo + k * vp.step);
    const h = Math.max(1, yBot - yTop - 1);
    const w = (v / vp.max) * maxW;
    ctx.fillStyle = k === vp.poc ? theme.vpPoc : theme.vp;
    ctx.globalAlpha = k === vp.poc ? 0.45 : k === vp.cur ? 0.38 : 0.22;
    ctx.fillRect(right - w, yTop + 0.5, w, h);
  });
  ctx.globalAlpha = 1;
  const label = `매물대 최대 ${Math.round(vp.pocPrice).toLocaleString('ko-KR')}`;
  const y = Math.round(yOf(vp.pocPrice));
  ctx.font = '9px system-ui, sans-serif';
  ctx.textAlign = 'right';
  ctx.fillStyle = theme.vpPoc;
  ctx.fillText(label, right - 4, Math.max(geo.price.top + 24, y - 4));
  ctx.restore();
}

const CHART_ORDER = ['1D', '1W', '1MO'];
const CHART_SPECS = {
  '1MO': { title: '월봉 차트', hint: '최근 2년(24개월) · MA5/10/20개월', bars: 24, unit: '월' },
  '1W': { title: '주봉 차트', hint: '최근 104주 · MA5/10/20/60/120주', bars: 104, unit: '주' },
  '1D': { title: '일봉 차트', hint: '최근 9개월(약 189거래일) · MA5/10/20/60/120일', bars: 189, unit: '일' },
};
const CHART_HEIGHT = 340;

function chartPixelHeight() {
  if (!phoneLayout()) return CHART_HEIGHT;
  if (window.matchMedia('(max-height: 500px)').matches) return 210;
  return 260;
}
const PRICE_MARKER_ROOM = 26;

const SIGNAL_META = {
  RSI_OS: { label: 'RSI 과매도 진입', short: 'RSI과매도', shape: 'triangle', color: 'rsi', pane: 'rsi', key: 'rsi', order: 0 },
  WR_BUY: { label: 'Williams 매수', short: 'W매수', shape: 'triangle', color: 'wr', pane: 'wr', key: 'wr', order: 1 },
};

const chartPayloads = { '1MO': null, '1W': null, '1D': null };
let chartLoadSeq = 0;
let chartResizeTimer = null;

const chartPriceLines = {};
// 추세선 끝점은 픽셀이 아니라 (봉 인덱스, 가격)으로 저장해야 리사이즈·MA 토글 후에도 같은 캔들에 붙어 있다
const chartTrendLines = {};
const chartTrendDrafts = {};
const chartDrawingEditVersions = {};
const chartDrawingSaveTimers = {};
const chartDrawingSaveQueues = {};
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
  return `<div class="analysis-chart-tools" role="group" aria-label="차트 그리기">${buttons}<span class="analysis-chart-tool-hint">${CHART_DRAW_HINTS[chartDrawMode]} · 선은 자동 저장됩니다</span></div>`;
}

function applyChartDrawMode() {
  document.querySelectorAll('.analysis-chart-tool').forEach((btn) => {
    const on = btn.dataset.mode === chartDrawMode;
    btn.classList.toggle('on', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  document.querySelectorAll('.analysis-chart-tool-hint').forEach((el) => {
    el.textContent = `${CHART_DRAW_HINTS[chartDrawMode] || ''} · 선은 자동 저장됩니다`;
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
  chartHistory = {};
  chartFocusKey = null;
}

let chartHistory = {};
let chartFocusKey = null;
const CHART_UNDO_LIMIT = 50;
function chartHistoryOf(key) {
  return chartHistory[key] || (chartHistory[key] = { undo: [], redo: [] });
}

function chartSnapshot(key) {
  return {
    price: [...(chartPriceLines[key] || [])],
    trend: (chartTrendLines[key] || []).map((line) => ({ ...line })),
  };
}

function pushChartUndo(key) {
  const h = chartHistoryOf(key);
  h.undo.push(chartSnapshot(key));
  if (h.undo.length > CHART_UNDO_LIMIT) h.undo.shift();
  h.redo.length = 0;
  chartFocusKey = key;
}

function stepChartHistory(key, redo) {
  const h = chartHistory[key];
  const from = h && (redo ? h.redo : h.undo);
  if (!from || !from.length) return false;
  (redo ? h.undo : h.redo).push(chartSnapshot(key));
  const snap = from.pop();
  chartPriceLines[key] = snap.price;
  chartTrendLines[key] = snap.trend;
  saveChartDrawings(key);
  if (chartPayloads[key]) renderChart(key);
  return true;
}

function saveChartDrawings(key, stockCode) {
  const code = String(stockCode || state.selectedCode || '');
  if (!code) return;
  const saveKey = `${code}:${key}`;
  chartDrawingEditVersions[saveKey] = (chartDrawingEditVersions[saveKey] || 0) + 1;
  const payload = {
    price_lines: [...(chartPriceLines[key] || [])],
    trend_lines: (chartTrendLines[key] || []).map((line) => ({ ...line })),
  };
  if (chartDrawingSaveTimers[saveKey]) clearTimeout(chartDrawingSaveTimers[saveKey]);
  const timer = setTimeout(() => {
    delete chartDrawingSaveTimers[saveKey];
    const previous = chartDrawingSaveQueues[saveKey] || Promise.resolve();
    const savePromise = previous
      .catch(() => {})
      .then(() => fetchJSON(`/fundamentals/${encodeURIComponent(code)}/drawings/${key}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }))
      .catch((error) => {
        console.error('차트 선 저장 오류:', error);
        if (state.selectedCode === code) toast('차트 선을 저장하지 못했습니다.', true);
      });
    chartDrawingSaveQueues[saveKey] = savePromise;
    savePromise.then(() => {
      if (chartDrawingSaveQueues[saveKey] === savePromise) delete chartDrawingSaveQueues[saveKey];
    });
  }, 250);
  chartDrawingSaveTimers[saveKey] = timer;
}

async function loadChartDrawings(stockCode, seq) {
  const versionsAtRequest = Object.fromEntries(
    CHART_ORDER.map((key) => [`${stockCode}:${key}`, chartDrawingEditVersions[`${stockCode}:${key}`] || 0]),
  );
  try {
    const data = await fetchJSON(`/fundamentals/${encodeURIComponent(stockCode)}/drawings`);
    if (seq !== chartLoadSeq) return;
    CHART_ORDER.forEach((key) => {
      const saveKey = `${stockCode}:${key}`;
      if ((chartDrawingEditVersions[saveKey] || 0) !== versionsAtRequest[saveKey]) return;
      const saved = (data.drawings && data.drawings[key]) || {};
      chartPriceLines[key] = Array.isArray(saved.price_lines) ? saved.price_lines : [];
      chartTrendLines[key] = Array.isArray(saved.trend_lines) ? saved.trend_lines : [];
      if (chartPayloads[key]) renderChart(key);
    });
  } catch (error) {
    if (seq !== chartLoadSeq) return;
    console.error('차트 선 불러오기 오류:', error);
    toast('저장된 차트 선을 불러오지 못했습니다.', true);
  }
}

function indicatorFieldsHtml() {
  return [
    ...MA_PERIODS.map((n) => detailField(`MA${n}`, `<span id="detailMa${n}">-</span>`)),
    detailField('주봉 RSI', '<span id="detailRsi1W">-</span>'),
    detailField('주봉 %R', '<span id="detailWr1W">-</span>'),
    detailField('일봉 RSI', '<span id="detailRsi1D">-</span>'),
    detailField('일봉 %R', '<span id="detailWr1D">-</span>'),
    detailField('월봉 RSI', '<span id="detailRsi1MO">-</span>'),
    detailField('월봉 %R', '<span id="detailWr1MO">-</span>'),
  ].join('');
}

function debtRatioPct(row) {
  const assets = parseNum(row.total_assets);
  const debt = parseNum(row.total_debt);
  if (assets === null || debt === null || assets <= 0) return null;
  return (debt / assets) * 100;
}

function financeScore(row) {
  const roe = parseNum(row.roe);
  const per = parseNum(row.per);
  const pbr = parseNum(row.pbr);
  const op = parseNum(row.operating_profit);
  const debtPct = debtRatioPct(row);
  let score = 50;
  let used = 0;

  if (roe !== null) {
    used += 1;
    if (roe >= 15) score += 20;
    else if (roe >= 10) score += 12;
    else if (roe >= 5) score += 5;
    else if (roe < 0) score -= 12;
    else score -= 3;
  }
  if (per !== null) {
    used += 1;
    if (per > 0 && per <= 12) score += 15;
    else if (per > 0 && per <= 18) score += 8;
    else if (per > 30) score -= 10;
    else if (per <= 0) score -= 6;
  }
  if (pbr !== null) {
    used += 1;
    if (pbr > 0 && pbr <= 1) score += 10;
    else if (pbr > 0 && pbr <= 1.5) score += 5;
    else if (pbr > 3) score -= 6;
  }
  if (debtPct !== null) {
    used += 1;
    if (debtPct < 30) score += 10;
    else if (debtPct < 50) score += 5;
    else if (debtPct > 100) score -= 15;
    else if (debtPct > 80) score -= 8;
  }
  if (op !== null) {
    used += 1;
    if (op > 0) score += 5;
    else if (op < 0) score -= 10;
  }

  if (!used) return null;
  score = Math.max(0, Math.min(100, Math.round(score)));
  let grade = 'C';
  if (score >= 80) grade = 'A';
  else if (score >= 65) grade = 'B';
  else if (score >= 50) grade = 'C';
  else if (score >= 35) grade = 'D';
  else grade = 'E';
  return { score, grade };
}

function financeStripHtml(row) {
  const scored = financeScore(row);
  const debtPct = debtRatioPct(row);
  const parts = [
    ['ROE', fmtPct(row.roe)],
    ['PER', fmtFloat(row.per, 2)],
    ['PBR', fmtFloat(row.pbr, 2)],
    ['매출', fmtEok(row.revenue)],
    ['영업익', fmtEok(row.operating_profit)],
    ['부채비율', debtPct === null ? '-' : fmtFloat(debtPct, 1) + '%'],
    ['배당', fmtWon(row.dividend_per_share)],
  ].filter(([, v]) => v && v !== '-');

  const metrics = parts
    .map(([k, v]) => `<span class="analysis-fin-metric"><span class="k">${esc(k)}</span><span class="v">${v}</span></span>`)
    .join('<span class="analysis-fin-sep" aria-hidden="true">·</span>');

  const badge = scored
    ? `<span class="analysis-fin-score grade-${scored.grade}" title="ROE·PER·PBR·부채비율·영업이익 기반 간이 재무지수">${esc(scored.grade)} ${scored.score}</span>`
    : `<span class="analysis-fin-score grade-na" title="재무지수 산출 불가">-</span>`;

  return `<div class="analysis-fin-line" aria-label="재무 요약">
    ${badge}
    <div class="analysis-fin-metrics">${metrics || '<span class="hint">재무 지표 없음</span>'}</div>
  </div>`;
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
  return interval === '1W' || interval === '1MO' ? `${s.slice(2, 4)}.${s.slice(5, 7)}` : s.slice(5, 10);
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
    vp: v('--vp-bar', '#94a3b8'),
    vpPoc: v('--vp-poc', '#f59e0b'),
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
  const padB = 6;
  const gap = 6;
  const avail = H - padT - padB - gap;
  const rsiH = Math.max(40, Math.round(avail * 0.14));
  const priceH = avail - rsiH;
  const plotW = W - padL - padR;
  const slotW = plotW / Math.max(n, 1);
  const price = { top: padT, h: priceH };
  const rsi = { top: price.top + priceH + gap, h: rsiH };
  return {
    W, H, padL, padR, padT, padB, plotW, slotW, price, rsi,
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
  const maPeriods = String(payload.interval || '').toUpperCase() === '1W' ? WEEKLY_MA_PERIODS : MA_PERIODS;
  const pane = geo.price;
  let lo = Infinity;
  let hi = -Infinity;
  bars.forEach((b) => {
    lo = Math.min(lo, b.low, b.open, b.close);
    hi = Math.max(hi, b.high, b.open, b.close);
    maPeriods.forEach((n) => {
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

  if (vpVisible) drawVolumeProfile(ctx, geo, payload, yOf, theme);

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

  maPeriods.slice().reverse().forEach((n) => {
    if (!maVisible.has(n)) return;
    drawMaLine(ctx, bars, `ma${n}`, MA_COLORS[n], MA_WIDTHS[n], geo, yOf);
  });
  geo.legendHits = drawChartLegend(ctx, [
    ...maPeriods.map((n) => ({
      id: n, label: `MA${n}`, color: MA_COLORS[n], width: MA_WIDTHS[n], off: !maVisible.has(n),
    })),
    { id: 'vp', label: '매물대', color: theme.vpPoc, width: 6, off: !vpVisible },
  ], geo.W, pane.top);
}

function oscSpecs(payload, theme) {
  const p = payload.params || {};
  const rsiOs = parseNum(p.rsi_os) ?? 30;
  return {
    rsi: {
      key: 'rsi', min: 0, max: 100, levels: [70, rsiOs], zone: rsiOs,
      label: `RSI(${p.rsi_period || 14})`, color: theme.sig.rsi,
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
      if (geo[meta.pane] && geo[meta.pane].yOf && v != null && Number.isFinite(v)) {
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
  pushChartUndo(key);
  trends.splice(near, 1);
  saveChartDrawings(key);
  renderChart(key);
  return true;
}

function togglePriceLineAt(key, geo, my) {
  const pane = geo.price;
  const lines = chartPriceLines[key] || (chartPriceLines[key] = []);
  const near = lines.findIndex((p) => Math.abs(pane.yOf(p) - my) <= chartLineHitPx());
  pushChartUndo(key);
  if (near >= 0) lines.splice(near, 1);
  else lines.push(pane.priceOf(my));
  saveChartDrawings(key);
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
  const maPeriods = String(payload.interval || '').toUpperCase() === '1W' ? WEEKLY_MA_PERIODS : MA_PERIODS;
  const ma = maPeriods
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
    chartFocusKey = key;
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
      if (commit && draft) {
        pushChartUndo(key);
        (chartTrendLines[key] || (chartTrendLines[key] = [])).push(draft);
        saveChartDrawings(key);
      }
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
      if (hit.id === 'vp') toggleVolumeProfile();
      else toggleMaVisible(hit.id);
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
  const vp = volumeProfile(payload);
  if (vp) parts.push(`매물대 최대 ${Math.round(vp.pocPrice).toLocaleString('ko-KR')}원 부근`);
  if (payload.data_source) {
    const srcLabel = payload.data_source === 'mart'
      ? '마트'
      : (payload.data_source === 'kiwoom' ? '키움API' : payload.data_source);
    parts.push(`출처 ${esc(srcLabel)}`);
  }
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

  observeChartWrap(wrap);
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

async function loadAnalysisCharts(stockCode) {
  resetChartState();
  const seq = chartLoadSeq;
  updateIndicatorFields();
  // 일봉 먼저 그려서 체감 속도를 올리고, 주·월봉·그리기는 일봉 이후에 병행
  const drawings = loadChartDrawings(stockCode, seq);
  await loadOneChart('1D', stockCode, seq);
  if (seq !== chartLoadSeq) return;
  await Promise.all([
    loadOneChart('1W', stockCode, seq),
    loadOneChart('1MO', stockCode, seq),
    drawings,
  ]);
}

let chartWrapObserver = null;
function observeChartWrap(wrap) {
  if (!wrap || wrap._chartObserved || typeof ResizeObserver === 'undefined') return;
  if (!chartWrapObserver) {
    chartWrapObserver = new ResizeObserver((entries) => {
      let changed = false;
      entries.forEach((e) => {
        const w = Math.round(e.contentRect.width);
        if (w > 0 && w !== e.target._chartLastW) {
          e.target._chartLastW = w;
          changed = true;
        }
      });
      if (changed) onChartResize();
    });
  }
  wrap._chartObserved = true;
  chartWrapObserver.observe(wrap);
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
  const chartsPromise = $('chartCanvas1D')
    ? Promise.resolve()
    : mountDetailChartShell(c);
  fillDetailFallback(c, name);
  await chartsPromise;
  loadStockInvestorHistoryForDetail(c);
}

async function selectStock(code, options) {
  const c = String(code || '').trim().replace(/^A/i, '').replace(/\D/g, '').padStart(6, '0');
  if (!c || c === '000000') return;
  state.selectedCode = c;
  const selectedRow = (state.lastItems || []).find((row) => normalizeStockCode(row.stock_code) === c);
  state.selectedName = selectedRow ? selectedRow.stock_name : '';
  syncDetailFavoriteButton();
  syncUrlCode(c);
  const search = $('codeSearch');
  if (search) search.value = c;
  markSelectedCode(c);
  $('detailThemes').dataset.stockCode = c;
  $('detailThemes').innerHTML = '<span class="analysis-detail-themes-label">테마 조회 중…</span>';
  $('detailTitle').textContent = state.selectedName ? `${state.selectedName} (${c})` : `종목 (${c})`;
  $('detailHint').textContent = '상세 불러오는 중…';
  if (options && options.scrollDetail) scrollAnalysisTarget('detailCard');
  // 재무 API를 기다리지 않고 일봉 차트부터 요청
  const chartsPromise = mountDetailChartShell(c);
  try {
    const row = await fetchJSON(`/fundamentals/${encodeURIComponent(c)}`);
    if (state.selectedCode !== c) return;
    fillDetailFromRow(row);
  } catch (e) {
    if (state.selectedCode !== c) return;
    fillDetailFallback(c);
    if (!(e && e.status === 404)) {
      toast('재무 지표 없음 · 차트·회사 정보만 표시', false);
    }
  }
  // 수급 DOM은 fill 이후에 생기므로, 재무 채운 뒤 + 차트 끝난 뒤에 조회
  Promise.resolve(chartsPromise).finally(() => {
    if (state.selectedCode === c) loadStockInvestorHistoryForDetail(c);
  });
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
  if (state.loading) {
    state.reloadAfterLoad = true;
    return;
  }
  state.loading = true;
  const wrap = $('tableWrap');
  if (wrap && !wrap.querySelector('.tbl, .analysis-list')) wrap.innerHTML = '<div class="skeleton">데이터 불러오는 중…</div>';
  try {
    let summary;
    let list;
    if ($('fltFavoritesOnly').checked && !favoriteCodesInScope().length) {
      summary = await loadSummary();
      list = { items: [], count: 0, condition_sets: [] };
    } else {
      [summary, list] = await Promise.all([
        loadSummary(),
        fetchJSON(`/fundamentals?${buildQuery()}`, { timeoutMs: 60000 }),
      ]);
    }
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
    if (state.reloadAfterLoad) {
      state.reloadAfterLoad = false;
      loadList({ autoSelect: false });
    }
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
  $('fltMa2060SoonOnly').checked = false;
  $('fltExcludeBelowMa60').checked = false;
  $('fltFavoritesOnly').checked = false;
  if ($('fltFavoriteFolder')) $('fltFavoriteFolder').value = '';
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
  if (p.get('favorites') === '1') $('fltFavoritesOnly').checked = true;
  const sortBy = p.get('sort_by');
  if (SORT_COLS.includes(sortBy)) {
    state.sortBy = sortBy;
    $('fltSortBy').value = state.sortBy;
  }
  const code = codeFromUrl() || DEFAULT_STOCK_CODE;
  state.selectedCode = code;
  const search = $('codeSearch');
  if (search) search.value = code;
}

function onPhoneLayoutChange() {
  const next = phoneLayout();
  if (next === lastPhoneLayout) return;
  lastPhoneLayout = next;
  applyFilterPanel();
  if (state.lastItems) renderTable(state.lastItems);
}

document.addEventListener('DOMContentLoaded', async () => {
  state.favorites = loadFavorites();
  state.favoriteFolderIds = loadFavoriteFolderIds();
  state.folders = loadLocalFolders();
  state.memos = loadMemos();
  await loadServerPreferences();
  if (state.lastItems) renderTable(state.lastItems);
  initFromUrl();
  if (phoneLayout() && hasActiveFilters()) filterUserOpen = true;
  applyFilterPanel();
  syncSortDirButton();
  $('btnRefresh').onclick = () => loadList();
  $('btnFilterToggle').onclick = () => {
    filterUserOpen = !filterUserOpen;
    applyFilterPanel();
  };
  $('btnFavoritesQuick').onclick = () => {
    const cb = $('fltFavoritesOnly');
    if (!cb) return;
    cb.checked = !cb.checked;
    applyFilterPanel();
    loadList({ autoSelect: false });
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
  $('fltFavoritesOnly').onchange = () => {
    applyFilterPanel();
    loadList({ autoSelect: false });
  };
  if ($('fltFavoriteFolder')) {
    $('fltFavoriteFolder').onchange = () => {
      updateFilterSummary();
      syncFavoritesQuickButton();
      if ($('fltFavoritesOnly').checked) loadList({ autoSelect: false });
    };
  }
  $('fltSortBy').onchange = () => {
    state.sortBy = $('fltSortBy').value;
    updateFilterSummary();
  };
  const codeSearch = $('codeSearch');
  // 포커스 시 기존 코드 전체 선택 → 바로 타이핑하면 덮어씀
  codeSearch.addEventListener('focus', (e) => {
    const el = e.target;
    el.dataset.selectAll = '1';
    requestAnimationFrame(() => {
      if (el.dataset.selectAll === '1') el.select();
    });
  });
  codeSearch.addEventListener('mousedown', (e) => {
    if (document.activeElement === e.target) e.target.dataset.selectAll = '0';
  });
  codeSearch.addEventListener('mouseup', (e) => {
    if (e.target.dataset.selectAll === '1') e.preventDefault();
  });
  const codeSearchList = $('codeSearchList');
  let searchSeq = 0;
  const searchStocks = async (q) => {
    try {
      const data = await fetchJSON(`/api/ladder/search?q=${encodeURIComponent(q)}&limit=15`);
      return (data && data.items) || [];
    } catch (_) {
      return [];
    }
  };
  const parseSearchCode = (raw) => {
    const m = raw.match(/\((\d{6})\)\s*$/) || raw.match(/^(\d{6})$/);
    return m ? m[1] : '';
  };
  const submitSearch = async () => {
    const raw = codeSearch.value.trim();
    if (!raw) return;
    let code = parseSearchCode(raw);
    if (!code) {
      const items = await searchStocks(raw);
      if (!items.length) { toast(`'${raw}' 종목을 찾을 수 없습니다.`, true); return; }
      code = items[0].code;
    }
    selectStock(code, { scrollDetail: true });
  };
  codeSearch.addEventListener('input', async () => {
    const raw = codeSearch.value.trim();
    const code = parseSearchCode(raw);
    if (code && raw.includes('(')) { submitSearch(); return; }
    if (!raw || code) { codeSearchList.innerHTML = ''; return; }
    const seq = ++searchSeq;
    const items = await searchStocks(raw);
    if (seq !== searchSeq) return;
    codeSearchList.innerHTML = items
      .map((r) => `<option value="${esc(`${r.name} (${r.code})`)}"></option>`)
      .join('');
  });
  codeSearch.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    submitSearch();
  });
  const pendingCode = state.selectedCode;
  if (pendingCode) selectStock(pendingCode, { scrollDetail: true });
  loadList({ autoSelect: !pendingCode });
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.analysis-chart-tool');
    if (!btn) return;
    setChartDrawMode(btn.dataset.mode);
  });
  document.addEventListener('input', (e) => {
    const textarea = e.target.closest('.analysis-memo-input');
    if (!textarea) return;
    const section = textarea.closest('[data-analysis-memo]');
    const count = section && section.querySelector('[data-memo-count]');
    const status = section && section.querySelector('[data-memo-status]');
    if (count) count.textContent = `${textarea.value.length.toLocaleString('ko-KR')} / ${MAX_MEMO_LENGTH.toLocaleString('ko-KR')}`;
    if (status) status.textContent = '수정된 내용이 있습니다. 저장 버튼을 눌러주세요.';
  });
  document.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-memo-save], [data-memo-delete]');
    if (!btn) return;
    const section = btn.closest('[data-analysis-memo]');
    const textarea = section && section.querySelector('.analysis-memo-input');
    const status = section && section.querySelector('[data-memo-status]');
    const code = section && section.dataset.analysisMemo;
    if (!textarea || !code) return;
    if (btn.matches('[data-memo-save]')) {
      if (!textarea.value.trim()) {
        toast('저장할 메모를 입력하세요. 기존 메모를 지우려면 메모 삭제를 눌러주세요.', true);
        return;
      }
      if (!await persistMemo(code, textarea.value)) return;
      section.querySelector('[data-memo-delete]').disabled = false;
      if (status) status.textContent = '메모를 저장했습니다.';
      toast('분석 메모를 저장했습니다.', false);
      return;
    }
    if (!state.memos.has(normalizeStockCode(code))) return;
    if (!await persistMemo(code, '')) return;
    textarea.value = '';
    section.querySelector('[data-memo-count]').textContent = `0 / ${MAX_MEMO_LENGTH.toLocaleString('ko-KR')}`;
    btn.disabled = true;
    if (status) status.textContent = '메모를 삭제했습니다.';
    toast('분석 메모를 삭제했습니다.', false);
  });
  window.addEventListener('resize', onChartResize);
  document.addEventListener('keydown', (e) => {
    if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
    const k = e.key.toLowerCase();
    const redo = k === 'y' || (k === 'z' && e.shiftKey);
    if (k !== 'z' && k !== 'y') return;
    const t = e.target;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    if (!chartFocusKey || !$(`chartCanvas${chartFocusKey}`)) return;
    if (stepChartHistory(chartFocusKey, redo)) e.preventDefault();
  });
  if (PHONE_MQ.addEventListener) PHONE_MQ.addEventListener('change', onPhoneLayoutChange);
  else if (PHONE_MQ.addListener) PHONE_MQ.addListener(onPhoneLayoutChange);
});
