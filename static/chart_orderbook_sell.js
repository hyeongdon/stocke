'use strict';

/* 매도사다리 시나리오 — 가격 도달 매도 + 이동평균선 이탈 매도 (chart_orderbook_split.js 헬퍼 사용) */

const SELL_PLANS_KEY = 'chartOrderbookSellPlans';
const SELL_PRICE_COLOR = '#ec4899';
const SELL_MA_COLOR = '#06b6d4';
const SELL_MODES = {
  price: '가격',
  ma: '이동평균선',
};
const SELL_SIDES = { reach: '도달', break: '이탈' };
function sellArmOptions(side) {
  return {
    none: '없음',
    cross: side === 'break' ? '상회 후' : '하회 후',
  };
}
const SELL_PRESETS = {
  equal: '균등',
  front: '앞차수 많이',
  back: '뒷차수 많이',
};
const SELL_MAS = {
  ma5: 'MA5',
  ma20: 'MA20',
  ma60: 'MA60',
  ma120: 'MA120',
};

function sellWeights(preset, n) {
  if (preset === 'front') return presetWeights('reverse', n);
  if (preset === 'back') return presetWeights('pyramid', n);
  return presetWeights('equal', n);
}

function readSellPlans() {
  try {
    const saved = JSON.parse(localStorage.getItem(SELL_PLANS_KEY) || '{}');
    return saved && typeof saved === 'object' ? saved : {};
  } catch (_) {
    return {};
  }
}

function defaultSellPlan() {
  return {
    mode: 'ma',
    qty: 0,
    avg: 0,
    count: 4,
    firstPct: 5,
    gapPct: 5,
    preset: 'equal',
    armPct: 15,
    levels: [],
  };
}

function sellSideOf(lv) {
  if (lv && (lv.side === 'reach' || lv.side === 'break')) return lv.side;
  return lv && lv.type === 'ma' ? 'break' : 'reach';
}

function sellArmOf(lv) {
  return lv && lv.arm === 'cross' ? 'cross' : 'none';
}
function sellArmLabel(lv) {
  if (sellArmOf(lv) !== 'cross') return '';
  return sellSideOf(lv) === 'break' ? '상회 후' : '하회 후';
}

class SellPlanner {
  constructor(panel) {
    this.panel = panel;
    this.code = '';
    this.open = false;
    this.plan = defaultSellPlan();
    this.currentPrice = 0;
    this.lines = [];
    this.tags = [];
    this.drag = null;
    this.boundHost = null;
    this.card = panel.el('sellCard');
    this.toggleBtn = panel.el('sellToggle');
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
    this.currentPrice = 0;
    if (!code) {
      this.plan = defaultSellPlan();
      if (this.open) this.setOpen(false);
      else {
        this.render();
        this.clearLines();
      }
      return;
    }
    const saved = readSellPlans()[code];
    this.plan = Object.assign(defaultSellPlan(), saved || {});
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
    if (first) this.drawLines();
  }

  save() {
    if (!this.code) return;
    try {
      const all = readSellPlans();
      all[this.code] = this.plan;
      localStorage.setItem(SELL_PLANS_KEY, JSON.stringify(all));
    } catch (_) { /* ignore */ }
  }

  /** 보유수량·평단: 직접 입력값, 없으면 매수사다리 예상치, 그래도 없으면 현재가 */
  holding() {
    let qty = this.plan.qty;
    let avg = this.plan.avg;
    const split = this.panel.split;
    if ((!qty || !avg) && split && split.plan && split.plan.levels.length) {
      const r = computeSplit(split.plan, this.currentPrice);
      if (!qty) qty = r.cumQty;
      if (!avg) avg = r.avg;
    }
    if (!avg) avg = this.currentPrice || this.lastBar().close || 0;
    return { qty: Math.max(0, Math.floor(qty || 0)), avg: Number(avg) || 0 };
  }

  lastBar() {
    const bars = this.panel.lastBars || [];
    return bars[bars.length - 1] || {};
  }

  maValue(key) {
    return Number(this.lastBar()[key]) || 0;
  }

  levelPrice(lv) {
    if (lv.type === 'ma') {
      const ma = this.maValue(lv.ma);
      return ma ? roundTick(ma * (1 + (Number(lv.offset) || 0) / 100)) : 0;
    }
    return lv.price;
  }

