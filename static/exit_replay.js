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
  if (n.includes('조건식 이력')) return '직접지정';
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
  return new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Seoul' });
}

let _uiMode = 'single';
let _dayResults = [];
let _selectedCondition = '';
let _dayConditions = [];

function setUiMode(mode) {
  _uiMode = mode === 'day' ? 'day' : 'single';
  document.querySelectorAll('.mode-tab').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.mode === _uiMode);
  });
  const isDay = _uiMode === 'day';
  $('stockCode').style.display = isDay ? 'none' : '';
  $('resolution').style.display = isDay ? 'none' : '';
  $('entryMode').style.display = isDay ? 'none' : '';
  $('limitLabel').style.display = isDay ? '' : 'none';
  $('dayLayout').style.display = isDay ? 'grid' : 'none';
  $('singlePanels').style.display = isDay ? 'none' : 'block';
  if ($('dayDetailHint')) $('dayDetailHint').style.display = 'none';
  if ($('dayProgress')) $('dayProgress').style.display = 'none';
  $('btnSimulate').textContent = isDay ? '다시 검증' : '시뮬레이션';
  $('btnSimulate').title = isDay
    ? '선택한 조건식으로 당일 검증 재실행'
    : '단일 종목 시뮬레이션';
  if (!isDay) {
    syncDaysOptions();
  } else {
    const sel = $('simDays');
    sel.innerHTML = [['1', '1일'], ['3', '3일'], ['5', '5일'], ['7', '7일']]
      .map(([v, l]) => `<option value="${v}">${l}</option>`).join('');
    if (![...sel.options].some((o) => o.value === sel.value)) sel.value = '1';
    loadDayConditions();
  }
}

function isIntradayResolution(res) {
  return res === '15m' || res === '5m';
}

function syncDaysOptions() {
  if (_uiMode === 'day') return;
  const res = $('resolution').value;
  const sel = $('simDays');
  const cur = sel.value;
  const opts = isIntradayResolution(res)
    ? [['1', '1일'], ['3', '3일'], ['5', '5일'], ['7', '7일']]
    : [['60', '60일'], ['90', '90일'], ['120', '120일'], ['180', '180일']];
  sel.innerHTML = opts.map(([v, l]) => `<option value="${v}">${l}</option>`).join('');
  const prefer = isIntradayResolution(res) ? '5' : '120';
  sel.value = opts.some(([v]) => v === cur) ? cur : prefer;
  $('entryMode').disabled = isIntradayResolution(res);
}

function initFromUrl() {
  const p = new URLSearchParams(window.location.search);
  const code = p.get('code');
  const entry = p.get('entry_date') || p.get('trade_date');
  const mode = p.get('entry_price_mode');
  const days = p.get('days') || p.get('hold_days');
  const strategy = p.get('strategy');
  const resolution = p.get('resolution');
  const uiMode = p.get('mode');
  if (code) $('stockCode').value = code;
  $('entryDate').value = entry && /^\d{4}-\d{2}-\d{2}$/.test(entry) ? entry : defaultEntryDate();
  if (resolution === '1d' || resolution === '15m' || resolution === '5m') {
    $('resolution').value = resolution;
  }
  if (mode) $('entryMode').value = mode;
  if (strategy && ['legacy', 'sangtta', 'breakout'].includes(strategy)) {
    $('strategy').value = strategy;
  }
  const limit = p.get('limit');
  if (limit && $('verifyLimit')) $('verifyLimit').value = limit;
  setUiMode(uiMode === 'day' ? 'day' : 'single');
  if (days) $('simDays').value = days;
  _selectedCondition = (p.get('condition_names') || '').split(',')[0].trim();
  window._conditionNamesOverride = _selectedCondition;
}

function syncUrl(extra) {
  const url = new URL(window.location.href);
  url.searchParams.set('mode', _uiMode);
  url.searchParams.set('strategy', $('strategy').value);
  url.searchParams.set('entry_date', ($('entryDate').value || '').trim());
  url.searchParams.set('days', $('simDays').value);
  if (_uiMode === 'single') {
    url.searchParams.set('code', ($('stockCode').value || '').trim().padStart(6, '0'));
    url.searchParams.set('entry_price_mode', $('entryMode').value);
    url.searchParams.set('resolution', $('resolution').value);
  } else {
    url.searchParams.set('limit', $('verifyLimit').value);
    url.searchParams.delete('code');
    url.searchParams.delete('resolution');
  }
  if (_selectedCondition || window._conditionNamesOverride) {
    url.searchParams.set('condition_names', _selectedCondition || window._conditionNamesOverride);
  } else {
    url.searchParams.delete('condition_names');
  }
  if (extra) {
    Object.entries(extra).forEach(([k, v]) => {
      if (v == null || v === '') url.searchParams.delete(k);
      else url.searchParams.set(k, v);
    });
  }
  window.history.replaceState({}, '', url);
}

