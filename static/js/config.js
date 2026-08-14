window.VANTAGE_CONFIG = Object.assign({
  apiBase: window.location.origin,
  mode: 'standalone',
  bridgeToken: null,
}, window.VANTAGE_CONFIG || {});

(function () {
  const params = new URLSearchParams(window.location.search);
  if (params.get('embed') === '1') {
    window.VANTAGE_CONFIG.mode = 'embedded';
    document.body.classList.add('telemetry-embedded');
  }
  const bridge = params.get('bridge');
  if (bridge) {
    window.VANTAGE_CONFIG.bridgeToken = bridge;
  }
  const api = params.get('apiBase');
  if (api) {
    window.VANTAGE_CONFIG.apiBase = api;
  }
})();

function apiUrl(path) {
  const base = (window.VANTAGE_CONFIG.apiBase || '').replace(/\/$/, '');
  return `${base}${path.startsWith('/') ? path : '/' + path}`;
}

function apiFetch(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (window.VANTAGE_CONFIG.bridgeToken) {
    headers.Authorization = `Bearer ${window.VANTAGE_CONFIG.bridgeToken}`;
  }
  return fetch(apiUrl(path), { ...options, headers, credentials: 'include' });
}
