'use strict';

const $ = (id) => document.getElementById(id);

let _trades = [];
const _chartLoaded = new Set();

/** KST 기준 오늘 YYYY-MM-DD */
function kstToday() {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Seoul' });
}

/** KST 기준 어제 YYYY-MM-DD */
function kstYesterday() {
  const parts = kstToday().split('-').map(Number);
  const d = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
  d.setUTCDate(d.getUTCDate() - 1);
  return d.toISOString().slice(0, 10);
}

function getDatePreset() {
  const d = new URLSearchParams(window.location.search).get('date');
  if (!d || d === 'all') return 'all';
  if (d === 'this_week') return 'this_week';
  if (d === kstToday()) return 'today';
  if (d === kstYesterday()) return 'yesterday';
  return 'custom';
}

function updateDatePresetButtons() {
  const preset = getDatePreset();
  document.querySelectorAll('[data-date-preset]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.datePreset === preset);
  });
}

function getDateFilterParam() {
  const p = new URLSearchParams(window.location.search).get('date');
  if (!p || p === 'all') return null;
  return p;
}

function getSelectedDate() {
  const el = $('tradeDate');
  const v = el && el.value ? el.value.trim() : '';
  return v || null;
}

function setDateFilter(value, opts) {
  const el = $('tradeDate');
  if (el) {
    el.value = (value && /^\d{4}-\d{2}-\d{2}$/.test(value)) ? value : '';
  }
  if (opts && opts.skipUrl) {
    updateDatePresetButtons();
    return;
  }
  const url = new URL(window.location.href);
  url.searchParams.set('date', value || 'all');
  window.history.replaceState({}, '', url);
  updateDatePresetButtons();
}

function initDateFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const fromUrl = params.get('date');
  if (fromUrl === 'all') {
    setDateFilter(null, { skipUrl: true });
    return;
  }
  if (fromUrl === 'this_week') {
    setDateFilter('this_week', { skipUrl: true });
    return;
  }
  if (fromUrl && /^\d{4}-\d{2}-\d{2}$/.test(fromUrl)) {
    setDateFilter(fromUrl, { skipUrl: true });
    return;
  }
  setDateFilter(kstToday(), { skipUrl: true });
  const url = new URL(window.location.href);
  url.searchParams.set('date', kstToday());
  window.history.replaceState({}, '', url);
  updateDatePresetButtons();
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}
function num(v) { return Math.round(Number(v) || 0).toLocaleString('ko-KR'); }
function pnlClass(n) { n = Number(n) || 0; return n > 0 ? 'up' : (n < 0 ? 'down' : ''); }
function pnlStr(n) {
  n = Number(n) || 0;
  const s = n > 0 ? '+' : '';
  return `${s}${num(n)}원`;
}
function rateStr(n) {
  if (n == null || n === '') return '-';
  n = Number(n);
  const s = n > 0 ? '+' : '';
  return `${s}${n.toFixed(2)}%`;
}
function fillTypePill(f) {
  const ft = (f.fill_type || 'INITIAL').toUpperCase();
  if (ft === 'ADD') return '<span class="pill verdict-check">추가매수</span>';
  const sizing = (f.sizing_method || '').toUpperCase();
  if (sizing === 'PYRAMIDING') return '<span class="pill verdict-ok">역피라미딩 초기</span>';
  return '<span class="pill off">초기매수</span>';
}
function checkMark(passed) {
  if (passed === true) return '<span class="v-chk pass" title="통과">✓</span>';
  if (passed === false) return '<span class="v-chk fail" title="미통과">✗</span>';
  return '<span class="v-chk unk" title="추정/데이터 없음">?</span>';
}
function sellStatusPill(status) {
  const m = {
    COMPLETED: ['verdict-ok', '체결'],
    ORDERED: ['verdict-check', '주문접수'],
    PENDING: ['verdict-check', '대기'],
    FAILED: ['verdict-fail', '실패'],
  };
  const [cls, label] = m[status] || ['off', status || '-'];
  return `<span class="pill ${cls}">${label}</span>`;
}
function formatSummaryLines(text) {
  if (!text || text === '-') return '<div class="hint">—</div>';
  return String(text)
    .split(/\s*[|·]\s*/)
    .filter(Boolean)
    .map((line) => `<div class="v-summary-line">${esc(line.trim())}</div>`)
    .join('');
}
function renderEntryVerdictBlock(t) {
  const notes = (t.entry_notes || []).map((n) => `<li>${esc(n)}</li>`).join('');
  return `<div class="v-block">
    <h4>매수 진입 검증</h4>
    <div class="v-verdict-row">${verdictPill(t.entry_verdict)}</div>
    ${notes ? `<ul class="v-notes">${notes}</ul>` : '<div class="hint">검증 메모 없음</div>'}
  </div>`;
}
function effectiveSellOrders(t) {
  const orders = t.sell_orders || [];
  if (orders.length) return orders;
  const sell = t.sell || {};
  if (sell.time || sell.price != null) {
    return [{
      status: sell.status || 'COMPLETED',
      status_label: sell.status === 'COMPLETED' ? '체결' : (sell.status || '체결'),
      reason: sell.reason,
      reason_code: sell.reason_code,
      completed_at: sell.time,
      ordered_at: sell.time,
      time: sell.time,
      price: sell.price,
      quantity: sell.quantity,
      amount: sell.amount,
      profit_loss: sell.profit_loss,
      profit_loss_rate: sell.profit_loss_rate,
      reason_detail: sell.reason_detail || '타임라인·포지션 기록',
      is_backfill: true,
    }];
  }
  return [];
}
function renderSellConditionChecksBlock(t) {
  const checks = t.sell_condition_checks || [];
  if (!checks.length && (t.sell?.reason || t.exit_rules)) {
    const sell = t.sell || {};
    const rows = [
      sell.reason ? `<tr><td class="v-chk-group">청산</td><td class="v-chk-mark">${checkMark(true)}</td><td>청산 사유</td><td>${esc(sell.reason)}</td><td>—</td><td class="v-chk-note"></td></tr>` : '',
      sell.time ? `<tr><td class="v-chk-group">체결</td><td class="v-chk-mark">${checkMark(true)}</td><td>매도 시각</td><td>${esc(sell.time)}</td><td>—</td><td class="v-chk-note"></td></tr>` : '',
      sell.price != null ? `<tr><td class="v-chk-group">체결</td><td class="v-chk-mark">${checkMark(true)}</td><td>매도가</td><td>${num(sell.price)}원</td><td>—</td><td class="v-chk-note"></td></tr>` : '',
    ].join('');
    if (rows) {
      return `<div class="v-block"><h4>매도 조건 체크</h4><div class="hint" style="color:var(--amber);margin-bottom:8px;">상세 체크 없음 — 서버 재시작 후 Ctrl+F5 하면 전체 규칙이 표시됩니다</div><div class="v-scroll-box v-scroll-box--x"><table class="tbl v-chk-tbl compact"><thead><tr><th>구분</th><th></th><th>조건</th><th>실제값</th><th>기준</th><th>비고</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
    }
  }
  if (!checks.length) {
    return '<div class="v-block"><h4>매도 조건 체크</h4><div class="hint">조건 체크 데이터 없음</div></div>';
  }
  const sum = t.sell_condition_summary || {};
  const sumLine = sum.total != null
    ? `<div class="v-fill-summary">${sum.passed || 0} / ${sum.total} 충족${sum.failed ? ` · <span style="color:var(--down)">미충족 ${sum.failed}</span>` : ''}${sum.unknown ? ` · 추정 ${sum.unknown}` : ''}</div>`
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
  return `<div class="v-block">
    <h4>매도 조건 체크</h4>
    ${sumLine}
    <div class="v-scroll-box v-scroll-box--x">
      <table class="tbl v-chk-tbl compact"><thead><tr>
        <th>구분</th><th></th><th>조건</th><th>실제값</th><th>기준</th><th>비고</th>
      </tr></thead><tbody>${rows}</tbody></table>
    </div>
  </div>`;
}
function renderSellOrdersBlock(t) {
  const orders = effectiveSellOrders(t);
  if (!orders.length) {
    const hint = t.status === 'HOLDING' ? '보유 중 — 매도 주문 없음' : '매도 주문·체결 이력 없음';
    return `<div class="v-block"><h4>매도 체결 이력</h4><div class="hint">${esc(hint)}</div></div>`;
  }
  const summary = t.sell_fills_summary ? `<div class="v-fill-summary">${esc(t.sell_fills_summary)}</div>` : '';
  const rows = orders.map((o, n) => `<tr>
    <td>${n + 1}</td>
    <td>${sellStatusPill(o.status)}</td>
    <td>${esc(o.reason || '-')}</td>
    <td>${esc(o.completed_at || o.ordered_at || o.time || '-')}</td>
    <td class="num">${num(o.price)}</td>
    <td class="num">${num(o.quantity)}</td>
    <td class="num">${num(o.amount)}</td>
    <td class="num ${pnlClass(o.profit_loss)}">${o.profit_loss != null ? pnlStr(o.profit_loss) : '-'}</td>
    <td class="num ${pnlClass(o.profit_loss_rate)}">${rateStr(o.profit_loss_rate)}</td>
    <td style="font-size:12px;color:var(--muted);">${esc(o.reason_detail || '-')}${o.is_backfill ? ' <span style="color:var(--amber)">(추정)</span>' : ''}</td>
  </tr>`).join('');
  return `<div class="v-block">
    <h4>매도 체결 이력</h4>
    ${summary}
    <div class="v-scroll-box" style="max-height:220px;">
      <table class="tbl v-fill-tbl"><thead><tr>
        <th>#</th><th>상태</th><th>사유</th><th>체결/주문시각</th><th class="num">단가</th>
        <th class="num">수량</th><th class="num">금액</th><th class="num">손익</th><th class="num">수익률</th><th>상세</th>
      </tr></thead><tbody>${rows}</tbody></table>
    </div>
  </div>`;
}
function compactNote(note) {
  if (!note) return '';
  const n = String(note);
  if (n.includes('당시 통과 추정')) return '통과 추정';
  if (n.includes('당시 시세 데이터 없음')) return '시세 없음';
  if (n.includes('이 규칙 미사용')) return '미사용';
  if (n.includes('트레일%')) return '트레일 청산';
  if (n === '청산 사유') return '청산';
  if (n.includes('reconcile')) return '추정';
  return n;
}
function renderBuyConditionChecksBlock(t) {
  const checks = t.buy_condition_checks || [];
  if (!checks.length) {
    return '<div class="v-block"><h4>매수 조건 체크</h4><div class="hint">조건 체크 데이터 없음</div></div>';
  }
  const sum = t.buy_condition_summary || {};
  const sumLine = sum.total != null
    ? `<div class="v-fill-summary">${sum.passed || 0} / ${sum.total} 통과${sum.failed ? ` · <span style="color:var(--down)">미통과 ${sum.failed}</span>` : ''}${sum.unknown ? ` · 추정 ${sum.unknown}` : ''}</div>`
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
  return `<div class="v-block">
    <h4>매수 조건 체크</h4>
    ${sumLine}
    <div class="v-scroll-box v-scroll-box--x">
      <table class="tbl v-chk-tbl compact"><thead><tr>
        <th>구분</th><th></th><th>조건</th><th>실제값</th><th>기준</th><th>비고</th>
      </tr></thead><tbody>${rows}</tbody></table>
    </div>
  </div>`;
}
function renderBuyFillsBlock(t) {
  const fills = t.buy_fills || [];
  if (!fills.length) {
    return '<div class="v-block"><h4>매수 체결 이력</h4><div class="hint">체결 이력 없음</div></div>';
  }
  const summary = t.buy_fills_summary ? `<div class="v-fill-summary">${esc(t.buy_fills_summary)}</div>` : '';
  const rows = fills.map((f, n) => `<tr>
    <td>${n + 1}</td>
    <td>${fillTypePill(f)}</td>
    <td>${esc(f.time || '-')}</td>
    <td class="num">${num(f.price)}</td>
    <td class="num">${f.order_quantity != null && f.order_quantity !== f.quantity ? `${num(f.order_quantity)} → ` : ''}${num(f.quantity)}</td>
    <td class="num">${num(f.amount)}</td>
    <td class="num">${f.planned_amount != null ? num(f.planned_amount) : '-'}</td>
    <td class="num">${f.change_rate != null ? rateStr(f.change_rate) : '-'}</td>
    <td style="font-size:12px;color:var(--muted);">${esc(f.detail || '-')}${f.is_backfill ? ' <span style="color:var(--amber)">(추정)</span>' : ''}</td>
  </tr>`).join('');
  return `<div class="v-block">
    <h4>매수 체결 이력</h4>
    ${summary}
    <div class="v-scroll-box" style="max-height:220px;">
      <table class="tbl v-fill-tbl"><thead><tr>
        <th>#</th><th>구분</th><th>체결시각</th><th class="num">단가</th><th class="num">수량</th>
        <th class="num">금액</th><th class="num">계획금액</th><th class="num">등락률</th><th>비고</th>
      </tr></thead><tbody>${rows}</tbody></table>
    </div>
  </div>`;
}
function verdictPill(v) {
  const m = { OK: ['verdict-ok', '진입 OK'], CHECK: ['verdict-check', '확인 필요'], FAIL: ['verdict-fail', '실패'] };
  const [cls, label] = m[v] || ['off', v || '-'];
  return `<span class="pill ${cls}">${label}</span>`;
}

function strategyPill(t) {
  const key = t.strategy_key || (t.signal && (t.signal.strategy || t.signal.source));
  if (!key) return '';
  const label = t.strategy_label || (key === 'sangtta' ? '상따' : (key === 'breakout' ? '과매도 돌파' : key));
  const cls = key === 'sangtta'
    ? 'strategy-sangtta'
    : (key === 'breakout' ? 'strategy-breakout' : 'strategy-legacy');
  return `<span class="pill ${cls}" title="strategy=${esc(key)}">${esc(label)}</span>`;
}

function sangttaExitPill(t) {
  if (!t.sangtta_exit_label) return '';
  return `<span class="pill strategy-sangtta-exit">${esc(t.sangtta_exit_label)}</span>`;
}

function showBanner(msg, isErr) {
  const b = $('statusBanner');
  if (!b) return;
  if (!msg) { b.style.display = 'none'; return; }
  b.style.display = 'block';
  b.className = 'verify-banner' + (isErr ? ' err' : ' ok');
  b.textContent = msg;
}

function showFatal(msg) {
  showBanner(msg, true);
  const sk = `<div class="empty"><span class="ico">⚠️</span>${esc(msg)}<br><small>server.bat restart 후 Ctrl+F5</small></div>`;
  if ($('summaryCards')) $('summaryCards').innerHTML = sk;
  if ($('tradeTableWrap')) $('tradeTableWrap').innerHTML = sk;
  if ($('tradeList')) $('tradeList').innerHTML = sk;
  if ($('guideBuy')) $('guideBuy').innerHTML = sk;
  if ($('guideExit')) $('guideExit').innerHTML = sk;
  if ($('failedList')) $('failedList').innerHTML = sk;
}

async function loadReport() {
  const dateParam = getDateFilterParam();
  showBanner('데이터 불러오는 중…', false);
  try {
    let url = '/verification/trades?limit=100';
    if (dateParam) url += `&date=${encodeURIComponent(dateParam)}`;
    const res = await fetch(url, {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    });
    if (!res.ok) {
      throw new Error(`API 오류 HTTP ${res.status} — 서버 재시작 필요할 수 있습니다 (/verification/trades)`);
    }
    const text = await res.text();
    let d;
    try {
      d = JSON.parse(text);
    } catch {
      throw new Error('API가 JSON이 아닌 응답을 반환했습니다. 서버가 최신 코드인지 확인하세요.');
    }
    if (!d.success) throw new Error(d.error || '조회 실패');

    $('genAt').textContent = d.generated_at || '-';
    const label = (d.filter && d.filter.trade_date_label) || (dateParam || '전체');
    const count = (d.summary && d.summary.positions) || 0;
    showBanner(`${label} · 매매 ${count}건`, false);
    updateDatePresetButtons();
    renderSummary(d.summary || {});
    renderGuide(d.calculation_guide || {});
    _trades = d.trades || [];
    _chartLoaded.clear();
    renderTradeTable(_trades);
    renderTrades(_trades);
    renderFailed(d.failed_signals || []);
  } catch (e) {
    console.error('[verify]', e);
    showFatal(e.message || String(e));
  }
}

function renderSummary(s) {
  const pnl = s.total_realized_pnl || 0;
  $('summaryCards').innerHTML = `
    <div class="v-stat"><div class="k">포지션</div><div class="v">${s.positions || 0}</div></div>
    <div class="v-stat"><div class="k">청산 완료</div><div class="v">${s.closed_trades || 0}</div></div>
    <div class="v-stat"><div class="k">보유 중</div><div class="v">${s.holding || 0}</div></div>
    <div class="v-stat"><div class="k">승 / 패</div><div class="v">${s.wins || 0} / ${s.losses || 0}</div></div>
    <div class="v-stat"><div class="k">승률</div><div class="v">${s.win_rate_pct != null ? s.win_rate_pct + '%' : '-'}</div></div>
    <div class="v-stat"><div class="k">실현 손익 합계</div><div class="v ${pnlClass(pnl)}">${pnlStr(pnl)}</div></div>
  `;
}

function renderGuide(g) {
  const stepHtml = (rows) => {
    if (!rows || !rows.length) return '<div class="hint">가이드 없음</div>';
    return rows.map((r) =>
      `<div class="guide-step"><span class="step">${esc(r.step)}</span><span>${esc(r.desc)}</span></div>`
    ).join('');
  };
  $('guideBuy').innerHTML = stepHtml(g.buy_pipeline);
  $('guideExit').innerHTML = stepHtml([...(g.exit_priority || []), ...(g.pnl || [])]);
}

function renderTradeTable(trades) {
  const el = $('tradeTableWrap');
  const tradeDate = getSelectedDate();
  if (!trades.length) {
    const hint = tradeDate
      ? `${tradeDate}에 매수·매도된 거래가 없습니다.`
      : '매매 내역이 없습니다.';
    el.innerHTML = `<div class="empty"><span class="ico">📭</span>${esc(hint)}</div>`;
    return;
  }
  const rows = trades.map((t, i) => {
    const buy = t.buy || {};
    const sell = t.sell || {};
    const pl = sell.profit_loss;
    return `<tr data-idx="${i}" class="verify-row" style="cursor:pointer;">
      <td>${verdictPill(t.entry_verdict)} ${strategyPill(t)}</td>
      <td><span class="stock-name">${esc(t.stock_name)}</span><span class="stock-code">${esc(t.stock_code)}</span></td>
      <td>${esc(buy.time || '-')}</td>
      <td class="num">${num(buy.price)}</td>
      <td class="num">${num(buy.quantity)}</td>
      <td>${esc(sell.time || (t.status === 'HOLDING' ? '보유' : '-'))}</td>
      <td class="num">${sell.price != null ? num(sell.price) : '-'}</td>
      <td>${esc(sell.reason || '-')}${t.sangtta_exit_label && !(sell.reason || '').includes(t.sangtta_exit_label) ? ` ${sangttaExitPill(t)}` : ''}</td>
      <td class="num ${pnlClass(pl)}">${pl != null ? pnlStr(pl) : '-'}</td>
      <td class="num ${pnlClass(sell.profit_loss_rate)}">${rateStr(sell.profit_loss_rate)}</td>
    </tr>`;
  }).join('');
  el.innerHTML = `<table class="tbl"><thead><tr>
    <th>진입·전략</th><th>종목</th><th>매수시각</th><th class="num">매수가</th><th class="num">수량</th>
    <th>매도시각</th><th class="num">매도가</th><th>사유</th><th class="num">손익</th><th class="num">수익률</th>
  </tr></thead><tbody>${rows}</tbody></table>`;
  el.querySelectorAll('.verify-row').forEach((row) => {
    row.addEventListener('click', () => openTrade(parseInt(row.dataset.idx, 10)));
  });
}

function renderTrades(trades) {
  const tradeDate = getSelectedDate();
  if (!trades.length) {
    const hint = tradeDate
      ? `${tradeDate}에 매수·매도된 거래가 없습니다.`
      : '매매 내역이 없습니다.';
    $('tradeList').innerHTML = `<div class="empty"><span class="ico">📭</span>${esc(hint)}</div>`;
    return;
  }
  $('tradeList').innerHTML = trades.map((t, i) => buildTradeCard(t, i)).join('');
  const firstOpen = document.querySelector('.trade-card.open');
  if (firstOpen && firstOpen.dataset.idx != null) {
    loadTradeChart(parseInt(firstOpen.dataset.idx, 10));
  }
}

function buildTradeCard(t, i) {
  const sell = t.sell || {};
  const buy = t.buy || {};
  const pl = sell.profit_loss;
  const closed = sell.status === 'COMPLETED';
  const headPnl = closed && pl != null ? pnlStr(pl) : (t.status === 'HOLDING' ? '보유' : '-');
  const pnlCls = closed ? pnlClass(pl) : '';

  const signalBlock = t.signal ? `
    <div class="v-kv"><div class="k">신호 시각</div><div class="v">${esc(t.signal.detected_at)}</div></div>
    <div class="v-kv"><div class="k">신호 상태</div><div class="v">${esc(t.signal.status)} · ${esc(t.signal.signal_type)}</div></div>
  ` : '<div class="v-kv"><div class="k">신호</div><div class="v">없음</div></div>';

  const exitSteps = (t.exit_calc_steps || []).map((s) =>
    `<div class="v-formula"><strong>${esc(s.rule)}</strong> ${esc(s.formula)}${s.price != null ? ` → <b>${num(s.price)}원</b>` : ''}${s.note ? `<br><span style="color:var(--muted)">${esc(s.note)}</span>` : ''}</div>`
  ).join('');

  const pnlCalc = t.pnl_calc ? `
    <div class="v-formula">${esc(t.pnl_calc.formula)}</div>
    <div class="v-kv"><div class="k">계산 손익</div><div class="v ${pnlClass(t.pnl_calc.calculated_pnl)}">${pnlStr(t.pnl_calc.calculated_pnl)} (${rateStr(t.pnl_calc.calculated_rate_pct)})</div></div>
    <div class="v-kv"><div class="k">DB 기록 손익</div><div class="v">${t.pnl_calc.recorded_pnl != null ? pnlStr(t.pnl_calc.recorded_pnl) : '-'}</div></div>
    ${t.pnl_calc.match === false ? '<div class="hint" style="color:var(--amber)">⚠ 계산값과 DB 기록 차이 — 수수료·체결가 반올림 가능</div>' : ''}
  ` : '';

  const openCls = i === 0 ? ' open' : '';

  return `<article class="trade-card${openCls}" id="trade-card-${i}" data-idx="${i}">
    <div class="trade-card-head" role="button" tabindex="0" data-idx="${i}">
      <div>
        <div class="trade-title">${esc(t.stock_name)} <span class="stock-code">${esc(t.stock_code)}</span> ${verdictPill(t.entry_verdict)} ${strategyPill(t)} ${sangttaExitPill(t)}</div>
        <div class="trade-meta">${esc(t.condition_name)}${t.strategy_key ? ` · strategy=${esc(t.strategy_key)}` : ''} · ${esc(buy.time || '')} → ${esc(sell.time || '보유')}${t.hold_hours != null ? ` · ${t.hold_hours}h` : ''}</div>
      </div>
      <div class="trade-pnl ${pnlCls}">${headPnl}${sell.profit_loss_rate != null ? `<div style="font-size:12px;font-weight:500;">${rateStr(sell.profit_loss_rate)}</div>` : ''}</div>
    </div>
    <div class="trade-body">
      <div class="v-block">
        <h4>타임라인</h4>
        <div class="v-timeline">
          ${signalBlock}
          ${t.strategy_key ? `<div class="v-kv"><div class="k">전략</div><div class="v">strategy=${esc(t.strategy_key)}${t.signal && t.signal.gate_pack ? ` · gate=${esc(t.signal.gate_pack)}` : ''}${t.breakout_level_kind ? ` · ${esc(t.breakout_level_kind)} ${num(t.breakout_level_price)}원` : ''}</div></div>` : ''}
          <div class="v-kv"><div class="k">매수</div><div class="v">${esc(buy.time)} · ${num(buy.price)}원 × ${num(buy.quantity)}주</div></div>
          <div class="v-kv"><div class="k">매수금액</div><div class="v">${num(buy.amount)}원${buy.actual_amount ? ` (실매입 ${num(buy.actual_amount)}원)` : ''}</div></div>
          ${sell.time ? `<div class="v-kv"><div class="k">매도</div><div class="v">${esc(sell.time)} · ${num(sell.price)}원 · ${esc(sell.reason)}</div></div>` : ''}
          ${sell.reason_detail ? `<div class="v-kv"><div class="k">매도 상세</div><div class="v">${esc(sell.reason_detail)}</div></div>` : ''}
          ${t.peak_price ? `<div class="v-kv"><div class="k">진입 후 고점</div><div class="v">${num(t.peak_price)}원</div></div>` : ''}
        </div>
      </div>
      ${renderBuyFillsBlock(t)}
      ${renderEntryVerdictBlock(t)}
      ${renderBuyConditionChecksBlock(t)}
      <div class="v-block">
        <h4>매수 진입 조건 (현재 DB 설정 기준)</h4>
        <div class="v-summary">${formatSummaryLines(t.entry_summary)}</div>
      </div>
      ${renderSellOrdersBlock(t)}
      ${renderSellConditionChecksBlock(t)}
      <div class="v-block">
        <h4>청산 규칙 · 계산식</h4>
        <div class="v-summary">${formatSummaryLines(t.exit_rules)}</div>
        ${exitSteps || '<div class="hint">청산 가격 계산식 없음</div>'}
      </div>
      ${pnlCalc ? `<div class="v-block"><h4>손익 검증</h4>${pnlCalc}</div>` : ''}
      <div class="v-block v-chart-block">
        <h4>매매 15분봉</h4>
        <div class="v-chart-wrap" id="chart-wrap-${i}" data-idx="${i}">
          <div class="v-chart-status">카드를 펼치면 차트를 불러옵니다</div>
          <canvas class="v-chart-canvas" aria-label="매매 15분봉 차트"></canvas>
          <div class="v-chart-legend" style="display:none;">
            <span class="lg-buy">● 매수(가격)</span>
            <span class="lg-sell">● 매도(가격)</span>
            <span class="lg-stop">┄ 손절</span>
            <span class="lg-take">┄ 익절</span>
            <span class="lg-up">▮ 양봉</span>
            <span class="lg-down">▮ 음봉</span>
          </div>
        </div>
      </div>
    </div>
  </article>`;
}

function openTrade(idx) {
  document.querySelectorAll('.trade-card').forEach((c) => c.classList.remove('open'));
  const el = document.getElementById(`trade-card-${idx}`);
  if (el) {
    el.classList.add('open');
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    loadTradeChart(idx);
  }
}

function parseTs(s) {
  if (!s) return null;
  const raw = String(s).trim().replace(' ', 'T');
  const t = Date.parse(/[zZ]|[+-]\d{2}:?\d{2}$/.test(raw) ? raw : `${raw}+09:00`);
  return Number.isFinite(t) ? t : null;
}

function barTs(bar) {
  return parseTs(bar.timestamp);
}

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

function buildChartLevelLines(t) {
  const lines = [];
  const snap = t.exit_snapshot || {};
  const steps = t.exit_calc_steps || [];

  let sl = snap.stop_loss_price;
  if (!sl) {
    const step = steps.find((s) => s.rule && String(s.rule).includes('ATR 손절'))
      || steps.find((s) => s.rule && String(s.rule).includes('손절 %'));
    if (step && step.price != null) sl = step.price;
  }
  if (sl) lines.push({ kind: 'stop', price: Number(sl), label: `손절 ${num(sl)}` });

  let tp = snap.trailing_floor_price || snap.take_profit_price;
  if (!tp) {
    const step = steps.find((s) => s.rule && String(s.rule).includes('익절 바닥'));
    if (step && step.price != null) tp = step.price;
  }
  if (tp) lines.push({ kind: 'take', price: Number(tp), label: `익절 ${num(tp)}` });

  const trail = steps.find((s) => s.rule && String(s.rule).includes('ATR 트레일'))
    || steps.find((s) => s.rule && String(s.rule).includes('트레일링 %'));
  if (trail && trail.price != null && Number(trail.price) !== Number(tp)) {
    lines.push({ kind: 'trail', price: Number(trail.price), label: `트레일 ${num(trail.price)}` });
  }

  return lines.filter((ln) => ln.price > 0);
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
    bars.forEach((b) => {
      volMax = Math.max(volMax, Number(b.volume) || 0);
    });
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
  const volYOf = (v) => volTop + volPanelH - (v / volMax) * volPanelH;
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
    const h = Math.max(1, Math.abs(closeY - openY));
    ctx.fillRect(x - bodyW / 2, top, bodyW, h);

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
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padL, volTop);
    ctx.lineTo(W - padR, volTop);
    ctx.stroke();
    ctx.fillStyle = text;
    ctx.font = '9px system-ui, sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText('거래량', padL - 4, volTop + 10);
    const volLbl = volMax >= 10000
      ? `${Math.round(volMax / 10000).toLocaleString('ko-KR')}만`
      : volMax.toLocaleString('ko-KR');
    ctx.fillText(volLbl, padL - 4, volTop + volPanelH - 2);
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
  drawMarker(markers.buy, buyC, buyPx ? `매수(${buyPx})` : '매수');
  drawMarker(markers.sell, sellC, sellPx ? `매도(${sellPx})` : '매도');

  ctx.fillStyle = text;
  ctx.font = '10px system-ui, sans-serif';
  ctx.textAlign = 'center';
  const multiDay = (payload.date_range && payload.date_range.length === 2 && payload.date_range[0] !== payload.date_range[1]);
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

async function fetchTradeChart(wrapId, cacheKey, url, trade) {
  const wrap = document.getElementById(wrapId);
  if (!wrap) return;
  if (_chartLoaded.has(cacheKey)) return;

  const statusEl = wrap.querySelector('.v-chart-status');
  const canvas = wrap.querySelector('.v-chart-canvas');
  if (!statusEl || !canvas) return;

  _chartLoaded.add(cacheKey);
  statusEl.textContent = '15분봉 불러오는 중…';
  statusEl.classList.remove('err', 'warn');
  statusEl.style.display = 'block';
  canvas.style.display = 'none';

  try {
    const res = await fetch(url, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    const data = await res.json();
    if (!res.ok || !data.success) {
      statusEl.textContent = data.error || data.detail || `차트 조회 실패 (HTTP ${res.status})`;
      statusEl.classList.add('err');
      statusEl.style.display = 'block';
      _chartLoaded.delete(cacheKey);
      return;
    }
    if (trade) {
      data.level_lines = buildChartLevelLines(trade);
    }
    renderIntradayChart(canvas, data);
  } catch (e) {
    console.error('[verify chart]', e);
    statusEl.textContent = '차트를 불러오지 못했습니다. 네트워크·서버 상태를 확인하세요.';
    statusEl.classList.add('err');
    statusEl.style.display = 'block';
    _chartLoaded.delete(cacheKey);
  }
}

function buildChartUrl(t) {
  const buy = t.buy || {};
  const sell = t.sell || {};
  const buyDate = t.buy_date_kst || (buy.time ? buy.time.slice(0, 10) : '');
  const sellDate = t.sell_date_kst || '';
  const buyTime = buy.time_kst || buy.time;
  const sellTime = sell.time_kst || sell.time;

  let url = `/verification/chart?stock_code=${encodeURIComponent(t.stock_code)}&date=${encodeURIComponent(buyDate)}`;
  if (sell.status === 'COMPLETED' && sellDate && sellDate !== buyDate) {
    url += `&end_date=${encodeURIComponent(sellDate)}`;
  }
  if (buyTime) {
    url += `&buy_time=${encodeURIComponent(buyTime)}`;
    if (buy.price != null) url += `&buy_price=${encodeURIComponent(buy.price)}`;
  }
  if (sell.status === 'COMPLETED' && sellTime) {
    url += `&sell_time=${encodeURIComponent(sellTime)}`;
    url += `&sell_date=${encodeURIComponent(sellDate)}`;
    if (sell.price != null) url += `&sell_price=${encodeURIComponent(sell.price)}`;
  }
  return url;
}

async function loadTradeChart(idx) {
  if (_chartLoaded.has(idx)) return;
  const t = _trades[idx];
  if (!t) return;

  const buyDate = t.buy_date_kst || (t.buy && t.buy.time ? t.buy.time.slice(0, 10) : '');
  if (!buyDate) {
    const wrap = document.getElementById(`chart-wrap-${idx}`);
    const statusEl = wrap && wrap.querySelector('.v-chart-status');
    if (statusEl) {
      statusEl.textContent = '매수 일자를 확인할 수 없습니다';
      statusEl.classList.add('err');
    }
    return;
  }

  await fetchTradeChart(`chart-wrap-${idx}`, String(idx), buildChartUrl(t), t);
}

function renderFailed(rows) {
  if (!rows.length) {
    $('failedList').innerHTML = '<div class="empty">실패 신호 없음</div>';
    return;
  }
  $('failedList').innerHTML = `<table class="tbl"><thead><tr><th>시각</th><th>종목</th><th>사유</th><th>조건</th></tr></thead><tbody>${
    rows.map((r) => `<tr>
      <td>${esc(r.detected_at)}</td>
      <td><span class="stock-name">${esc(r.stock_name)}</span><span class="stock-code">${esc(r.stock_code)}</span></td>
      <td style="color:var(--down);">${esc(r.reason)}</td>
      <td style="font-size:12px;color:var(--muted);max-width:320px;">${esc(r.entry_summary)}</td>
    </tr>`).join('')
  }</tbody></table>`;
}

function init() {
  initDateFromUrl();
  const btn = $('btnRefresh');
  if (btn) btn.addEventListener('click', loadReport);
  const dateEl = $('tradeDate');
  if (dateEl) {
    dateEl.addEventListener('change', () => {
      const v = getSelectedDate();
      if (v) setDateFilter(v);
      loadReport();
    });
  }
  const btnToday = $('btnToday');
  if (btnToday) btnToday.addEventListener('click', () => {
    setDateFilter(kstToday());
    loadReport();
  });
  const btnYesterday = $('btnYesterday');
  if (btnYesterday) btnYesterday.addEventListener('click', () => {
    setDateFilter(kstYesterday());
    loadReport();
  });
  const btnThisWeek = $('btnThisWeek');
  if (btnThisWeek) btnThisWeek.addEventListener('click', () => {
    setDateFilter('this_week');
    loadReport();
  });
  const btnAll = $('btnAllDates');
  if (btnAll) btnAll.addEventListener('click', () => {
    setDateFilter(null);
    loadReport();
  });
  updateDatePresetButtons();
  $('tradeList').addEventListener('click', (e) => {
    const head = e.target.closest('.trade-card-head');
    if (head && head.dataset.idx != null) openTrade(parseInt(head.dataset.idx, 10));
  });
  loadReport();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