function renderSummary(data) {
  const s = data.summary || {};
  const entry = data.entry || {};
  const exit = data.exit || {};
  const strat = data.strategy || {};
  const entryPass = entry.passed === true ? '통과' : (entry.passed === false ? '미통과' : '—');
  const entryPassCls = entry.passed === true ? 'up' : (entry.passed === false ? 'down' : '');
  const assumed = entry.assumed === true || (entry.passed === false && entry.price != null);
  const buyTime = entry.time || s.buy_time || entry.time_approx || entry.date || '-';
  const sellTime = exit.time || s.sell_time || exit.date || '-';
  const buyLabel = assumed
    ? `가정 ${buyTime} · ${num(entry.price)}원`
    : (entry.passed === false
      ? '매수 없음 (게이트 미통과)'
      : `${buyTime} · ${num(entry.price)}원`);
  const sellLabel = assumed
    ? (exit.price != null ? `가정 ${sellTime} · ${num(exit.price)}원` : '가정 미청산')
    : (entry.passed === false
      ? '—'
      : (exit.price != null ? `${sellTime} · ${num(exit.price)}원` : '미청산'));
  const pnlLabel = assumed
    ? `${rateStr(s.profit_loss_rate_pct)} (가정)`
    : (entry.passed === false ? '—' : rateStr(s.profit_loss_rate_pct));
  const cards = [
    { k: '전략', v: `${strat.label || '-'} · ${data.resolution || '15m'}` },
    { k: '종목', v: `${data.stock_name || '-'} (${data.stock_code || '-'})` },
    { k: '진입 게이트', v: entryPass, cls: entryPassCls },
    { k: assumed ? '가정 매수' : '매수', v: buyLabel, cls: assumed ? 'down' : '' },
    { k: assumed ? '가정 매도' : '매도', v: sellLabel },
    {
      k: assumed ? '가정 손익%' : '손익%',
      v: pnlLabel,
      cls: assumed || entry.passed === false ? '' : pnlClass(s.profit_loss_rate_pct),
    },
    { k: '청산 사유', v: assumed ? `(가정) ${s.reason_label || s.reason || '-'}` : (s.reason_label || s.reason || '-') },
    { k: '고점', v: `${num(s.peak_price)}원 (${rateStr(s.peak_rate_pct)})` },
  ];
  $('summaryCards').innerHTML = cards.map((c) =>
    `<div class="v-stat"><div class="k">${esc(c.k)}</div><div class="v ${c.cls || ''}" style="font-size:${String(c.k).includes('손익') ? '18px' : '14px'};">${esc(c.v)}</div></div>`,
  ).join('');

  const notes = [];
  if (entry.snap_note) notes.push(entry.snap_note);
  if (entry.passed === false) {
    notes.push(
      assumed
        ? `실제 매수 없음 — 게이트 미통과(${entry.reason || ''}). 표시된 매수·매도는 청산 참고용 가정 진입입니다.`
        : `진입 게이트 미통과: ${entry.reason || ''}`,
    );
  }
  if (notes.length) showBanner(notes.join(' · '), entry.passed === false ? 'warn' : '');
}

function renderSettings(settings, strategy) {
  if (!settings) {
    $('settingsPanel').innerHTML = '<div class="hint">설정 없음</div>';
    return;
  }
  const key = (strategy && strategy.key) || 'legacy';
  let rows;
  if (key === 'breakout') {
    rows = [
      ['손절 %', settings.breakout_stop_loss_pct != null ? `${settings.breakout_stop_loss_pct}%` : '-'],
      ['트레일 시작 %', settings.breakout_trailing_start_pct != null ? `${settings.breakout_trailing_start_pct}%` : '-'],
      ['트레일 %', settings.breakout_trailing_pct != null ? `${settings.breakout_trailing_pct}%` : '-'],
      ['구조 HARD %', settings.struct_break_hard_pct ?? '-'],
      ['SOFT 확인', settings.soft_confirm_polls ?? '-'],
      ['시간대', strategy.time_window || '-'],
    ];
  } else if (key === 'sangtta') {
    rows = [
      ['손절 %', settings.stop_loss_rate != null ? `${settings.stop_loss_rate}%` : '-'],
      ['익절(트레일 시작) %', settings.take_profit_rate != null ? `${settings.take_profit_rate}%` : '-'],
      ['상한가 HARD', settings.limit_break_hard_pct != null ? `${settings.limit_break_hard_pct}%` : '-'],
      ['급락 HARD', settings.sharp_drop_hard_pct != null ? `${settings.sharp_drop_hard_pct}%` : '-'],
      ['SOFT 확인', settings.soft_confirm_polls ?? '-'],
      ['시간대', strategy.time_window || '-'],
    ];
  } else {
    rows = [
      ['손절 %', settings.stop_loss_rate != null ? `${settings.stop_loss_rate}%` : '-'],
      ['익절(트레일 시작) %', settings.take_profit_rate != null ? `${settings.take_profit_rate}%` : '-'],
      ['트레일 %', settings.trailing_stop_pct != null ? `${settings.trailing_stop_pct}%` : '-'],
      ['진입 게이트', settings.use_entry_gate ? 'ON' : 'OFF'],
      ['VWAP', settings.require_above_vwap ? 'ON' : 'OFF'],
      ['시간대', strategy.time_window || '-'],
    ];
  }
  $('settingsPanel').innerHTML = `<dl class="replay-settings-grid">${rows.map(([k, v]) =>
    `<dt>${esc(k)}</dt><dd>${esc(String(v))}</dd>`,
  ).join('')}</dl>`;
}

function renderAssumptions(sim) {
  const items = (sim && sim.assumptions) || [];
  const extra = sim
    ? `<div class="hint" style="margin-top:8px;">구간 ${esc(sim.start_date)} ~ ${esc(sim.end_date)} · ${sim.bars_simulated}봉 · ${esc(sim.resolution || '')}</div>`
    : '';
  $('assumptionsPanel').innerHTML = items.length
    ? `<ul class="replay-assumptions">${items.map((a) => `<li>${esc(a)}</li>`).join('')}</ul>${extra}`
    : `<div class="hint">—</div>${extra}`;
}

function renderCheckTable(checks, sum, title) {
  if (!checks || !checks.length) return '';
  const sumLine = sum && sum.total != null
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
  return `<div class="section-title">${esc(title)}</div>
    <div class="v-block">
      ${sumLine}
      <div class="v-scroll-box v-scroll-box--x">
        <table class="tbl v-chk-tbl compact"><thead><tr>
          <th>구분</th><th></th><th>조건</th><th>실제값</th><th>기준</th><th>비고</th>
        </tr></thead><tbody>${rows}</tbody></table>
      </div>
    </div>`;
}

function renderChecks(data) {
  $('buyChecksBlock').innerHTML = renderCheckTable(
    data.buy_condition_checks,
    data.buy_condition_summary,
    '매수 조건 체크',
  );
  $('checksBlock').innerHTML = renderCheckTable(
    data.sell_condition_checks,
    data.sell_condition_summary,
    '매도 조건 체크',
  );
}

