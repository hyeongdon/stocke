'use strict';

(function () {
  const search = { items: [], active: -1, timer: null, seq: 0 };

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[char]));
  }

  function dateLabel(value) {
    if (!value) return '';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('ko-KR', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    });
  }

  async function request(url, options) {
    const response = await fetch(url, Object.assign({
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    }, options || {}));
    if (response.status === 401) {
      location.href = '/login?next=' + encodeURIComponent(location.pathname);
      throw new Error('로그인이 필요합니다.');
    }
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) {
      const detail = data.detail;
      throw new Error(typeof detail === 'string' ? detail : `${response.status} ${response.statusText}`);
    }
    return data;
  }

  function renderItems(items) {
    if (!items.length) return '<div class="ta-empty">등록된 가격 알림이 없습니다.</div>';
    return items.map((item) => {
      const active = item.status === 'active';
      const status = active
        ? (item.last_error ? '시세 조회/발송 재시도 중' : '감시 중')
        : '알림 전송 완료';
      const lastPrice = item.last_price == null ? '현재가 대기 중' : `최근가 ${Number(item.last_price).toLocaleString('ko-KR')}원`;
      const updated = dateLabel(item.triggered_at || item.last_checked_at || item.created_at);
      return `<li class="ta-item">
        <div class="ta-item-main">
          <strong>${esc(item.stock_code)} · ${Number(item.target_price).toLocaleString('ko-KR')}원 이하</strong>
          <span>${esc(status)} · ${esc(lastPrice)}${updated ? ` · ${esc(updated)}` : ''}</span>
          ${item.last_error ? `<small>${esc(item.last_error)}</small>` : ''}
        </div>
        <button type="button" class="btn sm" data-ta-delete="${Number(item.id)}" aria-label="가격 알림 삭제">삭제</button>
      </li>`;
    }).join('');
  }

  async function loadItems(message) {
    const list = document.getElementById('taList');
    if (!list) return;
    list.innerHTML = '<li class="ta-empty">가격 알림을 불러오는 중...</li>';
    try {
      const result = await request('/telegram/price-alerts');
      list.innerHTML = renderItems(result.items || []);
      const status = document.getElementById('taStatus');
      if (status) status.textContent = message || '';
    } catch (error) {
      list.innerHTML = `<li class="ta-empty ta-error">${esc(error.message)}</li>`;
    }
  }

  function openModal(open) {
    const modal = document.getElementById('taModal');
    if (!modal) return;
    modal.hidden = !open;
    if (open) {
      loadItems();
      document.getElementById('taStockSearch').focus();
    }
  }

  function renderSuggestions() {
    const list = document.getElementById('taSuggestions');
    const input = document.getElementById('taStockSearch');
    if (!search.items.length) {
      list.innerHTML = '<li class="ta-suggest-empty">검색 결과가 없습니다.</li>';
    } else {
      list.innerHTML = search.items.map((item, index) =>
        `<li role="option" aria-selected="${index === search.active}" data-ta-index="${index}" class="${index === search.active ? 'active' : ''}">
          <strong>${esc(item.name)}</strong><span>${esc(item.code)}</span><small>${esc(item.market || '')}</small>
        </li>`
      ).join('');
      const active = list.querySelector('li.active');
      if (active) active.scrollIntoView({ block: 'nearest' });
    }
    list.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  }

  function hideSuggestions() {
    const list = document.getElementById('taSuggestions');
    if (list) list.hidden = true;
    const input = document.getElementById('taStockSearch');
    if (input) input.setAttribute('aria-expanded', 'false');
    search.active = -1;
  }

  function chooseSuggestion(index) {
    const item = search.items[index];
    if (!item) return;
    document.getElementById('taStockSearch').value = `${item.name} (${item.code})`;
    document.getElementById('taStockCode').value = item.code;
    hideSuggestions();
  }

  function onStockSearchInput() {
    const input = document.getElementById('taStockSearch');
    const code = document.getElementById('taStockCode');
    const query = input.value.trim();
    code.value = '';
    clearTimeout(search.timer);
    if (!query) {
      search.items = [];
      hideSuggestions();
      return;
    }
    const seq = ++search.seq;
    search.timer = setTimeout(async () => {
      try {
        const result = await request('/api/ladder/search?q=' + encodeURIComponent(query) + '&limit=10');
        if (seq !== search.seq) return;
        search.items = result.items || [];
        search.active = search.items.length ? 0 : -1;
        renderSuggestions();
      } catch (error) {
        if (seq !== search.seq) return;
        search.items = [];
        search.active = -1;
        const list = document.getElementById('taSuggestions');
        list.innerHTML = `<li class="ta-suggest-empty ta-error">${esc(error.message)}</li>`;
        list.hidden = false;
      }
    }, 180);
  }

  function onStockSearchKeydown(event) {
    if (event.key === 'Escape') {
      hideSuggestions();
      return;
    }
    if (!search.items.length || !['ArrowDown', 'ArrowUp', 'Enter'].includes(event.key)) return;
    event.preventDefault();
    if (event.key === 'ArrowDown') {
      search.active = (search.active + 1) % search.items.length;
      renderSuggestions();
    } else if (event.key === 'ArrowUp') {
      search.active = (search.active - 1 + search.items.length) % search.items.length;
      renderSuggestions();
    } else {
      chooseSuggestion(search.active);
    }
  }

  function mount() {
    document.body.insertAdjacentHTML('beforeend', `
      <button class="ta-fab" type="button" id="taOpen" aria-label="텔레그램 가격 알림 열기" title="가격 도달 텔레그램 알림">🔔</button>
      <div class="ta-modal" id="taModal" hidden>
        <section class="ta-dialog" role="dialog" aria-modal="true" aria-labelledby="taTitle">
          <header class="ta-head">
            <div><h2 id="taTitle">가격 도달 알림</h2><p>목표가 이하에 도달하면 텔레그램으로 1회 알려드립니다.</p></div>
            <button type="button" class="ta-close" id="taClose" aria-label="닫기">×</button>
          </header>
          <form id="taForm" class="ta-form">
            <label class="ta-stock-label">종목명 또는 코드
              <div class="ta-stock-search">
                <input id="taStockSearch" type="text" role="combobox" aria-autocomplete="list" aria-controls="taSuggestions" aria-expanded="false" placeholder="예: 삼성중공업, 010140" autocomplete="off" required>
                <input id="taStockCode" name="stock_code" type="hidden">
                <ul class="ta-suggestions" id="taSuggestions" role="listbox" hidden></ul>
              </div>
            </label>
            <label>목표 가격 (원)<input name="target_price" type="number" min="1" max="2000000000" step="1" placeholder="예: 25000" required></label>
            <button class="btn primary" type="submit">알림 등록</button>
          </form>
          <div class="ta-hint">거래일 09:00~15:30에 1분 간격으로 확인합니다. 이미 목표가 이하라면 다음 확인 때 알립니다.</div>
          <div class="ta-list-head"><strong>내 가격 알림</strong><span>활성 알림 최대 50개</span></div>
          <div id="taStatus" class="ta-status" aria-live="polite"></div>
          <ul class="ta-list" id="taList" aria-live="polite"></ul>
        </section>
      </div>`);

    document.getElementById('taOpen').addEventListener('click', () => openModal(true));
    document.getElementById('taClose').addEventListener('click', () => openModal(false));
    document.getElementById('taModal').addEventListener('click', (event) => {
      if (event.target.id === 'taModal') openModal(false);
    });
    const stockSearch = document.getElementById('taStockSearch');
    stockSearch.addEventListener('input', onStockSearchInput);
    stockSearch.addEventListener('keydown', onStockSearchKeydown);
    stockSearch.addEventListener('blur', () => setTimeout(hideSuggestions, 120));
    document.getElementById('taSuggestions').addEventListener('mousedown', (event) => {
      event.preventDefault();
      const item = event.target.closest('[data-ta-index]');
      if (item) chooseSuggestion(Number(item.dataset.taIndex));
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') openModal(false);
    });
    document.getElementById('taForm').addEventListener('submit', async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const button = form.querySelector('button[type="submit"]');
      const code = form.elements.stock_code.value.trim();
      const target = Number(form.elements.target_price.value);
      if (!/^\d{6}$/.test(code) || !Number.isInteger(target) || target < 1) {
        document.getElementById('taStatus').textContent = '검색 결과에서 종목을 선택하고 목표 가격을 확인해 주세요.';
        return;
      }
      button.disabled = true;
      document.getElementById('taStatus').textContent = '가격 알림을 등록하는 중...';
      try {
        await request('/telegram/price-alerts', {
          method: 'POST',
          headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
          body: JSON.stringify({ stock_code: code, target_price: target }),
        });
        form.reset();
        await loadItems('가격 알림을 등록했습니다.');
      } catch (error) {
        document.getElementById('taStatus').textContent = error.message;
      } finally {
        button.disabled = false;
      }
    });
    document.getElementById('taList').addEventListener('click', async (event) => {
      const button = event.target.closest('[data-ta-delete]');
      if (!button) return;
      button.disabled = true;
      try {
        await request('/telegram/price-alerts/' + encodeURIComponent(button.dataset.taDelete), { method: 'DELETE' });
        await loadItems('가격 알림을 삭제했습니다.');
      } catch (error) {
        button.disabled = false;
        const status = document.getElementById('taStatus');
        if (status) status.textContent = error.message;
      }
    });
  }

  mount();
})();