  generate() {
    const p = this.plan;
    const n = Math.max(1, Math.min(10, Math.round(p.count) || 1));
    if (p.mode === 'ma') {
      const keys = ['ma5', 'ma20', 'ma60', 'ma120'].slice(0, Math.min(n, 4));
      const weights = sellWeights(p.preset, keys.length);
      p.levels = keys.map((ma, i) => ({
        type: 'ma', ma, offset: 0, weight: weights[i], side: 'break', arm: 'cross',
      }));
    } else {
      const { avg } = this.holding();
      if (!avg) {
        p.levels = [];
        return;
      }
      const weights = sellWeights(p.preset, n);
      let px = roundTick(avg * (1 + p.firstPct / 100));
      p.levels = weights.map((w, i) => {
        if (i > 0) px = roundTick(px * (1 + p.gapPct / 100));
        return { type: 'price', price: px, weight: w, side: 'reach', arm: 'none' };
      });
    }
    this.save();
  }

  compute() {
    const { qty, avg } = this.holding();
    const levels = this.plan.levels || [];
    const sumW = levels.reduce((a, l) => a + Math.max(0, l.weight), 0) || 1;
    const qtys = levels.map((l) => Math.floor(qty * (Math.max(0, l.weight) / sumW)));
    if (qtys.length) qtys[qtys.length - 1] += qty - qtys.reduce((a, b) => a + b, 0);
    let totalQty = 0;
    let proceeds = 0;
    let pnl = 0;
    const rows = levels.map((l, i) => {
      const price = this.levelPrice(l);
      const q = qtys[i];
      const amt = price * q;
      if (price > 0) {
        totalQty += q;
        proceeds += amt;
        pnl += (price - avg) * q;
      }
      return {
        lv: l,
        price,
        qty: q,
        amt,
        pct: (Math.max(0, l.weight) / sumW) * 100,
        vsAvg: avg > 0 && price > 0 ? ((price - avg) / avg) * 100 : null,
        vsCur: this.currentPrice > 0 && price > 0 ? ((price - this.currentPrice) / this.currentPrice) * 100 : null,
      };
    });
    const cost = avg * totalQty;
    return { rows, qty, avg, totalQty, proceeds, pnl, pnlRate: cost ? (pnl / cost) * 100 : null };
  }

  onInput(e) {
    const t = e.target;
    const p = this.plan;
    const field = t.dataset.field;
    if (field) {
      if (field === 'mode' || field === 'preset') p[field] = t.value;
      else p[field] = splitNum(t.value);
      if (field !== 'qty' && field !== 'armPct') this.generate();
    }
    const lvIdx = t.dataset.level;
    if (lvIdx != null) {
      const lv = p.levels[Number(lvIdx)];
      if (lv) {
        const key = t.dataset.key;
        if (key === 'price') lv.price = roundTick(splitNum(t.value));
        else if (key === 'ma') lv.ma = t.value;
        else if (key === 'offset') lv.offset = splitNum(t.value);
        else if (key === 'side') lv.side = t.value === 'break' ? 'break' : 'reach';
        else if (key === 'arm') lv.arm = t.value === 'cross' ? 'cross' : 'none';
        else lv.weight = Math.max(0, splitNum(t.value));
      }
    }
    this.save();
    this.render();
    this.drawLines();
  }

