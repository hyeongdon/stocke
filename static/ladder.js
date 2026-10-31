'use strict';

(function () {
  const STATUS_LABEL = {
    RSI_WAIT: '진입 대기',
    ACTIVE: '감시중',
    EXITING: '익절 진행',
    FULL: '10차·수동판단',
    PAUSED: '일시정지',
    DONE: '종료',
  };
  const RULE_LABEL = {
    S0: 'S0 수익 중 20선 50%',
    S1: 'S1 10선 50%',
    S2: 'S2 15선 25%',
    S3: 'S3 20선 잔량',
    MANUAL_HALF: '수동 50%',
    MANUAL_ALL: '수동 전량',
  };
  const NEAR_PCT = 3;
  const CHART_LEN = 150;
  const REFRESH_MS = 30000;
  const MA_COLORS = { 5: '#3b82f6', 10: '#f472b6', 20: '#9ae600', 60: '#e2e8f0' };
  const MA_WIDTH = { 5: 1.35, 10: 1.25, 20: 2.6, 60: 2.6 };
  const CHART_MAS = [5, 10, 20, 60];

  const state = {
    data: null,
    selectedId: null,
    filter: 'all',
    busy: false,
    quote: null,
    editingId: null,
    warmTries: 0,
    reloadTimer: null,
  };

  // ---------- 공통 ----------
  function tickSize(p) {
    if (p < 2000) return 1;
    if (p < 5000) return 5;
    if (p < 20000) return 10;
    if (p < 50000) return 50;
    if (p < 200000) return 100;
    if (p < 500000) return 500;
    return 1000;
  }
  function tickFloor(p) {
    const t = tickSize(p);
    return Math.floor(p / t) * t;
  }
  function fmt(n) {
    return n == null || isNaN(n) ? '—' : Math.round(n).toLocaleString('ko-KR');
  }
  function parseMoney(s) {
    const digits = String(s == null ? '' : s).replace(/\D/g, '');
    return digits ? Number(digits) : 0;
  }
  function formatMoneyInput(el) {
    const raw = el.value;
    const start = el.selectionStart == null ? raw.length : el.selectionStart;
    const digitsBefore = raw.slice(0, start).replace(/\D/g, '').length;
    const n = parseMoney(raw);
    const formatted = n ? n.toLocaleString('ko-KR') : '';
    if (el.value === formatted) return;
    el.value = formatted;
    let pos = formatted.length;
    if (digitsBefore <= 0) {
      pos = 0;
    } else {
      let seen = 0;
      for (let i = 0; i < formatted.length; i++) {
        if (/\d/.test(formatted[i])) seen++;
        if (seen === digitsBefore) {
          pos = i + 1;
          break;
        }
      }
    }
    if (document.activeElement === el) el.setSelectionRange(pos, pos);
  }
  function fmtPct(n, digits) {
    if (n == null || isNaN(n)) return '—';
    const d = digits == null ? 1 : digits;
    return (n > 0 ? '+' : '') + Number(n).toFixed(d) + '%';
  }
  function cls(n) {
    return n > 0 ? 'up' : n < 0 ? 'down' : '';
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function md(iso) {
    const s = String(iso || '');
    return s.length >= 10 ? s.slice(5, 7) + '/' + s.slice(8, 10) : s;
  }
  function hm(iso) {
    const s = String(iso || '');
    return s.length >= 16 ? s.slice(5, 10).replace('-', '/') + ' ' + s.slice(11, 16) : s;
  }
  function holdLeft(ps) {
    const end = Date.parse(ps && ps.execute_at || '');
    if (!end) return '3분';
    const sec = Math.max(0, Math.round((end - Date.now()) / 1000));
    return Math.floor(sec / 60) + '분 ' + (sec % 60) + '초';
  }

  function apiError(j, status) {
    const d = j && j.detail;
    if (typeof d === 'string' && d) return d;
    if (Array.isArray(d)) {
      const msg = d.map((x) => (x && (x.msg || x.message)) || '').filter(Boolean).join(' ');
      if (msg) return msg;
    }
    return '요청 실패 (' + status + ')';
  }

  async function api(method, url, body) {
    const opt = { method, headers: {}, cache: 'no-store' };
    if (body !== undefined) {
      opt.headers['Content-Type'] = 'application/json';
      opt.body = JSON.stringify(body);
    }
    const r = await fetch(url, opt);
    if (r.status === 401) {
      location.href = '/login?next=/ladder';
      throw new Error('로그인이 필요합니다');
    }
    let j = null;
    try { j = await r.json(); } catch (e) { j = null; }
    if (!r.ok) throw new Error(apiError(j, r.status));
    return j;
  }

  function toast(msg, kind) {
    let el = document.getElementById('ldToast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'ldToast';
      el.className = 'ld-toast';
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.className = 'ld-toast show ' + (kind || '');
    clearTimeout(el._t);
    el._t = setTimeout(() => { el.className = 'ld-toast'; }, 4500);
  }

  // ---------- 계산 (표시용) ----------
  function prof(p) { return p.profile || {}; }
  function canEdit(p) {
    return !!(p && p.status === 'RSI_WAIT' && !(p.fills || []).length && !p.pending_buy);
  }
  function maxSteps(p) { return prof(p).max_steps || 10; }
  function autoFills(p) { return (p.fills || []).filter((f) => !f.extra); }
  function smaOf(values, n) {
    const out = new Array(values.length).fill(null);
    let sum = 0;
    for (let i = 0; i < values.length; i++) {
      const v = Number(values[i]);
      if (!isFinite(v)) { sum = 0; continue; }
      sum += v;
      if (i >= n) sum -= Number(values[i - n]);
      if (i >= n - 1) out[i] = sum / n;
    }
    return out;
  }
  function ma(p) {
    const m = Object.assign({}, (p.market && p.market.ma) || {});
    if (m.ma5 == null) {
      const closes = ((p.market || {}).chart || {}).close || [];
      if (closes.length >= 5) m.ma5 = smaOf(closes, 5).pop();
    }
    return m;
  }
  function armPrice(p) { return p.avg ? tickFloor(p.avg * (1 + (prof(p).tp_arm_pct || 15) / 100)) : null; }

  function splitAmounts(budget, steps, mode, ratio) {
    const n = Math.max(1, steps || 1);
    const total = Math.max(0, Math.floor(Number(budget) || 0));
    const r = Number(ratio) || 1.2;
    const grow = mode === 'grow' && r > 1 && n > 1;
    if (!grow) {
      const per = Math.floor(total / n);
      return Array(n).fill(per);
    }
    const weights = [];
    for (let i = 0; i < n; i++) weights.push(Math.pow(r, i));
    const totalW = weights.reduce((a, b) => a + b, 0);
    const out = weights.map((w) => Math.floor(total * w / totalW));
    out[out.length - 1] += total - out.reduce((a, b) => a + b, 0);
    return out;
  }

  function stepAmounts(budget, steps, mode, ratio, firstBoost, firstPct) {
    const n = Math.max(1, steps || 1);
    const total = Math.max(0, Math.floor(Number(budget) || 0));
    if (firstBoost && n > 1 && total > 0) {
      const pct = Math.min(80, Math.max(5, Number(firstPct) || 20));
      const first = Math.floor(total * pct / 100);
      return [first].concat(splitAmounts(total - first, n - 1, mode, ratio));
    }
    return splitAmounts(total, n, mode, ratio);
  }

  function planAmounts(p) {
    const pr = prof(p);
    return stepAmounts(p.budget, maxSteps(p), pr.alloc_mode, pr.alloc_ratio, pr.first_boost, pr.first_boost_pct);
  }

  function allocText(p) {
    const pr = prof(p);
    const base = pr.alloc_mode === 'grow' ? ('증가 ' + (pr.alloc_ratio || 1.2) + '배') : '균등';
    if (pr.first_boost) return base + ' · 1차 ' + (pr.first_boost_pct || 20) + '%';
    return base;
  }

  function buildLadder(p) {
    const auto = autoFills(p);
    const steps = maxSteps(p);
    const stepPct = prof(p).step_pct || 5;
    const amounts = planAmounts(p);
    const first = auto[0] ? auto[0].price : (p.price || 0);
    const rows = [];
    let cumQty = 0, cumAmt = 0, prev = null;
    for (let n = 1; n <= steps; n++) {
      const f = auto[n - 1];
      let price;
      if (f) price = f.price;
      else if (n === 1) price = p.price || 0;
      else price = tickFloor(prev * (1 - stepPct / 100));
      const qty = f ? f.qty : (price ? Math.floor((amounts[n - 1] || 0) / price) : 0);
      cumQty += qty;
      cumAmt += qty * price;
      rows.push({
        step: n, price, qty, amount: qty * price, cumAmt,
        fromFirst: first ? (price / first - 1) * 100 : 0,
        avgAfter: cumQty ? cumAmt / cumQty : 0,
        state: f ? 'filled' : n === auto.length + 1 ? 'next' : 'pending',
        date: f ? md(f.at) : '',
      });
      prev = price;
    }
    (p.fills || []).filter((f) => f.extra).forEach((f, i) => {
      const amount = f.qty * f.price;
      cumQty += f.qty;
      cumAmt += amount;
      rows.push({ step: steps + i + 1, price: f.price, qty: f.qty, amount, cumAmt,
        fromFirst: first ? (f.price / first - 1) * 100 : 0,
        avgAfter: cumQty ? cumAmt / cumQty : 0, state: 'manual', date: md(f.at) });
    });
    return rows;
  }

  // ---------- 렌더 ----------
  function plans() { return (state.data && state.data.plans) || []; }

  function renderTop() {
    const d = state.data;
    if (!d) return;
    const toggle = document.getElementById('btnWatchToggle');
    toggle.classList.toggle('is-on', d.enabled);
    toggle.classList.toggle('is-off', !d.enabled);
    toggle.textContent = d.enabled ? '● 자동감시 ON' : '○ 자동감시 OFF';
    const badge = document.getElementById('modeBadge');
    const warn = !d.auto_trade_on ? ' · 자동매매 OFF(주문 안 됨)' : '';
    badge.textContent = (d.account_mode === 'real' ? '실전' : '모의') + ' 계좌' + warn;
    badge.classList.toggle('warn', !d.auto_trade_on || !d.enabled);
    const lj = d.last_judge || {};
    document.getElementById('lastJudge').textContent = lj.at ? '마지막 판정 ' + hm(lj.at) + ' (' + (lj.trigger || '') + ')' : '판정 기록 없음';
  }

  function renderSummary() {
    const live = plans().filter((p) => p.status !== 'DONE');
    let cost = 0, budget = 0, evalAmt = 0, buyNear = 0, sellFire = 0, entryOk = 0;
    live.forEach((p) => {
      cost += (p.hold || 0) * (p.avg || 0);
      budget += p.budget || 0;
      evalAmt += p.eval_amount || 0;
      if (p.next_trigger && p.price && p.price <= p.next_trigger) buyNear++;
      if (p.exit_preview && p.exit_preview.sells && p.exit_preview.sells.length) sellFire++;
      if (p.status === 'RSI_WAIT' && p.market && p.market.entry && p.market.entry.ok) entryOk++;
    });
    const pnl = evalAmt - cost;
    const pnlPct = cost ? (pnl / cost) * 100 : 0;
    const cards = [
      { k: '사다리', v: live.length + '개', s: ['ACTIVE', 'RSI_WAIT', 'EXITING', 'FULL'].map((st) => STATUS_LABEL[st] + ' ' + live.filter((p) => p.status === st).length).join(' · ') },
      { k: '보유 원가 / 계획', v: fmt(cost / 10000) + '만', s: '계획 ' + fmt(budget / 10000) + '만 (' + (budget ? ((cost / budget) * 100).toFixed(0) : 0) + '%)' },
      { k: '평가손익', v: '<span class="' + cls(pnl) + '">' + (pnl > 0 ? '+' : '') + fmt(pnl / 10000) + '만</span>', s: '<span class="' + cls(pnlPct) + '">' + fmtPct(pnlPct) + '</span> (보유분)' },
      { k: '1차 진입 조건 충족', v: entryOk + '개', s: '현재가 기준 · 15:05 에 확정' },
      { k: '지금 판정하면', v: '<span class="' + (buyNear ? 'ld-near' : '') + '">매수 ' + (buyNear + entryOk) + '</span> · <span class="' + (sellFire ? 'up' : '') + '">매도 ' + sellFire + '</span>', s: '추가매수는 장중 10분 감시 · 진입·매도는 15:05' },
    ];
    document.getElementById('summaryCards').innerHTML = cards
      .map((c) => '<div class="ld-stat"><div class="k">' + c.k + '</div><div class="v">' + c.v + '</div><div class="s">' + c.s + '</div></div>')
      .join('');
  }

  function dotsHtml(p) {
    const n = autoFills(p).length;
    const extra = (p.fills || []).length - n;
    const steps = maxSteps(p);
    let h = '<span class="ld-dots">';
    for (let i = 1; i <= steps; i++) {
      const c = i <= n ? 'on' : (i === n + 1 && p.status === 'ACTIVE') ? 'next' : '';
      h += '<i class="' + c + '"></i>';
    }
    for (let i = 0; i < extra; i++) h += '<i class="extra" title="수동 추가"></i>';
    return h + '</span><span class="ld-dots-label">' + n + '/' + steps + (extra ? '+' + extra : '') + '</span>';
  }

  function stageChips(p) {
    if (!(p.fills || []).length) return '<span class="hint">—</span>';
    const flags = p.sell_flags || {};
    const firing = new Set(((p.exit_preview || {}).sells || []).map((s) => s.rule));
    const chip = (rule, label) => '<span class="ld-chip ' + (flags[rule] ? 'done' : firing.has(rule) ? 'fire' : '') + '">' + label + '</span>';
    const armPct = prof(p).tp_arm_pct || 15;
    const peak = p.peak_pnl_pct || 0;
    if (!p.armed) {
      const armChip = '<span class="ld-chip ' + (peak >= armPct * 0.8 ? 'ready' : '') + '" title="최고 수익률 / 무장 기준">무장 ' + fmtPct(peak, 0) + '/' + armPct + '%</span>';
      return '<span class="ld-stage-chips">' + armChip + '</span>';
    }
    return '<span class="ld-stage-chips">' + chip('S1', '10선50%') + chip('S2', '15선25%') + chip('S3', '20선잔량') + '</span>';
  }

  function pendingTag(p) {
    if (p.pending_buy) return ' <span class="ld-chip ready" title="매수 신호 체결 대기">매수대기</span>';
    if (p.pending_sell && p.pending_sell.status === 'HOLD') return ' <span class="ld-chip ready" title="3분 안에 취소하지 않으면 익절">익절 확인</span>';
    if (p.pending_sell && ['QUEUED', 'SENT'].includes(p.pending_sell.status)) return ' <span class="ld-chip fire" title="매도 주문 대기">매도대기</span>';
    return '';
  }

  function renderTable() {
    const rows = plans().filter((p) => state.filter === 'all' ? p.status !== 'DONE' : p.status === state.filter);
    const tbody = document.querySelector('#planTable tbody');
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="9" class="hint">' + (plans().length ? '해당 상태의 사다리가 없습니다' : '등록된 사다리가 없습니다 — [+ 새 사다리]로 시작하세요') + '</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map((p) => {
      const e = (p.market || {}).entry;
      let nextCell = '—', distCell = '—';
      if (p.status === 'RSI_WAIT') {
        if (prof(p).entry_mode === 'manual') nextCell = '수동 대기';
        else if (e) nextCell = 'RSI ' + (e.r1 || e.r2 ? '✓' : '✗') + ' · GC ' + (e.g ? '✓' : e.gc && e.gc.state === 'near' ? '임박' : '✗');
        distCell = e && e.ok && prof(p).entry_mode !== 'manual' ? '<span class="ld-near">충족</span>' : '<span class="hint">대기</span>';
      } else if (p.status === 'ACTIVE' && p.next_trigger) {
        const dist = p.price ? (p.next_trigger / p.price - 1) * 100 : null;
        nextCell = fmt(p.next_trigger) + ' <span class="sub">' + (p.auto_steps + 1) + '차</span>';
        distCell = '<span class="' + (dist != null && Math.abs(dist) <= NEAR_PCT ? 'ld-near' : '') + '">' + fmtPct(dist) + '</span>';
      } else if (p.status === 'FULL') {
        nextCell = '<span class="down">수동 판단</span>';
      } else if (p.status === 'EXITING') {
        nextCell = '<span class="hint">매수 중지</span>';
      } else if (p.status === 'PAUSED') {
        nextCell = '<span class="hint">정지</span>';
      }
      const editable = canEdit(p);
      const nameHtml = editable
        ? '<b class="ld-editable" data-edit="1" title="클릭해서 설정 수정">' + esc(p.name) + '</b>'
        : '<b>' + esc(p.name) + '</b>';
      return '<tr data-id="' + p.id + '" class="' + (p.id === state.selectedId ? 'selected' : '') + '">' +
        '<td>' + nameHtml + '<span class="code">' + p.code + '</span></td>' +
        '<td><span class="ld-badge ' + p.status + '">' + STATUS_LABEL[p.status] + '</span>' + pendingTag(p) +
        (editable ? ' <button type="button" class="btn sm" data-edit="1">수정</button>' : '') + '</td>' +
        '<td>' + dotsHtml(p) + '</td>' +
        '<td class="r">' + fmt(p.price) + '</td>' +
        '<td class="r">' + (p.avg ? fmt(p.avg) : '—') + '</td>' +
        '<td class="r">' + (p.hold ? '<span class="' + cls(p.pnl_pct) + '">' + fmtPct(p.pnl_pct) + '</span><span class="sub">(' + fmtPct(p.peak_pnl_pct || 0, 0) + ')</span>' : '—') + '</td>' +
        '<td class="r">' + nextCell + '</td>' +
        '<td class="r">' + distCell + '</td>' +
        '<td>' + stageChips(p) + '</td>' +
        '</tr>';
    }).join('');
  }

  function entryPanel(p) {
    const pr = prof(p);
    const e = (p.market || {}).entry;
    const line = (ok, label, val, near) => '<div class="ld-cond ' + (ok ? 'ok' : near ? 'near' : 'no') + '"><span class="mark">' + (ok ? '✓' : near ? '…' : '✗') + '</span>' + label + '<span class="val">' + val + '</span></div>';
    let h = '<div class="ld-panel"><div class="ld-panel-head">1차 진입 조건 <span class="hint">' + (pr.entry_mode === 'manual' ? '수동 진입' : '자동') + '</span></div>';
    if (!e) {
      h += '<div class="hint">' + esc((p.market || {}).error || '지표 계산 불가 (일봉 61개 미만)') + '</div>';
      return h + '</div>';
    }
    const gc = e.gc || {};
    const gcVal = gc.state === 'today' ? '오늘' : gc.state === 'recent' ? gc.days_ago + '일 전' : gc.state === 'above' ? '이미 위 (' + fmtPct(gc.gap_pct) + ')' : gc.state === 'near' ? '이격 ' + fmtPct(gc.gap_pct) : gc.state === 'na' ? '데이터 부족' : '없음 (' + fmtPct(gc.gap_pct) + ')';
    h += line(e.r1, '주봉 RSI ≤ ' + pr.rsi_weekly_th + ' (' + pr.weekly_lookback_weeks + '주)', e.weekly_rsi_min != null ? '최저 ' + e.weekly_rsi_min.toFixed(1) + ' · 현재 ' + (e.weekly_rsi != null ? e.weekly_rsi.toFixed(1) : '—') : '—');
    h += '<div class="ld-cond-op">또는</div>';
    h += line(e.r2, '일봉 RSI ≤ ' + pr.rsi_daily_th + ' 저점 ' + pr.daily_low_count + '회 (' + pr.daily_lookback + '일)', e.low_count + '/' + pr.daily_low_count + '회 · 현재 ' + (e.daily_rsi != null ? e.daily_rsi.toFixed(1) : '—'), e.low_count === pr.daily_low_count - 1);
    h += '<div class="ld-cond-op">그리고</div>';
    h += line(e.g, '20일선 ↗ 60일선 GC (' + pr.gc_fresh_days + '일 이내)', gcVal, gc.state === 'near');
    if (p.status === 'RSI_WAIT') {
      if (pr.entry_mode === 'manual') h += '<div class="ld-verdict manual">수동 진입 — [1차 수동 매수] 버튼으로 매수</div>';
      else if (e.ok) h += '<div class="ld-verdict ok">현재가 기준 충족 → 15:05 판정에서 1차 매수</div>';
      else h += '<div class="ld-verdict wait">대기 — ' + (!(e.r1 || e.r2) ? 'RSI 저점 부족' : 'GC 대기') + ' · 수동 매수 가능</div>';
    } else {
      const f = (p.fills || [])[0];
      if (f) h += '<div class="ld-verdict ok">1차 ' + md(f.at) + ' ' + fmt(f.price) + ' × ' + fmt(f.qty) + '주</div>';
    }
    return h + '</div>';
  }

  function exitPanel(p) {
    const pr = prof(p);
    const m = ma(p);
    const flags = p.sell_flags || {};
    const firing = ((p.exit_preview || {}).sells || []);
    const fire = (r) => firing.some((a) => a.rule === r);
    const line = (done, f, label, val) => '<div class="ld-cond ' + (done ? 'ok' : f ? 'near' : 'no') + '"><span class="mark">' + (done ? '✓' : f ? '!' : '·') + '</span>' + label + '<span class="val">' + val + '</span></div>';
    const gap = (v) => v ? fmt(v) + ' (' + fmtPct((p.price / v - 1) * 100) + ')' : '—';
    const armPct = pr.tp_arm_pct || 15;
    let h = '<div class="ld-panel"><div class="ld-panel-head">매도 단계 <span class="hint">15:05 현재가 기준</span></div>';
    const ap = armPrice(p);
    h += '<div class="ld-cond ' + (p.armed ? 'ok' : 'no') + '"><span class="mark">' + (p.armed ? '✓' : '·') + '</span>익절 무장 +' + armPct + '%<span class="val">최고 ' + fmtPct(p.peak_pnl_pct || 0) + (ap && !p.armed ? ' · 무장가 ' + fmt(ap) : '') + '</span></div>';
    if (!p.armed) {
      h += '<div class="ld-cond-op">무장 전 매도 없음 · 무장 후: 10일선 50% → 15일선 25% → 20일선 나머지</div>';
    } else {
      h += line(flags.S1, fire('S1'), 'S1 10일선 이탈 → 50%', flags.S1 ? '완료' : gap(m.ma10));
      h += line(flags.S2, fire('S2'), 'S2 15일선 이탈 → 25%', flags.S2 ? '완료' : gap(m.ma15));
      h += line(flags.S3, fire('S3'), 'S3 20일선 이탈 → 나머지', flags.S3 ? '완료' : gap(m.ma20));
      if (p.base_qty) h += '<div class="ld-cond-op">기준수량 ' + fmt(p.base_qty) + '주 (무장 후 첫 매도 시점)</div>';
    }
    if (firing.length) h += '<div class="ld-verdict wait">지금 판정하면: ' + firing.map((a) => a.rule + ' ' + fmt(a.qty) + '주').join(', ') + '</div>';
    const ps = p.pending_sell;
    if (ps && ps.status === 'HOLD') {
      h += '<div class="ld-verdict wait">익절 확인 중: ' + (ps.rules || []).map((r) => RULE_LABEL[r] || r).join(' + ') + ' ' + fmt(ps.qty) + '주 · ' + holdLeft(ps) + ' 후 자동 익절 (텔레그램에서도 취소·바로 익절)</div>';
    } else if (ps && ['QUEUED', 'SENT'].includes(ps.status)) {
      h += '<div class="ld-verdict manual">매도 ' + (ps.status === 'SENT' ? '주문 접수' : '대기') + ': ' + (ps.rules || []).map((r) => RULE_LABEL[r] || r).join(' + ') + ' ' + fmt(ps.qty) + '주</div>';
    }
    if (p.status === 'FULL') h += '<div class="ld-verdict manual">' + maxSteps(p) + '차 소진 — 추가 매수·손절은 수동 판단</div>';
    return h + '</div>';
  }

  function chartSvg(p, ladder) {
    const ch = (p.market || {}).chart;
    if (!ch || !ch.close || ch.close.length < 2) return '<div class="hint">차트 데이터 없음</div>';
    const len = Math.min(CHART_LEN, ch.close.length);
    const off = ch.close.length - len;
    const prices = ch.close.slice(off);
    const dates = ch.dates.slice(off);
    const series = {};
    CHART_MAS.forEach((n) => {
      const raw = ch['ma' + n];
      series[n] = (raw && raw.length) ? raw.slice(off) : smaOf(ch.close, n).slice(off);
    });
    const shown = ladder.filter((r) => r.state !== 'manual' && (r.state !== 'pending' || r.step <= autoFills(p).length + 3) && r.price > 0);
    const ap = p.hold && !p.armed ? armPrice(p) : null;
    const all = prices.concat(shown.map((r) => r.price));
    if (ap) all.push(ap);
    const lo = Math.min.apply(null, all) * 0.98;
    const hi = Math.max.apply(null, all) * 1.02;
    const W = 640, H = 250, padL = 8, padR = 70, padT = 10, padB = 16;
    const x = (i) => padL + (i / (len - 1)) * (W - padL - padR);
    const y = (v) => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB);
    const hline = (v, color, dash, label, left) => {
      let t = '<line x1="' + padL + '" x2="' + (W - padR) + '" y1="' + y(v) + '" y2="' + y(v) + '" stroke="' + color + '" stroke-width="1"' + (dash ? ' stroke-dasharray="' + dash + '"' : '') + ' opacity="0.85"/>';
      t += left
        ? '<text x="' + (padL + 4) + '" y="' + (y(v) - 3) + '" style="fill:' + color + '">' + label + '</text>'
        : '<text x="' + (W - padR + 4) + '" y="' + (y(v) + 3) + '" style="fill:' + color + '">' + label + '</text>';
      return t;
    };
    let svg = '<svg class="ld-chart" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none">';
    shown.forEach((r) => {
      const color = r.state === 'filled' ? 'var(--accent-bright)' : r.state === 'next' ? 'var(--amber)' : 'var(--border-light)';
      svg += hline(r.price, color, r.state === 'filled' ? '' : '4 4', r.step + '차 ' + fmt(r.price));
    });
    if (p.avg && p.hold) svg += hline(p.avg, 'var(--cyan)', '', '평단 ' + fmt(p.avg), true);
    if (ap) svg += hline(ap, 'var(--up)', '6 3', '무장가 ' + fmt(ap), true);
    [60, 20, 10, 5].forEach((n) => {
      const s = series[n];
      const d = s.map((v, i) => v == null ? '' : (i && s[i - 1] != null ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1)).join(' ');
      const thick = n === 20 || n === 60;
      svg += '<path d="' + d + '" fill="none" stroke="' + MA_COLORS[n] + '" stroke-width="' + MA_WIDTH[n] + '" vector-effect="non-scaling-stroke" opacity="' + (thick ? '1' : '0.9') + '"/>';
    });
    svg += '<path d="' + prices.map((v, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1)).join(' ') + '" fill="none" stroke="var(--text)" stroke-width="1.6"/>';
    const idxOf = (iso) => dates.indexOf(String(iso || '').slice(0, 10));
    (p.fills || []).forEach((f) => {
      const i = idxOf(f.at);
      if (i < 0) return;
      svg += '<path d="M' + x(i) + ' ' + (y(f.price) + 4) + ' l-5 8 h10 z" fill="var(--accent-bright)"/>';
    });
    (p.sells || []).forEach((s) => {
      const i = idxOf(s.at);
      if (i < 0 || !s.price) return;
      svg += '<path d="M' + x(i) + ' ' + (y(s.price) - 4) + ' l-5 -8 h10 z" fill="var(--up)"/>';
      svg += '<text x="' + (x(i) - 6) + '" y="' + (y(s.price) - 14) + '" style="fill:var(--up)">' + esc((s.rules || []).join('+')) + '</text>';
    });
    svg += '<circle cx="' + x(len - 1) + '" cy="' + y(prices[len - 1]) + '" r="3.5" fill="var(--text)"/>';
    return svg + '</svg>';
  }

  function renderDetail() {
    const p = plans().find((x) => x.id === state.selectedId);
    const title = document.getElementById('detailTitle');
    const body = document.getElementById('detailBody');
    const actions = document.getElementById('detailActions');
    if (!p) {
      title.textContent = '종목을 선택하세요';
      body.innerHTML = '<div class="hint">왼쪽 목록에서 종목을 클릭하면 상세가 표시됩니다.</div>';
      actions.innerHTML = '';
      return;
    }
    const ladder = buildLadder(p);
    title.innerHTML = esc(p.name) + ' <span class="hint">' + p.code + '</span> <span class="ld-badge ' + p.status + '">' + STATUS_LABEL[p.status] + '</span>' + pendingTag(p);
    let btns = '';
    if (canEdit(p)) btns += '<button type="button" class="btn sm" data-act="edit">수정</button>';
    if (p.status === 'RSI_WAIT') btns += '<button type="button" class="btn sm primary" data-act="buy">1차 수동 매수</button>';
    if (['ACTIVE', 'FULL'].includes(p.status)) btns += '<button type="button" class="btn sm" data-act="buy">수동 추가 매수</button>';
    if (p.status === 'PAUSED') btns += '<button type="button" class="btn sm" data-act="resume">재개</button>';
    else if (p.status !== 'DONE') btns += '<button type="button" class="btn sm" data-act="pause">일시정지</button>';
    if (p.pending_sell && p.pending_sell.status === 'HOLD') {
      btns += '<button type="button" class="btn sm primary" data-act="sellNow">바로 익절</button>';
      btns += '<button type="button" class="btn sm" data-act="sellCancel">이번 익절 취소</button>';
    } else {
      if (p.hold > 1) btns += '<button type="button" class="btn sm" data-act="sellHalf">50% 수동매도</button>';
      if (p.hold > 0) btns += '<button type="button" class="btn sm danger" data-act="sellAll">전량매도</button>';
    }
    if (!p.hold && p.status !== 'DONE' && !p.pending_buy) btns += '<button type="button" class="btn sm" data-act="finish">종료</button>';
    if (!p.hold) btns += '<button type="button" class="btn sm danger" data-act="delete">삭제</button>';
    actions.innerHTML = btns;

    const m = ma(p);
    const e = (p.market || {}).entry || {};
    const realized = (p.sells || []).reduce((a, s) => a + (s.pnl || 0), 0);
    const unreal = p.hold && p.avg ? p.hold * (p.price - p.avg) : 0;
    const meta = [
      { k: '현재가 · RSI(일)', v: fmt(p.price) + ' <span class="hint">· ' + (e.daily_rsi != null ? e.daily_rsi.toFixed(1) : '—') + '</span>' },
      { k: '평단 · 보유', v: p.hold ? fmt(p.avg) + ' · ' + fmt(p.hold) + '주' : '—' },
      { k: '평가손익', v: p.hold ? '<span class="' + cls(unreal) + '">' + (unreal > 0 ? '+' : '') + fmt(unreal) + ' (' + fmtPct(p.pnl_pct) + ')</span>' : '—' },
      { k: '실현손익', v: (p.sells || []).length ? '<span class="' + cls(realized) + '">' + (realized > 0 ? '+' : '') + fmt(realized) + '</span>' : '—' },
      { k: '5 / 10일선', v: fmt(m.ma5) + ' / ' + fmt(m.ma10) },
      { k: '20 / 60일선', v: fmt(m.ma20) + ' / ' + fmt(m.ma60) },
      { k: '다음 매수가', v: p.next_trigger ? fmt(p.next_trigger) + ' <span class="hint">' + (p.auto_steps + 1) + '차 · ' + fmt(p.next_qty) + '주</span>' : '—' },
      { k: '투입 / 예산', v: fmt(p.invested / 10000) + ' / ' + fmt(p.budget / 10000) + '만' },
      { k: '금액 분할', v: allocText(p) },
    ];
    let html = '<div class="ld-detail-meta">' + meta.map((x) => '<div><div class="k">' + x.k + '</div><div class="v">' + x.v + '</div></div>').join('') + '</div>';
    if (p.status === 'DONE') {
      html += '<div class="ld-verdict manual">종료된 사이클' + (p.closed_at ? ' · ' + hm(p.closed_at) : '') + '</div>';
    } else {
      html += '<div class="ld-panels">' + entryPanel(p) + exitPanel(p) + '</div>';
      html += chartSvg(p, ladder);
      html += '<div class="ld-chart-legend"><span style="color:var(--text)">종가</span>' +
        '<span style="color:' + MA_COLORS[5] + '">5일선</span><span style="color:' + MA_COLORS[10] + '">10일선</span>' +
        '<span class="ld-ma-thick" style="color:' + MA_COLORS[20] + '">20일선</span><span class="ld-ma-thick" style="color:' + MA_COLORS[60] + '">60일선</span>' +
        '<span style="color:var(--cyan)">평단</span><span style="color:var(--amber)">다음 매수가</span> ▲매수 ▼매도 · 최근 ' + CHART_LEN + '거래일</div>';
    }

    html += '<table class="ld-table ld-steps"><thead><tr><th>차수</th><th>상태</th><th class="r">매수가</th><th class="r">1차 대비</th><th class="r">수량</th><th class="r">금액</th><th class="r">누적 금액</th><th class="r">누적 평단</th><th>체결일</th></tr></thead><tbody>';
    html += ladder.map((r) => {
      let label;
      if (r.state === 'filled') label = '체결';
      else if (r.state === 'manual') label = '수동 추가';
      else if (r.state === 'next') label = p.status === 'ACTIVE' ? '장중 감시' : p.status === 'RSI_WAIT' ? '진입 대기' : '정지';
      else label = '대기';
      return '<tr class="' + r.state + '"><td>' + r.step + '차</td>' +
        '<td><span class="ld-step-state ' + r.state + '">' + label + '</span></td>' +
        '<td class="r">' + fmt(r.price) + '</td>' +
        '<td class="r">' + (r.step === 1 ? '—' : fmtPct(r.fromFirst)) + '</td>' +
        '<td class="r">' + fmt(r.qty) + '</td>' +
        '<td class="r">' + fmt(r.amount) + '</td>' +
        '<td class="r">' + fmt(r.cumAmt) + '</td>' +
        '<td class="r">' + (r.avgAfter ? fmt(r.avgAfter) : '—') + '</td>' +
        '<td>' + r.date + '</td></tr>';
    }).join('');
    html += '</tbody></table>';
    html += '<div class="hint">누적 금액은 그 차수까지 투입한 합계입니다. ' +
      (autoFills(p).length ? '체결 전 차수는 예상 금액입니다.' : '1차 가격은 현재가 기준 예상치이고, 실제 체결가로 이후 차수가 다시 계산됩니다.') +
      '</div>';

    if ((p.sells || []).length) {
      html += '<div class="ld-sub-title">매도 내역</div><table class="ld-table ld-steps"><thead><tr><th>규칙</th><th class="r">가격</th><th class="r">수량</th><th class="r">실현손익</th><th>일시</th></tr></thead><tbody>';
      html += p.sells.map((s) => '<tr class="sell"><td>' + esc((s.rules || []).map((r) => RULE_LABEL[r] || r).join(' + ')) + '</td><td class="r">' + fmt(s.price) + '</td><td class="r">' + fmt(s.qty) + '</td><td class="r ' + cls(s.pnl) + '">' + (s.pnl != null ? fmt(s.pnl) : '—') + '</td><td>' + hm(s.at) + '</td></tr>').join('');
      html += '</tbody></table>';
    }
    if (p.memo) html += '<div class="hint">메모: ' + esc(p.memo) + '</div>';
    body.innerHTML = html;
  }

  const EVENT_KIND = {
    FILL: 'buy', FILL_PX: 'buy', BUY_SIGNAL: 'buy', SELL_FILL: 'sell', SELL_HOLD: 'sell', SELL_QUEUED: 'sell', SELL_CANCEL: 'warn', BUY_FAIL: 'warn', SELL_FAIL: 'warn',
    JUDGE: 'judge', ARM: 'judge', DONE: 'sell', SYSTEM: 'info',
  };

  function renderLog() {
    const el = document.getElementById('eventLog');
    const evs = (state.data && state.data.events) || [];
    el.innerHTML = evs.map((e) => '<div class="' + (EVENT_KIND[e.kind] || 'info') + '"><span class="t">' + hm(e.ts) + '</span>' +
      (e.name ? '<b>' + esc(e.name) + '</b> ' : '') + esc(e.msg) + '</div>').join('') || '<div class="hint">이벤트 없음</div>';
  }

  function renderAll() {
    renderTop();
    renderSummary();
    renderTable();
    renderDetail();
    renderLog();
  }

  // ---------- 데이터 ----------
  let loadGen = 0;
  function scheduleReload(ms) {
    clearTimeout(state.reloadTimer);
    state.reloadTimer = setTimeout(() => {
      if (!document.hidden && !state.busy) load(false);
    }, ms);
  }

  async function load(force) {
    const gen = ++loadGen;
    try {
      state.data = await api('GET', '/api/ladder/state' + (force ? '?force=true' : ''));
      if (gen !== loadGen) return;
      const ps = plans();
      if (!ps.some((p) => p.id === state.selectedId)) {
        const first = ps.find((p) => p.status !== 'DONE') || ps[0];
        state.selectedId = first ? first.id : null;
      }
      renderAll();
      if (state.data && state.data.warming && state.warmTries < 15) {
        state.warmTries += 1;
        scheduleReload(2000);
      } else if (force) {
        state.warmTries = 0;
        scheduleReload(4000);
      } else {
        state.warmTries = 0;
      }
    } catch (e) {
      if (gen === loadGen) toast(e.message, 'err');
    }
  }

  async function run(fn, okMsg) {
    if (state.busy) return;
    state.busy = true;
    try {
      const r = await fn();
      if (okMsg) toast(typeof okMsg === 'function' ? okMsg(r) : okMsg, 'ok');
      await load(true);
      return r;
    } catch (e) {
      toast(e.message, 'err');
    } finally {
      state.busy = false;
    }
  }

  function onAction(act) {
    const p = plans().find((x) => x.id === state.selectedId);
    if (!p) return;
    const base = '/api/ladder/plans/' + p.id;
    if (act === 'edit') return openEdit(p);
    if (act === 'pause') return run(() => api('POST', base + '/pause'), '일시정지 — 자동 매수·매도 판정 중지');
    if (act === 'resume') return run(() => api('POST', base + '/resume'), '재개');
    if (act === 'finish') {
      if (!confirm(p.name + ' 사이클을 종료할까요?')) return;
      return run(() => api('POST', base + '/finish'), '종료');
    }
    if (act === 'delete') {
      if (!confirm(p.name + ' 계획을 삭제할까요? (기록도 삭제)')) return;
      return run(() => api('DELETE', base), '삭제');
    }
    if (act === 'buy') {
      const done = autoFills(p).length;
      const amounts = planAmounts(p);
      const slice = amounts[Math.min(done, amounts.length - 1)] || 0;
      const qty = p.price ? Math.floor(slice / p.price) : 0;
      if (!confirm(p.name + ' 수동 매수: 현재가 약 ' + fmt(p.price) + ' × ' + qty + '주 (시장가 주문)')) return;
      return run(() => api('POST', base + '/buy'), (r) => '매수 신호 ' + r.qty + '주 @ ' + fmt(r.price) + ' — 체결 확인 중');
    }
    if (act === 'sellNow' || act === 'sellCancel') {
      const cancel = act === 'sellCancel';
      if (cancel && !confirm(p.name + ' 이번 익절을 취소할까요? 오늘은 다시 안 나가고, 다음 거래일에도 조건이 맞으면 다시 알립니다.')) return;
      if (!cancel && !confirm(p.name + ' 지금 바로 익절할까요?')) return;
      return run(() => api('POST', base + '/sell-decide', { action: cancel ? 'cancel' : 'go' }), cancel ? '이번 익절 취소' : '바로 익절');
    }
    if (act === 'sellHalf' || act === 'sellAll') {
      const all = act === 'sellAll';
      const q = all ? p.hold : Math.max(1, Math.floor(p.hold / 2));
      if (!confirm(p.name + ' ' + fmt(q) + '주 ' + (all ? '전량' : '50%') + ' 매도할까요?')) return;
      return run(() => api('POST', base + '/sell', { mode: all ? 'all' : 'half' }), (r) => '매도 대기 ' + r.qty + '주 — 손절 감시 루프에서 주문');
    }
  }

  async function runPreview() {
    const r = await run(() => api('POST', '/api/ladder/judge?dry_run=true'));
    if (!r) return;
    const lines = (r.results || []).map((x) => {
      if (x.error) return (x.name || x.code) + ': ' + x.error;
      const acts = [];
      if (x.sells && x.sells.length) acts.push('매도 ' + x.sells.map((s) => s.rule + ' ' + s.qty + '주').join(', '));
      else if (x.buy) acts.push('매수 ' + x.buy.step + '차 ' + x.buy.qty + '주');
      if (x.notes && x.notes.length) acts.push(x.notes.join(', '));
      return (x.name || x.code) + ' @ ' + fmt(x.price) + ': ' + (acts.join(' · ') || '행동 없음');
    });
    alert('지금 현재가로 판정하면 (미리보기 · 주문 없음)\n\n' + (lines.join('\n') || '진행 중인 계획 없음'));
  }

  // ---------- 새 계획 폼 ----------
  function readForm() {
    const f = document.getElementById('newPlanForm');
    const v = (n) => f.elements[n].value;
    return {
      code: v('code').trim(),
      name: v('name').trim(),
      budget: parseMoney(v('budget')),
      memo: v('memo').trim(),
      profile: {
        entry_mode: v('entry'),
        rsi_weekly_th: Number(v('rsiW')) || 30,
        rsi_daily_th: Number(v('rsiD')) || 30,
        daily_lookback: parseInt(v('lookback'), 10) || 120,
        daily_low_count: parseInt(v('lowCount'), 10) || 3,
        step_pct: Number(v('step')) || 5,
        max_steps: Math.min(20, Math.max(1, parseInt(v('maxSteps'), 10) || 10)),
        alloc_mode: v('alloc') === 'grow' ? 'grow' : 'equal',
        alloc_ratio: Math.min(3, Math.max(1.05, Math.round((Number(v('allocRatio')) || 1.2) * 100) / 100)),
        first_boost: !!f.elements.firstBoost.checked,
        first_boost_pct: Math.min(80, Math.max(5, Math.round((Number(v('firstBoostPct')) || 20) * 10) / 10)),
        tp_arm_pct: Number(v('arm')) || 15,
      },
    };
  }

  function allocSummary(o) {
    const pr = o.profile;
    const amounts = stepAmounts(o.budget, pr.max_steps, pr.alloc_mode, pr.alloc_ratio, pr.first_boost, pr.first_boost_pct);
    const tail = pr.max_steps + '차 ' + fmt(amounts[amounts.length - 1]) + '원';
    if (pr.first_boost) {
      const mode = pr.alloc_mode === 'grow' ? ('증가 ' + pr.alloc_ratio + '배') : '균등';
      return mode + ' · 1차 ' + pr.first_boost_pct + '% ' + fmt(amounts[0]) + '원 → ' + tail;
    }
    if (pr.alloc_mode === 'grow') {
      return '증가 ' + pr.alloc_ratio + '배 · 1차 ' + fmt(amounts[0]) + '원 → ' + tail;
    }
    return '균등 · 차수당 ' + fmt(amounts[0]) + '원';
  }

  function syncAllocFields() {
    const f = document.getElementById('newPlanForm');
    if (!f || !f.elements.alloc) return;
    const grow = f.elements.alloc.value === 'grow';
    const row = f.querySelector('.ld-alloc-grow');
    if (row) row.hidden = !grow;
    const boostRow = f.querySelector('.ld-first-boost');
    const boost = !!(f.elements.firstBoost && f.elements.firstBoost.checked);
    if (boostRow) boostRow.hidden = !boost;
    const hint = document.getElementById('allocHint');
    if (!hint) return;
    const ratio = Number(f.elements.allocRatio.value) || 1.2;
    const steps = Math.min(20, Math.max(1, parseInt(f.elements.maxSteps.value, 10) || 10));
    const pct = Math.min(80, Math.max(5, Number(f.elements.firstBoostPct.value) || 20));
    if (boost) {
      const equalPct = 100 / steps;
      const rest = grow
        ? ('나머지는 차수가 오를수록 이전의 ' + ratio + '배입니다.')
        : '나머지는 차수마다 같은 금액입니다.';
      const warn = steps > 1 && pct <= equalPct
        ? (' 균등(' + equalPct.toFixed(1) + '%)보다 크지 않아 1차가 커지지 않습니다.')
        : '';
      hint.textContent = '1차는 총 투자금의 ' + pct + '%입니다. ' + rest + warn;
      return;
    }
    hint.textContent = grow
      ? ('1차가 가장 작고, 다음 차수는 이전 차수의 ' + ratio + '배입니다. 합계는 총 투자금입니다. 차수를 늘리면 1차 금액은 더 작아집니다.')
      : '차수마다 같은 금액입니다. 차수당 = 총 투자금 ÷ 최대 차수.';
  }

  function renderPreview() {
    syncAllocFields();
    const o = readForm();
    const pr = o.profile;
    const base = state.quote && state.quote.price ? state.quote.price : 0;
    const head = document.getElementById('previewSummary');
    const tbody = document.querySelector('#previewTable tbody');
    if (!base) {
      head.innerHTML = '종목코드 6자리를 입력하면 현재가로 미리보기를 계산합니다.';
      tbody.innerHTML = '';
      return;
    }
    const rows = buildLadder({ budget: o.budget, price: base, fills: [], profile: pr, status: 'RSI_WAIT' });
    const total = rows.reduce((a, r) => a + r.amount, 0);
    const last = rows[rows.length - 1];
    const q = state.quote;
    const e = q.entry;
    head.innerHTML =
      '<b>' + esc(q.name || q.code) + '</b> 현재가 ' + fmt(base) +
      (e ? ' · 진입조건 ' + (e.ok ? '<span class="ld-near">충족</span>' : '미충족') + ' (주봉 RSI 최저 ' + (e.weekly_rsi_min != null ? e.weekly_rsi_min.toFixed(1) : '—') +
        ', 일봉 저점 ' + e.low_count + '회, GC ' + ((e.gc || {}).state || '—') + ')' : '') + '<br>' +
      allocSummary(o) + ' · ' + pr.max_steps + '차 매수가 <b>' + fmt(last.price) + '</b> (1차 대비 ' + fmtPct(last.fromFirst) + ')<br>' +
      '전부 체결 시 투입 ' + fmt(total) + '원, 평단 ' + fmt(last.avgAfter) + ' → +' + pr.tp_arm_pct + '% 무장가 ' + fmt(last.avgAfter * (1 + pr.tp_arm_pct / 100)) + '<br>' +
      '1차 진입: ' + (pr.entry_mode === 'auto'
        ? '(주봉 RSI ≤ ' + pr.rsi_weekly_th + ' 또는 일봉 ' + pr.daily_lookback + '일 RSI ≤ ' + pr.rsi_daily_th + ' 저점 ' + pr.daily_low_count + '회) + 20/60 GC → 15:05 매수'
        : (state.editingId ? '수동 진입 — 저장 후 [1차 수동 매수]로 매수' : '등록 즉시 시장가 1차 매수 (정규장)'));
    tbody.innerHTML = rows.map((r) =>
      '<tr><td>' + r.step + '차</td><td class="r">' + fmt(r.price) + '</td><td class="r">' + (r.step === 1 ? '—' : fmtPct(r.fromFirst)) +
      '</td><td class="r">' + fmt(r.qty) + '</td><td class="r">' + fmt(r.amount) + '</td><td class="r">' + fmt(r.cumAmt) + '</td><td class="r">' + fmt(r.avgAfter) +
      '</td><td class="r">' + fmt(r.avgAfter * (1 + pr.tp_arm_pct / 100)) + '</td></tr>'
    ).join('');
  }

  let quoteTimer = null;
  function onCodeInput() {
    const f = document.getElementById('newPlanForm');
    const code = f.elements.code.value.trim();
    clearTimeout(quoteTimer);
    if (!/^\d{6}$/.test(code)) {
      state.quote = null;
      renderPreview();
      return;
    }
    if (state.quote && state.quote.code === code) return;
    document.getElementById('previewSummary').textContent = '시세 조회 중…';
    quoteTimer = setTimeout(async () => {
      try {
        state.quote = await api('GET', '/api/ladder/quote/' + code);
        if (state.quote.name && !f.elements.name.value.trim()) f.elements.name.value = state.quote.name;
        if (state.quote.error) toast(state.quote.error, 'err');
      } catch (e) {
        state.quote = null;
        toast(e.message, 'err');
      }
      renderPreview();
    }, 400);
  }

  const suggest = { items: [], active: -1, timer: null, seq: 0 };

  function renderSuggest() {
    const ul = document.getElementById('stockSuggest');
    if (!suggest.items.length) {
      ul.innerHTML = '<li class="empty">검색 결과 없음</li>';
    } else {
      ul.innerHTML = suggest.items.map((s, i) =>
        '<li role="option" data-i="' + i + '"' + (i === suggest.active ? ' class="active" aria-selected="true"' : '') + '>' +
        '<b>' + esc(s.name) + '</b><span class="code">' + esc(s.code) + '</span><span class="mkt">' + esc(s.market) + '</span></li>'
      ).join('');
      const act = ul.querySelector('li.active');
      if (act) act.scrollIntoView({ block: 'nearest' });
    }
    ul.hidden = false;
  }

  function hideSuggest() {
    document.getElementById('stockSuggest').hidden = true;
    suggest.active = -1;
  }

  function onSearchInput() {
    const q = document.getElementById('newPlanForm').elements.search.value.trim();
    clearTimeout(suggest.timer);
    if (!q) {
      suggest.items = [];
      hideSuggest();
      return;
    }
    const seq = ++suggest.seq;
    suggest.timer = setTimeout(async () => {
      try {
        const r = await api('GET', '/api/ladder/search?q=' + encodeURIComponent(q));
        if (seq !== suggest.seq) return;
        suggest.items = r.items || [];
        suggest.active = suggest.items.length ? 0 : -1;
        renderSuggest();
      } catch (e) {
        if (seq === suggest.seq) toast(e.message, 'err');
      }
    }, 150);
  }

  function pickSuggest(i) {
    const s = suggest.items[i];
    if (!s) return;
    const f = document.getElementById('newPlanForm');
    f.elements.search.value = s.name + ' (' + s.code + ')';
    f.elements.code.value = s.code;
    f.elements.name.value = s.name;
    hideSuggest();
    onCodeInput();
    renderPreview();
  }

  function onSearchKey(e) {
    const ul = document.getElementById('stockSuggest');
    if (e.key === 'Escape') {
      if (!ul.hidden) { e.preventDefault(); hideSuggest(); }
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      if (!ul.hidden && suggest.active >= 0) pickSuggest(suggest.active);
      return;
    }
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && suggest.items.length) {
      e.preventDefault();
      const n = suggest.items.length;
      suggest.active = ul.hidden ? 0 : (suggest.active + (e.key === 'ArrowDown' ? 1 : -1) + n) % n;
      renderSuggest();
    }
  }

  const RSI_FIELDS = ['rsiW', 'rsiD', 'lookback', 'lowCount'];

  function syncEntryFields() {
    const f = document.getElementById('newPlanForm');
    const manual = f.elements.entry.value === 'manual';
    RSI_FIELDS.forEach((name) => { f.elements[name].disabled = manual; });
    document.querySelectorAll('.ld-entry-rsi').forEach((el) => el.classList.toggle('is-off', manual));
  }

  function setFormLocked(editing) {
    const f = document.getElementById('newPlanForm');
    const search = f.querySelector('.ld-stock-search');
    const hint = document.getElementById('editLockHint');
    document.getElementById('planModalTitle').textContent = editing ? '사다리 수정' : '새 분할매수 사다리';
    document.getElementById('planSubmit').textContent = editing ? '저장' : '등록';
    if (search) search.hidden = !!editing;
    f.elements.code.readOnly = !!editing;
    f.elements.search.disabled = !!editing;
    if (hint) hint.hidden = !editing;
  }

  function fillForm(p) {
    const f = document.getElementById('newPlanForm');
    const pr = prof(p);
    f.elements.search.value = (p.name || '') + ' (' + p.code + ')';
    f.elements.code.value = p.code || '';
    f.elements.name.value = p.name || '';
    f.elements.budget.value = String(p.budget || 0);
    formatMoneyInput(f.elements.budget);
    f.elements.alloc.value = pr.alloc_mode === 'grow' ? 'grow' : 'equal';
    f.elements.allocRatio.value = pr.alloc_ratio != null ? pr.alloc_ratio : 1.2;
    f.elements.firstBoost.checked = !!pr.first_boost;
    f.elements.firstBoostPct.value = pr.first_boost_pct != null ? pr.first_boost_pct : 20;
    f.elements.memo.value = p.memo || '';
    f.elements.entry.value = pr.entry_mode === 'manual' ? 'manual' : 'auto';
    f.elements.rsiW.value = pr.rsi_weekly_th != null ? pr.rsi_weekly_th : 30;
    f.elements.rsiD.value = pr.rsi_daily_th != null ? pr.rsi_daily_th : 30;
    f.elements.lookback.value = pr.daily_lookback != null ? pr.daily_lookback : 120;
    f.elements.lowCount.value = pr.daily_low_count != null ? pr.daily_low_count : 3;
    f.elements.step.value = pr.step_pct != null ? pr.step_pct : 5;
    f.elements.maxSteps.value = pr.max_steps != null ? pr.max_steps : 10;
    f.elements.arm.value = pr.tp_arm_pct != null ? pr.tp_arm_pct : 15;
  }

  function resetForm() {
    const f = document.getElementById('newPlanForm');
    f.reset();
    formatMoneyInput(f.elements.budget);
    state.quote = null;
    state.editingId = null;
    setFormLocked(false);
    syncEntryFields();
    syncAllocFields();
  }

  function openModal(show) {
    document.getElementById('newPlanModal').hidden = !show;
    if (show) {
      syncEntryFields();
      renderPreview();
      return;
    }
    state.editingId = null;
    setFormLocked(false);
  }

  function openNew() {
    resetForm();
    openModal(true);
  }

  function openEdit(p) {
    if (!canEdit(p)) return toast('진입 대기일 때만 수정할 수 있습니다', 'err');
    state.editingId = p.id;
    fillForm(p);
    const entry = (p.market || {}).entry || null;
    if (p.price) state.quote = { code: p.code, name: p.name, price: p.price, entry: entry };
    else state.quote = null;
    setFormLocked(true);
    openModal(true);
    if (!state.quote) onCodeInput();
  }

  async function submitForm(e) {
    e.preventDefault();
    const editingId = state.editingId;
    const o = readForm();
    if (!/^\d{6}$/.test(o.code)) return toast('종목코드 6자리를 입력하세요', 'err');
    if (o.budget <= 0) return toast('총 투자금을 입력하세요', 'err');
    if (editingId) {
      if (state.busy) return toast('저장 중입니다', 'err');
      const cur = plans().find((x) => x.id === editingId);
      if (!canEdit(cur)) return toast('진입 대기일 때만 수정할 수 있습니다', 'err');
      const btn = document.getElementById('planSubmit');
      state.busy = true;
      if (btn) { btn.disabled = true; btn.textContent = '저장 중…'; }
      try {
        const r = await api('PATCH', '/api/ladder/plans/' + editingId, {
          name: o.name, budget: o.budget, memo: o.memo, profile: o.profile,
        });
        if (cur && r) {
          cur.name = r.name;
          cur.budget = r.budget;
          cur.memo = r.memo;
          cur.profile = r.profile;
        }
        toast((r && r.name ? r.name : o.name) + ' 설정을 저장했습니다', 'ok');
        resetForm();
        openModal(false);
        renderAll();
        load(false);
      } catch (e) {
        toast(e.message || '저장에 실패했습니다', 'err');
        if (btn) btn.textContent = '저장';
      } finally {
        state.busy = false;
        if (btn) btn.disabled = false;
      }
      return;
    }
    const manual = o.profile.entry_mode === 'manual';
    if (manual && !confirm('등록 즉시 1차 시장가 매수를 시도합니다. 계속할까요?')) return;
    const r = await run(() => api('POST', '/api/ladder/plans', Object.assign({}, o, { buy_now: manual })));
    if (!r) return;
    state.selectedId = r.plan.id;
    let msg = r.plan.name + ' 사다리 등록';
    if (r.buy) msg += ' · 1차 매수 신호 ' + r.buy.qty + '주';
    if (r.buy_error) msg += ' · 1차 매수 실패: ' + r.buy_error;
    toast(msg, r.buy_error ? 'err' : 'ok');
    resetForm();
    openModal(false);
  }

  function bind() {
    document.querySelector('#planTable tbody').addEventListener('click', (e) => {
      const editBtn = e.target.closest('[data-edit]');
      const tr = e.target.closest('tr[data-id]');
      if (!tr) return;
      state.selectedId = tr.dataset.id;
      renderTable();
      renderDetail();
      if (editBtn) {
        const p = plans().find((x) => x.id === state.selectedId);
        if (p) openEdit(p);
      }
    });
    document.getElementById('statusFilter').addEventListener('click', (e) => {
      const b = e.target.closest('button[data-f]');
      if (!b) return;
      state.filter = b.dataset.f;
      document.querySelectorAll('#statusFilter button').forEach((x) => x.classList.toggle('active', x === b));
      renderTable();
    });
    document.getElementById('detailActions').addEventListener('click', (e) => {
      const b = e.target.closest('button[data-act]');
      if (b && !b.disabled) onAction(b.dataset.act);
    });
    document.getElementById('btnJudge').addEventListener('click', runPreview);
    document.getElementById('btnRefresh').addEventListener('click', () => load(true));
    document.getElementById('btnWatchToggle').addEventListener('click', () => {
      const on = !(state.data && state.data.enabled);
      if (on && !confirm('분할매수 자동 감시를 켭니다. 장중 10분마다 추가매수, 매일 15:05 판정에서 진입·매도 주문이 실제로 나갑니다.')) return;
      run(() => api('POST', '/api/ladder/enabled', { enabled: on }), on ? '자동 감시 ON' : '자동 감시 OFF — 판정·주문 중지');
    });
    document.getElementById('btnNewPlan').addEventListener('click', () => openNew());
    document.getElementById('btnCloseModal').addEventListener('click', () => openModal(false));
    document.getElementById('newPlanModal').addEventListener('click', (e) => {
      if (e.target.id === 'newPlanModal') openModal(false);
    });
    const form = document.getElementById('newPlanForm');
    form.elements.budget.addEventListener('input', () => formatMoneyInput(form.elements.budget));
    formatMoneyInput(form.elements.budget);
    form.elements.code.addEventListener('input', onCodeInput);
    form.elements.search.addEventListener('input', onSearchInput);
    form.elements.search.addEventListener('keydown', onSearchKey);
    form.elements.search.addEventListener('focus', () => { if (suggest.items.length && form.elements.search.value.trim()) renderSuggest(); });
    form.elements.search.addEventListener('blur', hideSuggest);
    document.getElementById('stockSuggest').addEventListener('mousedown', (e) => {
      e.preventDefault();
      const li = e.target.closest('li[data-i]');
      if (li) pickSuggest(Number(li.dataset.i));
    });
    form.elements.entry.addEventListener('change', syncEntryFields);
    form.elements.alloc.addEventListener('change', syncAllocFields);
    form.elements.allocRatio.addEventListener('input', syncAllocFields);
    form.addEventListener('input', renderPreview);
    form.addEventListener('change', renderPreview);
    syncEntryFields();
    syncAllocFields();
    form.addEventListener('submit', submitForm);
  }

  function updateClock() {
    const d = new Date();
    const mins = d.getHours() * 60 + d.getMinutes();
    const target = 15 * 60 + 5;
    const el = document.getElementById('judgeClock');
    if (!el) return;
    el.textContent = mins < target ? '다음 판정 15:05 (' + Math.floor((target - mins) / 60) + '시간 ' + ((target - mins) % 60) + '분 후)' : '오늘 판정 시각 지남 · 다음 거래일 15:05';
  }

  bind();
  load(false);
  updateClock();
  setInterval(() => { if (!document.hidden && !state.busy) load(false); }, REFRESH_MS);
  setInterval(updateClock, 30000);
})();
