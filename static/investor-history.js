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

  function formatDate(value) {
    const date = String(value || '');
    const match = /^(\d{4})(\d{2})(\d{2})$/.exec(date);
    return match ? `${match[1]}-${match[2]}-${match[3]}` : date;
  }

  function renderHistory(data) {
    const rows = [...(data.rows || [])].reverse();
    if (!rows.length) return '<div class="if-stock-empty">일별 수급 내역이 없습니다.</div>';
    return `<div class="if-stock-table-wrap"><table class="if-stock-table">
      <thead><tr><th>날짜</th><th>외국인 순매수</th><th>기관 순매수</th></tr></thead>
      <tbody>${rows.map((row) => `<tr>
        <td>${escapeHtml(formatDate(row.date))}</td>
        <td class="${signClass(row.foreign_net)}">${escapeHtml(formatQuantity(row.foreign_net))}</td>
        <td class="${signClass(row.institution_net)}">${escapeHtml(formatQuantity(row.institution_net))}</td>
      </tr>`).join('')}</tbody>
    </table></div>`;
  }

  async function fetchHistory(code) {
    const cached = historyCache.get(code);
    if (cached && Date.now() - cached.at < CACHE_TTL_MS) return cached.data;
    if (historyRequests.has(code)) return historyRequests.get(code);

    const request = (async () => {
      const response = await fetch(`/stocks/${encodeURIComponent(code)}/investor-history?days=20`, {
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
      historyCache.set(code, { at: Date.now(), data });
      return data;
    })();
    historyRequests.set(code, request);
    try {
      return await request;
    } finally {
      historyRequests.delete(code);
    }
  }

  async function loadInvestorHistory(result, code, button) {
    const normalizedCode = String(code || '').trim().replace(/^A/i, '');
    if (!/^\d{6}$/.test(normalizedCode)) {
      result.textContent = '종목코드 6자리를 입력해 주세요.';
      return;
    }

    if (button) button.disabled = true;
    result.innerHTML = '<div class="if-stock-empty">최근 20거래일 수급 내역 조회 중...</div>';
    try {
      const data = await fetchHistory(normalizedCode);
      result.innerHTML = `<div class="if-stock-caption">${escapeHtml(data.stock_code || normalizedCode)} · 최근 ${data.rows.length}거래일 · 순매수 단위: ${escapeHtml(data.unit || '주')}</div>${renderHistory(data)}`;
    } catch (error) {
      result.innerHTML = `<div class="if-stock-empty">수급 조회에 실패했습니다. 키움 연결 상태와 종목코드를 확인해 주세요. <span class="hint">${escapeHtml(error.message)}</span></div>`;
    } finally {
      if (button) button.disabled = false;
    }
  }

  window.loadStockInvestorHistory = loadInvestorHistory;

  document.addEventListener('submit', (event) => {
    const form = event.target.closest('[data-stock-investor-form]');
    if (!form) return;
    event.preventDefault();
    const input = form.querySelector('[data-stock-investor-code]');
    const result = form.parentElement.querySelector('[data-stock-investor-result]');
    loadInvestorHistory(result, input.value, form.querySelector('button[type="submit"]'));
  });
})();