  /** 이 종목의 진행 중인 매수사다리 계획에 매도사다리를 등록 → 엔진이 실제 매도 주문을 낸다 */
  async toEngine(enabled = true) {
    if (!this.code) return;
    let plan;
    try {
      const res = await fetch('/api/ladder/state');
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
      const st = await res.json();
      plan = (st.plans || []).find((p) => p.code === this.code && p.status !== 'DONE');
    } catch (err) {
      alert(`사다리 상태 조회 실패: ${err.message || err}`);
      return;
    }
    if (!plan) {
      alert('이 종목에 진행 중인 매수사다리 계획이 없습니다. 먼저 매수사다리를 [사다리로 등록] 하세요.');
      return;
    }
    let levels = [];
    if (enabled) {
      const src = (this.plan.levels || []).filter((l) => (l.type === 'ma' ? l.ma : l.price > 0) && l.weight > 0);
      if (!src.length) {
        alert('등록할 매도 차수가 없습니다.');
        return;
      }
      const sumW = src.reduce((a, l) => a + l.weight, 0);
      levels = src.map((l) => ({
        type: l.type === 'ma' ? 'ma' : 'price',
        price: l.type === 'ma' ? 0 : l.price,
        ma: l.type === 'ma' ? Number(String(l.ma).replace('ma', '')) : 0,
        offset: Number(l.offset) || 0,
        side: sellSideOf(l),
        arm: sellArmOf(l),
        pct: Math.round((l.weight / sumW) * 10000) / 100,
      }));
      levels[levels.length - 1].pct = Math.round((100 - levels.slice(0, -1).reduce((a, l) => a + l.pct, 0)) * 100) / 100;
      const armPct = Math.max(0, Number(this.plan.armPct) || 0);
      const desc = (armPct ? `익절 무장 +${armPct}% (이탈 차수)\n` : '')
        + levels.map((l, i) => `${i + 1}차 ${l.type === 'ma'
          ? `MA${l.ma}${l.offset ? ` ${l.offset > 0 ? '+' : ''}${l.offset}%` : ''}`
          : `${splitFmt(l.price)}원`} ${SELL_SIDES[l.side] || l.side}${sellArmLabel(l) ? ` ·${sellArmLabel(l)}` : ''} → ${l.pct}%`).join('\n');
      const ok = confirm(`${plan.name || this.code} 매수사다리 계획에 매도사다리를 연결합니다.\n\n${desc}\n\n`
        + '• 도달: 현재가 ≥ 목표가 / 이탈: 현재가 < 목표가\n'
        + '• 상회 후(이탈): 켠 뒤 목표가 위에 한 번 닿아야 무장 · 하회 후(도달): 먼저 아래\n'
        + '• 익절 무장%: 이탈 차수만 최고수익률 조건\n'
        + '• 가격 차수=장중 10분, MA 차수=15:05 · 텔레그램 3분 유예\n\n실제 주문이 나갑니다. 진행할까요?');
      if (!ok) return;
    } else if (!confirm('매도사다리 연결을 해제하고 기본 익절(S1~S3)로 되돌릴까요?')) {
      return;
    }
    try {
      const res = await fetch(`/api/ladder/plans/${encodeURIComponent(plan.id)}/sell-ladder`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          enabled,
          arm_pct: enabled ? Math.max(0, Number(this.plan.armPct) || 0) : 0,
          levels,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || res.statusText);
      this.plan.linkedPlanId = enabled ? plan.id : null;
      this.save();
      this.render();
      alert(enabled ? '매도사다리를 엔진에 연결했습니다.' : '매도사다리 연결을 해제했습니다.');
    } catch (err) {
      alert(`매도사다리 연결 실패: ${err.message || err}`);
    }
  }

