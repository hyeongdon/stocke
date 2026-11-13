'use strict';

(function () {
  const historyCache = new Map();
  const historyRequests = new Map();
  const CACHE_TTL_MS = 5 * 60 * 1000;

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"]/g, (char) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
    }[char]));
  }

  function signClass(value) {
    const quantity = Number(value) || 0;
    return quantity > 0 ? 'up' : (quantity < 0 ? 'down' : 'flat');
  }

  function formatQuantity(value) {
    if (value == null || !Number.isFinite(Number(value))) return '-';
    const quantity = Number(value);
    return `${quantity > 0 ? '+' : ''}${quantity.toLocaleString('ko-KR')}`;
  }

  function formatAmountEok(value) {
    if (value == null || !Number.isFinite(Number(value))) return '-';
    const amount = Number(value) / 100000000;
    return `${amount > 0 ? '+' : ''}${amount.toLocaleString('ko-KR', { maximumFractionDigits: 1 })}억`;
  }

  function amountEok(value) {
    return value == null || !Number.isFinite(Number(value)) ? null : Number(value) / 100000000;
  }

  function formatDate(value) {
    const date = String(value || '');
    const match = /^(\d{4})(\d{2})(\d{2})$/.exec(date);
    return match ? `${match[1]}-${match[2]}-${match[3]}` : date;
  }

  function renderChart(rows) {
    const width = 960;
    const height = 220;
    const pad = { left: 28, right: 52, top: 12, bottom: 28 };
    const plotHeight = height - pad.top - pad.bottom;
    const plotWidth = width - pad.left - pad.right;
    const series = [
      { key: 'foreign_net_estimated_amount', className: 'foreign', label: '외국인' },
      { key: 'institution_net_estimated_amount', className: 'institution', label: '기관' },
    ].map((item) => ({
      ...item,
      values: rows.map((row) => amountEok(row[item.key])),
    }));
    const values = series.flatMap((item) => item.values).filter((value) => value != null);
    if (!values.length) {
      return '<div class="if-stock-empty">종가 정보가 없어 순매수 추정액 차트를 표시할 수 없습니다.</div>';
    }

    const maxValue = Math.max(...values.map(Math.abs), 0.1);
    const zeroY = pad.top + plotHeight / 2;
    const y = (value) => pad.top + ((maxValue - value) / (maxValue * 2)) * plotHeight;
    const x = (index) => rows.length === 1
      ? pad.left + plotWidth / 2
      : pad.left + index / (rows.length - 1) * plotWidth;
    let svg = `<svg class="if-stock-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="최근 외국인·기관 순매수 추정액 선형 차트">`;
    svg += `<text x="2" y="${pad.top + 4}">+${maxValue.toLocaleString('ko-KR', { maximumFractionDigits: 1 })}</text>`;
    svg += `<text x="2" y="${height - pad.bottom + 2}">-${maxValue.toLocaleString('ko-KR', { maximumFractionDigits: 1 })}</text>`;
    svg += `<line x1="${pad.left}" x2="${width - pad.right}" y1="${zeroY}" y2="${zeroY}" class="if-stock-zero"/>`;
    series.forEach((item) => {
      let segment = [];
      const renderSegment = () => {
        if (!segment.length) return;
        if (segment.length > 1) {
          const path = segment.map((point, index) =>
            `${index ? 'L' : 'M'} ${x(point.index)} ${y(point.value)}`
          ).join(' ');
          svg += `<path d="${path}" class="if-stock-line ${item.className}"/>`;
        }
        segment.forEach((point) => {
          const row = rows[point.index];
          svg += `<circle cx="${x(point.index)}" cy="${y(point.value)}" r="2.5" class="if-stock-point ${item.className}"><title>${escapeHtml(formatDate(row.date))} ${item.label} ${escapeHtml(formatAmountEok(row[item.key]))}</title></circle>`;
        });
        segment = [];
      };
      item.values.forEach((value, index) => {
        if (value == null) {
          renderSegment();
          return;
        }
        segment.push({ index, value });
      });
      renderSegment();
    });
    const prices = rows.map((row) => {
      const price = Number(row.close_price);
      return row.close_price != null && Number.isFinite(price) && price > 0 ? price : null;
    });
    const validPrices = prices.filter((price) => price != null);
    if (validPrices.length) {
      const minPrice = Math.min(...validPrices);
      const maxPrice = Math.max(...validPrices);
      const span = maxPrice - minPrice || maxPrice * 0.01 || 1;
      const priceY = (price) => pad.top + ((maxPrice - price) / span) * plotHeight;
      const path = prices.reduce((acc, price, index) => {
        if (price == null) return { d: acc.d, open: false };
        return { d: `${acc.d} ${acc.open ? 'L' : 'M'} ${x(index)} ${priceY(price)}`, open: true };
      }, { d: '', open: false }).d;
      if (path) svg += `<path d="${path}" class="if-stock-line price"/>`;
      prices.forEach((price, index) => {
        if (price == null) return;
        svg += `<circle cx="${x(index)}" cy="${priceY(price)}" r="2" class="if-stock-point price"><title>${escapeHtml(formatDate(rows[index].date))} 종가 ${price.toLocaleString('ko-KR')}원</title></circle>`;
      });
      const priceLabelX = width - pad.right + 4;
      svg += `<text x="${priceLabelX}" y="${pad.top + 4}" class="if-stock-price-label">${maxPrice.toLocaleString('ko-KR')}</text>`;
      svg += `<text x="${priceLabelX}" y="${height - pad.bottom + 2}" class="if-stock-price-label">${minPrice.toLocaleString('ko-KR')}</text>`;
    }
    const tickCount = Math.min(6, rows.length);
    Array.from({ length: tickCount }, (_, index) =>
      Math.round(index * (rows.length - 1) / Math.max(1, tickCount - 1))
    ).forEach((index) => {
      const date = formatDate(rows[index].date);
      const label = date.length >= 10 ? date.slice(5) : date;
      svg += `<text x="${x(index)}" y="${height - 7}" text-anchor="middle">${escapeHtml(label)}</text>`;
    });
    svg += '</svg>';
    return svg;
  }

  function renderSummary(rows) {
    const foreign = rows.map((row) => row.foreign_net_estimated_amount).filter((value) => value != null);
    const institution = rows.map((row) => row.institution_net_estimated_amount).filter((value) => value != null);
    const turnover = rows.map((row) => row.trade_value_estimated).filter((value) => value != null);
    const sum = (values) => values.reduce((total, value) => total + Number(value), 0);
    const average = turnover.length ? sum(turnover) / turnover.length : null;
    const matched = rows.filter((row) => row.foreign_net_estimated_amount != null &&
      row.institution_net_estimated_amount != null && row.trade_value_estimated != null &&
      Number(row.trade_value_estimated) > 0);
    const matchedTurnover = sum(matched.map((row) => row.trade_value_estimated));
    const matchedNet = sum(matched.map((row) =>
      Number(row.foreign_net_estimated_amount) + Number(row.institution_net_estimated_amount)
    ));
    const turnoverRatio = matched.length && matchedTurnover > 0
      ? `${matchedNet > 0 ? '+' : ''}${(matchedNet / matchedTurnover * 100).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}%`
      : '-';
    return `<div class="if-stock-summary">
      <div><span>기간 외국인 순매수 추정액</span><strong class="${signClass(sum(foreign))}">${foreign.length ? formatAmountEok(sum(foreign)) : '-'}</strong></div>
      <div><span>기간 기관 순매수 추정액</span><strong class="${signClass(sum(institution))}">${institution.length ? formatAmountEok(sum(institution)) : '-'}</strong></div>
      <div><span>일평균 거래대금 근사</span><strong>${average == null ? '-' : formatAmountEok(average)}</strong></div>
      <div><span>외인+기관 순매수 / 거래대금</span><strong>${turnoverRatio}</strong></div>
    </div>`;
  }

  function turnoverRatio(row) {
    const foreign = Number(row.foreign_net_estimated_amount);
    const institution = Number(row.institution_net_estimated_amount);
    const turnover = Number(row.trade_value_estimated);
    if (row.foreign_net_estimated_amount == null || row.institution_net_estimated_amount == null ||
        row.trade_value_estimated == null || !Number.isFinite(turnover) || turnover <= 0) return '-';
    const ratio = (foreign + institution) / turnover * 100;
    return `${ratio > 0 ? '+' : ''}${ratio.toLocaleString('ko-KR', { maximumFractionDigits: 1 })}%`;
  }

  function renderPriceCell(row, previousRow) {
    const price = Number(row.close_price);
    if (row.close_price == null || !Number.isFinite(price) || price <= 0) return '<td>-</td>';
    const previous = previousRow ? Number(previousRow.close_price) : NaN;
    if (!Number.isFinite(previous) || previous <= 0) return `<td>${price.toLocaleString('ko-KR')}</td>`;
    const change = (price / previous - 1) * 100;
    const changeText = `${change > 0 ? '+' : ''}${change.toFixed(2)}%`;
    return `<td class="${signClass(change)}">${price.toLocaleString('ko-KR')} <span class="if-stock-price-change">${changeText}</span></td>`;
  }

  function renderHistory(data) {
    const rows = [...(data.rows || [])].reverse();
    if (!rows.length) return '<div class="if-stock-empty">일별 수급 내역이 없습니다.</div>';
    return `${renderSummary(rows)}
    <div class="if-stock-chart-legend"><span class="foreign">외국인 순매수 추정액</span><span class="institution">기관 순매수 추정액</span><span class="price">주가(종가 · 오른쪽 축)</span><span class="hint">단위: 억원 · 순매수 수량 × 종가 근사 · 0선 아래는 순매도</span></div>
    ${renderChart([...rows].reverse())}
    <div class="if-stock-table-wrap"><table class="if-stock-table">
      <thead><tr><th>날짜</th><th>주가(종가)</th><th>외국인 순매수</th><th>추정액</th><th>기관 순매수</th><th>추정액</th><th>거래대금 근사</th><th>순매수/거래대금</th></tr></thead>
      <tbody>${rows.map((row, index) => `<tr>
        <td>${escapeHtml(formatDate(row.date))}</td>
        ${renderPriceCell(row, rows[index + 1])}
        <td class="${signClass(row.foreign_net)}">${escapeHtml(formatQuantity(row.foreign_net))}</td>
        <td class="${signClass(row.foreign_net_estimated_amount)}">${escapeHtml(formatAmountEok(row.foreign_net_estimated_amount))}</td>
        <td class="${signClass(row.institution_net)}">${escapeHtml(formatQuantity(row.institution_net))}</td>
        <td class="${signClass(row.institution_net_estimated_amount)}">${escapeHtml(formatAmountEok(row.institution_net_estimated_amount))}</td>
        <td>${escapeHtml(formatAmountEok(row.trade_value_estimated))}</td>
        <td>${escapeHtml(turnoverRatio(row))}</td>
      </tr>`).join('')}</tbody>
    </table></div>`;
  }

  async function fetchHistory(code, days) {
    const cacheKey = `${code}:${days}`;
    const cached = historyCache.get(cacheKey);
    if (cached && Date.now() - cached.at < CACHE_TTL_MS) return cached.data;
    if (historyRequests.has(cacheKey)) return historyRequests.get(cacheKey);

    const request = (async () => {
      const response = await fetch(`/stocks/${encodeURIComponent(code)}/investor-history?days=${days}`, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) {
        let detail = `${response.status} ${response.statusText}`;
        try {
          const error = await response.json();
          detail = error.detail || detail;
        } catch (_) {}
        throw new Error(detail);
      }
      const data = await response.json();
      historyCache.set(cacheKey, { at: Date.now(), data });
      return data;
    })();
    historyRequests.set(cacheKey, request);
    try {
      return await request;
    } finally {
      historyRequests.delete(cacheKey);
    }
  }

  async function loadInvestorHistory(result, code, button, requestedDays) {
    const normalizedCode = String(code || '').trim().replace(/^A/i, '');
    if (!/^\d{6}$/.test(normalizedCode)) {
      result.textContent = '종목코드 6자리를 입력해 주세요.';
      return;
    }

    const days = requestedDays == null || requestedDays === '' ? 20 : (Number(requestedDays) === 60 ? 60 : 20);
    const requestId = Number(result.dataset.requestId || 0) + 1;
    result.dataset.requestId = String(requestId);
    if (button) button.disabled = true;
    result.innerHTML = `<div class="if-stock-empty">최근 ${days}거래일 수급 내역 조회 중...</div>`;
    try {
      const data = await fetchHistory(normalizedCode, days);
      if (result.dataset.requestId !== String(requestId)) return;
      result.innerHTML = `<div class="if-stock-caption">${escapeHtml(data.stock_code || normalizedCode)} · 최근 ${data.rows.length}거래일 · 순매수 단위: ${escapeHtml(data.unit || '주')} · API에서 총매수액은 제공되지 않아 순매수량×종가와 종가×거래량으로 금액을 근사합니다.</div>${renderHistory(data)}`;
    } catch (error) {
      if (result.dataset.requestId !== String(requestId)) return;
      result.innerHTML = `<div class="if-stock-empty">수급 조회에 실패했습니다. 키움 연결 상태와 종목코드를 확인해 주세요. <span class="hint">${escapeHtml(error.message)}</span> <button type="button" class="btn sm" data-investor-retry="${escapeHtml(normalizedCode)}" data-days="${days}">다시 조회</button></div>`;
    } finally {
      if (result.dataset.requestId === String(requestId) && button) button.disabled = false;
    }
  }

  window.loadStockInvestorHistory = loadInvestorHistory;

  document.addEventListener('submit', (event) => {
    const form = event.target.closest('[data-stock-investor-form]');
    if (!form) return;
    event.preventDefault();
    const input = form.querySelector('[data-stock-investor-code]');
    const result = form.parentElement.querySelector('[data-stock-investor-result]');
    const days = form.parentElement.querySelector('[data-investor-days]');
    loadInvestorHistory(result, input.value, form.querySelector('button[type="submit"]'), days && days.value);
  });

  document.addEventListener('change', (event) => {
    const select = event.target.closest('[data-investor-days]');
    if (!select) return;
    const section = select.closest('.if-stock-lookup');
    const result = section && section.querySelector('[data-stock-investor-result]');
    const input = section && section.querySelector('[data-stock-investor-code]');
    const code = section && (section.dataset.stockCode || section.dataset.stockInvestorStockCode ||
      (input && input.value));
    if (result && code) loadInvestorHistory(result, code, null, select.value);
  });

  document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-investor-retry]');
    if (!button) return;
    const section = button.closest('.if-stock-lookup');
    const result = section && section.querySelector('[data-stock-investor-result]');
    if (result) loadInvestorHistory(result, button.dataset.investorRetry, button, button.dataset.days);
  });
})();
