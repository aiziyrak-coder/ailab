/**
 * Bilimlar bazasi — klinika kutubxonasi bo'limi.
 * Qulf (bo'lim paroli) → kitoblar ro'yxati → kitob ichidagi qidiruv.
 */
(function () {
  'use strict';

  var BOOKS = null;

  function $(id) { return document.getElementById(id); }

  function toast(text, kind) {
    var box = $('toast');
    if (!box) return;
    var el = document.createElement('div');
    el.className = 'toast-ios ' + (kind === 'err' ? 'toast-ios--err' : 'toast-ios--ok');
    el.textContent = text;
    box.appendChild(el);
    setTimeout(function () { el.remove(); }, 3200);
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function nfmt(n) {
    return String(n == null ? 0 : n).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  }

  /* ── Holat ─────────────────────────────────────────────────────────── */

  async function loadStatus() {
    var r = await fetch(apiPath('/api/kb/status'), { credentials: apiCredentials() });
    if (r.status === 401 || r.status === 403) { window.location.href = '/login'; return null; }
    return r.json();
  }

  function showGate() {
    $('kbLoading').hidden = true;
    $('kbShell').hidden = true;
    $('kbGate').hidden = false;
    $('kbLockBtn').hidden = true;
    var f = $('kbPassword');
    if (f) setTimeout(function () { f.focus(); }, 60);
  }

  function showShell() {
    $('kbLoading').hidden = true;
    $('kbGate').hidden = true;
    $('kbShell').hidden = false;
    $('kbLockBtn').hidden = false;
  }

  /* ── Qulfni ochish ─────────────────────────────────────────────────── */

  async function unlock(e) {
    e.preventDefault();
    var input = $('kbPassword');
    var btn = $('kbUnlockBtn');
    var msg = $('kbGateMsg');
    var value = (input.value || '').trim();
    if (!value) { msg.textContent = 'Parolni kiriting'; return; }

    btn.disabled = true;
    msg.textContent = '';
    await ensureCsrfCookie();
    try {
      var r = await fetch(apiPath('/api/kb/unlock'), apiFetchInit('POST', { password: value }));
      var d = await r.json().catch(function () { return {}; });
      if (!r.ok || !d.ok) {
        msg.textContent = d.error || (r.status === 429
          ? 'Juda ko‘p urinish — biroz kuting'
          : 'Parol noto‘g‘ri');
        input.value = '';
        input.focus();
        return;
      }
      input.value = '';
      showShell();
      await loadBooks();
    } catch (err) {
      msg.textContent = 'Aloqa xatosi — qayta urinib ko‘ring';
    } finally {
      btn.disabled = false;
    }
  }

  window.kbLock = async function () {
    await ensureCsrfCookie();
    try { await fetch(apiPath('/api/kb/lock'), apiFetchInit('POST', {})); } catch (_) {}
    BOOKS = null;
    showGate();
    toast('Bo‘lim qulflandi');
  };

  /* ── Kitoblar ──────────────────────────────────────────────────────── */

  async function loadBooks() {
    var r = await fetch(apiPath('/api/kb/books'), { credentials: apiCredentials() });
    if (r.status === 403) { showGate(); return; }
    var d = await r.json().catch(function () { return {}; });
    if (!d.ok) { toast('Kutubxonani yuklab bo‘lmadi', 'err'); return; }
    BOOKS = d;
    renderStats(d);
    renderBooks(d.books || []);
  }

  function renderStats(d) {
    var clinic = (d.books || []).filter(function (b) { return b.clinic; });
    var chars = clinic.reduce(function (a, b) { return a + (b.chars || 0); }, 0);
    var files = clinic.reduce(function (a, b) { return a + (b.files || 0); }, 0);
    $('kbStats').innerHTML =
      stat(nfmt(clinic.length), 'klinika kitobi') +
      stat(nfmt(files), 'bo‘lim / fayl') +
      stat(nfmt(d.clinic_chunks || 0), 'o‘qitilgan parcha') +
      stat((chars / 1e6).toFixed(1) + ' mln', 'belgi');
  }

  function stat(value, label) {
    return '<div class="kb-stat"><strong>' + esc(value) + '</strong><span>' + esc(label) + '</span></div>';
  }

  function renderBooks(books) {
    var clinic = books.filter(function (b) { return b.clinic; });
    var canon = books.filter(function (b) { return !b.clinic; });
    $('kbClinicGrid').innerHTML = clinic.map(card).join('') ||
      '<p class="kb-empty">Klinika kitoblari hali o‘qitilmagan.</p>';
    $('kbCanonGrid').innerHTML = canon.map(card).join('') ||
      '<p class="kb-empty">Kanon kitoblari topilmadi.</p>';
  }

  function card(b) {
    var mb = ((b.chars || 0) / 1e6).toFixed(2);
    return '' +
      '<article class="kb-card' + (b.clinic ? ' kb-card--clinic' : '') + '" tabindex="0" role="button"' +
      ' onclick="kbOpenBook(\'' + esc(b.code) + '\')"' +
      ' onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();kbOpenBook(\'' + esc(b.code) + '\')}">' +
      (b.clinic ? '<span class="kb-badge">Ustuvor</span>' : '') +
      '<h3>' + esc(b.label.replace(/\s*\(klinika kutubxonasi\)$/, '')) + '</h3>' +
      '<dl class="kb-card-facts">' +
      '<div><dt>Parcha</dt><dd>' + nfmt(b.chunks) + '</dd></div>' +
      (b.files ? '<div><dt>Bo‘lim</dt><dd>' + nfmt(b.files) + '</dd></div>' : '') +
      '<div><dt>Hajm</dt><dd>' + mb + ' mln</dd></div>' +
      '</dl>' +
      '<span class="kb-card-more">' +
      (b.files ? 'Ichidagi bo‘limlar →' : 'Tafsilot →') + '</span>' +
      '</article>';
  }

  window.kbOpenBook = function (code) {
    if (!BOOKS) return;
    var b = (BOOKS.books || []).find(function (x) { return x.code === code; });
    if (!b) return;
    $('kbBookTitle').textContent = b.label.replace(/\s*\(klinika kutubxonasi\)$/, '');
    $('kbBookSub').textContent =
      nfmt(b.chunks) + ' parcha · ' +
      (b.files ? nfmt(b.files) + ' bo‘lim · ' : '') +
      ((b.chars || 0) / 1e6).toFixed(2) + ' mln belgi';
    var titles = b.titles || [];
    $('kbBookBody').innerHTML = titles.length
      ? '<ol class="kb-toc">' + titles.map(function (t) {
          return '<li><span>' + esc(t.title) + '</span><em>' + nfmt(t.chunks) + '</em></li>';
        }).join('') + '</ol>' +
        (b.files > titles.length
          ? '<p class="kb-empty">…va yana ' + nfmt(b.files - titles.length) + ' ta bo‘lim</p>'
          : '')
      : '<p class="kb-empty">Bu manba sahifa bo‘yicha indekslangan (bo‘lim nomlari yo‘q).</p>';
    $('kbBookOverlay').classList.remove('hidden');
    document.body.style.overflow = 'hidden';
  };

  window.kbCloseBook = function (e) {
    if (e && e.target && e.target.id !== 'kbBookOverlay' && e.type === 'click') return;
    $('kbBookOverlay').classList.add('hidden');
    document.body.style.overflow = '';
  };

  /* ── Qidiruv ───────────────────────────────────────────────────────── */

  async function search(e) {
    e.preventDefault();
    var q = ($('kbQuery').value || '').trim();
    var box = $('kbResults');
    if (q.length < 3) { toast('Kamida 3 ta belgi yozing', 'err'); return; }
    var btn = $('kbSearchBtn');
    btn.disabled = true;
    box.hidden = false;
    box.innerHTML = '<p class="kb-empty">Qidirilmoqda…</p>';
    await ensureCsrfCookie();
    try {
      var r = await fetch(apiPath('/api/kb/search'), apiFetchInit('POST', {
        q: q,
        scope: $('kbScope').value,
        limit: 12,
      }));
      if (r.status === 403) { showGate(); return; }
      var d = await r.json().catch(function () { return {}; });
      if (!d.ok) { box.innerHTML = '<p class="kb-empty">' + esc(d.error || 'Xato') + '</p>'; return; }
      renderHits(d.hits || [], q);
    } catch (err) {
      box.innerHTML = '<p class="kb-empty">Aloqa xatosi</p>';
    } finally {
      btn.disabled = false;
    }
  }

  function renderHits(hits, q) {
    var box = $('kbResults');
    if (!hits.length) {
      box.innerHTML = '<p class="kb-empty">“' + esc(q) + '” bo‘yicha hech narsa topilmadi.</p>';
      return;
    }
    box.innerHTML =
      '<div class="kb-results-head">“' + esc(q) + '” — ' + hits.length + ' ta natija</div>' +
      hits.map(function (h) {
        var where = esc(h.label.replace(/\s*\(klinika kutubxonasi\)$/, ''));
        if (h.title) where += ' · ' + esc(h.title);
        return '<article class="kb-hit' + (h.clinic ? ' kb-hit--clinic' : '') + '">' +
          '<div class="kb-hit-src">' + (h.clinic ? '<span class="kb-badge">Klinika</span>' : '') +
          where + '</div>' +
          '<p class="kb-hit-text">' + esc(h.text) + '</p>' +
          '</article>';
      }).join('');
  }

  /* ── Boshlash ──────────────────────────────────────────────────────── */

  async function boot() {
    try {
      var me = await fetch(apiPath('/api/auth/me'), { credentials: apiCredentials() });
      if (me.ok) {
        var d = await me.json().catch(function () { return null; });
        var u = (d && d.user) || d || {};
        if (u.username) $('userPill').textContent = u.username;
      }
    } catch (_) {}

    var st = await loadStatus();
    if (!st) return;
    if (st.unlocked) {
      showShell();
      await loadBooks();
    } else {
      showGate();
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    $('kbUnlockForm').addEventListener('submit', unlock);
    $('kbSearchForm').addEventListener('submit', search);
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') window.kbCloseBook();
    });
    boot();
  });
})();