  onClick(e) {
    if (e.target.closest('[data-sell-engine]')) {
      this.toEngine(true);
      return;
    }
    if (e.target.closest('[data-sell-unlink]')) {
      this.toEngine(false);
      return;
    }
    const del = e.target.closest('[data-del-level]');
    const lv = this.plan.levels;
    if (del) {
      lv.splice(Number(del.dataset.delLevel), 1);
    } else if (e.target.closest('[data-sell-add-price]')) {
      const prices = lv.filter((l) => l.type !== 'ma').map((l) => l.price);
      const top = prices.length ? Math.max(...prices) : 0;
      const price = top
        ? roundTick(top * (1 + (this.plan.gapPct || 5) / 100))
        : roundTick(this.holding().avg * (1 + (this.plan.firstPct || 5) / 100));
      if (!price) return;
      lv.push({ type: 'price', price, weight: 1, side: 'reach', arm: 'none' });
    } else if (e.target.closest('[data-sell-add-ma]')) {
      lv.push({ type: 'ma', ma: 'ma20', offset: 0, weight: 1, side: 'break', arm: 'cross' });
    } else if (e.target.closest('[data-sell-regen]')) {
      this.generate();
    } else if (e.target.closest('[data-sell-reset]')) {
      this.plan = Object.assign(defaultSellPlan(), { open: this.open });
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
    const r = this.compute();
    const opt = (map, cur) => Object.entries(map)
      .map(([k, v]) => `<option value="${k}"${k === cur ? ' selected' : ''}>${v}</option>`).join('');
    const cls = (v) => (v == null ? '' : v > 0 ? 'up' : v < 0 ? 'down' : '');
    const priceMode = p.mode !== 'ma';

    const condCell = (row, i) => (row.lv.type === 'ma'
      ? `<select class="cob-split-in" data-level="${i}" data-key="ma">${opt(SELL_MAS, row.lv.ma)}</select>
         <input class="cob-split-in sm" data-level="${i}" data-key="offset" value="${+Number(row.lv.offset || 0).toFixed(2)}" inputmode="decimal" title="MA 대비 오프셋(%)"><span class="dim">%</span>`
      : `<input class="cob-split-in" data-level="${i}" data-key="price" value="${splitFmt(row.price)}" inputmode="numeric"><span class="dim">원</span>`);

    const rowsHtml = r.rows.length
      ? r.rows.map((row, i) => `<tr>
          <td class="no sell">${i + 1}차</td>
          <td class="cond">${condCell(row, i)}</td>
          <td><select class="cob-split-in" data-level="${i}" data-key="side">${opt(SELL_SIDES, sellSideOf(row.lv))}</select></td>
          <td><select class="cob-split-in" data-level="${i}" data-key="arm" title="이탈=상회 후 / 도달=하회 후. 활성 이후 기준">${opt(sellArmOptions(sellSideOf(row.lv)), sellArmOf(row.lv))}</select></td>
          <td>${row.lv.type === 'ma' ? (row.price ? splitFmt(row.price) : '<span class="dim">MA 없음</span>') : ''}</td>
          <td class="dim ${cls(row.vsCur)}">${row.vsCur == null ? '-' : splitPct(row.vsCur)}</td>
          <td class="${cls(row.vsAvg)}">${row.vsAvg == null ? '-' : splitPct(row.vsAvg)}</td>
          <td><input class="cob-split-in sm" data-level="${i}" data-key="weight" value="${+Number(row.lv.weight).toFixed(2)}" inputmode="decimal"><span class="dim">${row.pct.toFixed(0)}%</span></td>
          <td>${splitFmt(row.qty)}</td>
          <td>${splitFmt(row.amt)}</td>
          <td><button type="button" class="cob-split-del" data-del-level="${i}" title="삭제">×</button></td>
        </tr>`).join('')
      : `<tr><td colspan="11" class="dim center">평단을 구할 수 없습니다. 평단을 직접 입력하세요</td></tr>`;

    this.card.innerHTML = `
      <div class="cob-split-head">
        <h3>매도사다리 시나리오</h3>
        <span class="hint">기본=MA 이탈+상회 후 · 익절 무장 · 가격 라인 드래그/Alt+클릭</span>
      </div>
      <div class="cob-split-form">
        <label>방식<select data-field="mode">${opt(SELL_MODES, p.mode)}</select></label>
        <label>보유수량<input data-field="qty" value="${p.qty ? splitFmt(p.qty) : ''}" placeholder="${splitFmt(r.qty)}" inputmode="numeric"></label>
        <label>평단<input data-field="avg" value="${p.avg ? splitFmt(p.avg) : ''}" placeholder="${splitFmt(r.avg)}" inputmode="numeric"></label>
        <label>익절무장%<input data-field="armPct" type="number" min="0" max="100" step="0.5" value="${p.armPct ?? 15}" title="이탈 차수만 적용. 0=끔"></label>
        <label>분할<input data-field="count" type="number" min="1" max="${priceMode ? 10 : 4}" value="${p.count}"></label>
        ${priceMode ? `
        <label>1차(평단+%)<input data-field="firstPct" type="number" step="0.5" value="${p.firstPct}"></label>
        <label>간격(%)<input data-field="gapPct" type="number" step="0.5" min="0" value="${p.gapPct}"></label>` : ''}
        <label>배분<select data-field="preset">${opt(SELL_PRESETS, p.preset)}</select></label>
        <div class="cob-split-actions">
          <button type="button" class="btn sm" data-sell-regen title="설정값으로 다시 생성">재생성</button>
          <button type="button" class="btn sm" data-sell-add-price>+ 가격 차수</button>
          <button type="button" class="btn sm" data-sell-add-ma>+ MA 차수</button>
          <button type="button" class="btn sm" data-sell-reset title="기본값으로">초기화</button>
          <button type="button" class="btn sm" data-sell-engine title="이 종목의 매수사다리 계획에 연결 — 실제 매도 주문">엔진에 연결 →</button>
          ${p.linkedPlanId ? '<button type="button" class="btn sm" data-sell-unlink title="기본 익절(S1~S3)로 되돌리기">연결 해제</button>' : ''}
        </div>
      </div>
      <div class="cob-split-table-wrap">
        <table class="cob-split-table">
          <thead><tr><th>차수</th><th>조건</th><th>방향</th><th>확인</th><th>현재 MA가</th><th>현재가比</th><th>평단比</th><th>비중</th><th>수량</th><th>금액</th><th></th></tr></thead>
          <tbody>${rowsHtml}</tbody>
        </table>
      </div>
      <div class="cob-split-summary">
        <span>매도 <b>${splitFmt(r.totalQty)}</b>/${splitFmt(r.qty)}주 · <b>${splitFmt(r.proceeds)}</b>원</span>
        <span>평단 <b class="avg">${splitFmt(r.avg)}</b></span>
        <span>예상 실현손익 <b class="${cls(r.pnl)}">${r.totalQty ? `${splitFmt(r.pnl)}원 (${splitPct(r.pnlRate)})` : '-'}</b></span>
        <span class="dim">도달≥ / 이탈&lt; · 상회 후·하회 후=활성 뒤 반대편 확인 · 익절무장%=이탈만</span>
      </div>`;
  }

  /* ---------- 차트 라인 ---------- */

  clearLines() {
    const s = this.panel.priceLineSeries ? this.panel.priceLineSeries() : this.panel.candleSeries;
    this.lines.forEach((l) => {
      try { if (s) s.removePriceLine(l); } catch (_) { /* ignore */ }
    });
    this.lines = [];
    this.tags = [];
    if (this.tagLayer) this.tagLayer.innerHTML = '';
  }

  onChartDestroyed() {
    if (this.tagRaf) {
      cancelAnimationFrame(this.tagRaf);
      this.tagRaf = null;
    }
    if (this.tagLayer) {
      this.tagLayer.remove();
      this.tagLayer = null;
    }
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
    const s = this.panel.priceLineSeries ? this.panel.priceLineSeries() : this.panel.candleSeries;
    if (!s || !this.open) return;
    const LS = LightweightCharts.LineStyle;
    const r = this.compute();
    r.rows.forEach((row, i) => {
      if (!row.price) return;
      const isMa = row.lv.type === 'ma';
      const color = isMa ? SELL_MA_COLOR : SELL_PRICE_COLOR;
      this.lines.push(s.createPriceLine({
        price: row.price,
        color,
        lineWidth: 1,
        lineStyle: isMa ? LS.Dotted : LS.Dashed,
        axisLabelVisible: false,
      }));
      const side = SELL_SIDES[sellSideOf(row.lv)] || '';
      const label = isMa
        ? `매도${i + 1}차 ${SELL_MAS[row.lv.ma] || row.lv.ma}${row.lv.offset ? ` ${row.lv.offset > 0 ? '+' : ''}${row.lv.offset}%` : ''} ${side} · ${row.qty}주`
        : `매도${i + 1}차 ${row.qty}주 · ${splitFmt(row.price)} ${side}`;
      this.addTag(row.price, label, color);
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
      this.addTag(avg, `평단 · ${splitFmt(avg)}`, SPLIT_AVG_COLOR);
    }
    this.positionTags();
  }

  levelIndexAt(y) {
    const s = this.panel.candleSeries;
    if (!s) return -1;
    let best = -1;
    let bestD = SPLIT_DRAG_PX + 1;
    this.plan.levels.forEach((l, i) => {
      if (l.type === 'ma') return;
      const c = s.priceToCoordinate(l.price);
      if (c == null) return;
      const d = Math.abs(c - y);
      if (d < bestD) { bestD = d; best = i; }
    });
    return best;
  }

  sortPriceLevels() {
    const lv = this.plan.levels;
    const priceRows = lv.filter((l) => l.type !== 'ma').sort((a, b) => a.price - b.price);
    this.plan.levels = [...priceRows, ...lv.filter((l) => l.type === 'ma')];
  }

  bindChart() {
    const host = this.panel.el('chartHost');
    if (!host || this.boundHost === host) return;
    this.boundHost = host;
    const yOf = (e) => e.clientY - host.getBoundingClientRect().top;

    if (!host.dataset.sellBound) {
      host.dataset.sellBound = '1';
      host.addEventListener('pointerdown', (e) => {
        if (!this.open || e.button !== 0 || this.panel.split?.drag) return;
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
          if (!this.panel.split?.drag && this.levelIndexAt(y) >= 0) host.style.cursor = 'ns-resize';
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
        this.sortPriceLevels();
        this.save();
        this.render();
        this.drawLines();
        if (e) e.stopPropagation();
      };
      host.addEventListener('pointerup', end, true);
      host.addEventListener('pointercancel', end, true);

      host.addEventListener('click', (e) => {
        if (!this.open || !e.altKey) return;
        const price = roundTick(this.panel.candleSeries?.coordinateToPrice(yOf(e)));
        if (!price) return;
        this.plan.levels.push({ type: 'price', price, weight: 1, side: 'reach', arm: 'none' });
        this.sortPriceLevels();
        this.save();
        this.render();
        this.drawLines();
      });
    }
  }
}

['ensureTagLayer', 'addTag', 'positionTags', 'startTagLoop', 'setChartInteractive'].forEach((m) => {
  SellPlanner.prototype[m] = SplitPlanner.prototype[m];
});

window.SellPlanner = SellPlanner;
