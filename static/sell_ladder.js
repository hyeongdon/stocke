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
  const MA_OPTS = [5, 10, 15, 20, 60, 120];
  const MAX_LEVELS = 10;
  const CHART_LEN = 150;
  const MA_COLORS = { 5: '#38bdf8', 10: '#3b82f6', 20: '#9ae600', 60: '#6b7280', 120: '#a78bfa' };
  const MA_WIDTH = { 5: 1.35, 10: 1.25, 20: 2.6, 60: 2.6, 120: 1.8 };
  const CHART_MAS = [5, 10, 20, 60];
  const SELL_PRICE_COLOR = '#ec4899';
  const SELL_MA_LINE = '#06b6d4';
  const AVG_COLOR = '#f59e0b';

  const state = {
    data: null,
    selectedId: null,
    draft: null,
    busy: false,
  };

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function fmt(n) {
    if (n == null || n === '' || Number.isNaN(Number(n))) return '—';
    return Number(n).toLocaleString('ko-KR');
  }
  function toast(msg, kind) {
    const el = $('toast');
    if (!el) return;
    el.textContent = msg;
    el.className = 'toast show' + (kind === 'err' ? ' err' : kind === 'ok' ? ' ok' : '');
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { el.className = 'toast'; }, 2800);
  }
  async function api(method, url, body) {
    const opt = { method, headers: {} };
    if (body != null) {
      opt.headers['Content-Type'] = 'application/json';
      opt.body = JSON.stringify(body);
    }
    const res = await fetch(url, opt);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText || '요청 실패');
    return data;
  }
  function plans() {
    return ((state.data && state.data.plans) || []).filter((p) => p.status !== 'DONE');
  }
  function selected() {
    return plans().find((p) => p.id === state.selectedId) || null;
  }
  function sellOn(p) {
    const sl = p && p.sell_ladder;
    return !!(sl && sl.enabled && (sl.levels || []).length);
  }
  function normSide(lv) {
    if (lv && (lv.side === 'reach' || lv.side === 'break')) return lv.side;
    return lv && lv.type === 'ma' ? 'break' : 'reach';
  }
  function normArm(lv) {
    return lv && lv.arm === 'cross' ? 'cross' : 'none';
  }
  function cloneLevels(levels) {
    return (levels || []).map((lv) => (lv.type === 'ma'
      ? { type: 'ma', ma: Number(lv.ma) || 20, offset: Number(lv.offset) || 0, pct: Number(lv.pct) || 0, side: normSide(lv), arm: normArm(lv) }
      : { type: 'price', price: Number(lv.price) || 0, pct: Number(lv.pct) || 0, side: normSide(lv), arm: normArm(lv) }));
  }
  function draftFromPlan(p) {
    if (!p) return null;
    const on = sellOn(p);
    const sl = (p && p.sell_ladder) || {};
    return {
      enabled: on,
      arm_pct: on ? (Number(sl.arm_pct) || 0) : 15,
      levels: on ? cloneLevels(sl.levels) : [],
    };
  }
  function equalizePct(levels) {
    if (!levels.length) return levels;
    const each = Math.floor((10000 / levels.length)) / 100;
    levels.forEach((lv, i) => {
      lv.pct = i === levels.length - 1
        ? Math.round((100 - each * (levels.length - 1)) * 100) / 100
        : each;
    });
    return levels;
  }
  function pctSum(levels) {
    return Math.round((levels || []).reduce((a, lv) => a + (Number(lv.pct) || 0), 0) * 100) / 100;
  }
  function defaultPriceLevels(p) {
    const base = Number(p.avg) || Number(p.price) || 0;
    if (!base) return [];
    const levels = [5, 10, 15].map((pct) => ({
      type: 'price',
      price: Math.round(base * (1 + pct / 100)),
      pct: 0,
      side: 'reach',
      arm: 'none',
    }));
    return equalizePct(levels);
  }
  function defaultMaLevels() {
    return equalizePct([5, 20, 60, 120].map((ma) => ({
      type: 'ma', ma, offset: 0, pct: 0, side: 'break', arm: 'cross',
    })));
  }
  function sideLabel(lv) {
    return normSide(lv) === 'break' ? '이탈' : '도달';
  }
  /** 이탈 매도 → 상회 후, 도달 매도 → 하회 후 */
  function armLabel(lv) {
    if (normArm(lv) !== 'cross') return '없음';
    return normSide(lv) === 'break' ? '상회 후' : '하회 후';
  }
  /** needAbove=true면 목표가까지 상승분, false면 목표가까지 하락분 */
  function fmtRemain(price, target, needAbove) {
    if (!price || !target) return '';
    const diff = needAbove ? (target - price) : (price - target);
    const pct = needAbove ? ((target / price) - 1) * 100 : ((price / target) - 1) * 100;
    const sign = needAbove ? '+' : '-';
    return sign + fmt(Math.round(Math.abs(diff))) + '원 (' + sign + Math.abs(pct).toFixed(1) + '%)';
  }
  /** 상회/하회 무장까지 남은 거리. 활성 이후 반대편에 닿아야 무장. */
  function armProgress(p, lv, rule) {
    if (normArm(lv) !== 'cross') return null;
    const armed = !!(p.sell_arm_flags && p.sell_arm_flags[rule]);
    if (armed) return { ok: true, text: armLabel(lv).replace(' 후', '확인') };
    const target = sellTarget(lv, maMap(p));
    const price = Number(p.price) || 0;
    if (!target || !price) return { ok: false, text: '목표가 없음' };
    const needAbove = normSide(lv) === 'break'; // 이탈매도 → 먼저 위
    if (needAbove) {
      if (price >= target) return { ok: true, text: '상회 도달' };
      return { ok: false, text: '상회까지 ' + fmtRemain(price, target, true) };
    }
    if (price < target) return { ok: true, text: '하회 도달' };
    return { ok: false, text: '하회까지 ' + fmtRemain(price, target, false) };
  }
  function levelRule(i, lv) {
    const side = normSide(lv);
    if (lv.type === 'ma') {
      const off = Number(lv.offset) || 0;
      const body = 'M' + lv.ma + (off ? ((off > 0 ? '+' : '') + off) : '');
      return 'L' + (i + 1) + ':' + body + (side === 'reach' ? '^' : '');
    }
    return 'L' + (i + 1) + ':P' + lv.price + (side === 'break' ? 'v' : '');
  }
  function summarize(sl) {
    if (!sellOn({ sell_ladder: sl })) return '기본 익절';
    const arm = Number(sl.arm_pct) || 0;
    const head = arm > 0 ? ('무장 +' + arm + '% · ') : '';
    return head + (sl.levels || []).map((lv, i) => {
      const armTxt = normArm(lv) === 'cross' ? ('·' + armLabel(lv)) : '';
      if (lv.type === 'ma') {
        const off = Number(lv.offset) || 0;
        return (i + 1) + '차 MA' + lv.ma + (off ? ' ' + (off > 0 ? '+' : '') + off + '%' : '') + ' ' + sideLabel(lv) + armTxt + ' ' + lv.pct + '%';
      }
      return (i + 1) + '차 ' + fmt(lv.price) + ' ' + sideLabel(lv) + armTxt + ' ' + lv.pct + '%';
    }).join(' · ');
  }
  function smaOf(closes, n) {
    const out = new Array(closes.length).fill(null);
    let sum = 0;
    for (let i = 0; i < closes.length; i++) {
      sum += closes[i];
      if (i >= n) sum -= closes[i - n];
      if (i >= n - 1) out[i] = sum / n;
    }
    return out;
  }
  function maMap(p) {
    const m = Object.assign({}, ((p && p.market) || {}).ma || {});
    const closes = (((p && p.market) || {}).chart || {}).close || [];
    [5, 10, 15, 20, 60, 120].forEach((n) => {
      if (m['ma' + n] == null && closes.length >= n) m['ma' + n] = smaOf(closes, n).pop();
    });
    return m;
  }
  function sellTarget(lv, ma) {
    if (!lv) return 0;
    if (lv.type === 'ma') {
      const v = Number(ma['ma' + lv.ma]);
      return v ? v * (1 + (Number(lv.offset) || 0) / 100) : 0;
    }
    return Number(lv.price) || 0;
  }
  function chartSvg(p, levels) {
    const ch = (p.market || {}).chart;
    if (!ch || !ch.close || ch.close.length < 2) return '<div class="hint">차트 데이터 없음</div>';
    const len = Math.min(CHART_LEN, ch.close.length);
    const off = ch.close.length - len;
    const prices = ch.close.slice(off);
    const dates = (ch.dates || []).slice(off);
    const series = {};
    CHART_MAS.forEach((n) => {
      const raw = ch['ma' + n];
      series[n] = (raw && raw.length) ? raw.slice(off) : smaOf(ch.close, n).slice(off);
    });
    const ma = maMap(p);
    const targets = (levels || []).map((lv, i) => {
      const price = sellTarget(lv, ma);
      return { i, lv, price };
    }).filter((t) => t.price > 0);
    const all = prices.concat(targets.map((t) => t.price));
    if (p.avg && p.hold) all.push(p.avg);
    const lo = Math.min.apply(null, all) * 0.98;
    const hi = Math.max.apply(null, all) * 1.02;
    const W = 640, H = 250, padL = 8, padR = 78, padT = 10, padB = 30;
    const x = (i) => padL + (i / (len - 1)) * (W - padL - padR);
    const y = (v) => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB);
    const hline = (v, color, dash, label, left) => {
      let t = '<line x1="' + padL + '" x2="' + (W - padR) + '" y1="' + y(v) + '" y2="' + y(v) + '" stroke="' + color + '" stroke-width="1.2"' + (dash ? ' stroke-dasharray="' + dash + '"' : '') + ' opacity="0.9"/>';
      t += left
        ? '<text x="' + (padL + 4) + '" y="' + (y(v) - 3) + '" style="fill:' + color + '">' + label + '</text>'
        : '<text x="' + (W - padR + 4) + '" y="' + (y(v) + 3) + '" style="fill:' + color + '">' + label + '</text>';
      return t;
    };
    let svg = '<svg class="ld-chart" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none">';
    targets.forEach((t) => {
      const isMa = t.lv.type === 'ma';
      const color = isMa ? SELL_MA_LINE : SELL_PRICE_COLOR;
      const side = sideLabel(t.lv);
      const label = isMa
        ? ((t.i + 1) + '차 MA' + t.lv.ma + (t.lv.offset ? ' ' + (t.lv.offset > 0 ? '+' : '') + t.lv.offset + '%' : '') + ' ' + side)
        : ((t.i + 1) + '차 ' + fmt(t.price) + ' ' + side);
      svg += hline(t.price, color, normSide(t.lv) === 'break' ? '2 3' : '5 4', label);
    });
    if (p.avg && p.hold) svg += hline(p.avg, AVG_COLOR, '4 4', '평단 ' + fmt(p.avg), true);
    CHART_MAS.forEach((n) => {
      const s = series[n];
      const d = s.map((v, i) => v == null ? '' : (i && s[i - 1] != null ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1)).join(' ');
      const thick = n === 20 || n === 60;
      svg += '<path d="' + d + '" fill="none" stroke="' + MA_COLORS[n] + '" stroke-width="' + MA_WIDTH[n] + '" vector-effect="non-scaling-stroke" opacity="' + (thick ? '1' : '0.9') + '"/>';
    });
    svg += '<path d="' + prices.map((v, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1)).join(' ') + '" fill="none" stroke="var(--text)" stroke-width="1.6"/>';
    svg += '<circle cx="' + x(len - 1) + '" cy="' + y(prices[len - 1]) + '" r="3.5" fill="var(--text)"/>';
    const axisY = H - padB;
    svg += '<line x1="' + padL + '" x2="' + (W - padR) + '" y1="' + axisY + '" y2="' + axisY + '" stroke="var(--border)" stroke-width="1"/>';
    const tickCount = Math.min(5, len);
    Array.from({ length: tickCount }, (_, k) => Math.round((k * (len - 1)) / (tickCount - 1))).forEach((i) => {
      const tickX = x(i);
      const date = String(dates[i] || '');
      const label = date.length >= 10 ? date.slice(2, 4) + '.' + date.slice(5, 7) + '.' + date.slice(8, 10) : date;
      svg += '<line x1="' + tickX + '" x2="' + tickX + '" y1="' + axisY + '" y2="' + (axisY + 4) + '" stroke="var(--muted)" stroke-width="1"/>';
      if (label) svg += '<text x="' + tickX + '" y="' + (H - 6) + '" text-anchor="middle">' + esc(label) + '</text>';
    });
    return svg + '</svg>';
  }

  function renderList() {
    const tbody = document.querySelector('#planTable tbody');
    const cards = $('planCards');
    const list = plans();
    $('listHint').textContent = list.length
      ? ('진행 중 ' + list.length + '개 · 선택 후 매도사다리를 연결하거나 수정합니다')
      : '진행 중인 매수사다리 계획이 없습니다. 먼저 매수사다리 탭에서 등록하세요.';
    if (!list.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="hint">진행 중인 계획이 없습니다</td></tr>';
      if (cards) cards.innerHTML = '<div class="hint" style="padding:12px">진행 중인 계획이 없습니다</div>';
      return;
    }
    tbody.innerHTML = list.map((p) => {
      const on = sellOn(p);
      const active = p.id === state.selectedId ? ' class="selected"' : '';
      return '<tr data-id="' + esc(p.id) + '"' + active + '>'
        + '<td><b>' + esc(p.name || p.code) + '</b> <span class="hint">' + esc(p.code) + '</span></td>'
        + '<td><span class="ld-badge ' + esc(p.status) + '">' + esc(STATUS_LABEL[p.status] || p.status) + '</span></td>'
        + '<td class="' + (on ? 'sl-row-on' : 'hint') + '">' + (on ? ('ON · ' + (p.sell_ladder.levels || []).length + '차') : 'OFF · 기본익절') + '</td>'
        + '<td class="r">' + fmt(p.price) + '</td>'
        + '<td class="r">' + (p.avg ? fmt(p.avg) : '—') + '</td>'
        + '<td class="r">' + (p.hold ? fmt(p.hold) + '주' : '—') + '</td>'
        + '</tr>';
    }).join('');
    if (cards) {
      cards.innerHTML = list.map((p) => {
        const on = sellOn(p);
        return '<article class="ld-plan-card' + (p.id === state.selectedId ? ' selected' : '') + '" data-id="' + esc(p.id) + '">'
          + '<div class="ld-plan-card-top">'
          + '<div class="ld-plan-card-name"><b>' + esc(p.name || p.code) + '</b><span class="code">' + esc(p.code) + '</span></div>'
          + '<div class="ld-plan-card-right">'
          + '<span class="ld-badge ' + esc(p.status) + '">' + esc(STATUS_LABEL[p.status] || p.status) + '</span>'
          + '<div class="ld-plan-card-pnl ' + (on ? 'sl-row-on' : 'hint') + '">' + (on ? ('ON · ' + (p.sell_ladder.levels || []).length + '차') : '기본익절') + '</div>'
          + '</div></div>'
          + '<div class="ld-plan-card-meta">'
          + '<div><div class="k">현재가 · 평단</div><div class="v">' + fmt(p.price) + (p.avg ? ' · ' + fmt(p.avg) : '') + '</div></div>'
          + '<div><div class="k">보유</div><div class="v">' + (p.hold ? fmt(p.hold) + '주' : '—') + '</div></div>'
          + '</div></article>';
      }).join('');
    }
  }

  function isNarrow() {
    return window.matchMedia('(max-width: 768px)').matches;
  }

  function levelFields(p, lv, i) {
    const isMa = lv.type === 'ma';
    const side = normSide(lv);
    const arm = normArm(lv);
    const crossLabel = side === 'break' ? '상회 후' : '하회 후';
    const prog = armProgress(p, lv, levelRule(i, lv));
    const status = (p.sell_flags && p.sell_flags[levelRule(i, lv)])
      ? '<span class="ok">매도완료</span>'
      : (prog
        ? ('<span class="' + (prog.ok ? 'ok' : 'hint') + '">' + esc(prog.text) + '</span>')
        : '<span class="hint">—</span>');
    const maOpts = MA_OPTS.map((n) =>
      '<option value="' + n + '"' + (Number(lv.ma) === n ? ' selected' : '') + '>MA' + n + '</option>'
    ).join('');
    const typeSel = '<select data-k="type" data-i="' + i + '">'
      + '<option value="price"' + (!isMa ? ' selected' : '') + '>가격</option>'
      + '<option value="ma"' + (isMa ? ' selected' : '') + '>이평</option>'
      + '</select>';
    const cond = isMa
      ? ('<select class="sl-ma" data-k="ma" data-i="' + i + '">' + maOpts + '</select>'
        + '<input class="sl-off" data-k="offset" data-i="' + i + '" type="number" step="0.5" value="' + (Number(lv.offset) || 0) + '">'
        + '<span class="sl-unit">%</span>')
      : ('<input class="sl-price" data-k="price" data-i="' + i + '" inputmode="numeric" value="' + (lv.price ? fmt(lv.price) : '') + '">'
        + '<span class="sl-unit">원</span>');
    const sideSel = '<select data-k="side" data-i="' + i + '" title="도달=이상 / 이탈=미만">'
      + '<option value="reach"' + (side === 'reach' ? ' selected' : '') + '>도달</option>'
      + '<option value="break"' + (side === 'break' ? ' selected' : '') + '>이탈</option>'
      + '</select>';
    const armSel = '<select data-k="arm" data-i="' + i + '" title="이탈 매도=먼저 위로(상회 후) / 도달 매도=먼저 아래로(하회 후). 활성 이후 기준">'
      + '<option value="none"' + (arm === 'none' ? ' selected' : '') + '>없음</option>'
      + '<option value="cross"' + (arm === 'cross' ? ' selected' : '') + '>' + crossLabel + '</option>'
      + '</select>';
    const pctIn = '<input class="sl-pct" data-k="pct" data-i="' + i + '" type="number" min="0.01" max="100" step="0.01" value="' + (Number(lv.pct) || 0) + '">'
      + '<span class="sl-unit">%</span>';
    return { typeSel, cond, sideSel, armSel, pctIn, status };
  }

  function levelRowHtml(p, lv, i) {
    const f = levelFields(p, lv, i);
    return '<tr>'
      + '<td>' + (i + 1) + '차</td>'
      + '<td>' + f.typeSel + '</td>'
      + '<td class="cond">' + f.cond + '</td>'
      + '<td>' + f.sideSel + '</td>'
      + '<td>' + f.armSel + '</td>'
      + '<td class="r">' + f.pctIn + '</td>'
      + '<td class="sl-status">' + f.status + '</td>'
      + '<td><button type="button" class="btn sm" data-del="' + i + '" title="삭제">×</button></td>'
      + '</tr>';
  }

  function levelCardHtml(p, lv, i) {
    const f = levelFields(p, lv, i);
    const isMa = lv.type === 'ma';
    return '<article class="sl-level-card">'
      + '<div class="sl-level-card-top">'
      + '<b>' + (i + 1) + '차</b>'
      + '<span class="sl-level-card-badge">' + (isMa ? '이평' : '가격') + '</span>'
      + '<span class="sl-status">' + f.status + '</span>'
      + '<button type="button" class="btn sm sl-level-del" data-del="' + i + '" title="삭제">×</button>'
      + '</div>'
      + '<div class="sl-level-card-grid">'
      + '<label>방식' + f.typeSel + '</label>'
      + '<label class="sl-level-cond">조건<span class="sl-level-cond-row">' + f.cond + '</span></label>'
      + '<label>방향' + f.sideSel + '</label>'
      + '<label>확인' + f.armSel + '</label>'
      + '<label>비중<span class="sl-level-cond-row">' + f.pctIn + '</span></label>'
      + '</div>'
      + '</article>';
  }

  function levelsEditorHtml(p, levels) {
    if (!levels.length) {
      return '<div class="sl-levels-wrap"><div class="sl-levels hint">차수를 추가하세요</div></div>';
    }
    if (isNarrow()) {
      return '<div class="sl-levels-wrap"><div class="sl-levels sl-level-cards">'
        + levels.map((lv, i) => levelCardHtml(p, lv, i)).join('')
        + '</div></div>';
    }
    return '<div class="sl-levels-wrap"><table class="ld-table sl-levels"><thead>'
      + '<tr><th>차수</th><th>방식</th><th>조건</th><th>방향</th><th>확인</th><th class="r">비중</th><th>상태</th><th></th></tr>'
      + '</thead><tbody>'
      + levels.map((lv, i) => levelRowHtml(p, lv, i)).join('')
      + '</tbody></table></div>';
  }

  function renderDetail() {
    const p = selected();
    const title = $('detailTitle');
    const body = $('detailBody');
    const actions = $('detailActions');
    if (!p) {
      title.textContent = '종목을 선택하세요';
      actions.innerHTML = '';
      body.innerHTML = '<div class="sl-empty">왼쪽에서 진행 중인 매수사다리 계획을 고르면 매도사다리를 설정할 수 있습니다.</div>';
      return;
    }
    if (!state.draft) state.draft = draftFromPlan(p);
    const d = state.draft;
    title.innerHTML = esc(p.name || p.code) + ' <span class="hint">' + esc(p.code) + '</span>'
      + ' <span class="ld-badge ' + esc(p.status) + '">' + esc(STATUS_LABEL[p.status] || p.status) + '</span>';
    actions.innerHTML =
      '<button type="button" class="btn sm primary" data-act="save"' + (state.busy ? ' disabled' : '') + '>저장</button>'
      + (sellOn(p) ? '<button type="button" class="btn sm" data-act="unlink"' + (state.busy ? ' disabled' : '') + '>연결 해제</button>' : '');

    if (!d.enabled) {
      body.innerHTML =
        '<div class="sl-editor">'
        + '<div class="sl-editor-head">'
        + '<label class="ld-check"><input type="checkbox" id="slEnabled"> 매도사다리 사용</label>'
        + '<span class="hint">끄면 기본 익절(+15% 무장 후 MA10/15/20)을 씁니다</span>'
        + '</div>'
        + '<div class="sl-default-box">'
        + '현재 <b>기본 익절</b> 모드입니다. 매도사다리를 켜면 MA 이탈(기본) · 가격 도달 차수로 매도합니다.<br>'
        + '· <b>도달</b>: 현재가 ≥ 목표가 · <b>이탈</b>: 현재가 &lt; 목표가<br>'
        + '· <b>익절 무장%</b>: 이탈 차수는 최고 수익률이 이 % 이상일 때만 매도<br>'
        + '· <b>상회 후</b>(이탈 매도): 켠 뒤 목표가 위에 한 번 닿아야 무장 · <b>하회 후</b>(도달 매도): 먼저 아래로<br>'
        + '· 가격 차수=장중 10분, MA 차수=15:05 · 텔레그램 3분 유예'
        + '</div>'
        + '</div>';
      $('slEnabled').onchange = () => {
        d.enabled = true;
        d.arm_pct = d.arm_pct > 0 ? d.arm_pct : 15;
        if (!d.levels.length) d.levels = defaultMaLevels();
        renderDetail();
      };
      return;
    }

    const sum = pctSum(d.levels);
    const sumOk = Math.abs(sum - 100) < 0.05;
    const armPct = Number(d.arm_pct) || 0;
    body.innerHTML =
      '<div class="sl-editor">'
      + '<div class="sl-editor-head">'
      + '<label class="ld-check"><input type="checkbox" id="slEnabled" checked> 매도사다리 사용</label>'
      + '<label class="sl-arm-pct">익절 무장 <input id="slArmPct" type="number" min="0" max="100" step="0.5" value="' + armPct + '"><span class="sl-unit">% (이탈 차수)</span></label>'
      + '<span class="hint">평단 ' + (p.avg ? fmt(p.avg) : '—') + ' · 현재가 ' + fmt(p.price) + (p.hold ? (' · 보유 ' + fmt(p.hold) + '주') : '')
      + (p.peak_pnl_pct != null ? (' · 최고 ' + Number(p.peak_pnl_pct).toFixed(1) + '%') : '') + '</span>'
      + '</div>'
      + '<div class="sl-tools">'
      + '<button type="button" class="btn sm" data-tool="add-price">+ 가격 차수</button>'
      + '<button type="button" class="btn sm" data-tool="add-ma">+ MA 차수</button>'
      + '<button type="button" class="btn sm" data-tool="preset-price">가격 3단</button>'
      + '<button type="button" class="btn sm" data-tool="preset-ma">MA 4단</button>'
      + '<button type="button" class="btn sm" data-tool="equal">비중 균등</button>'
      + '<span class="hint">최대 ' + MAX_LEVELS + '차 · MA 기본=이탈+상회 후</span>'
      + '</div>'
      + levelsEditorHtml(p, d.levels)
      + '<div class="sl-sum">'
      + '<span>비중 합계 <b class="' + (sumOk ? 'ok' : 'bad') + '">' + sum + '%</b>' + (sumOk ? '' : ' (100% 필요)') + '</span>'
      + '<span class="hint">' + esc(summarize({ enabled: true, arm_pct: armPct, levels: d.levels })) + '</span>'
      + '</div>'
      + '<div class="sl-chart-wrap">' + chartSvg(p, d.levels)
      + '<div class="sl-chart-legend">'
      + '<span style="color:var(--text)">종가</span>'
      + '<span style="color:' + MA_COLORS[5] + '">5일선</span>'
      + '<span style="color:' + MA_COLORS[10] + '">10일선</span>'
      + '<span style="color:' + MA_COLORS[20] + '"><b>20일선</b></span>'
      + '<span style="color:' + MA_COLORS[60] + '"><b>60일선</b></span>'
      + '<span style="color:' + AVG_COLOR + '">평단</span>'
      + '<span style="color:' + SELL_PRICE_COLOR + '">가격</span>'
      + '<span style="color:' + SELL_MA_LINE + '">이평</span>'
      + '<span>· 최근 ' + CHART_LEN + '거래일</span>'
      + '</div></div>'
      + '</div>';

    $('slEnabled').onchange = () => {
      d.enabled = false;
      d.levels = [];
      renderDetail();
    };
    const armEl = $('slArmPct');
    if (armEl) {
      armEl.onchange = armEl.oninput = () => {
        d.arm_pct = Math.max(0, Number(armEl.value) || 0);
      };
    }
  }

  function readLevelInputs() {
    const d = state.draft;
    if (!d || !d.enabled) return;
    const armEl = $('slArmPct');
    if (armEl) d.arm_pct = Math.max(0, Number(armEl.value) || 0);
    document.querySelectorAll('.sl-levels [data-i][data-k]').forEach((el) => {
      const i = Number(el.dataset.i);
      const k = el.dataset.k;
      const lv = d.levels[i];
      if (!lv) return;
      if (k === 'type') {
        const next = el.value === 'ma' ? 'ma' : 'price';
        if (lv.type === next) return;
        d.levels[i] = next === 'ma'
          ? { type: 'ma', ma: 20, offset: 0, pct: lv.pct || 0, side: 'break', arm: 'cross' }
          : { type: 'price', price: 0, pct: lv.pct || 0, side: 'reach', arm: 'none' };
        return;
      }
      if (k === 'ma') lv.ma = Number(el.value) || 20;
      else if (k === 'offset') lv.offset = Number(el.value) || 0;
      else if (k === 'pct') lv.pct = Number(el.value) || 0;
      else if (k === 'price') lv.price = Number(String(el.value).replace(/,/g, '')) || 0;
      else if (k === 'side') lv.side = el.value === 'break' ? 'break' : 'reach';
      else if (k === 'arm') lv.arm = el.value === 'cross' ? 'cross' : 'none';
    });
  }

  function onTool(tool) {
    const p = selected();
    const d = state.draft;
    if (!p || !d || !d.enabled) return;
    readLevelInputs();
    if (tool === 'add-price') {
      if (d.levels.length >= MAX_LEVELS) return toast('최대 ' + MAX_LEVELS + '차까지입니다', 'err');
      const prices = d.levels.filter((l) => l.type === 'price').map((l) => l.price).filter(Boolean);
      const top = prices.length ? Math.max(...prices) : (Number(p.avg) || Number(p.price) || 0);
      const price = top ? Math.round(top * 1.05) : 0;
      d.levels.push({ type: 'price', price, pct: 0, side: 'reach', arm: 'none' });
      equalizePct(d.levels);
    } else if (tool === 'add-ma') {
      if (d.levels.length >= MAX_LEVELS) return toast('최대 ' + MAX_LEVELS + '차까지입니다', 'err');
      d.levels.push({ type: 'ma', ma: 20, offset: 0, pct: 0, side: 'break', arm: 'cross' });
      equalizePct(d.levels);
    } else if (tool === 'preset-price') {
      d.levels = defaultPriceLevels(p);
      if (!d.levels.length) return toast('평단 또는 현재가가 없어 가격 3단을 만들 수 없습니다', 'err');
    } else if (tool === 'preset-ma') {
      d.levels = defaultMaLevels();
      if (!(Number(d.arm_pct) > 0)) d.arm_pct = 15;
    } else if (tool === 'equal') {
      if (!d.levels.length) return;
      equalizePct(d.levels);
    } else {
      return;
    }
    renderDetail();
  }

  async function saveDraft(unlink) {
    const p = selected();
    if (!p || state.busy) return;
    readLevelInputs();
    const d = state.draft || draftFromPlan(p);
    let payload;
    if (unlink || !d.enabled) {
      if (!confirm((p.name || p.code) + ' 매도사다리 연결을 해제하고 기본 익절(S1~S3)로 되돌릴까요?')) return;
      payload = { enabled: false, arm_pct: 0, levels: [] };
    } else {
      const levels = cloneLevels(d.levels).filter((lv) =>
        (lv.type === 'ma' ? MA_OPTS.includes(Number(lv.ma)) : lv.price > 0) && lv.pct > 0
      );
      if (!levels.length) return toast('유효한 매도 차수가 없습니다 (가격/이평 + 비중%)', 'err');
      const sum = pctSum(levels);
      if (Math.abs(sum - 100) >= 0.5) return toast('비중 합계가 100%여야 합니다 (현재 ' + sum + '%)', 'err');
      levels[levels.length - 1].pct = Math.round((100 - levels.slice(0, -1).reduce((a, l) => a + l.pct, 0)) * 100) / 100;
      const armPct = Math.max(0, Number(d.arm_pct) || 0);
      const desc = (armPct ? ('익절 무장 +' + armPct + '% (이탈 차수)\n') : '')
        + levels.map((lv, i) =>
          (i + 1) + '차 ' + (lv.type === 'ma'
            ? ('MA' + lv.ma + (lv.offset ? (' ' + (lv.offset > 0 ? '+' : '') + lv.offset + '%') : ''))
            : (fmt(lv.price) + '원'))
          + ' ' + sideLabel(lv)
          + (normArm(lv) === 'cross' ? (' ·' + armLabel(lv)) : '')
          + ' → ' + lv.pct + '%'
        ).join('\n');
      if (!confirm((p.name || p.code) + ' 매도사다리를 저장합니다.\n\n' + desc + '\n\n실제 매도 주문에 사용됩니다. 진행할까요?')) return;
      payload = { enabled: true, arm_pct: armPct, levels };
    }
    state.busy = true;
    renderDetail();
    try {
      const res = await api('PUT', '/api/ladder/plans/' + encodeURIComponent(p.id) + '/sell-ladder', payload);
      p.sell_ladder = res.sell_ladder;
      state.draft = draftFromPlan(p);
      toast(payload.enabled ? '매도사다리를 저장했습니다' : '매도사다리 연결을 해제했습니다', 'ok');
      renderList();
      renderDetail();
    } catch (e) {
      toast(e.message || '저장 실패', 'err');
      renderDetail();
    } finally {
      state.busy = false;
    }
  }

  function selectPlan(id, opts) {
    if (state.selectedId === id && !(opts && opts.force)) return;
    state.selectedId = id;
    state.draft = draftFromPlan(selected());
    renderList();
    renderDetail();
    if (opts && opts.scrollDetail && window.innerWidth <= 768) {
      const card = $('detailCard');
      if (card) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  async function load() {
    try {
      state.data = await api('GET', '/api/ladder/state');
      if (state.selectedId && !selected()) {
        state.selectedId = null;
        state.draft = null;
      }
      if (!state.selectedId && plans().length) {
        state.selectedId = plans()[0].id;
        state.draft = draftFromPlan(selected());
      }
      renderList();
      renderDetail();
    } catch (e) {
      toast(e.message || '불러오기 실패', 'err');
      document.querySelector('#planTable tbody').innerHTML =
        '<tr><td colspan="6" class="hint">불러오기 실패: ' + esc(e.message) + '</td></tr>';
    }
  }

  function bind() {
    const onPick = (e) => {
      const row = e.target.closest('[data-id]');
      if (row) selectPlan(row.dataset.id, { scrollDetail: !!e.target.closest('.ld-plan-card') });
    };
    document.querySelector('#planTable tbody').addEventListener('click', onPick);
    const planCards = $('planCards');
    if (planCards) planCards.addEventListener('click', onPick);
    $('detailActions').addEventListener('click', (e) => {
      const btn = e.target.closest('[data-act]');
      if (!btn) return;
      if (btn.dataset.act === 'save') saveDraft(false);
      else if (btn.dataset.act === 'unlink') saveDraft(true);
    });
    $('detailBody').addEventListener('click', (e) => {
      const tool = e.target.closest('[data-tool]');
      if (tool) return onTool(tool.dataset.tool);
      const del = e.target.closest('[data-del]');
      if (del && state.draft) {
        readLevelInputs();
        state.draft.levels.splice(Number(del.dataset.del), 1);
        equalizePct(state.draft.levels);
        renderDetail();
      }
    });
    $('detailBody').addEventListener('change', (e) => {
      if (!e.target.closest('.sl-levels')) return;
      readLevelInputs();
      if (e.target.dataset.k === 'type' || e.target.dataset.k === 'side' || e.target.dataset.k === 'arm') {
        renderDetail();
      } else {
        const sumEl = document.querySelector('.sl-sum b');
        if (sumEl && state.draft) {
          const sum = pctSum(state.draft.levels);
          const ok = Math.abs(sum - 100) < 0.05;
          sumEl.textContent = sum + '%';
          sumEl.className = ok ? 'ok' : 'bad';
        }
      }
    });
    $('btnRefresh').onclick = () => load();
    let wasNarrow = isNarrow();
    window.addEventListener('resize', () => {
      const now = isNarrow();
      if (now === wasNarrow) return;
      wasNarrow = now;
      if (state.draft && state.draft.enabled) {
        readLevelInputs();
        renderDetail();
      }
    });
  }

  bind();
  load();
})();
