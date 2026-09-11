'use strict';

(function () {
  function applyAccountTheme(isReal) {
    const isRealBool = (isReal === true || isReal === 'true' || isReal === 'real');
    const root = document.documentElement;
    const body = document.body;
    
    if (isRealBool) {
      root.classList.add('mode-real');
      root.classList.remove('mode-mock');
      if (body) {
        body.classList.add('mode-real');
        body.classList.remove('mode-mock');
      }
    } else {
      root.classList.remove('mode-real');
      root.classList.add('mode-mock');
      if (body) {
        body.classList.remove('mode-real');
        body.classList.add('mode-mock');
      }
    }
  }

  // 1. Instantly apply cached mode from sessionStorage
  try {
    var cachedMode = sessionStorage.getItem('account_mock_mode');
    if (cachedMode !== null) {
      applyAccountTheme(cachedMode === 'false');
    }
  } catch (_) {}

  // 2. Global helper to sync account theme
  window.syncAccountTheme = function (isReal) {
    const isRealBool = (isReal === true || isReal === 'true' || isReal === 'real');
    try {
      sessionStorage.setItem('account_mock_mode', isRealBool ? 'false' : 'true');
    } catch (_) {}
    applyAccountTheme(isRealBool);
  };

  // 3. Auto-fetch status on initial script execution
  if (typeof fetch === 'function') {
    fetch('/api/status')
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (data) {
        if (!data) return;
        var isReal = data.mock_mode === false || data.account_type === '실계좌' || data.account_type === '실전투자';
        window.syncAccountTheme(isReal);
      })
      .catch(function () {});
  }
})();
