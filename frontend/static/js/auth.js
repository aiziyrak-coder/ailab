/**
 * API bazaviy URL va Django sessiya + CSRF (login/register va app.js dan oldin yuklang).
 */
const API_BASE = String(
  typeof window.__MEDLAB_API_BASE__ === 'string' ? window.__MEDLAB_API_BASE__ : ''
).replace(/\/$/, '');

function apiPath(p) {
  if (!p.startsWith('/')) p = '/' + p;
  return API_BASE + p;
}

/** UI ailab.*, API ailabapi.* bo'lsa cookie yuborish uchun */
function apiCredentials() {
  try {
    const base = (API_BASE || '').replace(/\/$/, '');
    if (!base) return 'same-origin';
    if (base === window.location.origin) return 'same-origin';
    return 'include';
  } catch (_) {
    return 'same-origin';
  }
}

function getCookie(name) {
  const esc = name.replace(/([.$?*|{}()[\]\\/+^])/g, '\\$1');
  const m = document.cookie.match(new RegExp('(?:^|; )' + esc + '=([^;]*)'));
  return m ? decodeURIComponent(m[1]) : '';
}

/** POST/PUT/DELETE uchun Django CSRF sarlavhasi */
function csrfHeaders() {
  const t = getCookie('csrftoken');
  return t ? { 'X-CSRFToken': t } : {};
}

/** JSON API so'rovlari uchun fetch init */
function apiFetchInit(method, bodyObj) {
  const m = (method || 'GET').toUpperCase();
  const opts = {
    method: m,
    credentials: apiCredentials(),
    headers: { ...csrfHeaders() },
  };
  if (bodyObj != null) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(bodyObj);
  }
  return opts;
}

/** FormData (multipart) — Content-Type qo'ymang */
function formFetchInit(method) {
  return {
    method: method || 'POST',
    credentials: apiCredentials(),
    headers: { ...csrfHeaders() },
  };
}