function renderTimeline(data) {
  const timeline = data.timeline || [];
  const res = data.resolution || '';
  const isIntra = res === '15m' || res === '5m';
  const barLabel = res === '5m' ? '5분봉' : (res === '15m' ? '15분봉' : '');
  $('timelineTitle').textContent = isIntra
    ? `${barLabel} 타임라인 (매수 이후)`
    : '일별 타임라인';
  if (!timeline.length) {
    $('timelineWrap').innerHTML = '<div class="hint">타임라인 없음</div>';
    return;
  }
  const exitTs = (data.exit || {}).time || (data.exit || {}).date;
  const rows = timeline.map((r) => {
    const key = r.timestamp || r.date;
    const isExit = exitTs && key === exitTs && data.exit && data.exit.reason !== 'END_OF_PERIOD';
    const armedCls = r.trailing_armed ? 'armed-yes' : 'armed-no';
    const timeCell = isIntra ? (r.timestamp || r.date) : r.date;
    return `<tr class="${isExit ? 'exit-row' : ''}">
      <td>${esc(timeCell)}${isExit ? ' ★' : ''}</td>
      <td class="num">${num(r.open)}</td>
      <td class="num">${num(r.high)}</td>
      <td class="num">${num(r.low)}</td>
      <td class="num">${num(r.close)}</td>
      <td class="num">${num(r.peak)}</td>
      <td class="num ${pnlClass(r.peak_rate_pct)}">${rateStr(r.peak_rate_pct)}</td>
      <td class="${armedCls}">${r.trailing_armed ? 'Y' : '—'}</td>
      <td class="num">${r.effective_stop ? num(r.effective_stop) : '—'}</td>
      <td>${esc(r.effective_stop_reason || '—')}</td>
      <td class="num ${pnlClass(r.unrealized_pct)}">${rateStr(r.unrealized_pct)}</td>
    </tr>`;
  }).join('');
  $('timelineWrap').innerHTML = `<table class="tbl replay-timeline compact"><thead><tr>
    <th>${isIntra ? '시각' : '일자'}</th><th class="num">시</th><th class="num">고</th><th class="num">저</th><th class="num">종</th>
    <th class="num">고점</th><th class="num">고점%</th><th>armed</th>
    <th class="num">유효선</th><th>규칙</th><th class="num">미실현%</th>
  </tr></thead><tbody>${rows}</tbody></table>`;
}

/* ---- 검증 페이지와 동일한 15분봉 캔들 차트 ---- */
function parseTs(s) {
  if (!s) return null;
  const raw = String(s).trim().replace(' ', 'T');
  const t = Date.parse(/[zZ]|[+-]\d{2}:?\d{2}$/.test(raw) ? raw : `${raw}+09:00`);
  return Number.isFinite(t) ? t : null;
}
function barTs(bar) { return parseTs(bar.timestamp); }
function findBarIndex(bars, targetMs) {
  if (!bars.length || targetMs == null) return -1;
  let best = 0;
  let bestDiff = Infinity;
  for (let i = 0; i < bars.length; i++) {
    const d = Math.abs(barTs(bars[i]) - targetMs);
    if (d < bestDiff) { bestDiff = d; best = i; }
  }
  return best;
}

