'use strict';

const GROUP_KO = {
  trading: '매매',
  account: '계좌',
  positions: '포지션',
  'sell-orders': '매도주문',
  signals: '신호',
  conditions: '조건식',
  watchlist: '관심종목',
  strategies: '전략',
  strategy: '전략',
  screener: '스크리너',
  sangtta: '상따',
  breakout: '돌파',
  fractal: '프랙탈',
  jongga: '종가배팅',
  ma1592: '15/92홀드',
  stocks: '종목',
  chart: '차트',
  'theme-map': '테마맵',
  tags: '태그',
  keywords: '키워드',
  themes: '테마',
  fundamentals: '기본적분석',
  indicators: '지표',
  market: '시장',
  monitoring: '모니터링',
  'stop-loss': '손절모니터',
  'buy-executor': '매수실행',
  scalping: '스캘핑',
  cleanup: '정리',
  debug: '디버그',
  telegram: '텔레그램',
  'batch-status': '배치',
  verification: '검증',
  performance: '성과',
  auth: '인증',
  api: '시스템',
  health: '상태',
  login: '로그인',
};

let ALL_ENDPOINTS = [];
let ACTIVE_GROUP = 'all';

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function groupOf(path) {
  const parts = String(path || '').split('/').filter(Boolean);
  return parts[0] || 'root';
}

function groupLabel(key) {
  return GROUP_KO[key] || key;
}

function collectParams(op) {
  const params = Array.isArray(op.parameters) ? op.parameters.slice() : [];
  const body = op.requestBody && op.requestBody.content;
  const json = body && (body['application/json'] || body['application/x-www-form-urlencoded']);
  return { params, hasBody: Boolean(json), bodySchema: json && json.schema };
}

function pathParams(path) {
  return [...String(path).matchAll(/\{([^}]+)\}/g)].map((m) => m[1]);
}

function renderParamRow(p, isPath) {
  const name = p.name || '';
  const loc = isPath ? 'path' : (p.in || 'query');
  const req = p.required || isPath ? '필수' : '선택';
  const schema = p.schema || {};
  const typ = schema.type || (schema.anyOf ? 'any' : '') || '';
  const def = schema.default != null ? String(schema.default) : '';
  const desc = p.description || '';
  return `<tr>
    <td><code>${esc(name)}</code><div class="hint">${esc(loc)} · ${esc(req)}${typ ? ` · ${esc(typ)}` : ''}</div></td>
    <td><input data-param="${esc(name)}" data-in="${esc(loc)}" placeholder="${esc(desc || name)}" value="${esc(def)}"></td>
  </tr>`;
}

function renderEndpoint(ep, idx) {
  const { params, hasBody } = collectParams(ep.op);
  const pathPs = pathParams(ep.path).filter((n) => !params.some((p) => p.name === n && p.in === 'path'));
  const rows = [
    ...pathPs.map((n) => renderParamRow({ name: n, in: 'path', required: true }, true)),
    ...params.map((p) => renderParamRow(p, p.in === 'path')),
  ].join('');
  const paramTable = rows
    ? `<table class="api-params"><thead><tr><th>파라미터</th><th>값</th></tr></thead><tbody>${rows}</tbody></table>`
    : '<p class="hint" style="margin-top:10px;">파라미터 없음</p>';
  const bodyBlock = hasBody
    ? `<label class="hint" style="display:block;margin-top:10px;">JSON 본문</label>
       <textarea class="api-body-input" data-body rows="5">{}</textarea>`
    : '';
  return `<article class="api-ep" data-idx="${idx}" id="ep-${idx}">
    <button type="button" class="api-ep-head" data-toggle="${idx}">
      <span class="api-method ${esc(ep.method)}">${esc(ep.method)}</span>
      <span>
        <div class="api-path">${esc(ep.path)}</div>
        <div class="api-sum">${esc(ep.summary || ep.op.description || '')}</div>
      </span>
    </button>
    <div class="api-ep-body">
      ${paramTable}
      ${bodyBlock}
      <div class="api-actions">
        <button type="button" class="btn sm primary" data-try="${idx}">호출</button>
        <span class="api-status" data-status="${idx}"></span>
      </div>
      <pre class="api-result" data-result="${idx}" hidden></pre>
    </div>
  </article>`;
}

function renderList() {
  const q = (document.getElementById('apiSearch').value || '').trim().toLowerCase();
  const filtered = ALL_ENDPOINTS.filter((ep) => {
    if (ACTIVE_GROUP !== 'all' && ep.group !== ACTIVE_GROUP) return false;
    if (!q) return true;
    const blob = `${ep.method} ${ep.path} ${ep.summary || ''} ${ep.op.description || ''}`.toLowerCase();
    return blob.includes(q);
  });
  document.getElementById('apiCount').textContent = String(filtered.length);
  const byGroup = {};
  filtered.forEach((ep, i) => {
    const g = ep.group;
    (byGroup[g] || (byGroup[g] = [])).push({ ep, idx: ALL_ENDPOINTS.indexOf(ep), i });
  });
  const keys = Object.keys(byGroup).sort((a, b) => groupLabel(a).localeCompare(groupLabel(b), 'ko'));
  if (!keys.length) {
    document.getElementById('apiList').innerHTML = '<div class="empty">일치하는 엔드포인트가 없습니다.</div>';
    return;
  }
  document.getElementById('apiList').innerHTML = keys.map((g) => {
    const items = byGroup[g];
    return `<section class="api-group" id="grp-${esc(g)}">
      <div class="api-group-head">
        <h3>${esc(groupLabel(g))}</h3>
        <span class="hint">${items.length}개 · <code>/${esc(g)}</code></span>
      </div>
      ${items.map(({ ep, idx }) => renderEndpoint(ep, idx)).join('')}
    </section>`;
  }).join('');
}

