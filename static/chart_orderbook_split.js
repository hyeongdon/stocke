'use strict';

/* 매수사다리 예상 시나리오 — 차트/호가 패널별 플래너 */

const SPLIT_PLANS_KEY = 'chartOrderbookSplitPlans';
const SPLIT_LEVEL_COLOR = '#22c55e';
const SPLIT_AVG_COLOR = '#f59e0b';
const SPLIT_STOP_COLOR = '#94a3b8';
const SPLIT_DRAG_PX = 6;
const SPLIT_PRESETS = {
  equal: '균등',
  pyramid: '피라미드(아래로 많이)',
  reverse: '역피라미드(위에 많이)',
};
const SPLIT_BASES = {
  current: '현재가',
  ma20: 'MA20',
  ma60: 'MA60',
  low20: '20봉 최저가',
  manual: '직접 입력',
};

function splitFmt(n) {
  const v = Number(n);
  return Number.isFinite(v) ? Math.round(v).toLocaleString('ko-KR') : '-';
}

function splitPct(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return '-';
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`;
}

function splitNum(v) {
  const n = Number(String(v == null ? '' : v).replace(/[,\s원]/g, ''));
  return Number.isFinite(n) ? n : 0;
}

/** KRX 호가단위 (2023~) */
function krxTick(price) {
  if (price < 2000) return 1;
  if (price < 5000) return 5;
  if (price < 20000) return 10;
  if (price < 50000) return 50;
  if (price < 200000) return 100;
  if (price < 500000) return 500;
  return 1000;
}

function roundTick(price) {
  const p = Number(price);
  if (!Number.isFinite(p) || p <= 0) return 0;
  const t = krxTick(p);
  return Math.round(p / t) * t;
}

/** 호가 단위 내림 (utils/rsi_ladder.tick_floor 와 동일) */
function floorTick(price) {
  const p = Number(price);
  if (!Number.isFinite(p) || p <= 0) return 0;
  const t = krxTick(p);
  const v = Math.floor(p / t) * t;
  const t2 = krxTick(v);
  return t2 !== t ? Math.floor(p / t2) * t2 : v;
}

const SPLIT_GROW_RATIO = 1.2;

/** 피라미드 = 사다리 grow(직전 차수의 1.2배), 역피라미드는 그 반대 */
function presetWeights(preset, n) {
  return Array.from({ length: n }, (_, i) => {
    if (preset === 'pyramid') return Math.pow(SPLIT_GROW_RATIO, i);
    if (preset === 'reverse') return Math.pow(SPLIT_GROW_RATIO, n - 1 - i);
    return 1;
  });
}

/** 사다리 엔진과 같은 가격: 직전 차수 × (1 - 간격%) 호가 내림 */
function ladderPrices(base, gapPct, n) {
  const out = [];
  let px = floorTick(base);
  for (let i = 0; i < n; i++) {
    if (i > 0) px = floorTick(px * (1 - gapPct / 100));
    out.push(px);
  }
  return out;
}

function readSplitPlans() {
  try {
    const saved = JSON.parse(localStorage.getItem(SPLIT_PLANS_KEY) || '{}');
    return saved && typeof saved === 'object' ? saved : {};
  } catch (_) {
    return {};
  }
}

function defaultSplitPlan() {
  return {
    total: 3000000,
    count: 10,
    gapPct: 5,
    preset: 'equal',
    basis: 'current',
    base: 0,
    stop: 0,
    levels: [],
  };
}

/** levels → 차수별 수량·금액·누적평단 */
function computeSplit(plan, currentPrice) {
  const levels = (plan.levels || []).filter((l) => l.price > 0);
  const sumW = levels.reduce((a, l) => a + (l.weight > 0 ? l.weight : 0), 0) || 1;
  let cumQty = 0;
  let cumCost = 0;
  const budgets = levels.map((l) => Math.floor(plan.total * (Math.max(0, l.weight) / sumW)));
  if (budgets.length) budgets[budgets.length - 1] += Math.floor(plan.total) - budgets.reduce((a, b) => a + b, 0);
  const rows = levels.map((l, i) => {
    const budget = budgets[i];
    const qty = Math.floor(budget / l.price);
    const cost = qty * l.price;
    cumQty += qty;
    cumCost += cost;
    const avg = cumQty ? cumCost / cumQty : 0;
    return {
      price: l.price,
      weight: l.weight,
      pct: (Math.max(0, l.weight) / sumW) * 100,
      qty,
      cost,
      cumQty,
      cumCost,
      avg,
      vsCur: currentPrice > 0 ? ((l.price - currentPrice) / currentPrice) * 100 : null,
    };
  });
  const avg = cumQty ? cumCost / cumQty : 0;
  const pnlRate = avg && currentPrice > 0 ? ((currentPrice - avg) / avg) * 100 : null;
  const pnl = avg && currentPrice > 0 ? (currentPrice - avg) * cumQty : null;
  const stopLoss = plan.stop > 0 && cumQty ? (plan.stop - avg) * cumQty : null;
  const stopRate = plan.stop > 0 && avg ? ((plan.stop - avg) / avg) * 100 : null;
  return { rows, cumQty, cumCost, avg, pnl, pnlRate, stopLoss, stopRate };
}

class SplitPlanner {
  constructor(panel) {
    this.panel = panel;
    this.code = '';
    this.open = false;
    this.plan = defaultSplitPlan();
    this.currentPrice = 0;
    this.lines = [];
    this.drag = null;
    this.boundHost = null;
    this.card = panel.el('splitCard');
    this.toggleBtn = panel.el('splitToggle');
    this.toggleBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.setOpen(!this.open);
    });
    this.card?.addEventListener('change', (e) => this.onInput(e));
    this.card?.addEventListener('click', (e) => this.onClick(e));
  }

  setOpen(open) {
    this.open = !!open;
    if (this.card) this.card.hidden = !this.open;
    if (this.toggleBtn) {
      this.toggleBtn.classList.toggle('on', this.open);
      this.toggleBtn.setAttribute('aria-pressed', this.open ? 'true' : 'false');
    }
    if (this.open && !this.plan.levels.length) this.generate();
    if (!!this.plan.open !== this.open) {
      this.plan.open = this.open;
      this.save();
    }
    this.render();
    this.drawLines();
    requestAnimationFrame(() => this.panel.resizeCharts());
  }

  setCode(code) {
    if (code === this.code) return;
    this.code = code;
    const saved = code ? readSplitPlans()[code] : null;
    this.plan = Object.assign(defaultSplitPlan(), saved || {});
    this.currentPrice = 0;
    if (!!this.plan.open !== this.open) {
      this.setOpen(!!this.plan.open);
      return;
    }
    if (this.open && !this.plan.levels.length) this.generate();
    this.render();
    this.drawLines();
  }

  setCurrentPrice(price) {
    const p = Number(price) || 0;
    if (p === this.currentPrice) return;
    const first = !this.currentPrice;
    this.currentPrice = p;
    if (first && this.open && !this.plan.levels.length) this.generate();
    this.render();
  }

  save() {
    if (!this.code) return;
    try {
      const all = readSplitPlans();
      all[this.code] = this.plan;
      localStorage.setItem(SPLIT_PLANS_KEY, JSON.stringify(all));
    } catch (_) { /* ignore */ }
  }

  basisPrice(basis) {
    const bars = this.panel.lastBars || [];
    const last = bars[bars.length - 1] || {};
    if (basis === 'current') return this.currentPrice || Number(last.close) || 0;
    if (basis === 'ma20' || basis === 'ma60') return Number(last[basis]) || 0;
    if (basis === 'low20') {
      const lows = bars.slice(-20).map((b) => Number(b.low) || Number(b.close) || 0).filter((v) => v > 0);
      return lows.length ? Math.min(...lows) : 0;
    }
    return this.plan.base;
  }

  generate() {
    const p = this.plan;
    const base = floorTick(p.basis === 'manual' ? p.base : this.basisPrice(p.basis));
    if (!base) {
      p.levels = [];
      return;
    }
    p.base = base;
    const n = Math.max(1, Math.min(10, Math.round(p.count) || 1));
    const weights = presetWeights(p.preset, n);
    const prices = ladderPrices(base, p.gapPct, n);
    p.levels = weights.map((w, i) => ({ price: prices[i], weight: w }));
    this.save();
  }

  onInput(e) {
    const t = e.target;
    const p = this.plan;
    const field = t.dataset.field;
    if (field) {
      if (field === 'preset' || field === 'basis') p[field] = t.value;
      else p[field] = splitNum(t.value);
      if (field === 'base') p.basis = 'manual';
      if (field !== 'stop' && field !== 'total') this.generate();
    }
    const lvIdx = t.dataset.level;
    if (lvIdx != null) {
      const lv = p.levels[Number(lvIdx)];
      if (lv) {
        if (t.dataset.key === 'price') lv.price = roundTick(splitNum(t.value));
        else lv.weight = Math.max(0, splitNum(t.value));
      }
    }
    this.save();
    this.render();
    this.drawLines();
  }

  /** 시나리오 → /ladder 새 계획 모달 프리필 (가격 도달 진입, 등록은 사다리 화면에서 확인 후) */
  toLadder() {
    const p = this.plan;
    const levels = (p.levels || []).filter((l) => l.price > 0).sort((a, b) => b.price - a.price);
    if (!this.code || !levels.length) return;
    const n = levels.length;
    const gap = Math.max(0.5, Number(p.gapPct) || 5);
    const prices = ladderPrices(levels[0].price, gap, n);
    const weights = presetWeights(p.preset, n);
    const custom = levels.some((l, i) => l.price !== prices[i] || Math.abs(l.weight - weights[i]) > 1e-6);
    const warns = [];
    if (custom) warns.push(`차수 가격·비중을 직접 수정했습니다. 사다리는 1차 ${splitFmt(levels[0].price)}원에서 직전 체결가 대비 -${gap}%마다 매수하므로 예상 평단이 달라질 수 있습니다. [재생성]으로 맞춘 뒤 넘기는 것을 권장합니다.`);
    if (p.preset === 'reverse') warns.push('사다리는 역피라미드 배분을 지원하지 않아 균등 배분으로 넘깁니다.');
    if (warns.length && !confirm(`${warns.join('\n\n')}\n\n그래도 사다리 등록 화면을 열까요?`)) return;
    const q = new URLSearchParams({
      code: this.code,
      name: this.panel.name || '',
      budget: String(Math.floor(p.total) || 0),
      entry_price: String(levels[0].price),
      step_pct: String(gap),
      max_steps: String(n),
      alloc_mode: p.preset === 'pyramid' ? 'grow' : 'equal',
      from: 'split',
    });
    window.open(`/ladder?${q.toString()}`, '_blank');
  }

  onClick(e) {
    if (e.target.closest('[data-split-ladder]')) {
      this.toLadder();
      return;
    }
    const del = e.target.closest('[data-del-level]');
    if (del) {
      this.plan.levels.splice(Number(del.dataset.delLevel), 1);
    } else if (e.target.closest('[data-split-add]')) {
      const lv = this.plan.levels;
      const last = lv[lv.length - 1];
      const price = last
        ? roundTick(last.price * (1 - (this.plan.gapPct || 3) / 100))
        : roundTick(this.basisPrice('current'));
      if (!price) return;
      lv.push({ price, weight: last ? last.weight : 1 });
    } else if (e.target.closest('[data-split-regen]')) {
      this.generate();
    } else if (e.target.closest('[data-split-reset]')) {
      this.plan = Object.assign(defaultSplitPlan(), { open: this.open });
      this.generate();
    } else {
      return;
    }
    this.save();
    this.render();
    this.drawLines();
  }

  render() {
    if (!this.card || !this.open) return;
    const active = document.activeElement;
    let focusSel = '';
    if (active && this.card.contains(active)) {
      if (active.dataset.field) focusSel = `[data-field="${active.dataset.field}"]`;
      else if (active.dataset.level != null) focusSel = `[data-level="${active.dataset.level}"][data-key="${active.dataset.key}"]`;
    }
    this.renderBody();
    if (focusSel) this.card.querySelector(focusSel)?.focus();
  }

  renderBody() {
    const p = this.plan;
    const r = computeSplit(p, this.currentPrice);
    const opt = (map, cur) => Object.entries(map)
      .map(([k, v]) => `<option value="${k}"${k === cur ? ' selected' : ''}>${v}</option>`).join('');
    const cls = (v) => (v == null ? '' : v > 0 ? 'up' : v < 0 ? 'down' : '');

    const rowsHtml = r.rows.length
      ? r.rows.map((row, i) => `<tr>
          <td class="no">${i + 1}차</td>
          <td><input class="cob-split-in" data-level="${i}" data-key="price" value="${splitFmt(row.price)}" inputmode="numeric"></td>
          <td class="dim ${cls(row.vsCur)}">${row.vsCur == null ? '-' : splitPct(row.vsCur)}</td>
          <td><input class="cob-split-in sm" data-level="${i}" data-key="weight" value="${+Number(row.weight).toFixed(2)}" inputmode="decimal"><span class="dim">${row.pct.toFixed(0)}%</span></td>
          <td>${splitFmt(row.qty)}</td>
          <td>${splitFmt(row.cost)}</td>
          <td class="avg">${splitFmt(row.avg)}</td>
          <td><button type="button" class="cob-split-del" data-del-level="${i}" title="삭제">×</button></td>
        </tr>`).join('')
      : `<tr><td colspan="8" class="dim center">기준가를 구할 수 없습니다. 기준가를 직접 입력하세요</td></tr>`;

    const stopTxt = r.stopLoss == null
      ? '-'
      : `<b class="${cls(r.stopLoss)}">${splitFmt(r.stopLoss)}원 (${splitPct(r.stopRate)})</b>`;

    this.card.innerHTML = `
      <div class="cob-split-head">
        <h3>매수사다리 시나리오</h3>
        <span class="hint">차트의 초록 라인을 드래그해 가격 조정 · Shift+클릭으로 차수 추가</span>
      </div>
      <div class="cob-split-form">
        <label>총 투자금<input data-field="total" value="${splitFmt(p.total)}" inputmode="numeric"></label>
        <label>분할<input data-field="count" type="number" min="1" max="10" value="${p.count}"></label>
        <label>간격(%)<input data-field="gapPct" type="number" step="0.5" min="0" value="${p.gapPct}"></label>
        <label>배분<select data-field="preset">${opt(SPLIT_PRESETS, p.preset)}</select></label>
        <label>기준<select data-field="basis">${opt(SPLIT_BASES, p.basis)}</select></label>
        <label>기준가<input data-field="base" value="${splitFmt(p.base)}" inputmode="numeric"></label>
        <label>손절가<input data-field="stop" value="${p.stop ? splitFmt(p.stop) : ''}" placeholder="선택" inputmode="numeric"></label>
        <div class="cob-split-actions">
          <button type="button" class="btn sm" data-split-regen title="설정값으로 다시 생성">재생성</button>
          <button type="button" class="btn sm" data-split-add>+ 차수</button>
          <button type="button" class="btn sm" data-split-reset title="기본값으로">초기화</button>
          <button type="button" class="btn sm" data-split-ladder title="이 시나리오로 매수사다리 감시 계획 등록 화면 열기">사다리로 등록 →</button>
        </div>
      </div>
      <div class="cob-split-table-wrap">
        <table class="cob-split-table">
          <thead><tr><th>차수</th><th>가격</th><th>현재가比</th><th>비중</th><th>수량</th><th>금액</th><th>누적평단</th><th></th></tr></thead>
          <tbody>${rowsHtml}</tbody>
        </table>
      </div>
      <div class="cob-split-summary">
        <span>총 <b>${splitFmt(r.cumQty)}</b>주 · <b>${splitFmt(r.cumCost)}</b>원</span>
        <span>전체 체결 평단 <b class="avg">${splitFmt(r.avg)}</b></span>
        <span>현재가 기준 <b class="${cls(r.pnl)}">${r.pnl == null ? '-' : `${splitFmt(r.pnl)}원 (${splitPct(r.pnlRate)})`}</b></span>
        <span>손절 시 ${stopTxt}</span>
      </div>`;
  }

  /* ---------- 차트 라인 ---------- */

  clearLines() {
    const s = this.panel.candleSeries;
    this.lines.forEach((l) => {
      try { if (s) s.removePriceLine(l); } catch (_) { /* ignore */ }
    });
    this.lines = [];
    this.tags = [];
    if (this.tagLayer) this.tagLayer.innerHTML = '';
  }

  ensureTagLayer() {
    const host = this.panel.el('chartHost');
    if (!host) return null;
    if (this.tagLayer && this.tagLayer.parentNode === host) return this.tagLayer;
    if (getComputedStyle(host).position === 'static') host.style.position = 'relative';
    const layer = document.createElement('div');
    layer.className = 'cob-split-tags';
    layer.style.cssText = 'position:absolute;inset:0;pointer-events:none;z-index:5;overflow:hidden;';
    host.appendChild(layer);
    this.tagLayer = layer;
    return layer;
  }

  addTag(price, text, color) {
    const layer = this.ensureTagLayer();
    if (!layer) return;
    const el = document.createElement('div');
    el.style.cssText = `position:absolute;right:0;transform:translateY(-50%);display:flex;align-items:center;`
      + `font:600 11px/1 sans-serif;color:#fff;white-space:nowrap;`;
    el.innerHTML = `<span style="background:${color};padding:3px 6px;border-radius:3px 0 0 3px;">${text}</span>`
      + `<span style="width:0;height:0;border-top:8.5px solid transparent;border-bottom:8.5px solid transparent;`
      + `border-left:7px solid ${color};"></span>`;
    layer.appendChild(el);
    this.tags.push({ el, price });
    this.startTagLoop();
  }

  positionTags() {
    const s = this.panel.candleSeries;
    const chart = this.panel.chart;
    if (!s || !chart || !this.tags.length) return;
    let axisW = 0;
    try { axisW = chart.priceScale('right').width(); } catch (_) { /* ignore */ }
    const h = this.tagLayer ? this.tagLayer.clientHeight : 0;
    const placed = [];
    this.tags.forEach((t) => {
      const y = s.priceToCoordinate(t.price);
      const show = y != null && y >= 0 && y <= h;
      t.el.style.display = show ? 'flex' : 'none';
      if (!show) return;
      let right = axisW + 2;
      placed.forEach((p) => {
        if (Math.abs(p.y - y) < 17) right = Math.max(right, p.right + p.w + 4);
      });
      t.el.style.top = `${y}px`;
      t.el.style.right = `${right}px`;
      placed.push({ y, right, w: t.el.offsetWidth });
    });
  }

  startTagLoop() {
    if (this.tagRaf) return;
    const tick = () => {
      if (!this.open || !this.tags || !this.tags.length) { this.tagRaf = null; return; }
      this.positionTags();
      this.tagRaf = requestAnimationFrame(tick);
    };
    this.tagRaf = requestAnimationFrame(tick);
  }

  onChartDestroyed() {
    this.tagLayer = null;
    this.tags = [];
    this.lines = [];
    this.boundHost = null;
  }

  onChartReady() {
    this.bindChart();
    this.drawLines();
  }

  drawLines() {
    this.clearLines();
    const s = this.panel.candleSeries;
    if (!s || !this.open) return;
    const LS = LightweightCharts.LineStyle;
    const r = computeSplit(this.plan, this.currentPrice);
    r.rows.forEach((row, i) => {
      this.lines.push(s.createPriceLine({
        price: row.price,
        color: SPLIT_LEVEL_COLOR,
        lineWidth: 1,
        lineStyle: LS.Dashed,
        axisLabelVisible: false,
      }));
      this.addTag(row.price, `${i + 1}차 ${row.qty}주 · ${splitFmt(row.price)}`, SPLIT_LEVEL_COLOR);
    });
    if (r.avg > 0) {
      const avg = Math.round(r.avg);
      this.lines.push(s.createPriceLine({
        price: avg,
        color: SPLIT_AVG_COLOR,
        lineWidth: 2,
        lineStyle: LS.Solid,
        axisLabelVisible: false,
      }));
      this.addTag(avg, `예상평단 · ${splitFmt(avg)}`, SPLIT_AVG_COLOR);
    }
    if (this.plan.stop > 0) {
      this.lines.push(s.createPriceLine({
        price: this.plan.stop,
        color: SPLIT_STOP_COLOR,
        lineWidth: 1,
        lineStyle: LS.SparseDotted,
        axisLabelVisible: false,
      }));
      this.addTag(this.plan.stop, `손절 · ${splitFmt(this.plan.stop)}`, SPLIT_STOP_COLOR);
    }
    this.positionTags();
  }

  levelIndexAt(y) {
    const s = this.panel.candleSeries;
    if (!s) return -1;
    let best = -1;
    let bestD = SPLIT_DRAG_PX + 1;
    this.plan.levels.forEach((l, i) => {
      const c = s.priceToCoordinate(l.price);
      if (c == null) return;
      const d = Math.abs(c - y);
      if (d < bestD) { bestD = d; best = i; }
    });
    return best;
  }

  setChartInteractive(on) {
    const chart = this.panel.chart;
    if (!chart) return;
    chart.applyOptions(on
      ? { handleScroll: { vertTouchDrag: false, horzTouchDrag: true, mouseWheel: true, pressedMouseMove: true }, handleScale: true }
      : { handleScroll: false, handleScale: false });
  }

  bindChart() {
    const host = this.panel.el('chartHost');
    if (!host || this.boundHost === host) return;
    this.boundHost = host;
    const yOf = (e) => e.clientY - host.getBoundingClientRect().top;

    if (!host.dataset.splitBound) {
      host.dataset.splitBound = '1';
      host.addEventListener('pointerdown', (e) => {
        if (!this.open || e.button !== 0) return;
        const idx = this.levelIndexAt(yOf(e));
        if (idx < 0) return;
        e.preventDefault();
        e.stopPropagation();
        this.drag = { idx, pointerId: e.pointerId };
        this.setChartInteractive(false);
        try { host.setPointerCapture(e.pointerId); } catch (_) { /* ignore */ }
      }, true);

      host.addEventListener('pointermove', (e) => {
        if (!this.open) return;
        const y = yOf(e);
        if (!this.drag) {
          host.style.cursor = this.levelIndexAt(y) >= 0 ? 'ns-resize' : '';
          return;
        }
        e.stopPropagation();
        const price = roundTick(this.panel.candleSeries?.coordinateToPrice(y));
        const lv = this.plan.levels[this.drag.idx];
        if (!lv || !price || price === lv.price) return;
        lv.price = price;
        this.drawLines();
        this.render();
      }, true);

      const end = (e) => {
        if (!this.drag) return;
        try { host.releasePointerCapture(this.drag.pointerId); } catch (_) { /* ignore */ }
        this.drag = null;
        host.style.cursor = '';
        this.setChartInteractive(true);
        this.plan.levels.sort((a, b) => b.price - a.price);
        this.save();
        this.render();
        this.drawLines();
        if (e) e.stopPropagation();
      };
      host.addEventListener('pointerup', end, true);
      host.addEventListener('pointercancel', end, true);

      host.addEventListener('click', (e) => {
        if (!this.open || !e.shiftKey) return;
        const price = roundTick(this.panel.candleSeries?.coordinateToPrice(yOf(e)));
        if (!price) return;
        const lv = this.plan.levels;
        lv.push({ price, weight: lv.length ? lv[lv.length - 1].weight : 1 });
        lv.sort((a, b) => b.price - a.price);
        this.save();
        this.render();
        this.drawLines();
      });
    }
  }
}

window.SplitPlanner = SplitPlanner;