function renderIntradayChart(canvas, payload) {
  const bars = payload.bars || [];
  const wrap = canvas.parentElement;
  const statusEl = wrap.querySelector('.v-chart-status');
  const legend = wrap.querySelector('.v-chart-legend');

  if (!bars.length) {
    if (statusEl) {
      statusEl.textContent = payload.error || '해당 일자 분봉 데이터가 없습니다';
      statusEl.classList.add('err');
      statusEl.style.display = 'block';
    }
    canvas.style.display = 'none';
    if (legend) legend.style.display = 'none';
    return;
  }

  const padL = 52;
  const padR = 12;
  const padT = 22;
  const padB = 28;
  const minSlot = 9;
  const viewW = Math.max(wrap.clientWidth || 640, 280);
  const plotW = Math.max(bars.length * minSlot, viewW - padL - padR);
  const W = padL + padR + plotW;
  const H = 300;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(W * dpr);
  canvas.height = Math.round(H * dpr);
  canvas.style.width = `${W}px`;
  canvas.style.height = `${H}px`;
  canvas.style.display = 'block';
  wrap.classList.add('v-chart-scroll');
  if (statusEl) {
    const parts = [];
    const dr = payload.date_range;
    if (dr && dr.length === 2 && dr[0] !== dr[1]) parts.push(`${dr[0]} ~ ${dr[1]}`);
    if (payload.warning) parts.push(payload.warning);
    if (W > viewW + 4) parts.push('가로 스크롤로 전체 봉 확인');
    if (parts.length) {
      statusEl.textContent = parts.join(' · ');
      statusEl.classList.remove('err');
      statusEl.classList.add('warn');
      statusEl.style.display = 'block';
    } else {
      statusEl.style.display = 'none';
    }
  }
  if (legend) legend.style.display = 'flex';

  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const plotTotalH = H - padT - padB;
  const volGap = 5;
  const volPanelH = 52;
  const hasVolume = bars.some((b) => Number(b.volume) > 0);
  const pricePlotH = hasVolume ? plotTotalH - volPanelH - volGap : plotTotalH;
  const volTop = padT + pricePlotH + volGap;

  let volMax = 0;
  if (hasVolume) {
    bars.forEach((b) => { volMax = Math.max(volMax, Number(b.volume) || 0); });
  }

  let lo = Infinity;
  let hi = -Infinity;
  bars.forEach((b) => {
    lo = Math.min(lo, b.low, b.open, b.close);
    hi = Math.max(hi, b.high, b.open, b.close);
  });
  const markers = payload.markers || {};
  const levelLines = payload.level_lines || [];
  [markers.buy, markers.sell].forEach((m) => {
    if (m && m.price != null) {
      lo = Math.min(lo, m.price);
      hi = Math.max(hi, m.price);
    }
  });
  levelLines.forEach((ln) => {
    if (ln.price != null) {
      lo = Math.min(lo, ln.price);
      hi = Math.max(hi, ln.price);
    }
  });
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || lo === hi) {
    lo = lo || 0;
    hi = hi || lo + 1;
  }
  const pad = (hi - lo) * 0.06 || 1;
  lo -= pad;
  hi += pad;

  const yOf = (p) => padT + pricePlotH - ((p - lo) / (hi - lo)) * pricePlotH;
  const slotW = plotW / bars.length;

  const chartWrap = canvas.closest('.v-chart-wrap') || document.documentElement;
  const styles = getComputedStyle(chartWrap);
  const bg = styles.getPropertyValue('--card').trim() || '#1a1d24';
  const grid = styles.getPropertyValue('--border').trim() || '#333';
  const text = styles.getPropertyValue('--muted').trim() || '#888';
  const up = styles.getPropertyValue('--v-up').trim() || '#e8a0a0';
  const down = styles.getPropertyValue('--v-down').trim() || '#5b9bd5';
  const upVol = styles.getPropertyValue('--v-up-vol').trim() || 'rgba(232, 160, 160, 0.72)';
  const downVol = styles.getPropertyValue('--v-down-vol').trim() || 'rgba(91, 155, 213, 0.72)';
  const buyC = styles.getPropertyValue('--accent-bright').trim() || '#60a5fa';
  const sellC = styles.getPropertyValue('--amber').trim() || '#f59e0b';
  const stopC = styles.getPropertyValue('--v-down').trim() || '#5b9bd5';
  const takeC = styles.getPropertyValue('--v-up').trim() || '#e8a0a0';
  const trailC = styles.getPropertyValue('--muted').trim() || '#888';

  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, W, H);

  ctx.strokeStyle = grid;
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = padT + (pricePlotH * i) / 4;
    ctx.beginPath();
    ctx.moveTo(padL, y);
    ctx.lineTo(W - padR, y);
    ctx.stroke();
    const price = hi - ((hi - lo) * i) / 4;
    ctx.fillStyle = text;
    ctx.font = '10px system-ui, sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(Math.round(price).toLocaleString('ko-KR'), padL - 4, y + 3);
  }

  bars.forEach((b, i) => {
    const x = padL + i * slotW + slotW / 2;
    if (i > 0) {
      const d0 = String(bars[i - 1].timestamp).slice(0, 10);
      const d1 = String(b.timestamp).slice(0, 10);
      if (d0 !== d1) {
        const sepX = padL + i * slotW;
        ctx.strokeStyle = text;
        ctx.globalAlpha = 0.35;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(sepX, padT);
        ctx.lineTo(sepX, padT + pricePlotH);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;
        ctx.font = '9px system-ui, sans-serif';
        ctx.fillStyle = text;
        ctx.textAlign = 'left';
        ctx.fillText(d1.slice(5), sepX + 2, padT + 10);
      }
    }
    const openY = yOf(b.open);
    const closeY = yOf(b.close);
    const highY = yOf(b.high);
    const lowY = yOf(b.low);
    const bullish = b.close >= b.open;
    const color = bullish ? up : down;
    const bodyW = Math.max(3, Math.min(slotW * 0.7, slotW - 1));

    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, highY);
    ctx.lineTo(x, lowY);
    ctx.stroke();

    ctx.fillStyle = color;
    const top = Math.min(openY, closeY);
    const bh = Math.max(1, Math.abs(closeY - openY));
    ctx.fillRect(x - bodyW / 2, top, bodyW, bh);

    if (hasVolume && volMax > 0) {
      const vol = Number(b.volume) || 0;
      const vh = Math.max(1, (vol / volMax) * volPanelH);
      const vy = volTop + volPanelH - vh;
      ctx.fillStyle = bullish ? upVol : downVol;
      ctx.fillRect(x - bodyW / 2, vy, bodyW, vh);
    }
  });

  if (hasVolume && volMax > 0) {
    ctx.strokeStyle = grid;
    ctx.beginPath();
    ctx.moveTo(padL, volTop);
    ctx.lineTo(W - padR, volTop);
    ctx.stroke();
  }

  function drawHLine(ln, color) {
    if (!ln || ln.price == null || !Number.isFinite(ln.price)) return;
    const y = yOf(ln.price);
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.setLineDash([6, 4]);
    ctx.globalAlpha = 0.85;
    ctx.beginPath();
    ctx.moveTo(padL, y);
    ctx.lineTo(W - padR, y);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
    ctx.fillStyle = color;
    ctx.font = '9px system-ui, sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(ln.label || '', W - padR - 2, y - 3);
    ctx.restore();
  }
  const lineColor = { stop: stopC, take: takeC, trail: trailC };
  levelLines.forEach((ln) => drawHLine(ln, lineColor[ln.kind] || trailC));

  function drawMarker(m, color, label) {
    if (!m || !m.time) return;
    const idx = findBarIndex(bars, parseTs(m.time));
    if (idx < 0) return;
    const x = padL + idx * slotW + slotW / 2;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(x, padT);
    ctx.lineTo(x, padT + pricePlotH);
    ctx.stroke();
    ctx.setLineDash([]);
    const py = m.price != null ? yOf(m.price) : padT + pricePlotH / 2;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(x, py, 4, 0, Math.PI * 2);
    ctx.fill();
    ctx.font = '10px system-ui, sans-serif';
    ctx.textAlign = 'center';
    const labelY = py < padT + 14 ? py + 14 : padT - 2;
    ctx.fillText(label, x, labelY);
  }

  const buyPx = markers.buy && markers.buy.price != null ? num(markers.buy.price) : null;
  const sellPx = markers.sell && markers.sell.price != null ? num(markers.sell.price) : null;
  const buyAssumed = !!(markers.buy && markers.buy.assumed);
  const sellAssumed = !!(markers.sell && markers.sell.assumed);
  drawMarker(
    markers.buy,
    buyC,
    buyAssumed
      ? (buyPx ? `가정(${buyPx})` : '가정')
      : (buyPx ? `매수(${buyPx})` : '매수'),
  );
  drawMarker(
    markers.sell,
    sellC,
    sellAssumed
      ? (sellPx ? `가정청산(${sellPx})` : '가정청산')
      : (sellPx ? `매도(${sellPx})` : '매도'),
  );

  ctx.fillStyle = text;
  ctx.font = '10px system-ui, sans-serif';
  ctx.textAlign = 'center';
  const multiDay = payload.date_range && payload.date_range[0] !== payload.date_range[1];
  // 라벨이 겹치지 않도록 간격 유지 (목표 ~56px)
  const labelStep = Math.max(1, Math.ceil(56 / Math.max(slotW, 1)));
  const labelIdx = new Set();
  for (let i = 0; i < bars.length; i += labelStep) labelIdx.add(i);
  labelIdx.add(bars.length - 1);
  [...labelIdx].sort((a, b) => a - b).forEach((i) => {
    const b = bars[i];
    if (!b) return;
    const x = padL + i * slotW + slotW / 2;
    const ts = String(b.timestamp);
    const lbl = multiDay ? `${ts.slice(5, 10)} ${ts.slice(11, 16)}` : ts.slice(11, 16);
    ctx.fillText(lbl, x, H - 8);
  });
}