function renderGroupNav() {
  const counts = {};
  ALL_ENDPOINTS.forEach((ep) => { counts[ep.group] = (counts[ep.group] || 0) + 1; });
  const keys = Object.keys(counts).sort((a, b) => groupLabel(a).localeCompare(groupLabel(b), 'ko'));
  const nav = document.getElementById('groupNav');
  nav.innerHTML = [
    `<button type="button" data-group="all" class="${ACTIVE_GROUP === 'all' ? 'active' : ''}">전체 ${ALL_ENDPOINTS.length}</button>`,
    ...keys.map((g) =>
      `<button type="button" data-group="${esc(g)}" class="${ACTIVE_GROUP === g ? 'active' : ''}">${esc(groupLabel(g))} ${counts[g]}</button>`
    ),
  ].join('');
}

function fillUrl(path, form) {
  let url = path;
  const query = new URLSearchParams();
  form.querySelectorAll('[data-param]').forEach((el) => {
    const name = el.getAttribute('data-param');
    const loc = el.getAttribute('data-in') || 'query';
    const val = (el.value || '').trim();
    if (!val) return;
    if (loc === 'path') url = url.replace(`{${name}}`, encodeURIComponent(val));
    else query.set(name, val);
  });
  const qs = query.toString();
  return qs ? `${url}?${qs}` : url;
}

async function tryCall(idx) {
  const ep = ALL_ENDPOINTS[idx];
  const card = document.getElementById(`ep-${idx}`);
  const statusEl = card.querySelector(`[data-status="${idx}"]`);
  const resultEl = card.querySelector(`[data-result="${idx}"]`);
  const method = ep.method;
  if (method !== 'GET' && method !== 'HEAD') {
    const ok = window.confirm(`${method} ${ep.path}\n\n실제 서버 상태를 바꿀 수 있습니다. 호출할까요?`);
    if (!ok) return;
  }
  const url = fillUrl(ep.path, card);
  if (url.includes('{')) {
    statusEl.textContent = '경로 파라미터를 채워 주세요.';
    statusEl.className = 'api-status err';
    return;
  }
  const opts = {
    method,
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
  };
  const bodyEl = card.querySelector('[data-body]');
  if (bodyEl && method !== 'GET' && method !== 'HEAD') {
    const raw = (bodyEl.value || '').trim();
    if (raw) {
      try {
        JSON.parse(raw);
      } catch (e) {
        statusEl.textContent = 'JSON 본문이 올바르지 않습니다.';
        statusEl.className = 'api-status err';
        return;
      }
      opts.headers['Content-Type'] = 'application/json';
      opts.body = raw;
    }
  }
  statusEl.textContent = '호출 중…';
  statusEl.className = 'api-status';
  resultEl.hidden = false;
  resultEl.textContent = '';
  const t0 = performance.now();
  try {
    const res = await fetch(url, opts);
    const ms = Math.round(performance.now() - t0);
    const ct = (res.headers.get('content-type') || '').toLowerCase();
    let text = await res.text();
    if (ct.includes('json')) {
      try { text = JSON.stringify(JSON.parse(text), null, 2); } catch (_) { /* keep raw */ }
    }
    statusEl.textContent = `${res.status} ${res.statusText} · ${ms}ms`;
    statusEl.className = `api-status ${res.ok ? 'ok' : 'err'}`;
    resultEl.textContent = text || '(빈 응답)';
  } catch (e) {
    statusEl.textContent = `실패: ${e.message || e}`;
    statusEl.className = 'api-status err';
    resultEl.textContent = String(e);
  }
}

function parseOpenApi(spec) {
  const paths = spec.paths || {};
  const out = [];
  Object.keys(paths).sort().forEach((path) => {
    const item = paths[path] || {};
    ['get', 'post', 'put', 'patch', 'delete'].forEach((m) => {
      if (!item[m]) return;
      out.push({
        method: m.toUpperCase(),
        path,
        group: groupOf(path),
        summary: item[m].summary || '',
        op: item[m],
      });
    });
  });
  return out;
}

async function loadSpec() {
  const list = document.getElementById('apiList');
  list.innerHTML = '<div class="skeleton">OpenAPI 스키마 로딩 중…</div>';
  try {
    const res = await fetch('/openapi.json', { credentials: 'same-origin' });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const spec = await res.json();
    ALL_ENDPOINTS = parseOpenApi(spec);
    renderGroupNav();
    renderList();
  } catch (e) {
    list.innerHTML = `<div class="empty">스키마를 불러오지 못했습니다. ${esc(e.message || e)}</div>`;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  loadSpec();
  document.getElementById('btnRefresh').onclick = () => loadSpec();
  document.getElementById('apiSearch').oninput = () => renderList();
  document.getElementById('groupNav').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-group]');
    if (!btn) return;
    ACTIVE_GROUP = btn.getAttribute('data-group') || 'all';
    renderGroupNav();
    renderList();
  });
  document.getElementById('apiList').addEventListener('click', (e) => {
    const toggle = e.target.closest('[data-toggle]');
    if (toggle) {
      const idx = toggle.getAttribute('data-toggle');
      const card = document.getElementById(`ep-${idx}`);
      if (card) card.classList.toggle('open');
      return;
    }
    const tryBtn = e.target.closest('[data-try]');
    if (tryBtn) tryCall(Number(tryBtn.getAttribute('data-try')));
  });
});
