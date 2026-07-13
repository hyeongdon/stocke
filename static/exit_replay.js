'use strict';

const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}
function num(v) { return Math.round(Number(v) || 0).toLocaleString('ko-KR'); }
function rateStr(n) {
  if (n == null || n === '') return '-';
  n = Number(n);
  const s = n > 0 ? '+' : '';
  return `${s}${n.toFixed(2)}%`;
}
function pnlClass(n) { n = Number(n) || 0; return n > 0 ? 'up' : (n < 0 ? 'down' : ''); }
function checkMark(passed) {
  if (passed === true) return '<span class="v-chk pass" title="통과">✓</span>';
  if (passed === false) return '<span class="v-chk fail" title="미통과">✗</span>';
  return '<span class="v-chk unk" title="해당 없음/추정">?</span>';
}
function compactNote(note) {
  if (!note) return '';
  const n = String(note);
  if (n.includes('시뮬레이션')) return '시뮬';
  return n.length > 24 ? `${n.slice(0, 22)}…` : n;
}

function showBanner(msg, kind) {
  const el = $('statusBanner');
  if (!el) return;
  if (!msg) { el.style.display = 'none'; return; }
  el.textContent = msg;
  el.className = `verify-banner ${kind || ''}`;
  el.style.display = 'block';
}

function defaultEntryDate() {
  // KST 기준 약 90일 전 (UTC toISOString 오차 방지)
  const parts = new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Seoul' }).split('-').map(Number);
  const d = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
  d.setUTCDate(d.getUTCDate() - 90);
  return d.toISOString().slice(0, 10);
}

function initFromUrl() {
  const p = new URLSearchParams(window.location.search);
  const code = p.get('code');
  const entry = p.get('entry_date');
  const mode = p.get('entry_price_mode');
  const days = p.get('days');
  if (code) $('stockCode').value = code;
  $('entryDate').value = entry && /^\d{4}-\d{2}-\d{2}$/.test(entry) ? entry : defaultEntryDate();
  if (mode) $('entryMode').value = mode;
  if (days) $('simDays').value = days;
}

function syncUrl(code, entry, mode, days) {
  const url = new URL(window.location.href);
  url.searchParams.set('code', code);
  url.searchParams.set('entry_date', entry);
  url.searchParams.set('entry_price_mode', mode);
  url.searchParams.set('days', days);
  window.history.replaceState({}, '', url);
}

function renderSummary(data) {
  const s = data.summary || {};
  const entry = data.entry || {};
  const exit = data.exit || {};
  const entryLabel = entry.snapped
    ? `${entry.price_label || entry.date || '-'} · ${num(entry.price)}원 (조정)`
    : `${entry.price_label || entry.date || '-'} · ${num(entry.price)}원`;
  const cards = [
    { k: '종목', v: `${data.stock_name || '-'} (${data.stock_code || '-'})` },
    { k: '진입', v: entryLabel },
    {
      k: '청산',
      v: exit.price != null
        ? `${exit.date || '-'} · ${num(exit.price)}원`
        : '미청산 (기간 내 규칙 미발동)',
    },
    {
      k: '손익%',
      v: rateStr(s.profit_loss_rate_pct),
      cls: pnlClass(s.profit_loss_rate_pct),
    },
    { k: '청산 사유', v: s.reason_label || s.reason || '-' },
    { k: '고점', v: `${num(s.peak_price)}원 (${rateStr(s.peak_rate_pct)})` },
  ];
  $('summaryCards').innerHTML = cards.map((c) =>
    `<div class="v-stat"><div class="k">${esc(c.k)}</div><div class="v ${c.cls || ''}" style="font-size:${c.k === '손익%' ? '18px' : '14px'};">${esc(c.v)}</div></div>`,
  ).join('');
  if (entry.snap_note) showBanner(entry.snap_note, 'warn');
}

function renderSettings(settings) {
  if (!settings) {
    $('settingsPanel').innerHTML = '<div class="hint">설정 없음</div>';
    return;
  }
  const rows = [
    ['손절 %', settings.stop_loss_rate != null ? `${settings.stop_loss_rate}%` : '-'],
    ['익절(트레일 시작) %', settings.take_profit_rate != null ? `${settings.take_profit_rate}%` : '-'],
    ['트레일 %', settings.trailing_stop_pct != null ? `${settings.trailing_stop_pct}%` : '-'],
    ['수익 잠금', settings.profit_lock_trigger != null
      ? `트리거 ${settings.profit_lock_trigger}% / 바닥 ${settings.profit_lock_floor ?? '-'}%`
      : '-'],
    ['ATR 손절×', settings.atr_mult_stop ?? '-'],
    ['ATR 트레일×', settings.atr_mult_trail ?? '-'],
    ['장마감 청산', settings.liquidate_before_close ? (settings.liquidate_time || '15:10') : 'OFF'],
  ];
  $('settingsPanel').innerHTML = `<dl class="replay-settings-grid">${rows.map(([k, v]) =>
    `<dt>${esc(k)}</dt><dd>${esc(String(v))}</dd>`,
  ).join('')}</dl>`;
}

function renderAssumptions(sim) {
  const items = (sim && sim.assumptions) || [];
  const extra = sim
    ? `<div class="hint" style="margin-top:8px;">구간 ${esc(sim.start_date)} ~ ${esc(sim.end_date)} · ${sim.bars_simulated}거래일 · 데이터 ${esc(sim.data_through)}</div>`
    : '';
  $('assumptionsPanel').innerHTML = items.length
    ? `<ul class="replay-assumptions">${items.map((a) => `<li>${esc(a)}</li>`).join('')}</ul>${extra}`
    : `<div class="hint">—</div>${extra}`;
}