function renderChart(data) {
  const canvas = $('replayChart');
  const status = $('chartStatus');
  const legend = $('chartLegend');
  if (!canvas) return;

  if ((data.resolution === '15m' || data.resolution === '5m') && data.intraday_chart) {
    renderIntradayChart(canvas, data.intraday_chart);
    return;
  }

  // 일봉 폴백: 종가 라인
  const timeline = data.timeline || [];
  if (!timeline.length) {
    if (status) {
      status.style.display = 'block';
      status.textContent = '차트 데이터 없음';
      status.classList.add('err');
    }
    canvas.style.display = 'none';
    if (legend) legend.style.display = 'none';
    return;
  }

  const bars = timeline.map((r) => ({
    timestamp: `${r.date} 15:30:00`,
    open: r.open, high: r.high, low: r.low, close: r.close, volume: 0,
  }));
  const entry = data.entry || {};
  const exit = data.exit || {};
  renderIntradayChart(canvas, {
    bars,
    markers: {
      buy: entry.date ? { time: `${entry.date} 15:30:00`, price: entry.price } : null,
      sell: exit.date ? { time: `${exit.date} 15:30:00`, price: exit.price } : null,
    },
    level_lines: [],
    date_range: [bars[0].timestamp.slice(0, 10), bars[bars.length - 1].timestamp.slice(0, 10)],
    warning: '일봉 모드 — 종가 캔들 근사',
  });
}

async function runSimulation() {
  if (_uiMode === 'day') {
    await runDayVerify();
    return;
  }
  const code = ($('stockCode').value || '').trim().padStart(6, '0');
  const entry = ($('entryDate').value || '').trim();
  const mode = $('entryMode').value;
  const days = $('simDays').value;
  const strategy = $('strategy').value;
  const resolution = $('resolution').value;
  if (!/^\d{6}$/.test(code)) {
    showBanner('6자리 종목코드를 입력하세요', 'warn');
    return;
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(entry)) {
    showBanner('진입일을 선택하세요', 'warn');
    return;
  }
  showBanner('시뮬레이션 중… (15분봉 조회 시 수 초 소요)', '');
  $('summaryCards').innerHTML = '<div class="v-stat"><div class="k">상태</div><div class="v">계산 중…</div></div>';
  $('chartStatus').style.display = 'block';
  $('chartStatus').textContent = '15분봉 불러오는 중…';
  $('chartStatus').classList.remove('err', 'warn');
  $('replayChart').style.display = 'none';
  syncUrl();
  try {
    const qs = new URLSearchParams({
      code, entry_date: entry, entry_price_mode: mode, days, strategy, resolution,
    });
    const res = await fetch(`/api/stock-exit-replay?${qs}`);
    const data = await res.json();
    if (!res.ok) {
      const detail = data.detail;
      const msg = typeof detail === 'string'
        ? detail
        : (Array.isArray(detail) ? detail.map((d) => d.msg || d).join('; ') : null);
      throw new Error(msg || data.error || `HTTP ${res.status}`);
    }
    if (!(data.entry && data.entry.passed === false) && !(data.entry && data.entry.snap_note)) {
      showBanner('');
    }
    renderSummary(data);
    renderSettings(data.settings_used, data.strategy);
    renderAssumptions(data.simulation);
    renderChecks(data);
    renderTimeline(data);
    renderChart(data);
  } catch (e) {
    showBanner(String(e.message || e), 'error');
    $('summaryCards').innerHTML = '<div class="v-stat"><div class="k">오류</div><div class="v down" style="font-size:14px;">시뮬레이션 실패</div></div>';
    $('buyChecksBlock').innerHTML = '';
    $('checksBlock').innerHTML = '';
    $('chartStatus').textContent = '차트 없음';
    $('chartStatus').classList.add('err');
    $('replayChart').style.display = 'none';
  }
}

function renderDaySummary(data) {
  const s = data.summary || {};
  const strat = data.strategy || {};
  const skippedN = data.skipped_count ?? (data.skipped || []).length ?? 0;
  const cards = [
    { k: '모드', v: '당일 검증 (주문 없음)' },
    { k: '전략', v: strat.label || '-' },
    { k: '날짜', v: data.trade_date || '-' },
    { k: '조건식', v: data.condition_name || (data.condition_names || []).join(', ') || '-' },
    { k: '편입', v: `${data.universe_count || 0}종목` },
    { k: '시뮬', v: `${data.simulated_count || 0}종목` },
    {
      k: '미시뮬',
      v: skippedN ? `${skippedN} (한도 ${data.limit})` : '0',
      cls: skippedN ? 'down' : '',
    },
    { k: '게이트 통과', v: String(s.entry_passed ?? 0), cls: 'up' },
    { k: '게이트 실패', v: String(s.entry_failed ?? 0), cls: 'down' },
    {
      k: '통과 평균손익',
      v: s.avg_pnl_pct_on_passed != null ? rateStr(s.avg_pnl_pct_on_passed) : '-',
      cls: pnlClass(s.avg_pnl_pct_on_passed),
    },
  ];
  $('summaryCards').innerHTML = cards.map((c) =>
    `<div class="v-stat"><div class="k">${esc(c.k)}</div><div class="v ${c.cls || ''}" style="font-size:14px;">${esc(c.v)}</div></div>`,
  ).join('');
}

function renderDaySkipped(data) {
  const block = $('daySkippedBlock');
  const wrap = $('daySkippedWrap');
  if (!block || !wrap) return;
  const skipped = data.skipped || [];
  if (!skipped.length) {
    block.style.display = 'none';
    wrap.innerHTML = '';
    return;
  }
  block.style.display = 'block';
  wrap.innerHTML = `<div class="hint" style="margin-bottom:8px;">
      편입 ${data.universe_count || 0}종목 중 시뮬수 한도(${data.limit})로
      <b>${skipped.length}종목</b>은 시뮬하지 않았습니다. 상단 「시뮬수」를 올리세요.
    </div>
    <ul class="day-skipped-list">${skipped.map((s) => `
      <li class="day-skipped-item">
        <div>
          <span class="sk-name">${esc(s.stock_name || '-')}</span>
          <span class="sk-code">${esc(s.stock_code || '')}</span>
        </div>
        <div class="sk-reason">${esc(s.reason_label || '시뮬 종목수 한도로 제외')}</div>
      </li>`).join('')}</ul>`;
}

