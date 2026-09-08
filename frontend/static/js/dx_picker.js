/**
 * Klinik tashxisni tanlash: qidiruv + ro'yxat + qo'lda yozish.
 *
 * Yo'llanmadagi tashxis avtomatik tanlanadi, shifokor qo'shimcha tashxis
 * qo'shishi yoki ro'yxatda yo'q nomni qo'lda yozishi mumkin. Tanlanganlar
 * tahlilga gipoteza sifatida va kitob qidiruviga kalit sifatida boradi.
 */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const LIST = Array.isArray(window.DX_LIST) ? window.DX_LIST : [];

  let selected = [];
  let activeIndex = -1;

  function norm(s) {
    return String(s || '')
      .toLowerCase()
      .replace(/[‘’ʻ`]/g, "'")
      .replace(/\s+/g, ' ')
      .trim();
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /** Qidiruv: o'zbekcha nom, ruscha sinonim va guruh bo'yicha */
  function search(q) {
    const n = norm(q);
    if (!n) return LIST.slice(0, 40);
    const starts = [];
    const contains = [];
    for (const row of LIST) {
      const u = norm(row[0]);
      const r = norm(row[1]);
      if (u.startsWith(n) || r.startsWith(n)) starts.push(row);
      else if (u.includes(n) || r.includes(n) || norm(row[2]).includes(n)) contains.push(row);
    }
    return starts.concat(contains).slice(0, 40);
  }

  function renderChips() {
    const box = $('dxChips');
    if (!box) return;
    box.innerHTML = selected.map((name, i) =>
      '<span class="dx-chip">' + esc(name) +
      '<button type="button" aria-label="Olib tashlash" onclick="dxRemove(' + i + ')">✕</button></span>'
    ).join('');
    box.hidden = selected.length === 0;
  }

  function renderResults(rows, q) {
    const box = $('dxResults');
    if (!box) return;
    const chosen = new Set(selected.map(norm));
    let html = '';
    let group = '';
    rows.forEach((row) => {
      if (row[2] !== group) {
        group = row[2];
        html += '<div class="dx-group">' + esc(group) + '</div>';
      }
      const on = chosen.has(norm(row[0]));
      html += '<button type="button" class="dx-item' + (on ? ' is-on' : '') +
        '" onclick="dxAdd(' + JSON.stringify(row[0]).replace(/"/g, '&quot;') + ')">' +
        '<span>' + esc(row[0]) + '</span><em>' + esc(row[1]) + '</em></button>';
    });
    const q2 = (q || '').trim();
    if (q2.length >= 3 && !rows.some((r) => norm(r[0]) === norm(q2))) {
      html = '<button type="button" class="dx-item dx-item--free" onclick="dxAddFree()">' +
        '<span>“' + esc(q2) + '” — ro‘yxatda yo‘q, shunday qo‘shish</span></button>' + html;
    }
    box.innerHTML = html || '<div class="dx-empty">Topilmadi — yozib qo‘shing</div>';
    box.hidden = false;
  }

  window.dxAdd = function (name) {
    if (!name) return;
    if (!selected.some((s) => norm(s) === norm(name))) selected.push(String(name).trim());
    const inp = $('dxSearch');
    if (inp) { inp.value = ''; inp.focus(); }
    renderChips();
    renderResults(search(''), '');
  };

  window.dxAddFree = function () {
    const inp = $('dxSearch');
    if (inp && inp.value.trim()) window.dxAdd(inp.value.trim());
  };

  window.dxRemove = function (i) {
    selected.splice(i, 1);
    renderChips();
  };

  window.dxClear = function () {
    selected = [];
    renderChips();
    const box = $('dxResults');
    if (box) box.hidden = true;
  };

  /** Tahlilga yuboriladigan qiymat */
  window.getClinicalDx = function () {
    return selected.slice();
  };

  /** Yo'llanmadan kelgan tashxisni tanlash (mos kelsa ro'yxatdan, aks holda matn) */
  window.setDxFromReferral = function (text) {
    const raw = String(text || '').trim();
    if (!raw) return 0;
    const parts = raw.split(/[,;·|]|\s\/\s/).map((s) => s.trim()).filter((s) => s.length > 2);
    let added = 0;
    parts.forEach((part) => {
      const p = norm(part.replace(/\?+$/, ''));
      if (!p) return;
      const hit = LIST.find((row) => norm(row[0]) === p || norm(row[1]) === p)
        || LIST.find((row) => norm(row[1]).includes(p) && p.length >= 5)
        || LIST.find((row) => norm(row[0]).includes(p) && p.length >= 5);
      const name = hit ? hit[0] : part.replace(/\?+$/, '').trim();
      if (name && !selected.some((s) => norm(s) === norm(name))) {
        selected.push(name);
        added += 1;
      }
    });
    if (added) renderChips();
    return added;
  };

  document.addEventListener('DOMContentLoaded', function () {
    const inp = $('dxSearch');
    const box = $('dxResults');
    if (!inp || !box) return;

    inp.addEventListener('focus', () => renderResults(search(inp.value), inp.value));
    inp.addEventListener('input', () => {
      activeIndex = -1;
      renderResults(search(inp.value), inp.value);
    });
    inp.addEventListener('keydown', (e) => {
      const items = [...box.querySelectorAll('.dx-item')];
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        if (!items.length) return;
        activeIndex += (e.key === 'ArrowDown' ? 1 : -1);
        if (activeIndex < 0) activeIndex = items.length - 1;
        if (activeIndex >= items.length) activeIndex = 0;
        items.forEach((el, i) => el.classList.toggle('is-active', i === activeIndex));
        items[activeIndex].scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (activeIndex >= 0 && items[activeIndex]) items[activeIndex].click();
        else window.dxAddFree();
      } else if (e.key === 'Escape') {
        box.hidden = true;
      }
    });
    document.addEventListener('click', (e) => {
      const picker = $('dxPicker');
      if (picker && !picker.contains(e.target)) box.hidden = true;
    });
    renderChips();
  });
})();