function renderChecks(data) {
  const checks = data.sell_condition_checks || [];
  const sum = data.sell_condition_summary || {};
  if (!checks.length) {
    $('checksBlock').innerHTML = '';
    return;
  }
  const sumLine = sum.total != null
    ? `<div class="v-fill-summary">${sum.passed || 0} / ${sum.total} 충족${sum.failed ? ` · <span style="color:var(--down)">미충족 ${sum.failed}</span>` : ''}${sum.unknown ? ` · 해당없음 ${sum.unknown}` : ''}</div>`
    : '';
  const rows = checks.map((c, i) => {
    const showGroup = i === 0 || checks[i - 1].group !== c.group;
    return `<tr class="${c.enabled === false ? 'v-chk-disabled' : ''}">
      <td class="v-chk-group">${showGroup ? esc(c.group) : ''}</td>
      <td class="v-chk-mark">${checkMark(c.passed)}</td>
      <td>${esc(c.label)}</td>
      <td>${esc(c.actual || '—')}</td>
      <td>${esc(c.required || '—')}</td>
      <td class="v-chk-note" title="${esc(c.note || '')}">${esc(compactNote(c.note))}</td>
    </tr>`;
  }).join('');
  $('checksBlock').innerHTML = `<div class="section-title">매도 조건 체크 (검증 페이지 형식)</div>
    <div class="v-block">
      ${sumLine}
      <div class="v-scroll-box v-scroll-box--x">
        <table class="tbl v-chk-tbl compact"><thead><tr>
          <th>구분</th><th></th><th>조건</th><th>실제값</th><th>기준</th><th>비고</th>
        </tr></thead><tbody>${rows}</tbody></table>
      </div>
    </div>`;
}

function renderTimeline(data) {
  const timeline = data.timeline || [];
  const exitDate = (data.exit || {}).date;
  if (!timeline.length) {
    $('timelineWrap').innerHTML = '<div class="hint">타임라인 없음</div>';
    return;
  }
  const rows = timeline.map((r) => {
    const isExit = r.date === exitDate && data.exit && data.exit.reason !== 'END_OF_PERIOD';
    const armedCls = r.trailing_armed ? 'armed-yes' : 'armed-no';
    return `<tr class="${isExit ? 'exit-row' : ''}">
      <td>${esc(r.date)}${isExit ? ' ★' : ''}</td>
      <td class="num">${num(r.open)}</td>
      <td class="num">${num(r.high)}</td>
      <td class="num">${num(r.low)}</td>
      <td class="num">${num(r.close)}</td>
      <td class="num">${num(r.peak)}</td>
      <td class="num ${pnlClass(r.peak_rate_pct)}">${rateStr(r.peak_rate_pct)}</td>
      <td class="${armedCls}">${r.trailing_armed ? 'Y' : '—'}</td>
      <td class="num">${r.trailing_floor ? num(r.trailing_floor) : '—'}</td>
      <td class="num">${r.effective_stop ? num(r.effective_stop) : '—'}</td>
      <td>${esc(r.effective_stop_reason || '—')}</td>
      <td class="num ${pnlClass(r.unrealized_pct)}">${rateStr(r.unrealized_pct)}</td>
    </tr>`;
  }).join('');
  $('timelineWrap').innerHTML = `<table class="tbl replay-timeline compact"><thead><tr>
    <th>일자</th><th class="num">시가</th><th class="num">고가</th><th class="num">저가</th><th class="num">종가</th>
    <th class="num">고점</th><th class="num">고점%</th><th>armed</th><th class="num">바닥</th>
    <th class="num">유효선</th><th>규칙</th><th class="num">미실현%</th>
  </tr></thead><tbody>${rows}</tbody></table>`;
}

async function runSimulation() {
  const code = ($('stockCode').value || '').trim().padStart(6, '0');
  const entry = ($('entryDate').value || '').trim();
  const mode = $('entryMode').value;
  const days = $('simDays').value;
  if (!/^\d{6}$/.test(code)) {
    showBanner('6자리 종목코드를 입력하세요', 'warn');
    return;
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(entry)) {
    showBanner('진입일을 선택하세요', 'warn');
    return;
  }
  showBanner('시뮬레이션 중…', '');
  $('summaryCards').innerHTML = '<div class="v-stat"><div class="k">상태</div><div class="v">계산 중…</div></div>';
  syncUrl(code, entry, mode, days);
  try {
    const qs = new URLSearchParams({ code, entry_date: entry, entry_price_mode: mode, days });
    const res = await fetch(`/api/stock-exit-replay?${qs}`);
    const data = await res.json();
    if (!res.ok) {
      const detail = data.detail;
      const msg = typeof detail === 'string'
        ? detail
        : (Array.isArray(detail) ? detail.map((d) => d.msg || d).join('; ') : null);
      throw new Error(msg || data.error || `HTTP ${res.status}`);
    }
    showBanner('');
    renderSummary(data);
    renderSettings(data.settings_used);
    renderAssumptions(data.simulation);
    renderChecks(data);
    renderTimeline(data);
  } catch (e) {
    showBanner(String(e.message || e), 'error');
    $('summaryCards').innerHTML = '<div class="v-stat"><div class="k">오류</div><div class="v down" style="font-size:14px;">시뮬레이션 실패</div></div>';
  }
}

document.addEventListener('DOMContentLoaded', () => {
  initFromUrl();
  $('btnSimulate').addEventListener('click', runSimulation);
  $('stockCode').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') runSimulation();
  });
});