function renderDayCondList(payload) {
  const list = $('dayCondList');
  const hint = $('dayCondHint');
  _dayConditions = (payload && payload.conditions) || [];
  if (hint) {
    hint.textContent = payload.empty_hint
      || '조건식을 클릭하면 편입 종목 시뮬이 실행됩니다 (주문 없음).';
  }
  if (!_dayConditions.length) {
    list.innerHTML = `<li class="hint">${esc(payload.empty_hint || '등록된 검증 조건식이 없습니다')}</li>`;
    return;
  }
  list.innerHTML = _dayConditions.map((c) => {
    const name = c.name || '';
    const active = name === _selectedCondition ? ' is-active' : '';
    const meta = c.also_in_live
      ? '<span class="cond-meta warn">실매매에도 동일명 있음 — 검증 전용과 혼동 주의</span>'
      : '<span class="cond-meta">검증 전용 · 주문 없음</span>';
    return `<li><button type="button" class="day-cond-item${active}" data-name="${esc(name)}">
      <span class="cond-name">${esc(name)}</span>${meta}
    </button></li>`;
  }).join('');
  list.querySelectorAll('.day-cond-item').forEach((btn) => {
    btn.addEventListener('click', () => selectDayCondition(btn.dataset.name));
  });
}

async function loadDayConditions() {
  const list = $('dayCondList');
  list.innerHTML = '<li class="hint">목록 로딩…</li>';
  try {
    const qs = new URLSearchParams({ strategy: $('strategy').value });
    const res = await fetch(`/api/strategy-day-verify/conditions?${qs}`);
    const data = await res.json();
    if (!res.ok) {
      const detail = data.detail;
      throw new Error(typeof detail === 'string' ? detail : (data.error || `HTTP ${res.status}`));
    }
    renderDayCondList(data);
    if (_selectedCondition && _dayConditions.some((c) => c.name === _selectedCondition)) {
      // keep selection; user can re-run with 다시 검증
      document.querySelectorAll('.day-cond-item').forEach((btn) => {
        btn.classList.toggle('is-active', btn.dataset.name === _selectedCondition);
      });
    } else if (_dayConditions.length === 1) {
      _selectedCondition = _dayConditions[0].name;
      window._conditionNamesOverride = _selectedCondition;
      document.querySelectorAll('.day-cond-item').forEach((btn) => {
        btn.classList.toggle('is-active', btn.dataset.name === _selectedCondition);
      });
    }
  } catch (e) {
    list.innerHTML = `<li class="hint">${esc(e.message || e)}</li>`;
  }
}

function selectDayCondition(name) {
  _selectedCondition = name || '';
  window._conditionNamesOverride = _selectedCondition;
  document.querySelectorAll('.day-cond-item').forEach((btn) => {
    btn.classList.toggle('is-active', btn.dataset.name === _selectedCondition);
  });
  $('singlePanels').style.display = 'none';
  if ($('dayDetailHint')) $('dayDetailHint').style.display = 'none';
  runDayVerify();
}

function dayBuySellCells(r) {
  const assumed = r.entry_assumed || (r.entry_passed === false && r.buy_time);
  if (r.entry_passed === true) {
    return {
      buy: r.buy_time || '—',
      sell: r.sell_time || '—',
      reason: r.sell_reason || '—',
      pnl: rateStr(r.profit_loss_rate_pct),
      pnlCls: pnlClass(r.profit_loss_rate_pct),
    };
  }
  // 게이트 미통과 = 실제 매수 없음
  if (assumed) {
    return {
      buy: r.buy_time ? `가정 ${String(r.buy_time).slice(11, 16)}` : '매수없음',
      sell: r.sell_time ? `가정 ${String(r.sell_time).slice(11, 16)}` : '—',
      reason: r.sell_reason ? `(가정) ${r.sell_reason}` : '—',
      pnl: r.profit_loss_rate_pct != null ? `${rateStr(r.profit_loss_rate_pct)}*` : '—',
      pnlCls: '',
    };
  }
  return { buy: '매수없음', sell: '—', reason: '—', pnl: '—', pnlCls: '' };
}

function dayResultCardHtml(r, i) {
  const gate = r.entry_passed === true ? '통과' : (r.entry_passed === false ? '미통과' : (r.error ? '오류' : '—'));
  const gateCls = r.entry_passed === true ? 'up' : (r.entry_passed === false || r.error ? 'down' : '');
  const failText = r.gate_fail_summary || r.entry_reason || r.error || '—';
  const failCls = (r.entry_passed === false || r.error) ? 'dr-fail has-fail' : 'dr-fail';
  const cells = dayBuySellCells(r);
  return `<button type="button" class="day-result-item" data-idx="${i}">
    <div class="dr-stock">
      <span class="stock-name">${esc(r.stock_name || '-')}</span>
      <span class="stock-code">${esc(r.stock_code || '')}</span>
    </div>
    <div class="dr-gate ${gateCls}">${esc(gate)}</div>
    <div class="${failCls}" title="${esc(failText)}">${esc(failText)}</div>
    <div class="dr-trades">
      <span title="${esc(r.buy_time || '')}"><b>매수</b> ${esc(cells.buy)}</span>
      <span title="${esc(r.sell_time || '')}"><b>매도</b> ${esc(cells.sell)}</span>
      <span title="${esc(r.sell_reason || '')}"><b>청산</b> ${esc(cells.reason)}</span>
      <span class="${cells.pnlCls}"><b>손익</b> ${esc(cells.pnl)}</span>
    </div>
  </button>`;
}

function bindDayResultClicks(wrap) {
  wrap.querySelectorAll('.day-result-item').forEach((el) => {
    el.addEventListener('click', () => {
      wrap.querySelectorAll('.day-result-item').forEach((x) => {
        x.classList.toggle('is-selected', x === el);
      });
      openDayResultDetail(Number(el.dataset.idx));
    });
  });
}

function renderDayResults(data) {
  _dayResults = data.results || [];
  const wrap = $('dayResultsWrap');
  const title = $('dayResultsTitle');
  if (title) {
    title.textContent = data.condition_name
      ? `당일 검증 · ${data.condition_name}`
      : '당일 검증 결과';
  }
  if (!_dayResults.length) {
    wrap.innerHTML = '<div class="hint">편입 종목 없음 — 조건식·장중 여부를 확인하세요</div>';
    return;
  }
  wrap.innerHTML = `<div class="day-results-list">${_dayResults.map(dayResultCardHtml).join('')}</div>
    <div class="hint" style="margin-top:8px;">카드 클릭 → 체크리스트·차트 상세 · *는 가정 진입(실제 매수 아님)</div>`;
  bindDayResultClicks(wrap);
}

