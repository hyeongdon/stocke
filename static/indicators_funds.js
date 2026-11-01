(() => {
  const $ = (id) => document.getElementById(id);
  let rows = [];
  let range = 250;
  let fundChart;
  let forcedChart;

  const jo = (v) => (v == null ? null : v / 1e6);
  const eok = (v) => (v == null ? null : v / 100);

  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function fmt(n, digits) {
    if (n == null || Number.isNaN(Number(n))) return '—';
    return Number(n).toLocaleString('ko-KR', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }

  function delta(n, digits, unit) {
    if (n == null || Number.isNaN(Number(n))) return '<span>—</span>';
    const cls = n > 0 ? 'ind-pos' : (n < 0 ? 'ind-neg' : '');
    const sign = n > 0 ? '+' : '';
    return `<span class="${cls}">${sign}${fmt(n, digits)}${unit}</span>`;
  }

  function lastWith(key, skip = 0) {
    let seen = 0;
    for (let i = rows.length - 1; i >= 0; i -= 1) {
      if (rows[i][key] != null) {
        if (seen === skip) return rows[i];
        seen += 1;
      }
    }
    return null;
  }

  function diff(key) {
    const a = lastWith(key);
    const b = lastWith(key, 1);
    if (!a || !b) return null;
    return a[key] - b[key];
  }

  function tile(label, date, value, sub, tone) {
    return `<div class="ind-fund-tile tone-${tone}">
      <div class="ind-fund-tile-head">
        <div class="lbl">${esc(label)}</div>
        <div class="ind-fund-date">${esc(date || '—')}</div>
      </div>
      <div class="val">${value}</div>
      <div class="chg">${sub}</div>
    </div>`;
  }

  function renderTiles() {
    const dep = lastWith('deposit');
    const cr = lastWith('credit');
    const unc = lastWith('uncollected');
    const fs = lastWith('forced_sale_pct');
    if (!dep && !cr) {
      $('fundTiles').innerHTML = '<div class="ind-empty">데이터가 없습니다.</div>';
      return;
    }
    const d = jo(diff('deposit'));
    const c = jo(diff('credit'));
    const u = jo(diff('uncollected'));
    $('fundTiles').innerHTML = [
      tile(
        '투자자예탁금',
        dep?.date,
        `${fmt(jo(dep?.deposit), 2)}조`,
        `전일 대비 ${delta(d, 2, '조')}`,
        'deposit',
      ),
      tile(
        '신용융자 잔고',
        cr?.date,
        `${fmt(jo(cr?.credit), 2)}조`,
        `${delta(c, 2, '조')} · 코스피 ${fmt(jo(cr?.credit_kospi), 1)} / 코스닥 ${fmt(jo(cr?.credit_kosdaq), 1)}`,
        'credit',
      ),
      tile(
        '위탁매매 미수금',
        unc?.date,
        `${fmt(jo(unc?.uncollected), 2)}조`,
        `전일 대비 ${delta(u, 2, '조')}`,
        'uncollected',
      ),
      tile(
        '반대매매 비중',
        fs?.date,
        `${fmt(fs?.forced_sale_pct, 1)}%`,
        `반대매매 ${fmt(eok(fs?.forced_sale), 0)}억원`,
        'forced',
      ),
    ].join('');
  }

  function visibleRows() {
    return range > 0 ? rows.slice(-range) : rows;
  }

  function chartColor(name, fallback) {
    return getComputedStyle(document.body).getPropertyValue(name).trim() || fallback;
  }

  function renderCharts() {
    const view = visibleRows();
    const labels = view.map((r) => r.date.slice(2));
    const axisTicks = { color: chartColor('--ind-chart-label', '#94a3b8'), font: { size: 10 } };
    const gridColor = chartColor('--ind-chart-grid', 'rgba(255,255,255,0.06)');
    const depositColor = chartColor('--ind-deposit', '#38bdf8');
    const creditColor = chartColor('--ind-credit', '#f472b6');
    const forcedColor = chartColor('--ind-forced', '#f87171');
    const forcedFill = chartColor('--ind-forced-fill', 'rgba(248,113,113,.42)');
    const uncollectedColor = chartColor('--ind-uncollected', '#fbbf24');

    if (fundChart) fundChart.destroy();
    fundChart = new Chart($('fundChart').getContext('2d'), {
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: '투자자예탁금(조)', data: view.map((r) => jo(r.deposit)), borderColor: depositColor, yAxisID: 'y', tension: 0.28, borderWidth: 2.5, pointRadius: 0, pointHoverRadius: 3, spanGaps: true },
          { label: '신용융자(조)', data: view.map((r) => jo(r.credit)), borderColor: creditColor, yAxisID: 'y1', tension: 0.28, borderWidth: 2.5, pointRadius: 0, pointHoverRadius: 3, spanGaps: true },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            position: 'top',
            align: 'start',
            labels: { color: chartColor('--text-secondary', '#cbd5e1'), usePointStyle: true, pointStyle: 'line', boxWidth: 22, boxHeight: 8, padding: 16, font: { size: 10, weight: '600' } },
          },
          tooltip: { backgroundColor: chartColor('--ind-tooltip-bg', '#131720'), titleColor: chartColor('--text', '#e8edf5'), bodyColor: chartColor('--text-secondary', '#b4bcc9'), borderColor: chartColor('--border', '#252b38'), borderWidth: 1, padding: 10, displayColors: true, usePointStyle: true },
        },
        scales: {
          x: { border: { display: false }, ticks: { ...axisTicks, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 }, grid: { display: false } },
          y: { position: 'left', border: { display: false }, ticks: axisTicks, grid: { color: gridColor }, title: { display: true, text: '예탁금', color: depositColor, font: { size: 10, weight: '600' } } },
          y1: { position: 'right', border: { display: false }, ticks: axisTicks, grid: { drawOnChartArea: false }, title: { display: true, text: '신용융자', color: creditColor, font: { size: 10, weight: '600' } } },
        },
      },
    });

    if (forcedChart) forcedChart.destroy();
    forcedChart = new Chart($('forcedChart').getContext('2d'), {
      data: {
        labels,
        datasets: [
          { type: 'bar', label: '반대매매(억원)', data: view.map((r) => eok(r.forced_sale)), backgroundColor: forcedFill, hoverBackgroundColor: forcedColor, borderRadius: 3, borderSkipped: false, maxBarThickness: 9, yAxisID: 'y' },
          { type: 'line', label: '비중(%)', data: view.map((r) => r.forced_sale_pct), borderColor: uncollectedColor, yAxisID: 'y1', tension: 0.28, borderWidth: 2.5, pointRadius: 0, pointHoverRadius: 3, spanGaps: true },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            position: 'top',
            align: 'start',
            labels: { color: chartColor('--text-secondary', '#cbd5e1'), usePointStyle: true, pointStyle: 'line', boxWidth: 22, boxHeight: 8, padding: 16, font: { size: 10, weight: '600' } },
          },
          tooltip: { backgroundColor: chartColor('--ind-tooltip-bg', '#131720'), titleColor: chartColor('--text', '#e8edf5'), bodyColor: chartColor('--text-secondary', '#b4bcc9'), borderColor: chartColor('--border', '#252b38'), borderWidth: 1, padding: 10, displayColors: true, usePointStyle: true },
        },
        scales: {
          x: { border: { display: false }, ticks: { ...axisTicks, maxRotation: 0, autoSkip: true, maxTicksLimit: 6 }, grid: { display: false } },
          y: { position: 'left', border: { display: false }, ticks: axisTicks, grid: { color: gridColor } },
          y1: { position: 'right', border: { display: false }, ticks: axisTicks, grid: { drawOnChartArea: false } },
        },
      },
    });
  }

  function setRange(next) {
    range = next;
    document.querySelectorAll('.ind-range-btn').forEach((btn) => {
      btn.classList.toggle('active', Number(btn.getAttribute('data-range')) === range);
    });
    if (rows.length) renderCharts();
  }

  async function load() {
    try {
      const r = await fetch('/indicators/market-funds');
      const d = await r.json();
      if (!r.ok || !d.success) throw new Error(d.detail || `HTTP ${r.status}`);
      rows = d.rows || [];
      const at = d.updated_at ? String(d.updated_at).replace('T', ' ').slice(0, 16) : '—';
      $('fundsUpdated').innerHTML = d.stale
        ? `<span class="ind-stale">최신 조회 실패 · ${esc(at)} 저장본 표시</span>`
        : `수집 ${esc(at)}`;
      renderTiles();
      renderCharts();
    } catch (e) {
      $('fundTiles').innerHTML = `<div class="ind-empty">오류: ${esc(e.message || e)}</div>`;
    }
  }

  document.querySelectorAll('.ind-range-btn').forEach((btn) => {
    btn.addEventListener('click', () => setRange(Number(btn.getAttribute('data-range'))));
  });
  $('btnRefresh')?.addEventListener('click', load);
  let chartTheme = document.body.classList.contains('mode-real') ? 'real' : 'dark';
  new MutationObserver(() => {
    const nextTheme = document.body.classList.contains('mode-real') ? 'real' : 'dark';
    if (nextTheme !== chartTheme) {
      chartTheme = nextTheme;
      if (rows.length) renderCharts();
    }
  }).observe(document.body, { attributes: true, attributeFilter: ['class'] });
  load();
})();
