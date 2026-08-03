(() => {
  const $ = (id) => document.getElementById(id);
  let chart;
  let cache = [];
  let grain = 'mti';

  const GRAIN_HINT = {
    mti: '산업코드(세분) · 동일 테마를 HS 단위로 나눔',
    tag: '테마 태그 · 하위 산업코드 합산',
    hs: 'HS 4자리 품목 · 바스켓에 등록된 코드',
  };

  function wonUsd(n) {
    if (n == null || Number.isNaN(Number(n))) return '—';
    const v = Number(n);
    const abs = Math.abs(v);
    if (abs >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
    if (abs >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
    if (abs >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
    return v.toFixed(0);
  }

  function pct(n) {
    if (n == null || Number.isNaN(Number(n))) return '—';
    const v = Number(n);
    const cls = v > 0 ? 'ind-pos' : (v < 0 ? 'ind-neg' : '');
    const sign = v > 0 ? '+' : '';
    return `<span class="${cls}">${sign}${v.toFixed(1)}%</span>`;
  }

  function rowLabel(it) {
    if (grain === 'mti') {
      return `<div class="ind-name">${esc(it.mti_name || it.label || it.grain_key)}</div>
        <div class="ind-sub"><code>${esc(it.mti_code || it.grain_key)}</code> · ${esc(it.tag || '—')}</div>`;
    }
    if (grain === 'hs') {
      const tags = (it.tags || it.meta?.tags || []).join(', ') || '—';
      return `<div class="ind-name">HS ${esc(it.hs_code || it.grain_key)}</div>
        <div class="ind-sub">${esc(it.label || '')} · ${esc(tags)}</div>`;
    }
    const mtis = (it.meta?.mti_codes || []).join(', ');
    return `<div class="ind-name">${esc(it.tag || it.label || it.grain_key)}</div>
      <div class="ind-sub">${mtis ? `산업 ${esc(mtis)}` : '테마 합산'}</div>`;
  }

  function headerCols() {
    if (grain === 'mti') return '산업코드 / 명';
    if (grain === 'hs') return 'HS 품목';
    return '테마 태그';
  }

  function renderTable(items, latestPeriod) {
    const el = $('tradeTableBody');
    if (!items.length) {
      el.innerHTML = '<div class="ind-empty">데이터가 없습니다. 배치 재집계 후 새로고침하세요.</div>';
      return;
    }
    const rows = items.map((it, idx) => {
      const L = it.latest || {};
      return `<tr data-idx="${idx}">
        <td>${rowLabel(it)}</td>
        <td class="num">${wonUsd(L.exp_usd)}</td>
        <td class="num">${pct(L.exp_yoy)}</td>
        <td class="num">${pct(L.exp_mom)}</td>
        <td class="num">${wonUsd(L.imp_usd)}</td>
        <td class="num">${pct(L.imp_yoy)}</td>
      </tr>`;
    }).join('');
    el.innerHTML = `<table class="ind-tbl"><thead><tr>
      <th>${headerCols()}</th><th class="num">수출</th><th class="num">YoY</th><th class="num">MoM</th>
      <th class="num">수입</th><th class="num">수입YoY</th>
    </tr></thead><tbody>${rows}</tbody></table>
    <div class="hint" style="margin-top:8px;">기준월 ${esc(latestPeriod || '—')} · 행 클릭 → 우측 시계열</div>`;
    el.querySelectorAll('tr[data-idx]').forEach((tr) => {
      tr.addEventListener('click', () => {
        el.querySelectorAll('tr').forEach((x) => x.classList.remove('active'));
        tr.classList.add('active');
        const i = Number(tr.getAttribute('data-idx'));
        drawChart(cache[i]);
      });
    });
    const first = el.querySelector('tr[data-idx="0"]');
    if (first) first.click();
  }

  function drawChart(item) {
    if (!item) return;
    const labels = (item.series || []).map((s) => s.period_yyyymm);
    const exp = (item.series || []).map((s) => s.exp_usd);
    const imp = (item.series || []).map((s) => s.imp_usd);
    const title = item.label || item.mti_name || item.tag || item.grain_key || '';
    const code = item.mti_code || item.hs_code || '';
    $('chartHint').textContent = `${title}${code ? ` (${code})` : ''} · 월별 수출/수입 (USD)`;
    const ctx = $('tradeChart').getContext('2d');
    if (chart) chart.destroy();
    chart = new Chart(ctx, {
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: '수출', data: exp, borderColor: '#38bdf8', tension: 0.2, pointRadius: 2 },
          { label: '수입', data: imp, borderColor: '#fbbf24', tension: 0.2, pointRadius: 2 },
        ],
      },
      options: {
        responsive: true,
        plugins: { legend: { labels: { color: '#cbd5e1' } } },
        scales: {
          x: { ticks: { color: '#94a3b8', maxRotation: 0, autoSkip: true, maxTicksLimit: 8 } },
          y: { ticks: { color: '#94a3b8', callback: (v) => wonUsd(v) } },
        },
      },
    });
  }

  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function setGrain(next) {
    grain = next;
    document.querySelectorAll('.ind-grain-btn').forEach((btn) => {
      btn.classList.toggle('active', btn.getAttribute('data-grain') === grain);
    });
    load();
  }

  async function load() {
    $('tradeHint').textContent = '조회 중…';
    try {
      const r = await fetch(`/indicators/trade/latest?grain=${encodeURIComponent(grain)}&limit_tags=80`);
      const d = await r.json();
      if (!d.success) throw new Error(d.detail || 'fail');
      cache = d.items || [];
      $('latestPeriod').textContent = d.latest_period || '—';
      $('tradeHint').textContent = `${GRAIN_HINT[grain] || ''} · ${cache.length}건`;
      renderTable(cache, d.latest_period);
    } catch (e) {
      $('tradeHint').textContent = '조회 실패';
      $('tradeTableBody').innerHTML = `<div class="ind-empty">오류: ${esc(e.message || e)}</div>`;
    }
  }

  document.querySelectorAll('.ind-grain-btn').forEach((btn) => {
    btn.addEventListener('click', () => setGrain(btn.getAttribute('data-grain')));
  });
  $('btnRefresh')?.addEventListener('click', load);
  load();
})();