function openDayResultDetail(idx) {
  const r = _dayResults[idx];
  if (!r || !r.stock_code) return;
  document.querySelectorAll('.day-result-row').forEach((tr) => {
    tr.classList.toggle('is-selected', Number(tr.dataset.idx) === idx);
  });
  document.querySelectorAll('.day-result-item').forEach((el) => {
    el.classList.toggle('is-selected', Number(el.dataset.idx) === idx);
  });
  if ($('dayDetailHint')) $('dayDetailHint').style.display = 'none';
  $('singlePanels').style.display = 'block';

  const failed = r.gate_failed_checks || [];
  let failBanner = '';
  if (failed.length) {
    failBanner = '게이트 미통과: ' + failed.map((c) =>
      `${c.label || c.key}: ${c.actual || c.note || '미충족'}`,
    ).join(' · ');
  } else if (r.entry_passed === false) {
    failBanner = `게이트 미통과: ${r.entry_reason || ''}`;
  }
  if (failBanner) showBanner(failBanner, 'warn');

  const fake = {
    success: true,
    resolution: '15m',
    stock_code: r.stock_code,
    stock_name: r.stock_name,
    strategy: { key: $('strategy').value, label: $('strategy').selectedOptions[0]?.text || '' },
    entry: {
      passed: r.entry_passed,
      reason: r.entry_reason,
      time: r.buy_time,
      price: r.buy_price,
      assumed: r.entry_assumed,
    },
    exit: {
      time: r.sell_time,
      price: r.sell_price,
      reason_label: r.sell_reason,
    },
    summary: {
      buy_price: r.buy_price,
      sell_price: r.sell_price,
      profit_loss_rate_pct: r.profit_loss_rate_pct,
      reason_label: r.sell_reason,
      peak_price: null,
      peak_rate_pct: null,
      entry_passed: r.entry_passed,
    },
    simulation: {
      assumptions: [
        '당일 검증 결과 상세',
        `조건식: ${_selectedCondition || r.condition_name || '-'}`,
        '검증 전용 편입 → 15분봉 시뮬 (주문 없음)',
      ],
      bars_simulated: null,
    },
    settings_used: null,
    intraday_chart: r.intraday_chart
      ? {
          ...r.intraday_chart,
          markers: {
            ...(r.intraday_chart.markers || {}),
            buy: r.intraday_chart.markers?.buy
              ? { ...r.intraday_chart.markers.buy, assumed: !!r.entry_assumed || r.entry_passed === false }
              : null,
            sell: r.intraday_chart.markers?.sell
              ? { ...r.intraday_chart.markers.sell, assumed: !!r.entry_assumed || r.entry_passed === false }
              : null,
          },
        }
      : r.intraday_chart,
    buy_condition_checks: r.buy_condition_checks,
    buy_condition_summary: r.buy_condition_summary,
    sell_condition_checks: r.sell_condition_checks,
    sell_condition_summary: r.sell_condition_summary,
    timeline: [],
  };
  renderSummary(fake);
  renderSettings(null, fake.strategy);
  renderAssumptions(fake.simulation);
  renderChecks(fake);
  $('timelineWrap').innerHTML = '<div class="hint">타임라인은 단일 시뮬 모드에서 확인</div>';
  if (r.intraday_chart) renderChart(fake);
  else {
    $('chartStatus').style.display = 'block';
    $('chartStatus').textContent = '차트 없음 (게이트 미통과 또는 분봉 없음)';
    $('replayChart').style.display = 'none';
    if ($('chartLegend')) $('chartLegend').style.display = 'none';
  }
  $('singlePanels').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function setDayProgress(opts) {
  const box = $('dayProgress');
  if (!box) return;
  const show = opts && opts.show !== false;
  box.style.display = show ? 'block' : 'none';
  if (!show) return;
  const total = Math.max(0, Number(opts.total) || 0);
  const index = Math.max(0, Number(opts.index) || 0);
  const pct = total > 0 ? Math.min(100, Math.round((index / total) * 100)) : 0;
  const done = !!opts.done;
  const err = !!opts.error;
  if ($('dayProgressPct')) $('dayProgressPct').textContent = `${pct}%`;
  if ($('dayProgressCount')) $('dayProgressCount').textContent = total ? `${index} / ${total}` : '—';
  if ($('dayProgressFill')) {
    $('dayProgressFill').style.width = `${pct}%`;
    $('dayProgressFill').className = `batch-progress-fill ${err ? '' : (done ? 'done' : 'running')}`;
  }
  if ($('dayProgressBadge')) {
    $('dayProgressBadge').textContent = err ? '오류' : (done ? '완료' : '진행');
    $('dayProgressBadge').className = `batch-progress-badge ${err ? '' : (done ? 'done' : 'running')}`;
  }
  if ($('dayProgressTitle')) {
    $('dayProgressTitle').textContent = opts.title || '당일 검증';
  }
  if ($('dayProgressSub')) {
    $('dayProgressSub').textContent = opts.message || '';
  }
}

function appendDayResultRow(r, i) {
  const wrap = $('dayResultsWrap');
  if (!wrap) return;
  let list = wrap.querySelector('.day-results-list');
  if (!list) {
    wrap.innerHTML = `<div class="day-results-list"></div>
      <div class="hint" style="margin-top:8px;">카드 클릭 → 체크리스트·차트 상세 · *는 가정 진입(실제 매수 아님)</div>`;
    list = wrap.querySelector('.day-results-list');
  }
  list.insertAdjacentHTML('beforeend', dayResultCardHtml(r, i));
  const el = list.querySelector(`.day-result-item[data-idx="${i}"]`);
  if (el) {
    el.addEventListener('click', () => {
      list.querySelectorAll('.day-result-item').forEach((x) => {
        x.classList.toggle('is-selected', x === el);
      });
      openDayResultDetail(i);
    });
  }
}

async function runDayVerify() {
  const entry = ($('entryDate').value || '').trim();
  const strategy = $('strategy').value;
  const holdDays = $('simDays').value;
  const limit = $('verifyLimit').value;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(entry)) {
    showBanner('검증일을 선택하세요', 'warn');
    return;
  }
  if (!_selectedCondition) {
    showBanner('왼쪽에서 검증 조건식을 선택하세요', 'warn');
    $('dayResultsWrap').innerHTML = '<div class="hint">조건식을 선택하세요</div>';
    return;
  }
  _dayResults = [];
  showBanner(`「${_selectedCondition}」 검증 중… (종목 간 텀으로 API 부하 완화)`, '');
  $('summaryCards').innerHTML = '<div class="v-stat"><div class="k">상태</div><div class="v">검증 중…</div></div>';
  $('dayResultsWrap').innerHTML = '<div class="hint">조건식 편입 조회 중…</div>';
  $('singlePanels').style.display = 'none';
  if ($('dayDetailHint')) $('dayDetailHint').style.display = 'none';
  if ($('daySkippedBlock')) $('daySkippedBlock').style.display = 'none';
  setDayProgress({
    show: true,
    index: 0,
    total: 0,
    title: _selectedCondition,
    message: '조건식 편입 종목 조회 중…',
  });
  syncUrl();
  try {
    const qs = new URLSearchParams({
      strategy,
      trade_date: entry,
      limit,
      hold_days: holdDays,
      run_sim: 'true',
      condition_names: _selectedCondition,
      stream: 'true',
      pause_sec: '3.2',
    });
    const res = await fetch(`/api/strategy-day-verify?${qs}`);
    if (!res.ok) {
      let msg = `HTTP ${res.status}`;
      try {
        const data = await res.json();
        const detail = data.detail;
        msg = typeof detail === 'string'
          ? detail
          : (Array.isArray(detail) ? detail.map((d) => d.msg || d).join('; ') : (data.error || msg));
      } catch (_) { /* ndjson or empty */ }
      throw new Error(msg);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let finalData = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop() || '';
      for (const line of lines) {
        if (!line.trim()) continue;
        let ev;
        try { ev = JSON.parse(line); } catch (_) { continue; }
        const type = ev.event;

        if (type === 'error') {
          throw new Error(ev.error || '당일 검증 실패');
        }
        if (type === 'start') {
          const est = ev.api_estimate || {};
          setDayProgress({
            show: true,
            index: 0,
            total: ev.total || 0,
            title: ev.condition_name || _selectedCondition,
            message: ev.message || `편입 ${ev.universe_count || 0}종목`,
          });
          showBanner(
            `예상 API ≈${est.grand_total_min ?? '?'}~${est.grand_total_max ?? '?'}회`
            + (est.note ? ` · ${est.note}` : '')
            + (ev.truncated ? ` · 상위 ${ev.limit}개만` : ''),
            'warn',
          );
          $('dayResultsWrap').innerHTML = '<div class="hint">시뮬 시작…</div>';
          if ($('dayResultsTitle')) {
            $('dayResultsTitle').textContent = ev.condition_name
              ? `당일 검증 · ${ev.condition_name}`
              : '당일 검증 결과';
          }
        }
        if (type === 'progress') {
          setDayProgress({
            show: true,
            index: ev.phase === 'sim' ? Math.max(0, (ev.index || 1) - 1) : (ev.index || 0),
            total: ev.total || 0,
            title: _selectedCondition,
            message: ev.message || '',
          });
        }
        if (type === 'stock' && ev.result) {
          _dayResults.push(ev.result);
          if (_dayResults.length === 1) {
            $('dayResultsWrap').innerHTML = '';
          }
          appendDayResultRow(ev.result, _dayResults.length - 1);
          setDayProgress({
            show: true,
            index: ev.index || _dayResults.length,
            total: ev.total || _dayResults.length,
            title: _selectedCondition,
            message: `${ev.index || _dayResults.length}/${ev.total || '?'} `
              + `${ev.result.stock_name || ''} 완료`,
          });
        }
        if (type === 'done') {
          finalData = ev;
          setDayProgress({
            show: true,
            index: ev.simulated_count || (_dayResults.length),
            total: ev.simulated_count || (_dayResults.length),
            done: true,
            title: ev.condition_name || _selectedCondition,
            message: `완료 — 게이트 통과 ${ev.summary?.entry_passed ?? 0}`
              + ` / 실패 ${ev.summary?.entry_failed ?? 0}`
              + (ev.api_estimate
                ? ` · API 예상 ${ev.api_estimate.grand_total_min}~${ev.api_estimate.grand_total_max}회`
                : ''),
          });
        }
      }
    }

    if (!finalData) {
      throw new Error('스트림이 완료 이벤트 없이 종료되었습니다');
    }
    if (finalData.results && finalData.results.length) {
      _dayResults = finalData.results;
    }
    showBanner(
      finalData.truncated
        ? `편입 ${finalData.universe_count}종목 중 상위 ${finalData.limit}개만 시뮬 — 나머지 ${(finalData.skipped || []).length}개는 「시뮬수」한도로 제외`
        : '',
      finalData.truncated ? 'warn' : '',
    );
    renderDaySummary(finalData);
    renderDaySkipped(finalData);
    if ($('dayDetailHint') && _dayResults.length) {
      $('dayDetailHint').style.display = 'block';
    }
  } catch (e) {
    showBanner(String(e.message || e), 'error');
    setDayProgress({
      show: true,
      index: _dayResults.length,
      total: _dayResults.length || 1,
      error: true,
      message: String(e.message || e),
    });
    $('summaryCards').innerHTML = '<div class="v-stat"><div class="k">오류</div><div class="v down" style="font-size:14px;">당일 검증 실패</div></div>';
    if (!_dayResults.length) {
      $('dayResultsWrap').innerHTML = '<div class="hint">실패</div>';
    }
  }
}

document.addEventListener('DOMContentLoaded', () => {
  initFromUrl();
  $('resolution').addEventListener('change', syncDaysOptions);
  $('btnSimulate').addEventListener('click', () => {
    if (_uiMode === 'day') runDayVerify();
    else runSimulation();
  });
  $('stockCode').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') runSimulation();
  });
  document.querySelectorAll('.mode-tab').forEach((btn) => {
    btn.addEventListener('click', () => setUiMode(btn.dataset.mode));
  });
  $('strategy').addEventListener('change', () => {
    if (_uiMode === 'day') {
      _selectedCondition = '';
      window._conditionNamesOverride = '';
      loadDayConditions();
    }
  });
  if ($('btnReloadConditions')) {
    $('btnReloadConditions').addEventListener('click', () => loadDayConditions());
  }
});
